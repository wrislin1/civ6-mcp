# Controlled-position benchmark report

> **NON-COUNTING VALIDATION EVIDENCE** -- this run was produced with `validation=true` (full live admission gate pipeline, but no campaign/session counted-fingerprint pair). It is NOT a counted session and must never be treated as calibration or screening evidence.

- Session fingerprint: b0e5001e1de96b204676fb4848572dc5dc83966f449fb6cb0dbc8c39a981ccb3
- Scorer fingerprint: 8ec4f244500d5e618c3a58f3d284226c87f53c7e88d39549529c2c2d4dc0d4c2
- Scorer evaluator: civ_mcp.arena.action_metrics.evaluate_predicate

## Run completeness

| position | expected trials | committed trials |
|---|---|---|
| builder-economy-cal-v1 | 2 | 2 |

## Position: builder-economy-cal-v1

- Total trials (all models/arms): 2

### builder-economy-cal-v1 / gemma4-26b::minimal

- Trials: 1
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'step_limit': 1}
- Attempts: total=1, max=1, trials_with_retries=0
- Seeds: [101] (count=1)
- Endpoint topology: models=['gemma4-26b'], arms=['minimal']
- Latency (s): mean=18.4934, median=18.4934, max=18.4934
- Tokens: prompt_total=22849, completion_total=138
- Cost (USD): total=0 (UNPRICED, excluded from total: gemma4-26b)
- Action quality: invalid_calls=0, domain_rejections=3, successful_mutations=0, repetitions=1, useful_actions=0 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 1 | 1 | step_limit | 101 | gemma4-26b | minimal | 0.2500 |

### builder-economy-cal-v1 / gemma4-26b::standard

- Trials: 1
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'step_limit': 1}
- Attempts: total=1, max=1, trials_with_retries=0
- Seeds: [101] (count=1)
- Endpoint topology: models=['gemma4-26b'], arms=['standard']
- Latency (s): mean=22.0161, median=22.0161, max=22.0161
- Tokens: prompt_total=30637, completion_total=166
- Cost (USD): total=0 (UNPRICED, excluded from total: gemma4-26b)
- Action quality: invalid_calls=0, domain_rejections=4, successful_mutations=1, repetitions=0, useful_actions=0 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 2 | 1 | step_limit | 101 | gemma4-26b | standard | 0.2500 |

## Aggregate (per model::arm group -- never pooled across groups)

### gemma4-26b::minimal

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-economy-cal-v1 (median 0.2500)

### gemma4-26b::standard

- Equal-weight mean (normalized rubric median across positions): 0.2500
- Worst position: builder-economy-cal-v1 (median 0.2500)

## Calibration

- Pairs compared: 1
- Win counts: {}
- Ties (count for neither arm): 1
- Median paired absolute delta: 0.0000
- Median paired signed delta (treatment - baseline): 0.0000

