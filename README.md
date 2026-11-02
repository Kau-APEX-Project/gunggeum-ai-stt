# gunggeum-ai-stt

宮今(궁금) 실시간 해설 자막의 음성 인식 부분.

| 폴더 | 내용 |
|---|---|
| `gunggeum_stt/` | 실시간 자막 파이프라인 패키지: VAD → Amazon Transcribe → 후처리 사전 → Amazon Translate → 카드 링크 |
| `STT학습데이터/` | STT 엔진 비교·소음/VAD 측정 코드, 녹음 180개, 보고서 (`Benchmark/README.md`) |
| `tests/` | 단위 테스트 (Transcribe·Translate는 가짜로 대체) |

## 파이프라인 쓰기

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[server,bench,dev]"
.venv/bin/python -m pytest -q
```

```python
from gunggeum_stt import LivePipeline

pipe = LivePipeline(languages=["en", "th"], location="광화문")   # 참여 언어, 해설 위치(판정 존 이름)
await pipe.start()
await pipe.feed(pcm_bytes)      # 마이크 PCM 16 kHz·16 bit·모노, 조각 크기 자유
pipe.set_location("근정전")      # 위치가 바뀌면 다음 Transcribe 연결부터 그 위치 어휘
async for ev in pipe.events():  # partial / final / subtitle / gate / error
    ...
await pipe.stop()
```

자막 이벤트:

```json
{"type": "subtitle", "seq": 7, "ko": "광화문은 경복궁의 정문입니다.",
 "text": {"en": "Gwanghwamun (Palace Gate) is the main gate of Gyeongbokgung.", "th": "…"},
 "links": {"en": "[Gwanghwamun](card:b-151) (Palace Gate) is …", "th": "…", "ko": "[광화문](card:b-151)은 …"},
 "ms": {"stt": 820.0, "post": 0.4, "translate": 210.0, "per_lang": {"en": 180.0, "th": 210.0}, "total": 1040.0}}
```

`ms.stt` = 문장 끝 오디오를 보낸 뒤 확정 결과가 올 때까지, `ms.total` = 자막이 만들어질 때까지.

### 동작 방식

- **VAD** (`vad.py`): Silero VAD(onnx) 확률 0.5 + 해설사 발화 세기 대비 −10 dB 음량 문턱 + 시작 0.1초/유지 1.5초. 말이 시작되면
  0.3초 전부터 Transcribe로 보내고, 1.5초 넘게 조용하면 연결을 닫는다(쉬는 동안 과금 없음, 마지막 문장 확정). `GateConfig(enabled=False)`면 항상 보냄.
  근거: `STT학습데이터/Benchmark/STT_소음_VAD_측정_보고서.md`.
- **Transcribe** (`transcribe.py`): 서울 리전 스트리밍 ko-KR, 위치별 사용자 지정 어휘 `gungeum-loc-00~25`(없는 위치는 `gungeum-gyeongbokgung-ko`).
- **후처리 사전** (`postcorrect.py`): 위치별 용어와 자모 단위 Jaro-Winkler ≥ 0.85면 교정 (벤치마크 v2 설정).
- **Translate** (`translate.py`): 언어별 병렬 호출, 사용자 지정 용어집 `gungeum-ko`(ko→en·th, 공통 용어집 329개), 같은 문장 캐시.
- **카드 링크** (`cards.py`): 박윤서님 `gunggeum-ai-llm-gps/server/cards.py`의 `link_names`와 같은 규칙으로 `[이름](card:b-151)`.
- 용어집·어휘 데이터는 `gunggeum_stt/data/` (카드 302장 `cards.json`, 위치별 용어 `hints_by_location.json`, 용어집 `translate_terms.csv`).

AWS에 어휘·용어집이 없으면 한 번 만든다 (Transcribe·Translate 권한 필요):

```bash
.venv/bin/python -m gunggeum_stt.glossary --create-vocab --create-terminology
```

## 시험 서버와 지연 측정

```bash
.venv/bin/uvicorn gunggeum_stt.server:app --port 8765          # REDIS_URL 있으면 live:<session> 채널에도 발행
.venv/bin/python -m gunggeum_stt.bench_client --langs en,th     # 녹음 180개를 실제 속도로 보내 문장 끝→자막 지연 측정
```

- 해설사: `ws://…/ws/guide?session=S&location=광화문&langs=en,th` 에 PCM 바이너리 프레임, `{"type":"location",…}`·`{"type":"mic","on":false}` 텍스트 프레임
- 관람객: `ws://…/ws/listen?session=S&lang=en` 에서 `{"type":"subtitle","seq","ko","text","linked","ms"}`
- 결과: `results_live/bench_<tag>.jsonl`, `bench_<tag>_summary.json`

### 지연 실측 (2026-11-02, 녹음 20개, 맥북 → 서울 리전, 번역 제외)

"말 끝"은 녹음에서 말소리가 끝나는 지점(파일 끝 무음 제외)을 보낸 시각, "확정"은 Transcribe 확정 문장이 서버를 거쳐 해설사 소켓에 닿은 시각.

| 구성 | 말 끝 → 확정 평균 (ms) ↓ | p90 (ms) ↓ | 확정 문장 수 (발화 20개) | 비고 |
|---|---|---|---|---|
| VAD 켬, 연결 유지(무음 1.5초 뒤 닫음) | 617 | 756 | 20 | 문장 사이 1초 쉼은 그대로 보내 Transcribe가 스스로 문장 끝 판정 |
| VAD 켬, 무음 0.7초 뒤 닫음 | 245 | 387 | 26 | 닫으면 바로 확정되지만 0.7초 쉼마다 문장이 잘림(6개 추가) |
| VAD 끔(항상 보냄) | 589 | 762 | 20 | 연속 스트림, 쉬는 동안도 과금 |

Transcribe 자체 문장 끝 판정은 말이 끝나고 약 0.6초 뒤에 확정을 보냅니다. 목표(문장 끝 → 자막 3초) 중 STT가 쓰는 몫은
p90 0.8초라 번역(0.2~0.4초)을 더해도 여유가 있으므로, 기본값은 **VAD 켬 + 무음 1.5초 뒤 닫기**(문장이 잘리지 않음)입니다.
번역을 포함한 전체 지연은 Translate 권한이 붙은 뒤 `bench_client --langs en,th`로 잽니다.
