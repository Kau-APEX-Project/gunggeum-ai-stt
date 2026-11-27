"""GPT-6 Luna 한 번 호출로 받아쓰기 교정과 번역을 함께 하고(Amazon Translate 대신), 번역문의 용어 표기를 잰다.

Luna 에 넣는 것: 한국어 문장 + 그 위치의 용어 목록(hints_by_location.json)과 문장에서 찾은 용어, 각 용어의 그 언어 표기(용어 표).
Luna 가 내는 것: {"corrected": 고친 한국어, "translation": 번역문} (답 모양 강제, 추론 끔 — 교정 측정과 같은 설정).

  python luna_translate_eval.py --be <api 폴더> --set ref300|quiet|noise [--lang en th] [--cap 1.5] [--limit N]
  python luna_translate_eval.py --be <api 폴더> --report        저장된 결과로 채점만 (과금 없음)

  --be     용어 표(data/translate_terms.csv)와 묶음 표기 판정(app.live.translate)을 읽을 api 폴더
           (gunggeum-be develop + gunggeum-ai-stt main 의 나눈 용어 표 — 태국어 93.0% 를 잰 조건)
  ref300   엑셀 해설 300문장 정답 그대로 (번역 실력: Amazon Translate 300문장 결과와 비교)
  quiet    조용한 녹음 180문장, Transcribe(위치별 단어집) → 서버 교정까지 거친 문장 (11/22 Luna 교정 측정의 B 단계)
  noise    소음 합성 7조건 × 180문장, 같은 방식
키는 ~/.config/gungeum/openai.env 에서 읽기만 함. 비용 상한(--cap, 한 실행의 두 언어 합)을 넘으면 멈춤.
  --mode wrap  Luna 를 번역기 자리에만 넣음: 서버 코드(AwsTranslator, 기본 방식)가 용어를 번역 제외 표시로 감싸고
               연도를 감싼 뒤 Luna 가 번역하고, 서버 코드가 표시를 그 언어 표기로 되돌림. 교정은 따로(서버 교정 → Luna 교정,
               11/22 결과의 D 단계)를 넣음. 결과 이름 <set>_<lang>_wrap_raw.jsonl
결과: results_luna_translate/<set>_<lang>[_wrap]_raw.jsonl, summary.csv
"""
import asyncio
import os
import argparse
import csv
import json
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python -I 로 돌려도 같은 폴더의 모듈을 찾게
import notice_translate_eval as N  # noqa: E402
import translate_terms_eval as T  # noqa: E402
from metrics import edit_distance, normalize

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results_luna_translate"
MODEL = "gpt-6-luna"
PRICE = (0.10, 0.50)  # 달러 / 100만 토큰 (입력, 출력)
NOISE = ["babble20", "babble10", "babble5", "babble0", "talker10", "talker5", "talker0"]
LANG = {"en": "영어", "th": "태국어"}
HANGUL = re.compile(r"[가-힣]+")
LATIN = re.compile(r"[A-Za-z]{2,}")

SYSTEM = ("너는 경복궁 해설을 음성 인식한 한국어 문장을 받아 고친 뒤 {lang}로 번역하는 번역기다.\n"
          "1) 교정: 용어 목록에 있는 말이 소리가 비슷한 다른 말로 잘못 받아써졌으면 목록의 한국어 표기로 고친다. "
          "그 밖의 낱말, 띄어쓰기, 숫자는 바꾸지 않는다. 고칠 것이 없으면 그대로 둔다.\n"
          "2) 번역: 고친 문장을 {lang}로 자연스럽게 번역한다. 용어 목록에서 '→' 뒤에 표기가 있는 말은 그 표기를 괄호까지 글자 그대로 쓴다. "
          "문장에 없는 내용을 덧붙이거나 빼지 않는다. 번역문에 한국어를 남기지 않는다.")
SCHEMA = {"type": "object", "properties": {"corrected": {"type": "string"}, "translation": {"type": "string"}},
          "required": ["corrected", "translation"], "additionalProperties": False}
