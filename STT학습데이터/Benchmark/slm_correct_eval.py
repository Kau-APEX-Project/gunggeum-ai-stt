"""받아쓰기 → 서버 교정(자주 틀리는 표기 표 + 위치별 교정 사전) → 작은 언어 모델 보정을 단계별로 잰다.

Transcribe(위치별 단어집)로 이미 받아 둔 결과를 쓰므로 Transcribe 요금은 없다.
언어 모델은 이 맥의 llama-server(OpenAI 호환 /v1/chat/completions)로 부른다. 사고(thinking)는 끈다(실시간 자막용).

  llama-server -m <LFM3-1.7B-Q8_0.gguf> --port 8091 -ngl 99 -c 4096
  python slm_correct_eval.py --be ../../gunggeum-be/api [--only quiet] [--model-name LFM3-1.7B-Q8_0]

조건: quiet(조용한 녹음 180문장), 소음 합성 7조건(babble20/10/5/0, talker10/5/0),
      field152·field153(11/8 경복궁 현장 녹음, 11/9 팀 계정 Transcribe 결과 results_slm/field_transcribe_20261109.jsonl, 전각별 구간)
단계: A 받아쓰기 그대로 / B A+서버 교정 / C A+언어 모델 / D B+언어 모델
결과: results_slm/<모델>_raw.jsonl (문장마다 단계별 문장·처리 시간), <모델>_summary.csv, <모델>_changes.csv
"""
import argparse
import csv
import json
import statistics
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from metrics import edit_distance, normalize, term_groups, term_hits

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results_slm"
NOISE = ["babble20", "babble10", "babble5", "babble0", "talker10", "talker5", "talker0"]

SYSTEM = ("너는 경복궁 해설을 음성 인식한 문장을 고치는 교정기다. "
          "용어 목록에 있는 말이 소리가 비슷한 다른 말로 잘못 받아써졌으면 목록의 표기로 고친다. "
          "그 밖의 낱말, 띄어쓰기, 문장부호, 숫자는 바꾸지 않는다. 고칠 것이 없으면 문장을 그대로 낸다. "
          "설명 없이 고친 문장 한 줄만 출력한다.")


def load_inputs(only):
    """조건별 [(id, 받아쓰기 원문)]"""
    sets = {}
    if only in (None, "quiet"):
        sets["quiet"] = [(r["id"], r["hyp"]) for r in map(json.loads, (ROOT / "results" / "aws-transcribe+location.jsonl").open(encoding="utf-8"))]
    if only in (None, "noise"):
        for c in NOISE:
            p = ROOT / "results_noise" / f"aws-transcribe+location+post__{c}.jsonl"
            sets[c] = [(r["id"], r["hyp_raw"]) for r in map(json.loads, p.open(encoding="utf-8"))]
    if only in (None, "field"):
        for r in map(json.loads, (OUT / "field_transcribe_20261109.jsonl").open(encoding="utf-8")):
            sets.setdefault("field" + r["id"][3:6], []).append((r["id"], r["hyp"]))
    return sets


def server_correct(be_api):
    """서버(gunggeum-be)의 교정 그대로: 자주 틀리는 표기 표 → 위치별 교정 사전(자모 0.85)"""
    sys.path.insert(0, str(Path(be_api).resolve()))
    from app.live import postcorrect as pc
    return lambda text, terms: pc.correct(pc.apply_corrections(text), terms)


