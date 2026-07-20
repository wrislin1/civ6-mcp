# Arena Reusable Live-Gate Driver — Unofficial Channels Scenario (Design)

**Date:** 2026-07-17
**Status:** Design approved by riz; written-spec review applied 2026-07-17
**Target branch:** `arena-unofficial-channels-core`
**Predecessor:** `2026-07-09-arena-unofficial-channels-design.md`
**Live evidence:** `../plans/2026-07-16-arena-unofficial-channels-core-live-gate.md`

## Goal

Add permanent, reusable, coordinator-owned infrastructure for deterministic
attended arena live gates. Its first scenario, `unofficial_channels_core_v1`,
must drive the existing unofficial-channel entry paths through a complete live
lifecycle without asking a model to choose channel actions.

The scenario must:

- run only when explicitly enabled by a validated smoke experiment;
- schedule a privacy canary, proposals, acceptance, funding, payment response,
  an honored continuous term, an intentional breach, and inclusive deadline
  checks;
- exercise the bound API action path for the configured API actor;
- exercise the production `CHANNEL {...}` parsing path for the configured CLI
  actor;
- advance only after persisted acknowledgements and canonical state confirm the
  expected transition;
- select valid terms from authoritative live observations, without editing the
  channel ledger or injecting evidence;
- stop after the official payment's synchronous settlement is durably recorded
  in the funding seat window, request a watcher restart, and resume from the
  persisted journals;
- fail closed on acknowledgement, source, state, observation, fingerprint,
  privacy, or deadline disagreement; and
- leave ordinary arena policy behavior unchanged when the gate is disabled.

## Non-Goals

This design does not:

- automate the inherited conquest, recapture, loyalty-transfer, or other raw
  FireTuner component-ID probes. Those require a separate scenario and a
  different disposable-save contract;
- test whether an unconstrained language model voluntarily uses channel tools;
- add channel actions to the ordinary game-tool registry;
- write directly to canonical channel state, acknowledgements, payment records,
  observations, outcomes, or grievances;
- create synthetic game evidence or infer payment from treasury deltas alone
  (revision 3 verifies settlement with three concurrent same-window signals —
  the exact ±gold treasury deltas, the seat-independent absent offer state,
  and the authoritative fund acknowledgement — never a delta by itself; the
  payee's payment response is channel-ledger bookkeeping that consumes no
  engine offer);
- choose ordinary game strategy for the smoke seats; or
- alter normal local, CLI, or seat-zero agent behavior outside an explicitly
  enabled live gate.

The overall Task 12 attended gate remains blocked until both this lifecycle
scenario and the separate raw FireTuner-probe scenario pass.

## Locked Decisions

1. The infrastructure is reusable; unofficial channels are its first registered
   scenario rather than a coordinator-specific hard-coded branch.
2. The coordinator owns orchestration. An external script does not inject
   actions or edit artifacts.
3. Gate mode uses deterministic minimal turn handling and does not invoke model
   backends or CLI model subprocesses.
4. The API actor still stages each action through its production
   `ChannelTurnContext.dispatch` binding. The scenario never calls
   `ChannelRuntime.apply_staged` for that actor.
5. The CLI actor returns exact one-line JSON in `transcript.final_summary` and
   relies on `ChannelRuntime.finish_player` to call the production parser. The
   scenario never calls `parse_cli_channel_lines` directly.
6. A channel-enabled passive observer takes deterministic no-action turns so
   its production projection, rendered prompt block, acknowledgements, and
   transcript can be checked for privacy.
7. Semantic roles are configured, not hard-coded. The first smoke binds
   `api_actor=1`, `cli_actor=2`, and `privacy_observer=3`.
8. A validated experiment block is the sole activation switch. The same command
   and run ID must resume a restart checkpoint without another flag.
9. The watcher exits cleanly at the restart boundary with exit code 75 and a
   persisted, machine-readable `restart_required` state. The persisted state
   and the printed restart line are the authoritative operator signals; the
   live wrapper launches the watcher detached (`setsid … &`), so the exit code
   is observable only to foreground and test invocations.
