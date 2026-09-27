# Controlled-position benchmark report

- Session fingerprint: 3e872bc7ec032a1fa358934d40926a63f7029b9103bb6bd366a10825380c018b
- Scorer fingerprint: 30783b592a620d3e035a268615d46170f8b53ff87dc06d281f05eaec44d3c0e6
- Scorer evaluator: civ_mcp.arena.action_metrics.evaluate_predicate

## Run completeness

| position | expected trials | committed trials |
|---|---|---|
| builder-economy-cal-v2 | 24 | 24 |

## Position: builder-economy-cal-v2

- Total trials (all models/arms): 24

### builder-economy-cal-v2 / gemma4-26b::minimal

- Trials: 12
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'step_limit': 12}
- Attempts: total=12, max=1, trials_with_retries=0
- Seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201] (count=12)
- Endpoint topology: models=['gemma4-26b'], arms=['minimal']
- Latency (s): mean=19.8590, median=18.9578, max=22.4237
- Tokens: prompt_total=273696, completion_total=1873
- Cost (USD): total=0 (UNPRICED, excluded from total: gemma4-26b)
- Action quality: invalid_calls=0, domain_rejections=43, successful_mutations=4, repetitions=11, useful_actions=0 (objective_verified)

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

### builder-economy-cal-v2 / gemma4-26b::standard

- Trials: 12
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'step_limit': 11, 'implicit_finish': 1}
- Attempts: total=12, max=1, trials_with_retries=0
- Seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201] (count=12)
- Endpoint topology: models=['gemma4-26b'], arms=['standard']
- Latency (s): mean=21.4099, median=22.4396, max=23.1361
- Tokens: prompt_total=361780, completion_total=1912
- Cost (USD): total=0 (UNPRICED, excluded from total: gemma4-26b)
- Action quality: invalid_calls=0, domain_rejections=29, successful_mutations=17, repetitions=0, useful_actions=0 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 2 | 1 | step_limit | 101 | gemma4-26b | standard | 0.2500 |
| 3 | 1 | step_limit | 211 | gemma4-26b | standard | 0.2500 |
| 6 | 1 | step_limit | 307 | gemma4-26b | standard | 0.2500 |
| 7 | 1 | step_limit | 401 | gemma4-26b | standard | 0.2500 |
| 10 | 1 | step_limit | 503 | gemma4-26b | standard | 0.2500 |
| 11 | 1 | step_limit | 601 | gemma4-26b | standard | 0.2500 |
| 14 | 1 | step_limit | 701 | gemma4-26b | standard | 0.2500 |
| 15 | 1 | step_limit | 809 | gemma4-26b | standard | 0.2500 |
| 18 | 1 | implicit_finish | 907 | gemma4-26b | standard | 0.2500 |
| 19 | 1 | step_limit | 1009 | gemma4-26b | standard | 0.2500 |
| 22 | 1 | step_limit | 1103 | gemma4-26b | standard | 0.2500 |
| 23 | 1 | step_limit | 1201 | gemma4-26b | standard | 0.2500 |

## Aggregate (per model::arm group -- never pooled across groups)

### gemma4-26b::minimal

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-economy-cal-v2 (median 0.2500)

### gemma4-26b::standard

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-economy-cal-v2 (median 0.2500)

## Calibration

- Pairs compared: 12
- Win counts: {}
- Ties (count for neither arm): 12
- Median paired absolute delta: 0.0000
- Median paired signed delta (treatment - baseline): 0.0000

