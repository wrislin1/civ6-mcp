# Arena benchmark calibration campaign — Design

Date: 2026-08-31

Parent design: `2026-08-30-arena-controlled-position-benchmark-design.md`

Implementation-plan target:
`docs/superpowers/plans/2026-08-30-arena-benchmark-position-authoring.md`

This focused design governs Plan 2 wherever it conflicts with the parent
design, especially the Plan 2/Plan 3 split, model order, lock hierarchy,
calibration verdicts, and instrument-freeze requirements. The parent's later
research stages remain the roadmap after this design's exit criteria pass.

## Context

Plan 1 delivered a reviewed, live-smoke-tested controlled-position benchmark
runner. It can deploy and reload a save, verify queried starting state, execute
strictly serial single-turn trials with fresh model contexts, preserve raw
evidence, resume safely, and derive reports. The first live smoke run proved
that the runner works, but it also showed why its evidence is not yet suitable
for counted calibration:

- The CLI runs only with `--ungated-smoke`; the pure admission gates exist but
  are not wired to live evidence.
- A model can pass an identity probe while ignoring OpenAI tool schemas.
- An orphaned FireTuner client can silently wedge every new connection.
- A valid-looking rubric predicate can abort reporting when its subject is
  consumed or deleted.
- Save-menu and frontend continuation behavior still has known operational
  edges.
- The smoke position and saturated rubric were fixtures, not a calibration
  instrument.

This design defines Plan 2: everything between “the runner works” and “the
repository contains counted calibration evidence that can be trusted.” It does
not author the nine-position decision-quality library or run the later model
screen. Those become Plan 3 because rubric-authoring conventions must first
survive a counted calibration campaign.

## Goals

1. Wire fail-closed, fresh live admission into counted benchmark execution.
2. Preserve the reviewed trial loop, retry classifier, journal, store, and
   resume behavior as the trusted execution core.
3. Add one immutable campaign identity plus one evolved session identity per
   model block.
4. Author and freeze one controlled builder-economy calibration position.
5. Version and freeze the evidence, predicate, and report contracts used by
   future position authors.
6. Run a 24-trial Gemma4 calibration block followed by a 24-trial Qwen3.6
   replication block.
7. Produce an audited campaign verdict that structurally excludes ungated
   smoke evidence.

## Non-goals

- The nine-position development/held-out decision library.
- The Qwen 3.8, Granite, and Ornith model screen.
- Briefing, playbook, task-tracker, or other scaffold treatments.
- Non-empty treatment-arm `options`.
- A reusable position generator.
- Generalized deep save-menu scrolling unless fresh deployment fails live.
- Short rollouts, `end_turn`, interturn recovery, or multi-turn scoring.
- Rewriting the existing single-turn trial engine.

## Research boundary and exit criteria

Plan 2 ends when all of the following are true:

1. Counted mode cannot start without fresh live admission.
2. `BUILDER_ECONOMY_CAL_V1` is archived, deployed, digest-pinned, and proven
   reproducible through twelve runner-path reloads.
3. Its objective-blind prompt, rubric, seed schedule, audit indices, model
   configurations, and campaign rules are frozen before counted trials.
4. Gemma4 receives a complete counted block; inability to admit or complete
   the primary block leaves Plan 2 blocked.
5. Qwen3.6 replication is attempted and either completes or is recorded as an
   admission deferral under the rules below.
6. All admitted blocks pass the preregistered metric-fidelity audit.
7. At least one admitted model passes the calibration separation gate.
8. The evidence, predicate, and report contracts are published as the frozen
   instrument version against which Plan 3 positions will be authored.

Plan 3 remains blocked if no admitted model passes. A Qwen admission deferral
does not block Plan 3 when Gemma has already passed, but the campaign must be
reported as `CALIBRATED_REPLICATION_DEFERRED`, not as a completed replication.

## Architectural approach

Plan 2 adds an integrated campaign lifecycle around the existing runner.
Admission and execution are one operation: time-sensitive evidence is gathered
immediately before a counted model block and cannot be reused from a stale
preflight file. An `--admit-only` mode may diagnose problems, but its output
never authorizes a later run.

