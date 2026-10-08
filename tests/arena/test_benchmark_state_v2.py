"""Grammar tests for the version-2 single-execution benchmark capture.

Every parser input below is a literal, hand-written wire string authored from
the grammar table in the Plan 3 Part 1 brief (Task 2). None of it is produced
by `build_benchmark_state_query_v2` or by any serializer that shares the
parser's field definitions. The only computed token is the BEGIN coverage
digest, which is `document_digest(coverage)` by definition.

The Lua query builder cannot run offline, so it is tested structurally.
"""
from __future__ import annotations

import copy
import random
import re

import pytest

from civ_mcp.arena import benchmark_contract_v2 as c2
from civ_mcp.arena.benchmark_contract_v2 import document_digest
from civ_mcp.arena.benchmark_state import BenchmarkStateError, state_digest
from civ_mcp.arena.benchmark_state_v2 import (
    digest_state_v2,
    normalize_state_v2,
    parse_state_v2,
    require_complete,
)


def test_truncated_capture_is_not_an_empty_world():
    coverage = {"include_owned_tiles": True, "area": [], "tracked_targets": []}
    raw = (f"BEGIN|2.0.0|{document_digest(coverage)}\n"
           "IDENTITY|CIVILIZATION_KOREA|7|100|0|0|100|0\n")
    with pytest.raises(BenchmarkStateError, match="incomplete"):
        parse_state_v2(raw, coverage=coverage)


# ---------------------------------------------------------------------------
# Literal fixture
# ---------------------------------------------------------------------------

COVERAGE = {
    "include_owned_tiles": True,
    "area": [[10, 10], [11, 10]],
    "tracked_targets": [[1, 70001]],
}

IDENTITY = "IDENTITY|CIVILIZATION_KOREA|7|100|0|0|100.5|12"
UNIT = "UNIT|0|131073|1|UNIT_BUILDER|civilian|10|10|100|100|2|3"
TARGET = "TARGET|1|70001|1|combat|1|1|alive_visible|11|10|90.5|100"
CITY = "CITY|0|65536|Seoul|9|10|4|5.5"
BUILDING = "BUILDING|0|65536|BUILDING_MONUMENT|1|0"
DISTRICT = "DISTRICT|0|65536|7|DISTRICT_CITY_CENTER|9|10|1|0"
QUEUE = "QUEUE|0|65536|NONE|NONE|0|~|~"
TILE_CITY = "TILE|9|10|0|TERRAIN_PLAINS|NONE|NONE|NONE|0|DISTRICT_CITY_CENTER|1|2|2|1|0|0|0"
TILE_FARM = "TILE|10|10|0|TERRAIN_GRASS|NONE|RESOURCE_WHEAT|IMPROVEMENT_FARM|0|NONE|1|3|1|0|0|0|0"
TILE_FOREST = "TILE|11|10|-1|TERRAIN_PLAINS|FEATURE_FOREST|NONE|NONE|0|NONE|1|1|2|0|0|0|0"
RESOURCE = "RESOURCE|RESOURCE_IRON|1|3|~"
END = "END|2.0.0|1|1|1|1|1|1|1|3|1"

BODY = [IDENTITY, UNIT, TARGET, CITY, BUILDING, DISTRICT, QUEUE,
        TILE_CITY, TILE_FARM, TILE_FOREST, RESOURCE, END]


def wire(lines, coverage=COVERAGE, digest=None):
    head = f"BEGIN|2.0.0|{digest or document_digest(coverage)}"
    return "\n".join([head, *lines]) + "\n"


def replace(old, new, lines=BODY):
    assert old in lines
    return [new if line == old else line for line in lines]


def parse(lines, coverage=COVERAGE):
    return parse_state_v2(wire(lines, coverage), coverage=coverage)


def rejects(lines, match, coverage=COVERAGE):
    with pytest.raises(BenchmarkStateError, match=match):
        parse(lines, coverage)


