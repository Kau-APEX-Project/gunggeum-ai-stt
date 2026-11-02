"""자막 지연 측정: 녹음 180개를 해설사처럼 실제 속도로 시험 서버에 흘려 넣고, 관람객처럼 자막을 받아 지연을 잰다.

  python -m gunggeum_stt.bench_client --server ws://localhost:8765 --langs en --gap 1.0 [--limit 20] [--vad 0]
  → results_live/bench_<tag>.jsonl, bench_<tag>_summary.json (평균·p90: 문장 끝→확정, →자막 도착, 단계별)

입력: STT학습데이터/Benchmark/data/references.csv 와 .cache/wav16k/*.wav (16 kHz). 발화마다 해설 위치를 먼저 보낸다.
"문장 끝" = 그 발화에서 말이 끝나는 지점(녹음 파일 끝의 무음은 뺌, Silero VAD로 찾음)의 오디오 조각을 보낸 벽시계.
확정 문장은 Transcribe가 준 오디오 위치(audio_end_abs)로 발화에 대응시킨다. 발화 사이에는 gap 초 무음을 보낸다.
  --close-ms  발화 끝 뒤 Transcribe 연결을 닫기까지 무음 시간(VAD 켤 때). gap 보다 짧으면 발화마다 연결을 닫고 확정을 강제한다.
"""
import argparse
import asyncio
import csv
import json
import statistics
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets

from .vad import FRAME, SileroOnnx

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "STT학습데이터" / "Benchmark"  # --bench-dir 로 바꿀 수 있음 (.cache/wav16k 는 prepare.py 가 만듦)
CHUNK = 3200  # 100 ms


def speech_end_sec(x: np.ndarray, vad: SileroOnnx) -> float:
    """파일 안에서 말이 끝나는 지점(초): Silero 확률 ≥ 0.5 인 마지막 32 ms 조각의 끝"""
    vad.reset_states()
    last = 0
    for i in range(0, len(x) - FRAME + 1, FRAME):
        if vad(x[i:i + FRAME].astype(np.float32) / 32768.0) >= 0.5:
            last = i + FRAME
    return last / 16000 if last else len(x) / 16000


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(len(v) * q) - 1))] if v else None


