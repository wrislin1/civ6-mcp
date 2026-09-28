# Controlled-position benchmark report

- Session fingerprint: b87145900791d174a2d257e7c5782a8da24467bc66346077a251802fa0b946ff
- Scorer fingerprint: 30783b592a620d3e035a268615d46170f8b53ff87dc06d281f05eaec44d3c0e6
- Scorer evaluator: civ_mcp.arena.action_metrics.evaluate_predicate

## Run completeness

| position | expected trials | committed trials |
|---|---|---|
| builder-posctrl-v1 | 24 | 24 |

## Position: builder-posctrl-v1

- Total trials (all models/arms): 24

### builder-posctrl-v1 / gemma4-26b::minimal

- Trials: 12
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'step_limit': 12}
- Attempts: total=12, max=1, trials_with_retries=0
- Seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201] (count=12)
- Endpoint topology: models=['gemma4-26b'], arms=['minimal']
- Latency (s): mean=20.1835, median=19.8175, max=22.2651
- Tokens: prompt_total=275271, completion_total=1923
- Cost (USD): total=0 (UNPRICED, excluded from total: gemma4-26b)
- Action quality: invalid_calls=0, domain_rejections=46, successful_mutations=5, repetitions=9, useful_actions=0 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 1 | 1 | step_limit | 101 | gemma4-26b | minimal | 0.2500 |
| 4 | 1 | step_limit | 211 | gemma4-26b | minimal | 0.2500 |
| 5 | 1 | step_limit | 307 | gemma4-26b | minimal | 0.2500 |
| 8 | 1 | step_limit | 401 | gemma4-26b | minimal | 0.2500 |
| 9 | 1 | step_limit | 503 | gemma4-26b | minimal | 0.2500 |
| 12 | 1 | step_limit | 601 | gemma4-26b | minimal | 0.2500 |
| 13 | 1 | step_limit | 701 | gemma4-26b | minimal | 0.2500 |
| 16 | 1 | step_limit | 809 | gemma4-26b | minimal | 0.2500 |
| 17 | 1 | step_limit | 907 | gemma4-26b | minimal | 0.2500 |
| 20 | 1 | step_limit | 1009 | gemma4-26b | minimal | 0.2500 |
| 21 | 1 | step_limit | 1103 | gemma4-26b | minimal | 0.2500 |
| 24 | 1 | step_limit | 1201 | gemma4-26b | minimal | 0.2500 |

### builder-posctrl-v1 / gemma4-26b::standard

- Trials: 12
- Normalized rubric median: 1.0000 (raw median: 12.0000)
- Terminal conditions: {'finish_trial': 2, 'step_limit': 10}
- Attempts: total=12, max=1, trials_with_retries=0
- Seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201] (count=12)
- Endpoint topology: models=['gemma4-26b'], arms=['standard']
- Latency (s): mean=20.8756, median=20.4000, max=22.7173
- Tokens: prompt_total=386439, completion_total=1941
- Cost (USD): total=0 (UNPRICED, excluded from total: gemma4-26b)
- Action quality: invalid_calls=0, domain_rejections=10, successful_mutations=36, repetitions=0, useful_actions=36 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 2 | 1 | finish_trial | 101 | gemma4-26b | standard | 1.0000 |
| 3 | 1 | step_limit | 211 | gemma4-26b | standard | 1.0000 |
| 6 | 1 | step_limit | 307 | gemma4-26b | standard | 1.0000 |
| 7 | 1 | step_limit | 401 | gemma4-26b | standard | 1.0000 |
| 10 | 1 | finish_trial | 503 | gemma4-26b | standard | 1.0000 |
| 11 | 1 | step_limit | 601 | gemma4-26b | standard | 1.0000 |
| 14 | 1 | step_limit | 701 | gemma4-26b | standard | 1.0000 |
| 15 | 1 | step_limit | 809 | gemma4-26b | standard | 1.0000 |
| 18 | 1 | step_limit | 907 | gemma4-26b | standard | 1.0000 |
| 19 | 1 | step_limit | 1009 | gemma4-26b | standard | 1.0000 |
| 22 | 1 | step_limit | 1103 | gemma4-26b | standard | 1.0000 |
| 23 | 1 | step_limit | 1201 | gemma4-26b | standard | 1.0000 |

## Aggregate (per model::arm group -- never pooled across groups)

### gemma4-26b::minimal

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-posctrl-v1 (median 0.2500)

### gemma4-26b::standard

- Equal-weight mean (normalized rubric median across positions): 1.0000
- Worst position: builder-posctrl-v1 (median 1.0000)

## Calibration

- Pairs compared: 12
- Win counts: {'standard': 12}
- Ties (count for neither arm): 0
- Median paired absolute delta: 9.0000
- Median paired signed delta (treatment - baseline): 9.0000

