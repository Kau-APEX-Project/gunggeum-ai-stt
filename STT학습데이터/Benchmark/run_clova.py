"""NAVER CLOVA Speech 장문 인식(sync, 로컬 파일 업로드)으로 녹음 180개를 전사한다 (ko-KR).

사용법
  python run_clova.py --check                         # 키 확인: 1개만 보내 결과를 출력 (5원)
  python run_clova.py                                 # 사전 없음
  python run_clova.py --hint location --weight 3      # 위치별 키워드 부스팅 (가중치 1~5)
  python run_clova.py --hint loo --weight 3           # 그 문장을 뺀 위치별 키워드 부스팅
  python run_clova.py --hint global --weight 3        # 전체 사전 336개 키워드 부스팅

요청 1건마다 15초 단위로 과금된다(Basic 플랜 장문 인식 15초당 5원, 발화 1개 = 1단위). 화자 인식은 꺼서 추가 요금이 없다.
키는 ~/.config/gungeum/clova.env 에서 읽는다 (CLOVA_INVOKE_URL=..., CLOVA_SECRET=...). 키 값은 출력하지 않는다.
발화당 대기 = WAV(16kHz) 업로드부터 결과 수신까지 걸린 시간(네트워크 포함).
"""
import argparse
import csv
import json
import platform
import re
import time
from pathlib import Path

import requests

from metrics import num_to_korean
from postcorrect import live_post

ROOT = Path(__file__).resolve().parent
WAV_DIR = ROOT / ".cache" / "wav16k"
ENV = Path.home() / ".config" / "gungeum" / "clova.env"


def load_env():
    if not ENV.exists():
        raise SystemExit(f"{ENV} 가 없습니다. CLOVA_INVOKE_URL과 CLOVA_SECRET을 넣어 주세요.")
    env = dict(l.split("=", 1) for l in ENV.read_text(encoding="utf-8").splitlines() if "=" in l)
    url, key = env.get("CLOVA_INVOKE_URL", "").strip().rstrip("/"), env.get("CLOVA_SECRET", "").strip()
    if not url.startswith("https://") or not key:
        raise SystemExit(f"{ENV} 의 CLOVA_INVOKE_URL(https://...)이나 CLOVA_SECRET이 비어 있습니다.")
    return url, key


def boost_words(terms):
    """부스팅 단어: 숫자는 한글 읽기, 한글·영문·숫자·공백 외 문자는 뺀다. 최대 1,000개."""
    out = []
    for t in terms:
        t = re.sub(r"\s+", " ", re.sub(r"[^가-힣A-Za-z0-9 ]", " ", num_to_korean(t))).strip()
        if len(t) >= 2 and t not in out:
            out.append(t)
    return out[:1000]


def recognize(session, url, key, wav: Path, words, weight):
    params = {"language": "ko-KR", "completion": "sync", "wordAlignment": False, "fullText": True,
              "diarization": {"enable": False}}
    if words:
        params["boostings"] = [{"words": ",".join(words), "weight": weight}]
    for attempt in range(3):
        t = time.perf_counter()
        with wav.open("rb") as f:
            r = session.post(f"{url}/recognizer/upload", headers={"X-CLOVASPEECH-API-KEY": key},
                             files={"media": (wav.name, f, "audio/wav")},
                             data={"params": json.dumps(params, ensure_ascii=False)}, timeout=60)
        dt = time.perf_counter() - t
        if r.status_code == 200 and r.json().get("result") == "COMPLETED":
            return r.json().get("text", "").strip(), dt
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(2 * (attempt + 1))
            continue
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:300]
        raise SystemExit(f"CLOVA 오류 {r.status_code}: {body}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hint", choices=["none", "location", "loo", "global"], default="none")
    ap.add_argument("--weight", type=float, default=3.0, help="키워드 부스팅 가중치 (1~5)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--check", action="store_true", help="1개만 보내 키·형식 확인")
    ap.add_argument("--out", default="results", help="결과 폴더")
    ap.add_argument("--post", action="store_true", help="받아쓰기 직후 후처리 사전(위치별·자모·0.85) 적용")
    args = ap.parse_args()

    url, key = load_env()
    session = requests.Session()
    refs = list(csv.DictReader(open(ROOT / "data" / "references.csv", encoding="utf-8")))
    loc_hints = json.loads((ROOT / "data" / "hints_by_location.json").read_text(encoding="utf-8"))
    loo_hints = json.loads((ROOT / "data" / "hints_loo.json").read_text(encoding="utf-8"))
    all_hints = (ROOT / "data" / "hint_terms.txt").read_text(encoding="utf-8").splitlines()
    terms_for = lambda r: {"none": [], "location": loc_hints[r["location"]], "loo": loo_hints[r["id"]],
                           "global": all_hints}[args.hint]

    if args.check:
        r = refs[0]
        hyp, dt = recognize(session, url, key, WAV_DIR / f"{r['id']}.wav", boost_words(terms_for(r)), args.weight)
        print(f"정답: {r['text']}\n전사: {hyp}\n{dt:.2f}초")
        return

    if args.limit:
        refs = refs[:args.limit]
    name = "clova" + ("" if args.hint == "none" else f"+{args.hint}-w{args.weight:g}") + ("+post" if args.post else "")
    res_dir = ROOT / args.out
    res_dir.mkdir(exist_ok=True)
    out_path = res_dir / f"{name}.jsonl"
    done = {json.loads(l)["id"] for l in out_path.open(encoding="utf-8")} if out_path.exists() else set()

    recognize(session, url, key, WAV_DIR / f"{refs[0]['id']}.wav", [], args.weight)  # 워밍업(연결)
    meta = {"model": "clova", "hint": args.hint, "weight": args.weight, "device": "CLOVA Speech 장문 인식 sync",
            "machine": platform.platform()}
    (res_dir / f"{name}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    with out_path.open("a", encoding="utf-8") as fp:
        for i, r in enumerate(refs, 1):
            if r["id"] in done:
                continue
            hyp, dt = recognize(session, url, key, WAV_DIR / f"{r['id']}.wav", boost_words(terms_for(r)), args.weight)
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


if __name__ == "__main__":
    main()
