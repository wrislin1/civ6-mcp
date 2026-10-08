"""Tests for the finite v2 predicate vocabulary and civilian cover geometry."""
from __future__ import annotations

from copy import deepcopy

import pytest

from civ_mcp.arena import benchmark_contract_v2 as c2
from civ_mcp.arena.benchmark_predicates_v2 import (
    civilian_covered,
    civilian_exposed,
    evaluate_predicate,
    validate_predicate,
)
from civ_mcp.arena.benchmark_state import BenchmarkStateError

from .benchmark_v2_fixtures import state_v2


def test_observation_cannot_create_exposure_debit():
    unit = dict(owner=0, id=1, unit_index=1, type="UNIT_BUILDER", role="civilian",
                x=10, y=10, hp=100, max_hp=100, moves=2, charges=1)
    foe = dict(owner=1, id=70001, role="combat", hostile=True, visible=True,
               x=11, y=10, hp=100, max_hp=100, status="alive_visible")
    initial = state_v2(units=[unit], targets=[foe])
    assert not evaluate_predicate(
        {"kind": "new_civilian_exposure", "unit": [0, 1]},
        initial=initial, final=deepcopy(initial),
    )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def civ(*, id=1, x=10, y=10, hp=100, charges=1, type="UNIT_BUILDER", role="civilian",
        owner=0):
    return dict(owner=owner, id=id, unit_index=id, type=type, role=role, x=x, y=y,
                hp=hp, max_hp=100, moves=2, charges=charges)


def mil(*, id=2, x=10, y=10, type="UNIT_WARRIOR", owner=0, role="combat"):
    return dict(owner=owner, id=id, unit_index=id, type=type, role=role, x=x, y=y,
                hp=100, max_hp=100, moves=2, charges=0)


def foe(*, x, y, id=70001, hostile=True, visible=True, role="combat",
        status="alive_visible", hp=100):
    return dict(owner=1, id=id, tracked=True, role=role, hostile=hostile, visible=visible,
                status=status, x=x, y=y, hp=hp, max_hp=100)


def hidden_foe(*, id=70001, status="alive_not_visible"):
    return dict(owner=1, id=id, tracked=True, role=None, hostile=True, visible=False,
                status=status, x=None, y=None, hp=None, max_hp=None)


def tile(x=10, y=10, *, owner=0, resource="NONE", improvement="NONE", pillaged=False,
         food=2, feature="NONE"):
    return dict(x=x, y=y, owner=owner, terrain="TERRAIN_GRASS", feature=feature,
                resource=resource, improvement=improvement, pillaged=pillaged,
                district="NONE", visible=True,
                yields=dict(food=food, production=0, gold=0, science=0, culture=0, faith=0))


def city(*, id=65536, x=9, y=10, owner=0, population=4, housing=5, buildings=(),
         districts=(), queue=None):
    return dict(owner=owner, id=id, name="Seoul", x=x, y=y, population=population,
                housing=housing, buildings=list(buildings), districts=list(districts),
                queue=queue or dict(item_kind="NONE", item_type="NONE", repair=False,
                                    target_x=None, target_y=None))


def ev(pred, initial, final=None, transition=None):
    return evaluate_predicate(pred, initial=initial,
                              final=final if final is not None else initial,
                              transition=transition)


# ---------------------------------------------------------------------------
# Shared geometry
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cx,cy,fx,fy", [
    (10, 10, 11, 10), (10, 10, 9, 9), (10, 10, 10, 9), (10, 10, 9, 11), (10, 10, 10, 11),
    (10, 11, 11, 10), (10, 11, 10, 10), (10, 11, 11, 12), (10, 11, 10, 12), (10, 11, 9, 11),
])
def test_adjacent_hostile_exposes_on_both_row_parities(cx, cy, fx, fy):
    s = state_v2(units=[civ(x=cx, y=cy)], targets=[foe(x=fx, y=fy)])
    assert civilian_exposed(s, (0, 1))
    assert not civilian_covered(s, (0, 1))


@pytest.mark.parametrize("cx,cy,fx,fy", [
    (10, 10, 11, 9), (10, 10, 11, 11),    # even row: not neighbours
    (10, 11, 9, 10), (10, 11, 9, 12),     # odd row: not neighbours
    (10, 10, 12, 10),                     # distance 2
    (10, 10, 10, 10),                     # same tile is not "adjacent"
])
def test_non_adjacent_hostile_does_not_expose(cx, cy, fx, fy):
    s = state_v2(units=[civ(x=cx, y=cy)], targets=[foe(x=fx, y=fy)])
    assert not civilian_exposed(s, (0, 1))


