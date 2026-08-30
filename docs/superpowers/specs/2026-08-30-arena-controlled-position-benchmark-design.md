# Arena controlled-position benchmark — Design

**Date:** 2026-08-30 · **Status:** approved (design presented and accepted in
session) · **Scope:** single-turn controlled comparisons, calibration, model
screening, and scaffold A/B experiments

## Context

The arena campaign can now run long, evidence-rich games, but those runs are
expensive and difficult to compare causally. A thirty-turn result combines the
model, tool surface, prompt scaffold, sampling, interturn AI behavior, popup
recovery, and accumulating game-state differences. It is valuable external
evidence, but it is a poor first instrument for deciding whether one model or
one scaffold variable improves decisions.

The target capability is controlled comparison across several identical saved
positions. Most trials should answer a smaller question: given exactly the same
queried game state, what does one model configuration do during one complete
decision episode? Promising configurations can then advance to short rollouts
and, finally, ordinary arena runs.

This design extends the arena because the arena already owns the treatment
knobs: tool tiers, result caps, step budgets, briefing, playbook, memory, task
tracking, channels, provider selection, model gateways, transcripts, and cost
accounting. It does not build on CivBench. CivBench's identical saves are useful
precedent, but its driver and scoring are oriented around long turn-1/full-game
runs rather than treatment-controlled one-turn trials.

The benchmark is a dedicated runner under the arena package. It reuses arena
primitives without invoking `ArenaCoordinator` for Stage 1. That boundary is
load-bearing: coordinator seat shepherding, blocker repair, drain arms, and
turn advancement would add behavior to the object being measured.

## Goals

1. Compare models and scaffold treatments from identical, versioned game
   positions.
2. Establish that the measurement instrument can detect a known, mechanical,
   large effect before using it on unknown behavioral effects.
3. Separate decision quality from latency, token use, and cost.
4. Make every recorded trial attributable to a clean code revision, exact
   model endpoint, exact save, queried starting state, treatment fingerprint,
   sampling configuration, and immutable rubric.
5. Survive process crashes without silently changing schedules, discarding bad
   model outcomes, or rerunning completed episodes.
6. Report each position independently and aggregate positions with equal
   weight so one strong position cannot hide a regression elsewhere.

## Non-goals

- Replacing the arena coordinator for long games.
- Designing the short-rollout driver in this spec.
- Claiming that single-turn rubric quality proves long-run strategy quality.
- Using an LLM judge as the primary calibration scorer.
- Testing the task tracker in a fresh one-turn episode. The tracker requires
  prior-turn state and belongs in the future rollout design.
- Parallel trials. FireTuner port 4318 is single-client, so every live trial is
  strictly serial.

## Research progression

The research program has six stages.

### Stage 1 — Instrument calibration

Use a deliberately clear builder-economy position and a known tool-surface
contrast. Qwen 3.6 calibrates first; Gemma 4 repeats the same protocol as an
independent replication.

### Stage 2 — Fixed-scaffold model screen

Screen complete model configurations on six development positions. The
scaffold is standard tools with all unproven treatments off. Models advance by
predeclared overall, robustness, and optional specialist rules.

### Stage 3 — Targeted scaffold A/B experiments

Run independent briefing and playbook A/B suites on the selected models. A
third budget-related experiment runs only if preregistered diagnostics show a
step, repetition, or truncation bottleneck. Treatments are combined only after
their individual effects qualify.

### Stage 4 — Held-out confirmation

Compare each locked candidate scaffold with that model's original treatment-off
baseline on three held-out positions. No second tuning round follows held-out
results.

### Stage 5 — Short rollouts

Deferred to a separate design. Five-to-ten-turn trials reintroduce `end_turn`,
interturn AI processing, popup/orphan/World Congress stalls, recovery policy,
and T+N outcome scoring. They are not "the same manifest with a longer
driver."

### Stage 6 — Thirty-turn arena finalists

Use the established arena machinery for a small number of finalists. These
runs test external validity and longer-run behavior; they are not the primary
controlled benchmark.

## Architecture

Three approaches were considered:

