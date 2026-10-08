# Arena benchmark: builder-economy calibration — findings (campaigns v1, v2 and v3)

Date: 2026-09-26. Driver: Claude Fable 5.1 (Claude Code session on the gaming PC), operator present.
Campaign v2 commit: `bf0f0b5`. Full repository suite at that tree: 3153 passed.

## 1. Verdict

**BLOCKED.** Neither admitted model block passed the preregistered separation gates under the
frozen campaign (`benchmark_runs/builder-economy-cal-v2/campaign_report.md`):

| block | decided pairs (≥10) | standard wins (≥10) | median signed Δ, normalized (≥0.3333) | outcome |
|---|---|---|---|---|
| gemma4-26b | 1 / 12 | 1 | 0.0000 | MODEL_FLOOR_NULL |
| qwen3.6-27b | 11 / 12 | 11 | 0.2917 (3.5 / 12) | MODEL_NULL |

Metric fidelity passed on both blocks (six hand-audited indices each agree with the scorer); every
tied pair is attributed `model_floor` with hash-bound transcript, final-state and counterfactual
findings. Thresholds were not modified and no trial was omitted.

The instrument contract stays `benchmarks/contracts/instrument-v1-candidate.yaml`; no
`instrument-v1.yaml` is released. **Plan 3 (nine-position library, multi-model screen) remains
blocked.**

> **Update (2026-10-08, original text above kept as written):** the v1–v3 verdicts stand, but the
> calibration requirement was subsequently met by the positive-control campaign (section 11). The contract
> is released as `benchmarks/contracts/instrument-v1.yaml` and Plan 3 is unlocked (section 12).

Interpretation, kept separate from the verdict: the treatment (builder tools) has a clear,
one-directional effect on qwen3.6-27b (11 wins, 0 losses, 1 tie) whose size fell just under the
preregistered 4/12 bar in v2 after clearing it (4.5/12, 12/12 wins) in v1 with the old scorer.
The v1 result cannot be counted (section 6). gemma4-26b sits at the task floor in both arms and
carries no information about the treatment.

## 2. Position provenance

`BUILDER_ECONOMY_CAL_V1` — manifest `benchmarks/positions/builder-economy-cal-v1.yaml`, archive
sha256 `a0d3ee9b019672b17c12f4569a27fdc1610d95b929d3d600002ac375823e38ec`, frozen canonical state
digest `1acfff788dff6fa126f452c57e66addab61f1a536ff492d9ee819ab8343c0c21`.

- Base save: organic single-player `SEONDEOK 100 400 BC` (Korea, turn 100, Prince, huge map,
  Expansion 2, game build 1.0.12.68, 36 active mods journaled with modinfo hashes in
  `benchmarks/provenance/builder-economy-cal-v1-authoring.json`).
- Six FireTuner mutations, each with before/after readback: pillage the iron mine at (68,23);
  forest onto the stone at (71,21); place builders 1572874 → (72,21) and 1507329 → (74,29); spawn
  builder 1769484 at (69,22). Task 3's builder originally started at (71,22); the live legality probe
  showed that tile crosses a river into (71,21), which ends movement, so it was moved.
- Three isolated single-turn tasks: repair the iron mine (Jeonju), pasture on horses at (74,30)
  (Jinju), clear forest then quarry stone at (71,21) (Gwangju). Rubric per task: 1 observed via
  `get_units`, 2 correct builder on its target, 4 exact tile mutation; maximum 12.
- Level 4 for each task was executed live through the same GameState methods the standard arm's
  tools dispatch to; twelve fresh deploy/reload cycles reproduced the digest; the crash-recovery
  menu path (kill → launch → OCR select → Escape) also loaded to the frozen digest.
- The map seed is not exposed to in-game Lua under any key; the map is pinned by the archive hash.

## 3. Instrument contract and campaign

- Contract candidate: evidence/predicate/report schema 1.0.0, scorer fingerprint
  `30783b592a620d3e035a268615d46170f8b53ff87dc06d281f05eaec44d3c0e6`.
