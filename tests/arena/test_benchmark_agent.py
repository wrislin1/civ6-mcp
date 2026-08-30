import pytest

import civ_mcp.arena.benchmark_agent as benchmark_agent
from civ_mcp.arena.backends import Reply
from civ_mcp.arena.benchmark_agent import (
    BENCHMARK_SYSTEM,
    EpisodeTerminal,
    EpisodeTimedOut,
    SingleTurnAgent,
    benchmark_prompt,
    resolved_benchmark_tools,
)


def test_prompt_is_objective_blind_and_control_surface_is_common():
    prompt = benchmark_prompt(turn=157, player_id=0)
    assert prompt == (
        "It is turn 157. You control player 0. Assess the current situation and "
        "issue the best orders available for this turn. When finished, call finish_trial."
    )
    forbidden = {"builder", "luxury", "repair", "9,10", "rubric"}
    assert not any(word in prompt.lower() for word in forbidden)
    assert resolved_benchmark_tools("minimal")[-1]["function"]["name"] == "finish_trial"
    assert resolved_benchmark_tools("standard")[-1]["function"]["name"] == "finish_trial"


def test_benchmark_system_is_also_objective_blind():
    forbidden = {"builder", "luxury", "repair", "9,10", "rubric"}
    assert not any(word in BENCHMARK_SYSTEM.lower() for word in forbidden)


def test_no_end_turn_schema_in_resolved_benchmark_tiers():
    for tier in ("minimal", "standard"):
        names = [t["function"]["name"] for t in resolved_benchmark_tools(tier)]
        assert "end_turn" not in names
        assert names[-1] == "finish_trial"


def test_resolved_benchmark_tools_rejects_a_tier_that_resolves_end_turn(monkeypatch):
    monkeypatch.setattr(
        benchmark_agent, "resolve_tools", lambda tier: ("get_units", "end_turn")
    )
    with pytest.raises(ValueError, match="end_turn"):
        resolved_benchmark_tools("minimal")


class FakeGS:
    def __init__(self):
        self.calls = []

    async def get_game_overview(self):
        return "OVERVIEW"

    async def fortify_unit(self, unit_index):
        self.calls.append(("fortify", unit_index))
        return "FORTIFIED"


class TwoRoundBackend:
    """Round 1: one game call. Round 2: finish_trial alone."""

    def __init__(self):
        self.n = 0

    async def chat(self, messages, tools):
        self.n += 1
        if self.n == 1:
            return Reply(
                text=None,
                tool_calls=[
                    {"id": "1", "name": "fortify_unit", "arguments": '{"unit_index": 0}'},
                ],
                prompt_tokens=5,
                completion_tokens=2,
            )
        return Reply(
            text=None,
            tool_calls=[{"id": "2", "name": "finish_trial", "arguments": "{}"}],
            prompt_tokens=5,
            completion_tokens=2,
        )


@pytest.mark.asyncio
async def test_explicit_finish_stops_after_processing_game_calls():
    backend = TwoRoundBackend()
    gs = FakeGS()
    agent = SingleTurnAgent(backend, "minimal", episode_wall_s=5.0, max_steps=6)

    evidence = await agent.run(gs, player_id=0, turn=1)

    assert evidence.terminal == EpisodeTerminal.FINISH_TRIAL
    assert backend.n == 2  # no further chat() round after finish_trial
    assert gs.calls == [("fortify", 0)]
    assert len(evidence.steps) == 1
    assert evidence.steps[0]["tool_name"] == "fortify_unit"
    assert evidence.steps[0]["tool_result_full"] == "FORTIFIED"


class ImmediateDoneBackend:
    async def chat(self, messages, tools):
        return Reply(text="nothing to do", tool_calls=[], prompt_tokens=3, completion_tokens=2)


@pytest.mark.asyncio
async def test_implicit_finish_with_no_tool_calls():
    agent = SingleTurnAgent(ImmediateDoneBackend(), "minimal", episode_wall_s=5.0, max_steps=4)

    evidence = await agent.run(FakeGS(), player_id=0, turn=1)

    assert evidence.terminal == EpisodeTerminal.IMPLICIT_FINISH
    assert evidence.steps == []
    assert evidence.final_summary == "nothing to do"


class RepeatingBackend:
    """Never calls finish_trial -- keeps issuing game calls forever."""

    def __init__(self):
        self.n = 0

    async def chat(self, messages, tools):
        self.n += 1
        return Reply(
            text=None,
            tool_calls=[
                {"id": str(self.n), "name": "fortify_unit", "arguments": '{"unit_index": 0}'},
            ],
            prompt_tokens=1,
            completion_tokens=1,
        )


@pytest.mark.asyncio
async def test_step_limit_reached_without_finish():
    backend = RepeatingBackend()
    agent = SingleTurnAgent(backend, "minimal", episode_wall_s=5.0, max_steps=3)

    evidence = await agent.run(FakeGS(), player_id=0, turn=1)

    assert evidence.terminal == EpisodeTerminal.STEP_LIMIT
    assert backend.n == 3
    assert len(evidence.steps) == 3


