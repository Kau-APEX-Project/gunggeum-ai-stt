# STT 벤치마크

경복궁 해설 발화문 녹음 180개(U0001~U0180)로 STT 모델의 CER·용어 인식률·RTF를 잰다.

## 구성

| 파일 | 역할 |
|---|---|
| `prepare.py` | 엑셀·녹음 → `data/references.csv`, 사전(`data/hint_terms.txt`, `data/hints_by_location.json`, 그 문장을 뺀 위치별 사전 `data/hints_loo.json`), 16kHz WAV 캐시 |
| `metrics.py` | 정규화, CER, 용어 인식 판정 |
| `run_asr.py` | 로컬 모델(Whisper·Qwen3-ASR·Cohere Transcribe) 전사 (`--hint none\|location\|global\|loo`, Qwen 붕괴 방지 `--guard`, 실시간 후처리 `--post`) |
| `run_apple.py`, `apple_stt/` | Apple 음성인식(SpeechAnalyzer·SFSpeechRecognizer) 전사 |
| `run_transcribe.py` | Amazon Transcribe 스트리밍(서울 리전) 전사, 사용자 지정 어휘 생성 |
| `run_clova.py` | NAVER CLOVA Speech 장문 인식(sync) 전사, 요청별 키워드 부스팅(`--hint`, `--weight`). 키는 `~/.config/gungeum/clova.env`(`CLOVA_INVOKE_URL`, `CLOVA_SECRET`) |
| `evaluate.py` | `results/*.jsonl` → `summary.csv`, `per_utterance.csv`, `term_misses.csv` |
| `run_all.sh` | 로컬 모델·Apple 전체 실행 (`base` / `hint`) |
| `run_device.sh` | 집 장비(Z6 G4·Mac mini)에서 로컬 모델 실행 (`./run_device.sh <결과 폴더>`) |
| `webgpu/index.html`, `webgpu/serve.py` | 브라우저(WebGPU) 측정 페이지와 결과 수신 서버 |
| `devices_table.py` | 장비별 `summary.csv`를 `results_devices.csv` 한 표로 합침 |
| `postcorrect.py` | 후처리 사전(유사도 threshold 방식: Jaro-Winkler, 한국어 보정, 위치별·전체·그 문장을 뺀 사전) → `results_postcorrect/` (`python postcorrect.py && python evaluate.py results_postcorrect`) |
| `run_live.sh`, `compare_live.py` | 녹음 → STT → 후처리 실시간 재측정 → `results_live/`, 저장된 전사와 비교 `results_live/compare.csv` |
| `run_loo.sh` | 그 문장을 뺀 사전(Whisper·Qwen3-ASR)과 Qwen3-ASR 붕괴 방지 조건 측정 → `results/*+loo*.jsonl`, `*-guard.jsonl` |
| `charts.py` | 보고서 그래프 (`report_assets/term_recall.png`, `latency_paths.png`, `postcorrect_threshold.png`) |
| `make_pdf.py` | 보고서 Markdown → PDF (Chrome 헤드리스, `python make_pdf.py <보고서.md>`) |
| `STT_벤치마크_결과보고서.md` / `.pdf` | 결과 보고서 v1 (MacBook Pro 기준) |
| `STT_벤치마크_결과보고서_v2.md` / `.pdf` | 결과 보고서 v2 (핸드폰에서 쓰는 경로별·장비별) |
| `rename_log.csv` | 녹음 파일명 정리 전후 대응표 |

## 실행

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python "transformers>=5.13.0" torch torchaudio mlx-whisper soundfile numpy librosa accelerate sentencepiece openpyxl amazon-transcribe boto3 matplotlib markdown
caffeinate -dims ./run_all.sh base        # 사전 없음
caffeinate -dims ./run_all.sh hint        # 위치별·전체 사전
.venv/bin/python run_transcribe.py        # AWS 자격 증명 필요
.venv/bin/python run_transcribe.py --create-vocab global && .venv/bin/python run_transcribe.py --vocab global
.venv/bin/python run_transcribe.py --create-vocab location && .venv/bin/python run_transcribe.py --vocab location
.venv/bin/python evaluate.py
.venv/bin/python charts.py && .venv/bin/python make_pdf.py
```

`.cache/`(16kHz WAV, Apple 측정 앱)는 실행할 때 자동으로 다시 만들어진다.

### 집 장비·브라우저

```bash
# 집 장비: 코드·data·.cache/wav16k를 복사한 뒤 (Linux는 faster-whisper, nvidia-cublas-cu12, nvidia-cudnn-cu12 추가 설치)
./run_device.sh results_z6g4          # macOS는 caffeinate -dims ./run_device.sh results_macmini
# 브라우저: 서버를 띄우고 http://localhost:8765/webgpu/index.html?hint=location&guard&autorun
.venv/bin/python webgpu/serve.py 8765 results_webgpu
# 결과 폴더마다 집계 → 장비 비교표
.venv/bin/python evaluate.py results_z6g4 && .venv/bin/python devices_table.py
```

측정 페이지 쿼리: `model`(onnx-community 저장소), `enc`/`dec`(dtype), `hint=location`, `guard`(반복 방지), `nocache`, `limit`, `name`.

Cohere Transcribe는 Hugging Face에서 약관 동의(자동 승인)가 필요하다.

## 측정 규칙

- **CER**: 공백·문장부호를 빼고 글자 단위 편집 거리 / 정답 글자 수(전체 합산). 숫자는 양쪽 모두 한자어 읽기로 바꾼 뒤 비교한다(1395년 = 천삼백구십오년). `CER_원문`은 숫자 변환 없이 잰 값.
- **용어 인식률**: 엑셀의 `포함 용어`·`포함 전각명` 중 정답 문장에 실제로 나온 표기가 전사 결과에 그대로 나왔는지(표기 변형 중 하나라도 일치하면 인식).
- **RTF**: 처리 시간 합 / 오디오 길이 합. 모델 로딩은 빼고, 첫 파일 1회 워밍업 후 잰다. **지연**은 발화 하나를 넣고 텍스트를 받기까지 걸린 시간.
- 측정 기기: MacBook Pro M1 Max 32GB, macOS 27. Transcribe는 네트워크 왕복·연결 시간이 포함된다.

## 데이터 메모

- `Data/U0061~U0090`의 U0061~U0069 파일에는 U0060~U0068 문장이 녹음되어 있다(U0069 문장은 녹음 없음). `prepare.py`의 `AUDIO_TEXT_OVERRIDE`로 실제 녹음 문장과 비교한다.
- `Data/U0121~U0150`은 원래 `새로운 녹음 122~151`이었고 1씩 당겨 U0121~U0150으로 바꿨다.
