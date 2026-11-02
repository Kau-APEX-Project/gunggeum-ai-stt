"""보고서 .md -> .pdf (Chrome 헤드리스 인쇄, 그림은 base64로 넣는다)

사용법: python make_pdf.py [보고서.md]  (기본 STT_벤치마크_결과보고서.md)
"""
import base64
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown
from markdown.extensions.toc import slugify_unicode

ROOT = Path(__file__).resolve().parent
SRC = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "STT_벤치마크_결과보고서.md")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page { size: A4; margin: 16mm 14mm 16mm 14mm; }
body { font-family: "Apple SD Gothic Neo", "Pretendard", sans-serif; font-size: 10pt; line-height: 1.55;
       color: #0b0b0b; background: #fff; }
h1 { font-size: 19pt; margin: 0 0 4px; }
h1 + p { color: #52514e; margin-top: 0; }
h2 { font-size: 14pt; margin: 22px 0 8px; padding-bottom: 4px; border-bottom: 1.5px solid #0b0b0b; break-after: avoid; break-before: page; }
h3 { font-size: 11.5pt; margin: 16px 0 6px; break-after: avoid; }
h4 { font-size: 10.5pt; margin: 12px 0 4px; break-after: avoid; }
hr { display: none; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 8.6pt; break-inside: avoid; }
th, td { border: 1px solid #d5d4cf; padding: 4px 6px; vertical-align: top; word-break: keep-all; }
p:has(+ table), p:has(+ p + table) { break-after: avoid; }
th { background: #f1f0ec; font-weight: 600; }
td:has(strong) { }
ul { margin: 4px 0 8px; padding-left: 18px; }
li { margin: 2px 0; overflow-wrap: anywhere; }
img { width: 100%; margin: 8px 0; }
.toc ul { list-style: none; padding-left: 16px; margin: 2px 0; }
.toc > ul { padding-left: 0; }
.toc li { margin: 1px 0; }
.toc a { color: #0b0b0b; text-decoration: none; }
p.note { font-size: 8.6pt; color: #52514e; margin: -4px 0 10px; line-height: 1.5; }
code { font-family: "SF Mono", Menlo, monospace; font-size: 8.6pt; background: #f1f0ec; padding: 0 3px; border-radius: 3px; }
"""


def main():
    md = SRC.read_text(encoding="utf-8")

    def inline_img(m):
        data = base64.b64encode((ROOT / m.group(2)).read_bytes()).decode()
        return f"![{m.group(1)}](data:image/png;base64,{data})"

    md = re.sub(r"!\[(.*?)\]\((report_assets/.*?\.png)\)", inline_img, md)
    body = markdown.markdown(md, extensions=["tables", "attr_list", "toc"],
                             extension_configs={"toc": {"toc_depth": "2-3", "slugify": slugify_unicode}})
    body = re.sub(r"(\d%?) → (\d)", "\\1&nbsp;→&nbsp;\\2", body)  # 표 안의 '전 → 후' 수치가 줄바꿈되지 않게
    html = f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"

    tmp = Path(tempfile.mkdtemp(prefix="stt_report_"))
    try:
        page = tmp / "report.html"
        page.write_text(html, encoding="utf-8")
        out = SRC.with_suffix(".pdf")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                        f"--user-data-dir={tmp / 'profile'}", "--no-pdf-header-footer",
                        f"--print-to-pdf={out}", page.as_uri()],
                       check=True, capture_output=True, timeout=120)
        print(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
