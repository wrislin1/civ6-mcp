"""Tests for the uncredited-mutation classifier and the all-action loss audit."""
from __future__ import annotations

from civ_mcp.arena.benchmark_audit import (
    audit_losses,
    classify_mutation,
    mutation_category,
    mutation_records,
    undercredited_completions,
)
from civ_mcp.arena.benchmark_scoring_v2 import attribute_progress, score_trial

from .benchmark_v2_fixtures import state_v2


def test_deleted_unscored_asset_has_a_report_bucket():
    record = {"step": 3, "declared_harm_ids": [], "objective_ids": [],
              "losses": [{"kind": "unit", "entity": [0, 9],
                          "lifecycle": "lost", "declared": False}],
              "economic_changes": [], "movement": None,
              "coverage": {"loss": "complete"}}
    result = classify_mutation(record)
    assert result["category"] == "undeclared_loss"
    assert result["primary_deduction"] == 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def builder(*, id=1, x=10, y=10, charges=2):
    return dict(owner=0, id=id, unit_index=id, type="UNIT_BUILDER", role="civilian",
                x=x, y=y, hp=100, max_hp=100, moves=2, charges=charges)


def warrior(*, id=9, x=12, y=10):
    return dict(owner=0, id=id, unit_index=id, type="UNIT_WARRIOR", role="combat",
                x=x, y=y, hp=100, max_hp=100, moves=2, charges=0)


def tile(x=10, y=10, *, improvement="NONE", food=2):
    return dict(x=x, y=y, owner=0, terrain="TERRAIN_GRASS", feature="NONE",
                resource="NONE", improvement=improvement, pillaged=False,
                district="NONE", visible=True,
                yields=dict(food=food, production=0, gold=0, science=0, culture=0, faith=0))


def state(*, units=(), tiles=None):
    return state_v2(units=list(units), tiles=[tile()] if tiles is None else list(tiles))


def step(idx, before, after, tool_name, tool_args, result="OK", *, mutated=True):
    return {"idx": idx, "role": "tool", "tool_name": tool_name, "tool_args": tool_args,
            "tool_result_full": result, "state_before": before, "state_after": after,
            "state_digest_before": f"b{idx}",
            "state_digest_after": f"a{idx}" if mutated else f"b{idx}"}


def trial(initial, steps):
    final = steps[-1]["state_after"] if steps else initial
    return {"initial_state": initial, "final_state": final, "steps": steps}


FARM_AT_10_10 = {"kind": "tile_matches", "tiles": [[10, 10]],
                 "fields": {"improvement": "IMPROVEMENT_FARM"}}
WARRIOR_LOST = {"kind": "unit_lost", "unit": [0, 9]}


def rubric(harms=()):
    return {"objectives": [{"id": "farm", "rungs": [{"points": 2,
                                                     "predicate": FARM_AT_10_10}]}],
            "harms": list(harms)}


def harm(id, *, compensation=()):
    return {"id": id, "loss_key": id, "objective_id": "farm", "weight": 1,
            "weight_reason": "unscored escort", "timing": "event",
            "predicate": WARRIOR_LOST, "compensation": list(compensation), "priority": 1}


def audit(t, r=None):
    r = rubric() if r is None else r
    return attribute_progress(t, r), score_trial(t, r)["harms"]


def delete_warrior_trial(result="OK"):
    s0 = state(units=[builder(), warrior()])
    s1 = state(units=[builder()])
    return trial(s0, [step(0, s0, s1, "delete_unit", {"unit_index": 9}, result)])


# ---------------------------------------------------------------------------
# All-action loss coverage
# ---------------------------------------------------------------------------