WRAP_SYSTEM = ("너는 경복궁 해설 자막 번역기다. 한국어 문장을 {lang}로 자연스럽게 번역한다. "
               "<span translate=\"no\">…</span> 으로 감싼 부분은 표시와 안의 글자를 바꾸지 않고 번역문의 알맞은 자리에 그대로 둔다. "
               "문장에 없는 내용을 덧붙이거나 빼지 않는다. 번역문에 한국어를 남기지 않는다.")
WRAP_SCHEMA = {"type": "object", "properties": {"translation": {"type": "string"}},
               "required": ["translation"], "additionalProperties": False}
SPENT = {"in": 0, "out": 0}
LOCK = threading.Lock()
_key = None


def cost():
    return (SPENT["in"] * PRICE[0] + SPENT["out"] * PRICE[1]) / 1e6


def glossary(text, location, lang, table, hints):
    """그 위치의 용어 + 문장에서 찾은 용어(용어 표의 두 글자 이상 이름). 표에 표기가 있으면 '한국어 → 표기'"""
    names = list(hints.get(location, []))
    names += [k for k in sorted(table, key=len, reverse=True) if len(k) >= 2 and k in text and k not in names]
    return "\n".join(f"{k} → {table[k][lang]}" if table.get(k, {}).get(lang) else k for k in names)


def luna(text, location, lang, table, hints, cap):
    """교정+번역 한 번 (용어 표기는 지시문으로만 줌)"""
    d, sec = responses(SYSTEM.format(lang=LANG[lang]), f"용어 목록:\n{glossary(text, location, lang, table, hints)}\n\n문장: {text}", SCHEMA, cap)
    return d["corrected"].strip(), d["translation"].strip(), sec


