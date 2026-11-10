"""영어 번역의 용어 표기 일치율 측정 (보고서 STT_현장녹음_번역용어집_측정_보고서 5·6장).

해설 문장 300개에 든 용어·전각 이름이 영어 번역 결과에서 용어집의 영문 표기 그대로 나오는지 센다.

  python translate_terms_eval.py translate --mode terminology   # 원문 + 사용자 지정 용어집(gungeum-ko)
  python translate_terms_eval.py translate --mode plain         # 원문, 용어집 없이
  python translate_terms_eval.py translate --mode inject-full   # 용어를 영문 표기(괄호 설명 포함)로 바꿔 넣어 번역
  python translate_terms_eval.py translate --mode inject-name   # 용어를 영문 이름만 바꿔 넣어 번역
  python translate_terms_eval.py translate --mode notrans       # 이름을 <span translate="no">로 감싸 번역 (아직 안 맞는 항목이 든 문장만)
  python translate_terms_eval.py score                          # 저장된 번역 결과로 채점·집계 (AWS 호출 없음)

translate 는 Amazon Translate(서울 리전)를 부르므로 요금이 든다(100만 글자당 15달러). 계정은 AWS_PROFILE 로 고른다.
score 는 results_translate/raw_<mode>.jsonl 만 읽으므로 몇 번을 돌려도 요금이 없다.

입력
  ../경복궁_STT_발화문_300.xlsx          해설 문장 300개와 문장별 "포함 용어"·"포함 전각명"
  gunggeum_stt/data/translate_terms.csv   용어집(ko,en,th). 영어 칸이 채점 기준
출력 (results_translate/)
  raw_<mode>.jsonl           번역 결과 원본 {id, sent(보낸 문장), en(받은 영어)}
  terms300_summary.csv       용어집 켬·끔 일치율 (전체, 용어, 전각 이름, 조사 유무별)
  terms300_by_term.csv       용어별 등장 수·일치 수·조사가 붙어 나온 수
  terms300_scored.jsonl      문장별 용어집 켬·끔 결과와 항목별 일치 여부
  inject300_summary.csv      방식 여섯 가지 비교
  inject300_scored.jsonl     문장별 방식별 영어 결과와 항목별 일치 여부
  notrans37_scored.jsonl     번역 제외 표시를 씌운 문장의 전후 비교

채점 규칙: 기대 표기가 영어 결과에 들어 있으면 일치. 대소문자, 빗금 주변 띄어쓰기, 따옴표·줄표 모양의 차이는 무시한다
(Translate가 용어집 표기를 넣으면서 `A / B`를 `A/B`로, 굽은 따옴표를 곧은 따옴표로 바꾸기 때문).
"""
import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from metrics import term_variants  # noqa: E402

OUT = ROOT / "results_translate"
XLSX = ROOT.parent / "경복궁_STT_발화문_300.xlsx"
REGION = "ap-northeast-2"
TERMINOLOGY = "gungeum-ko"
MODES = ("terminology", "plain", "inject-full", "inject-name", "notrans")
TAG = re.compile(r'<span translate="no">(.*?)</span>')
SUFFIX = {"gate", "hall", "pavilion", "palace", "bridge"}  # 이름 뒤에 붙어 나오면 함께 바꾸는 낱말
KIND = {"term": "용어", "building": "전각 이름"}
M_TERM, M_JW = "용어집만", "용어집+영어 유사도 교정(0.85)"
M_FULL, M_NAME = "바꿔 넣기(설명까지)", "바꿔 넣기(이름만, 설명은 뒤에)"
M_RULES = "바꿔 넣기(이름만) + 후처리 규칙 두 가지"
M_TAG = "바꿔 넣기(이름만) + 번역 제외 표시(37문장 측정, 나머지 환산)"


def terms_csv() -> Path:
    """레포 안(…/gunggeum-ai-stt/STT학습데이터/Benchmark)과 레포 밖 작업 폴더 양쪽에서 찾는다."""
    for base in (ROOT.parents[1], ROOT.parents[1] / "gunggeum-ai-stt"):
        p = base / "gunggeum_stt" / "data" / "translate_terms.csv"
        if p.exists():
            return p
    raise FileNotFoundError("gunggeum_stt/data/translate_terms.csv")