The campaign layer wraps the existing runner rather than replacing it. The
existing runner retains ownership of:

- per-trial reload and popup hygiene;
- canonical-state verification;
- fresh model conversation construction;
- tool dispatch and evidence capture;
- timeout health discrimination;
- infrastructure-attempt classification;
- append-only journaling;
- atomic trial commits; and
- fingerprint-gated resume.

Plan 2 may change that core only at its existing boundaries: accept a fully
resolved model block, use the block's locked settings, and stamp both campaign
and session fingerprints when committing evidence. If implementation begins
reworking the trial loop, retry classifier, or store semantics, that is a scope
violation requiring a new design review.

## Evidence layout and identities

```text
benchmark_runs/<campaign-id>/
  campaign.json
  schedule.json
  campaign-journal.jsonl
  admissions/
    gemma4-26b-attempt-001.json
    qwen3.6-27b-attempt-001.json
  blocks/
    gemma4-26b/
      session.json
      schedule.json
      journal.jsonl
      attempts/
      trials/
    qwen3.6-27b/
      session.json
      schedule.json
      journal.jsonl
      attempts/
      trials/
  report.json
  report.md
```

### Campaign lock

`campaign.json` is the new immutable shared lock. It freezes:

- campaign ID and campaign-schema version;
- clean repository commit expected on WSL and Windows;
- position ID/version, archive hash, save name, and expected-state hash;
- environment requirements: game build, ruleset, DLC, and mods;
- objective-blind prompt and digest;
- rubric and digest;
- evidence, predicate, and report schema versions;
- model order: Gemma4, then Qwen3.6;
- each model's predeclared inference configuration;
- minimal and standard arm definitions;
- expected tool-surface fingerprints;
- twelve seeds, per-block ABBA order, and frozen audit indices;
- sensitivity, separation, effect-size, and campaign outcome rules; and
- full campaign-schedule fingerprint.

Every field contributes to `campaign_fingerprint` except the fingerprint field
itself. The campaign lock contains no live admission claim.

### Evolved session lock

The existing `session.json` becomes the per-model block lock. No third lock
artifact is introduced. It retains its current evidence and adds:

- parent `campaign_fingerprint`;
- block schedule fingerprint;
- fresh clean-checkout evidence;
- boot-health and tuner-holder evidence;
- deployment and canonical-state evidence;
- live endpoint, host, GPU topology, artifact, quantization, and context;
- response-reported model identity;
- exact sampling and `chat_template_kwargs`;
- per-arm structured tool-call canary results;
- seed verdict and representative warm-latency samples;
- locked episode-wall calculation;
- tool-surface fingerprint; and
- exact tool-input fingerprint for each arm.

Every field contributes to the existing `session_fingerprint`. Each raw trial
is stamped with both fingerprints. Resume, reporting, and aggregation fail
closed on a missing or mismatched stamp.

### Structural smoke separation

The ungated smoke path never mints a campaign fingerprint. It may retain its
own explicitly ungated smoke-session identity for diagnostics, but a campaign
report requires both a matching campaign fingerprint and a matching block
session fingerprint on every trial. Copying or renaming a smoke artifact
cannot promote it into counted evidence.

Scoring consumes only the campaign lock, completed block session locks, and
their `trials/` directories. Admission files and infrastructure `attempts/`
may be rendered as operational context but never contribute scores.

## Campaign orchestration

The campaign wrapper performs four operations:

1. Create or reopen the immutable campaign.
2. Admit the next incomplete model block using fresh live evidence.
3. Hand the existing runner a resolved block directory, schedule, session
   lock, backend configuration, and dependencies.
4. Derive campaign status and reports after the block exits.

It does not pre-admit future blocks. Qwen admission happens only after the
Gemma block completes, so GPU state, endpoint identity, latency, and game
health are current when Qwen begins.

Admission failures are written append-only under `admissions/` and summarized
in `campaign-journal.jsonl`. They never create `session.json` or a trial. A
remediated admission is a new numbered attempt. Unknown failures stop for
classification rather than falling through a broad retry.

## Live admission pipeline

Admission runs in this order for every model block.

### 1. Clean checkout

