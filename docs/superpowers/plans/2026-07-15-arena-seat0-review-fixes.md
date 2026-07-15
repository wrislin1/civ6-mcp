# Arena Seat-0 Review Fixes Implementation Plan

> **Status:** ✓ DONE — executed 2026-07-15 (review-1 hardening wave, 10 commits
> `be30516..ac32ce1`, 1174 tests green); merged to main at `845ae09`. Do not
> re-execute. Follow-up findings were fixed by
> `2026-07-15-arena-seat0-review2-fixes.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden autonomous seat-0 piloting against degraded polls, long AI drains, mechanical-operation failures, malformed transcript snapshots, and failed-turn metric skew without transferring strategic authority from the policy to the coordinator.

**Architecture:** Keep the approved split: `hook.py` owns narrow FireTuner primitives, `seat0.py` owns the phase machine and mechanical completion rules, and `coordinator.py` owns admission plus orchestration. Make invalid poll samples explicit, keep valid AI processing outside the human-idle deadline, consolidate shared calculations, and convert ordinary seat-0 completion failures into bounded diagnostics plus human handoff instead of aborting healthy puppet work. Replace the untyped recheck dictionary with a typed resume context, but defer a wholesale `run_arena` decomposition until after live gates so the stabilization patch does not mix a large structural rewrite with unprobed behavior.

**Tech Stack:** Python 3.12, asyncio, dataclasses and `StrEnum`, pytest/pytest-asyncio, FireTuner GameCore/InGame Lua, existing arena JSONL transcripts.

**Source branch:** `arena-seat0-piloting` at reviewed commit `be30516`.

**Design authority:** `docs/superpowers/specs/2026-07-14-arena-seat0-piloting-design.md` and `docs/superpowers/plans/2026-07-14-arena-seat0-piloting.md`.

## Global Constraints

- Execute in the existing isolated worktree `/home/riz/dev/civ6-mcp/.claude/worktrees/arena-seat0-piloting`; do not merge or push without riz's explicit direction.
- Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before each completion claim.
- Before any live game or FireTuner operation, read and follow `civ6-arena-live`.
- Run tests with `uv run --extra test pytest tests/ -q`; always scope collection to `tests/`.
- Preserve unrelated workspace state and never stage `.serena/memories/`.
- The policy retains all strategic decision authority. Coordinator and seat-0 helpers may not select research, civics, production, promotions, policies, governors, beliefs, envoys, dedications, World Congress votes, city-capture outcomes, stacked-unit moves, spy escape routes, or any other strategic option.
- The human remains a passive observer during normal operation and intervenes only after an explicit hard block reaches `human_pending`.
- `end_turn` remains unavailable to every policy. Only `hook.end_turn()` may dispatch seat 0's InGame end request.
- GameCore / `execute_read` owns hook inject, disable, poll, finish-units, restore-local, and every valid `AI_PROCESSING` drain poll. InGame / `execute_write` owns blocker queries/cleanup, recovery saves, and `hook.end_turn`, only while seat 0 is known local and active.
- A normal policy call and its optional focused repair are one logical turn and consume one shared budget charge.
- Admission budgets never abort a valid in-flight seat-0 drain. `idle_poll_limit` continues to bound `HUMAN_PENDING` and persistently degraded poll samples; it does not bound valid `AI_PROCESSING`.
- A seat-0 transcript record remains append-only and is written once at `advanced`, `human_pending`, or `interrupted`.
- Catch ordinary `Exception` for per-turn degradation; never catch or suppress `BaseException` cancellation.
- Use concise commit subjects such as `fix(arena): ignore degraded seat-zero polls` and run the task's focused tests before each commit.

## Review Disposition

The supplied review repeats four findings. The ten distinct findings resolve as follows:

| Finding | Disposition | Plan coverage |
|---|---|---|
| Any turn-number change terminalizes seat 0 | Accepted. A malformed or backward sample must not advance, consume grace, or permit readmission. | Task 1 |
| Unguarded `Players[0]:IsTurnActive()` aborts the whole poll | Accepted. Wrap only the new engine call and keep the established GameCore calls unchanged. | Task 1 |
| Drain suppresses the orphan-session sweep | Rejected. `AI_PROCESSING` is GameCore-only by approved design, and `HUMAN_PENDING` is an intentional human decision wait rather than the orphan-wedge signature. The existing sweep skips local-player sessions and remains in the ordinary human-idle path. | Preserved by Task 2 tests |
| Every drain poll burns `deadline_polls` | Partly accepted. Valid `AI_PROCESSING` must drain until advance or cancellation; `HUMAN_PENDING` deliberately exits after `idle_poll_limit`. | Task 2 |
| Mechanical pass, save, and end request all abort without a record | Partly accepted. `save_recovery_anchor()` already catches ordinary failures, and end-request failures occur after record construction. Mechanical-pass failures before record construction are real; end-request exceptions also need containment so the run can keep polling. | Task 7 |
| `_state_delta` has three inconsistent copies | Accepted. Route slept, puppet, and seat-0 records through the guarded helper. | Task 4 |
| `finish_units(0)` is issued twice for a units blocker | Accepted. Query first and let `apply_mechanical_cleanup()` own the single units call. | Task 3 |
| `run_arena` needs a per-turn object | Partly accepted. Remove the unsafe untyped `seat0_ctx` and silent fallback now with `Seat0ResumeContext`. Defer full orchestration extraction until live correctness is established. | Task 6 |
| Failed turns remain in the attention skip-rate denominator | Accepted. Keep `captured` as the total diagnostic count, but compute skip rate from played plus slept turns only. | Task 5 |
| Repair bypasses the optional-kwarg signature gate | Accepted with a different resolution. Silently omitting `blocker_block` would invoke an unfocused second normal turn; instead, signature-check it, skip the repair call, and enter an explicit hard handoff. | Task 6 |

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/civ_mcp/arena/hook.py` | Modify | Guard the seat-0 engine probe without losing core poll output. |
| `src/civ_mcp/arena/seat0.py` | Modify | Reject degraded/regressed turn samples and hold typed recheck context. |
| `src/civ_mcp/arena/coordinator.py` | Modify | Drain semantics, one-pass cleanup, shared deltas, repair gating, and operational failure containment. |
| `src/civ_mcp/arena/analyze.py` | Modify | Exclude failed turns from the skip-rate denominator. |
| `tests/arena/test_hook.py` | Modify | Lua guard regression. |
| `tests/arena/test_seat0.py` | Modify | Malformed/backward phase-machine regressions and resume-context reset. |
| `tests/arena/test_coordinator.py` | Modify | No replay, long AI drain, bounded human wait, one units call, malformed snapshot, repair compatibility, and tuner failure containment. |
| `tests/arena/test_analyze.py` | Modify | Mixed played/slept/failed attention denominator. |

