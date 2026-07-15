"""Tests for the pure seat-0 state machine, blocker authority boundary,
mechanical cleanup, recovery save, and policy-attempt merge.

Global constraint under test throughout: the coordinator/seat0 module never
makes a strategic choice. Mechanical cleanup only finishes unit moves,
dismisses a stale notification once Lua proves the underlying choice is
already set, and acknowledges purely informational prompts.
"""

import inspect

import pytest

import civ_mcp.arena.seat0 as seat0
import civ_mcp.lua as lq
from civ_mcp.arena.seat0 import (
    MECHANICAL_BLOCKERS,
    BlockerGroups,
    Seat0Phase,
    Seat0Poll,
    Seat0ResumeContext,
    Seat0TurnState,
    apply_mechanical_cleanup,
    automation_failure_blocker,
    build_blocker_block,
    classify_blockers,
    merge_policy_attempts,
    query_blockers,
    save_recovery_anchor,
)


class ScriptedConn:
    """Fake connection: records every call and hands back queued canned
    responses for execute_write, in call order. execute_read is recorded
    but always returns [] (nothing in this module reads game state)."""

    def __init__(self):
        self.reads: list[str] = []
        self.writes: list[str] = []
        self._write_queue: list[list[str]] = []

    def queue_write(self, lines: list[str]) -> None:
        self._write_queue.append(lines)

    async def execute_read(self, lua: str) -> list[str]:
        self.reads.append(lua)
        return []

    async def execute_write(self, lua: str) -> list[str]:
        self.writes.append(lua)
        if self._write_queue:
            return self._write_queue.pop(0)
        return []


def test_automation_failure_blocker_is_hard():
    blocker = automation_failure_blocker(
        "after_normal", "ConnectionError('blocker query unavailable')"
    )

    groups = classify_blockers([blocker])

    assert groups.mechanical == []
    assert groups.decision == []
    assert groups.hard == [blocker]


# ---------------------------------------------------------------------------
# Step 1: phase-machine tests
# ---------------------------------------------------------------------------


def test_state_does_not_readmit_same_active_turn():
    state = Seat0TurnState()
    assert state.can_admit(turn=7, seat0_active=True)
    state.admit(7)
    state.mark_policy_played()
    state.mark_end_fired()
    for _ in range(5):
        assert state.observe(turn=7, seat0_active=True) == Seat0Poll.WAIT
    assert state.observe(turn=7, seat0_active=True) == Seat0Poll.RECHECK
    assert not state.can_admit(turn=7, seat0_active=True)


def state_after_one_end_request(turn: int) -> Seat0TurnState:
    state = Seat0TurnState()
    state.admit(turn)
    state.mark_policy_played()
    state.mark_end_fired()
    return state


def test_state_distinguishes_ai_processing_from_advance():
    state = state_after_one_end_request(turn=7)
    assert state.observe(turn=7, seat0_active=False) == Seat0Poll.WAIT
    assert state.phase is Seat0Phase.AI_PROCESSING
    assert state.observe(turn=8, seat0_active=True) == Seat0Poll.ADVANCED
    assert state.phase is Seat0Phase.ADVANCED


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


def test_end_turn_requests_are_bounded_at_three():
    state = Seat0TurnState()
    state.admit(7)
    for expected in (1, 2, 3):
        state.mark_end_fired()
        assert state.end_turn_requests == expected
    assert not state.may_fire_end_turn


def test_cannot_admit_before_seat0_is_active():
    state = Seat0TurnState()
    assert not state.can_admit(turn=7, seat0_active=False)


def test_needs_drain_covers_in_flight_phases_only():
    state = Seat0TurnState()
    assert state.needs_drain is False  # READY
    state.mark_policy_played()
    assert state.needs_drain is True
    state.mark_end_fired()
    assert state.needs_drain is True
    state.phase = Seat0Phase.AI_PROCESSING
    assert state.needs_drain is True
    state.mark_human_pending()
    assert state.needs_drain is True
    state.phase = Seat0Phase.ADVANCED
    assert state.needs_drain is False
    state.mark_interrupted()
    assert state.needs_drain is False


