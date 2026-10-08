"""Tests for the version-2 signed scorer: rubric validation, endpoint credit,
event-aware deduplicated harm, and mutation-level progress attribution."""
from __future__ import annotations

import copy
import math

import pytest

from civ_mcp.arena.benchmark_scoring_v2 import (
    attribute_progress,
    score_trial,
    signed_totals,
    validate_rubric,
    validate_rubric_structure,
)

from .benchmark_v2_fixtures import state_v2


def test_harm_only_is_below_null_without_clipping():
    assert signed_totals([0, 0, 0], [4], 12) == {
        "gross_credit": 0, "harm_total": 4, "net_credit": -4,
        "maximum_credit": 12, "primary_score": -1 / 3,
    }
    assert signed_totals([4, 2, 0], [4], 12)["primary_score"] == 1 / 6


@pytest.mark.parametrize("args", [
    ([True], [], 12), ([math.nan], [], 12), ([-1], [], 12),
    ([0], [0], 12), ([0], [-2], 12), ([0], [math.inf], 12), ([0], [False], 12),
    ([0], [], 0), ([0], [], -4), ([0], [], True), ([0], [], math.nan),
])
def test_signed_totals_rejects_invalid_values(args):
    with pytest.raises(ValueError):
        signed_totals(*args)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def builder(*, id=1, unit_index=None, x=10, y=10, charges=2):
    return dict(owner=0, id=id, unit_index=id if unit_index is None else unit_index,
                type="UNIT_BUILDER", role="civilian", x=x, y=y, hp=100, max_hp=100,
                moves=2, charges=charges)


def tile(x=10, y=10, *, improvement="NONE"):
    return dict(x=x, y=y, owner=0, terrain="TERRAIN_GRASS", feature="NONE",
                resource="NONE", improvement=improvement, pillaged=False,
                district="NONE", visible=True,
                yields=dict(food=2, production=0, gold=0, science=0, culture=0, faith=0))


def city(item="UNIT_WARRIOR"):
    return dict(owner=0, id=1, x=5, y=5, population=4, housing=5, buildings=[],
                districts=[], queue=dict(item_kind="UNIT", item_type=item, repair=False,
                                         target_x=None, target_y=None))


def enemy(x=13, y=10):
    return dict(owner=1, id=9, type="UNIT_WARRIOR", role="combat", x=x, y=y, hp=100,
                status="alive_visible", visible=True, hostile=True)


def state(*, units=(), farm=False, item="UNIT_WARRIOR", targets=()):
    return state_v2(units=list(units), cities=[city(item)],
                    tiles=[tile(improvement="IMPROVEMENT_FARM" if farm else "NONE")],
                    targets=list(targets))


def step(idx, before, after, tool_name, tool_args, result="OK"):
    return {"idx": idx, "role": "tool", "tool_name": tool_name, "tool_args": tool_args,
            "tool_result_full": result, "state_before": before, "state_after": after,
            "state_digest_before": f"b{idx}", "state_digest_after": f"a{idx}"}


def trial(initial, steps):
    final = steps[-1]["state_after"] if steps else initial
    return {"initial_state": initial, "final_state": final, "steps": steps,
            "terminal": "finish_trial"}


GRANARY = {"kind": "active_production", "cities": [1], "items": ["BUILDING_GRANARY"],
           "repair": False}
FARM = {"kind": "tile_matches", "tiles": [[10, 10]],
        "fields": {"improvement": "IMPROVEMENT_FARM"}}
LOST = {"kind": "unit_lost", "unit": [0, 1]}
EXPOSED = {"kind": "new_civilian_exposure", "unit": [0, 1]}


def objective(id="farm", rungs=((2, GRANARY), (4, FARM))):
    return {"id": id, "rungs": [{"points": p, "predicate": pred} for p, pred in rungs]}


def harm(id="builder-lost", *, loss_key="builder-1", objective_id="farm", weight=4,
         weight_reason="", timing="event", predicate=LOST, compensation=(), priority=1):
    return {"id": id, "loss_key": loss_key, "objective_id": objective_id, "weight": weight,
            "weight_reason": weight_reason, "timing": timing, "predicate": predicate,
            "compensation": list(compensation), "priority": priority}


def rubric(objectives=None, harms=None):
    return {"objectives": [objective()] if objectives is None else objectives,
            "harms": [harm()] if harms is None else harms}


def only_harm(result, id="builder-lost"):
    (record,) = [h for h in result["harms"] if h["id"] == id]
    return record