---

### Task 1: Make Poll Degradation Non-Terminal

**Files:**
- Modify: `src/civ_mcp/arena/hook.py:34-41`
- Modify: `src/civ_mcp/arena/seat0.py:39-145`
- Modify: `src/civ_mcp/arena/coordinator.py:604-650`
- Test: `tests/arena/test_hook.py`
- Test: `tests/arena/test_seat0.py`
- Test: `tests/arena/test_coordinator.py`

**Interfaces:**
- Consumes: existing `PuppetState.turn == -1` malformed-poll sentinel and admitted `Seat0TurnState.turn`.
- Produces: `Seat0Poll.DEGRADED`, returned without changing phase or grace counters when the observed turn is negative or lower than the admitted turn.

- [ ] **Step 1: Write the Lua and phase-machine regressions**

Add to `tests/arena/test_hook.py`:

```python
def test_poll_lua_guards_seat0_activity_probe():
    assert "local seat0OK, seat0Active = pcall(function()" in POLL_LUA
    assert "Players[0] ~= nil and Players[0]:IsTurnActive()" in POLL_LUA
    assert "tostring((seat0OK and seat0Active) or false)" in POLL_LUA
    assert POLL_LUA.index("pcall(function()") < POLL_LUA.index('print("LOCAL|"')
```

Add to `tests/arena/test_seat0.py`:

```python
@pytest.mark.parametrize("observed_turn", [-1, 6])
def test_state_ignores_malformed_or_backward_turn(observed_turn):
    state = state_after_one_end_request(turn=7)

    assert state.observe(
        turn=observed_turn, seat0_active=False
    ) == Seat0Poll.DEGRADED
    assert state.phase is Seat0Phase.END_FIRED
    assert state.grace_polls == 0
    assert state.end_turn_requests == 1


def test_state_advances_only_on_strictly_newer_turn_after_degraded_sample():
    state = state_after_one_end_request(turn=7)
    assert state.observe(turn=-1, seat0_active=False) == Seat0Poll.DEGRADED
    assert state.observe(turn=7, seat0_active=False) == Seat0Poll.WAIT
    assert state.phase is Seat0Phase.AI_PROCESSING
    assert state.observe(turn=8, seat0_active=True) == Seat0Poll.ADVANCED
```

- [ ] **Step 2: Write the coordinator no-replay regression**

Add to `tests/arena/test_coordinator.py` beside the seat-0 duplicate-play tests:

```python
@pytest.mark.asyncio
async def test_seat0_degraded_and_backward_polls_do_not_terminalize_or_replay(
    monkeypatch, tmp_path
):
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        PuppetState(local=-1, turn=-1, active=False, last=None, seat0_active=False),
        seat0_poll(6, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(tmp_path, run_id="seat0-degraded-poll")

    result = await run_arena(
        conn, FakeGSWithConn(conn), cfg, policy=pol, transcript=sink
    )

    assert [call[:2] for call in pol.calls] == [(0, 7)]
    assert result["seat0_turns_played"] == 1
    assert len(sink.records) == 1
    assert sink.records[0]["turn"] == 7
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
```

- [ ] **Step 3: Run the focused tests and confirm RED**

Run:

```bash
uv run --extra test pytest tests/arena/test_hook.py tests/arena/test_seat0.py tests/arena/test_coordinator.py -q
```

Expected: the Lua guard assertion fails, `Seat0Poll.DEGRADED` is absent, and the coordinator regression records a false advance or replay.

- [ ] **Step 4: Guard the Lua call and make turn comparison monotonic**

Replace the start of `POLL_LUA` in `src/civ_mcp/arena/hook.py` with:

```python
POLL_LUA = """
local seat0OK, seat0Active = pcall(function()
  return Players[0] ~= nil and Players[0]:IsTurnActive()
end)
print("LOCAL|" .. tostring(Game.GetLocalPlayer()))
print("TURN|" .. tostring(Game.GetCurrentGameTurn()))
print("ACTIVE|" .. tostring(__pt_active))
print("LAST|" .. tostring(__pt_last))
print("SEAT0_ACTIVE|" .. tostring((seat0OK and seat0Active) or false))
print("---END---")
"""
```

