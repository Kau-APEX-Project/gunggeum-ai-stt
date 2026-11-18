"""해설 문장 300개를 백엔드의 번역 코드(서버 기본 방식)로 번역하고 용어 표기 일치율을 센다.

사용법
  python subtitle_translate_eval.py --be <gunggeum-be/api> --lang th [--dry]
    --dry  Amazon Translate 를 부르지 않고 보낼 글자 수와 채점 항목 수만 센다 (과금 없음)
  python subtitle_translate_eval.py --be <gunggeum-be/api> --lang th --score   저장된 번역으로 채점만 다시 (과금 없음)
  AWS 는 환경 변수 AWS_PROFILE 의 계정을 쓴다 (팀 계정 gungeum-team). 요금은 보낸 글자 수 기준(100만 자당 15달러).

채점
  항목은 translate_terms_eval.py 와 같다: 엑셀(경복궁_STT_발화문_300.xlsx)에 적힌 문장별 포함 용어·전각 이름 772개.
  기대 표기는 백엔드 용어 표(api/data/translate_terms.csv)의 그 언어 칸. 번역문에 그대로 들어 있으면 일치.
  묶음 표기(영문 이름에 / 나 and 가 든 것)는 서버가 바꿔 넣지 않으므로, 뺀 값과 전체 값을 함께 낸다.

결과 (results_translate/)
  server300_raw_<언어>.jsonl      {id, src, sent(번역기에 보낸 글), out(서버 코드가 돌려준 번역)}
  server300_scored_<언어>.jsonl   문장별 항목과 일치 여부
  server300_summary_<언어>.csv    집계
"""
import argparse
import asyncio
import collections
import csv
import json
import os
import re
from pathlib import Path

import notice_translate_eval as N
import translate_terms_eval as T

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results_translate"
HANGUL = re.compile(r"[가-힣]+")


def expected(be: Path, lang: str) -> dict[str, dict]:
    """백엔드 용어 표의 모든 행: {한국어: {en, 그 언어 표기}}"""
    rows = csv.DictReader((be / "data" / "translate_terms.csv").open(encoding="utf-8-sig"))
    return {r["ko"].strip(): {"en": (r["en"] or "").strip(), "to": (r.get(lang) or "").strip()} for r in rows}


def score(items, raw, table, mod, lang):
    c, rows = collections.Counter(), []
    for it in items:
        out = raw[it["id"]]["out"]
        ex = []
        for e in it["expect"]:
            row = table.get(e["ko"]) if e["ko"] else None
            if not row or not row["to"]:
                continue
            ok = T.norm(row["to"]) in T.norm(out)
            bundle = mod.is_bundle(row["en"])
            att = "용어 뒤에 조사가 붙음" if e["attached"] == "조사 붙음" else "용어 뒤가 띄어쓰기·문장부호"
            keys = ["전체", T.KIND[e["kind"]], att] + ([] if bundle else ["묶음 표기를 뺀 항목"])
            for k in keys:
                c[k, "n"] += 1
                c[k, "ok"] += ok
            ex.append({"kind": e["kind"], "ko": e["ko"], "to": row["to"], "bundle": bundle, "attached": e["attached"], "ok": ok})
        hangul, markup = HANGUL.findall(out), N.MARKUP.findall(out)
        c["한글이 남은 문장"] += bool(hangul)
        c["표시가 남은 문장"] += bool(markup)
        rows.append({"id": it["id"], "ko": it["text"], "sent": raw[it["id"]]["sent"], lang: out,
                     "hangul": hangul, "markup": markup, "expect": ex})
    return c, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--be", required=True, help="gunggeum-be/api 폴더")
    ap.add_argument("--lang", required=True, choices=("en", "th"))
    ap.add_argument("--dry", action="store_true", help="번역기를 부르지 않고 글자 수만 셈")
    ap.add_argument("--score", action="store_true", help="저장된 번역으로 채점만 다시 함")
    a = ap.parse_args()
    be, lang = Path(a.be), a.lang
    os.environ.setdefault("TRANSLATE_PROVIDER", "aws")
    for k in ("TRANSLATE_TERM_MODE", "TRANSLATE_TERMS_PATH", "TRANSLATE_TERMINOLOGY"):
        os.environ.pop(k, None)  # 서버 기본값(번역 제외 표시 방식, 저장소의 용어 표)

    items = T.load_items()
    mod = N.load(be)
    table = expected(be, lang)
    raw_path = OUT / f"server300_raw_{lang}.jsonl"

    if a.score:
        raw = {r["id"]: r for r in map(json.loads, raw_path.open(encoding="utf-8"))}
    else:
        real = None
        if not a.dry:
            import boto3
            print("계정", boto3.client("sts").get_caller_identity()["Account"], flush=True)
            real = boto3.client("translate", region_name="ap-northeast-2")
        client = N.Client(real, {})
        tr = mod.AwsTranslator(client)
        raw = {}
        for it in items:
            client.sent.clear()
            out = asyncio.run(tr.translate(it["text"], lang))
            raw[it["id"]] = {"id": it["id"], "src": it["text"], "sent": client.sent[-1], "out": out}
        print(f"문장 {len(items)}개 | 보낸 글자 {client.chars}자 (약 {client.chars * 15 / 1e6:.2f}달러) | 부른 횟수 {client.calls}"
              + (" — 세기만 함" if a.dry else ""), flush=True)
        if not a.dry:
            T.write_jsonl(raw_path, raw.values())

    c, rows = score(items, raw, table, mod, lang)
    keys = ("묶음 표기를 뺀 항목", "전체", "용어", "전각 이름", "용어 뒤가 띄어쓰기·문장부호", "용어 뒤에 조사가 붙음")
    for k in keys:
        n = c[k, "n"]
        print(f"{k}: {c[k, 'ok']}/{n}" + ("" if a.dry or not n else f" = {100 * c[k, 'ok'] / n:.1f}%"))
    if a.dry:
        return
    print("한글이 남은 문장", c["한글이 남은 문장"], "| 표시가 남은 문장", c["표시가 남은 문장"])
    T.write_jsonl(OUT / f"server300_scored_{lang}.jsonl", rows)
    with (OUT / f"server300_summary_{lang}.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["구분", "항목 수", "일치", "일치율(%)"])
        for k in keys:
            w.writerow([k, c[k, "n"], c[k, "ok"], round(100 * c[k, "ok"] / c[k, "n"], 1)])
        w.writerow(["한글이 남은 문장 (300문장 중)", 300, c["한글이 남은 문장"], ""])
        w.writerow(["표시가 남은 문장 (300문장 중)", 300, c["표시가 남은 문장"], ""])


if __name__ == "__main__":
    main()