def responses(instructions, user, schema, cap):
    """OpenAI Responses API, 추론 끔, 답 모양 강제 → (답 dict, 초)"""
    global _key
    if _key is None:
        env = Path.home() / ".config" / "gungeum" / "openai.env"
        _key = next(l.split("=", 1)[1].strip().strip("\"'") for l in env.read_text().splitlines() if l.startswith("OPENAI_API_KEY="))
    body = {"model": MODEL, "reasoning": {"effort": "none"}, "max_output_tokens": 600, "instructions": instructions, "input": user,
            "text": {"format": {"type": "json_schema", "name": "out", "schema": schema, "strict": True}}}
    req = urllib.request.Request("https://api.openai.com/v1/responses", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {_key}"})
    for attempt in range(6):
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                res = json.load(r)
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            code = getattr(e, "code", None)
            if (code is not None and code not in (429, 500, 502, 503)) or attempt == 5:
                raise SystemExit(f"OpenAI 오류 {code}: {e.read()[:300]!r}" if code else f"OpenAI 연결 오류: {e}")
            time.sleep(2 ** attempt)
    sec = time.perf_counter() - t
    with LOCK:
        SPENT["in"] += res["usage"]["input_tokens"]
        SPENT["out"] += res["usage"]["output_tokens"]
        if cost() > cap:
            raise SystemExit(f"비용 상한 {cap}달러를 넘어 멈춤 (지금까지 {cost():.4f}달러)")
    out = "".join(c["text"] for o in res["output"] if o["type"] == "message" for c in o["content"] if c["type"] == "output_text")
    return json.loads(out), sec


class LunaClient:
    """서버 AwsTranslator 가 부르는 boto3 translate_text 자리에 Luna 를 끼움"""

    def __init__(self, cap):
        self.cap = cap

    def translate_text(self, Text, SourceLanguageCode, TargetLanguageCode, **_kw):
        d, _sec = responses(WRAP_SYSTEM.format(lang=LANG[TargetLanguageCode]), Text, WRAP_SCHEMA, self.cap)
        return {"TranslatedText": d["translation"].strip()}


def inputs(name, key="B"):
    """[(cond, id, 넣을 한국어, 위치, 정답 한국어)]. key: 11/22 결과의 어느 단계를 넣을지 (B 서버 교정, D 서버 교정 → Luna 교정)"""
    items = T.load_items()
    if name == "ref300":
        return [("ref300", it["id"], it["text"], it["location"], it["text"]) for it in items]
    refs = {r["id"]: r for r in csv.DictReader((ROOT / "data" / "references.csv").open(encoding="utf-8"))}
    conds = ["quiet"] if name == "quiet" else NOISE
    rows = [json.loads(l) for l in (ROOT / "results_slm" / "gpt-6-luna+json_raw.jsonl").open(encoding="utf-8")]
    return [(r["cond"], r["id"], r[key], refs[r["id"]]["location"], refs[r["id"]]["text"]) for r in rows if r["cond"] in conds]


def run(a, lang, table, hints):
    OUT.mkdir(exist_ok=True)
    wrap = a.mode == "wrap"
    path = OUT / f"{a.set}_{lang}{'_wrap' if wrap else ''}_raw.jsonl"
    done = {(r["cond"], r["id"]) for r in map(json.loads, path.open(encoding="utf-8"))} if path.exists() else set()
    todo = [x for x in inputs(a.set, "D" if wrap else "B") if (x[0], x[1]) not in done]
    if a.limit:
        todo = todo[:a.limit]
    if wrap:
        for k in ("TRANSLATE_TERM_MODE", "TRANSLATE_TERMS_PATH", "TRANSLATE_TERMINOLOGY"):
            os.environ.pop(k, None)  # 서버 기본값 (번역 제외 표시 방식, --be 의 용어 표)
        client = LunaClient(a.cap)
        tr = N.load(a.be).AwsTranslator(client)

    def one(x):
        cond, uid, text, loc, ref = x
        if wrap:
            t = time.perf_counter()  # 서버 코드의 감싸기·되돌리기 포함 (연도를 지어내면 한 번 더 부름)
            out = asyncio.run(tr.translate(text, lang))
            corrected, sec = text, time.perf_counter() - t
        else:
            corrected, out, sec = luna(text, loc, lang, table, hints, a.cap)
        return {"cond": cond, "id": uid, "location": loc, "ref": ref, "input": text, "corrected": corrected, "out": out, "sec": round(sec, 3)}

    with path.open("a", encoding="utf-8") as fp, ThreadPoolExecutor(6) as ex:
        for i, row in enumerate(ex.map(one, todo), 1):
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
            fp.flush()
            if i % 100 == 0:
                print(f"{i}/{len(todo)} | 입력 {SPENT['in']} 출력 {SPENT['out']} 토큰, {cost():.4f}달러", flush=True)
    print(f"{a.set} {lang} {len(todo)}문장 끝 | 입력 {SPENT['in']} 출력 {SPENT['out']} 토큰, {cost():.4f}달러", flush=True)


def score_rows(rows, by_text, table, mod, lang):
    """용어 표기 일치(전체 / 묶음 표기 뺀 것), 한글·라틴 글자가 남은 문장"""
    n = ok = nb = okb = hangul = latin = markup = 0
    for r in rows:
        out = T.norm(r["out"])
        for e in T.scorable(by_text[r["ref"].strip()]):
            row = table.get(e["ko"])
            if not row or not row[lang]:
                continue
            hit = T.norm(row[lang]) in out
            n += 1
            ok += hit
            if not mod.is_bundle(row["en"]):
                nb += 1
                okb += hit
        hangul += bool(HANGUL.search(r["out"]))
        markup += bool(N.MARKUP.search(r["out"]))
        latin += bool(lang == "th" and LATIN.search(r["out"]))
    return {"항목 수": n, "용어 일치율(%)": round(100 * ok / n, 1), "묶음 표기 뺀 일치율(%)": round(100 * okb / nb, 1),
            "한글이 남은 문장": hangul, "라틴 글자가 남은 문장(태국어)": latin if lang == "th" else "", "표시가 남은 문장": markup}


def cer(rows, key):
    e = n = 0
    for r in rows:
        ref = normalize(r["ref"], True)
        e += edit_distance(ref, normalize(r[key], True))
        n += len(ref)
    return round(100 * e / n, 2)


def report(be):
    mod = N.load(be)
    table = {r["ko"].strip(): {"en": (r["en"] or "").strip(), "th": (r["th"] or "").strip()}
             for r in csv.DictReader((be / "data" / "translate_terms.csv").open(encoding="utf-8-sig"))}
    by_text = {it["text"].strip(): it for it in T.load_items()}
    refs = {r["id"]: r for r in csv.DictReader((ROOT / "data" / "references.csv").open(encoding="utf-8"))}
    rec_texts = {r["text"].strip() for r in refs.values()}
    luna_fix = {(r["cond"], r["id"]): r["D"] for r in map(json.loads, (ROOT / "results_slm" / "gpt-6-luna+json_raw.jsonl").open(encoding="utf-8"))}
    summary = []
    for lang in ("en", "th"):
        # 기준: Amazon Translate 가 정답 문장을 번역한 기존 결과 (태국어 11/23 나눈 표, 영어 11/22 나누기 전 표로 번역)
        old = "th_split" if lang == "th" else "en_pr19"
        base = [{"ref": r["src"], "out": r["out"]} for r in map(json.loads, (ROOT / "results_translate" / f"server300_raw_{old}.jsonl").open(encoding="utf-8"))]
        for label, rows in (("300문장", base), ("녹음 180문장", [r for r in base if r["ref"].strip() in rec_texts])):
            summary.append({"언어": lang, "조건": f"정답 {label}", "방식": f"Amazon Translate (기존 결과 {old})", "문장": len(rows),
                            **score_rows(rows, by_text, table, mod, lang)})
        for s, mode in [(s, m) for m in ("", "_wrap") for s in ("ref300", "quiet", "noise")]:
            p = OUT / f"{s}_{lang}{mode}_raw.jsonl"
            if not p.exists():
                continue
            allrows = [json.loads(l) for l in p.open(encoding="utf-8")]
            groups = [("정답 300문장", allrows), ("정답 녹음 180문장", [r for r in allrows if r["ref"].strip() in rec_texts])] if s == "ref300" \
                else [(c, [r for r in allrows if r["cond"] == c]) for c in (["quiet"] if s == "quiet" else NOISE)]
            for label, rows in groups:
                if not rows:
                    continue
                extra = {"한국어 글자 오류율(%) Luna 교정만(11/22)": cer([{**r, "D": luna_fix[(r["cond"], r["id"])]} for r in rows], "D")} if s != "ref300" else {}
                summary.append({"언어": lang, "조건": label, "방식": "서버 용어 고정 + Luna 번역 (교정은 따로)" if mode else "Luna 교정+번역 한 번", "문장": len(rows),
                                **score_rows(rows, by_text, table, mod, lang),
                                "한국어 글자 오류율(%) 넣은 문장": cer(rows, "input"), "한국어 글자 오류율(%) Luna 고친 문장": cer(rows, "corrected"), **extra,
                                "평균(초)": round(statistics.mean(r["sec"] for r in rows), 2),
                                "p90(초)": sorted(r["sec"] for r in rows)[int(0.9 * (len(rows) - 1))]})
    fields = list(dict.fromkeys(k for s in summary for k in s))
    with (OUT / "summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(summary)
    for s in summary:
        print(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--be", required=True, type=Path)
    ap.add_argument("--set", choices=("ref300", "quiet", "noise"))
    ap.add_argument("--lang", nargs="+", choices=("en", "th"), default=["en", "th"])
    ap.add_argument("--cap", type=float, default=1.5, help="이 실행(두 언어 합)의 비용 상한(달러)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--mode", choices=("once", "wrap"), default="once", help="once 교정+번역 한 번 / wrap 서버 용어 고정 코드 안에서 번역만")
    a = ap.parse_args()
    if a.report:
        return report(a.be)
    table = {r["ko"].strip(): {"en": (r["en"] or "").strip(), "th": (r["th"] or "").strip()}
             for r in csv.DictReader((a.be / "data" / "translate_terms.csv").open(encoding="utf-8-sig"))}
    hints = json.loads((ROOT / "data" / "hints_by_location.json").read_text(encoding="utf-8"))
    for lang in a.lang:
        run(a, lang, table, hints)


if __name__ == "__main__":
    sys.exit(main())
