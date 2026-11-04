"""Amazon Transcribe 스트리밍 연결 하나. 위치별 사용자 지정 어휘를 붙여 열고, PCM을 보내고, 중간/확정 결과를 콜백으로 준다.

결과마다 Transcribe가 주는 오디오 시각(start_time·end_time, 초)과 우리가 그 오디오를 보낸 벽시계 시각을 같이 넘겨
"문장 끝(오디오) → 확정 결과 도착" 지연을 잴 수 있게 한다.
"""
import asyncio
import time
from bisect import bisect_right
from dataclasses import dataclass

from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler

SR = 16000


@dataclass
class Recognized:
    text: str
    is_partial: bool
    start_sec: float      # 오디오 기준 시작
    end_sec: float        # 오디오 기준 끝
    sent_at: float        # end_sec 지점 오디오를 보낸 벽시계(perf_counter)
    received_at: float    # 결과를 받은 벽시계


class _Handler(TranscriptResultStreamHandler):
    def __init__(self, stream, owner):
        super().__init__(stream)
        self.owner = owner

    async def handle_transcript_event(self, event):
        now = time.perf_counter()
        for res in event.transcript.results:
            if not res.alternatives:
                continue
            text = res.alternatives[0].transcript.strip()
            if not text:
                continue
            rec = Recognized(text, bool(res.is_partial), float(res.start_time or 0), float(res.end_time or 0),
                             self.owner.sent_time_at(float(res.end_time or 0)), now)
            await self.owner.on_result(rec)


class TranscribeStream:
    def __init__(self, on_result, region="ap-northeast-2", vocabulary: str | None = None, language="ko-KR"):
        self.on_result = on_result
        self.region, self.vocabulary, self.language = region, vocabulary, language
        self.client = TranscribeStreamingClient(region=region)
        self.stream = None
        self._task = None
        self.audio_sec = 0.0
        self._sent_marks: list[tuple[float, float]] = []  # (오디오 누적 초, 보낸 벽시계)
        self.opened_at = None
        self.closed = asyncio.Event()

    async def open(self):
        t = time.perf_counter()
        self.stream = await self.client.start_stream_transcription(
            language_code=self.language, media_sample_rate_hz=SR, media_encoding="pcm",
            vocabulary_name=self.vocabulary)
        self.opened_at = time.perf_counter()
        self.connect_ms = (self.opened_at - t) * 1000
        handler = _Handler(self.stream.output_stream, self)
        self._task = asyncio.create_task(self._run(handler))

    async def _run(self, handler):
        try:
            await handler.handle_events()
        except Exception as e:  # 연결이 끊기면 파이프라인이 다시 연다
            self.error = e
        finally:
            self.closed.set()

    async def send(self, pcm: bytes):
        if not pcm or self.stream is None:
            return
        await self.stream.input_stream.send_audio_event(audio_chunk=pcm)
        self.audio_sec += len(pcm) / 2 / SR
        self._sent_marks.append((self.audio_sec, time.perf_counter()))
        if len(self._sent_marks) > 20000:
            del self._sent_marks[:10000]

    def sent_time_at(self, audio_sec: float) -> float:
        """오디오 시각 audio_sec 지점을 보낸 벽시계. 그 지점이 든 조각을 보낸 시각."""
        if not self._sent_marks:
            return time.perf_counter()
        i = bisect_right([m[0] for m in self._sent_marks], audio_sec)
        return self._sent_marks[min(i, len(self._sent_marks) - 1)][1]

    async def end(self, wait: float = 5.0):
        """오디오 끝을 알려 남은 결과를 확정시키고 닫는다."""
        if self.stream is None:
            return
        try:
            await self.stream.input_stream.end_stream()
        except Exception:
            pass
        try:
            await asyncio.wait_for(self.closed.wait(), timeout=wait)
        except asyncio.TimeoutError:
            if self._task:
                self._task.cancel()
        self.stream = None