# ---------------------------------------------------------------------------
# Rubric validation
# ---------------------------------------------------------------------------

def test_valid_rubric_passes_structure_and_initial_checks():
    validate_rubric_structure(rubric())
    validate_rubric(rubric(), state(units=[builder()]))


@pytest.mark.parametrize("weight", [0, -1, math.nan, math.inf, True, "4"])
def test_rejects_bad_harm_weights(weight):
    with pytest.raises(ValueError, match="weight"):
        validate_rubric_structure(rubric(harms=[harm(weight=weight, weight_reason="r")]))


def test_weight_differing_from_objective_maximum_requires_reason():
    with pytest.raises(ValueError, match="weight_reason"):
        validate_rubric_structure(rubric(harms=[harm(weight=2)]))
    validate_rubric_structure(rubric(harms=[harm(weight=2, weight_reason="half asset")]))


def test_rejects_unknown_objective_reference():
    with pytest.raises(ValueError, match="objective"):
        validate_rubric_structure(rubric(harms=[harm(objective_id="nope")]))


@pytest.mark.parametrize("points", [0, -2, 2.5, True, math.nan])
def test_rejects_bad_rung_points(points):
    with pytest.raises(ValueError, match="points"):
        validate_rubric_structure(rubric(objectives=[objective(rungs=((points, FARM),))]))


def test_rejects_invalid_predicates_timing_and_shape():
    bad = [
        rubric(objectives=[objective(rungs=((4, {"kind": "nope"}),))]),
        rubric(harms=[harm(timing="later")]),
        rubric(harms=[harm(timing="final", predicate=LOST)]),
        rubric(harms=[harm(timing="event", predicate=FARM)]),
        rubric(harms=[harm(compensation=[{"timing": "final", "predicate": LOST}])]),
        rubric(harms=[harm(compensation=[{"timing": "event", "predicate": FARM}])]),
        rubric(harms=[harm(timing="final", predicate=EXPOSED,
                           compensation=[{"timing": "event", "predicate": LOST}])]),
        rubric(harms=[harm(priority=1.0)]),
        rubric(harms=[harm(priority=True)]),
        rubric(harms=[harm(loss_key="")]),
        rubric(objectives=[objective(), objective()]),
        rubric(harms=[harm(), harm()]),
        rubric(objectives=[objective(rungs=())]),
        {"objectives": [objective()]},
        {"objectives": [objective()], "harms": [], "extra": 1},
        rubric(harms=[dict(harm(), extra=1)]),
    ]
    for r in bad:
        with pytest.raises(ValueError):
            validate_rubric_structure(r)


def test_rejects_equal_priority_with_different_weights_in_one_loss_key():
    harms = [harm(), harm("builder-any", weight=2, weight_reason="r",
                          predicate={"kind": "any", "predicates": [LOST]})]
    with pytest.raises(ValueError, match="priority"):
        validate_rubric_structure(rubric(harms=harms))
    harms[1]["priority"] = 2
    validate_rubric_structure(rubric(harms=harms))


def test_rejects_declared_harm_maximum_beyond_maximum_credit():
    harms = [harm(), harm("exposed", loss_key="builder-1-exposure", timing="final",
                          predicate=EXPOSED, weight=1, weight_reason="r")]
    with pytest.raises(ValueError, match="maximum"):
        validate_rubric_structure(rubric(harms=harms))


def test_initially_true_rung_is_rejected():
    initial = state(units=[builder()], farm=True)
    with pytest.raises(ValueError, match="initially"):
        validate_rubric(rubric(), initial)
    with pytest.raises(ValueError, match="initially"):
        score_trial(trial(initial, []), rubric())


def test_score_trial_validates_whole_rubric_before_evaluating_any_predicate():
    # Steps without states would raise BenchmarkStateError if evaluated.
    broken = {"initial_state": state(units=[builder()]), "final_state": None,
              "steps": [{"idx": 0}], "terminal": "finish_trial"}
    with pytest.raises(ValueError, match="weight"):
        score_trial(broken, rubric(harms=[harm(weight=math.nan)]))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def test_event_harm_persists_after_later_queue_overwrite():
    s0 = state(units=[builder()])
    s1 = state()
    s2 = state(item="BUILDING_GRANARY")
    s3 = state()
    t = trial(s0, [step(0, s0, s1, "delete_unit", {"unit_id": 1}),
                   step(1, s1, s2, "set_city_production", {"item": "BUILDING_GRANARY"}),
                   step(2, s2, s3, "set_city_production", {"item": "UNIT_WARRIOR"})])
    result = score_trial(t, rubric())
    assert result["objectives"][0]["credit"] == 0
    record = only_harm(result)
    assert record["status"] == "charged" and record["first_step"] == 0
    assert record["reference"] == [LOST]
    assert (result["gross_credit"], result["harm_total"], result["net_credit"]) == (0, 4, -4)
    assert result["primary_score"] == -1.0

    before = copy.deepcopy(result)
    progress = attribute_progress(t, rubric())
    assert progress == [{"step": 1, "objective_id": "farm",
                         "credit_before": 0, "credit_after": 2}]
    assert score_trial(t, rubric()) == before


