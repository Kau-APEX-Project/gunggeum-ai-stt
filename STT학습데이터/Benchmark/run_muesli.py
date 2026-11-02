"""Muesli의 실제 후처리 코드(muesli_matcher/, 고치지 않은 CustomWordMatcher.swift)를 전사 결과에 적용한다.

사용법: python run_muesli.py [결과 폴더]   (기본 results)
→ results_muesli/<실행>__muesli-<location|global>-<threshold>.jsonl
그다음 python evaluate.py results_muesli
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

from postcorrect import RUNS, THRESHOLDS

ROOT = Path(__file__).resolve().parent
BIN = ROOT / ".cache" / "muesli_apply"
OUT = ROOT / "results_muesli"


def build():
    src = ROOT / "muesli_matcher"
    BIN.parent.mkdir(exist_ok=True)
    subprocess.run(["xcrun", "swiftc", "-O", str(src / "CustomWordMatcher.swift"), str(src / "main.swift"),
                    "-o", str(BIN)], check=True)


def main():
    src = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "results")
    if not BIN.exists():
        build()
    refs = {r["id"]: r for r in csv.DictReader(open(ROOT / "data" / "references.csv", encoding="utf-8"))}
    loc_hints = json.loads((ROOT / "data" / "hints_by_location.json").read_text(encoding="utf-8"))
    all_hints = (ROOT / "data" / "hint_terms.txt").read_text(encoding="utf-8").splitlines()
    OUT.mkdir(exist_ok=True)

    lines, rows = [], {}
    for run in RUNS:
        rows[run] = [json.loads(l) for l in (src / f"{run}.jsonl").open(encoding="utf-8")]
        for dic in ["location", "global"]:
            for th in THRESHOLDS:
                for r in rows[run]:
                    terms = loc_hints[refs[r["id"]]["location"]] if dic == "location" else all_hints
                    lines.append(json.dumps({"id": f"{run}|{dic}|{th:.2f}|{r['id']}", "text": r["hyp"],
                                             "terms": terms, "threshold": th}, ensure_ascii=False))
    out = subprocess.run([str(BIN)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
    fixed = {}
    for l in out.stdout.splitlines():
        o = json.loads(l)
        fixed[o["id"]] = o["text"]
    for run in RUNS:
        for dic in ["location", "global"]:
            for th in THRESHOLDS:
                with (OUT / f"{run}__muesli-{dic}-{th:.2f}.jsonl").open("w", encoding="utf-8") as fp:
                    for r in rows[run]:
                        fp.write(json.dumps({**r, "hyp": fixed[f"{run}|{dic}|{th:.2f}|{r['id']}"]},
                                            ensure_ascii=False) + "\n")
    print(f"{len(lines)}줄 처리 → {OUT}")


if __name__ == "__main__":
    main()
