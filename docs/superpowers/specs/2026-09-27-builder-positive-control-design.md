# Builder positive-control position — design note

Status: design, revised 2026-09-27 after review (intact-improvement predicates, progress predicates, median
wording, pilot freeze rules, hypothesis wording, scope boundary). Offline until authoring starts. Supersedes nothing:
the builder-economy position stays retired and campaigns v1–v3 stay `BLOCKED`.

> **Status update (2026-10-08):** executed. Position authored 2026-09-27 (§3 authoring result), pilot round 1
> passed 2026-09-28 (§9), counted campaign `builder-posctrl-cal-v1` **CALIBRATED** 2026-09-28 (findings §11),
> instrument contract released 2026-10-08 (findings §12). The design text above is kept as frozen.

## 1. Claim

Narrow by design: **the instrument detects a known capability difference — builder improvement tools present
(standard arm) versus absent (minimal arm) — under favourable conditions.** It does not test navigation,
prioritisation among competing opportunities, general builder play, or crediting of beneficial alternatives.
Those remain open questions for later positions. Passing this positive control would satisfy the calibration
requirement only; it would not validate scoring of broader beneficial play. That concern stays unresolved and
must remain visible when Plan 3 opens.

## 2. What the v1–v3 evidence says a positive control must avoid

From the v3 Qwen standard trials (mine 9/12, pasture 8/12, both 5/12, quarry 0/12) and the audit:

| observed limiter | trials affected | design response (hypothesis, tested by the pilot) |
|---|---|---|
| farm built with the repair builder on its own improvable tile, spending its moves | 3 / 12 (v3), 24 / 24 Gemma standard (v1+v2) | the builder's own tile is the task tile, so acting in place is the task |
| 8-turn cap reached while the model was still observing, before a builder acted | 4 / 12 | no movement calls: three successful action calls suffice; whether the model chooses them within eight steps is what the pilot tests |
| task 3 builder drawn to an alternative (iron hill) | 8 / 12 | no move is needed; whether the model still turns to other tiles is what the pilot tests |
| route-scored objective (forest removal) undercredited a real quarry | 2 / 12 (v2) | every level 4 scores the outcome on the task tile, never a route |

## 3. Position shape

Same organic base save as before (`SEONDEOK 100 400 BC`, identity already journaled), freshly authored into a
**new archive** with new mutations — not `BUILDER_ECONOMY_CAL_V1`'s archive, which is retired. Candidate tasks,
chosen from the Task 11 survey of the unmutated base save so that every improvement is available at the
empire's current tech and none needs a feature removed:

| task | tile | base-save state | builder starts | intended action | outcome predicate (level 4) |
|---|---|---|---|---|---|
| repair | (68,23) Jeonju | iron mine, grass hills | on the tile, after pillaging the mine | `repair_improvement` | `improvement == MINE` and `pillaged == false` |
| pasture | (74,30) Jinju | horses, flat grass, unimproved | on the tile | `improve_tile PASTURE` | `improvement == PASTURE` and `pillaged == false` |
| camp | (70,20) Gwangju | ivory, flat roaded plains, unimproved | on the tile | `improve_tile CAMP` | `improvement == CAMP` and `pillaged == false` |

All three are to be re-verified live during authoring (legal in one call, no river or stacking issue,
builder charges ≥ 2 so the builder survives).

**Authoring result (2026-09-27, before any pilot):** the camp on the ivory at (70,20) was refused through
the standard arm's `improve_tile` on both an in-session placement and a clean reload
(`UnitManager.CanStartOperation` false with no failure reasons), although the rules allow it
(`CanHaveImprovement` true, Animal Husbandry researched); cause not identified. A task the standard tools
cannot perform cannot serve a positive control, so it was replaced by the quarry on the bare stone at
(71,21), which the probe accepted. Authored archive `BUILDER_POSCTRL_V1`
(sha256 `265bb002…`), details in `benchmarks/provenance/builder-posctrl-v1-authoring.json`. Final tasks:
repair the iron mine (68,23), pasture on the horses (74,30), quarry on the stone (71,21).

Rubric per task: level 1 = observed via `get_units` (both arms reach it); level 4 = the outcome predicate,
which requires the named improvement to exist and be intact. No level 2 (a builder standing on its target is
the starting state, not progress) and no level 3. Maximum 12. Every level 4 predicate must be false in the
frozen starting state, asserted by test.

Objective progress predicates equal the completion predicates and nothing else. The "builder on target"
conditions used by earlier positions are removed: they are true at entry, so under the delta scorer they
could only flip by a pointless leave-and-return. Useful-action attribution therefore measures the
improvement's own transition to completion.

