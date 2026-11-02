"""Amazon Translate: 확정 문장을 참여 언어별로 병렬 번역한다. 사용자 지정 용어집(ko→en·th)을 붙이고 같은 문장은 캐시."""
import asyncio
import time
from functools import lru_cache

import boto3
from botocore.config import Config


class Translator:
    def __init__(self, region="ap-northeast-2", terminology: str | None = "gungeum-ko", cache_size=2000):
        self.client = boto3.client("translate", region_name=region,
                                   config=Config(retries={"max_attempts": 3, "mode": "adaptive"}, max_pool_connections=16))
        self.terminology = terminology
        self._cache: dict[tuple[str, str], str] = {}
        self.cache_size = cache_size

    def _one(self, text: str, lang: str) -> str:
        key = (text, lang)
        if key in self._cache:
            return self._cache[key]
        kw = {"TerminologyNames": [self.terminology]} if self.terminology else {}
        out = self.client.translate_text(Text=text, SourceLanguageCode="ko", TargetLanguageCode=lang, **kw)["TranslatedText"]
        if len(self._cache) >= self.cache_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = out
        return out

    async def translate(self, text: str, languages: list[str]) -> tuple[dict[str, str], dict[str, float]]:
        """(언어별 번역, 언어별 걸린 ms). 언어마다 동시에 호출."""
        async def one(lang):
            t = time.perf_counter()
            out = await asyncio.to_thread(self._one, text, lang)
            return lang, out, (time.perf_counter() - t) * 1000
        results = await asyncio.gather(*(one(l) for l in languages))
        return {l: o for l, o, _ in results}, {l: round(ms, 1) for l, _, ms in results}
