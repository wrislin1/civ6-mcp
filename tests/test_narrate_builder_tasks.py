"""Builder task board narration across the MCP and arena tool surfaces."""

import pytest

from civ_mcp import lua as lq
from civ_mcp.narrate import narrate_builder_tasks


def _task(**overrides) -> lq.BuilderTask:
    fields = {
        "priority": "high",
        "x": 12,
        "y": 19,
        "improvement": "IMPROVEMENT_MINE",
        "resource": "IRON",
        "resource_class": "strategic",
        "city_name": "Capital",
        "nearest_builder_id": 65541,
        "distance": 2,
    }
    fields.update(overrides)
    return lq.BuilderTask(**fields)


def _builder(**overrides) -> lq.BuilderInfo:
    fields = {
        "unit_id": 65541,
        "unit_index": 5,
        "x": 10,
        "y": 18,
        "charges": 2,
        "moves": 2,
    }
    fields.update(overrides)
    return lq.BuilderInfo(**fields)


def test_default_mcp_surface_renders_callable_mcp_form():
    board = narrate_builder_tasks([_task()], [_builder()])

    assert "improve_tile" not in board
    assert "repair_improvement" not in board
    assert "unit_index" not in board
    assert "nearest builder id:65541, 2 tiles" in board
    assert (
        'on that tile call unit_action(unit_id=65541, action="improve")'
        in board
    )


def test_explicit_mcp_surface_renders_callable_repair_form():
    board = narrate_builder_tasks(
        [_task(resource_class="pillaged")],
        [_builder()],
        surface="mcp",
    )

    assert "repair IRON" in board
    assert "IMPROVEMENT_MINE" not in board
    assert (
        'on that tile call unit_action(unit_id=65541, action="repair")'
        in board
    )


def test_arena_surface_hints_the_call_with_the_full_improvement_name():
    board = narrate_builder_tasks([_task()], [_builder()], surface="arena")

    assert "build MINE" in board
    assert (
        'call improve_tile with {"unit_index": 5, '
        '"improvement_name": "IMPROVEMENT_MINE"}' in board
    )
    assert "unit_index:5" in board


def test_arena_surface_skips_the_hint_for_an_unmapped_improvement():
    # The Lua scan emits UNKNOWN for a resource with no mapped improvement;
    # improve_tile would answer ERR:IMPROVEMENT_NOT_FOUND.
    board = narrate_builder_tasks(
        [_task(improvement="UNKNOWN", resource="ANTIQUITY")],
        [_builder()],
        surface="arena",
    )

    assert "call improve_tile" not in board
    assert "nearest builder id:65541" in board


def test_arena_surface_skips_the_hint_for_a_builder_with_no_moves():
    board = narrate_builder_tasks(
        [_task(resource_class="pillaged", nearest_builder_id=65542)],
        [_builder(unit_id=65542, unit_index=6, moves=0)],
        surface="arena",
    )

    assert "call repair_improvement" not in board
    assert "BUSY BUILDERS (1):" in board


def test_mcp_surface_skips_the_hint_for_a_builder_with_no_moves():
    board = narrate_builder_tasks(
        [_task(nearest_builder_id=65542)],
        [_builder(unit_id=65542, unit_index=6, moves=0)],
        surface="mcp",
    )

    assert "call unit_action" not in board


def test_invalid_surface_is_rejected():
    with pytest.raises(ValueError, match="surface"):
        narrate_builder_tasks([_task()], [_builder()], surface="browser")
