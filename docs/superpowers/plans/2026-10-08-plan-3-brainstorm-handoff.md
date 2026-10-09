# Plan 3 brainstorm — handoff

Written 2026-10-08 at the close of Plan 2, for a fresh session that will run `superpowers:brainstorming`
on Plan 3 of the arena benchmark. Read this first, then the design and the findings it points to. Nothing
in the original handoff is a decision; it records the state of the world and questions for the
brainstorm. Dated follow-ups below record subsequent decisions.

**Research direction update (2026-10-09):** the user reports that Claude is implementing Part 1.
The primary research objective is sustained pursuit of a self-chosen victory condition. The broad
SystemOne comparison is on hold following the Emerald screen; the next design should develop
persistent strategy and long-horizon evaluation. See the
[decision record and paper-derived research inputs](../../research/2026-10-09-victory-strategy-research-direction.md).
This changes follow-on prioritization, not the active Part 1 implementation contract.

**Brainstorm follow-up (2026-10-08):** the design sections for Part 1 were approved in conversation.
The [Part 1 authoring and scoring spec](../specs/2026-10-08-arena-benchmark-plan-3-part-1-design.md)
records those decisions and the delta roadmap for Parts 2 and 3. The six written-review amendments
were incorporated on 2026-10-08. The [Part 1 implementation plan](2026-10-08-arena-benchmark-plan-3-part-1.md)
is the execution handoff; its review revision separates the work into 22 tasks, pins tracked historical
evidence, and adds cancellation-safe single-query capture and a positive-control timing gate. Software
implementation and live authoring had not started at that October 8 handoff.
The handoff below retains the pre-brainstorm questions and evidence.

## 1. Where things stand

- **Plan 2 is closed with its exit gate met.** Campaign `builder-posctrl-cal-v1` is `CALIBRATED`:
  gemma4-26b and qwen3.6-27b both 12/12 pairs decided, 12 standard wins, median normalized Δ 0.750
  (gate 0.333). Metric-fidelity audits agree on both blocks; no ties. Report:
  `benchmark_runs/builder-posctrl-cal-v1/campaign_report.md`.
- **The instrument contract is released:** `benchmarks/contracts/instrument-v1.yaml` (+ `instrument-v1.md`
  release record: predicate vocabulary, authoring conventions, compatibility rules, evidence digests).
  Scorer fingerprint `30783b59…`, schema versions 1.0.0 / 1.0.0 / 1.0.0. Plan 3 positions are authored
  against this contract.
- **What was proven, exactly:** the instrument detects a known capability difference (builder improvement
  tools present vs absent) under favourable conditions — each task builder started on its target tile, so
  each task was one tool call. Standard scored 12/12 in 47 of 48 standard trials across pilot and campaign.
- **What was not proven, and why it matters for Plan 3:** three campaigns on the harder builder-economy
  position (`builder-economy-cal-v{1,2,3}`, all `BLOCKED`, position retired) showed that once navigation is
  required the effect is capped by (a) a farm-in-place attractor — the repair builder stood on an improvable
  tile and both models farmed it (Gemma 24/24 standard trials); (b) the 8-step budget — Qwen spends half its
  steps observing and hit the cap before acting in 4/12; (c) a decoy — an URGENT-labelled iron hill drew the
  task-3 builder in 8/12; (d) a route-scored predicate that under-credited a real quarry (fixed in v3).
  Full account: `docs/research/arena-benchmark-builder-calibration-v1-findings.md` §§6–11 and
  `docs/research/arena-benchmark-builder-calibration-uncredited-actions-audit.md`.

Campaign verdicts for reference:

| campaign | position | gemma4-26b | qwen3.6-27b | verdict |
|---|---|---|---|---|
| builder-economy-cal-v1 | builder-economy-cal-v1 | floor | 12/12 wins, but metric fidelity failed (scorer bugs) | BLOCKED |
| builder-economy-cal-v2 | builder-economy-cal-v1 | floor | 11/12 wins, median 0.292 | BLOCKED |
| builder-economy-cal-v3 | builder-economy-cal-v2 (quarry outcome-scored) | floor | 12/12 wins, median 0.250 | BLOCKED; position retired |
| builder-posctrl-cal-v1 | builder-posctrl-v1 | PASS 0.750 | PASS 0.750 | CALIBRATED |

## 2. What Plan 3 is, per the design

Parent design: `docs/superpowers/specs/2026-08-30-arena-controlled-position-benchmark-design.md`
(stages 2–4). Calibration design: `docs/superpowers/specs/2026-08-31-arena-benchmark-calibration-campaign-design.md`.
Plan 2's exit gate (`docs/superpowers/plans/2026-08-30-arena-benchmark-position-authoring.md`, "Plan 2 exit
gate") names three deliverables:

