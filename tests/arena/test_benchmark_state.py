"""Tests for canonical benchmark-state capture: query, parser, canonicalizer.

`civ_mcp.lua.benchmark` builds the sentinel-framed FireTuner query and parses
its response into JSON-safe primitives; `civ_mcp.arena.benchmark_state`
normalizes (sorts), hashes, and diffs the result so a benchmark trial can
compare "expected" vs "actual" state independent of Lua iteration order.
"""
from __future__ import annotations

import pytest

from civ_mcp.arena.benchmark_state import (
    capture_canonical_state,
    diff_state,
    normalize_state,
    state_digest,
)
from civ_mcp.lua.benchmark import build_benchmark_state_query, parse_benchmark_state


def test_state_digest_is_order_independent_but_changes_for_declared_tile():
    unit_1 = {"id": 1, "type": "UNIT_BUILDER", "x": 8, "y": 8, "charges": 2}
    unit_2 = {"id": 2, "type": "UNIT_BUILDER", "x": 9, "y": 8, "charges": 1}
    mined = {"x": 9, "y": 8, "improvement": "IMPROVEMENT_MINE",
             "feature": None, "resource": "RESOURCE_IRON", "pillaged": False}
    left = {"turn": 157, "player_id": 0, "units": [unit_2, unit_1], "cities": [],
            "tiles": [mined]}
    right = {"turn": 157, "player_id": 0, "units": [unit_1, unit_2], "cities": [],
             "tiles": [mined]}
    assert state_digest(left) == state_digest(right)

    changed = {**right, "tiles": [{**mined, "improvement": None}]}
    assert state_digest(left) != state_digest(changed)
    assert diff_state(left, changed)["tiles[9,8].improvement"] == ["IMPROVEMENT_MINE", None]


def test_normalize_state_leaves_non_list_fields_untouched():
    state = {"turn": 157, "player_id": 0, "gold": 12.5, "units": [], "cities": [],
              "tiles": []}
    assert normalize_state(state) == state


def test_diff_state_reports_scalar_top_level_changes_only():
    left = {"turn": 157, "units": [], "cities": [], "tiles": []}
    right = {"turn": 158, "units": [], "cities": [], "tiles": []}
    assert diff_state(left, right) == {"turn": [157, 158]}


def test_diff_state_reports_added_and_removed_rows_without_field_breakdown():
    scout = {"id": 1, "type": "UNIT_SCOUT", "x": 1, "y": 1, "charges": 0}
    left = {"units": [scout]}
    right = {"units": []}
    assert diff_state(left, right) == {"units[1]": [scout, None]}
    assert diff_state(right, left) == {"units[1]": [None, scout]}


def test_build_benchmark_state_query_frames_output_and_scopes_declared_tiles():
    lua = build_benchmark_state_query(0, [(8, 8), (9, 8)])
    assert lua.strip().endswith('print("---END---")')
    assert "{8,8}" in lua
    assert "{9,8}" in lua
    assert "local pid = 0" in lua


def test_parse_benchmark_state_extracts_identity_units_cities_and_tiles():
    lines = [
        "IDENTITY|CIVILIZATION_ROME|12345|157|0|0|42.5|3.0",
        "UNIT|2|UNIT_BUILDER|9|8|1",
        "UNIT|1|UNIT_BUILDER|8|8|2",
        "CITY|10|Roma|7|7|4",
        "TILE|9|8|IMPROVEMENT_MINE|NONE|RESOURCE_IRON|false",
    ]
    state = parse_benchmark_state(lines)

    assert state["civ_type"] == "CIVILIZATION_ROME"
    assert state["seed"] == 12345
    assert state["turn"] == 157
    assert state["active_player"] == 0
    assert state["player_id"] == 0
    assert state["gold"] == 42.5
    assert state["faith"] == 3.0
    assert state["units"] == [
        {"id": 2, "type": "UNIT_BUILDER", "x": 9, "y": 8, "charges": 1},
        {"id": 1, "type": "UNIT_BUILDER", "x": 8, "y": 8, "charges": 2},
    ]
    assert state["cities"] == [{"id": 10, "name": "Roma", "x": 7, "y": 7, "population": 4}]
    assert state["tiles"] == [
        {"x": 9, "y": 8, "improvement": "IMPROVEMENT_MINE", "feature": None,
         "resource": "RESOURCE_IRON", "pillaged": False},
    ]


def test_parse_benchmark_state_ignores_malformed_lines():
    state = parse_benchmark_state(["GARBAGE", "UNIT|not-an-int|X|1|1|0", "TILE|1|1"])
    assert state["units"] == []
    assert state["tiles"] == []


@pytest.mark.asyncio
async def test_capture_canonical_state_queries_parses_and_normalizes():
    class FakeConnection:
        def __init__(self):
            self.sent: list[str] = []

        async def execute_read(self, lua_code, timeout=5.0):
            self.sent.append(lua_code)
            return [
                "IDENTITY|CIVILIZATION_ROME|1|157|0|0|10.0|1.0",
                "UNIT|2|UNIT_BUILDER|9|8|1",
                "UNIT|1|UNIT_BUILDER|8|8|2",
            ]

    fake = FakeConnection()
    state = await capture_canonical_state(fake, player_id=0, tile_coords=[])

    assert len(fake.sent) == 1
    assert "local pid = 0" in fake.sent[0]
    assert [u["id"] for u in state["units"]] == [1, 2]
    assert state["turn"] == 157
