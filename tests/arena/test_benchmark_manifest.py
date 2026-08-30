from pathlib import Path

import pytest

from civ_mcp.arena.backends import SamplingConfig
from civ_mcp.arena.benchmark_manifest import (
    PositionManifest,
    SuiteManifest,
    TreatmentArm,
    fingerprint,
    load_position_manifest,
    load_suite_manifest,
)


def _position_yaml() -> str:
    return """
position_id: builder-cal-v1
version: 1
archive: positions/builder-cal-v1.Civ6Save
archive_sha256: "abc123"
game_save_name: builder-cal-v1
player_id: 0
expected_state:
  turn: 42
  gold: 100
expected_state_sha256: "def456"
relevant_tiles:
  - [9, 24]
  - [9, 26]
objectives:
  - id: obj1
    description: improve the farm tile
rubric:
  - id: r1
    weight: 1.0
split: calibration
"""


def _suite_yaml() -> str:
    return """
suite_id: builder-cal-v1
driver: single_turn
positions:
  - builder-cal-v1
models:
  - qwen3.6-27b
arms:
  - arm_id: minimal
    tools: minimal
    options: {}
  - arm_id: standard
    tools: standard
    options: {}
seeds: [101, 102]
order: abba
sampling:
  temperature: 0.2
  top_p: 0.95
  seed: null
  max_tokens: 6144
max_steps: 15
result_char_cap: 6000
audit_indices: [1, 2]
"""


def test_manifest_fingerprint_changes_for_prompt_rubric_sampling_or_tool_arm():
    base = {
        "suite_id": "builder-cal-v1",
        "prompt_digest": "p1",
        "rubric_digest": "r1",
        "sampling": {"temperature": 0.2, "top_p": 0.95},
        "arms": [{"id": "minimal", "tools": "minimal"}],
    }
    digests = {
        fingerprint({**base, "prompt_digest": "p2"}),
        fingerprint({**base, "rubric_digest": "r2"}),
        fingerprint({**base, "sampling": {"temperature": 0.4}}),
        fingerprint({**base, "arms": [{"id": "standard", "tools": "standard"}]}),
    }
    assert len(digests) == 4


def test_fingerprint_is_stable_for_key_order():
    a = fingerprint({"a": 1, "b": 2})
    b = fingerprint({"b": 2, "a": 1})
    assert a == b


def test_load_position_manifest_round_trip(tmp_path: Path):
    path = tmp_path / "position.yaml"
    path.write_text(_position_yaml())

    manifest = load_position_manifest(path)

    assert manifest == PositionManifest(
        position_id="builder-cal-v1",
        version=1,
        archive="positions/builder-cal-v1.Civ6Save",
        archive_sha256="abc123",
        game_save_name="builder-cal-v1",
        player_id=0,
        expected_state={"turn": 42, "gold": 100},
        expected_state_sha256="def456",
        relevant_tiles=((9, 24), (9, 26)),
        objectives=({"id": "obj1", "description": "improve the farm tile"},),
        rubric=({"id": "r1", "weight": 1.0},),
        split="calibration",
    )
    assert isinstance(manifest.relevant_tiles, tuple)
    assert isinstance(manifest.relevant_tiles[0], tuple)
    assert isinstance(manifest.objectives, tuple)
    assert isinstance(manifest.rubric, tuple)


def test_load_position_manifest_rejects_unknown_key(tmp_path: Path):
    path = tmp_path / "position.yaml"
    path.write_text(_position_yaml() + "\nextra_field: nope\n")

    with pytest.raises(ValueError, match="unknown"):
        load_position_manifest(path)


def test_load_position_manifest_rejects_missing_key(tmp_path: Path):
    text = _position_yaml().replace("split: calibration\n", "")
    path = tmp_path / "position.yaml"
    path.write_text(text)

    with pytest.raises(ValueError, match="missing"):
        load_position_manifest(path)


def test_load_suite_manifest_round_trip(tmp_path: Path):
    path = tmp_path / "suite.yaml"
    path.write_text(_suite_yaml())

    manifest = load_suite_manifest(path)

    assert manifest == SuiteManifest(
        suite_id="builder-cal-v1",
        driver="single_turn",
        positions=("builder-cal-v1",),
        models=("qwen3.6-27b",),
        arms=(
            TreatmentArm("minimal", "minimal", {}),
            TreatmentArm("standard", "standard", {}),
        ),
        seeds=(101, 102),
        order="abba",
        sampling=SamplingConfig(temperature=0.2, top_p=0.95, seed=None, max_tokens=6144),
        max_steps=15,
        result_char_cap=6000,
        audit_indices=(1, 2),
    )
    assert isinstance(manifest.positions, tuple)
    assert isinstance(manifest.models, tuple)
    assert isinstance(manifest.arms, tuple)
    assert isinstance(manifest.seeds, tuple)
    assert isinstance(manifest.audit_indices, tuple)


def test_load_suite_manifest_rejects_unknown_key(tmp_path: Path):
    path = tmp_path / "suite.yaml"
    path.write_text(_suite_yaml() + "\nextra_field: nope\n")

    with pytest.raises(ValueError, match="unknown"):
        load_suite_manifest(path)


def test_load_suite_manifest_rejects_missing_key(tmp_path: Path):
    text = _suite_yaml().replace("driver: single_turn\n", "")
    path = tmp_path / "suite.yaml"
    path.write_text(text)

    with pytest.raises(ValueError, match="missing"):
        load_suite_manifest(path)


def test_load_suite_manifest_rejects_arm_missing_options_key(tmp_path: Path):
    text = _suite_yaml().replace(
        "  - arm_id: minimal\n    tools: minimal\n    options: {}\n",
        "  - arm_id: minimal\n    tools: minimal\n",
    )
    path = tmp_path / "suite.yaml"
    path.write_text(text)

    with pytest.raises(ValueError, match="missing"):
        load_suite_manifest(path)


def test_load_suite_manifest_rejects_sampling_missing_key(tmp_path: Path):
    text = _suite_yaml().replace("  seed: null\n", "")
    path = tmp_path / "suite.yaml"
    path.write_text(text)

    with pytest.raises(ValueError, match="missing"):
        load_suite_manifest(path)