1. **The nine-position library** — six development, three held-out, all rubrics authored and digested
   *before any tested-model transcript from those positions is viewed*:
   development — early expansion and civilian safety; builder economy and repair (less artificial than
   calibration); city planning and production; tactical defense; diplomacy and trade; Great People and
   strategic spending. Held-out — religion and conversion response; World Congress and diplomatic
   positioning; late-game multi-system triage. Each: commandable without advancing the turn, two to four
   independently scored objectives, rubrics that reward verified progress and penalize harm without
   requiring one exact action sequence.
2. **Non-empty treatment options** — briefing and playbook arms (Stage 3 A/B suites), combined only
   after individual effects qualify. Plan 2 arms had `options: {}` by construction. Task-tracker evaluation
   remains deferred to multi-turn rollouts; the parent design forbids it in fresh single-turn trials.
3. **The multi-model screen** — roster `gemma4-26b`, `qwen3.6-27b` (anchors), `qwen3.8-27b-cpp`,
   `granite4.2-30b-cpp`, `ornith-1.5-35b-cpp` on `home-llm` (RTX 3090 24 GB + RTX 5060 Ti 16 GB; registry
   endpoint `home-gpu0-cpp` is llama-swap `--parallel 4`). Stage 4 held-out rules: equal-weight held-out
   improvement ≥ +0.05, ≥ 2 of 3 positions improve, none regresses by more than −0.10, no second tuning round.

## 3. Open problems the brainstorm must take a position on

These are carried from Plan 2 evidence; none is settled.

1. **Crediting beneficial alternative play.** The rubric scores declared objectives only. The uncredited-actions
   audit classified 117 uncredited mutations (28 farm-on-own-tile, 13 moves toward unscored task tiles, 45 no
   measured progress, 31 non-builder moves, 0 harmful). The design says rubrics "reward verified progress and
   penalize harmful choices without requiring one exact action sequence" — Plan 2 rubrics do not do that yet.
   A second "verified economic benefit" dimension was sketched (audit §4) but needs a preregistered benefit
   model (which yields count, charge pricing, strategic-resource valuation, unscored positioning) tested on a
   position other than the one it was derived from. It must never be combined into the primary score without
   re-deriving the threshold.
2. **Budget semantics.** `max_steps: 8` counts model turns, not tool calls or game turns; Qwen's episodes are
   observation-heavy (median 16 calls, 105 s wall) and Gemma's are not (8 calls, 20 s). A step budget that
   bounds the minimal arm also bounds the treatment. Whether to budget by steps, tool calls, wall time, or
   action count — and whether that differs per stage — is open.
3. **Threshold re-derivation.** 4/12 means "one complete task's value over a 12-point maximum". Two-to-four
   objectives per position with harm penalties changes the maximum and the meaning; each position (or the
   library as a whole) needs its own preregistered threshold before freezing.
4. **Seed diversity is weak.** Gemma at temperature 0.2 was near-deterministic across all 12 seeds on every
   position (identical action shapes). Twelve pairs on one save measure one decision twelve times. Position
   diversity, not seed count, carries the statistical weight; the Codex research (§5 below) makes the same
   point for held-out data.
5. **Authoring cost and tooling.** One position costs about half a day live: mutations via FireTuner Lua,
   legality probes through the standard arm's tools, capture, twelve reload cycles, menu-path check. Nine
   positions at that rate is a week of live time. The authoring helpers from Plan 2 lived in a session
   scratchpad and were lost once; Plan 3 should commit an authoring toolkit (`tools/` or
   `src/civ_mcp/arena/position_authoring.py`) before the first position.
6. **An unexplained tool defect.** `improve_tile IMPROVEMENT_CAMP` on ivory at (70,20) was refused
   (`UnitManager.CanStartOperation` false, no failure reasons) although `CanHaveImprovement` was true and
   Animal Husbandry was researched; reproduced on a clean reload. Any position that expects a camp must first
   resolve this (`benchmarks/provenance/builder-posctrl-v1-authoring.json` has the probe record).
7. **Objective-blind candidate generation.** Canonical state snapshots capture only the rubric's declared
   tiles; they are evidence, not a decision observation. Any treatment (briefing, tracker, or the decision-model
   experiment) must build its view from player-visible tools, never from the manifest's target list.
8. **Treatment definitions.** "Briefing" and "tracker" exist in the arena (`src/civ_mcp/arena/agent.py`
   `LLMPolicy`, `task_tracker.py`) but the benchmark agent (`benchmark_agent.py` `SingleTurnAgent`) is a
   separate objective-blind chat loop with no briefing/plan/channels. What a treatment arm injects, and how
   it is fingerprinted into the lock, is undefined.

## 4. Operational constraints (unchanged; do not rediscover these)

- The Claude session's own `civ-mcp` holds the single FireTuner slot; kill it (python + `uv run` parent)
  before any benchmark CLI. The session then has no civ6 MCP tools.
- Source `~/.config/riz-llm/.env` only into the runner's environment; never print the key. Gateway for
  counted runs: `http://192.168.20.146:11440/v1` (direct llama-swap on home-llm, the subject model).