EXPECTED = {
    "wire_version": "2.0.0",
    "civ_type": "CIVILIZATION_KOREA",
    "seed": 7,
    "turn": 100,
    "active_player": 0,
    "player_id": 0,
    "gold": 100.5,
    "faith": 12,
    "units": [{
        "owner": 0, "id": 131073, "unit_index": 1, "type": "UNIT_BUILDER",
        "role": "civilian", "x": 10, "y": 10, "hp": 100, "max_hp": 100,
        "moves": 2, "charges": 3,
    }],
    "targets": [{
        "owner": 1, "id": 70001, "tracked": True, "role": "combat",
        "hostile": True, "visible": True, "status": "alive_visible",
        "x": 11, "y": 10, "hp": 90.5, "max_hp": 100,
    }],
    "cities": [{
        "owner": 0, "id": 65536, "name": "Seoul", "x": 9, "y": 10,
        "population": 4, "housing": 5.5,
        "buildings": [{"building_type": "BUILDING_MONUMENT", "present": True,
                       "pillaged": False}],
        "districts": [{"district_id": 7, "district_type": "DISTRICT_CITY_CENTER",
                       "x": 9, "y": 10, "complete": True, "pillaged": False}],
        "queue": {"item_kind": "NONE", "item_type": "NONE", "repair": False,
                  "target_x": None, "target_y": None},
    }],
    "tiles": [
        {"x": 9, "y": 10, "owner": 0, "terrain": "TERRAIN_PLAINS",
         "feature": "NONE", "resource": "NONE", "improvement": "NONE",
         "pillaged": False, "district": "DISTRICT_CITY_CENTER", "visible": True,
         "yields": {"food": 2, "production": 2, "gold": 1, "science": 0,
                    "culture": 0, "faith": 0}},
        {"x": 10, "y": 10, "owner": 0, "terrain": "TERRAIN_GRASS",
         "feature": "NONE", "resource": "RESOURCE_WHEAT",
         "improvement": "IMPROVEMENT_FARM", "pillaged": False, "district": "NONE",
         "visible": True,
         "yields": {"food": 3, "production": 1, "gold": 0, "science": 0,
                    "culture": 0, "faith": 0}},
        {"x": 11, "y": 10, "owner": -1, "terrain": "TERRAIN_PLAINS",
         "feature": "FEATURE_FOREST", "resource": "NONE", "improvement": "NONE",
         "pillaged": False, "district": "NONE", "visible": True,
         "yields": {"food": 1, "production": 2, "gold": 0, "science": 0,
                    "culture": 0, "faith": 0}},
    ],
    "resources": [{"resource_type": "RESOURCE_IRON", "access": True,
                   "stock": 3, "flow": None}],
    "coverage": {"include_owned_tiles": True, "area": [[10, 10], [11, 10]],
                 "tracked_targets": [[1, 70001]]},
    "row_counts": {"identity": 1, "unit": 1, "target": 1, "city": 1,
                   "building": 1, "district": 1, "queue": 1, "tile": 3,
                   "resource": 1},
}


# ---------------------------------------------------------------------------
# Positive literal rows
# ---------------------------------------------------------------------------

def test_complete_literal_capture_parses_every_family():
    assert parse(BODY) == EXPECTED


def test_parsed_types_are_exact():
    state = parse(BODY)
    assert type(state["faith"]) is int  # "12" -> int
    assert type(state["gold"]) is float
    assert type(state["units"][0]["hp"]) is int  # integral n canonicalised
    assert state["targets"][0]["tracked"] is True
    assert state["cities"][0]["queue"]["repair"] is False
    assert list(state["tiles"][0]["yields"]) == [
        "food", "production", "gold", "science", "culture", "faith"]


def test_active_queue_with_target_parses():
    q = "QUEUE|0|65536|DISTRICT|DISTRICT_CAMPUS|1|10|11"
    state = parse(replace(QUEUE, q))
    assert state["cities"][0]["queue"] == {
        "item_kind": "DISTRICT", "item_type": "DISTRICT_CAMPUS", "repair": True,
        "target_x": 10, "target_y": 11}


def test_escaped_string_decodes_once():
    city = "CITY|0|65536|A%25B%7CC%0D%0AD%7EE%2541|9|10|4|5.5"
    state = parse(replace(CITY, city))
    assert state["cities"][0]["name"] == "A%B|C\r\nD~E%41"


def test_utf8_name_is_kept():
    state = parse(replace(CITY, "CITY|0|65536|Hanseong 漢城|9|10|4|5.5"))
    assert state["cities"][0]["name"] == "Hanseong 漢城"


def test_resource_with_unavailable_quantities():
    state = parse(replace(RESOURCE, "RESOURCE|RESOURCE_SILK|0|~|~"))
    assert state["resources"] == [{"resource_type": "RESOURCE_SILK",
                                   "access": False, "stock": None, "flow": None}]