10. The successful up-front deal uses a continuously monitored trade-route
    restriction chosen against authoritative route observations.
11. The broken on-delivery deal first satisfies a safe authoritative treasury
    term; the configured CLI actor then intentionally omits funding through its
    inclusive deadline.
12. Gate orchestration state has its own journal and snapshot. Canonical channel
    state remains owned exclusively by `ChannelRuntime`.
13. (Revision 2, 2026-07-18.) The official payment settles in the same round it
    is offered: the API actor funds and the CLI actor accepts in consecutive
    seat windows of one game turn. Nothing pending in the engine may span a
    seat-window boundary the scenario does not control, and the restart
    checkpoint anchors exclusively on durable settlement evidence, never on
    live pending-deal state. Grounds: attended runs `arena-channels-core-gate-v1`
    and `-v2` plus seat-0 probes proved the engine auto-resolves deals offered
    to an AI player asynchronously — even mid-human-turn and even when every
    gate seat completes its no-action capture — so a pending official payment
    cannot survive to the round boundary, let alone across the restart gap.
14. (Revision 3, 2026-07-20.) The engine enacts an AI→AI `PROPOSED` official
    deal synchronously at send: `SendWorkingDeal(PROPOSED, payer, payee)`
    transfers the gold before the first observable poll, and no pending deal
    ever exists for the pair. Settlement is therefore verified in the same
    seat window that funds: the payer's window records write-ahead treasury
    baselines, sends the offer, then immediately requires the seat-independent
    payment-state query to report `absent` and the same-window treasury deltas
    to equal exactly −gold/+gold, and records the settlement digest. The payee
    still emits `respond_to_payment(accept=true)` — it exercises the
    production CLI parse path and settles the channel ledger — but it is
    bookkeeping: its preflight expects `absent` (an `exact` offer at response
    time is the anomaly), and it performs no engine mutation. Grounds:
    attended run `arena-channels-core-gate-v3` failed
    `official_payment_auto_resolved` with the offer already absent at the
    first CLI-window poll of the same game turn, and a controlled lifecycle
    probe (2026-07-20, same save, turn 158) observed `pending=false` with
    deltas of exactly −1.000/+1.000 on the first sub-second poll after send,
    unchanged for 300 s with no turn advance.

## Architecture and File Boundaries

The implementation adds a generic live-gate layer alongside, not inside, the
channel reducer:

| File | Responsibility |
|---|---|
| `src/civ_mcp/arena/live_gate.py` | Generic options, driver protocol, immutable state/events, strict reducer, persistence, registry, restart/failure signals |
| `src/civ_mcp/arena/live_gate_channels.py` | `unofficial_channels_core_v1` phase planner, action construction, canonical assertions, privacy checks, terminal evidence |
| `src/civ_mcp/arena/config.py` | Disabled-by-default `LiveGateOptions` on `ArenaConfig` and cross-field validation; update the `scripted` provider's test-only comment to name the live-gate observer as its second sanctioned use |
| `src/civ_mcp/arena/experiment.py` | Strict parsing of the top-level `live_gate` experiment block |
| `src/civ_mcp/arena/arena.py` | Resolve the registered driver before ordinary policy construction; gate mode creates no model-backed policies; translate the driver's restart/terminal signal into the machine-readable result line and the process exit status |
| `src/civ_mcp/arena/coordinator.py` | Narrow admission/capture hooks that ask the driver for deterministic turn input and report persisted results back |
| `experiments/arena-channels-core-smoke.yaml` | Explicit scenario enablement, semantic roles, passive player 3, and sufficient capture budgets |
| `tests/arena/test_live_gate.py` | Generic reducer, persistence, replay, restart, failure, and registry tests |
| `tests/arena/test_live_gate_channels.py` | Scenario planning, lifecycle, term selection, privacy, and terminal evidence tests |
| Existing arena tests | Disabled-path equivalence and coordinator wiring regressions |

`live_gate.py` must not import unofficial-channel scenario details. The scenario
module may depend on the public channel runtime, protocol, term, projection, and
prompt interfaces. Neither module may import Lua builders or mutate a
`ChannelState` directly.

