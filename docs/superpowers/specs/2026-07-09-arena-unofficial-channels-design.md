# Arena Unofficial Channels Core — Deterministic Bilateral Deals & Grievances (Design)

**Date:** 2026-07-09
**Revised:** 2026-07-16
**Status:** Approved by riz (written-spec review, 2026-07-16)
**Predecessor:** Seat-0 Piloting (`845ae09` plus attended live-gate fixes through `0c72cca`)
**Follow-ons:** `2026-07-16-arena-game-master-design.md`, then `2026-07-16-arena-channels-human-surface-design.md`

## Goal

Add private, bilateral, unofficial communication to arena runs. Channel-enabled civs can exchange prose, accept structured commitments, settle the gold leg through real Civilization VI trades, and accumulate private unofficial grievances when a commitment is proven broken.

The coordinator owns the authoritative ledger. Models may request transitions, but they cannot invent senders, mark payments complete, declare deterministic terms fulfilled, or write grievances directly.

## Locked Decisions

1. Unofficial state remains arena-side and never writes Civ 6 grievances or diplomatic justification.
2. Gold moves only through exact, linked Civ 6 trades. There is no shadow currency.
3. Deterministic terms are satisfied or broken only by persisted game evidence.
4. Messages, deals, and grievances are private to their parties. Only explicit analyst/director modes receive a full projection.
5. Deal acceptance is explicit and separate from accepting the official payment trade.
6. Condition-based terms use condition attribution: if the requested condition becomes true in time, it counts even if a third party caused it.
7. Grievances decay with a configurable 30-turn default half-life.
8. With no active game master, only registered deterministic terms are enforceable. Free prose is non-binding.
9. An active game master may adjudicate narrative terms only as specified by the follow-on master design; its rulings are tagged rather than disguised as deterministic evidence.
10. The implementation is a coordinator-owned runtime. Master and web processes are optional consumers, never authorities over canonical state.

## Current-Code Findings

- Per-civ prompt state already follows a load/render/inject/capture/save pattern in `memory.py`, `task_tracker.py`, and `attention.py`; `run_arena` owns the orchestration.
- `build_opening_prompt` in `prompting.py` has a fixed block order and accepts independently gated blocks.
- API policies dispatch ordinary game tools through `registry.dispatch(gs, ...)`. That interface has no caller/run identity, so channel actions must not be ordinary `TOOL_REGISTRY` entries.
- CLI policies preserve an unclamped `transcript.final_summary`, which is the authoritative captured-line source.
- The existing pending-deal query exposes exact incoming items, and `respond_to_trade` returns an engine result. That makes linked payment actions reliable; gold-balance deltas are unnecessary and ambiguous.
- Trade routes expose their destination owner and persist while active. Completed diplomatic trades require the arena action transcript because lump-sum deals may not remain in active game state.
- Units expose stable per-owner IDs, type, combat values, and position. Territory exposes owner IDs and hex coordinates. These support acquisition and exclusion-zone terms without free-text judgment.

## Architecture and File Boundaries

| File | Responsibility |
|---|---|
| `src/civ_mcp/arena/channels.py` | Immutable records, state reduction, privacy projections, grievance decay, schema serialization |
| `src/civ_mcp/arena/channel_terms.py` | Term registry, parameter validation, observation requirements, baselines, pure verifier functions, privacy-safe evidence rendering |
| `src/civ_mcp/arena/channel_protocol.py` | Typed actions, API schemas, CLI JSON-line parsing, source IDs, acknowledgements |
| `src/civ_mcp/arena/channel_runtime.py` | Single writer; observation collection, action application, payment side effects, deadlines, journal/snapshot persistence |
| `src/civ_mcp/lua/channel_observation.py` | One targeted InGame query per admission stage for the union of required evidence families |
| `src/civ_mcp/lua/channel_payments.py` | Exact gold-only pending-offer creation and fingerprint-safe response builders |
| `src/civ_mcp/game_state.py` | Narrow typed wrappers for channel observation and linked payment operations |
| `src/civ_mcp/arena/config.py` | Per-civ `ChannelOptions` and run-wide `ChannelRules` |
| `src/civ_mcp/arena/experiment.py` | YAML parsing and contextual validation |
| `src/civ_mcp/arena/agent.py` | Compose game tools with context-bound channel tools for API policies |
| `src/civ_mcp/arena/cli_agent.py` | Channel capture instruction and raw-summary metadata only |
| `src/civ_mcp/arena/prompting.py` | Add `channel_block` to the fixed prompt order |
| `src/civ_mcp/arena/coordinator.py` | Wiring only: tick, observe, build context/block, apply captured actions, persist |
| `src/civ_mcp/arena/playbook.md` | Concise explanation of channel actions and enforceable versus prose commitments |