| Approach | Advantage | Rejected because |
|---|---|---|
| Add benchmark mode to `ArenaCoordinator` | Maximum immediate reuse | Coordinator behavior can contaminate a one-turn treatment |
| Dedicated runner using arena primitives | Clean measurement boundary with treatment/provider reuse | Selected; requires a small orchestration layer |
| Shell-orchestrate many normal arena runs | Small prototype | Fragile recovery, process overhead, and weak atomicity |

The selected architecture is:

```text
Benchmark suite + position manifests
                 |
                 v
         Serial BenchmarkRunner
                 |
                 +-- deploy and reload named position
                 +-- continue load, reconnect, dismiss popups
                 +-- capture and verify canonical queried state
                 +-- construct fresh model context
                 +-- apply treatment and sampling configuration
                 +-- SingleTurnTrialDriver
                 +-- capture immutable raw trial evidence
                 +-- derive metrics, rubric scores, and reports
```

The runner reuses:

- Arena tool-tier resolution and tool schemas.
- `CivOptions` treatment configuration and fingerprinting.
- OpenAI-compatible model providers and endpoint registry resolution.
- Arena prompt construction where it remains treatment-neutral.
- Full transcript records, cost accounting, and the shared action classifier.

It does not reuse:

- Coordinator seat admission or shepherding.
- Seat-zero blocker-repair passes.
- Drain arms or automatic end-turn requests.
- Persistent conversation, memory, or task state unless a later suite
  explicitly designs and admits those treatments.

## Prerequisite fixes

Several existing gaps become critical-path dependencies.

### In-game Lua reload continuation

The frontend `LoadGameMenu` Lua path already invokes
`continue_after_lua_load`. The in-game `Network.LoadGame` tier does not. The
benchmark reloads from a running world on every trial, so the in-game path
must:

1. Prove `Network.LoadGame` matched and engaged.
2. Invoke the existing continue-after-load helper.
3. Wait through the load/leader screen without manual input.
4. Re-establish a fresh FireTuner connection.
5. Prove the world is commandable with a queried-state call.

This is TDD'd before any trial-runner logic is implemented.

### Shared popup hygiene

Reloads can land with blocking contexts such as Historic Moments or disaster
popups. `_dismiss_blocking_popups` currently lives as a coordinator private.
It moves to a shared module such as `arena/popups.py`; the coordinator and
benchmark both import the shared helper.

The helper retains the context-native close rule. In particular,
`NaturalDisasterPopup` must execute its own context's `Close()`; hiding it from
`InGame` leaks a `PopupManager` engine hold and can wedge the game. The runner
executes popup hygiene once after every reload, before canonical state capture,
then verifies that no known blocking popup remains.

### Explicit backend sampling and retry contract

`OpenAICompatBackend.chat` currently sends neither `temperature`, `top_p`, nor
`seed`, and its broad internal retry can invisibly resample malformed model
output. The backend gains an explicit, recorded sampling object and retry
policy while preserving current arena defaults for existing callers.

Benchmark mode sends the suite's temperature, `top_p`, maximum completion
tokens, and requested seed. Hidden response resampling is disabled. The runner
owns whole-trial retry admission under the preregistered failure classes below.

### Shared measurement layer

The current analyzer has channel-specific rejection counts and older scenario
rubrics, but it lacks general useful-action, domain-rejection, and repetition
metrics. A shared action classifier feeds both `civ-arena-analyze` and the
benchmark scorer so future arena runs receive the same measurement vocabulary.

## Repository layout and manifests

Canonical saves are small (the existing CivBench files are approximately
0.5–0.75 MB), so they are versioned directly in this repository without Git
LFS.

```text
benchmarks/
  saves/
    BUILDER_ECONOMY_CAL_V1.Civ6Save
  positions/
    builder-economy-cal-v1.yaml
  suites/
    builder-economy-calibration-v1.yaml
benchmark_runs/
  <run-id>/
```

An existing save is immutable. Any position change creates a new versioned
filename and position ID.

### Position manifest

A position manifest owns stable game and scoring facts:

- Position ID and version.
- Repo archive path.
- Archive SHA-256.
- In-game save name passed to `load_game_save`.
- Player ID and game identity.
- Canonical normalized queried-state snapshot and checksum.
- Manifest-scoped units, cities, and tiles.
- Position-specific invariants.
- Two-to-four scored decision objectives.
- Observable rubric predicates and score levels.
- Setup, authoring, and validation notes.
- Development or held-out designation.

The archive hash proves deployment provenance. It is not the runtime load gate.

### Suite manifest

A suite manifest owns experiment-specific facts:

- Suite ID and `single_turn` driver.
- Position IDs.
- Models, providers, endpoint constraints, and model-block order.
- Treatment arms and exact `CivOptions`.
- Common benchmark-control tools.
- Model-step, result-character, completion-token, and wall-time limits.
- Temperature, `top_p`, and the preregistered seed list.
- ABBA treatment ordering where applicable.
- Rubric gates and treatment advancement rules.
- Preselected transcript-audit indices.
- Startup, endpoint, and treatment-can-fire assertions.
- Maximum infrastructure attempts and failure taxonomy.

### Immutable session lock

At startup the runner resolves the suite into a lock containing:

- Git commit and clean-tree evidence for WSL and the Windows companion.
- Manifest and expanded schedule fingerprints.
- Requested and resolved model identity and endpoint.
- Registry digest, host, service mode, GPU indexes, quantization, and context.
- Prompt, playbook, tool-schema, rubric, and scorer digests.
- Sampling parameters and seed-probe result.
- Position archive and canonical queried-state hashes.
- Exact ordered trial schedule.

Resume refuses any lock mismatch. Code, position, rubric, prompt, tool schema,
sampling, model topology, or treatment changes require a new run.

## Windows save deployment

The repository save is an archive, not the file Civ VI loads. The game runs
natively on Windows and uses the OneDrive-redirected Documents Known Folder.
When imported under WSL, `game_launcher` selects the unrelated Linux/Aspyr
save path, so WSL must not infer the destination itself.

A native-Windows launcher operation performs deployment:

1. Resolve the Windows single-player save directory through
   `SHGetKnownFolderPath(FOLDERID_Documents)`.
2. Verify the archive SHA-256 from the manifest.
3. Copy through a temporary file and atomically replace the dedicated deployed
   save.
4. Verify the deployed file's SHA-256.
5. Return structured evidence to the WSL runner.

The runner invokes this through the existing Windows companion/launcher bridge.
The companion checkout must be clean and at the same commit as WSL. Deployment
and hash verification occur before the first load, and the queried-state
checksum is verified at startup before any model episode can count.

## Canonical queried-state checksum

Save-file bytes prove only that the expected file was installed. They do not
prove that the correct world loaded or that the load completed. The runtime
gate hashes normalized state queried through the new connection after reload
and popup hygiene.

The canonical snapshot includes:

- Game identity, turn, and active player.
- Player gold and faith.
- Sorted unit IDs, types, coordinates, movement, and charges.
- Sorted city IDs and coordinates.
- Ownership, resource, feature, improvement, and pillage state for every
  manifest-declared relevant tile.
- Additional position-specific invariants declared in the manifest.

The normalized JSON is stored alongside its digest. A mismatch produces a
field-level diff, aborts the entire session, and yields no treatment data. It
is never converted into a failed model trial. Restarting later resumes from
the first incomplete schedule index only after the canonical state matches
again.

## Session startup gates

The runner refuses to start recorded work until all gates pass:

1. WSL worktree is clean and at the locked commit.
2. Windows companion is clean and at the same commit.
3. Boot-health check passes.
4. Canonical save deploys and both source and destination hashes match.
5. Endpoint allocation is isolated from unrelated local-model work.
6. Requested model identity is proven on the resolved endpoint.
7. Model is warm and representative latency is recorded.
8. Sampling/seed behavior is probed.
9. Save reload completes without manual input.
10. Popup hygiene completes and the world is commandable.
11. Canonical queried-state checksum matches.
12. Treatment-can-fire assertions pass.

Treatment-can-fire assertions include:

