# Builder positive-control position — design note

Status: design proposal, 2026-09-27. Offline only; no position authored, no trial run. Supersedes nothing:
the builder-economy position stays retired and campaigns v1–v3 stay `BLOCKED`.

## 1. Claim

Narrow by design: **the instrument detects a known capability difference — builder improvement tools present
(standard arm) versus absent (minimal arm) — under favourable conditions.** It does not test navigation,
prioritisation among competing opportunities, general builder play, or crediting of beneficial alternatives.
Those remain open questions for later positions.

## 2. What the v1–v3 evidence says a positive control must avoid

From the v3 Qwen standard trials (mine 9/12, pasture 8/12, both 5/12, quarry 0/12) and the audit:

| observed limiter | trials affected | design response |
|---|---|---|
| farm built with the repair builder on its own improvable tile, spending its moves | 3 / 12 (v3), 24 / 24 Gemma standard (v1+v2) | the builder's own tile **is** the task tile, so acting in place is the task |
| 8-turn cap reached while the model was still observing, before a builder acted | 4 / 12 | no walking: each task needs exactly one tool call; three actions fit well inside the budget |
| task 3 builder drawn to an alternative (iron hill) | 8 / 12 | no move is needed, so no nearby target competes on the path |
| route-scored objective (forest removal) undercredited a real quarry | 2 / 12 (v2) | every level 4 scores the outcome on the task tile, never a route |

## 3. Position shape

Same organic base save as before (`SEONDEOK 100 400 BC`, identity already journaled), freshly authored into a
**new archive** with new mutations — not `BUILDER_ECONOMY_CAL_V1`'s archive, which is retired. Candidate tasks,
chosen from the Task 11 survey of the unmutated base save so that every improvement is available at the
empire's current tech and none needs a feature removed:

| task | tile | base-save state | builder starts | intended action | outcome predicate (level 4) |
|---|---|---|---|---|---|
| repair | (68,23) Jeonju | iron mine, grass hills | on the tile, after pillaging the mine | `repair_improvement` | `pillaged == false` |
| pasture | (74,30) Jinju | horses, flat grass, unimproved | on the tile | `improve_tile PASTURE` | `improvement == PASTURE` |
| camp | (70,20) Gwangju | ivory, flat roaded plains, unimproved | on the tile | `improve_tile CAMP` | `improvement == CAMP` |

The stone at (71,21) is not used: in the base save it has no forest, and a quarry task would duplicate the
camp's shape. All three are to be re-verified live during authoring (legal in one call, no river or
stacking issue, builder charges ≥ 2 so the builder survives).

Rubric per task: level 1 = observed via `get_units` (both arms reach it); level 4 = the outcome predicate.
No level 2 (a builder standing on its target is the starting state, not progress) and no level 3. Maximum 12.

Expected arms: minimal can observe but has no improvement tool, so it stays at 3/12 unless it does something
unanticipated; standard reaches 12/12 with three calls. The treatment headroom is 9/12.

## 4. Threshold and gates

Unchanged from the calibration design: effect gate = one complete task's maximum value over the rubric
maximum = 4/12; at least 10 of 12 pairs decided and at least 10 standard wins. Written into the new campaign
only after the rubric freezes, per the spec. With the observed-baseline floor at 3/12, passing the effect gate
requires the median standard trial to complete at least two of the three tasks (3 → 9 is Δ 6; one task,
3 → 6, is Δ 3 and fails).

## 5. Pilot budget and stopping rule (preregister before any pilot)

- Development pilot: at most **two rounds** of non-counting validation, **four qwen3.6-27b episodes per arm**
  each, on the candidate archive. Every pilot record is retained.
- A round passes if standard completes ≥ 2 tasks in ≥ 3 of 4 episodes and minimal stays at 3/12.
- Between rounds only position-authoring defects may change (an illegal action, a stacking or charge
  problem, a mis-specified predicate); the rubric shape and threshold do not.
- If round 2 does not pass, the positive-control design is abandoned and reported; no third round.
- After a passing round: freeze the position and campaign, then **fresh** counted trials (validation,
  Gemma block, audit, Qwen block, audit) under the same one-campaign stopping rule as v3.

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