Expected arms (hypotheses): minimal can observe but has no improvement tool, so it should stay at 3/12;
standard can reach 12/12 with three successful calls. The treatment headroom is 9/12.

## 4. Threshold and gates

Unchanged from the calibration design: effect gate = one complete task's maximum value over the rubric
maximum = 4/12; at least 10 of 12 pairs decided and at least 10 standard wins. Written into the new campaign
only after the rubric freezes, per the spec. One completed task adds three points over the observed
baseline; the gate requires a median paired gain of at least four. With twelve pairs the median is the mean of
the sixth and seventh sorted deltas, so for example six one-task pairs (Δ 3) and six two-task pairs (Δ 6)
give a median of 4.5 and pass.

## 5. Pilot budget and stopping rule (preregistered here, before any pilot)

- Mechanics: the ungated suite runner (non-counting by construction, stamped as such), qwen3.6-27b only,
  both arms, ABBA, **four pairs** on pilot seeds **2011, 2027, 2039, 2053** (disjoint from the counted
  seeds), same sampling, prompt and eight-step cap as the counted campaign. Suite file
  `benchmarks/suites/builder-posctrl-pilot-r1.yaml`, which carries the campaign prompt through the suite
  manifest's optional `prompt` field (added for this pilot; the ungated path otherwise sends the legacy
  per-turn prompt).
- Freeze before viewing any transcript: the round's archive hash, position manifest (rubric and progress
  predicates), prompt, sampling, seeds and the pass criterion below are committed before the round runs.
- Pass criterion (deliberately stricter than the counted gate): standard completes at least two tasks in at
  least three of four episodes, and minimal scores exactly 3/12 in all four.
- **Round two only after a documented authoring defect** (an illegal action, a stacking or charge problem, a
  mis-specified predicate). If round one runs on a sound position and the model misses the criterion, the
  design stops there and is reported. A correction after a defect is a new position version with complete
  re-validation, and round two uses fresh seeds.
- Every pilot record is retained. Pilot results never count toward the campaign.
- After a passing round: freeze the counted position and campaign, then **fresh** counted trials
  (validation, Gemma block, audit, Qwen block, audit) under the same one-campaign stopping rule as v3.

A pilot of four episodes per arm exposes obvious defects; it does not assure a passing median.

## 6. Campaign

Same models, sampling, seeds, ABBA order, audit indices, prompt and turn cap as v3, so the only designed
change is the position. Gemma stays the mandatory first block for comparability; whether it clears floor on a
no-navigation position is itself informative, and a floor there is still `MODEL_FLOOR_NULL`, not an
instrument failure.

## 7. Explicitly out of scope

- Crediting beneficial alternative play (a separate scoring problem; removing alternatives here does not
  answer it).
- Using the builder-task tool's priority labels as a scoring authority. They may be checked during authoring
  to confirm each objective is visible in the tool's output, nothing more.
- Any claim of general instrument validity from a positive control.

## 8. Cost

Authoring about half a day live (mutations, legality probes, save, capture, twelve reload cycles, menu load
check); pilot about 30 minutes per round; counted campaign about 2.5 hours.

## 9. Pilot round 1 result (appended 2026-09-28, after the round)

- First execution `benchmark_runs/builder-posctrl-pilot-r1` aborted on infrastructure before completing the
  round: trial 3 exhausted its three reload attempts (the game was left on the continue screen while the
  operator was using the mouse and the window was minimized; the game process then exited). Two trials
  completed (minimal 3/12, standard 12/12). Retained; not evaluated as the round.
- The frozen round was then executed in full, unchanged (same suite file, seeds, order, commit `6888647`),
  as `benchmark_runs/builder-posctrl-pilot-r1-rerun1`, with no infrastructure attempts.

| arm | seed 2011 | 2027 | 2039 | 2053 |
|---|---|---|---|---|
| minimal | 3/12 | 3/12 | 3/12 | 3/12 |
| standard | 12/12 (3 tasks) | 12/12 (3) | 12/12 (3) | 12/12 (3) |

Criterion met: standard completed at least two tasks in 4 of 4 episodes; minimal exactly 3/12 in all four.
No authoring defect observed: every standard episode issued `repair_improvement`, `improve_tile PASTURE`
and `improve_tile QUARRY` in place and each succeeded; minimal episodes moved builders off their tiles or
hit a stacking rejection, neither of which touches a scored predicate. Pilot results do not count. Next,
per §5: freeze the counted position and campaign and run fresh counted trials.
