"""공지 예문(data/notice_samples.jsonl)을 백엔드의 번역 코드로 영어·태국어로 번역하고 기계적으로 채점한다.

사용법
  python notice_translate_eval.py --be <gunggeum-be/api> [--old <고치기 전 api 폴더>] [--dry]
    --dry  Amazon Translate 를 부르지 않고 보낼 글자 수만 센다 (과금 없음)
  AWS 는 환경 변수 AWS_PROFILE 의 계정을 쓴다 (팀 계정 gungeum-team). 요금은 보낸 글자 수 기준.

결과
  results_notice/raw_<판>.jsonl      공지·언어·제목/본문별 원문, 번역기에 보낸 글, 번역 결과
  results_notice/summary.csv         판·언어별 채점 합계
  results_notice/per_notice.csv      공지별 채점

채점 항목
  용어 표기   원문의 용어(용어 표에 있고 그 언어 표기가 있는 것)가 번역문에 카드 표기 그대로 나왔는가
  한글 남음   번역문에 한글이 남았는가
  표시 남음   번역 제외 표시(<span …>)·자리표시자(GGT0)·HTML 문자(&amp; 등)가 남았는가
  시각·전화·주소  HH:MM, 전화번호, URL 이 그대로 있는가
  줄 수       줄바꿈 개수가 원문과 같은가
"""
import argparse
import asyncio
import csv
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results_notice"
LANGS = ("en", "th")
TIME = re.compile(r"\d{1,2}:\d{2}")
PHONE = re.compile(r"\d{2,3}-\d{3,4}-\d{4}")
URL = re.compile(r"https?://[A-Za-z0-9./_\-?=&%]+")
HANGUL = re.compile(r"[가-힣]+")
MARKUP = re.compile(r"<\s*/?\s*span|translate\s*=|GGT\d|&(?:amp|lt|gt|quot|apos|#\d+);", re.I)


def load(root):
    """root(api 폴더)의 app.live.translate 를 다른 판과 섞이지 않게 불러온다"""
    sys.path.insert(0, str(root))
    for k in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
        del sys.modules[k]
    import app.live.translate as m
    sys.path.pop(0)
    return m


class Client:
    """같은 글·같은 언어는 한 번만 보내고, 보낸 글자 수를 센다"""

    def __init__(self, real, cache):
        self.real, self.cache, self.chars, self.calls, self.sent = real, cache, 0, 0, []

    def translate_text(self, **kw):
        key = (kw["TargetLanguageCode"], kw["Text"])
        self.sent.append(kw["Text"])
        if key not in self.cache:
            self.chars += len(kw["Text"])
            self.calls += 1
            self.cache[key] = kw["Text"] if self.real is None else self.real.translate_text(**kw)["TranslatedText"]
        return {"TranslatedText": self.cache[key]}


def expected_terms(new, table, text, lang):
    """원문의 용어 → 그 언어 카드 표기 목록 (고친 판의 나누기 규칙으로 찾음)"""
    return [table[t][lang] for _h, t, _r in new._each_term(text, new.term_table(lang, table)) if t]


