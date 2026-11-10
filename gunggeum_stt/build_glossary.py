"""엑셀 용어집 → 파이프라인 데이터. 영문 표기의 원본은 레포 루트의 두 엑셀이다.

  경복궁_전각.xlsx  [building] serial_number, name, name_en, name_th, nameId …   (전각 71곳)
  경복궁_용어.xlsx  [용어 목록] No, 분류, 용어, 영문 표기 (작성용), 태국어 표기 …   (용어 231개)

만드는 것
  gunggeum_stt/data/cards.json          카드 메타(type·serial/no·name·name_en·name_th·nameId) — 자막 카드 링크용
  gunggeum_stt/data/translate_terms.csv Amazon Translate 사용자 지정 용어집 (ko,en,th). 한국어 이름마다 한 줄,
                                        '삼도 / 어도'는 둘로, 괄호(한자)는 떼고, 1글자 이름은 빼고, 같은 이름은 전각 우선
                                        (gunggeum-ai-llm-gps/server/export_terms.py 와 같은 규칙)
                                        + 아래 일반 단어 목록을 끝에 합침

일반 단어 목록 (손으로 관리)
  gunggeum_stt/data/translate_plain_words.csv  카드는 아니지만 번역이 자주 틀리는 일반 단어 (ko,en,th).
                                        괄호 풀이 없이 일반 영어로 적는다(예: 왕비 → the queen). 카드 목록(cards.json)에는 들어가지 않는다.
                                        표기가 없는 언어 칸은 비워 둔다(백엔드는 빈 칸이면 그 언어에서 바꾸지 않음).
                                        1글자 단어(왕)는 백엔드가 뒤가 조사이거나 낱말이 거기서 끝날 때만 바꾼다
                                        ('왕이'·'왕의'는 바꾸고 '왕실'·'왕권'은 그대로, gunggeum-be PR #7).
                                        띄어쓰기가 든 단어는 넣지 않는다(백엔드가 읽지 않음).

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


def plain_rows(path=None):
    """일반 단어 목록 (ko, en, th). 파일이 없으면 빈 목록"""
    path = Path(path) if path else DATA / "translate_plain_words.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return [(r["ko"].strip(), r["en"].strip(), (r.get("th") or "").strip()) for r in csv.DictReader(f)]


def all_rows(metas):
    """translate_terms.csv 에 쓰는 행: 카드 표기(terminology_rows) + 일반 단어, 한국어 순"""
    rows = terminology_rows(metas)
    taken = {ko for ko, *_ in rows}
    for ko, en, th in plain_rows():
        if not ko or " " in ko or not en:
            raise ValueError(f"일반 단어는 띄어쓰기가 없고 영어 표기가 있어야 함: {ko!r}")
        if ko in taken:
            raise ValueError(f"일반 단어가 카드 이름과 겹침: {ko}")
        taken.add(ko)
        rows.append((ko, en, th))
    return sorted(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buildings", default=str(ROOT / "경복궁_전각.xlsx"))
    ap.add_argument("--terms", default=str(ROOT / "경복궁_용어.xlsx"))
    a = ap.parse_args()
    metas = card_metas(a.buildings, a.terms)
    rows = all_rows(metas)
    (DATA / "cards.json").write_text(json.dumps(metas, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(DATA / "translate_terms.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([("ko", "en", "th"), *rows])
    nb = sum(m["type"] == "building" for m in metas)
    print(f"카드 {len(metas)}장(전각 {nb}, 용어 {len(metas) - nb}), 영문 없음 {sum(not m['name_en'] for m in metas)}, "
          f"용어집 {len(rows)}행(태국어 없음 {sum(not th for *_, th in rows)})")


if __name__ == "__main__":
    main()
