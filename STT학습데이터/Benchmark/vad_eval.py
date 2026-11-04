"""소음 합성본(.cache/noisy/vad/<cond>)에서 Silero VAD·WebRTC VAD의 발화 구간 검출 성능을 잰다.

정답은 깨끗한 녹음에서 Silero VAD가 찾은 발화 구간(mix_noise.py가 만든 vad_truth.json).
각 파일은 앞 1.5초·뒤 2.5초 무음(배경만 있는) 구간을 가진다.

지표 (30ms 프레임 단위)
  검출률(%)      정답 발화 프레임 중 VAD가 발화로 본 비율 (놓치면 자막이 끊김)
  오검출률(%)    정답 비발화 프레임 중 VAD가 발화로 본 비율 (배경에 켜지면 엉뚱한 자막)
  무음구간 오작동(%)  앞뒤 무음 구간에서 0.3초 이상 연속으로 켜진 파일 비율 (경쟁 화자를 해설사로 착각)
  시작 지연(ms)  정답 발화 시작 대비 VAD가 처음 켜진 시점 (평균, 양수면 늦게 켜짐)
  처리 속도      파일 1개 처리 시간 평균(ms) — 두 VAD 모두 폰에서 실시간 가능한 수준인지

검출기
  silero-0.5 / silero-0.7 : Silero VAD, 32ms 조각씩 실시간처럼 넣고 확률이 문턱 이상이면 발화
  webrtc-3                : WebRTC VAD 가장 엄격한 모드
  silero+gate-10 / -15    : Silero 0.5에 음량 문턱을 더한 것. 해설사 발화 세기(깨끗한 녹음의 발화 RMS)보다
                            10/15 dB 이상 작은 프레임은 발화로 치지 않는다. 앱에서는 해설사가 처음 몇 초 말할 때
                            세기를 재 두는 식으로 쓸 수 있다 (근접 마이크라 해설사 목소리가 배경보다 훨씬 큼).
  silero+gate-15+smooth / silero+gate-10+smooth : 위 음량 문턱에 "0.1초 이상 이어져야 켜지고, 꺼진 뒤 0.3초는 켜진 것으로 유지"를
                            더한 것. 순간 튀는 배경은 무시하고 작은 음절에서 잠깐 끊기는 것은 이어 준다. 앱에 넣을 형태.

사용법: python vad_eval.py  → results_noise/vad_summary.csv, vad_per_file.csv
"""
import csv
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import webrtcvad
from silero_vad import load_silero_vad

ROOT = Path(__file__).resolve().parent
NOISY = ROOT / ".cache" / "noisy"
OUT = ROOT / "results_noise"
SR = 16000
FRAME = 480  # 30ms
CONDS = ["clean", "babble20", "babble10", "babble5", "babble0", "talker10", "talker5", "talker0"]
MIN_FALSE_RUN = 10  # 0.3초


