"""Amazon Transcribe 스트리밍 API로 녹음 180개를 전사한다 (서울 리전, ko-KR).

사용법
  python run_transcribe.py                          # 사전 없음
  python run_transcribe.py --create-vocab global    # 전체 사전으로 사용자 지정 어휘 1개 생성(최초 1회)
  python run_transcribe.py --create-vocab location  # 해설 위치별 어휘 생성(위치마다 1개)
  python run_transcribe.py --vocab global|location  # 사용자 지정 어휘 적용
  --pace realtime  : 100ms 조각을 실제 말하는 속도로 보내고, 마지막 조각 뒤 최종 결과까지 걸린 시간을 잰다
  --pace fast      : 조각을 쉬지 않고 보내고, 연결부터 최종 결과까지 걸린 시간을 잰다 (기본, RTF 비교용)
  --wav-dir DIR --tag NAME : 다른 폴더의 wav(예: 소음 합성본)를 전사하고 결과 이름 뒤에 __NAME을 붙인다

AWS 자격 증명은 표준 경로(~/.aws/credentials 또는 환경 변수)에서 읽는다.
"""
import argparse
import asyncio
import csv
import json
import platform
import re
import time
from pathlib import Path

import soundfile as sf
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler

from metrics import num_to_korean
from postcorrect import live_post

ROOT = Path(__file__).resolve().parent
WAV_DIR = ROOT / ".cache" / "wav16k"
RES_DIR = ROOT / "results"
REGION = "ap-northeast-2"
VOCAB = "gungeum-gyeongbokgung-ko"
CHUNK = 3200  # 16kHz 16bit mono 100ms


class Collector(TranscriptResultStreamHandler):
    def __init__(self, stream):
        super().__init__(stream)
        self.finals, self.last_at = [], None

    async def handle_transcript_event(self, event):
        for res in event.transcript.results:
            if not res.is_partial and res.alternatives:
                self.finals.append(res.alternatives[0].transcript)
                self.last_at = time.perf_counter()


async def transcribe(client, pcm: bytes, vocab, pace):
    t0 = time.perf_counter()
    stream = await client.start_stream_transcription(
        language_code="ko-KR", media_sample_rate_hz=16000, media_encoding="pcm", vocabulary_name=vocab)
    handler = Collector(stream.output_stream)
    sent_at = {}

    async def send():
        for i in range(0, len(pcm), CHUNK):
            await stream.input_stream.send_audio_event(audio_chunk=pcm[i:i + CHUNK])
            if pace == "realtime":
                await asyncio.sleep(0.1)
        sent_at["end"] = time.perf_counter()
        await stream.input_stream.end_stream()

    await asyncio.gather(send(), handler.handle_events())
    done = handler.last_at or time.perf_counter()
    elapsed = done - (sent_at["end"] if pace == "realtime" else t0)
    return " ".join(handler.finals).strip(), elapsed


def vocab_phrases(terms):
    """ko-KR 목록형 어휘: 공백은 하이픈, 숫자는 한글 읽기, 한글 외 문자는 뺀다."""
    out = []
    for t in terms:
        t = re.sub(r"[^가-힣 ]", "", num_to_korean(t)).strip()
        t = re.sub(r"\s+", "-", t)
        if t and t not in out:
            out.append(t)
    return out


def location_vocabs():
    """해설 위치 -> (어휘 이름, 용어 목록). 어휘 이름에는 한글을 못 써서 위치 순번으로 붙인다."""
    loc_hints = json.loads((ROOT / "data" / "hints_by_location.json").read_text(encoding="utf-8"))
    return {loc: (f"gungeum-loc-{i:02d}", terms) for i, (loc, terms) in enumerate(loc_hints.items())}