def test_co_located_escort_covers():
    s = state_v2(units=[civ(), mil(x=10, y=10)], targets=[foe(x=11, y=10)])
    assert civilian_covered(s, (0, 1))
    assert not civilian_exposed(s, (0, 1))


@pytest.mark.parametrize("ex,ey,covered", [(9, 11, True), (10, 11, True), (11, 11, False)])
def test_adjacent_escort_covers_with_even_row_parity(ex, ey, covered):
    s = state_v2(units=[civ(), mil(x=ex, y=ey)], targets=[foe(x=11, y=10)])
    assert civilian_covered(s, (0, 1)) is covered
    assert civilian_exposed(s, (0, 1)) is (not covered)


@pytest.mark.parametrize("ex,ey,covered", [(10, 12, True), (11, 12, True), (9, 12, False)])
def test_adjacent_escort_covers_with_odd_row_parity(ex, ey, covered):
    s = state_v2(units=[civ(x=10, y=11), mil(x=ex, y=ey)], targets=[foe(x=11, y=11)])
    assert civilian_covered(s, (0, 1)) is covered
    assert civilian_exposed(s, (0, 1)) is (not covered)


def test_foreign_combat_unit_does_not_cover():
    s = state_v2(units=[civ(), mil(x=10, y=10, owner=2)], targets=[foe(x=11, y=10)])
    assert not civilian_covered(s, (0, 1))
    assert civilian_exposed(s, (0, 1))


def test_own_civilian_does_not_cover():
    s = state_v2(units=[civ(), civ(id=3, x=10, y=10)], targets=[foe(x=11, y=10)])
    assert civilian_exposed(s, (0, 1))


def test_friendly_target_does_not_expose():
    s = state_v2(units=[civ()], targets=[foe(x=11, y=10, hostile=False)])
    assert not civilian_exposed(s, (0, 1))


def test_hostile_civilian_target_does_not_expose():
    s = state_v2(units=[civ()], targets=[foe(x=11, y=10, role="civilian")])
    assert not civilian_exposed(s, (0, 1))


def test_owned_city_tile_covers_and_prevents_exposure():
    s = state_v2(units=[civ(x=10, y=10)], cities=[city(x=10, y=10)],
                 targets=[foe(x=11, y=10)])
    assert civilian_covered(s, (0, 1))
    assert not civilian_exposed(s, (0, 1))


def test_foreign_city_tile_does_not_cover():
    s = state_v2(units=[civ(x=10, y=10)], cities=[city(x=10, y=10, owner=3)],
                 targets=[foe(x=11, y=10)])
    assert civilian_exposed(s, (0, 1))


def test_explicitly_invisible_hostile_is_not_a_threat():
    s = state_v2(units=[civ()], targets=[foe(x=11, y=10, visible=False)])
    assert not civilian_exposed(s, (0, 1))
    s = state_v2(units=[civ()], targets=[hidden_foe()])
    assert not civilian_exposed(s, (0, 1))


@pytest.mark.parametrize("field", ["visible", "hostile", "role"])
def test_absent_threat_evidence_raises(field):
    s = state_v2(units=[civ()], targets=[foe(x=11, y=10, **{field: None})])
    with pytest.raises(BenchmarkStateError):
        civilian_exposed(s, (0, 1))


@pytest.mark.parametrize("field", ["role", "hp"])
def test_civilian_with_unknown_role_or_health_raises(field):
    s = state_v2(units=[dict(civ(), **{field: None})], targets=[foe(x=11, y=10)])
    with pytest.raises(BenchmarkStateError):
        civilian_exposed(s, (0, 1))
    with pytest.raises(BenchmarkStateError):
        civilian_covered(s, (0, 1))


def test_geometry_on_absent_civilian_raises():
    with pytest.raises(BenchmarkStateError):
        civilian_exposed(state_v2(), (0, 1))


def test_geometry_with_incomplete_capture_raises():
    s = state_v2(units=[civ()], targets=[foe(x=11, y=10)])
    s["row_counts"]["target"] = 2
    with pytest.raises(BenchmarkStateError):
        civilian_exposed(s, (0, 1))


# ---------------------------------------------------------------------------
# new_civilian_exposure
# ---------------------------------------------------------------------------

NEW = {"kind": "new_civilian_exposure", "unit": [0, 1]}


