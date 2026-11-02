import asyncio
import time

import numpy as np
import pytest
import soundfile as sf
from pathlib import Path

from gunggeum_stt.pipeline import LivePipeline, PipelineConfig
from gunggeum_stt.transcribe import Recognized
from gunggeum_stt.vad import GateConfig

WAV = Path(__file__).parent / "fixtures" / "U0001_16k.wav"


class FakeStream:
    """열리면 받은 오디오 양에 따라 partial 하나, end()에서 final 하나를 낸다."""
    opened = []

    def __init__(self, on_result, vocabulary):
        self.on_result, self.vocabulary = on_result, vocabulary
        self.audio = 0
        FakeStream.opened.append(vocabulary)

    async def open(self):
        pass

    async def send(self, pcm):
        self.audio += len(pcm)
        if self.audio > 32000 and not getattr(self, "partial_sent", False):
            self.partial_sent = True
            await self.on_result(Recognized("광화문은", True, 0, 1, time.perf_counter(), time.perf_counter()))

    async def end(self, wait=5.0):
        now = time.perf_counter()
        await self.on_result(Recognized("광화문은 경복궁의 정문이자 남문입니다", False, 0, 2, now - 0.3, now))


class FakeTranslator:
    async def translate(self, text, languages):
        return {l: f"[{l}] Gwanghwamun (Palace Gate) is the main gate." for l in languages}, {l: 50.0 for l in languages}


@pytest.fixture(autouse=True)
def _clear():
    FakeStream.opened.clear()


async def collect(pipe):
    evs = []
    async for ev in pipe.events():
        evs.append(ev)
    return evs


async def test_pipeline_emits_final_and_subtitle_with_links():
    pipe = LivePipeline(["en", "th"], "광화문", PipelineConfig(vad=GateConfig(enabled=True), close_after_ms=1000),
                        transcribe_factory=FakeStream, translator=FakeTranslator())
    await pipe.start()
    x, _ = sf.read(WAV, dtype="int16")
    clip = np.concatenate([np.zeros(8000, np.int16), x, np.zeros(24000, np.int16)]).tobytes()  # 뒤 1.5초 무음 (close_after 1초)
    task = asyncio.create_task(collect(pipe))
    for i in range(0, len(clip), 3200):
        await pipe.feed(clip[i:i + 3200])
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.1)      # 뒤 무음이 1초를 넘으며 연결이 닫히고 final
    await pipe.stop()
    evs = await task
    kinds = [e["type"] for e in evs]
    assert "gate" in kinds and "partial" in kinds and "final" in kinds and "subtitle" in kinds
    final = next(e for e in evs if e["type"] == "final")
    sub = next(e for e in evs if e["type"] == "subtitle")
    assert final["seq"] == sub["seq"] == 1
    assert sub["text"]["en"].startswith("[en]") and sub["text"]["th"].startswith("[th]")
    assert "card:b-151" in sub["links"]["en"] and "card:b-151" in sub["links"]["ko"]
    assert sub["ms"]["stt"] >= 290 and sub["ms"]["total"] >= sub["ms"]["stt"]
    assert FakeStream.opened == ["gungeum-loc-00"]   # 광화문 어휘로 한 번 열림


async def test_location_change_applies_to_next_connection():
    pipe = LivePipeline(["en"], "광화문", PipelineConfig(vad=GateConfig(enabled=False)),
                        transcribe_factory=FakeStream, translator=FakeTranslator())
    await pipe.start()
    assert FakeStream.opened == ["gungeum-loc-00"]
    pipe.set_location("근정전")
    await pipe._close_stream(); await pipe._open_stream()
    assert FakeStream.opened[-1] != "gungeum-loc-00"
    await pipe.stop()
