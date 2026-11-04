"""엑셀 용어집 → 파이프라인 데이터. 영문 표기의 원본은 레포 루트의 두 엑셀이다.

  경복궁_전각.xlsx  [building] serial_number, name, name_en, name_th, nameId …   (전각 71곳)
  경복궁_용어.xlsx  [용어 목록] No, 분류, 용어, 영문 표기 (작성용), 태국어 표기 …   (용어 231개)

만드는 것
  gunggeum_stt/data/cards.json          카드 메타(type·serial/no·name·name_en·name_th·nameId) — 자막 카드 링크용
  gunggeum_stt/data/translate_terms.csv Amazon Translate 사용자 지정 용어집 (ko,en,th). 한국어 이름마다 한 줄,
                                        '삼도 / 어도'는 둘로, 괄호(한자)는 떼고, 1글자 이름은 빼고, 같은 이름은 전각 우선
                                        (gunggeum-ai-llm-gps/server/export_terms.py 와 같은 규칙)

사용법: python -m gunggeum_stt.build_glossary [--buildings 경복궁_전각.xlsx] [--terms 경복궁_용어.xlsx]
"""
import argparse
import csv
import json
import re
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "data"


def read_sheet(path, sheet):
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)[sheet]
    rows = ws.iter_rows(values_only=True)
    hdr = [str(h).strip() if h is not None else "" for h in next(rows)]
    return [dict(zip(hdr, r)) for r in rows if any(c is not None for c in r)]


def col(row, *names):
    for n in names:
        for k, v in row.items():
            if k.startswith(n) and v not in (None, ""):
                return str(v).strip()
    return ""


def card_metas(buildings_xlsx, terms_xlsx):
    metas = []
    for r in read_sheet(buildings_xlsx, "building"):
        metas.append({"type": "building", "serial": int(float(r["serial_number"])), "name": str(r["name"]).strip(),
                      "name_en": col(r, "name_en"), "name_th": col(r, "name_th"), "nameId": col(r, "nameId")})
    for r in read_sheet(terms_xlsx, "용어 목록"):
        if r.get("No") is None or not r.get("용어"):
            continue
        metas.append({"type": "term", "no": int(float(r["No"])), "name": str(r["용어"]).strip(), "category": col(r, "분류"),
                      "priority": col(r, "우선순위"), "name_en": col(r, "영문 표기"), "name_th": col(r, "태국어 표기")})
    return metas


def card_id(m):
    return f"b-{m['serial']}" if m["type"] == "building" else f"t-{m['no']}"


def terminology_rows(metas):
    names = {}
    cards = {card_id(m): m for m in metas}
    for cid, m in cards.items():
        for p in m["name"].split("/"):
            if len(p := re.sub(r"\(.*?\)", "", p).strip()) > 1:
                names.setdefault(p, set()).add(cid)
    out = []
    for ko, ids in sorted(names.items()):
        ids = {c for c in ids if c.startswith("b-")} or ids
        if len(ids) != 1:
            continue
        m = cards[ids.pop()]
        if m.get("name_en"):
            out.append((ko, m["name_en"], m.get("name_th") or ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buildings", default=str(ROOT / "경복궁_전각.xlsx"))
    ap.add_argument("--terms", default=str(ROOT / "경복궁_용어.xlsx"))
    a = ap.parse_args()
    metas = card_metas(a.buildings, a.terms)
    rows = terminology_rows(metas)
    (DATA / "cards.json").write_text(json.dumps(metas, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(DATA / "translate_terms.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([("ko", "en", "th"), *rows])
    nb = sum(m["type"] == "building" for m in metas)
    print(f"카드 {len(metas)}장(전각 {nb}, 용어 {len(metas) - nb}), 영문 없음 {sum(not m['name_en'] for m in metas)}, "
          f"용어집 {len(rows)}행(태국어 없음 {sum(not th for *_, th in rows)})")


if __name__ == "__main__":
    main()
