"""LivePipeline: 해설사 오디오 → VAD → Transcribe → 후처리 사전 → Translate → 카드 링크 → 자막 이벤트.

이벤트(dict, events()로 받음)
  {"type": "partial", "text": "…"}                                  해설사 화면 미리보기용 중간 인식
  {"type": "final", "seq": n, "ko_raw": "…", "ko": "…", "changes": [[원래, 용어, 점수]], "audio_end": 초(이 연결 기준),
   "audio_end_abs": 초(세션 시작부터 받은 오디오 기준), "ms": {...}}
  {"type": "subtitle", "seq": n, "ko": "…", "text": {"en": "…", "th": "…"}, "links": {"en": "…", "th": "…"},
   "ms": {"stt": Transcribe가 말한 문장 끝 시각→확정 도착, "stt_speech": VAD가 본 마지막 말소리→확정 도착(VAD 켤 때만),
          "post": 후처리, "translate": 번역(가장 늦은 언어), "total": 문장 끝→자막}}
  Transcribe의 end_time은 문장 뒤 무음까지 포함하는 경향이 있어 "stt"가 실제보다 작게 나온다. 자막 지연은 stt_speech·total로 본다.
  {"type": "gate", "kind": "start"|"end", "audio_sec": 초}           VAD가 발화 시작·끝을 본 시점
  {"type": "error", "where": "...", "message": "..."}

Transcribe 연결은 VAD가 발화 시작을 보면 열고(시작 전 0.3초부터 보냄), 발화가 끝난 뒤 close_after_ms 동안 말이 없으면 닫는다.
그 사이의 짧은 쉼(무음)은 그대로 보내서 Transcribe가 문장 끝을 스스로 알아채게 하고, 닫을 때 남은 문장이 확정된다.
쉬는 동안은 아무것도 보내지 않으므로 과금되지 않는다. VAD를 끄면 연결 하나를 계속 열어 두고 모든 오디오를 보낸다.
해설 위치가 바뀌면 다음 연결부터 그 위치의 어휘를 쓴다(열려 있는 연결은 발화가 끝날 때 닫힘).
"""
import asyncio
import time
from dataclasses import dataclass, field

from . import cards as cards_mod
from . import glossary
from .postcorrect import correct
from .vad import GateConfig, StreamGate


@dataclass
class PipelineConfig:
    region: str = "ap-northeast-2"
    terminology: str | None = "gungeum-ko"
    vad: GateConfig = field(default_factory=GateConfig)
    close_after_ms: int = 1500      # 발화 끝 뒤 이만큼 말이 없으면 Transcribe 연결을 닫음
    link_cards: bool = True
    emit_partials: bool = True


