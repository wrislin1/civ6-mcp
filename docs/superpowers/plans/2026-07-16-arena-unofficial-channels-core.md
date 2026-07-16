# Arena Unofficial Channels Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add coordinator-owned private bilateral messages, deterministic commitments, exact Civ 6 gold settlement, evidence-backed outcomes, and decaying unofficial grievances.

**Architecture:** `ChannelRuntime` is the only writer. It journals typed events before reducing them into an atomic snapshot, stages API and CLI requests until the coordinator's post-policy boundary, gathers one union observation per admission stage, and delegates pure term decisions to a closed registry. Policy adapters only expose or capture typed requests; neither models nor the generic arena tool registry can mutate channel state.

**Tech Stack:** Python 3.12, frozen dataclasses and `StrEnum`, pytest/pytest-asyncio, FireTuner InGame Lua, JSONL write-ahead journal, atomic JSON snapshots.

**Spec:** `docs/superpowers/specs/2026-07-09-arena-unofficial-channels-design.md` (read it before Task 1).

## Global Constraints

- Schema version is exactly `1`; IDs are monotonic `msg-000001`, `deal-000001`, `grv-000001`, `obs-000001`, and `evt-000001` within one run.
- The deterministic channel runtime is the source of truth. Models can request actions but cannot supply actors, payment evidence, verdicts, or grievances.
- Gold moves only through an exact gold-only Civ 6 pending trade; gold deltas and prose are never payment evidence.
- Acceptance, funding, payment response, and favor completion are separate states. A deal is honored only when favor and payment are both complete.
- Inclusive defaults are: acceptance 3 turns, funding 2 turns, payment response 2 turns, completion `within` 1–30 turns, grievance half-life 30 turns.
- Bounds are: 3 active deals per ordered pair, 1–10,000 gold, 2,000 message characters, 1,000 narrative characters, 200 persisted messages per ordered pair, 10 prompt messages per counterpart, 5 recent terminal deals, zone distance 1–10, prompt grievance threshold 0.05, and queued action size 8 KiB.
- Core mode rejects `term_type: narrative`; only the game-master follow-on may activate it.
- Missing required observations end an otherwise undecidable obligation as `unverifiable` with no grievance. An already-observed success or violation remains decisive.
- Privacy filtering happens on typed records before formatting. Third-party canaries must not appear in a player's projection, prompt block, acknowledgement, or transcript fields.
- Channel actions are not added to `TOOL_REGISTRY`; API actions use a bound `ChannelTurnContext`, and CLI actions use exact `CHANNEL {json}` lines from raw `transcript.final_summary`.
- New coordinator components catch `Exception` and fail open for gameplay, while `BaseException` cancellation still propagates to existing human-seat cleanup.
- Runtime files/directories use owner-only permissions (`0o700` directories, `0o600` files); journal appends are flushed and `fsync`ed before reduction/snapshot.
- Run tests as `uv run pytest tests/ -q`; no bare repository-wide collection outside `tests/`.
- End state is an unmerged local branch `arena-unofficial-channels-core`, created in an isolated worktree with `superpowers:using-git-worktrees`. Do not push or merge without riz's direction.

## Stable Interfaces

`src/civ_mcp/arena/config.py` exports:

```python
@dataclass(frozen=True)
class ChannelOptions:
    enabled: bool = False


@dataclass(frozen=True)
class ChannelRules:
    acceptance_turns: int = 3
    funding_turns: int = 2
    payment_response_turns: int = 2
    max_completion_turns: int = 30
    max_active_deals_per_pair: int = 3
    max_payment_gold: int = 10_000
    max_message_chars: int = 2_000
    max_narrative_chars: int = 1_000
    max_messages_per_pair: int = 200
    prompt_messages_per_counterpart: int = 10
    recent_terminal_deals: int = 5
    max_zone_distance: int = 10
    grievance_half_life_turns: int = 30
    prompt_grievance_threshold: float = 0.05
    max_queued_action_bytes: int = 8 * 1024

    def fingerprint(self) -> dict[str, int | float]: ...
```

`src/civ_mcp/arena/channel_protocol.py` exports `ChannelAction`, `ParsedChannelLine`, `ChannelTurnContext`, `parse_channel_action`, `parse_cli_channel_lines`, and `channel_tool_schemas` with the signatures defined in Task 3.

`src/civ_mcp/arena/channel_terms.py` exports `ObservationRequest`, `ChannelObservation`, `Verification`, `TermSpec`, `TERM_REGISTRY`, `compile_observation_request`, `capture_baseline`, and `verify_term` with the signatures defined in Tasks 4–7.

`src/civ_mcp/lua/channel_payments.py` exports:

```python
@dataclass(frozen=True)
class ExactPaymentOffer:
    payer: int
    payee: int
    gold: int
    duration: int = 0
    item_count: int = 1

    def fingerprint(self) -> dict[str, int]: ...

def build_channel_payment_offer(payee: int, gold: int) -> str: ...
def build_channel_payment_query(payer: int, gold: int) -> str: ...
def parse_channel_payment_query(lines: list[str]) -> ExactPaymentOffer | None: ...
def build_channel_payment_response(payer: int, gold: int, accept: bool) -> str: ...
```

The parser returns an offer only when payer, current player as payee, amount, zero duration, and exactly one lump-sum gold item match; every other pending-deal shape returns `None`.

`src/civ_mcp/arena/channel_runtime.py` exports:

```python
@dataclass(frozen=True)
class ChannelAdmission:
    player_id: int
    turn: int
    observation_id: str | None
    projection: ChannelProjection
    block: str
    context: ChannelTurnContext
    wake_reasons: tuple[str, ...]


class ChannelRuntime:
    @classmethod
    def open(cls, run_dir: Path, run_id: str, enabled_players: frozenset[int],
             rules: ChannelRules) -> "ChannelRuntime": ...
    async def admit_player(self, gs: Any, player_id: int, turn: int) -> ChannelAdmission: ...
    async def finish_player(self, gs: Any, admission: ChannelAdmission,
                            policy_result: dict | None) -> tuple[ChannelAcknowledgement, ...]: ...
    async def poll_unseated(self, gs: Any, turn: int, local_player_id: int | None) -> None: ...
    async def apply_staged(self, gs: Any, staged: StagedChannelAction, *, turn: int,
                           observation: ChannelObservation | None) -> ChannelAcknowledgement: ...
    def deal(self, deal_id: str) -> Deal: ...
    def project_for_player(self, player_id: int, turn: int) -> ChannelProjection: ...
```

`run_arena` gains an optional keyword-only `channel_runtime: ChannelRuntime | None = None`. Production core runs open one when absent; follow-on slices may pre-open and inject the same object so the master and human surface share exactly one writer.

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/civ_mcp/arena/channels.py` | Create | Immutable records, reducer, serialization, projections, formatting, grievance decay |
| `src/civ_mcp/arena/channel_protocol.py` | Create | Typed request union, validation, schemas, CLI parsing, bound turn context/source IDs |
| `src/civ_mcp/arena/channel_terms.py` | Create | Observation DTOs, registry, baselines, closed deterministic verifier catalog |
| `src/civ_mcp/arena/channel_runtime.py` | Create | Single writer, WAL/snapshot, lifecycle, observations, payments, recovery |
| `src/civ_mcp/lua/channel_observation.py` | Create | One targeted InGame observation query and parser |
| `src/civ_mcp/lua/channel_payments.py` | Create | Exact linked payment offer/query/response builders |
| `src/civ_mcp/lua/__init__.py`, `src/civ_mcp/game_state.py` | Modify | Export and wrap channel-only Lua operations |
| `src/civ_mcp/arena/config.py`, `src/civ_mcp/arena/experiment.py` | Modify | Channel options/rules, YAML parsing, validation, fingerprints |
| `src/civ_mcp/arena/agent.py`, `src/civ_mcp/arena/cli_agent.py` | Modify | Context-bound API schemas and raw CLI capture instructions |
| `src/civ_mcp/arena/prompting.py` | Modify | Fixed `channel_block` slot before the reserved master slot |
| `src/civ_mcp/arena/coordinator.py` | Modify | Runtime lifecycle and admission/finish wiring only |
| `src/civ_mcp/arena/attention.py` | Modify | Channel wake reason integration without changing scan semantics |
| `src/civ_mcp/arena/analyze.py` | Modify | Per-player/pair activity, payment, outcome, grievance, source summaries |
| `src/civ_mcp/arena/playbook.md` | Modify | Concise channel syntax and deterministic/non-binding distinction |
| `tests/arena/test_channels.py` | Create | Reducer, projection, privacy, decay, serialization |
| `tests/arena/test_channel_protocol.py` | Create | Typed action validation, schemas, CLI source ordering/replay IDs |
| `tests/arena/test_channel_terms.py` | Create | Registry and all pure verifier branches |
| `tests/arena/test_channel_runtime.py` | Create | Persistence, lifecycle, payment, recovery, bounds |
| `tests/test_channel_lua.py` | Create | Lua output/parser and exact payment builder contracts |
| Existing arena tests | Modify | Config, policy, prompt, coordinator, attention, analysis regressions |
| `experiments/arena-channels-core-smoke.yaml` | Create | Attended API+CLI core gate |
| `docs/superpowers/plans/2026-07-16-arena-unofficial-channels-core-live-gate.md` | Create | Exact attended evidence checklist |

## Dependency Order and Spec Coverage

Tasks 1–3 define configuration/state/protocol. Task 4 adds authoritative observations. Tasks 5–7 implement the term catalog. Tasks 8–9 add persistence, lifecycle, and exact payment recovery. Task 10 exposes policy entry paths. Task 11 wires the coordinator. Task 12 adds analysis and the attended gate.

| Approved design area | Task |
|---|---|
| Config, bounds, fingerprints, incompatible resume | 1, 8 |
| Canonical records, IDs, decay, typed privacy projections | 2 |
| API/CLI actions, spoof prevention, idempotent source IDs | 3, 10 |
| Union observations, sanitized evidence, missing-data policy | 4–7 |
| Full deterministic catalog | 5–7 |
| Proposal/acceptance/deadline lifecycle | 8 |
| Exact up-front/on-delivery payment and crash recovery | 9 |
| Prompt order, wake events, coordinator admission/capture | 10–11 |
| Eight-player bounds, analysis, API+CLI live gate | 12 |

---

### Task 1: Channel configuration, YAML parsing, and fingerprints

**Files:**
- Modify: `src/civ_mcp/arena/config.py:31-196`
- Modify: `src/civ_mcp/arena/experiment.py:13-410`
- Test: `tests/arena/test_config.py`
- Test: `tests/arena/test_experiment.py`

**Interfaces:**
- Consumes: existing `CivOptions.fingerprint()`, `_parse_civ`, `load_experiment`, `validate_arena_config`.
- Produces: `ChannelOptions`, `ChannelRules`, `CivOptions.channels`, `ArenaConfig.channel_rules`, `channel_config_fingerprint(config)`.

- [ ] **Step 1: Write failing dataclass and fingerprint tests**

Append to `tests/arena/test_config.py`:

```python
from dataclasses import replace