def create_vocab(kind):
    import boto3
    tc = boto3.client("transcribe", region_name=REGION)
    if kind == "global":
        terms = (ROOT / "data" / "hint_terms.txt").read_text(encoding="utf-8").splitlines()
        todo = {VOCAB: vocab_phrases(terms)}
    else:
        used = {r["location"] for r in csv.DictReader(open(ROOT / "data" / "references.csv", encoding="utf-8"))}
        todo = {name: vocab_phrases(terms) for loc, (name, terms) in location_vocabs().items() if loc in used}

    pending = dict(todo)
    while pending:
        for name, phrases in list(pending.items()):
            try:
                tc.create_vocabulary(VocabularyName=name, LanguageCode="ko-KR", Phrases=phrases)
            except tc.exceptions.ConflictException:
                state = tc.get_vocabulary(VocabularyName=name)["VocabularyState"]
                if state == "PENDING":
                    continue
                tc.update_vocabulary(VocabularyName=name, LanguageCode="ko-KR", Phrases=phrases)
            except tc.exceptions.LimitExceededException:
                break  # 동시에 만들 수 있는 어휘 수 제한 -> 앞의 것이 끝나면 다시 시도
            del pending[name]
        time.sleep(10)

    while True:
        states = {n: tc.get_vocabulary(VocabularyName=n) for n in todo}
        if all(s["VocabularyState"] != "PENDING" for s in states.values()):
            break
        time.sleep(10)
    for n, s in states.items():
        print(f"어휘 {n}: {s['VocabularyState']} ({len(todo[n])}개)", s.get("FailureReason", ""))


async def run(args):
    refs = list(csv.DictReader(open(ROOT / "data" / "references.csv", encoding="utf-8")))
    if args.limit:
        refs = refs[:args.limit]
    loc_vocab = {loc: name for loc, (name, _) in location_vocabs().items()}
    vocab_for = lambda r: {"none": None, "global": VOCAB, "location": loc_vocab[r["location"]]}[args.vocab]
    name = ("aws-transcribe" + ("-realtime" if args.pace == "realtime" else "")
            + ("" if args.vocab == "none" else f"+{args.vocab}") + ("+post" if args.post else "")
            + (f"__{args.tag}" if args.tag else ""))
    wav_dir = Path(args.wav_dir) if args.wav_dir else WAV_DIR
    res_dir = ROOT / args.out
    res_dir.mkdir(exist_ok=True)
    out_path = res_dir / f"{name}.jsonl"
    loc_hints = json.loads((ROOT / "data" / "hints_by_location.json").read_text(encoding="utf-8"))
    done = {json.loads(l)["id"] for l in out_path.open(encoding="utf-8")} if out_path.exists() else set()

    client = TranscribeStreamingClient(region=REGION)
    pcm = lambda r: sf.read(wav_dir / f"{r['id']}.wav", dtype="int16")[0].tobytes()
    await transcribe(client, pcm(refs[0]), vocab_for(refs[0]), args.pace)  # 워밍업(연결·인증)

    meta = {"model": "aws-transcribe", "hint": args.vocab, "device": f"AWS {REGION} streaming",
            "pace": args.pace, "machine": platform.platform(), "audio": str(wav_dir.relative_to(ROOT)) if wav_dir.is_relative_to(ROOT) else str(wav_dir)}
    (res_dir / f"{name}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    with out_path.open("a", encoding="utf-8") as fp:
        for i, r in enumerate(refs, 1):
            if r["id"] in done:
                continue
            hyp, dt = await transcribe(client, pcm(r), vocab_for(r), args.pace)
            raw = hyp
            if args.post:
                t = time.perf_counter()
                hyp, _ = live_post(loc_hints[r["location"]], raw)
                dt += time.perf_counter() - t
            fp.write(json.dumps({"id": r["id"], "hyp": hyp, "proc_sec": round(dt, 4), "duration": float(r["duration"]),
                                 **({"hyp_raw": raw} if args.post else {})}, ensure_ascii=False) + "\n")
            fp.flush()
            if i % 30 == 0 or i == len(refs):
                print(f"[{name}] {i}/{len(refs)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocab", choices=["none", "global", "location"], default="none")
    ap.add_argument("--create-vocab", choices=["global", "location"])
    ap.add_argument("--pace", choices=["fast", "realtime"], default="fast")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="results", help="결과 폴더")
    ap.add_argument("--post", action="store_true", help="받아쓰기 직후 후처리 사전(위치별·자모·0.85) 적용")
    ap.add_argument("--wav-dir", help="전사할 wav 폴더 (기본 .cache/wav16k)")
    ap.add_argument("--tag", help="결과 파일 이름에 붙일 조건 이름")
    args = ap.parse_args()
    if args.create_vocab:
        create_vocab(args.create_vocab)
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