def score(src, out, notations):
    low = out.lower()
    need = {}
    for n in notations:
        need[n] = need.get(n, 0) + 1
    hit = sum(min(c, low.count(n.lower())) for n, c in need.items())
    urls = URL.findall(src)
    return {
        "terms": len(notations), "terms_hit": hit,
        "missed": [n for n, c in need.items() if low.count(n.lower()) < c],
        "hangul": HANGUL.findall(out), "markup": MARKUP.findall(out),
        "times": len(TIME.findall(src)), "times_kept": sum(t in out for t in TIME.findall(src)),
        "phones": len(PHONE.findall(src)), "phones_kept": sum(p in out for p in PHONE.findall(src)),
        "urls": len(urls), "urls_kept": sum(u.rstrip(".") in out for u in urls),
        "lines_same": src.count("\n") == out.count("\n"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--be", required=True, help="고친 판: gunggeum-be/api 폴더")
    ap.add_argument("--old", help="고치기 전 판의 api 폴더 (비교용)")
    ap.add_argument("--dry", action="store_true", help="번역기를 부르지 않고 글자 수만 셈")
    args = ap.parse_args()
    os.environ.setdefault("TRANSLATE_PROVIDER", "aws")
    for k in ("TRANSLATE_TERM_MODE", "TRANSLATE_TERMS_PATH", "TRANSLATE_TERMINOLOGY"):
        os.environ.pop(k, None)  # 서버 기본값(번역 제외 표시 방식, 저장소의 용어 표)

    notices = [json.loads(l) for l in (ROOT / "data" / "notice_samples.jsonl").open(encoding="utf-8")]
    real = None
    if not args.dry:
        import boto3
        real = boto3.client("translate", region_name="ap-northeast-2")
    cache = {}
    new = load(args.be)
    table = new.glossary()
    versions = [("new", args.be)] + ([("old", args.old)] if args.old else [])
    OUT.mkdir(exist_ok=True)
    summary, per = [], []
    total_chars = 0
    for name, root in versions:
        mod = load(root)
        client = Client(real, cache)
        tr = mod.AwsTranslator(client)
        rows = []
        agg = {lang: dict(terms=0, terms_hit=0, hangul=0, markup=0, times=0, times_kept=0, phones=0, phones_kept=0,
                          urls=0, urls_kept=0, lines_diff=0) for lang in LANGS}
        for n in notices:
            for lang in LANGS:
                for field in ("title", "body"):
                    src = n[field]
                    client.sent.clear()
                    out = asyncio.run(tr.translate(src, lang))
                    s = score(src, out, expected_terms(new, table, src, lang))
                    rows.append({"id": n["id"], "type": n["type"], "lang": lang, "field": field, "src": src,
                                 "sent": client.sent[-1], "out": out, **s})
                    a = agg[lang]
                    for k in ("terms", "terms_hit", "times", "times_kept", "phones", "phones_kept", "urls", "urls_kept"):
                        a[k] += s[k]
                    a["hangul"] += bool(s["hangul"])
                    a["markup"] += bool(s["markup"])
                    a["lines_diff"] += not s["lines_same"]
                    per.append({"판": name, "id": n["id"], "언어": lang, "칸": field, "용어": s["terms"], "용어 일치": s["terms_hit"],
                                "빠진 표기": " | ".join(s["missed"]), "남은 한글": " ".join(s["hangul"]),
                                "남은 표시": " ".join(s["markup"]), "줄 수 같음": s["lines_same"]})
        total_chars += client.chars
        if not args.dry:
            with (OUT / f"raw_{name}.jsonl").open("w", encoding="utf-8") as fp:
                for r in rows:
                    fp.write(json.dumps(r, ensure_ascii=False) + "\n")
        for lang, a in agg.items():
            summary.append({"판": name, "언어": lang, "용어": a["terms"], "용어 일치": a["terms_hit"],
                            "용어 일치율(%)": round(100 * a["terms_hit"] / a["terms"], 1) if a["terms"] else None,
                            "한글 남은 칸": a["hangul"], "표시 남은 칸": a["markup"],
                            "시각": f'{a["times_kept"]}/{a["times"]}', "전화": f'{a["phones_kept"]}/{a["phones"]}',
                            "주소": f'{a["urls_kept"]}/{a["urls"]}', "줄 수 다른 칸": a["lines_diff"],
                            "새로 보낸 글자": client.chars, "새로 부른 횟수": client.calls})
    print(f"보낸 글자 합계 {total_chars}자 (100만 자당 15달러 기준 약 {total_chars * 15 / 1e6:.2f}달러)" + (" — 세기만 함" if args.dry else ""))
    if args.dry:
        return
    for name, rows in (("summary.csv", summary), ("per_notice.csv", per)):
        with open(OUT / name, "w", newline="", encoding="utf-8-sig") as fp:
            w = csv.DictWriter(fp, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    for r in summary:
        print(r)


if __name__ == "__main__":
    main()