from civ_mcp.arena.config import (
    ArenaConfig, ChannelOptions, ChannelRules, CivOptions, PlayerSpec,
    channel_config_fingerprint,
)


def test_channel_defaults_are_off_and_fingerprinted():
    opts = CivOptions()
    assert opts.channels == ChannelOptions(enabled=False)
    assert opts.fingerprint()["channels"] == {"enabled": False}


def test_channel_rules_defaults_and_enabled_set_are_canonical():
    cfg = ArenaConfig(players=[
        PlayerSpec(2, "local", "m", options=CivOptions(channels=ChannelOptions(True))),
        PlayerSpec(1, "local", "m"),
    ])
    fp = channel_config_fingerprint(cfg)
    assert fp["schema_version"] == 1
    assert fp["enabled_players"] == [2]
    assert fp["rules"] == ChannelRules().fingerprint()
    assert channel_config_fingerprint(replace(cfg, players=list(reversed(cfg.players)))) == fp
```

- [ ] **Step 2: Write failing YAML validation tests**

Append to `tests/arena/test_experiment.py`:

```python
def test_loads_per_civ_channels_and_run_wide_rules(tmp_path):
    path = tmp_path / "channels.yaml"
    path.write_text("""
channel_rules:
  acceptance_turns: 3
  grievance_half_life_turns: 30
civs:
  - player: 1
    provider: local
    model: m
    channels: {enabled: true}
  - player: 2
    provider: cli-codex
    model: gpt-5
    channels: {enabled: false}
""")
    cfg = load_experiment(path)
    assert cfg.players[0].options.channels.enabled is True
    assert cfg.players[1].options.channels.enabled is False
    assert cfg.channel_rules == ChannelRules()


@pytest.mark.parametrize("fragment, match", [
    ("channels: {enabled: yes}", "channels.enabled must be a boolean"),
    ("channel_rules: {max_payment_gold: 10001}", "max_payment_gold must be 1..10000"),
    ("channel_rules: {max_completion_turns: 31}", "max_completion_turns must be 1..30"),
    ("channel_rules: {max_zone_distance: 0}", "max_zone_distance must be 1..10"),
])
def test_rejects_invalid_channel_config(tmp_path, fragment, match):
    path = tmp_path / "bad.yaml"
    if fragment.startswith("channels"):
        text = f"civs:\n  - {{player: 1, provider: local, model: m, {fragment}}}\n"
    else:
        text = f"{fragment}\ncivs:\n  - {{player: 1, provider: local, model: m}}\n"
    path.write_text(text)
    with pytest.raises(ValueError, match=match):
        load_experiment(path)
```

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_config.py tests/arena/test_experiment.py -q`

Expected: collection/import failures for `ChannelOptions`, `ChannelRules`, and `channel_config_fingerprint`.

- [ ] **Step 4: Implement exact config records and parser wiring**

Add to `config.py`, include `channels: ChannelOptions` in `CivOptions`, include it in `fingerprint()`, and add `channel_rules: ChannelRules` to `ArenaConfig`:

```python
@dataclass(frozen=True)
class ChannelOptions:
    enabled: bool = False


@dataclass(frozen=True)
class ChannelRules:
    acceptance_turns: int = 3
    funding_turns: int = 2
    payment_response_turns: int = 2
    max_completion_turns: int = 30
    max_active_deals_per_pair: int = 3
    max_payment_gold: int = 10_000
    max_message_chars: int = 2_000
    max_narrative_chars: int = 1_000
    max_messages_per_pair: int = 200
    prompt_messages_per_counterpart: int = 10
    recent_terminal_deals: int = 5
    max_zone_distance: int = 10
    grievance_half_life_turns: int = 30
    prompt_grievance_threshold: float = 0.05
    max_queued_action_bytes: int = 8 * 1024

    def fingerprint(self) -> dict[str, int | float]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def channel_config_fingerprint(config: ArenaConfig) -> dict:
    return {
        "schema_version": 1,
        "enabled_players": sorted(
            spec.player_id for spec in config.players if spec.options.channels.enabled
        ),
        "rules": config.channel_rules.fingerprint(),
    }
```

In `experiment.py`, add `channels` to `_SHARED_KNOBS`/`_CIV_KEYS`, `channel_rules` to `_TOP_KEYS`, implement `_parse_channels` with strict `enabled: bool`, implement `_parse_channel_rules` with the exact bounds above, pass `channels=` through both local and CLI `CivOptions` constructors, and pass `channel_rules=` through `replace(...)` in `load_experiment`.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_config.py tests/arena/test_experiment.py -q`

Expected: all focused tests pass.

```bash
git add src/civ_mcp/arena/config.py src/civ_mcp/arena/experiment.py tests/arena/test_config.py tests/arena/test_experiment.py
git commit -m "feat(arena): add unofficial channel configuration"
```

### Task 2: Canonical records, reducer, serialization, projections, and decay

**Files:**
- Create: `src/civ_mcp/arena/channels.py`
- Create: `tests/arena/test_channels.py`

**Interfaces:**
- Consumes: `ChannelRules` from Task 1.
- Produces: `Message`, `FavorTerm`, `Deal`, `Grievance`, `ChannelAcknowledgement`, `ChannelEvent`, `ChannelState`, `ChannelProjection`, `initial_channel_state`, `reduce_event`, `state_to_dict`, `state_from_dict`, `project_for_player`, `project_all`, `format_channel_block`.

- [ ] **Step 1: Write failing reducer, privacy, and decay tests**

Create `tests/arena/test_channels.py` with these core tests:

```python
import math

from civ_mcp.arena.channels import (
    ChannelEvent, ChannelState, effective_magnitude, format_channel_block,
    initial_channel_state, project_for_player, reduce_event, state_from_dict,
    state_to_dict,
)
from civ_mcp.arena.config import ChannelRules


def _event(seq: int, kind: str, payload: dict) -> ChannelEvent:
    return ChannelEvent(1, f"evt-{seq:06d}", seq, kind, payload)


def test_reducer_assigns_records_from_event_payload_and_round_trips():
    state = initial_channel_state("run-a", frozenset({1, 2, 3}), ChannelRules())
    state = reduce_event(state, _event(1, "message_sent", {
        "id": "msg-000001", "from_player": 1, "to_player": 2,
        "turn": 4, "text": "canary-a-b", "deal_id": None,
    }))
    restored = state_from_dict(state_to_dict(state))
    assert restored == state
    assert restored.next_message == 2
    assert restored.last_event_sequence == 1


