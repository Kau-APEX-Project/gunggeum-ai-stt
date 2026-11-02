"""보고서용 그래프: 모델별 사전 적용 전후 용어 인식률 (results/summary.csv -> report_assets/term_recall.png)"""
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from metrics import term_groups, term_hits

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "report_assets"

SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BEFORE, AFTER = "#2a78d6", "#eb6834"  # 기본 팔레트 1·2번 (validate_palette.js 통과)

LABELS = {
    "whisper-large-v3": "Whisper large-v3",
    "whisper-large-v3-turbo": "Whisper large-v3-turbo",
    "qwen3-asr-1.7b": "Qwen3-ASR 1.7B",
    "qwen3-asr-0.6b": "Qwen3-ASR 0.6B",
    "aws-transcribe": "Amazon Transcribe",
    "apple-transcriber": "Apple SpeechTranscriber",
    "apple-dictation": "Apple 받아쓰기",
    "cohere-transcribe": "Cohere Transcribe",
    "clova": "CLOVA Speech",
}


# 모델별 권장 사전 조합(보고서 3.5 표와 같음). 받아쓰기 사전이 잘 듣는 모델은 받아쓰기 사전만,
# 없거나 약한 모델은 후처리 사전(자모·0.85)을 쓴다. (결과 폴더, 사전 완비 실행, 그 문장을 뺀 사전 실행, 조합 이름)
BEST = {
    "whisper-large-v3": ("results", "whisper-large-v3+location", "whisper-large-v3+loo", "받아쓰기 사전"),
    "whisper-large-v3-turbo": ("results", "whisper-large-v3-turbo+location", "whisper-large-v3-turbo+loo", "받아쓰기 사전"),
    "qwen3-asr-1.7b": ("results", "qwen3-asr-1.7b+location-guard", "qwen3-asr-1.7b+loo-guard", "받아쓰기 사전 + 붕괴 방지"),
    "qwen3-asr-0.6b": ("results", "qwen3-asr-0.6b+location-guard", "qwen3-asr-0.6b+loo-guard", "받아쓰기 사전 + 붕괴 방지"),
    "aws-transcribe": ("results_postcorrect", "aws-transcribe+location__post-location-jamo-0.85",
                       "aws-transcribe__post-loo-jamo-0.85", "어휘 + 후처리 사전"),
    "apple-dictation": ("results_postcorrect", "apple-dictation__post-location-jamo-0.85",
                        "apple-dictation__post-loo-jamo-0.85", "후처리 사전"),
    "apple-transcriber": ("results_postcorrect", "apple-transcriber__post-location-jamo-0.85",
                          "apple-transcriber__post-loo-jamo-0.85", "후처리 사전"),
    "cohere-transcribe": ("results_postcorrect", "cohere-transcribe__post-location-jamo-0.85",
                          "cohere-transcribe__post-loo-jamo-0.85", "후처리 사전"),    "clova": ("results", "clova+location-w1", "clova+loo-w1", "키워드 부스팅 (가중치 1)"),
}
LOO = "#1baf7a"  # 기본 팔레트 3번 (사전 없음·그 문장 뺀 사전·사전 완비 순서로 validate_palette.js 통과)


def summary(folder):
    return {r["run"]: r for r in csv.DictReader(open(ROOT / folder / "summary.csv", encoding="utf-8-sig"))}


