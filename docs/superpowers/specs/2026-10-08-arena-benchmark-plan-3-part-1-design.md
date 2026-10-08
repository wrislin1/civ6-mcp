# Arena benchmark Plan 3 Part 1 authoring and scoring design

Date: 2026-10-08. Status: approved for planning; written-review amendments incorporated on 2026-10-08.
Implementation has not started. Scope: authoring and scoring foundations plus three development positions;
Parts 2 and 3 appear only as changes to the existing roadmap.

## 1. Decision and scope

Plan 2 established that the instrument detects a known tool-capability difference under favourable
conditions. It did not establish that navigation, prioritisation, beneficial alternatives, or harmful
play are scored adequately. Part 1 addresses those measurement questions before further model screening.

Build a repeatable authoring toolkit and validate three development positions with scripted trajectories
through the existing benchmark runner. Extend the scoring contract to distinguish progress, loss, and
unscored economic changes. Restore the parent design's fifteen model round trips. Do not run tested models
on library positions during Part 1.

The governing documents are the [parent benchmark design](2026-08-30-arena-controlled-position-benchmark-design.md),
the [calibration design](2026-08-31-arena-benchmark-calibration-campaign-design.md), and the
[Plan 3 handoff](../plans/2026-10-08-plan-3-brainstorm-handoff.md). The parent design's Stage 2 screen,
advancement rules, independent Stage 3 experiments, and Stage 4 confirmation rules remain authoritative
except for the explicit deltas below. The [released v1 contract](../../../benchmarks/contracts/instrument-v1.md)
and its historical campaigns remain immutable.

Part 1 delivers:

- A committed toolkit and recipes for builder economy, city planning, and tactical defense.
- A scripted validation backend using `SingleTurnAgent` and `BenchmarkRunner`.
- A versioned successor evidence, predicate, and report contract with signed primary scores.
- Three authored development archives, rubrics, provenance records, scripted checks, and reproducible reports.
- A benefit ledger and a standard report section classifying uncredited mutations.
- A preregistered budget rule and an exit-gate report, including live authoring time.

It does not implement briefing/playbook treatments, run the model screen, author the other six positions,
advance game turns, or implement the decision-model experiment. Task-tracker evaluation remains a
multi-turn experiment. The decision-model experiment needs a separate design and a same-menu LLM control;
it is not a Stage 3 injection arm.

## 2. Explicit changes from the existing instrument

| Area | Part 1 decision |
|---|---|
| Observation credit | Removed for Plan 3. The positive control awards one point per task for `get_units`, or 3/12 without a mutation. Plan 3 observation alone earns zero. |
| Primary score | Signed progress less harm, divided by maximum positive objective credit. Gross credit and deductions remain separately visible. |
| Partial progress | Observable intermediate endpoint states only. Distance reduction, tool calls, and intent are not primary progress. |
| Harm | Frozen loss conditions and newly created civilian exposure, objective-anchored weights, compensation rules, and deduplication. |
| Economic alternatives | A measured ledger, without a combined utility score or advancement role. |
| Budget | Fifteen model round trips, restoring the parent design rather than carrying forward Plan 2's eight. |
| Validation actor | Scripted, explicitly non-counting episodes through the benchmark loop. |
| Tool surface | Freeze the 35-tool Part 1 list in section 12 and in its implementation plan. Part 2 may extend it under a new identity. |

**No Plan 3 primary score or primary-score improvement is comparable to a Plan 2 primary score or
improvement.** Observation rungs, loss semantics, evidence coverage, budgets, and eventually the tool
surface differ. Retain Plan 2 reports under their original contracts. Historical action counts may be
reproduced under their original definitions, but they do not establish cross-contract score comparability.

The successor contract sets evidence, predicate, and report schema versions to `2.0.0`. Its scorer fingerprint must
cover every implementation dependency that determines scores or derived classifications, including any
new modules. Existing version-1 loaders and frozen Plan 2 restrictions must not be loosened to admit
version-2 work. Dispatch by contract version, and retain reproducibility of historical reports. Exact
historical report reproduction uses its pinned code revision and fingerprint; a current-code compatibility
path must preserve version-1 interpretation without claiming that changed source has the old fingerprint.

## 3. Component boundaries