async def run(args):
    global BENCH
    if args.bench_dir:
        BENCH = Path(args.bench_dir)
    refs = list(csv.DictReader(open(BENCH / "data" / "references.csv", encoding="utf-8")))[:args.limit or None]
    out_dir = ROOT / "results_live"; out_dir.mkdir(exist_ok=True)
    tag = args.tag or f"{'vad' if args.vad else 'novad'}-{args.langs.replace(',', '+')}"
    session = f"bench-{int(time.time())}"
    langs = args.langs.split(",")
    guide_url = (f"{args.server}/ws/guide?session={session}&langs={args.langs}&location={refs[0]['location']}"
                 f"&vad={1 if args.vad else 0}&close_ms={args.close_ms}")
    rows, finals, subs = [], {}, {}
    utter = {}              # 발화 순번 → {"abs_start", "abs_speech_end", "abs_end", "t_speech_end", "t_end"}
    audio_sent = [0.0]      # 보낸 오디오 누적 초
    vad = SileroOnnx()

    async with websockets.connect(guide_url, max_size=None) as guide:
        listeners = {}
        for l in langs:
            listeners[l] = await websockets.connect(f"{args.server}/ws/listen?session={session}&lang={l}")
            await listeners[l].recv()  # hello

        async def read_guide():
            async for m in guide:
                ev = json.loads(m)
                if ev["type"] == "final":
                    finals[ev["seq"]] = (time.perf_counter(), ev)
                elif ev["type"] == "error":
                    print("오류:", ev, flush=True)

        async def read_listener(lang, ws):
            async for m in ws:
                ev = json.loads(m)
                if ev["type"] == "subtitle":
                    subs.setdefault(ev["seq"], {})[lang] = (time.perf_counter(), ev)
                elif ev["type"] == "ended":
                    return

        tasks = [asyncio.create_task(read_guide())] + [asyncio.create_task(read_listener(l, ws)) for l, ws in listeners.items()]
        t_start = time.perf_counter()
        for i, r in enumerate(refs, 1):
            await guide.send(json.dumps({"type": "location", "location": r["location"]}, ensure_ascii=False))
            x = sf.read(BENCH / ".cache" / "wav16k" / f"{r['id']}.wav", dtype="int16")[0]
            pcm = x.tobytes()
            sp_end = speech_end_sec(x, vad)
            u = utter[i] = {"id": r["id"], "abs_start": audio_sent[0], "abs_speech_end": audio_sent[0] + sp_end,
                            "abs_end": audio_sent[0] + len(x) / 16000, "t_speech_end": None, "t_end": None}
            next_at = time.perf_counter()
            for j in range(0, len(pcm), CHUNK):
                await guide.send(pcm[j:j + CHUNK])
                if u["t_speech_end"] is None and (j + CHUNK) / 2 / 16000 >= sp_end:
                    u["t_speech_end"] = time.perf_counter()
                next_at += 0.1
                await asyncio.sleep(max(0, next_at - time.perf_counter()))
            u["t_end"] = time.perf_counter()
            audio_sent[0] += len(x) / 16000
            silence = b"\x00" * CHUNK
            for _ in range(int(args.gap * 10)):
                await guide.send(silence)
                audio_sent[0] += 0.1
                next_at += 0.1
                await asyncio.sleep(max(0, next_at - time.perf_counter()))
            if i % 20 == 0:
                print(f"{i}/{len(refs)} 보냄, 확정 {len(finals)}, 자막 {len(subs)}", flush=True)
        await asyncio.sleep(args.gap + 4)
        await guide.send(json.dumps({"type": "mic", "on": False}))
        await asyncio.sleep(3)
        for t in tasks:
            t.cancel()
        for ws in listeners.values():
            await ws.close()

    # 발화 ↔ 확정 문장 대조: Transcribe가 준 오디오 끝 위치(audio_end_abs)가 든 발화. 한 발화가 여러 문장으로 잘리면 모두 그 발화.
    def owner(pos):
        for k, u in utter.items():
            if u["abs_start"] - 0.05 <= pos <= u["abs_end"] + args.gap + 0.05:
                return k
        return None
    for seq in sorted(finals):
        t_final, ev = finals[seq]
        k = owner(ev.get("audio_end_abs") or -1)
        u = utter.get(k)
        row = {"seq": seq, "utterance": k, "id": u["id"] if u else None, "ko_raw": ev["ko_raw"], "ko": ev["ko"], "changes": ev["changes"],
               "audio_end_abs": ev.get("audio_end_abs"), "stt_ms": ev["ms"]["stt"], "post_ms": ev["ms"]["post"],
               "speech_end_to_final_ms": round((t_final - u["t_speech_end"]) * 1000, 1) if u else None,
               "file_end_to_final_ms": round((t_final - u["t_end"]) * 1000, 1) if u else None}
        for lang in langs:
            if seq in subs and lang in subs[seq]:
                t_sub, sev = subs[seq][lang]
                row[f"{lang}"] = sev["text"]; row[f"{lang}_linked"] = sev["linked"]
                row[f"{lang}_translate_ms"] = sev["ms"]["per_lang"].get(lang)
                row[f"speech_end_to_{lang}_ms"] = round((t_sub - u["t_speech_end"]) * 1000, 1) if u else None
        rows.append(row)
    with open(out_dir / f"bench_{tag}.jsonl", "w", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 발화 단위 집계: 발화마다 마지막 문장의 지연
    last_by_utt = {}
    for r in rows:
        if r["utterance"]:
            last_by_utt[r["utterance"]] = r
    def stat(key):
        v = [r[key] for r in last_by_utt.values() if r.get(key) is not None]
        return {"n": len(v), "avg": round(statistics.mean(v), 1), "p90": round(pct(v, 0.9), 1), "max": round(max(v), 1)} if v else None
    summary = {"tag": tag, "vad": args.vad, "close_ms": args.close_ms, "gap_sec": args.gap, "utterances": len(refs), "finals": len(rows),
               "utterances_with_final": len(last_by_utt), "unmatched_finals": sum(1 for r in rows if r["utterance"] is None),
               "missing_utterances": [utter[k]["id"] for k in utter if k not in last_by_utt],
               "speech_end_to_final_ms": stat("speech_end_to_final_ms"), "file_end_to_final_ms": stat("file_end_to_final_ms"),
               "stt_ms(transcribe_end_time→final)": stat("stt_ms"),
               **{f"speech_end_to_{l}_ms": stat(f"speech_end_to_{l}_ms") for l in langs},
               **{f"{l}_translate_ms": stat(f"{l}_translate_ms") for l in langs},
               "wall_sec": round(time.perf_counter() - t_start, 1), "audio_sec": round(audio_sent[0], 1)}
    (out_dir / f"bench_{tag}_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="ws://localhost:8765")
    ap.add_argument("--langs", default="en", help="자막 언어, 쉼표로 여러 개 (예: en,th). 빈 문자열이면 번역 없이 STT만")
    ap.add_argument("--gap", type=float, default=1.0, help="발화 사이 무음(초)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--vad", type=int, default=1)
    ap.add_argument("--close-ms", type=int, default=1500)
    ap.add_argument("--tag")
    ap.add_argument("--bench-dir", help="data/references.csv 와 .cache/wav16k 가 있는 Benchmark 폴더")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
