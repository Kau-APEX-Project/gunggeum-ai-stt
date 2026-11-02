"""후처리 사전: 받아쓰기 결과를 위치별 용어 목록과 자모 단위 Jaro-Winkler 유사도로 비교해 0.85 이상이면 용어로 바꾼다.

STT 벤치마크(STT학습데이터/Benchmark/postcorrect.py)에서 고른 설정 그대로: 자모 단위, threshold 0.85, 위치별 사전.
- 정확히 같은 단어는 그대로 둔다.
- 여러 어절 용어는 어절마다 비교해 모두 threshold 이상이어야 하고 점수는 평균.
- 마지막 어절은 조사·어미를 떼기 위해 용어와 같은 길이의 앞부분만 비교·교체한다.
- 1음절 용어는 쓰지 않는다. 1발화 약 0.5 ms.
"""
from functools import lru_cache

PUNCT = ".,!?;:\"'()[]{}“”‘’…·"
UNIT, THRESHOLD = "jamo", 0.85


@lru_cache(maxsize=None)
def jamo(s: str) -> str:
    out = []
    for ch in s:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out += [chr(0x1100 + code // 588), chr(0x1161 + code % 588 // 28)]
            if code % 28:
                out.append(chr(0x11A7 + code % 28))
        else:
            out.append(ch)
    return "".join(out)


@lru_cache(maxsize=None)
def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(max(len(a), len(b)) // 2 - 1, 0)
    a_hit, b_hit = [False] * len(a), [False] * len(b)
    m = 0
    for i, ca in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not b_hit[j] and b[j] == ca:
                a_hit[i] = b_hit[j] = True
                m += 1
                break
    if not m:
        return 0.0
    bs = [b[j] for j in range(len(b)) if b_hit[j]]
    t = sum(ca != bs[k] for k, ca in enumerate(c for i, c in enumerate(a) if a_hit[i])) / 2
    jaro = (m / len(a) + m / len(b) + (m - t) / m) / 3
    prefix = 0
    for ca, cb in zip(a[:4], b[:4]):
        if ca != cb:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def split_token(tok: str):
    core = tok.strip(PUNCT)
    if not core:
        return None
    start = tok.find(core)
    return tok[:start], core, tok[start + len(core):]


class Corrector:
    def __init__(self, terms, unit: str = UNIT, threshold: float = THRESHOLD):
        self.unit, self.threshold = unit, threshold
        self.entries = []
        for t in dict.fromkeys(terms):
            words = t.split()
            if len("".join(words)) < 2:
                continue
            self.entries.append((t, words, "".join(words)))

    def score(self, cand: str, target: str) -> float:
        if self.unit == "jamo":
            return jaro_winkler(jamo(cand), jamo(target))
        return jaro_winkler(cand, target)

    def best_at(self, cores, i):
        best = None
        for term, words, target in self.entries:
            k = len(words)
            if i + k > len(cores) or len(cores[i + k - 1]) < len(words[-1]):
                continue
            n = len(words[-1])
            cands = cores[i:i + k - 1] + [cores[i + k - 1][:n]]
            scores = [1.0 if c == w else self.score(c, w) for c, w in zip(cands, words)]
            if min(scores) < self.threshold:
                continue
            s = sum(scores) / k
            if best is None or s > best[0] or (s == best[0] and len(target) > len(best[3].replace(" ", ""))):
                best = (s, k, n, term)
        return best

    def apply(self, text: str):
        """(교정된 문장, [(원래, 용어, 점수), ...])"""
        toks = text.split(" ")
        parts = [split_token(t) for t in toks]
        out, changes, i = [], [], 0
        while i < len(toks):
            if parts[i] is None:
                out.append(toks[i]); i += 1; continue
            run = []
            for p in parts[i:]:
                if p is None:
                    break
                run.append(p)
                if p[2]:
                    break
            best = self.best_at([p[1] for p in run], 0)
            if best is None:
                out.append(toks[i]); i += 1; continue
            s, k, n, term = best
            first, last = run[0], run[k - 1]
            original = " ".join(p[1] for p in run[:k - 1] + [(None, last[1][:n], None)]).strip()
            if s == 1.0 or original.replace(" ", "") == term.replace(" ", ""):
                out += toks[i:i + k]; i += k; continue
            new = first[0] + term + last[1][n:] + last[2]
            changes.append((original, term, round(s, 3)))
            out.append(new); i += k
        return " ".join(out), changes


_cache: dict[tuple, Corrector] = {}


def correct(terms, text: str):
    """위치별 용어 목록으로 문장을 교정한다. Corrector는 용어 목록별로 재사용."""
    key = tuple(terms)
    if key not in _cache:
        _cache[key] = Corrector(terms)
    return _cache[key].apply(text)
