#!/bin/bash
# 집 장비(Z6 G4·맥미니)에서 로컬 모델을 사전 없음·위치별 사전으로 돌린다.
# 사용법: ./run_device.sh <결과 폴더>   (macOS에서는 caffeinate -dims로 감싸서 실행)
# macOS에서는 스왑이 시작 시점보다 5GB 넘게 늘면 전사를 멈춘다.
set -e
cd "$(dirname "$0")"
OUT=$1
PY=.venv/bin/python

if [ "$(uname)" = Linux ]; then
  # faster-whisper(CTranslate2)는 CUDA 12용 cuBLAS·cuDNN을 쓴다 (pip nvidia-cublas-cu12, nvidia-cudnn-cu12)
  export LD_LIBRARY_PATH=$($PY -c "import os, nvidia.cublas.lib as b, nvidia.cudnn.lib as d; print(list(b.__path__)[0] + ':' + list(d.__path__)[0])"):$LD_LIBRARY_PATH
fi

if [ "$(uname)" = Darwin ]; then
  swap_mb() { sysctl -n vm.swapusage | awk '{gsub("M","",$6); printf "%d", $6}'; }
  BASE_SWAP=$(swap_mb)
  (
    while sleep 10; do
      if [ $(( $(swap_mb) - BASE_SWAP )) -gt 5120 ]; then
        echo "[watchdog] 스왑 +5GB 초과 → 중단"
        pkill -f run_asr.py
        exit 1
      fi
    done
  ) &
  WATCHDOG=$!
  trap "kill $WATCHDOG 2>/dev/null" EXIT
fi

for m in whisper-large-v3-turbo whisper-large-v3 qwen3-asr-0.6b qwen3-asr-1.7b; do
  $PY run_asr.py $m --out "$OUT"
  $PY run_asr.py $m --out "$OUT" --hint location
done
$PY run_asr.py cohere-transcribe --out "$OUT" --repo models/cohere-transcribe-03-2026
echo "[run_device] 완료"