def test_round_trip_decimal_precision():
    ident = "IDENTITY|CIVILIZATION_KOREA|7|100|0|0|0.10000000000000001|1e3"
    state = parse(replace(IDENTITY, ident))
    assert state["gold"] == 0.1
    assert state["faith"] == 1000 and type(state["faith"]) is int


def test_hidden_tracked_target():
    t = "TARGET|1|70001|1|~|1|0|alive_not_visible|~|~|~|~"
    state = parse(replace(TARGET, t))
    assert state["targets"] == [{
        "owner": 1, "id": 70001, "tracked": True, "role": None, "hostile": True,
        "visible": False, "status": "alive_not_visible",
        "x": None, "y": None, "hp": None, "max_hp": None}]


def test_destroyed_tracked_target():
    t = "TARGET|1|70001|1|~|1|0|destroyed|~|~|~|~"
    state = parse(replace(TARGET, t))
    assert state["targets"][0]["status"] == "destroyed"
    assert state["targets"][0]["visible"] is False
    assert state["targets"][0]["x"] is None


def test_untracked_nearby_visible_hostile():
    nearby = "TARGET|63|9|0|combat|1|1|alive_visible|10|9|100|100"
    lines = replace(TARGET, TARGET + "\n" + nearby)
    lines = replace(END, "END|2.0.0|1|1|2|1|1|1|1|3|1", lines)
    state = parse(lines)
    assert [(t["owner"], t["id"], t["tracked"]) for t in state["targets"]] == [
        (1, 70001, True), (63, 9, False)]


def test_area_only_coverage_without_owned_tiles():
    coverage = {"include_owned_tiles": False, "area": [[10, 10], [11, 10]],
                "tracked_targets": [[1, 70001]]}
    lines = [line for line in BODY if line != TILE_CITY]
    lines = replace(END, "END|2.0.0|1|1|1|1|1|1|1|2|1", lines)
    state = parse(lines, coverage)
    assert [(t["x"], t["y"]) for t in state["tiles"]] == [(10, 10), (11, 10)]


def test_coverage_tuples_digest_like_lists():
    coverage = {"include_owned_tiles": True, "area": [(10, 10), (11, 10)],
                "tracked_targets": [(1, 70001)]}
    assert parse(BODY, coverage)["coverage"] == EXPECTED["coverage"]


def test_require_complete_body():
    counts = {"identity": 1, "unit": 0}
    require_complete(dict(counts), dict(counts))
    with pytest.raises(BenchmarkStateError, match="incomplete"):
        require_complete({"identity": 1, "unit": 1}, counts)
    with pytest.raises(BenchmarkStateError, match="incomplete"):
        require_complete({"identity": 0}, {"identity": 0})


# ---------------------------------------------------------------------------
# Framing, counts, ordering
# ---------------------------------------------------------------------------

def test_missing_end_is_incomplete():
    rejects(BODY[:-1], "incomplete")


def test_missing_identity_is_incomplete():
    lines = [line for line in BODY if line != IDENTITY]
    rejects(replace(END, "END|2.0.0|0|1|1|1|1|1|1|3|1", lines), "incomplete")
    rejects(lines, "incomplete")


def test_duplicate_identity_rejected():
    rejects([IDENTITY, *BODY], "IDENTITY")


def test_count_mismatch_is_incomplete():
    rejects(replace(END, "END|2.0.0|1|2|1|1|1|1|1|3|1"), "incomplete")
    rejects(replace(END, "END|2.0.0|1|1|1|1|1|1|1|4|1"), "incomplete")


def test_dropped_row_is_incomplete_even_with_matching_shape():
    rejects([line for line in BODY if line != BUILDING], "incomplete")


def test_missing_begin_rejected():
    with pytest.raises(BenchmarkStateError, match="BEGIN"):
        parse_state_v2("\n".join(BODY), coverage=COVERAGE)


def test_wrong_version_rejected():
    raw = wire(BODY).replace("BEGIN|2.0.0|", "BEGIN|1.0.0|")
    with pytest.raises(BenchmarkStateError, match="version"):
        parse_state_v2(raw, coverage=COVERAGE)
    rejects(replace(END, "END|2.0.1|1|1|1|1|1|1|1|3|1"), "version")


def test_coverage_digest_mismatch_rejected():
    other = dict(COVERAGE, include_owned_tiles=False)
    with pytest.raises(BenchmarkStateError, match="coverage"):
        parse_state_v2(wire(BODY, other), coverage=COVERAGE)