`channel_runtime.py` keeps term, protocol, and persistence logic out of the already-large coordinator. The coordinator never reimplements a state transition.

## Canonical State

Run-local IDs are monotonic and deterministic: `msg-000001`, `deal-000001`, `grv-000001`, and `evt-000001`.

### Message

```json
{
  "id": "msg-000001",
  "from_player": 1,
  "to_player": 2,
  "turn": 41,
  "text": "Clear the northern camp and I will pay 100 gold.",
  "deal_id": "deal-000001"
}
```

Messages are immutable. A civ may repeat information to another civ in a new message; the runtime never propagates it automatically.

### Deal

```json
{
  "id": "deal-000001",
  "proposer": 1,
  "counterparty": 2,
  "created_turn": 41,
  "accepted_turn": 42,
  "accept_by_turn": 44,
  "completion_window_turns": 10,
  "favor": {
    "term_type": "destroy_camp",
    "params": {"x": 12, "y": 7},
    "baseline": {},
    "monitor": {}
  },
  "payment_gold": 100,
  "timing": "up_front",
  "state": "active",
  "favor_status": "due",
  "payment_status": "settled",
  "fund_by_turn": 44,
  "payment_response_by_turn": 46,
  "favor_due_turn": 56,
  "terminal": null
}
```

`state` is one of `proposed`, `active`, `honored`, `broken`, `declined`, `expired`, or `unverifiable`. Favor status is `not_due`, `due`, `satisfied`, `failed`, or `released`. Payment status is `not_due`, `due`, `offered`, `settled`, `failed`, or `waived`.

Terminal breach data records the wronged party, offender, reason, decisive event/observation references, and adjudication source.

### Grievance

```json
{
  "id": "grv-000001",
  "wronged": 1,
  "offender": 2,
  "deal_id": "deal-000001",
  "turn": 56,
  "reason": "paid favor was not delivered",
  "payment_gold": 100,
  "base_magnitude": 1.0,
  "half_life_turns": 30,
  "adjudication_source": "deterministic",
  "adjudication_metadata": null
}
```

Effective magnitude is `base_magnitude * 0.5 ** (age_turns / half_life_turns)`. The raw promised value and evidence never decay or disappear.

Version 1 derives `base_magnitude` deterministically as `min(10.0, max(0.25, payment_gold / 100.0))`. Thus 100 gold has magnitude 1.0, very small deals remain visible, and a single large deal cannot dominate the prompt indefinitely.

## Entry Paths

### API Policies

When a civ has `channels.enabled`, the coordinator passes a `ChannelTurnContext` bound to the actual run, player, turn, enabled counterparties, runtime, and source-ID factory. `LLMPolicy` adds these schemas independently of the game-tool tier:

- `send_message(to_player, text)`
- `propose_deal(to_player, text, favor, payment_gold, timing, within)`
- `respond_to_deal(deal_id, accept)`
- `fund_deal(deal_id)`
- `respond_to_payment(deal_id, accept)`

The actor is never a model argument. Channel tools are absent when the option is off, including for `tools: full`.

### CLI Policies

CLI policies append one-line JSON actions to the raw final summary:

```text
CHANNEL {"action":"send_message","to_player":2,"text":"..."}
CHANNEL {"action":"propose_deal","to_player":2,"text":"...","favor":{"term_type":"keep_peace_with","params":{"player_id":3}},"payment_gold":100,"timing":"on_delivery","within":10}
CHANNEL {"action":"respond_to_deal","deal_id":"deal-000001","accept":true}
CHANNEL {"action":"fund_deal","deal_id":"deal-000001"}
CHANNEL {"action":"respond_to_payment","deal_id":"deal-000001","accept":true}
```

The coordinator parses the unclamped `transcript.final_summary` and applies valid lines in source order before releasing the captured seat. A line receives a deterministic source ID derived from run, player, turn, line index, and content hash. Replays are no-ops. Malformed/rejected lines do not abort capture; their acknowledgement appears in the player's next channel block.

## Deal and Payment Lifecycle

### Proposal and Acceptance

- Both parties must be configured and channel-enabled.
- `within` is a relative completion window of 1–30 turns.
- A proposal expires after three turns if not accepted.
- Declined and expired deals remain audit records but create no grievance.
- Acceptance activates the correct first obligation; it never implies that an official gold trade was accepted.

Every `*_by_turn` and `favor_due_turn` is inclusive. The responsible party may act during its admission for that numbered game turn; the runtime evaluates expiry/default only after that party's captured actions, or at the next deterministic poll if that seat is no longer part of the run. Due channel obligations wake a sleeping automated seat so sleep cannot consume its final opportunity.

### Up-Front Payment

1. Proposer has two turns after unofficial acceptance to call `fund_deal`.
2. The runtime refuses funding while any pre-existing pending trade from that payer to that payee would make linkage ambiguous. Otherwise it creates an exact gold-only outgoing Civ 6 trade and records its fingerprint.
3. Counterparty has two turns to call `respond_to_payment`.
4. That action verifies the live pending trade exactly matches the recorded payer, payee, amount, duration zero, and absence of other items before invoking the engine responder.
5. Successful payment settlement starts the favor completion window.

Failure to initiate payment is the proposer's breach. Rejecting or ignoring a valid linked payment is the counterparty's breach. Once payment settles, failure to deliver the favor by its due turn is the counterparty's breach.

### On-Delivery Payment

1. Unofficial acceptance starts the favor completion window.
2. Proven favor satisfaction makes payment due.
3. Proposer has two turns to fund; counterparty has two turns to accept.
4. Missing funding is the proposer's breach. Rejecting/ignoring the exact payment is the counterparty's breach.
5. The deal is honored only when favor and payment are both complete.

Only one unresolved channel-payment offer may exist for an ordered pair. Failed engine calls leave the obligation due until its deadline. Gold deltas and model statements are never payment evidence.

The responsible-party mapping is fixed: a favor failure wrongs the proposer and names the counterparty as offender; missing funding wrongs the counterparty and names the proposer; rejecting or ignoring an exact linked payment wrongs the proposer and names the counterparty. Because lifecycle phases are sequential, the runtime never collapses two simultaneous obligations into one ambiguous breach.

Game side effects use write-ahead intent/result events. Recovery reconciles an unfinished intent against the exact unique pending trade. For a funding intent, presence of that trade proves the offer exists and the runtime records `offered` without sending again. For a response intent, presence proves the response did not consume the offer, so the runtime may retry the journaled accept/reject exactly once. If the trade is absent and no authoritative engine result was journaled, the side effect is ambiguous: the deal becomes `unverifiable`, no grievance is created, and the runtime never retries an action that could double-pay. Conflicting pending trades fail closed for that action and remain retriable only while the original deadline is open.

## Observations and Verifier Registry

Each `TermSpec` declares:

- accepted parameter schema and bounds;
- baseline requirements;
- observation families (`units`, `territory`, `cities`, `treasury`, `diplomacy`, `trade_routes`, `camps`, `action_audit`);
- endpoint or continuous evaluation;
- sanitized evidence rendering.

