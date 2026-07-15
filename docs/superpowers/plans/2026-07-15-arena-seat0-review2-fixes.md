# Arena Seat-0 Review-2 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the seven confirmed findings from the 2026-07-15 code review of the seat-0 piloting fix wave (`be30516..ac32ce1`): unbounded AI-processing drain, permanent DEGRADED wedge on turn regression, dead retry gate in `_mech_pass`, overloaded `idle_poll_limit` knob for human-pending, orphan sweep skipped during human-pending, duplicated one-shot repair block, and the record-skeleton interruption hole.

**Architecture:** All changes live in the arena layer: `seat0.py` (pure phase machine) gains regression detection with a new terminal `REGRESSED` phase; `coordinator.py` gains two dedicated drain budgets, a regression terminalizer, an interruption-safe record skeleton, and a shared repair helper; `config.py`/`experiment.py` gain two YAML-tunable poll-limit knobs. No new modules.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (in the `test` extra), dataclasses, asyncio.

## Global Constraints

- **Work in the branch worktree:** `/home/riz/dev/civ6-mcp/.claude/worktrees/arena-seat0-piloting` (branch `arena-seat0-piloting`, base commit `ac32ce1`). Never commit to `main`. Do not push anywhere.
- **Test command:** always `uv run --extra test pytest ...` (pytest-asyncio lives in the `test` extra; without it async tests fail collection en masse).
- **Full suite green after every task:** `uv run --extra test pytest tests/ -q` must report 1174+ passed before each commit.
- **Degrade, not abort:** a per-civ or per-turn failure must never kill the multi-civ run; escalation is `human_pending`, hard exits go through the `finally` handback.
- **The coordinator chooses nothing strategic:** blocker resolution stays inside the existing `MECHANICAL_BLOCKERS` / decision / hard authority boundary. No task here may add a strategic default.
- **Append-once transcript contract:** each admitted seat-0 turn produces exactly one terminal record (`advanced` / `human_pending` / `interrupted` / new: `regressed`); a written record is never rewritten or duplicated.
- **Lua context rule:** `execute_read` = GameCore, `execute_write` = InGame. No task changes any Lua.
- **New config knobs are YAML + dataclass only.** No new CLI flags (YAGNI — the smoke YAMLs and defaults cover the live gates). Do not edit `experiments/*.yaml` (defaults suffice).
- **Commit style:** `fix(arena): ...` / `feat(arena): ...` / `refactor(arena): ...`, lowercase imperative, matching branch history.
- **Task order matters:** Task 2 needs Task 1; Task 5 needs Task 4; Task 8 needs Task 7. Execute in numeric order.

---

## File Structure

| File | Role in this plan |
|---|---|
| `src/civ_mcp/arena/seat0.py` | Phase machine: regression detection (Task 1) |
| `src/civ_mcp/arena/coordinator.py` | Regression terminalizer (Task 2), retry gate (Task 3), drain budgets (Task 5), human-pending sweep (Task 6), record skeleton (Task 7), shared repair helper (Task 8) |
| `src/civ_mcp/arena/config.py` | Two new `ArenaConfig` fields (Task 4) |
| `src/civ_mcp/arena/experiment.py` | YAML plumbing for the new fields (Task 4) |
| `tests/arena/test_seat0.py` | Phase-machine unit tests (Task 1) |
| `tests/arena/test_coordinator.py` | Coordinator behavior tests (Tasks 2, 3, 5, 6, 7, 8) |
| `tests/arena/test_experiment.py` | YAML knob tests (Task 4) |

Existing test infrastructure you will reuse (all already defined in `tests/arena/test_coordinator.py`; do NOT redefine them):
- `Seat0Harness(monkeypatch, polls)` — patches `hook.poll/inject/finish_units/end_turn/restore_local/disable`, `seat0.query_blockers/save_recovery_anchor`, and `asyncio.sleep`; records an ordered `events` stream; **repeats the final poll forever** once the list is exhausted.
- `seat0_poll(turn, *, active=True)` — a `PuppetState` with `local=0, seat0_active=active`.
- `Seat0ScriptPolicy(harness, behaviors)` — each element of `behaviors` is a dict (returned) or a `BaseException` instance (raised); records `(player_id, turn, kwargs)` in `.calls`.
- `Seat0RecordingPolicy(harness)` — always-succeeding policy.
- `Seat0CapsConn` — `FakeConnWithOverview` subclass; dead when disconnected.
- `FakeConn`, `FakeGS`, `FakeGSWithConn(conn)`, `EventSink(harness)`, `_seat0_cfg(tmp_path, **kwargs)`, `_blocker(type, msg)`, `_returned(summary)`.

---

### Task 1: Regression detection in the seat-0 phase machine

The review found (`seat0.py:139`): a valid backward turn number (human loaded an older save mid-drain) returns `DEGRADED` forever — `self.turn` is never resynced, `reset()` (only legal from `ADVANCED`) can never run, and seat 0 is silently dead for the rest of the run. Fix: three **consecutive** valid backward polls are a genuine regression; report it as a new terminal poll outcome so the coordinator can terminalize and re-admit. Malformed polls (`turn < 0`) never count as regression evidence.

**Files:**
- Modify: `src/civ_mcp/arena/seat0.py` (Seat0Phase, Seat0Poll, module constants, `Seat0TurnState` fields, `observe()`, `reset()`)
- Test: `tests/arena/test_seat0.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (Task 2 relies on these exact names): `Seat0Phase.REGRESSED = "regressed"`, `Seat0Poll.REGRESSED = "regressed"`, `Seat0TurnState.regression_polls: int`, `_REGRESSION_POLL_LIMIT = 3`, `reset()` legal from `{ADVANCED, REGRESSED}`. `REGRESSED` is deliberately NOT in `needs_drain` — the coordinator handles it synchronously on the same poll.

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_seat0.py` (it already imports `Seat0Phase`, `Seat0Poll`, `Seat0TurnState` — check the import block at the top and add any missing name to the existing `from civ_mcp.arena.seat0 import ...` line):

