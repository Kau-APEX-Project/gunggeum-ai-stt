"""연도 숫자에 번역 제외 표시를 씌우면 태국어 연도 깨짐이 없어지는지 시험한다 (해설 300문장 중 'NNNN년'이 든 문장).

백엔드 번역 코드(서버 기본 방식)를 그대로 쓰고, 용어를 감싼 뒤 'NNNN년'의 숫자만 <span translate="no">로 한 번 더 감싼다.
백엔드 코드는 바꾸지 않고 이 스크립트 안에서만 바꿔 끼운다.

  python year_protect_eval.py --be <gunggeum-be/api> [--dry]         번역 (--dry 는 글자 수만, 과금 없음)
  python year_protect_eval.py --be <gunggeum-be/api> --score         저장된 번역으로 채점만 (과금 없음)
결과: results_translate/yearprotect_raw.jsonl, yearprotect_summary.csv
비교 기준(태국어, 표시 없음)은 subtitle_translate_eval.py 의 server300_raw_th.jsonl
"""
import argparse
import asyncio
import csv
import json
import os
import re
from pathlib import Path

import notice_translate_eval as N
import subtitle_translate_eval as S
import translate_terms_eval as T

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results_translate"
YEAR = re.compile(r"(\d{4})년")
CUT = re.compile(r"(?:ค|พ)\.(?!ศ)")  # 서기·불기 표기가 '.'에서 잘린 것


TH_MONTHS = ("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม",
             "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม")
YEAR_SPAN = re.compile(r"(\s?)['‘’\"]?<span translate=\"no\">(\d{4})</span>['‘’\"]?")


def put_year(text, lang):
    """번역문의 연도 표시를 그 언어 표기로: 앞뒤 따옴표를 떼고, 태국어는 앞에 'ปี'가 없으면 붙인다(달 이름·ปี/ปีที่/ค.ศ. 뒤는 그대로).
    태국어 'ปี'는 앞 낱말에 붙여 씀: 'ใน ปี 1985' 가 아니라 'ในปี 1985'"""
    def one(m):
        space, y = m.group(1), m.group(2)
        if lang != "th":
            return space + y
        before = text[:m.start()].rstrip()
        if before.endswith(("ปี", "ปีที่", "ค.ศ.", "พ.ศ.")) or before.endswith(TH_MONTHS) or re.search(r"\d$", before):
            return space + y
        glue = bool(re.search(r"[฀-๿]$", before))  # 바로 앞이 태국 글자면 붙여 씀
        return ("" if glue else space) + "ปี " + y
    return YEAR_SPAN.sub(one, text)


def protect(mod, whole=False, post=False):
    """mod.wrap_terms 뒤에 연도 감싸기를 붙인다.
    기본      : 'NNNN년'의 숫자만 감쌈 (1차 시험)
    whole=True: 'NNNN년'을 통째로 숫자 하나로 감싸고, 번역 뒤 put_year 로 그 언어 표기로 바꿈 (2차, 버림)
    post=True : 숫자만 감싸고('년'은 남김), 번역 뒤 put_year 로 따옴표를 떼고 태국어 'ปี'를 붙임 (3차)"""
    orig_wrap, orig_unwrap = mod.wrap_terms, mod.unwrap_terms

    def wrap(text, lang, table):
        text, marks = orig_wrap(text, lang, table)
        rep = r'<span translate="no">\1</span>' if whole else r'<span translate="no">\1</span>년'
        return YEAR.sub(rep, text), marks
    mod.wrap_terms = wrap
    if whole or post:
        orig_translate = mod.AwsTranslator.translate

        async def translate(self, text, lang):
            if self.mode != "notranslate":
                return await orig_translate(self, text, lang)
            text, marks = mod.wrap_terms(text, lang, mod.glossary())
            return orig_unwrap(put_year(await self._call(text, lang, None), lang), marks)
        mod.AwsTranslator.translate = translate


