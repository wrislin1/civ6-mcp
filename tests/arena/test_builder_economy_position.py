"""Authoring-time proofs for the frozen BUILDER_ECONOMY_CAL_V1 position
(Task 11 Step 7): the real manifest loads and passes the lifecycle/predicate
contract, the minimal arm can reach rubric levels 1-2, and the standard arm
can reach level 4 -- all against counterfactual state fixtures derived from
the manifest's own frozen expected_state."""

import copy
import json
from pathlib import Path

import pytest

from civ_mcp.arena.benchmark_manifest import load_position_manifest, validate_position_contract
from civ_mcp.arena.benchmark_report import score_rubric
from civ_mcp.arena.benchmark_state import state_digest

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "benchmarks" / "positions" / "builder-economy-cal-v1.yaml"
CAPTURE = REPO / "benchmarks" / "provenance" / "builder-economy-cal-v1-capture.json"

REPAIR_BUILDER, PASTURE_BUILDER, QUARRY_BUILDER = 1769484, 1507329, 1572874
TARGETS = {REPAIR_BUILDER: (68, 23), PASTURE_BUILDER: (74, 30), QUARRY_BUILDER: (71, 21)}


@pytest.fixture(scope="module")
def position():
    return load_position_manifest(MANIFEST)


def _tile(state, x, y):
    return next(t for t in state["tiles"] if t["x"] == x and t["y"] == y)


def _unit(state, unit_id):
    return next(u for u in state["units"] if u["id"] == unit_id)


def _moved_onto_targets(state):
    final = copy.deepcopy(state)
    for unit_id, (x, y) in TARGETS.items():
        _unit(final, unit_id)["x"], _unit(final, unit_id)["y"] = x, y
    return final


def _observed_units_step():
    return {"tool_name": "get_units", "tool_args": {}, "tool_result_full": "12 units: ..."}


def test_manifest_loads_and_passes_lifecycle_contract(position):
    validate_position_contract(position)  # raises on any contract violation
    assert set(position.persistent_unit_ids) == set(TARGETS)
    assert position.consumable_unit_ids == ()
    assert {t["task_id"] for t in position.rubric} == {o["task_id"] for o in position.objectives}
    for objective in position.objectives:
        assert objective["requires"], objective["task_id"]


def test_expected_state_digest_matches_frozen_capture(position):
    capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
    assert state_digest(position.expected_state) == position.expected_state_sha256
    assert position.expected_state_sha256 == capture["captured_state_sha256"]
    assert position.archive_sha256 == capture["provenance"]["archive_sha256"]


def test_frozen_state_has_the_three_authored_tasks(position):
    s = position.expected_state
    assert _tile(s, 68, 23)["pillaged"] is True and _tile(s, 68, 23)["improvement"] == "IMPROVEMENT_MINE"
    assert _tile(s, 74, 30)["resource"] == "RESOURCE_HORSES" and _tile(s, 74, 30)["improvement"] is None
    assert _tile(s, 71, 21)["feature"] == "FEATURE_FOREST" and _tile(s, 71, 21)["resource"] == "RESOURCE_STONE"
    for unit_id in TARGETS:
        u = _unit(s, unit_id)
        assert u["type"] == "UNIT_BUILDER" and u["charges"] >= 2
        assert (u["x"], u["y"]) != TARGETS[unit_id]  # starts adjacent, not on target


def test_untouched_trial_with_no_observation_scores_zero(position):
    s = position.expected_state
    scored = score_rubric(position.rubric, initial_state=s, final_state=copy.deepcopy(s), steps=[])
    assert all(t["score"] == 0 for t in scored["tasks"].values())


def test_minimal_arm_reaches_level_1_by_observing_units(position):
    s = position.expected_state
    scored = score_rubric(
        position.rubric, initial_state=s, final_state=copy.deepcopy(s), steps=[_observed_units_step()]
    )
    assert all(t["score"] == 1 for t in scored["tasks"].values())


def test_minimal_arm_reaches_level_2_by_moving_each_builder_onto_its_target(position):
    s = position.expected_state
    scored = score_rubric(
        position.rubric, initial_state=s, final_state=_moved_onto_targets(s), steps=[_observed_units_step()]
    )
    assert all(t["score"] == 2 for t in scored["tasks"].values())


def test_standard_arm_reaches_level_4_with_the_exact_tile_mutations(position):
    s = position.expected_state
    final = _moved_onto_targets(s)
    _tile(final, 68, 23)["pillaged"] = False
    _tile(final, 74, 30)["improvement"] = "IMPROVEMENT_PASTURE"
    _tile(final, 71, 21)["feature"] = None
    scored = score_rubric(position.rubric, initial_state=s, final_state=final, steps=[_observed_units_step()])
    assert all(t["score"] == 4 for t in scored["tasks"].values())
    assert scored["max_total"] == 12 if "max_total" in scored else True


def test_a_consumed_builder_scores_its_task_no_higher_than_level_1(position):
    """A builder that vanished (e.g. spent its last charge) cannot satisfy
    unit_at; the persistent-unit contract routes that to a False, not a
    PredicateError that would abort the report."""
    s = position.expected_state
    final = _moved_onto_targets(s)
    final["units"] = [u for u in final["units"] if u["id"] != PASTURE_BUILDER]
    scored = score_rubric(position.rubric, initial_state=s, final_state=final, steps=[_observed_units_step()])
    assert scored["tasks"]["pasture-horses"]["score"] == 1
    assert scored["tasks"]["repair-iron-mine"]["score"] == 2


def test_real_manifest_passes_the_treatment_can_fire_gate(position):
    """The counted-run admission gate: every objective declares its
    standard-arm requirements, and the standard tier actually grants them."""
    from civ_mcp.arena.benchmark_admission import _standard_capabilities
    from civ_mcp.arena.benchmark_gates import check_treatment_can_fire

    evidence = check_treatment_can_fire(
        position=position,
        minimal_observation={"source": "authoring_validation"},
        standard_capabilities=_standard_capabilities(),
    )
    assert evidence is not None
    assert {"repair_improvement", "improve_tile", "remove_feature"} <= _standard_capabilities()