def main():
    metric = "용어인식률(%)"
    base_rows = summary("results")
    data = []
    for model, label in LABELS.items():
        folder, full, loo, how = BEST[model]
        rows = summary(folder)
        data.append((label, float(base_rows[model][metric]), float(rows[loo][metric]), float(rows[full][metric]), how))
    data.sort(key=lambda d: d[3])
    refs = csv.DictReader(open(ROOT / "data" / "references.csv", encoding="utf-8"))
    n_terms = sum(len(term_hits(r["text"], r["text"], term_groups(r["terms"], r["buildings"]))) for r in refs)

    plt.rcParams.update({"font.family": "Apple SD Gothic Neo", "font.size": 11})
    fig, ax = plt.subplots(figsize=(8.6, 4.8), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    for y, (label, base, loo, full, how) in enumerate(data):
        ax.plot([min(base, loo, full), max(base, loo, full)], [y, y], color=GRID, lw=2, zorder=1, solid_capstyle="round")
        for x, color in [(base, BEFORE), (loo, LOO), (full, AFTER)]:
            ax.scatter(x, y, s=70, color=color, zorder=3, edgecolors=SURFACE, linewidths=2)
        ax.text(max(base, loo, full) + 1.2, y, f"{full:.1f}%  · {how}", va="center", color=INK, fontsize=9.5)

    ax.set_yticks(range(len(data)), [d[0] for d in data], color=INK)
    ax.set_xlim(40, 118)
    ax.set_xticks(range(40, 101, 10), [f"{x}%" for x in range(40, 101, 10)], color=INK_2)
    ax.grid(axis="x", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    ax.set_title(f"용어·전각명 인식률: 모델별 권장 사전 조합 ({n_terms}건)  높을수록 좋음", loc="left", color=INK, fontsize=13, pad=26)
    ax.scatter([], [], s=70, color=BEFORE, label="사전 없음")
    ax.scatter([], [], s=70, color=LOO, label="그 문장을 뺀 사전")
    ax.scatter([], [], s=70, color=AFTER, label="사전 완비 (숫자)")
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=10,
              handletextpad=0.3, columnspacing=1.2, borderaxespad=0.2, labelcolor=INK_2)
    fig.tight_layout()
    OUT.mkdir(exist_ok=True)
    fig.savefig(OUT / "term_recall.png", facecolor=SURFACE)
    print(OUT / "term_recall.png")


# 폰에서 쓰는 경로별 발화당 평균 대기 (보고서 v2). (결과 폴더, 실행 이름, 막대 이름)
PATHS = [
    ("results_z6g4", "whisper-large-v3-turbo+location", "집 서버 Z6 G4 (RTX 2070)\nWhisper turbo + 위치별 받아쓰기 사전"),
    ("results", "whisper-large-v3-turbo+location", "MacBook Pro M1 Max (기준)\nWhisper turbo + 위치별 받아쓰기 사전"),
    ("results_postcorrect", "aws-transcribe+location__post-location-jamo-0.85",
     "Amazon Transcribe (서울)\n위치별 어휘 + 후처리 사전 · 네트워크 포함"),
    ("results", "clova+location-w1", "CLOVA Speech (네이버)\n위치별 키워드 부스팅 · 파일 업로드·네트워크 포함"),
    ("results_webgpu", "webgpu-whisper-large-v3-turbo-q4f16-q4f16+location-guard",
     "맥 브라우저 WebGPU\nWhisper turbo 4비트 + 위치별 받아쓰기 사전"),
    ("results_macmini", "whisper-large-v3-turbo+location", "집 서버 맥미니 (M1 16GB)\nWhisper turbo + 위치별 받아쓰기 사전"),
    ("results_iphone", "iphone-whisper-base-fp16+location-guard", "iPhone 브라우저 WebGPU\nWhisper base · 1개 처리 후 탭 종료"),
]


def latency_chart(paths=PATHS, name="latency_paths.png", highlight=None, subtitle=None, sort=False):
    """highlight: 이 글자가 들어간 막대만 강조색으로 칠하고 '결정'을 붙인다. sort: 대기 시간이 짧은 순서로 위에서부터 (선정 보고서용)."""
    rows = []
    for folder, run, label in paths:
        s = next(r for r in csv.DictReader(open(ROOT / folder / "summary.csv", encoding="utf-8-sig")) if r["run"] == run)
        rows.append((label, float(s["지연_평균(s)"]), float(s["CER(%)"]), int(s["n"])))
    if sort:
        rows.sort(key=lambda r: r[1])

    plt.rcParams.update({"font.family": "Apple SD Gothic Neo", "font.size": 10.5})
    fig, ax = plt.subplots(figsize=(8.6, 4.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ys = range(len(rows))[::-1]
    picked = [bool(highlight and highlight in r[0]) for r in rows]
    ax.barh(list(ys), [r[1] for r in rows], height=0.56, color=[AFTER if p else BEFORE for p in picked], zorder=2)
    for y, (label, sec, cer, n), p in zip(ys, rows, picked):
        note = f"{sec:.2f}초 · CER {cer:.1f}%" if n > 1 else f"{sec:.2f}초 (1개)"
        note += "  · 결정" if p else ""
        ax.text(sec + 0.05, y, note, va="center", color=INK, fontsize=9.5, zorder=3,
                bbox=dict(facecolor=SURFACE, edgecolor="none", pad=1.5))
    ax.axvline(2.0, color=INK_2, lw=1, ls=(0, (4, 3)), zorder=1)
    ax.set_ylim(-0.95, len(rows) - 0.45)
    ax.text(2.03, -0.82, "PWA 기준 2초", color=INK_2, fontsize=9, va="bottom")
    ax.set_yticks(list(ys), [r[0] for r in rows], color=INK, fontsize=9.5)
    ax.set_xlim(0, 3.6)
    ax.set_xticks([0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5], ["0", "0.5", "1", "1.5", "2", "2.5", "3", "3.5초"], color=INK_2)
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    ax.set_title("발화당 평균 대기 시간  짧을수록 좋음", loc="left", color=INK, fontsize=12.5, pad=22)
    ax.text(0, 1.015, subtitle or "평균 7.6초 음성 · 폰→서버 업로드 제외 · CER은 낮을수록 좋음", transform=ax.transAxes, color=INK_2, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(OUT / name, facecolor=SURFACE)
    print(OUT / name)


# 후처리 사전(위치별·자모) threshold별 CER. 기본 팔레트 1~5번 (validate_palette.js 통과, 대비 경고 → 직접 레이블)
POST_SERIES = [
    ("whisper-large-v3-turbo", "Whisper turbo", "#2a78d6"),
    ("qwen3-asr-1.7b", "Qwen3-ASR 1.7B", "#eb6834"),
    ("cohere-transcribe", "Cohere Transcribe", "#1baf7a"),
    ("aws-transcribe+location", "Amazon Transcribe + 위치별 어휘", "#eda100"),
    ("apple-transcriber", "Apple SpeechTranscriber", "#e87ba4"),
]
THRESHOLDS = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]


def postcorrect_chart():
    base = {s["run"]: s for s in csv.DictReader(open(ROOT / "results" / "summary.csv", encoding="utf-8-sig"))}
    post = {s["run"]: s for s in csv.DictReader(open(ROOT / "results_postcorrect" / "summary.csv", encoding="utf-8-sig"))}
    plt.rcParams.update({"font.family": "Apple SD Gothic Neo", "font.size": 10.5})
    fig, ax = plt.subplots(figsize=(8.6, 4.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    xs = list(range(1, len(THRESHOLDS) + 1))
    ends = []
    for run, label, color in POST_SERIES:
        ys = [float(post[f"{run}__post-location-jamo-{t:.2f}"]["CER(%)"]) for t in THRESHOLDS]
        ax.plot(xs, ys, color=color, lw=2, zorder=3, marker="o", ms=5.5, mec=SURFACE, mew=1.5)
        ax.scatter([0], [float(base[run]["CER(%)"])], s=40, color=color, zorder=3, edgecolors=SURFACE, linewidths=1.5)
        ends.append([ys[-1], label, color])
    ends.sort()
    for k in range(1, len(ends)):  # 끝 레이블이 겹치지 않게 최소 간격을 둔다
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + 0.55)
    for y, label, color in ends:
        ax.text(xs[-1] + 0.12, y, label, va="center", color=INK, fontsize=9)
    ax.axvline(4, color=GRID, lw=6, zorder=1)
    ax.set_xticks([0] + xs, ["후처리\n없음"] + [f"{t:.2f}" for t in THRESHOLDS], color=INK_2)
    ax.set_xlim(-0.4, xs[-1] + 1.9)
    ax.set_ylim(2, 13)
    ax.set_yticks(range(2, 14, 2), [f"{v}%" for v in range(2, 14, 2)], color=INK_2)
    ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    ax.set_xlabel("threshold (자모 단위 Jaro-Winkler 유사도)", color=INK_2, fontsize=9.5)
    ax.set_title("후처리 사전 threshold별 CER  낮을수록 좋음", loc="left", color=INK, fontsize=12.5, pad=22)
    ax.text(0, 1.015, "위치별 사전 · 회색 띠는 threshold 0.85 · 왼쪽 점은 후처리 전", transform=ax.transAxes,
            color=INK_2, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(OUT / "postcorrect_threshold.png", facecolor=SURFACE)
    print(OUT / "postcorrect_threshold.png")


# 선정 보고서용: '집 서버'를 '개인 서버'로 쓰고 결정한 Amazon Transcribe를 강조한다.
SELECTION_PATHS = [(f, r, l.replace("집 서버 맥미니", "개인 서버 Mac mini").replace("집 서버", "개인 서버")) for f, r, l in PATHS]


if __name__ == "__main__":
    main()
    latency_chart()
    latency_chart(SELECTION_PATHS, "latency_paths_selection.png", "Amazon Transcribe",
                  "평균 7.6초 음성 · 개인 서버·브라우저는 업로드 제외, 클라우드는 네트워크 포함 · CER은 낮을수록 좋음", sort=True)
    postcorrect_chart()