```python
def test_backward_polls_below_limit_stay_degraded():
    state = Seat0TurnState()
    state.admit(7)
    state.mark_policy_played()
    assert state.observe(turn=5, seat0_active=False) == Seat0Poll.DEGRADED
    assert state.observe(turn=5, seat0_active=False) == Seat0Poll.DEGRADED
    assert state.phase is Seat0Phase.POLICY_PLAYED


def test_third_consecutive_backward_poll_reports_regressed():
    state = Seat0TurnState()
    state.admit(7)
    state.mark_policy_played()
    state.observe(turn=5, seat0_active=False)
    state.observe(turn=5, seat0_active=False)
    assert state.observe(turn=5, seat0_active=False) == Seat0Poll.REGRESSED
    assert state.phase is Seat0Phase.REGRESSED
    # REGRESSED is terminal: reset must be legal so the rolled-back turn
    # can be re-admitted.
    state.reset()
    assert state.phase is Seat0Phase.READY
    assert state.regression_polls == 0


def test_same_turn_poll_resets_regression_counter():
    state = Seat0TurnState()
    state.admit(7)
    state.mark_policy_played()
    state.observe(turn=5, seat0_active=False)
    state.observe(turn=5, seat0_active=False)
    assert state.observe(turn=7, seat0_active=False) == Seat0Poll.WAIT
    state.observe(turn=5, seat0_active=False)
    state.observe(turn=5, seat0_active=False)
    assert state.phase is not Seat0Phase.REGRESSED


def test_negative_turn_never_counts_toward_regression():
    state = Seat0TurnState()
    state.admit(7)
    state.mark_policy_played()
    for _ in range(5):
        assert state.observe(turn=-1, seat0_active=False) == Seat0Poll.DEGRADED
    assert state.phase is Seat0Phase.POLICY_PLAYED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_seat0.py -q -k regress`
Expected: FAIL — `AttributeError: REGRESSED` (enum member missing).

- [ ] **Step 3: Implement in `src/civ_mcp/arena/seat0.py`**

Add the enum members:

```python
class Seat0Phase(StrEnum):
    READY = "ready"
    POLICY_PLAYED = "policy_played"
    END_FIRED = "end_fired"
    AI_PROCESSING = "ai_processing"
    ADVANCED = "advanced"
    REGRESSED = "regressed"
    HUMAN_PENDING = "human_pending"
    INTERRUPTED = "interrupted"
```

```python
class Seat0Poll(StrEnum):
    """Poll outcome, distinct from `phase`: what the coordinator should do
    next, not what state seat 0 is in."""

    WAIT = "wait"
    RECHECK = "recheck"
    ADVANCED = "advanced"
    DEGRADED = "degraded"
    REGRESSED = "regressed"
```

Add the constant below `_MAX_END_TURN_REQUESTS`:

```python
# Consecutive VALID backward turn samples required before a rollback is
# declared. One sample may be a transient misread; a run of them means a
# human loaded an earlier save. Malformed polls (turn < 0) never count.
_REGRESSION_POLL_LIMIT = 3
```

Add the field to `Seat0TurnState` (after `resume_context`):

```python
    regression_polls: int = 0
```

Replace the top of `observe()` (currently `if self.turn is None or turn < 0 or turn < self.turn: return Seat0Poll.DEGRADED` followed by the `turn > self.turn` block) with:

```python
    def observe(self, *, turn: int, seat0_active: bool) -> Seat0Poll:
        """Advance the phase machine for one poll and report what the
        coordinator should do. A strictly newer turn is the authoritative
        advance signal; a PERSISTENTLY older valid turn is a rollback (a
        human loaded an earlier save) and terminalizes the turn as
        REGRESSED so the coordinator can re-admit at the older turn."""
        if self.turn is None or turn < 0:
            return Seat0Poll.DEGRADED
        if turn < self.turn:
            self.regression_polls += 1
            if self.regression_polls >= _REGRESSION_POLL_LIMIT:
                self.phase = Seat0Phase.REGRESSED
                return Seat0Poll.REGRESSED
            return Seat0Poll.DEGRADED
        self.regression_polls = 0
        if turn > self.turn:
            self.phase = Seat0Phase.ADVANCED
            return Seat0Poll.ADVANCED
```

(The `END_FIRED` grace block and the trailing `return Seat0Poll.WAIT` stay exactly as they are.)

Update `reset()` — the guard and the field zeroing:

```python
    def reset(self) -> None:
        """Return to READY for the next admission. Only valid once the
        phase has actually advanced or regressed -- resetting a live turn
        would let the coordinator silently drop an in-flight
        policy/repair/save attempt."""
        if self.phase not in (Seat0Phase.ADVANCED, Seat0Phase.REGRESSED):
            raise RuntimeError(
                "Seat0TurnState.reset() is only valid after the phase has "
                f"advanced or regressed; current phase is {self.phase!r}"
            )
        self.phase = Seat0Phase.READY
        self.turn = None
        self.repair_used = False
        self.end_turn_requests = 0
        self.grace_polls = 0
        self.critical_emitted = False
        self.record = None
        self.record_written = False
        self.resume_context = None
        self.regression_polls = 0
```

Do NOT add `REGRESSED` to `needs_drain` — add this comment above the `needs_drain` property body instead:

```python
        # REGRESSED is intentionally absent: observe() only runs while
        # needs_drain is true, and the coordinator terminalizes a REGRESSED
        # result synchronously on the same poll -- the phase never persists
        # across polls.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_seat0.py -q`
Expected: PASS (all, including the pre-existing degraded-poll tests).

Run: `uv run --extra test pytest tests/ -q`
Expected: 1174 + 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/seat0.py tests/arena/test_seat0.py
git commit -m "fix(arena): detect seat-zero turn regression in the phase machine"
```

---

### Task 2: Coordinator terminalizes a regressed turn and re-admits

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py` (new closure next to `_terminalize_seat0_advanced` at ~line 421; new `elif` in the poll dispatch at ~line 655)
- Test: `tests/arena/test_coordinator.py`

**Interfaces:**
- Consumes: `Seat0Poll.REGRESSED`, `Seat0Phase.REGRESSED`, `reset()`-from-REGRESSED (Task 1).
- Produces: transcript records may now carry `seat0.terminal_state == "regressed"` with `turn_kind == "failed"`; a structured log event `{"level": "CRITICAL", "event": "seat0_turn_regressed", "turn": <in-flight>, "observed_turn": <rolled-back>}`. `seat0_turns_failed` counts a regressed turn whose record it writes.

- [ ] **Step 1: Write the failing test**

Append to `tests/arena/test_coordinator.py` (near the other drain tests):