def year_checks(src, out, lang):
    years = YEAR.findall(src)
    kept = sum(y in out for y in years)
    front = bool(re.match(r"\d{4}", out)) and not re.match(r"\d{4}", src)
    no_pi = lang == "th" and any(re.search(rf"(?<!ปี )(?<!ปีที่ ){y}", out) and f"ปี {y}" not in out and f"ปีที่ {y}" not in out
                                 for y in years if y in out)
    return {"years": len(years), "kept": kept, "cut": bool(CUT.search(out)), "front": front, "no_pi": no_pi}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--be", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--whole", action="store_true", help="'NNNN년'을 통째로 감싸고 번역 뒤 언어별 표기로 바꿈")
    ap.add_argument("--post", action="store_true", help="숫자만 감싸고 번역 뒤 따옴표 떼기·태국어 'ปี' 붙이기")
    a = ap.parse_args()
    os.environ.setdefault("TRANSLATE_PROVIDER", "aws")
    for k in ("TRANSLATE_TERM_MODE", "TRANSLATE_TERMS_PATH", "TRANSLATE_TERMINOLOGY"):
        os.environ.pop(k, None)
    items = [it for it in T.load_items() if YEAR.search(it["text"])]
    mod = N.load(Path(a.be))
    protect(mod, a.whole, a.post)
    tag = "yearprotect_whole" if a.whole else "yearprotect_post" if a.post else "yearprotect"
    raw_path = OUT / f"{tag}_raw.jsonl"

    if a.score:
        rows = [json.loads(l) for l in raw_path.open(encoding="utf-8")]
    else:
        real = None
        if not a.dry:
            import boto3
            print("계정", boto3.client("sts").get_caller_identity()["Account"], flush=True)
            real = boto3.client("translate", region_name="ap-northeast-2")
        client = N.Client(real, {})
        tr = mod.AwsTranslator(client)
        rows = []
        for lang in ("th", "en"):
            for it in items:
                client.sent.clear()
                out = asyncio.run(tr.translate(it["text"], lang))
                rows.append({"id": it["id"], "lang": lang, "src": it["text"], "sent": client.sent[-1], "out": out})
        print(f"문장 {len(items)}개 × 2언어 | 보낸 글자 {client.chars}자 (약 {client.chars * 15 / 1e6:.2f}달러)"
              + (" — 세기만 함" if a.dry else ""), flush=True)
        if a.dry:
            return
        T.write_jsonl(raw_path, rows)

    base = {r["id"]: r["out"] for r in map(json.loads, (OUT / "server300_raw_th.jsonl").open(encoding="utf-8"))}
    ids = {it["id"] for it in items}
    summary = []
    for lang in ("th", "en"):
        table = S.expected(Path(a.be), lang)
        sets = [("연도 보호", {r["id"]: {"out": r["out"], "sent": r["sent"]} for r in rows if r["lang"] == lang})]
        if lang == "th":
            sets.insert(0, ("지금 (보호 없음)", {i: {"out": base[i], "sent": ""} for i in ids}))
        for name, raw in sets:
            c, _ = S.score([it for it in items], raw, table, mod, lang)
            yc = [year_checks(it["text"], raw[it["id"]]["out"], lang) for it in items]
            summary.append({
                "언어": lang, "방식": name, "문장": len(items),
                "연도 수": sum(y["years"] for y in yc), "연도 보존": sum(y["kept"] for y in yc),
                "서기 표기가 잘린 문장": sum(y["cut"] for y in yc), "연도가 문장 앞으로 간 문장": sum(y["front"] for y in yc),
                "연도 앞 ปี 빠진 문장": sum(y["no_pi"] for y in yc) if lang == "th" else "",
                "용어 일치(묶음 제외)": f'{c["묶음 표기를 뺀 항목", "ok"]}/{c["묶음 표기를 뺀 항목", "n"]}',
                "한글 남은 문장": c["한글이 남은 문장"], "표시 남은 문장": c["표시가 남은 문장"]})
    with (OUT / f"{tag}_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    for r in summary:
        print(r)


if __name__ == "__main__":
    main()