def test_projection_filters_typed_records_before_rendering():
    state = initial_channel_state("run-a", frozenset({1, 2, 3}), ChannelRules())
    for seq, sender, recipient, text in [
        (1, 1, 2, "secret-12"), (2, 2, 3, "secret-23"), (3, 3, 1, "secret-31")
    ]:
        state = reduce_event(state, _event(seq, "message_sent", {
            "id": f"msg-{seq:06d}", "from_player": sender,
            "to_player": recipient, "turn": 10, "text": text, "deal_id": None,
        }))
    p1 = project_for_player(state, 1, current_turn=10)
    rendered = format_channel_block(p1)
    assert "secret-12" in rendered and "secret-31" in rendered
    assert "secret-23" not in rendered


def test_grievance_magnitude_has_fixed_formula_and_threshold():
    assert effective_magnitude(1.0, created_turn=10, current_turn=40, half_life_turns=30) == 0.5
    assert math.isclose(
        effective_magnitude(10.0, created_turn=10, current_turn=70, half_life_turns=30),
        2.5,
    )
```

- [ ] **Step 2: Run the new test and confirm RED**

Run: `uv run pytest tests/arena/test_channels.py -q`

Expected: import failure because `civ_mcp.arena.channels` does not exist.

- [ ] **Step 3: Implement immutable schema and reducer**

Create `channels.py` with `SCHEMA_VERSION = 1`, string-valued `DealState`, `FavorStatus`, and `PaymentStatus` enums, frozen records matching the spec, and this state envelope:

```python
@dataclass(frozen=True)
class ChannelState:
    schema_version: int
    run_id: str
    enabled_players: frozenset[int]
    rules_fingerprint: dict[str, int | float]
    messages: tuple[Message, ...] = ()
    deals: tuple[Deal, ...] = ()
    grievances: tuple[Grievance, ...] = ()
    acknowledgements: tuple[ChannelAcknowledgement, ...] = ()
    observations: tuple[dict, ...] = ()
    applied_source_ids: frozenset[str] = frozenset()
    queue_cursor: int = 0
    queue_reservation: dict | None = None
    applied_request_ids: frozenset[str] = frozenset()
    privacy_contaminated: bool = False
    next_message: int = 1
    next_deal: int = 1
    next_grievance: int = 1
    next_observation: int = 1
    next_event: int = 1
    last_event_sequence: int = 0
```

`reduce_event` must use a closed `match event.kind` over `message_sent`, `deal_proposed`, `deal_changed`, `grievance_created`, `acknowledged`, `observation_recorded`, `source_applied`, `queue_advanced`, and `privacy_contaminated`; reject unknown kinds, non-consecutive sequences, duplicate IDs, illegal enum transitions, and IDs that do not match the current counters.

- [ ] **Step 4: Implement typed projections and bounded rendering**

Use this filtering rule before any prose rendering:

```python
def project_for_player(state: ChannelState, player_id: int, current_turn: int) -> ChannelProjection:
    if player_id not in state.enabled_players:
        return ChannelProjection(player_id=player_id)
    messages = tuple(m for m in state.messages if player_id in (m.from_player, m.to_player))
    deals = tuple(d for d in state.deals if player_id in (d.proposer, d.counterparty))
    grievances = tuple(g for g in state.grievances if player_id in (g.wronged, g.offender))
    acknowledgements = tuple(a for a in state.acknowledgements if a.player_id == player_id)
    return _bound_projection(
        ChannelProjection(player_id, messages, deals, grievances, acknowledgements),
        state.rules_fingerprint, current_turn,
    )
```

`_bound_projection` keeps the newest 10 messages per counterpart, all unresolved deals, newest 5 terminal deals, grievances whose effective magnitude is at least 0.05, and newest 20 acknowledgements. `project_all` returns unbounded typed records only for explicit analyst/director consumers. `format_channel_block` renders only fields present in the projection and includes exact CLI action examples when `projection.cli_instructions` is true.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channels.py -q`

Expected: all tests pass.

```bash
git add src/civ_mcp/arena/channels.py tests/arena/test_channels.py
git commit -m "feat(arena): add canonical unofficial channel state"
```

### Task 3: Typed action protocol, source IDs, API schemas, and CLI parsing

**Files:**
- Create: `src/civ_mcp/arena/channel_protocol.py`
- Create: `tests/arena/test_channel_protocol.py`

**Interfaces:**
- Consumes: `ChannelRules`; this task performs envelope validation only and never appends an event.
- Produces: `ChannelAction` union, `StagedChannelAction`, `ParsedChannelLine`, `ChannelTurnContext`, `parse_channel_action`, `parse_cli_channel_lines`, `channel_tool_schemas`.

- [ ] **Step 1: Write failing actor, bound, CLI, and idempotency tests**

Create `tests/arena/test_channel_protocol.py`:

```python
import json
import pytest

from civ_mcp.arena.channel_protocol import (
    ChannelTurnContext, parse_channel_action, parse_cli_channel_lines,
)
from civ_mcp.arena.config import ChannelRules


def test_actor_is_bound_and_never_accepted_from_model_args():
    with pytest.raises(ValueError, match="unknown field.*actor"):
        parse_channel_action(
            "send_message", {"actor": 7, "to_player": 2, "text": "x"},
            actor=1, enabled_players=frozenset({1, 2}), rules=ChannelRules(),
        )


def test_message_and_deal_bounds_are_checked_before_staging():
    with pytest.raises(ValueError, match="message text must be 1..2000"):
        parse_channel_action(
            "send_message", {"to_player": 2, "text": "x" * 2001},
            actor=1, enabled_players=frozenset({1, 2}), rules=ChannelRules(),
        )
    with pytest.raises(ValueError, match="within must be 1..30"):
        parse_channel_action(
            "propose_deal", {"to_player": 2, "text": "camp", "favor": {
                "term_type": "destroy_camp", "params": {"x": 4, "y": 5}},
                "payment_gold": 100, "timing": "on_delivery", "within": 31},
            actor=1, enabled_players=frozenset({1, 2}), rules=ChannelRules(),
        )


def test_cli_parser_isolates_bad_lines_and_has_deterministic_source_ids():
    summary = "\n".join([
        'CHANNEL {"action":"send_message","to_player":2,"text":"hello"}',
        "CHANNEL not-json",
        'CHANNEL {"action":"respond_to_deal","deal_id":"deal-000001","accept":true}',
    ])
    first = parse_cli_channel_lines(
        summary, run_id="r", actor=1, turn=9,
        enabled_players=frozenset({1, 2}), rules=ChannelRules(),
    )
    second = parse_cli_channel_lines(
        summary, run_id="r", actor=1, turn=9,
        enabled_players=frozenset({1, 2}), rules=ChannelRules(),
    )
    assert [line.source_id for line in first] == [line.source_id for line in second]
    assert [line.error for line in first] == ["", "invalid CHANNEL JSON", ""]


def test_context_stages_in_source_order_without_mutating_runtime():
    ctx = ChannelTurnContext("r", 1, 7, frozenset({1, 2}), ChannelRules())
    result = ctx.dispatch("send_message", {"to_player": 2, "text": "hello"})
    assert result.startswith("QUEUED channel action")
    assert len(ctx.staged_actions) == 1
    assert ctx.staged_actions[0].source_id.startswith("api:")
```

- [ ] **Step 2: Run the new tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_protocol.py -q`

Expected: import failure because `channel_protocol.py` does not exist.

- [ ] **Step 3: Implement the exact action union and validation**

Use frozen records with no actor field in their public argument schemas:

```python
@dataclass(frozen=True)
class SendMessage:
    to_player: int
    text: str

@dataclass(frozen=True)
class ProposeDeal:
    to_player: int
    text: str
    favor: dict
    payment_gold: int
    timing: Literal["up_front", "on_delivery"]
    within: int

@dataclass(frozen=True)
class RespondToDeal:
    deal_id: str
    accept: bool

@dataclass(frozen=True)
class FundDeal:
    deal_id: str

@dataclass(frozen=True)
class RespondToPayment:
    deal_id: str
    accept: bool

ChannelAction = SendMessage | ProposeDeal | RespondToDeal | FundDeal | RespondToPayment
```

`parse_channel_action` must reject unknown keys, bool-as-int values, self-targeting, disabled/unknown counterparties, malformed IDs, blank text, malformed favor envelopes, unknown core term names, and `narrative`. It must enforce the exact global bounds before constructing a record. Add an optional `term_validator: Callable[[dict], dict] | None` parameter to `parse_channel_action` and `ChannelTurnContext`; Task 5 supplies the registry validator for exact per-term parameters before any action reaches persistence. Until then, this task's default validates only `{"term_type": <closed core name>, "params": <object>}`.

- [ ] **Step 4: Implement schemas, source IDs, and staged context**

Define:

```python
def parse_cli_channel_lines(summary: str, *, run_id: str, actor: int, turn: int,
                            enabled_players: frozenset[int], rules: ChannelRules,
                            narrative_allowed: bool = False) -> tuple[ParsedChannelLine, ...]: ...

def channel_tool_schemas(*, narrative_allowed: bool = False) -> list[dict]: ...

