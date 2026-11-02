"""Muesli식 후처리 사전: 받아쓰기가 끝난 결과를 사전 단어와 유사도(Jaro-Winkler)로 비교해,
threshold 이상이면 사전 단어로 바꾼다. 이미 받은 전사 결과(results/*.jsonl)에 적용한다.

Muesli(CustomWordMatcher.swift)와 같은 점
- 정확히 같은 단어가 있으면 그대로 두고, 아니면 Jaro-Winkler 유사도가 threshold 이상인 사전 단어 중 가장 비슷한 것으로 바꾼다.
- 여러 단어로 된 용어는 같은 수의 어절을 묶어 비교한다.

한국어에 맞춘 점
- 어절 끝의 조사·어미를 떼고 비교한다: 마지막 어절은 사전 단어와 같은 길이의 앞부분만 비교하고, 바꿀 때도 그 앞부분만 바꾼다.
- 유사도 단위: syllable(음절, Muesli 그대로) / jamo(초·중·종성으로 쪼개 비교).
- 1음절 용어(칸 등)는 쓰지 않는다.

사용법: python postcorrect.py   → results_postcorrect/*.jsonl, results_postcorrect/changes.csv
사전: location(위치별, 그 위치 발화문 전체) / global(전체 336개) / loo(위치별에서 평가하는 그 문장을 뺀 것)
그다음 python evaluate.py results_postcorrect 로 CER·용어 인식률을 집계한다.
"""
import csv
import json
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

from metrics import edit_distance, normalize

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "results"
OUT = ROOT / "results_postcorrect"
THRESHOLDS = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
RUNS = [
    "whisper-large-v3", "whisper-large-v3-turbo", "qwen3-asr-1.7b", "qwen3-asr-0.6b",
    "cohere-transcribe", "aws-transcribe", "apple-dictation", "apple-transcriber",
    "whisper-large-v3+location", "whisper-large-v3-turbo+location", "qwen3-asr-1.7b+location",
    "qwen3-asr-0.6b+location", "aws-transcribe+location", "apple-dictation+location",
    # 누출 없는 위치별 받아쓰기 사전(loo)과 Qwen 붕괴 방지(guard)
    "whisper-large-v3+loo", "whisper-large-v3-turbo+loo", "qwen3-asr-1.7b+loo-guard", "qwen3-asr-0.6b+loo-guard",
    "qwen3-asr-1.7b+location-guard", "qwen3-asr-0.6b+location-guard",
    # CLOVA Speech: 사전 없음, 키워드 부스팅(가중치 1) 위치별·그 문장을 뺀 사전
    "clova", "clova+location-w1", "clova+loo-w1",
]
PUNCT = ".,!?;:\"'()[]{}“”‘’…·"


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
    def __init__(self, terms, unit: str, threshold: float):
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
        """cores[i:]에서 시작하는 가장 비슷한 사전 단어 -> (점수, 바꿀 어절 수, 앞부분 길이, 용어)

        Muesli처럼 어절마다 따로 비교해 모두 threshold 이상이어야 하고, 점수는 어절 점수의 평균이다.
        마지막 어절은 조사·어미를 떼기 위해 사전 단어와 같은 길이의 앞부분만 비교한다.
        """
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
        toks = text.split(" ")
        parts = [split_token(t) for t in toks]
        out, changes, i = [], [], 0
        while i < len(toks):
            if parts[i] is None:
                out.append(toks[i]); i += 1; continue
            # 여러 어절 용어는 중간 어절에 문장부호가 없을 때만 묶는다
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


LIVE_UNIT, LIVE_THRESHOLD = "jamo", 0.85  # 실시간 측정·앱에 쓸 설정 (위치별 사전과 함께)


def live_post(location_terms, text):
    """받아쓰기 직후 바로 적용하는 후처리. (교정된 문장, 교정 목록)을 돌려준다."""
    return Corrector(location_terms, LIVE_UNIT, LIVE_THRESHOLD).apply(text)


def correct_run(run):
    """실행 하나에 사전 2종 × 단위 2종 × threshold 6개를 적용하고 교정 통계를 돌려준다."""
    refs = {r["id"]: r for r in csv.DictReader(open(ROOT / "data" / "references.csv", encoding="utf-8"))}
    loc_hints = json.loads((ROOT / "data" / "hints_by_location.json").read_text(encoding="utf-8"))
    all_hints = (ROOT / "data" / "hint_terms.txt").read_text(encoding="utf-8").splitlines()
    loo_hints = json.loads((ROOT / "data" / "hints_loo.json").read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in (SRC / f"{run}.jsonl").open(encoding="utf-8")]
    stats = []
    for dic in ["location", "global", "loo"]:
        for unit in ["syllable", "jamo"]:
            for th in THRESHOLDS:
                name = f"{run}__post-{dic}-{unit}-{th:.2f}"
                better = worse = n_changes = 0
                with (OUT / f"{name}.jsonl").open("w", encoding="utf-8") as fp:
                    for r in rows:
                        ref = refs[r["id"]]
                        terms = {"location": loc_hints[ref["location"]], "global": all_hints,
                                 "loo": loo_hints[r["id"]]}[dic]
                        hyp, changes = Corrector(terms, unit, th).apply(r["hyp"])
                        n_changes += len(changes)
                        if changes:
                            rn = normalize(ref["text"], True)
                            before = edit_distance(rn, normalize(r["hyp"], True))
                            after = edit_distance(rn, normalize(hyp, True))
                            better += after < before
                            worse += after > before
                        fp.write(json.dumps({**r, "hyp": hyp}, ensure_ascii=False) + "\n")
                stats.append({"run": name, "base_run": run, "dict": dic, "unit": unit, "threshold": th,
                              "교정 횟수": n_changes, "좋아진 발화": better, "나빠진 발화": worse})
    return stats


def main():
    OUT.mkdir(exist_ok=True)
    with ProcessPoolExecutor(max_workers=6) as ex:
        change_rows = [row for stats in ex.map(correct_run, RUNS) for row in stats]
    with open(OUT / "changes.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=list(change_rows[0]))
        w.writeheader()
        w.writerows(change_rows)
    print(f"{len(change_rows)}개 조건 생성 → {OUT}")


if __name__ == "__main__":
    main()