- WSL and the native Windows checkout must be clean.
- Both must report a non-empty commit.
- Both commits must match the campaign commit.
- Missing commit evidence is a failure, not a successful equality of nulls.

### 2. Native boot health

- The native Windows bridge must be reachable.
- `Profile.csv` must exist and produce fresh frame evidence after an explicit
  baseline offset.
- A missing profile is `profile_missing`; it never becomes offset zero.
- Rotation, truncation, malformed evidence, or timeout fails admission.
- FireTuner availability is not a substitute because it does not prove native
  render/frame-loop health.

### 3. FireTuner ownership

Before the runner opens a connection, admission identifies existing clients
attached to port 4318 and records PID, command, start time, and connection
details.

- Unknown holders always block.
- Known repo-owned `civ-mcp`, `civ-arena`, or prior benchmark clients block by
  default.
- A run-scoped option may terminate one explicitly named PID only after
  revalidating its PID, executable, command, and repository identity.
- No broad process sweep or persistent automatic-kill permission exists.
- After termination, admission repeats holder detection and a commandability
  probe.

### 4. Save deployment and game state

- Deploy the archived save through the native Windows bridge.
- Verify source archive hash, deployed-copy hash, and manifest hash are equal.
- Fresh deployment keeps the benchmark save at the top of the Windows save
  list for cold-start fallback.
- Reload through the production `reload_position` path.
- Dismiss blocking popups through the shared context-native helper.
- Verify the world is commandable.
- Capture canonical queried state and require the frozen digest.

Wrong-save or drift evidence aborts before a model sees an observation. A
verified reload followed by a mismatch aborts the campaign. The existing
unverified-reload retry classification remains unchanged.

### 5. GPU and endpoint isolation

- Record GPU process snapshots before model warmup.
- Resolve the exact registry endpoint, host, GPU indexes, mode, artifact,
  quantization, context size, and reported identity.
- Conflicting workloads block by default.
- Draining requires a run-scoped acknowledgment naming exactly the managed
  services permitted to stop.
- Unknown or unmanaged processes are never killed automatically.
- Allocation is fixed for the block; host or topology changes require a new
  admission and session lock.

### 6. Model configuration and identity

Gemma and Qwen each declare their full configuration before admission:

- model and endpoint;
- temperature, `top_p`, the twelve-seed schedule, and `max_tokens`;
- context size and result cap;
- `chat_template_kwargs`, including the declared thinking behavior;
- backend retry policy; and
- maximum tool rounds.

The configuration may differ between models because calibration does not rank
their quality. It must be identical between arms within one model block. No
configuration may be tuned from counted outcomes.

At execution time, every request uses its `TrialSpec.seed`; no static sampling
default may replace the scheduled value. The block lock records the common
sampling fields and full seed schedule, while each trial records the exact
seed sent on that request.

The identity probe must return the requested model. Identity alone is not
admission.

### 7. Structured tool-call capability

Admission runs one canary against each full resolved arm schema using the
block's exact system prompt shape, sampling, token limit, context, and chat
template. The canary explicitly requests the shared no-argument
`finish_trial` control call.

Admission requires a structured OpenAI `tool_calls` response containing the
correct function and arguments. Prose, fenced pseudo-calls, missing calls,
wrong functions, exhausted thinking output, or identity drift fail the block.
The canary is non-counting and does not mutate the game.

This gate prevents identity-correct models that silently ignore tool schemas
from becoming scientific nulls.

### 8. Seed and latency probes

- Use the suite's exact sampling, prompt shape, and full tool schema.
- At nonzero temperature, repeated identical seeds must reproduce and at
  least some differing seeds must diverge to claim seed honoring.
- At greedy sampling, record `not_applicable_greedy` and require repeated
  consistency.
- Otherwise seeds degrade to repetition/order labels; no paired-seed claim is
  made.
- Record at least ten representative warm round trips and their p95.
- Lock the block wall as
  `max(300, ceil(max_steps * p95_roundtrip_s * 1.5))`.

### 9. Treatment can fire

Admission proves:

- the resolved tool surfaces differ exactly as declared;
- `finish_trial` is in both arms;
- `end_turn` is in neither arm;
- minimal observation tools can discover every task;
- minimal movement tools can legally reach rubric levels 1–2;
- standard tools can legally reach each task's maximum state; and
- the exact model-visible schema for each arm matches its locked digest.