- The resolved tool schemas differ exactly as declared.
- `finish_trial` is present in every arm.
- `end_turn` is absent in every arm.
- Position-required actions are legal and observable.
- For calibration specifically, the minimal arm can discover every target
  through its actual observation tools and can legally reach rubric levels
  1–2 through recognition and `move_unit`; the standard arm can legally reach
  level 4 through repair, improvement, or feature removal. The gate therefore
  proves that the rubric has meaningful range in both arms, not only that the
  standard mutation tools exist.
- A briefing arm has positive computed budget, nonzero sections, and actual
  injected briefing content.
- A fresh one-turn suite cannot claim to test task tracking without
  preregistered prior task state; the initial benchmark forbids that suite.

## Endpoint and GPU admission

Every model runs as a strictly serial block. Immediately before a block, the
runner records the requested model, live registry resolution, actual URL,
response-reported identity, registry fingerprint, warm canary, and latency.
The gate strengthens the existing live-skill endpoint-isolation rule: green
health alone is insufficient.

The initial roster is:

- `gemma4-26b` — historical anchor.
- `qwen3.6-27b` — historical anchor and calibration model.
- `qwen3.8-27b-cpp` — newer Qwen candidate.
- `granite4.2-30b-cpp` — alternative architecture.
- `ornith-1.5-35b-cpp` — larger alternative candidate.

GPU routing policy:

- Require the exact model artifact/quantization and benchmark context size.
- Prefer a single-GPU llama.cpp endpoint when the model and KV cache fit with
  safe headroom.
- Use a live-registry-declared unified mode for Granite or Ornith when needed.
- Detect and report conflicting GPU workloads before warming the model. The
  runner blocks by default; it may drain only services named in an explicit,
  scoped operator acknowledgment for that run, and never kills unmanaged or
  unidentified processes automatically.
- Record host, endpoint, GPU indexes, mode, model identity, quantization,
  context size, and warm latency.
- Fix the allocation for the entire model block.
- Refuse mid-block per-GPU/unified or host failover; a topology change requires
  a new lock.

The current vendored registry explicitly exposes `riz-unified-cpp`. Home-LLM
per-GPU entries advertise unified capability but the snapshot has no separate
stable `home-unified-cpp` ID, so code resolves the live registry instead of
inventing an endpoint. The operator authorized use of both `riz-llm` and
`home-llm` for this campaign, but that historical authorization is not itself
a runner permission to terminate future workloads; the scoped acknowledgment
above remains required.

Quality remains the primary comparison. Latency and throughput are reported
as topology-conditioned when models require different GPU modes.

## Seed probe and ordering

The calibration uses twelve preregistered seeds. The same requested seed is
used within a treatment pair; seeds differ across pairs. Temperature, `top_p`,
token limits, and all other sampling parameters are fixed within a suite.

A startup probe sends a canonical request with repeated and differing seeds.
It uses the suite's exact temperature, `top_p`, token settings, prompt shape,
and tool schema so a temperature-zero or otherwise mismatched probe cannot
misclassify seed support:

- If identical seed/configuration reproduces and at least some differing seeds
  diverge, the endpoint is recorded as seed-honoring.
- If seed is ignored, the requested value is still recorded, but seed blocks
  degrade to repetition/time-order structure and no paired-seed claim is made.

Treatment order follows ABBA across adjacent pairs. Full reload and fresh
conversation eliminate ordinary carryover; counterbalancing protects against
time-correlated drift such as game-session degradation or gateway changes.
Every reload verifies the canonical checksum. Drift aborts the session instead
of becoming a data point.

## Single-turn trial semantics

Each Stage 1 trial is one complete decision episode, not one tool call:

1. Reload the named save.
2. Continue, reconnect, and dismiss blocking popups.
3. Capture and verify canonical starting state.
4. Construct a new backend/agent conversation with no prior memory.
5. Apply the arm's tools and fixed sampling configuration.
6. Allow up to fifteen model round trips and the model block's locked,
   latency-derived episode wall time.
7. Capture final queried state without calling `end_turn`.
8. Atomically persist raw scoreable evidence.

The episode wall catches runaway work rather than pacing healthy models. Before
the block starts, the endpoint gate runs at least ten representative warm
round trips using the suite's prompt shape, full tool schema, and sampling
configuration. It locks:

