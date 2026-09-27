"""Positive-control position builder-posctrl-v1 (design:
docs/superpowers/specs/2026-09-27-builder-positive-control-design.md). Each
task builder starts ON its target; completion = the named improvement present
and intact; objective progress = the completion predicate only."""

import copy
import json
from pathlib import Path

import pytest

from civ_mcp.arena.action_metrics import evaluate_predicate
from civ_mcp.arena.benchmark_manifest import load_position_manifest, validate_position_contract
from civ_mcp.arena.benchmark_report import score_rubric
from civ_mcp.arena.benchmark_state import state_digest

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "benchmarks" / "positions" / "builder-posctrl-v1.yaml"
CAPTURE = REPO / "benchmarks" / "provenance" / "builder-posctrl-v1-capture.json"
TASKS = {"repair-iron-mine": ((68, 23), "IMPROVEMENT_MINE", 1769484),
         "pasture-horses": ((74, 30), "IMPROVEMENT_PASTURE", 1507329),
         "quarry-stone": ((71, 21), "IMPROVEMENT_QUARRY", 1572874)}


@pytest.fixture(scope="module")
def position():
    return load_position_manifest(MANIFEST)


def _tile(state, xy):
    return next(t for t in state["tiles"] if (t["x"], t["y"]) == xy)


def _observe():
    return [{"tool_name": "get_units", "tool_args": {}, "tool_result_full": "13 units: ..."}]


def _complete(state, task):
    xy, imp, _ = TASKS[task]
    t = _tile(state, xy)
    t["improvement"], t["pillaged"] = imp, False


def _score(position, final, steps=None):
    return score_rubric(position.rubric, initial_state=position.expected_state, final_state=final,
                        steps=_observe() if steps is None else steps)


def test_manifest_loads_contract_digest_and_shape(position):
    validate_position_contract(position)
    capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
    assert state_digest(position.expected_state) == position.expected_state_sha256 == capture["captured_state_sha256"]
    assert position.archive_sha256 == capture["provenance"]["archive_sha256"]
    assert {t["task_id"] for t in position.rubric} == set(TASKS) == {o["task_id"] for o in position.objectives}
    for task in position.rubric:
        assert [l["score"] for l in task["levels"]] == [1, 4]   # no level 2/3: on-target is the start state
    assert sum(max(l["score"] for l in t["levels"]) for t in position.rubric) == 12


def test_every_task_builder_starts_on_its_target_with_charges(position):
    units = {u["id"]: u for u in position.expected_state["units"]}
    for task, (xy, _imp, uid) in TASKS.items():
        assert (units[uid]["x"], units[uid]["y"]) == xy, task
        assert units[uid]["charges"] >= 2, task
    assert set(position.persistent_unit_ids) == {uid for *_x, uid in TASKS.values()}


def test_every_completion_and_progress_predicate_is_false_at_the_frozen_start(position):
    s = position.expected_state
    for task in position.rubric:
        assert not evaluate_predicate(task["levels"][-1]["predicate"], initial_state=s, final_state=s, steps=[]), task["task_id"]
    for objective in position.objectives:
        assert not evaluate_predicate(objective["progress_predicate"], initial_state=s, final_state=s, steps=[])
        # progress == completion; the entry-true 'builder on target' conditions are gone
        rubric_task = next(t for t in position.rubric if t["task_id"] == objective["task_id"])
        assert objective["progress_predicate"] == rubric_task["levels"][-1]["predicate"]


def test_floor_and_ceiling(position):
    s = position.expected_state
    assert _score(position, copy.deepcopy(s), steps=[])["raw_total"] == 0
    assert _score(position, copy.deepcopy(s))["raw_total"] == 3            # observed only (minimal's expected floor)
    final = copy.deepcopy(s)
    for task in TASKS:
        _complete(final, task)
    assert _score(position, final)["raw_total"] == 12


@pytest.mark.parametrize("task", sorted(TASKS))
def test_each_task_completes_independently(position, task):
    final = copy.deepcopy(position.expected_state)
    _complete(final, task)
    tasks = _score(position, final)["tasks"]
    assert tasks[task]["score"] == 4
    assert all(v["score"] == 1 for k, v in tasks.items() if k != task)


def test_a_pillaged_or_wrong_improvement_is_not_completion(position):
    final = copy.deepcopy(position.expected_state)
    t = _tile(final, (71, 21)); t["improvement"], t["pillaged"] = "IMPROVEMENT_QUARRY", True
    p = _tile(final, (74, 30)); p["improvement"] = "IMPROVEMENT_FARM"
    tasks = _score(position, final)["tasks"]
    assert tasks["quarry-stone"]["score"] == 1 and tasks["pasture-horses"]["score"] == 1
    # the repair task starts pillaged: removing the pillage flag alone without the mine is not completion
    m = _tile(final, (68, 23)); m["improvement"], m["pillaged"] = None, False
    assert _score(position, final)["tasks"]["repair-iron-mine"]["score"] == 1


def test_passes_the_treatment_can_fire_gate(position):
    from civ_mcp.arena.benchmark_admission import _standard_capabilities
    from civ_mcp.arena.benchmark_gates import check_treatment_can_fire

    assert check_treatment_can_fire(position=position, minimal_observation={"source": "authoring_validation"},
                                    standard_capabilities=_standard_capabilities()) is not None
