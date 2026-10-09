# Instrument contract v2 — candidate record (Plan 3 Part 1)

**Status: candidate, not released.** `instrument-v2.yaml` is the candidate contract used for scripted
authoring of the three Part 1 positions (builder, city, tactical). It is released at Task 22 only if every
Part 1 gate passes (`check_part1_gate` in `src/civ_mcp/arena/benchmark_part1_gate.py`, spec
`docs/superpowers/specs/2026-10-08-arena-benchmark-plan-3-part-1-design.md` §11). Until then no score
produced under it is a released measurement, and its primary score is **not comparable** to Plan 2's
calibration scores (`primary_comparable_to_plan2: false`). The released v1 records
(`instrument-v1.yaml`, `instrument-v1.md`, `instrument-v1-candidate.yaml`) are unchanged and remain the
contract for the v1 campaigns.

## Budget (`plan3-part1-budget.yaml`, `budget_id: plan3-part1-v1`)

| field | value |
|---|---|
| unit | `backend_round_trip` (one model/backend round trip; a batch of calls is one step) |
| max_steps | 15 |
| script_episode_wall_s | 300 |
| capture_wall_s / capture_lua_executions | 2.0 s / exactly 1 |
| positive_control_timing_samples | 20 |
| model_episode_wall_formula | `max(300, ceil(15 * p95_roundtrip_s * 1.5))`, locked before each model block |
| benefit_ledger_gates_advancement | false (the ledger is an observation vector, not a gate) |
| authoring_seconds_per_scenario | 10800 (three elapsed live hours per scenario attempt) |
| scenario_substitutions_per_family | 1 |

### Why fifteen rounds

The parent design specified fifteen model round trips with diagnostic comparisons around that baseline;
Plan 2 froze eight for its calibration only. The Plan 2 findings
(`docs/research/arena-benchmark-builder-calibration-v1-findings.md`) record Qwen reaching the eight-round
cap before acting with the pasture builder in 4/12 v3 standard trials, and round-trip budgeting let Qwen
batch roughly two calls per round versus Gemma's one. That evidence **supports restoring** the parent's
fifteen-round allowance. It is **not proof of an optimal budget**, and it does not justify
position-specific budgets tuned from future model results. Because batching is permitted, equal round
budgets do not imply equal tool-call opportunities; reports show non-finish tool-call attempts per round
trip for each model.

## Schemas

| schema | version |
|---|---|
| evidence (position, script, case, validation suite, trial) | 2.0.0 |
| predicate | 2.0.0 |
| report | 2.0.0 |

Predicate kinds (unknown kinds are rejected before any predicate is evaluated): `all`, `any`,
`tile_matches`, `charged_builder_at`, `active_production`, `housing_resolved`, `district_committed`,
`target_damaged`, `target_neutralised`, `civilian_covered`, `civilian_exposed`, `unit_in_area`,
`unit_lost`, `asset_displaced`, `new_civilian_exposure`. Missing evidence for a predicate is an error,
never a false value or safety.

Report sections, in order: provenance, identity, mechanics, terminal, counters, score, ledger, audits,
capture, model_usage, digests. Reports are derived deterministically from raw trials and the locked scorer
and must regenerate byte-identically.

## Finite penalty rules

- `primary_score = (gross_credit - harm_total) / M`, neither clipped nor shifted; `M = 12` (three
  four-point objectives per position; optional intermediate rungs are worth two points).
- The default deduction for a harm equals the maximum credit of the objective the lost asset serves (four
  points); any deviation needs a preregistered `weight_reason` and a recomputed maximum/increment analysis.
- Declared harm maxima, never above `M`: builder 8 (escort loss + new civilian exposure), city 4
  (destructive placement), tactical 4 (military loss). The primary range is therefore within `[-1, 1]`.
- Harms are deduplicated per `loss_key`; compensated harms keep their evidence and deduct nothing;
  `final` harms compare endpoints, `event` harms inspect every recorded transition (`unit_lost`,
  `asset_displaced` only); exposure is final-only; missed progress is never debited.

## Identities

- **Toolset:** `plan3-part1-v1` (`benchmarks/toolsets/plan3-part1-v1.yaml`), identity
  `source_sha256 e09dca93f7afd7e57866009315226facaa00aa63e3c5aeff4d5da30ecdff2882`,
  `schemas_sha256 79098f6c05ef8ac5b5a7c5317a8df9256313326a5108e0ca09de52edfa3f5de7`.
- **Coverage:** the recipe's `coverage_rule` (owned tiles when `include_owned_tiles`, every tile within
  `area_radius` in x and y of each bound tile, and the tracked target units) is resolved once and frozen in
  the position; it is a comparison identity.
- **Contract/code identity:** `implementation_fingerprint` over the 30 fingerprint dependencies listed in
  `instrument-v2.yaml` (the v2 construction; never equal to, or presented as, the v1 scorer fingerprint).
  It is recorded as each position's `contract_identity`, in every validation suite, and as the offline
  preflight's `code_identity`; the gate requires all of them to match.
- **Capture implementation:** the positive-control timing probe (Task 17) is bound by
  `capture_implementation_digest`, which hashes only the capture path, so appending a fingerprint
  dependency does not invalidate a timing measurement.

## Acceptance gate

`check_part1_packet` evaluates each family packet against named requirements (twelve-cycle verify, menu
recovery, joint and materially different alternative full scores, intermediate rungs, closer-only zero,
positive and negative case for every harm, null digest chain / observation calls / full-scope capture
timing, capture completeness, final restore, failed-attempt history, the 117-record offline audit, the
positive-control timing probe, tracked evidence inventory, no-model provenance, frozen measured parameters,
declared rejections, scenario duration, no undefined predicate support, live-versus-offline case marking,
passed validation cases). `check_part1_gate` requires all three families to pass under one code, contract
and toolset identity and a passing offline preflight bound to the same probe. Each output names failed
requirements with the evidence paths read.

## Amendments during live work

A code or contract amendment during live authoring is **prospective**: it creates a new identity, retains
all prior evidence (including failed attempts) unchanged, forces every affected packet to be revalidated
under the new identity, and **does not reset scenario time** — the three-hour clock of an attempt keeps
running across the amendment. Changed bounds, extra attempts or a second substitution require a
prospective amendment recorded before use.
