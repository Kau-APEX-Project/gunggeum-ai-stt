"""음성 구간 검출(VAD) 게이트: 말이 있는 조각만 Transcribe로 보낸다.

소음·VAD 측정(STT학습데이터/Benchmark/STT_소음_VAD_측정_보고서.md)에서 고른 구성:
  Silero VAD(onnx, 32 ms 조각, 확률 ≥ 0.5)
  + 음량 문턱: 해설사 발화 세기보다 margin_db 이상 작은 조각은 말로 치지 않음 (배경 말소리 차단)
  + 시작 조건 onset_ms 이상 연속으로 켜져야 발화 시작, 꺼진 뒤 hang_ms 동안은 유지(그 사이 무음도 보냄)
  + 발화 시작 전 preroll_ms 만큼은 되돌려 보냄 (첫 음절이 잘리지 않게)
파이프라인은 hang_ms 를 close_after_ms(기본 1.5초)로 올려 쓴다: 문장 사이 쉼은 Transcribe에 그대로 들어가 문장 끝 판정에 쓰이고,
1.5초 넘게 조용하면 연결을 닫는다.

해설사 발화 세기는 따로 보정하지 않고, 지금까지 Silero가 말로 본 조각 중 큰 쪽(상위 10%) 세기를 기준으로 삼는다.
말을 1초 이상 들은 뒤부터 음량 문턱이 켜진다.
"""
from collections import deque
from dataclasses import dataclass, field
from importlib import resources

import numpy as np

SR = 16000
FRAME = 512  # 32 ms — Silero 입력 단위


@dataclass
class GateConfig:
    enabled: bool = True
    threshold: float = 0.5
    margin_db: float = 10.0
    onset_ms: int = 100
    hang_ms: int = 300
    preroll_ms: int = 300
    calib_min_frames: int = 30  # 약 1초


@dataclass
class GateEvent:
    kind: str           # "start" | "end"
    audio_sec: float    # 입력 오디오 기준 시각


class SileroOnnx:
    """Silero VAD v5 ONNX를 onnxruntime으로 직접 돌림 (torch 불필요). 512샘플(16 kHz)씩 넣고 확률을 돌려준다."""

    def __init__(self):
        import onnxruntime as ort
        path = str(resources.files("gunggeum_stt").joinpath("data", "silero_vad.onnx"))
        opts = ort.SessionOptions(); opts.inter_op_num_threads = 1; opts.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
        self.reset_states()

    def reset_states(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)  # 직전 조각 끝 64샘플을 앞에 붙임 (silero_vad와 같은 방식)

    def __call__(self, x: np.ndarray, sr: int = SR) -> float:
        x = np.concatenate([self.context, x.reshape(1, -1).astype(np.float32)], axis=1)
        out, self.state = self.sess.run(None, {"input": x, "state": self.state, "sr": np.array(sr, dtype=np.int64)})
        self.context = x[:, -64:]
        return float(out[0, 0])


class StreamGate:
    """feed(pcm16 bytes) → (Transcribe로 보낼 bytes, [GateEvent]). enabled=False면 그대로 통과."""

    def __init__(self, config: GateConfig | None = None):
        self.cfg = config or GateConfig()
        self.model = None
        if self.cfg.enabled:
            self.model = SileroOnnx()
        self.buf = bytearray()
        self.frames_in = 0
        self.open = False
        self.run_on = 0
        self.last_on = -10 ** 9
        self.pre = deque(maxlen=max(1, self.cfg.preroll_ms * SR // 1000 // FRAME))
        self.speech_db: list[float] = []
        self.ref_db: float | None = None

    @property
    def audio_sec(self) -> float:
        return self.frames_in * FRAME / SR

    @property
    def last_speech_sec(self) -> float | None:
        """마지막으로 말로 본 조각의 끝(세션 오디오 기준). VAD를 끄면 None."""
        return self.last_on * FRAME / SR if self.cfg.enabled and self.last_on > 0 else None

    def reset(self):
        if self.model is not None:
            self.model.reset_states()
        self.buf.clear(); self.open = False; self.run_on = 0; self.last_on = -10 ** 9; self.pre.clear()

    def _frame_speech(self, x: np.ndarray) -> bool:
        p = self.model(x, SR)
        if p < self.cfg.threshold:
            return False
        db = 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)
        self.speech_db.append(db)
        if len(self.speech_db) > 3000:
            self.speech_db = self.speech_db[-2000:]
        if len(self.speech_db) >= self.cfg.calib_min_frames:
            self.ref_db = float(np.percentile(self.speech_db, 90))
            return db >= self.ref_db - self.cfg.margin_db
        return True

    def feed(self, pcm: bytes) -> tuple[bytes, list[GateEvent]]:
        if not self.cfg.enabled:
            self.frames_in += len(pcm) // (FRAME * 2)
            return pcm, []
        self.buf += pcm
        out, events = bytearray(), []
        onset = max(1, self.cfg.onset_ms * SR // 1000 // FRAME)
        hang = self.cfg.hang_ms * SR // 1000 // FRAME
        while len(self.buf) >= FRAME * 2:
            raw = bytes(self.buf[:FRAME * 2]); del self.buf[:FRAME * 2]
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            on = self._frame_speech(x)
            self.frames_in += 1
            self.run_on = self.run_on + 1 if on else 0
            if self.run_on >= onset:
                self.last_on = self.frames_in
            active = self.run_on >= onset or (self.frames_in - self.last_on) <= hang
            if active and not self.open:
                self.open = True
                events.append(GateEvent("start", self.audio_sec - len(self.pre) * FRAME / SR))
                for p in self.pre:
                    out += p
                self.pre.clear()
            if self.open:
                out += raw
                if not active:
                    self.open = False
                    events.append(GateEvent("end", self.audio_sec))
            else:
                self.pre.append(raw)
        return bytes(out), events
