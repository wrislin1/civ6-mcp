"""Tests for the descriptive measured benefit ledger (never scored)."""
from __future__ import annotations

import copy
import inspect

from civ_mcp.arena import benchmark_ledger
from civ_mcp.arena.benchmark_ledger import build_ledger, measured_delta

from .benchmark_v2_fixtures import state_v2


def test_unavailable_is_not_zero_benefit():
    assert measured_delta(None, 2) == {
        "before": None, "after": 2, "delta": None, "coverage": "unavailable"}
    assert measured_delta(3, 3)["delta"] == 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def builder(*, id=1, unit_index=1, x=10, y=10, charges=2, owner=0):
    return dict(owner=owner, id=id, unit_index=unit_index, type="UNIT_BUILDER",
                role="civilian", x=x, y=y, hp=100, max_hp=100, moves=2, charges=charges)


def tile(x=10, y=10, *, improvement="NONE", feature="NONE", food=2, production=0):
    return dict(x=x, y=y, owner=0, terrain="TERRAIN_GRASS", feature=feature,
                resource="NONE", improvement=improvement, pillaged=False,
                district="NONE", visible=True,
                yields=dict(food=food, production=production, gold=0, science=0,
                            culture=0, faith=0))


def state(*, units=(), tiles=(), gold=100, faith=0, resources=()):
    s = state_v2(units=list(units), tiles=list(tiles), gold=gold, faith=faith)
    s["resources"] = [dict(r) for r in resources]
    s["row_counts"]["resource"] = len(s["resources"])
    return s


def step(idx, before, after, tool_name, tool_args, result="OK"):
    return {"idx": idx, "role": "tool", "tool_name": tool_name, "tool_args": tool_args,
            "tool_result_full": result, "state_before": before, "state_after": after}


def trial(initial, steps):
    final = steps[-1]["state_after"] if steps else initial
    return {"initial_state": initial, "final_state": final, "steps": steps}


def by_path(entries):
    return {tuple(e["path"]): e for e in entries}


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

