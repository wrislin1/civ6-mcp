# Controlled-position benchmark report

> **WARNING: UNGATED SMOKE EVIDENCE** -- this run was produced with `ungated_smoke=true` (no live admission gate pipeline). It is NOT a counted session and must never be treated as calibration or screening evidence.

- Session fingerprint: ede24e2b84be2e8c3d4495bba088499b13dafeb7631009c1efdb82895562ffeb
- Scorer fingerprint: b5ee53b54da42638b7f2fe63887eee3cde43c8382e4e7c4b74b3eda787197168
- Scorer evaluator: civ_mcp.arena.action_metrics.evaluate_predicate

## Run completeness

| position | expected trials | committed trials |
|---|---|---|
| builder-posctrl-v1 | 8 | 8 |

## Position: builder-posctrl-v1

- Total trials (all models/arms): 8

### builder-posctrl-v1 / qwen3.6-27b::minimal

- Trials: 4
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'step_limit': 4}
- Attempts: total=4, max=1, trials_with_retries=0
- Seeds: [2011, 2027, 2039, 2053] (count=4)
- Endpoint topology: models=['qwen3.6-27b'], arms=['minimal']
- Latency (s): mean=46.7606, median=46.2722, max=49.7979
- Tokens: prompt_total=105330, completion_total=1600
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=3, successful_mutations=9, repetitions=0, useful_actions=0 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 1 | 1 | step_limit | 2011 | qwen3.6-27b | minimal | 0.2500 |
| 4 | 1 | step_limit | 2027 | qwen3.6-27b | minimal | 0.2500 |
| 5 | 1 | step_limit | 2039 | qwen3.6-27b | minimal | 0.2500 |
| 8 | 1 | step_limit | 2053 | qwen3.6-27b | minimal | 0.2500 |

### builder-posctrl-v1 / qwen3.6-27b::standard

- Trials: 4
- Normalized rubric median: 1.0000 (raw median: 12.0000)
- Terminal conditions: {'step_limit': 4}
- Attempts: total=4, max=1, trials_with_retries=0
- Seeds: [2011, 2027, 2039, 2053] (count=4)
- Endpoint topology: models=['qwen3.6-27b'], arms=['standard']
- Latency (s): mean=105.4015, median=105.4770, max=109.1622
- Tokens: prompt_total=248198, completion_total=4355
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=13, successful_mutations=18, repetitions=0, useful_actions=12 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 2 | 1 | step_limit | 2011 | qwen3.6-27b | standard | 1.0000 |
| 3 | 1 | step_limit | 2027 | qwen3.6-27b | standard | 1.0000 |
| 6 | 1 | step_limit | 2039 | qwen3.6-27b | standard | 1.0000 |
| 7 | 1 | step_limit | 2053 | qwen3.6-27b | standard | 1.0000 |

## Aggregate (per model::arm group -- never pooled across groups)

### qwen3.6-27b::minimal

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-posctrl-v1 (median 0.2500)

### qwen3.6-27b::standard

- Equal-weight mean (normalized rubric median across positions): 1.0000
- Worst position: builder-posctrl-v1 (median 1.0000)

## Calibration

- Pairs compared: 4
- Win counts: {'standard': 4}
- Ties (count for neither arm): 0
- Median paired absolute delta: 9.0000
- Median paired signed delta (treatment - baseline): 9.0000

