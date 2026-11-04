#!/bin/zsh
# 녹음 → STT → 후처리 사전(위치별·자모·0.85)을 처음부터 실시간으로 다시 돌린다.
# 사용법: caffeinate -dims ./run_live.sh   → results_live/
# 스왑이 시작 시점보다 5GB 넘게 늘면 멈춘다. Transcribe 2회 비용은 약 $0.46.
set -e
cd "$(dirname "$0")"
PY=.venv/bin/python
OUT=results_live

swap_mb() { sysctl -n vm.swapusage | awk '{gsub("M","",$6); printf "%d", $6}'; }
BASE_SWAP=$(swap_mb)
(
  while /bin/sleep 10; do
    if (( $(swap_mb) - BASE_SWAP > 5120 )); then
      echo "[watchdog] 스왑 +5GB 초과 → 중단"
      pkill -f "run_asr.py|run_apple.py|run_transcribe.py|AppleSTT.app"
      exit 1
    fi
  done
) &
WATCHDOG=$!
trap "kill $WATCHDOG 2>/dev/null" EXIT

$PY run_asr.py whisper-large-v3-turbo --post --out $OUT
$PY run_asr.py whisper-large-v3-turbo --hint location --post --out $OUT
$PY run_asr.py qwen3-asr-1.7b --post --out $OUT
$PY run_asr.py qwen3-asr-0.6b --hint location --post --out $OUT
$PY run_asr.py cohere-transcribe --post --out $OUT
$PY run_apple.py transcriber --post --out $OUT
$PY run_apple.py dictation --post --out $OUT
$PY run_transcribe.py --post --out $OUT
$PY run_transcribe.py --vocab location --post --out $OUT
echo "[run_live] 완료, 스왑 증가 $(( $(swap_mb) - BASE_SWAP ))MB"