def test_mark_human_pending_holds_same_turn_until_advance():
    state = Seat0TurnState()
    state.admit(7)
    state.mark_policy_played()
    state.mark_human_pending()
    assert state.phase is Seat0Phase.HUMAN_PENDING
    # neither activity flag flips human_pending back into play -- only a
    # real turn-number change does, unlike END_FIRED's grace/AI split.
    assert state.observe(turn=7, seat0_active=True) == Seat0Poll.WAIT
    assert state.phase is Seat0Phase.HUMAN_PENDING
    assert state.observe(turn=7, seat0_active=False) == Seat0Poll.WAIT
    assert state.phase is Seat0Phase.HUMAN_PENDING
    assert state.observe(turn=8, seat0_active=True) == Seat0Poll.ADVANCED
    assert state.phase is Seat0Phase.ADVANCED


def test_mark_interrupted_is_a_terminal_marker():
    state = Seat0TurnState()
    state.admit(7)
    state.mark_policy_played()
    state.mark_interrupted()
    assert state.phase is Seat0Phase.INTERRUPTED
    assert state.needs_drain is False


def test_reset_only_permitted_after_advance():
    state = state_after_one_end_request(turn=7)
    with pytest.raises(RuntimeError):
        state.reset()


def test_reset_returns_to_ready_for_next_admission():
    state = state_after_one_end_request(turn=7)
    state.observe(turn=7, seat0_active=False)  # -> AI_PROCESSING
    assert state.observe(turn=8, seat0_active=True) == Seat0Poll.ADVANCED
    state.reset()
    assert state.phase is Seat0Phase.READY
    assert state.turn is None
    assert state.end_turn_requests == 0
    assert state.grace_polls == 0
    assert state.repair_used is False
    assert state.critical_emitted is False
    assert state.can_admit(turn=8, seat0_active=True)


def test_reset_clears_typed_resume_context():
    state = state_after_one_end_request(turn=7)
    state.resume_context = Seat0ResumeContext(
        policy=object(), caps={"government": True}, exclusive=True
    )
    state.observe(turn=8, seat0_active=True)

    state.reset()

    assert state.resume_context is None


def test_critical_emitted_marker_fires_exactly_once():
    state = Seat0TurnState()
    assert state.mark_critical_emitted() is True
    assert state.mark_critical_emitted() is False
    assert state.mark_critical_emitted() is False
    assert state.critical_emitted is True


# ---------------------------------------------------------------------------
# Step 2: blocker-authority tests
# ---------------------------------------------------------------------------

_ALL_BLOCKING_TYPES_BUT_MECHANICAL = sorted(set(lq.BLOCKING_TOOL_MAP) - MECHANICAL_BLOCKERS)


def test_mechanical_blockers_is_exactly_the_closed_list():
    assert MECHANICAL_BLOCKERS == {
        "ENDTURN_BLOCKING_UNITS",
        "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE",
        "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK",
    }


def test_classify_blockers_separates_mechanical_decision_and_hard():
    blockers = (
        [{"type": t, "message": f"msg-{t}"} for t in sorted(MECHANICAL_BLOCKERS)]
        + [{"type": t, "message": f"msg-{t}"} for t in _ALL_BLOCKING_TYPES_BUT_MECHANICAL]
        + [
            {"type": "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE", "message": "Spy escape"},
            {"type": "UNKNOWN", "message": "??"},
            {"type": "ENDTURN_BLOCKING_SOME_FUTURE_TYPE", "message": "future type"},
        ]
    )
    groups = classify_blockers(blockers)

    assert {b["type"] for b in groups.mechanical} == MECHANICAL_BLOCKERS
    # Research/civic/production remain decision blockers even though a stale
    # check may mechanically clear their notification later.
    assert {"ENDTURN_BLOCKING_RESEARCH", "ENDTURN_BLOCKING_CIVIC", "ENDTURN_BLOCKING_PRODUCTION"} <= {
        b["type"] for b in groups.decision
    }
    assert "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE" not in {b["type"] for b in groups.decision}
    assert {b["type"] for b in groups.hard} == {
        "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE",
        "UNKNOWN",
        "ENDTURN_BLOCKING_SOME_FUTURE_TYPE",
    }
    # decision blockers with a registered resolver stay out of hard
    assert set(_ALL_BLOCKING_TYPES_BUT_MECHANICAL) - {"ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE"} == {
        b["type"] for b in groups.decision
    }