| Component | Responsibility and interface |
|---|---|
| Authoring toolkit | Read a committed recipe, load its identified base, apply declared setup mutations, probe actions through the proposed benchmark tool surface, and produce archive/provenance inputs for existing capture and verification commands. |
| Scripted backend | Implement the existing backend `chat(messages, tools) -> Reply` interface with a finite sequence of declared response batches. It supplies actions, never expected scores. |
| Existing agent and runner | Dispatch tools, enforce episode limits, capture before/after state, reload positions, and persist immutable trial evidence. |
| Snapshot extension | Add the facts required by the new predicates and ledger to existing captures. It does not add another capture loop. |
| Version-2 scorer | Evaluate endpoint progress and recorded losses, deduplicate deductions, and return gross credit, harm, net credit, and signed normalisation. |
| Benefit and audit reporter | Derive measured deltas and uncredited-mutation classifications from raw evidence, with explicit coverage. It cannot affect primary scoring or advancement. |

The authoring helper belongs in the arena benchmark package, with small supporting CLI commands. Recipes,
scripts, expected results, and provenance are versioned under `benchmarks/`. These responsibilities should
remain separate from the already large runner and report modules; exact file organisation belongs in the
implementation plan.

Use the existing `RunnerDependencies.make_agent` seam to inject the scripted backend into `SingleTurnAgent`.
The live factory currently constructs only LLM backends; Part 1 adds a validation entry point. A separate
scripted agent would duplicate evidence and dispatch mechanics. The arena's `ScriptedPolicy` includes
coordinator repair and channel behaviour and is not the benchmark validation actor.

## 4. Authoring and the library freeze

Each recipe identifies its base archive and digest, intended scenario family, player, setup operations,
assertions, legality probes, output archive, and authoring record. Begin from the identified base on every
reconstruction; never apply the recipe on top of an unknown partially mutated world. Resolve and record
actual entity IDs from the resulting world rather than assuming setup preserved old IDs.

Raw setup Lua is confined to authoring. Every scored objective, intermediate endpoint, harm, and accepted
compensation must be reachable and verifiable through the proposed benchmark tools. A setup API saying
that an action is legal is insufficient: execute the action through those tools on a disposable reload,
read the result, and restore the authored starting state. Do not depend on the unresolved camp-on-ivory
defect. A camp-dependent variant requires a separate successful legality probe after that defect is resolved.

Reuse `benchmark_position.capture_position` and `verify_position`, including their deployment,
reload-confirmation, popup hygiene, and twelve matching checksum cycles. Also verify the crash-recovery
menu path against the same archive. Failures retain their evidence and stop admission; they do not become
zero-scoring trajectories.

The three development positions may be refined from scripted mechanics evidence before their final
versions freeze. Archive versions are immutable. The final position packet contains the archive, recipe,
rubric, harm definitions, observation/discoverability evidence, scripts, separate expected results,
contract identity, reports, and provenance digests.

All nine development and held-out rubrics must be authored together and frozen before Stage 2 and before
viewing any tested-model transcript from those positions, as required by the parent design. Completing
Part 1's three positions does not grant permission to pilot them with Gemma, Qwen, or another roster model.
Scripted validation is the only live episode actor on library positions in Part 1.

A future development-position LLM pilot would require an explicit amendment to that rule. Any such
exposure must mark the position and its derived versions `pilot-informed`, retain the transcripts, and
create a new version before further use. Reversioning does not restore blindness. Held-out positions have
no pilot exception. Keep scenario-family provenance so seed or save-version changes cannot conceal exposure.

## 5. Scripted validation episodes

A trajectory is an ordered list of backend response batches containing ordinary tool names and arguments,
ending with `finish_trial`. Batching is explicit. The backend receives the same schemas as the eventual
model actor, and all calls pass through the same allowed-tool check, dispatch, result caps, state captures,
and runner persistence. It has no direct game connection or rubric access. An invalid script or rejected
required operation fails validation rather than being repaired by hidden scripted policy.

Expected endpoints, primary scores, harm IDs, and ledger assertions are stored in a separate validation
case. The scorer derives actual results from raw evidence and then compares them with those expectations.
Knowing a rubric is legitimate for the author of a mechanics test; injecting it into a model or a future
candidate generator is not.

Every script uses a fresh archive reload. Record `actor_kind: scripted`, script identity and digest,
validation-case digest, position/contract/tool identities, ordered schedule, and `counting: false` in the
version-2 lock and evidence. Scripted records must not impersonate a model endpoint or claim seed control,
tokens, model cost, or model latency. Model-only fields are explicitly not applicable. Keep actual episode
wall time and operation timing for operational validation.

The validation entry point requires no model endpoint admission. It retains game ownership, deployment,
reload, checksum, and tool-identity requirements. Version-2 model reports reject scripted records as inputs
to comparisons; a directory name or a human remembering that a run was a test is not sufficient separation.