The runtime compiles the union of required families into one targeted observation at participant admission and one after its policy/captured actions. It does not query once per deal. Observations and decisive monitor state persist.

Missing evidence never creates an accusation. An observed success or violation remains decisive. If gaps make the result unknowable, the terminal state is `unverifiable` and no grievance is created.

### Initial Deterministic Catalog

| Term | Semantics |
|---|---|
| `destroy_camp(x,y)` | Camp must exist at proposal; satisfied once absence is observed |
| `dont_settle_within(radius,x,y)` | Continuous; fails if obligated civ founds a city in the area, even if it later disappears |
| `found_city_within(radius,x,y)` | Endpoint; a newly founded obligated-civ city must appear by the due turn |
| `declare_war_on(player_id)` | Endpoint; required war state observed by due turn |
| `keep_peace_with(player_id)` | Continuous; fails on any observed war state |
| `forbid_unit_acquisition(category)` | Continuous; no new per-owner unit identity in the selected category |
| `allow_only_unit_category(category)` | Continuous; every newly acquired unit must match; non-unit production is unrestricted |
| `military_unit_cap(max_units)` | Continuous total military-unit ceiling |
| `withdraw_units_from(player_id,min_distance,unit_scope)` | Endpoint withdrawal by due turn |
| `keep_units_away(player_id,min_distance,unit_scope)` | Continuous minimum hex distance from the protected player's current territory, after a one-turn withdrawal grace |
| `dont_trade_with(target_player,trade_kinds)` | Continuous; completed diplomatic deals and/or outgoing routes are forbidden. City-state targets use routes |
| `send_trade_route_to(target_player)` | Endpoint; matching active outgoing route observed by due turn |
| `maintain_gold_reserve(min_gold)` | Continuous treasury floor |

Unit categories come from the unit definition, not model prose. `military` means a unit whose formation class is land combat, naval, air, or support; `civilian` means the civilian formation class; `religious` is the civilian subset with positive religious strength. `forbid_unit_acquisition` and `allow_only_unit_category` accept `military`, `civilian`, or `religious`. Religious units match both `civilian` and `religious`; support units, including Military Engineers, match `military`.

Military acquisition includes training, purchase, grant, levy, and transfer. An upgrade retaining the same per-owner unit identity is not a new acquisition. Unit baselines are captured when the favor window starts. A new identity violates the applicable continuous rule regardless of how it was obtained.

`unit_scope` is `military` or `all`. Distance is the minimum hex distance from any in-scope obligated-civ unit to any tile currently owned by the protected player. The withdrawal grace for `keep_units_away` is exactly one game turn after the favor window starts; later transient violations remain decisive even if the unit subsequently leaves. `military_unit_cap` applies immediately when the favor window starts, including when the observed baseline is already above the accepted cap.

`trade_kinds` is a non-empty subset of `diplomatic_deal` and `trade_route`. For a major-civ target, `diplomatic_deal` covers a successful outgoing proposal or accepted incoming deal involving the target, while `trade_route` covers an outgoing route owned by the obligated civ. For a city-state target, only `trade_route` is valid. Rejected proposals and incoming routes controlled by another player do not count.

Completed diplomatic deals are proven from successful arena action audit records. The term is rejected for an actor whose trade actions can bypass arena instrumentation, such as a human trading directly through the game UI. With an active master, such a request may instead be a narrative term.

Zone verification may use authoritative hidden positions but renders only that the boundary was violated; it never leaks the hidden unit or coordinate.

`pay_gold` is not a favor term because payment already has a dedicated leg. `spread_religion_to` is deferred until presence/follower/majority semantics are designed.

## Narrative Extension Point

Schema version 1 reserves `term_type: narrative`, but core mode rejects narrative proposals. The game-master slice registers the adjudicator described in the follow-on spec; its terminal records and grievances use `adjudication_source: game_master`. It cannot override deterministic terms or payment state.

## Privacy and Prompt Projection

`project_for_player(player_id)` includes only messages where the player is sender/recipient, deals where it is proposer/counterparty, grievances where it is wronged/offender, and its own acknowledgements. The formatter never filters by prose after rendering; it filters typed records first.

