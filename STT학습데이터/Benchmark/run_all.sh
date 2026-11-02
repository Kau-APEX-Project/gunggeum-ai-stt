#!/bin/zsh
# 벤치마크 전체 실행. 사용법: caffeinate -dims ./run_all.sh base|hint
#   base: 사전 없이 모든 모델
#   hint: 위치별 사전(location)·전체 사전(global)을 넣은 실행
# 스왑이 시작 시점보다 5GB 넘게 늘면 실행 중인 전사를 멈춘다.
set -e
cd "$(dirname "$0")"
PY=.venv/bin/python

swap_mb() { sysctl -n vm.swapusage | awk '{gsub("M","",$6); printf "%d", $6}'; }
BASE_SWAP=$(swap_mb)
(
  while /bin/sleep 10; do
    if (( $(swap_mb) - BASE_SWAP > 5120 )); then
      echo "[watchdog] 스왑 +5GB 초과 → 중단"
      pkill -f "run_asr.py|run_apple.py|AppleSTT.app"
      exit 1
    fi
  done
) &
WATCHDOG=$!
trap "kill $WATCHDOG 2>/dev/null" EXIT

$PY prepare.py
case "$1" in
  base)
    for m in whisper-large-v3-turbo whisper-large-v3 qwen3-asr-0.6b qwen3-asr-1.7b cohere-transcribe; do
      $PY run_asr.py $m
    done
    for m in transcriber dictation; do $PY run_apple.py $m; done
    ;;
  hint)
    for m in whisper-large-v3-turbo whisper-large-v3 qwen3-asr-0.6b qwen3-asr-1.7b; do
      $PY run_asr.py $m --hint location
    done
    for m in qwen3-asr-0.6b qwen3-asr-1.7b; do $PY run_asr.py $m --hint global; done
    for m in transcriber dictation; do
      $PY run_apple.py $m --hint location
      $PY run_apple.py $m --hint global
    done
    ;;
  *) echo "usage: $0 base|hint"; exit 2 ;;
esac
echo "[run_all] $1 완료, 스왑 증가 $(( $(swap_mb) - BASE_SWAP ))MB"