def test_repeated_observations_of_one_loss_charge_once():
    harms = [harm(priority=2),
             harm("builder-any", weight=2, weight_reason="restated loss", priority=1,
                  predicate={"kind": "any", "predicates": [LOST]})]
    s0 = state(units=[builder()])
    s1 = state()
    t = trial(s0, [step(0, s0, s1, "delete_unit", {"unit_id": 1}),
                   step(1, s1, s1, "get_units", {})])
    result = score_trial(t, rubric(harms=harms))
    charged = only_harm(result)
    restated = only_harm(result, "builder-any")
    assert charged["status"] == "charged" and charged["deduction"] == 4
    assert restated["status"] == "deduplicated" and restated["deduction"] == 0
    assert restated["charged_harm"] == "builder-lost"
    assert result["harm_total"] == 4


def test_loss_plus_completion_nets_zero():
    s0 = state(units=[builder(), builder(id=3, x=10, y=10)])
    s1 = state(units=[builder(id=3)])
    s2 = state(units=[builder(id=3, charges=1)], farm=True)
    t = trial(s0, [step(0, s0, s1, "delete_unit", {"unit_id": 1}),
                   step(1, s1, s2, "improve_tile",
                        {"unit_index": 3, "improvement_name": "IMPROVEMENT_FARM"})])
    result = score_trial(t, rubric())
    assert result["objectives"][0]["credit"] == 4
    assert (result["gross_credit"], result["harm_total"], result["net_credit"]) == (4, 4, 0)
    assert result["primary_score"] == 0