```python
@pytest.mark.asyncio
async def test_seat0_turn_regression_terminalizes_and_replays(monkeypatch, tmp_path):
    """A human loading an older save mid-drain: three consecutive backward
    polls terminalize the in-flight turn as `regressed` (turn_kind failed),
    and the rolled-back turn is re-admitted and replayed."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),    # admit turn 7
        seat0_poll(7, active=False),   # AI processing after the end request
        seat0_poll(5, active=False),   # regression sample 1 -> DEGRADED
        seat0_poll(5, active=False),   # regression sample 2 -> DEGRADED
        seat0_poll(5, active=False),   # regression sample 3 -> REGRESSED
        seat0_poll(5, active=True),    # re-admit the rolled-back turn 5
        seat0_poll(5, active=False),   # AI processing
        seat0_poll(6, active=True),    # turn 5 advanced
    ])
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("turn 7"), _returned("turn 5")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-regressed", max_puppet_turns=2)

    result = await run_arena(
        conn, FakeGSWithConn(conn), cfg, policy=pol, transcript=sink
    )

    assert [call[:2] for call in pol.calls] == [(0, 7), (0, 5)]
    assert result["seat0_turns_failed"] == 1
    assert result["seat0_turns_played"] == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "regressed"
    assert sink.records[0]["turn_kind"] == "failed"
    assert sink.records[1]["seat0"]["terminal_state"] == "advanced"
    events = [e for e in result["log"] if e.get("event") == "seat0_turn_regressed"]
    assert len(events) == 1
    assert events[0]["turn"] == 7
    assert events[0]["observed_turn"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py::test_seat0_turn_regression_terminalizes_and_replays -q`
Expected: FAIL — without the coordinator `elif`, the REGRESSED poll falls through to the idle branch, seat 0 is never re-admitted, and the run exits with `pol.calls == [(0, 7)]` and no regressed record. (It must not hang: the phase machine left `needs_drain` false, so the idle budget drains normally.)

- [ ] **Step 3: Implement in `src/civ_mcp/arena/coordinator.py`**

Add a closure directly below `_terminalize_seat0_advanced` (same 8-space indent):

```python
        def _terminalize_seat0_regressed(*, observed_turn: int) -> None:
            """The game state rolled back under an in-flight seat-0 turn (a
            human loaded an earlier save): write the pending record exactly
            once with terminal `regressed`, count it as failed (its outcome
            no longer exists in the timeline), emit one CRITICAL event, and
            reset for re-admission at the rolled-back turn. A record already
            written (human_pending) is never rewritten -- only the reset and
            the CRITICAL apply."""
            nonlocal seat0_failed
            regressed_from = seat0_state.turn
            if seat0_state.record is not None and not seat0_state.record_written:
                seat0_state.record["turn_kind"] = "failed"
                seat0_state.record["seat0"]["terminal_state"] = "regressed"
                seat0_state.record["seat0"]["end_turn_requests"] = (
                    seat0_state.end_turn_requests
                )
                if _tx_on:
                    transcript.write(seat0_state.record)
                seat0_state.record_written = True
                seat0_failed += 1
            log.append({
                "level": "CRITICAL",
                "event": "seat0_turn_regressed",
                "turn": regressed_from,
                "observed_turn": observed_turn,
            })
            print(
                f"[arena] CRITICAL seat0_turn_regressed: in-flight turn "
                f"{regressed_from} rolled back to {observed_turn}; the turn "
                f"will be re-piloted when seat 0 polls active",
                file=sys.stderr,
            )
            seat0_state.reset()
```

In the poll dispatch (~line 655), add an `elif` between the ADVANCED and RECHECK arms:

```python
                if poll_action is Seat0Poll.ADVANCED:
                    _terminalize_seat0_advanced()
                    # Fall through: this same poll may re-admit the next turn.
                elif poll_action is Seat0Poll.REGRESSED:
                    _terminalize_seat0_regressed(observed_turn=st.turn)
                    # Fall through: seat 0 re-admits at the rolled-back turn
                    # once it polls active again.
                elif poll_action is Seat0Poll.RECHECK:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py -q -k "regress or degraded"`
Expected: PASS — both the new test and the pre-existing `test_seat0_degraded_and_backward_polls_do_not_terminalize_or_replay` (which feeds only ONE backward poll, below the threshold).

Run: `uv run --extra test pytest tests/ -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py
git commit -m "fix(arena): terminalize and replay regressed seat-zero turns"
```

---

### Task 3: `_mech_pass` retries only after a live reconnect