def slm(url, model, text, terms):
    body = {"model": model, "temperature": 0, "max_tokens": 256,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": f"용어 목록: {', '.join(terms)}\n문장: {text}"}]}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.load(r)["choices"][0]["message"]["content"]
    sec = time.perf_counter() - t
    out = out.strip().splitlines()[0].strip() if out.strip() else ""
    for p in ("문장:", "고친 문장:"):
        if out.startswith(p):
            out = out[len(p):].strip()
    return out, sec


PRICE = {"claude-haiku-5-5": (0.10, 0.50)}  # 달러 / 100만 토큰 (입력, 출력), 10만 토큰 이하 프롬프트
SPENT = {"in": 0, "out": 0}
LOCK = threading.Lock()
_claude = None


SCHEMA = {"type": "json_schema", "schema": {"type": "object", "properties": {"corrected": {"type": "string"}},
                                           "required": ["corrected"], "additionalProperties": False}}
FORCE = False  # --force-format: 답을 {"corrected": "..."} JSON 으로만 받음 (구조화 출력)


def claude(model, text, terms, cap):
    """Claude API (콘솔 키는 ~/.config/gungeum/anthropic.env 에서 읽기만 함). 생각하는 단계는 끔."""
    global _claude
    if _claude is None:
        import anthropic
        env = Path.home() / ".config" / "gungeum" / "anthropic.env"
        key = next(l.split("=", 1)[1].strip() for l in env.read_text().splitlines() if l.startswith("ANTHROPIC_API_KEY="))
        _claude = anthropic.Anthropic(api_key=key, max_retries=5)
    t = time.perf_counter()
    extra = {"output_config": {"format": SCHEMA}} if FORCE else {}
    r = _claude.messages.create(model=model, max_tokens=256, system=SYSTEM, thinking={"type": "disabled"},
                                messages=[{"role": "user", "content": f"용어 목록: {', '.join(terms)}\n문장: {text}"}], **extra)
    sec = time.perf_counter() - t
    pin, pout = PRICE[model]
    with LOCK:
        SPENT["in"] += r.usage.input_tokens
        SPENT["out"] += r.usage.output_tokens
        cost = (SPENT["in"] * pin + SPENT["out"] * pout) / 1e6
    if cost > cap:
        raise SystemExit(f"비용 상한 {cap}달러를 넘어 멈춤 (지금까지 {cost:.4f}달러)")
    out = "".join(b.text for b in r.content if b.type == "text").strip()
    if FORCE:
        return json.loads(out)["corrected"].strip(), sec
    out = out.splitlines()[0].strip() if out else ""
    for p in ("문장:", "고친 문장:"):
        if out.startswith(p):
            out = out[len(p):].strip()
    return out, sec


def spent(model):
    pin, pout = PRICE[model]
    return SPENT["in"], SPENT["out"], (SPENT["in"] * pin + SPENT["out"] * pout) / 1e6


def score(rows, refs, key):
    e = n = 0
    hit = tot = bhit = btot = 0
    for r in rows:
        ref = refs[r["id"]]
        rn = normalize(ref["text"], True)
        e += edit_distance(rn, normalize(r[key], True))
        n += len(rn)
        for kind, _, h in term_hits(ref["text"], r[key], term_groups(ref["terms"], ref["buildings"])):
            tot += 1
            hit += h
            if kind == "전각":
                btot += 1
                bhit += h
    return {"CER(%)": round(100 * e / n, 2), "용어 인식률(%)": round(100 * hit / tot, 1), "전각명 인식률(%)": round(100 * bhit / btot, 1)}


def cer1(ref, hyp):
    return edit_distance(normalize(ref, True), normalize(hyp, True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--be", required=True, help="gunggeum-be/api 경로")
    ap.add_argument("--url", default="http://127.0.0.1:8091/v1/chat/completions")
    ap.add_argument("--model-name", default="LFM3-1.7B-Q8_0")
    ap.add_argument("--only", choices=["quiet", "noise", "field"])
    ap.add_argument("--score", action="store_true", help="저장된 결과로 채점만")
    ap.add_argument("--claude", help="Claude API 모델 (예: claude-haiku-5-5). 주면 llama-server 대신 씀")
    ap.add_argument("--cap", type=float, default=0.3, help="Claude 비용 상한(달러). 넘으면 멈춤")
    ap.add_argument("--limit", type=int, help="조건마다 앞에서 이만큼만 (형식 확인용)")
    ap.add_argument("--force-format", action="store_true", help="Claude 답을 JSON 한 칸으로 강제 (결과 이름 뒤에 +json)")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    refs = {r["id"]: r for r in csv.DictReader((ROOT / "data" / "references.csv").open(encoding="utf-8"))}
    hints = json.loads((ROOT / "data" / "hints_by_location.json").read_text(encoding="utf-8"))
    field_path = OUT / "field_transcribe_20261109.jsonl"
    if field_path.exists():
        for r in map(json.loads, field_path.open(encoding="utf-8")):
            refs[r["id"]] = r  # 구간 정답·위치·용어(구간에 든 문장들의 용어를 합침)
    if a.claude:
        a.model_name = a.claude + ("+json" if a.force_format else "")
        global FORCE
        FORCE = a.force_format
    raw_path = OUT / f"{a.model_name}_raw.jsonl"

    done = {}
    if raw_path.exists():
        for r in map(json.loads, raw_path.open(encoding="utf-8")):
            done[(r["cond"], r["id"])] = r
    fix = server_correct(a.be)
    call = (lambda t, terms: claude(a.claude, t, terms, a.cap)) if a.claude else (lambda t, terms: slm(a.url, a.model_name, t, terms))
    if not a.score:
        def one(cond, uid, hyp):
            terms = hints[refs[uid]["location"]]
            b = fix(hyp, terms)
            c, c_sec = call(hyp, terms)
            d, d_sec = call(b, terms)
            return {"cond": cond, "id": uid, "A": hyp, "B": b, "C": c, "D": d, "E": fix(c, terms),
                    "C_sec": round(c_sec, 3), "D_sec": round(d_sec, 3)}
        with raw_path.open("a", encoding="utf-8") as fp, ThreadPoolExecutor(6 if a.claude else 1) as ex:
            for cond, items in load_inputs(a.only).items():
                todo = [(uid, hyp) for uid, hyp in items[:a.limit] if (cond, uid) not in done]
                for row in ex.map(lambda x: one(cond, *x), todo):
                    fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fp.flush()
                    done[(cond, row["id"])] = row
                print(cond, len(todo), "문장", (f"| 지금까지 입력 {spent(a.claude)[0]} 출력 {spent(a.claude)[1]} 토큰, {spent(a.claude)[2]:.4f}달러" if a.claude else ""), flush=True)
    for (c, uid), r in done.items():
        if "E" not in r:
            r["E"] = fix(r["C"], hints[refs[uid]["location"]])

    conds = ["quiet"] + NOISE + ["field152", "field153"]
    names = {"A": "받아쓰기 그대로", "B": "+ 서버 교정", "C": "+ 언어 모델", "D": "서버 교정 → 언어 모델", "E": "언어 모델 → 서버 교정"}
    prev = {"B": "A", "C": "A", "D": "B", "E": "B"}
    summary, changes = [], []
    for cond in conds:
        rows = [r for (c, _), r in done.items() if c == cond]
        if not rows:
            continue
        for k in "ABCDE":
            s = {"조건": cond, "단계": names[k], "문장": len(rows), **score(rows, refs, k)}
            if k in prev:
                better = sum(cer1(refs[r["id"]]["text"], r[k]) < cer1(refs[r["id"]]["text"], r[prev[k]]) for r in rows)
                worse = sum(cer1(refs[r["id"]]["text"], r[k]) > cer1(refs[r["id"]]["text"], r[prev[k]]) for r in rows)
                s.update({"앞 단계보다 좋아진 문장": better, "나빠진 문장": worse})
            if k in "CD":
                secs = sorted(r[f"{k}_sec"] for r in rows)
                s.update({"언어 모델 평균(초)": round(statistics.mean(secs), 3), "언어 모델 p90(초)": secs[int(0.9 * (len(secs) - 1))]})
            summary.append(s)
        for r in rows:
            ref = refs[r["id"]]["text"]
            if r["D"] != r["B"] or r["C"] != r["A"]:
                changes.append({"조건": cond, "id": r["id"], "정답": ref, "A": r["A"], "B": r["B"], "C": r["C"], "D": r["D"],
                                "B→D 틀린 글자": f'{cer1(ref, r["B"])}→{cer1(ref, r["D"])}'})
    fields = list(dict.fromkeys(k for s in summary for k in s))
    with (OUT / f"{a.model_name}_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(summary)
    if changes:
        with (OUT / f"{a.model_name}_changes.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(changes[0]))
            w.writeheader()
            w.writerows(changes)
    for s in summary:
        print(s)


if __name__ == "__main__":
    main()
