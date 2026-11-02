"""깨끗한 녹음 180개에 군중 소음·동시 발화자를 섞어 조건별 wav를 만든다.

조건
  babble{SNR}  : 다른 낭독자 6명의 녹음을 겹친 웅성거림을 신호대잡음비 SNR dB로 섞음 (20/10/5/0)
  talker{SIR}  : 다른 낭독자 1명의 녹음을 같은 구간에 SIR dB 낮게 겹침 (10/5/0)

출력
  .cache/noisy/stt/<cond>/U0001.wav   원 녹음과 같은 구간 (Transcribe 정확도 측정용)
  .cache/noisy/vad/<cond>/U0001.wav   앞 1.5초·뒤 2.5초 무음 구간을 붙이고 배경을 전체에 깔음 (VAD 측정용)
  .cache/noisy/vad/clean/U0001.wav    배경 없이 무음 구간만 붙인 것
  .cache/noisy/vad_truth.json         VAD 정답 = 깨끗한 녹음에서 Silero VAD로 찾은 발화 구간(초, 앞 무음 포함 좌표)
  .cache/noisy/speech_db.json         녹음별 해설사 발화 세기(dBFS RMS) — 음량 문턱 VAD 실험용

신호 세기는 Silero VAD가 발화로 본 구간의 RMS로 재고, 배경은 그 RMS 대비 SNR dB 낮게 맞춘다.
같은 낭독자(30개 단위 폴더)·같은 해설 위치의 녹음은 배경으로 쓰지 않는다.
"""
import csv
import json
import random
from pathlib import Path

import numpy as np
import soundfile as sf
from silero_vad import get_speech_timestamps, load_silero_vad
import torch

ROOT = Path(__file__).resolve().parent
WAV_DIR = ROOT / ".cache" / "wav16k"
OUT = ROOT / ".cache" / "noisy"
SR = 16000
PRE, POST = 1.5, 2.5  # VAD용 무음 구간(초)
BABBLE_N = 6
CONDS = {f"babble{s}": ("babble", s) for s in (20, 10, 5, 0)} | {f"talker{s}": ("talker", s) for s in (10, 5, 0)}


def reader_of(uid: str) -> int:
    return (int(uid[1:]) - 1) // 30


def rms_db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)


def tile_to(x: np.ndarray, n: int, rng: random.Random) -> np.ndarray:
    """x를 이어 붙여 길이 n으로 만들되 시작 위치를 무작위로 민다."""
    reps = int(np.ceil((n + len(x)) / len(x))) + 1
    y = np.concatenate([x] * reps)
    off = rng.randrange(len(x))
    return y[off:off + n]


def main():
    vad = load_silero_vad()
    refs = list(csv.DictReader(open(ROOT / "data" / "references.csv", encoding="utf-8")))
    audio, speech_db, truth = {}, {}, {}
    for r in refs:
        x, sr = sf.read(WAV_DIR / f"{r['id']}.wav", dtype="float32")
        assert sr == SR
        audio[r["id"]] = x
        ts = get_speech_timestamps(torch.from_numpy(x), vad, sampling_rate=SR)
        mask = np.zeros(len(x), bool)
        for t in ts:
            mask[t["start"]:t["end"]] = True
        speech_db[r["id"]] = rms_db(x[mask]) if mask.any() else rms_db(x)
        truth[r["id"]] = [[round(t["start"] / SR + PRE, 3), round(t["end"] / SR + PRE, 3)] for t in ts]
    (OUT).mkdir(parents=True, exist_ok=True)
    (OUT / "vad_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=0))
    (OUT / "speech_db.json").write_text(json.dumps({k: round(float(v), 2) for k, v in speech_db.items()}, indent=0))

    pre, post = int(PRE * SR), int(POST * SR)
    for cond, (kind, level) in CONDS.items():
        for sub in ("stt", "vad"):
            (OUT / sub / cond).mkdir(parents=True, exist_ok=True)
        for r in refs:
            uid, x = r["id"], audio[r["id"]]
            rng = random.Random(f"{cond}:{uid}")
            others = [o for o in refs if reader_of(o["id"]) != reader_of(uid) and o["location"] != r["location"]]
            n_vad = pre + len(x) + post
            if kind == "babble":
                picks = rng.sample(others, BABBLE_N)
                bg = sum(tile_to(audio[p["id"]], n_vad, rng) for p in picks) / np.sqrt(BABBLE_N)
            else:
                p = rng.choice(others)
                same_reader = [o for o in others if reader_of(o["id"]) == reader_of(p["id"])]
                rng.shuffle(same_reader)
                bg = tile_to(np.concatenate([audio[o["id"]] for o in same_reader[:4]]), n_vad, rng)
            bg = bg * 10 ** ((speech_db[uid] - level - rms_db(bg)) / 20)
            vad_clip = np.concatenate([np.zeros(pre, np.float32), x, np.zeros(post, np.float32)]) + bg
            stt_clip = vad_clip[pre:pre + len(x)]
            for sub, clip in (("stt", stt_clip), ("vad", vad_clip)):
                peak = np.abs(clip).max()
                if peak > 0.99:
                    clip = clip / peak * 0.99
                sf.write(OUT / sub / cond / f"{uid}.wav", clip.astype(np.float32), SR, subtype="PCM_16")
        print(f"{cond}: {len(refs)}개 생성", flush=True)
    (OUT / "vad" / "clean").mkdir(parents=True, exist_ok=True)
    for r in refs:
        x = audio[r["id"]]
        clip = np.concatenate([np.zeros(pre, np.float32), x, np.zeros(post, np.float32)])
        sf.write(OUT / "vad" / "clean" / f"{r['id']}.wav", clip, SR, subtype="PCM_16")
    print("clean(vad): 완료")


if __name__ == "__main__":
    main()
