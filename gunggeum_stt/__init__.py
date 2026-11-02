"""궁금(宮今) 실시간 해설 자막 파이프라인.

해설사 마이크 PCM(16 kHz·16 bit·모노) → 음성 구간 검출(VAD) → Amazon Transcribe 스트리밍(위치별 사용자 지정 어휘)
→ 후처리 사전(고유명사 교정) → Amazon Translate(용어집) → 카드 링크 → 자막 이벤트.

    from gunggeum_stt import LivePipeline
    pipe = LivePipeline(languages=["en"], location="광화문")   # 영어 우선, 태국어는 나중에 추가
    await pipe.start()
    await pipe.feed(pcm_bytes)          # 마이크 조각이 올 때마다
    async for ev in pipe.events():      # partial / final / subtitle
        ...
"""
from .pipeline import LivePipeline, PipelineConfig

__all__ = ["LivePipeline", "PipelineConfig"]