### Driver boundary

The generic driver receives an admitted seat, the authoritative channel runtime,
the current game-state connection, and the persisted gate state. It may:

- perform explicit read-only preflight observations;
- create an exact planned input for the current semantic role;
- call the admitted API role's bound context;
- return a deterministic policy-result mapping for the CLI or observer role;
- inspect canonical state and acknowledgements after normal
  `ChannelRuntime.finish_player` processing;
- request a clean restart or terminal stop; and
- append gate events through the generic persistence layer.

It may not apply a staged channel action itself, manufacture an acknowledgement,
change a deal, write an observation into the channel journal, respond to an
official trade outside the channel payment runtime, or bypass the ordinary
coordinator admission/finish ordering.

### Disabled path

When `live_gate.enabled` is false, driver resolution returns `None`. Policy
construction, provider preflight, policy invocation, turn capture, repair,
attention, and transcript behavior follow the existing path. The coordinator
must not create a live-gate directory or evaluate scenario code.

When enabled, driver resolution happens before ordinary policy construction.
The configured provider identities are validated as path contracts, but no
local backend, Codex process, or other model policy is constructed or invoked
for a gate role.

## Experiment Configuration

The first scenario uses this strict configuration shape:

```yaml
run_id: arena-channels-core-gate-v3
max_puppet_turns: 36
max_game_turns: 36

live_gate:
  enabled: true
  scenario: unofficial_channels_core_v1
  roles:
    api_actor: 1
    cli_actor: 2
    privacy_observer: 3
```

The existing civ entries remain the source of player/provider/channel identity.
The smoke configuration keeps player 1 as an in-process local provider, player 2
as a CLI provider, and adds player 3 as a scripted passive observer. All three
must have `channels.enabled: true`.

Validation requires:

- `enabled` is an exact boolean;
- disabled options cannot carry a scenario or roles;
- the scenario name is non-blank and present in the registry;
- the roles mapping contains exactly the scenario's required role names;
- role player IDs are exact, distinct integers present in `civs`;
- `api_actor.driver_kind()` is `in_process`;
- `cli_actor.driver_kind()` is `cli`;
- the observer is the deterministic scripted provider;
- every role is channel-enabled;
- `attention.mode` is `off` for every gate civ — turn skipping would starve
  the phase machine of admissions;
- no player-0 (seat-zero piloting) entry is combined with the gate — the gate
  relies on the human owning seat 0 across the restart boundary;
- `run_id` is explicit and safe;
- both turn budgets meet the scenario-reported minimum; and
- the run directory is new on first initialization or contains a matching gate
  identity on resume.

The scenario's default rules and fixed one-turn favor windows require 24 seat
captures in the expected eight-round path: revision 2's same-round settlement
removes the dedicated post-resume payment-response round, and the first
post-resume round is the up-front favor's inclusive due round (settlement in
round 2 puts the `within=1` deadline exactly on round 3), so no idle resume
round exists. The checked-in budget is 36 to leave room for watcher handoff
and deterministic blocker cleanup. If channel-rule values change the computed
minimum, configuration validation uses the scenario's computed value rather
than silently accepting 36.

The prior failed two-seat run directory cannot be reused because adding the
privacy observer changes the canonical enabled-player identity. The new run ID
prevents accidental continuation of incompatible channel state.

## Gate State and Persistence

Gate artifacts live under the private run directory:

```text
arena_runs/<run_id>/live_gate/events.jsonl
arena_runs/<run_id>/live_gate/state.json
arena_runs/<run_id>/live_gate/result.json
```

The directory and files use the same private ownership and regular-file safety
rules as channel persistence. `events.jsonl` is append-only and authoritative;
`state.json` is an atomically replaced derived snapshot. `result.json` appears
only for `restart_required`, terminal PASS, or terminal FAIL and contains no
secret from another player's projection.

The reduced state records:

- schema version and scenario revision;
- run ID, scenario name, semantic role mapping, and configuration fingerprint;
- current phase, expected role, and current/last verified game turn;
- authoritative preflight observation references and sanitized hashes;
- selected term parameters and payment amount;
- the deterministic canary digest and private canary text;
- planned action payloads, content digests, line indexes, and expected source
  IDs;
- captured message/deal IDs and acknowledgement identities;
- canonical state/event identity at each phase boundary;
- the official payment fingerprint and its same-turn settlement evidence
  (payer/payee treasury baselines at funding, post-settlement deltas, and the
  settlement digest carried across the restart);
- observer privacy assertion digests;
- restart count; and
- terminal status, reason, and evidence references.

The configuration fingerprint includes the scenario revision, role bindings,
provider driver kinds, channel-enabled flags, channel-rule fingerprint, fixed
scenario parameters, and run ID. A resumed process must match it exactly.

### Write-ahead orchestration

Before supplying an action, the driver appends an `action_planned` event with
the exact normalized payload and expected deterministic source identity. After
the normal channel finish, it advances only after finding the expected persisted
acknowledgement and canonical state transition.

If the process stops after channel persistence but before gate phase advancement,
reopening scans canonical acknowledgements and state. A matching transition is
recorded once and the phase advances. A missing transition is reissued only when
the same bound player and game turn can reproduce the exact source identity;
otherwise the gate fails. Replayed source IDs remain protected by the channel
runtime's existing idempotency.

The gate journal never substitutes for the channel journal. Its claim that a
phase succeeded is invalid unless the canonical state independently proves it.

## Authentic Entry Paths

### API actor

For each planned API action, the driver calls:

```text
admission.context.dispatch(action_name, normalized_args)
```

The bound context supplies the run, actor, turn, enabled counterparties, rules,
validation, action ordering, and `api:` source ID. The coordinator then passes
the admission through unmodified `ChannelRuntime.finish_player`, which collects
the staged context actions and applies them normally.

### CLI actor

For each planned CLI action, the deterministic result contains exactly one
line at a recorded index:

```text
CHANNEL {<canonical compact JSON>}
```

The line is present in `transcript.final_summary`. The driver does not parse it.
Unmodified `ChannelRuntime.finish_player` reads the raw summary, invokes the
production parser, derives the `cli:` source ID, observes the required evidence,
and applies or rejects the staged action.

### Privacy observer

The observer emits no channel or game action. Its turn uses the admitted
production `ChannelProjection`, `format_channel_block`, and
`build_opening_prompt` output to create an inspectable deterministic transcript.
No model sees the prompt. The transcript and public turn record contain only the
observer's authorized empty/private view plus non-private gate metadata.

## Authoritative Preflight and Term Selection

Before the first proposal, the scenario performs read-only queries through the
typed game-state wrapper:

- player 1 treasury (`treasury` observation family);
- player 2 treasury and active trade routes (`treasury` and `trade_routes`
  observation families); and
- the exact pending-trade state for the ordered payment pair, via the
  payment-state query (`get_channel_payment_state`) — pending trades are a
  payment-runtime query, not an `ObservationFamily`.

Every requested family must be present and carry no parser/engine error. Player
1 must be able to fund the fixed one-gold official payment, and no conflicting
pending trade may make linkage ambiguous. Failure stops the gate; there is no
fallback to a guessed term or ledger fixture.

The up-front favor is:

```json
{
  "term_type": "dont_trade_with",
  "params": {
    "target_player": 3,
    "trade_kinds": ["trade_route"]
  }
}
```

The target is the configured privacy observer, not a hard-coded integer. Player
2's authoritative route observation is required before selection. At unofficial
acceptance, the channel runtime captures the authoritative baseline again;
already-active matching routes are baseline-exempt. Deterministic gate turns do
not create or redirect a trader, so only real live route observations decide the
continuous result.

The on-delivery favor is:

```json
{
  "term_type": "maintain_gold_reserve",
  "params": {"min_gold": 0}
}
```

The driver selects it only after a complete player-1 treasury observation shows
that the threshold is currently satisfied. The channel runtime captures and
monitors the real treasury family through the inclusive due turn. The driver
does not inject a treasury value.