Extend `Seat0Poll` and replace the first branch of `Seat0TurnState.observe()`:

```python
class Seat0Poll(StrEnum):
    WAIT = "wait"
    RECHECK = "recheck"
    ADVANCED = "advanced"
    DEGRADED = "degraded"


def observe(self, *, turn: int, seat0_active: bool) -> Seat0Poll:
    if self.turn is None or turn < 0 or turn < self.turn:
        return Seat0Poll.DEGRADED
    if turn > self.turn:
        self.phase = Seat0Phase.ADVANCED
        return Seat0Poll.ADVANCED

    if self.phase is Seat0Phase.END_FIRED:
        if not seat0_active:
            self.phase = Seat0Phase.AI_PROCESSING
            return Seat0Poll.WAIT
        self.grace_polls += 1
        if self.grace_polls > _GRACE_POLL_LIMIT:
            return Seat0Poll.RECHECK
    return Seat0Poll.WAIT
```

Require a nonnegative turn for either new admission in `run_arena`:

```python
captured_puppet = (
    st.turn >= 0
    and st.active
    and st.local in puppet_ids
    and admission_open()
)
local_seat0 = (
    st.turn >= 0
    and seat0_spec is not None
    and st.local == 0
    and st.seat0_active
    and seat0_state.can_admit(turn=st.turn, seat0_active=True)
    and admission_open()
)
```

- [ ] **Step 5: Run the focused tests and commit**

Run the Step 3 command. Expected: PASS.

```bash
git add src/civ_mcp/arena/hook.py src/civ_mcp/arena/seat0.py src/civ_mcp/arena/coordinator.py tests/arena/test_hook.py tests/arena/test_seat0.py tests/arena/test_coordinator.py
git commit -m "fix(arena): ignore degraded seat-zero polls"
```

---

### Task 2: Separate AI Drain From Human and Degraded Deadlines

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py:604-670,1375-1405`
- Test: `tests/arena/test_coordinator.py`
- Test: `tests/arena/test_orphan_sweep.py`

**Interfaces:**
- Consumes: `Seat0Phase.AI_PROCESSING`, `Seat0Phase.HUMAN_PENDING`, and `Seat0Poll.DEGRADED` from Task 1.
- Produces: phase-aware deadline charging; no new InGame call is reachable from `AI_PROCESSING`.

- [ ] **Step 1: Write the long-AI-drain regression**

Add to `tests/arena/test_coordinator.py`:

```python
@pytest.mark.asyncio
async def test_seat0_ai_processing_outlives_idle_poll_limit(monkeypatch, tmp_path):
    harness = Seat0Harness(
        monkeypatch,
        [seat0_poll(7, active=True)]
        + [seat0_poll(7, active=False)] * 7
        + [seat0_poll(8, active=True)],
    )
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(
        tmp_path, run_id="seat0-long-ai", idle_poll_limit=3
    )

    result = await run_arena(
        conn, FakeGSWithConn(conn), cfg, policy=pol, transcript=sink
    )

    assert result["seat0_turns_played"] == 1
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
```

In `test_seat0_human_pending_exits_after_idle_poll_limit`, replace the loose sleep assertion with:

```python
assert harness.names().count("sleep") == cfg.idle_poll_limit
```

Together the tests pin the required split and give a newly entered hard block its full configured observation window.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
uv run --extra test pytest tests/arena/test_coordinator.py tests/arena/test_orphan_sweep.py -q
```

Expected: the long-AI test exits before turn 8 and records `interrupted`; the existing human-pending and orphan-idle tests remain green.

- [ ] **Step 3: Charge only bounded wait kinds**

Replace the drain-wait branch in `run_arena` with:

```python
if seat0_state.needs_drain:
    await asyncio.sleep(1.0)
    if (
        seat0_state.phase is Seat0Phase.HUMAN_PENDING
        or poll_action is Seat0Poll.DEGRADED
    ):
        deadline_polls -= 1
continue
```

In `_seat0_enter_human_pending`, add `deadline_polls` to the `nonlocal` declaration and reset it when the phase first enters the hard-block wait:

```python
nonlocal seat0_pending, seat0_failed, deadline_polls
seat0_state.mark_human_pending()
deadline_polls = config.idle_poll_limit
```

Do not increment `idle_streak` and do not call `_sweep_orphan_sessions()` in this branch. Leave the existing human-idle branch and its unconditional bottom-of-loop `deadline_polls -= 1` unchanged.

- [ ] **Step 4: Run the focused tests and commit**

Run the Step 2 command. Expected: PASS, including `_StrictAIWriteConn.ai_phase_writes == []` and all orphan-sweep tests.

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py tests/arena/test_orphan_sweep.py
git commit -m "fix(arena): drain valid seat-zero AI phases fully"
```

---

### Task 3: Finish Seat-0 Units Once Per Mechanical Pass

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py:462-480`
- Test: `tests/arena/test_coordinator.py`

**Interfaces:**
- Consumes: `seat0.query_blockers()` and `seat0.apply_mechanical_cleanup()`, whose units branch remains authoritative.
- Produces: one `hook.finish_units(conn, 0)` call only when the queried blocker snapshot contains `ENDTURN_BLOCKING_UNITS`.

- [ ] **Step 1: Write the one-call regression**

Add beside the coordinator seat-0 blocker tests:

```python
@pytest.mark.asyncio
async def test_seat0_units_blocker_finishes_once_per_mechanical_pass(
    monkeypatch, tmp_path
):
    units = _blocker("ENDTURN_BLOCKING_UNITS", "Units need orders")
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    harness.blocker_queue = [[units], []]
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])

    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _seat0_cfg(tmp_path, run_id="seat0-one-finish"),
        policy=pol,
        transcript=sink,
    )

    assert result["seat0_turns_played"] == 1
    assert harness.names().count("finish_units") == 1
    assert sink.records[0]["seat0"]["mechanical_cleanup"] == [{
        "type": "ENDTURN_BLOCKING_UNITS",
        "action": "finish_units",
        "result": "requested",
    }]
```

In `test_seat0_happy_path_single_play_then_terminal_advanced`, replace the current finish-order assertions with:

```python
i_policy = events.index(("policy", 0, 7))
i_blockers = names.index("query_blockers")
i_anchor = events.index(("save_anchor", 7))
i_end = names.index("end_turn")
assert i_policy < i_blockers < i_anchor < i_end
assert "finish_units" not in names[i_policy:i_end]
assert names.count("end_turn") == 1
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
uv run --extra test pytest tests/arena/test_coordinator.py -q
```

Expected: the units-blocker test observes two `finish_units` calls.

- [ ] **Step 3: Query before cleanup**

Remove the unconditional first line from `_mech_pass` and retain the existing cleanup helper:

```python
async def _mech_pass(prefix):
    first = await seat0.query_blockers(conn)
    snaps = [{"stage": prefix, "blockers": first}]
    records: list = []
    if first:
        records = await seat0.apply_mechanical_cleanup(conn, first)
        after = await seat0.query_blockers(conn)
        snaps.append({"stage": prefix + "_cleanup", "blockers": after})
    else:
        after = first
    return after, records, snaps, seat0.classify_blockers(after)
```

Do not remove `ENDTURN_BLOCKING_UNITS` from `MECHANICAL_BLOCKERS` or from `apply_mechanical_cleanup()`.

- [ ] **Step 4: Run the focused test and commit**

Run the Step 2 command. Expected: PASS.

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py
git commit -m "fix(arena): finish seat-zero units once per pass"
```

---

### Task 4: Use One Guarded State-Delta Contract

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py:61-78,785-805,1328-1340`
- Test: `tests/arena/test_coordinator.py`

**Interfaces:**
- Consumes: `_state_delta(state_before: dict | None, state_after: dict | None) -> dict | None`.
- Produces: slept, puppet-played, and seat-0 records all degrade missing or wrong-typed snapshot keys to `state_delta=None`.

- [ ] **Step 1: Write the puppet malformed-snapshot regression**

Add to `tests/arena/test_coordinator.py`:

```python
@pytest.mark.asyncio
async def test_puppet_partial_post_snapshot_state_delta_none(
    monkeypatch, tmp_path
):
    from civ_mcp.arena import coordinator as coord

    before = dict(_ATTN_BASELINE_SNAPSHOT)
    after = dict(_ATTN_BASELINE_SNAPSHOT)
    del after["units"]
    snapshots = iter([before, after])

    async def fake_snapshot(_gs):
        return next(snapshots)

    monkeypatch.setattr(coord, "_overview_snapshot", fake_snapshot)
    conn = AttnConn()
    sink = FakeSink()
    opts = CivOptions()
    cfg = ArenaConfig(
        players=[PlayerSpec(1, "local", "m", options=opts)],
        max_puppet_turns=1,
        idle_poll_limit=5,
        transcript_dir=str(tmp_path),
        run_id="puppet-partial-after",
        puppet_ids=[1],
    )

    result = await run_arena(
        conn, FakeGSWithConn(conn), cfg, policy=CountingPolicy(opts), transcript=sink
    )

    assert result["puppet_turns_played"] == 1
    assert sink.records[-1]["state_delta"] is None
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
uv run --extra test pytest tests/arena/test_coordinator.py -q
```

Expected: `KeyError: 'units'` escapes from the puppet transcript path.

- [ ] **Step 3: Replace both inline calculations**

In the slept record path, replace the local numeric-field block and its `try/except` with:

```python
state_delta = _state_delta(prev_snapshot, state_before)
```

In the puppet played-record path, replace the local `_num` calculation with:

```python
state_delta = _state_delta(state_before, state_after)
```

Keep the existing seat-0 call unchanged:

```python
"state_delta": _state_delta(state_before, state_after),
```

Update `_state_delta`'s docstring to state that it is the only transcript delta contract for all three paths.

- [ ] **Step 4: Run the focused test and commit**

