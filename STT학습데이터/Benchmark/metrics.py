"""CER·용어 인식률 계산.

정규화 규칙
- raw: NFC, 소문자, 한글·영문·숫자 외 문자(공백·문장부호) 제거
- num: raw 전에 아라비아 숫자를 한자어 수사 읽기로 바꿈 (1395년 -> 천삼백구십오년)
  정답은 숫자를 아라비아 숫자로 적었고 모델은 둘 중 아무거나 낼 수 있으므로, 양쪽을 같은 규칙으로 맞춘다.
"""
import re
import unicodedata

DIGITS = "영일이삼사오육칠팔구"
SMALL = ["", "십", "백", "천"]
LARGE = ["", "만", "억", "조"]


def read_sino(n: int) -> str:
    if n == 0:
        return "영"
    out = []
    for gi, large in enumerate(LARGE):
        group = n // (10000 ** gi) % 10000
        if not group:
            continue
        s = ""
        for i in range(3, -1, -1):
            d = group // (10 ** i) % 10
            if d:
                s += ("" if d == 1 and i > 0 else DIGITS[d]) + SMALL[i]
        if gi == 1 and group == 1:
            s = ""  # 만 (일만 X)
        out.append(s + large)
    return "".join(reversed(out))


def num_to_korean(text: str) -> str:
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    # 5·16처럼 가운뎃점으로 이은 숫자는 한 자리씩 읽는다 (오일육)
    text = re.sub(r"\d+(?:·\d+)+", lambda m: "".join(DIGITS[int(c)] for c in m.group() if c.isdigit()), text)
    return re.sub(r"\d+", lambda m: read_sino(int(m.group())), text)


def normalize(text: str, numbers: bool = False) -> str:
    text = unicodedata.normalize("NFC", text or "").lower()
    if numbers:
        text = num_to_korean(text)
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def term_variants(term: str) -> list[str]:
    """'홍문관(옥당)' -> ['홍문관', '옥당'], '국가유산청(구 문화재청)' -> ['국가유산청', '문화재청']"""
    base = re.sub(r"\s*\(.*?\)", "", term).strip()
    inner = [re.sub(r"^구\s+", "", i).strip() for i in re.findall(r"\((.*?)\)", term)]
    return [v for v in [base] + [i for i in inner if re.fullmatch(r"[가-힣 ]+", i)] if v]


def term_groups(terms_cell: str, buildings_cell: str) -> list[tuple[str, list[str]]]:
    """발화의 포함 용어·전각명을 (종류, 표기 변형 목록)으로 펼친다."""
    groups = []
    for chunk in re.split(r",\s*(?![^()]*\))", terms_cell or ""):
        variants = [v for part in chunk.split("/") for v in term_variants(part.strip())]
        if variants:
            groups.append(("용어", variants))
    for b in filter(None, (buildings_cell or "").split("|")):
        groups.append(("전각", [b]))
    return groups


def heard_as(ref: str, hyp: str, term: str) -> str:
    """정답에서 term이 있던 자리에 가설에서는 무엇이 나왔는지(공백·문장부호 제거 상태)."""
    from difflib import SequenceMatcher
    r, h = normalize(ref), normalize(hyp)
    start = r.find(normalize(term))
    if start < 0:
        return ""
    end = start + len(normalize(term))
    h_start = h_end = None
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, r, h, autojunk=False).get_opcodes():
        if i2 <= start or i1 >= end:
            continue
        lo = j1 + (max(start, i1) - i1 if tag == "equal" else 0)
        hi = j1 + (min(end, i2) - i1 if tag == "equal" else j2 - j1)
        h_start = lo if h_start is None else h_start
        h_end = hi
    return h[h_start:h_end] if h_start is not None else ""


def term_hits(ref: str, hyp: str, groups) -> list[tuple[str, str, bool]]:
    """정답에 실제로 나온 표기를 기준으로 가설에 같은 표기가 있는지 본다."""
    ref_r, hyp_r = normalize(ref), normalize(hyp)
    ref_n, hyp_n = normalize(ref, True), normalize(hyp, True)
    out = []
    for kind, variants in groups:
        present = [v for v in variants if normalize(v) in ref_r or normalize(v, True) in ref_n]
        if not present:
            continue
        hit = any(normalize(v) in hyp_r or normalize(v, True) in hyp_n for v in present)
        out.append((kind, max(present, key=len), hit))
    return out