- Campaign `benchmarks/campaigns/builder-economy-cal-v2.yaml`: gemma4-26b (max_tokens 3072) then
  qwen3.6-27b (max_tokens 6144) on `home-gpu0-cpp` (llama-swap / llama.cpp on home-llm, RTX 3090),
  temperature 0.2, top_p 0.95, thinking disabled, twelve seeds, ABBA, 24 trials per block, six audit
  indices (1, 2, 11, 12, 23, 24), rules 10 decided / 10 standard wins / median Δ ≥ 4/12. Identical to
  v1 except the campaign id (pinned by test).
- Arms differ only by tool tier: minimal (observe, move, research, production) vs standard (adds
  `get_builder_tasks`, `improve_tile`, `remove_feature`, `repair_improvement`, map/tech/great-people
  reads and combat). No briefing, tracker, memory or channels. Both arms expose `finish_trial`;
  neither exposes `end_turn`. Prompt: "Assess the current game situation, issue the best available
  orders for this turn, and call finish_trial when you are done."
- Prompt caching is pinned off for every benchmark request (section 7, item 8).

## 4. Admissions

Both v2 blocks passed the full live admission on the first attempt: clean identical checkouts
(WSL and Windows companion at `bf0f0b5`), boot health, tuner-holder, save deploy with hash chain,
verified production reload, popup hygiene, exact canonical digest, GPU isolation on home-llm,
model identity against the served model list, tool canaries for both arms (`finish_trial` and a
tool call observed), seed probe (repeated consistency plus seed sensitivity on an entropy canary).

| block | probe p95 latency | episode wall | boot-health evidence |
|---|---|---|---|
| gemma4-26b | 1.12 s | 300 s | responsive window (no slow frames logged while idle) |
| qwen3.6-27b | 9.20 s | 300 s | responsive window |

No infrastructure retries in either block (`attempts/` empty). Non-counting validation ran first on
gemma4-26b and passed with the same gates.

## 5. Results (v2)

Scores are raw rubric totals out of 12 (minimal, standard) per seed.

gemma4-26b: 1009 (3,3) · 101 (3,3) · 1103 (3,3) · 1201 (3,3) · 211 (3,3) · 307 (3,3) · 401 (3,3) ·
503 (3,3) · **601 (3,4)** · 701 (3,3) · 809 (3,3) · 907 (3,3).

qwen3.6-27b: 1009 (3,9) · 101 (3,7) · 1103 (3,9) · 1201 (3,9) · 211 (3,6) · 307 (3,10) · 401 (3,10) ·
503 (3,6) · 601 (3,6) · **701 (3,3)** · 809 (3,6) · 907 (3,6).

Action quality (block totals): gemma minimal 42 domain rejections, 0 mutations, 6 repetitions;
gemma standard 26 rejections, 16 mutations, 1 useful action; qwen minimal 27 rejections, 24
mutations, 0 useful; qwen standard 24 rejections, 52 mutations, 37 useful, 4 repetitions.
Tokens: gemma 607k prompt / 3.5k completion over 462 s; qwen 1.11M prompt / 19.9k completion over
1924 s.

What the transcripts show:

- gemma4-26b, standard arm, in almost every trial: build a farm with the repair builder on its own
  tile (69,22), then call repair on the wrong builder or a tile with no improvement, move a builder
  onto an occupied tile, attack a city-state at peace. The one decided pair (seed 601) moved the
  pasture builder onto (74,30) for level 2. Minimal arm: cycles of already-completed research or
  moving the swordsman.
- qwen3.6-27b, standard arm: reads `get_builder_tasks`, moves the repair builder onto (68,23) and
  repairs it in 11 of 12 trials; moves the pasture builder onto (74,30) and often builds the pasture;
  never removes the forest at (71,21) (it usually moves that builder toward the iron hill at (71,19)),
  but in two trials (seeds 307 and 401) it built a quarry directly on the forested stone tile. The game
  accepted that and the forest stayed, so the task 3 level 4 predicate ("feature absent") awarded only
  level 2 for a real quarry on the intended tile; those pairs scored 10, not 12. The tied pair (seed
  701) spent five steps surveying distant map areas and then acted with the wrong builder. Minimal arm:
  moves builders toward city centres, never onto a target.