Only after every gate passes does admission mint `session.json` and
immediately invoke the block runner.

## Tool identity

Plan 2 replaces the archived “tool names only” backlog item with two explicit
identities.

### Tool-surface fingerprint

The surface fingerprint hashes canonical tool names and capability IDs. It is
used to prove the declared minimal/standard treatment and to explain whether a
callable capability changed.

### Tool-input fingerprint

The input fingerprint hashes the complete canonical schemas sent to the
model, including names, descriptions, argument types, required fields, and
benchmark control schema. Tool schemas are model input; changing them can
change behavior even when names remain constant.

Any tool-input change requires a new counted session. The clean commit already
captures code provenance, but the explicit digest makes the actual model input
auditable without reconstructing it from source.

## Position authoring

### Construction policy

`BUILDER_ECONOMY_CAL_V1` is constructed live from a stable organic
single-player base save. FireTuner or ordinary in-game commands may make only
the minimum mutations required to create the three tasks. A general generator
is not built.

The provenance journal records:

- base save name and hash;
- every authoring mutation;
- game build version;
- ruleset and enabled DLC;
- enabled mods and versions;
- player/civilization and turn;
- Windows save destination; and
- final archived file hash.

### Position shape

The position contains three independent builders and targets:

1. An owned pillaged resource improvement requiring repair.
2. An owned unimproved luxury or strategic resource requiring its legal
   improvement.
3. An owned tile whose feature must be removed to enable its declared future
   improvement.

Each full-movement builder starts adjacent to its target on a flat or roaded
approach. Minimal can discover the task and position the builder. Standard can
move and perform the intended tile action in the same decision episode.

The position excludes visible military danger, civilian danger, pending
diplomacy, empty production/research choices, promotions, and competing
builder priorities. All targets are owned and workable, builders have enough
charges, and every expected action is verified through the same MCP tools the
agent receives.

`end_turn` is absent from both tool tiers and declared forbidden in the
position/campaign manifest. The calibration scores decisions within one fixed
turn; AI and barbarian processing can never enter a trial.

### Freeze sequence

The state captured during live mutation is diagnostic only. The canonical
state is captured from the artifact after a real archive/deploy/reload round
trip:

1. Mutate the stable base state minimally.
2. Verify legality and save the game.
3. Archive the `.Civ6Save` and record its SHA-256.
4. Deploy the archive through the native bridge.
5. Reload it through the runner's production `reload_position` function.
6. Capture `expected_state` through production `capture_canonical_state`.
7. Freeze the resulting digest in the manifest.
8. Run eleven more deploy/reload/checksum checks against that digest.

The capture/verify CLI is a thin wrapper over those production functions. It
must not implement a sibling reload loop or state query.

Fresh deployment must make the exact benchmark save reachable by the existing
menu fallback. If that live check fails, Plan 2 may deepen exact-save menu
navigation; otherwise generalized scrolling remains deferred.

## Rubric and predicate contract

### Rubric shape

Each builder task has a maximum value of four:

- 0 — no verified progress or harmful play;
- 1 — the relevant builder and target were actually observed;
- 2 — the correct builder survives and reaches the target tile; and
- 4 — the intended tile-state mutation is verified.

Level 3 is included only if the authored state contains a genuine,
independently observable intermediate outcome. The author must not add a level
merely to make the scale contiguous.

The initial rubric therefore has three equal-weight tasks and a maximum of
twelve unless live authoring proves a necessary, preregistered refinement.
Whatever final rubric is selected, the effect threshold is calculated from
its frozen values rather than assumed in advance.

### Safe unit predicates

Runtime model behavior may delete or consume a unit. That is a scoreable bad
or alternative outcome, not a scorer crash. Predicate version 1 therefore
distinguishes:

- an entity missing from canonical initial state: manifest/predicate error;
- an expected-persistent entity missing from final state: predicate false;
  and
- a position-declared consumed entity: score its resulting state, not its
  distance.