The review found (`coordinator.py:505`): `if attempt == 1 and await _reconnect_with_retry(conn): continue` is dead code — the `for` loop advances to attempt 2 whether or not the reconnect succeeded, so a confirmed-dead connection gets a second doomed query whose error then masks the original in `errors[-1]`.

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py` (`_mech_pass`, ~lines 493–516)
- Test: `tests/arena/test_coordinator.py`

**Interfaces:**
- Consumes: `_reconnect_with_retry(conn) -> bool` (module-level, unchanged), `_mech_pass_once`, `seat0.automation_failure_blocker`.
- Produces: unchanged 5-tuple return `(blockers, cleanup_records, snapshots, groups, errors)`; on a dead reconnect, `errors` now has exactly one entry (the original failure).

- [ ] **Step 1: Write the failing test**

Append to `tests/arena/test_coordinator.py`, next to `test_seat0_permanent_mechanical_failure_records_human_pending`:

```python
@pytest.mark.asyncio
async def test_seat0_mech_pass_does_not_retry_on_dead_reconnect(
    monkeypatch, tmp_path
):
    """When the reconnect after a failed blocker query cannot restore the
    tuner, the second query attempt must not run -- it is guaranteed to fail
    and would bury the original error in the automation blocker."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    calls = 0

    async def broken_query(_conn):
        nonlocal calls
        calls += 1
        raise ConnectionError("blocker query unavailable")

    monkeypatch.setattr(seat0_mod, "query_blockers", broken_query)

    class DeadReconnectConn(Seat0CapsConn):
        def __init__(self):
            super().__init__()
            self.connect_attempts = 0

        async def connect(self):
            self.connect_attempts += 1
            raise ConnectionError("tuner gone")

    conn = DeadReconnectConn()
    sink = EventSink(harness)
    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _seat0_cfg(tmp_path, run_id="seat0-mech-dead", idle_poll_limit=3),
        policy=Seat0ScriptPolicy(harness, [_returned("normal ok")]),
        transcript=sink,
    )

    assert calls == 1                      # no second doomed attempt
    # >= 5: the mech-pass reconnect makes 5 attempts; the finally block's
    # reclaim-retry step adds 5 more on the same dead conn.
    assert conn.connect_attempts >= 5
    assert result["seat0_human_pending"] == 1
    errors = sink.records[0]["seat0"]["automation_errors"]
    assert len(errors) == 1
    assert "blocker query unavailable" in errors[0]["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py::test_seat0_mech_pass_does_not_retry_on_dead_reconnect -q`
Expected: FAIL — `assert calls == 1` sees `calls == 2` (the dead-code gate ran attempt 2 anyway), and `len(errors) == 2`.

- [ ] **Step 3: Fix the loop in `_mech_pass`**

Replace the `except` arm's last two lines:

```python
                except Exception as exc:
                    errors.append({
                        "stage": prefix,
                        "attempt": attempt,
                        "error": repr(exc),
                    })
                    # Retry only when the reconnect actually restored the
                    # tuner -- a second attempt against a connection that
                    # just failed to reconnect is guaranteed to fail and
                    # would bury the original error. No reconnect after the
                    # final attempt: the human-pending path and the finally
                    # block do their own reclaim.
                    if attempt == 2 or not await _reconnect_with_retry(conn):
                        break
```

(Everything else in `_mech_pass` — the automation blocker built from `errors[-1]` and the 5-tuple return — stays as is; with the break, `errors[-1]` is now the correct, original failure when the reconnect died.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py -q -k "mech"`
Expected: PASS — the new test, plus the two existing containment tests (`test_seat0_transient_mechanical_failure_reconnects_and_continues` still sees 2 query calls because its reconnect *succeeds*; `test_seat0_permanent_mechanical_failure_records_human_pending` still sees 2 errors for the same reason).

Run: `uv run --extra test pytest tests/ -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py
git commit -m "fix(arena): retry the seat-zero mech pass only after a live reconnect"
```

---

### Task 4: Config knobs — `seat0_drain_poll_limit` and `seat0_human_pending_poll_limit`

Two new positive-int knobs so Task 5 can bound the AI-processing drain and the human-pending window separately from `idle_poll_limit` (the review's finding: one knob was carrying three meanings). Defaults 1800 (~30 min at 1 poll/s).

**Files:**
- Modify: `src/civ_mcp/arena/config.py` (ArenaConfig, 2 fields)
- Modify: `src/civ_mcp/arena/experiment.py` (`_TOP_KEYS` at line 40; the `ArenaConfig(...)` construction in `load_experiment`, next to the existing `idle_poll_limit=_top_int(...)` block at ~line 381)
- Test: `tests/arena/test_experiment.py`

**Interfaces:**
- Consumes: `_top_int(path, field, value) -> int` (existing; rejects None/bool/non-Integral/`<= 0`).
- Produces (Task 5 relies on these exact names): `ArenaConfig.seat0_drain_poll_limit: int = 1800`, `ArenaConfig.seat0_human_pending_poll_limit: int = 1800`; both parseable as top-level YAML keys.

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_experiment.py`:

```python
def test_seat0_poll_limit_knobs_default_and_parse(tmp_path):
    cfg = load_experiment(_write(tmp_path, GOOD))
    assert cfg.seat0_drain_poll_limit == 1800
    assert cfg.seat0_human_pending_poll_limit == 1800

    text = GOOD.replace(
        "idle_poll_limit: 3600",
        "idle_poll_limit: 3600\n"
        "seat0_drain_poll_limit: 900\n"
        "seat0_human_pending_poll_limit: 1200",
    )
    cfg = load_experiment(_write(tmp_path, text))
    assert cfg.seat0_drain_poll_limit == 900
    assert cfg.seat0_human_pending_poll_limit == 1200


@pytest.mark.parametrize("bad", ["0", "-5", "true", '"x"'])
@pytest.mark.parametrize(
    "field", ["seat0_drain_poll_limit", "seat0_human_pending_poll_limit"]
)
def test_seat0_poll_limit_knobs_reject_non_positive(tmp_path, field, bad):
    text = GOOD.replace(
        "idle_poll_limit: 3600",
        f"idle_poll_limit: 3600\n{field}: {bad}",
    )
    with pytest.raises(ValueError):
        load_experiment(_write(tmp_path, text))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_experiment.py -q -k "poll_limit_knobs"`
Expected: FAIL — `AttributeError: 'ArenaConfig' object has no attribute 'seat0_drain_poll_limit'` on the first test; the reject tests fail because the unknown top-level key raises a DIFFERENT ValueError message (unknown key) — that's fine, they go green once the key is known and `_top_int` does the rejecting.

- [ ] **Step 3: Implement**

In `src/civ_mcp/arena/config.py`, add to `ArenaConfig` after `idle_poll_limit: int = 600`:

```python
    # Task 5 drain budgets, distinct from idle_poll_limit ("consecutive polls
    # with nothing to do"). drain: total quiet polls allowed for one admitted
    # seat-0 turn's end-fired/AI-processing drain before the run declares the
    # game hung and exits. human_pending: polls allowed for a human to resolve
    # an escalated blocker before the run exits cleanly.
    seat0_drain_poll_limit: int = 1800
    seat0_human_pending_poll_limit: int = 1800
```

In `src/civ_mcp/arena/experiment.py`, extend `_TOP_KEYS`:

```python
_TOP_KEYS = {
    "run_id", "max_puppet_turns", "idle_poll_limit", "gateway_url",
    "max_game_turns", "seat0_drain_poll_limit",
    "seat0_human_pending_poll_limit", "civs",
}
```

and in the `ArenaConfig(...)` construction, directly after the `idle_poll_limit=_top_int(...)` entry:

```python
        seat0_drain_poll_limit=_top_int(
            config_path,
            "seat0_drain_poll_limit",
            data.get("seat0_drain_poll_limit", arena_defaults.seat0_drain_poll_limit),
        ),
        seat0_human_pending_poll_limit=_top_int(
            config_path,
            "seat0_human_pending_poll_limit",
            data.get(
                "seat0_human_pending_poll_limit",
                arena_defaults.seat0_human_pending_poll_limit,
            ),
        ),
```

(`arena.py`'s explicit `ArenaConfig(...)` constructions pick up the dataclass defaults automatically — no change there, and no CLI flags per Global Constraints.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_experiment.py -q`
Expected: PASS.

Run: `uv run --extra test pytest tests/ -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/config.py src/civ_mcp/arena/experiment.py tests/arena/test_experiment.py
git commit -m "feat(arena): add seat-zero drain and human-pending poll limits"
```

---

### Task 5: Bound the drain with the dedicated budgets

The review's two most severe findings live in one branch (`coordinator.py:1459-1472`):
1. AI_PROCESSING/END_FIRED waits decrement nothing → a hung AI turn spins `run_arena` forever (no finally, no handback).
2. HUMAN_PENDING decrements `deadline_polls` (refilled with `idle_poll_limit` on entry) → the human's decision window is bound to the wrong knob and its expiry has no explanatory CRITICAL.

Fix: two per-turn counters checked in the drain-wait branch; on expiry emit a CRITICAL and `break` (the `finally` then terminalizes an unwritten record as `interrupted` and hands back). `DEGRADED` keeps decrementing `deadline_polls` (transient-noise budget). The `idle_poll_limit` refill in `_seat0_enter_human_pending` is removed — `deadline_polls` goes back to meaning exactly one thing.

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py` (counter init at ~line 400; reset at admission ~line 686; `_seat0_enter_human_pending` ~line 444; drain-wait branch ~line 1459)
- Test: `tests/arena/test_coordinator.py` (one rewritten test, one new test)

**Interfaces:**
- Consumes: `config.seat0_drain_poll_limit`, `config.seat0_human_pending_poll_limit` (Task 4).
- Produces: structured log events `{"level": "CRITICAL", "event": "seat0_drain_deadline", "turn", "phase", "polls"}` and `{"level": "CRITICAL", "event": "seat0_human_pending_deadline", "turn", "polls"}`. Task 6 inserts the orphan sweep into the HUMAN_PENDING arm this task creates.

- [ ] **Step 1: Rewrite the human-pending exit test and add the hung-AI test**

In `tests/arena/test_coordinator.py`, REPLACE the entire body of `test_seat0_human_pending_exits_after_idle_poll_limit` (line ~3103) with:

```python
@pytest.mark.asyncio
async def test_seat0_human_pending_exits_after_human_pending_poll_limit(
    monkeypatch, tmp_path
):
    """If the human never advances, the drain exits once the DEDICATED
    human-pending budget is spent -- idle_poll_limit no longer bounds a
    human's decision window -- and a CRITICAL names the deadline."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    hard = _blocker("UNKNOWN", "??")
    harness.blocker_queue = [[hard], [hard]]
    conn = FakeConn()
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(
        tmp_path, run_id="seat0-idle", idle_poll_limit=5,
        seat0_human_pending_poll_limit=3,
    )

    result = await asyncio.wait_for(
        run_arena(conn, FakeGS(), cfg, policy=pol), timeout=5.0
    )

    # Entered human_pending and then quietly drained without further work.
    assert result["seat0_human_pending"] == 1
    assert harness.names().count("policy") == 1
    assert harness.names().count("end_turn") == 0
    assert harness.names().count("sleep") == cfg.seat0_human_pending_poll_limit
    # Human never advanced -> the pending turn is still counted exactly once.
    assert result["seat0_turns_played"] == 0
    events = [
        e for e in result["log"]
        if e.get("event") == "seat0_human_pending_deadline"
    ]
    assert len(events) == 1
    assert events[0]["turn"] == 7