def test_output_after_end_rejected():
    rejects([*BODY, RESOURCE], "after END")
    rejects([*BODY, "---END---"], "after END")


def test_family_order_enforced():
    lines = list(BODY)
    lines.remove(TILE_FARM)
    lines.insert(lines.index(CITY), TILE_FARM)
    rejects(lines, "order")


def test_unknown_tag_rejected():
    rejects(replace(RESOURCE, "WONDER|RESOURCE_IRON|1|3|~"), "unknown")
    rejects(replace(RESOURCE, ""), "unknown")


def test_error_line_fails_capture():
    with pytest.raises(BenchmarkStateError, match="ERR:V2_CAPTURE"):
        parse_state_v2(wire([IDENTITY, "ERR:V2_CAPTURE|boom"]), coverage=COVERAGE)


def test_empty_capture_rejected():
    with pytest.raises(BenchmarkStateError):
        parse_state_v2("", coverage=COVERAGE)


# ---------------------------------------------------------------------------
# Field syntax
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row", [
    "UNIT|0|131073|1|UNIT_BUILDER|civilian|10|10|100|100|2",
    "UNIT|0|131073|1|UNIT_BUILDER|civilian|10|10|100|100|2|3|9",
])
def test_row_arity_rejected(row):
    rejects(replace(UNIT, row), "fields")


@pytest.mark.parametrize("charges", ["true", "false", "1.0", "+1", "", "0x1", " 1"])
def test_integer_field_syntax(charges):
    rejects(replace(UNIT, f"UNIT|0|131073|1|UNIT_BUILDER|civilian|10|10|100|100|2|{charges}"),
            "integer")


@pytest.mark.parametrize("gold", ["nan", "inf", "-inf", "1e999", "NaN", "-nan(ind)",
                                  "true", "", "1,5"])
def test_number_field_must_be_finite_decimal(gold):
    rejects(replace(IDENTITY, f"IDENTITY|CIVILIZATION_KOREA|7|100|0|0|{gold}|12"),
            "number")


@pytest.mark.parametrize("flag", ["2", "true", "", "~"])
def test_boolean_field_syntax(flag):
    rejects(replace(BUILDING, f"BUILDING|0|65536|BUILDING_MONUMENT|{flag}|0"),
            "boolean|unavailable")


@pytest.mark.parametrize("row", [
    "UNIT|0|131073|1|UNIT_BUILDER|civilian|10|10|~|100|2|3",
    "CITY|0|65536|~|9|10|4|5.5",
    "TILE|10|10|0|TERRAIN_GRASS|NONE|RESOURCE_WHEAT|IMPROVEMENT_FARM|0|NONE|1|3|~|0|0|0|0",
])
def test_unavailable_marker_only_in_optional_fields(row):
    family = row.split("|")[0]
    old = {"UNIT": UNIT, "CITY": CITY, "TILE": TILE_FARM}[family]
    rejects(replace(old, row), "unavailable")


@pytest.mark.parametrize("name", ["A%41", "A%", "A%7", "A%7c", "A~B", "A\rB"])
def test_bad_string_escapes_rejected(name):
    rejects(replace(CITY, f"CITY|0|65536|{name}|9|10|4|5.5"), "escape|unescaped")


# ---------------------------------------------------------------------------
# Keys, references, ownership, coverage
# ---------------------------------------------------------------------------

def test_duplicate_unit_rejected():
    lines = replace(UNIT, UNIT + "\n" + UNIT)
    rejects(replace(END, "END|2.0.0|1|2|1|1|1|1|1|3|1", lines), "duplicate")


def test_duplicate_tile_rejected():
    lines = replace(TILE_FARM, TILE_FARM + "\n" + TILE_FARM)
    rejects(replace(END, "END|2.0.0|1|1|1|1|1|1|1|4|1", lines), "duplicate")


def test_duplicate_queue_rejected():
    lines = replace(QUEUE, QUEUE + "\n" + QUEUE)
    rejects(replace(END, "END|2.0.0|1|1|1|1|1|1|2|3|1", lines), "duplicate")


def test_duplicate_resource_rejected():
    lines = replace(RESOURCE, RESOURCE + "\n" + RESOURCE)
    rejects(replace(END, "END|2.0.0|1|1|1|1|1|1|1|3|2", lines), "duplicate")


