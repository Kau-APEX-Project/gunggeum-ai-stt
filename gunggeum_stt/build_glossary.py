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
  gunggeum_stt/data/translate_plain_words.csv  카드는 아니지만 번역이 자주 틀리는 단어 (ko,en,th). 카드 목록(cards.json)에는 들어가지 않는다.
                                        흔한 낱말은 일반 영어로(왕비 → the queen), 사람·건물 이름은 용어 엑셀처럼 '로마자 (설명)'으로 적는다.
                                        용어에 다른 말이 붙은 이름(광화문광장·경복궁역)도 여기 넣는다. 백엔드는 긴 이름부터 맞추므로
                                        넣지 않으면 앞부분만 용어로 바뀐다('Gwanghwamun (Palace Gate) Square'). gunggeum-be 이슈 16번 6번.
                                        표기가 없는 언어 칸은 비워 둔다(백엔드는 빈 칸이면 그 언어에서 바꾸지 않음).
                                        1글자 단어(왕)는 백엔드가 뒤가 조사이거나 낱말이 거기서 끝날 때만 바꾼다
                                        ('왕이'·'왕의'는 바꾸고 '왕실'·'왕권'은 그대로, gunggeum-be PR #7).
                                        띄어쓰기가 든 단어는 넣지 않는다(백엔드가 읽지 않음).

묶음 표기를 나눈 이름 (손으로 관리)
  gunggeum_stt/data/translate_split_names.csv  카드 표기가 'Seja / Wangseja (Crown Prince)'처럼 이름 여럿을 묶은 것을
                                        한국어 이름마다 제 이름만 적은 것 (ko,en,th). 백엔드는 묶음 표기를 감싸지 않고 건너뛰어,
                                        번역기가 이름을 영어로 남기거나('Seja') 오역했다('공포' → personal fears).
                                        용어집에서는 묶음 행 대신 이 표기를 쓰고, 카드(cards.json)는 엑셀 표기 그대로 둔다.
                                        나눈 이름은 원래 카드 표기에 든 이름이어야 한다. 괄호 설명은 그 이름의 몫만 남긴다.
                                        창건·중건·재건·복원·소실은 나누지 않는다: 해설에서 '복원됐습니다'처럼 동사로 쓰여
                                        지금은 번역기가 'was restored'로 잘 옮기는데, 나누면 'Bogwon (Restoration)'으로 바뀐다.

공지에서 감싸지 않을 용어 (손으로 관리, 이 스크립트는 읽지 않음)
  gunggeum_stt/data/notice_plain_words.csv  용어와 글자가 같지만 공지에서는 일반 낱말로 쓰이는 것 (ko, 공지에서 쓰이는 뜻).
                                        백엔드가 공지 번역(notice=True)에서만 감싸지 않는다('폭염 대비', '보호자 동반').
                                        단 바로 앞 어절에 다른 용어 이름이 있으면 이름으로 보고 감싼다('근정전앞 조정에서는'
                                        → 마당, '관람 시간 조정 안내' → 바꿈). 이 판정은 백엔드 wrap_terms 가 함.

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


KEEP_BUNDLED = {"창건", "중건", "재건", "복원", "소실"}  # 위 머리말 참고


def roman_parts(en):
    """영어 표기의 괄호 앞 이름들: 'Sura, Byeolsik, and Dasik (…)' → ['Sura', 'Byeolsik', 'Dasik']"""
    return [p.strip() for p in re.split(r"/|,|\band\b", en.split(" (")[0]) if p.strip()]


def is_bundle(en):
    """백엔드(gunggeum-be translate.py is_bundle)와 같은 기준: 괄호 앞 이름에 / 나 and 가 있음"""
    head = en.split(" (")[0]
    return "/" in head or re.search(r"\band\b", head) is not None


def split_rows(path=None):
    """묶음 표기를 나눈 이름 {ko: (en, th)}. 파일이 없으면 빈 표"""
    path = Path(path) if path else DATA / "translate_split_names.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        return {r["ko"].strip(): (r["en"].strip(), (r.get("th") or "").strip()) for r in csv.DictReader(f)}


def all_rows(metas):
    """translate_terms.csv 에 쓰는 행: 카드 표기(묶음은 나눈 이름으로) + 일반 단어, 한국어 순"""
    split = split_rows()
    rows = []
    for ko, en, th in terminology_rows(metas):
        if ko in split:
            new_en, new_th = split.pop(ko)
            if not is_bundle(en) or is_bundle(new_en) or roman_parts(new_en)[0] not in roman_parts(en):
                raise ValueError(f"나눈 이름이 원래 묶음 표기의 이름 하나가 아님: {ko} {en!r} → {new_en!r}")
            en, th = new_en, new_th
        rows.append((ko, en, th))
    if split:
        raise ValueError(f"나눈 이름 파일에 용어집에 없는 이름이 있음: {sorted(split)}")
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