@dataclass
class ChannelTurnContext:
    run_id: str
    player_id: int
    turn: int
    enabled_players: frozenset[int]
    rules: ChannelRules
    narrative_allowed: bool = False
    term_validator: Callable[[dict], dict] | None = None
    staged_actions: list[StagedChannelAction] = field(default_factory=list)

    def dispatch(self, name: str, args: dict) -> str:
        action = parse_channel_action(
            name, args, actor=self.player_id, enabled_players=self.enabled_players,
            rules=self.rules, narrative_allowed=self.narrative_allowed,
            term_validator=self.term_validator,
        )
        index = len(self.staged_actions)
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        source_id = f"api:{self.run_id}:{self.player_id}:{self.turn}:{index}:{digest}"
        self.staged_actions.append(StagedChannelAction(source_id, self.player_id, action))
        return f"QUEUED channel action {source_id}; canonical result appears next turn"
```

CLI IDs use `cli:{run_id}:{actor}:{turn}:{line_index}:{sha256(line)[:16]}`. Every physical `CHANNEL ` line creates one `ParsedChannelLine`; malformed lines retain their line index and error so later valid lines remain ordered.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channel_protocol.py -q`

Expected: all tests pass.

```bash
git add src/civ_mcp/arena/channel_protocol.py tests/arena/test_channel_protocol.py
git commit -m "feat(arena): add typed unofficial channel protocol"
```

### Task 4: Authoritative union observations and exact channel payment Lua

**Files:**
- Create: `src/civ_mcp/lua/channel_observation.py`
- Create: `src/civ_mcp/lua/channel_payments.py`
- Modify: `src/civ_mcp/lua/__init__.py`
- Modify: `src/civ_mcp/game_state.py:924-959`
- Create: `tests/test_channel_lua.py`

**Interfaces:**
- Consumes: `GameConnection.execute_write`, existing Civ 6 route fields `DestinationCityPlayer`/`DestinationCityID`, existing pending-deal APIs.
- Produces: `ObservationRequest`, `ChannelObservation`, Lua builders/parsers, `GameState.get_channel_observation`, `GameState.offer_channel_payment`, `GameState.get_channel_payment_offer`, `GameState.respond_to_channel_payment`.

- [ ] **Step 1: Write failing parser and exact-builder tests**

Create `tests/test_channel_lua.py`:

```python
from civ_mcp.arena.channel_terms import ObservationFamily, ObservationRequest
from civ_mcp.lua.channel_observation import (
    build_channel_observation_query, parse_channel_observation_response,
)
from civ_mcp.lua.channel_payments import (
    build_channel_payment_offer, build_channel_payment_response,
)


def test_observation_parser_preserves_stable_ids_categories_and_route_owner():
    request = ObservationRequest(
        families=frozenset({ObservationFamily.UNITS, ObservationFamily.TRADE_ROUTES}),
        protected_players=(2,), zone_centers=((12, 7),),
    )
    obs = parse_channel_observation_response(1, 44, request, [
        "UNIT|1|17|UNIT_MILITARY_ENGINEER|FORMATION_CLASS_SUPPORT|0|9|8",
        "DIST|1|17|2|3",
        "ROUTE|1|22|5|1",
        "---END---",
    ])
    assert obs.units[0].unit_id == 17
    assert obs.units[0].formation_class == "FORMATION_CLASS_SUPPORT"
    assert obs.unit_distances[(1, 17, 2)] == 3
    assert obs.trade_routes[0].destination_player == 5
    assert obs.trade_routes[0].destination_is_city_state is True


def test_observation_builder_is_one_query_for_a_union_request():
    request = ObservationRequest(
        families=frozenset(ObservationFamily), protected_players=(2, 3),
        zone_centers=((12, 7), (20, 9)),
    )
    lua = build_channel_observation_query(1, request)
    assert "Players[1]:GetUnits():Members()" in lua
    assert "Map.GetPlotDistance" in lua
    assert "DestinationCityPlayer" in lua
    assert lua.count('print("---END---")') == 1


def test_payment_builders_are_exact_gold_only_and_never_accept_counteroffers():
    offer = build_channel_payment_offer(2, 100)
    assert "DealItemTypes.GOLD" in offer
    assert "SetAmount(100)" in offer
    assert "SetDuration(0)" in offer
    assert "DealProposalAction.PROPOSED" in offer
    assert "DealProposalAction.ACCEPTED" not in offer
    response = build_channel_payment_response(1, 100, True)
    assert "GetItemCount() ~= 1" in response
    assert "DealProposalAction.ACCEPTED" in response
```

- [ ] **Step 2: Run the new tests and confirm RED**

Run: `uv run pytest tests/test_channel_lua.py -q`

Expected: import failures for both new Lua modules and `channel_terms` DTOs.

- [ ] **Step 3: Add observation DTOs and the single targeted query**

Start `channel_terms.py` with `ObservationFamily` values `units`, `territory`, `cities`, `treasury`, `diplomacy`, `trade_routes`, `camps`, and `action_audit`. Define frozen `ObservedUnit`, `ObservedCity`, `ObservedRoute`, `ObservedAction`, `ObservationRequest`, and `ChannelObservation` records.

`build_channel_observation_query(player_id, request)` must validate every integer before interpolation and print only requested families. It iterates `Players[player_id]` units/cities/treasury/diplomacy, all map plots for territory/camps, and local outgoing routes. Unit formation comes from `GameInfo.Units[unit:GetType()].FormationClass`; religious strength is `ReligiousStrength or 0`; route destination IDs come from `r.DestinationCityPlayer`. For each unique protected player, print `DIST|owner|unit_id|protected|min(Map.GetPlotDistance(...))`. Print one sentinel.

- [ ] **Step 4: Add exact payment builders and GameState wrappers**

Implement these wrappers in `game_state.py`:

```python
async def get_channel_observation(self, player_id: int, turn: int,
                                  request: ObservationRequest) -> ChannelObservation:
    lines = await self.conn.execute_write(build_channel_observation_query(player_id, request))
    return parse_channel_observation_response(player_id, turn, request, lines)

async def offer_channel_payment(self, payee: int, gold: int) -> str:
    return _action_result(await self.conn.execute_write(build_channel_payment_offer(payee, gold)))

async def get_channel_payment_offer(self, payer: int, gold: int) -> ExactPaymentOffer | None:
    lines = await self.conn.execute_write(build_channel_payment_query(payer, gold))
    return parse_channel_payment_query(lines)

async def respond_to_channel_payment(self, payer: int, gold: int, accept: bool) -> str:
    lines = await self.conn.execute_write(build_channel_payment_response(payer, gold, accept))
    return _action_result(lines)
```

Define `ExactPaymentOffer` exactly as in Stable Interfaces. Its `fingerprint()` returns all five integer fields. The offer builder fails if any pending deal already exists for the ordered pair, clears only a new outgoing working deal, adds exactly one lump-sum gold item, and sends `PROPOSED`. Query/response require one item, payer/payee match, `GOLD`, exact amount, duration zero, and no other items. Export the DTO and all builders/parsers through `lua/__init__.py`.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/test_channel_lua.py tests/test_lua_injection_hardening.py -q`

Expected: all focused tests pass and injection-hardening regressions stay green.

```bash
git add src/civ_mcp/lua/channel_observation.py src/civ_mcp/lua/channel_payments.py src/civ_mcp/lua/__init__.py src/civ_mcp/game_state.py src/civ_mcp/arena/channel_terms.py tests/test_channel_lua.py
git commit -m "feat(arena): add authoritative channel observations and payments"
```

### Task 5: Term registry foundation and endpoint/condition verifiers

**Files:**
- Modify: `src/civ_mcp/arena/channel_terms.py`
- Create: `tests/arena/test_channel_terms.py`

**Interfaces:**
- Consumes: observation DTOs from Task 4.
- Produces: `TermMode`, `Verification`, `TermSpec`, registry entries for `destroy_camp`, `dont_settle_within`, `found_city_within`, `declare_war_on`, `keep_peace_with`, and `maintain_gold_reserve`.

- [ ] **Step 1: Write failing table-driven verifier tests**

Create `tests/arena/test_channel_terms.py` with a fixture constructor and these cases:

```python
import dataclasses
import pytest

from civ_mcp.arena.channel_terms import (
    ChannelObservation, ObservationFamily, ObservedAction, ObservedCity,
    ObservedRoute, ObservedUnit, TermValidationContext, capture_baseline,
    unit_categories, validate_term, verify_term,
)


def obs(turn: int, **changes) -> ChannelObservation:
    base = ChannelObservation(
        player_id=2, turn=turn, families_present=frozenset(ObservationFamily),
        units=(), cities=(), camps=frozenset(), territory=frozenset(),
        wars=frozenset(), treasury_gold=500, trade_routes=(), action_audit=(),
        unit_distances={}, zone_distances={}, errors=(),
    )
    return dataclasses.replace(base, **changes)