Prompt content is bounded to the newest 10 messages per counterpart, all unresolved deals, five recent terminal deals, non-negligible grievances, and recent acknowledgements. Full audit history remains in persistence.

The fixed opening order becomes:

1. game briefing;
2. standing memory;
3. task tracker;
4. channel block;
5. future game-master block;
6. attention digest;
7. blocker repair;
8. turn announcement and capture instructions.

New messages, proposals, due responses/payments, breaches, and due adjudications are attention wake events. A sleep directive cannot hide a channel obligation.

## Configuration and Bounds

Per-civ participation:

```yaml
channels:
  enabled: true
```

`ChannelOptions(enabled=False)` is included in `CivOptions.fingerprint()`.

Run-wide rules are a single `channel_rules` object and cannot differ by civ:

| Rule | Default/bound |
|---|---|
| acceptance window | 3 turns |
| funding initiation | 2 turns |
| payment response | 2 turns |
| completion window | 1–30 turns |
| active deals | 3 per ordered pair |
| payment | 1–10,000 gold |
| message text | 2,000 characters |
| narrative text | 1,000 characters |
| persisted messages | 200 per ordered pair |
| prompt messages | newest 10 per counterpart |
| recent terminal deals | 5 |
| zone distance | 1–10 hexes |
| grievance half-life | 30 turns |
| prompt grievance threshold | 0.05 effective magnitude |
| queued action size | 8 KiB (used by the human follow-on) |

The enabled-player set and normalized rules are fingerprinted into canonical state. Resume with incompatible rules fails before another turn is admitted.

Bounds are enforced before an event is appended. The 200-message limit is a hard per-ordered-pair send limit for schema version 1: additional messages are rejected with an acknowledgement rather than pruning audit state. Proposed and active deals both count toward the active-deal limit until they become terminal.

## Persistence and Recovery

- `channels/events.jsonl` is the write-ahead event journal.
- `channels/state.json` is the complete atomic snapshot: ledger, monitor evidence, applied source IDs, queue cursor reservation, counters, rule fingerprint, and last event sequence.
- Commit order is append+flush event, reduce in memory, atomically replace state.
- Startup loads the snapshot and replays journal events with higher sequence numbers.
- Unknown schema, corrupt state, or an impossible event fails closed before turn admission; it never silently starts an empty ledger.
- Projection/render/model failures fail open for gameplay and surface explicit errors.

The runtime creates `channels/` with owner-only directory permissions and writes canonical state, journals, projections, queues, master memory, and run-local channel logs as owner-readable/writable files. Follow-on processes never widen those permissions.

The journal retains full history when prompt projections reach their caps.

## Testing and Live Gate

Offline coverage includes:

- table-driven lifecycle branches for both payment timings and every responsible-party default;
- pure endpoint/continuous verifier fixtures, transient violations, missing-observation outcomes, and sanitized evidence;
- privacy property-style tests with secret canaries;
- API actor spoofing, option gating, and tool/result source IDs;
- CLI JSON parsing, ordering, malformed isolation, raw-summary capture, and replay;
- intent/result crash recovery at every game-side-effect boundary;
- eight-player maximum-shape tests proving bounded projections and constant observation-query count;
- coordinator injection/capture/wake wiring;
- fingerprint and incompatible-resume rejection.

The attended core live gate uses at least one API and one CLI seat. It exercises up-front and on-delivery payments, one honored and one breached deal, one continuous strategic term, restart with a payment pending, and a third-party privacy canary.

Analysis reports channel activity per player/pair, payment progression, terminal outcome, grievance magnitude, and adjudication source.

## Non-Goals

- No writes to official Civ 6 grievance/justification state.
- No parallel currency or direct gold mutation.
- No automatic gossip to third parties.
- No model adjudication when `master.mode` is off.
- No synchronous same-round bargaining.
- No web server or human queue consumer in the core slice.
- No game-master inference in the core slice.
