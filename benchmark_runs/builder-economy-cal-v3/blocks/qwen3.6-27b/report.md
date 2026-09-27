# Controlled-position benchmark report

- Session fingerprint: d34ff6f6bc8ef4fd2dcaf855317bf6d9e1db07f6e6d661316a3f4279ee9432af
- Scorer fingerprint: 30783b592a620d3e035a268615d46170f8b53ff87dc06d281f05eaec44d3c0e6
- Scorer evaluator: civ_mcp.arena.action_metrics.evaluate_predicate

## Run completeness

| position | expected trials | committed trials |
|---|---|---|
| builder-economy-cal-v2 | 24 | 24 |

## Position: builder-economy-cal-v2

- Total trials (all models/arms): 24

### builder-economy-cal-v2 / qwen3.6-27b::minimal

- Trials: 12
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'step_limit': 12}
- Attempts: total=12, max=1, trials_with_retries=0
- Seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201] (count=12)
- Endpoint topology: models=['qwen3.6-27b'], arms=['minimal']
- Latency (s): mean=48.9622, median=49.4326, max=55.2660
- Tokens: prompt_total=321954, completion_total=5965
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=21, successful_mutations=19, repetitions=0, useful_actions=0 (objective_verified)

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

### builder-economy-cal-v2 / qwen3.6-27b::standard

- Trials: 12
- Normalized rubric median: 0.5000 (raw median: 6.0000)
- Terminal conditions: {'step_limit': 12}
- Attempts: total=12, max=1, trials_with_retries=0
- Seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201] (count=12)
- Endpoint topology: models=['qwen3.6-27b'], arms=['standard']
- Latency (s): mean=103.2936, median=103.6222, max=126.4643
- Tokens: prompt_total=736379, completion_total=12862
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=26, successful_mutations=55, repetitions=1, useful_actions=34 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 2 | 1 | step_limit | 101 | qwen3.6-27b | standard | 0.5000 |
| 3 | 1 | step_limit | 211 | qwen3.6-27b | standard | 0.7500 |
| 6 | 1 | step_limit | 307 | qwen3.6-27b | standard | 0.5000 |
| 7 | 1 | step_limit | 401 | qwen3.6-27b | standard | 0.7500 |
| 10 | 1 | step_limit | 503 | qwen3.6-27b | standard | 0.5000 |
| 11 | 1 | step_limit | 601 | qwen3.6-27b | standard | 0.7500 |
| 14 | 1 | step_limit | 701 | qwen3.6-27b | standard | 0.7500 |
| 15 | 1 | step_limit | 809 | qwen3.6-27b | standard | 0.5000 |
| 18 | 1 | step_limit | 907 | qwen3.6-27b | standard | 0.7500 |
| 19 | 1 | step_limit | 1009 | qwen3.6-27b | standard | 0.5000 |
| 22 | 1 | step_limit | 1103 | qwen3.6-27b | standard | 0.5000 |
| 23 | 1 | step_limit | 1201 | qwen3.6-27b | standard | 0.5000 |

## Aggregate (per model::arm group -- never pooled across groups)

### qwen3.6-27b::minimal

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-economy-cal-v2 (median 0.2500)

### qwen3.6-27b::standard

- Equal-weight mean (normalized rubric median across positions): 0.5000
- Worst position: builder-economy-cal-v2 (median 0.5000)

## Calibration

- Pairs compared: 12
- Win counts: {'standard': 12}
- Ties (count for neither arm): 0
- Median paired absolute delta: 3.0000
- Median paired signed delta (treatment - baseline): 3.0000