def test_classify_blockers_preserves_order_within_each_group():
    blockers = [
        {"type": "ENDTURN_BLOCKING_RESEARCH", "message": "Choose Research"},
        {"type": "ENDTURN_BLOCKING_UNITS", "message": "Units need orders"},
        {"type": "ENDTURN_BLOCKING_CIVIC", "message": "Choose Civic"},
    ]
    groups = classify_blockers(blockers)
    assert [b["type"] for b in groups.decision] == [
        "ENDTURN_BLOCKING_RESEARCH",
        "ENDTURN_BLOCKING_CIVIC",
    ]
    assert [b["type"] for b in groups.mechanical] == ["ENDTURN_BLOCKING_UNITS"]


def test_classify_blockers_of_empty_list_is_all_empty_groups():
    groups = classify_blockers([])
    assert groups == BlockerGroups()


def test_build_blocker_block_includes_message_and_tool_hint():
    blockers = [{"type": "ENDTURN_BLOCKING_RESEARCH", "message": "Choose Research"}]
    block = build_blocker_block(blockers)
    assert "Choose Research" in block
    assert lq.BLOCKING_TOOL_MAP["ENDTURN_BLOCKING_RESEARCH"] in block
    assert "Prior policy error" not in block


def test_build_blocker_block_includes_prior_error_when_supplied():
    block = build_blocker_block([], prior_error="gateway unavailable")
    assert "Prior policy error: gateway unavailable" in block


def test_build_blocker_block_states_one_repair_pass_and_no_end_turn():
    block = build_blocker_block([{"type": "ENDTURN_BLOCKING_CIVIC", "message": "Choose Civic"}])
    assert "one" in block.lower() and "repair" in block.lower()
    assert "end_turn" in block
    assert "not available" in block.lower()


def test_build_blocker_block_covers_multiple_blockers():
    blockers = [
        {"type": "ENDTURN_BLOCKING_RESEARCH", "message": "Choose Research"},
        {"type": "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT", "message": "Appoint a governor"},
    ]
    block = build_blocker_block(blockers)
    assert "Choose Research" in block
    assert "Appoint a governor" in block
    assert lq.BLOCKING_TOOL_MAP["ENDTURN_BLOCKING_RESEARCH"] in block
    assert lq.BLOCKING_TOOL_MAP["ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT"] in block


# ---------------------------------------------------------------------------
# Step 3: async cleanup / autosave tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_blockers_uses_only_execute_write():
    conn = ScriptedConn()
    conn.queue_write(["BLOCKING|ENDTURN_BLOCKING_RESEARCH|Choose Research", lq.SENTINEL])
    blockers = await query_blockers(conn)
    assert blockers == [{"type": "ENDTURN_BLOCKING_RESEARCH", "message": "Choose Research"}]
    assert conn.reads == []
    assert len(conn.writes) == 1
    assert conn.writes[0] == lq.build_end_turn_blocking_query()


@pytest.mark.asyncio
async def test_query_blockers_none_is_empty_list():
    conn = ScriptedConn()
    conn.queue_write(["NONE", lq.SENTINEL])
    assert await query_blockers(conn) == []


@pytest.mark.asyncio
async def test_apply_mechanical_cleanup_finishes_units(monkeypatch):
    calls = []

    async def fake_finish_units(conn, pid):
        calls.append((conn, pid))
        return ["FINISHED|1"]

    monkeypatch.setattr(seat0.hook, "finish_units", fake_finish_units)
    conn = ScriptedConn()
    blockers = [{"type": "ENDTURN_BLOCKING_UNITS", "message": "Units need orders"}]
    cleanup = await apply_mechanical_cleanup(conn, blockers)

    assert calls == [(conn, 0)]
    assert cleanup == [
        {"type": "ENDTURN_BLOCKING_UNITS", "action": "finish_units", "result": "requested"}
    ]
    # finish_units is a GameCore/execute_read primitive -- no InGame write here.
    assert conn.writes == []