def words() -> set[str]:
    """일반 영어 낱말 목록(맥·리눅스 기본 사전). 없으면 빈 집합."""
    p = Path("/usr/share/dict/words")
    return {w.strip().lower() for w in p.open()} if p.exists() else set()


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", re.sub(r"\s*/\s*", "/", s))


def hit(en: str, text: str) -> bool:
    return norm(en) in norm(text)


def head_of(en: str) -> str:
    """영문 표기에서 괄호 설명 앞의 이름 부분"""
    return en.split(" (")[0]


def variants(cell: str) -> list[str]:
    out = []
    for part in cell.split("/"):
        for v in term_variants(part.strip()):
            if v and v not in out:
                out.append(v)
    return out


def load_items() -> list[dict]:
    """문장마다 채점 항목(용어·전각 이름)과 기대 표기를 붙인다."""
    term_en = {r["ko"]: r["en"] for r in csv.DictReader(terms_csv().open(encoding="utf-8"))}
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    rows = [r for r in wb["발화문"].iter_rows(values_only=True) if r and r[0]]
    items = []
    for uid, loc, text, terms, _nos, _core, blds, *_ in rows[1:]:
        exp = []
        cells = [("term", g) for g in (re.split(r",\s*(?![^()]*\))", terms) if terms else [])]
        cells += [("building", b) for b in re.findall(r"\[(.*?)\]", blds or "")]
        for kind, cell in cells:
            found = [v for v in variants(cell) if v in text]
            ko = max(found, key=len) if found else None
            en = term_en.get(ko) if ko else None
            e = {"kind": kind, "group": cell, "ko": ko, "en": en}
            if en:
                i = text.find(ko)
                nxt = text[i + len(ko):i + len(ko) + 1]
                e["attached"] = "조사 붙음" if re.match(r"[가-힣]", nxt or "") else "떨어져 있음"
            exp.append(e)
        items.append({"id": uid, "location": loc, "text": text, "expect": exp})
    return items


def scorable(item: dict) -> list[dict]:
    return [e for e in item["expect"] if e["en"]]


# ---- 번역 전에 바꿔 넣기 ----
def spans(item: dict) -> list[tuple[int, int, str]]:
    """바꿔 넣을 자리 (시작, 끝, 영문 표기). 긴 용어부터 잡고 겹치는 자리는 건너뛴다."""
    text, out = item["text"], []
    for ko, en in sorted({(e["ko"], e["en"]) for e in scorable(item)}, key=lambda x: -len(x[0])):
        i = text.find(ko)
        while i >= 0:
            if not any(a < i + len(ko) and i < b for a, b, _ in out):
                out.append((i, i + len(ko), en))
            i = text.find(ko, i + len(ko))
    return sorted(out)


def build(item: dict, mode: str) -> str:
    """보낼 문장. full=괄호 설명까지, name=이름만, notrans=이름을 번역 제외 표시로 감쌈"""
    text, s, p = item["text"], "", 0
    for a, b, en in spans(item):
        piece = {"full": en, "name": head_of(en), "notrans": f'<span translate="no">{head_of(en)}</span>'}[mode]
        s += text[p:a] + piece
        p = b
    return s + text[p:]


def heads(item: dict) -> list[tuple[str, str]]:
    return sorted({(head_of(en), en) for _, _, en in spans(item)})


