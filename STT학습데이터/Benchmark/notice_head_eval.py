"""공지 첫머리에서 다른 말을 꾸미는 용어('광화문 야간 조명')가 번역문에서 문장과 따로 떨어지는 원인을 가린다 (공지 5번 문제).

같은 공지(제목과 본문 첫 줄)를 세 가지로 영어 번역해 비교한다. 백엔드 번역 코드는 바꾸지 않고 불러와 쓴다.
  ① 원문 그대로       : 번역 제외 표시 없이 Translate 에 바로 보냄
  ② 지금 서버 방식    : 용어를 번역 제외 표시로 감쌈 (notranslate)
  ③ '의' 넣기 + 서버  : 줄 첫머리 용어 바로 뒤가 띄어쓰기면 '의'를 넣고 ②와 같이 보냄

  python notice_head_eval.py --be <gunggeum-be/api> [--dry]
결과: results_notice/head_raw.jsonl
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

import notice_translate_eval as N

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results_notice"
IDS = ("N06", "N10", "N23", "N28")


def add_ui(mod, text, table):
    """줄마다 첫 덩어리가 용어 하나이고 바로 뒤가 띄어쓰기면 '의'를 넣음 ('광화문 야간' → '광화문의 야간')"""
    terms = mod.term_table("en", table)
    lines = []
    for line in text.split("\n"):
        head = line.split(" ", 1)
        t = next((t for t in terms if head[0] == t), None)
        lines.append(f"{t}의 {head[1]}" if t and len(head) == 2 else line)
    return "\n".join(lines)


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
    notices = [r for r in map(json.loads, (ROOT / "data" / "notice_samples.jsonl").open(encoding="utf-8")) if r["id"] in IDS]
    real = None
    if not a.dry:
        import boto3
        print("계정", boto3.client("sts").get_caller_identity()["Account"], flush=True)
        real = boto3.client("translate", region_name="ap-northeast-2")
    client = N.Client(real, {})
    tr = mod.AwsTranslator(client)
    rows = []
    for r in notices:
        for field in ("title", "body"):
            src = r[field].split("\n")[0]  # 첫 줄만 (문제가 첫 줄 첫머리에서 생김, 보내는 글자를 줄이려고)
            ui = add_ui(mod, src, table)
            if ui == src:  # 첫머리가 용어가 아닌 줄은 이 시험과 관계없음
                continue
            plain = asyncio.run(tr._call(src, "en", None))
            server = asyncio.run(tr.translate(src, "en"))
            with_ui = asyncio.run(tr.translate(ui, "en"))
            rows.append({"id": r["id"], "field": field, "src": src, "src_ui": ui, "plain": plain, "server": server, "with_ui": with_ui})
    print(f"보낸 글자 {client.chars}자 (약 {client.chars * 15 / 1e6:.4f}달러)" + (" — 세기만 함" if a.dry else ""), flush=True)
    if a.dry:
        return
    with (OUT / "head_raw.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    for row in rows:
        print(f"\n[{row['id']} {row['field']}]\n 원문  {row['src'][:80]}\n ①그대로 {row['plain'][:160]}\n ②서버  {row['server'][:160]}\n ③'의'  {row['with_ui'][:160]}")


if __name__ == "__main__":
    main()