@pytest.mark.parametrize("term, before, after, expected", [
    ({"term_type": "destroy_camp", "params": {"x": 12, "y": 7}},
     obs(1, camps=frozenset({(12, 7)})), obs(2), "satisfied"),
    ({"term_type": "found_city_within", "params": {"x": 12, "y": 7, "radius": 3}},
     obs(1), obs(2, cities=(ObservedCity(2, 99, 13, 7),), zone_distances={(99, 12, 7): 1}), "satisfied"),
    ({"term_type": "declare_war_on", "params": {"player_id": 3}},
     obs(1), obs(2, wars=frozenset({(2, 3)})), "satisfied"),
    ({"term_type": "maintain_gold_reserve", "params": {"min_gold": 400}},
     obs(1), obs(2, treasury_gold=399), "failed"),
])
def test_registered_term_outcomes(term, before, after, expected):
    baseline = capture_baseline(term, before)
    result = verify_term(term, baseline, {}, after, due_turn=3)
    assert result.status == expected


def test_missing_required_family_is_unverifiable_only_at_deadline():
    term = {"term_type": "keep_peace_with", "params": {"player_id": 3}}
    missing = dataclasses.replace(obs(3), families_present=frozenset(), errors=("diplomacy",))
    assert verify_term(term, {}, {}, missing, due_turn=3).status == "unverifiable"


def test_camp_must_exist_when_proposed():
    with pytest.raises(ValueError, match="camp must exist at proposal"):
        capture_baseline(
            {"term_type": "destroy_camp", "params": {"x": 12, "y": 7}}, obs(1)
        )
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_terms.py -q`

Expected: imports fail for registry/verification symbols.

- [ ] **Step 3: Implement closed registry and parameter schemas**

Define:

```python
class TermMode(StrEnum):
    ENDPOINT = "endpoint"
    CONTINUOUS = "continuous"

@dataclass(frozen=True)
class Verification:
    status: Literal["pending", "satisfied", "failed", "unverifiable"]
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()
    monitor: dict = field(default_factory=dict)

@dataclass(frozen=True)
class TermSpec:
    term_type: str
    mode: TermMode
    baseline_phase: Literal["proposal", "favor_start"]
    families: frozenset[ObservationFamily]
    validate_params: Callable[[dict, TermValidationContext], dict]
    capture_baseline: Callable[[dict, ChannelObservation], dict]
    verify: Callable[[dict, dict, dict, ChannelObservation, int], Verification]
    render_evidence: Callable[[Verification], str]

@dataclass(frozen=True)
class TermValidationContext:
    obligated_player: int
    enabled_players: frozenset[int]
    city_state_players: frozenset[int] = frozenset()
    instrumented_trade_players: frozenset[int] = frozenset()
```

`validate_term` accepts only exact registry keys and strict parameter fields. Player targets must be legal, zone radius is 1–10, coordinates are integers (not bool), and `min_gold` is 0–10,000. `narrative` raises `ValueError("narrative terms require an active game master")`. `destroy_camp` uses `baseline_phase="proposal"`; every newly-created-city/unit and continuous term uses `baseline_phase="favor_start"`. Wire `validate_term` into the protocol's `term_validator` hook so final `ChannelTurnContext` staging rejects bad per-term parameters before the runtime sees them.

- [ ] **Step 4: Implement endpoint and continuous condition semantics**

For `dont_settle_within` and `keep_peace_with`, persist `monitor={"violation_observation_id": ...}` on the first violation and always return failed thereafter. `found_city_within` compares stable city IDs to the baseline and uses query-provided zone distances. `destroy_camp` requires presence at proposal and succeeds when a later complete camp observation omits the coordinate. `maintain_gold_reserve` fails on the first observed value below the accepted floor. Evidence renderers may state the condition and turn but must not reveal hidden coordinates beyond coordinates already present in the accepted term.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channel_terms.py -q`

Expected: all current term tests pass.

```bash
git add src/civ_mcp/arena/channel_terms.py tests/arena/test_channel_terms.py
git commit -m "feat(arena): add deterministic channel term registry"
```

### Task 6: Unit acquisition, cap, withdrawal, and exclusion verifiers

**Files:**
- Modify: `src/civ_mcp/arena/channel_terms.py`
- Modify: `tests/arena/test_channel_terms.py`

**Interfaces:**
- Consumes: stable per-owner unit IDs, formation class, religious strength, and query-provided unit-to-territory distances.
- Produces: registry entries `forbid_unit_acquisition`, `allow_only_unit_category`, `military_unit_cap`, `withdraw_units_from`, `keep_units_away`.

- [ ] **Step 1: Add failing unit-category and transient-violation tests**

Append:

```python
@pytest.mark.parametrize("formation,religious,categories", [
    ("FORMATION_CLASS_LAND_COMBAT", 0, {"military"}),
    ("FORMATION_CLASS_SUPPORT", 0, {"military"}),
    ("FORMATION_CLASS_CIVILIAN", 0, {"civilian"}),
    ("FORMATION_CLASS_CIVILIAN", 110, {"civilian", "religious"}),
])
def test_unit_category_is_definition_driven(formation, religious, categories):
    unit = ObservedUnit(2, 7, "UNIT_X", formation, religious, 1, 1)
    assert unit_categories(unit) == categories


def test_upgrade_same_identity_is_not_acquisition_but_new_identity_is():
    term = {"term_type": "forbid_unit_acquisition", "params": {"category": "military"}}
    old = ObservedUnit(2, 7, "UNIT_WARRIOR", "FORMATION_CLASS_LAND_COMBAT", 0, 1, 1)
    upgraded = ObservedUnit(2, 7, "UNIT_SWORDSMAN", "FORMATION_CLASS_LAND_COMBAT", 0, 1, 1)
    new = ObservedUnit(2, 8, "UNIT_ARCHER", "FORMATION_CLASS_LAND_COMBAT", 0, 2, 1)
    baseline = capture_baseline(term, obs(1, units=(old,)))
    assert verify_term(term, baseline, {}, obs(2, units=(upgraded,)), due_turn=4).status == "pending"
    assert verify_term(term, baseline, {}, obs(2, units=(upgraded, new)), due_turn=4).status == "failed"


def test_keep_units_away_has_one_turn_grace_and_persists_transient_violation():
    term = {"term_type": "keep_units_away", "params": {
        "player_id": 1, "min_distance": 3, "unit_scope": "military"}}
    unit = ObservedUnit(2, 7, "UNIT_WARRIOR", "FORMATION_CLASS_LAND_COMBAT", 0, 5, 5)
    baseline = capture_baseline(term, obs(10, units=(unit,), unit_distances={(2, 7, 1): 1}))
    grace = verify_term(term, baseline, {}, obs(11, units=(unit,), unit_distances={(2, 7, 1): 1}), 15)
    breach = verify_term(term, baseline, grace.monitor, obs(12, units=(unit,), unit_distances={(2, 7, 1): 1}), 15)
    left = verify_term(term, baseline, breach.monitor, obs(13, units=(unit,), unit_distances={(2, 7, 1): 5}), 15)
    assert (grace.status, breach.status, left.status) == ("pending", "failed", "failed")
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_terms.py -q`

Expected: failures for missing unit registry entries and `unit_categories`.

- [ ] **Step 3: Implement exact unit categories and acquisition rules**

Use exact formation sets:

```python
MILITARY_FORMATIONS = frozenset({
    "FORMATION_CLASS_LAND_COMBAT", "FORMATION_CLASS_NAVAL",
    "FORMATION_CLASS_AIR", "FORMATION_CLASS_SUPPORT",
})

def unit_categories(unit: ObservedUnit) -> frozenset[str]:
    values: set[str] = set()
    if unit.formation_class in MILITARY_FORMATIONS:
        values.add("military")
    if unit.formation_class == "FORMATION_CLASS_CIVILIAN":
        values.add("civilian")
        if unit.religious_strength > 0:
            values.add("religious")
    return frozenset(values)
```

Acquisition baselines store stable `(owner_id, unit_id)` identities. Training, purchase, grants, levies, and transfers all appear as a new identity and therefore count; a type change under the same identity does not. `allow_only_unit_category` rejects any new unit whose categories omit the accepted category. `military_unit_cap` checks immediately at favor start and records the first count above the cap.

- [ ] **Step 4: Implement withdrawal and continuous-distance semantics**