def attach(text: str, hs, all_heads=frozenset(), dictionary=frozenset(), plural=False, spelling=False, log=None) -> str:
    """이름만 넣어 받은 영어에서 이름을 찾아 정해진 표기(괄호 설명 포함)로 바꾼다. 이름은 낱말 단위로만 찾는다.
    plural: 이름 뒤에 복수형 s가 붙은 것도 같은 이름으로 본다.
    spelling: 넣은 이름이 없을 때만, 유사도 0.90 이상인 낱말 하나를 그 이름으로 되돌린다
              (한 낱말 이름만. 다른 용어의 이름이거나 사전에 있는 소문자 영어 낱말이면 건드리지 않는다)."""
    slots, missing = [], []

    def put(en):
        slots.append(en)
        return f"\x00{len(slots) - 1}\x00"

    for head, en in sorted(hs, key=lambda x: -len(x[0])):
        body = r"\s*/\s*".join(re.escape(p.strip()) for p in head.split("/"))
        m = re.search(r"(?<![A-Za-z])" + body + r"(?![A-Za-z])", text, re.I)
        if m:
            text = text[:m.start()] + put(en) + text[m.end():]
            continue
        if plural:
            m = re.search(r"(?<![A-Za-z])" + body + r"s(?![A-Za-z])", text, re.I)
            if m:
                tail = "’s" if text[m.end():m.end() + 1] in ("'", "’") else ""
                if log is not None:
                    log.append(("복수형", m.group(), en))
                text = text[:m.start()] + put(en) + tail + text[m.end() + (1 if tail else 0):]
                continue
        missing.append((head, en))
    if spelling:
        here = {h.lower() for h, _ in hs}
        for head, en in missing:
            if " " in head or "/" in head:
                continue
            best = (0.0, None)
            for m in re.finditer(r"[A-Za-z]+", text):
                w = m.group()
                lw = w.lower()
                if lw in here or lw in all_heads or (w[0].islower() and lw in dictionary):
                    continue
                s = jw(head.lower(), lw)
                if s > best[0]:
                    best = (s, m)
            if best[1] is not None and best[0] >= 0.90:
                m = best[1]
                if log is not None:
                    log.append((f"철자 {best[0]:.3f}", m.group(), en))
                text = text[:m.start()] + put(en) + text[m.end():]
    return re.sub(r"\x00(\d+)\x00", lambda m: slots[int(m.group(1))], text)


def untag(text: str, hs) -> str:
    """번역 제외 표시 자리에 정해진 표기를 넣는다."""
    h2e = dict(hs)
    return re.sub(r"\s+", " ", TAG.sub(lambda m: h2e.get(m.group(1), m.group(1)), text))


