import re
from pathlib import Path

import pytest
import yaml

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


def _position_data() -> dict:
    return yaml.safe_load(_position_yaml())


def _suite_data() -> dict:
    return yaml.safe_load(_suite_yaml())


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize(
    ("manifest_kind", "field", "value", "message"),
    [
        ("position", "relevant_tiles", None, "position manifest.relevant_tiles must be a list"),
        ("position", "expected_state", None, "position manifest.expected_state must be a mapping"),
        ("suite", "arms", None, "suite manifest.arms must be a list"),
        ("suite", "seeds", None, "suite manifest.seeds must be a list"),
        ("suite", "max_steps", None, "suite manifest.max_steps must be an integer"),
        ("suite", "sampling", None, "suite manifest.sampling must be a mapping"),
        ("position", "objectives", None, "position manifest.objectives must be a list"),
        ("position", "rubric", None, "position manifest.rubric must be a list"),
        ("suite", "positions", None, "suite manifest.positions must be a list"),
        ("suite", "models", None, "suite manifest.models must be a list"),
        ("suite", "audit_indices", None, "suite manifest.audit_indices must be a list"),
    ],
)
def test_manifest_nulls_raise_field_specific_value_errors(
    tmp_path: Path, manifest_kind: str, field: str, value: object, message: str
):
    if manifest_kind == "position":
        data = _position_data()
        data[field] = value
        path = _write(tmp_path, "position.yaml", data)
        with pytest.raises(ValueError, match=re.escape(message)):
            load_position_manifest(path)
    else:
        data = _suite_data()
        data[field] = value
        path = _write(tmp_path, "suite.yaml", data)
        with pytest.raises(ValueError, match=re.escape(message)):
            load_suite_manifest(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", True),
        ("player_id", True),
    ],
)
def test_load_position_manifest_rejects_boolean_for_integer_fields(
    tmp_path: Path, field: str, value: object
):
    data = _position_data()
    data[field] = value
    path = _write(tmp_path, "position.yaml", data)

    with pytest.raises(ValueError, match=re.escape(f"position manifest.{field} must be an integer")):
        load_position_manifest(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_steps", True),
        ("result_char_cap", True),
    ],
)
def test_load_suite_manifest_rejects_boolean_for_integer_fields(
    tmp_path: Path, field: str, value: object
):
    data = _suite_data()
    data[field] = value
    path = _write(tmp_path, "suite.yaml", data)

    with pytest.raises(ValueError, match=re.escape(f"suite manifest.{field} must be an integer")):
        load_suite_manifest(path)


@pytest.mark.parametrize(
    "tile",
    [
        [9],
        [9, 24, 1],
        ["a", 24],
        [9, True],
        None,
    ],
)
def test_load_position_manifest_rejects_malformed_tile(tmp_path: Path, tile: object):
    data = _position_data()
    data["relevant_tiles"] = [tile]
    path = _write(tmp_path, "position.yaml", data)

    with pytest.raises(ValueError, match=re.escape("position manifest.relevant_tiles[0]")):
        load_position_manifest(path)


@pytest.mark.parametrize("bad_seed", ["nope", True, 1.5])
def test_load_suite_manifest_rejects_non_integer_seed(tmp_path: Path, bad_seed: object):
    data = _suite_data()
    data["seeds"] = [101, bad_seed]
    path = _write(tmp_path, "suite.yaml", data)

    with pytest.raises(ValueError, match=re.escape("suite manifest.seeds[1] must be an integer")):
        load_suite_manifest(path)


@pytest.mark.parametrize("bad_index", ["nope", True, 1.5])
def test_load_suite_manifest_rejects_non_integer_audit_index(tmp_path: Path, bad_index: object):
    data = _suite_data()
    data["audit_indices"] = [1, bad_index]
    path = _write(tmp_path, "suite.yaml", data)

    with pytest.raises(ValueError, match=re.escape("suite manifest.audit_indices[1] must be an integer")):
        load_suite_manifest(path)


@pytest.mark.parametrize(
    "field",
    ["position_id", "archive", "archive_sha256", "game_save_name", "split"],
)
def test_load_position_manifest_rejects_empty_string_fields(tmp_path: Path, field: str):
    data = _position_data()
    data[field] = ""
    path = _write(tmp_path, "position.yaml", data)

    with pytest.raises(
        ValueError, match=re.escape(f"position manifest.{field} must be a non-empty string")
    ):
        load_position_manifest(path)


@pytest.mark.parametrize("field", ["suite_id", "driver", "order"])
def test_load_suite_manifest_rejects_empty_string_fields(tmp_path: Path, field: str):
    data = _suite_data()
    data[field] = ""
    path = _write(tmp_path, "suite.yaml", data)

    with pytest.raises(
        ValueError, match=re.escape(f"suite manifest.{field} must be a non-empty string")
    ):
        load_suite_manifest(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("temperature", "hot", "suite manifest.sampling.temperature must be a number or null"),
        ("temperature", True, "suite manifest.sampling.temperature must be a number or null"),
        ("top_p", "high", "suite manifest.sampling.top_p must be a number or null"),
        ("seed", "abc", "suite manifest.sampling.seed must be an integer or null"),
        ("seed", 1.5, "suite manifest.sampling.seed must be an integer or null"),
        ("seed", True, "suite manifest.sampling.seed must be an integer or null"),
        ("max_tokens", None, "suite manifest.sampling.max_tokens must be an integer"),
        ("max_tokens", 1.5, "suite manifest.sampling.max_tokens must be an integer"),
    ],
)
def test_load_suite_manifest_rejects_bad_sampling_values(
    tmp_path: Path, field: str, value: object, message: str
):
    data = _suite_data()
    data["sampling"][field] = value
    path = _write(tmp_path, "suite.yaml", data)

    with pytest.raises(ValueError, match=re.escape(message)):
        load_suite_manifest(path)


def test_load_suite_manifest_allows_sampling_numeric_fields_to_be_null(tmp_path: Path):
    data = _suite_data()
    data["sampling"]["temperature"] = None
    data["sampling"]["top_p"] = None
    path = _write(tmp_path, "suite.yaml", data)

    manifest = load_suite_manifest(path)

    assert manifest.sampling.temperature is None
    assert manifest.sampling.top_p is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("arm_id", "", "suite manifest.arms[0].arm_id must be a non-empty string"),
        ("tools", "", "suite manifest.arms[0].tools must be a non-empty string"),
        ("options", None, "suite manifest.arms[0].options must be a mapping"),
    ],
)
def test_load_suite_manifest_rejects_bad_arm_fields(
    tmp_path: Path, field: str, value: object, message: str
):
    data = _suite_data()
    data["arms"][0][field] = value
    path = _write(tmp_path, "suite.yaml", data)

    with pytest.raises(ValueError, match=re.escape(message)):
        load_suite_manifest(path)
