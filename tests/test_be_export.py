import json

from gunggeum_stt import be_export, glossary


def test_every_poi_gets_terms():
    by_poi = be_export.hints_by_poi()
    ids = [p["id"] for p in be_export.pois()]
    assert list(by_poi) == ids  # 71곳 전부, POI 목록 순서
    assert all(by_poi[i] for i in ids)


def test_name_match_and_grouped_locations():
    by_poi = be_export.hints_by_poi()
    hints = glossary.hints_by_location()
    name_to_id = {p["name"]: p["id"] for p in be_export.pois()}
    assert by_poi[name_to_id["근정전"]] == hints["근정전"]
    assert by_poi[name_to_id["함화당"]] == hints["협길당·함화당·흥복전"]
    assert by_poi[name_to_id["공묵재"]] == [t for t in hints["태원전 일곽"] if t != "태원전 일곽"]
    assert by_poi[name_to_id["태원전"]] == hints["태원전"]  # 권역 대표 전각은 자기 목록
    assert by_poi[name_to_id["곤녕합"]] == [t for t in hints["건청궁 부속"] if t != "건청궁 부속"]


def test_group_names_are_not_terms():
    junk = {"기타", "태원전 일곽", "건청궁 부속"}
    assert not any(junk & set(terms) for terms in be_export.hints_by_poi().values())
    phrases = {p for v in be_export.vocabularies().values() for p in v}
    assert not {"기타", "태원전-일곽", "건청궁-부속"} & phrases


def test_vocab_names_follow_backend_rule():
    vocab = be_export.vocabularies()
    assert "gungeum-place-geunjeongjeon-hall" in vocab
    assert glossary.GLOBAL_VOCAB in vocab
    assert len(vocab) == len(be_export.pois()) + 1 <= 100  # 계정당 어휘 100개 한도
    assert all(phrases and all(" " not in p for p in phrases) for phrases in vocab.values())


def test_write(tmp_path):
    be_export.write(tmp_path)
    data = json.loads((tmp_path / "stt_hints.json").read_text(encoding="utf-8"))
    assert len(data) == 71
    first = (tmp_path / "vocab" / "gungeum-place-gwanghwamun-gate.txt").read_text(encoding="utf-8").splitlines()
    assert first[0] == "Phrase\tSoundsLike\tIPA\tDisplayAs" and first[1].count("\t") == 3
