# CLM / Jev Decision Benchmark Report

This report contains **measured model outputs only**. The bundled 1,300 labels are synthetic and have not undergone expert validation.

| Model | n | Success | Accuracy(all) | Macro-F1 | ECE | P50 (ms) | P95 (ms) | P99 (ms) | QPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| clm_latest | 1300 | 100.00% | 42.62% | 0.30 | 0.27 | 53.92 | 133.57 | 137.02 | 37.78 |
| clm_raw | 1300 | 100.00% | 30.08% | 0.15 | 0.33 | 0.93 | 0.96 | 1.01 | 1058.57 |
| jev_latest | 1300 | 100.00% | 91.62% | 0.92 | 0.04 | 259.13 | 406.04 | 1072.91 | 0.00 |

## Categories

| Category | clm_latest accuracy | clm_raw accuracy | jev_latest accuracy |
|---|---:|---:|---:|
| commerce_workflow | 34.17% | 25.00% | 100.00% |
| customer_support | 25.62% | 12.50% | 98.75% |
| document_grounding | 97.50% | 70.83% | 100.00% |
| incident_response | 25.00% | 33.33% | 100.00% |
| option_order_robustness | 36.25% | 15.00% | 97.50% |
| out_of_scope | 54.00% | 44.00% | 83.00% |
| security_triage | 37.50% | 31.67% | 75.00% |
| semantic_duplicates | 50.00% | 50.00% | 100.00% |
| sentiment_scoring | 24.00% | 20.00% | 87.00% |
| tool_routing | 51.88% | 13.75% | 96.88% |
| urgency_scoring | 30.00% | 20.00% | 60.00% |

## Calibration, inference and fairness notes

- Accuracy(all) counts failed or invalid calls as incorrect; success-only accuracy is in per-model JSON.
- ECE uses predicted-label probability from model distributions; a smaller value is better. Results are sample-dependent.
- Binary Brier uses (P(yes)-y)^2; multi-class Brier is sum over classes and is not directly comparable to binary values.
- Score exact match comes from argmax probability over ordinal levels (or rounded expected score if probabilities unavailable); MAE is provided.
- 0.90 gate coverage = all evaluated cases automatically accepted / all evaluated cases; gate error rate = errors / accepted.
- P50/P95/P99 are observed CLIENT wall times per request, including network and retries, not isolated GPU kernel time.
- QPS comes from the LAST executed request batch; it depends on worker count and should be compared only under matched loads.
- GPU memory is nvidia-smi global device used memory, NOT per-model attributable memory when GPUs share workloads.
- The option-permutation category contains paired variants and is not statistically independent from its base set.
- Synthetic tasks are for smoke/regression testing. Prefer public benchmark and separately human-verified samples for performance claims.