def test_dangling_city_references_rejected():
    rejects(replace(BUILDING, "BUILDING|0|99|BUILDING_MONUMENT|1|0"), "city")
    rejects(replace(DISTRICT, "DISTRICT|0|99|7|DISTRICT_CITY_CENTER|9|10|1|0"), "city")
    rejects(replace(QUEUE, "QUEUE|0|99|NONE|NONE|0|~|~"), "city")


def test_city_without_queue_rejected():
    lines = [line for line in BODY if line != QUEUE]
    rejects(replace(END, "END|2.0.0|1|1|1|1|1|1|0|3|1", lines), "no QUEUE")


def test_owned_rows_must_belong_to_player():
    rejects(replace(UNIT, "UNIT|1|131073|1|UNIT_BUILDER|civilian|10|10|100|100|2|3"),
            "owner")


def test_area_tile_missing_rejected():
    lines = [line for line in BODY if line != TILE_FOREST]
    rejects(replace(END, "END|2.0.0|1|1|1|1|1|1|1|2|1", lines), "area")


def test_owned_tile_outside_scope_rejected():
    coverage = {"include_owned_tiles": False, "area": [[10, 10], [11, 10]],
                "tracked_targets": [[1, 70001]]}
    rejects(BODY, "scope", coverage)


def test_unknown_coverage_key_rejected():
    bad = dict(COVERAGE, radius=3)
    with pytest.raises(BenchmarkStateError, match="coverage"):
        parse_state_v2(wire(BODY, bad), coverage=bad)


@pytest.mark.parametrize("bad", [
    {"include_owned_tiles": True, "area": []},
    {"include_owned_tiles": 1, "area": [], "tracked_targets": []},
    {"include_owned_tiles": True, "area": [[1]], "tracked_targets": []},
    {"include_owned_tiles": True, "area": [[True, 1]], "tracked_targets": []},
    {"include_owned_tiles": True, "area": [[1, 1], [1, 1]], "tracked_targets": []},
    {"include_owned_tiles": True, "area": [], "tracked_targets": [[1, 2.0]]},
])
def test_malformed_coverage_rejected(bad):
    with pytest.raises(BenchmarkStateError, match="coverage"):
        parse_state_v2(wire(BODY, bad), coverage=bad)


# ---------------------------------------------------------------------------
# Target visibility semantics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row", [
    # visible targets require role, coordinates and health
    "TARGET|1|70001|1|~|1|1|alive_visible|11|10|90|100",
    "TARGET|1|70001|1|combat|1|1|alive_visible|~|10|90|100",
    "TARGET|1|70001|1|combat|1|1|alive_visible|11|10|~|100",
    "TARGET|1|70001|1|combat|1|0|alive_visible|11|10|90|100",
    # hidden targets carry no position/health
    "TARGET|1|70001|1|~|1|0|alive_not_visible|11|10|~|~",
    "TARGET|1|70001|1|~|1|1|alive_not_visible|~|~|~|~",
    # destroyed: visibility 0 and null position/health
    "TARGET|1|70001|1|~|1|1|destroyed|~|~|~|~",
    "TARGET|1|70001|1|~|1|0|destroyed|~|~|0|100",
    "TARGET|1|70001|1|~|1|0|dead|~|~|~|~",
])
def test_target_visibility_semantics(row):
    rejects(replace(TARGET, row), "target")


def test_tracked_target_without_row_rejected():
    lines = [line for line in BODY if line != TARGET]
    rejects(replace(END, "END|2.0.0|1|1|0|1|1|1|1|3|1", lines), "tracked")


def test_tracked_flag_must_match_coverage():
    rejects(replace(TARGET, "TARGET|1|70001|0|combat|1|1|alive_visible|11|10|90|100"),
            "tracked")
    rejects(replace(TARGET, "TARGET|1|70002|1|combat|1|1|alive_visible|11|10|90|100"),
            "tracked")


@pytest.mark.parametrize("row", [
    "TARGET|63|9|0|combat|0|1|alive_visible|10|9|100|100",
    "TARGET|63|9|0|~|1|0|alive_not_visible|~|~|~|~",
    "TARGET|63|9|0|~|1|0|destroyed|~|~|~|~",
])
def test_untracked_target_must_be_visible_hostile(row):
    lines = replace(TARGET, TARGET + "\n" + row)
    rejects(replace(END, "END|2.0.0|1|1|2|1|1|1|1|3|1", lines), "target")


