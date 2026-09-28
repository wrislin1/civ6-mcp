# Builder positive-control counted campaign — preregistration note

Frozen 2026-09-28, before any counted trial. Campaign `benchmarks/campaigns/builder-posctrl-cal-v1.yaml`,
position `benchmarks/positions/builder-posctrl-v1.yaml` (archive sha256 `265bb002…`, state digest
`d619e33b…`, frozen at `6888647`). Design: `docs/superpowers/specs/2026-09-27-builder-positive-control-design.md`.

## Basis

Pilot round 1 (non-counting, `benchmark_runs/builder-posctrl-pilot-r1-rerun1`) met the frozen pilot
criterion: standard 12/12 in 4/4 episodes, minimal 3/12 in 4/4. No authoring defect. Pilot seeds
(2011, 2027, 2039, 2053) are disjoint from the counted seeds; no pilot trial counts.

## What is frozen

Identical to campaign v3 (`builder-economy-cal-v3.yaml`) except `campaign_id`, `position` and
`position_provenance` (pinned by `tests/arena/test_benchmark_contract.py`): models and block order
(gemma4-26b first, then qwen3.6-27b), sampling, seeds 101…1201, ABBA order, eight-step cap, prompt,
audit indices 1, 2, 11, 12, 23, 24, contract candidate and scorer fingerprint (`30783b59…`), rules
(≥10/12 decided, ≥10 standard wins, median normalized Δ ≥ 4/12). Rubric maximum 12, so 4/12 keeps its
meaning: one complete task's value over the rubric maximum.

## Claim and scope

Narrow, per the design note §1: the instrument detects a known capability difference (improvement tools
present vs absent) under favourable conditions. A pass satisfies the calibration requirement only; it does
not validate scoring of broader beneficial play.

## Stopping rule

One counted campaign under this freeze, both blocks, reported whatever the result. A Gemma floor is
`MODEL_FLOOR_NULL`, not an instrument failure. No rerun on the basis of the outcome; an infrastructure
abort resumes the same run (the runner's attempt budget and resume rules apply).

## Operator preconditions

Session civ-mcp stopped (tuner slot free); `~/.config/riz-llm/.env` sourced into the runner only;
`--gateway-url http://192.168.20.146:11440/v1`; both checkouts clean at the freeze commit; non-counting
validation, then `--one-block` gemma4-26b, audit, then qwen3.6-27b, audit, campaign report.