# ---- 영어 결과를 유사도로 고치기 ----
def jaro(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    w = max(la, lb) // 2 - 1
    ma, mb, m = [False] * la, [False] * lb, 0
    for i, c in enumerate(a):
        for j in range(max(0, i - w), min(lb, i + w + 1)):
            if not mb[j] and b[j] == c:
                ma[i] = mb[j] = True
                m += 1
                break
    if not m:
        return 0.0
    t = k = 0
    for i in range(la):
        if ma[i]:
            while not mb[k]:
                k += 1
            t += a[i] != b[k]
            k += 1
    return (m / la + m / lb + (m - t / 2) / m) / 3


def jw(a: str, b: str) -> float:
    j, p = jaro(a, b), 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        p += 1
    return j + p * 0.1 * (1 - j)


def letters(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def best_span(tokens, used, en):
    """영어 결과의 낱말 1~3개 묶음 중 표기의 이름 부분과 가장 비슷한 자리"""
    h = head_of(en)
    cands = [x for x in dict.fromkeys([h] + [p.strip() for p in h.split("/")]) if len(letters(x)) >= 4]
    best = (0, None)
    for c in cands:
        hn = letters(c)
        for n in (1, 2, 3):
            for i in range(len(tokens) - n + 1):
                if any(k in used for k in range(i, i + n)):
                    continue
                cand = letters("".join(tokens[i:i + n]))
                if len(cand) < 4:
                    continue
                s = jw(hn, cand)
                if s > best[0]:
                    best = (s, (i, n))
    return best


def jw_fix(text: str, expect: list[dict], th: float) -> tuple[str, int]:
    """아직 안 맞는 항목마다, 유사도 th 이상인 가장 비슷한 자리를 정해진 표기로 바꾼다. (고친 문장, 고친 자리 수)"""
    tokens, used, fixed = text.split(), set(), 0
    todo = [e for e in expect if e["en"] and not hit(e["en"], text)]
    for _, e in sorted(((best_span(tokens, set(), e["en"]), e) for e in todo), key=lambda x: -x[0][0]):
        s, span = best_span(tokens, used, e["en"])
        if span is None or s < th:
            continue
        i, n = span
        if i + n < len(tokens) and letters(tokens[i + n]) in SUFFIX:
            n += 1
        tail = re.search(r"[,.;:!?]+$", " ".join(tokens[i:i + n]))
        used.update(range(i, i + n))
        tokens[i] = e["en"] + (tail.group() if tail else "")
        for k in range(i + 1, i + n):
            tokens[k] = ""
        fixed += 1
    return " ".join(t for t in tokens if t), fixed


# ---- 입출력 ----
def read_raw(mode: str) -> dict[str, dict]:
    p = OUT / f"raw_{mode}.jsonl"
    return {r["id"]: r for r in map(json.loads, p.open(encoding="utf-8"))}


def write_jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def name_results(items) -> dict[str, str]:
    """이름만 넣는 방식에 후처리 규칙 두 가지를 적용한 최종 영어"""
    raw = read_raw("inject-name")
    all_heads = {p.strip().lower() for r in csv.DictReader(terms_csv().open(encoding="utf-8"))
                 for p in head_of(r["en"]).split("/")}
    dictionary = words()
    return {it["id"]: attach(raw[it["id"]]["en"], heads(it), all_heads, dictionary, plural=True, spelling=True)
            for it in items}


def cmd_translate(mode: str) -> None:
    import boto3
    items = load_items()
    if mode == "notrans":  # 후처리 규칙까지 하고도 안 맞는 항목이 든 문장만 다시 번역한다
        rules = name_results(items)
        items = [it for it in items if any(not hit(e["en"], rules[it["id"]]) for e in scorable(it))]
    sent = {"terminology": lambda it: it["text"], "plain": lambda it: it["text"],
            "inject-full": lambda it: build(it, "full"), "inject-name": lambda it: build(it, "name"),
            "notrans": lambda it: build(it, "notrans")}[mode]
    extra = {"TerminologyNames": [TERMINOLOGY]} if mode == "terminology" else {}
    chars = sum(len(sent(it)) for it in items)
    print(f"계정 {boto3.client('sts').get_caller_identity()['Account']} | 문장 {len(items)}개 | 보낼 글자 {chars}자 "
          f"(약 {chars * 15 / 1e6:.2f}달러)", flush=True)
    tl = boto3.client("translate", region_name=REGION)
    rows = []
    for it in items:
        r = tl.translate_text(Text=sent(it), SourceLanguageCode="ko", TargetLanguageCode="en", **extra)
        row = {"id": it["id"], "sent": sent(it), "en": r["TranslatedText"]}
        if mode == "terminology":
            row["applied"] = [[x["SourceText"], x["TargetText"]] for a in r.get("AppliedTerminologies", [])
                              for x in a.get("Terms", [])]
        rows.append(row)
    OUT.mkdir(exist_ok=True)
    write_jsonl(OUT / f"raw_{mode}.jsonl", rows)
    print(f"저장: {OUT / f'raw_{mode}.jsonl'}")


def cmd_score() -> None:
    items = load_items()
    raw = {m: read_raw(m) for m in MODES}
    for it in items:  # 저장된 번역이 지금 코드가 만드는 문장으로 보낸 것인지 확인
        assert raw["inject-full"][it["id"]]["sent"] == build(it, "full"), it["id"]
        assert raw["inject-name"][it["id"]]["sent"] == build(it, "name"), it["id"]
        if it["id"] in raw["notrans"]:
            assert raw["notrans"][it["id"]]["sent"] == build(it, "notrans"), it["id"]
    rules = name_results(items)

    # 5장: 용어집 켬·끔
    c, by_term, term_en, rows = collections.Counter(), collections.defaultdict(collections.Counter), {}, []
    for it in items:
        w, wo = raw["terminology"][it["id"]]["en"], raw["plain"][it["id"]]["en"]
        ex = []
        for e in it["expect"]:
            if not e["en"]:
                ex.append({**e, "with": None, "without": None})
                continue
            a, b = hit(e["en"], w), hit(e["en"], wo)
            ex.append({**e, "with": a, "without": b})
            att = "용어 뒤가 띄어쓰기·문장부호" if e["attached"] == "떨어져 있음" else "용어 뒤에 조사가 붙음"
            for k in ("전체", KIND[e["kind"]], att):
                c[k, "n"] += 1
                c[k, "w"] += a
                c[k, "wo"] += b
            term_en[e["ko"]] = e["en"]
            by_term[e["ko"]]["n"] += 1
            by_term[e["ko"]]["ok"] += a
            by_term[e["ko"]]["att"] += e["attached"] == "조사 붙음"
        rows.append({"id": it["id"], "location": it["location"], "ko": it["text"], "en_with": w, "en_without": wo,
                     "applied": raw["terminology"][it["id"]].get("applied", []), "expect": ex})
    write_jsonl(OUT / "terms300_scored.jsonl", rows)
    with (OUT / "terms300_summary.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["구분", "항목 수", "용어집 켬 일치", "용어집 켬 일치율(%)", "용어집 끔 일치", "용어집 끔 일치율(%)"])
        for k in ("전체", "용어", "전각 이름", "용어 뒤가 띄어쓰기·문장부호", "용어 뒤에 조사가 붙음"):
            n = c[k, "n"]
            wr.writerow([k, n, c[k, "w"], round(100 * c[k, "w"] / n, 1), c[k, "wo"], round(100 * c[k, "wo"] / n, 1)])
    with (OUT / "terms300_by_term.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["용어", "기대 영문 표기", "등장 수", "용어집 켬 일치 수", "조사가 붙어 나온 수"])
        for ko, v in sorted(by_term.items(), key=lambda x: (-x[1]["n"], x[0])):
            wr.writerow([ko, term_en[ko], v["n"], v["ok"], v["att"]])

    # 6장: 방식 비교
    c, rows, nt_rows = collections.Counter(), [], []
    for it in items:
        uid, hs = it["id"], heads(it)
        en = {M_TERM: raw["terminology"][uid]["en"],
              M_JW: jw_fix(raw["terminology"][uid]["en"], it["expect"], 0.85)[0],
              M_FULL: raw["inject-full"][uid]["en"],
              M_NAME: attach(raw["inject-name"][uid]["en"], hs),
              M_RULES: rules[uid]}
        en[M_TAG] = untag(raw["notrans"][uid]["en"], hs) if uid in raw["notrans"] else en[M_RULES]
        ex = []
        for e in it["expect"]:
            if not e["en"]:
                ex.append({"kind": e["kind"], "ko": e["ko"], "en": None})
                continue
            flags = {m: hit(e["en"], t) for m, t in en.items()}
            ex.append({"kind": e["kind"], "ko": e["ko"], "en": e["en"], "attached": e["attached"], "match": flags})
            for m, ok in flags.items():
                for k in ("전체", KIND[e["kind"]]):
                    c[m, k, "n"] += 1
                    c[m, k, "ok"] += ok
        rows.append({"id": uid, "ko": it["text"], "sent_full": raw["inject-full"][uid]["sent"],
                     "sent_name": raw["inject-name"][uid]["sent"], "en_full": en[M_FULL],
                     "en_name_raw": raw["inject-name"][uid]["en"], "en_name": en[M_NAME], "en_name_rules": en[M_RULES],
                     "en_terminology_jw": en[M_JW], "expect": ex})
        if uid in raw["notrans"]:
            nt_rows.append({"id": uid, "ko": it["text"], "sent": raw["notrans"][uid]["sent"],
                            "en_raw": raw["notrans"][uid]["en"], "en": en[M_TAG], "en_before": en[M_RULES],
                            "expect": [{"ko": e["ko"], "en": e["en"], "before": hit(e["en"], en[M_RULES]),
                                        "after": hit(e["en"], en[M_TAG])} for e in scorable(it)]})
    write_jsonl(OUT / "inject300_scored.jsonl", rows)
    write_jsonl(OUT / "notrans37_scored.jsonl", nt_rows)
    with (OUT / "inject300_summary.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["방식", "전체 항목", "전체 일치", "전체 일치율(%)", "용어 일치율(%)", "전각 이름 일치율(%)"])
        for m in (M_TERM, M_JW, M_FULL, M_NAME, M_RULES, M_TAG):
            pct = lambda k: round(100 * c[m, k, "ok"] / c[m, k, "n"], 1)  # noqa: E731
            wr.writerow([m, c[m, "전체", "n"], c[m, "전체", "ok"], pct("전체"), pct("용어"), pct("전각 이름")])
            print(f"{m}: {c[m, '전체', 'ok']}/{c[m, '전체', 'n']} = {pct('전체')}%")
    print(f"번역 제외 표시를 씌운 문장: {len(nt_rows)}개")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("translate", help="Amazon Translate로 번역 (요금 발생)")
    t.add_argument("--mode", required=True, choices=MODES)
    sub.add_parser("score", help="저장된 번역 결과로 채점·집계 (AWS 호출 없음)")
    a = ap.parse_args()
    cmd_translate(a.mode) if a.cmd == "translate" else cmd_score()