def test_duplicate_target_rejected():
    lines = replace(TARGET, TARGET + "\n" + TARGET)
    rejects(replace(END, "END|2.0.0|1|1|2|1|1|1|1|3|1", lines), "duplicate")


# ---------------------------------------------------------------------------
# Normalisation and digests
# ---------------------------------------------------------------------------

RICH_BODY = [
    IDENTITY,
    "UNIT|0|131073|1|UNIT_BUILDER|civilian|10|10|100|100|2|3",
    "UNIT|0|131074|2|UNIT_WARRIOR|combat|9|10|80|100|1|0",
    TARGET,
    "TARGET|63|9|0|combat|1|1|alive_visible|10|9|100|100",
    "CITY|0|65536|Seoul|9|10|4|5.5",
    "CITY|0|65537|Busan|14|12|2|3",
    "BUILDING|0|65536|BUILDING_MONUMENT|1|0",
    "BUILDING|0|65536|BUILDING_GRANARY|1|1",
    "BUILDING|0|65537|BUILDING_PALACE|1|0",
    "DISTRICT|0|65536|7|DISTRICT_CITY_CENTER|9|10|1|0",
    "DISTRICT|0|65536|8|DISTRICT_CAMPUS|10|11|0|0",
    "DISTRICT|0|65537|9|DISTRICT_CITY_CENTER|14|12|1|0",
    "QUEUE|0|65536|DISTRICT|DISTRICT_CAMPUS|0|10|11",
    "QUEUE|0|65537|UNIT|UNIT_SETTLER|0|~|~",
    TILE_CITY, TILE_FARM, TILE_FOREST,
    "RESOURCE|RESOURCE_IRON|1|3|~",
    "RESOURCE|RESOURCE_SILK|0|~|~",
    "RESOURCE|RESOURCE_WHEAT|0|~|~",
    "END|2.0.0|1|2|2|2|3|3|2|3|3",
]


def _shuffled(lines, seed):
    """Shuffle rows within each family (families keep their order)."""
    rng = random.Random(seed)
    groups: dict[str, list[str]] = {}
    for line in lines[1:-1]:
        groups.setdefault(line.split("|")[0], []).append(line)
    out = [lines[0]]
    for rows in groups.values():
        rows = list(rows)
        rng.shuffle(rows)
        out.extend(rows)
    return out + [lines[-1]]


def test_row_order_does_not_change_digest():
    base = digest_state_v2(parse(RICH_BODY))
    for seed in range(5):
        shuffled = _shuffled(RICH_BODY, seed)
        assert digest_state_v2(parse(shuffled)) == base


def test_shuffled_input_state_normalises_identically():
    state = parse(RICH_BODY)
    scrambled = copy.deepcopy(state)
    rng = random.Random(3)
    for key in ("units", "targets", "cities", "tiles", "resources"):
        rng.shuffle(scrambled[key])
    for city in scrambled["cities"]:
        city["buildings"].reverse()
        city["districts"].reverse()
    scrambled["coverage"]["area"].reverse()
    assert normalize_state_v2(scrambled) == normalize_state_v2(state)
    assert digest_state_v2(scrambled) == digest_state_v2(state)


def test_normalisation_is_idempotent_and_digest_is_document_digest():
    state = parse(RICH_BODY)
    once = normalize_state_v2(state)
    assert normalize_state_v2(once) == once
    assert digest_state_v2(state) == document_digest(once)


def test_integral_numbers_digest_identically():
    a = parse(RICH_BODY)
    b = parse(replace(
        "UNIT|0|131074|2|UNIT_WARRIOR|combat|9|10|80|100|1|0",
        "UNIT|0|131074|2|UNIT_WARRIOR|combat|9|10|80.0|100.000|1.0|0",
        RICH_BODY))
    assert digest_state_v2(a) == digest_state_v2(b)
    hand = copy.deepcopy(a)
    hand["units"][0]["hp"] = 100.0
    assert digest_state_v2(hand) == digest_state_v2(a)


def test_field_change_changes_digest():
    a = parse(RICH_BODY)
    b = parse(replace(TILE_FARM, TILE_FARM.replace("|3|1|0|0|0|0", "|3|1|0|0|0|1"),
                      RICH_BODY))
    assert digest_state_v2(a) != digest_state_v2(b)


