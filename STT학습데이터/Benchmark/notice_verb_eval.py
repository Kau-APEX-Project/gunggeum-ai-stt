"""공지에서 용어가 '하다·되다'와 붙어 동사로 쓰인 줄('대비해', '조정됩니다')을 감싸지 않으면 나아지는지 시험한다.
공지 예문 30개 중 해당하는 줄만 골라 영어·태국어로 지금 방식과 '동사로 쓰인 용어는 감싸지 않음' 방식을 비교한다.
  python notice_verb_eval.py --be <gunggeum-be/api>
결과: results_notice/verb_raw.jsonl
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import notice_translate_eval as N
import verb_term_eval as V

ROOT = Path(__file__).resolve().parent


def main(be):
    os.environ.setdefault("TRANSLATE_PROVIDER", "aws")
    for k in ("TRANSLATE_TERM_MODE", "TRANSLATE_TERMS_PATH", "TRANSLATE_TERMINOLOGY"):
        os.environ.pop(k, None)
    mod = N.load(Path(be))
    table = mod.glossary()
    lines = []
    for n in map(json.loads, (ROOT / "data" / "notice_samples.jsonl").open(encoding="utf-8")):
        for f in ("title", "body"):
            for line in n[f].split("\n"):
                if V.verb_terms(mod, line, table, "en"):
                    lines.append((n["id"], f, line))
    import boto3
    print("계정", boto3.client("sts").get_caller_identity()["Account"], flush=True)
    client = N.Client(boto3.client("translate", region_name="ap-northeast-2"), {})
    tr = mod.AwsTranslator(client)
    orig, each = V.skip_verbs(mod)
    rows = []
    for lang in ("en", "th"):
        for nid, f, line in lines:
            mod._each_term = orig
            now = asyncio.run(tr.translate(line, lang))
            mod._each_term = each
            new = asyncio.run(tr.translate(line, lang))
            mod._each_term = orig
            rows.append({"id": nid, "field": f, "lang": lang, "src": line, "now": now, "skip_verb": new})
            print(f"[{nid} {lang}] {line}\n  지금    : {now}\n  감싸지 않음: {new}", flush=True)
    print(f"보낸 글자 {client.chars}자 (약 {client.chars * 15 / 1e6:.4f}달러)", flush=True)
    with (ROOT / "results_notice" / "verb_raw.jsonl").open("w", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--be") + 1])