## Scenario Phase Machine

The scenario advances through the following persisted phases. A passive privacy
assertion runs on every observer admission, not only at the end.

Synchronous fund-window settlement is `scenario_revision: 3`. The revision
participates in the configuration fingerprint, so a revision-1 or revision-2
journal (runs `-v1`/`-v2`/`-v3`) can never be resumed by a revision-3 process;
a rerun requires a fresh run ID and the reloaded disposable save.

| Phase | Input | Required canonical evidence before advancement |
|---|---|---|
| `preflight` | Read-only treasury, route, and pending-trade queries | Complete requested families, no errors, legal roles/target, sufficient player-1 gold, no conflicting offer |
| `canary_and_upfront_proposal` | API actor dispatches `send_message` then `propose_deal` | Exactly two expected `api:` acknowledgements; captured canary message and up-front deal IDs |
| `accept_upfront` | CLI actor emits `respond_to_deal(accept=true)` | Expected `cli:` acknowledgement; deal active, payment due, canonical trade-route baseline attached |
| `fund_upfront` | API actor dispatches `fund_deal`; settlement verified in the same seat window | Write-ahead payer/payee treasury baselines recorded before the send; expected `api:` acknowledgement; exact official fingerprint recorded by channel runtime; immediately after the send, the seat-independent payment-state query reports `absent` and the same-window treasury deltas equal exactly −gold/+gold; settlement digest recorded. A non-`absent` post-send state or any delta mismatch is the terminal forensic `official_payment_not_enacted` |
| `accept_upfront_payment` | CLI actor emits `respond_to_payment(accept=true)` in its seat window of the same game turn | Expected `cli:` acknowledgement; settlement digest already recorded; pre-response seat-independent payment-state is `absent` (the enacted offer is gone); channel ledger transitions the payment to `settled` exactly once with no engine mutation. An `exact` or conflicting offer at response time is the distinct terminal forensic `official_payment_unexpectedly_pending` |
| `restart_required` | Deterministic no-action turns for the round's remaining gate seats | Settlement digest present and internally consistent; canonical channel state/event identity recorded; gate snapshot and result persisted; the round completes with no gate seat released to the game AI; watcher exits 75. No live pending-deal query participates in this checkpoint |
| `restart_verify` | Reopen and reconcile only | Same configuration/channel identity; exactly one restart; settlement digest unchanged; the seat-independent payment-state query reports `absent` for the ordered pair (no stray or resurrected official offer); no duplicate payment-response acknowledgement |
| `await_upfront_favor_deadline` | Deterministic no-action turns | No premature terminal state; complete route observations on each obligated admission |
| `verify_upfront_honored` | No new channel action | Favor satisfied on its inclusive due turn; up-front deal terminal `honored` |
| `propose_on_delivery` | CLI actor emits a one-gold on-delivery proposal | Expected `cli:` acknowledgement; captured second deal ID |
| `accept_on_delivery` | API actor dispatches `respond_to_deal(accept=true)` | Expected `api:` acknowledgement; active deal and complete player-1 treasury baseline |
| `await_on_delivery_favor` | Deterministic no-action turns | No premature success/failure; favor satisfied on its inclusive due turn; payment becomes due |
| `withhold_on_delivery_funding` | CLI actor deliberately emits no channel action | Deal remains nonterminal before `fund_by_turn`; no unexpected acknowledgement |
| `verify_funding_breach` | CLI actor finishes its inclusive funding-deadline turn without funding | Deal terminal `broken`; payment failed; deterministic grievance has the expected proposer offender/counterparty wronged mapping |
| `verify_terminal_gate` | Observer no-action capture and analyzer-ready checks | At least one honored deal, one broken deal, one settled payment, and one deterministic grievance; all privacy assertions pass |

Deal and message IDs are learned from canonical acknowledgements/state and then
persisted. The driver never assumes `deal-000001` or another sequence number.

## Restart Handshake

