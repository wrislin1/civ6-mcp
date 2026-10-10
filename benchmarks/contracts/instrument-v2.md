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
- **Contract/code identity:** `implementation_fingerprint` over the 49 `fingerprint_dependencies` listed
  in `instrument-v2.yaml` — the score/classification/evidence-determining chain only (the v2
  construction; never equal to, or presented as, the v1 scorer fingerprint). The chain includes
  `game_state.py` and `narrate.py`, which determine the tool results the actor observes and the
  dispatch outcomes that become evidence, every `lua/*.py` module `GameState` dispatches through
  (they produce the result text the scoring chain parses and the engine refusals that become
  dispatch evidence), and `lua/benchmark.py`, the v1 identity/digest path used by
  `capture_position` and the positive-control probe's identity check (the plan's original list named
  "registry/narration" and "GameState/Lua"). It is recorded as each
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
stage records but no `evidence-index.json` fails; a substitute's imported predecessor must reference one of
those journals byte-for-byte, and the stage CLI refuses attempt directories and predecessor journals
anywhere else before the clock opens). Claims the packet makes about itself are ignored. Files of the
attempt (stage records, validation results, locks, trials, reports, case documents) are read only when listed
in the hash-verified `evidence-index.json` with a matching sha256; any file in the attempt directory absent
from the index fails `evidence_index_complete`. The loader never raises: malformed or missing evidence fails
`finish_packet_resolved` with the problem named.

`check_part1_packet` then evaluates named requirements: finish-packet resolution, twelve-cycle verify, menu
recovery, joint and materially different alternative full scores, the family's required live tags,
intermediate rungs, closer-only zero, a charged case and an uncharged counterpart for every harm, a
harm-only case with gross 0 and primary < 0, the null digest chain / observation calls / full-scope capture
timing, capture completeness, final restore, report regeneration, failed-attempt history, the 117-record
offline audit, the positive-control timing probe (record digest, verdict, sample count and capture digest,
plus its indexed raw samples, summary and index: present, hash-correct and Git-tracked), tracked evidence
inventory, no-model provenance, frozen
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
| builder | `null_discovery`, `joint_full`, `alternative_full`, `partial_repair`, `partial_resource`, `partial_food`, `closer_only`, `escort_loss`, `escort_legitimate`, `new_exposure`, `covered_route`, `temporary_exposure_repaired`, `mixed_gain_loss`, `harm_only`, `repeat_undo` |
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

### Recorded amendments

- **2026-10-09, before any live command (pre-live review).** Scoring-chain: `fingerprint_dependencies`
  extended from 27 to 49 files with every `src/civ_mcp/lua/*.py` module `GameState` dispatches through
  (their result strings are what the classifier and predicates parse), `connection.py` gained
  `execute_mutation` (GameCore context for authoring setup, never re-sent), the loss audit classifies every
  non-success result shape through the shared classifier, and validation records a report-construction
  evidence failure per case. No packet existed; the full suite and the committed preflight evidence were
  regenerated under the new `code_identity`. Toolkit (no identity consequence): authoring setup runs in
  GameCore, coverage squares are grid-clipped, backwards wall clocks are journaled rather than fatal,
  native exports stage under the Windows checkout's gitignored `benchmark_runs/plan3-part1/exports/` with a
  pre-save `stat-save` signature excluding stale same-name saves, `finish` recovers a packet-only write
  failure, attempt directories and predecessor journals are confined to `benchmark_runs/plan3-part1/`, the
  gate verifies the timing probe's indexed raw evidence and imported predecessor references, and binding
  selectors accept list values ("any of"). Recipes: the city family's housing objective moved to Gongju
  (granary removed in setup) and its district objective became a hills-only Seowon over mine assets; the
  tactical family relocates Gongju's archer off the city tile.
- **2026-10-09, during the live phase, before any family clock opened (positive-control timing probe,
  Task 17).** Scoring-chain: the first live execution of the v2 capture program in `GameCore_Tuner` found
  two API differences from the InGame context that the offline tests cannot see. `CityDistricts` has no
  `Members()` iterator there (`GetNumDistricts()` + zero-based `GetDistrictByIndex(i)` replace it; the
  player-level collection iterates but carries no city id), and `BuildQueue:CurrentlyBuilding()` answers
  the string `"NONE"` for an empty queue (now read as no production, like nil/""). Every other call the
  program makes was verified present by a read-only probe; `GetResourceAccumulationPerTurn` is absent and
  was already guarded. `src/civ_mcp/lua/benchmark_v2.py` is a fingerprint dependency, so `code_identity`
  moved from `e9255e84…` to `0858553801927366fda0e90f66dfec072f57de4511bbf45b1d0448ab91f7974a`
  (commit `ecec720`); no packet existed; the full suite and the committed preflight evidence were
  regenerated under the new identity, and the probe passed under it (20/20 samples, mean 0.386 s,
  p95 0.396 s, max 0.408 s, one Lua execution each, one digest). Three earlier probe directories
  (`capture-probe`, `-2`, `-3`) retain the failed attempts: the first lost its reload to a cold
  save-list enumeration after game launch (the frontend load tier's 30 s cancel landed after the
  late result had already fired `Network.LoadGame`; operator note, no code change), the second and
  third hit the two API differences above.
