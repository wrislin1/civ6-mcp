"""Tests for lifecycle classification of owned units across one recorded step."""
from __future__ import annotations

import pytest

from civ_mcp.arena.benchmark_lifecycle import classify_lifecycle
from civ_mcp.arena.benchmark_state import BenchmarkStateError

from .benchmark_v2_fixtures import state_v2


def builder(*, id=1, unit_index=1, x=10, y=10, charges=1, type="UNIT_BUILDER",
            role="civilian", hp=100):
    return dict(owner=0, id=id, unit_index=unit_index, type=type, role=role,
                x=x, y=y, hp=hp, max_hp=100, moves=2, charges=charges)


def warrior(*, id=2, unit_index=2, x=12, y=10, type="UNIT_WARRIOR"):
    return dict(owner=0, id=id, unit_index=unit_index, type=type, role="combat",
                x=x, y=y, hp=100, max_hp=100, moves=2, charges=0)


def tile(x=10, y=10, *, improvement="NONE", feature="NONE", pillaged=False, owner=0):
    return dict(x=x, y=y, owner=owner, terrain="TERRAIN_GRASS", feature=feature,
                resource="NONE", improvement=improvement, pillaged=pillaged,
                district="NONE", visible=True,
                yields=dict(food=2, production=0, gold=0, science=0, culture=0, faith=0))


def step(before, after, tool_name, tool_args, result="OK"):
    return {
        "idx": 0, "role": "tool", "ts_start": 0.0, "ts_end": 1.0,
        "tool_name": tool_name, "tool_args": tool_args,
        "tool_result_full": result, "result_total_chars": len(result),
        "result_chars_fed_to_model": len(result), "truncated": False,
        "prompt_tokens": 0, "completion_tokens": 0,
        "state_before": before, "state_after": after,
        "state_digest_before": "a", "state_digest_after": "b",
    }


def only(records):
    assert len(records) == 1, records
    return records[0]


def test_unchanged_units_produce_no_records():
    s = state_v2(units=[builder(charges=2)], tiles=[tile()])
    assert classify_lifecycle(step(s, s, "get_units", {})) == []