def test_new_exposure_is_detected():
    initial = state_v2(units=[civ(x=10, y=10)], targets=[foe(x=13, y=10)])
    final = state_v2(units=[civ(x=12, y=10)], targets=[foe(x=13, y=10)])
    assert ev(NEW, initial, final)


def test_initial_exposure_stays_zero():
    initial = state_v2(units=[civ()], targets=[foe(x=11, y=10)])
    final = state_v2(units=[civ(x=10, y=11)], targets=[foe(x=11, y=10)])
    assert civilian_exposed(final, (0, 1))
    assert not ev(NEW, initial, final)


def test_lost_or_consumed_civilian_has_no_exposure_debit():
    initial = state_v2(units=[civ()], targets=[foe(x=12, y=10)])
    final = state_v2(targets=[foe(x=11, y=10)])
    assert not ev(NEW, initial, final)


def test_new_exposure_with_incomplete_final_capture_raises():
    initial = state_v2(units=[civ()], targets=[foe(x=12, y=10)])
    final = state_v2(targets=[foe(x=11, y=10)])
    final["row_counts"]["unit"] = 1
    with pytest.raises(BenchmarkStateError):
        ev(NEW, initial, final)
    del final["row_counts"]
    with pytest.raises(BenchmarkStateError):
        ev(NEW, initial, final)


def test_absent_visibility_in_final_raises_rather_than_reading_as_safe():
    initial = state_v2(units=[civ()], targets=[foe(x=12, y=10)])
    final = state_v2(units=[civ()], targets=[foe(x=11, y=10, visible=None)])
    with pytest.raises(BenchmarkStateError):
        ev(NEW, initial, final)


# ---------------------------------------------------------------------------
# civilian_covered predicate
# ---------------------------------------------------------------------------

COVER = {"kind": "civilian_covered", "unit": [0, 1], "tiles": [[10, 11]]}


def test_civilian_covered_requires_accepted_tile_and_cover():
    assert ev(COVER, state_v2(units=[civ(x=10, y=11), mil(x=11, y=11)]))
    assert not ev(COVER, state_v2(units=[civ(x=10, y=10), mil(x=10, y=10)]))
    assert not ev(COVER, state_v2(units=[civ(x=10, y=11)]))


def test_no_positive_rung_for_moving_closer_to_cover():
    initial = state_v2(units=[civ(x=10, y=14), mil(x=12, y=11)])
    closer = state_v2(units=[civ(x=10, y=11), mil(x=12, y=11)])  # distance 2
    assert not ev(COVER, initial, closer)


def test_civilian_covered_on_missing_civilian_is_false():
    assert not ev(COVER, state_v2(units=[mil(x=10, y=11)]))


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

DMG = {"kind": "target_damaged", "target": [1, 70001], "minimum_damage": 30}
NEUT = {"kind": "target_neutralised", "target": [1, 70001]}


def test_target_damaged_threshold():
    initial = state_v2(targets=[foe(x=11, y=10, hp=100)])
    assert ev(DMG, initial, state_v2(targets=[foe(x=11, y=10, hp=70)]))
    assert not ev(DMG, initial, state_v2(targets=[foe(x=11, y=10, hp=71)]))


def test_target_damaged_needs_live_visible_endpoints():
    initial = state_v2(targets=[foe(x=11, y=10, hp=100)])
    assert not ev(DMG, initial, state_v2(targets=[hidden_foe()]))
    assert not ev(DMG, initial, state_v2(targets=[hidden_foe(status="destroyed")]))


def test_target_damaged_with_unknown_hp_raises():
    initial = state_v2(targets=[foe(x=11, y=10, hp=100)])
    with pytest.raises(BenchmarkStateError):
        ev(DMG, initial, state_v2(targets=[foe(x=11, y=10, hp=None)]))


def test_no_positive_rung_for_moving_closer_to_target():
    initial = state_v2(units=[mil(x=14, y=10)], targets=[foe(x=11, y=10)])
    final = state_v2(units=[mil(x=12, y=10)], targets=[foe(x=11, y=10)])
    assert not ev(DMG, initial, final)
    assert not ev(NEUT, initial, final)


def test_target_neutralised_requires_destroyed_status():
    initial = state_v2(targets=[foe(x=11, y=10)])
    assert ev(NEUT, initial, state_v2(targets=[hidden_foe(status="destroyed")]))


def test_alive_not_visible_is_never_neutralised():
    initial = state_v2(targets=[foe(x=11, y=10)])
    assert not ev(NEUT, initial, state_v2(targets=[hidden_foe()]))