- Hands off the gaming PC during a run: operator mouse use minimized the window, the Escape press was refused
  (Civ not foreground), and the run aborted on reload. The runner's attempt budget persists per run id, so a
  same-id resume after `attempts_exhausted` aborts immediately — rerun under a new run id.
- Huge-map load ≈ 90 s; a 24-trial block ≈ 30–55 min (Gemma ≈ 33 min, Qwen ≈ 56 min); twelve-cycle verify ≈ 25 min.
- Full operational playbook: `tools/skills/civ6-arena-live/SKILL.md` ("Benchmark runner sessions").

## 5. Deferred library consumer: decision-model experiment (on hold)

**2026-10-09 status:** this proposal is retained for a possible future scoped use case. The
user's current direction prioritizes persistent victory strategy and puts the broad comparison
on hold. The constraints below remain applicable if it is resumed; they do not queue backend
implementation or a new library experiment.

Codex research (2026-10-08, `~/.claude/research/2026-10-08-civ6-decision-model-practicality.md`, reviewed by
Claude the same day) proposes a bounded builder-selector experiment: code builds a candidate menu of complete
assignments, a decision model (Cloudflare Clef-flash first; Clef and TypeSafe Jev as comparisons) picks an id,
code executes and verifies. The review's load-bearing points, all of which bind Plan 3's library design:

- the **same-menu LLM control** is mandatory (a chat LLM on the identical menu), or any gain is attributable
  to the menu and surrounding code rather than the model;
- candidate generation must be objective-blind (open problem 7);
- the experiment needs exactly the navigation / scarce-charge / threatened-route / competing-task positions
  Plan 3 defines, split by scenario rather than seed (open problem 4);
- the calibrated positive control is a smoke test for it, not an evaluation (no score headroom).
- The original research treated local Clef deployment as outstanding and excluded Laya without a training
  corpus. Those infrastructure/roster assumptions are superseded by the update below; the historical
  research is not a current admission decision.

**Infrastructure update (2026-10-08):** the [SystemOne server handoff](../../handoffs/2026-10-08-systemone-decision-servers.md)
at `02f0176` records local Clef-Flash on riz-llm GPU1, Laya on home-llm GPU1, and a hosted Clef 27B reference.
Registry resolution is already vendored; the arena decision backend and protocol-specific admission are
deferred consumer requirements. The [Part 1 implementation plan](2026-10-08-arena-benchmark-plan-3-part-1.md) carries those
requirements by linking to the consumer-design section of the server handoff. Laya is now an available evaluation candidate; stronger
27B probabilities and the small Flash parity sample are not evidence of benchmark quality. The
same-menu control, objective-blind public observations, all-nine freeze and held-out restrictions remain.

## 6. Pointers

| what | where |
|---|---|
| released contract | `benchmarks/contracts/instrument-v1.yaml`, `instrument-v1.md` |
| runner / lock / report code | `src/civ_mcp/arena/benchmark_{runner,contract,manifest,report,campaign_report,state,gates,agent}.py`, `action_metrics.py` |
| position manifests, provenance, saves | `benchmarks/positions/`, `benchmarks/provenance/`, `benchmarks/saves/` |
| campaign manifests | `benchmarks/campaigns/` (four frozen; `builder-posctrl-cal-v1.yaml` is the template for new ones) |
| campaign evidence | `benchmark_runs/builder-{economy-cal-v1,v2,v3,posctrl-cal-v1}/`, `benchmark_runs/builder-posctrl-pilot-r1{,-rerun1}/` |
| findings | `docs/research/arena-benchmark-builder-calibration-v1-findings.md` (§11 posctrl, §12 closure) |
| positive-control design + pilot rule | `docs/superpowers/specs/2026-09-27-builder-positive-control-design.md` |
| preregistrations | `docs/superpowers/plans/2026-09-27-builder-calibration-v3-preregistration.md`, `2026-09-28-builder-posctrl-campaign-preregistration.md` |
| decision-server protocol and operational handoff | `docs/handoffs/2026-10-08-systemone-decision-servers.md` (`02f0176`) |
| tests pinning the frozen choices | `tests/arena/test_benchmark_contract.py`, `tests/arena/test_builder_posctrl_position.py` |

## 7. Suggested first brainstorm questions

1. Is the nine-position library still the right shape, or should Plan 3 start with three development positions
   (builder-economy-realistic, city planning, tactical defense) and the authoring toolkit, and defer the rest?
2. What does a rubric that "penalizes harmful choices" look like in the predicate vocabulary — new predicate
   kinds (bumps predicate schema version) or composition of existing ones?
3. Primary score only, or primary + a reported-but-not-gating benefit dimension from day one?
4. What is the budget unit for Stage 2, and is it the same for all models?
5. Should the decision-model experiment be a Stage 3 treatment arm, or a separate design that consumes the
   held-out library after Stage 4?