```text
episode_wall_s = max(300, ceil(max_steps × p95_roundtrip_s × 1.5))
```

The measured samples, p95, formula inputs, and resulting wall are stored in the
session lock and shared by every arm in that model block. Changing the wall
requires a new lock.

The calibration scaffold holds everything except tool tier constant:

- Qwen 3.6 first; Gemma 4 replication second.
- Same system and benchmark prompt.
- No briefing, task tracker, memory, channels, attention, or standing-plan
  carryover.
- No playbook treatment.
- Same working step, result, context, completion-token, and sampling budgets.
- Historical full `minimal` versus full `standard` tool tiers.
- Identical benchmark-control schema containing `finish_trial`.

The position removes meaningful opportunities for unrelated standard-tier
tools, but the fingerprint records the entire schema delta.

An episode is complete and scoreable when:

- The model calls `finish_trial`.
- The model emits a response with no tool calls (implicit finish, matching the
  existing `LLMPolicy` behavior).
- Fifteen model round trips are exhausted.
- The block's latency-derived episode wall is reached.
- The model emits malformed response/tool output.

If `finish_trial` accompanies game actions in one response, supplied game
actions execute in order and the episode then terminates. Step-limit
exhaustion, zero useful actions, incoherent orders, domain rejections, loops,
and model-generation timeouts with a healthy post-timeout endpoint are
outcomes, not retry reasons.

## Retry and evidence-admission policy

The maximum is three total infrastructure attempts per scheduled trial,
including attempts across process restarts.

| Event | Admission behavior |
|---|---|
| Save deployment or reload failure | Infrastructure attempt; retry |
| Popup or reconnect failure before a commandable episode | Infrastructure attempt; retry |
| Harness crash before a complete episode | Infrastructure attempt; retry |
| Demonstrably exogenous connection failure or unavailable gateway | Infrastructure attempt; retry |
| Unknown failure class | Stop for classification; never automatically retry |
| Malformed model response/tool output | Scoreable trial failure |
| Request/episode timeout; immediate canary is unhealthy, unresponsive, or wrong-identity | Infrastructure attempt; retry |
| Request/episode timeout; immediate canary is healthy and identity-correct | Scoreable runaway-generation failure |
| Step-limit exhaustion | Completed, scoreable trial |
| Zero useful actions or incoherent decisions | Completed, scoreable trial |
| Domain rejection or repeated action loop | Completed, scoreable trial |

Every attempt is journaled with a preregistered failure class. Exhausting three
attempts stops the session rather than shrinking the arm. Completed trials
record the attempt count and prior infrastructure classes so retry pressure is
visible in reports. Every timeout triggers a short, independently bounded
canary against the exact endpoint; the request failure, canary response,
latency, health, and identity verdict are journaled whether the episode is
admitted as infrastructure or scoreable evidence.

## Persistence and resume

The expanded schedule is immutable and the journal is append-only.

```text
benchmark_runs/<run-id>/
  session.json
  schedule.json
  journal.jsonl
  attempts/
    trial-001-attempt-001.json
  trials/
    trial-001.json
  report.json
  report.md
```

`attempts/` contains only non-scoreable infrastructure attempts.

`trials/trial-NNN.json` is immutable raw evidence. It is written through an
atomic rename immediately after a scoreable episode and final-state capture.
It contains the full transcript, terminal condition, starting and ending
queried state, attempt count, configuration references, and evidence digests.
Once it exists, the model episode is never run again.

Scoring is downstream of evidence admission. A scorer crash resumes scoring,
not gameplay. Reports are disposable projections regenerated solely from the
session lock and `trials/`; scoring never consumes `attempts/`. A scorer fix
creates a newly fingerprinted report over the same raw evidence.

Resume skips a scheduled trial only when its raw artifact exists and matches
the session fingerprint. Partial infrastructure work stays under `attempts/`.
Finished evidence is never silently overwritten, and re-running finished
trials cannot disturb the ABBA schedule.

## Shared action metrics

The shared classifier uses complete `tool_result_full` content and queried
state, never only the result text truncated for the model.