Run the Step 2 command. Expected: PASS, including the existing slept partial-snapshot regression.

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py
git commit -m "fix(arena): share guarded transcript state deltas"
```

---

### Task 5: Exclude Failed Turns From Attention Skip Rate

**Files:**
- Modify: `src/civ_mcp/arena/analyze.py:527-542`
- Test: `tests/arena/test_analyze.py`

**Interfaces:**
- Consumes: `_turn_kind()` values `played`, `slept`, and `failed`.
- Produces: `captured` remains all records; `skip_rate = slept_turns / (slept_turns + model_turns)`.

- [ ] **Step 1: Extend the failed-turn attention regression**

Replace `test_attention_metrics_failed_turn_excluded_from_baselines` with:

```python
def test_attention_metrics_failed_turn_excluded_from_baselines_and_skip_rate():
    recs = [
        _played(1, usd=0.02),
        {"player_id": 1, "turn": 2, "turn_kind": "failed", "usd": 999.0},
        _slept(3),
        _played(4, "STREAK_CAP", usd=0.02),
    ]
    m = attention_metrics(recs)[1]

    assert m["captured"] == 4
    assert m["slept_turns"] == 1
    assert m["model_turns"] == 2
    assert m["skip_rate"] == pytest.approx(1 / 3)
    assert m["savings"]["est_usd"] == pytest.approx(0.02)
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
uv run --extra test pytest tests/arena/test_analyze.py -q
```

Expected: `skip_rate` is `0.25` instead of one third.

- [ ] **Step 3: Use the eligible-turn denominator**

Replace the calculation in `attention_metrics()` with:

```python
captured = len(recs)
slept_turns = sum(1 for r in recs if _turn_kind(r) == "slept")
model_turns = sum(1 for r in recs if _turn_kind(r) == "played")
attention_turns = slept_turns + model_turns
skip_rate = slept_turns / attention_turns if attention_turns else 0.0
```

Do not change `captured`, streak splitting, savings baselines, or failed-turn series visibility.

- [ ] **Step 4: Run the focused test and commit**

Run the Step 2 command. Expected: PASS.

```bash
git add src/civ_mcp/arena/analyze.py tests/arena/test_analyze.py
git commit -m "fix(arena): exclude failed turns from skip rate"
```

---

### Task 6: Gate Focused Repair and Type the Resume Context

**Files:**
- Modify: `src/civ_mcp/arena/seat0.py:47-165`
- Modify: `src/civ_mcp/arena/coordinator.py:333-346,451-535,978-1005,1145-1175`
- Test: `tests/arena/test_seat0.py`
- Test: `tests/arena/test_coordinator.py`

**Interfaces:**
- Produces: immutable `Seat0ResumeContext(policy: object, caps: dict | None, exclusive: bool)` stored on `Seat0TurnState.resume_context`.
- Produces: `_repair_kwargs(policy, blocker_block, caps) -> dict | None`; `None` means the policy cannot receive the required focused context and must not be invoked again.

- [ ] **Step 1: Write resume-context lifecycle coverage**

Add to `tests/arena/test_seat0.py`:

```python
def test_reset_clears_typed_resume_context():
    state = state_after_one_end_request(turn=7)
    state.resume_context = Seat0ResumeContext(
        policy=object(), caps={"government": True}, exclusive=True
    )
    state.observe(turn=8, seat0_active=True)

    state.reset()

    assert state.resume_context is None
```

Import `Seat0ResumeContext` with the other seat-0 symbols.

- [ ] **Step 2: Write the legacy-policy repair regression**

Add to `tests/arena/test_coordinator.py`:

```python
@pytest.mark.asyncio
async def test_seat0_policy_without_blocker_kwarg_is_not_called_unfocused(
    monkeypatch, tmp_path
):
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    harness.blocker_queue = [[_RESEARCH], [_RESEARCH]]
    sink = EventSink(harness)

    class LegacyPolicy:
        provider = "local"
        model = "legacy"
        options = CivOptions()

        def __init__(self):
            self.calls = 0

        async def __call__(self, gs, player_id, turn):
            self.calls += 1
            return {"summary": "normal returned", "actions": []}

    pol = LegacyPolicy()
    result = await run_arena(
        FakeConn(),
        FakeGS(),
        _seat0_cfg(tmp_path, run_id="seat0-legacy-repair", idle_poll_limit=3),
        policy=pol,
        transcript=sink,
    )

    assert pol.calls == 1
    assert result["seat0_human_pending"] == 1
    assert sink.records[0]["seat0"]["repair"]["attempted"] is False
    assert "required blocker_block keyword" in sink.records[0]["seat0"]["repair"]["error"]
```

- [ ] **Step 3: Run the focused tests and confirm RED**

Run:

```bash
uv run --extra test pytest tests/arena/test_seat0.py tests/arena/test_coordinator.py -q
```

Expected: `Seat0ResumeContext` is absent and the legacy policy's second invocation reaches the caught `TypeError` path.

- [ ] **Step 4: Add typed context to the phase state**

In `src/civ_mcp/arena/seat0.py`, add:

```python
@dataclass(frozen=True)
class Seat0ResumeContext:
    policy: object
    caps: dict | None
    exclusive: bool
```

Add this field to `Seat0TurnState`:

```python
resume_context: Seat0ResumeContext | None = None
```

Clear it in `reset()`:

```python
self.resume_context = None
```

Replace `seat0_ctx` in the coordinator. When the initial played record is ready:

```python
seat0_state.resume_context = seat0.Seat0ResumeContext(
    policy=pol,
    caps=caps_kwarg,
    exclusive=exclusive,
)
```

At recheck entry, require the context rather than silently deriving defaults:

```python
ctx = seat0_state.resume_context
if ctx is None:
    raise RuntimeError("seat-0 recheck missing resume context")
pol = ctx.policy
caps_kwarg = ctx.caps
exclusive = ctx.exclusive
```

- [ ] **Step 5: Add the required-kwarg gate at both repair sites**

Add beside `_policy_accepts_kwarg`:

```python
def _repair_kwargs(policy, blocker_block: str, caps: dict | None) -> dict | None:
    if not _policy_accepts_kwarg(policy, "blocker_block"):
        return None
    kwargs = {"blocker_block": blocker_block}
    if caps is not None and _policy_accepts_kwarg(policy, "caps"):
        kwargs["caps"] = caps
    return kwargs