class LivePipeline:
    def __init__(self, languages=("en",), location=None, config: PipelineConfig | None = None,
                 transcribe_factory=None, translator=None):
        """languages: 자막 언어. 영어("en")가 기본이고, 태국어("th")는 용어집·품질 검증 뒤 추가."""
        self.languages = [l for l in languages if l != "ko"]
        self.location = location
        self.cfg = config or PipelineConfig()
        self.cfg.vad.hang_ms = max(self.cfg.vad.hang_ms, self.cfg.close_after_ms)
        self.gate = StreamGate(self.cfg.vad)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.seq = 0
        self.stream = None
        self._translate_tasks: set = set()
        self.stream_offset = 0.0
        self._stopped = False
        self._factory = transcribe_factory or self._default_factory
        self.translator = translator
        self.cards = glossary.cards() if self.cfg.link_cards else []

    # ---- 구성 ----
    def _default_factory(self, on_result, vocabulary):
        from .transcribe import TranscribeStream
        return TranscribeStream(on_result, region=self.cfg.region, vocabulary=vocabulary)

    async def start(self):
        if self.translator is None and self.languages:
            from .translate import Translator
            self.translator = Translator(region=self.cfg.region, terminology=self.cfg.terminology)
        if not self.cfg.vad.enabled:
            await self._open_stream()

    def set_location(self, location: str | None):
        """해설 위치 변경. 열려 있는 연결은 그대로 두고 다음 연결부터 새 어휘."""
        self.location = location

    async def events(self):
        while True:
            ev = await self.queue.get()
            if ev is None:
                return
            yield ev

    # ---- 오디오 입력 ----
    async def feed(self, pcm: bytes):
        if self._stopped:
            return
        out, events = self.gate.feed(pcm)
        starts = [ev for ev in events if ev.kind == "start"]
        ends = [ev for ev in events if ev.kind == "end"]
        for ev in events:
            await self.queue.put({"type": "gate", "kind": ev.kind, "audio_sec": round(ev.audio_sec, 3)})
        if starts and self.stream is None:
            await self._open_stream(offset=starts[0].audio_sec)
        if out and self.stream is not None:
            try:
                await self.stream.send(out)
            except Exception as e:
                await self.queue.put({"type": "error", "where": "transcribe.send", "message": str(e)})
                await self._close_stream()
        if ends and not self.gate.open and self.cfg.vad.enabled:
            await self._close_stream()

    async def stop(self):
        """마이크 꺼짐·세션 종료: 남은 결과를 확정시키고 닫는다."""
        self._stopped = True
        await self._close_stream()
        if self._translate_tasks:
            await asyncio.gather(*self._translate_tasks, return_exceptions=True)
        await self.queue.put(None)

    # ---- Transcribe 연결 ----
    async def _open_stream(self, offset: float = 0.0):
        vocab = glossary.vocab_for(self.location)
        self.stream_location = self.location
        self.stream_offset = offset  # 이 연결의 0초가 세션 오디오의 몇 초인지
        self.stream = self._factory(self._on_result, vocab)
        try:
            await self.stream.open()
        except Exception as e:
            await self.queue.put({"type": "error", "where": "transcribe.open", "message": str(e)})
            self.stream = None

    async def _close_stream(self):
        s, self.stream = self.stream, None
        if s is not None:
            await s.end()

    # ---- 결과 처리 ----
    async def _on_result(self, rec):
        if rec.is_partial:
            if self.cfg.emit_partials:
                await self.queue.put({"type": "partial", "text": rec.text})
            return
        t0 = time.perf_counter()
        terms = glossary.location_terms(self.stream_location)
        ko, changes = correct(terms, rec.text)
        post_ms = (time.perf_counter() - t0) * 1000
        self.seq += 1
        seq = self.seq
        stt_ms = (rec.received_at - rec.sent_at) * 1000
        audio_end_abs = round(self.stream_offset + rec.end_sec, 3)
        ms = {"stt": round(stt_ms, 1), "post": round(post_ms, 2)}
        last_speech = self.gate.last_speech_sec
        if last_speech is not None and self.stream is not None and last_speech - self.stream_offset <= rec.end_sec + 0.05:
            ms["stt_speech"] = round((rec.received_at - self.stream.sent_time_at(last_speech - self.stream_offset)) * 1000, 1)
        await self.queue.put({"type": "final", "seq": seq, "ko_raw": rec.text, "ko": ko, "changes": changes,
                             "audio_end": round(rec.end_sec, 3), "audio_end_abs": audio_end_abs, "ms": ms})
        task = asyncio.create_task(self._translate_and_emit(seq, ko, rec.sent_at, stt_ms, post_ms, audio_end_abs))
        self._translate_tasks.add(task)
        task.add_done_callback(self._translate_tasks.discard)

    async def _translate_and_emit(self, seq, ko, sent_at, stt_ms, post_ms, audio_end_abs=None):
        text, per_lang = {}, {}
        if self.languages and self.translator is not None:
            try:
                text, per_lang = await self.translator.translate(ko, self.languages)
            except Exception as e:
                await self.queue.put({"type": "error", "where": "translate", "message": str(e), "seq": seq})
        links = {}
        if self.cards:
            for lang, t in text.items():
                links[lang] = cards_mod.link_names(t, self.cards, glossary.LANG_LABEL.get(lang, lang))
            links["ko"] = cards_mod.link_names(ko, self.cards, "한국어")
        total_ms = (time.perf_counter() - sent_at) * 1000
        await self.queue.put({"type": "subtitle", "seq": seq, "ko": ko, "text": text, "links": links, "audio_end_abs": audio_end_abs,
                             "ms": {"stt": round(stt_ms, 1), "post": round(post_ms, 2),
                                    "translate": round(max(per_lang.values(), default=0), 1), "per_lang": per_lang,
                                    "total": round(total_ms, 1)}})
