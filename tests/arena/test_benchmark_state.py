"""Tests for canonical benchmark-state capture: query, parser, canonicalizer.

`civ_mcp.lua.benchmark` builds the sentinel-framed FireTuner query and parses
its response into JSON-safe primitives; `civ_mcp.arena.benchmark_state`
normalizes (sorts), hashes, and diffs the result so a benchmark trial can
compare "expected" vs "actual" state independent of Lua iteration order.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from civ_mcp.arena.benchmark_state import (
    BenchmarkStateError,
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


def test_state_digest_normalizes_integral_floats_to_ints():
    """F12 repro: json.dumps distinguishes 24 from 24.0 (they really are
    different JSON values), so a hand-authored YAML int (turn: 24)
    permanently mismatches a live-captured float (turn: 24.0) even though
    they represent the identical game state."""
    left = {"turn": 24, "player_id": 0, "units": [], "cities": [], "tiles": []}
    right = {"turn": 24.0, "player_id": 0, "units": [], "cities": [], "tiles": []}
    assert state_digest(left) == state_digest(right)


def test_state_digest_normalizes_integral_floats_nested_in_rows():
    left = {
        "turn": 1, "units": [{"id": 1, "x": 8, "y": 8, "charges": 2}],
        "cities": [], "tiles": [],
    }
    right = {
        "turn": 1, "units": [{"id": 1, "x": 8.0, "y": 8.0, "charges": 2.0}],
        "cities": [], "tiles": [],
    }
    assert state_digest(left) == state_digest(right)


def test_state_digest_still_distinguishes_genuinely_different_non_integral_floats():
    left = {"turn": 1, "gold": 42.5, "units": [], "cities": [], "tiles": []}
    right = {"turn": 1, "gold": 42.6, "units": [], "cities": [], "tiles": []}
    assert state_digest(left) != state_digest(right)


def test_state_digest_uses_ensure_ascii_false_matching_other_canonical_encoders():
    """Cheap fold-in: state_digest's canonical JSON drifted from every
    other canonical encoder in this codebase (benchmark_manifest.
    fingerprint, benchmark_report._canonical_bytes, benchmark_store.
    _canonical_bytes -- all explicitly ensure_ascii=False). state_digest
    omitted it (defaulting to True), so a state containing a non-ASCII
    character would digest differently here than an equivalent canonical
    encoding elsewhere in the pipeline."""
    state = {
        "turn": 1, "player_id": 0, "units": [], "tiles": [],
        "cities": [{"id": 1, "name": "Ки́їв", "x": 0, "y": 0, "population": 1}],
    }
    expected = hashlib.sha256(
        json.dumps(
            normalize_state(state), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    assert state_digest(state) == expected


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


def test_parse_benchmark_state_raises_on_a_corrupt_identity_row():
    """G15 repro: the per-line `except (ValueError, IndexError): continue`
    used to cover the IDENTITY branch too -- a corrupt field there (e.g. a
    non-numeric turn) silently discarded the whole identity row, and the
    resulting state was later misreported by capture_canonical_state as a
    TRUNCATED response (missing identity row entirely) when the row was
    actually present but corrupt. Other row types (UNIT/CITY/TILE) still
    skip-on-corrupt -- see test_parse_benchmark_state_ignores_malformed_lines."""
    from civ_mcp.lua.benchmark import CorruptIdentityRow

    lines = ["IDENTITY|CIVILIZATION_ROME|12345|NOT_A_NUMBER|0|0|42.5|3.0"]
    with pytest.raises(CorruptIdentityRow):
        parse_benchmark_state(lines)


def test_parse_benchmark_state_still_skips_corrupt_unit_rows_after_a_good_identity_row():
    """The IDENTITY-specific fail-loud change must not regress the
    existing skip-on-corrupt behavior for other row types."""
    lines = [
        "IDENTITY|CIVILIZATION_ROME|12345|157|0|0|42.5|3.0",
        "UNIT|not-an-int|X|1|1|0",
    ]
    state = parse_benchmark_state(lines)
    assert state["turn"] == 157
    assert state["units"] == []


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


@pytest.mark.asyncio
async def test_capture_canonical_state_raises_on_err_line_instead_of_hashing_empty_state():
    """A stale/wrong manifest player_id must surface as a clear error, not as
    a near-empty state that silently hashes to "everything differs" later.
    parse_benchmark_state only recognizes IDENTITY/UNIT/CITY/TILE prefixes
    and drops anything else — so without this guard, an ERR: line reaches
    the parser, matches no prefix, and capture_canonical_state would return
    the all-None/empty default state instead of raising."""

    class ErrConnection:
        async def execute_read(self, lua_code, timeout=5.0):
            return ["ERR:PLAYER_NOT_FOUND"]

    with pytest.raises(BenchmarkStateError, match="ERR:PLAYER_NOT_FOUND"):
        await capture_canonical_state(ErrConnection(), player_id=99, tile_coords=[])


@pytest.mark.asyncio
async def test_capture_canonical_state_raises_on_err_line_anywhere_in_response():
    class ErrConnection:
        async def execute_read(self, lua_code, timeout=5.0):
            return ["UNIT|1|UNIT_BUILDER|8|8|2", "ERR:SOMETHING_WENT_WRONG"]

    with pytest.raises(BenchmarkStateError, match="ERR:SOMETHING_WENT_WRONG"):
        await capture_canonical_state(ErrConnection(), player_id=0, tile_coords=[])


@pytest.mark.asyncio
async def test_capture_canonical_state_raises_on_truncated_response_missing_identity():
    """F13 repro: execute_read swallows read timeouts and returns whatever
    lines it collected so far. A truncated/empty response with no ERR:
    line and no IDENTITY row parses (via parse_benchmark_state's all-None
    defaults) to a near-empty "default" state instead of raising --
    hashing that as real game state turns a retryable harness failure into
    a session-killing checksum abort."""

    class TruncatedConnection:
        async def execute_read(self, lua_code, timeout=5.0):
            return ["UNIT|1|UNIT_BUILDER|8|8|2"]  # no IDENTITY line at all

    with pytest.raises(BenchmarkStateError):
        await capture_canonical_state(TruncatedConnection(), player_id=0, tile_coords=[])


@pytest.mark.asyncio
async def test_capture_canonical_state_names_a_corrupt_identity_row_not_truncation():
    """G15: a corrupt (present-but-unparseable) IDENTITY row must surface
    as a distinct, correctly-diagnosed BenchmarkStateError -- not the
    generic "missing identity row" truncation message, which would
    mislead triage toward a network/timeout explanation instead of a
    corrupt Lua data row."""

    class CorruptIdentityConnection:
        async def execute_read(self, lua_code, timeout=5.0):
            return ["IDENTITY|CIVILIZATION_ROME|12345|NOT_A_NUMBER|0|0|42.5|3.0"]

    with pytest.raises(BenchmarkStateError, match="(?i)corrupt.*identity"):
        await capture_canonical_state(CorruptIdentityConnection(), player_id=0, tile_coords=[])


@pytest.mark.asyncio
async def test_capture_canonical_state_raises_on_completely_empty_response():
    class EmptyConnection:
        async def execute_read(self, lua_code, timeout=5.0):
            return []

    with pytest.raises(BenchmarkStateError):
        await capture_canonical_state(EmptyConnection(), player_id=0, tile_coords=[])