```

At the normal-path repair site, set the one-shot latch before branching:

```python
seat0_state.repair_used = True
repair_kwargs = _repair_kwargs(pol, blocker_block, caps_kwarg)
if repair_kwargs is None:
    repair_error = "policy does not accept required blocker_block keyword"
else:
    repair_attempted = True
    if exclusive and conn.is_connected:
        await conn.disconnect()
    try:
        repair_result = await pol(gs, 0, st.turn, **repair_kwargs)
    except Exception as e:
        repair_error = repr(e)
```

Retain the existing reconnect and successful post-repair mechanical-pass logic after this block. Replace the recheck repair invocation with:

```python
seat0_state.repair_used = True
repair_kwargs = _repair_kwargs(pol, blocker_block, caps_kwarg)
if repair_kwargs is None:
    repair_error = "policy does not accept required blocker_block keyword"
    s0["repair"]["error"] = repair_error
else:
    s0["repair"]["attempted"] = True
    if exclusive and conn.is_connected:
        await conn.disconnect()
    try:
        repair_result = await pol(gs, 0, turn, **repair_kwargs)
        s0["repair"]["completed"] = True
        s0["repair"]["summary"] = (repair_result or {}).get("summary", "")
    except Exception as e:
        repair_error = repr(e)
        s0["repair"]["error"] = repair_error
```

Run the existing tuner reclaim after either branch. Run the post-repair mechanical pass only when `repair_kwargs is not None and repair_error == ""`; otherwise leave the decision blocker untouched for `human_pending`.

- [ ] **Step 6: Run the focused tests and commit**

Run the Step 3 command. Expected: PASS, including existing one-repair, exclusive-tuner, and cancellation tests.

```bash
git add src/civ_mcp/arena/seat0.py src/civ_mcp/arena/coordinator.py tests/arena/test_seat0.py tests/arena/test_coordinator.py
git commit -m "fix(arena): gate and type seat-zero repair context"
```

---

### Task 7: Contain Mechanical and End-Request Failures

**Files:**
- Modify: `src/civ_mcp/arena/seat0.py:180-235`
- Modify: `src/civ_mcp/arena/coordinator.py:451-590,1060-1175`
- Test: `tests/arena/test_coordinator.py`
- Test: `tests/arena/test_seat0.py`

**Interfaces:**
- Produces: `seat0.automation_failure_blocker(stage: str, error: str) -> dict` with an unmapped hard-block type.
- Produces: seat-0 transcript arrays `automation_errors` and `end_turn_errors`.
- Preserves: `save_recovery_anchor()` remains best-effort and never raises an ordinary exception.

- [ ] **Step 1: Write a transient mechanical-failure regression**

Add to `tests/arena/test_coordinator.py`:

```python
@pytest.mark.asyncio
async def test_seat0_transient_mechanical_failure_reconnects_and_continues(
    monkeypatch, tmp_path
):
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    calls = 0

    async def flaky_query(_conn):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("transient blocker query")
        return []

    monkeypatch.setattr(seat0_mod, "query_blockers", flaky_query)
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])

    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _seat0_cfg(tmp_path, run_id="seat0-mech-retry"),
        policy=pol,
        transcript=sink,
    )

    assert calls == 2
    assert result["seat0_turns_played"] == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    assert "transient blocker query" in sink.records[0]["seat0"]["automation_errors"][0]["error"]
```

- [ ] **Step 2: Write permanent failure and uncertain end-request regressions**

Add:

```python
@pytest.mark.asyncio
async def test_seat0_permanent_mechanical_failure_records_human_pending(
    monkeypatch, tmp_path
):
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])

    async def broken_query(_conn):
        raise ConnectionError("blocker query unavailable")

    monkeypatch.setattr(seat0_mod, "query_blockers", broken_query)
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _seat0_cfg(tmp_path, run_id="seat0-mech-hard", idle_poll_limit=3),
        policy=Seat0ScriptPolicy(harness, [_returned("normal ok")]),
        transcript=sink,
    )

    assert result["seat0_human_pending"] == 1
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "human_pending"
    assert len(sink.records[0]["seat0"]["automation_errors"]) == 2
    assert "end_turn" not in harness.names()


@pytest.mark.asyncio
async def test_seat0_end_request_exception_keeps_polling_to_advance(
    monkeypatch, tmp_path
):
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(8, active=True),
    ])

    async def uncertain_end(_conn):
        harness.events.append(("end_turn",))
        raise ConnectionError("response lost after dispatch")

    monkeypatch.setattr(hook_mod, "end_turn", uncertain_end)
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _seat0_cfg(tmp_path, run_id="seat0-end-uncertain"),
        policy=Seat0ScriptPolicy(harness, [_returned("normal ok")]),
        transcript=sink,
    )

    assert result["seat0_turns_played"] == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    assert "response lost after dispatch" in sink.records[0]["seat0"]["end_turn_errors"][0]["error"]
```

- [ ] **Step 3: Run the focused tests and confirm RED**

Run:

```bash
uv run --extra test pytest tests/arena/test_seat0.py tests/arena/test_coordinator.py -q
```

Expected: each ordinary connection exception escapes `run_arena`; the permanent case has no seat-0 record.

- [ ] **Step 4: Add an explicit automation hard blocker**

In `src/civ_mcp/arena/seat0.py`, add:

```python
AUTOMATION_FAILURE_TYPE = "ARENA_SEAT0_AUTOMATION_FAILURE"


