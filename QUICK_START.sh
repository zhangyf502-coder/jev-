#!/usr/bin/env bash
set -euo pipefail
python3 benchmark.py validate --dataset data/clm_jev_1300.jsonl
curl -sf http://127.0.0.1:8700/health >/dev/null || { echo 'CLM :8700 unreachable'; exit 1; }
python3 benchmark.py run --dataset data/clm_jev_1300.jsonl \
  --endpoint http://127.0.0.1:8700 --model clm-latest \
  --name clm_latest --out results --workers 1 --warmup 5 --passes 2 --gpus 3,4
python3 benchmark.py run --dataset data/clm_jev_1300.jsonl \
  --endpoint http://127.0.0.1:8700 --model clm-raw \
  --name clm_raw --out results --workers 1 --warmup 5 --passes 2 --gpus 3,4
python3 benchmark.py compare --out results --models clm_latest clm_raw
printf '\nReport: results/comparison_report.html\n'
