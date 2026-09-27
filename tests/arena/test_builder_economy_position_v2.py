"""Position v2 (builder-economy-cal-v2): identical archive, frozen state and
tasks 1-2 as v1; task 3 scores the OUTCOME (quarried stone) instead of the
route (forest removed). Motivated by the 2026-09-27 uncredited-actions audit:
the game accepts a quarry on the forested stone tile, and v1's level 4
predicate ("feature absent") awarded such a quarry only level 2."""

import copy
import json
from pathlib import Path

import pytest
import yaml

from civ_mcp.arena.benchmark_manifest import load_position_manifest, validate_position_contract
from civ_mcp.arena.benchmark_report import score_rubric
from civ_mcp.arena.benchmark_state import state_digest

REPO = Path(__file__).resolve().parents[2]
V1 = REPO / "benchmarks" / "positions" / "builder-economy-cal-v1.yaml"
V2 = REPO / "benchmarks" / "positions" / "builder-economy-cal-v2.yaml"
CAPTURE = REPO / "benchmarks" / "provenance" / "builder-economy-cal-v1-capture.json"
QUARRY_BUILDER = 1572874


@pytest.fixture(scope="module")
def position():
    return load_position_manifest(V2)


def _tile(state, x, y):
    return next(t for t in state["tiles"] if t["x"] == x and t["y"] == y)


def _observe():
    return [{"tool_name": "get_units", "tool_args": {}, "tool_result_full": "13 units: ..."}]


def _quarry_task(position, final):
    scored = score_rubric(position.rubric, initial_state=position.expected_state, final_state=final, steps=_observe())
    return scored["tasks"]["quarry-stone"]["score"]


def _on_target(state):
    final = copy.deepcopy(state)
    u = next(u for u in final["units"] if u["id"] == QUARRY_BUILDER)
    u["x"], u["y"] = 71, 21
    return final


def test_v2_loads_passes_contract_and_keeps_v1_frozen_evidence(position):
    validate_position_contract(position)
    v1 = yaml.safe_load(V1.read_text())
    v2 = yaml.safe_load(V2.read_text())
    for key in ("archive", "archive_sha256", "game_save_name", "player_id", "expected_state",
                "expected_state_sha256", "relevant_tiles", "persistent_unit_ids", "consumable_unit_ids", "split"):
        assert v1[key] == v2[key], key
    assert state_digest(position.expected_state) == json.loads(CAPTURE.read_text())["captured_state_sha256"]
    # tasks 1 and 2 are byte-identical to v1; only task 3 changed
    assert v1["rubric"][:2] == v2["rubric"][:2] and v1["objectives"][:2] == v2["objectives"][:2]
    assert [t["task_id"] for t in v2["rubric"]] == ["repair-iron-mine", "pasture-horses", "quarry-stone"]
    assert sum(max(l["score"] for l in t["levels"]) for t in v2["rubric"]) == 12


def test_quarry_on_the_forested_tile_is_completion(position):
    final = _on_target(position.expected_state)
    _tile(final, 71, 21)["improvement"] = "IMPROVEMENT_QUARRY"   # forest stays, as the game allows
    assert _tile(final, 71, 21)["feature"] == "FEATURE_FOREST"
    assert _quarry_task(position, final) == 4


def test_forest_removed_without_quarry_is_prerequisite_credit_only(position):
    final = _on_target(position.expected_state)
    _tile(final, 71, 21)["feature"] = None
    assert _quarry_task(position, final) == 3


def test_forest_removed_and_quarried_is_completion(position):
    final = _on_target(position.expected_state)
    _tile(final, 71, 21)["feature"] = None
    _tile(final, 71, 21)["improvement"] = "IMPROVEMENT_QUARRY"
    assert _quarry_task(position, final) == 4


def test_pillaged_quarry_is_not_completion(position):
    final = _on_target(position.expected_state)
    _tile(final, 71, 21)["improvement"] = "IMPROVEMENT_QUARRY"
    _tile(final, 71, 21)["pillaged"] = True
    assert _quarry_task(position, final) == 2


def test_builder_on_target_and_observation_levels_unchanged(position):
    assert _quarry_task(position, copy.deepcopy(position.expected_state)) == 1
    assert _quarry_task(position, _on_target(position.expected_state)) == 2


def test_v2_passes_the_treatment_can_fire_gate(position):
    from civ_mcp.arena.benchmark_admission import _standard_capabilities
    from civ_mcp.arena.benchmark_gates import check_treatment_can_fire

    assert check_treatment_can_fire(
        position=position,
        minimal_observation={"source": "authoring_validation"},
        standard_capabilities=_standard_capabilities(),
    ) is not None