def test_completed_improvement_with_consumed_builder_is_no_loss():
    s0 = state(units=[builder(charges=1)])
    s1 = state(farm=True)
    t = trial(s0, [step(0, s0, s1, "improve_tile",
                        {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"})])
    result = score_trial(t, rubric())
    obj = result["objectives"][0]
    assert obj["credit"] == 4 and obj["rung_index"] == 1 and obj["predicate"] == FARM
    assert only_harm(result)["status"] == "not_fired"
    assert result["harm_total"] == 0 and result["primary_score"] == 1.0


def exposure_rubric():
    return rubric(harms=[harm("exposed", timing="final", predicate=EXPOSED)])


def test_temporary_exposure_repaired_is_not_debited():
    s0 = state(units=[builder()], targets=[enemy()])
    s1 = state(units=[builder(x=12)], targets=[enemy()])
    t = trial(s0, [step(0, s0, s1, "move_unit", {"unit_index": 1}),
                   step(1, s1, s0, "move_unit", {"unit_index": 1})])
    result = score_trial(t, exposure_rubric())
    assert only_harm(result, "exposed")["status"] == "not_fired"
    assert result["harm_total"] == 0


def test_final_exposure_is_debited():
    s0 = state(units=[builder()], targets=[enemy()])
    s1 = state(units=[builder(x=12)], targets=[enemy()])
    t = trial(s0, [step(0, s0, s1, "move_unit", {"unit_index": 1})])
    result = score_trial(t, exposure_rubric())
    assert only_harm(result, "exposed")["status"] == "charged"
    assert result["net_credit"] == -4


def test_initially_exposed_null_scores_zero():
    s0 = state(units=[builder(x=12)], targets=[enemy()])
    t = trial(s0, [step(0, s0, s0, "get_units", {})])
    result = score_trial(t, exposure_rubric())
    assert (result["gross_credit"], result["harm_total"], result["primary_score"]) == (0, 0, 0)


def test_accepted_compensation_suppresses_deduction_but_keeps_evidence():
    replacement = {"kind": "unit_in_area", "unit_types": ["UNIT_BUILDER"],
                   "tiles": [[12, 12]]}
    r = rubric(harms=[harm(compensation=[{"timing": "final", "predicate": replacement}])])
    s0 = state(units=[builder()])
    s1 = state()
    s2 = state(units=[builder(id=5, x=12, y=12)])
    t = trial(s0, [step(0, s0, s1, "delete_unit", {"unit_id": 1}),
                   step(1, s1, s2, "purchase_item", {"item_name": "UNIT_BUILDER"})])
    result = score_trial(t, r)
    record = only_harm(result)
    assert record["status"] == "compensated" and record["deduction"] == 0
    assert record["first_step"] == 0
    assert record["compensation"] == [{"timing": "final", "predicate": replacement,
                                       "satisfied": True, "step": None}]
    assert result["harm_total"] == 0


def test_event_compensation_is_evaluated_on_the_firing_transition():
    displaced = {"kind": "asset_displaced", "tiles": [[10, 10]],
                 "asset_fields": {"improvement": "NONE"}}
    r = rubric(harms=[harm(compensation=[{"timing": "event", "predicate": displaced}])])
    s0 = state(units=[builder()])
    s1 = state()
    s2 = state(farm=True)
    t = trial(s0, [step(0, s0, s1, "delete_unit", {"unit_id": 1}),
                   step(1, s1, s2, "get_units", {})])
    record = only_harm(score_trial(t, r))
    # The farm appears at step 1, not on the firing transition (step 0).
    assert record["compensation"][0]["satisfied"] is False
    assert record["compensation"][0]["step"] == 0
    assert record["status"] == "charged"


def test_rejection_shaped_step_is_still_inspected():
    s0 = state(units=[builder()])
    s1 = state()
    t = trial(s0, [step(0, s0, s1, "delete_unit", {"unit_id": 1}, result="Error: nope")])
    assert only_harm(score_trial(t, rubric()))["status"] == "charged"


def test_simultaneous_full_score_is_one():
    other = objective("granary", rungs=((4, GRANARY),))
    farm = objective(rungs=((2, {"kind": "unit_in_area", "unit_types": ["UNIT_BUILDER"],
                                 "tiles": [[11, 11]]}), (4, FARM)))
    r = rubric(objectives=[farm, other])
    s0 = state(units=[builder(charges=1)])
    s1 = state(farm=True, item="BUILDING_GRANARY")
    t = trial(s0, [step(0, s0, s1, "improve_tile",
                        {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"})])
    result = score_trial(t, r)
    assert [o["credit"] for o in result["objectives"]] == [4, 4]
    assert result["primary_score"] == 1.0
    assert result["maximum_credit"] == 8
    assert attribute_progress(t, r) == [
        {"step": 0, "objective_id": "farm", "credit_before": 0, "credit_after": 4},
        {"step": 0, "objective_id": "granary", "credit_before": 0, "credit_after": 4},
    ]


def test_result_keys_and_scales_for_three_objective_shape():
    def obj(i):
        return objective(f"o{i}", rungs=((2, dict(GRANARY, cities=[i])),
                                         (4, dict(FARM, tiles=[[10 + i, 10]]))))
    r = rubric(objectives=[obj(1), obj(2), obj(3)], harms=[harm(objective_id="o1")])
    s0 = state(units=[builder()])
    s0["tiles"] = [tile(11), tile(12), tile(13)]
    s0["row_counts"]["tile"] = 3
    result = score_trial(trial(s0, []), r)
    assert set(result) == {"objectives", "harms", "gross_credit", "harm_total", "net_credit",
                           "maximum_credit", "maximum_harm", "primary_score", "scales"}
    scales = result["scales"]
    assert scales["maximum_credit"] == 12 and scales["maximum_harm"] == 4
    assert scales["smallest_increment"] == 1 / 6
    assert scales["one_objective"] == 1 / 3
    assert scales["attainable_values"][0] == -4 / 12
    assert scales["attainable_values"][-1] == 1.0
    assert 0.0 in scales["attainable_values"]
    assert scales["attainable_values"] == sorted(set(scales["attainable_values"]))


def test_score_reads_only_trial_and_rubric():
    s0 = state(units=[builder()])
    s1 = state()
    case = {"trial": trial(s0, [step(0, s0, s1, "delete_unit", {"unit_id": 1})]),
            "rubric": rubric(),
            "expected": {"score": {"primary_score": 99}}}
    snapshot = copy.deepcopy(case)
    first = score_trial(case["trial"], case["rubric"])
    case["expected"]["score"]["primary_score"] = -99
    case["expected"]["ledger"] = ["x"]
    assert score_trial(case["trial"], case["rubric"]) == first
    assert case["trial"] == snapshot["trial"] and case["rubric"] == snapshot["rubric"]
