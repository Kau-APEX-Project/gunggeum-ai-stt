"""소음·VAD 보고서 그래프 (results_noise/summary.csv, vad_summary.csv -> report_assets/noise_cer.png, vad_false.png)"""
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "report_assets"
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"  # charts.py와 같은 검증된 팔레트
plt.rcParams["font.family"] = ["Apple SD Gothic Neo", "AppleGothic"]
plt.rcParams["axes.unicode_minus"] = False

BABBLE = [("깨끗", "clean"), ("20", "babble20"), ("10", "babble10"), ("5", "babble5"), ("0", "babble0")]
TALKER = [("깨끗", "clean"), ("10", "talker10"), ("5", "talker5"), ("0", "talker0")]


def style(ax, title, ylabel):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, color=INK, fontsize=11, loc="left", pad=10)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=9)


def main():
    stt = {r["run"].split("__")[-1]: r for r in csv.DictReader(open(ROOT / "results_noise" / "summary.csv", encoding="utf-8-sig"))}
    base = [r for r in csv.DictReader(open(ROOT / "results_postcorrect" / "summary.csv", encoding="utf-8-sig"))
            if r["run"] == "aws-transcribe+location__post-location-jamo-0.85"][0]
    stt["clean"] = base
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.6), facecolor=SURFACE)
    for ax, series, color, title in ((axes[0], BABBLE, C1, "군중 웅성거림 (6명 겹친 말소리)"), (axes[1], TALKER, C2, "옆에서 말하는 사람 1명")):
        xs = [lab for lab, _ in series]
        ys = [float(stt[k]["CER(%)"]) for _, k in series]
        ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=7, markeredgecolor=SURFACE, markeredgewidth=2)
        for x, y in zip(xs, ys):
            ax.annotate(f"{y:.1f}", (x, y), textcoords="offset points", xytext=(0, 9), ha="center", fontsize=9, color=INK)
        style(ax, title, "CER (%) ↓ 낮을수록 좋음")
        ax.set_xlabel("해설사 목소리 대비 배경 세기 차이 (dB, 작을수록 시끄러움)", color=INK_2, fontsize=9)
        ax.set_ylim(0, 70)
    fig.suptitle("Amazon Transcribe(위치별 어휘+후처리 사전) 글자 오류율 — 배경 소리 세기별", color=INK, fontsize=12, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(OUT / "noise_cer.png", dpi=180, facecolor=SURFACE)

    vad = {(r["cond"], r["vad"]): r for r in csv.DictReader(open(ROOT / "results_noise" / "vad_summary.csv", encoding="utf-8-sig"))}
    conds = [("깨끗", "clean"), ("웅성 20", "babble20"), ("웅성 10", "babble10"), ("웅성 5", "babble5"), ("웅성 0", "babble0"),
             ("1명 10", "talker10"), ("1명 5", "talker5"), ("1명 0", "talker0")]
    dets = [("silero-0.5", "Silero VAD", C1), ("webrtc-3", "WebRTC VAD", C2), ("silero+gate-10+smooth", "Silero + 음량 문턱(-10 dB) + 시작/유지 조건", C3)]
    fig, ax = plt.subplots(figsize=(9.5, 3.6), facecolor=SURFACE)
    w = 0.26
    for i, (key, label, color) in enumerate(dets):
        ys = [float(vad[(c, key)]["무음구간_오작동(%)"]) for _, c in conds]
        xs = [j + (i - 1) * (w + 0.02) for j in range(len(conds))]
        ax.bar(xs, ys, width=w, color=color, label=label, linewidth=0)
        for x, y in zip(xs, ys):
            if y > 0:
                ax.annotate(f"{y:.0f}", (x, y), textcoords="offset points", xytext=(0, 3), ha="center", fontsize=7.5, color=INK)
    ax.set_xticks(range(len(conds)), [l for l, _ in conds])
    style(ax, "해설사가 말을 쉬는 동안 VAD가 배경 소리에 켜진 파일 비율 (0.3초 이상 연속)", "오작동 파일 (%) ↓ 낮을수록 좋음")
    ax.set_xlabel("배경 종류와 해설사 대비 세기 차이 (dB)", color=INK_2, fontsize=9)
    ax.set_ylim(0, 112)
    ax.legend(frameon=False, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=3, labelcolor=INK_2)
    fig.tight_layout()
    fig.savefig(OUT / "vad_false.png", dpi=180, facecolor=SURFACE)
    print("저장:", OUT / "noise_cer.png", OUT / "vad_false.png")


if __name__ == "__main__":
    main()
