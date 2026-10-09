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
- **Contract/code identity:** `implementation_fingerprint` over the 24 `fingerprint_dependencies` listed
  in `instrument-v2.yaml` — the score/classification/evidence-determining chain only (the v2
  construction; never equal to, or presented as, the v1 scorer fingerprint). It is recorded as each
  position's `contract_identity`, in every validation suite, and as the offline preflight's
  `code_identity`; the gate requires all of them to match.
- **Toolkit identity:** `toolkit_fingerprint` over the 8 `toolkit_dependencies` (authoring stages and
  journal, gate and evidence loader, capture probe, deployment, launcher). These produce and check evidence
  but never change what a score means; `toolkit_identity` is recorded in the preflight and the gate
  result for information and is **never** compared for equality.
- **Capture implementation:** the positive-control timing probe (Task 17) is bound by
  `capture_implementation_digest`, which hashes only the capture path, so appending a fingerprint
  dependency does not invalidate a timing measurement.

## Acceptance gate

The gate reads **raw evidence only**. `load_gate_evidence` (`benchmark_part1_evidence.py`) resolves each
family's immutable finish packet: it verifies the sha256 of the position, authoring input, journal,
evidence index and every `validation.json`, then derives identities from the position and validation lock,
stage verdicts from the attempt's stage records, case scores/harms/digests from the derived reports,
capture cost and coverage from the raw trials, durations from the authoring journals, and the attempt list
by enumerating every attempt directory of the family under `benchmark_runs/plan3-part1/` (an attempt with
stage records but no `evidence-index.json` fails). Claims the packet makes about itself are ignored. Files of the
attempt (stage records, validation results, locks, trials, reports, case documents) are read only when listed
in the hash-verified `evidence-index.json` with a matching sha256; any file in the attempt directory absent
from the index fails `evidence_index_complete`. The loader never raises: malformed or missing evidence fails
`finish_packet_resolved` with the problem named.

`check_part1_packet` then evaluates named requirements: finish-packet resolution, twelve-cycle verify, menu
recovery, joint and materially different alternative full scores, the family's required live tags,
intermediate rungs, closer-only zero, a charged case and an uncharged counterpart for every harm, a
harm-only case with gross 0 and primary < 0, the null digest chain / observation calls / full-scope capture
timing, capture completeness, final restore, report regeneration, failed-attempt history, the 117-record
offline audit, the positive-control timing probe, tracked evidence inventory, no-model provenance, frozen
measured parameters, declared rejections, scenario duration, no undefined predicate support (by
construction: the v2 predicate layer raises on any undefined value and validation records that as a case
error, so a passed case with no error relied on none), live case marking, the offline fixture inventory and
passed validation cases. `check_part1_gate` requires all three families to pass under one code, contract
and toolset identity that equals the **current checkout's** `implementation_fingerprint`, a preflight
whose probe capture digest equals the current `capture_implementation_digest`, and a passing preflight
bound to the same probe. Each output names failed requirements with the evidence paths read.

### Required live case tags (each on at least one passed live case)

| family | tags |
|---|---|
| builder | `null_discovery`, `joint_full`, `alternative_full`, `partial_repair`, `partial_resource`, `partial_food`, `closer_only`, `escort_loss`, `escort_legitimate`, `new_exposure`, `covered_route`, `temporary_exposure_repaired`, `mixed_gain_loss`, `harm_only`, `repeat_undo`, `final_charge` |
| city | `null_discovery`, `joint_full`, `alternative_full`, `housing_partial`, `uncredited_preparation`, `destructive_placement`, `accepted_replacement`, `mixed_gain_loss`, `harm_only`, `queue_overwrite`, `repeat_undo` |
| tactical | `null_discovery`, `joint_full`, `alternative_full`, `meaningful_damage`, `reinforcement_partial`, `closer_only`, `covered_rescue`, `initial_exposure_null`, `military_loss`, `accepted_compensation`, `mixed_gain_loss`, `harm_only`, `repeat_undo` |

Harm counterparts (a passed case with one of these tags where the harm is **not** charged):
`escort-loss` ← `escort_legitimate`; `new-exposure` ← `covered_route` or `temporary_exposure_repaired`;
`destructive-placement` ← `accepted_replacement`; `military-loss` ← `accepted_compensation`. A harm with
no preregistered counterpart fails the gate.

### Offline robustness fixtures (pytest node IDs, required to exist)

| case | node ID |
|---|---|
| snapshot incompleteness | `tests/arena/test_benchmark_state_v2.py::test_dropped_row_is_incomplete_even_with_matching_shape` |
| wrong identity | `tests/arena/test_benchmark_report_v2.py::test_identity_drift_is_detected_from_states_not_validation_status` |
| malformed predicate | `tests/arena/test_benchmark_predicates_v2.py::test_unknown_kind_in_unvisited_any_branch_raises` |
| unsupported tool | `tests/arena/test_benchmark_scripted_runner.py::test_script_naming_a_tool_outside_the_toolset_is_refused_before_any_trial` |
| capture timeout | `tests/arena/test_benchmark_capture.py::test_local_capture_timeout_is_capture_failure_not_cancellation` |
| external cancellation | `tests/arena/test_benchmark_agent.py::test_external_cancel_during_capture_propagates_through_real_agent` |
| scripted rejected from model aggregates | `tests/arena/test_benchmark_report_v2.py::test_scripted_trials_cannot_enter_model_comparisons` |

These run in the full suite that the preflight binds; the gate checks they still exist in the checkout.

### Report regeneration

The gate requires the validate stage record to show `reports_identical: true` (reports rebuilt twice from
the retained lock and raw trials, byte-identical) and the on-disk `validation.json` to hash to the value in
both the finish packet and the validate record. Task 22's regeneration from a temporary checkout of the
staged candidate tree is the release-time re-verification.

## Amendments during live work

A code or contract amendment during live authoring is **prospective**: it creates a new identity, retains
all prior evidence (including failed attempts) unchanged, forces every affected packet to be revalidated
under the new identity, and **does not reset scenario time** — the three-hour clock of an attempt keeps
running across the amendment. Changed bounds, extra attempts or a second substitution require a
prospective amendment recorded before use.

Amendment procedure by kind of change:

- **Scoring-chain change** (any file in `fingerprint_dependencies`): the change yields a new
  `contract_identity`. Record the amendment here before use, re-run the full suite and the offline
  preflight under the new identity, and revalidate every affected packet (re-run its `validate` stage
  under the new identity; earlier packets stay retained, never edited). The gate passes only when every
  packet and the preflight carry the new identity.
- **Toolkit change** (any file in `toolkit_dependencies`): no revalidation. The new `toolkit_identity`
  is recorded by the next preflight and gate run; packets already produced keep their contract identity
  and remain valid. A toolkit change must not alter what any stage records as scored evidence — if it
  would, the module belongs in `fingerprint_dependencies` and the scoring-chain procedure applies.
- `capture_implementation_digest` (the timing probe's binding) is separate and unaffected by either list.
