"""results/*.jsonl을 모아 CER·용어 인식률·RTF를 집계한다.

출력
- <결과 폴더>/summary.csv    : 실행(모델+사전 조건)별 요약 (python evaluate.py [결과 폴더], 기본 results)
- results/per_utterance.csv  : 발화별 정답·전사·CER
- results/term_misses.csv    : 실행별로 놓친 용어와 실제 전사된 형태
"""
import csv
import json
import sys
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from metrics import edit_distance, heard_as, normalize, term_groups, term_hits

ROOT = Path(__file__).resolve().parent
RES_DIR = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "results")  # 장비별 결과 폴더
TAGS = ["숫자·연도", "용어 3개 이상", "긴 발화"]


def pct(x):
    return round(100 * x, 2)


def evaluate_run(path, refs):
    rows = [json.loads(l) for l in path.open(encoding="utf-8")]
    per, misses = [], []
    agg = Counter()
    tag_agg = defaultdict(Counter)
    for r in rows:
        ref = refs[r["id"]]
        e_raw = edit_distance(normalize(ref["text"]), normalize(r["hyp"]))
        n_raw = len(normalize(ref["text"]))
        e_num = edit_distance(normalize(ref["text"], True), normalize(r["hyp"], True))
        n_num = len(normalize(ref["text"], True))
        hits = term_hits(ref["text"], r["hyp"], term_groups(ref["terms"], ref["buildings"]))
        agg.update(e_raw=e_raw, n_raw=n_raw, e_num=e_num, n_num=n_num, exact=int(e_num == 0),
                   collapse=int(e_num >= 0.5 * n_num), proc=r["proc_sec"], dur=r["duration"])
        for kind, term, hit in hits:
            agg[f"t_{kind}"] += 1
            agg[f"h_{kind}"] += hit
            if not hit:
                misses.append({"id": r["id"], "kind": kind, "term": term,
                               "heard_as": heard_as(ref["text"], r["hyp"], term), "hyp": r["hyp"]})
        for tag in TAGS:
            if tag in ref["tags"]:
                tag_agg[tag].update(e=e_num, n=n_num)
        per.append({"id": r["id"], "hyp": r["hyp"], "cer": pct(e_num / n_num), "proc_sec": r["proc_sec"]})

    lat = sorted(r["proc_sec"] for r in rows)
    t_all = agg["t_용어"] + agg["t_전각"]
    summary = {
        "n": len(rows),
        "CER(%)": pct(agg["e_num"] / agg["n_num"]),
        "CER_원문(%)": pct(agg["e_raw"] / agg["n_raw"]),
        "완전일치(%)": pct(agg["exact"] / len(rows)),
        "붕괴발화(CER≥50%)": agg["collapse"],
        "용어인식률(%)": pct((agg["h_용어"] + agg["h_전각"]) / t_all) if t_all else None,
        "용어집용어(%)": pct(agg["h_용어"] / agg["t_용어"]) if agg["t_용어"] else None,
        "전각명(%)": pct(agg["h_전각"] / agg["t_전각"]) if agg["t_전각"] else None,
        "RTF": round(agg["proc"] / agg["dur"], 4),
        "지연_평균(s)": round(statistics.mean(lat), 3),
        "지연_p90(s)": round(lat[int(0.9 * (len(lat) - 1))], 3),
        "지연_최대(s)": round(lat[-1], 3),
    }
    for tag in TAGS:
        summary[f"CER_{tag}(%)"] = pct(tag_agg[tag]["e"] / tag_agg[tag]["n"]) if tag_agg[tag]["n"] else None
    return summary, per, misses


def main():
    refs = {r["id"]: r for r in csv.DictReader(open(ROOT / "data" / "references.csv", encoding="utf-8"))}
    runs = sorted(RES_DIR.glob("*.jsonl"))
    summaries, per_all, miss_all = [], defaultdict(dict), []
    for path in runs:
        name = path.stem
        meta_path = RES_DIR / f"{name}.meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        s, per, misses = evaluate_run(path, refs)
        model, _, hint = name.partition("+")
        summaries.append({"run": name, "model": model, "hint": hint or "none",
                          "load_sec": meta.get("load_sec"), **s})
        for p in per:
            per_all[p["id"]][name] = p
        miss_all += [{"run": name, **m} for m in misses]

    if not summaries:
        raise SystemExit("results/*.jsonl 없음")
    with open(RES_DIR / "summary.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=list(summaries[0]))
        w.writeheader()
        w.writerows(summaries)

    names = [p.stem for p in runs]
    with open(RES_DIR / "per_utterance.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["id", "location", "tags", "정답"] + [f"{n}|{c}" for n in names for c in ("전사", "CER")])
        for uid, ref in refs.items():
            row = [uid, ref["location"], ref["tags"], ref["text"]]
            for n in names:
                p = per_all[uid].get(n, {})
                row += [p.get("hyp", ""), p.get("cer", "")]
            w.writerow(row)

    with open(RES_DIR / "term_misses.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=["run", "id", "kind", "term", "heard_as", "hyp"])
        w.writeheader()
        w.writerows(miss_all)

    cols = ["run", "CER(%)", "CER_원문(%)", "용어인식률(%)", "전각명(%)", "RTF", "지연_평균(s)", "지연_p90(s)"]
    print(" | ".join(cols))
    for s in sorted(summaries, key=lambda s: s["CER(%)"]):
        print(" | ".join(str(s[c]) for c in cols))


if __name__ == "__main__":
    main()