def test_last_charge_improvement_with_intended_outcome_is_consumed():
    before = state_v2(units=[builder(charges=1)], tiles=[tile()])
    after = state_v2(tiles=[tile(improvement="IMPROVEMENT_FARM")])
    rec = only(classify_lifecycle(step(
        before, after, "improve_tile",
        {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"})))
    assert rec["entity"] == [0, 1]
    assert rec["status"] == "consumed"
    assert rec["facts"]["tool_name"] == "improve_tile"


def test_consumption_holds_even_when_result_text_looks_like_rejection():
    before = state_v2(units=[builder(charges=1)], tiles=[tile()])
    after = state_v2(tiles=[tile(improvement="IMPROVEMENT_FARM")])
    rec = only(classify_lifecycle(step(
        before, after, "improve_tile",
        {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"},
        result="ERR:CANNOT_IMPROVE")))
    assert rec["status"] == "consumed"


def test_disappearance_without_intended_outcome_is_unresolved():
    before = state_v2(units=[builder(charges=1)], tiles=[tile()])
    after = state_v2(tiles=[tile()])
    rec = only(classify_lifecycle(step(
        before, after, "improve_tile",
        {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"})))
    assert rec["status"] == "unresolved"


def test_multi_charge_builder_disappearing_is_unresolved():
    before = state_v2(units=[builder(charges=2)], tiles=[tile()])
    after = state_v2(tiles=[tile(improvement="IMPROVEMENT_FARM")])
    rec = only(classify_lifecycle(step(
        before, after, "improve_tile",
        {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"})))
    assert rec["status"] == "unresolved"


def test_improvement_by_another_builder_does_not_consume_this_one():
    before = state_v2(units=[builder(charges=1), builder(id=3, unit_index=3, charges=1)],
                      tiles=[tile()])
    after = state_v2(units=[builder(id=3, unit_index=3, charges=1)],
                     tiles=[tile(improvement="IMPROVEMENT_FARM")])
    rec = only(classify_lifecycle(step(
        before, after, "improve_tile",
        {"unit_index": 3, "improvement_name": "IMPROVEMENT_FARM"})))
    assert rec["entity"] == [0, 1] and rec["status"] == "unresolved"


def test_untracked_target_tile_leaves_consumption_unresolved():
    before = state_v2(units=[builder(charges=1)])
    after = state_v2()
    rec = only(classify_lifecycle(step(
        before, after, "improve_tile",
        {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"})))
    assert rec["status"] == "unresolved"


def test_remove_feature_and_repair_outcomes_are_consumption():
    before = state_v2(units=[builder()], tiles=[tile(feature="FEATURE_FOREST")])
    after = state_v2(tiles=[tile()])
    assert only(classify_lifecycle(step(
        before, after, "remove_feature", {"unit_index": 1})))["status"] == "consumed"
    before = state_v2(units=[builder()],
                      tiles=[tile(improvement="IMPROVEMENT_FARM", pillaged=True)])
    after = state_v2(tiles=[tile(improvement="IMPROVEMENT_FARM")])
    assert only(classify_lifecycle(step(
        before, after, "repair_improvement", {"unit_index": 1})))["status"] == "consumed"


# Real `GameState.attack_unit` output: narrated estimate, then the Lua
# `OK:MELEE_ATTACK|...` line with `OK:` stripped, then the follow-up.
REAL_MELEE_RESULT = (
    "Combat Estimate (Melee):\n"
    "  UNIT_WARRIOR (CS:20, HP:100) vs UNIT_BARBARIAN_HORSEMAN (CS:36, HP:100)\n"
    "  Modifiers: none\n"
    "  Est damage to defender: ~12\n"
    "  Est damage to attacker: ~110\n"
    "  -> WARNING: attacker likely dies!\n"
    "MELEE_ATTACK|target:UNIT_BARBARIAN_HORSEMAN at (13,10)"
    "|enemy HP:100 -> 88/100|your HP:100 -> 100 CS:20|est damage dealt:~12\n"
    "  Post-combat: ~88/100 (estimate — verify with get_units)"
)


def test_executed_melee_attack_with_absent_attacker_is_lost():
    before = state_v2(units=[warrior()])
    after = state_v2()
    rec = only(classify_lifecycle(step(
        before, after, "attack_unit", {"unit_index": 2, "x": 13, "y": 10},
        result=REAL_MELEE_RESULT)))
    assert rec["entity"] == [0, 2] and rec["status"] == "lost"
    assert rec["facts"]["tool_name"] == "attack_unit"
    assert rec["facts"]["battle"].startswith("MELEE_ATTACK|target:")


def test_estimate_alone_is_not_battle_evidence():
    before = state_v2(units=[warrior()])
    estimate_only = REAL_MELEE_RESULT.split("\nMELEE_ATTACK|")[0]
    rec = only(classify_lifecycle(step(
        before, state_v2(), "attack_unit", {"unit_index": 2, "x": 13, "y": 10},
        result=estimate_only + "\nError: NO_ENEMY")))
    assert rec["status"] == "unresolved"


def test_ranged_attack_with_absent_attacker_is_unresolved():
    before = state_v2(units=[warrior()])
    rec = only(classify_lifecycle(step(
        before, state_v2(), "attack_unit", {"unit_index": 2, "x": 13, "y": 10},
        result="Combat Estimate (Ranged):\n  ...\nRANGE_ATTACK|target at (13,10)")))
    assert rec["status"] == "unresolved"


def test_rejected_attack_with_absent_attacker_is_unresolved():
    before = state_v2(units=[warrior()])
    after = state_v2()
    rec = only(classify_lifecycle(step(
        before, after, "attack_unit", {"unit_index": 2, "x": 13, "y": 10},
        result="Error: NO_ENEMY")))
    assert rec["status"] == "unresolved"


def test_explicit_delete_is_lost():
    before = state_v2(units=[warrior()])
    rec = only(classify_lifecycle(step(before, state_v2(), "delete_unit",
                                       {"unit_index": 2})))
    assert rec["status"] == "lost"


def test_unexplained_disappearance_is_unresolved_not_lost():
    before = state_v2(units=[builder(charges=3)])
    rec = only(classify_lifecycle(step(before, state_v2(), "move_unit",
                                       {"unit_index": 1, "x": 11, "y": 10})))
    assert rec["status"] == "unresolved"


def test_verified_upgrade_is_transformed():
    before = state_v2(units=[warrior()])
    after = state_v2(units=[warrior(id=9, unit_index=9, type="UNIT_SWORDSMAN")])
    rec = only(classify_lifecycle(step(before, after, "upgrade_unit", {"unit_id": 2})))
    assert rec["status"] == "transformed"
    assert rec["facts"]["replacement"] == [0, 9]


def test_type_change_in_place_with_upgrade_is_transformed():
    before = state_v2(units=[warrior()])
    after = state_v2(units=[warrior(type="UNIT_SWORDSMAN")])
    rec = only(classify_lifecycle(step(before, after, "upgrade_unit", {"unit_id": 2})))
    assert rec["status"] == "transformed"
    assert rec["facts"]["replacement"] == [0, 2]


def test_type_change_without_upgrade_is_unresolved():
    before = state_v2(units=[warrior()])
    after = state_v2(units=[warrior(type="UNIT_SWORDSMAN")])
    rec = only(classify_lifecycle(step(before, after, "move_unit", {"unit_index": 2})))
    assert rec["status"] == "unresolved"


def test_upgrade_without_replacement_is_unresolved():
    before = state_v2(units=[warrior()])
    rec = only(classify_lifecycle(step(before, state_v2(), "upgrade_unit", {"unit_id": 2})))
    assert rec["status"] == "unresolved"


def test_incomplete_unit_capture_raises():
    before = state_v2(units=[builder()])
    after = state_v2()
    after["row_counts"]["unit"] = 1
    with pytest.raises(BenchmarkStateError):
        classify_lifecycle(step(before, after, "move_unit", {}))
    del before["row_counts"]
    with pytest.raises(BenchmarkStateError):
        classify_lifecycle(step(before, state_v2(), "move_unit", {}))


def test_missing_snapshot_raises():
    s = step(state_v2(), state_v2(), "move_unit", {})
    s["state_after"] = None
    with pytest.raises(BenchmarkStateError):
        classify_lifecycle(s)


def test_foreign_units_are_not_classified():
    foreign = dict(builder(), owner=1, id=99)
    before = state_v2(units=[foreign])
    assert classify_lifecycle(step(before, state_v2(), "move_unit", {})) == []
