"""소음·VAD 보고서의 표를 CSV에서 만들어 보고서 md의 표시 자리에 끼워 넣는다 (python noise_report_tables.py)."""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MD = ROOT / "STT_소음_VAD_측정_보고서.md"
COND = {"clean": ("깨끗한 녹음(기준)", "—"), "babble20": ("웅성거림", "20"), "babble10": ("웅성거림", "10"), "babble5": ("웅성거림", "5"),
        "babble0": ("웅성거림", "0"), "talker10": ("옆 사람 1명", "10"), "talker5": ("옆 사람 1명", "5"), "talker0": ("옆 사람 1명", "0")}
ORDER = ["clean", "babble20", "babble10", "babble5", "babble0", "talker10", "talker5", "talker0"]
VADS = [("silero-0.5", "Silero VAD (문턱 0.5)"), ("silero-0.7", "Silero VAD (문턱 0.7)"), ("webrtc-3", "WebRTC VAD (모드 3)"),
        ("silero+gate-10", "Silero + 음량 문턱 −10 dB"), ("silero+gate-15", "Silero + 음량 문턱 −15 dB"),
        ("silero+gate-15+smooth", "Silero + 음량 문턱 −15 dB + 시작 0.1초/유지 0.3초"),
        ("silero+gate-10+smooth", "Silero + 음량 문턱 −10 dB + 시작 0.1초/유지 0.3초")]


def stt_table():
    s = {r["run"].split("__")[-1]: r for r in csv.DictReader(open(ROOT / "results_noise" / "summary.csv", encoding="utf-8-sig"))}
    s["clean"] = [r for r in csv.DictReader(open(ROOT / "results_postcorrect" / "summary.csv", encoding="utf-8-sig"))
                  if r["run"] == "aws-transcribe+location__post-location-jamo-0.85"][0]
    out = ["| 배경 | 해설사 대비 세기 차이 (dB) | CER (%) ↓ | 완전 일치 (%) ↑ | 붕괴 발화 (개, CER≥50%) ↓ | 용어 인식률 (%) ↑ | 전각명 인식률 (%) ↑ |", "|---|---|---|---|---|---|---|"]
    for k in ORDER:
        r = s[k]; name, lvl = COND[k]
        out.append(f"| {name} | {lvl} | {float(r['CER(%)']):.2f} | {float(r['완전일치(%)']):.1f} | {r['붕괴발화(CER≥50%)']} | {float(r['용어인식률(%)']):.1f} | {float(r['전각명(%)']):.1f} |")
    return "\n".join(out)


def vad_table():
    v = {(r["cond"], r["vad"]): r for r in csv.DictReader(open(ROOT / "results_noise" / "vad_summary.csv", encoding="utf-8-sig"))}
    out = ["| 검출기 | " + " | ".join(f"{COND[k][0]} {COND[k][1]}" if k != "clean" else "깨끗" for k in ORDER) + " |", "|---|" + "---|" * len(ORDER)]
    blocks = []
    for metric, title in (("무음구간_오작동(%)", "해설사가 쉬는 동안 배경에 켜진 파일 (%) ↓"), ("검출률(%)", "해설사 발화 프레임 검출률 (%) ↑"), ("오검출률(%)", "비발화 프레임 오검출률 (%) ↓")):
        rows = [f"**{title}**", out[0], out[1]]
        for key, label in VADS:
            rows.append(f"| {label} | " + " | ".join(f"{float(v[(k, key)][metric]):.1f}" for k in ORDER) + " |")
        blocks.append("\n".join(rows))
    speed = {key: v[("clean", key)]["처리_ms/파일"] for key, _ in VADS}
    blocks.append("처리 시간(맥북 CPU, 10초 파일 1개): Silero 계열 약 " + f"{float(speed['silero-0.5']):.0f} ms, WebRTC {float(speed['webrtc-3']):.1f} ms — 둘 다 실시간보다 100배 이상 빠릅니다.")
    return "\n\n".join(blocks)


def main():
    md = MD.read_text(encoding="utf-8")
    md = re.sub(r"<!--STT_TABLE-->.*?<!--/STT_TABLE-->", "<!--STT_TABLE-->\n" + stt_table() + "\n<!--/STT_TABLE-->", md, flags=re.S)
    md = re.sub(r"<!--VAD_TABLE-->.*?<!--/VAD_TABLE-->", "<!--VAD_TABLE-->\n" + vad_table() + "\n<!--/VAD_TABLE-->", md, flags=re.S)
    MD.write_text(md, encoding="utf-8")
    print("표 갱신:", MD.name)


if __name__ == "__main__":
    main()