@pytest.mark.asyncio
async def test_apply_mechanical_cleanup_records_not_set_for_stale_research():
    conn = ScriptedConn()
    conn.queue_write(["NOT_SET", lq.SENTINEL])
    blockers = [{"type": "ENDTURN_BLOCKING_RESEARCH", "message": "Choose Research"}]
    cleanup = await apply_mechanical_cleanup(conn, blockers)

    assert len(cleanup) == 1
    assert cleanup[0]["type"] == "ENDTURN_BLOCKING_RESEARCH"
    assert cleanup[0]["result"] == "NOT_SET"
    assert conn.writes == [lq.build_clear_stale_end_turn_blocker("ENDTURN_BLOCKING_RESEARCH")]


@pytest.mark.asyncio
async def test_apply_mechanical_cleanup_clears_stale_when_lua_proves_it_set():
    conn = ScriptedConn()
    conn.queue_write(["STALE_CLEARED", lq.SENTINEL])
    blockers = [{"type": "ENDTURN_BLOCKING_CIVIC", "message": "Choose Civic"}]
    cleanup = await apply_mechanical_cleanup(conn, blockers)

    assert cleanup[0]["result"] == "STALE_CLEARED"


@pytest.mark.asyncio
async def test_apply_mechanical_cleanup_acknowledges_world_congress_look():
    conn = ScriptedConn()
    conn.queue_write(["PROMPT_SEEN", lq.SENTINEL])
    blockers = [{"type": "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK", "message": "Review WC results"}]
    cleanup = await apply_mechanical_cleanup(conn, blockers)

    assert len(cleanup) == 1
    assert cleanup[0]["type"] == "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK"
    assert cleanup[0]["action"] == "acknowledge_informational"
    assert conn.writes == [
        lq.build_mark_end_turn_prompt_seen("ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK")
    ]


@pytest.mark.asyncio
async def test_apply_mechanical_cleanup_acknowledges_government_change_prompt():
    conn = ScriptedConn()
    conn.queue_write(["PROMPT_SEEN", lq.SENTINEL])
    blockers = [
        {"type": "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE", "message": "Consider government"}
    ]
    cleanup = await apply_mechanical_cleanup(conn, blockers)

    assert cleanup[0]["action"] == "acknowledge_informational"
    assert conn.writes == [
        lq.build_mark_end_turn_prompt_seen("ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE")
    ]


@pytest.mark.asyncio
async def test_apply_mechanical_cleanup_ignores_pure_decision_blockers():
    """No strategic choice may be made here: a blocker with no mechanical or
    stale-clear handler (e.g. governor appointment) triggers no Lua call."""
    conn = ScriptedConn()
    blockers = [{"type": "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT", "message": "Appoint a governor"}]
    cleanup = await apply_mechanical_cleanup(conn, blockers)

    assert cleanup == []
    assert conn.writes == []
    assert conn.reads == []


@pytest.mark.asyncio
async def test_apply_mechanical_cleanup_handles_multiple_blockers_in_order(monkeypatch):
    calls = []

    async def fake_finish_units(conn, pid):
        calls.append((conn, pid))
        return ["FINISHED|2"]

    monkeypatch.setattr(seat0.hook, "finish_units", fake_finish_units)
    conn = ScriptedConn()
    conn.queue_write(["PROMPT_SEEN", lq.SENTINEL])  # for WORLD_CONGRESS_LOOK
    conn.queue_write(["NOT_SET", lq.SENTINEL])  # for RESEARCH
    blockers = [
        {"type": "ENDTURN_BLOCKING_UNITS", "message": "Units need orders"},
        {"type": "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK", "message": "Review WC results"},
        {"type": "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT", "message": "Appoint a governor"},
        {"type": "ENDTURN_BLOCKING_RESEARCH", "message": "Choose Research"},
    ]
    cleanup = await apply_mechanical_cleanup(conn, blockers)

    assert calls == [(conn, 0)]
    assert [c["type"] for c in cleanup] == [
        "ENDTURN_BLOCKING_UNITS",
        "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK",
        "ENDTURN_BLOCKING_RESEARCH",
    ]
    assert cleanup[1]["result"] == "PROMPT_SEEN"
    assert cleanup[2]["result"] == "NOT_SET"