def automation_failure_blocker(stage: str, error: str) -> dict:
    return {
        "type": AUTOMATION_FAILURE_TYPE,
        "message": f"Seat-0 automation failed during {stage}: {error}",
    }
```

Because this type is absent from `lq.BLOCKING_TOOL_MAP`, `classify_blockers()` already places it in `hard`; add a direct unit assertion to `tests/arena/test_seat0.py`.

```python
def test_automation_failure_blocker_is_hard():
    blocker = automation_failure_blocker(
        "after_normal", "ConnectionError('blocker query unavailable')"
    )

    groups = classify_blockers([blocker])

    assert groups.mechanical == []
    assert groups.decision == []
    assert groups.hard == [blocker]
```

Import `automation_failure_blocker` with the other seat-0 symbols.

- [ ] **Step 5: Make the mechanical pass retry once and return diagnostics**

Split the current helper into `_mech_pass_once` and a bounded wrapper:

```python
async def _mech_pass_once(prefix):
    first = await seat0.query_blockers(conn)
    snaps = [{"stage": prefix, "blockers": first}]
    records: list = []
    if first:
        records = await seat0.apply_mechanical_cleanup(conn, first)
        after = await seat0.query_blockers(conn)
        snaps.append({"stage": prefix + "_cleanup", "blockers": after})
    else:
        after = first
    return after, records, snaps, seat0.classify_blockers(after)


async def _mech_pass(prefix):
    errors: list[dict] = []
    for attempt in (1, 2):
        try:
            result = await _mech_pass_once(prefix)
            return (*result, errors)
        except Exception as exc:
            errors.append({
                "stage": prefix,
                "attempt": attempt,
                "error": repr(exc),
            })
            if attempt == 1 and await _reconnect_with_retry(conn):
                continue

    blocker = seat0.automation_failure_blocker(prefix, errors[-1]["error"])
    blockers = [blocker]
    snapshots = [{"stage": prefix + "_error", "blockers": blockers}]
    return (
        blockers,
        [],
        snapshots,
        seat0.classify_blockers(blockers),
        errors,
    )
```

Initialize diagnostics with the other normal-path locals:

```python
automation_errors: list[dict] = []
```

Unpack the initial and repair pass results as follows:

```python
(
    after_blockers,
    cleanup_records,
    blocker_snapshots,
    groups,
    pass_errors,
) = await _mech_pass("after_normal")
automation_errors.extend(pass_errors)
```

```python
(
    after_blockers,
    repair_cleanup,
    repair_snaps,
    groups,
    pass_errors,
) = await _mech_pass("after_repair")
automation_errors.extend(pass_errors)
cleanup_records = cleanup_records + repair_cleanup
blocker_snapshots = blocker_snapshots + repair_snaps
```

In `_recheck_cleanup_repair_or_refire()`, unpack and persist diagnostics before making the terminal decision:

```python
(
    after_blockers,
    cleanup_records,
    snaps,
    groups,
    pass_errors,
) = await _mech_pass("after_refire")
s0["automation_errors"] = s0["automation_errors"] + pass_errors
```

Use the same five-value unpack for its optional `after_repair` call and append those errors as well. Add these backward-compatible fields in `_base_seat0_record`:

```python
"automation_errors": list(automation_errors),
"end_turn_errors": [],
```

The hard synthetic blocker follows the existing direct `human_pending` path without a repair call or coordinator choice.

- [ ] **Step 6: Contain uncertain end requests without blind redispatch**

Add a nested helper used by both the first request and re-fire path:

```python
async def _fire_seat0_end(record: dict) -> None:
    seat0_state.mark_end_fired()
    try:
        await hook.end_turn(conn)
    except Exception as exc:
        error = {
            "request": seat0_state.end_turn_requests,
            "error": repr(exc),
        }
        record["seat0"]["end_turn_errors"].append(error)
        log.append({
            "level": "WARNING",
            "event": "seat0_end_turn_uncertain",
            "turn": record["turn"],
            **error,
        })
        await _reconnect_with_retry(conn)