```

Then append the new hung-AI test right after it:

```python
@pytest.mark.asyncio
async def test_seat0_hung_ai_drain_exits_after_drain_poll_limit(
    monkeypatch, tmp_path
):
    """A hung AI turn (the turn number never advances) must not spin the
    arena forever: the drain cap breaks the loop, the finally terminalizes
    the record as `interrupted`, and a CRITICAL names the deadline."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),   # harness repeats this forever
    ])
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(
        tmp_path, run_id="seat0-hung-ai", seat0_drain_poll_limit=4
    )

    result = await asyncio.wait_for(
        run_arena(conn, FakeGSWithConn(conn), cfg, policy=pol, transcript=sink),
        timeout=5.0,
    )

    assert result["seat0_turns_played"] == 0
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "interrupted"
    events = [e for e in result["log"] if e.get("event") == "seat0_drain_deadline"]
    assert len(events) == 1
    assert events[0]["turn"] == 7
```

(`asyncio.wait_for` is the hang guard: `Seat0Harness` patches `asyncio.sleep` to an instant no-op that still yields, so an unbounded loop spins fast but interruptibly, and the failing run dies with `TimeoutError` after 5 real seconds instead of hanging pytest. `wait_for` itself uses the loop timer, not `asyncio.sleep`, so the patch doesn't disarm it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py -q -k "human_pending_poll_limit or hung_ai"`
Expected: FAIL — the rewritten test runs (the config knob exists since Task 4) but sees a `sleep` count of `idle_poll_limit` (5) instead of 3 and no `seat0_human_pending_deadline` event; the hung-AI test dies with `asyncio.TimeoutError` because the current code has NO decrement at all for AI-processing waits.

- [ ] **Step 3: Implement in `src/civ_mcp/arena/coordinator.py`**

**(a)** At the counter init (line ~400), after `deadline_polls = config.idle_poll_limit  # ...`:

```python
        # Per-turn drain budgets (Task 5 knobs), distinct from deadline_polls:
        # drain_polls counts quiet end-fired/AI-processing waits for the
        # CURRENT admitted seat-0 turn; human_polls counts human-pending waits.
        # Both reset at admission.
        drain_polls = 0
        human_polls = 0
```

**(b)** At admission (~line 686), extend:

```python
                is_seat0 = local_seat0
                if is_seat0:
                    seat0_state.admit(st.turn)
                    drain_polls = 0
                    human_polls = 0
```

**(c)** In `_seat0_enter_human_pending` (~line 444), delete the refill — change:

```python
            nonlocal seat0_pending, seat0_failed, deadline_polls
            seat0_state.mark_human_pending()
            deadline_polls = config.idle_poll_limit
```

to:

```python
            nonlocal seat0_pending, seat0_failed
            seat0_state.mark_human_pending()
```

(Rationale: HUMAN_PENDING no longer consumes `deadline_polls` at all — see (d) — so the refill that protected the window is obsolete, and `deadline_polls` regains its single meaning.)

**(d)** Replace the drain-wait branch (~line 1459):

```python
                if seat0_state.needs_drain:
                    # An in-flight seat-0 turn is draining (end request fired /
                    # AI processing / human pending): quiet GameCore-only
                    # polling until the turn number flips. Each wait charges
                    # the budget matching WHY we are waiting -- never the
                    # puppet-era idle budget.
                    await asyncio.sleep(1.0)
                    if seat0_state.phase is Seat0Phase.HUMAN_PENDING:
                        human_polls += 1
                        if human_polls >= config.seat0_human_pending_poll_limit:
                            log.append({
                                "level": "CRITICAL",
                                "event": "seat0_human_pending_deadline",
                                "turn": seat0_state.turn,
                                "polls": human_polls,
                            })
                            print(
                                f"[arena] CRITICAL seat0_human_pending_deadline: "
                                f"turn {seat0_state.turn} unresolved after "
                                f"{human_polls} polls; ending the run",
                                file=sys.stderr,
                            )
                            break
                    elif poll_action is Seat0Poll.DEGRADED:
                        deadline_polls -= 1
                    else:
                        drain_polls += 1
                        if drain_polls >= config.seat0_drain_poll_limit:
                            log.append({
                                "level": "CRITICAL",
                                "event": "seat0_drain_deadline",
                                "turn": seat0_state.turn,
                                "phase": str(seat0_state.phase),
                                "polls": drain_polls,
                            })
                            print(
                                f"[arena] CRITICAL seat0_drain_deadline: turn "
                                f"{seat0_state.turn} stuck in {seat0_state.phase} "
                                f"after {drain_polls} polls; game presumed hung",
                                file=sys.stderr,
                            )
                            break
                    continue
```

(The `break` exits the while loop; the return dict — which holds a reference to `log`, so the CRITICAL is included — is built, and the `finally` terminalizes an unwritten record as `interrupted` and restores the human. A human-pending record is already written and is never rewritten.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py -q`
Expected: PASS. Watch these specifically: `test_seat0_ai_processing_outlives_idle_poll_limit` (7 waits ≪ default 1800 — still green), `test_seat0_permanent_mechanical_failure_records_human_pending` and the recheck tests (their human-pending drains now spin up to 1800 instant fake sleeps before exiting — still fast, still green), and the Task 3 dead-reconnect test.

Run: `uv run --extra test pytest tests/ -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py
git commit -m "fix(arena): bound seat-zero drains with dedicated poll budgets"
```

---

### Task 6: Orphan-diplomacy sweep during human-pending

The review found (`coordinator.py:1459`): the drain-wait `continue` skips `idle_streak`/`_sweep_orphan_sessions` entirely, and HUMAN_PENDING — a human-idle window by construction — is exactly where an orphan puppet-to-puppet session (unclickable by the human, wedges the AI phase) needs the sweep that main shipped at `7875728`. AI-processing phases correctly stay sweep-free (no InGame calls mid-AI — pinned by `test_seat0_ai_phase_issues_no_execute_write`).

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py` (HUMAN_PENDING arm from Task 5)
- Test: `tests/arena/test_coordinator.py`

**Interfaces:**
- Consumes: `_sweep_orphan_sessions(conn) -> str` and `ORPHAN_SWEEP_IDLE_POLLS = 45` (module-level, both monkeypatchable at `coordinator` module scope), `idle_streak` (existing loop local).
- Produces: no new names; the sweep now also fires every `ORPHAN_SWEEP_IDLE_POLLS` polls inside the human-pending window.

- [ ] **Step 1: Write the failing test**

First add the module import at the top of `tests/arena/test_coordinator.py` alongside the existing `from civ_mcp.arena import hook as hook_mod` line:

```python
from civ_mcp.arena import coordinator as coordinator_mod
```

Then append:

```python
@pytest.mark.asyncio
async def test_seat0_human_pending_drain_runs_orphan_sweep(
    monkeypatch, tmp_path
):
    """HUMAN_PENDING is a human-idle window: the orphan diplomacy sweep must
    keep firing on its usual idle cadence while the arena waits (an orphan
    puppet-to-puppet session can never be clicked by the human)."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    hard = _blocker("UNKNOWN", "??")
    harness.blocker_queue = [[hard], [hard]]
    sweeps = []

    async def fake_sweep(_conn):
        sweeps.append(True)
        return "ORPHANS|none"

    monkeypatch.setattr(coordinator_mod, "_sweep_orphan_sessions", fake_sweep)
    monkeypatch.setattr(coordinator_mod, "ORPHAN_SWEEP_IDLE_POLLS", 2)
    conn = FakeConn()
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(
        tmp_path, run_id="seat0-hp-sweep", idle_poll_limit=5,
        seat0_human_pending_poll_limit=6,
    )

    result = await asyncio.wait_for(
        run_arena(conn, FakeGS(), cfg, policy=pol), timeout=5.0
    )

    assert result["seat0_human_pending"] == 1
    assert len(sweeps) == 3   # human-pending polls 2, 4, and 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py::test_seat0_human_pending_drain_runs_orphan_sweep -q`