@pytest.mark.asyncio
async def test_save_recovery_anchor_calls_save_game_with_padded_turn_name(monkeypatch):
    calls = []

    async def fake_save_game(conn, name):
        calls.append((conn, name))
        return f"Saved: {name}"

    monkeypatch.setattr(seat0, "save_game", fake_save_game)
    conn = ScriptedConn()
    result = await save_recovery_anchor(conn, 7)

    assert calls == [(conn, "0_MCP_0007")]
    assert result == {"name": "0_MCP_0007", "ok": True, "result": "Saved: 0_MCP_0007"}


@pytest.mark.asyncio
async def test_save_recovery_anchor_pads_turn_number():
    conn = ScriptedConn()
    conn.queue_write(["OK|0_MCP_0123", lq.SENTINEL])
    result = await save_recovery_anchor(conn, 123)
    assert result["name"] == "0_MCP_0123"
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_save_recovery_anchor_returns_structured_failure_on_exception(monkeypatch):
    async def raising_save_game(conn, name):
        raise RuntimeError("tuner disconnected")

    monkeypatch.setattr(seat0, "save_game", raising_save_game)
    conn = ScriptedConn()
    result = await save_recovery_anchor(conn, 12)

    assert result["ok"] is False
    assert result["name"] == "0_MCP_0012"
    assert "tuner disconnected" in result["error"]


def test_save_recovery_anchor_has_no_os_gate():
    """The save must not be gated on any host-OS check -- WSL may be
    fronting a Windows-hosted game, so os.name/platform/sys.platform checks
    would silently skip the save on the exact host that needs it."""
    src = inspect.getsource(seat0.save_recovery_anchor)
    for forbidden in ("platform.", "os.name", "sys.platform"):
        assert forbidden not in src


# ---------------------------------------------------------------------------
# Step 4: merge tests
# ---------------------------------------------------------------------------


def _normal_attempt() -> dict:
    return {
        "summary": "normal done",
        "actions": [{"tool": "set_research", "result": "ok"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "usd": 0.01},
        "transcript": {
            "steps": [{"idx": 0, "tool_name": "set_research"}],
            "invalid_tool_calls": [],
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "wall_clock_s": 1.5,
            "final_summary": "normal done",
            "civ_options": {"max_steps": 6},
        },
    }


def _repair_attempt() -> dict:
    return {
        "summary": "repair done",
        "actions": [{"tool": "set_city_production", "result": "ok"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "usd": 0.002},
        "transcript": {
            "steps": [{"idx": 0, "tool_name": "set_city_production"}],
            "invalid_tool_calls": [{"tool_name": "end_turn", "reason": "gated"}],
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "wall_clock_s": 0.4,
            "final_summary": "repair done",
        },
    }


def test_merge_policy_attempts_concatenates_and_sums():
    normal = _normal_attempt()
    repair = _repair_attempt()
    normal_snapshot = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                       for k, v in normal.items()}
    repair_snapshot = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                       for k, v in repair.items()}

    merged = merge_policy_attempts(normal, repair)

    assert merged["transcript"]["steps"] == normal["transcript"]["steps"] + repair["transcript"]["steps"]
    assert merged["transcript"]["invalid_tool_calls"] == repair["transcript"]["invalid_tool_calls"]
    assert merged["transcript"]["prompt_tokens"] == 120
    assert merged["transcript"]["completion_tokens"] == 60
    assert merged["transcript"]["wall_clock_s"] == pytest.approx(1.9)
    assert merged["usage"]["usd"] == pytest.approx(0.012)
    assert merged["usage"]["prompt_tokens"] == 120
    assert merged["usage"]["completion_tokens"] == 60
    # Normal's final summary wins at the transcript level; repair's own
    # summary is recorded separately by the caller under seat0.repair.
    assert merged["transcript"]["final_summary"] == "normal done"
    assert merged["transcript"]["civ_options"] == {"max_steps": 6}
    assert merged["actions"] == normal["actions"] + repair["actions"]

    # neither input dict was mutated
    assert normal == normal_snapshot
    assert repair == repair_snapshot