Turn and active-player identity must remain unchanged throughout each script. `end_turn` is unavailable.
Unexpected turn advancement, incomplete capture, or identity drift is an instrument failure and blocks
position validation. Existing per-step snapshots are sufficient to perform these checks.

## 6. Evidence content and observation boundaries

`SingleTurnAgent` already records state before and after each dispatched tool, plus both digests.
Preserve that timing. Extend the state query, parser, normalisation, and schema together to include the
following scoring-relevant facts where required by a position:

- Own units: stable identity and owner, type, civilian/combat classification, location, health, remaining movement, and charges.
- Tactical targets: identity, hostility, combat classification, visibility, health, and verifiable destruction. Losing visibility is not
  destruction and cannot earn kill credit. Fixtures must support unambiguous outcome verification.
- Cities: housing state, buildings and repair state, district placement/construction, and the active
  production item, including whether it is a repair.
- Tiles: ownership, terrain/features, resource/improvement state, visibility, and all six tile yields.
- Player resources: gold and faith balances and resource access facts that can be read reliably. Do not
  substitute resource-stock growth for a same-turn resource-connection observation.

Canonical ordering and numeric precision are part of the contract. An unavailable optional ledger field
has an explicit coverage status. Missing evidence required by a primary predicate is an admission or
reporting error, never a false predicate or fabricated zero. Normal absence of a destroyed or consumed
entity is different from a truncated response.

Every version-2 capture has a **2.0-second hard wall limit**, covering its query, parsing, normalisation,
and digest. A timeout or overrun is an infrastructure failure, never a scoreable model timeout. The same
classification applies if the episode wall expires while a capture is in flight. Discard an incomplete
capture and require the existing reload/reconnect path before another attempt.

Measure each capture with a monotonic clock. Keep timing outside canonical state and its digest; persist
phase, duration, and completion status in trial/attempt diagnostics. The null script must exercise the
full position scope and report capture count, mean, p95, maximum, and total duration. Every capture must
meet the limit before admission. Model reports expose total capture time and the in-episode capture
share of episode wall time. Only tool-before/tool-after captures enter that share; the runner's initial
and final captures are outside `EpisodeEvidence.wall_clock_s`. The existing episode wall still includes
capture work, but that cost must not be hidden in model latency. Optimise the query or prospectively
amend the budget if it fails; do not remove required evidence to pass the timing gate.

Capture enough tile content before actions to measure alternatives outside the declared objective tiles.
For these three positions, include player-owned tiles and a frozen, player-discoverable area covering
reachable actions and relevant adjacency effects. Store that coverage in the position packet. Out-of-area
effects remain explicitly uncovered; do not infer their before values after the fact. Before/after
captures may therefore contain unchanged tiles, while the ledger reports only changed tiles.

The scoring snapshot is private evidence, not a model observation. Models, treatments, and a future
candidate generator obtain their view from player-visible tools. They must not receive manifest targets,
rubric IDs, expected scores, script expectations, or the manifest-selected snapshot as a decision menu.

For each position, an observation-only script must demonstrate that all candidate-relevant facts are
discoverable through the proposed tool surface. Save its ordinary tool results as the discoverability
record. `get_builder_tasks` priorities are heuristics, not scoring authority. The Part 1 deliverable proves
information availability; implementing candidate generation belongs to the separate decision-model design.

## 7. Primary score and loss semantics

For objective credits `p_i`, admitted harm deductions `h_j`, and maximum positive credit `M`:

```text
gross_credit = sum(p_i)
harm_total = sum(h_j after compensation and deduplication)
net_credit = gross_credit - harm_total
primary_score = net_credit / M
```

There is no floor and no shift into `[0, 1]`. Each of the three positions has three four-point objectives,
so `M = 12`. Optional intermediate rungs are worth two points. The declared maximum sum of deductions
cannot exceed `M`, yielding a primary range within `[-1, 1]` without clipping.

Validate finite numeric values, a positive denominator, nonnegative objective credits, positive deductions,
and a finite declared loss scope before running an episode. Reject unknown predicate kinds, ambiguous loss
precedence, or an over-budget penalty definition before evaluating any individual predicate.

### Progress

An objective receives its highest satisfied endpoint rung, not the sum of successful actions. Every
positive rung is false in the frozen initial state. Observation, a shorter distance, an issued command,
or a success-shaped tool response alone earns nothing. A builder on an eligible work tile with charges
left is an observable intermediate state; a builder that merely moved closer is not.