def test_target_missing_from_final_raises():
    initial = state_v2(targets=[foe(x=11, y=10)])
    with pytest.raises(BenchmarkStateError):
        ev(NEUT, initial, state_v2())


def test_target_not_initially_visible_is_not_neutralised():
    initial = state_v2(targets=[hidden_foe()])
    assert not ev(NEUT, initial, state_v2(targets=[hidden_foe(status="destroyed")]))


# ---------------------------------------------------------------------------
# Tiles and builders
# ---------------------------------------------------------------------------

FARM = {"kind": "tile_matches", "tiles": [[10, 10], [11, 10]],
        "fields": {"improvement": "IMPROVEMENT_FARM", "pillaged": False, "owner": 0}}


def test_tile_matches_completion_independent_of_which_builder_acted():
    tiles = [tile(10, 10, improvement="IMPROVEMENT_FARM"), tile(11, 10)]
    by_a = state_v2(units=[civ(id=3, charges=2)], tiles=tiles)
    by_b = state_v2(units=[civ(id=1, charges=1)], tiles=tiles)
    by_consumed = state_v2(tiles=tiles)
    assert ev(FARM, by_a) and ev(FARM, by_b) and ev(FARM, by_consumed)


def test_tile_matches_false_and_food_bounds():
    s = state_v2(tiles=[tile(10, 10), tile(11, 10, food=3)])
    assert not ev(FARM, s)
    pred = {"kind": "tile_matches", "tiles": [[11, 10]], "fields": {"food": {"min": 3}}}
    assert ev(pred, s)
    assert not ev(dict(pred, fields={"food": {"max": 2}}), s)
    assert ev(dict(pred, fields={"food": 3}), s)
    assert ev(dict(pred, fields={"food": {"min": 2, "max": 3}}), s)


def test_tile_matches_on_uncaptured_tile_raises():
    with pytest.raises(BenchmarkStateError):
        ev(FARM, state_v2(tiles=[tile(10, 10)]))


CHARGED = {"kind": "charged_builder_at", "tiles": [[10, 10]]}


def test_charged_builder_at():
    assert ev(CHARGED, state_v2(units=[civ(charges=1)], tiles=[tile()]))
    assert ev(CHARGED, state_v2(units=[dict(civ(charges=1), moves=0)], tiles=[tile()]))
    assert not ev(CHARGED, state_v2(units=[civ(charges=0)], tiles=[tile()]))
    assert not ev(CHARGED, state_v2(units=[civ(charges=1)], tiles=[tile(owner=3)]))
    assert not ev(CHARGED, state_v2(units=[civ(charges=1, x=11)], tiles=[tile()]))
    assert not ev(CHARGED, state_v2(units=[civ(charges=1, type="UNIT_SETTLER")],
                                    tiles=[tile()]))
    assert not ev(CHARGED, state_v2(units=[civ(charges=1, owner=2)], tiles=[tile()]))


def test_charged_builder_with_unknown_charges_raises():
    with pytest.raises(BenchmarkStateError):
        ev(CHARGED, state_v2(units=[civ(charges=None)], tiles=[tile()]))


# ---------------------------------------------------------------------------
# Cities
# ---------------------------------------------------------------------------

def queue(kind, item, repair=False, x=None, y=None):
    return dict(item_kind=kind, item_type=item, repair=repair, target_x=x, target_y=y)


def test_active_production():
    pred = {"kind": "active_production", "cities": [65536],
            "items": ["IMPROVEMENT_FARM", "BUILDING_GRANARY"], "repair": False}
    assert ev(pred, state_v2(cities=[city(queue=queue("BUILDING", "BUILDING_GRANARY"))]))
    assert not ev(pred, state_v2(cities=[city(queue=queue("BUILDING", "BUILDING_GRANARY",
                                                          repair=True))]))
    assert not ev(pred, state_v2(cities=[city()]))
    assert not ev(pred, state_v2(cities=[city(owner=2,
                                              queue=queue("BUILDING", "BUILDING_GRANARY"))]))
    with_tiles = dict(pred, items=["DISTRICT_CAMPUS"], tiles=[[10, 11]])
    assert ev(with_tiles, state_v2(cities=[city(queue=queue("DISTRICT", "DISTRICT_CAMPUS",
                                                            x=10, y=11))]))
    assert not ev(with_tiles, state_v2(cities=[city(queue=queue("DISTRICT", "DISTRICT_CAMPUS",
                                                                x=10, y=12))]))


