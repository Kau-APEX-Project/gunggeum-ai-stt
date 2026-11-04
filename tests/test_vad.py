from pathlib import Path

import numpy as np
import soundfile as sf

from gunggeum_stt.vad import FRAME, GateConfig, StreamGate

WAV = Path(__file__).parent / "fixtures" / "U0001_16k.wav"


def pcm_of(x):
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def test_gate_opens_on_speech_and_closes_on_silence():
    x, sr = sf.read(WAV, dtype="float32")
    assert sr == 16000
    clip = np.concatenate([np.zeros(16000, np.float32), x, np.zeros(2 * 16000, np.float32)])
    gate = StreamGate(GateConfig(hang_ms=1500))
    sent, events = bytearray(), []
    for i in range(0, len(clip), 1600):
        out, ev = gate.feed(pcm_of(clip[i:i + 1600]))
        sent += out; events += ev
    kinds = [e.kind for e in events]
    assert kinds == ["start", "end"]                   # 1.5초 유지 덕에 발화 안 쉼으로 끊기지 않음
    assert 0.6 < events[0].audio_sec < 1.6            # 첫 1초 무음 뒤(앞 0.3초는 되돌려 보냄)
    sent_sec = len(sent) / 2 / 16000
    assert sent_sec < len(clip) / 16000 - 1.0          # 뒤쪽 무음 2초 중 1.5초 유지 뒤는 보내지 않음
    assert sent_sec > len(x) / 16000 * 0.9             # 발화는 거의 다 보냄


def test_disabled_gate_passes_everything():
    gate = StreamGate(GateConfig(enabled=False))
    pcm = b"\x00" * (FRAME * 2 * 10)
    out, ev = gate.feed(pcm)
    assert out == pcm and ev == []