Completion predicates describe outcomes and allow all preregistered acceptable alternatives. They do not
require a particular worker, route, feature-removal sequence, or order unless that distinction is part of
the outcome itself. A completed improvement takes precedence over the builder's intermediate location;
legitimate final-charge consumption does not erase completion credit.

Undoing a reversible outcome removes its endpoint credit. Repeating an action or leaving and returning
cannot accumulate points. Mutation-level progress attribution uses these same predicates and is reported
separately from endpoint credit; it cannot inflate the primary score.

### Losses and weights

A harm definition declares the asset or resource lost, an observable loss predicate, its associated
progress objective, deduction, acceptable compensation predicates, evaluation timing, and deduplication
key. The default deduction for losing asset X equals the maximum credit of the objective X serves:
four points here. Any deviation requires a preregistered rationale and a recomputed maximum and increment
analysis. It cannot be chosen after model results are seen.

Objectives are progress-based. Harms cover losses and the explicitly declared new-exposure condition
below. Do not pair a four-point "keep the archer alive"
objective with a four-point "archer lost" penalty. No positive rung rewards merely retaining the starting
state, and no separate deduction charges a missed objective. A loss can prevent independent progress,
but the scorer must not encode the same loss once as forfeited survival credit and again as a debit.

Ordinary spending, taking combat damage, consuming a builder's last charge, and making a productive
replacement are not inherently harmful. Compensation is determined by frozen observable outcomes, not
an after-the-fact opinion about the action. Each harm needs a positive case and a closely related
legitimate-action case proving the compensation or exclusion works.

For reversible harms, evaluate the final deficit. For irreversible losses, retain the event from the
existing per-step evidence even if a later action obscures it. Repeated observations of one loss do not
charge it again. Overlapping predicates referring to the same lost asset/event use one declared loss key
and one deduction; precedence must be explicit if severities differ. Distinct losses may add only within
the finite, preregistered penalty budget.

Reports expose each objective's credit, each fired or compensated harm with supporting evidence, gross
credit, total harm, net credit, denominator, and signed score. A harmful action by itself can score below
the zero earned by inaction.

### Shared civilian exposure and cover geometry

For a living civilian, `covered` means it occupies an owned city tile or has an owned military unit on
its tile or an adjacent hex. `exposed` means it is outside an owned city, is adjacent to a currently
visible hostile combat unit, and is not covered. Hex adjacency uses the existing offset-grid distance,
not Cartesian distance. Missing visibility, hostility, or unit-role evidence is an error, not safety.

A declared civilian incurs a four-point **new exposure** deduction only when it was not exposed initially
and is exposed finally. Link the weight to the objective that civilian serves. This is an explicit
end-state safety proxy, not a claim that a capture has occurred or will occur. Temporary exposure followed
by recovery has no exposure debit. A consumed or lost unit cannot also incur an exposure debit.

An initially exposed civilian left in place incurs no new-exposure deduction and earns no rescue credit.
This preserves null-script zero. The tactical rescue objective uses the same `covered` geometry, with
accepted endpoints that are uncovered initially and covered finally. Do not implement a second safety
geometry for the builder position or turn exposure into a survival reward.

## 8. Benefit ledger and uncredited mutations

The ledger is a vector of observations, not a utility score. Record before/after values, units, affected
entities, source steps, and coverage for:

- Tile yields on changed tiles, including observable adjacency changes.
- Resource access gained or lost; stock or flow quantities only when actually measured.
- Gold and faith balance changes.
- Builder charges consumed, including legitimate consumption of the last charge.

Use tile yields rather than city totals: citizen reassignment can change city totals without the action
changing a tile's productivity. Tile yield is potential output, not proof the tile is worked. Keep one-off
chop/harvest receipts separate when observable; a missing measurement is not zero compensation. Ledger
coverage cannot stand in for evidence required by a harm's compensation rule.

Show individual step deltas for audit and net initial-to-final changes for the episode. Do not present the
sum of positive changes as net benefit: an improve/undo cycle must not manufacture benefit. No ledger
field changes primary scores, qualifies a model, cancels harm, or participates in advancement.
The same raw measurement may support a separately frozen primary or compensation predicate; the ledger's
derived classification is never itself a scoring input.

Every report includes successful mutations that received no objective-progress attribution, their raw
tool/entity references, and their classification. Separate measured economic changes, builder positioning,
non-builder movement, declared harm, **undeclared loss**, and insufficient evidence. Undeclared loss means
an observed asset loss outside the declared scoring scope, such as a removed improvement or a lost scout.
It has no automatic primary deduction and does not assert that the loss was strategically unjustified.
Verified consumption or transformation, such as a builder's successful final charge or a unit upgrade,
must not be mistaken for destruction. Unknown lifecycle evidence remains explicitly unresolved.

