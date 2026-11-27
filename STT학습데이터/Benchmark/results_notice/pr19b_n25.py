"""gunggeum-be PR 19번 두 번째 커밋(77aa000)으로 N25 본문만 영어·태국어 번역 (기간 끝 날짜 확인)"""
import asyncio, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notice_translate_eval as N
import boto3
os.environ["TRANSLATE_PROVIDER"] = "aws"
for k in ("TRANSLATE_TERM_MODE", "TRANSLATE_TERMS_PATH", "TRANSLATE_TERMINOLOGY"):
    os.environ.pop(k, None)
print("계정", boto3.client("sts").get_caller_identity()["Account"])
mod = N.load(Path(sys.argv[1]))
src = next(r for r in map(json.loads, open(Path(__file__).parent.parent / "data/notice_samples.jsonl", encoding="utf-8")) if r["id"] == "N25")["body"]
client = N.Client(boto3.client("translate", region_name="ap-northeast-2"), {})
tr = mod.AwsTranslator(client)
rows = [{"id": "N25", "lang": l, "src": src, "out": asyncio.run(tr.translate(src, l, notice=True))} for l in ("en", "th")]
print(f"보낸 글자 {client.chars}자 (약 {client.chars * 15 / 1e6:.4f}달러)")
with open(Path(__file__).parent / "pr19b_n25_raw.jsonl", "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(r["lang"], r["out"].split("\n")[1])
