"""용어가 '하다·되다'와 붙어 동사로 쓰일 때('훼철됐다', '봉안했습니다') 번역 제외 표시로 감싸지 않으면 나아지는지 시험한다.

해설 300문장 중 서버가 감싸는 용어 바로 뒤가 하다·되다 활용형인 문장만 골라, 영어·태국어로 두 가지 번역한다.
  지금     : 백엔드 서버 방식 그대로 (notranslate)
  동사 제외 : 같은 방식인데, 용어 바로 뒤가 하다·되다 활용형이면 그 용어는 감싸지 않음
백엔드 코드는 바꾸지 않고 이 스크립트 안에서만 바꿔 끼운다.

  python verb_term_eval.py --be <gunggeum-be/api> [--dry]
결과: results_translate/verbterm_raw.jsonl
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import notice_translate_eval as N
import translate_terms_eval as T

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results_translate"
VERB = re.compile(r"(하|했|한|할|함|합|해|되|됐|된|될|됨|됩|돼)")  # 하다·되다 활용형의 첫 글자


def verb_terms(mod, text, table, lang):
    """감싸질 용어 중 바로 뒤가 하다·되다 활용형인 것"""
    return [t for _h, t, rest in mod._each_term(text, mod.term_table(lang, table)) if t and VERB.match(rest)]


def skip_verbs(mod):
    """mod.wrap_terms 를 '동사로 쓰인 용어는 감싸지 않음'으로 바꿈 (원래 함수는 돌려줌)"""
    orig = mod._each_term

    def each(text, terms):
        for head, term, rest in orig(text, terms):
            if term and VERB.match(rest):
                yield head, None, term + rest
            else:
                yield head, term, rest
    return orig, each


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--be", required=True)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("TRANSLATE_PROVIDER", "aws")
    for k in ("TRANSLATE_TERM_MODE", "TRANSLATE_TERMS_PATH", "TRANSLATE_TERMINOLOGY"):
        os.environ.pop(k, None)
    mod = N.load(Path(a.be))
    table = mod.glossary()
    items = [it for it in T.load_items() if verb_terms(mod, it["text"], table, "en")]
    real = None
    if not a.dry:
        import boto3
        print("계정", boto3.client("sts").get_caller_identity()["Account"], flush=True)
        real = boto3.client("translate", region_name="ap-northeast-2")
    client = N.Client(real, {})
    tr = mod.AwsTranslator(client)
    orig, each = skip_verbs(mod)
    rows = []
    for lang in ("en", "th"):
        for it in items:
            mod._each_term = orig
            now = asyncio.run(tr.translate(it["text"], lang))
            mod._each_term = each
            new = asyncio.run(tr.translate(it["text"], lang))
            mod._each_term = orig
            rows.append({"id": it["id"], "lang": lang, "src": it["text"], "verbs": verb_terms(mod, it["text"], table, lang),
                         "now": now, "skip_verb": new})
    print(f"문장 {len(items)}개 × 2언어 | 보낸 글자 {client.chars}자 (약 {client.chars * 15 / 1e6:.4f}달러)" + (" — 세기만 함" if a.dry else ""), flush=True)
    if a.dry:
        return
    with (OUT / "verbterm_raw.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__" and "--plain" not in sys.argv:
    main()


# ── 2차: 동사로 쓰인 용어를 감싸지 않고 '하다'가 붙는 쉬운 한국어 낱말로 바꿔 넣기 ──
#   (1차에서 그냥 두면 번역기가 '승하'를 '이기다(won)'로 옮기는 등 뜻이 틀려서)
#   python verb_term_eval.py --be <gunggeum-be/api> --plain
PLAIN = {"승하": "사망", "훼철": "철거", "집자": "수집", "대리청정": "대리 통치", "봉안": "보관", "모사": "복제"}


def plain_verbs(orig):
    def each(text, terms):
        for head, term, rest in orig(text, terms):
            if term and VERB.match(rest):
                yield head, None, PLAIN.get(term, term) + rest
            else:
                yield head, term, rest
    return each


def main_plain(be):
    os.environ.setdefault("TRANSLATE_PROVIDER", "aws")
    for k in ("TRANSLATE_TERM_MODE", "TRANSLATE_TERMS_PATH", "TRANSLATE_TERMINOLOGY"):
        os.environ.pop(k, None)
    mod = N.load(Path(be))
    import boto3
    print("계정", boto3.client("sts").get_caller_identity()["Account"], flush=True)
    client = N.Client(boto3.client("translate", region_name="ap-northeast-2"), {})
    tr = mod.AwsTranslator(client)
    orig = mod._each_term
    each = plain_verbs(orig)
    path = OUT / "verbterm_raw.jsonl"
    rows = [json.loads(l) for l in path.open(encoding="utf-8")]
    for r in rows:
        mod._each_term = each
        r["plain_verb"] = asyncio.run(tr.translate(r["src"], r["lang"]))
        mod._each_term = orig
    print(f"보낸 글자 {client.chars}자 (약 {client.chars * 15 / 1e6:.4f}달러)", flush=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__" and "--plain" in sys.argv:
    main_plain(sys.argv[sys.argv.index("--be") + 1])