The contract provides a safe entity-existence/position form for final-state
checks. Raw `unit_distance_decreased` is allowed only for manifest-declared
persistent units and is rejected by authoring validation for consumable
subjects.

### Objective-blind prompt

The prompt is generic across arms and contains no position name, coordinates,
unit IDs, builder hints, resource names, objectives, or rubric language. It
asks the model to assess the current turn, issue the best available orders,
and call `finish_trial` when done.

The prompt and rubric are authored and frozen before either tested model's
trial transcript from this position is viewed. A later semantic edit creates
a new campaign version and invalidates counted evidence under the old version.

## Instrument contract versioning

Plan 2 publishes separate contract versions:

- `evidence_schema_version` — raw trial fields and their meanings;
- `predicate_schema_version` — predicate vocabulary and evaluation semantics;
- `report_schema_version` — report fields, grouping, normalization, and
  verdict meanings; and
- `scorer_fingerprint` — exact scorer implementation used for one report.

Plan 3 position manifests pin the predicate version. Campaign locks pin all
three schema versions plus the scorer fingerprint used for their report.

A pure implementation correction that makes the scorer conform to already
frozen semantics changes the scorer fingerprint and may regenerate reports
over unchanged raw evidence. A change to evidence meaning, predicate meaning,
normalization, grouping, or verdict semantics bumps the applicable contract
version and cannot silently rescore an existing campaign under the old label.

The released contract is committed under `benchmarks/contracts/` after
calibration. It records semantic versions, authoring rules, and compatibility
requirements. It does not claim that later scorer implementations have the
same fingerprint.

## Frozen campaign configuration

Before counted work, the campaign freezes:

- Gemma4 first, Qwen3.6 second;
- twelve preregistered seeds per model;
- minimal versus standard arms;
- ABBA order within each block;
- fixed fresh conversation per trial;
- one fixed model configuration per block;
- identical configuration between arms within a block;
- objective-blind prompt;
- no briefing, playbook, tracker, memory, channels, or attention treatment;
- no `end_turn`;
- maximum steps, result cap, and latency-derived wall;
- six audit indices per model, balanced three minimal/three standard and
  spread across early, middle, and late schedule segments; and
- the verdict rules below.

The two model blocks total 48 counted trials:

```text
Gemma4:  12 pairs * 2 arms = 24 trials
Qwen3.6: 12 pairs * 2 arms = 24 trials
```

Trials remain strictly serial because port 4318 is single-client.

## Non-counting validation before evidence

After the freeze and before the first counted trial, run an explicit
non-counting validation mode. It executes the same live evidence collectors
and pure gates, then immediately runs its validation episodes, but it is
structurally unable to call the counted block-lock writer or stamp a campaign
fingerprint. Its evidence expires with that invocation and cannot authorize
the later counted block.

The validation performs:

1. The full live gate pipeline in non-counting validation mode.
2. One complete non-counting episode per arm.
3. Deterministic report regeneration from that validation evidence.
4. Structural proof that the smoke/validation artifacts carry no campaign
   fingerprint and are rejected by counted reporting.

If this validation exposes a defect, fix it before counting. A semantic
instrument change bumps the affected version and creates a fresh campaign
lock. Validation transcripts may be viewed because the rubric and prompt are
already frozen; they can test implementation but cannot be used to tune the
rubric.

## Counted trial semantics

For every scheduled trial:

1. Reload the named save through the production path.
2. Continue, reconnect, and dismiss blocking popups.
3. Capture and verify the canonical starting state.
4. Construct a fresh backend/agent conversation with no prior memory.
5. Apply the locked arm tool schema and block configuration.
6. Run the reviewed single-turn decision episode.
7. Capture final queried state without calling `end_turn`.
8. Atomically commit evidence stamped with both fingerprints.

Infrastructure retry and model-outcome admission remain exactly as defined by
the existing runner. Step-limit exhaustion, zero useful actions, incoherent
orders, domain rejections, loops, malformed model output, and a timeout with a
healthy identity-correct endpoint are scoreable outcomes. Exogenous reload,
connection, gateway-health, or harness failures are infrastructure attempts.

## Calibration metrics and verdict

### Metric-fidelity gate

