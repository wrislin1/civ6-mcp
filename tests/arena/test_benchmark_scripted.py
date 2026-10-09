import json

import pytest

from civ_mcp.arena.benchmark_agent import resolved_benchmark_tools
from civ_mcp.arena.benchmark_scripted import ScriptedBackend, batch_reply


def _script(**overrides):
    script = {
        "schema_version": "2.0.0",
        "script_id": "observe",
        "batches": [
            {"calls": [
                {"name": "get_units", "arguments": {}},
                {"name": "get_cities", "arguments": {}},
            ]},
            {"calls": [{"name": "finish_trial", "arguments": {}}]},
        ],
    }
    script.update(overrides)
    return script


GAME_TOOLS = ("get_units", "get_cities")


@pytest.mark.asyncio
async def test_script_returns_batches_without_scoring_access():
    script = {"schema_version": "2.0.0", "script_id": "observe",
              "batches": [{"calls": [
                  {"name": "get_units", "arguments": {}},
                  {"name": "get_cities", "arguments": {}}]},
                  {"calls": [{"name": "finish_trial", "arguments": {}}]}]}
    backend = ScriptedBackend(script, game_tools=("get_units", "get_cities"))
    from civ_mcp.arena.benchmark_agent import resolved_benchmark_tools
    schemas = resolved_benchmark_tools(("get_units", "get_cities"))
    assert len((await backend.chat([], schemas)).tool_calls) == 2
    assert (await backend.chat([], schemas)).tool_calls[0]["name"] == "finish_trial"
    backend.assert_exhausted()


@pytest.mark.asyncio
async def test_deterministic_ids_and_json_object_arguments():
    script = _script(batches=[
        {"calls": [
            {"name": "get_units", "arguments": {"b": 2, "a": 1}},
            {"name": "get_cities", "arguments": {}},
        ]},
        {"calls": [{"name": "finish_trial", "arguments": {}}]},
    ])
    backend = ScriptedBackend(script, game_tools=GAME_TOOLS)
    schemas = resolved_benchmark_tools(GAME_TOOLS)
    first = await backend.chat([], schemas)
    second = await backend.chat([], schemas)
    assert [tc["id"] for tc in first.tool_calls] == ["observe:0:0", "observe:0:1"]
    assert [tc["id"] for tc in second.tool_calls] == ["observe:1:0"]
    assert first.tool_calls[0]["arguments"] == '{"a": 1, "b": 2}'
    assert json.loads(first.tool_calls[1]["arguments"]) == {}
    assert first.text is None


def test_batch_reply_matches_declared_calls():
    reply = batch_reply("s", 3, {"calls": [{"name": "get_units", "arguments": {"x": 1}}]})
    assert reply.tool_calls == [{"id": "s:3:0", "name": "get_units", "arguments": '{"x": 1}'}]


@pytest.mark.asyncio
async def test_exhausted_script_raises_rather_than_implicit_finish():
    backend = ScriptedBackend(_script(), game_tools=GAME_TOOLS)
    schemas = resolved_benchmark_tools(GAME_TOOLS)
    await backend.chat([], schemas)
    await backend.chat([], schemas)
    with pytest.raises(ValueError, match="script exhausted"):
        await backend.chat([], schemas)


@pytest.mark.asyncio
async def test_assert_exhausted_passes_only_after_last_batch():
    backend = ScriptedBackend(_script(), game_tools=GAME_TOOLS)
    schemas = resolved_benchmark_tools(GAME_TOOLS)
    with pytest.raises(ValueError):
        backend.assert_exhausted()
    await backend.chat([], schemas)
    with pytest.raises(ValueError):
        backend.assert_exhausted()
    await backend.chat([], schemas)
    backend.assert_exhausted()


@pytest.mark.asyncio
async def test_schema_identity_mismatch_is_rejected():
    backend = ScriptedBackend(_script(), game_tools=GAME_TOOLS)
    # Same names, different order.
    with pytest.raises(ValueError, match="schema"):
        await backend.chat([], resolved_benchmark_tools(("get_cities", "get_units")))
    # An extra tool in the passed schemas.
    with pytest.raises(ValueError, match="schema"):
        await backend.chat([], resolved_benchmark_tools(("get_units", "get_cities", "get_overview")))
    # finish_trial missing.
    with pytest.raises(ValueError, match="schema"):
        await backend.chat([], resolved_benchmark_tools(GAME_TOOLS)[:-1])
    # Rejected calls consume no batch.
    reply = await backend.chat([], resolved_benchmark_tools(GAME_TOOLS))
    assert reply.tool_calls[0]["id"] == "observe:0:0"


def test_unknown_tool_name_rejected_at_construction():
    script = _script(batches=[
        {"calls": [{"name": "move_unit", "arguments": {}}]},
        {"calls": [{"name": "finish_trial", "arguments": {}}]},
    ])
    with pytest.raises(ValueError, match="move_unit"):
        ScriptedBackend(script, game_tools=GAME_TOOLS)


def test_script_cannot_carry_expectations():
    with pytest.raises(ValueError):
        ScriptedBackend(_script(expected={"units": 1}), game_tools=GAME_TOOLS)
