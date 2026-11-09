"""공통 용어집(전각 71곳 + 용어 231개 카드)과 해설 위치별 용어 목록.

영문·태국어 표기의 원본은 레포 루트의 엑셀 `경복궁_전각.xlsx`(name_en) · `경복궁_용어.xlsx`("영문 표기 (작성용)") 이고,
`python -m gunggeum_stt.build_glossary` 가 아래 두 파일을 만든다. 엑셀이 바뀌면 다시 돌려 커밋한다.

data/cards.json          카드 메타: type·serial/no·name·name_en·name_th·nameId (자막 카드 링크용)
data/hints_by_location.json  해설 위치(26곳) → 그 위치에서 쓰는 용어 목록 (STT 벤치마크 data/와 같음)
data/translate_terms.csv  Amazon Translate 사용자 지정 용어집 (ko,en,th). 규칙은 gunggeum-ai-llm-gps/server/export_terms.py 와 같음

Transcribe 사용자 지정 어휘 이름은 벤치마크에서 만든 것과 같다: 전체 gungeum-gyeongbokgung-ko, 위치별 gungeum-loc-00~25
(hints_by_location.json의 순서). 다른 계정에 새로 만들 때는 `python -m gunggeum_stt.glossary --create-vocab`.
"""
import csv
import json
import re
from functools import lru_cache
from importlib import resources

from .textnorm import num_to_korean

GLOBAL_VOCAB = "gungeum-gyeongbokgung-ko"
LANG_LABEL = {"ko": "한국어", "en": "English", "th": "ภาษาไทย"}  # cards.link_names 의 언어 이름


def _data(name: str) -> str:
    return resources.files("gunggeum_stt").joinpath("data", name).read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def cards() -> list[dict]:
    return json.loads(_data("cards.json"))


@lru_cache(maxsize=None)
def hints_by_location() -> dict[str, list[str]]:
    return json.loads(_data("hints_by_location.json"))


@lru_cache(maxsize=None)
def vocab_names() -> dict[str, str]:
    """해설 위치 → Transcribe 사용자 지정 어휘 이름"""
    return {loc: f"gungeum-loc-{i:02d}" for i, loc in enumerate(hints_by_location())}


def location_terms(location: str | None) -> list[str]:
    """후처리 사전에 쓸 용어 목록. 모르는 위치면 전체 용어(전각·용어 이름) 사용."""
    if location and location in hints_by_location():
        return hints_by_location()[location]
    return all_terms()


@lru_cache(maxsize=None)
def all_terms() -> list[str]:
    seen = {}
    for terms in hints_by_location().values():
        for t in terms:
            seen[t] = None
    for m in cards():
        for p in re.sub(r"\(.*?\)", "", m.get("name") or "").split("/"):
            if len(p := p.strip()) > 1:
                seen[p] = None
    return list(seen)


def vocab_for(location: str | None) -> str | None:
    """위치별 어휘 이름. 모르는 위치면 전체 어휘."""
    return vocab_names().get(location, GLOBAL_VOCAB) if location else GLOBAL_VOCAB


def vocab_phrases(terms) -> list[str]:
    """ko-KR 목록형 어휘: 공백은 하이픈, 숫자는 한글 읽기, 한글 외 문자는 뺀다."""
    out = []
    for t in terms:
        t = re.sub(r"[^가-힣 ]", "", num_to_korean(t)).strip()
        t = re.sub(r"\s+", "-", t)
        if t and t not in out:
            out.append(t)
    return out


def terminology_rows() -> list[tuple[str, str, str]]:
    return [(r["ko"], r["en"], r["th"]) for r in csv.DictReader(_data("translate_terms.csv").splitlines())]


def create_vocabularies(region: str = "ap-northeast-2"):
    """Transcribe 사용자 지정 어휘(전체 1개 + 위치별)를 만든다. 이미 있으면 갱신. 완료까지 수 분."""
    todo = {GLOBAL_VOCAB: vocab_phrases(all_terms())}
    todo |= {name: vocab_phrases(hints_by_location()[loc]) for loc, name in vocab_names().items()}
    register_vocabularies(todo, region)


def register_vocabularies(todo: dict[str, list[str]], region: str = "ap-northeast-2"):
    """{어휘 이름: 문구 목록}을 Transcribe 에 등록한다. 이미 있으면 갱신. 동시 처리 한도에 걸리면 기다렸다 이어서."""
    import time
    import boto3
    tc = boto3.client("transcribe", region_name=region)
    pending = dict(todo)
    while pending:
        for name, phrases in list(pending.items()):
            try:
                tc.create_vocabulary(VocabularyName=name, LanguageCode="ko-KR", Phrases=phrases)
            except tc.exceptions.ConflictException:
                if tc.get_vocabulary(VocabularyName=name)["VocabularyState"] == "PENDING":
                    continue
                tc.update_vocabulary(VocabularyName=name, LanguageCode="ko-KR", Phrases=phrases)
            except tc.exceptions.LimitExceededException:
                break
            del pending[name]
        if pending:
            print(f"등록 대기 {len(pending)}개 …", flush=True)
        time.sleep(10)
    while any(tc.get_vocabulary(VocabularyName=n)["VocabularyState"] == "PENDING" for n in todo):
        time.sleep(10)
    failed = 0
    for n in todo:
        s = tc.get_vocabulary(VocabularyName=n)
        failed += s["VocabularyState"] != "READY"
        print(f"어휘 {n}: {s['VocabularyState']} ({len(todo[n])}개)", s.get("FailureReason", ""))
    print(f"어휘 {len(todo)}개 중 실패 {failed}개")


def create_terminology(name: str = "gungeum-ko", region: str = "ap-northeast-2", langs=("en", "th")):
    """Amazon Translate 사용자 지정 용어집(ko→langs) 등록. 이미 있으면 덮어쓴다.
    Translate 는 용어 하나가 200바이트를 넘으면 받지 않는다(태국어 묶음 항목 8행이 넘음) → 영어만 올릴 때는 langs=("en",)."""
    import boto3
    col = {"en": 1, "th": 2}
    rows = terminology_rows()
    lines = [",".join(["ko", *langs])]
    for r in rows:
        lines.append(",".join('"' + c.replace('"', '""') + '"' for c in [r[0], *(r[col[l]] for l in langs)]))
    boto3.client("translate", region_name=region).import_terminology(
        Name=name, MergeStrategy="OVERWRITE",
        TerminologyData={"File": "\n".join(lines).encode("utf-8"), "Format": "CSV", "Directionality": "UNI"})
    print(f"용어집 {name}: {len(rows)}개 (ko→{'·'.join(langs)})")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--create-vocab", action="store_true", help="Transcribe 사용자 지정 어휘 생성")
    ap.add_argument("--create-terminology", action="store_true", help="Translate 용어집 생성")
    ap.add_argument("--terminology-name", default="gungeum-ko")
    a = ap.parse_args()
    if a.create_vocab:
        create_vocabularies()
    if a.create_terminology:
        create_terminology(a.terminology_name)