The loss-coverage audit examines all recorded changes, including credited actions and actions returning
an error. Thus an undeclared loss cannot disappear merely because the same action earned progress or had
a rejection-shaped result. Link these loss records back to the uncredited-mutation section where applicable.
A distance-based positioning category is descriptive;
it never earns primary credit or claims a move was strategically useful or wasted. Also report completed
outcomes receiving less than completion credit as a distinct under-credit audit, not as uncredited actions.

The [historical audit](../../research/arena-benchmark-builder-calibration-uncredited-actions-audit.md)
becomes an **offline, data-driven regression fixture** under its original scoring and classification definitions. Across its 96
trials, reproduce the membership of 117 uncredited mutations and these mutually exclusive action buckets:

| Historical bucket | Count |
|---|---:|
| Farm on the builder's own tile | 28 |
| Builder closer to another task tile | 13 |
| Builder distance unchanged or farther from the task set | 45 |
| Non-builder movement | 31 |

Preserve the 45 split into 36 unchanged and 9 farther, the 64 affected trials, and the separate two
under-credited quarry completions. The historical task set comes from the archived player-facing
`get_builder_tasks` evidence, not the rubric's target list. The audit's harmful count is zero. Pin fixture
inputs and the historical evaluator identity so the new contract does not silently redefine membership.
The audit identifies `bf0f0b5` as its corrected historical scorer; preserve that interpretation when
constructing the fixture rather than applying the new observation and endpoint rules to old trials.
Store expected trial/step membership and classifications in
`tests/arena/fixtures/builder_uncredited_audit_v1.json`, with digests of raw inputs and public task-list
evidence. Compute actual membership from the raw trials and their frozen version-1 objective definitions;
do not select input actions from the expected-membership list. Feed the resulting records through the same
classifier used for new reports. There is no separate historical-mode classifier or live regression run.
This acceptance test must pass offline before any authoring clock starts.

The original evidence did not record tile yields. The audit's later city-yield probes are supplementary
historical measurements, explicitly labelled as such. They can support the historical interpretation of
the 28 farms, but cannot be relabelled as measured tile deltas in those trials. Automate what the archived
evidence establishes, attach the named supplement where needed, and mark historical tile yields unavailable.

## 9. Development position contracts

All three are new development position versions, not reused calibration archives. Their manifests bind
exact entities, endpoints, alternatives, and penalties after scripted legality probes. Those concrete game
bindings are outputs of authoring, not unanswered design choices. Each full-score witness must jointly
achieve all three objectives within fifteen rounds; individually feasible but mutually exclusive maxima
are not admitted. Each position also needs a materially different acceptable full-score trajectory.

### Builder economy

Author navigation, scarce usable charges, competing work, and a visible route threat. Builders begin away
from their scored work tiles. Tasks must be discoverable through ordinary unit, map, and builder-task
queries. Accepted work sites may include multiple economically equivalent alternatives. The position
must leave enough movement and charges for its joint full-score witness; scarcity makes allocation matter
without making the maximum fictional.

| Objective | Two points | Four points |
|---|---|---|
| Restore pillaged production | A charged builder occupies an eligible repair tile whose improvement remains pillaged. | An eligible productive improvement is intact again. |
| Connect a missing resource | A charged builder occupies an eligible resource work tile. | A player-owned, legally connected resource improvement is intact; no future stock accumulation is assumed. |
| Improve a food tile | A charged builder occupies an eligible food-improvement tile. | An eligible improvement is intact and the declared tile-food outcome is verified. |

Partial endpoints must be useful and legally actionable under the known tech and ownership conditions;
remaining movement may be exhausted. They must not already hold at entry. Declare alternatives before
freezing rather than adding them after observing a model's preferred farm.

Declare two four-point harms: uncompensated loss of an escort serving the resource objective, and newly
exposing a civilian serving that objective under the shared geometry. Their maximum combined deduction is
eight points. Probe the lethal attack and corresponding safe or justified action, and the exposed versus
covered civilian endpoints. The route decision is therefore scored even though the enemy takes no turn.
Do not label a small unscored farm gain harmful merely because it spent a charge that could have served
a scored task; record its measured effects in the ledger.

### City planning

