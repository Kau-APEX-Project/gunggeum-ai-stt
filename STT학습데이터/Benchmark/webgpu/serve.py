"""WebGPU 측정 페이지용 로컬 서버.

Benchmark 폴더를 정적으로 서빙하고(webgpu/index.html, .cache/wav16k, data/),
페이지가 POST /save?name=<실행 이름> 으로 보내는 결과 한 줄을 results_webgpu/<이름>.jsonl에 붙인다.

사용법: python webgpu/serve.py [port] [결과 폴더]   → http://localhost:8765/webgpu/index.html
"""
import json
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / (sys.argv[2] if len(sys.argv) > 2 else "results_webgpu")


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        url = urlparse(self.path)
        name = parse_qs(url.query).get("name", [""])[0]
        if url.path != "/save" or not re.fullmatch(r"[\w.+-]+", name):
            self.send_error(400)
            return
        row = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        OUT.mkdir(exist_ok=True)
        if "log" in row:  # 페이지 진행 로그·오류 (폰 화면을 옮겨 적지 않아도 되게)
            with (OUT / f"{name}.log.txt").open("a", encoding="utf-8") as fp:
                fp.write(row["log"] + "\n")
        elif row.get("meta"):
            (OUT / f"{name}.meta.json").write_text(json.dumps(row["meta"], ensure_ascii=False, indent=1))
        else:
            with (OUT / f"{name}.jsonl").open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.send_response(204)
        self.end_headers()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"http://localhost:{port}/webgpu/index.html", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), partial(Handler, directory=str(ROOT))).serve_forever()