def silero_stream(model, x: np.ndarray, threshold: float):
    """실시간처럼 512샘플(32ms)씩 넣어 확률을 얻고, threshold 이상이면 발화. 30ms 프레임 마스크로 바꾼다."""
    model.reset_states()
    probs = []
    for i in range(0, len(x) - 511, 512):
        probs.append(model(torch.from_numpy(x[i:i + 512]), SR).item())
    on = np.array(probs) >= threshold
    mask = np.zeros(len(x) // FRAME, bool)
    for j, v in enumerate(on):
        if v:
            a, b = j * 512 // FRAME, min(len(mask), (j * 512 + 512) // FRAME + 1)
            mask[a:b] = True
    return mask


def webrtc_mask(vad, pcm16: bytes, n_frames: int):
    mask = np.zeros(n_frames, bool)
    for f in range(n_frames):
        mask[f] = vad.is_speech(pcm16[f * FRAME * 2:(f + 1) * FRAME * 2], SR)
    return mask


def frame_db(x: np.ndarray, n_frames: int) -> np.ndarray:
    f = x[:n_frames * FRAME].reshape(n_frames, FRAME)
    return 20 * np.log10(np.sqrt((f ** 2).mean(axis=1)) + 1e-9)


def truth_mask(spans, n_frames):
    m = np.zeros(n_frames, bool)
    for a, b in spans:
        m[int(a * SR / FRAME):int(np.ceil(b * SR / FRAME))] = True
    return m


def longest_run(v: np.ndarray) -> int:
    best = cur = 0
    for x in v:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def main():
    truth = json.loads((NOISY / "vad_truth.json").read_text())
    silero = load_silero_vad()
    ref_db = json.loads((NOISY / "speech_db.json").read_text())
    uid_now = {"id": None}

    def gated(x, n, margin):
        m = silero_stream(silero, x, 0.5)[:n]
        return m & (frame_db(x, n) >= ref_db[uid_now["id"]] - margin)

    def smooth(m, onset=3, hang=10):
        """onset 프레임 이상 연속으로 켜져야 발화 시작, 꺼진 뒤 hang 프레임까지는 유지."""
        out = np.zeros(len(m), bool); run = 0; last = -10 ** 9
        for i, v in enumerate(m):
            run = run + 1 if v else 0
            if run >= onset:
                out[i - onset + 1:i + 1] = True; last = i
            elif i - last <= hang:
                out[i] = True
        return out

    detectors = {
        "silero-0.5": lambda x, pcm, n: silero_stream(silero, x, 0.5),
        "silero-0.7": lambda x, pcm, n: silero_stream(silero, x, 0.7),
        "webrtc-3": lambda x, pcm, n: webrtc_mask(webrtcvad.Vad(3), pcm, n),
        "silero+gate-10": lambda x, pcm, n: gated(x, n, 10),
        "silero+gate-15": lambda x, pcm, n: gated(x, n, 15),
        "silero+gate-15+smooth": lambda x, pcm, n: smooth(gated(x, n, 15)),
        "silero+gate-10+smooth": lambda x, pcm, n: smooth(gated(x, n, 10)),
    }
    rows, summary = [], []
    for cond in CONDS:
        for det, fn in detectors.items():
            hit = tot_sp = fa = tot_ns = 0
            onsets, times, false_files = [], [], 0
            for uid, spans in truth.items():
                uid_now["id"] = uid
                x, _ = sf.read(NOISY / "vad" / cond / f"{uid}.wav", dtype="float32")
                n = len(x) // FRAME
                pcm = (x[:n * FRAME] * 32767).astype(np.int16).tobytes()
                gt = truth_mask(spans, n)
                t0 = time.perf_counter()
                m = fn(x, pcm, n)[:n]
                times.append((time.perf_counter() - t0) * 1000)
                hit += int((m & gt).sum()); tot_sp += int(gt.sum())
                fa += int((m & ~gt).sum()); tot_ns += int((~gt).sum())
                pre_end = int(1.5 * SR / FRAME); post_start = int(np.ceil((len(x) / SR - 2.5) * SR / FRAME))
                gaps = np.concatenate([m[:pre_end], m[post_start:]])
                bad = longest_run(gaps) >= MIN_FALSE_RUN
                false_files += bad
                if gt.any() and m[gt].any():
                    first_gt = int(np.argmax(gt))
                    first_on = int(np.argmax(m & (np.arange(n) >= first_gt - 10)))
                    onsets.append((first_on - first_gt) * 30)
                rows.append({"cond": cond, "vad": det, "id": uid, "검출률": round(100 * (m & gt).sum() / max(1, gt.sum()), 1),
                             "오검출률": round(100 * (m & ~gt).sum() / max(1, (~gt).sum()), 1), "무음구간_오작동": int(bad)})
            summary.append({"cond": cond, "vad": det, "검출률(%)": round(100 * hit / tot_sp, 1), "오검출률(%)": round(100 * fa / tot_ns, 1),
                            "무음구간_오작동(%)": round(100 * false_files / len(truth), 1),
                            "시작지연_평균(ms)": round(float(np.mean(onsets)), 0) if onsets else "", "처리_ms/파일": round(float(np.mean(times)), 1)})
            print(summary[-1], flush=True)
    OUT.mkdir(exist_ok=True)
    with open(OUT / "vad_summary.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=summary[0].keys()); w.writeheader(); w.writerows(summary)
    with open(OUT / "vad_per_file.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    main()
