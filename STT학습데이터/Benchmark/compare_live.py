"""실시간 재측정(results_live)과 저장된 전사에 후처리를 적용한 값(results_postcorrect)을 비교한다.

먼저 python evaluate.py results_live 를 돌려 summary.csv를 만들어 둔다.
→ results_live/compare.csv
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIVE = ROOT / "results_live"
PAIRS = {  # 실시간 실행 이름 -> 저장된 전사 실행 이름 (후처리 전)
    "whisper-large-v3-turbo+post": "whisper-large-v3-turbo",
    "whisper-large-v3-turbo+location+post": "whisper-large-v3-turbo+location",
    "qwen3-asr-1.7b+post": "qwen3-asr-1.7b",
    "qwen3-asr-0.6b+location+post": "qwen3-asr-0.6b+location",
    "cohere-transcribe+post": "cohere-transcribe",
    "apple-transcriber+post": "apple-transcriber",
    "apple-dictation+post": "apple-dictation",
    "aws-transcribe+post": "aws-transcribe",
    "aws-transcribe+location+post": "aws-transcribe+location",
}


def summary(folder):
    return {r["run"]: r for r in csv.DictReader(open(ROOT / folder / "summary.csv", encoding="utf-8-sig"))}


def main():
    live, post = summary("results_live"), summary("results_postcorrect")
    rows = []
    for run, cached in PAIRS.items():
        if run not in live:
            continue
        old = {json.loads(l)["id"]: json.loads(l)["hyp"] for l in (ROOT / "results" / f"{cached}.jsonl").open(encoding="utf-8")}
        new = [json.loads(l) for l in (LIVE / f"{run}.jsonl").open(encoding="utf-8")]
        same = sum(r["hyp_raw"] == old[r["id"]] for r in new)
        c = post[f"{cached}__post-location-jamo-0.85"]
        l = live[run]
        rows.append({"구성": run, "저장 전사 CER(%)": c["CER(%)"], "실시간 CER(%)": l["CER(%)"],
                     "저장 전사 전각명(%)": c["전각명(%)"], "실시간 전각명(%)": l["전각명(%)"],
                     "저장 전사 용어(%)": c["용어인식률(%)"], "실시간 용어(%)": l["용어인식률(%)"],
                     "실시간 평균 대기(s)": l["지연_평균(s)"], "실시간 p90 대기(s)": l["지연_p90(s)"],
                     "STT 전사가 같은 발화": f"{same}/{len(new)}"})
    with open(LIVE / "compare.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(" | ".join(str(v) for v in r.values()))


if __name__ == "__main__":
    main()
