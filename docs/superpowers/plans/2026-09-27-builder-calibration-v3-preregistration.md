# Builder-economy calibration v3 — preregistration note

Frozen 2026-09-27, before any v3 trial. Campaign file `benchmarks/campaigns/builder-economy-cal-v3.yaml`,
position `benchmarks/positions/builder-economy-cal-v2.yaml`.

## Why a v3

Campaign v2 is `BLOCKED` and stays blocked under its own frozen rules. The exploratory
uncredited-actions audit (`docs/research/arena-benchmark-builder-calibration-uncredited-actions-audit.md`)
found a validity defect in task 3: the game accepts a quarry on the forested stone tile, and v1's
level 4 predicate ("feature absent") scored the route rather than the outcome, awarding a real quarry
on the intended tile only level 2. Correcting that defect would not have rescued v2's median (the two
affected pairs move from 10 to 12; the median is set by other pairs), so this is a validity correction,
not threshold chasing. The audit's rescoring explains the defect; it is not an acceptance result and
does not replace v2.

## What changes

Position v2, task 3 only (`quarry-stone`, formerly `clear-forest-stone`):

| level | v1 | v2 |
|---|---|---|
| 1 | observed via `get_units` | unchanged |
| 2 | builder 1572874 on (71,21) | unchanged |
| 3 | unused | forest removed at (71,21) — prerequisite credit, not completion |
| 4 | forest removed at (71,21) | quarry on (71,21), not pillaged, forest present or not |

The task 3 objective's progress predicate adds "quarry present"; its declared standard-arm requirement
is `improve_tile`. Tasks 1 and 2, the archive, frozen state digest, relevant tiles, unit lifecycle and
split are byte-identical to v1 (pinned by `tests/arena/test_builder_economy_position_v2.py`).

Campaign v3 differs from v2 only by `campaign_id` and `position` (pinned by
`tests/arena/test_benchmark_contract.py`). Same models and sampling, seeds, ABBA order, audit indices,
rules, prompt, contract candidate and scorer fingerprint (`30783b59…`).

## Threshold rationale (reaffirmed, not carried over)

Each of the three tasks is worth at most 4 points, so the rubric maximum is still 12 and
`minimum_median_normalized_delta = 4/12` still means "the treatment arm completes one full task more
than the baseline arm in the median pair". Adding level 3 changes the granularity inside task 3, not
the scale. The decided-pair and standard-win minimums (10 of 12) are unchanged because the pair count
and the direction question are unchanged.

> **Correction (2026-09-27, after the campaign; original text above kept as frozen):** the phrase
> "completes one full task more than the baseline arm" is misleading. The baseline arm reaches level 1
> on every task by observation, so completing one task moves it from 1 to 4 and adds 3 points, not 4.
> The threshold itself is unchanged and is as the calibration design specifies: one complete task's
> maximum value (4) over the rubric maximum (12), which in practice asks for somewhat more than one
> additional completed task in the median pair. v3's median of 3/12 is exactly one additional
> completed task.

## Stopping rule

One counted campaign under this freeze, both blocks, reported whatever the result. If the verdict is
`BLOCKED`, this position is retired for calibration: no further campaign on
`BUILDER_ECONOMY_CAL_V1`'s archive under any rubric, and the next calibration attempt uses a new
position. No rerun on the basis of the outcome.

## Out of scope for the verdict

A broader "verified economic benefit" dimension (alternative good play, resource access, charge cost,
positioning) is recorded as exploratory analysis alongside the report. It is never combined into the
primary score and never gates this campaign. It becomes a gate only after its rules are preregistered
and validated on at least one other position.

## Operator preconditions for the live run

- Stop the Claude session's own `civ-mcp` process first; its watchers hold the single FireTuner slot.
- Source `~/.config/riz-llm/.env` into the runner's environment; `--gateway-url http://192.168.20.146:11440/v1`.
- Both checkouts clean at the freeze commit; non-counting validation first, then `--one-block` for
  gemma4-26b, audit it, then Qwen.

## Result (appended after the campaign, 2026-09-27)

`BLOCKED`: gemma4-26b MODEL_FLOOR_NULL (0/12 decided); qwen3.6-27b MODEL_NULL (12/12 standard wins,
median Δ 3.0/12 = 0.250 < 0.333). Metric fidelity and tie attribution passed. The stopping rule applies:
this position is retired for calibration. Report: `benchmark_runs/builder-economy-cal-v3/campaign_report.md`;
findings: `docs/research/arena-benchmark-builder-calibration-v1-findings.md` section 10.
