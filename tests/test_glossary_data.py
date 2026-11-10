"""커밋된 data/cards.json·translate_terms.csv 가 레포 루트 엑셀과 같은지 확인 (엑셀을 고쳤으면 build_glossary 를 다시 돌릴 것)."""
import csv
import json
from pathlib import Path

import pytest

from gunggeum_stt import glossary
from gunggeum_stt.build_glossary import ROOT, all_rows, card_metas, plain_rows, terminology_rows

XLSX_B, XLSX_T = ROOT / "경복궁_전각.xlsx", ROOT / "경복궁_용어.xlsx"


@pytest.mark.skipif(not (XLSX_B.exists() and XLSX_T.exists()), reason="엑셀 원본 없음")
def test_data_matches_excel():
    metas = card_metas(XLSX_B, XLSX_T)
    assert metas == glossary.cards()
    rows = [(r["ko"], r["en"], r["th"]) for r in csv.DictReader(open(Path(glossary.__file__).parent / "data" / "translate_terms.csv", encoding="utf-8"))]
    assert rows == all_rows(metas)


@pytest.mark.skipif(not (XLSX_B.exists() and XLSX_T.exists()), reason="엑셀 원본 없음")
def test_plain_words_are_merged_but_not_cards():
    metas = card_metas(XLSX_B, XLSX_T)
    plain = plain_rows()
    assert ("왕비", "the queen", "") in plain and ("경복궁", "Gyeongbokgung Palace", "") in plain
    assert all(len(ko) > 1 and en and "(" not in en for ko, en, _ in plain)  # 2글자 이상, 괄호 풀이 없는 일반 영어
    assert not {ko for ko, *_ in plain} & {ko for ko, *_ in terminology_rows(metas)}  # 카드 이름과 겹치지 않음
    assert len(all_rows(metas)) == len(terminology_rows(metas)) + len(plain)


def test_every_card_has_english_name():
    assert all(m.get("name_en") for m in glossary.cards())
    assert sum(m["type"] == "building" for m in glossary.cards()) == 71
