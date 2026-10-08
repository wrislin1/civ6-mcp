"""Tests for strict version-2 toolset and input-document loaders."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from civ_mcp.arena.benchmark_manifest_v2 import (
    load_toolset,
    load_v2_document,
    validate_v2_document,
)

REPO = Path(__file__).resolve().parents[2]
TOOLSET = REPO / "benchmarks/toolsets/plan3-part1-v1.yaml"

PART1_TOOLS = [
    "get_overview", "get_units", "get_cities", "move_unit", "found_city",
    "set_city_production", "set_research", "fortify_unit", "skip_unit",
    "get_unit_promotions", "promote_unit", "get_map_area", "get_tech_civics",
    "attack_unit", "get_builder_tasks", "improve_tile", "remove_feature",
    "repair_improvement", "get_great_people", "recruit_great_person",
    "activate_great_person", "purchase_item", "heal_unit", "alert_unit",
    "set_civic", "get_pending_diplomacy", "respond_to_diplomacy",
    "get_pending_trades", "respond_to_trade", "get_city_production",
    "get_district_advisor", "get_purchasable_tiles", "purchase_tile",
    "get_pathing_estimate", "get_empire_resources",
]


def test_part1_toolset_is_explicit_and_has_production_discovery():
    tools = load_toolset(TOOLSET)
    assert tools["toolset_id"] == "plan3-part1-v1"
    assert len(tools["game_tools"]) == len(set(tools["game_tools"])) == 35
    assert tools["game_tools"] == PART1_TOOLS
    assert tools["game_tools"][-6:] == [
        "get_city_production", "get_district_advisor", "get_purchasable_tiles",
        "purchase_tile", "get_pathing_estimate", "get_empire_resources",
    ]
    assert tools["schemas"][-1]["function"]["name"] == "finish_trial"
    assert [s["function"]["name"] for s in tools["schemas"][:-1]] == PART1_TOOLS
    assert "end_turn" not in tools["game_tools"]
    assert set(tools["identity"]) == {"source_sha256", "schemas_sha256"}


def _write_toolset(tmp_path, tools):
    path = tmp_path / "tools.yaml"
    path.write_text(yaml.safe_dump({"toolset_id": "t", "game_tools": tools}))
    return path


def test_mutable_alias_is_rejected(tmp_path):
    path = tmp_path / "tools.yaml"
    path.write_text("toolset_id: bad\ngame_tools: standard\n")
    with pytest.raises(ValueError, match="explicit.*list"):
        load_toolset(path)


def test_duplicates_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        load_toolset(_write_toolset(tmp_path, ["get_units", "get_units"]))


def test_unknown_name_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown"):
        load_toolset(_write_toolset(tmp_path, ["get_units", "no_such_tool"]))


def test_end_turn_rejected(tmp_path):
    with pytest.raises(ValueError, match="end_turn"):
        load_toolset(_write_toolset(tmp_path, ["get_units", "end_turn"]))


def test_finish_trial_in_game_tools_rejected(tmp_path):
    with pytest.raises(ValueError, match="finish_trial"):
        load_toolset(_write_toolset(tmp_path, ["get_units", "finish_trial"]))


def test_unknown_toolset_key_rejected(tmp_path):
    path = tmp_path / "tools.yaml"
    path.write_text("toolset_id: t\ngame_tools: [get_units]\nextra: 1\n")
    with pytest.raises(ValueError, match="extra"):
        load_toolset(path)


def test_schema_change_changes_identity(monkeypatch):
    base = load_toolset(TOOLSET)
    from civ_mcp.arena import benchmark_manifest_v2 as m2

    real = m2.resolved_benchmark_tools

    def changed(names):
        out = copy.deepcopy(real(names))
        out[0]["function"]["description"] += " changed"
        return out

    monkeypatch.setattr(m2, "resolved_benchmark_tools", changed)
    other = load_toolset(TOOLSET)
    assert other["identity"]["schemas_sha256"] != base["identity"]["schemas_sha256"]
    assert other["identity"]["source_sha256"] == base["identity"]["source_sha256"]


# ---- generic envelopes -------------------------------------------------

REF = {"path": "x.bin", "sha256": "a" * 64}

VALID = {
    "position": {
        "schema_version": "2.0.0", "position_id": "p", "version": 1,
        "family": "f", "split": "development",
        "archive": {"path": "a.zip", "sha256": "a" * 64},
        "game_save_name": "save", "player_id": 0,
        "expected_state": {"turn": 1}, "expected_state_sha256": "b" * 64,
        "coverage": {"x": 1},
        "toolset": {"path": "t.yaml", "identity": {"source_sha256": "c", "schemas_sha256": "d"}},
        "contract_identity": "e" * 64,
        "rubric": {"objectives": [{"id": "o", "rungs": [{"points": 4, "predicate": {
            "kind": "unit_in_area", "unit_types": ["UNIT_BUILDER"], "tiles": [[1, 2]]}}]}],
            "harms": []},
        "provenance": REF, "public_observation": REF,
        "public_task_tiles": [[1, 2]], "pilot_informed": False,
    },
    "script": {
        "schema_version": "2.0.0", "script_id": "s",
        "batches": [
            {"calls": [{"name": "get_units", "arguments": {}}]},
            {"calls": [{"name": "finish_trial", "arguments": {}}]},
        ],
    },
    "case": {
        "schema_version": "2.0.0", "case_id": "c",
        "position": REF, "script": REF, "tags": ["a"],
        "expected": {"score": {}, "endpoints": [], "ledger": []},
    },
    "validation_suite": {
        "schema_version": "2.0.0", "suite_id": "s",
        "cases": [REF], "position": REF,
        "toolset_identity": {"source_sha256": "c", "schemas_sha256": "d"},
        "contract_identity": "e" * 64,
        "max_steps": 15, "episode_wall_s": 300, "result_char_cap": 4000,
        "actor_kind": "scripted", "counting": False,
    },
    "lock": {
        "schema_version": "2.0.0", "lock_id": "l", "suite": REF,
        "scripts": [REF], "cases": [REF],
        "archive_identity": {"sha256": "a"}, "state_identity": {"sha256": "b"},
        "provenance_identity": {"sha256": "c"},
        "schedule": [{"case_id": "c"}],
        "code_identity": "f" * 64, "schema_identity": "g" * 64,
        "limits": {"max_steps": 15},
        "actor": {"actor_kind": "scripted"},
        "model": None, "seed": None, "token_budget": None,
        "cost": None, "latency": None,
    },
}


@pytest.mark.parametrize("kind", sorted(VALID))
def test_valid_documents_pass(kind):
    validate_v2_document(copy.deepcopy(VALID[kind]), kind=kind)


@pytest.mark.parametrize("kind", sorted(VALID))
def test_unknown_key_rejected(kind):
    raw = copy.deepcopy(VALID[kind])
    raw["surprise"] = 1
    with pytest.raises(ValueError, match="surprise"):
        validate_v2_document(raw, kind=kind)


@pytest.mark.parametrize("kind", sorted(VALID))
def test_missing_key_rejected(kind):
    raw = copy.deepcopy(VALID[kind])
    raw.pop("schema_version")
    with pytest.raises(ValueError, match="schema_version"):
        validate_v2_document(raw, kind=kind)


@pytest.mark.parametrize("kind", sorted(VALID))
def test_wrong_schema_version_rejected(kind):
    raw = copy.deepcopy(VALID[kind])
    raw["schema_version"] = "1.0.0"
    with pytest.raises(ValueError, match="2.0.0"):
        validate_v2_document(raw, kind=kind)


def test_unknown_kind_and_non_mapping_rejected():
    with pytest.raises(ValueError, match="kind"):
        validate_v2_document({}, kind="nope")
    with pytest.raises(ValueError, match="mapping"):
        validate_v2_document([1], kind="script")  # type: ignore[arg-type]


def test_script_must_end_with_finish_trial_in_last_batch():
    raw = copy.deepcopy(VALID["script"])
    raw["batches"].append({"calls": [{"name": "get_units", "arguments": {}}]})
    with pytest.raises(ValueError, match="finish_trial"):
        validate_v2_document(raw, kind="script")
    raw = copy.deepcopy(VALID["script"])
    raw["batches"][0]["calls"].append({"name": "finish_trial", "arguments": {}})
    with pytest.raises(ValueError, match="finish_trial"):
        validate_v2_document(raw, kind="script")


def test_script_rejects_expectation_keys_and_bad_calls():
    raw = copy.deepcopy(VALID["script"])
    raw["rubric"] = {}
    with pytest.raises(ValueError, match="rubric"):
        validate_v2_document(raw, kind="script")
    raw = copy.deepcopy(VALID["script"])
    raw["batches"][0]["calls"][0]["arguments"] = "x"
    with pytest.raises(ValueError, match="arguments"):
        validate_v2_document(raw, kind="script")
    raw = copy.deepcopy(VALID["script"])
    raw["batches"][0]["calls"][0]["expect"] = 1
    with pytest.raises(ValueError, match="expect"):
        validate_v2_document(raw, kind="script")


def test_case_expected_keys_exact():
    raw = copy.deepcopy(VALID["case"])
    raw["expected"].pop("ledger")
    with pytest.raises(ValueError, match="ledger"):
        validate_v2_document(raw, kind="case")


def test_position_split_values():
    raw = copy.deepcopy(VALID["position"])
    raw["split"] = "held_out"
    validate_v2_document(raw, kind="position")
    raw["split"] = "test"
    with pytest.raises(ValueError, match="split"):
        validate_v2_document(raw, kind="position")


def test_position_rubric_shape():
    raw = copy.deepcopy(VALID["position"])
    raw["rubric"] = {"objectives": []}
    with pytest.raises(ValueError, match="harms"):
        validate_v2_document(raw, kind="position")


def test_position_rubric_is_structurally_validated():
    raw = copy.deepcopy(VALID["position"])
    raw["rubric"]["objectives"][0]["rungs"][0]["points"] = 0
    with pytest.raises(ValueError, match="points"):
        validate_v2_document(raw, kind="position")
    raw = copy.deepcopy(VALID["position"])
    raw["rubric"]["objectives"] = [{}]
    with pytest.raises(ValueError, match="objectives"):
        validate_v2_document(raw, kind="position")


def test_suite_fixed_operating_values():
    for key, bad in [("max_steps", 8), ("episode_wall_s", 100),
                     ("actor_kind", "model"), ("counting", True)]:
        raw = copy.deepcopy(VALID["validation_suite"])
        raw[key] = bad
        with pytest.raises(ValueError, match=key):
            validate_v2_document(raw, kind="validation_suite")


def test_lock_nullable_fields_must_be_null():
    raw = copy.deepcopy(VALID["lock"])
    raw["model"] = "gemma"
    with pytest.raises(ValueError, match="model"):
        validate_v2_document(raw, kind="lock")


def test_load_resolves_relative_paths_without_requiring_existence(tmp_path):
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump(VALID["case"]))
    loaded = load_v2_document(path, kind="case")
    assert loaded["position"]["path"] == str(tmp_path / "x.bin")
    assert loaded["script"]["sha256"] == "a" * 64
    assert yaml.safe_load(path.read_text())["position"]["path"] == "x.bin"


def test_load_rejects_non_mapping_and_keeps_absolute_paths(tmp_path):
    path = tmp_path / "d.yaml"
    path.write_text("- 1\n")
    with pytest.raises(ValueError, match="mapping"):
        load_v2_document(path, kind="script")
    doc = copy.deepcopy(VALID["case"])
    doc["position"]["path"] = "/abs/p.yaml"
    path.write_text(yaml.safe_dump(doc))
    assert load_v2_document(path, kind="case")["position"]["path"] == "/abs/p.yaml"


def test_load_position_resolves_nested_paths(tmp_path):
    path = tmp_path / "pos.yaml"
    path.write_text(yaml.safe_dump(VALID["position"]))
    loaded = load_v2_document(path, kind="position")
    for key, sub in [("archive", "a.zip"), ("toolset", "t.yaml"),
                     ("provenance", "x.bin"), ("public_observation", "x.bin")]:
        assert loaded[key]["path"] == str(tmp_path / sub)