- A "step" is one model turn and may carry several tool calls; qwen emitted up to 22 calls in 8 steps.

## 6. Campaign v1 (retained, BLOCKED)

The first counted campaign (`benchmark_runs/builder-economy-cal-v1`, scorer fingerprint
`8ec4f244…`) completed both blocks. gemma4-26b: 0/12 decided, all pairs 3–3, tie-attributed
`model_floor`. qwen3.6-27b: 12/12 decided, all standard wins, median signed Δ 4.5/12 = 0.375,
which would have passed all three gates. The Qwen hand audit disagreed with the scorer at indices 2
and 11:

- `recruit_great_person` answered `ERR:CANNOT_RECRUIT…`; the classifier recognised only `Error:` /
  `|BLOCKED` and scored the refusal as a success.
- `useful_actions` evaluated an objective's progress predicate on the resulting state alone, so once
  the repair builder stood on its target every later mutation, including an unrelated builder's
  off-target move, was credited.

Both deviate from the frozen definitions in the controlled-position design ("a valid call reached
game rules/state and was rejected"; "the action advances a position-declared objective; success
alone is insufficient"). By the preregistered rule the block is `METRIC_FIDELITY_FAILED` and the
campaign `BLOCKED`. The scorer was corrected as a pure implementation change (schema versions
unchanged, fingerprint changed), the contract re-frozen, and the identical preregistration rerun as
v2. v1 evidence is committed unchanged with its own report. The pair-level verdicts of v1 do not
depend on the corrected metrics, but the campaign rules do not allow counting them.

## 7. Instrument defects found and fixed during the live session

1. Save-list query window (5 s) shorter than a cold 283-save query; the still-registered Lua handler
   fired a stray reload later. 30 s window plus a cancel flag (`f458891`).
2. OCR read the save row as `BUILDER ECONOMY CAL VI`; the matcher folds 1/I/l like 0/O (`41d558f`).
3. The menu loader's blind post-select continuation (fixed wait, then positional click grid) fired
   during a long load; it now hands off to the classification-gated waiter (`c13650d`).
4. The bottom Load Game button is readable only as an OCR fragment; fragment and positional click
   added (`0147135`).
5. The FireTuner port is open on the continue screen and the load menu; screen evidence now wins
   over the port in both the classifier and the waiter (`2428b6c`).
6. The boot-health gate timed out on a healthy idle game because the profiler logs only slow frames;
   on a clean timeout it accepts a responsive game window, recorded distinctly (`08bd3ec`).
7. The `ss` tuner-holder filter was passed as one token; split into arguments (`6779a3d`).
8. llama.cpp's prompt cache changed seeded numerics between a cold and a warm call; benchmark
   requests pin `cache_prompt: false` (`34284ce`).
9. Seed sensitivity is unobservable on a peaked prompt (one identical tool call at every seed); an
   entropy canary proves seed plumbing when the locked prompt shows no difference (`b1d35fb`).
10. Scorer: `ERR:` prefix is a domain rejection; a useful action must itself flip a progress
    sub-predicate (`bf0f0b5`, section 6).

Operational: the Claude session's own `civ-mcp` server holds the single FireTuner slot through its
background watchers and must be stopped before any benchmark CLI runs; the runner requires
`LITELLM_OPENAI_API_KEY` even for the unauthenticated llama-swap endpoint.

## 8. Limits

- One position, two local models, single turn, eight model turns. The effect size on qwen3.6-27b is
  bounded by task 3: the model never removes the forest, and when it built the quarry directly (a legal
  move the authoring did not anticipate) the level 4 predicate did not credit it. The maximum Δ the
  model actually reached was 7/12; the predicate caps a direct-quarry play at 9/12.
- gemma4-26b is at the task floor in both arms; its block cannot inform the treatment question.
- qwen3.6-27b's v1 and v2 blocks differ (12/12 vs 11/12 wins; 4.5 vs 3.5 median) under identical
  seeds and sampling with prompt caching off: llama.cpp with four parallel slots is not bitwise
  reproducible across sessions. Seed pairing controls within-pair variance, not between-session.
- Boot-health evidence on an idle game rests on window responsiveness, not fresh frames.
- The audits were performed by the driving agent, not an independent reviewer.

## 9. Recommendation (not part of the verdict)

Re-run the preregistration once more as v3 without any change, to establish whether Qwen's effect
sits stably below or above the 4/12 bar; or, before Plan 3, revise the position so that the third
task is one a mid-size model plausibly attempts, and re-freeze under a new position version. Either
path keeps the thresholds fixed.

## 10. Campaign v3 (outcome-scored task 3) — BLOCKED; position retired

Preregistered 2026-09-27 (`docs/superpowers/plans/2026-09-27-builder-calibration-v3-preregistration.md`)
after the uncredited-actions audit showed task 3's level 4 scored the route (forest removed) rather
than the outcome (stone quarried). Position v2 changed task 3 only; campaign v3 was otherwise
identical to v2. Run at commit `6872e2d` (full suite 3165 passed), non-counting validation first,
both blocks admitted on the first counted attempt, no infrastructure retries.

| block | decided | standard wins | median signed Δ (norm.) | outcome |
|---|---|---|---|---|
| gemma4-26b | 0 / 12 | 0 | 0.000 | MODEL_FLOOR_NULL |
| qwen3.6-27b | 12 / 12 | 12 | 0.250 (3.0 / 12) | MODEL_NULL |

Metric fidelity passed on both blocks (all twelve audited indices agree with the scorer); all twelve
Gemma ties are attributed `model_floor`. **Verdict: BLOCKED.**

What the twelve Qwen standard trials completed (final states, `blocks/qwen3.6-27b/trials/`):

| outcome | trials |
|---|---|
| mine repaired (task 1) | 9 / 12 |
| pasture built (task 2) | 8 / 12 |
| both | 5 / 12 |
| stone quarried (task 3) | 0 / 12 |
| task 3 builder ended on the iron hill (71,19) | 8 / 12 |

Five trials completed two tasks (standard 9) and seven completed one (standard 6). The seven one-task
trials have two causes, neither of them task 3:

- **Mine missed (trials 2, 6, 19):** the first builder action was a farm with the repair builder on
  its own tile (69,22), which spent its moves; in trial 19 the model then sent a different builder
  toward the mine.
- **Pasture missed (trials 10, 15, 22, 23):** the episode reached its 8-turn cap. These trials spent
  7–9 of their 12–13 calls on observation, finished the repair chain, and ended before acting with
  the pasture builder.

So the effect was limited by three things together: a farm-in-place attractor on the repair builder's
own tile, the episode turn budget, and the iron-hill alternative drawing the task 3 builder.
Consistently completing tasks 1 and 2 alone would give a 6/12 effect against the observed baseline,
above the gate without any quarry. The corrected task 3 predicate never came into play because the
quarry was never built.

By the preregistered stopping rule, `BUILDER_ECONOMY_CAL_V1`'s archive is retired for calibration
under any rubric; the next calibration attempt uses a new position. The contract stays a candidate
and Plan 3 stays blocked.
*(Superseded 2026-10-08: the positive-control campaign met the requirement; contract released — see §§11–12.)*

What the evidence supports: under v2 and v3 (each under its own frozen rubric version) the treatment
arm won 23 of 24 pairs with one tie, a consistent direction on this position. v1 failed metric
fidelity and does not count toward that. This is not evidence of the instrument's general validity.
gemma4-26b is at floor on this position under this configuration; nothing here tests it elsewhere.

Next calibration candidate (design direction, not a commitment to a campaign): a deliberately simple
positive-control position — independently useful objectives, each verified achievable within the
episode turn budget, outcome-based scoring, no task builder standing on an improvable tile, and few
competing demands near the task builders. Its claim stays narrow: the instrument detects a known
capability difference under favourable conditions. The builder-task tool's own priority labels are
not a scoring authority (that would measure agreement with its heuristic); checking that each
objective is visible in its output is a design check only. Crediting beneficial alternatives remains
a separate scoring question that a positive-control position does not answer. Any pilot runs under a
fixed development budget with failures retained, and counted trials are fresh after the final freeze.

Two further loader defects surfaced in v3 preparation and were fixed before counting (`adc64d8`,
`6872e2d`): the save-list handler acted on results from the game's own menu queries (FileListQuery
results are broadcast; the main menu fires its own query), and a minimized game window made the
screen classifier read `unknown` indefinitely. The first v3 run directory, stopped at its
production-reload gate by the first defect, is retained as
`benchmark_runs/builder-economy-cal-v3.pre-queryid-fix-1dd405c`.