`withdraw_units_from` is satisfied only when every in-scope unit has a complete distance entry at or above `min_distance`; it stays pending until due and becomes unverifiable if distance data is incomplete at due. `keep_units_away` ignores violations through `favor_started_turn + 1`, then persists the first violation. `unit_scope=military` uses `unit_categories`; `unit_scope=all` includes all observed units. Sanitized evidence says only the protected player, scope, threshold, and violation turn—not unit ID, type, or coordinate.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channel_terms.py -q`

Expected: all unit and prior registry tests pass.

```bash
git add src/civ_mcp/arena/channel_terms.py tests/arena/test_channel_terms.py
git commit -m "feat(arena): verify strategic unit commitments"
```

### Task 7: Trade-route and diplomatic-deal verifiers

**Files:**
- Modify: `src/civ_mcp/arena/channel_terms.py`
- Modify: `tests/arena/test_channel_terms.py`

**Interfaces:**
- Consumes: active outgoing route records and normalized successful arena action-audit records.
- Produces: `dont_trade_with`, `send_trade_route_to`, `normalize_action_audit(policy_result, actor, turn)`.

- [ ] **Step 1: Add failing major/city-state/action-audit tests**

Append:

```python
def test_dont_trade_with_counts_successful_outgoing_deal_and_outgoing_route_only():
    term = {"term_type": "dont_trade_with", "params": {
        "target_player": 3, "trade_kinds": ["diplomatic_deal", "trade_route"]}}
    baseline = capture_baseline(term, obs(1))
    route = ObservedRoute(2, 11, 3, False)
    audit = ObservedAction(2, 2, "propose_trade", {"other_player_id": 3}, "OK:PROPOSED")
    assert verify_term(term, baseline, {}, obs(2, trade_routes=(route,)), 5).status == "failed"
    assert verify_term(term, baseline, {}, obs(2, action_audit=(audit,)), 5).status == "failed"


def test_rejected_trade_and_incoming_route_do_not_violate():
    term = {"term_type": "dont_trade_with", "params": {
        "target_player": 3, "trade_kinds": ["diplomatic_deal"]}}
    audit = ObservedAction(2, 2, "propose_trade", {"other_player_id": 3}, "OK:REJECTED")
    assert verify_term(term, capture_baseline(term, obs(1)), {}, obs(2, action_audit=(audit,)), 5).status == "pending"


def test_city_state_rejects_diplomatic_deal_kind():
    with pytest.raises(ValueError, match="city-state targets support trade_route only"):
        validate_term({"term_type": "dont_trade_with", "params": {
            "target_player": 55, "trade_kinds": ["diplomatic_deal"]}},
            TermValidationContext(obligated_player=2,
                enabled_players=frozenset({1, 2}), city_state_players=frozenset({55}),
                instrumented_trade_players=frozenset({1, 2})))


def test_send_trade_route_to_is_endpoint_success():
    term = {"term_type": "send_trade_route_to", "params": {"target_player": 3}}
    result = verify_term(
        term, capture_baseline(term, obs(1)), {},
        obs(2, trade_routes=(ObservedRoute(2, 11, 3, False),)), due_turn=4,
    )
    assert result.status == "satisfied"
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_terms.py -q`

Expected: registry failures for the two trade terms.

- [ ] **Step 3: Implement trade parameter and instrumentation validation**

`trade_kinds` must be a non-empty unique subset of `{"diplomatic_deal", "trade_route"}`. City-state targets accept only `trade_route`. Reject `diplomatic_deal` when `TermValidationContext.instrumented_trade_players` does not contain the obligated player; this prevents direct human UI trading from being treated as fully observable.

`normalize_action_audit` accepts transcript steps only when `tool_name` is `propose_trade` or `respond_to_trade`, `tool_args` identify the target, and `tool_result_full` proves success (`OK:PROPOSED`, `OK:ACCEPTED`, or an accepted incoming response). It emits immutable `ObservedAction` records; rejected/test-mode attempts are omitted.

- [ ] **Step 4: Implement continuous and endpoint trade verification**

`dont_trade_with` compares audit/route identities against baseline and persists the first violation. Only routes whose owner is the obligated player and destination is the target count. `send_trade_route_to` succeeds on a matching active route at any observation through the inclusive due turn. Evidence names the target and kind but never includes unrelated deal contents.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channel_terms.py -q`

Expected: complete deterministic registry suite passes.

```bash
git add src/civ_mcp/arena/channel_terms.py tests/arena/test_channel_terms.py
git commit -m "feat(arena): verify unofficial trade commitments"
```

### Task 8: Secure WAL/snapshot runtime and non-payment deal lifecycle

**Files:**
- Create: `src/civ_mcp/arena/channel_runtime.py`
- Create: `tests/arena/test_channel_runtime.py`

**Interfaces:**
- Consumes: Tasks 1–7 state, protocol, and verifiers.
- Produces: secure `ChannelRuntime.open`, `_commit`, staged action application, admission/finish observations, deadlines, grievances, replay.

- [ ] **Step 1: Write failing persistence, idempotency, deadline, and grievance tests**

Create `tests/arena/test_channel_runtime.py`:

```python
import json
import os
import pytest

from civ_mcp.arena.channel_protocol import ChannelTurnContext
from civ_mcp.arena.channel_runtime import ChannelRuntime
from civ_mcp.arena.config import ChannelRules


def runtime(tmp_path):
    return ChannelRuntime.open(tmp_path, "run-a", frozenset({1, 2}), ChannelRules())


def test_open_creates_owner_only_journal_and_snapshot(tmp_path):
    rt = runtime(tmp_path)
    assert (tmp_path / "channels" / "events.jsonl").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "channels" / "state.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "channels").stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
async def test_pure_action_replay_is_a_noop(tmp_path, fake_gs):
    rt = runtime(tmp_path)
    staged = stage("src-1", 1, "send_message", {"to_player": 2, "text": "hello"})
    await rt.apply_staged(fake_gs, staged, turn=4, observation=None)
    await rt.apply_staged(fake_gs, staged, turn=4, observation=None)
    assert len(rt.state.messages) == 1
    assert "src-1" in rt.state.applied_source_ids


def test_resume_replays_events_newer_than_snapshot(tmp_path):
    rt = runtime(tmp_path)
    rt._commit("message_sent", {
        "id": "msg-000001", "from_player": 1, "to_player": 2,
        "turn": 1, "text": "persist", "deal_id": None,
    })
    state_path = tmp_path / "channels" / "state.json"
    snapshot = json.loads(state_path.read_text())
    snapshot["last_event_sequence"] = 0
    snapshot["messages"] = []
    state_path.write_text(json.dumps(snapshot))
    reopened = runtime(tmp_path)
    assert reopened.state.messages[0].text == "persist"


def test_base_magnitude_is_fixed_and_bounded():
    assert grievance_base_magnitude(1) == 0.25
    assert grievance_base_magnitude(100) == 1.0
    assert grievance_base_magnitude(50_000) == 10.0
```

- [ ] **Step 2: Run new tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_runtime.py -q`

Expected: import failure because `channel_runtime.py` does not exist.

- [ ] **Step 3: Implement secure event commit and strict resume**

`ChannelRuntime.open` creates `channels/` with `mode=0o700`, opens/creates journal and snapshot with `0o600`, validates run ID/enabled set/rules fingerprint, loads snapshot, and replays only strictly consecutive higher-sequence events. Unknown schema, malformed JSON, impossible reducer transition, truncated non-final journal record, or fingerprint mismatch raises `ChannelStateError` before admission.

Use this commit order:

```python
def _commit(self, kind: str, payload: dict) -> ChannelEvent:
    seq = self.state.last_event_sequence + 1
    event = ChannelEvent(1, f"evt-{seq:06d}", seq, kind, payload)
    encoded = (json.dumps(event_to_dict(event), sort_keys=True, separators=(",", ":")) + "\n").encode()
    with self.events_path.open("ab", buffering=0) as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    self.state = reduce_event(self.state, event)
    self._write_snapshot()
    return event
```

`_write_snapshot` writes a sibling file with mode `0o600`, flushes/fsyncs it, `os.replace`s it, then fsyncs the directory.

- [ ] **Step 4: Implement pure lifecycle and inclusive deadlines**

Apply `send_message`, `propose_deal`, and `respond_to_deal` as one logical event plus `source_applied`. Enforce 200 messages/ordered pair and 3 proposed-or-active deals/ordered pair before journaling. Proposal records the post-policy observation data required by any `baseline_phase="proposal"` term. Acceptance activates `up_front` payment or `on_delivery` favor; `_start_favor` captures `baseline_phase="favor_start"` only when the favor window actually starts—at unofficial acceptance for on-delivery, or after exact payment settlement for up-front. A camp cleared by any party after proposal can therefore satisfy its stored proposal-time condition, while unit/city identities cannot be counted before their obligation begins.

After the responsible party's staged actions and post-observation, evaluate its inclusive deadline. Use fixed mapping: favor failure wrongs proposer/offender counterparty; missing funding wrongs counterparty/offender proposer; rejected/ignored exact payment wrongs proposer/offender counterparty. Create grievances only for `broken`, using:

```python
def grievance_base_magnitude(payment_gold: int) -> float:
    return min(10.0, max(0.25, payment_gold / 100.0))
```

Declined/expired proposals create no grievance. Missing required evidence yields `unverifiable`. Due obligations create wake reasons.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channel_runtime.py tests/arena/test_channels.py -q`

Expected: persistence/lifecycle tests pass.

```bash
git add src/civ_mcp/arena/channel_runtime.py tests/arena/test_channel_runtime.py
git commit -m "feat(arena): persist unofficial channel lifecycle"
```

