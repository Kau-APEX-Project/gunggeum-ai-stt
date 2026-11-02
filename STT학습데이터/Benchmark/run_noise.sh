#!/bin/zsh
# 소음 합성본 7조건을 Transcribe(위치별 어휘+후처리)로 전사한다. 조건별로 동시에 돌린다.
cd "$(dirname "$0")"
for c in babble20 babble10 babble5 babble0 talker10 talker5 talker0; do
  .venv/bin/python run_transcribe.py --vocab location --post --wav-dir .cache/noisy/stt/$c --tag $c --out results_noise > results_noise/log_$c.txt 2>&1 &
done
wait
echo "전사 완료"
