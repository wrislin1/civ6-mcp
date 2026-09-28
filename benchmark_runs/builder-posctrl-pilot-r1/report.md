# Controlled-position benchmark report

> **WARNING: UNGATED SMOKE EVIDENCE** -- this run was produced with `ungated_smoke=true` (no live admission gate pipeline). It is NOT a counted session and must never be treated as calibration or screening evidence.

- Session fingerprint: ede24e2b84be2e8c3d4495bba088499b13dafeb7631009c1efdb82895562ffeb
- Scorer fingerprint: b5ee53b54da42638b7f2fe63887eee3cde43c8382e4e7c4b74b3eda787197168
- Scorer evaluator: civ_mcp.arena.action_metrics.evaluate_predicate

## Run completeness

| position | expected trials | committed trials |
|---|---|---|
| builder-posctrl-v1 | 8 | 2 |

## Position: builder-posctrl-v1

- Total trials (all models/arms): 2

### builder-posctrl-v1 / qwen3.6-27b::minimal

- Trials: 1
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'step_limit': 1}
- Attempts: total=1, max=1, trials_with_retries=0
- Seeds: [2011] (count=1)
- Endpoint topology: models=['qwen3.6-27b'], arms=['minimal']
- Latency (s): mean=50.3394, median=50.3394, max=50.3394
- Tokens: prompt_total=26299, completion_total=383
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=0, successful_mutations=3, repetitions=0, useful_actions=0 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 1 | 1 | step_limit | 2011 | qwen3.6-27b | minimal | 0.2500 |

### builder-posctrl-v1 / qwen3.6-27b::standard

- Trials: 1
- Normalized rubric median: 1.0000 (raw median: 12.0000)
- Terminal conditions: {'step_limit': 1}
- Attempts: total=1, max=1, trials_with_retries=0
- Seeds: [2011] (count=1)
- Endpoint topology: models=['qwen3.6-27b'], arms=['standard']
- Latency (s): mean=111.8454, median=111.8454, max=111.8454
- Tokens: prompt_total=70325, completion_total=1141
- Cost (USD): total=0 (UNPRICED, excluded from total: qwen3.6-27b)
- Action quality: invalid_calls=0, domain_rejections=2, successful_mutations=3, repetitions=0, useful_actions=3 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 2 | 1 | step_limit | 2011 | qwen3.6-27b | standard | 1.0000 |

## Aggregate (per model::arm group -- never pooled across groups)

### qwen3.6-27b::minimal

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-posctrl-v1 (median 0.2500)

### qwen3.6-27b::standard

- Equal-weight mean (normalized rubric median across positions): 1.0000
- Worst position: builder-posctrl-v1 (median 1.0000)

## Calibration

- Pairs compared: 1
- Win counts: {'standard': 1}
- Ties (count for neither arm): 0
- Median paired absolute delta: 9.0000
- Median paired signed delta (treatment - baseline): 9.0000

