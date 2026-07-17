# Arena Reusable Live-Gate Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the reusable, coordinator-owned deterministic live-gate driver and its first scenario, `unofficial_channels_core_v1`, which drives the unofficial-channels lifecycle (canary, proposals, acceptance, funding, restart handshake, honored/broken deals, grievance, privacy checks) through the production channel entry paths without invoking any model.

**Architecture:** A generic gate layer (`live_gate.py`: options, strict event reducer, write-ahead journal + snapshot + result persistence, scenario registry, restart/terminal signals) sits alongside the channel reducer. The scenario module (`live_gate_channels.py`) plans deterministic per-seat inputs, dispatches through the production `ChannelTurnContext` (API actor) and `transcript.final_summary` `CHANNEL {...}` lines (CLI actor), verifies every transition against canonical `ChannelRuntime` state, and asserts observer privacy every round. The coordinator gains three narrow hooks (attach, note_admission, after_seat_capture); `arena.py` resolves the driver instead of model policies and translates the terminal signal into exit code 75/0/1.

**Tech Stack:** Python 3.12, dataclasses, pytest + pytest-asyncio (in the `test` extra — run tests via `uv run --extra test pytest`), PyYAML (experiment parsing already in place).

**Spec:** `docs/superpowers/specs/2026-07-17-arena-live-gate-driver-design.md` (read it before starting; every task below implements a named spec section).

## Global Constraints

- `live_gate.py` must not import unofficial-channel scenario details; `live_gate_channels.py` may depend only on the public channel runtime/protocol/term/projection/prompt interfaces. Neither module may import Lua builders (`civ_mcp.lua.*` query builders) or mutate a `ChannelState` directly.
- The driver never calls `ChannelRuntime.apply_staged` or `parse_cli_channel_lines` itself; the API actor stages via `ChannelTurnContext.dispatch`, the CLI actor's line is parsed only by unmodified `ChannelRuntime.finish_player`.
- Gate mode constructs and invokes no model backend, no CLI subprocess, and no ordinary LLM/CLI policy.
- Gate artifacts live only under `arena_runs/<run_id>/live_gate/` (`events.jsonl`, `state.json`, `result.json`) with the same private ownership rules as channel persistence (dir 0o700, files 0o600, regular files only, atomic snapshot replace).
- The watcher exits 75 only after the persisted restart checkpoint; terminal PASS exits 0; any other gate outcome exits 1. The persisted `result.json` and the printed machine-readable line are the authoritative operator signals.
- Fail closed: every mismatch in the spec's "Fail-Closed Rules" list writes `gate_failed` + `result.json`, stops all gate actions, and requests safe coordinator deactivation. No retry may create a second official payment side effect.
- Deterministic gate turns may observe, skip units, and make the existing `ScriptedPolicy` blocker repairs only — never buy/sell, create/redirect/cancel trade routes, do ordinary diplomacy, declare war, move units strategically, or invoke a model.
- When `live_gate.enabled` is false (or the block is absent) every existing arena path must be byte-for-byte unchanged: no gate directory, no scenario code evaluated.
- Test command in this worktree: `uv run --extra test pytest <path> -v` (pytest-asyncio lives in the `test` extra).
- Commit per task on branch `arena-unofficial-channels-core`. NEVER merge to main or push without riz's explicit direction.
- Before offering the branch for an attended run: full suite green (`uv run --extra test pytest`) and `git diff --check` clean.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/civ_mcp/arena/live_gate.py` | create | Generic: `GateEvent`/`GateState`, strict reducer, `LiveGateJournal` persistence, scenario registry (`ScenarioMeta`, `register_scenario`, `resolve_scenario`, `resolve_live_gate_driver`), status vocabulary |
| `src/civ_mcp/arena/live_gate_channels.py` | create | `unofficial_channels_core_v1`: role contracts, minimum captures, config fingerprint, canary, gate seat policies, phase machine, canonical assertions, restart handshake, privacy checks, terminal evidence |
| `src/civ_mcp/arena/config.py` | modify | `LiveGateOptions` on `ArenaConfig`; `_validate_live_gate` in `validate_arena_config`; update the `scripted` provider comment |
| `src/civ_mcp/arena/experiment.py` | modify | Strict top-level `live_gate` block parsing |
| `src/civ_mcp/arena/arena.py` | modify | Resolve driver before policy construction; gate mode builds no model policies and skips backend/CLI preflight; exit-code + machine-readable result line |
| `src/civ_mcp/arena/coordinator.py` | modify | `live_gate_driver` kwarg; attach after channel-runtime open; `note_admission` + `after_seat_capture` hooks; signal break; `live_gate` result field |
| `experiments/arena-channels-core-smoke.yaml` | modify | New run ID, player 3 scripted observer, `live_gate` block, 36/36 budgets |
| `tests/arena/live_gate_fakes.py` | create | `GateGameState` fake + `run_gate_seat`/`run_gate_round` harness shared by both new test files |
| `tests/arena/test_live_gate.py` | create | Generic reducer/persistence/replay/registry tests |
| `tests/arena/test_live_gate_channels.py` | create | Scenario planning/lifecycle/terms/privacy/restart/terminal tests |
| `tests/arena/test_config.py` | modify | Gate cross-field validation tests |
| `tests/arena/test_experiment.py` | modify | `live_gate` block parsing tests |
| `tests/arena/test_coordinator.py` | modify | Coordinator isolation + wiring regressions |
| `tests/arena/test_arena_wiring.py` | modify | Gate-mode policy construction, preflight skip, exit-code plumbing |

Verified code facts this plan relies on (checked 2026-07-17 on this branch):

- `ChannelTurnContext.dispatch(name, args) -> str` is synchronous; source ID is `api:{run_id}:{player}:{turn}:{index}:{sha256(canonical_args)[:16]}` where `canonical_args = json.dumps(args, sort_keys=True, separators=(",", ":"))` and `index = len(context.staged_actions)` before the append (`channel_protocol.py:494-531`).
- CLI source ID is `cli:{run_id}:{actor}:{turn}:{line_index}:{sha256(raw_line_bytes)[:16]}` where `line_index` counts `summary.splitlines()` positions (`channel_protocol.py:343-348`).
- `ChannelRuntime.admit_player(gs, player_id, turn) -> ChannelAdmission` (fields: `player_id, turn, observation_id, projection, block, context, wake_reasons`); `finish_player(gs, admission, policy_result) -> tuple[ChannelAcknowledgement, ...]`; acknowledgement statuses are `"applied"` / `"rejected"` (`channel_runtime.py:78-87, 1779-1818, 3040-3155`).
- `_payment_fingerprint(deal)` is `{"payer": proposer, "payee": counterparty, "gold": payment_gold, "duration": 0, "item_count": 1}`; live offers expose `.fingerprint()` with the same fields (`channel_runtime.py:2379-2404`).
- `gs.get_channel_payment_state(payer, payee, gold)` returns an object with `.status` in `{"absent","exact","conflicting"}` and `.offer`; `gs.offer_channel_payment(payee, gold)` succeeds with `"CHANNEL_PAYMENT_PROPOSED"`; `gs.respond_to_channel_payment(payer, gold, accept)` succeeds with `"CHANNEL_PAYMENT_ACCEPTED"`/`"CHANNEL_PAYMENT_REJECTED"` (`game_state.py:939-971`, `channel_runtime.py:2359-2373`).
- Deadline arithmetic: acceptance of an `up_front` deal sets `fund_by_turn = accepted_turn + funding_turns`; its favor starts at payment settlement with `favor_due_turn = settlement_turn + completion_window_turns`. Acceptance of an `on_delivery` deal sets `favor_due_turn = accepted_turn + completion_window_turns`; funding becomes due after favor satisfaction with `fund_by_turn = satisfaction_turn + funding_turns` (`channel_runtime.py:684-761, 1378, 1423, 2925-2927`).
- A funding breach maps `wronged, offender = deal.counterparty, deal.proposer` (`channel_runtime.py:1543`).
- Deal states: `proposed/active/honored/broken/declined/expired/unverifiable`; favor: `not_due/due/satisfied/failed/released`; payment: `not_due/due/offered/settled/failed/waived` (`channels.py:16-40`).
- `ObservationFamily` has NO pending-trades member — pending trades are a payment-runtime query (`channel_terms.py:11-19`).
- `validate_arena_config` in `config.py:255` is the single choke point both the YAML loader and CLI path call.
- The coordinator's puppet flow: `admit_player` at `coordinator.py:1667`, policy call at `:2328`, `_finish_channel_turn(result)` at `:2376` (failure path finish at `:2365`), `hook.restore_local(conn, 0)` at `:2369`/`:2567`; the run-scope `finally` (`:2745`) ALWAYS restores the human and disables the hook.
- `ScriptedPolicy` (`coordinator.py:192`) provides the deterministic observe/skip normal turn and `_repair(gs, blocker_block)` scoped blocker resolution the spec's "Deterministic Minimal Turns" section names.

## Expected round schedule (drives budgets and tests)

With the checked-in rules (`acceptance_turns: 3, funding_turns: 2, payment_response_turns: 2`) and both deals using `within=1`, seats admitted in order 1, 2, 3 per game turn:

| Round | api_actor (1) | cli_actor (2) | observer (3) | Phase movement |
|---|---|---|---|---|
| R1 | preflight, canary `send_message`, `propose_deal` up_front | `respond_to_deal` accept | no-action + privacy | preflight → canary_and_upfront_proposal → accept_upfront → fund_upfront |
| R2 | `fund_deal` | no-action | no-action + privacy | fund_upfront → restart_required at round boundary; watcher exits 75 |
| R3 (resumed) | no-action (restart already verified at attach) | `respond_to_payment` accept | no-action + privacy | restart_verify → accept_upfront_payment → await_upfront_favor_deadline |
| R4 | no-action | no-action (favor due turn: routes observed, favor satisfied → honored) | no-action + privacy | → verify_upfront_honored → propose_on_delivery |
| R5 | no-action | `propose_deal` on_delivery | no-action + privacy | → accept_on_delivery |
| R6 | `respond_to_deal` accept | no-action | no-action + privacy | → await_on_delivery_favor |
| R7 | no-action (favor due: treasury observed, satisfied; payment due) | no-action | no-action + privacy | → withhold_on_delivery_funding |
| R8 | no-action | deliberately no channel action | no-action + privacy | (deal nonterminal before fund_by) |
| R9 | no-action | inclusive funding deadline passes unfunded → broken + grievance | no-action + privacy | verify_funding_breach → verify_terminal_gate → gate_passed |

27 captures in 9 rounds; the checked-in budget is 36 (`max_puppet_turns`/`max_game_turns`) for watcher-handoff and blocker-cleanup slack. `minimum_captures()` computes 27 from the rules, so validation tracks rule changes instead of silently accepting 36.

---

### Task 1: Generic gate state, strict reducer, and write-ahead journal

**Files:**
- Create: `src/civ_mcp/arena/live_gate.py`
- Test: `tests/arena/test_live_gate.py`

**Interfaces:**
- Consumes: nothing project-specific (stdlib only).
- Produces (later tasks rely on these exact names):
  - `GATE_SCHEMA_VERSION = 1`; statuses `GATE_ACTIVE = "active"`, `GATE_RESTART_REQUIRED = "restart_required"`, `GATE_FAILED = "failed"`, `GATE_PASSED = "passed"`.
  - `class GateStateError(RuntimeError)`.
  - `@dataclass(frozen=True) GateEvent(schema_version, sequence, kind, payload)`.
  - `@dataclass(frozen=True) GateState(...)` with fields listed below.
  - `reduce_gate_event(state: GateState | None, event: GateEvent) -> GateState`.
  - `class LiveGateJournal` with `open(run_dir, *, run_id, scenario, scenario_revision, roles, config_fingerprint, initial_phase) -> LiveGateJournal`, `append(kind, payload) -> GateEvent`, `write_result() -> None`, attributes `state`, `gate_dir`, `events_path`, `state_path`, `result_path`.

- [ ] **Step 1: Write the failing tests**

Create `tests/arena/test_live_gate.py`:

```python
import json
import os
import stat

import pytest

from civ_mcp.arena.live_gate import (
    GATE_ACTIVE,
    GATE_FAILED,
    GATE_PASSED,
    GATE_RESTART_REQUIRED,
    GATE_SCHEMA_VERSION,
    GateStateError,
    LiveGateJournal,
)


FINGERPRINT = {"scenario": "demo_v1", "run_id": "run-g", "rules": {"x": 1}}


def open_journal(tmp_path, **overrides):
    kwargs = dict(
        run_id="run-g",
        scenario="demo_v1",
        scenario_revision=1,
        roles={"actor": 1, "observer": 2},
        config_fingerprint=FINGERPRINT,
        initial_phase="preflight",
    )
    kwargs.update(overrides)
    return LiveGateJournal.open(tmp_path, **kwargs)


def planned(source_id="api:run-g:1:5:0:abc", **overrides):
    payload = {
        "turn": 5,
        "player_id": 1,
        "phase": "preflight",
        "name": "send_message",
        "source_id": source_id,
        "payload_digest": "d" * 16,
    }
    payload.update(overrides)
    return payload


def test_open_initializes_identity_and_private_files(tmp_path):
    journal = open_journal(tmp_path)
    state = journal.state
    assert state.run_id == "run-g"
    assert state.scenario == "demo_v1"
    assert state.scenario_revision == 1
    assert state.roles == (("actor", 1), ("observer", 2))
    assert state.config_fingerprint == FINGERPRINT
    assert state.phase == "preflight"
    assert state.status == GATE_ACTIVE
    assert state.restart_count == 0
    assert state.last_event_sequence == 1  # gate_initialized
    gate_dir = tmp_path / "live_gate"
    assert stat.S_IMODE(os.stat(gate_dir).st_mode) == 0o700
    for path in (journal.events_path, journal.state_path):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not journal.result_path.exists()


