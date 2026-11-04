"""장비별 결과 폴더의 summary.csv를 합쳐 한 표로 만든다 (results_devices.csv).

먼저 각 폴더에 python evaluate.py <폴더> 를 돌려 summary.csv를 만들어 둔다.
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEVICES = {
    "results": "MacBook Pro M1 Max (MLX·MPS)",
    "results_macmini": "Mac mini M1 16GB (MLX·MPS)",
    "results_z6g4": "Z6 G4 RTX 2070 (CUDA)",
    "results_webgpu": "브라우저 WebGPU, M1 Max",
    "results_iphone": "iPhone Safari WebGPU",
}
COLS = ["CER(%)", "CER_원문(%)", "용어인식률(%)", "전각명(%)", "붕괴발화(CER≥50%)", "RTF", "지연_평균(s)", "지연_p90(s)", "지연_최대(s)", "load_sec"]


def main():
    rows = []
    for folder, device in DEVICES.items():
        path = ROOT / folder / "summary.csv"
        if not path.exists():
            continue
        for s in csv.DictReader(open(path, encoding="utf-8-sig")):
            rows.append({"device": device, "run": s["run"], "model": s["model"], "hint": s["hint"],
                         **{c: s.get(c, "") for c in COLS}})
    with open(ROOT / "results_devices.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(" | ".join([r["device"], r["run"]] + [r[c] for c in ["CER(%)", "용어인식률(%)", "전각명(%)", "RTF", "지연_평균(s)", "지연_p90(s)"]]))


if __name__ == "__main__":
    main()
