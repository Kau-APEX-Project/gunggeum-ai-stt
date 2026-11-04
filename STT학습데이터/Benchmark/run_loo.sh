#!/bin/zsh
# 누출 없는 위치별 사전(그 문장 제외)과 Qwen 붕괴 방지로 다시 측정한다 (MacBook Pro).
# 사용법: caffeinate -dims ./run_loo.sh   → results/
set -e
cd "$(dirname "$0")"
PY=.venv/bin/python
swap_mb() { sysctl -n vm.swapusage | awk '{gsub("M","",$6); printf "%d", $6}'; }
BASE_SWAP=$(swap_mb)
( while /bin/sleep 10; do
    if (( $(swap_mb) - BASE_SWAP > 5120 )); then echo "[watchdog] 스왑 +5GB 초과 → 중단"; pkill -f run_asr.py; exit 1; fi
  done ) &
WATCHDOG=$!
trap "kill $WATCHDOG 2>/dev/null" EXIT
$PY run_asr.py whisper-large-v3-turbo --hint loo
$PY run_asr.py whisper-large-v3 --hint loo
for m in qwen3-asr-1.7b qwen3-asr-0.6b; do
  $PY run_asr.py $m --hint location --guard
  $PY run_asr.py $m --hint loo --guard
done
echo "[run_loo] 완료, 스왑 증가 $(( $(swap_mb) - BASE_SWAP ))MB"
