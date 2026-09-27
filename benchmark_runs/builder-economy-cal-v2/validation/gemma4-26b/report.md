# Controlled-position benchmark report

> **NON-COUNTING VALIDATION EVIDENCE** -- this run was produced with `validation=true` (full live admission gate pipeline, but no campaign/session counted-fingerprint pair). It is NOT a counted session and must never be treated as calibration or screening evidence.

- Session fingerprint: 03fd08f8258ac554f0dd795fb9301da21f56e16e6f81a29dc936e1143898a5d3
- Scorer fingerprint: 30783b592a620d3e035a268615d46170f8b53ff87dc06d281f05eaec44d3c0e6
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
- Latency (s): mean=18.5135, median=18.5135, max=18.5135
- Tokens: prompt_total=22849, completion_total=138
- Cost (USD): total=0 (UNPRICED, excluded from total: gemma4-26b)
- Action quality: invalid_calls=0, domain_rejections=3, successful_mutations=0, repetitions=1, useful_actions=0 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 1 | 1 | step_limit | 101 | gemma4-26b | minimal | 0.2500 |

### builder-economy-cal-v1 / gemma4-26b::standard

- Trials: 1
- Normalized rubric median: 0.2500 (raw median: 3.0000)
- Terminal conditions: {'implicit_finish': 1}
- Attempts: total=1, max=1, trials_with_retries=0
- Seeds: [101] (count=1)
- Endpoint topology: models=['gemma4-26b'], arms=['standard']
- Latency (s): mean=8.6495, median=8.6495, max=8.6495
- Tokens: prompt_total=12620, completion_total=41
- Cost (USD): total=0 (UNPRICED, excluded from total: gemma4-26b)
- Action quality: invalid_calls=0, domain_rejections=0, successful_mutations=0, repetitions=0, useful_actions=0 (objective_verified)

| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |
|---|---|---|---|---|---|---|
| 2 | 1 | implicit_finish | 101 | gemma4-26b | standard | 0.2500 |

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

