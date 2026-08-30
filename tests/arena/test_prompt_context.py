import pytest

from civ_mcp.arena.briefing import Briefing
from civ_mcp.arena.budget import briefing_budget
from civ_mcp.arena.config import BriefingOptions, CivOptions
from civ_mcp.arena.prompt_context import maybe_build_briefing


@pytest.mark.asyncio
async def test_maybe_build_briefing_returns_supplied_empty_briefing(monkeypatch):
    async def fail_build_briefing(*args, **kwargs):
        raise AssertionError("supplied briefing must be authoritative")

    monkeypatch.setattr(
        "civ_mcp.arena.prompt_context.build_briefing",
        fail_build_briefing,
    )
    supplied = Briefing()
    options = CivOptions(briefing=BriefingOptions(enabled=True))

    result = await maybe_build_briefing(
        object(),
        options,
        n_ctx=8192,
        playbook_chars=0,
        tool_schema_chars=0,
        supplied=supplied,
        surface="arena",
        available_tools=("get_units",),
    )

    assert result is supplied


@pytest.mark.asyncio
async def test_maybe_build_briefing_builds_with_shared_budget(monkeypatch):
    captured = {}

    async def fake_build_briefing(
        gs,
        opts,
        budget,
        *,
        surface,
        available_tools,
    ):
        captured["gs"] = gs
        captured["opts"] = opts
        captured["budget"] = budget
        captured["surface"] = surface
        captured["available_tools"] = available_tools
        return Briefing(text="brief", tokens=1, sections=("overview",), radius=3)

    monkeypatch.setattr(
        "civ_mcp.arena.prompt_context.build_briefing",
        fake_build_briefing,
    )
    gs = object()
    options = CivOptions(
        max_steps=2,
        result_char_cap=600,
        briefing=BriefingOptions(enabled=True, sections=("overview",)),
    )

    result = await maybe_build_briefing(
        gs,
        options,
        n_ctx=8192,
        playbook_chars=400,
        tool_schema_chars=800,
        surface="arena",
        available_tools=("get_units", "activate_great_person"),
    )

    assert result.text == "brief"
    assert captured == {
        "gs": gs,
        "opts": options.briefing,
        "budget": briefing_budget(
            8192,
            options,
            playbook_chars=400,
            tool_schema_chars=800,
        ),
        "surface": "arena",
        "available_tools": ("get_units", "activate_great_person"),
    }


@pytest.mark.asyncio
async def test_maybe_build_briefing_returns_empty_when_disabled(monkeypatch):
    async def fail_build_briefing(*args, **kwargs):
        raise AssertionError("disabled briefing must not be built")

    monkeypatch.setattr(
        "civ_mcp.arena.prompt_context.build_briefing",
        fail_build_briefing,
    )

    result = await maybe_build_briefing(
        object(),
        CivOptions(),
        n_ctx=8192,
        playbook_chars=0,
        tool_schema_chars=0,
        surface="mcp",
    )

    assert result == Briefing()


@pytest.mark.asyncio
async def test_enabled_zero_budget_briefing_records_misconfiguration(monkeypatch, caplog):
    """An enabled briefing must not disappear silently as it did in v8."""
    async def fail_build_briefing(*args, **kwargs):
        raise AssertionError("zero-budget briefing must not query game state")

    monkeypatch.setattr(
        "civ_mcp.arena.prompt_context.build_briefing",
        fail_build_briefing,
    )
    options = CivOptions(
        max_steps=15,
        result_char_cap=6000,
        briefing=BriefingOptions(enabled=True),
    )

    result = await maybe_build_briefing(
        object(),
        options,
        n_ctx=32768,
        playbook_chars=0,
        tool_schema_chars=0,
        surface="arena",
    )

    assert result.text == ""
    assert result.errors == [
        "briefing_budget_zero: n_ctx=32768 max_steps=15 result_char_cap=6000"
    ]
    assert "enabled briefing has zero effective budget" in caplog.text