- **2026-10-09, during the live phase, city family open (menu-check, city-a2 stage 014).** Toolkit
  (no identity consequence: `game_launcher.py` is a toolkit dependency, `code_identity` stays
  `08585538…`): the stage's crash-recovery loader called `game_launcher.restart_and_load` from WSL,
  whose Linux branch cannot reach the Windows game (`pgrep`/`pkill`/a `steam` binary that does not
  exist: `FileNotFoundError` before any game action). Under WSL with the Windows bridge present it
  now delegates the whole kill / relaunch / menu-load sequence to the native
  `civ6-launcher restart-and-load` in the Windows checkout and returns its result line unchanged
  (commit `316d203`, amended). The full suite was re-run and the committed preflight evidence
  regenerated under the unchanged identity; city-a2's capture and verify records stand and
  menu-check re-runs. Recipes (no identity consequence): the city family's a1 was abandoned on the
  live map (Jeonju is the only Seowon-capable city and offers two legal tiles) and the substitute
  `plan3-city-a2` authors a third legal site (tile (67,22) granted to Jeonju and mined); its housing
  shortfall moved from Gongju to Jeonju because Gongju's `get_cities` line falls past the
  1500-character feed cap at the archived start.
- **2026-10-09, during the live phase, tactical family open (validate, tactical-a1 stage 010).**
  Scoring-chain: a unit killed in combat lingers in GameCore for the rest of the turn as a
  delayed-death row (position -9999,-9999, hp 0); the v2 capture listed a dead own warrior as a
  living unit and a killed tracked attacker as "alive, not visible", so no loss was charged and no
  kill counted. The capture now skips dead and delayed-death units in the owned-unit rows, the
  tracked-target lookup and the visible-hostile scan (commit `12050df`); `code_identity` moved from
  `08585538…` to `af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697`. One packet existed (`plan3-city-a2-v1`, captured under the
  previous identity): per the revalidation rule it is repeated from survey under the new identity
  (same scenario identity, archive `plan3-city-a2-v2`), the full suite and committed preflight
  evidence are regenerated, and the preflight is rerun. Recipe (no identity consequence): the
  tactical family's a1, revised in place, chooses the attacker, archer, settler and warrior tiles
  jointly (Gongju is hemmed by mountains, coast and Jerusalem; its land approaches are forest, so
  the settler starts beside the city), starts the attacker at 65 hp with the provisional damage
  threshold at 40 (measured archer 56 / warrior 28), buys a spearman (the base holds 10 of the 20
  iron a swordsman needs), and repeats a purchase rather than an archer shot. Toolset defects
  recorded for a later identity: `attack_unit` answers a settler's attack and an archer's second
  shot, and `fortify_unit` answers a settler, with success-shaped text instead of rejections.
- **2026-10-10, before the builder family clock opens (decision 1, builder family).** Toolkit
  (no identity consequence: `benchmark_part1_gate.py` is a toolkit dependency, `code_identity`
  stays `af0de817…`): the builder family's required live case tag `final_charge` is withdrawn
  from `REQUIRED_LIVE_TAGS["builder"]` and from the tag table above. GameCore offers no
  build-charge setter (`Unit:ChangeBuildCharges` and `UnitManager.ChangeBuildCharges` are nil,
  `Set/ChangeActionCharges` do not touch build charges, every DB builder-charge modifier is
  positive), so the one-charge builder that `final_charge` presupposed is not expressible in
  the game's own API; `UnitManager.InitUnit` gives a builder 3 charges and 4 moves. Recipe
  (no identity consequence): `plan3-builder-a1` keeps the engine default (selectors and the
  setup readback require exactly 3 charges), `joint-full` drops the tag, and the
  `temporary-exposure-repaired` case is re-expressed as a retreat: the builder reaches the
  horses, the escort steps away, and the builder walks back into Jinju, whose city tile
  covers it (expected 0/0/0 with a `civilian_exposed` = false endpoint), because a
  three-charge builder that built the pasture would survive on the tile and the old
  completion-under-final-charge ending no longer exists. The escort's leave move targets an
  explicit tile, (72,31) across the river (binding `escort_leave_tile`, measured live on the
  base 2026-10-10: owned flat floodplain, empty, hex distance 1 from Jinju and 2 from the
  horses, in the swordsman's reachable set this turn) instead of the far food site, for which
  the live path query returned no path at all; `new-exposure` and the `escort-leaves-cover`
  probe use the same tile, and a `reach-escort-leave` pathing probe witnesses it. Every other
  case, objective, harm and expected score is unchanged. The scarcity rule of the spec (a
  stray improvement starves an objective) is forfeited for this family.
- **2026-10-10, before the city revalidation clock opens (decision 2, city family).**
  Prospective amendment granting the city family one revalidation attempt. The 2026-10-09
  delayed-death amendment required `plan3-city-a2-v1` (captured under `08585538…`) to be
  repeated under `af0de817…`, but the stage machine refuses every stage on a closed attempt,
  a fresh attempt for a scenario already journaled in a sibling directory is refused
  ("already journaled"), and a third scenario identity is refused by the two-identity family
  budget. The revalidation attempt is therefore declared explicitly: same recipe
  `plan3-city-a2.yaml` at `version: 2` (archive `plan3-city-a2-v2`, packet
  `plan3-city-a2-v2.json`), new attempt directory `benchmark_runs/plan3-part1/city-a2-reval`,
  new three-hour clock from `survey`, predecessor packet `plan3-city-a2-v1.json` named here and
  retained unchanged with its attempt directory. Toolkit (no identity consequence:
  `benchmark_authoring.py` and `benchmark_authoring_journal.py` are toolkit dependencies): the
  stage CLI gains `--revalidates JOURNAL`, the sibling journal holding the same scenario as
  passed; the new journal records that reference (`revalidates: {journal, sha256}`), the
  "already journaled" refusal is waived for exactly that journal, and nothing else about the
  identity budget, substitution rules or clock changes. The gate keeps reading every attempt
  directory of the family; the passed `city-a2` attempt remains in the attempt list with its
  own journal and index. Nothing in the city scenario depends on the fixed code path (no unit
  dies there), so the revalidation is expected to pass unchanged.