Expected: FAIL — `len(sweeps) == 0` (the drain-wait branch never reaches the sweep).

- [ ] **Step 3: Implement**

In the HUMAN_PENDING arm Task 5 created, insert the idle bookkeeping BEFORE the deadline check (so the poll that expires the budget still sweeps first):

```python
                    if seat0_state.phase is Seat0Phase.HUMAN_PENDING:
                        # A human-idle window: keep the orphan-session sweep
                        # cadence alive. Sessions involving the local player
                        # are skipped by the sweep by construction, so this
                        # never touches a leader scene the human is using.
                        idle_streak += 1
                        if idle_streak % ORPHAN_SWEEP_IDLE_POLLS == 0:
                            swept_sessions = await _sweep_orphan_sessions(conn)
                            if swept_sessions not in ("ORPHANS|none", "?", "err"):
                                print(f"[arena] orphan diplomacy sessions closed "
                                      f"after {idle_streak} idle polls: "
                                      f"{swept_sessions}", file=sys.stderr)
                                log.append({
                                    "turn": st.turn,
                                    "orphan_sweep": swept_sessions,
                                })
                        human_polls += 1
                        if human_polls >= config.seat0_human_pending_poll_limit:
```

(Rest of the arm unchanged. AI-processing waits — the `else` arm — deliberately remain sweep-free: seat 0 is not local there and InGame calls mid-AI are illegal, which `test_seat0_ai_phase_issues_no_execute_write` pins.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py -q -k "sweep or human_pending or ai_phase"`
Expected: PASS — including `test_seat0_ai_phase_issues_no_execute_write` (sweep only fires in the HUMAN_PENDING arm) and `test_seat0_human_pending_exits_after_human_pending_poll_limit` (3 polls < default `ORPHAN_SWEEP_IDLE_POLLS` 45, so no sweep, sleep count unchanged).

Run: `uv run --extra test pytest tests/ -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py
git commit -m "fix(arena): sweep orphan diplomacy during seat-zero human-pending"
```

---

### Task 7: Interruption-safe record skeleton

The review found (`coordinator.py:1041`, also the fable review's top deferred minor): `seat0_state.record` is first assigned only after the mechanical pass, so a `BaseException` (Ctrl-C / CancelledError) during the policy call, the mech pass, or the recovery save reaches the `finally` with `record is None` — the interrupted-record write is skipped and the turn (with its token spend) vanishes from the transcript. Fix: assign a skeleton record at the top of the seat-0 branch, before the first await of the logical turn; the played/human-pending paths overwrite it with the fully-built record exactly as today.

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py` (top of the `if is_seat0:` branch, ~line 990, right after the AUTHORITY FLOW comment and before the normal-attempt `try`)
- Test: `tests/arena/test_coordinator.py`

**Interfaces:**
- Consumes: `run_id`, `st.turn`, `state_before`, `pol` (all in scope at the insertion point); `datetime`/`timezone` (already imported).
- Produces (Task 8 relies on this): `seat0_state.record` is non-None — with a fully-keyed `record["seat0"]["repair"]` sub-dict — for the ENTIRE logical seat-0 turn, from before the normal policy call onward.

- [ ] **Step 1: Write the failing tests**

Append to `tests/arena/test_coordinator.py`:

```python
@pytest.mark.asyncio
async def test_seat0_cancel_during_policy_call_writes_interrupted_record(
    monkeypatch, tmp_path
):
    """A cancellation during the (potentially very long) seat-0 policy call
    must still leave an `interrupted` transcript record -- the record
    skeleton exists before the first await of the logical turn."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await run_arena(
            conn, FakeGSWithConn(conn),
            _seat0_cfg(tmp_path, run_id="seat0-cancel-policy"),
            policy=pol, transcript=sink,
        )

    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec["player_id"] == 0
    assert rec["turn"] == 7
    assert rec["turn_kind"] == "failed"
    assert rec["seat0"]["terminal_state"] == "interrupted"


@pytest.mark.asyncio
async def test_seat0_cancel_during_mech_pass_writes_interrupted_record(
    monkeypatch, tmp_path
):
    """Same guarantee one await later: a cancellation inside the mechanical
    pass (blocker query) may not skip the interrupted record."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])

    async def cancelled_query(_conn):
        raise asyncio.CancelledError()

    monkeypatch.setattr(seat0_mod, "query_blockers", cancelled_query)
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])

    with pytest.raises(asyncio.CancelledError):
        await run_arena(
            conn, FakeGSWithConn(conn),
            _seat0_cfg(tmp_path, run_id="seat0-cancel-mech"),
            policy=pol, transcript=sink,
        )

    assert len(sink.records) == 1
    assert sink.records[0]["turn"] == 7
    assert sink.records[0]["seat0"]["terminal_state"] == "interrupted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py -q -k "cancel_during"`
Expected: FAIL — `len(sink.records) == 0` in both (the CancelledError propagates with `record is None`; the finally skips the write but still re-raises).

- [ ] **Step 3: Implement**

At the top of the `if is_seat0:` branch, immediately after the `# ===== SEAT-0 AUTHORITY FLOW ...` comment block and BEFORE the `normal_result = None` line, insert:

```python
                    # Interruption-safe record skeleton: assigned BEFORE the
                    # first await of the logical turn so a BaseException at
                    # ANY point (the long CLI policy call, the mechanical
                    # pass, the recovery save) leaves a record the finally
                    # block can terminalize as `interrupted`. The played and
                    # human_pending paths overwrite this with the fully-built
                    # record; the skeleton is only ever written on interrupt.
                    seat0_state.record = {
                        "schema_version": 1,
                        "run_id":   run_id,
                        "ts":       datetime.now(timezone.utc).isoformat(),
                        "player_id": 0,
                        "turn":     st.turn,
                        "provider": getattr(pol, "provider", "local"),
                        "model":    getattr(pol, "model", ""),
                        "driver":   "cli" if str(getattr(pol, "provider", "local")).startswith("cli") else "in_process",
                        "steps": [],
                        "step_count": 0,
                        "usd": 0.0,
                        "state_before": state_before,
                        "state_after": None,
                        "state_delta": None,
                        "turn_kind": "failed",
                        "seat0": {
                            "normal": {"completed": False, "summary": "", "error": ""},
                            "repair": {"attempted": False, "completed": False,
                                       "summary": "", "error": ""},
                            "blocker_snapshots": [],
                            "mechanical_cleanup": [],
                            "automation_errors": [],
                            "end_turn_errors": [],
                            "autosave": {"name": "", "attempts": []},
                            "end_turn_requests": 0,
                            "terminal_state": "",
                        },
                    }
```

No other change: the played path's later `seat0_state.record = _base_seat0_record(...)` and the human-pending path's `_seat0_enter_human_pending(record=..., ...)` replace the skeleton wholesale, so terminal records are byte-identical to today's.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py -q`
Expected: PASS — the two new tests plus every existing terminal-record and append-once test unchanged.

Run: `uv run --extra test pytest tests/ -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py
git commit -m "fix(arena): build the seat-zero record skeleton before first await"
```

---

### Task 8: Extract the shared one-shot repair helper

The review found (`coordinator.py:1056` vs `:578`): the played branch and the RECHECK path carry byte-similar ~40-line copies of the repair sequence (build block → mark used → kwargs gate → exclusive disconnect → call → error capture → reclaim → guarded after-repair mech pass), and the fix wave had to patch both copies in parallel. Extract one closure. Bonus behavior (test-first below): the helper mutates the record's `seat0.repair` sub-dict in place, so a cancellation mid-repair now leaves `attempted: true` in the interrupted record — the repair charge was genuinely spent.

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py` (new closure after `_fire_seat0_end` ~line 522; rewrite the repair block inside `_recheck_cleanup_repair_or_refire` ~lines 571–614 and inside the played branch ~lines 1049–1096)
- Test: `tests/arena/test_coordinator.py`

**Interfaces:**
- Consumes: `seat0_state.record["seat0"]["repair"]` always present (Task 7); `_repair_kwargs`, `_mech_pass`, `_reconnect_with_retry`, `seat0.build_blocker_block`.
- Produces: `async def _attempt_seat0_repair(pol, repair, after_blockers, *, prior_error, caps_kwarg, exclusive, turn) -> (repair_result: dict | None, mech: tuple | None)` — `repair` is mutated in place (`attempted`/`completed`/`summary`/`error`); `mech` is the `_mech_pass("after_repair")` 5-tuple when the repair returned, else `None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/arena/test_coordinator.py`:

```python
@pytest.mark.asyncio
async def test_seat0_cancel_during_repair_marks_attempted_in_interrupted_record(
    monkeypatch, tmp_path
):
    """The one-shot repair mutates the record's repair sub-dict in place, so
    a cancellation mid-repair still shows attempted=true in the interrupted
    record -- the single repair charge was genuinely spent."""
    research = _blocker("ENDTURN_BLOCKING_RESEARCH", "Choose research")
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    # Decision blocker survives the mechanical pass -> need_repair fires.
    harness.blocker_queue = [[research], [research]]
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(
        harness, [_returned("normal ok"), asyncio.CancelledError()]
    )

    with pytest.raises(asyncio.CancelledError):
        await run_arena(
            conn, FakeGSWithConn(conn),
            _seat0_cfg(tmp_path, run_id="seat0-cancel-repair"),
            policy=pol, transcript=sink,
        )

    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec["seat0"]["terminal_state"] == "interrupted"
    assert rec["seat0"]["repair"]["attempted"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py::test_seat0_cancel_during_repair_marks_attempted_in_interrupted_record -q`
Expected: FAIL — `rec["seat0"]["repair"]["attempted"] is False` (the current played branch tracks `repair_attempted` in a LOCAL, never touching the skeleton).

- [ ] **Step 3: Implement**

**(a)** Add the closure directly below `_fire_seat0_end` (8-space indent):

```python
        async def _attempt_seat0_repair(
            pol, repair, after_blockers, *, prior_error, caps_kwarg,
            exclusive, turn,
        ):
            """One-shot focused repair shared by the played branch and the
            RECHECK path. Mutates `repair` (the record's seat0.repair
            sub-dict) in place so an interruption mid-repair still leaves
            attempted=True in the terminal record. Returns
            (repair_result, mech) where mech is the _mech_pass("after_repair")
            5-tuple when the repair returned, else None."""
            blocker_block = seat0.build_blocker_block(
                after_blockers, prior_error=prior_error
            )
            # Set BEFORE awaiting so a cancellation/exception can never
            # permit a second repair.
            seat0_state.repair_used = True
            repair_kwargs = _repair_kwargs(pol, blocker_block, caps_kwarg)
            if repair_kwargs is None:
                repair["error"] = (
                    "policy does not accept required blocker_block keyword"
                )
                return None, None
            repair["attempted"] = True
            if exclusive and conn.is_connected:
                await conn.disconnect()   # repair owns the tuner
            repair_result = None
            try:
                repair_result = await pol(gs, 0, turn, **repair_kwargs)
                repair["completed"] = True
                repair["summary"] = (repair_result or {}).get("summary", "")
            except Exception as e:
                repair["error"] = repr(e)
                print(f"[arena] seat-0 turn {turn} repair failed: {e!r}",
                      file=sys.stderr)
                log.append({"turn": turn, "player_id": 0,
                            "skipped": True, "repair_error": repair["error"]})
            # Reclaim the tuner regardless of outcome: the post-repair pass
            # and the human-pending drain both need a live connection.
            if exclusive and not conn.is_connected:
                await _reconnect_with_retry(conn)
            if repair["error"] == "":
                return repair_result, await _mech_pass("after_repair")
            return repair_result, None
```

**(b)** In `_recheck_cleanup_repair_or_refire`, replace everything from `blocker_block = seat0.build_blocker_block(after_blockers)` through the end of the `if repair_kwargs is not None and repair_error == "":` block with:

```python
                repair_result, repair_mech = await _attempt_seat0_repair(
                    pol, s0["repair"], after_blockers,
                    prior_error="", caps_kwarg=caps_kwarg,
                    exclusive=exclusive, turn=turn,
                )
                repair_error = s0["repair"]["error"]
                if repair_mech is not None:
                    (
                        after_blockers,
                        rep_cleanup,
                        rep_snaps,
                        groups,
                        pass_errors,
                    ) = repair_mech
                    s0["automation_errors"] = s0["automation_errors"] + pass_errors
                    s0["blocker_snapshots"] = s0["blocker_snapshots"] + rep_snaps
                    s0["mechanical_cleanup"] = s0["mechanical_cleanup"] + rep_cleanup
```

(The `repair_error = ""` initializer above the `if` and the surrounding condition stay. `prior_error=""` is identical to the old no-argument `build_blocker_block(after_blockers)` — the block only renders a prior-error line when non-empty. The recheck's stderr line loses the word "recheck"; nothing asserts on it.)

**(c)** In the played branch, replace the entire `if need_repair:` block — from the `if need_repair:` line itself through the `blocker_snapshots = blocker_snapshots + repair_snaps` line — with:

```python
                    if need_repair:
                        s0_repair = seat0_state.record["seat0"]["repair"]
                        repair_result, repair_mech = await _attempt_seat0_repair(
                            pol, s0_repair, after_blockers,
                            prior_error=normal_error_msg,
                            caps_kwarg=caps_kwarg, exclusive=exclusive,
                            turn=st.turn,
                        )
                        repair_attempted = s0_repair["attempted"]
                        repair_error = s0_repair["error"]
                        if repair_mech is not None:
                            (
                                after_blockers,
                                repair_cleanup,
                                repair_snaps,
                                groups,
                                pass_errors,
                            ) = repair_mech
                            automation_errors.extend(pass_errors)
                            cleanup_records = cleanup_records + repair_cleanup
                            blocker_snapshots = blocker_snapshots + repair_snaps
```

(`_base_seat0_record` still builds the terminal record's `repair` sub-dict from the locals `repair_attempted`/`repair_result`/`repair_error` — those are equal to the mutated skeleton values, so terminal records are unchanged; the in-place mutation only matters for the interrupted path.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/arena/test_coordinator.py -q`
Expected: PASS — the new test, plus the existing coverage over both call sites: `test_seat0_decision_blocker_triggers_one_repair`, the recheck re-fire tests, and BOTH "required blocker_block keyword" incompatible-policy tests (played and recheck paths).

Run: `uv run --extra test pytest tests/ -q && uv run ruff check src/civ_mcp/arena/`
Expected: all tests passed; ruff reports no new findings.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py
git commit -m "refactor(arena): share the one-shot seat-zero repair helper"
```

---

## Findings-to-tasks map (spec coverage)

| Review finding | Task(s) |
|---|---|
| 1. Unbounded AI_PROCESSING drain (`coordinator.py:1467`) | 4, 5 |
| 2. Backward turn wedges phase machine (`seat0.py:139`) | 1, 2 |
| 3. Dead `_mech_pass` retry gate (`coordinator.py:505`) | 3 |
| 4. `idle_poll_limit` overloaded for human-pending (`coordinator.py:449`) | 4, 5 |
| 5. Orphan sweep skipped during human-pending (`coordinator.py:1459`) | 6 |
| 6. Duplicated one-shot repair block (`coordinator.py:1056`) | 8 |
| 7. Record built only after the mech pass (`coordinator.py:1041`) | 7 |
