"""해설 전사문을 외국어 자막으로 바꾸는 번역 방식 비교 측정.

입력
  data/references.csv                       정답 문장 180개 (깨끗한 전사)
  results_postcorrect/aws-transcribe+location__post-location-jamo-0.85.jsonl   Transcribe 전사 결과(오류 포함) — 실제 자막 입력
  data/glossary_en.csv                      용어집 영문 표기 (235개) — 용어 번역 정답·사용자 지정 용어집 원본

엔진 (--engine, 여러 번 지정 가능)
  translate            Amazon Translate (서울 리전), 용어집 없이
  translate+term       Amazon Translate + 사용자 지정 용어집(gungeum-ko-en, --create-terminology로 1회 생성; 영어만)
  bedrock:<modelId>    Bedrock Converse API. 시스템 프롬프트에 자막 규칙과 문장에 나온 용어의 영문 표기를 넣는다.

사용법
  python translate_eval.py --create-terminology
  python translate_eval.py --engine translate --engine translate+term --engine bedrock:<id> --lang en --lang th [--src ref|stt] [--limit N]
  결과: results_translate/<engine>__<src>__<lang>.jsonl  {id, src, out, ms, in_tok, out_tok}
  python translate_eval.py --eval  → results_translate/summary.csv (용어 일치율·지연·토큰), judge는 judge_translate.py
"""
import argparse
import csv
import json
import re
import statistics
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results_translate"
REGION = "ap-northeast-2"
TERMINOLOGY = "gungeum-ko-en"
STT_RUN = ROOT / "results_postcorrect" / "aws-transcribe+location__post-location-jamo-0.85.jsonl"
LANG_NAME = {"en": "English", "th": "Thai"}

SYSTEM = """You translate a Korean tour guide's live commentary at Gyeongbokgung Palace into {lang} subtitles for foreign visitors.
Rules:
- Output only the translated subtitle text, one line, no notes or quotes.
- Keep it faithful and natural; the input is live speech-to-text and may contain small recognition errors — translate the most plausible intended meaning.
- Proper names of gates, halls and places: use the official romanized name plus a short English descriptor the first time (e.g. Gwanghwamun Gate, Geunjeongjeon Hall). In Thai, write the romanized name in Latin letters followed by a Thai descriptor.
- For the glossary terms below, use exactly the given rendering.
Glossary:
{glossary}"""


def load_refs():
    return list(csv.DictReader(open(ROOT / "data" / "references.csv", encoding="utf-8")))


def load_glossary():
    return list(csv.DictReader(open(ROOT / "data" / "glossary_en.csv", encoding="utf-8")))


def sentence_terms(ref, glossary):
    """문장의 '포함 용어'·'전각명' 중 용어집에 영문 표기가 있는 것."""
    cells = (ref.get("terms") or "") + "," + (ref.get("buildings") or "")
    names = {t.strip() for t in re.split(r"[,/]", cells) if t.strip()}
    return [g for g in glossary if g["term"] in names or g["term"] in ref["text"]]


def create_terminology():
    gl = load_glossary()
    body = "ko,en\n" + "\n".join(f'"{g["term"]}","{g["en"]}"' for g in gl)
    tr = boto3.client("translate", region_name=REGION)
    tr.import_terminology(Name=TERMINOLOGY, MergeStrategy="OVERWRITE",
                          TerminologyData={"File": body.encode("utf-8"), "Format": "CSV", "Directionality": "UNI"})
    print(f"용어집 {TERMINOLOGY} 등록: {len(gl)}개")


def run_translate(client, text, lang, term):
    kw = {"TerminologyNames": [TERMINOLOGY]} if term and lang == "en" else {}
    t0 = time.perf_counter()
    r = client.translate_text(Text=text, SourceLanguageCode="ko", TargetLanguageCode=lang, **kw)
    return r["TranslatedText"], (time.perf_counter() - t0) * 1000, 0, 0


