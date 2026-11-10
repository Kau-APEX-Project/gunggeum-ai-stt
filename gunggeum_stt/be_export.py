"""백엔드(gunggeum-be) 연동용 자료 내보내기.

백엔드는 해설 위치를 pois.json 의 id(예: place:geunjeongjeon-hall)로 받고,
 - STT_HINTS_PATH 의 JSON {"<poiId>": [용어, ...]} 을 후처리 사전으로 쓰고
 - Transcribe 사용자 지정 어휘를 TRANSCRIBE_VOCABULARY_PREFIX + poiId(':' → '-') 이름으로 찾는다.
이 모듈은 우리 위치별 용어 목록(data/hints_by_location.json, 키=해설 위치 이름)을 poiId 기준으로 바꾼다.

  python -m gunggeum_stt.be_export --out dist/be        # stt_hints.json · 어휘 파일 · 용어집 CSV 만들기
  AWS_PROFILE=<팀 계정> python -m gunggeum_stt.be_export --register   # 팀 계정에 어휘·용어집 등록

위치 이름 → POI 대응 규칙
 - 이름이 같은 POI 가 있으면 그대로 (29곳)
 - "A·B·C" 처럼 묶인 이름은 A, B, C 각각의 POI 에 같은 목록
 - "태원전 일곽" · "건청궁 부속" 은 그 권역(group)의 나머지 POI 전부
 - "기타" 는 대응하는 POI 가 없어 뺀다
 - 묶음 이름("기타"·"태원전 일곽"·"건청궁 부속")은 전각 이름이 아니므로 단어 목록에서도 뺀다
data/pois.json 은 백엔드 POI 목록에서 id·name·group 만 뽑은 것이다. POI 가 바뀌면 다시 받아 넣는다.
"""
import json
import re
from pathlib import Path

from . import glossary

VOCAB_PREFIX = "gungeum-"  # 백엔드 TRANSCRIBE_VOCABULARY_PREFIX 와 같아야 한다
AREA_REST = {"태원전 일곽": "태원전", "건청궁 부속": "건청궁"}  # 위치 이름 → 권역(group)
SKIP = {"기타"}
NOT_TERMS = SKIP | set(AREA_REST)  # 전각이 아닌 묶음 이름. 원본 목록 맨 앞에 섞여 있어 단어에서 뺀다


def pois() -> list[dict]:
    return json.loads(glossary._data("pois.json"))


def hints_by_poi() -> dict[str, list[str]]:
    """poiId → 용어 목록. POI 목록 순서를 따른다."""
    by_name = {p["name"]: p for p in pois()}
    hints = glossary.hints_by_location()
    out: dict[str, list[str]] = {}
    for loc, terms in hints.items():
        if loc in SKIP:
            continue
        if loc in by_name:
            targets = [by_name[loc]]
        elif loc in AREA_REST:
            targets = [p for p in pois() if p["group"] == AREA_REST[loc] and p["name"] not in hints]
        else:
            names = loc.split("·")
            missing = [n for n in names if n not in by_name]
            if missing:
                raise KeyError(f"POI 목록에 없는 위치: {loc} → {missing}")
            targets = [by_name[n] for n in names]
        for p in targets:
            if p["id"] in out:
                raise ValueError(f"POI 가 두 위치에 걸림: {p['name']}")
            out[p["id"]] = [t for t in terms if t not in NOT_TERMS]
    order = {p["id"]: i for i, p in enumerate(pois())}
    return dict(sorted(out.items(), key=lambda kv: order[kv[0]]))


def vocab_name(poi_id: str) -> str:
    return VOCAB_PREFIX + poi_id.replace(":", "-")


def vocabularies() -> dict[str, list[str]]:
    """어휘 이름 → 어휘 문구. 위치를 모를 때 쓰는 전체 어휘 1개 + POI 별."""
    todo = {glossary.GLOBAL_VOCAB: glossary.vocab_phrases(t for t in glossary.all_terms() if t not in NOT_TERMS)}
    todo |= {vocab_name(pid): glossary.vocab_phrases(terms) for pid, terms in hints_by_poi().items()}
    for name in todo:
        if not re.fullmatch(r"[0-9a-zA-Z._-]{1,200}", name):
            raise ValueError(f"Transcribe 어휘 이름으로 쓸 수 없음: {name}")
    return todo


def write(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "stt_hints.json").write_text(
        json.dumps(hints_by_poi(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    vdir = out / "vocab"
    vdir.mkdir(exist_ok=True)
    for name, phrases in vocabularies().items():  # 콘솔 "파일 업로드"용 표 형식(탭 구분)
        rows = ["Phrase\tSoundsLike\tIPA\tDisplayAs"] + [f"{p}\t\t\t" for p in phrases]
        (vdir / f"{name}.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (out / "translate_terms.csv").write_text(glossary._data("translate_terms.csv"), encoding="utf-8")
    print(f"{out}/stt_hints.json: POI {len(hints_by_poi())}곳")
    print(f"{vdir}/: 어휘 파일 {len(vocabularies())}개")
    print(f"{out}/translate_terms.csv: 용어 {len(glossary.terminology_rows())}개")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, help="파일로 내보낼 폴더")
    ap.add_argument("--register", action="store_true", help="현재 AWS 자격 증명의 계정에 어휘·용어집 등록")
    ap.add_argument("--terminology-name", default="gungeum-ko")
    ap.add_argument("--terminology-langs", default="en", help="용어집에 넣을 언어(쉼표 구분). 태국어는 200바이트 넘는 묶음 항목을 정리한 뒤 추가")
    a = ap.parse_args()
    if a.out:
        write(a.out)
    if a.register:
        glossary.register_vocabularies(vocabularies())
        glossary.create_terminology(a.terminology_name, langs=tuple(a.terminology_langs.split(",")))
