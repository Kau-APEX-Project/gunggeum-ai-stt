#!/bin/bash
# 최고 측정치와 같은 스크립트·설정(run_asr.py whisper-large-v3 --hint location)으로 녹음 폴더만 바꿔 돌린다.
cd /work/bench && . /work/env.sh
[ -d .cache/clean ] || mv .cache/wav16k .cache/clean
for c in clean babble20 babble10 babble5 babble0 talker10 talker5 talker0; do
  rm -f .cache/wav16k
  if [ $c = clean ]; then ln -s clean .cache/wav16k; else ln -s noisy/stt/$c .cache/wav16k; fi
  /work/venv/bin/python run_asr.py whisper-large-v3 --hint location --out res_$c 2>&1 | grep -v Warning
done
echo ALLDONE