def test_housing_resolved():
    pred = {"kind": "housing_resolved", "cities": [65536],
            "remedy_buildings": ["BUILDING_GRANARY"], "minimum_surplus": 1}
    granary = dict(building_type="BUILDING_GRANARY", present=True, pillaged=False)
    assert ev(pred, state_v2(cities=[city(population=4, housing=5, buildings=[granary])]))
    assert not ev(pred, state_v2(cities=[city(population=5, housing=5,
                                              buildings=[granary])]))
    assert not ev(pred, state_v2(cities=[city(population=4, housing=5)]))
    assert not ev(pred, state_v2(cities=[city(population=4, housing=5, buildings=[
        dict(granary, pillaged=True)])]))
    assert not ev(pred, state_v2(cities=[city(population=4, housing=5, buildings=[
        dict(granary, present=False)])]))


def test_district_committed():
    pred = {"kind": "district_committed", "cities": [65536],
            "district_types": ["DISTRICT_CAMPUS"], "tiles": [[10, 11]]}
    d = dict(district_id=8, district_type="DISTRICT_CAMPUS", x=10, y=11,
             complete=False, pillaged=False)
    q = queue("DISTRICT", "DISTRICT_CAMPUS", x=10, y=11)
    assert ev(pred, state_v2(cities=[city(districts=[d], queue=q)]))
    assert not ev(pred, state_v2(cities=[city(districts=[d])]))
    assert not ev(pred, state_v2(cities=[city(queue=q)]))
    assert not ev(pred, state_v2(cities=[city(districts=[dict(d, x=11)],
                                              queue=queue("DISTRICT", "DISTRICT_CAMPUS",
                                                          x=11, y=11))]))


def test_unit_in_area():
    pred = {"kind": "unit_in_area", "unit_types": ["UNIT_WARRIOR"], "tiles": [[11, 10]]}
    assert ev(pred, state_v2(units=[mil(x=11, y=10)]))
    assert not ev(pred, state_v2(units=[mil(x=12, y=10)]))
    assert not ev(pred, state_v2(units=[mil(x=11, y=10, owner=2)]))
    assert not ev(pred, state_v2(units=[mil(x=11, y=10, type="UNIT_SLINGER")]))


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def step(before, after, tool_name, tool_args, result="OK"):
    return {"idx": 0, "role": "tool", "tool_name": tool_name, "tool_args": tool_args,
            "tool_result_full": result, "state_before": before, "state_after": after}


def test_asset_displaced():
    pred = {"kind": "asset_displaced", "tiles": [[10, 10]],
            "asset_fields": {"improvement": "IMPROVEMENT_FARM"}}
    before = state_v2(tiles=[tile(improvement="IMPROVEMENT_FARM")])
    removed = state_v2(tiles=[tile()])
    replaced = state_v2(tiles=[tile(improvement="IMPROVEMENT_MINE")])
    assert ev(pred, before, removed, step(before, removed, "remove_improvement", {}))
    assert ev(pred, before, replaced, step(before, replaced, "improve_tile", {}))
    assert not ev(pred, before, before, step(before, before, "get_units", {}))
    assert not ev(pred, removed, removed, step(removed, removed, "get_units", {}))


def test_event_predicates_require_transition():
    with pytest.raises(BenchmarkStateError):
        ev({"kind": "unit_lost", "unit": [0, 1]}, state_v2())
    with pytest.raises(BenchmarkStateError):
        ev({"kind": "asset_displaced", "tiles": [[10, 10]],
            "asset_fields": {"improvement": "IMPROVEMENT_FARM"}}, state_v2())


