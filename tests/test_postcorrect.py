from gunggeum_stt import glossary
from gunggeum_stt.postcorrect import Corrector, correct, jamo, jaro_winkler


def test_jamo_similarity_beats_syllable():
    assert jaro_winkler(jamo("흑례문"), jamo("흥례문")) >= 0.85
    assert jaro_winkler("흑례문", "흥례문") < 0.85


def test_correct_replaces_near_term_and_keeps_particle():
    terms = glossary.location_terms("광화문")
    text, changes = correct(terms, "흥예문 앞 월대에서 설명합니다.")
    assert text.startswith("흥례문 앞")
    assert changes and changes[0][1] == "흥례문"


def test_exact_match_untouched():
    out, changes = Corrector(["근정전"]).apply("근정전은 정전입니다")
    assert out == "근정전은 정전입니다" and changes == []


def test_unknown_location_falls_back_to_all_terms():
    assert len(glossary.location_terms("없는 곳")) > 300
