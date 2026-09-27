# Controlled-position benchmark report

- Session fingerprint: adf83595ba665aa154518f481c2483d7ecb86acd1a55bbbab78577275fa1b5ef
- Scorer fingerprint: 8ec4f244500d5e618c3a58f3d284226c87f53c7e88d39549529c2c2d4dc0d4c2
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
- Latency (s): mean=54.1636, median=51.2736, max=76.6168
- Tokens: prompt_total=326392, completion_total=6916
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=31, successful_mutations=29, repetitions=0, useful_actions=0 (objective_verified)

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
- Normalized rubric median: 0.6250 (raw median: 7.5000)
- Terminal conditions: {'step_limit': 12}
- Attempts: total=12, max=1, trials_with_retries=0
- Seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201] (count=12)
- Endpoint topology: models=['qwen3.6-27b'], arms=['standard']
- Latency (s): mean=104.2801, median=103.5476, max=126.8839
- Tokens: prompt_total=738300, completion_total=13361
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=25, successful_mutations=49, repetitions=2, useful_actions=45 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 2 | 1 | step_limit | 101 | qwen3.6-27b | standard | 0.7500 |
| 3 | 1 | step_limit | 211 | qwen3.6-27b | standard | 0.5000 |
| 6 | 1 | step_limit | 307 | qwen3.6-27b | standard | 0.7500 |
| 7 | 1 | step_limit | 401 | qwen3.6-27b | standard | 0.7500 |
| 10 | 1 | step_limit | 503 | qwen3.6-27b | standard | 0.5000 |
| 11 | 1 | step_limit | 601 | qwen3.6-27b | standard | 0.5000 |
| 14 | 1 | step_limit | 701 | qwen3.6-27b | standard | 0.7500 |
| 15 | 1 | step_limit | 809 | qwen3.6-27b | standard | 0.7500 |
| 18 | 1 | step_limit | 907 | qwen3.6-27b | standard | 0.5000 |
| 19 | 1 | step_limit | 1009 | qwen3.6-27b | standard | 0.5000 |
| 22 | 1 | step_limit | 1103 | qwen3.6-27b | standard | 0.7500 |
| 23 | 1 | step_limit | 1201 | qwen3.6-27b | standard | 0.3333 |

## Aggregate (per model::arm group -- never pooled across groups)

### qwen3.6-27b::minimal

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-economy-cal-v1 (median 0.2500)

### qwen3.6-27b::standard

- Equal-weight mean (normalized rubric median across positions): 0.6250
- Worst position: builder-economy-cal-v1 (median 0.6250)

## Calibration

- Pairs compared: 12
- Win counts: {'standard': 12}
- Ties (count for neither arm): 0
- Median paired absolute delta: 4.5000
- Median paired signed delta (treatment - baseline): 4.5000