- **Valid tool call:** schema and arguments were accepted by the harness.
- **Domain rejection:** a valid call reached game rules/state and was
  rejected.
- **Successful mutation:** the game accepted the action and relevant queried
  state confirms it.
- **Useful action:** the action advances a position-declared objective;
  success alone is insufficient.
- **Constructive positioning:** verified movement advances a declared
  objective without completing it.
- **Repetition:** the same normalized action and arguments recur after the
  same result with no relevant intervening state change.
- **Loop excess:** repeated occurrences after the initial reasonable attempt.
- **Observation overhead:** read-only calls and tokens before the first useful
  action and per completed objective.

Reports keep harness-invalid calls, game-domain rejections, successful but
irrelevant mutations, and useful progress separate. This prevents a technically
true "0% invalid" rate from hiding a high game-rule rejection rate.

## Calibration position and rubric

The calibration uses one dedicated, versioned builder-heavy save with no
military distractions or alternative builder priorities.

1. A full-movement builder stands on a pillaged resource improvement; repair
   is the unambiguous action.
2. A full-movement builder is adjacent to an unimproved luxury or strategic
   resource on a flat/roaded approach that permits move-then-improve in one
   turn.
3. A charged builder stands on a tile whose feature must be removed before the
   preregistered improvement can eventually be built; feature removal is the
   complete one-turn objective.

All tiles are owned and workable. Builders have sufficient charges. The world
has no visible enemy/civilian danger, pending diplomacy, production/research
choice, promotion, or other blocker. Unit IDs and target coordinates are
recorded.

Each task scores from 0–4 using observable predicates:

- 0 — no progress or a harmful action.
- 1 — correctly identifies and commits to the task.
- 2 — correctly positions the builder.
- 3 — completes a valid prerequisite or partial objective.
- 4 — verified target tile state achieved.

The total is twelve points. The primary calibration gate is not successful
builder-call count: minimal structurally cannot call `improve_tile`, so that
contrast alone proves only plumbing.

Calibration passes only if both conditions hold:

1. Standard has a higher rubric score in at least ten of twelve schedule
   pairs; ties count as non-wins. The one-sided sign-test probability for ten
   or more wins under an even null is approximately 0.019.
2. Median paired improvement is at least four rubric points, one complete
   task's value.

Builder-action counts remain diagnostics. Metric fidelity is a second required
gate: six transcript indices are preregistered, balanced three minimal/three
standard and spread across early, middle, and late ABBA blocks. Human audit
must agree with automatic useful-action, domain-rejection, and repetition
classification. Any discrepancy blocks calibration until the metric is fixed
and the protocol reruns.

Qwen 3.6 is the primary calibration model because v8 recorded 17 successful
builder actions across 21 action-bearing turns versus Gemma's 7 successes.
Gemma then runs the same twelve-pair protocol as a generality check.

## Position library and rubric freeze

The initial library contains the calibration save plus nine decision-quality
positions: six development and three held-out.

Development:

1. Early expansion and civilian safety.
2. Builder economy and repair, less artificial than calibration.
3. City planning and production.
4. Tactical defense.
5. Diplomacy and trade.
6. Great People and strategic spending.

Held-out:

7. Religion and conversion response.
8. World Congress and diplomatic positioning.
9. Late-game multi-system triage.

Every decision-quality position is commandable without advancing the turn and
contains two-to-four independently scored objectives. Rubrics reward verified
progress and penalize harmful choices without requiring one exact action
sequence.

All development and held-out rubrics are authored together before Stage 2 and
before any tested model transcripts from those positions are viewed. Their
digests enter the lock. A post-freeze rubric edit creates a new rubric/position
version and applies only to future runs; it never retroactively rescales
evidence. This differs from a scorer implementation fix, which may regenerate
reports over unchanged rubric predicates and raw evidence.

The benchmark prompt is objective-blind and generic across every position. Its
instruction is limited to the equivalent of "assess the current situation and
issue the best orders available for this turn; finish when done," plus the
standard turn/player announcement and generic `finish_trial` protocol. It
contains no position names, objective labels, coordinates, unit references,
resource names, target outcomes, rubric text, or hints such as "your builders
have work available." Position manifests and rubrics are never injected. The
prompt is authored without model pilot transcripts, frozen and digested
alongside the rubrics before Stage 2, and any later edit creates a new suite
version rather than silently tuning an existing comparison. The calibration
uses the same objective-blind template, frozen before its first counted trial.