Every model block has six frozen audit indices: three per arm across the
block's time order. Human review must exactly agree with automatic:

- rubric task scores;
- useful actions;
- domain rejections; and
- repetitions/loop excess.

Any disagreement is an instrument failure. Evidence affected by a semantic
metric fix cannot be retained under the same contract version.

### Per-model sensitivity and separation

A pair is decided when standard and minimal have different normalized rubric
scores.

1. **Sensitivity precondition:** at least ten of twelve pairs must be decided.
   Fewer produces `RUBRIC_NONDISCRIMINATIVE`, not a pass or a model null.
2. **Direction gate:** standard must win at least ten of the original twelve
   pairs. Ties count as non-wins for this gate.
3. **Effect gate:** the median signed paired normalized delta must be at least
   one complete task's maximum value divided by the frozen rubric maximum.

With a three-task 0/1/2/4 rubric, the effect threshold is `4 / 12`; the value
is written numerically into the frozen campaign only after the rubric freezes.

A sufficiently decided block that misses either separation gate is a
legitimate model-specific null. A block that never passed identity,
tool-calling, inference-configuration, or other admission cannot produce a
null.

### Campaign outcomes

- `CALIBRATED`: at least one admitted model passes, all completed blocks pass
  metric fidelity, and no block is non-discriminative.
- `CALIBRATED_REPLICATION_DEFERRED`: Gemma passes and Qwen admission cannot be
  completed after documented remediation. Deferral requires at least one
  journaled retry after a concrete remediation, or two confirming attempts
  for a demonstrated non-remediable capability failure. Unknown failures
  cannot be converted into a deferral. Qwen remains an admission failure, not
  a null.
- `BLOCKED`: neither admitted model passes; or Gemma does not pass and Qwen
  cannot be admitted; or Gemma cannot be admitted/completed; or metric
  fidelity fails without a completed corrected rerun.
- `RUBRIC_NONDISCRIMINATIVE`: any completed model block has fewer than ten
  decided pairs. Rubric review is required. A rubric edit creates a new
  campaign version and all counted blocks rerun.

Qwen replication is mandatory to attempt but not mandatory to admit after a
passing Gemma block. If Gemma does not pass, Qwen admission and execution are
required before any instrument verdict is possible. Gemma is the mandatory
primary block: a Qwen-only pass does not complete Plan 2 when Gemma never
produced a complete audited block.

## Reporting

The campaign report is a disposable projection over locked evidence.

- Group every score by model, arm, and position; never pool model or arm
  trials into one median.
- Report all twelve paired deltas, decisions, ties, wins, sensitivity, median
  delta, and threshold result per model.
- Report useful actions, domain rejections, invalid calls, repetitions,
  successful mutations, steps, latency, tokens, cost, and prior
  infrastructure pressure.
- Keep quality separate from speed and cost.
- Surface topology and inference configuration.
- Surface admission deferrals and operational attempts without scoring them.
- Include every contract and evidence fingerprint.
- Refuse incomplete official schedules except the explicitly defined Qwen
  admission-deferral outcome.

Report generation reads no infrastructure attempt as treatment evidence. A
scorer crash or implementation-only fix regenerates reports without replaying
trials.

## Gate-critical hardening included in Plan 2

### Manifest null validation

Strict YAML loading must reject null or wrong-typed required fields with a
field-specific manifest error. Bare downstream `TypeError`s are not an
admission contract.

### Boot baseline

`baseline_offset` is required evidence. Bridge parsing must preserve missing
as missing rather than defaulting it to zero, and the lock must reject it.

### Frontend Escape disarming

The frontend continuation helper may send Escape only while the native
Windows path positively recognizes a continue/leader screen. A recognized
in-world HUD or an open tuner port permanently disarms Escape for that load.
An unknown screen waits; it does not blindly toggle Escape. This prevents the
observed pause-menu toggle after the world was already ready.

### Save-menu reachability

Fresh native deployment is the first-line fix for old saves buried below
autosaves. The live admission test must prove exact selection of the deployed
name. Generalized scrolling is added only if that proof fails.

## Error handling and resume

