"""숫자 읽기·정규화 (벤치마크 metrics.py에서 가져옴). 사용자 지정 어휘 목록은 한글만 받으므로 숫자를 한자어 수사로 바꾼다."""
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
            s = ""
        out.append(s + large)
    return "".join(reversed(out))


def num_to_korean(text: str) -> str:
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    text = re.sub(r"\d+(?:·\d+)+", lambda m: "".join(DIGITS[int(c)] for c in m.group() if c.isdigit()), text)
    return re.sub(r"\d+", lambda m: read_sino(int(m.group())), text)


def normalize(text: str, numbers: bool = False) -> str:
    text = unicodedata.normalize("NFC", text or "").lower()
    if numbers:
        text = num_to_korean(text)
    return re.sub(r"[^0-9a-z가-힣]", "", text)