def test_merge_policy_attempts_normal_missing_uses_repair_as_base():
    repair = _repair_attempt()
    merged = merge_policy_attempts(None, repair)

    assert merged["transcript"]["steps"] == repair["transcript"]["steps"]
    assert merged["transcript"]["prompt_tokens"] == 20
    assert merged["transcript"]["completion_tokens"] == 10
    assert merged["transcript"]["final_summary"] == "repair done"
    assert merged["summary"] == "repair done"
    assert merged["usage"]["usd"] == pytest.approx(0.002)


def test_merge_policy_attempts_repair_missing_is_normal_only():
    normal = _normal_attempt()
    merged = merge_policy_attempts(normal, None)

    assert merged["transcript"]["steps"] == normal["transcript"]["steps"]
    assert merged["transcript"]["invalid_tool_calls"] == []
    assert merged["transcript"]["final_summary"] == "normal done"
    assert merged["usage"]["usd"] == pytest.approx(0.01)


def test_merge_policy_attempts_both_missing_returns_empty_shape():
    merged = merge_policy_attempts(None, None)

    assert merged["transcript"]["steps"] == []
    assert merged["transcript"]["invalid_tool_calls"] == []
    assert merged["transcript"].get("prompt_tokens", 0) == 0
    assert merged["transcript"].get("wall_clock_s", 0) == 0
    assert merged["actions"] == []
    assert merged["summary"] == ""
    assert "usage" not in merged


# ---------------------------------------------------------------------------
# Task 6: repair-block shape for the coordinator's normal-exception path
# ---------------------------------------------------------------------------


def test_build_blocker_block_normal_exception_empty_blockers_verbatim():
    """For a normal exception with no detected blockers the repair block still
    leads with the header + prior error verbatim and instructs the pilot to
    inspect and finish the turn."""
    block = build_blocker_block([], prior_error="gateway unavailable")
    assert block.startswith(
        "== END-TURN REPAIR ==\nPrior policy error: gateway unavailable"
    )
    lowered = block.lower()
    assert "inspect" in lowered and "finish the turn" in lowered
    # end_turn is explicitly withheld from the repair pass.
    assert "end_turn" in block and "not available" in lowered


# ---------------------------------------------------------------------------
# Task 7: grace / re-fire bound interplay the coordinator drives
# ---------------------------------------------------------------------------


def test_grace_then_recheck_then_bounded_refires():
    """Simulate the coordinator drain loop: after each end request five
    same-turn/active polls are WAIT, the sixth is RECHECK (drive a re-fire).
    After the third request the state refuses a fourth (may_fire_end_turn
    False) even though observe keeps returning RECHECK -- the coordinator must
    escalate to human_pending instead of firing a fourth ACTION_ENDTURN."""
    state = Seat0TurnState()
    state.admit(7)
    state.mark_policy_played()

    fired = 0
    rechecks = 0
    for _cycle in range(5):
        if not state.may_fire_end_turn:
            break
        state.mark_end_fired()
        fired += 1
        # Five quiet grace polls: no recheck yet.
        for _ in range(seat0._GRACE_POLL_LIMIT):
            assert state.observe(turn=7, seat0_active=True) is Seat0Poll.WAIT
        # The sixth same-turn/active poll asks the coordinator to recheck.
        assert state.observe(turn=7, seat0_active=True) is Seat0Poll.RECHECK
        rechecks += 1

    assert fired == 3
    assert rechecks == 3
    assert state.end_turn_requests == 3
    assert state.may_fire_end_turn is False
    # An inactive observation while END_FIRED is AI processing, never RECHECK.
    state.grace_polls = 0
    assert state.observe(turn=7, seat0_active=False) is Seat0Poll.WAIT
    assert state.phase is Seat0Phase.AI_PROCESSING