## 11. Positive-control campaign `builder-posctrl-cal-v1` — CALIBRATED (2026-09-28)

Position `builder-posctrl-v1` (design: `docs/superpowers/specs/2026-09-27-builder-positive-control-design.md`;
preregistration: `docs/superpowers/plans/2026-09-28-builder-posctrl-campaign-preregistration.md`): each task
builder starts on its target tile, so each task is one tool call; level 4 scores the intact improvement.

| block | decided | standard wins | median normalized Δ | outcome |
|---|---|---|---|---|
| gemma4-26b | 12/12 | 12 | 0.750 (9/12) | PASS |
| qwen3.6-27b | 12/12 | 12 | 0.750 (9/12) | PASS |

- Minimal scored 3/12 in all 48 minimal trials (observation only; no improvement tool).
- Standard scored 12/12 in 23 of 24 trials. The exception, Qwen seed 809 (trial 15), built the pasture
  and quarry and exhausted its eight model turns before issuing the repair (9/12).
- Gemma, at floor on the builder-economy position in v1–v3 (farm on its repair builder's own tile), completed
  all three tasks in every standard trial here. That supports the v3 diagnosis that the earlier position's
  shape, not a missing capability, limited the effect; it is not evidence about harder positions.
- Metric fidelity: independent semantic reads of indices 1, 2, 11, 12, 23, 24 in both blocks agree with the
  scorer on rejections, repetitions, useful actions and task scores. No tied pairs, so no tie attribution.
- Pilot (non-counting): round 1 passed on a full rerun after an infrastructure abort; both runs retained.

Scope (unchanged from the design note §1): this satisfies the calibration requirement only — the instrument
detects a known capability difference under favourable conditions. It does not validate scoring of
navigation, prioritisation, or beneficial alternative play; that concern stays open for Plan 3. Report:
`benchmark_runs/builder-posctrl-cal-v1/campaign_report.md`.

## 12. Plan 2 closure (2026-10-08)

Task 15 of the plan, executed against campaign `builder-posctrl-cal-v1` at commit `2227b19`:

- Full repository suite: **3177 passed** (`uv run pytest -q`, 152.9 s); `git diff --check` clean.
- Campaign report regenerated twice from the lock plus `trials/`; byte-identical both times:
  `campaign_report.json` `03d7a47e…`, `campaign_report.md` `d7a15e9c…`.
- Verdict applied as preregistered: both admitted blocks pass all three separation gates, both pass metric
  fidelity, no tied pairs. `CALIBRATED`. No threshold, audit index, or model configuration was modified.
- Instrument contract released: `benchmarks/contracts/instrument-v1.yaml`, regenerated from the scorer source
  with `benchmark_contract freeze` and identical in value to the candidate (fingerprint `30783b59…`). The
  release record `instrument-v1.md` carries the predicate vocabulary, authoring conventions, compatibility
  rules and evidence digests. The candidate file is retained unchanged because the four frozen campaign
  manifests reference it by path; a test pins the released values to the campaign lock.
- Plan 2 exit gate: every condition met (mandatory live admission; twelve reload cycles on the archive;
  prompt, rubric, sampling, tool identities, audits and verdict rules frozen before counting; one audited
  Gemma block; one audited Qwen block; byte-identical report regeneration; `CALIBRATED`). **Plan 3 is
  unlocked.** Handoff for its brainstorm: `docs/superpowers/plans/2026-10-08-plan-3-brainstorm-handoff.md`.

The scope caveat of section 11 carries forward unchanged: a positive control satisfies the calibration
requirement; it does not validate scoring of navigation, prioritisation, or beneficial alternative play.