```

Replace both `mark_end_fired(); await hook.end_turn(conn)` pairs with:

```python
await _fire_seat0_end(record)
```

Do not immediately re-dispatch after an exception: the request may have reached the game before the response was lost. Poll first; a newer turn terminalizes `advanced`, while a same-turn active seat reaches the existing five-poll recheck and three-request bound.

- [ ] **Step 7: Run focused and cancellation tests, then commit**

Run:

```bash
uv run --extra test pytest tests/arena/test_seat0.py tests/arena/test_coordinator.py -q
```

Expected: PASS. In particular, every existing `CancelledError` test still propagates and cleanup still restores seat 0.

```bash
git add src/civ_mcp/arena/seat0.py src/civ_mcp/arena/coordinator.py tests/arena/test_seat0.py tests/arena/test_coordinator.py
git commit -m "fix(arena): contain seat-zero completion failures"
```

---

### Task 8: Offline Verification and Attended Live Gates

**Files:**
- Modify only with observed evidence: `docs/superpowers/plans/2026-07-14-arena-seat0-live-gates.md`
- Modify source/tests only through a new red-first fix commit if a gate reveals another defect.

**Interfaces:**
- Consumes: Tasks 1-7 and the two existing smoke configs.
- Produces: an offline-green branch and live evidence for scripted, LLM, and human-escape paths.

- [ ] **Step 1: Run the complete offline matrix**

Run from the seat-0 worktree:

```bash
uv run --extra test pytest tests/arena/test_hook.py tests/arena/test_seat0.py tests/arena/test_coordinator.py tests/arena/test_analyze.py tests/arena/test_orphan_sweep.py -q
uv run --extra test pytest tests/arena -q
uv run --extra test pytest tests/test_parsers.py tests/test_save_scumming.py -q
uv run --extra test pytest tests/ -q
git diff --check
git status --short --branch
```

Expected: every test command passes, `git diff --check` emits no output, and the status contains only intentional branch changes.

- [ ] **Step 2: Audit the authority and execution-context boundaries**

Run:

```bash
rg -n "ACTION_ENDTURN|end_turn|execute_write|execute_read|sweep_promotions|set_research|set_city_production|ARENA_SEAT0_AUTOMATION_FAILURE" src/civ_mcp/arena src/civ_mcp/lua/notifications.py
```

Verify from the matches:

- no arena policy can call `end_turn`;
- only `hook.end_turn()` dispatches seat 0's ACTION_ENDTURN;
- valid `AI_PROCESSING` reaches only poll and sleep;
- the orphan sweep remains in ordinary human-idle handling;
- the synthetic automation blocker causes a handoff and never a strategic default;
- research and production choices remain policy-owned.

- [ ] **Step 3: Read the live-operation skill and prepare evidence**

Read `tools/skills/civ6-arena-live/SKILL.md` completely. Create or update `docs/superpowers/plans/2026-07-14-arena-seat0-live-gates.md` with the current commit SHA, unique run IDs, save names, transcript paths, and one row per seat-0 turn.

- [ ] **Step 4: Run the scripted mixed-seat gate**

Using the live skill's startup and cleanup sequence, run:

```bash
uv run civ-arena --config experiments/arena-seat0-scripted-smoke.yaml --run-id seat0-scripted-20260715
uv run civ-arena-analyze --run-id seat0-scripted-20260715
```

Acceptance: 5-10 distinct seat-0 turns advance without routine human input; both puppet seats run; the focused scripted repair makes any research/production choice; malformed samples do not duplicate a turn; every seat-0 turn has one terminal record; an `0_MCP_NNNN` save or precise save-failure diagnostic is present.

- [ ] **Step 5: Run the LLM seat-0 gate**

Run:

```bash
uv run civ-arena --config experiments/arena-seat0-llm-smoke.yaml --run-id seat0-llm-20260715
uv run civ-arena-analyze --run-id seat0-llm-20260715
```

Acceptance: 10-20 distinct seat-0 turns advance; exclusive tuner handoff reconnects; all strategic choices are made by the LLM; at least one supported blocker repair completes when available; player 0 analysis shows no duplicate logical turn and failed records do not skew skip rate.

- [ ] **Step 6: Run the hard-block human escape gate**

Load a real save with an unsupported or inaccessible blocker, preferably `ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE`. Verify exactly one `seat0_human_pending` event, no repeated policy/end request on that turn, local human control, and watcher resume after the human resolves and ends the turn. Record `NOT EXERCISED` with the checked saves if no real blocker can be produced; do not fabricate Lua state.

- [ ] **Step 7: Stop on any live failure**

Preserve the run directory and recovery save, record observed versus expected behavior, stop safely, and return to a new failing test plus fix commit. Run the full offline matrix before repeating the failed gate.

- [ ] **Step 8: Final verification and evidence commit**

Run:

```bash
uv run --extra test pytest tests/ -q
git diff --check
git status --short --branch
```

Update the evidence document with PASS, FAIL, or NOT EXERCISED for each gate, then commit only the evidence file:

```bash
git add docs/superpowers/plans/2026-07-14-arena-seat0-live-gates.md
git commit -m "docs(arena): record hardened seat-zero live gates"
```

---

## Final Review Checklist

- [ ] Malformed `turn=-1` and backward turns cannot mark `advanced`, alter grace state, or readmit the same seat-0 turn.
- [ ] The seat-0 engine probe cannot abort core poll output.
- [ ] Valid AI processing is not capped by `idle_poll_limit`; human pending and persistently degraded polls remain bounded.
- [ ] No orphan sweep or other InGame operation runs during valid AI processing.
- [ ] A units blocker causes one finish-units call per mechanical pass.
- [ ] Every transcript path uses `_state_delta` and malformed snapshots degrade to `None`.
- [ ] Failed turns are excluded from the attention skip-rate denominator and remain visible in `captured` and series diagnostics.
- [ ] A policy lacking `blocker_block` is never called a second time without focused context.
- [ ] Recheck state uses `Seat0ResumeContext`; no untyped dictionary or `policy_for(0)` fallback remains.
- [ ] Ordinary mechanical failures retry once, then produce one explicit hard handoff record rather than aborting the run.
- [ ] An uncertain end request is polled before any retry and remains bounded by the existing three-request limit.
- [ ] Autosave remains best-effort and ordinary save failures remain transcript data.
- [ ] `CancelledError` still propagates through record finalization and human-safety cleanup.
- [ ] Full offline tests pass before any live gate.
- [ ] The human remains passive except for an explicit hard-block escape.

## Deferred Follow-Up

After all three live gates, separately design a behavior-preserving extraction of seat-0 orchestration from `run_arena`. The follow-up should use the live evidence to choose the boundary and should not be folded into this correctness patch. The typed resume context in Task 6 removes the current silent-default hazard without forcing that larger rewrite before runtime behavior is proven.
