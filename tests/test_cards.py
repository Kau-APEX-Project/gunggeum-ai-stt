from gunggeum_stt import glossary
from gunggeum_stt.cards import link_names


def test_links_first_occurrence_only_english():
    out = link_names("Gwanghwamun (Palace Gate) is the main gate. Gwanghwamun again.", glossary.cards(), "English")
    assert out.startswith("[Gwanghwamun](card:b-151) (Palace Gate)")
    assert out.count("card:b-151") == 1


def test_longer_name_wins_over_inner_term():
    metas = [{"type": "building", "serial": 1, "name": "근정전", "name_en": "Geunjeongjeon (Throne Hall)"},
             {"type": "term", "no": 9, "name": "정전", "name_en": "Jeongjeon (main hall)"}]
    assert link_names("근정전과 정전", metas, "한국어") == "[근정전](card:b-1)과 [정전](card:t-9)"
    assert link_names("Geunjeongjeon", metas, "English") == "[Geunjeongjeon](card:b-1)"


def test_thai_uses_full_glossary_form_only():
    metas = [{"type": "term", "no": 86, "name": "나티", "name_th": "นาที (หน้ากากอสูร)"}]
    assert link_names("ใช้เวลา 5 นาที", metas, "ภาษาไทย") == "ใช้เวลา 5 นาที"
    assert "card:t-86" in link_names("นาที (หน้ากากอสูร) อยู่บนหลังคา", metas, "ภาษาไทย")