Revision 2 (2026-07-18): the restart no longer parks on a live pending offer.
The original design assumed that completing the round with deterministic
no-action captures would shield the pending official payment from player 2's
engine AI until the resumed gate accepted it. Attended runs
`arena-channels-core-gate-v1` and `-v2` disproved that mechanism (see Observed
Engine Constraints below): the engine auto-resolves deals offered to an AI
player asynchronously, regardless of round choreography.

Revision 3 (2026-07-20): attended run `arena-channels-core-gate-v3` and the
controlled lifecycle probe showed that even consecutive seat windows of one
game turn cannot bracket a pending offer — enactment is synchronous with the
send (Locked Decision 14). Settlement is therefore verified entirely inside
the funding seat window (baselines → send → absent-state check → exact-delta
check → settlement digest), and the CLI actor's `respond_to_payment` in the
same game turn settles only the channel ledger. The restart checkpoint
continues to anchor on durable settlement evidence only.

After settlement, the driver completes the current channel finish before
requesting a restart. The restart takes effect only at the round boundary:
every remaining gate seat in the current game turn first completes its
deterministic no-action capture, including the observer's privacy assertion.
This ordering remains load-bearing for the favor terms — coordinator shutdown
restores the human seat and disables the puppet hook, so exiting mid-round
would release the unplayed gate seats to the game AI, whose free play could
violate the monitored trade-route restriction or move treasuries. At the round
boundary the driver then:

1. re-reads the recorded settlement digest and requires internal consistency
   (fingerprint, same-turn treasury deltas, consumed offer, acknowledgement
   identity);
2. records the current channel state/event identity;
3. appends `restart_required` and writes `result.json`;
4. asks the coordinator to deactivate safely; and
5. prints a machine-readable restart line and returns exit code 75.

The coordinator's shutdown path performs its normal handback (restore the
human seat, disable the hook). The operator follows the `civ6-arena-live`
ownership workflow and reruns the same experiment and run ID — and must not
end the human turn in the restart gap, because with the hook disabled an
ended turn hands every gate seat to the game AI. On resume, the channel
runtime performs its existing journal replay and payment-intent
reconciliation before the gate may act. The driver then requires:

- the gate and channel identities match the checkpoint;
- the gate has exactly one prior restart request;
- the settlement digest is unchanged;
- the seat-independent payment-state query reports `absent` for the ordered
  pair — no stray or resurrected official offer; and
- no duplicate payment-response acknowledgement exists.

Only then does the scenario advance to `await_upfront_favor_deadline`. A second
restart request or any settlement-evidence disagreement is terminal failure.

### Observed Engine Constraints (live evidence, 2026-07-18 and 2026-07-20)

Recorded from attended runs `arena-channels-core-gate-v1`/`-v2`/`-v3`, seat-0
FireTuner probes on the reloaded gate save (fix commit `a9682b2`), and the
2026-07-20 deal-lifecycle probe (retained run directories; probe script
`probe_deal_lifecycle.py`):

- (Supersedes "within minutes".) The engine enacts an AI→AI `PROPOSED` deal
  synchronously at `SendWorkingDeal`: the 2026-07-20 lifecycle probe created
  a one-gold player-1→player-2 deal mid-human-turn and observed
  `HasPendingDeal=false` with treasury deltas of exactly −1.000/+1.000 on the
  first sub-second poll, stable for 300 s with no turn advance. Attended run
  `-v3` independently observed the funded offer already absent at the first
  CLI-window poll of the same game turn. A pending AI→AI official payment is
  never observable at all; enactment (gold transfer) is part of the send.
- Within one game turn, gate-seat treasuries are otherwise static (the same
  300 s trace showed no other movement), so exact same-window −gold/+gold
  deltas immediately after a send are attributable to the official payment.
- Puppeting a seat suspends its AI only during that seat's window.
- `PlayerManager.SetLocalPlayerAndObserver` silently refuses to switch seats
  outside the turn-start event context the puppet hook runs in, so a
  checkpoint can never "borrow" an involved seat; any query it needs must be
  seat-independent.