def test_improve_then_undo_has_zero_net_and_two_step_entries():
    s0 = state(units=[builder(charges=3)], tiles=[tile()])
    s1 = state(units=[builder(charges=2)],
               tiles=[tile(improvement="IMPROVEMENT_FARM", food=3)])
    s2 = state(units=[builder(charges=1)], tiles=[tile()])
    ledger = build_ledger(trial(s0, [
        step(0, s0, s1, "improve_tile",
             {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"}),
        step(1, s1, s2, "remove_improvement", {"unit_index": 1}),
    ]))
    net = by_path(ledger["net"])
    assert ("tiles", "10,10", "food") not in net  # identical before/after
    assert net[("gold",)]["delta"] == 0 and net[("faith",)]["delta"] == 0
    assert net[("gold",)]["coverage"] == "measured"
    charges = net[("units", "0:1", "charges")]
    assert (charges["before"], charges["after"], charges["delta"]) == (3, 1, -2)
    assert charges["source_steps"] == [0, 1]
    gain = by_path(ledger["steps"][0]["entries"])[("tiles", "10,10", "food")]
    undo = by_path(ledger["steps"][1]["entries"])[("tiles", "10,10", "food")]
    assert (gain["delta"], undo["delta"]) == (1, -1)
    assert gain["refs"] == {"tile": [10, 10]} and gain["units"] == "yield"
    assert gain["source_steps"] == [0] and undo["source_steps"] == [1]
    assert ledger["steps"][0]["tool_name"] == "improve_tile"
    assert gain["tool_name"] == "improve_tile"


def test_net_is_endpoint_comparison_not_sum_of_steps():
    s0 = state(tiles=[tile()], gold=100)
    s1 = state(tiles=[tile(food=3)], gold=130)
    s2 = state(tiles=[tile(food=4)], gold=90)
    ledger = build_ledger(trial(s0, [step(0, s0, s1, "end_turn", {}),
                                     step(1, s1, s2, "end_turn", {})]))
    net = by_path(ledger["net"])
    assert net[("gold",)]["delta"] == -10
    assert net[("gold",)]["source_steps"] == [0, 1]
    assert net[("tiles", "10,10", "food")]["delta"] == 2


def test_city_total_only_drift_emits_no_tile_yield_gain():
    s0 = state(units=[builder(x=10, y=10)], tiles=[tile()])
    s1 = state(units=[builder(x=11, y=10)], tiles=[tile()])
    s1["cities"] = [dict(owner=0, id=1, x=5, y=5, population=5, housing=6,
                         buildings=[], districts=[], queue=None)]
    s1["row_counts"]["city"] = 1
    ledger = build_ledger(trial(s0, [step(0, s0, s1, "move_unit",
                                          {"unit_index": 1, "x": 11, "y": 10})]))
    assert ledger["steps"][0]["entries"] == []
    assert not [e for e in ledger["net"] if e["path"][0] == "tiles"]


def test_field_present_only_after_has_unavailable_before():
    s0 = state(tiles=[tile()])
    s1 = state(tiles=[tile(), tile(11, 10, food=1)],
               resources=[dict(resource_type="RESOURCE_IRON", access=True,
                               stock=2, flow=1)])
    ledger = build_ledger(trial(s0, [step(0, s0, s1, "end_turn", {})]))
    entries = by_path(ledger["steps"][0]["entries"])
    food = entries[("tiles", "11,10", "food")]
    assert food["before"] is None and food["after"] == 1
    assert food["delta"] is None and food["coverage"] == "unavailable"
    access = entries[("resources", "RESOURCE_IRON", "access")]
    assert access["before"] is None and access["coverage"] == "unavailable"
    assert ("tiles", "10,10", "food") not in entries


def test_legitimate_last_charge_is_consumed_and_measured():
    s0 = state(units=[builder(charges=1)], tiles=[tile()])
    s1 = state(units=[], tiles=[tile(improvement="IMPROVEMENT_FARM", food=3)])
    ledger = build_ledger(trial(s0, [step(
        0, s0, s1, "improve_tile",
        {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"})]))
    for entries in (ledger["steps"][0]["entries"], ledger["net"]):
        e = by_path(entries)[("units", "0:1", "charges")]
        assert (e["before"], e["after"], e["delta"]) == (1, 0, -1)
        assert e["coverage"] == "measured" and e["lifecycle"] == "consumed"
        assert e["refs"] == {"unit": [0, 1]} and e["units"] == "charges"
        assert e["lifecycle"] not in ("lost", "destroyed")


def test_unresolved_disappearance_is_unavailable_not_charge_spent():
    s0 = state(units=[builder(charges=2)], tiles=[tile()])
    s1 = state(units=[], tiles=[tile()])
    ledger = build_ledger(trial(s0, [step(0, s0, s1, "end_turn", {})]))
    for entries in (ledger["steps"][0]["entries"], ledger["net"]):
        e = by_path(entries)[("units", "0:1", "charges")]
        assert e["after"] is None and e["delta"] is None
        assert e["coverage"] == "unavailable" and e["lifecycle"] == "unresolved"


def test_resource_access_gained_is_zero_to_one():
    iron = dict(resource_type="RESOURCE_IRON", access=False, stock=0, flow=0)
    s0 = state(resources=[iron])
    s1 = state(resources=[dict(iron, access=True)])
    ledger = build_ledger(trial(s0, [step(0, s0, s1, "end_turn", {})]))
    e = by_path(ledger["net"])[("resources", "RESOURCE_IRON", "access")]
    assert (e["before"], e["after"], e["delta"]) == (0, 1, 1)
    assert e["units"] == "access" and e["refs"] == {"resource": "RESOURCE_IRON"}


def test_harvest_receipt_is_tagged_on_remove_feature_step():
    s0 = state(units=[builder(charges=2)], tiles=[tile(feature="FEATURE_FOREST")],
               gold=100)
    s1 = state(units=[builder(charges=1)], tiles=[tile()], gold=140)
    s2 = state(units=[builder(charges=1)], tiles=[tile()], gold=150)
    ledger = build_ledger(trial(s0, [
        step(0, s0, s1, "remove_feature", {"unit_index": 1}),
        step(1, s1, s2, "end_turn", {})]))
    receipt = by_path(ledger["steps"][0]["entries"])[("gold",)]
    assert receipt["receipt"] is True and receipt["delta"] == 40
    later = by_path(ledger["steps"][1]["entries"])[("gold",)]
    assert "receipt" not in later
    assert "receipt" not in by_path(ledger["net"])[("gold",)]


def test_paths_unique_and_sorted():
    s0 = state(units=[builder(id=2, unit_index=2, charges=3), builder(charges=3)],
               tiles=[tile(), tile(11, 10)], gold=10, faith=5)
    s1 = state(units=[builder(id=2, unit_index=2, charges=2), builder(charges=2)],
               tiles=[tile(food=5, production=2), tile(11, 10, food=3)],
               gold=20, faith=6)
    ledger = build_ledger(trial(s0, [step(0, s0, s1, "end_turn", {})]))
    for entries in (ledger["net"], ledger["steps"][0]["entries"]):
        paths = [e["path"] for e in entries]
        assert len({tuple(p) for p in paths}) == len(paths)
        assert paths == sorted(paths)
        assert all(isinstance(c, str) for p in paths for c in p)


def test_deterministic_and_input_order_independent():
    s0 = state(units=[builder(charges=3), builder(id=2, unit_index=2, charges=3)],
               tiles=[tile(), tile(11, 10)])
    s1 = state(units=[builder(charges=2), builder(id=2, unit_index=2, charges=1)],
               tiles=[tile(food=3), tile(11, 10, food=4)])
    t = trial(s0, [step(0, s0, s1, "end_turn", {})])
    r0, r1 = copy.deepcopy(s0), copy.deepcopy(s1)
    for s in (r0, r1):
        s["units"].reverse()
        s["tiles"].reverse()
    shuffled = trial(r0, [step(0, r0, r1, "end_turn", {})])
    assert shuffled["initial_state"]["units"][0]["id"] == 2
    assert build_ledger(t) == build_ledger(copy.deepcopy(t)) == build_ledger(shuffled)


def test_ledger_is_independent_of_rubric():
    s0 = state(tiles=[tile()])
    s1 = state(tiles=[tile(food=3)], gold=110)
    t = trial(s0, [step(0, s0, s1, "end_turn", {})])
    with_rubric = dict(copy.deepcopy(t), rubric={"weights": {"gold": 99}},
                       score={"primary_score": 1.0})
    assert build_ledger(with_rubric) == build_ledger(t)
    source = inspect.getsource(benchmark_ledger)
    assert "rubric" not in source and "weight" not in source