Use separate cities or compatible production commitments so the objectives can coexist. Provide an
observable housing shortfall, a district decision with multiple acceptable sites, and a distinct urgent
building repair. The available treasury and legal options must support the joint witness.

| Objective | Two points | Four points |
|---|---|---|
| Address the housing shortfall | An eligible housing remedy is verified as the active production item. | A completed remedy is present and the specified housing shortfall is resolved this episode. |
| Commit a suitable district placement | No intermediate rung. | An accepted district location is committed and its corresponding construction is active. |
| Initiate the urgent building repair | No intermediate rung. | The correct repair is verified as active production. |

The latter two objectives explicitly score current commitments, not future completed construction.
Overwriting their queues removes the commitment credit. District criteria must include the value of
displaced assets; adjacency alone is not a universal definition of a suitable site.

The initial harm is destruction of a productive asset through a placement outside the accepted replacement
conditions, anchored at four points to the district objective. An accepted productive replacement must
not fire it. Irreversible displacement remains observable in step evidence even if the queue is changed
later. A poor queue choice without an actual declared loss is simply uncredited, not a harm by definition.

### Tactical defense

Use enemies already at war with the player so declaration-turn combat synchronisation cannot determine
the result. Make threats visible and outcomes verifiable without interturn AI activity. Supply enough
legal options for both full-score trajectories and an actual uncompensated military-loss case.

| Objective | Two points | Four points |
|---|---|---|
| Neutralise a visible attacker | Verified damage meets a frozen tactically meaningful health threshold; the target remains alive. | The attacker is verifiably neutralised under the declared outcome rule. |
| Relocate an exposed civilian into defended cover | No intermediate rung. | The civilian reaches an accepted defended endpoint that did not hold initially. |
| Provide a reinforcement | An eligible reinforcement is verified as active production. | An eligible reinforcement is in play in the defended area this episode. |

Defended endpoints describe present position and cover, not a prediction that an AI will refrain from
attacking next turn. A model's own combat estimate or narrative cannot substitute for measured damage.
Freeze the damage threshold from the authored health/defense situation and scripted readback, not model
performance. If a plausible intermediate state is not useful, omit that rung rather than awarding any
nonzero damage automatically.

The initial harm is an uncompensated military asset loss, normally four points for the objective it serves.
Ordinary damage does not trigger the loss rule. Accepted tactical compensation is specified before
freezing and tested separately from a futile attack.

## 10. Budgets and threshold derivation

Set `max_steps = 15` for every Plan 3 model and arm. One step is one backend/model round trip, including
a final response containing `finish_trial`; multiple tool calls in one response consume one round trip.
It is not a tool-call or game-turn budget. Record both round trips and tool calls, as well as wall time,
tokens, truncation, and terminal reason. Script batches follow the same counting rule.
Reports show **non-finish tool-call attempts per round trip for each model**, both per episode and as a
distribution, alongside dispatched-call counts. Count rounds directly, including finish-only rounds;
never infer them from the number of tool rows. Round-trip budgeting permits batching, so the roughly two
calls per step seen for Qwen versus one for Gemma in Plan 2 is a potential budget advantage to attribute,
not evidence that equal round budgets imply equal tool-call opportunities.

The parent design already specifies fifteen and diagnostic comparisons around that baseline. Plan 2
froze eight for its calibration. The [findings](../../research/arena-benchmark-builder-calibration-v1-findings.md)
document Qwen reaching that cap before acting with the pasture builder in 4/12 v3 standard trials.
This supports restoring the original allowance; it does not establish an optimal budget or justify
position-specific budgets tuned from future model results.

Keep the parent's latency-derived wall limit, locked before each model block:

```text
episode_wall_s = max(300, ceil(15 * p95_roundtrip_s * 1.5))
```

Context, completion-token and result-character limits are common within a comparison and frozen before
model exposure. Record their exact values in the later suite; Part 1 does not tune them using library
model episodes. Scripted validation uses an explicit locked wall limit and is not a substitute for model
latency admission. Preserve the parent's diagnostic triggers for a later budget experiment.

Each position packet includes its score maximum, penalty maximum, attainable rung increments, and a
meaningful-improvement derivation. With the proposed 0/2/4 rungs and four-point losses, a smallest positive
episode-score increment is `2/12 = 1/6`; one complete objective from zero is `4/12 = 1/3`. These are
interpretation scales derived anew from the signed rubric, not the old calibration effect gate.