- The payment-state query is seat-independent as of `a9682b2`
  (`HasPendingDeal(payer, payee)` + `GetWorkingDeal(OUTGOING, payer, payee)`
  with explicit ids, no `Game.GetLocalPlayer()` dependence).
- `DealManager.GetWorkingDeal` can return a stale one-item working buffer when
  no pending deal exists; `HasPendingDeal` must remain the gating check.
- Treasury reads (`Players[i]:GetTreasury():GetGoldBalance()`) are
  seat-independent, which is the basis for same-window settlement deltas.

## Privacy Contract

The canary text is deterministic for a run but unguessable from player 3's
ordinary channel state, for example a fixed prefix plus a digest of run identity
and the gate configuration fingerprint. The private gate journal may record it;
player-3 artifacts may not.

On every observer admission, serialize and scan all of:

- `ChannelProjection`;
- the production-formatted channel block;
- the production opening prompt containing that block;
- acknowledgements visible to the observer;
- the deterministic policy result;
- the observer's pending transcript record; and
- persisted player-3 transcript records accumulated so far.

The raw-text scan forbids the canary text/digest, private proposal text, private
payment fingerprints, and acknowledgement sources belonging to players 1 or 2.
The structured projection/acknowledgement scan separately requires no message,
deal, grievance, or acknowledgement involving players 1 and 2. Deal-ID text
alone is not a valid raw-text assertion because production CLI instructions use
generic examples such as `deal-000001`, which can coincide with a real monotonic
ID without disclosing the deal. Player 3 must have no channel acknowledgement
because it emits no action.

The driver also confirms the canary is present in the authorized player-1 and
player-2 projections. Absence there means the canary was not actually exercised
and is failure, not privacy success.

Privacy assertion events store the inspected artifact kind, turn, player, input
digest, forbidden-value digest set, and PASS/FAIL result. A failure preserves
the private forensic input in the protected gate directory but never copies the
leaked text into a public turn summary.

## Deterministic Minimal Turns

Gate turns reuse existing deterministic scripted observation and mechanical
cleanup behavior. They may observe the overview/units, skip or finish units, and
choose a legal tile-free repair/production/research item when the existing
scripted resolver has an unambiguous option.

They must not:

- buy or sell anything;
- create, redirect, or cancel a trade route;
- propose or respond to ordinary diplomacy outside the channel payment runtime;
- declare war;
- move a civilian or military unit for strategic reasons;
- select a policy through a model; or
- invoke any model backend or CLI agent.

If the current save presents a blocker that existing deterministic cleanup
cannot resolve within those bounds, the gate fails and returns human control.
It does not broaden its gameplay authority to keep the test moving.

## Fail-Closed Rules

Any of the following writes a terminal `gate_failed` event, writes
`result.json`, stops all gate actions, does not accept, cancel, recreate, or
retry any pending official trade, and requests safe coordinator deactivation:

- wrong run, scenario revision, config fingerprint, role, player, or turn;
- missing, duplicate, reordered, rejected, or unexpected acknowledgement;
- source prefix, line index, payload digest, message ID, or deal ID mismatch;
- unexpected channel action from any role;
- missing observation family, observation/parser error, or changed term input;
- canonical state transition before or after the specified inclusive boundary;
- a post-send payment state other than `absent` or same-window treasury
  deltas other than exactly −gold/+gold inside the funding seat window (the
  terminal forensic `official_payment_not_enacted`);
- an `exact` or conflicting official offer at payment-response time (the
  distinct terminal forensic `official_payment_unexpectedly_pending`;
  revision 2's `official_payment_auto_resolved` is retired — an absent offer
  at response time is now the expected state);
- disagreement within the recorded settlement evidence, or between the
  recorded settlement digest and the resume-boundary re-check;
- a non-`absent` seat-independent payment-state result for the ordered pair
  after settlement;
- restart count other than exactly one at the resume boundary;
- canary/private data in any player-3 artifact;
- canary absent from both authorized participant projections;
- unexpected model/backend/CLI process creation; or
- a deterministic game blocker that cannot be resolved safely.

There is no retry that could create a second official payment side effect. The
channel runtime's existing payment intent/result reconciliation remains the only
authority for retrying or declaring payment ambiguity.