### Task 9: Exact linked payment lifecycle and intent/result crash recovery

**Files:**
- Modify: `src/civ_mcp/arena/channel_runtime.py`
- Modify: `tests/arena/test_channel_runtime.py`

**Interfaces:**
- Consumes: exact GameState methods from Task 4 and persisted deal lifecycle from Task 8.
- Produces: up-front/on-delivery payment transitions, one unresolved payment per ordered pair, `reconcile_payment_intents`.

- [ ] **Step 1: Add failing payment matrix and crash-boundary tests**

Append parameterized cases covering both timings and each responsible-party default:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ["up_front", "on_delivery"])
async def test_deal_honored_only_after_favor_and_exact_payment(tmp_path, payment_gs, timing):
    rt, deal = await accepted_deal(tmp_path, payment_gs, timing=timing)
    if timing == "on_delivery":
        await satisfy_favor(rt, payment_gs, deal)
    await apply_action(rt, payment_gs, deal.proposer, "fund_deal", {"deal_id": deal.id})
    assert rt.deal(deal.id).payment_status == "offered"
    await apply_action(rt, payment_gs, deal.counterparty, "respond_to_payment", {
        "deal_id": deal.id, "accept": True})
    if timing == "up_front":
        await satisfy_favor(rt, payment_gs, deal)
    assert rt.deal(deal.id).state == "honored"


@pytest.mark.asyncio
async def test_funding_refuses_ambiguous_preexisting_pending_trade(tmp_path, payment_gs):
    rt, deal = await accepted_deal(tmp_path, payment_gs, timing="up_front")
    payment_gs.pending[(deal.proposer, deal.counterparty)] = "unrelated"
    ack = await apply_action(rt, payment_gs, deal.proposer, "fund_deal", {"deal_id": deal.id})
    assert ack.status == "rejected"
    assert rt.deal(deal.id).payment_status == "due"


@pytest.mark.asyncio
async def test_recovery_observed_offer_records_offered_without_resend(tmp_path, payment_gs):
    rt, deal = await accepted_deal(tmp_path, payment_gs, timing="up_front")
    rt._commit("payment_fund_intent", payment_intent_payload(deal, "src-fund"))
    payment_gs.install_exact_offer(deal.proposer, deal.counterparty, deal.payment_gold)
    reopened = ChannelRuntime.open(tmp_path, "run-a", frozenset({1, 2}), ChannelRules())
    await reopened.reconcile_payment_intents(payment_gs)
    assert reopened.deal(deal.id).payment_status == "offered"
    assert payment_gs.offer_calls == 0


@pytest.mark.asyncio
async def test_ambiguous_missing_offer_becomes_unverifiable_without_retry(tmp_path, payment_gs):
    rt, deal = await accepted_deal(tmp_path, payment_gs, timing="up_front")
    rt._commit("payment_fund_intent", payment_intent_payload(deal, "src-fund"))
    reopened = ChannelRuntime.open(tmp_path, "run-a", frozenset({1, 2}), ChannelRules())
    await reopened.reconcile_payment_intents(payment_gs)
    assert reopened.deal(deal.id).state == "unverifiable"
    assert payment_gs.offer_calls == 0
    assert reopened.state.grievances == ()
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_runtime.py -q`

Expected: payment actions are rejected/unimplemented and recovery tests fail.

- [ ] **Step 3: Implement funding/response intent-result transitions**

Funding validates actor, due state/deadline, one unresolved offer for the ordered pair, and no pre-existing pending trade. Append `payment_fund_intent`, call `gs.offer_channel_payment(payee, gold)`, then append `payment_fund_result` containing the exact fingerprint and authoritative engine result. Only a proven successful result sets `offered`.

Response validates counterparty, response deadline, and `gs.get_channel_payment_offer(payer, gold)` exact match before appending `payment_response_intent`. Call `gs.respond_to_channel_payment`; append `payment_response_result`. Accept success sets `settled`; reject success creates the fixed counterparty breach. Failed engine calls leave the obligation due through the inclusive deadline.

- [ ] **Step 4: Implement startup reconciliation without double-pay**

For an unfinished funding intent: exact offer present → append recovered success, never resend; offer absent with no result → `unverifiable`; conflicting offer → retain due only if original deadline is open. For an unfinished response intent: exact offer present → retry the journaled boolean exactly once and record result; offer absent with no authoritative result → `unverifiable`, never retry. Mark the source applied only with the result/recovery event.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channel_runtime.py tests/test_channel_lua.py -q`

Expected: full lifecycle/payment/recovery matrix passes.

```bash
git add src/civ_mcp/arena/channel_runtime.py tests/arena/test_channel_runtime.py
git commit -m "feat(arena): settle linked unofficial channel payments"
```

### Task 10: API tools, CLI capture instructions, and fixed prompt slot

**Files:**
- Modify: `src/civ_mcp/arena/agent.py:74-281`
- Modify: `src/civ_mcp/arena/cli_agent.py:112-654`
- Modify: `src/civ_mcp/arena/prompting.py:50-91`
- Modify: `src/civ_mcp/arena/playbook.md`
- Modify: `tests/arena/test_agent.py`
- Modify: `tests/arena/test_cli_agent.py`
- Modify: `tests/arena/test_prompting.py`
- Modify: `tests/arena/test_registry.py`

**Interfaces:**
- Consumes: `ChannelTurnContext` and `channel_tool_schemas` from Task 3.
- Produces: optional policy keyword `channel_context`, transcript injection metadata, exact CLI `CHANNEL` instruction, prompt order `briefing → memory → tasks → channels → master → attention → blocker → announcement`.

- [ ] **Step 1: Add failing API/CLI/prompt-order tests**

Add tests asserting:

```python
@pytest.mark.asyncio
async def test_llm_policy_exposes_bound_channel_tools_outside_registry(fake_backend, fake_cost):
    ctx = ChannelTurnContext("r", 1, 4, frozenset({1, 2}), ChannelRules())
    fake_backend.replies = [tool_reply("send_message", {"to_player": 2, "text": "hello"}), text_reply("done")]
    result = await LLMPolicy(fake_backend, fake_cost)(FakeGS(), 1, 4, channel_context=ctx)
    assert ctx.staged_actions[0].actor == 1
    assert "send_message" not in TOOL_REGISTRY
    assert result["transcript"]["prompt_injections"]["channels"] is True


def test_opening_prompt_has_channel_and_reserved_master_slots():
    text = build_opening_prompt(
        player_id=1, turn=3, briefing_text="BRIEF", memory_block="MEM",
        task_block="TASK", channel_block="CHAN", master_block="MASTER",
        digest_block="DIGEST", blocker_block="BLOCK",
    )
    assert [text.index(x) for x in ["BRIEF", "MEM", "TASK", "CHAN", "MASTER", "DIGEST", "BLOCK", "It is turn"]] == sorted(
        text.index(x) for x in ["BRIEF", "MEM", "TASK", "CHAN", "MASTER", "DIGEST", "BLOCK", "It is turn"]
    )


@pytest.mark.asyncio
async def test_cli_prompt_requires_exact_channel_json_line(cli_policy, monkeypatch):
    captured = capture_cli_prompt(monkeypatch)
    ctx = ChannelTurnContext("r", 1, 4, frozenset({1, 2}), ChannelRules())
    await cli_policy(FakeGS(), 1, 4, channel_context=ctx, channel_block="== PRIVATE CHANNELS ==")
    assert 'CHANNEL {"action":"send_message"' in captured.text
    assert "== PRIVATE CHANNELS ==" in captured.text
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_agent.py tests/arena/test_cli_agent.py tests/arena/test_prompting.py tests/arena/test_registry.py -q`

Expected: policies reject new keyword; prompt builder rejects new blocks.

- [ ] **Step 3: Extend prompt and policy signatures**

Add `channel_block: str = ""` and `master_block: str = ""` before `digest_block` in `build_opening_prompt`, `LLMPolicy.__call__`, and `CLIAgentPolicy.__call__`; add `channel_context: ChannelTurnContext | None = None` to both policies. Insert blocks in the exact order. Add `"channels"` and `"master"` booleans to transcript `prompt_injections`.

For `LLMPolicy`, concatenate `channel_tool_schemas()` to the game schema when context exists. In the tool loop, route names in `CHANNEL_ACTION_NAMES` to:

```python
args = json.loads(tc["arguments"] or "{}")
result = channel_context.dispatch(tc["name"], args)
```

Do not pass channel names to `registry.dispatch`, `filter_tools`, tiers, or capability gating.

- [ ] **Step 4: Add exact CLI instructions and playbook guidance**

