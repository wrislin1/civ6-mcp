# Controlled-position benchmark report

- Session fingerprint: e085f0d5342e92133e2a8708e88e94a931e5ebce506f95d30166d3f37207703c
- Scorer fingerprint: 30783b592a620d3e035a268615d46170f8b53ff87dc06d281f05eaec44d3c0e6
- Scorer evaluator: civ_mcp.arena.action_metrics.evaluate_predicate

## Run completeness

| position | expected trials | committed trials |
|---|---|---|
| builder-posctrl-v1 | 24 | 24 |

## Position: builder-posctrl-v1

- Total trials (all models/arms): 24

### builder-posctrl-v1 / qwen3.6-27b::minimal

- Trials: 12
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'step_limit': 12}
- Attempts: total=12, max=1, trials_with_retries=0
- Seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201] (count=12)
- Endpoint topology: models=['qwen3.6-27b'], arms=['minimal']
- Latency (s): mean=47.5690, median=47.3716, max=51.3350
- Tokens: prompt_total=323121, completion_total=5264
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=25, successful_mutations=16, repetitions=0, useful_actions=0 (objective_verified)

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

### builder-posctrl-v1 / qwen3.6-27b::standard

- Trials: 12
- Normalized rubric median: 1.0000 (raw median: 12.0000)
- Terminal conditions: {'step_limit': 12}
- Attempts: total=12, max=1, trials_with_retries=0
- Seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201] (count=12)
- Endpoint topology: models=['qwen3.6-27b'], arms=['standard']
- Latency (s): mean=107.3144, median=105.0593, max=135.8079
- Tokens: prompt_total=769250, completion_total=14235
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=24, successful_mutations=42, repetitions=3, useful_actions=35 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 2 | 1 | step_limit | 101 | qwen3.6-27b | standard | 1.0000 |
| 3 | 1 | step_limit | 211 | qwen3.6-27b | standard | 1.0000 |
| 6 | 1 | step_limit | 307 | qwen3.6-27b | standard | 1.0000 |
| 7 | 1 | step_limit | 401 | qwen3.6-27b | standard | 1.0000 |
| 10 | 1 | step_limit | 503 | qwen3.6-27b | standard | 1.0000 |
| 11 | 1 | step_limit | 601 | qwen3.6-27b | standard | 1.0000 |
| 14 | 1 | step_limit | 701 | qwen3.6-27b | standard | 1.0000 |
| 15 | 1 | step_limit | 809 | qwen3.6-27b | standard | 0.7500 |
| 18 | 1 | step_limit | 907 | qwen3.6-27b | standard | 1.0000 |
| 19 | 1 | step_limit | 1009 | qwen3.6-27b | standard | 1.0000 |
| 22 | 1 | step_limit | 1103 | qwen3.6-27b | standard | 1.0000 |
| 23 | 1 | step_limit | 1201 | qwen3.6-27b | standard | 1.0000 |

## Aggregate (per model::arm group -- never pooled across groups)

### qwen3.6-27b::minimal

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-posctrl-v1 (median 0.2500)

### qwen3.6-27b::standard

- Equal-weight mean (normalized rubric median across positions): 1.0000
- Worst position: builder-posctrl-v1 (median 1.0000)

## Calibration

- Pairs compared: 12
- Win counts: {'standard': 12}
- Ties (count for neither arm): 0
- Median paired absolute delta: 9.0000
- Median paired signed delta (treatment - baseline): 9.0000