## Stage 2 model screen

The model screen uses the six development positions only. Its scaffold is
explicitly treatment-free:

- Standard tool tier.
- Fixed working model-step, result-character, completion-token, and context
  budgets.
- Briefing off.
- Task tracker off.
- Memory off.
- Channels and attention off.
- Playbook treatment off.

Every model passes endpoint isolation and identity immediately before its
block. Trials are strict serial. Each model-position receives three trials:

```text
5 models × 6 development positions × 3 trials = 90 trials
```

The same three requested seeds are used across models only when the endpoint
seed probe passes. Otherwise they are repetition/order labels and within-seed
pairing claims are dropped.

For each model, compute the median normalized rubric score per position. The
overall development score is the equal-weight mean of those six medians; the
worst-position median is reported separately.

At most three models advance:

1. **Overall leader:** highest equal-weight mean.
2. **Robustness leader:** highest worst-position median. If the overall leader
   also wins robustness, the second-highest overall model takes this slot.
3. **Optional specialist:** a remaining model advances only if it wins at least
   two positions by a normalized margin of 0.10. Otherwise the slot remains
   empty.

Every position is reported separately regardless of advancement.

## Stage 3 scaffold experiments

Selected models run independent A/B suites on all six development positions.
Each arm-position has six repetitions in ABBA order:

```text
6 positions × 2 arms × 6 repetitions = 72 trials per finalist per A/B
```

### Briefing A/B

The first treatment is briefing off versus verified-positive briefing. Both
arms use standard tools and identical prompt, playbook state, step/cap budget,
context, and sampling. If context or common budgets must change to make the
briefing fit, they change identically in both arms. The briefing arm cannot
start unless its budget is positive and content was actually injected.

### Playbook A/B

The second treatment is no playbook versus the condensed playbook. It branches
from the original screen baseline, not from whichever briefing arm wins. This
measures the playbook's independent marginal effect.

### Diagnostic-triggered budget A/B

A third budget treatment is not chosen in advance. A new preregistered suite
is allowed only if screen diagnostics trigger it:

- At least 20% of trials hit the fifteen-step limit while still making rubric
  progress: test fifteen versus a higher step budget.
- At least 20% show repetition after step eight with no further rubric gain:
  test eight versus fifteen.
- At least 10% truncate score-relevant tool results: test the result-character
  cap while holding steps constant.
- If none fire, skip the budget experiment.

Task-tracker A/B is forbidden here. A fresh one-turn trial has no prior task
state, so enabling it would not test multi-turn follow-through.

### Treatment advancement and interaction

A treatment qualifies for a model only if all conditions hold:

1. Equal-weight mean normalized improvement is at least +0.05.
2. Position median improves on at least four of six development positions.
3. No position regresses by more than -0.10.

If neither briefing nor playbook qualifies, the model keeps the screen
baseline. If one qualifies, it becomes the candidate scaffold. If both
qualify, run another six-repetition A/B comparing the stronger individual
treatment with briefing-plus-playbook. This measures interaction rather than
assuming gains add.

## Stage 4 held-out confirmation

For each finalist, compare its locked candidate scaffold with that model's
original treatment-off baseline on the three held-out positions:

```text
3 positions × 2 arms × 8 repetitions = 48 trials per treated finalist
```

If no treatment qualified and candidate equals baseline, run only eight
baseline repetitions per held-out position.

The candidate confirms only if:

1. Equal-weight held-out improvement is at least +0.05.
2. It improves at least two of three held-out positions.
3. No held-out position regresses by more than -0.10.

A failed candidate reverts to the original baseline for later research. There
is no further single-turn tuning against held-out results. Candidate-arm scores
also supply the complete-configuration model comparison; baseline arms preserve
treatment-effect interpretation.

## Aggregation and reporting

The position is the blocking unit. For every stage:

- Report every position independently.
- Normalize each position rubric to [0, 1].
- Compute treatment/model differences within position and schedule/seed block
  when the relevant pairing is supported.
- Weight positions equally, never by number of tasks, tool calls, or trials.
- Report wins/ties/losses and median differences alongside equal-weight means.
- Report useful actions, constructive positioning, domain rejections,
  repetition, observation overhead, latency, tokens, cost, attempt count, and
  topology.
- Keep quality separate from speed and cost.
- Keep development and held-out evidence visibly separate.
- Never let an aggregate hide a position-level regression.

## Error handling

The runner fails closed around evidence admission:

- Dirty worktree, lock mismatch, endpoint identity failure, deployment hash
  mismatch, treatment-can-fire failure, or canonical-state drift prevents a
  session from starting or continuing.
- Unknown failures stop for classification instead of being retried under a
  broad catch-all.
- Interruptions retain attempt evidence and never overwrite raw trials.
- Official results require the complete preregistered schedule. Partial
  operational status may be displayed but is not an experiment report.
- Reports can be regenerated after scorer fixes without touching raw evidence.

## Implementation order

1. Shared action classifier and existing analyzer integration.
2. In-game Lua reload continuation and commandable reconnection.
3. Shared context-native popup helper.
4. Explicit backend sampling/seed/retry contract.
5. Position/suite manifests, validation, schedules, and fingerprints.
6. Native Windows save deployment and hash evidence.
7. Canonical queried-state capture and mismatch diagnostics.
8. Serial runner, fresh contexts, retry journal, atomic raw trials, and resume.
9. Derived scorer and deterministic reports.
10. Author/freeze ten initial saves and rubrics, then execute calibration.

## Automated testing

- In-game Lua load calls continuation and reconnects to a commandable world.
- Popup extraction preserves context-native close behavior for coordinator and
  runner callers.
- Backend sends exact sampling parameters and exposes rather than hides
  benchmark retries.
- Seed and warm-latency probes use the suite's exact sampling/prompt/schema;
  the derived episode wall is locked and identical across arms in a model
  block.
- Timeout health probes deterministically separate endpoint failure from
  healthy runaway generation and journal both verdict paths.
- Metric fixtures distinguish harness-invalid calls, domain rejections,
  successful mutations, useful progress, and repeated loops.
- Counterfactual mutations prove each metric test can fail.
- Every outcome-affecting manifest change changes the fingerprint.
- Dirty or mismatched checkouts block startup.
- Windows deployment rejects source or destination hash mismatch.
- Canonical hashes are order-stable and change for every declared invariant.
- State drift aborts instead of creating a treatment observation.
- Infrastructure failures retry; bad model outcomes do not.
- Crashes at every persistence boundary resume without duplicate trials.
- A raw completed episode survives scorer failure and is never replayed.
- Reports ignore `attempts/` and regenerate deterministically from lock plus
  `trials/`.
- Neither tier exposes `end_turn`; both expose `finish_trial`.
- The objective-blind prompt contains no manifest/rubric terms or
  position-specific hints, and the minimal calibration arm can attain rubric
  levels 1–2 without possessing standard builder mutation tools.
- Regression tests that guard absence, restore, cancellation, or admission
  paths receive counterfactual/severing checks during implementation.

## Live admission before calibration

The 24-trial Qwen calibration cannot start until a non-counting live gate
passes:

1. Windows deployment and deployed hash verification.
2. Twelve consecutive unattended reloads of the calibration save.
3. Commandable state and identical queried checksum after every reload.
4. No surviving blocking popup.
5. Endpoint isolation/identity and seed-probe evidence.
6. One smoke decision episode per tool-tier arm.
7. Deterministic report regeneration from smoke evidence.
8. Clean committed WSL and Windows checkouts.

The smoke episodes are not part of the calibration sample.

## Deferred work

- Short-rollout driver, recovery playbook integration, and T+N outcome rubric
  framework.
- Task-tracker off/on evaluation.
- Automated harvesting of additional positions after the curated core is
  stable.
- Thirty-turn finalist experiment design and execution.
- Parallel model execution; port 4318 remains single-client.