def test_v1_state_digest_on_normalised_v2_state_is_order_stable():
    base = normalize_state_v2(parse(RICH_BODY))
    for seed in range(5):
        other = normalize_state_v2(parse(_shuffled(RICH_BODY, seed)))
        assert state_digest(other) == state_digest(base)
    assert state_digest(normalize_state_v2(base)) == state_digest(base)


def test_normalise_rejects_missing_metadata():
    state = parse(BODY)
    for key in ("coverage", "row_counts", "turn", "tiles"):
        broken = copy.deepcopy(state)
        del broken[key]
        with pytest.raises(BenchmarkStateError, match=key):
            normalize_state_v2(broken)
    broken = copy.deepcopy(state)
    del broken["cities"][0]["queue"]
    with pytest.raises(BenchmarkStateError, match="queue"):
        normalize_state_v2(broken)


def test_state_holds_no_timing_or_transport_data():
    keys = set(parse(BODY))
    assert not {k for k in keys if re.search(r"time|elapsed|latency|_s$", k)}


# ---------------------------------------------------------------------------
# Query builder (structural; Lua is not executable offline)
# ---------------------------------------------------------------------------

def _query(player_id=3, coverage=COVERAGE):
    from civ_mcp.lua.benchmark_v2 import build_benchmark_state_query_v2
    return build_benchmark_state_query_v2(player_id, coverage)


def test_query_embeds_player_and_coverage_digest():
    q = _query()
    assert "local PID = 3\n" in q
    assert document_digest(COVERAGE) in q
    assert "{10, 10}" in q and "{11, 10}" in q
    assert "{1, 70001}" in q
    assert "local INCLUDE_OWNED_TILES = true" in q
    assert "local INCLUDE_OWNED_TILES = false" in _query(
        coverage=dict(COVERAGE, include_owned_tiles=False))


def test_query_emits_every_family_in_wire_order():
    from civ_mcp.lua._helpers import SENTINEL
    q = _query()
    # Rows are buffered per family and printed in ORDER between BEGIN and END.
    positions = [q.index(tag) for tag in (
        'emit("IDENTITY"', 'emit("UNIT"', 'emit("TARGET"', 'emit("CITY"',
        'emit("BUILDING"', 'emit("DISTRICT"', 'emit("QUEUE"', 'emit("TILE"',
        'emit("RESOURCE"')]
    assert positions == sorted(positions)
    assert ('local ORDER = {"IDENTITY", "UNIT", "TARGET", "CITY", "BUILDING", '
            '"DISTRICT", "QUEUE", "TILE", "RESOURCE"}') in q
    framing = [q.index(s) for s in (
        'print("BEGIN|2.0.0|', "for _, tag in ipairs(ORDER) do\n        for _, line",
        'print("END|2.0.0|', f'print("{SENTINEL}")')]
    assert framing == sorted(framing)
    assert q.count(f'print("{SENTINEL}")') == 1
    assert q.count('print("END|') == 1


def test_query_uses_wire_escapes_and_round_trip_numbers():
    q = _query()
    escapes = ['"%%", "%%25"', '"|", "%%7C"', '"\\r", "%%0D"', '"\\n", "%%0A"',
               '"~", "%%7E"']
    positions = [q.index(e) for e in escapes]
    assert positions == sorted(positions)  # % is escaped first
    assert 'string.format("%.17g", v)' in q
    assert 'string.format("%.0f", v)' in q


def test_query_is_one_gamecore_program_without_follow_up():
    q = _query()
    for ingame_only in ("GetCurrentProductionTypeHash", "UnitManager", "CityManager",
                        "execute", "UI.", "LuaEvents"):
        assert ingame_only not in q
    assert "CurrentlyBuilding()" in q
    assert "pcall(capture)" in q
    # A failed read prints an error instead of END.
    assert 'print("ERR:V2_CAPTURE|"' in q


@pytest.mark.parametrize("player_id", [True, -1, "0", 1.0])
def test_query_rejects_bad_player_id(player_id):
    with pytest.raises((TypeError, ValueError)):
        _query(player_id=player_id)


def test_query_rejects_unknown_coverage_key():
    with pytest.raises(BenchmarkStateError, match="coverage"):
        _query(coverage=dict(COVERAGE, radius=2))


def test_fingerprint_lists_v2_state_modules():
    deps = list(c2.FINGERPRINT_DEPENDENCIES)
    assert "src/civ_mcp/lua/benchmark_v2.py" in deps
    assert "src/civ_mcp/arena/benchmark_state_v2.py" in deps
    assert deps == sorted(deps)
