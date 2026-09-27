# Uncredited-actions audit — builder-economy calibration (exploratory, non-counting)

Date: 2026-09-27. Scope: every successful mutation in campaigns v1 and v2 (both models, both arms,
96 trials) that the frozen rubric and the corrected scorer did not credit as useful. This is a
classification exercise to inform a future rubric; it changes nothing about the v2 verdict
(`BLOCKED`), which stands under its frozen rules.

Method. (1) Offline: re-score all 96 committed trials with the corrected scorer (`bf0f0b5`),
collect each successful mutation that flipped no objective progress predicate, identify the moved
unit from the step's before/after state, and classify moves by hex distance to the position's own
30 builder-task tiles (the list `get_builder_tasks` shows the model). (2) Live: reload the frozen
position and execute each distinct builder action once, recording per-city yields before and after
through the FireTuner gamecore state. Same-turn city yields are the only benefit measure available;
strategic-resource accumulation is not exposed by the API used.

## 1. What the uncredited mutations were

117 uncredited mutations across 64 trials, 33 distinct action sequences.

| class | count | what it is |
|---|---|---|
| `improve_tile FARM` with the repair builder on its own tile (69,22) | 28 | Gemma standard in all 24 trials (v1 and v2); Qwen standard in 4 |
| non-builder unit moves (swordsman, scouts, archer, apostles) | 31 | exploring or shuffling military; no economic effect this turn |
| builder move, no change in distance to any task tile | 36 | pasture builder to (73,29); forest builder to (70,21); repair builder to (70,23)/(71,22) |
| builder move away from all task tiles | 9 | repair builder to (67,23) or (70,22) |
| builder move toward another improvable task tile | 13 | forest builder onto the iron hill (71,19), listed URGENT by `get_builder_tasks`; pasture builder to (74,27) diamonds |

So 45 builder moves were waste in the position's own terms, 13 were constructive positioning toward
a legitimate task the rubric does not score, and 28 were the farm.

## 2. Measured benefit of each distinct builder action

Reload → action → same-turn city yield readback (food/production per turn). Only rows that changed
are shown; all other cities and yields were unchanged.

| action | credited by rubric | Gwangju | Jeonju | Jinju | other effects |
|---|---|---|---|---|---|
| farm on (69,22), own tile (uncredited Gemma play) | no | prod 7.0 → 8.0 | — | — | builder 1769484 loses 1 of 3 charges, cannot repair this turn |
| move to (68,23) + repair mine (task 1) | yes, level 4 | — | prod 17.1 → 18.0 | — | iron mine active again; iron per turn not measurable same turn |
| move to (74,30) + pasture (task 2) | yes, level 4 | — | — | none same turn | horses access; Jinju is pop 1 and works one tile |
| move to (71,21) + quarry on the forested stone (Qwen v2 seeds 307, 401) | level 2 only | prod 7.0 → 9.0 | — | — | forest retained; the largest immediate gain of the four |

Two observations matter for rubric design:

- **The position's task 3 predicate is mis-specified.** Level 4 is "feature absent", on the authoring
  assumption that a quarry needs the forest removed first. The game accepts a quarry on the forested
  stone tile directly; Qwen did that twice and received level 2 for the economically best action
  available. The intended objective was "the stone is quarried", and the predicate should have been
  `improvement == IMPROVEMENT_QUARRY` (with `feature absent` as an alternative route), not the route.
- **The farm is a real but poor use of the charge.** It yields +1 production at Gwangju this turn and
  consumes the charge and movement the repair needed; the repair is worth +0.9 production plus iron
  supply. "Model floor on the scored objectives" is the accurate description of Gemma's block, not
  "no useful capability".

Caveat: same-turn city yield readback may lag for some improvements (the pasture showed no change);
these numbers are indicative for classification, not a benefit model.

## 3. Classification summary

| verdict | count | basis |
|---|---|---|
| verified benefit, uncredited | 2 | quarry on forested stone: +2 production, intended tile |
| small benefit at the cost of a scored objective | 28 | farm on own tile: +1 production, spends the repair charge |
| constructive positioning toward an unscored legitimate task | 13 | forest builder onto iron hill (URGENT in the model's own task list); pasture builder to diamonds |
| waste of moves | 45 | builder moves that do not reduce distance to any task tile |
| neutral | 31 | non-builder unit moves |
| harmful | 0 | no destructive replacements or losses observed |
| insufficient evidence | 0 | every mutation resolves to a unit and tile in the committed state |

## 4. Implications for a two-dimension rubric (design input, not a proposal to score)

1. Fix the task 3 objective so it scores the outcome (quarried stone) rather than the route. This is a
   predicate change → new position version, new freeze.
2. A second reported dimension, "verified economic benefit", needs a preregistered benefit model:
   which yield deltas count, how a spent charge is priced, how a strategic-resource gain is valued
   when it is not observable same turn, and how "toward an unscored task" positioning is treated.
   None of that can be derived from these 96 trials without fitting to them; it should be tested on
   the smoke position and at least one new position before it is frozen.
3. If the primary score changes, the 4/12 threshold's justification does not carry over; it must be
   re-derived for the new maximum and the new definition.
4. Repeated-action credit must be excluded explicitly (the corrected scorer already requires a
   predicate flip per action; a benefit dimension needs the same rule on yields).

## 5. Reproduction

- Offline classification: re-score `benchmark_runs/builder-economy-cal-v{1,2}/blocks/*/trials/*.json`
  with `civ_mcp.arena.action_metrics` at `bf0f0b5`; task tiles parsed from any committed
  `get_builder_tasks` result; hex distance on Civ6 offset coordinates (odd rows shifted right).
- Live yields: reload `BUILDER_ECONOMY_CAL_V1` through `civ_mcp.game_lifecycle.load_game_save`,
  snapshot per-city `City:GetYield` for all yields, execute the action through
  `GameState.move_unit` / `improve_tile` / `repair_improvement` (unit indices 12, 1, 10 after
  reload), snapshot again. Four reloads, about four minutes; the frozen position was restored after.
