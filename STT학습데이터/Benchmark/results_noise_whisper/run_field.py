"""현장 녹음 2개를 예전 Transcribe 측정과 같은 지점에서 6구간으로 잘라,
최고 측정치와 같은 설정(run_asr.py의 FasterWhisper: large-v3, 위치별 용어, 탐색 폭 1)으로 전사한다."""
import json, sys
import soundfile as sf
sys.argv = ["run_asr.py"]
import run_asr
PLAN = {"152": [("광화문", ["U0003", "U0005", "U0006"], 28.1), ("흥례문", ["U0038", "U0041", "U0042"], 55.2), ("건춘문", ["U0013", "U0014"], None)],
        "153": [("근정전 조정", ["U0065", "U0066", "U0071"], 26.6), ("근정전", ["U0074", "U0076", "U0079"], 46.1), ("근정전 내부", ["U0084"], None)]}
hints = json.load(open("data/hints_by_location.json", encoding="utf-8"))
asr = run_asr.FasterWhisper("Systran/faster-whisper-large-v3")
out = []
for n, parts in PLAN.items():
    x, sr = sf.read(f"field/rec{n}.wav", dtype="float32"); assert sr == 16000
    start = 0
    for loc, ids, b in parts:
        end = len(x) if b is None else int(b * 16000)
        hyp = asr(x[start:end], list(hints[loc]))
        out.append({"rec": n, "location": loc, "ids": ids, "start": start / 16000, "end": end / 16000, "hyp": hyp})
        print(loc, hyp, flush=True)
        start = end
json.dump(out, open("field/whisper_field.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("FIELDDONE")