def test_append_reduce_snapshot_reopen_equivalence(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("phase_advanced", {"phase": "act", "turn": 5})
    journal.append("data_recorded", {"data": {"deal_id": "deal-000007"}})
    journal.append("observation_recorded", {"turn": 5, "player_id": 1, "families": ["treasury"]})
    reopened = open_journal(tmp_path)
    assert reopened.state == journal.state
    assert reopened.state.phase == "act"
    assert reopened.state.data == {"deal_id": "deal-000007"}
    assert len(reopened.state.observations) == 1


def test_action_planned_then_verified_lifecycle(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("action_planned", planned())
    assert len(journal.state.pending_actions) == 1
    journal.append("action_verified", {"source_id": "api:run-g:1:5:0:abc", "turn": 5})
    assert journal.state.pending_actions == ()
    assert len(journal.state.verified_actions) == 1


def test_phase_advance_blocked_by_unverified_action(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("action_planned", planned())
    with pytest.raises(GateStateError):
        journal.append("phase_advanced", {"phase": "next", "turn": 5})


def test_action_verified_requires_matching_plan(tmp_path):
    journal = open_journal(tmp_path)
    with pytest.raises(GateStateError):
        journal.append("action_verified", {"source_id": "api:run-g:1:5:0:zzz", "turn": 5})


def test_duplicate_planned_source_rejected(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("action_planned", planned())
    with pytest.raises(GateStateError):
        journal.append("action_planned", planned())


def test_second_restart_required_rejected(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("restart_required", {"turn": 6})
    assert journal.state.status == GATE_RESTART_REQUIRED
    assert journal.state.restart_count == 1
    journal.append("restart_verified", {"turn": 7})
    assert journal.state.status == GATE_ACTIVE
    with pytest.raises(GateStateError):
        journal.append("restart_required", {"turn": 8})


def test_restart_verified_only_from_restart_required(tmp_path):
    journal = open_journal(tmp_path)
    with pytest.raises(GateStateError):
        journal.append("restart_verified", {"turn": 6})


def test_terminal_states_reject_further_events(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("gate_failed", {"reason": "boom"})
    assert journal.state.status == GATE_FAILED
    assert journal.state.reason == "boom"
    with pytest.raises(GateStateError):
        journal.append("phase_advanced", {"phase": "next", "turn": 6})

    passed = open_journal(tmp_path.parent / "p2")
    passed.append("gate_passed", {"evidence": {"honored": 1}})
    assert passed.state.status == GATE_PASSED
    with pytest.raises(GateStateError):
        passed.append("data_recorded", {"data": {}})


def test_privacy_fail_permits_only_gate_failed(tmp_path):
    journal = open_journal(tmp_path)
    journal.append(
        "privacy_asserted",
        {"turn": 5, "player_id": 2, "artifact_kind": "projection",
         "input_digest": "a" * 16, "forbidden_digests": [], "result": "FAIL"},
    )
    with pytest.raises(GateStateError):
        journal.append("phase_advanced", {"phase": "next", "turn": 5})
    journal.append("gate_failed", {"reason": "privacy"})
    assert journal.state.status == GATE_FAILED


def test_gate_passed_requires_active_status(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("restart_required", {"turn": 6})
    with pytest.raises(GateStateError):
        journal.append("gate_passed", {"evidence": {}})


def test_reopen_identity_mismatch_fails(tmp_path):
    open_journal(tmp_path)
    with pytest.raises(GateStateError):
        open_journal(tmp_path, run_id="other-run")
    with pytest.raises(GateStateError):
        open_journal(tmp_path, config_fingerprint={"scenario": "demo_v1", "changed": True})
    with pytest.raises(GateStateError):
        open_journal(tmp_path, roles={"actor": 1, "observer": 9})


def test_snapshot_newer_than_journal_rejected(tmp_path):
    journal = open_journal(tmp_path)
    snapshot = json.loads(journal.state_path.read_text())
    snapshot["last_event_sequence"] = 99
    journal.state_path.write_text(json.dumps(snapshot))
    with pytest.raises(GateStateError):
        open_journal(tmp_path)


def test_symlinked_journal_rejected(tmp_path):
    journal = open_journal(tmp_path)
    real = tmp_path / "elsewhere.jsonl"
    real.write_text(journal.events_path.read_text())
    journal.events_path.unlink()
    journal.events_path.symlink_to(real)
    with pytest.raises(GateStateError):
        open_journal(tmp_path)


def test_result_written_only_for_signal_states(tmp_path):
    journal = open_journal(tmp_path)
    with pytest.raises(GateStateError):
        journal.write_result()
    journal.append("restart_required", {"turn": 6})
    journal.write_result()
    payload = json.loads(journal.result_path.read_text())
    assert payload["status"] == GATE_RESTART_REQUIRED
    assert payload["run_id"] == "run-g"
    assert payload["restart_count"] == 1
    assert payload["schema_version"] == GATE_SCHEMA_VERSION
    assert stat.S_IMODE(os.stat(journal.result_path).st_mode) == 0o600


def test_unknown_event_kind_rejected(tmp_path):
    journal = open_journal(tmp_path)
    with pytest.raises(GateStateError):
        journal.append("mystery_event", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_live_gate.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'civ_mcp.arena.live_gate'`.

- [ ] **Step 3: Write the implementation**

Create `src/civ_mcp/arena/live_gate.py`:

```python
"""Generic coordinator-owned live-gate driver infrastructure.

Design: docs/superpowers/specs/2026-07-17-arena-live-gate-driver-design.md

Scenario-agnostic by contract: this module owns the immutable gate state,
the strict event reducer, write-ahead persistence (events.jsonl is
authoritative; state.json is an atomically replaced derived snapshot;
result.json appears only for restart_required / terminal states), the
scenario registry, and the restart/terminal signal vocabulary. It must not
import unofficial-channel scenario details or Lua builders, and it never
mutates canonical channel state — the gate journal's claim that a phase
succeeded is invalid unless canonical channel state independently proves it
(that proof lives in the scenario module).
"""

from __future__ import annotations

import importlib
import json
import os
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

GATE_SCHEMA_VERSION = 1

GATE_ACTIVE = "active"
GATE_RESTART_REQUIRED = "restart_required"
GATE_FAILED = "failed"
GATE_PASSED = "passed"

_TERMINAL_STATUSES = frozenset({GATE_FAILED, GATE_PASSED})
# restart_required is a checkpoint, not terminal: the resumed process
# transitions it back to active via restart_verified.
_SIGNAL_STATUSES = frozenset({GATE_RESTART_REQUIRED, GATE_FAILED, GATE_PASSED})

GATE_EVENT_KINDS = frozenset({
    "gate_initialized",
    "phase_advanced",
    "data_recorded",
    "observation_recorded",
    "action_planned",
    "action_verified",
    "privacy_asserted",
    "restart_required",
    "restart_verified",
    "gate_failed",
    "gate_passed",
})

_ACTION_PLANNED_FIELDS = ("turn", "player_id", "phase", "name", "source_id", "payload_digest")
_PRIVACY_FIELDS = ("turn", "player_id", "artifact_kind", "input_digest", "result")


class GateStateError(RuntimeError):
    """Invalid gate event, journal, snapshot, or identity."""


@dataclass(frozen=True)
class GateEvent:
    schema_version: int
    sequence: int
    kind: str
    payload: dict


@dataclass(frozen=True)
class GateState:
    schema_version: int
    run_id: str
    scenario: str
    scenario_revision: int
    roles: tuple[tuple[str, int], ...]
    config_fingerprint: dict
    phase: str
    status: str = GATE_ACTIVE
    reason: str = ""
    restart_count: int = 0
    pending_actions: tuple[dict, ...] = ()
    verified_actions: tuple[dict, ...] = ()
    observations: tuple[dict, ...] = ()
    privacy_assertions: tuple[dict, ...] = ()
    data: dict = field(default_factory=dict)
    last_event_sequence: int = 0


def _normalized_roles(roles: Any) -> tuple[tuple[str, int], ...]:
    if not isinstance(roles, dict) or not roles:
        raise GateStateError(f"gate roles must be a non-empty mapping, got {roles!r}")
    return tuple(sorted((str(name), int(pid)) for name, pid in roles.items()))


def reduce_gate_event(state: GateState | None, event: GateEvent) -> GateState:
    if event.schema_version != GATE_SCHEMA_VERSION:
        raise GateStateError(f"unknown gate event schema {event.schema_version!r}")
    if event.kind not in GATE_EVENT_KINDS:
        raise GateStateError(f"unknown gate event kind {event.kind!r}")
    payload = event.payload
    if not isinstance(payload, dict):
        raise GateStateError("gate event payload must be a mapping")

    if event.kind == "gate_initialized":
        if state is not None:
            raise GateStateError("gate_initialized must be the first event")
        if event.sequence != 1:
            raise GateStateError("gate_initialized must have sequence 1")
        return GateState(
            schema_version=GATE_SCHEMA_VERSION,
            run_id=str(payload["run_id"]),
            scenario=str(payload["scenario"]),
            scenario_revision=int(payload["scenario_revision"]),
            roles=_normalized_roles(payload["roles"]),
            config_fingerprint=payload["config_fingerprint"],
            phase=str(payload["phase"]),
            last_event_sequence=1,
        )

    if state is None:
        raise GateStateError("gate journal must begin with gate_initialized")
    if event.sequence != state.last_event_sequence + 1:
        raise GateStateError(
            f"gate event sequence {event.sequence} does not follow {state.last_event_sequence}"
        )
    if state.status in _TERMINAL_STATUSES:
        raise GateStateError(f"gate is terminal ({state.status}); no further events")
    privacy_failed = any(a.get("result") == "FAIL" for a in state.privacy_assertions)
    if privacy_failed and event.kind != "gate_failed":
        raise GateStateError("a failed privacy assertion permits only gate_failed")

    changes: dict[str, Any] = {"last_event_sequence": event.sequence}
    kind = event.kind
    if kind == "phase_advanced":
        if state.pending_actions:
            raise GateStateError("cannot advance phase with unverified planned actions")
        if state.status == GATE_RESTART_REQUIRED:
            raise GateStateError("cannot advance phase before restart_verified")
        changes["phase"] = str(payload["phase"])
    elif kind == "data_recorded":
        data = payload.get("data")
        if not isinstance(data, dict):
            raise GateStateError("data_recorded payload.data must be a mapping")
        changes["data"] = {**state.data, **data}
    elif kind == "observation_recorded":
        changes["observations"] = state.observations + (payload,)
    elif kind == "action_planned":
        missing = [name for name in _ACTION_PLANNED_FIELDS if name not in payload]
        if missing:
            raise GateStateError(f"action_planned missing field(s) {missing}")
        source_id = payload["source_id"]
        known = state.pending_actions + state.verified_actions
        if any(entry["source_id"] == source_id for entry in known):
            raise GateStateError(f"duplicate planned source {source_id!r}")
        changes["pending_actions"] = state.pending_actions + (payload,)
    elif kind == "action_verified":
        source_id = payload.get("source_id")
        matches = [entry for entry in state.pending_actions if entry["source_id"] == source_id]
        if len(matches) != 1:
            raise GateStateError(f"action_verified for unplanned source {source_id!r}")
        changes["pending_actions"] = tuple(
            entry for entry in state.pending_actions if entry["source_id"] != source_id
        )
        changes["verified_actions"] = state.verified_actions + (payload,)
    elif kind == "privacy_asserted":
        missing = [name for name in _PRIVACY_FIELDS if name not in payload]
        if missing:
            raise GateStateError(f"privacy_asserted missing field(s) {missing}")
        if payload["result"] not in ("PASS", "FAIL"):
            raise GateStateError("privacy result must be PASS or FAIL")
        changes["privacy_assertions"] = state.privacy_assertions + (payload,)
    elif kind == "restart_required":
        if state.restart_count >= 1:
            raise GateStateError("a second restart request is not allowed")
        if state.pending_actions:
            raise GateStateError("cannot request restart with unverified planned actions")
        changes["status"] = GATE_RESTART_REQUIRED
        changes["restart_count"] = state.restart_count + 1
    elif kind == "restart_verified":
        if state.status != GATE_RESTART_REQUIRED:
            raise GateStateError("restart_verified requires restart_required status")
        changes["status"] = GATE_ACTIVE
    elif kind == "gate_failed":
        changes["status"] = GATE_FAILED
        changes["reason"] = str(payload.get("reason", ""))
    elif kind == "gate_passed":
        if state.status != GATE_ACTIVE:
            raise GateStateError("gate_passed requires an active gate")
        if state.pending_actions:
            raise GateStateError("cannot pass with unverified planned actions")
        changes["status"] = GATE_PASSED
    return replace(state, **changes)


# --- Private persistence (mirrors ChannelRuntime's ownership rules) ---------

def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise GateStateError(f"gate path {path} is not a directory")
    os.chmod(path, 0o700)


def _require_regular_file(path: Path) -> None:
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise GateStateError(f"gate file {path} is not a regular file")


def _ensure_private_file(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    _require_regular_file(path)
    os.chmod(path, 0o600)


def _atomic_private_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _state_to_dict(state: GateState) -> dict:
    return {
        "schema_version": state.schema_version,
        "run_id": state.run_id,
        "scenario": state.scenario,
        "scenario_revision": state.scenario_revision,
        "roles": [list(pair) for pair in state.roles],
        "config_fingerprint": state.config_fingerprint,
        "phase": state.phase,
        "status": state.status,
        "reason": state.reason,
        "restart_count": state.restart_count,
        "pending_actions": list(state.pending_actions),
        "verified_actions": list(state.verified_actions),
        "observations": list(state.observations),
        "privacy_assertions": list(state.privacy_assertions),
        "data": state.data,
        "last_event_sequence": state.last_event_sequence,
    }


def _read_journal(path: Path) -> tuple[GateEvent, ...]:
    events: list[GateEvent] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GateStateError(f"cannot read gate journal: {exc}") from exc
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GateStateError(f"invalid gate journal line {line_number}: {exc}") from exc
        if not isinstance(raw, dict):
            raise GateStateError(f"invalid gate journal line {line_number}: not a mapping")
        try:
            events.append(GateEvent(
                schema_version=int(raw["schema_version"]),
                sequence=int(raw["sequence"]),
                kind=str(raw["kind"]),
                payload=raw["payload"],
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise GateStateError(f"invalid gate journal line {line_number}: {exc}") from exc
    return tuple(events)


class LiveGateJournal:
    """Sole writer for one run's gate journal, snapshot, and result."""

    def __init__(self, gate_dir: Path, state: GateState) -> None:
        self.gate_dir = gate_dir
        self.events_path = gate_dir / "events.jsonl"
        self.state_path = gate_dir / "state.json"
        self.result_path = gate_dir / "result.json"
        self.state = state

    @classmethod
    def open(
        cls,
        run_dir: Path,
        *,
        run_id: str,
        scenario: str,
        scenario_revision: int,
        roles: dict[str, int],
        config_fingerprint: dict,
        initial_phase: str,
    ) -> "LiveGateJournal":
        gate_dir = Path(run_dir) / "live_gate"
        _ensure_private_directory(gate_dir)
        events_path = gate_dir / "events.jsonl"
        _ensure_private_file(events_path)
        events = _read_journal(events_path)

        if not events:
            journal = cls.__new__(cls)
            journal.gate_dir = gate_dir
            journal.events_path = events_path
            journal.state_path = gate_dir / "state.json"
            journal.result_path = gate_dir / "result.json"
            journal.state = None
            journal._append_event("gate_initialized", {
                "run_id": run_id,
                "scenario": scenario,
                "scenario_revision": scenario_revision,
                "roles": dict(roles),
                "config_fingerprint": config_fingerprint,
                "phase": initial_phase,
            })
            return journal

        state: GateState | None = None
        for event in events:
            state = reduce_gate_event(state, event)
        expected = (run_id, scenario, int(scenario_revision), _normalized_roles(dict(roles)))
        actual = (state.run_id, state.scenario, state.scenario_revision, state.roles)
        if expected != actual:
            raise GateStateError(
                f"gate identity mismatch: journal has {actual}, configuration wants {expected}"
            )
        if state.config_fingerprint != config_fingerprint:
            raise GateStateError("gate configuration fingerprint mismatch on resume")

        state_path = gate_dir / "state.json"
        if state_path.exists() or state_path.is_symlink():
            _require_regular_file(state_path)
            try:
                snapshot = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise GateStateError(f"invalid gate snapshot: {exc}") from exc
            if int(snapshot.get("last_event_sequence", -1)) > state.last_event_sequence:
                raise GateStateError("gate snapshot is newer than the write-ahead journal")

        journal = cls(gate_dir, state)
        journal._write_snapshot()
        return journal

    def append(self, kind: str, payload: dict) -> GateEvent:
        return self._append_event(kind, payload)

    def _append_event(self, kind: str, payload: dict) -> GateEvent:
        sequence = 1 if self.state is None else self.state.last_event_sequence + 1
        event = GateEvent(GATE_SCHEMA_VERSION, sequence, kind, payload)
        new_state = reduce_gate_event(self.state, event)  # validate BEFORE persisting
        line = json.dumps(
            {
                "schema_version": event.schema_version,
                "sequence": event.sequence,
                "kind": event.kind,
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with open(self.events_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.state = new_state
        self._write_snapshot()
        return event

    def _write_snapshot(self) -> None:
        _atomic_private_json(self.state_path, _state_to_dict(self.state))

    def write_result(self) -> None:
        if self.state.status not in _SIGNAL_STATUSES:
            raise GateStateError(
                f"result.json is written only for {sorted(_SIGNAL_STATUSES)}, "
                f"not {self.state.status!r}"
            )
        _atomic_private_json(self.result_path, {
            "schema_version": GATE_SCHEMA_VERSION,
            "run_id": self.state.run_id,
            "scenario": self.state.scenario,
            "scenario_revision": self.state.scenario_revision,
            "status": self.state.status,
            "phase": self.state.phase,
            "reason": self.state.reason,
            "restart_count": self.state.restart_count,
        })
```

Note the `test_terminal_states_reject_further_events` test opens a second journal at `tmp_path.parent / "p2"` — pytest guarantees `tmp_path.parent` is writable and test-unique.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_live_gate.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/live_gate.py tests/arena/test_live_gate.py
git commit -m "feat(arena): generic live-gate state, strict reducer, write-ahead journal"
```

---

### Task 2: Scenario registry and driver resolution

**Files:**
- Modify: `src/civ_mcp/arena/live_gate.py` (append)
- Test: `tests/arena/test_live_gate.py` (append)

**Interfaces:**
- Consumes: Task 1's module.
- Produces:
  - `@dataclass(frozen=True) ScenarioMeta(name: str, revision: int, role_contracts: tuple[tuple[str, str], ...], minimum_captures: Callable[[Any], int], create_driver: Callable[[Any], Any])` — `role_contracts` pairs each required role name with the required `PlayerSpec.driver_kind()` string.
  - `register_scenario(meta: ScenarioMeta) -> None` (duplicate name → `ValueError`).
  - `resolve_scenario(name: str) -> ScenarioMeta` (imports `_BUILTIN_SCENARIO_MODULES` lazily; unknown → `ValueError`).
  - `resolve_live_gate_driver(config) -> Any | None` (`None` when `config.live_gate.enabled` is false or the attribute is missing).
  - `_SCENARIOS: dict[str, ScenarioMeta]` module dict (tests monkeypatch it) and `_BUILTIN_SCENARIO_MODULES: tuple[str, ...]` (empty until Task 5 adds `"civ_mcp.arena.live_gate_channels"`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_live_gate.py`:

```python
from types import SimpleNamespace

from civ_mcp.arena import live_gate
from civ_mcp.arena.live_gate import (
    ScenarioMeta,
    register_scenario,
    resolve_live_gate_driver,
    resolve_scenario,
)


def fake_meta(name="fake_gate_v1", **overrides):
    kwargs = dict(
        name=name,
        revision=1,
        role_contracts=(("actor", "in_process"), ("observer", "scripted")),
        minimum_captures=lambda config: 6,
        create_driver=lambda config: SimpleNamespace(config=config, kind="fake-driver"),
    )
    kwargs.update(overrides)
    return ScenarioMeta(**kwargs)


def test_register_and_resolve_scenario(monkeypatch):
    monkeypatch.setattr(live_gate, "_SCENARIOS", {})
    meta = fake_meta()
    register_scenario(meta)
    assert resolve_scenario("fake_gate_v1") is meta
    with pytest.raises(ValueError):
        register_scenario(fake_meta())  # duplicate name


def test_resolve_unknown_scenario_rejected(monkeypatch):
    monkeypatch.setattr(live_gate, "_SCENARIOS", {})
    with pytest.raises(ValueError, match="unknown live-gate scenario"):
        resolve_scenario("nope_v1")


def test_resolve_live_gate_driver_disabled_returns_none():
    config = SimpleNamespace(live_gate=SimpleNamespace(enabled=False, scenario="", roles=()))
    assert resolve_live_gate_driver(config) is None
    assert resolve_live_gate_driver(SimpleNamespace()) is None  # attribute missing


def test_resolve_live_gate_driver_enabled_creates_driver(monkeypatch):
    monkeypatch.setattr(live_gate, "_SCENARIOS", {})
    register_scenario(fake_meta())
    config = SimpleNamespace(
        live_gate=SimpleNamespace(enabled=True, scenario="fake_gate_v1", roles=(("actor", 1),))
    )
    driver = resolve_live_gate_driver(config)
    assert driver.kind == "fake-driver"
    assert driver.config is config
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_live_gate.py -v -k "scenario or driver"`
Expected: FAIL — `ImportError: cannot import name 'ScenarioMeta'`.

- [ ] **Step 3: Write the implementation**

Append to `src/civ_mcp/arena/live_gate.py`:

```python
# --- Scenario registry -------------------------------------------------------

@dataclass(frozen=True)
class ScenarioMeta:
    """Static scenario metadata used by configuration validation and driver
    construction. role_contracts pairs each required role name with the
    required PlayerSpec.driver_kind() ('in_process' | 'cli' | 'scripted').
    minimum_captures computes the scenario's minimum seat-capture budget from
    an ArenaConfig (so budget validation tracks channel-rule changes)."""

    name: str
    revision: int
    role_contracts: tuple[tuple[str, str], ...]
    minimum_captures: Callable[[Any], int]
    create_driver: Callable[[Any], Any]


_SCENARIOS: dict[str, ScenarioMeta] = {}

# Modules imported lazily by resolve_scenario so configuration validation can
# see built-in scenarios without arena.py having imported them first. Import
# is idempotent (sys.modules cache); each module registers itself at import.
_BUILTIN_SCENARIO_MODULES: tuple[str, ...] = ()


def register_scenario(meta: ScenarioMeta) -> None:
    if meta.name in _SCENARIOS:
        raise ValueError(f"live-gate scenario {meta.name!r} is already registered")
    _SCENARIOS[meta.name] = meta


def _ensure_builtin_scenarios() -> None:
    for module in _BUILTIN_SCENARIO_MODULES:
        importlib.import_module(module)


def resolve_scenario(name: str) -> ScenarioMeta:
    _ensure_builtin_scenarios()
    meta = _SCENARIOS.get(name)
    if meta is None:
        raise ValueError(
            f"unknown live-gate scenario {name!r}; registered: {sorted(_SCENARIOS)}"
        )
    return meta


def resolve_live_gate_driver(config: Any) -> Any | None:
    """Return the configured scenario driver, or None when the gate is
    disabled. This is the single disabled-path switch: a None return means
    policy construction, preflight, capture, and transcript behavior all
    follow the existing arena path untouched."""

    gate = getattr(config, "live_gate", None)
    if gate is None or not getattr(gate, "enabled", False):
        return None
    return resolve_scenario(gate.scenario).create_driver(config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_live_gate.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/live_gate.py tests/arena/test_live_gate.py
git commit -m "feat(arena): live-gate scenario registry and driver resolution"
```

---

### Task 3: `LiveGateOptions` on `ArenaConfig` and cross-field validation

**Files:**
- Modify: `src/civ_mcp/arena/config.py` (options dataclass near `ChannelOptions` ~line 64; validation in `validate_arena_config` at line 255; `scripted` comment at lines 31-34)
- Test: `tests/arena/test_config.py` (append)

**Interfaces:**
- Consumes: `ScenarioMeta`, `resolve_scenario`, `_SCENARIOS` from Task 2 (lazy import inside the validator only — `config.py` must stay import-light and `live_gate.py` already imports nothing from config, so no cycle).
- Produces:
  - `@dataclass(frozen=True) LiveGateOptions(enabled: bool = False, scenario: str = "", roles: tuple[tuple[str, int], ...] = ())`.
  - `ArenaConfig.live_gate: LiveGateOptions` field (default factory).
  - `_validate_live_gate(config) -> None` called from `validate_arena_config` — later tasks and the YAML loader rely on `validate_arena_config` rejecting every invalid gate config listed in the spec's "Validation requires" bullet list.

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_config.py` (it already imports `ArenaConfig`, `PlayerSpec`, `validate_arena_config`; add the missing imports at the top of the new block):

```python
import pytest

from civ_mcp.arena import live_gate
from civ_mcp.arena.config import (
    ArenaConfig,
    AttentionOptions,
    ChannelOptions,
    CivOptions,
    LiveGateOptions,
    PlayerSpec,
    validate_arena_config,
)
from civ_mcp.arena.live_gate import ScenarioMeta


def _gate_spec(player_id, provider, model=""):
    return PlayerSpec(
        player_id, provider, model,
        options=CivOptions(channels=ChannelOptions(enabled=True)),
    )


def _gate_config(**overrides):
    kwargs = dict(
        players=[
            _gate_spec(1, "local", "m"),
            _gate_spec(2, "cli-codex"),
            _gate_spec(3, "scripted"),
        ],
        max_puppet_turns=36,
        max_game_turns=36,
        run_id="run-gate",
        live_gate=LiveGateOptions(
            enabled=True,
            scenario="fake_gate_v1",
            roles=(("api_actor", 1), ("cli_actor", 2), ("privacy_observer", 3)),
        ),
    )
    kwargs.update(overrides)
    return ArenaConfig(**kwargs)


@pytest.fixture
def gate_registry(monkeypatch):
    meta = ScenarioMeta(
        name="fake_gate_v1",
        revision=1,
        role_contracts=(
            ("api_actor", "in_process"),
            ("cli_actor", "cli"),
            ("privacy_observer", "scripted"),
        ),
        minimum_captures=lambda config: 27,
        create_driver=lambda config: object(),
    )
    monkeypatch.setattr(live_gate, "_SCENARIOS", {meta.name: meta})
    return meta


def test_live_gate_defaults_disabled_and_valid():
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")])
    assert cfg.live_gate == LiveGateOptions()
    validate_arena_config(cfg)  # must not raise


def test_live_gate_disabled_cannot_carry_scenario_or_roles():
    cfg = ArenaConfig(
        players=[PlayerSpec(1, "local", "m")],
        live_gate=LiveGateOptions(enabled=False, scenario="fake_gate_v1"),
    )
    with pytest.raises(ValueError, match="cannot carry"):
        validate_arena_config(cfg)


def test_live_gate_valid_config_passes(gate_registry):
    validate_arena_config(_gate_config())


def test_live_gate_unknown_scenario_rejected(monkeypatch):
    monkeypatch.setattr(live_gate, "_SCENARIOS", {})
    with pytest.raises(ValueError, match="unknown live-gate scenario"):
        validate_arena_config(_gate_config())


def test_live_gate_roles_must_match_contract_exactly(gate_registry):
    cfg = _gate_config()
    missing = LiveGateOptions(enabled=True, scenario="fake_gate_v1",
                              roles=(("api_actor", 1), ("cli_actor", 2)))
    with pytest.raises(ValueError, match="exactly"):
        validate_arena_config(_gate_config(live_gate=missing))
    extra = LiveGateOptions(enabled=True, scenario="fake_gate_v1",
                            roles=cfg.live_gate.roles + (("stranger", 3),))
    with pytest.raises(ValueError, match="exactly"):
        validate_arena_config(_gate_config(live_gate=extra))


def test_live_gate_role_ids_distinct_and_configured(gate_registry):
    dup = LiveGateOptions(enabled=True, scenario="fake_gate_v1",
                          roles=(("api_actor", 1), ("cli_actor", 1), ("privacy_observer", 3)))
    with pytest.raises(ValueError, match="distinct"):
        validate_arena_config(_gate_config(live_gate=dup))
    ghost = LiveGateOptions(enabled=True, scenario="fake_gate_v1",
                            roles=(("api_actor", 1), ("cli_actor", 2), ("privacy_observer", 9)))
    with pytest.raises(ValueError, match="not a configured civ"):
        validate_arena_config(_gate_config(live_gate=ghost))


def test_live_gate_driver_kind_contract_enforced(gate_registry):
    players = [
        _gate_spec(1, "cli-claude"),   # api_actor must be in_process
        _gate_spec(2, "cli-codex"),
        _gate_spec(3, "scripted"),
    ]
    with pytest.raises(ValueError, match="driver kind"):
        validate_arena_config(_gate_config(players=players))


def test_live_gate_roles_must_be_channel_enabled(gate_registry):
    players = [
        PlayerSpec(1, "local", "m"),   # channels disabled
        _gate_spec(2, "cli-codex"),
        _gate_spec(3, "scripted"),
    ]
    with pytest.raises(ValueError, match="channel-enabled"):
        validate_arena_config(_gate_config(players=players))


def test_live_gate_rejects_attention_on_gate_civ(gate_registry):
    noisy = PlayerSpec(
        1, "local", "m",
        options=CivOptions(
            channels=ChannelOptions(enabled=True),
            attention=AttentionOptions(mode="auto"),
        ),
    )
    players = [noisy, _gate_spec(2, "cli-codex"), _gate_spec(3, "scripted")]
    with pytest.raises(ValueError, match="attention"):
        validate_arena_config(_gate_config(players=players))


def test_live_gate_rejects_seat_zero_entry(gate_registry):
    players = [
        _gate_spec(0, "scripted"),
        _gate_spec(1, "local", "m"),
        _gate_spec(2, "cli-codex"),
        _gate_spec(3, "scripted"),
    ]
    with pytest.raises(ValueError, match="seat-zero"):
        validate_arena_config(_gate_config(players=players))


def test_live_gate_rejects_unbound_extra_civ(gate_registry):
    players = [
        _gate_spec(1, "local", "m"),
        _gate_spec(2, "cli-codex"),
        _gate_spec(3, "scripted"),
        _gate_spec(4, "local", "m2"),
    ]
    with pytest.raises(ValueError, match="unbound"):
        validate_arena_config(_gate_config(players=players))


def test_live_gate_requires_explicit_run_id(gate_registry):
    with pytest.raises(ValueError, match="run_id"):
        validate_arena_config(_gate_config(run_id=""))


def test_live_gate_budgets_must_meet_scenario_minimum(gate_registry):
    with pytest.raises(ValueError, match="at least 27"):
        validate_arena_config(_gate_config(max_puppet_turns=26))
    with pytest.raises(ValueError, match="at least 27"):
        validate_arena_config(_gate_config(max_game_turns=26))
    # max_game_turns=0 means uncapped and trivially meets the minimum.
    validate_arena_config(_gate_config(max_game_turns=0))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_config.py -v -k live_gate`
Expected: FAIL — `ImportError: cannot import name 'LiveGateOptions'`.

- [ ] **Step 3: Write the implementation**

In `src/civ_mcp/arena/config.py`:

(a) Update the `scripted` provider comment (lines 31-34) to name its second sanctioned use:

```python
# `scripted` selects the deterministic no-LLM ScriptedPolicy for that seat,
# needs no backend/CLI/tuner handoff, and has exactly two sanctioned uses:
# the mixed stage-1 seat-0 live gate (Task 9, test-only) and the live-gate
# passive privacy observer (spec 2026-07-17).
```

(b) Add the options dataclass directly after `ChannelOptions` (~line 67):

```python
@dataclass(frozen=True)
class LiveGateOptions:
    """Deterministic attended live-gate scenario switch (spec 2026-07-17).

    roles is a sorted tuple of (role_name, player_id) pairs so the frozen
    dataclass stays hashable and fingerprint-stable."""

    enabled: bool = False
    scenario: str = ""
    roles: tuple[tuple[str, int], ...] = ()
```

(c) Add the field to `ArenaConfig` (after `channel_rules`, ~line 223):

```python
    live_gate: LiveGateOptions = field(default_factory=LiveGateOptions)
```

(d) Add the validator and call it from `validate_arena_config` (append the call after the existing seat-0 attention check):

```python
def _validate_live_gate(config: "ArenaConfig") -> None:
    gate = config.live_gate
    if not gate.enabled:
        if gate.scenario or gate.roles:
            raise ValueError(
                "disabled live_gate cannot carry a scenario or roles"
            )
        return
    # Lazy import: config.py stays import-light and live_gate.py imports
    # nothing from config at module scope, so there is no cycle.
    from civ_mcp.arena.live_gate import resolve_scenario

    meta = resolve_scenario(gate.scenario)
    roles = dict(gate.roles)
    expected = sorted(name for name, _kind in meta.role_contracts)
    if sorted(roles) != expected:
        raise ValueError(
            f"live_gate.roles must contain exactly {expected}, got {sorted(roles)}"
        )
    pids = list(roles.values())
    if len(pids) != len(set(pids)):
        raise ValueError(f"live_gate role player ids must be distinct, got {pids}")
    if any(spec.player_id == 0 for spec in config.players):
        # The gate relies on the human owning seat 0 across the restart
        # boundary; seat-zero piloting cannot be combined with it.
        raise ValueError("live_gate cannot be combined with a seat-zero (player 0) entry")
    specs = {spec.player_id: spec for spec in config.players}
    for role, kind in meta.role_contracts:
        pid = roles[role]
        spec = specs.get(pid)
        if spec is None:
            raise ValueError(f"live_gate role {role!r} player {pid} is not a configured civ")
        if spec.driver_kind() != kind:
            raise ValueError(
                f"live_gate role {role!r} requires driver kind {kind!r}, "
                f"got {spec.driver_kind()!r}"
            )
        if not spec.options.channels.enabled:
            raise ValueError(f"live_gate role {role!r} player {pid} must be channel-enabled")
        if spec.options.attention.mode != "off":
            # Turn skipping would starve the phase machine of admissions.
            raise ValueError(
                f"live_gate role {role!r} player {pid} requires attention.mode 'off'"
            )
    unbound = sorted(spec.player_id for spec in config.players if spec.player_id not in set(pids))
    if unbound:
        # Gate mode constructs no model-backed policies, so a configured civ
        # without a role would have no policy at all.
        raise ValueError(f"live_gate mode admits only gate-role civs; unbound players {unbound}")
    if not config.run_id:
        raise ValueError("live_gate requires an explicit run_id")
    minimum = meta.minimum_captures(config)
    if config.max_puppet_turns < minimum:
        raise ValueError(
            f"live_gate scenario {gate.scenario!r} needs at least {minimum} puppet turns, "
            f"got {config.max_puppet_turns}"
        )
    if config.max_game_turns and config.max_game_turns < minimum:
        raise ValueError(
            f"live_gate scenario {gate.scenario!r} needs at least {minimum} game turns, "
            f"got {config.max_game_turns}"
        )
```

And at the end of `validate_arena_config`:

```python
    _validate_live_gate(config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_config.py tests/arena/test_live_gate.py -v`
Expected: all PASS (existing config tests untouched).

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/config.py tests/arena/test_config.py
git commit -m "feat(arena): LiveGateOptions and cross-field live-gate validation"
```

---

### Task 4: Strict `live_gate` experiment-block parsing

**Files:**
- Modify: `src/civ_mcp/arena/experiment.py` (`_TOP_KEYS` at line 44; new `_parse_live_gate`; wiring in `load_experiment`'s `replace(...)` at ~line 453)
- Test: `tests/arena/test_experiment.py` (append)

**Interfaces:**
- Consumes: `LiveGateOptions` from Task 3; existing `_validate_mapping_keys`, `_err`, `_non_blank_string`, `_int` helpers.
- Produces: `load_experiment` returns an `ArenaConfig` whose `live_gate` field is populated; `validate_arena_config` (already called at the end of `load_experiment`) then applies Task 3's cross-field rules. YAML strict-bool loading already rejects `yes/on/1` via `_UniqueKeySafeLoader`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_experiment.py` (it already imports `load_experiment` and writes YAML via `tmp_path`; reuse its local helper style):

```python
from civ_mcp.arena import live_gate as live_gate_module
from civ_mcp.arena.config import LiveGateOptions
from civ_mcp.arena.live_gate import ScenarioMeta

GATE_CIVS = """
civs:
  - player: 1
    provider: local
    model: m
    channels: {enabled: true}
  - player: 2
    provider: cli-codex
    channels: {enabled: true}
  - player: 3
    provider: scripted
    channels: {enabled: true}
"""


def _write_gate_yaml(tmp_path, live_gate_block, *, run_id="run-gate"):
    text = (
        f"run_id: {run_id}\n"
        "max_puppet_turns: 36\n"
        "max_game_turns: 36\n"
        f"{live_gate_block}\n"
        f"{GATE_CIVS}"
    )
    path = tmp_path / "gate.yaml"
    path.write_text(text)
    return path


@pytest.fixture
def registered_gate(monkeypatch):
    meta = ScenarioMeta(
        name="fake_gate_v1",
        revision=1,
        role_contracts=(
            ("api_actor", "in_process"),
            ("cli_actor", "cli"),
            ("privacy_observer", "scripted"),
        ),
        minimum_captures=lambda config: 27,
        create_driver=lambda config: object(),
    )
    monkeypatch.setattr(live_gate_module, "_SCENARIOS", {meta.name: meta})
    return meta


def test_live_gate_block_parses_and_validates(tmp_path, registered_gate):
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: true\n"
        "  scenario: fake_gate_v1\n"
        "  roles:\n"
        "    api_actor: 1\n"
        "    cli_actor: 2\n"
        "    privacy_observer: 3\n"
    ))
    cfg = load_experiment(path)
    assert cfg.live_gate == LiveGateOptions(
        enabled=True,
        scenario="fake_gate_v1",
        roles=(("api_actor", 1), ("cli_actor", 2), ("privacy_observer", 3)),
    )


def test_live_gate_absent_defaults_disabled(tmp_path):
    path = _write_gate_yaml(tmp_path, "")
    cfg = load_experiment(path)
    assert cfg.live_gate == LiveGateOptions()


def test_live_gate_requires_exact_boolean_enabled(tmp_path):
    path = _write_gate_yaml(tmp_path, "live_gate:\n  enabled: yes\n")
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        load_experiment(path)
    path = _write_gate_yaml(tmp_path, "live_gate:\n  scenario: fake_gate_v1\n")
    with pytest.raises(ValueError, match="enabled is required"):
        load_experiment(path)


def test_live_gate_disabled_cannot_carry_scenario_or_roles(tmp_path):
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: false\n"
        "  scenario: fake_gate_v1\n"
    ))
    with pytest.raises(ValueError, match="disabled live_gate"):
        load_experiment(path)


def test_live_gate_unknown_key_rejected(tmp_path):
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: true\n"
        "  scenario: fake_gate_v1\n"
        "  roles: {api_actor: 1, cli_actor: 2, privacy_observer: 3}\n"
        "  surprise: 1\n"
    ))
    with pytest.raises(ValueError, match="unknown key"):
        load_experiment(path)


def test_live_gate_roles_must_be_string_to_int_mapping(tmp_path, registered_gate):
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: true\n"
        "  scenario: fake_gate_v1\n"
        "  roles: {api_actor: one, cli_actor: 2, privacy_observer: 3}\n"
    ))
    with pytest.raises(ValueError, match="must be an integer"):
        load_experiment(path)
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: true\n"
        "  scenario: fake_gate_v1\n"
        "  roles: {}\n"
    ))
    with pytest.raises(ValueError, match="non-empty mapping"):
        load_experiment(path)


def test_live_gate_enabled_requires_scenario(tmp_path):
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: true\n"
        "  roles: {api_actor: 1, cli_actor: 2, privacy_observer: 3}\n"
    ))
    with pytest.raises(ValueError, match="requires a scenario"):
        load_experiment(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_experiment.py -v -k live_gate`
Expected: the enabled-happy-path test FAILS with `unknown key(s) ['live_gate']` (strict `_TOP_KEYS`); default-disabled test passes trivially — that is fine.

- [ ] **Step 3: Write the implementation**

In `src/civ_mcp/arena/experiment.py`:

(a) Import `LiveGateOptions` in the existing `from civ_mcp.arena.config import (...)` block, and add a module default next to the other defaults (~line 55): `_LIVE_GATE_DEFAULTS = LiveGateOptions()`.

(b) Add `"live_gate"` to `_TOP_KEYS`.

(c) Add the parser (after `_parse_channel_rules`):

```python
def _parse_live_gate(raw: object) -> LiveGateOptions:
    if not isinstance(raw, dict):
        raise _err("live_gate", f"must be a mapping, got {raw!r}")
    _validate_mapping_keys("live_gate", raw, {"enabled", "scenario", "roles"})
    if "enabled" not in raw:
        raise _err("live_gate", "enabled is required")
    enabled = raw["enabled"]
    if not isinstance(enabled, bool):
        raise _err("live_gate", f"enabled must be a boolean, got {enabled!r}")
    if not enabled:
        if "scenario" in raw or "roles" in raw:
            raise _err("live_gate", "disabled live_gate cannot carry scenario or roles")
        return LiveGateOptions()
    if "scenario" not in raw:
        raise _err("live_gate", "enabled live_gate requires a scenario")
    scenario = _non_blank_string("live_gate", "scenario", raw["scenario"])
    roles_raw = raw.get("roles")
    if not isinstance(roles_raw, dict) or not roles_raw:
        raise _err("live_gate", f"roles must be a non-empty mapping, got {roles_raw!r}")
    roles: list[tuple[str, int]] = []
    for name, pid in roles_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise _err("live_gate", f"role names must be non-empty strings, got {name!r}")
        roles.append((name, _int("live_gate", f"roles.{name}", pid)))
    return LiveGateOptions(enabled=True, scenario=scenario, roles=tuple(sorted(roles)))
```

(d) Wire it into `load_experiment`'s `replace(...)` call:

```python
        live_gate=(
            _LIVE_GATE_DEFAULTS
            if "live_gate" not in data
            else _parse_live_gate(data["live_gate"])
        ),
```

Note: `_UniqueKeySafeLoader` turns `enabled: yes` into a `_LegacyBooleanToken`, which is not a `bool`, so the exact-boolean test passes with no extra code. The registered-scenario/roles/budget cross-checks run via the `validate_arena_config(cfg)` call already at the end of `load_experiment`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_experiment.py tests/arena/test_config.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/experiment.py tests/arena/test_experiment.py
git commit -m "feat(arena): strict top-level live_gate experiment block"
```

---

### Task 5: Scenario metadata, registration, fingerprint, and canary

**Files:**
- Create: `src/civ_mcp/arena/live_gate_channels.py`
- Modify: `src/civ_mcp/arena/live_gate.py` (`_BUILTIN_SCENARIO_MODULES`)
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Consumes: `ScenarioMeta`, `register_scenario`, `resolve_scenario` (Task 2); `LiveGateOptions` (Task 3).
- Produces (all later scenario tasks build inside this module):
  - `SCENARIO_NAME = "unofficial_channels_core_v1"`, `SCENARIO_REVISION = 1`.
  - `ROLE_API = "api_actor"`, `ROLE_CLI = "cli_actor"`, `ROLE_OBSERVER = "privacy_observer"`, `ROLE_CONTRACTS` tuple pairing each with `in_process`/`cli`/`scripted`.
  - Fixed parameters: `PAYMENT_GOLD = 1`, `UPFRONT_WITHIN = 1`, `ON_DELIVERY_WITHIN = 1`, `TRADE_KINDS = ("trade_route",)`, `MIN_GOLD = 0`, `UPFRONT_PROPOSAL_TEXT`, `ON_DELIVERY_PROPOSAL_TEXT`.
  - The 15 phase constants (`PHASE_PREFLIGHT` ... `PHASE_VERIFY_TERMINAL_GATE`) exactly matching the spec's phase table names.
  - `minimum_captures(config) -> int`, `gate_config_fingerprint(config) -> dict`, `canary_text(run_id, fingerprint) -> str`.
  - `class ChannelsCoreDriver` constructor `(config)` (behavior filled in Tasks 6-10).
  - Module-level `register_scenario(ScenarioMeta(...))` and the `_BUILTIN_SCENARIO_MODULES` entry.

- [ ] **Step 1: Write the failing tests**

Create `tests/arena/test_live_gate_channels.py`:

```python
import pytest

from civ_mcp.arena.config import (
    ArenaConfig,
    AttentionOptions,
    ChannelOptions,
    ChannelRules,
    CivOptions,
    LiveGateOptions,
    PlayerSpec,
    validate_arena_config,
)
from civ_mcp.arena.live_gate import resolve_scenario
from civ_mcp.arena import live_gate_channels as lgc


def gate_spec(player_id, provider, model=""):
    return PlayerSpec(
        player_id, provider, model,
        options=CivOptions(channels=ChannelOptions(enabled=True)),
    )


def gate_config(**overrides):
    kwargs = dict(
        players=[
            gate_spec(1, "local", "m"),
            gate_spec(2, "cli-codex"),
            gate_spec(3, "scripted"),
        ],
        max_puppet_turns=36,
        max_game_turns=36,
        run_id="arena-channels-core-gate-v1",
        channel_rules=ChannelRules(
            acceptance_turns=3, funding_turns=2, payment_response_turns=2,
        ),
        live_gate=LiveGateOptions(
            enabled=True,
            scenario=lgc.SCENARIO_NAME,
            roles=(("api_actor", 1), ("cli_actor", 2), ("privacy_observer", 3)),
        ),
    )
    kwargs.update(overrides)
    return ArenaConfig(**kwargs)


def test_scenario_registered_with_contracts():
    meta = resolve_scenario("unofficial_channels_core_v1")
    assert meta.revision == lgc.SCENARIO_REVISION
    assert meta.role_contracts == (
        ("api_actor", "in_process"),
        ("cli_actor", "cli"),
        ("privacy_observer", "scripted"),
    )


def test_minimum_captures_is_27_for_smoke_rules():
    # 9 expected rounds x 3 seats with the checked-in rules (spec budget note).
    assert lgc.minimum_captures(gate_config()) == 27


def test_minimum_captures_tracks_funding_turns():
    cfg = gate_config(channel_rules=ChannelRules(
        acceptance_turns=3, funding_turns=4, payment_response_turns=2,
    ))
    assert lgc.minimum_captures(cfg) == 33  # two extra withheld rounds x 3 seats


def test_gate_config_validates_end_to_end():
    validate_arena_config(gate_config())  # real registry entry, no fakes


def test_fingerprint_covers_identity_and_rules():
    cfg = gate_config()
    fp = lgc.gate_config_fingerprint(cfg)
    assert fp["scenario"] == "unofficial_channels_core_v1"
    assert fp["scenario_revision"] == lgc.SCENARIO_REVISION
    assert fp["run_id"] == "arena-channels-core-gate-v1"
    assert fp["roles"] == {"api_actor": 1, "cli_actor": 2, "privacy_observer": 3}
    assert fp["driver_kinds"] == {"1": "in_process", "2": "cli", "3": "scripted"}
    assert fp["channel_rules"] == cfg.channel_rules.fingerprint()
    assert fp["parameters"]["payment_gold"] == 1
    other_rules = gate_config(channel_rules=ChannelRules(funding_turns=3))
    assert lgc.gate_config_fingerprint(other_rules) != fp


def test_canary_deterministic_and_fingerprint_bound():
    cfg = gate_config()
    fp = lgc.gate_config_fingerprint(cfg)
    text = lgc.canary_text(cfg.run_id, fp)
    assert text == lgc.canary_text(cfg.run_id, fp)
    assert text.startswith("GATE-CANARY-")
    assert len(text) > len("GATE-CANARY-") + 16
    assert lgc.canary_text("other-run", fp) != text


def test_create_driver_binds_roles():
    driver = resolve_scenario(lgc.SCENARIO_NAME).create_driver(gate_config())
    assert isinstance(driver, lgc.ChannelsCoreDriver)
    assert driver.role_pid == {"api_actor": 1, "cli_actor": 2, "privacy_observer": 3}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py -v`
Expected: FAIL at import — no module `live_gate_channels`.

- [ ] **Step 3: Write the implementation**

Create `src/civ_mcp/arena/live_gate_channels.py`:

```python
"""unofficial_channels_core_v1 — the first registered live-gate scenario.

Design: docs/superpowers/specs/2026-07-17-arena-live-gate-driver-design.md

This module plans deterministic per-seat channel inputs and verifies every
transition against canonical ChannelRuntime state. It depends only on the
public channel runtime/protocol/term/projection/prompt interfaces; it never
calls apply_staged or parse_cli_channel_lines, never edits the channel
ledger, and never injects evidence."""

from __future__ import annotations

import hashlib
import json

from civ_mcp.arena.live_gate import ScenarioMeta, register_scenario

SCENARIO_NAME = "unofficial_channels_core_v1"
SCENARIO_REVISION = 1

ROLE_API = "api_actor"
ROLE_CLI = "cli_actor"
ROLE_OBSERVER = "privacy_observer"
ROLE_CONTRACTS = (
    (ROLE_API, "in_process"),
    (ROLE_CLI, "cli"),
    (ROLE_OBSERVER, "scripted"),
)

# Fixed scenario parameters (part of the configuration fingerprint).
PAYMENT_GOLD = 1
UPFRONT_WITHIN = 1        # up-front favor window: settlement turn + 1
ON_DELIVERY_WITHIN = 1    # on-delivery favor window: acceptance turn + 1
TRADE_KINDS = ("trade_route",)
MIN_GOLD = 0
UPFRONT_PROPOSAL_TEXT = (
    "Gate up-front deal: 1 gold now for a one-turn trade-route freeze."
)
ON_DELIVERY_PROPOSAL_TEXT = (
    "Gate on-delivery deal: hold your reserve one turn, then 1 gold."
)

PHASE_PREFLIGHT = "preflight"
PHASE_CANARY_AND_UPFRONT_PROPOSAL = "canary_and_upfront_proposal"
PHASE_ACCEPT_UPFRONT = "accept_upfront"
PHASE_FUND_UPFRONT = "fund_upfront"
PHASE_RESTART_REQUIRED = "restart_required"
PHASE_RESTART_VERIFY = "restart_verify"
PHASE_ACCEPT_UPFRONT_PAYMENT = "accept_upfront_payment"
PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE = "await_upfront_favor_deadline"
PHASE_VERIFY_UPFRONT_HONORED = "verify_upfront_honored"
PHASE_PROPOSE_ON_DELIVERY = "propose_on_delivery"
PHASE_ACCEPT_ON_DELIVERY = "accept_on_delivery"
PHASE_AWAIT_ON_DELIVERY_FAVOR = "await_on_delivery_favor"
PHASE_WITHHOLD_ON_DELIVERY_FUNDING = "withhold_on_delivery_funding"
PHASE_VERIFY_FUNDING_BREACH = "verify_funding_breach"
PHASE_VERIFY_TERMINAL_GATE = "verify_terminal_gate"


def minimum_captures(config) -> int:
    """Seat captures for the expected deterministic path.

    Handshake rounds: R1 canary+propose+accept, R2 fund (restart boundary),
    R3 restart-verify + payment response — 3 rounds. Then UPFRONT_WITHIN
    rounds to the up-front favor's inclusive deadline, 2 rounds for the
    on-delivery proposal + acceptance, ON_DELIVERY_WITHIN rounds to its favor
    deadline, then funding_turns withheld rounds through the inclusive
    funding deadline. 9 rounds x 3 seats = 27 with the checked-in rules."""

    rounds = 3 + UPFRONT_WITHIN + 2 + ON_DELIVERY_WITHIN + config.channel_rules.funding_turns
    return rounds * len(ROLE_CONTRACTS)


def gate_config_fingerprint(config) -> dict:
    roles = dict(config.live_gate.roles)
    specs = {spec.player_id: spec for spec in config.players}
    role_pids = sorted(roles.values())
    return {
        "scenario": SCENARIO_NAME,
        "scenario_revision": SCENARIO_REVISION,
        "run_id": config.run_id,
        "roles": {name: roles[name] for name in sorted(roles)},
        "driver_kinds": {str(pid): specs[pid].driver_kind() for pid in role_pids},
        "channels_enabled": {
            str(pid): specs[pid].options.channels.enabled for pid in role_pids
        },
        "channel_rules": config.channel_rules.fingerprint(),
        "parameters": {
            "payment_gold": PAYMENT_GOLD,
            "upfront_within": UPFRONT_WITHIN,
            "on_delivery_within": ON_DELIVERY_WITHIN,
            "trade_kinds": list(TRADE_KINDS),
            "min_gold": MIN_GOLD,
        },
    }


def canary_text(run_id: str, fingerprint: dict) -> str:
    """Deterministic for a run, unguessable from player 3's ordinary channel
    state: a fixed prefix plus a digest of run identity and the gate
    configuration fingerprint (spec Privacy Contract)."""

    canonical = json.dumps(
        {"run_id": run_id, "fingerprint": fingerprint},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "GATE-CANARY-" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


class ChannelsCoreDriver:
    """Coordinator-owned deterministic driver for the first scenario.

    Lifecycle: arena.py constructs it from the validated config; run_arena
    calls attach() once (after ChannelRuntime.open), note_admission() per
    admitted gate seat, and after_seat_capture() once per finished capture;
    pending_signal()/result_summary() report restart/terminal outcomes."""

    def __init__(self, config) -> None:
        self.config = config
        self.role_pid: dict[str, int] = dict(config.live_gate.roles)
        self.pid_role: dict[int, str] = {pid: role for role, pid in self.role_pid.items()}
        self.gate_pids: frozenset[int] = frozenset(self.role_pid.values())
        self.fingerprint = gate_config_fingerprint(config)
        self.canary = canary_text(config.run_id, self.fingerprint)
        # Filled by attach() (Task 6):
        self._journal = None
        self._runtime = None
        self._gs = None
        self._run_dir = None
        self._signal: str | None = None


register_scenario(ScenarioMeta(
    name=SCENARIO_NAME,
    revision=SCENARIO_REVISION,
    role_contracts=ROLE_CONTRACTS,
    minimum_captures=minimum_captures,
    create_driver=ChannelsCoreDriver,
))
```

In `src/civ_mcp/arena/live_gate.py`, update the builtin tuple:

```python
_BUILTIN_SCENARIO_MODULES: tuple[str, ...] = ("civ_mcp.arena.live_gate_channels",)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py tests/arena/test_live_gate.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py src/civ_mcp/arena/live_gate.py tests/arena/test_live_gate_channels.py
git commit -m "feat(arena): register unofficial_channels_core_v1 scenario metadata"
```

---

### Task 6: Gate fakes, driver attach, gate seat policies, and authoritative preflight

**Files:**
- Create: `tests/arena/live_gate_fakes.py`
- Modify: `src/civ_mcp/arena/live_gate_channels.py`
- Test: `tests/arena/test_live_gate_channels.py` (append)

**Interfaces:**
- Consumes: `LiveGateJournal` (Task 1); `ChannelRuntime.open/admit_player/finish_player` (production); `ScriptedPolicy` from `civ_mcp.arena.coordinator` (deterministic observe/skip + blocker repair — the spec's only sanctioned game authority); `ObservationRequest`, `ObservationFamily` from `channel_terms`; `gs.get_channel_payment_state(payer, payee, gold)`.
- Produces:
  - `tests/arena/live_gate_fakes.py`: `observation(player_id, turn, **changes)`, `PaymentStateView(status, offer)`, `class GateGameState` (channel observations from per-player `treasury`/`routes` dicts, exact-payment engine with `pending`/`active_player`, plus the `get_game_overview/get_units/skip_unit` surface `ScriptedPolicy` needs), `async run_gate_seat(driver, runtime, gs, pid, turn)`, `async run_gate_round(driver, runtime, gs, turn, seats=(1, 2, 3))` — the coordinator-shaped harness every scenario test uses.
  - Driver: `async attach(*, gs, channel_runtime, run_dir)`, `policy_for(player_id) -> _GateSeatPolicy`, `note_admission(player_id, turn, admission, error)`, `pending_signal() -> str | None`, `result_summary() -> dict`, `_fail(reason)`; `_GateSeatPolicy` with `provider/model/options` mirroring the spec's `PlayerSpec` identity; `async _run_preflight(gs, turn)`.

- [ ] **Step 1: Write the fakes module**

Create `tests/arena/live_gate_fakes.py`:

```python
"""Shared fakes for live-gate tests: a typed game-state fake plus a
coordinator-shaped harness that drives a scenario driver through
admissions/finishes exactly the way run_arena does (admit -> note_admission
-> gate policy -> finish_player -> after_seat_capture -> signal check)."""

import dataclasses

from civ_mcp.arena.channel_terms import ChannelObservation, ObservationFamily
from civ_mcp.lua.channel_payments import ExactPaymentOffer


def observation(player_id, turn, **changes):
    base = ChannelObservation(
        player_id=player_id,
        turn=turn,
        families_present=frozenset(ObservationFamily),
        treasury_gold=500,
    )
    return dataclasses.replace(base, **changes)


@dataclasses.dataclass(frozen=True)
class PaymentStateView:
    status: str
    offer: ExactPaymentOffer | None = None


class GateGameState:
    """Complete observations + an exact-payment engine + the minimal
    overview/units surface ScriptedPolicy needs. Result strings mirror the
    live engine wrappers (channel_runtime._funding_succeeded /
    _response_succeeded)."""

    def __init__(self):
        self.active_player = 0            # set by the harness before each seat
        self.treasury = {1: 500, 2: 500, 3: 500}
        self.routes = {}                  # player_id -> tuple[ObservedRoute, ...]
        self.missing_families = {}        # player_id -> frozenset to drop
        self.observation_errors = {}      # player_id -> tuple[str, ...]
        self.pending = {}                 # (payer, payee) -> ExactPaymentOffer
        self.skipped = 0

    # --- ScriptedPolicy surface ---
    async def get_game_overview(self):
        return "OV"

    async def get_units(self):
        return []

    async def skip_unit(self, index):
        self.skipped += 1
        return "SKIP"

    # --- channel observation surface ---
    async def get_channel_observation(self, player_id, turn, request):
        present = frozenset(ObservationFamily) - self.missing_families.get(
            player_id, frozenset()
        )
        return observation(
            player_id,
            turn,
            families_present=present,
            treasury_gold=self.treasury.get(player_id, 500),
            trade_routes=self.routes.get(player_id, ()),
            errors=self.observation_errors.get(player_id, ()),
        )

    # --- exact-payment engine ---
    async def offer_channel_payment(self, payee, gold):
        payer = self.active_player
        if (payer, payee) in self.pending:
            return "Error: CHANNEL_PAYMENT_PENDING_DEAL"
        self.pending[(payer, payee)] = ExactPaymentOffer(payer, payee, gold)
        return "CHANNEL_PAYMENT_PROPOSED"

    async def get_channel_payment_state(self, payer, payee, gold):
        pending = self.pending.get((payer, payee))
        expected = ExactPaymentOffer(payer, payee, gold)
        if pending is None:
            return PaymentStateView("absent")
        if pending == expected:
            return PaymentStateView("exact", expected)
        return PaymentStateView("conflicting")

    async def respond_to_channel_payment(self, payer, gold, accept):
        payee = self.active_player
        expected = ExactPaymentOffer(payer, payee, gold)
        if self.pending.get((payer, payee)) != expected:
            return "Error: NO_EXACT_CHANNEL_PAYMENT"
        del self.pending[(payer, payee)]
        if accept:
            self.treasury[payer] -= gold
            self.treasury[payee] += gold
            return "CHANNEL_PAYMENT_ACCEPTED"
        return "CHANNEL_PAYMENT_REJECTED"


async def run_gate_seat(driver, runtime, gs, pid, turn):
    """One coordinator-shaped capture for one gate seat."""
    gs.active_player = pid
    admission = await runtime.admit_player(gs, pid, turn)
    driver.note_admission(pid, turn, admission, "")
    if driver.pending_signal() is not None:
        return None
    policy = driver.policy_for(pid)
    result = await policy(gs, pid, turn)
    acknowledgements = await runtime.finish_player(gs, admission, result)
    await driver.after_seat_capture(
        player_id=pid,
        turn=turn,
        channel_fields={
            "enabled": True,
            "acknowledgements": len(acknowledgements),
            "error": "",
        },
    )
    return result


async def run_gate_round(driver, runtime, gs, turn, seats=(1, 2, 3)):
    for pid in seats:
        if driver.pending_signal() is not None:
            return
        await run_gate_seat(driver, runtime, gs, pid, turn)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/arena/test_live_gate_channels.py`:

```python
from pathlib import Path

from civ_mcp.arena.channel_runtime import ChannelRuntime
from civ_mcp.arena.live_gate import GATE_FAILED

from tests.arena.live_gate_fakes import GateGameState, run_gate_seat


def open_runtime(tmp_path, cfg):
    run_dir = Path(tmp_path) / cfg.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )


async def attached_driver(tmp_path, cfg=None, gs=None):
    cfg = cfg or gate_config()
    gs = gs or GateGameState()
    run_dir, runtime = open_runtime(tmp_path, cfg)
    driver = lgc.ChannelsCoreDriver(cfg)
    await driver.attach(gs=gs, channel_runtime=runtime, run_dir=run_dir)
    return driver, runtime, gs


@pytest.mark.asyncio
async def test_attach_opens_gate_journal_in_preflight(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    state = driver._journal.state
    assert state.phase == lgc.PHASE_PREFLIGHT
    assert state.scenario == lgc.SCENARIO_NAME
    assert state.config_fingerprint == driver.fingerprint
    assert (Path(tmp_path) / gate_config().run_id / "live_gate" / "events.jsonl").exists()


@pytest.mark.asyncio
async def test_attach_requires_channel_runtime(tmp_path):
    driver = lgc.ChannelsCoreDriver(gate_config())
    with pytest.raises(Exception):
        await driver.attach(
            gs=GateGameState(), channel_runtime=None,
            run_dir=Path(tmp_path) / "r",
        )


def test_policy_for_returns_role_policies_with_spec_identity():
    driver = lgc.ChannelsCoreDriver(gate_config())
    api = driver.policy_for(1)
    cli = driver.policy_for(2)
    observer = driver.policy_for(3)
    assert api.provider == "local" and api.model == "m"
    assert cli.provider == "cli-codex"
    assert observer.provider == "scripted"
    with pytest.raises(KeyError):
        driver.policy_for(9)


@pytest.mark.asyncio
async def test_preflight_runs_once_and_advances_phase(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    state = driver._journal.state
    assert driver.pending_signal() is None
    # Preflight completed and the canary/proposal actions were planned+verified
    # within the same admission (Task 7 asserts the actions themselves).
    assert state.phase != lgc.PHASE_PREFLIGHT
    assert any(
        entry.get("kind") == "preflight" for entry in state.observations
    )
    assert gs.skipped >= 1  # deterministic minimal turn ran


@pytest.mark.asyncio
async def test_preflight_missing_family_fails_closed(tmp_path):
    from civ_mcp.arena.channel_terms import ObservationFamily
    gs = GateGameState()
    gs.missing_families[2] = frozenset({ObservationFamily.TRADE_ROUTES})
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    assert driver.pending_signal() == GATE_FAILED
    assert "trade_routes" in driver._journal.state.reason
    assert driver._journal.result_path.exists()


@pytest.mark.asyncio
async def test_preflight_observation_error_fails_closed(tmp_path):
    gs = GateGameState()
    gs.observation_errors[1] = ("LUA_ERROR|boom",)
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    assert driver.pending_signal() == GATE_FAILED


@pytest.mark.asyncio
async def test_preflight_insufficient_gold_fails_closed(tmp_path):
    gs = GateGameState()
    gs.treasury[1] = 0
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    assert driver.pending_signal() == GATE_FAILED
    assert "gold" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_preflight_conflicting_pending_trade_fails_closed(tmp_path):
    from civ_mcp.lua.channel_payments import ExactPaymentOffer
    gs = GateGameState()
    gs.pending[(1, 2)] = ExactPaymentOffer(1, 2, 1)
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    assert driver.pending_signal() == GATE_FAILED
    assert "pending" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_missing_admission_fails_closed(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    driver.note_admission(1, 10, None, "admission exploded")
    assert driver.pending_signal() == GATE_FAILED
    assert "admission" in driver._journal.state.reason
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py -v`
Expected: new tests FAIL — `AttributeError: 'ChannelsCoreDriver' object has no attribute 'attach'` (Task 5 tests keep passing).

- [ ] **Step 4: Write the implementation**

Extend `src/civ_mcp/arena/live_gate_channels.py`. Add imports:

```python
from pathlib import Path

from civ_mcp.arena.channel_terms import ObservationFamily, ObservationRequest
from civ_mcp.arena.live_gate import (
    GATE_ACTIVE,
    GATE_FAILED,
    GATE_PASSED,
    GATE_RESTART_REQUIRED,
    LiveGateJournal,
)
```

Add the gate seat policy class (module level, above the driver):

```python
class _GateSeatPolicy:
    """Deterministic gate policy for one bound seat.

    Carries the configured PlayerSpec identity (provider/model/options) so
    transcripts and fingerprints record the validated path contract, while
    the actual turn behavior is the driver's deterministic plan. blocker_block
    reuses ScriptedPolicy's scoped repair — the only game authority the spec
    grants a gate turn."""

    def __init__(self, driver: "ChannelsCoreDriver", spec) -> None:
        # Import here, not at module top: coordinator imports channel modules,
        # and this module must stay importable from config validation.
        from civ_mcp.arena.coordinator import ScriptedPolicy

        self.provider = spec.provider
        self.model = spec.model
        self.options = spec.options
        self.player_id = spec.player_id
        self._driver = driver
        self._scripted = ScriptedPolicy(options=spec.options)

    async def __call__(
        self, gs, player_id: int, turn: int, *, blocker_block: str = "", **kwargs
    ) -> dict:
        if blocker_block:
            return await self._scripted._repair(gs, blocker_block)
        base = await self._scripted(gs, player_id, turn)
        return await self._driver.seat_turn(gs, player_id, turn, base)
```

Extend `ChannelsCoreDriver`:

```python
    # --- construction additions (extend __init__) ---
    # after self._signal = None add:
        self._policies = {
            spec.player_id: _GateSeatPolicy(self, spec)
            for spec in config.players
            if spec.player_id in self.gate_pids
        }
        self._admissions: dict[tuple[int, int], object] = {}
        self._captured_this_turn: dict[int, set[int]] = {}
        self._restart_armed = False

    # --- coordinator-facing surface ---

    async def attach(self, *, gs, channel_runtime, run_dir) -> None:
        if channel_runtime is None:
            raise RuntimeError("live gate requires the channel runtime")
        self._gs = gs
        self._runtime = channel_runtime
        self._run_dir = Path(run_dir)
        self._journal = LiveGateJournal.open(
            self._run_dir,
            run_id=self.config.run_id,
            scenario=SCENARIO_NAME,
            scenario_revision=SCENARIO_REVISION,
            roles=dict(self.role_pid),
            config_fingerprint=self.fingerprint,
            initial_phase=PHASE_PREFLIGHT,
        )
        state = self._journal.state
        if state.status in (GATE_FAILED, GATE_PASSED):
            raise RuntimeError(f"gate already terminal: {state.status} ({state.reason})")
        if state.status == GATE_RESTART_REQUIRED:
            await self._verify_restart()   # Task 8 implements; stub below for now

    def policy_for(self, player_id: int) -> _GateSeatPolicy:
        return self._policies[player_id]

    def note_admission(self, player_id, turn, admission, error) -> None:
        if self._signal is not None or player_id not in self.gate_pids:
            return
        if admission is None:
            self._fail(f"gate seat {player_id} turn {turn} has no channel admission: {error}")
            return
        self._admissions[(player_id, turn)] = admission

    def pending_signal(self) -> str | None:
        return self._signal

    def result_summary(self) -> dict:
        state = self._journal.state if self._journal is not None else None
        if state is None:
            return {"status": GATE_FAILED, "phase": "", "reason": "gate never attached",
                    "restart_count": 0, "run_id": self.config.run_id}
        return {
            "status": state.status,
            "phase": state.phase,
            "reason": state.reason,
            "restart_count": state.restart_count,
            "run_id": state.run_id,
        }

    def _fail(self, reason: str) -> None:
        if self._signal == GATE_FAILED:
            return
        self._signal = GATE_FAILED
        if self._journal is not None and self._journal.state.status not in (
            GATE_FAILED, GATE_PASSED,
        ):
            self._journal.append("gate_failed", {"reason": reason})
            self._journal.write_result()

    async def _verify_restart(self) -> None:
        raise NotImplementedError("Task 8")   # replaced in Task 8

    # --- deterministic turn planning (Task 7 fills the phase machine) ---

    async def seat_turn(self, gs, player_id, turn, base_result) -> dict:
        try:
            return await self._seat_turn_inner(gs, player_id, turn, base_result)
        except Exception as exc:
            self._fail(f"seat_turn seat {player_id} turn {turn}: {exc!r}")
            return base_result

    async def _seat_turn_inner(self, gs, player_id, turn, base_result) -> dict:
        if self._signal is not None:
            return base_result
        admission = self._admissions.get((player_id, turn))
        if admission is None:
            self._fail(f"gate seat {player_id} turn {turn} acted without an admission")
            return base_result
        if self._journal.state.phase == PHASE_PREFLIGHT:
            await self._run_preflight(gs, turn)
            if self._signal is not None:
                return base_result
        return base_result   # Task 7 replaces this tail with phase-planned input

    async def after_seat_capture(self, *, player_id, turn, channel_fields) -> None:
        # Task 7 fills verification; Task 6 only tracks the round set.
        if player_id not in self.gate_pids or self._signal is not None:
            return
        self._captured_this_turn.setdefault(turn, set()).add(player_id)
        self._admissions.pop((player_id, turn), None)

    # --- authoritative preflight (spec: Authoritative Preflight and Term
    # Selection). Read-only; failures stop the gate, no guessed fallback. ---

    async def _run_preflight(self, gs, turn) -> None:
        api = self.role_pid[ROLE_API]
        cli = self.role_pid[ROLE_CLI]
        checks = (
            (api, frozenset({ObservationFamily.TREASURY})),
            (cli, frozenset({ObservationFamily.TREASURY, ObservationFamily.TRADE_ROUTES})),
        )
        summaries = []
        for pid, families in checks:
            request = ObservationRequest(families=families)
            observed = await gs.get_channel_observation(pid, turn, request)
            if observed.errors:
                self._fail(f"preflight observation error for player {pid}: {observed.errors}")
                return
            missing = families - observed.families_present
            if missing:
                self._fail(
                    f"preflight missing observation families for player {pid}: "
                    f"{sorted(f.value for f in missing)}"
                )
                return
            summaries.append({
                "player_id": pid,
                "families": sorted(f.value for f in families),
                "treasury_gold": observed.treasury_gold,
                "route_count": len(observed.trade_routes),
            })
        if summaries[0]["treasury_gold"] < PAYMENT_GOLD:
            self._fail(
                f"preflight: player {api} gold {summaries[0]['treasury_gold']} cannot fund "
                f"the fixed {PAYMENT_GOLD}-gold official payment"
            )
            return
        # Pending trades are a payment-runtime query, not an ObservationFamily.
        payment_state = await gs.get_channel_payment_state(api, cli, PAYMENT_GOLD)
        status = getattr(payment_state, "status", None)
        status = getattr(status, "value", status)
        if status != "absent":
            self._fail(
                f"preflight: pending official trade for pair ({api},{cli}) is "
                f"{status!r}; linkage would be ambiguous"
            )
            return
        self._journal.append("observation_recorded", {
            "kind": "preflight",
            "turn": turn,
            "players": summaries,
            "payment_pair_status": "absent",
        })
        self._journal.append("phase_advanced", {
            "phase": PHASE_CANARY_AND_UPFRONT_PROPOSAL, "turn": turn,
        })
```

Note the `attach` stub calls `self._verify_restart()` which raises `NotImplementedError` — no Task 6 test reaches it (no restart checkpoint exists yet); Task 8 replaces it.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py -v`
Expected: all PASS except none — `test_preflight_runs_once_and_advances_phase` asserts phase moved off preflight, which this task satisfies via the `phase_advanced` append.

- [ ] **Step 6: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py tests/arena/live_gate_fakes.py tests/arena/test_live_gate_channels.py
git commit -m "feat(arena): live-gate driver attach, seat policies, authoritative preflight"
```

---

### Task 7: Handshake phase machine — canary, proposal, acceptance, funding, write-ahead verification

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py`
- Test: `tests/arena/test_live_gate_channels.py` (append)

**Interfaces:**
- Consumes: `admission.context.dispatch(name, args)` (production API staging; source `api:{run_id}:{pid}:{turn}:{index}:{digest16}`); `transcript.final_summary` CLI lines (source `cli:{run_id}:{pid}:{turn}:{line_index}:{digest16-of-line-bytes}`); `ChannelRuntime.state` (acknowledgements, deals, messages) for canonical verification.
- Produces (Tasks 8-10 build on these):
  - `seat_turn` tail replaced by `_planned_channel_input(role, phase, turn)` + `_emit_api(admission, turn, plans)` + `_emit_cli(base_result, player_id, turn, plans)`.
  - `after_seat_capture` verification: `_verify_planned_actions(player_id, turn)`, `_advance_after_capture(player_id, turn)`.
  - Gate data keys (persisted via `data_recorded`): `"canary_message_source"`, `"upfront_deal_id"`, `"on_delivery_deal_id"`, `"upfront_payment_fingerprint"` (Task 8 reads it).
  - Recovery scan on attach: `_recover_pending_actions(current_turn)` — called from the first `note_admission` after a resume.

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_live_gate_channels.py`:

```python
from civ_mcp.arena.channels import DealState, FavorStatus, PaymentStatus
from tests.arena.live_gate_fakes import run_gate_round


def deals(runtime):
    return {deal.id: deal for deal in runtime.state.deals}


@pytest.mark.asyncio
async def test_round1_canary_proposal_and_acceptance(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    assert driver.pending_signal() is None

    state = runtime.state
    # Exactly two api: acknowledgements from the bound context, both applied.
    api_acks = [a for a in state.acknowledgements if a.source_id.startswith("api:")]
    assert len(api_acks) == 2
    assert all(a.status == "applied" for a in api_acks)
    run_id = driver.config.run_id
    assert all(a.source_id.startswith(f"api:{run_id}:1:10:") for a in api_acks)

    # Canary message reached the CLI actor's projection; captured, not assumed.
    assert any(m.text == driver.canary for m in state.messages)
    projection_cli = runtime.project_for_player(2, 10)
    assert any(m.text == driver.canary for m in projection_cli.messages)

    # One cli: acceptance acknowledgement through final_summary parsing.
    cli_acks = [a for a in state.acknowledgements if a.source_id.startswith("cli:")]
    assert len(cli_acks) == 1
    assert cli_acks[0].status == "applied"
    assert cli_acks[0].source_id.startswith(f"cli:{run_id}:2:10:")

    deal_id = driver._journal.state.data["upfront_deal_id"]
    deal = deals(runtime)[deal_id]
    assert deal.state is DealState.ACTIVE
    assert deal.timing == "up_front"
    assert deal.payment_status is PaymentStatus.DUE
    assert deal.favor.term_type == "dont_trade_with"
    assert deal.favor.params["target_player"] == 3
    assert deal.proposer == 1 and deal.counterparty == 2
    assert driver._journal.state.phase == lgc.PHASE_FUND_UPFRONT


@pytest.mark.asyncio
async def test_round2_funding_offers_exact_payment(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_seat(driver, runtime, gs, 1, 11)

    deal_id = driver._journal.state.data["upfront_deal_id"]
    deal = deals(runtime)[deal_id]
    assert deal.payment_status is PaymentStatus.OFFERED
    fingerprint = driver._journal.state.data["upfront_payment_fingerprint"]
    assert fingerprint == {
        "payer": 1, "payee": 2, "gold": 1, "duration": 0, "item_count": 1,
    }
    from civ_mcp.lua.channel_payments import ExactPaymentOffer
    assert gs.pending[(1, 2)] == ExactPaymentOffer(1, 2, 1)


@pytest.mark.asyncio
async def test_cli_line_is_exact_single_channel_line(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    gs.active_player = 1
    admission1 = await runtime.admit_player(gs, 1, 10)
    driver.note_admission(1, 10, admission1, "")
    result1 = await driver.policy_for(1)(gs, 1, 10)
    await runtime.finish_player(gs, admission1, result1)
    await driver.after_seat_capture(player_id=1, turn=10, channel_fields={
        "enabled": True, "acknowledgements": 2, "error": ""})

    gs.active_player = 2
    admission2 = await runtime.admit_player(gs, 2, 10)
    driver.note_admission(2, 10, admission2, "")
    result2 = await driver.policy_for(2)(gs, 2, 10)
    summary = result2["transcript"]["final_summary"]
    channel_lines = [l for l in summary.splitlines() if l.startswith("CHANNEL ")]
    assert len(channel_lines) == 1
    import json as json_module
    payload = json_module.loads(channel_lines[0][len("CHANNEL "):])
    assert payload["action"] == "respond_to_deal"
    assert payload["accept"] is True
    assert payload["deal_id"] == driver._journal.state.data["upfront_deal_id"]
    # The driver does not parse its own line — no staged action on the context.
    assert admission2.context.staged_actions == []


@pytest.mark.asyncio
async def test_write_ahead_action_planned_before_dispatch(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    state = driver._journal.state
    verified = {entry["source_id"]: entry for entry in state.verified_actions}
    assert len(verified) == 3  # canary + proposal + acceptance
    api_sources = [s for s in verified if s.startswith("api:")]
    cli_sources = [s for s in verified if s.startswith("cli:")]
    assert len(api_sources) == 2 and len(cli_sources) == 1
    for entry in verified.values():
        assert entry["turn"] == 10
    assert state.pending_actions == ()


@pytest.mark.asyncio
async def test_rejected_acknowledgement_fails_closed(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    # Simulate a duplicate-source rejection: run round 1, then force the phase
    # machine to re-plan the same inputs at the same turn. The second pass
    # reuses the exact source ids, the runtime's idempotency rejects them,
    # and verification must fail closed.
    await run_gate_round(driver, runtime, gs, 10)
    assert driver.pending_signal() is None
    # Force the phase machine back to re-plan the same inputs at turn 10.
    journal = driver._journal
    journal.append("phase_advanced", {"phase": lgc.PHASE_CANARY_AND_UPFRONT_PROPOSAL,
                                      "turn": 10})
    await run_gate_seat(driver, runtime, gs, 1, 10)
    assert driver.pending_signal() == GATE_FAILED


@pytest.mark.asyncio
async def test_unexpected_channel_action_from_awaiting_role_fails(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    # Round 2: CLI actor must take NO channel action. Inject a rogue ack by
    # staging through a real admission before the driver's after-capture check.
    gs.active_player = 1
    await run_gate_seat(driver, runtime, gs, 1, 11)
    gs.active_player = 2
    admission = await runtime.admit_player(gs, 2, 11)
    driver.note_admission(2, 11, admission, "")
    result = await driver.policy_for(2)(gs, 2, 11)
    result = {"transcript": {"steps": [], "final_summary":
        result["transcript"]["final_summary"] + "\nCHANNEL {\"action\": \"send_message\", \"to_player\": 1, \"text\": \"rogue\"}"}}
    await runtime.finish_player(gs, admission, result)
    await driver.after_seat_capture(player_id=2, turn=11, channel_fields={
        "enabled": True, "acknowledgements": 1, "error": ""})
    assert driver.pending_signal() == GATE_FAILED
    assert "unexpected" in driver._journal.state.reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py -v -k "round1 or round2 or cli_line or write_ahead or rejected or unexpected"`
Expected: FAIL — round 1 produces no acknowledgements (`seat_turn` still returns `base_result` untouched).

- [ ] **Step 3: Write the implementation**

In `src/civ_mcp/arena/live_gate_channels.py`:

(a) Add the deterministic phase→input table and helpers:

```python
    def _planned_channel_input(self, role: str, phase: str, turn: int) -> tuple[tuple[str, dict], ...]:
        """Exact (action_name, args) tuples the current phase expects from
        this role right now. Empty tuple = deterministic no-action turn."""

        data = self._journal.state.data
        api = self.role_pid[ROLE_API]
        cli = self.role_pid[ROLE_CLI]
        observer = self.role_pid[ROLE_OBSERVER]
        if phase == PHASE_CANARY_AND_UPFRONT_PROPOSAL and role == ROLE_API:
            return (
                ("send_message", {"to_player": cli, "text": self.canary}),
                ("propose_deal", {
                    "to_player": cli,
                    "text": UPFRONT_PROPOSAL_TEXT,
                    "favor": {
                        "term_type": "dont_trade_with",
                        "params": {
                            "target_player": observer,
                            "trade_kinds": list(TRADE_KINDS),
                        },
                    },
                    "payment_gold": PAYMENT_GOLD,
                    "timing": "up_front",
                    "within": UPFRONT_WITHIN,
                }),
            )
        if phase == PHASE_ACCEPT_UPFRONT and role == ROLE_CLI:
            return (("respond_to_deal", {"deal_id": data["upfront_deal_id"], "accept": True}),)
        if phase == PHASE_FUND_UPFRONT and role == ROLE_API:
            return (("fund_deal", {"deal_id": data["upfront_deal_id"]}),)
        if phase == PHASE_ACCEPT_UPFRONT_PAYMENT and role == ROLE_CLI:
            return (("respond_to_payment", {"deal_id": data["upfront_deal_id"], "accept": True}),)
        if phase == PHASE_PROPOSE_ON_DELIVERY and role == ROLE_CLI:
            return (("propose_deal", {
                "to_player": api,
                "text": ON_DELIVERY_PROPOSAL_TEXT,
                "favor": {
                    "term_type": "maintain_gold_reserve",
                    "params": {"min_gold": MIN_GOLD},
                },
                "payment_gold": PAYMENT_GOLD,
                "timing": "on_delivery",
                "within": ON_DELIVERY_WITHIN,
            }),)
        if phase == PHASE_ACCEPT_ON_DELIVERY and role == ROLE_API:
            return (("respond_to_deal", {"deal_id": data["on_delivery_deal_id"], "accept": True}),)
        return ()
```

(b) Replace `_seat_turn_inner`'s tail (`return base_result` after preflight) with role dispatch:

```python
        role = self.pid_role[player_id]
        if role == ROLE_OBSERVER:
            return base_result   # Task 10 adds privacy assertions here
        phase = self._journal.state.phase
        plans = self._planned_channel_input(role, phase, turn)
        if not plans:
            return base_result
        if role == ROLE_API:
            self._emit_api(admission, player_id, turn, phase, plans)
            return base_result
        return self._emit_cli(base_result, player_id, turn, phase, plans)
```

(c) The two production entry paths, each write-ahead planned:

```python
    @staticmethod
    def _canonical_args(args: dict) -> str:
        return json.dumps(args, sort_keys=True, separators=(",", ":"))

    def _emit_api(self, admission, player_id, turn, phase, plans) -> None:
        """Locked Decision 4: stage through the bound production context.
        The expected api: source id is derived exactly as dispatch does, and
        the plan is journaled BEFORE the dispatch call."""

        context = admission.context
        for name, args in plans:
            canonical = self._canonical_args(args)
            digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
            index = len(context.staged_actions)
            source_id = f"api:{self.config.run_id}:{player_id}:{turn}:{index}:{digest}"
            self._journal.append("action_planned", {
                "turn": turn, "player_id": player_id, "phase": phase,
                "name": name, "source_id": source_id,
                "payload_digest": digest,
            })
            queued = context.dispatch(name, args)
            if source_id not in queued:
                self._fail(
                    f"dispatch source mismatch: planned {source_id}, got {queued!r}"
                )
                return

    def _emit_cli(self, base_result, player_id, turn, phase, plans) -> dict:
        """Locked Decision 5: exact one-line JSON in transcript.final_summary;
        only unmodified ChannelRuntime.finish_player parses it."""

        summary = ""
        if isinstance(base_result, dict):
            transcript = base_result.get("transcript")
            if isinstance(transcript, dict) and isinstance(transcript.get("final_summary"), str):
                summary = transcript["final_summary"]
            elif isinstance(base_result.get("summary"), str):
                summary = base_result["summary"]
        lines = summary.splitlines()
        for name, args in plans:
            payload = {"action": name, **args}
            line = "CHANNEL " + json.dumps(payload, sort_keys=True, separators=(",", ":"))
            line_index = len(lines)
            digest = hashlib.sha256(
                line.encode("utf-8", errors="surrogatepass")
            ).hexdigest()[:16]
            source_id = (
                f"cli:{self.config.run_id}:{player_id}:{turn}:{line_index}:{digest}"
            )
            self._journal.append("action_planned", {
                "turn": turn, "player_id": player_id, "phase": phase,
                "name": name, "source_id": source_id,
                "payload_digest": digest, "line_index": line_index,
            })
            lines.append(line)
        return {
            "summary": summary or "live-gate deterministic turn",
            "actions": (base_result or {}).get("actions", []),
            "transcript": {"steps": [], "final_summary": "\n".join(lines)},
        }
```

(d) Replace `after_seat_capture` with canonical verification + phase advancement:

```python
    async def after_seat_capture(self, *, player_id, turn, channel_fields) -> None:
        try:
            await self._after_seat_capture_inner(
                player_id=player_id, turn=turn, channel_fields=channel_fields
            )
        except Exception as exc:
            self._fail(f"after_seat_capture seat {player_id} turn {turn}: {exc!r}")

    async def _after_seat_capture_inner(self, *, player_id, turn, channel_fields) -> None:
        if player_id not in self.gate_pids or self._signal is not None:
            return
        self._admissions.pop((player_id, turn), None)
        self._captured_this_turn.setdefault(turn, set()).add(player_id)
        if channel_fields.get("error"):
            self._fail(
                f"channel finish error for seat {player_id} turn {turn}: "
                f"{channel_fields['error']}"
            )
            return
        if not self._verify_planned_actions(player_id, turn):
            return
        if not self._check_no_unexpected_acknowledgements(player_id, turn):
            return
        self._advance_after_capture(player_id, turn)
        if self._signal is None:
            await self._maybe_finish_round(turn)   # Task 8 (restart) + Task 9 (terminal)

    def _verify_planned_actions(self, player_id, turn) -> bool:
        """Every planned action for this seat/turn must have exactly one
        applied acknowledgement with the exact source id."""

        state = self._journal.state
        pending = [
            entry for entry in state.pending_actions
            if entry["player_id"] == player_id and entry["turn"] == turn
        ]
        acks = {a.source_id: a for a in self._runtime.state.acknowledgements}
        for entry in pending:
            ack = acks.get(entry["source_id"])
            if ack is None:
                self._fail(f"missing acknowledgement for planned {entry['source_id']}")
                return False
            if ack.status != "applied":
                self._fail(
                    f"acknowledgement for {entry['source_id']} is {ack.status!r}: "
                    f"{ack.message}"
                )
                return False
            self._journal.append("action_verified", {
                "source_id": entry["source_id"], "turn": turn,
                "deal_id": ack.deal_id,
            })
        return True

    def _check_no_unexpected_acknowledgements(self, player_id, turn) -> bool:
        verified = {entry["source_id"] for entry in self._journal.state.verified_actions}
        for ack in self._runtime.state.acknowledgements:
            if ack.player_id != player_id or ack.turn != turn:
                continue
            if ack.source_id not in verified:
                self._fail(
                    f"unexpected channel acknowledgement {ack.source_id} from seat "
                    f"{player_id} turn {turn}"
                )
                return False
        return True

    def _deal(self, deal_id: str):
        for deal in self._runtime.state.deals:
            if deal.id == deal_id:
                return deal
        self._fail(f"deal {deal_id!r} is missing from canonical state")
        return None
```

(e) Phase advancement after each verified capture (handshake part; Tasks 8-9 extend the same method):

```python
    def _advance_after_capture(self, player_id, turn) -> None:
        from civ_mcp.arena.channels import DealState, PaymentStatus

        state = self._journal.state
        phase = state.phase
        role = self.pid_role[player_id]
        if phase == PHASE_CANARY_AND_UPFRONT_PROPOSAL and role == ROLE_API:
            acks = {a.source_id: a for a in self._runtime.state.acknowledgements}
            deal_ids = [
                acks[e["source_id"]].deal_id
                for e in state.verified_actions
                if e["source_id"].startswith("api:") and acks[e["source_id"]].deal_id
            ]
            if len(deal_ids) != 1:
                self._fail(f"expected exactly one captured up-front deal id, got {deal_ids}")
                return
            canary_sources = [
                e["source_id"] for e in state.verified_actions
                if e["source_id"].startswith("api:") and not acks[e["source_id"]].deal_id
            ]
            if not any(m.text == self.canary for m in self._runtime.state.messages):
                self._fail("canary message is missing from canonical state")
                return
            self._journal.append("data_recorded", {"data": {
                "upfront_deal_id": deal_ids[0],
                "canary_message_source": canary_sources[0] if canary_sources else "",
            }})
            self._journal.append("phase_advanced", {"phase": PHASE_ACCEPT_UPFRONT, "turn": turn})
        elif phase == PHASE_ACCEPT_UPFRONT and role == ROLE_CLI:
            deal = self._deal(state.data["upfront_deal_id"])
            if deal is None:
                return
            if deal.state is not DealState.ACTIVE or deal.payment_status is not PaymentStatus.DUE:
                self._fail(
                    f"up-front deal not active/payment-due after acceptance: "
                    f"{deal.state}/{deal.payment_status}"
                )
                return
            baseline = deal.favor.baseline
            if not baseline or any(
                key.endswith("baseline_complete") and value is not True
                for key, value in baseline.items()
            ):
                self._fail("up-front favor baseline is missing or incomplete")
                return
            self._journal.append("phase_advanced", {"phase": PHASE_FUND_UPFRONT, "turn": turn})
        elif phase == PHASE_FUND_UPFRONT and role == ROLE_API:
            deal = self._deal(state.data["upfront_deal_id"])
            if deal is None:
                return
            if deal.payment_status is not PaymentStatus.OFFERED:
                self._fail(f"up-front payment not offered: {deal.payment_status}")
                return
            fingerprint = {
                "payer": deal.proposer, "payee": deal.counterparty,
                "gold": deal.payment_gold, "duration": 0, "item_count": 1,
            }
            self._journal.append("data_recorded", {"data": {
                "upfront_payment_fingerprint": fingerprint,
            }})
            self._restart_armed = True
            self._journal.append("phase_advanced", {
                "phase": PHASE_RESTART_REQUIRED, "turn": turn,
            })
        # Tasks 8-9 extend this dispatch for the remaining phases.

    async def _maybe_finish_round(self, turn) -> None:
        return   # Task 8 implements the round-boundary restart
```

(f) Recovery scan (spec: Write-ahead orchestration). Add to `note_admission`, before storing the admission:

```python
        if self._journal.state.pending_actions and (player_id, turn) not in self._admissions:
            self._recover_pending_actions(turn)
            if self._signal is not None:
                return
```

and the method:

```python
    def _recover_pending_actions(self, current_turn: int) -> None:
        """A stop between channel persistence and gate advancement leaves
        planned-but-unverified actions. A matching applied acknowledgement is
        recorded once; a missing one is reissued only when the same bound
        player and game turn can reproduce the exact source identity —
        otherwise the gate fails (spec: Write-ahead orchestration)."""

        acks = {a.source_id: a for a in self._runtime.state.acknowledgements}
        for entry in tuple(self._journal.state.pending_actions):
            ack = acks.get(entry["source_id"])
            if ack is not None and ack.status == "applied":
                self._journal.append("action_verified", {
                    "source_id": entry["source_id"],
                    "turn": entry["turn"],
                    "deal_id": ack.deal_id,
                    "recovered": True,
                })
                continue
            if ack is not None:
                self._fail(f"recovered acknowledgement for {entry['source_id']} is {ack.status!r}")
                return
            if entry["turn"] != current_turn:
                self._fail(
                    f"planned action {entry['source_id']} cannot be reissued: game turn "
                    f"moved from {entry['turn']} to {current_turn}"
                )
                return
            # Same player + same turn reproduces the same source id, and the
            # channel runtime's idempotency protects a replay: drop the stale
            # plan and let the phase machine re-plan it this turn.
            # (reduce has no 'unplan'; reissue by verifying later under the
            # SAME id — the re-planned dispatch produces the identical source,
            # which the pending entry already covers. Nothing to do here.)
```

Note the reissue branch intentionally does nothing: the phase machine will re-plan the same `(name, args)` this turn, `_emit_api`/`_emit_cli` recompute the identical source id, and the reducer's duplicate-source check must therefore SKIP appending a second `action_planned` when an identical pending entry exists. Implement that skip in BOTH `_emit_api` and `_emit_cli` by wrapping their journal append (shown here for `_emit_api`; `_emit_cli` is identical apart from its extra `"line_index"` field):

```python
            already_planned = any(
                entry["source_id"] == source_id
                for entry in self._journal.state.pending_actions
            )
            if not already_planned:
                self._journal.append("action_planned", {
                    "turn": turn, "player_id": player_id, "phase": phase,
                    "name": name, "source_id": source_id,
                    "payload_digest": digest,
                })
```

(g) Add `import hashlib`/`import json` are already present from Task 5's canary code; verify.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py tests/arena/test_live_gate.py -v`
Expected: all PASS. If `test_round1_canary_proposal_and_acceptance` fails on deal-state or baseline grounds, read the acknowledgement `message` strings in the failure output — the runtime names the exact rejection reason (e.g., term validation or capacity), and the fix belongs in the planned args, never in the runtime.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
git commit -m "feat(arena): live-gate handshake phases with write-ahead verification"
```

---

### Task 8: Round-boundary restart handshake and resume verification

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py`
- Test: `tests/arena/test_live_gate_channels.py` (append)

**Interfaces:**
- Consumes: `gs.get_channel_payment_state(payer, payee, gold)`; gate data key `"upfront_payment_fingerprint"` (Task 7); `GATE_RESTART_REQUIRED`; journal events `restart_required`/`restart_verified`.
- Produces: `_maybe_finish_round(turn)` (real implementation), `_verify_restart()` (replaces the Task 6 stub), signal value `"restart_required"`; after resume the phase is `PHASE_ACCEPT_UPFRONT_PAYMENT`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_live_gate_channels.py`:

```python
from civ_mcp.arena.live_gate import GATE_ACTIVE, GATE_RESTART_REQUIRED


async def drive_to_restart(tmp_path, gs=None):
    gs = gs or GateGameState()
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)   # R1 handshake
    await run_gate_round(driver, runtime, gs, 11)   # R2 funding + boundary
    return driver, runtime, gs


@pytest.mark.asyncio
async def test_restart_defers_to_round_boundary(tmp_path):
    gs = GateGameState()
    driver, runtime, gs2 = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)
    # Funding seat is FIRST in round 2: no signal until seats 2 and 3 finish.
    await run_gate_seat(driver, runtime, gs, 1, 11)
    assert driver.pending_signal() is None
    await run_gate_seat(driver, runtime, gs, 2, 11)
    assert driver.pending_signal() is None
    await run_gate_seat(driver, runtime, gs, 3, 11)
    assert driver.pending_signal() == GATE_RESTART_REQUIRED


@pytest.mark.asyncio
async def test_restart_checkpoint_persists_fingerprint_and_result(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    state = driver._journal.state
    assert state.status == GATE_RESTART_REQUIRED
    assert state.restart_count == 1
    assert state.data["upfront_payment_fingerprint"]["gold"] == 1
    import json as json_module
    result = json_module.loads(driver._journal.result_path.read_text())
    assert result["status"] == GATE_RESTART_REQUIRED


@pytest.mark.asyncio
async def test_restart_live_fingerprint_mismatch_fails(tmp_path):
    gs = GateGameState()
    driver, runtime, _ = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_seat(driver, runtime, gs, 1, 11)
    # The official offer vanishes before the boundary check.
    gs.pending.clear()
    await run_gate_seat(driver, runtime, gs, 2, 11)
    await run_gate_seat(driver, runtime, gs, 3, 11)
    assert driver.pending_signal() == GATE_FAILED
    assert "pending" in driver._journal.state.reason or "fingerprint" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_resume_verifies_offer_and_continues(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    # Second invocation: same run id, same config, fresh objects; the channel
    # runtime replays its own journal first (production behavior).
    runtime2 = ChannelRuntime.open(run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules)
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)
    assert driver2.pending_signal() is None
    state = driver2._journal.state
    assert state.status == GATE_ACTIVE
    assert state.phase == lgc.PHASE_ACCEPT_UPFRONT_PAYMENT
    assert state.restart_count == 1
    # R3: payment response settles the exact offer once.
    await run_gate_round(driver2, runtime2, gs, 12)
    deal = deals(runtime2)[state.data["upfront_deal_id"]]
    assert deal.payment_status is PaymentStatus.SETTLED
    assert (1, 2) not in gs.pending          # offer consumed
    assert gs.treasury[2] == 501


@pytest.mark.asyncio
async def test_resume_with_changed_offer_fails(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    from civ_mcp.lua.channel_payments import ExactPaymentOffer
    gs.pending[(1, 2)] = ExactPaymentOffer(1, 2, 5)   # changed gold
    runtime2 = ChannelRuntime.open(run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules)
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)
    assert driver2.pending_signal() == GATE_FAILED


@pytest.mark.asyncio
async def test_resume_with_absent_offer_fails(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    gs.pending.clear()
    runtime2 = ChannelRuntime.open(run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules)
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)
    assert driver2.pending_signal() == GATE_FAILED


@pytest.mark.asyncio
async def test_resume_config_fingerprint_mismatch_fails(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    cfg = gate_config(channel_rules=ChannelRules(funding_turns=3))
    run_dir = Path(tmp_path) / gate_config().run_id
    runtime2 = ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), gate_config().channel_rules
    )
    driver2 = lgc.ChannelsCoreDriver(cfg)
    with pytest.raises(Exception, match="fingerprint"):
        await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py -v -k restart`
Expected: FAIL — `_maybe_finish_round` returns immediately (boundary test) and `_verify_restart` raises `NotImplementedError` (resume tests).

- [ ] **Step 3: Write the implementation**

Replace `_maybe_finish_round` and `_verify_restart` in `ChannelsCoreDriver`:

```python
    def _round_complete(self, turn: int) -> bool:
        return self._captured_this_turn.get(turn, set()) >= self.gate_pids

    async def _maybe_finish_round(self, turn: int) -> None:
        """Round-boundary work. The restart takes effect only here: every
        remaining gate seat first completes its deterministic no-action
        capture, because coordinator shutdown always restores the human seat
        and disables the puppet hook — exiting mid-round would release the
        unplayed gate seats to the game AI (spec: Restart Handshake)."""

        if not self._round_complete(turn):
            return
        if self._restart_armed and self._journal.state.phase == PHASE_RESTART_REQUIRED:
            await self._request_restart(turn)
            return
        # Task 9 adds the terminal-gate round check here.

    async def _request_restart(self, turn: int) -> None:
        api = self.role_pid[ROLE_API]
        cli = self.role_pid[ROLE_CLI]
        recorded = self._journal.state.data.get("upfront_payment_fingerprint")
        if not recorded:
            self._fail("restart requested without a recorded payment fingerprint")
            return
        live = await self._live_offer_fingerprint(api, cli)
        if live is None:
            return   # _live_offer_fingerprint already failed the gate
        if live != recorded:
            self._fail(
                f"live pending trade fingerprint {live} does not equal the "
                f"recorded canonical fingerprint {recorded}"
            )
            return
        channel_state = self._runtime.state
        self._journal.append("data_recorded", {"data": {
            "restart_channel_sequence": channel_state.last_event_sequence,
            "restart_turn": turn,
        }})
        self._journal.append("restart_required", {"turn": turn})
        self._journal.write_result()
        self._restart_armed = False
        self._signal = GATE_RESTART_REQUIRED

    async def _live_offer_fingerprint(self, payer: int, payee: int) -> dict | None:
        payment_state = await self._gs.get_channel_payment_state(payer, payee, PAYMENT_GOLD)
        status = getattr(payment_state, "status", None)
        status = getattr(status, "value", status)
        if status != "exact":
            self._fail(
                f"official pending trade for ({payer},{payee}) is {status!r}; "
                "expected exactly one exact offer"
            )
            return None
        offer = getattr(payment_state, "offer", None)
        try:
            fingerprint = offer.fingerprint()
        except Exception as exc:
            self._fail(f"live offer fingerprint unavailable: {exc!r}")
            return None
        return fingerprint

    async def _verify_restart(self) -> None:
        """Resume-boundary reconciliation (spec: Restart Handshake). Runs
        inside attach(), after LiveGateJournal.open proved gate identity and
        configuration fingerprint equality and after ChannelRuntime.open
        performed its own journal replay and payment-intent reconciliation."""

        state = self._journal.state
        if state.restart_count != 1:
            self._fail(f"restart count {state.restart_count} at resume; expected exactly 1")
            return
        recorded = state.data.get("upfront_payment_fingerprint")
        live = await self._live_offer_fingerprint(
            self.role_pid[ROLE_API], self.role_pid[ROLE_CLI]
        )
        if live is None:
            return
        if live != recorded:
            self._fail(
                f"resumed offer fingerprint {live} does not equal the recorded "
                f"pre-restart fingerprint {recorded}"
            )
            return
        response_acks = [
            a for a in self._runtime.state.acknowledgements
            if a.deal_id == state.data.get("upfront_deal_id")
            and a.player_id == self.role_pid[ROLE_CLI]
            and a.turn > state.data.get("restart_turn", -1)
        ]
        if response_acks:
            self._fail("a payment-response acknowledgement already exists at resume")
            return
        self._journal.append("restart_verified", {"turn": state.data.get("restart_turn")})
        self._journal.append("phase_advanced", {
            "phase": PHASE_ACCEPT_UPFRONT_PAYMENT,
            "turn": state.data.get("restart_turn"),
        })
```

Also harden `attach`'s failure path so a resume-check failure records `gate_failed` instead of raising through the coordinator: wrap the `_verify_restart()` call —

```python
        if state.status == GATE_RESTART_REQUIRED:
            await self._verify_restart()
```

(`_verify_restart` itself only uses `_fail`, so nothing raises; the config-fingerprint mismatch test raises from `LiveGateJournal.open`, which happens BEFORE any journal exists for this process — that raise is correct and the coordinator's attach guard turns it into a failed run result in Task 11.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py -v`
Expected: all PASS. `test_resume_verifies_offer_and_continues` proves the R3 payment response settles and consumes the offer exactly once via the production `respond_to_payment` path.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
git commit -m "feat(arena): round-boundary restart handshake and resume verification"
```

---

### Task 9: Deadline phases — honored up-front deal, on-delivery breach, grievance, terminal PASS

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py`
- Test: `tests/arena/test_live_gate_channels.py` (append)

**Interfaces:**
- Consumes: deadline semantics verified in the file-structure notes (up-front favor: `favor_due_turn = settlement_turn + within`; on-delivery: `favor_due_turn = accepted_turn + within`, then `fund_by_turn = satisfaction_turn + funding_turns`); grievance mapping `wronged=counterparty, offender=proposer` for a funding breach.
- Produces: `_advance_after_capture` extended for the nine remaining phases; `_verify_terminal_evidence(turn)`; gate data key `"on_delivery_deal_id"`; signal value `"passed"`. Task 10's privacy pass is the last terminal condition — until then `_verify_terminal_evidence` treats an empty privacy-assertion list as passing vacuously (Task 10 makes it required).

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_live_gate_channels.py`:

```python
from civ_mcp.arena.live_gate import GATE_PASSED


async def full_run(tmp_path, *, stop_before_round=None):
    """Drive both invocations of the expected nine-round path. Returns the
    second-invocation driver plus runtime/gs. Rounds run at turns 10..18."""

    gs = GateGameState()
    driver, runtime, _ = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)   # R1
    await run_gate_round(driver, runtime, gs, 11)   # R2 -> restart
    assert driver.pending_signal() == GATE_RESTART_REQUIRED

    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    runtime2 = ChannelRuntime.open(run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules)
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)
    for offset, turn in enumerate(range(12, 19), start=3):   # R3..R9
        if stop_before_round is not None and offset >= stop_before_round:
            break
        await run_gate_round(driver2, runtime2, gs, turn)
        if driver2.pending_signal() is not None:
            break
    return driver2, runtime2, gs


@pytest.mark.asyncio
async def test_upfront_deal_honored_on_inclusive_deadline(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=5)
    # R3 settled at turn 12; favor due turn 13 (R4) — honored there, not earlier.
    deal = deals(runtime)[driver._journal.state.data["upfront_deal_id"]]
    assert deal.state is DealState.HONORED
    assert deal.favor_status is FavorStatus.SATISFIED
    assert deal.favor_due_turn == 13


@pytest.mark.asyncio
async def test_upfront_not_terminal_before_deadline(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=4)
    # After R3 (turn 12) the deal must be nonterminal: favor window still open.
    deal = deals(runtime)[driver._journal.state.data["upfront_deal_id"]]
    assert deal.state is DealState.ACTIVE
    assert driver.pending_signal() is None


@pytest.mark.asyncio
async def test_existing_routes_are_baseline_exempt(tmp_path):
    from civ_mcp.arena.channel_terms import ObservedRoute
    gs = GateGameState()
    # CLI actor already runs a route to the observer BEFORE acceptance: the
    # acceptance baseline must exempt it and the favor must still be honored.
    gs.routes[2] = (ObservedRoute(owner_id=2, trader_unit_id=77,
                                  destination_player=3,
                                  destination_is_city_state=False),)
    driver, runtime, _ = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_round(driver, runtime, gs, 11)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    runtime2 = ChannelRuntime.open(run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules)
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)
    await run_gate_round(driver2, runtime2, gs, 12)
    await run_gate_round(driver2, runtime2, gs, 13)
    deal = deals(runtime2)[driver2._journal.state.data["upfront_deal_id"]]
    assert deal.state is DealState.HONORED


@pytest.mark.asyncio
async def test_on_delivery_proposed_accepted_and_treasury_satisfied(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=8)
    # R5 (turn 14) CLI proposes; R6 (turn 15) API accepts; R7 (turn 16) favor due.
    deal_id = driver._journal.state.data["on_delivery_deal_id"]
    deal = deals(runtime)[deal_id]
    assert deal.proposer == 2 and deal.counterparty == 1
    assert deal.timing == "on_delivery"
    assert deal.favor.term_type == "maintain_gold_reserve"
    assert deal.favor_status is FavorStatus.SATISFIED
    assert deal.payment_status is PaymentStatus.DUE
    assert deal.fund_by_turn == 18    # satisfied turn 16 + funding_turns 2


@pytest.mark.asyncio
async def test_withholding_does_not_breach_early(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=9)
    # After R8 (turn 17): before fund_by (18), the deal must remain nonterminal
    # and no unexpected acknowledgement may exist for the CLI actor.
    deal = deals(runtime)[driver._journal.state.data["on_delivery_deal_id"]]
    assert deal.state is DealState.ACTIVE
    assert driver.pending_signal() is None


@pytest.mark.asyncio
async def test_full_run_breaches_grieves_and_passes(tmp_path):
    driver, runtime, gs = await full_run(tmp_path)
    assert driver.pending_signal() == GATE_PASSED

    state = runtime.state
    on_delivery = deals(runtime)[driver._journal.state.data["on_delivery_deal_id"]]
    assert on_delivery.state is DealState.BROKEN
    assert on_delivery.payment_status is PaymentStatus.FAILED

    assert len(state.grievances) == 1
    grievance = state.grievances[0]
    assert grievance.offender == 2       # proposer withheld funding
    assert grievance.wronged == 1
    assert grievance.deal_id == on_delivery.id

    upfront = deals(runtime)[driver._journal.state.data["upfront_deal_id"]]
    assert upfront.state is DealState.HONORED
    assert upfront.payment_status is PaymentStatus.SETTLED

    gate_state = driver._journal.state
    assert gate_state.status == GATE_PASSED
    import json as json_module
    result = json_module.loads(driver._journal.result_path.read_text())
    assert result["status"] == GATE_PASSED


@pytest.mark.asyncio
async def test_premature_terminal_state_fails_gate(tmp_path):
    gs = GateGameState()
    driver, runtime, _ = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_round(driver, runtime, gs, 11)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    runtime2 = ChannelRuntime.open(run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules)
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)
    await run_gate_round(driver2, runtime2, gs, 12)
    # Sabotage the favor before its due turn: the CLI actor starts a NEW route
    # to the observer, so the continuous term fails on the due turn and the
    # deal breaks instead of honoring — the gate must fail closed.
    from civ_mcp.arena.channel_terms import ObservedRoute
    gs.routes[2] = (ObservedRoute(owner_id=2, trader_unit_id=99,
                                  destination_player=3,
                                  destination_is_city_state=False),)
    await run_gate_round(driver2, runtime2, gs, 13)
    assert driver2.pending_signal() == GATE_FAILED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py -v -k "honored or on_delivery or withholding or full_run or premature or baseline_exempt"`
Expected: FAIL — after R3 the phase machine has no `PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE` handling, so nothing advances past `accept_upfront_payment`.

- [ ] **Step 3: Write the implementation**

Extend `_advance_after_capture` in `ChannelsCoreDriver` with the remaining phase arms (append to the existing `if/elif` chain; `DealState`, `FavorStatus`, `PaymentStatus` imported at the top of the method):

```python
        elif phase == PHASE_ACCEPT_UPFRONT_PAYMENT and role == ROLE_CLI:
            deal = self._deal(state.data["upfront_deal_id"])
            if deal is None:
                return
            if deal.payment_status is not PaymentStatus.SETTLED:
                self._fail(f"up-front payment not settled: {deal.payment_status}")
                return
            self._journal.append("data_recorded", {"data": {
                "upfront_favor_due_turn": deal.favor_due_turn,
            }})
            self._journal.append("phase_advanced", {
                "phase": PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE, "turn": turn,
            })
        elif phase == PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE:
            deal = self._deal(state.data["upfront_deal_id"])
            if deal is None:
                return
            due = state.data.get("upfront_favor_due_turn")
            if turn < due:
                if deal.is_terminal:
                    self._fail(
                        f"up-front deal terminal ({deal.state}) before its inclusive "
                        f"deadline turn {due}"
                    )
                return
            # Inclusive due turn: the obligated CLI actor's capture finalizes
            # the continuous term; the observer may also land here same-round.
            if deal.state is DealState.HONORED:
                self._journal.append("phase_advanced", {
                    "phase": PHASE_VERIFY_UPFRONT_HONORED, "turn": turn,
                })
                self._advance_after_capture(player_id, turn)   # fall through
            elif turn > due or deal.is_terminal:
                self._fail(
                    f"up-front deal is {deal.state}/{deal.favor_status} after its "
                    f"inclusive deadline turn {due}"
                )
        elif phase == PHASE_VERIFY_UPFRONT_HONORED:
            deal = self._deal(state.data["upfront_deal_id"])
            if deal is None:
                return
            if deal.state is not DealState.HONORED or deal.favor_status is not FavorStatus.SATISFIED:
                self._fail(f"up-front deal not honored: {deal.state}/{deal.favor_status}")
                return
            self._journal.append("phase_advanced", {
                "phase": PHASE_PROPOSE_ON_DELIVERY, "turn": turn,
            })
        elif phase == PHASE_PROPOSE_ON_DELIVERY and role == ROLE_CLI:
            acks = {a.source_id: a for a in self._runtime.state.acknowledgements}
            new_ids = [
                acks[e["source_id"]].deal_id
                for e in state.verified_actions
                if e["source_id"].startswith("cli:")
                and acks[e["source_id"]].deal_id
                and acks[e["source_id"]].deal_id != state.data["upfront_deal_id"]
            ]
            if len(new_ids) != 1:
                self._fail(f"expected exactly one captured on-delivery deal id, got {new_ids}")
                return
            self._journal.append("data_recorded", {"data": {"on_delivery_deal_id": new_ids[0]}})
            self._journal.append("phase_advanced", {
                "phase": PHASE_ACCEPT_ON_DELIVERY, "turn": turn,
            })
        elif phase == PHASE_ACCEPT_ON_DELIVERY and role == ROLE_API:
            deal = self._deal(state.data["on_delivery_deal_id"])
            if deal is None:
                return
            if deal.state is not DealState.ACTIVE:
                self._fail(f"on-delivery deal not active after acceptance: {deal.state}")
                return
            baseline = deal.favor.baseline
            if not baseline or any(
                key.endswith("baseline_complete") and value is not True
                for key, value in baseline.items()
            ):
                self._fail("on-delivery treasury baseline is missing or incomplete")
                return
            self._journal.append("data_recorded", {"data": {
                "on_delivery_favor_due_turn": deal.favor_due_turn,
            }})
            self._journal.append("phase_advanced", {
                "phase": PHASE_AWAIT_ON_DELIVERY_FAVOR, "turn": turn,
            })
        elif phase == PHASE_AWAIT_ON_DELIVERY_FAVOR:
            deal = self._deal(state.data["on_delivery_deal_id"])
            if deal is None:
                return
            due = state.data.get("on_delivery_favor_due_turn")
            if turn < due:
                if deal.is_terminal:
                    self._fail(f"on-delivery deal terminal early: {deal.state}")
                return
            if deal.favor_status is FavorStatus.SATISFIED and deal.payment_status is PaymentStatus.DUE:
                self._journal.append("data_recorded", {"data": {
                    "on_delivery_fund_by_turn": deal.fund_by_turn,
                }})
                self._journal.append("phase_advanced", {
                    "phase": PHASE_WITHHOLD_ON_DELIVERY_FUNDING, "turn": turn,
                })
            elif turn > due or deal.is_terminal:
                self._fail(
                    f"on-delivery favor is {deal.favor_status} after its inclusive "
                    f"deadline turn {due}"
                )
        elif phase == PHASE_WITHHOLD_ON_DELIVERY_FUNDING:
            deal = self._deal(state.data["on_delivery_deal_id"])
            if deal is None:
                return
            fund_by = state.data.get("on_delivery_fund_by_turn")
            if turn < fund_by:
                if deal.is_terminal:
                    self._fail(
                        f"on-delivery deal terminal ({deal.state}) before the "
                        f"inclusive funding deadline turn {fund_by}"
                    )
                return
            if role != ROLE_CLI and deal.state is DealState.ACTIVE:
                return   # deadline finalizes on the responsible player's capture
            if deal.state is DealState.BROKEN:
                self._journal.append("phase_advanced", {
                    "phase": PHASE_VERIFY_FUNDING_BREACH, "turn": turn,
                })
                self._advance_after_capture(player_id, turn)   # fall through
            elif turn > fund_by:
                self._fail(
                    f"on-delivery deal is {deal.state} after its inclusive funding "
                    f"deadline turn {fund_by}"
                )
        elif phase == PHASE_VERIFY_FUNDING_BREACH:
            deal = self._deal(state.data["on_delivery_deal_id"])
            if deal is None:
                return
            if deal.state is not DealState.BROKEN or deal.payment_status is not PaymentStatus.FAILED:
                self._fail(f"breach not canonical: {deal.state}/{deal.payment_status}")
                return
            grievances = [
                g for g in self._runtime.state.grievances if g.deal_id == deal.id
            ]
            if len(grievances) != 1:
                self._fail(f"expected exactly one deterministic grievance, got {len(grievances)}")
                return
            grievance = grievances[0]
            if grievance.offender != deal.proposer or grievance.wronged != deal.counterparty:
                self._fail(
                    f"grievance mapping wrong: offender {grievance.offender} wronged "
                    f"{grievance.wronged}; expected proposer {deal.proposer} offender / "
                    f"counterparty {deal.counterparty} wronged"
                )
                return
            self._journal.append("phase_advanced", {
                "phase": PHASE_VERIFY_TERMINAL_GATE, "turn": turn,
            })
```

Extend `_maybe_finish_round` (replace the Task 8 comment line) so the terminal round closes the gate after the observer's capture:

```python
        if self._journal.state.phase == PHASE_VERIFY_TERMINAL_GATE:
            self._verify_terminal_evidence(turn)
```

And add:

```python
    def _verify_terminal_evidence(self, turn: int) -> None:
        from civ_mcp.arena.channels import DealState, PaymentStatus

        state = self._journal.state
        channel_state = self._runtime.state
        by_id = {deal.id: deal for deal in channel_state.deals}
        upfront = by_id.get(state.data.get("upfront_deal_id"))
        broken = by_id.get(state.data.get("on_delivery_deal_id"))
        checks = (
            (upfront is not None and upfront.state is DealState.HONORED, "an honored deal"),
            (upfront is not None and upfront.payment_status is PaymentStatus.SETTLED,
             "a settled payment"),
            (broken is not None and broken.state is DealState.BROKEN, "a broken deal"),
            (bool(channel_state.grievances), "a deterministic grievance"),
        )
        for ok, label in checks:
            if not ok:
                self._fail(f"terminal evidence missing: {label}")
                return
        for pid in (self.role_pid[ROLE_API], self.role_pid[ROLE_CLI]):
            projection = self._runtime.project_for_player(pid, turn)
            if not any(m.text == self.canary for m in projection.messages):
                self._fail(
                    f"canary absent from authorized player {pid} projection — the "
                    "canary was not actually exercised"
                )
                return
        if any(a.get("result") == "FAIL" for a in state.privacy_assertions):
            self._fail("a privacy assertion failed")   # unreachable: reducer locks first
            return
        self._journal.append("gate_passed", {"evidence": {
            "honored_deal": upfront.id,
            "broken_deal": broken.id,
            "grievances": len(channel_state.grievances),
            "privacy_assertions": len(state.privacy_assertions),
        }})
        self._journal.write_result()
        self._signal = GATE_PASSED
```

Two behavioral notes for the implementer:
- The `_advance_after_capture(player_id, turn)` self-recursion after an inclusive-deadline `phase_advanced` lets a single capture both detect the transition and run the follow-up verification arm — bounded at one hop by construction.
- Deadline finalization timing is owned by the channel runtime (`_finalize_player` / admission evaluation). The tests pin the driver's tolerance exactly: nonterminal strictly before the due turn, canonical transition visible by the end of the due turn's round, terminal failure if the turn moves past the inclusive deadline without it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py -v`
Expected: all PASS. If a deadline assertion fires one round early/late, do NOT touch the runtime: adjust only the driver's expected due-turn arithmetic to the canonical `deal.favor_due_turn`/`deal.fund_by_turn` values the runtime computed (they are read from the deal, so the schedule table in the plan header is the only place to reconcile).

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
git commit -m "feat(arena): live-gate deadline phases, funding breach, terminal PASS"
```

---

### Task 10: Observer privacy assertions

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py`
- Test: `tests/arena/test_live_gate_channels.py` (append)

**Interfaces:**
- Consumes: `admission.projection` (`ChannelProjection`), `admission.block` (production `format_channel_block` output), `build_opening_prompt` from `civ_mcp.arena.prompting`, run-dir `transcript.jsonl` (player-3 records), gate data fingerprints.
- Produces: `_observer_assertions(admission, base_result, turn)` wired into the observer arm of `_seat_turn_inner`; privacy events (`privacy_asserted`) per artifact kind; forensic preservation file `live_gate/privacy_fail_<turn>_<kind>.json`; terminal gate now REQUIRES at least one privacy assertion per observer capture (tighten `_verify_terminal_evidence`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_live_gate_channels.py`:

```python
def privacy_assertions(driver):
    return driver._journal.state.privacy_assertions


@pytest.mark.asyncio
async def test_observer_assertions_run_every_admission(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    first = privacy_assertions(driver)
    kinds = {a["artifact_kind"] for a in first}
    assert {"projection", "channel_block", "opening_prompt",
            "policy_result", "transcript_records"} <= kinds
    assert all(a["result"] == "PASS" for a in first)
    await run_gate_round(driver, runtime, gs, 11)
    assert len(privacy_assertions(driver)) > len(first)


@pytest.mark.asyncio
async def test_observer_projection_is_empty_of_participants(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    projection = runtime.project_for_player(3, 10)
    assert projection.messages == ()
    assert projection.deals == ()
    assert projection.acknowledgements == ()
    block = runtime.project_for_player(3, 10)
    assert driver.canary not in str(block)


@pytest.mark.asyncio
async def test_planted_canary_in_observer_view_fails_gate(tmp_path, monkeypatch):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    await run_gate_seat(driver, runtime, gs, 2, 10)
    # Plant the canary into the observer's admitted block: the raw-text scan
    # must fail the assertion, lock the journal, and fail the gate.
    original = runtime.admit_player

    async def tainted(gs_arg, player_id, turn):
        admission = await original(gs_arg, player_id, turn)
        if player_id == 3:
            import dataclasses as dc
            admission = dc.replace(admission, block=admission.block + "\n" + driver.canary)
        return admission

    monkeypatch.setattr(runtime, "admit_player", tainted)
    await run_gate_seat(driver, runtime, gs, 3, 10)
    assert driver.pending_signal() == GATE_FAILED
    failures = [a for a in privacy_assertions(driver) if a["result"] == "FAIL"]
    assert failures and failures[0]["artifact_kind"] == "channel_block"
    forensic = list((driver._journal.gate_dir).glob("privacy_fail_*"))
    assert forensic, "forensic input must be preserved in the protected gate dir"
    # The leaked text never reaches the public reason string.
    assert driver.canary not in driver._journal.state.reason


@pytest.mark.asyncio
async def test_deal_id_text_alone_is_not_a_privacy_failure(tmp_path):
    # Production CLI instructions use generic examples like deal-000001; a
    # coinciding monotonic id in the observer's block must NOT fail the scan.
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    upfront_id = driver._journal.state.data["upfront_deal_id"]
    forbidden = driver._forbidden_values()
    assert upfront_id not in forbidden
    assert all(a["result"] == "PASS" for a in privacy_assertions(driver))


@pytest.mark.asyncio
async def test_terminal_pass_requires_observer_assertions(tmp_path):
    driver, runtime, gs = await full_run(tmp_path)
    assert driver.pending_signal() == GATE_PASSED
    per_turn = {}
    for assertion in privacy_assertions(driver):
        per_turn.setdefault(assertion["turn"], set()).add(assertion["artifact_kind"])
    # The shared journal also holds first-invocation assertions (turns 10-11);
    # the resumed run must have covered every round R3..R9 (turns 12..18).
    assert set(range(12, 19)) <= set(per_turn)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py -v -k "privacy or observer"`
Expected: FAIL — no `privacy_asserted` events exist (`_seat_turn_inner`'s observer arm returns immediately).

- [ ] **Step 3: Write the implementation**

Add imports at the top of `live_gate_channels.py`:

```python
import dataclasses

from civ_mcp.arena.prompting import build_opening_prompt
```

Replace the observer arm in `_seat_turn_inner`:

```python
        role = self.pid_role[player_id]
        if role == ROLE_OBSERVER:
            self._observer_assertions(admission, base_result, turn)
            return base_result
```

Add the privacy machinery to `ChannelsCoreDriver`:

```python
    def _forbidden_values(self) -> tuple[str, ...]:
        """Raw-text values that must never appear in a player-3 artifact.
        Deal-ID text alone is NOT included: production CLI instructions use
        generic examples such as deal-000001, which can coincide with a real
        monotonic id without disclosing the deal (spec Privacy Contract)."""

        data = self._journal.state.data
        values = [
            self.canary,
            self.canary.removeprefix("GATE-CANARY-"),
            UPFRONT_PROPOSAL_TEXT,
            ON_DELIVERY_PROPOSAL_TEXT,
            f"api:{self.config.run_id}:{self.role_pid[ROLE_API]}:",
            f"cli:{self.config.run_id}:{self.role_pid[ROLE_CLI]}:",
        ]
        fingerprint = data.get("upfront_payment_fingerprint")
        if fingerprint:
            values.append(json.dumps(fingerprint, sort_keys=True, separators=(",", ":")))
        return tuple(values)

    @staticmethod
    def _jsonable(value):
        if isinstance(value, (frozenset, set)):
            return sorted(map(str, value))
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return dataclasses.asdict(value)
        return str(value)

    def _player3_transcript_text(self) -> str:
        path = self._run_dir / "transcript.jsonl"
        if not path.exists():
            return ""
        observer = self.role_pid[ROLE_OBSERVER]
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("player_id") == observer:
                lines.append(line)
        return "\n".join(lines)

    def _observer_assertions(self, admission, policy_result, turn) -> None:
        observer = self.role_pid[ROLE_OBSERVER]
        forbidden = self._forbidden_values()
        projection = admission.projection
        artifacts = (
            ("projection", json.dumps(
                dataclasses.asdict(projection), sort_keys=True, default=self._jsonable
            )),
            ("channel_block", admission.block),
            ("opening_prompt", build_opening_prompt(
                player_id=observer, turn=turn, channel_block=admission.block,
            )),
            ("policy_result", json.dumps(
                policy_result, sort_keys=True, default=self._jsonable
            )),
            ("transcript_records", self._player3_transcript_text()),
        )
        participants = {self.role_pid[ROLE_API], self.role_pid[ROLE_CLI]}
        structure_ok = (
            not any(
                m.from_player in participants or m.to_player in participants
                for m in projection.messages
            )
            and not any(
                d.proposer in participants or d.counterparty in participants
                for d in projection.deals
            )
            and not any(
                g.offender in participants or g.wronged in participants
                for g in projection.grievances
            )
            and projection.acknowledgements == ()
        )
        for kind, text in artifacts:
            leaked = [value for value in forbidden if value and value in text]
            failed = bool(leaked) or (kind == "projection" and not structure_ok)
            payload = {
                "turn": turn,
                "player_id": observer,
                "artifact_kind": kind,
                "input_digest": hashlib.sha256(text.encode()).hexdigest()[:16],
                "forbidden_digests": [
                    hashlib.sha256(v.encode()).hexdigest()[:16] for v in forbidden
                ],
                "result": "FAIL" if failed else "PASS",
            }
            self._journal.append("privacy_asserted", payload)
            if failed:
                # Preserve the private forensic input in the protected gate
                # directory; never copy leaked text into a public summary.
                forensic = self._journal.gate_dir / f"privacy_fail_{turn}_{kind}.json"
                forensic.write_text(json.dumps({
                    "turn": turn, "artifact_kind": kind, "input": text,
                    "leaked_digests": [
                        hashlib.sha256(v.encode()).hexdigest()[:16] for v in leaked
                    ],
                }, sort_keys=True))
                import os as os_module
                os_module.chmod(forensic, 0o600)
                self._fail(
                    f"privacy assertion failed: {kind} at turn {turn} "
                    f"(forensic input preserved in the gate directory)"
                )
                return
```

Tighten `_verify_terminal_evidence` — replace the vacuous privacy check with a required-coverage check:

```python
        if not state.privacy_assertions:
            self._fail("terminal evidence missing: observer privacy assertions")
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_live_gate_channels.py tests/arena/test_live_gate.py -v`
Expected: all PASS (including all earlier scenario tests — the observer arm change must not disturb the lifecycle).

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
git commit -m "feat(arena): observer privacy assertions with forensic fail-closed handling"
```

---

### Task 11: Coordinator wiring — attach, admission/capture hooks, signal break

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py` (`run_arena` signature at line 553; channel-runtime open block ending ~line 631; puppet admission block at ~lines 1661-1690; failed-policy path after `hook.restore_local(conn, 0)` at ~line 2369; played path after `hook.restore_local(conn, 0)` at ~line 2567; the result dict at ~line 2737)
- Test: `tests/arena/test_coordinator.py` (append)

**Interfaces:**
- Consumes: driver protocol from Tasks 6-10 (`attach`, `note_admission`, `after_seat_capture`, `pending_signal`, `result_summary`).
- Produces: `run_arena(..., live_gate_driver=None)`; result dict gains a `"live_gate"` key when a driver is present. Task 12's arena wiring and exit codes depend on that key.

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_coordinator.py` (reuse the module's existing `FakeConn`, `FakeGS`, `ArenaConfig`, `PlayerSpec`, `run_arena`, `ScriptedPolicy` imports):

```python
class FakeGateDriver:
    """Minimal driver double satisfying the coordinator-facing protocol."""

    def __init__(self, signal_after=None):
        self.attached = False
        self.attach_kwargs = None
        self.admissions = []
        self.captures = []
        self._signal = None
        self._signal_after = signal_after  # capture count that raises the signal
        self._policy = ScriptedPolicy()

    async def attach(self, *, gs, channel_runtime, run_dir):
        self.attached = True
        self.attach_kwargs = {"gs": gs, "channel_runtime": channel_runtime,
                              "run_dir": run_dir}

    def policy_for(self, player_id):
        return self._policy

    def note_admission(self, player_id, turn, admission, error):
        self.admissions.append((player_id, turn, admission is not None, error))

    async def after_seat_capture(self, *, player_id, turn, channel_fields):
        self.captures.append((player_id, turn, dict(channel_fields)))
        if self._signal_after is not None and len(self.captures) >= self._signal_after:
            self._signal = "restart_required"

    def pending_signal(self):
        return self._signal

    def result_summary(self):
        return {"status": self._signal or "active", "phase": "p", "reason": "",
                "restart_count": 0, "run_id": "run-gate"}


def _channel_civ(pid, provider="local", model="m"):
    from civ_mcp.arena.config import ChannelOptions, CivOptions
    return PlayerSpec(pid, provider, model,
                      options=CivOptions(channels=ChannelOptions(enabled=True)))


# FakeGS has no get_channel_observation, so a channel-enabled seat would fail
# admission; GateGameState (Task 6 fakes) serves complete observations plus
# the overview/units surface ScriptedPolicy needs.
from tests.arena.live_gate_fakes import GateGameState


@pytest.mark.asyncio
async def test_gate_driver_attach_receives_runtime_and_run_dir(tmp_path):
    conn, gs = FakeConn(), GateGameState()
    driver = FakeGateDriver()
    cfg = ArenaConfig(players=[_channel_civ(1)], max_puppet_turns=1,
                      puppet_ids=[1], run_id="run-gate",
                      transcript_dir=str(tmp_path))
    result = await run_arena(conn, gs, cfg, policy_for=lambda pid: driver.policy_for(pid),
                             live_gate_driver=driver)
    assert driver.attached is True
    assert driver.attach_kwargs["channel_runtime"] is not None
    assert str(driver.attach_kwargs["run_dir"]).endswith("run-gate")
    assert result["live_gate"]["status"] == "active"
    assert conn.restored is True


@pytest.mark.asyncio
async def test_gate_admission_and_capture_hooks_fire_in_order(tmp_path):
    conn, gs = FakeConn(), GateGameState()
    driver = FakeGateDriver()
    cfg = ArenaConfig(players=[_channel_civ(1)], max_puppet_turns=1,
                      puppet_ids=[1], run_id="run-gate",
                      transcript_dir=str(tmp_path))
    await run_arena(conn, gs, cfg, policy_for=lambda pid: driver.policy_for(pid),
                    live_gate_driver=driver)
    assert driver.admissions == [(1, 2, True, "")]
    assert [(pid, turn) for pid, turn, _fields in driver.captures] == [(1, 2)]


@pytest.mark.asyncio
async def test_gate_signal_breaks_admission_loop(tmp_path):
    conn, gs = FakeConn(), GateGameState()
    conn._polls = iter([
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
        ["LOCAL|1", "TURN|2", "ACTIVE|true", "LAST|1"],
        ["LOCAL|1", "TURN|3", "ACTIVE|true", "LAST|1"],   # must never be served
    ])
    driver = FakeGateDriver(signal_after=1)
    cfg = ArenaConfig(players=[_channel_civ(1)], max_puppet_turns=5,
                      puppet_ids=[1], run_id="run-gate",
                      transcript_dir=str(tmp_path))
    result = await run_arena(conn, gs, cfg, policy_for=lambda pid: driver.policy_for(pid),
                             live_gate_driver=driver)
    assert len(driver.captures) == 1               # loop broke on the signal
    assert result["live_gate"]["status"] == "restart_required"
    assert conn.restored is True                   # finally still handed back


@pytest.mark.asyncio
async def test_gate_attach_failure_fails_run_and_restores(tmp_path):
    class ExplodingDriver(FakeGateDriver):
        async def attach(self, **kwargs):
            raise RuntimeError("fingerprint mismatch")

        def result_summary(self):
            return {"status": "failed", "phase": "", "reason": "fingerprint mismatch",
                    "restart_count": 0, "run_id": "run-gate"}

    conn, gs = FakeConn(), FakeGS()
    driver = ExplodingDriver()
    cfg = ArenaConfig(players=[_channel_civ(1)], max_puppet_turns=1,
                      puppet_ids=[1], run_id="run-gate",
                      transcript_dir=str(tmp_path))
    result = await run_arena(conn, gs, cfg, policy_for=lambda pid: driver.policy_for(pid),
                             live_gate_driver=driver)
    assert result["live_gate"]["status"] == "failed"
    assert result["puppet_turns_played"] == 0
    assert conn.restored is True


@pytest.mark.asyncio
async def test_gate_requires_channel_runtime(tmp_path):
    # No channel-enabled players -> no runtime -> the gate must fail the run
    # rather than proceed without canonical state.
    conn, gs = FakeConn(), FakeGS()
    driver = FakeGateDriver()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      puppet_ids=[1], run_id="run-gate",
                      transcript_dir=str(tmp_path))
    result = await run_arena(conn, gs, cfg, policy_for=lambda pid: driver.policy_for(pid),
                             live_gate_driver=driver)
    assert result["live_gate"]["status"] in ("failed", "active")
    assert driver.attached is False or result["puppet_turns_played"] == 0


@pytest.mark.asyncio
async def test_no_driver_leaves_result_shape_unchanged(tmp_path):
    conn, gs = FakeConn(), FakeGS()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])
    result = await run_arena(conn, gs, cfg, policy=ScriptedPolicy())
    assert "live_gate" not in result
```

Note on `test_gate_requires_channel_runtime`: pin the exact expected behavior while implementing — the implementation below makes the run return `status "failed"` with zero puppet turns; tighten the assertion to that once green.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py -v -k gate`
Expected: FAIL — `run_arena() got an unexpected keyword argument 'live_gate_driver'`.

- [ ] **Step 3: Write the implementation**

In `src/civ_mcp/arena/coordinator.py`:

(a) Signature (line 553):

```python
async def run_arena(
    conn,
    gs,
    config,
    policy=None,
    policy_for=None,
    transcript=None,
    channel_runtime=None,
    live_gate_driver=None,
) -> dict:
```

(b) Immediately after the channel-runtime open/validate block (after the `else: channel_runtime = None` at ~line 620, before the seat-0 capture comments):

```python
    # --- Live-gate attach (spec 2026-07-17). The driver is resolved by
    # arena.py; a None driver leaves every existing path untouched — the
    # coordinator must not create a live-gate directory or evaluate scenario
    # code on the disabled path.
    def _gate_result_field() -> dict:
        return {"live_gate": live_gate_driver.result_summary()}

    if live_gate_driver is not None:
        try:
            if channel_runtime is None:
                raise RuntimeError(
                    "live gate requires the channel runtime: "
                    + (channel_runtime_error or "no channel-enabled players")
                )
            await live_gate_driver.attach(
                gs=gs,
                channel_runtime=channel_runtime,
                run_dir=Path(config.transcript_dir) / run_id,
            )
        except Exception as e:
            print(f"[arena] live gate attach failed: {e!r}", file=sys.stderr)
            return {
                "puppet_turns_played": 0,
                "turns_slept": 0,
                "seat0_turns_played": 0,
                "seat0_turns_failed": 0,
                "seat0_human_pending": 0,
                "log": log,
                **_gate_result_field(),
            }
```

(`Path` is already imported at the top of coordinator.py; verify and add `from pathlib import Path` if not.) The early return still passes through the run-scope `finally`, which performs the human handback best-effort — exactly the safe-deactivation contract.

(c) In the puppet admission block, after the `try/except` around `admit_player` and the `channel_fields_state["error"] = channel_error` line (~line 1677), add:

```python
                if live_gate_driver is not None and not is_seat0:
                    live_gate_driver.note_admission(
                        st.local, st.turn, channel_admission, channel_error
                    )
```

(d) Failed-policy path — after `await hook.restore_local(conn, 0)` / `remaining -= 1` (~lines 2369-2372), before `continue`:

```python
                    if live_gate_driver is not None:
                        await live_gate_driver.after_seat_capture(
                            player_id=st.local, turn=st.turn,
                            channel_fields=_channel_fields(),
                        )
                        if live_gate_driver.pending_signal() is not None:
                            break
                    continue
```

(the existing `continue` moves after the new block).

(e) Played path — after `await hook.restore_local(conn, 0)` at ~line 2567:

```python
                if live_gate_driver is not None:
                    await live_gate_driver.after_seat_capture(
                        player_id=st.local, turn=st.turn,
                        channel_fields=_channel_fields(),
                    )
                    if live_gate_driver.pending_signal() is not None:
                        break
```

(f) Result dict (~line 2737):

```python
        return {
            "puppet_turns_played": played,
            "turns_slept": slept,
            "seat0_turns_played": seat0_played,
            "seat0_turns_failed": seat0_failed,
            "seat0_human_pending": seat0_pending,
            "log": log,
            **({} if live_gate_driver is None else _gate_result_field()),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py -v`
Expected: all PASS — the new gate tests AND the full existing coordinator suite (the disabled path must be regression-free).

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py
git commit -m "feat(arena): coordinator live-gate hooks with round-safe signal break"
```

---

### Task 12: arena.py wiring — gate-mode policy construction, preflight skip, exit codes

**Files:**
- Modify: `src/civ_mcp/arena/arena.py` (`_run` at lines 181-226; `main` at line 228)
- Test: `tests/arena/test_arena_wiring.py` (append)

**Interfaces:**
- Consumes: `resolve_live_gate_driver` (Task 2), the `"live_gate"` result key (Task 11).
- Produces: `_run` returns the gate summary dict (or `None` when no gate); `main()` maps `restart_required` → `SystemExit(75)`, `passed` → exit 0, anything else with a gate → `SystemExit(1)`; a machine-readable `LIVE_GATE {...}` line printed for every gate outcome.

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_arena_wiring.py`:

```python
import asyncio
import json

import pytest

from civ_mcp.arena import arena as arena_module
from civ_mcp.arena import live_gate as live_gate_module
from civ_mcp.arena.config import (
    ArenaConfig,
    ChannelOptions,
    CivOptions,
    LiveGateOptions,
    PlayerSpec,
)
from civ_mcp.arena.live_gate import ScenarioMeta


class _StubDriver:
    def __init__(self, config):
        self.config = config

    def policy_for(self, player_id):
        async def policy(gs, pid, turn, **kwargs):
            return {"summary": "stub"}
        return policy


def _gate_cfg(run_id="run-gate"):
    def civ(pid, provider, model=""):
        return PlayerSpec(pid, provider, model,
                          options=CivOptions(channels=ChannelOptions(enabled=True)))
    return ArenaConfig(
        players=[civ(1, "local", "m"), civ(2, "cli-codex"), civ(3, "scripted")],
        max_puppet_turns=36, max_game_turns=36, run_id=run_id,
        live_gate=LiveGateOptions(
            enabled=True, scenario="stub_gate_v1",
            roles=(("api_actor", 1), ("cli_actor", 2), ("privacy_observer", 3)),
        ),
    )


@pytest.fixture
def stub_gate(monkeypatch):
    meta = ScenarioMeta(
        name="stub_gate_v1", revision=1,
        role_contracts=(("api_actor", "in_process"), ("cli_actor", "cli"),
                        ("privacy_observer", "scripted")),
        minimum_captures=lambda config: 1,
        create_driver=_StubDriver,
    )
    monkeypatch.setattr(live_gate_module, "_SCENARIOS", {meta.name: meta})
    return meta


def test_gate_mode_skips_backend_and_cli_preflight(monkeypatch, tmp_path, stub_gate):
    """Gate mode must construct no local backend, check no CLI binary, and
    invoke run_arena with the resolved driver and gate policies."""
    seen = {}

    async def fake_run_arena(conn, gs, cfg, policy_for=None, transcript=None,
                             live_gate_driver=None):
        seen["driver"] = live_gate_driver
        seen["policy_for"] = policy_for
        return {"puppet_turns_played": 0, "turns_slept": 0, "seat0_turns_played": 0,
                "seat0_turns_failed": 0, "seat0_human_pending": 0, "log": [],
                "live_gate": {"status": "passed", "phase": "verify_terminal_gate",
                              "reason": "", "restart_count": 1, "run_id": cfg.run_id}}

    class FakeConn:
        async def connect(self): pass

    def fail_which(cmd):
        raise AssertionError(f"CLI preflight must not run in gate mode: {cmd}")

    def fail_backend(*args, **kwargs):
        raise AssertionError("no local backend may be constructed in gate mode")

    monkeypatch.setattr(arena_module, "run_arena", fake_run_arena)
    monkeypatch.setattr(arena_module, "GameConnection", lambda: FakeConn())
    monkeypatch.setattr(arena_module, "resolve_config", lambda args: _gate_cfg())
    monkeypatch.setattr("civ_mcp.arena.backends.OpenAICompatBackend", fail_backend)
    monkeypatch.setattr(arena_module.shutil, "which", fail_which)

    args = arena_module.build_args([
        "--transcript-dir", str(tmp_path), "--no-transcript",
    ])
    gate = asyncio.run(arena_module._run(args))
    assert gate == {"status": "passed", "phase": "verify_terminal_gate",
                    "reason": "", "restart_count": 1, "run_id": "run-gate"}
    assert isinstance(seen["driver"], _StubDriver)
    # Ordinary policies are never built: the gate driver provides them.
    assert seen["policy_for"](1) is not None


def test_gate_mode_rejects_dry_run(monkeypatch, tmp_path, stub_gate):
    monkeypatch.setattr(arena_module, "resolve_config", lambda args: _gate_cfg())
    args = arena_module.build_args([
        "--transcript-dir", str(tmp_path), "--no-transcript", "--dry-run",
    ])
    with pytest.raises(SystemExit, match="dry-run"):
        asyncio.run(arena_module._run(args))


def test_main_exit_codes_for_gate_outcomes(monkeypatch, capsys):
    outcomes = {
        "restart_required": 75,
        "failed": 1,
        "active": 1,
    }
    for status, code in outcomes.items():
        async def fake_run(args, _status=status):
            return {"status": _status, "phase": "p", "reason": "r",
                    "restart_count": 1, "run_id": "run-gate"}
        monkeypatch.setattr(arena_module, "_run", fake_run)
        monkeypatch.setattr(arena_module, "build_args", lambda argv=None: object())
        with pytest.raises(SystemExit) as exc_info:
            arena_module.main()
        assert exc_info.value.code == code
        line = [l for l in capsys.readouterr().out.splitlines()
                if l.startswith("LIVE_GATE ")]
        assert line and json.loads(line[0][len("LIVE_GATE "):])["status"] == status


def test_main_gate_passed_exits_zero(monkeypatch, capsys):
    async def fake_run(args):
        return {"status": "passed", "phase": "verify_terminal_gate", "reason": "",
                "restart_count": 1, "run_id": "run-gate"}
    monkeypatch.setattr(arena_module, "_run", fake_run)
    monkeypatch.setattr(arena_module, "build_args", lambda argv=None: object())
    arena_module.main()   # no SystemExit
    line = [l for l in capsys.readouterr().out.splitlines()
            if l.startswith("LIVE_GATE ")]
    assert json.loads(line[0][len("LIVE_GATE "):])["status"] == "passed"


def test_main_without_gate_prints_no_gate_line(monkeypatch, capsys):
    async def fake_run(args):
        return None
    monkeypatch.setattr(arena_module, "_run", fake_run)
    monkeypatch.setattr(arena_module, "build_args", lambda argv=None: object())
    arena_module.main()
    assert "LIVE_GATE" not in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_arena_wiring.py -v -k gate`
Expected: FAIL — `_run` returns `None` implicitly and knows no gate mode; `main` exits without gate mapping.

- [ ] **Step 3: Write the implementation**

In `src/civ_mcp/arena/arena.py`:

(a) `_run` — after `cfg = resolve_config(args)` / `specs = cfg.players` (line 186), insert driver resolution and branch policy construction (replacing the current unconditional `build_policies` call and guarding the preflight block):

```python
    from civ_mcp.arena.live_gate import resolve_live_gate_driver
    live_gate_driver = resolve_live_gate_driver(cfg)
    if live_gate_driver is not None and args.dry_run:
        raise SystemExit("--dry-run cannot be combined with an enabled live_gate")
    # (lines 187-199 — run_id resolution, run_dir/cost/transcript setup —
    #  stay exactly as they are today)
    if live_gate_driver is not None:
        # Gate mode: deterministic driver-owned policies only. The configured
        # provider identities were validated as path contracts; no local
        # backend, Codex process, or other model policy is constructed.
        policies, local_backends = {}, []
        policy_for = live_gate_driver.policy_for
    else:
        policies, local_backends = build_policies(specs, cost, cfg)
        if args.dry_run:
            sp = ScriptedPolicy()
            policy_for = lambda pid: sp
        else:
            # Move arena.py's EXISTING preflight block here verbatim (today at
            # lines 205-221: the `for b in local_backends: ... reachable()`
            # loop, the CLI `shutil.which` PATH check, and the cli-claude
            # .mcp.json check) — byte-for-byte unchanged, just re-indented
            # under this else-branch so gate mode never reaches it.
            policy_for = lambda pid: policies[pid]
```

(b) `_run` tail — pass the driver through and return the gate summary:

```python
    result = await run_arena(conn, gs, cfg, policy_for=policy_for,
                             transcript=transcript,
                             live_gate_driver=live_gate_driver)
    print(json.dumps({"result": result, "cost": cost.summary()}, indent=2))
    return result.get("live_gate") if isinstance(result, dict) else None
```

(c) `main` — translate the driver's persisted signal into the machine-readable line and the process exit status (Locked Decision 9: the persisted state and printed line are the authoritative operator signals; the exit code serves foreground and test invocations):

```python
def main():
    gate = asyncio.run(_run(build_args()))
    if gate is None:
        return
    print("LIVE_GATE " + json.dumps(gate, sort_keys=True, separators=(",", ":")))
    status = gate.get("status")
    if status == "restart_required":
        raise SystemExit(75)
    if status != "passed":
        raise SystemExit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_arena_wiring.py -v`
Expected: all PASS, including every pre-existing wiring test (the non-gate path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/arena.py tests/arena/test_arena_wiring.py
git commit -m "feat(arena): gate-mode arena wiring, LIVE_GATE result line, exit 75/1/0"
```

---

### Task 13: Checked-in gate experiment, full-suite gate, diff hygiene

**Files:**
- Modify: `experiments/arena-channels-core-smoke.yaml`
- Test: `tests/arena/test_experiment.py` (append one test)

**Interfaces:**
- Consumes: everything above; the real scenario registration (Task 5) makes the checked-in YAML self-validating.
- Produces: the exact experiment the attended run invokes twice with the same run ID.

- [ ] **Step 1: Write the failing test**

Append to `tests/arena/test_experiment.py`:

```python
def test_checked_in_channels_core_gate_experiment_validates():
    cfg = load_experiment("experiments/arena-channels-core-smoke.yaml")
    assert cfg.run_id == "arena-channels-core-gate-v1"
    assert cfg.live_gate.enabled is True
    assert cfg.live_gate.scenario == "unofficial_channels_core_v1"
    assert dict(cfg.live_gate.roles) == {
        "api_actor": 1, "cli_actor": 2, "privacy_observer": 3,
    }
    assert cfg.max_puppet_turns == 36
    assert cfg.max_game_turns == 36
    providers = {spec.player_id: spec.provider for spec in cfg.players}
    assert providers == {1: "local", 2: "cli-codex", 3: "scripted"}
    assert all(spec.options.channels.enabled for spec in cfg.players)
    assert all(spec.options.attention.mode == "off" for spec in cfg.players)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/arena/test_experiment.py -v -k checked_in_channels_core_gate`
Expected: FAIL — the current YAML has run_id `arena-channels-core-smoke`, two civs, and no `live_gate` block.

- [ ] **Step 3: Update the experiment**

Replace `experiments/arena-channels-core-smoke.yaml` with:

```yaml
# Deterministic unofficial-channels live gate (spec 2026-07-17).
# The prior failed two-seat run directory cannot be reused: adding the privacy
# observer changes the canonical enabled-player identity, so the new run_id
# prevents accidental continuation of incompatible channel state.
run_id: arena-channels-core-gate-v1
# 27 captures expected on the nine-round path; 36 leaves room for watcher
# handoff and deterministic blocker cleanup. Validation recomputes the
# scenario minimum from channel_rules rather than trusting this number.
max_puppet_turns: 36
max_game_turns: 36
channel_rules:
  acceptance_turns: 3
  funding_turns: 2
  payment_response_turns: 2
live_gate:
  enabled: true
  scenario: unofficial_channels_core_v1
  roles:
    api_actor: 1
    cli_actor: 2
    privacy_observer: 3
civs:
  - player: 1
    provider: local
    model: gemma4-26b
    gateway: http://192.168.20.196:11440/v1
    tools: full
    channels: {enabled: true}
  - player: 2
    provider: cli-codex
    model: gpt-5.5
    channels: {enabled: true}
  - player: 3
    provider: scripted
    channels: {enabled: true}
```

(Provider/model/gateway fields stay as validated path contracts — gate mode never constructs their backends.)

- [ ] **Step 4: Run the full suite and diff hygiene gate**

Run: `uv run --extra test pytest`
Expected: entire repository suite PASS (baseline before this plan: 1237 green; this plan adds tests on top — zero pre-existing failures tolerated).

Run: `git diff --check && git diff main --check`
Expected: no output (no whitespace/conflict-marker problems).

- [ ] **Step 5: Commit**

```bash
git add experiments/arena-channels-core-smoke.yaml tests/arena/test_experiment.py
git commit -m "feat(arena): check in unofficial-channels live-gate experiment"
```

---

## Out of scope for this plan (deliberately)

- **The attended run itself** — the two-invocation live procedure (exit 75, rearm via the `civ6-arena-live` skill, exit 0), retained-evidence collection, and updating `docs/superpowers/plans/2026-07-16-arena-unofficial-channels-core-live-gate.md` from FAIL/BLOCKED to lifecycle PASS all happen live with riz driving, per the spec's Attended Acceptance section. The operator rules to carry into that session: don't end the human turn in the restart gap until the second watcher is armed; the gate needs a disposable midgame save with alive players 0-3, sufficient player-1 gold, and no conflicting pending deal.
- **The raw FireTuner-probe scenario** — a separate required gate (spec Non-Goals); Task 12 attended-gate completion stays blocked on both.
- **Analyzer additions** — the spec's terminal evidence references analyzer JSON/Markdown; the existing analyzer already reports deals/grievances from the channel journal. If the attended run shows a gap, that is a follow-up, not part of this driver.

## Self-Review (performed while writing)

**Spec coverage:** Locked Decisions 1-12 → Tasks 1-2 (infra/journal), 5-10 (scenario, roles configured not hard-coded, canary, term selection, restart at exit 75), 3-4 (validated experiment block as sole activation switch), 11 (coordinator owns orchestration; ChannelRuntime keeps canonical state), 12 (exit codes / result line). Validation bullet list → Task 3 (each bullet has a named test) + Task 4 (parse strictness) + run-dir identity in Task 1's journal open. Phase table → Tasks 6-9 phase constants match all 15 spec names exactly. Restart Handshake (round boundary, operator gap rule) → Task 8 + out-of-scope note. Privacy Contract → Task 10 (deal-ID-text exemption included). Deterministic Minimal Turns → `_GateSeatPolicy` reuses `ScriptedPolicy` observe/skip/repair only. Fail-Closed Rules → `_fail` routes in Tasks 6-10; no-retry-on-payment preserved by leaving reconciliation to the channel runtime. Offline Verification's three test groups → `test_live_gate.py` (generic), `test_live_gate_channels.py` (scenario), `test_coordinator.py`/`test_arena_wiring.py`/`test_config.py`/`test_experiment.py` (isolation).

**Known judgment calls the executor should not "fix" silently:**
1. `minimum_captures` uses the deterministic-path arithmetic (`3 + UPFRONT_WITHIN + 2 + ON_DELIVERY_WITHIN + funding_turns` rounds x 3 seats). If the runtime's canonical deadline fields disagree with the schedule table during Task 9, reconcile the TABLE and this formula together in one commit, reading `deal.favor_due_turn`/`fund_by_turn` from the real runtime — never adjust the runtime.
2. The generic reducer forbids a second `restart_required` outright (Task 1) — slightly stronger than "second restart request is terminal failure", by design: the scenario's `_fail` path still records the terminal reason because `_request_restart` guards on `restart_armed` state before appending.
3. Gate policies carry the configured provider/model identity so transcripts record the validated path contract even though no model runs; summaries stay `ScriptedPolicy`-shaped. If riz prefers a distinct `driver` label for gate turns, that is a one-line `_transcript_driver` follow-up, not assumed here.

**Type consistency check:** `LiveGateOptions.roles` is `tuple[tuple[str, int], ...]` everywhere (config, experiment parser sorts before constructing, fingerprint converts to dict, journal normalizes identically via `_normalized_roles`). `ScenarioMeta.role_contracts` `(name, driver_kind)` pairs are consumed by config validation (Task 3) and provided by the scenario (Task 5) with identical spelling. Driver hook names (`attach`/`note_admission`/`after_seat_capture`/`pending_signal`/`result_summary`/`policy_for`) are identical across Tasks 6, 11, and 12 and in `FakeGateDriver`.
