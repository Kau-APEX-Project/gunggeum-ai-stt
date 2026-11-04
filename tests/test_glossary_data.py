"""커밋된 data/cards.json·translate_terms.csv 가 레포 루트 엑셀과 같은지 확인 (엑셀을 고쳤으면 build_glossary 를 다시 돌릴 것)."""
import csv
import json
from pathlib import Path

import pytest

from gunggeum_stt import glossary
from gunggeum_stt.build_glossary import ROOT, card_metas, terminology_rows

XLSX_B, XLSX_T = ROOT / "경복궁_전각.xlsx", ROOT / "경복궁_용어.xlsx"


@pytest.mark.skipif(not (XLSX_B.exists() and XLSX_T.exists()), reason="엑셀 원본 없음")
def test_data_matches_excel():
    metas = card_metas(XLSX_B, XLSX_T)
    assert metas == glossary.cards()
    rows = [(r["ko"], r["en"], r["th"]) for r in csv.DictReader(open(Path(glossary.__file__).parent / "data" / "translate_terms.csv", encoding="utf-8"))]
    assert rows == terminology_rows(metas)


def test_every_card_has_english_name():
    assert all(m.get("name_en") for m in glossary.cards())
    assert sum(m["type"] == "building" for m in glossary.cards()) == 71