## Offline Verification

### Generic driver tests

Tests cover:

- strict event/state schema and exact identity validation;
- append/reduce/snapshot/reopen equivalence;
- duplicate event and terminal-state rejection;
- action planning before emission;
- recovery at every boundary between plan, channel acknowledgement, and phase
  advancement;
- same-turn exact-source replay and changed-turn failure;
- registry lookup and unknown scenario rejection;
- clean restart status/exit signal;
- config mismatch on resume; and
- private directory/file safety.

### Scenario tests

Using the real channel reducer/runtime with a fake typed game-state adapter,
tests prove:

- authoritative preflight requirements and no guessed fallback;
- API canary/proposal/funding/acceptance sources begin with `api:` and came from
  the bound context;
- CLI acceptance/proposal/payment-response sources begin with `cli:` and came
  through `transcript.final_summary` parsing;
- deal IDs are captured rather than assumed;
- existing player-2 routes are baseline-exempt;
- the continuous trade term remains pending before and succeeds on its inclusive
  deadline;
- the payment fingerprint is identical before and after reopen;
- payment enactment is verified synchronously in the funding window (absent
  state plus exact deltas), and the payee response settles the ledger exactly
  once with no engine mutation;
- the treasury term is satisfied from real observations;
- omission before the funding deadline does not breach early;
- omission on the responsible player's inclusive deadline produces the expected
  broken deal and deterministic grievance;
- the restart request defers until every remaining gate seat in the round has
  completed its deterministic capture;
- every named fail-closed mismatch terminates without another action; and
- player 3 never receives the canary or participant-private data in any inspected
  artifact.

### Coordinator isolation tests

Existing coordinator/config/experiment suites gain regressions proving:

- no `live_gate` block preserves current behavior and artifacts;
- `enabled: false` cannot configure a latent scenario;
- gate mode constructs no local backend and spawns no CLI/model process;
- ordinary policies are not called in gate mode;
- each semantic role receives only its planned deterministic input;
- `ChannelRuntime.finish_player` remains the shared authoritative finish path;
- gate configurations with attention enabled or a seat-zero (player 0) entry
  are rejected at configuration load;
- exit 75 occurs only after the persisted restart checkpoint;
- terminal failure and PASS return human control cleanly; and
- insufficient capture budgets fail at configuration load.

The full repository test suite and `git diff --check` remain required before
the implementation branch can be offered for an attended run.

## Attended Acceptance

The attended lifecycle uses a disposable midgame save with alive players 0, 1,
2, and 3; sufficient player-1 gold; legal official trades between players 1 and
2; no conflicting pending deal; and enough stable turns for the deterministic
sequence. The existing T314 save may be used only if preflight proves those
conditions.

The operator follows the `civ6-arena-live` ownership workflow and invokes the
same checked-in experiment twice:

1. First invocation completes synchronous settlement of the official payment
   in the funding seat window, reaches `restart_required`, exits 75, and
   leaves the settlement digest recorded with no pending official offer in
   the engine. The human turn is not ended again until the second watcher is
   armed.
2. Second invocation uses the same run ID, verifies the settlement evidence
   and the absence of any official offer for the pair, honors the up-front
   deal, breaks the on-delivery deal at its funding deadline, records a
   grievance, and exits 0 with terminal PASS.

Required retained evidence is:

- live-gate event journal, snapshot, and terminal result;
- canonical channel event journal and snapshot;
- the official payment fingerprint and same-turn settlement evidence,
  including the digest re-check at the resume boundary;
- transcripts for players 1, 2, and 3;
- per-turn observer privacy assertion records;
- analyzer JSON/Markdown with at least one `honored`, one `broken`, one
  `settled`, and one deterministic grievance; and
- an updated attended live-gate document that distinguishes lifecycle PASS from
  the still-separate raw FireTuner-probe result.

A driver terminal PASS is necessary but not sufficient to mark the complete
Task 12 attended gate PASS. The destructive raw-probe scenario remains a
separate required gate.