Report the smallest useful episode increment as the position's meaningful-change threshold. It is a
diagnostic annotation, not an extra advancement gate. Medians of even-sized groups can have smaller
increments than individual episodes. Stage 2 advancement and the existing Stage 3/4 rules continue to use
their prescribed medians, position counts, and margins. In particular, `+0.05` still means five percentage
points of maximum positive objective credit; do not renormalise the signed range to change that meaning.

## 11. Validation and the exit gate

Freeze this gate before implementation and before starting live authoring. Part 1 is complete only when
every condition below is met. Scripted success validates the mechanics and examples, not general model
decision quality or the scientific validity of every weight.

### Required cases for each position

Live witnesses cover each position's declared objectives and harms, including both full-score paths and
its own observation-only null. Shared geometry edge cases and malformed-input, timeout, and actor-separation
checks also have offline fixtures. The builder supplies live new-exposure/covered/recovered witnesses;
the tactical position supplies the initially-exposed null. A city position need not acquire an unrelated
civilian harm just to repeat a shared geometry test.

- Joint full score, and a materially different acceptable full-score trajectory.
- Each intermediate rung, plus a closer-but-not-at-an-eligible-endpoint action receiving no partial credit.
- Every harm firing and a corresponding legitimate-action or accepted-compensation case avoiding it.
- New exposure versus covered endpoints, temporary exposure repaired before finishing, and an initially
  exposed civilian left unchanged with zero primary credit and zero new-exposure harm.
- A pure-observation null script with gross credit zero, harm zero, primary zero, and identical initial and
  final digests. Every recorded before/after pair and adjacent snapshot boundary must agree as well.
- Mixed gain and loss, an uncompensated harm-only negative score, and repeated/undone actions.
- Legitimate final-charge consumption where applicable, with completion retained and no false loss.
- Snapshot incompleteness, wrong identity, malformed predicates, and unsupported tools failing explicitly.
- Full-scope null capture timings within 2.0 seconds each, plus offline timeout/cancellation cases proving
  capture failures cannot become model failures and timing cannot change a state digest.
- Scripted records rejected from model-comparison aggregates.

The null case tests stability of the recorded state under the tested query sequence. It does not prove
that every unrecorded engine field is immutable. Any measured drift blocks validation until explained
and resolved; do not silently remove a score-relevant field to make the digest match.

### Live authoring time

Each of the three positions has a maximum of **three elapsed live hours**. Start the clock at the first
live command used to author that position on its chosen base, including initial live survey or reload.
Stop only after setup, legality probes, archive capture, twelve reload checks, menu-path verification,
scripted cases, and the final restored-state check have passed. Persist start/end times and stage timings
in provenance. All retries, debugging during that attempt, load waits, and interruptions count.

Toolkit implementation, the 117-mutation regression, other offline tests, and offline scenario design
occur before this clock starts. A failed recipe replay or restarted process does not reset it. Exceeding
the bound fails that scenario attempt; retain it and report the failure.

Each family permits **one declared scenario substitution**, for at most two scenario attempts. Before
the substitute's first live command, record a new scenario ID, the failed predecessor, failure reason,
and the material scenario change. It starts a fresh three-hour clock and must satisfy the same family
objectives, tool surface, scoring rules, and complete validation gate. This allowance is preregistered
here; it does not require an ad hoc amendment after the first failure. A restart or mere rename is not
a substitution. Retain all failed artifacts, durations, and evidence, and report the whole family's
cost even when its substitute passes. A second failed scenario blocks the family and Part 1; further
attempts or changed bounds require a prospective amendment.

### Required evidence packet

All three final positions must have matching archive/provenance digests, their verification records,
complete scripted cases, the capability/discoverability record, and byte-identical report regeneration
from the same raw evidence and locked scorer. Every harm must have anchored weights and passing positive
and negative cases. The budget rule and each score/threshold derivation must be preregistered. The benefit
ledger must include undeclared-loss coverage, and the offline historical 117-mutation regression must
already have reproduced its declared membership and counts. The null timing gate must pass. No
tested-model transcript from a library position may have been
produced or viewed during Part 1.

Unit and integration validation must additionally preserve version-1 interpretation, exercise signed
normalisation and deduction deduplication, and prove that expected script scores cannot override derived
scores. The implementation plan selects the concrete tests; it must include the actual production runner
and dispatch integration, not just tests of standalone predicate helpers.

## 12. Parts 2 and 3 delta roadmap

The existing parent design already defines the research stages and their rules. Do not redesign the
screen size, repetition schedules, advancement slots, treatment qualification, or held-out confirmation.