def test_unit_lost_uses_lifecycle():
    pred = {"kind": "unit_lost", "unit": [0, 1]}
    before = state_v2(units=[civ(charges=1)], tiles=[tile()])
    after = state_v2(tiles=[tile(improvement="IMPROVEMENT_FARM")])
    consumed = step(before, after, "improve_tile",
                    {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"})
    assert not ev(pred, before, after, consumed)
    deleted = step(before, after, "delete_unit", {"unit_index": 1})
    assert ev(pred, before, after, deleted)
    assert not ev(pred, before, before, step(before, before, "get_units", {}))


def test_no_destruction_claim_for_unresolved_disappearance():
    pred = {"kind": "unit_lost", "unit": [0, 1]}
    before = state_v2(units=[civ(charges=3)], tiles=[tile()])
    after = state_v2(tiles=[tile()])
    with pytest.raises(BenchmarkStateError):
        ev(pred, before, after, step(before, after, "move_unit", {"unit_index": 1}))


# ---------------------------------------------------------------------------
# Composition and validation
# ---------------------------------------------------------------------------

def test_all_and_any():
    s = state_v2(units=[mil(x=11, y=10)])
    yes = {"kind": "unit_in_area", "unit_types": ["UNIT_WARRIOR"], "tiles": [[11, 10]]}
    no = dict(yes, tiles=[[12, 10]])
    assert ev({"kind": "any", "predicates": [no, yes]}, s)
    assert not ev({"kind": "all", "predicates": [yes, no]}, s)
    assert ev({"kind": "all", "predicates": [yes, {"kind": "any", "predicates": [yes]}]}, s)


def test_unknown_kind_in_unvisited_any_branch_raises():
    s = state_v2(units=[mil(x=11, y=10)])
    yes = {"kind": "unit_in_area", "unit_types": ["UNIT_WARRIOR"], "tiles": [[11, 10]]}
    pred = {"kind": "any", "predicates": [yes, {"kind": "python_expr", "expr": "True"}]}
    with pytest.raises(ValueError):
        validate_predicate(pred)
    with pytest.raises(ValueError):
        ev(pred, s)


def test_invalid_args_in_unvisited_branch_raise():
    s = state_v2(units=[mil(x=11, y=10)])
    yes = {"kind": "unit_in_area", "unit_types": ["UNIT_WARRIOR"], "tiles": [[11, 10]]}
    bad = {"kind": "tile_matches", "tiles": [], "fields": {"owner": 0}}
    with pytest.raises(ValueError):
        ev({"kind": "any", "predicates": [yes, bad]}, s)


@pytest.mark.parametrize("pred", [
    {"kind": "distance_to", "unit": [0, 1], "tiles": [[1, 1]]},
    {"kind": "any", "predicates": []},
    {"kind": "all"},
    {"predicates": []},
    "unit_lost",
    {"kind": "unit_lost", "unit": [0, True]},
    {"kind": "unit_lost", "unit": [0, 1, 2]},
    {"kind": "unit_lost", "unit": [0, 1], "extra": 1},
    {"kind": "tile_matches", "tiles": [[True, 1]], "fields": {"owner": 0}},
    {"kind": "tile_matches", "tiles": [[1, 1]], "fields": {}},
    {"kind": "tile_matches", "tiles": [[1, 1]], "fields": {"terrain": "X"}},
    {"kind": "tile_matches", "tiles": [[1, 1]], "fields": {"owner": True}},
    {"kind": "tile_matches", "tiles": [[1, 1]], "fields": {"food": {"min": float("nan")}}},
    {"kind": "tile_matches", "tiles": [[1, 1]], "fields": {"food": {"avg": 1}}},
    {"kind": "tile_matches", "tiles": [[1, 1]], "fields": {"food": {}}},
    {"kind": "tile_matches", "tiles": [[1, 1]], "fields": {"pillaged": 0}},
    {"kind": "charged_builder_at", "tiles": [[1.5, 1]]},
    {"kind": "active_production", "cities": [1], "items": [], "repair": False},
    {"kind": "active_production", "cities": [1], "items": ["X"], "repair": 0},
    {"kind": "active_production", "cities": [True], "items": ["X"], "repair": False},
    {"kind": "housing_resolved", "cities": [1], "remedy_buildings": ["B"],
     "minimum_surplus": float("inf")},
    {"kind": "housing_resolved", "cities": [1], "remedy_buildings": ["B"],
     "minimum_surplus": True},
    {"kind": "district_committed", "cities": [1], "district_types": [""], "tiles": [[1, 1]]},
    {"kind": "target_damaged", "target": [1, 2], "minimum_damage": float("nan")},
    {"kind": "target_damaged", "target": [1, 2], "minimum_damage": 0},
    {"kind": "unit_in_area", "unit_types": ["UNIT_WARRIOR"], "tiles": "11,10"},
    {"kind": "asset_displaced", "tiles": [[1, 1]], "asset_fields": {}},
    {"kind": "new_civilian_exposure"},
])
def test_invalid_predicates_raise_value_error(pred):
    with pytest.raises(ValueError):
        validate_predicate(pred)


def test_predicate_modules_are_fingerprinted():
    deps = c2.FINGERPRINT_DEPENDENCIES
    assert "src/civ_mcp/arena/benchmark_lifecycle.py" in deps
    assert "src/civ_mcp/arena/benchmark_predicates_v2.py" in deps