def test_unscored_loss_on_credited_action_is_audited_but_not_a_mutation_record():
    # A mine replaced by the credited farm: credited step, unscored asset lost.
    s0 = state(units=[builder()], tiles=[tile(improvement="IMPROVEMENT_MINE")])
    s1 = state(units=[builder(charges=1)],
               tiles=[tile(improvement="IMPROVEMENT_FARM", food=3)])
    t = trial(s0, [step(0, s0, s1, "improve_tile",
                        {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"})])
    progress, harms = audit(t)
    assert [p["step"] for p in progress] == [0]
    assert audit_losses(t, harms) == [{
        "step": 0, "kind": "improvement", "entity": [10, 10], "lifecycle": "lost",
        "declared": False, "declared_harm_ids": [], "tool_name": "improve_tile",
        "result_shape": "ok"}]
    assert mutation_records(t, progress, task_tiles=[], declared_losses=harms) == []


def test_unscored_loss_on_uncredited_action_is_undeclared_loss():
    t = delete_warrior_trial()
    progress, harms = audit(t)
    (record,) = mutation_records(t, progress, task_tiles=[], declared_losses=harms)
    assert record["losses"] == [{"kind": "unit", "entity": [0, 9], "lifecycle": "lost",
                                 "declared": False}]
    assert record["declared_harm_ids"] == [] and record["objective_ids"] == []
    assert classify_mutation(record)["category"] == "undeclared_loss"


def test_loss_on_error_returning_action_is_audited_with_error_shape():
    t = delete_warrior_trial(result="Error: unit could not be deleted")
    progress, harms = audit(t)
    (loss,) = audit_losses(t, harms)
    assert loss["result_shape"] == "error" and loss["lifecycle"] == "lost"
    assert loss["entity"] == [0, 9] and loss["tool_name"] == "delete_unit"
    assert mutation_records(t, progress, task_tiles=[], declared_losses=harms) == []


def test_unmutated_steps_are_not_mutation_records():
    s0 = state(units=[builder()])
    t = trial(s0, [step(0, s0, s0, "get_units", {}, mutated=False)])
    progress, harms = audit(t)
    assert mutation_records(t, progress, task_tiles=[], declared_losses=harms) == []


def test_compensated_declared_loss_is_declared_harm_not_undeclared():
    t = delete_warrior_trial()
    r = rubric([harm("escort-lost", compensation=[{"timing": "event",
                                                    "predicate": WARRIOR_LOST}])])
    progress, harms = audit(t, r)
    assert [h["status"] for h in harms] == ["compensated"]
    (loss,) = audit_losses(t, harms)
    assert loss["declared"] is True and loss["declared_harm_ids"] == ["escort-lost"]
    (record,) = mutation_records(t, progress, task_tiles=[], declared_losses=harms)
    assert record["declared_harm_ids"] == ["escort-lost"]
    assert classify_mutation(record)["category"] == "declared_harm"
    # The same loss with no rubric harm stays undeclared.
    progress, harms = audit(t)
    (record,) = mutation_records(t, progress, task_tiles=[], declared_losses=harms)
    assert classify_mutation(record)["category"] == "undeclared_loss"


def test_unresolved_lifecycle_is_never_reported_as_lost():
    s0 = state(units=[builder(charges=1)])
    s1 = state(units=[])
    t = trial(s0, [step(0, s0, s1, "end_turn", {})])
    progress, harms = audit(t)
    (loss,) = audit_losses(t, harms)
    assert loss["lifecycle"] == "unresolved" and loss["declared"] is False
    (record,) = mutation_records(t, progress, task_tiles=[], declared_losses=harms)
    assert record["losses"][0]["lifecycle"] == "unresolved"
    assert classify_mutation(record)["category"] != "undeclared_loss"


def test_consumed_builder_is_not_a_loss():
    s0 = state(units=[builder(x=11, charges=1)], tiles=[tile(), tile(11, 10)])
    s1 = state(units=[], tiles=[tile(), tile(11, 10, improvement="IMPROVEMENT_FARM")])
    t = trial(s0, [step(0, s0, s1, "improve_tile",
                        {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"})])
    progress, harms = audit(t)
    assert progress == []
    (loss,) = audit_losses(t, harms)
    assert loss["lifecycle"] == "consumed" and loss["kind"] == "unit"
    (record,) = mutation_records(t, progress, task_tiles=[(10, 10)],
                                 declared_losses=harms)
    assert record["improvement"] == {"type": "IMPROVEMENT_FARM", "tile": [11, 10],
                                     "on_unit_tile": True, "on_task_tile": False}
    result = classify_mutation(record)
    assert result["category"] == "economic_change"
    assert "farm_on_own_tile" in result["tags"]


# ---------------------------------------------------------------------------
# Movement and improvements
# ---------------------------------------------------------------------------

def test_non_builder_move_is_non_builder_movement():
    s0 = state(units=[builder(), warrior()])
    s1 = state(units=[builder(), warrior(x=13)])
    t = trial(s0, [step(0, s0, s1, "move_unit", {"unit_index": 9, "x": 13, "y": 10})])
    progress, harms = audit(t)
    (record,) = mutation_records(t, progress, task_tiles=[(14, 10)], declared_losses=harms)
    assert record["movement"] == {"builder": False, "unit": [0, 9], "from": [12, 10],
                                  "to": [13, 10], "distance_before": 2,
                                  "distance_after": 1}
    result = classify_mutation(record)
    assert result["category"] == "non_builder_movement"
    assert result["tags"] == ["non_builder_move"]
    assert result["primary_deduction"] == 0


def test_builder_move_distance_is_measured_to_nearest_public_task_tile():
    s0 = state(units=[builder()])
    closer = state(units=[builder(x=11)])
    farther = state(units=[builder(x=9)])
    t = trial(s0, [step(0, s0, closer, "move_unit", {"unit_index": 1, "x": 11, "y": 10}),
                   step(1, closer, farther, "move_unit", {"unit_index": 1, "x": 9, "y": 10})])
    progress, harms = audit(t)
    records = mutation_records(t, progress, task_tiles=[(14, 10), (0, 0)],
                               declared_losses=harms)
    assert [r["movement"]["distance_before"] for r in records] == [4, 3]
    assert [r["movement"]["distance_after"] for r in records] == [3, 5]
    results = [classify_mutation(r) for r in records]
    assert [r["category"] for r in results] == ["builder_positioning"] * 2
    assert [r["tags"] for r in results] == [["closer_to_public_task"],
                                            ["farther_from_public_task"]]
    untargeted = mutation_records(t, progress, task_tiles=[], declared_losses=harms)
    assert untargeted[0]["movement"]["distance_before"] is None
    assert classify_mutation(untargeted[0])["tags"] == []


def movement_record(before, after, *, builder=True, **extra):
    record = {"step": 4, "declared_harm_ids": [], "objective_ids": [], "losses": [],
              "economic_changes": [],
              "movement": {"builder": builder, "unit": [0, 1], "from": [1, 1],
                           "to": [2, 1], "distance_before": before,
                           "distance_after": after},
              "coverage": {"loss": "complete"}}
    record.update(extra)
    return record


def test_builder_distance_tags_cover_closer_same_and_farther():
    assert classify_mutation(movement_record(3, 2))["tags"] == ["closer_to_public_task"]
    assert classify_mutation(movement_record(3, 3))["tags"] == [
        "same_distance_to_public_task"]
    assert classify_mutation(movement_record(3, 4))["tags"] == ["farther_from_public_task"]
    assert classify_mutation(movement_record(None, 4))["tags"] == []
    assert all(classify_mutation(movement_record(3, d))["primary_deduction"] == 0
               for d in (2, 3, 4))


def test_farm_on_own_tile_tag_from_record():
    record = movement_record(None, None, improvement={
        "type": "IMPROVEMENT_FARM", "tile": [1, 1], "on_unit_tile": True,
        "on_task_tile": False})
    assert "farm_on_own_tile" in classify_mutation(record)["tags"]
    record["improvement"]["on_unit_tile"] = False
    assert "farm_on_own_tile" not in classify_mutation(record)["tags"]


def test_precedence_keeps_secondary_effects_visible():
    ledger_entry = {"path": ["gold"], "before": 100, "after": 90, "delta": -10}
    record = movement_record(None, None, builder=False,
                             declared_harm_ids=["escort-lost"], tool_name="delete_unit",
                             tool_args={"unit_index": 9},
                             losses=[{"kind": "unit", "entity": [0, 9],
                                      "lifecycle": "lost", "declared": True}],
                             economic_changes=[ledger_entry])
    assert mutation_category(record) == "declared_harm"
    result = classify_mutation(record)
    assert result["category"] == "declared_harm"
    assert result["tags"] == ["non_builder_move"]
    assert result["primary_deduction"] == 0
    assert result["evidence"] == {
        "step": 4, "tool_name": "delete_unit", "losses": record["losses"],
        "economic_paths": [["gold"]], "movement": record["movement"],
        "improvement": None}


def test_record_without_evidence_is_insufficient_evidence():
    record = movement_record(None, None, movement=None)
    assert classify_mutation(record)["category"] == "insufficient_evidence"


def test_mutation_records_preserve_raw_tool_references():
    t = delete_warrior_trial()
    progress, harms = audit(t)
    (record,) = mutation_records(t, progress, task_tiles=[], declared_losses=harms)
    assert record["step"] == 0 and record["tool_name"] == "delete_unit"
    assert record["tool_args"] == {"unit_index": 9} and record["ok"] is True
    assert record["coverage"] == {"loss": "complete"}
    assert record["improvement"] is None and record["movement"] is None
    assert classify_mutation(record)["evidence"]["losses"][0]["entity"] == [0, 9]


# ---------------------------------------------------------------------------
# Under-credit audit
# ---------------------------------------------------------------------------

def test_undercredited_completion_reports_attained_credit_that_dropped():
    s0 = state(units=[builder()])
    s1 = state(units=[builder(charges=1)],
               tiles=[tile(improvement="IMPROVEMENT_FARM", food=3)])
    s2 = state(units=[builder(charges=0)])
    t = trial(s0, [step(0, s0, s1, "improve_tile",
                        {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"}),
                   step(1, s1, s2, "remove_improvement", {"unit_index": 1})])
    r = rubric()
    progress = attribute_progress(t, r)
    assert undercredited_completions(progress, score_trial(t, r)) == [{
        "objective_id": "farm", "max_attained": 2, "final_credit": 0, "steps": [0]}]
    kept = trial(s0, t["steps"][:1])
    assert undercredited_completions(attribute_progress(kept, r),
                                     score_trial(kept, r)) == []