| Delta or completion dependency | Required next step |
|---|---|
| Remaining library | Apply the Part 1 contract to the remaining three development and three held-out positions, then freeze all nine together before any model transcript exposure. |
| Five-model roster | Use anchors `gemma4-26b`, `qwen3.6-27b`, plus `qwen3.8-27b-cpp`, `granite4.2-30b-cpp`, and `ornith-1.5-35b-cpp`; verify their actual identities and topology during admission. |
| Budget | Carry forward fifteen model round trips with common comparison limits and the parent's latency-derived wall guard and diagnostic A/B rules. |
| Scoring | Use the signed version-2 contract, with per-position maximums, loss weights, and meaningful-increment derivations. Do not import calibration score interpretations. |
| Common tool surface | Part 1 uses the frozen list below. Part 2 may extend it for remaining domains, creating a new identity and revalidating the common surface before model screening. |

### Frozen Part 1 tool surface

The implementation plan must create `benchmarks/toolsets/plan3-part1-v1.yaml` with toolset ID
`plan3-part1-v1` and this exact ordered `game_tools` list. It is the current 29-tool standard surface plus
six named capabilities; do not resolve a mutable `standard` alias at runtime. `finish_trial` is the
separate common control tool and is appended by the benchmark agent. `end_turn` is absent.

```yaml
game_tools:
  - get_overview
  - get_units
  - get_cities
  - move_unit
  - found_city
  - set_city_production
  - set_research
  - fortify_unit
  - skip_unit
  - get_unit_promotions
  - promote_unit
  - get_map_area
  - get_tech_civics
  - attack_unit
  - get_builder_tasks
  - improve_tile
  - remove_feature
  - repair_improvement
  - get_great_people
  - recruit_great_person
  - activate_great_person
  - purchase_item
  - heal_unit
  - alert_unit
  - set_civic
  - get_pending_diplomacy
  - respond_to_diplomacy
  - get_pending_trades
  - respond_to_trade
  - get_city_production
  - get_district_advisor
  - get_purchasable_tiles
  - purchase_tile
  - get_pathing_estimate
  - get_empire_resources
```

The capability audit is a newly discovered prerequisite. The current standard tier omits production-option
and district-advisor queries, and several later diplomacy/religion/congress capabilities. Use an explicit,
versioned benchmark allowlist with the same surface for all screen models and for both arms of an injection
comparison. Part 1 scripts exercise this frozen surface, including production-option discoverability.
Part 2 extends it only where the remaining library needs additional capabilities, before the common freeze;
a surface change invalidates earlier tool-identity validation
and requires revalidation of all positions using the changed common surface. This Part 2 revalidation is
separate from the recorded Part 1 authoring attempt and cannot reuse its old tool-identity certification.
Do not silently widen the arena's general standard tier.

Production/read advisories are capabilities, not rubric authority. Tool admission must also distinguish
an action from its future outcome: for example, a single-turn Congress position cannot claim an actual
resolution outcome that requires `end_turn`. Unobservable or unavailable objectives block authoring.

Briefing and playbook remain separate Stage 3 injections into an otherwise identical chat loop, with
actual-injection checks and input fingerprints. Combine them only under the parent's qualification and
interaction rules. Non-empty options remain rejected until they are actually implemented. The
decision-model experiment is separately designed, has a same-menu LLM control, and consumes the library
through player-visible observations. Tracker evaluation remains deferred to multi-turn rollouts.

The [SystemOne server handoff](../../handoffs/2026-10-08-systemone-decision-servers.md), committed as
`02f0176`, records available local Clef-Flash/Laya services and a hosted Clef 27B reference. Their registry
support exists; the arena decision adapter and health/protocol admission still belong to that separate
experiment. They are not chat endpoints, additional screen models, or a Part 1 dependency. The implementation
plan carries the consumer's protocol, timing, probability, menu and telemetry requirements forward. Part 1
discoverability must cite facts visible in the tool results actually delivered under the locked character
cap, not facts found only in private or untruncated scoring evidence. Infrastructure availability does not
authorize any model warm-up or trial on library positions during Part 1.

## 13. Implementation handoff

The written spec is reviewed before invoking `superpowers:writing-plans`. The implementation plan must
respect the component boundaries and the gate above, and build the authoring/validation tooling before
starting the first position's live authoring clock. Live execution follows the existing
[arena operating playbook](../../../tools/skills/civ6-arena-live/SKILL.md), including single FireTuner
ownership and the established benchmark reload workflow.

The [Part 1 implementation plan](../plans/2026-10-08-arena-benchmark-plan-3-part-1.md) specifies the work,
offline checks, and live gates. Implementation and live authoring have not started.