class BatchFinishBackend:
    """One response containing two game calls AND finish_trial."""

    async def chat(self, messages, tools):
        return Reply(
            text=None,
            tool_calls=[
                {"id": "1", "name": "get_overview", "arguments": "{}"},
                {"id": "2", "name": "fortify_unit", "arguments": '{"unit_index": 1}'},
                {"id": "3", "name": "finish_trial", "arguments": "{}"},
            ],
            prompt_tokens=9,
            completion_tokens=4,
        )


@pytest.mark.asyncio
async def test_multiple_game_calls_and_finish_processed_in_one_response():
    backend = BatchFinishBackend()
    gs = FakeGS()
    agent = SingleTurnAgent(backend, "minimal", episode_wall_s=5.0, max_steps=6)

    evidence = await agent.run(gs, player_id=0, turn=1)

    assert evidence.terminal == EpisodeTerminal.FINISH_TRIAL
    assert [s["tool_name"] for s in evidence.steps] == ["get_overview", "fortify_unit"]
    assert gs.calls == [("fortify", 1)]


class HangingBackend:
    async def chat(self, messages, tools):
        import asyncio

        await asyncio.sleep(10)
        return Reply(text="never", tool_calls=[], prompt_tokens=0, completion_tokens=0)


@pytest.mark.asyncio
async def test_episode_wall_timeout_raises_typed_error():
    agent = SingleTurnAgent(HangingBackend(), "minimal", episode_wall_s=0.05, max_steps=4)

    with pytest.raises(EpisodeTimedOut):
        await agent.run(FakeGS(), player_id=0, turn=1)


class RecordingGS:
    def __init__(self):
        self.conn = "FAKE_CONN"
        self.calls = []

    async def fortify_unit(self, unit_index):
        self.calls.append(unit_index)
        return "FORTIFIED"


class OneShotFinishBackend:
    def __init__(self):
        self.n = 0

    async def chat(self, messages, tools):
        self.n += 1
        if self.n == 1:
            return Reply(
                text=None,
                tool_calls=[
                    {"id": "1", "name": "fortify_unit", "arguments": '{"unit_index": 0}'},
                ],
                prompt_tokens=1,
                completion_tokens=1,
            )
        return Reply(
            text=None,
            tool_calls=[{"id": "2", "name": "finish_trial", "arguments": "{}"}],
            prompt_tokens=1,
            completion_tokens=1,
        )


@pytest.mark.asyncio
async def test_per_mutation_state_capture_uses_injected_capture_state_and_tile_coords():
    calls = []

    async def fake_capture_state(conn, player_id, tile_coords):
        calls.append((conn, player_id, tile_coords))
        return {"n": len(calls)}

    agent = SingleTurnAgent(
        OneShotFinishBackend(),
        "minimal",
        episode_wall_s=5.0,
        max_steps=4,
        tile_coords=[(9, 10)],
        capture_state=fake_capture_state,
    )
    gs = RecordingGS()

    evidence = await agent.run(gs, player_id=0, turn=1)

    assert evidence.terminal == EpisodeTerminal.FINISH_TRIAL
    step = evidence.steps[0]
    assert step["state_before"] == {"n": 1}
    assert step["state_after"] == {"n": 2}
    assert step["state_digest_before"] != step["state_digest_after"]
    assert len(calls) == 2
    assert calls[0] == ("FAKE_CONN", 0, ((9, 10),))


@pytest.mark.asyncio
async def test_no_state_capture_by_default():
    agent = SingleTurnAgent(OneShotFinishBackend(), "minimal", episode_wall_s=5.0, max_steps=4)
    gs = RecordingGS()

    evidence = await agent.run(gs, player_id=0, turn=1)

    step = evidence.steps[0]
    assert step["state_before"] is None
    assert step["state_after"] is None
    assert step["state_digest_before"] is None
    assert step["state_digest_after"] is None


@pytest.mark.asyncio
async def test_unknown_tool_call_is_recorded_as_invalid_and_not_dispatched():
    class UnknownToolBackend:
        def __init__(self):
            self.n = 0

        async def chat(self, messages, tools):
            self.n += 1
            if self.n == 1:
                return Reply(
                    text=None,
                    tool_calls=[{"id": "1", "name": "imaginary_tool", "arguments": "{}"}],
                    prompt_tokens=1,
                    completion_tokens=1,
                )
            return Reply(
                text=None,
                tool_calls=[{"id": "2", "name": "finish_trial", "arguments": "{}"}],
                prompt_tokens=1,
                completion_tokens=1,
            )

    agent = SingleTurnAgent(UnknownToolBackend(), "minimal", episode_wall_s=5.0, max_steps=4)
    evidence = await agent.run(FakeGS(), player_id=0, turn=1)

    assert evidence.terminal == EpisodeTerminal.FINISH_TRIAL
    assert len(evidence.invalid_tool_calls) == 1
    assert evidence.invalid_tool_calls[0]["reason"] == "unknown_tool"
    assert evidence.steps[0]["tool_result_full"].startswith("UNAVAILABLE")