When `channel_context` exists, append a bounded instruction listing the five exact JSON-line shapes from the spec. The raw final summary remains unchanged; compact `summary` may remain clamped. Document that prose alone is non-binding and only registered terms are enforceable in core mode.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_agent.py tests/arena/test_cli_agent.py tests/arena/test_prompting.py tests/arena/test_registry.py -q`

Expected: all policy/prompt/registry tests pass; registry test proves channel names remain absent.

```bash
git add src/civ_mcp/arena/agent.py src/civ_mcp/arena/cli_agent.py src/civ_mcp/arena/prompting.py src/civ_mcp/arena/playbook.md tests/arena/test_agent.py tests/arena/test_cli_agent.py tests/arena/test_prompting.py tests/arena/test_registry.py
git commit -m "feat(arena): expose private channel entry paths"
```

### Task 11: Coordinator admission, capture, observations, deadlines, and wake wiring

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py:405-2060`
- Modify: `src/civ_mcp/arena/attention.py`
- Modify: `tests/arena/test_coordinator.py`
- Modify: `tests/arena/test_attention.py`

**Interfaces:**
- Consumes: `ChannelRuntime.open/admit_player/finish_player/poll_unseated`, policy `channel_context/channel_block`, raw final summaries.
- Produces: exactly two observation queries per acting channel participant, channel wake precedence, post-policy capture/application before release.

- [ ] **Step 1: Add failing coordinator integration tests**

Add fake-runtime tests:

```python
@pytest.mark.asyncio
async def test_coordinator_admits_and_finishes_channels_around_one_policy_call(monkeypatch):
    rt = FakeChannelRuntime(wake_reasons=())
    monkeypatch.setattr(ChannelRuntime, "open", classmethod(lambda cls, *a, **k: rt))
    policy = RecordingPolicy(final_summary='CHANNEL {"action":"send_message","to_player":2,"text":"hi"}')
    await run_arena(conn_for_one_turn(player=1), FakeGS(), channels_config(), policy=policy)
    assert rt.calls == ["admit:1:7", "finish:1:7"]
    assert policy.kwargs["channel_block"] == "CHANNEL BLOCK"
    assert policy.kwargs["channel_context"].player_id == 1


@pytest.mark.asyncio
async def test_channel_wake_cancels_sleep_and_invokes_policy(monkeypatch):
    rt = FakeChannelRuntime(wake_reasons=("payment response due",))
    policy = RecordingPolicy()
    await run_arena(sleeping_conn(), FakeGS(), channels_config(attention="auto"), policy=policy)
    assert policy.calls == 1
    assert rt.finish_calls == 1


@pytest.mark.asyncio
async def test_channel_failure_is_visible_but_gameplay_continues(monkeypatch):
    rt = FakeChannelRuntime(admit_error=RuntimeError("projection failed"))
    result = await run_arena(conn_for_one_turn(player=1), FakeGS(), channels_config(), policy=RecordingPolicy())
    assert result["turns"] == 1
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_coordinator.py tests/arena/test_attention.py -q`

Expected: runtime is never constructed/called and policy kwargs lack channel context/block.

- [ ] **Step 3: Construct the runtime once and wire admission before sleep**

At `run_arena` startup, derive `run_dir = Path(config.transcript_dir) / run_id`; if enabled players are non-empty and the optional injected `channel_runtime` is `None`, call `ChannelRuntime.open`. If a runtime is injected, validate its run ID/enabled set/rules fingerprint and use that exact object. Before attention's final sleep decision, call `admit_player`; merge its wake reasons into the attention wake set. On exception, record `channel_error`, use an empty block/context, and continue the model turn.

Pass `channel_block` and `channel_context` through `policy_kwargs` for API, CLI, scripted, and seat-0 paths only when the policy accepts those keywords. Repair/diplomacy/WC passes reuse the already-created admission and must not create a second channel admission.

- [ ] **Step 4: Finish after policy actions and poll unseated deadlines**

After the normal policy result, before task/memory capture and seat release, call `finish_player`. It extracts normalized action audit from transcript steps, consumes API staged actions, parses CLI lines from raw `transcript.final_summary`, makes the post-policy observation, applies requests in source order, evaluates due obligations, and persists acknowledgements. Add channel result/error counts to the turn transcript without copying private third-party content.

On idle/unconfigured-seat polls call `poll_unseated` so expired proposals and obligations for seats no longer in the run progress deterministically. Never evaluate an inclusive deadline before its responsible party's captured actions for that numbered turn.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_coordinator.py tests/arena/test_attention.py tests/arena/test_transcript.py -q`

Expected: coordinator, attention, and transcript tests pass.

```bash
git add src/civ_mcp/arena/coordinator.py src/civ_mcp/arena/attention.py tests/arena/test_coordinator.py tests/arena/test_attention.py
git commit -m "feat(arena): coordinate deterministic unofficial channels"
```

### Task 12: Analysis, maximum-shape regressions, experiment, and attended live gate

**Files:**
- Modify: `src/civ_mcp/arena/analyze.py:803-1202`
- Modify: `tests/arena/test_analyze.py`
- Modify: `tests/arena/test_channel_runtime.py`
- Create: `experiments/arena-channels-core-smoke.yaml`
- Create: `docs/superpowers/plans/2026-07-16-arena-unofficial-channels-core-live-gate.md`

**Interfaces:**
- Consumes: completed core runtime and persisted state.
- Produces: `analyze_channels(state_payload, current_turn)`, bounded eight-player proof, reproducible attended gate.

- [ ] **Step 1: Add failing analysis and maximum-shape tests**

Add:

```python
def test_channel_analysis_groups_pairs_payments_outcomes_and_sources():
    report = analyze_channels(channel_state_fixture(), current_turn=60)
    assert report["messages_by_player"]["1"] == 2
    assert report["pairs"]["1->2"]["payments"]["settled"] == 1
    assert report["outcomes"]["honored"] == 1
    assert report["grievances"]["deterministic"]["count"] == 1


@pytest.mark.asyncio
async def test_eight_player_shape_has_bounded_projection_and_two_queries_per_turn(tmp_path):
    gs = CountingObservationGS()
    rt = ChannelRuntime.open(tmp_path, "r", frozenset(range(8)), ChannelRules())
    seed_maximum_legal_state(rt)
    admission = await rt.admit_player(gs, 4, 90)
    await rt.finish_player(gs, admission, {"transcript": {"steps": [], "final_summary": ""}})
    assert gs.observation_calls == 2
    assert all(len(group) <= 10 for group in messages_grouped_by_counterpart(admission.projection))
```

- [ ] **Step 2: Run analysis/runtime tests and confirm RED**

Run: `uv run pytest tests/arena/test_analyze.py tests/arena/test_channel_runtime.py -q`

Expected: missing `analyze_channels` and maximum-shape helpers/behavior.

- [ ] **Step 3: Implement analysis and write exact smoke config**

`analyze.py` reads `channels/state.json` when present, validates schema 1, and adds a `channels` report without failing older runs that lack channels. Aggregate per player, ordered pair, payment status, terminal outcome, grievance raw/effective magnitude, and adjudication source.

Create `experiments/arena-channels-core-smoke.yaml` with an API mover on the existing GPU-0 endpoint and a CLI seat:

```yaml
run_id: arena-channels-core-smoke
max_game_turns: 20
channel_rules:
  acceptance_turns: 3
  funding_turns: 2
  payment_response_turns: 2
civs:
  - player: 1
    provider: local
    model: gemma4-26b
    gateway: http://192.168.20.196:11440/v1
    tools: full
    channels: {enabled: true}
  - player: 2
    provider: cli-codex
    model: gpt-5
    channels: {enabled: true}
```

- [ ] **Step 4: Write and execute the attended core gate**

The live-gate document requires `superpowers:verification-before-completion` and `civ6-arena-live`. Record run ID, save, branch/commit, endpoint identities, and artifacts. Exercise: up-front honored deal, on-delivery breached deal, one continuous unit/trade term, restart with payment pending, API/CLI acknowledgements, and a player-3 privacy canary. Verify exact official pending trade fingerprints and no third-party leakage. If any FireTuner API differs from the offline fixture, stop and record the observed response before changing Lua.

Run:

```bash
uv run civ-arena --config experiments/arena-channels-core-smoke.yaml
uv run civ-arena-analyze arena_runs/arena-channels-core-smoke
```

Expected: attended checklist records PASS for every item; analysis shows at least one `honored`, one `broken`, one settled payment, and one deterministic grievance.

- [ ] **Step 5: Run the full verification suite and commit**

Run:

```bash
uv run pytest tests/ -q
git diff --check
git status --short --branch
```

Expected: full suite passes; diff check is silent; only intentional core files and pre-existing `.serena/memories/` are shown.

```bash
git add src/civ_mcp/arena/analyze.py tests/arena/test_analyze.py tests/arena/test_channel_runtime.py experiments/arena-channels-core-smoke.yaml docs/superpowers/plans/2026-07-16-arena-unofficial-channels-core-live-gate.md
git commit -m "test(arena): gate deterministic unofficial channels"
```

After the commit, leave `arena-unofficial-channels-core` unmerged for riz's separate-session review. The game-master plan must branch from this reviewed tip, not from an earlier partial task.
