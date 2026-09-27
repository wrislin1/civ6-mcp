# Controlled-position benchmark report

- Session fingerprint: d8edd9e1b112676c70628d5f0b263b9725a96ad08563ecc20649f6e9cc5add58
- Scorer fingerprint: 30783b592a620d3e035a268615d46170f8b53ff87dc06d281f05eaec44d3c0e6
- Scorer evaluator: civ_mcp.arena.action_metrics.evaluate_predicate

## Run completeness

| position | expected trials | committed trials |
|---|---|---|
| builder-economy-cal-v1 | 24 | 24 |

## Position: builder-economy-cal-v1

- Total trials (all models/arms): 24

### builder-economy-cal-v1 / qwen3.6-27b::minimal

- Trials: 12
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'step_limit': 12}
- Attempts: total=12, max=1, trials_with_retries=0
- Seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201] (count=12)
- Endpoint topology: models=['qwen3.6-27b'], arms=['minimal']
- Latency (s): mean=52.2461, median=50.3500, max=67.3299
- Tokens: prompt_total=323538, completion_total=6666
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=27, successful_mutations=24, repetitions=0, useful_actions=0 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 1 | 1 | step_limit | 101 | qwen3.6-27b | minimal | 0.2500 |
| 4 | 1 | step_limit | 211 | qwen3.6-27b | minimal | 0.2500 |
| 5 | 1 | step_limit | 307 | qwen3.6-27b | minimal | 0.2500 |
| 8 | 1 | step_limit | 401 | qwen3.6-27b | minimal | 0.2500 |
| 9 | 1 | step_limit | 503 | qwen3.6-27b | minimal | 0.2500 |
| 12 | 1 | step_limit | 601 | qwen3.6-27b | minimal | 0.2500 |
| 13 | 1 | step_limit | 701 | qwen3.6-27b | minimal | 0.2500 |
| 16 | 1 | step_limit | 809 | qwen3.6-27b | minimal | 0.2500 |
| 17 | 1 | step_limit | 907 | qwen3.6-27b | minimal | 0.2500 |
| 20 | 1 | step_limit | 1009 | qwen3.6-27b | minimal | 0.2500 |
| 21 | 1 | step_limit | 1103 | qwen3.6-27b | minimal | 0.2500 |
| 24 | 1 | step_limit | 1201 | qwen3.6-27b | minimal | 0.2500 |

### builder-economy-cal-v1 / qwen3.6-27b::standard

- Trials: 12
- Normalized rubric median: 0.5417 (raw median: 6.5000)
- Terminal conditions: {'step_limit': 12}
- Attempts: total=12, max=1, trials_with_retries=0
- Seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201] (count=12)
- Endpoint topology: models=['qwen3.6-27b'], arms=['standard']
- Latency (s): mean=108.0777, median=107.9806, max=134.1011
- Tokens: prompt_total=781674, completion_total=13223
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=24, successful_mutations=52, repetitions=4, useful_actions=37 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 2 | 1 | step_limit | 101 | qwen3.6-27b | standard | 0.5833 |
| 3 | 1 | step_limit | 211 | qwen3.6-27b | standard | 0.5000 |
| 6 | 1 | step_limit | 307 | qwen3.6-27b | standard | 0.8333 |
| 7 | 1 | step_limit | 401 | qwen3.6-27b | standard | 0.8333 |
| 10 | 1 | step_limit | 503 | qwen3.6-27b | standard | 0.5000 |
| 11 | 1 | step_limit | 601 | qwen3.6-27b | standard | 0.5000 |
| 14 | 1 | step_limit | 701 | qwen3.6-27b | standard | 0.2500 |
| 15 | 1 | step_limit | 809 | qwen3.6-27b | standard | 0.5000 |
| 18 | 1 | step_limit | 907 | qwen3.6-27b | standard | 0.5000 |
| 19 | 1 | step_limit | 1009 | qwen3.6-27b | standard | 0.7500 |
| 22 | 1 | step_limit | 1103 | qwen3.6-27b | standard | 0.7500 |
| 23 | 1 | step_limit | 1201 | qwen3.6-27b | standard | 0.7500 |

## Aggregate (per model::arm group -- never pooled across groups)

### qwen3.6-27b::minimal

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-economy-cal-v1 (median 0.2500)

### qwen3.6-27b::standard

- Equal-weight mean (normalized rubric median across positions): 0.5417
- Worst position: builder-economy-cal-v1 (median 0.5417)

## Calibration

- Pairs compared: 12
- Win counts: {'standard': 11}
- Ties (count for neither arm): 1
- Median paired absolute delta: 3.5000
- Median paired signed delta (treatment - baseline): 3.5000

