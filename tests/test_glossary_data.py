"""커밋된 data/cards.json·translate_terms.csv 가 레포 루트 엑셀과 같은지 확인 (엑셀을 고쳤으면 build_glossary 를 다시 돌릴 것)."""
import csv
import json
from pathlib import Path

import pytest

from gunggeum_stt import glossary
from gunggeum_stt.build_glossary import (KEEP_BUNDLED, ROOT, all_rows, card_metas, is_bundle, plain_rows, split_rows,
                                         terminology_rows)

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
    assert {("왕", "the king"), ("왕비", "the queen"), ("경복궁", "Gyeongbokgung Palace"),
            ("광화문광장", "Gwanghwamun Square"), ("경복궁역", "Gyeongbokgung Station")} <= {(ko, en) for ko, en, _ in plain}
    assert all(ko and " " not in ko and en and th for ko, en, th in plain)  # 띄어쓰기 없음, 영어·태국어 표기 있음
    assert all(" / " not in en and " and " not in en.split(" (")[0] for _, en, _ in plain)  # 백엔드가 묶음으로 보고 건너뛰지 않게
    assert not {ko for ko, *_ in plain} & {ko for ko, *_ in terminology_rows(metas)}  # 카드 이름과 겹치지 않음
    assert len(all_rows(metas)) == len(terminology_rows(metas)) + len(plain)


def test_every_card_has_english_name():
    assert all(m.get("name_en") for m in glossary.cards())
    assert sum(m["type"] == "building" for m in glossary.cards()) == 71


def test_notice_plain_words_are_terms():
    """공지에서 감싸지 않을 용어는 모두 용어 표에 있는 이름.
    '조정'은 '시간 조정 안내'(바꿈)가 마당 이름으로 바뀌어 넣음. 마당 뜻('근정전앞 조정')은 앞 어절에 용어 이름이 있으면
    감싸는 백엔드 규칙으로 맞춤 (2026-11-27)"""
    words = [r["ko"] for r in csv.DictReader(glossary._data("notice_plain_words.csv").splitlines())]
    names = {ko for ko, *_ in glossary.terminology_rows()}
    assert {"대비", "부재", "조회", "동반", "보물", "국보", "복원", "조정"} <= set(words)
    assert set(words) <= names and len(words) == len(set(words))


@pytest.mark.skipif(not (XLSX_B.exists() and XLSX_T.exists()), reason="엑셀 원본 없음")
def test_bundles_are_split_except_keep():
    """용어집에 남는 묶음 표기는 창건·중건·재건·복원·소실 5개뿐. 카드(cards.json)는 엑셀 묶음 표기 그대로"""
    metas = card_metas(XLSX_B, XLSX_T)
    rows = all_rows(metas)
    assert {ko for ko, en, _ in rows if is_bundle(en)} == KEEP_BUNDLED
    split = split_rows()
    assert len(split) == 55 and all(en and th for en, th in split.values())
    assert dict((ko, en) for ko, en, _ in rows)["세자"] == "Seja (Crown Prince)"
    assert any(m["name_en"] == "Seja / Wangseja (Crown Prince)" for m in glossary.cards())
