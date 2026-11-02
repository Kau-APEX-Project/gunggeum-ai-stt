"""Apple 음성인식(apple_stt)을 run_asr.py와 같은 형식으로 돌린다.

사용법: python run_apple.py <sf-ondevice|sf-server|dictation|transcriber> [--hint none|location|global]
처음 실행하면 apple_stt/apple_stt.swift를 .cache/AppleSTT.app으로 빌드한다.
SFSpeechRecognizer는 macOS 음성 인식 권한이 필요해서, 명령줄 바이너리가 아니라 앱 번들로 `open`해야
권한 창이 뜬다(처음 한 번 '허용'을 눌러야 한다).
"""
import argparse
import csv
import json
import platform
import shutil
import subprocess
import time
from pathlib import Path

from postcorrect import live_post

ROOT = Path(__file__).resolve().parent
APP = ROOT / ".cache" / "AppleSTT.app"
WAV_DIR = ROOT / ".cache" / "wav16k"
RES_DIR = ROOT / "results"


def build():
    src = ROOT / "apple_stt"
    (APP / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    shutil.copy(src / "Info.plist", APP / "Contents" / "Info.plist")
    subprocess.run(["xcrun", "swiftc", "-O", "-parse-as-library", str(src / "apple_stt.swift"),
                    "-o", str(APP / "Contents" / "MacOS" / "apple_stt")], check=True)
    subprocess.run(["codesign", "-s", "-", "--force", str(APP)], check=True, capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["sf-ondevice", "sf-server", "dictation", "transcriber"])
    ap.add_argument("--hint", choices=["none", "location", "global"], default="none")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="results", help="결과 폴더")
    ap.add_argument("--post", action="store_true", help="받아쓰기 직후 후처리 사전(위치별·자모·0.85) 적용")
    args = ap.parse_args()
    RES_DIR = ROOT / args.out
    if not APP.exists():
        build()

    refs = list(csv.DictReader(open(ROOT / "data" / "references.csv", encoding="utf-8")))
    if args.limit:
        refs = refs[:args.limit]
    loc_hints = json.loads((ROOT / "data" / "hints_by_location.json").read_text(encoding="utf-8"))
    all_hints = (ROOT / "data" / "hint_terms.txt").read_text(encoding="utf-8").splitlines()
    terms_for = lambda r: {"none": [], "location": loc_hints[r["location"]], "global": all_hints}[args.hint]

    name = f"apple-{args.mode}" + ("" if args.hint == "none" else f"+{args.hint}") + ("+post" if args.post else "")
    RES_DIR.mkdir(exist_ok=True)
    tsv = ROOT / ".cache" / f"{name}.tsv"
    tsv.write_text("".join(f"{r['id']}\t{WAV_DIR / (r['id'] + '.wav')}\t{'|'.join(terms_for(r))}\n"
                           for r in refs), encoding="utf-8")
    raw = ROOT / ".cache" / f"{name}.raw.jsonl"
    log = ROOT / ".cache" / f"{name}.log"
    raw.unlink(missing_ok=True)
    subprocess.run(["open", "-W", "-n", str(APP), "--stdout", str(log), "--stderr", str(log),
                    "--args", args.mode, str(tsv), str(raw)], check=True)
    print(log.read_text(encoding="utf-8").strip()[-2000:])
    if not raw.exists():
        raise SystemExit(f"{name}: 결과 없음")

    dur = {r["id"]: float(r["duration"]) for r in refs}
    locs = {r["id"]: r["location"] for r in refs}
    with open(RES_DIR / f"{name}.jsonl", "w", encoding="utf-8") as fp:
        for line in raw.open(encoding="utf-8"):
            row = json.loads(line)
            hyp, sec = row["hyp"], row["proc_sec"]
            if args.post:  # Apple 엔진 결과를 받은 직후 후처리 (시간 합산)
                t = time.perf_counter()
                hyp, _ = live_post(loc_hints[locs[row["id"]]], row["hyp"])
                sec += time.perf_counter() - t
            fp.write(json.dumps({"id": row["id"], "hyp": hyp, "proc_sec": round(sec, 4), "duration": dur[row["id"]],
                                 **({"hyp_raw": row["hyp"]} if args.post else {})}, ensure_ascii=False) + "\n")
    meta = {"model": f"apple-{args.mode}", "hint": args.hint, "device": "Apple Speech framework",
            "machine": platform.platform()}
    (RES_DIR / f"{name}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    for p in (tsv, raw, log):
        p.unlink()


if __name__ == "__main__":
    main()
