"""발화문 엑셀과 녹음 파일을 벤치마크 입력으로 정리한다.

- data/references.csv : 녹음이 있는 발화의 정답 전사, 포함 용어, 전각명, 난이도 태그, 길이(초)
- data/hint_terms.txt : 전체 사전(용어집 용어 + 전각명)
- data/hints_by_location.json : 해설 위치별 사전 (그 위치 발화문 전체에서 모음)
- data/hints_loo.json : 누출 없는 위치별 사전 (녹음 ID별, 평가하는 그 문장은 빼고 모음)
- .cache/wav16k/*.wav : 16kHz mono PCM (macOS afconvert로 변환)
"""
import csv
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import openpyxl
import soundfile as sf

from metrics import term_variants

ROOT = Path(__file__).resolve().parent
XLSX = ROOT.parent / "경복궁_STT_발화문_300.xlsx"
AUDIO_DIR = ROOT.parent / "Data"
WAV_DIR = ROOT / ".cache" / "wav16k"
OUT_DIR = ROOT / "data"

# 녹음 파일 -> 실제로 읽은 발화문. U0061~U0069 파일에는 U0060~U0068 문장이 녹음되어 있다
# (녹음자가 U0060부터 읽고 U0069를 건너뜀). 전사 결과를 300개 발화문 전체와 대조해서 확인했다.
AUDIO_TEXT_OVERRIDE = {f"U{n:04d}": f"U{n - 1:04d}" for n in range(61, 70)}


def split_terms(cell):
    """'홍예 / 홍예문, 석축' -> [['홍예', '홍예문'], ['석축']] (한 용어의 표기 변형을 묶는다)"""
    if not cell:
        return []
    groups = []
    for chunk in re.split(r",\s*(?![^()]*\))", cell):
        variants = [v.strip() for v in chunk.split("/") if v.strip()]
        if variants:
            groups.append(variants)
    return groups


def main():
    wb = openpyxl.load_workbook(XLSX)
    utt = {}
    for row in wb["발화문"].iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
        uid = f"U{int(row[0][1:]):04d}"
        utt[uid] = {
            "id": uid,
            "location": row[1],
            "text": row[2],
            "terms": row[3] or "",
            "buildings": (row[6] or "").replace("][", "|").strip("[]"),
            "tags": row[7] or "",
        }

    audio = {p.stem: p for p in AUDIO_DIR.rglob("U*.m4a")}
    WAV_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)

    rows = []
    for uid in sorted(audio):
        wav = WAV_DIR / f"{uid}.wav"
        if not wav.exists():
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                            str(audio[uid]), str(wav)], check=True)
        info = sf.info(wav)
        text_id = AUDIO_TEXT_OVERRIDE.get(uid, uid)
        rows.append({**utt[text_id], "id": uid, "text_id": text_id,
                     "duration": round(info.frames / info.samplerate, 3)})

    with open(OUT_DIR / "references.csv", "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # 전체 사전: 용어집 전체(표기 변형 포함) + 전각명. 괄호 속 한자·구명칭은 별도 표기로 분리한다.
    hints = []
    for row in wb["용어 커버리지"].iter_rows(min_row=2, values_only=True):
        if not row[2]:
            continue
        for part in row[2].split("/"):
            for t in term_variants(part.strip()):
                if t not in hints:
                    hints.append(t)
    for row in wb["전각 커버리지"].iter_rows(min_row=2, values_only=True):
        if row[1] and row[1] not in hints:
            hints.append(row[1])
    (OUT_DIR / "hint_terms.txt").write_text("\n".join(hints) + "\n", encoding="utf-8")

    # 위치별 사전: 해설 위치마다 그 위치 발화 300개에 나온 전각명·용어를 빈도순으로 모은다.
    # 앱이 GPS로 현재 전각을 알 때 그 구역 용어만 힌트로 넣는 상황을 가정한다.
    def location_terms(loc, exclude=None):
        c = Counter({n: 1000 for n in loc.split("·")})
        for uid, u in utt.items():
            if u["location"] != loc or uid == exclude:
                continue
            for b in filter(None, u["buildings"].split("|")):
                c[b] += 10
            for chunk in re.split(r",\s*(?![^()]*\))", u["terms"]):
                for part in chunk.split("/"):
                    for v in term_variants(part.strip()):
                        c[v] += 1
        return [t for t, _ in c.most_common()]

    loc_hints = {loc: location_terms(loc) for loc in dict.fromkeys(u["location"] for u in utt.values())}
    (OUT_DIR / "hints_by_location.json").write_text(
        json.dumps(loc_hints, ensure_ascii=False, indent=1), encoding="utf-8")
    # 누출 없는 위치별 사전: 평가하는 그 문장은 빼고, 같은 위치의 다른 발화문으로만 만든다 (녹음 파일 ID 기준).
    loo_hints = {r["id"]: location_terms(r["location"], exclude=r["text_id"]) for r in rows}
    (OUT_DIR / "hints_loo.json").write_text(json.dumps(loo_hints, ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(r["duration"] for r in rows)
    print(f"{len(rows)}개 발화, 총 {total:.1f}초 (평균 {total / len(rows):.2f}초), 사전 {len(hints)}개")


if __name__ == "__main__":
    main()
