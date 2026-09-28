# Controlled-position benchmark report

> **NON-COUNTING VALIDATION EVIDENCE** -- this run was produced with `validation=true` (full live admission gate pipeline, but no campaign/session counted-fingerprint pair). It is NOT a counted session and must never be treated as calibration or screening evidence.

- Session fingerprint: 46805dfee4e91a3f20b78dd553567a153282ecf237619d95575eac2d131f1a24
- Scorer fingerprint: 30783b592a620d3e035a268615d46170f8b53ff87dc06d281f05eaec44d3c0e6
- Scorer evaluator: civ_mcp.arena.action_metrics.evaluate_predicate

## Run completeness

| position | expected trials | committed trials |
|---|---|---|
| builder-posctrl-v1 | 2 | 2 |

## Position: builder-posctrl-v1

- Total trials (all models/arms): 2

### builder-posctrl-v1 / gemma4-26b::minimal

- Trials: 1
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'step_limit': 1}
- Attempts: total=1, max=1, trials_with_retries=0
- Seeds: [101] (count=1)
- Endpoint topology: models=['gemma4-26b'], arms=['minimal']
- Latency (s): mean=22.5784, median=22.5784, max=22.5784
- Tokens: prompt_total=22923, completion_total=180
- Cost (USD): total=0 (UNPRICED, excluded from total: gemma4-26b)
- Action quality: invalid_calls=0, domain_rejections=3, successful_mutations=2, repetitions=0, useful_actions=0 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 1 | 1 | step_limit | 101 | gemma4-26b | minimal | 0.2500 |

### builder-posctrl-v1 / gemma4-26b::standard

- Trials: 1
- Normalized rubric median: 1.0000 (raw median: 12.0000)
- Terminal conditions: {'finish_trial': 1}
- Attempts: total=1, max=1, trials_with_retries=0
- Seeds: [101] (count=1)
- Endpoint topology: models=['gemma4-26b'], arms=['standard']
- Latency (s): mean=19.6638, median=19.6638, max=19.6638
- Tokens: prompt_total=30876, completion_total=159
- Cost (USD): total=0 (UNPRICED, excluded from total: gemma4-26b)
- Action quality: invalid_calls=0, domain_rejections=0, successful_mutations=3, repetitions=0, useful_actions=3 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 2 | 1 | finish_trial | 101 | gemma4-26b | standard | 1.0000 |

## Aggregate (per model::arm group -- never pooled across groups)

### gemma4-26b::minimal

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-posctrl-v1 (median 0.2500)

### gemma4-26b::standard

- Equal-weight mean (normalized rubric median across positions): 1.0000
- Worst position: builder-posctrl-v1 (median 1.0000)

## Calibration

- Pairs compared: 1
- Win counts: {'standard': 1}
- Ties (count for neither arm): 0
- Median paired absolute delta: 9.0000
- Median paired signed delta (treatment - baseline): 9.0000