def run_bedrock(client, model, text, lang, glossary_lines):
    sysmsg = SYSTEM.format(lang=LANG_NAME[lang], glossary="\n".join(glossary_lines) or "(none)")
    t0 = time.perf_counter()
    r = client.converse(modelId=model, system=[{"text": sysmsg}],
                        messages=[{"role": "user", "content": [{"text": text}]}],
                        inferenceConfig={"maxTokens": 300, "temperature": 0})
    out = "".join(c.get("text", "") for c in r["output"]["message"]["content"]).strip()
    u = r.get("usage", {})
    return out, (time.perf_counter() - t0) * 1000, u.get("inputTokens", 0), u.get("outputTokens", 0)


def run(args):
    refs = load_refs()
    gl = load_glossary()
    stt = {json.loads(l)["id"]: json.loads(l)["hyp"] for l in open(STT_RUN, encoding="utf-8")}
    OUT.mkdir(exist_ok=True)
    tr = boto3.client("translate", region_name=REGION)
    br = boto3.client("bedrock-runtime", region_name=REGION)
    for engine in args.engine:
        for lang in args.lang:
            if engine == "translate+term" and lang != "en":
                continue
            path = OUT / f"{engine.replace(':', '_').replace('/', '_')}__{args.src}__{lang}.jsonl"
            done = {json.loads(l)["id"] for l in path.open(encoding="utf-8")} if path.exists() else set()
            with path.open("a", encoding="utf-8") as fp:
                for i, r in enumerate(refs[:args.limit or None], 1):
                    if r["id"] in done:
                        continue
                    text = r["text"] if args.src == "ref" else stt[r["id"]]
                    if engine.startswith("translate"):
                        out, ms, it, ot = run_translate(tr, text, lang, engine.endswith("+term"))
                    else:
                        lines = [f"- {g['term']}: {g['en']}" for g in sentence_terms(r, gl)]
                        out, ms, it, ot = run_bedrock(br, engine.split(":", 1)[1], text, lang, lines)
                    fp.write(json.dumps({"id": r["id"], "src": text, "out": out, "ms": round(ms, 1), "in_tok": it, "out_tok": ot}, ensure_ascii=False) + "\n")
                    fp.flush()
                    if i % 30 == 0:
                        print(f"[{path.stem}] {i}", flush=True)
            print(f"[{path.stem}] 완료")


def term_hit(out, en):
    """용어집 영문 표기의 머리말(괄호 앞 첫 단어들)이 출력에 있으면 적중. 예: 'Jeongjeon (main hall...)' → Jeongjeon"""
    head = re.split(r"[(\[/,]", en)[0].strip()
    head = re.sub(r"^(the|a)\s+", "", head, flags=re.I)
    return head.lower() in out.lower()


def evaluate():
    refs = {r["id"]: r for r in load_refs()}
    gl = load_glossary()
    rows = []
    for path in sorted(OUT.glob("*.jsonl")):
        recs = [json.loads(l) for l in open(path, encoding="utf-8")]
        hits = tot = 0
        for rec in recs:
            for g in sentence_terms(refs[rec["id"]], gl):
                tot += 1
                hits += term_hit(rec["out"], g["en"])
        ms = [r["ms"] for r in recs]
        ms_sorted = sorted(ms)
        rows.append({"run": path.stem, "n": len(recs),
                     "용어일치(%)": round(100 * hits / tot, 1) if tot else "", "용어수": tot,
                     "지연_평균(ms)": round(statistics.mean(ms)), "지연_p90(ms)": round(ms_sorted[int(len(ms) * 0.9) - 1]),
                     "입력토큰/문장": round(statistics.mean(r["in_tok"] for r in recs)), "출력토큰/문장": round(statistics.mean(r["out_tok"] for r in recs)),
                     "원문글자/문장": round(statistics.mean(len(r["src"]) for r in recs)), "빈출력": sum(1 for r in recs if not r["out"].strip())})
        print(rows[-1])
    with open(OUT / "summary.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", action="append", default=[])
    ap.add_argument("--lang", action="append", default=[])
    ap.add_argument("--src", choices=["ref", "stt"], default="stt")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--create-terminology", action="store_true")
    ap.add_argument("--eval", action="store_true")
    args = ap.parse_args()
    if args.create_terminology:
        create_terminology()
    elif args.eval:
        evaluate()
    else:
        run(args)


if __name__ == "__main__":
    main()