- A campaign lock is immutable after creation.
- A session lock is minted only after successful fresh admission.
- Admission attempts are append-only and never become scoreable trials.
- Canonical drift produces no model observation.
- Unknown failures stop for classification.
- Completed trials are immutable and skip only when both fingerprints match.
- Partial infrastructure work remains under `attempts/`.
- A Qwen admission deferral preserves completed Gemma evidence because the
  campaign and block identities are separate.
- A model/topology/configuration change starts a new session lock and cannot
  resume the old block.
- A rubric, prompt, campaign-rule, or semantic contract change starts a new
  campaign.

## Automated verification

Implementation uses TDD and counterfactual proof for guard tests.

### Campaign and lock tests

- Campaign fingerprints change for every outcome-affecting input.
- Session fingerprints change for admission, model, topology, sampling, and
  exact schema changes.
- Tool-surface and tool-input fingerprints react to their intended changes.
- Missing/mismatched campaign or session stamps reject resume and reporting.
- Smoke trials lacking campaign identity cannot enter a campaign report.
- A future block's admission is never accepted from `--admit-only` output.

### Admission tests

- Dirty, mismatched, or commit-less checkouts fail.
- Missing/rotated/truncated/stale `Profile.csv` evidence fails.
- Missing baseline offset cannot become zero.
- Unknown tuner holders block.
- PID-scoped termination revalidates identity and reruns the gate.
- GPU conflicts require exact scoped acknowledgment.
- Wrong model identity fails.
- Prose pseudo-tools fail the structured tool canary.
- Canary tests run against both complete arm schemas and exact sampling.
- Qwen thinking/token misconfiguration fails admission, not calibration.
- Seed verdicts and latency-derived walls are deterministic from evidence.
- Treatment-can-fire proves both minimal reachability and standard completion.

### Authoring and predicate tests

- Capture/verify invokes the production reload and capture functions.
- Canonical state is taken after archive deployment and reload.
- All twelve reloads use the same production path and digest.
- Environment provenance and archive/deployed hashes are required.
- Runtime unit disappearance yields predicate false where declared safe.
- Missing initial entities remain hard predicate errors.
- Distance predicates on consumable units fail authoring validation.
- Positive and counterfactual fixtures prove every rubric predicate can both
  pass and fail.

### Runner preservation tests

- Existing retry classes, timeout discriminator, journal, atomic commit, and
  resume tests remain unchanged and green.
- New integration tests hand a resolved block to the existing runner rather
  than duplicating the trial loop.
- Each committed trial receives both fingerprints at the existing finalization
  boundary.
- Neither tier exposes `end_turn`; both expose `finish_trial`.

### Reporting tests

- Groups remain separate by model, arm, and position.
- Attempts and admission evidence never contribute scores.
- Sensitivity, direction, and normalized complete-task gates match frozen
  fixtures.
- Non-discriminative, null, calibrated, deferred, and blocked outcomes are
  independently tested.
- Reports regenerate byte-identically from the same locks and trials.
- Scorer-only fingerprint changes can regenerate without replay.

### Live verification

Before counting:

1. Full repository suite passes on the clean campaign commit.
2. Windows checkout fast-forwards to the same commit and is clean.
3. Integrated admission passes.
4. The frozen position survives twelve production-path reloads.
5. One non-counting episode per arm completes.
6. Smoke-to-campaign evidence promotion is rejected.
7. The full suite passes again before the first counted trial.

## Implementation boundary and order

The later implementation plan should preserve this order:

1. Contract versions, strict manifest validation, and tool fingerprints.
2. Campaign lock/store wrapper and dual-stamp enforcement.
3. Live evidence collectors and integrated admission.
4. Tuner-holder, boot-health, tool-canary, and frontend continuation fixes.
5. Production-path capture/verify CLI and safe predicate additions.
6. Controlled authoring and twelve-reload freeze of
   `BUILDER_ECONOMY_CAL_V1`.
7. Freeze prompt, rubric, seeds, audits, model configs, and campaign rules.
8. Non-counting live validation.
9. Gemma counted block and audit.
10. Qwen admission, counted replication block, and audit.
11. Campaign report, verdict, and publication of the frozen instrument
    contract.

Non-empty arm options and the nine-position library begin only after this
design's exit criteria are satisfied.
