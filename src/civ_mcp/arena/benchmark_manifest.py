"""Position/suite manifests for the arena controlled-position benchmark runner.

Manifests are the immutable inputs to a benchmark run: a `PositionManifest`
pins one starting game state (archive + expected state + rubric); a
`SuiteManifest` pins the treatment design (positions x models x arms x seeds)
that will be expanded into a schedule by `benchmark_schedule.compile_schedule`.

YAML loaders are strict on purpose: an unknown key silently ignored, or a
missing key silently defaulted, would make a benchmark run's inputs
ambiguous after the fact. Every key in the source YAML must match exactly
one field on the target dataclass. Values are validated field-by-field
before being handed to a container constructor (`tuple`, `dict`) — a null
or wrong-typed YAML value must raise a field-specific `ValueError`, not a
bare `TypeError` from the constructor.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Mapping

import yaml

from civ_mcp.arena.backends import SamplingConfig


@dataclasses.dataclass(frozen=True)
class PositionManifest:
    position_id: str
    version: int
    archive: str
    archive_sha256: str
    game_save_name: str
    player_id: int
    expected_state: dict[str, object]
    expected_state_sha256: str
    relevant_tiles: tuple[tuple[int, int], ...]
    objectives: tuple[dict[str, object], ...]
    rubric: tuple[dict[str, object], ...]
    split: str


@dataclasses.dataclass(frozen=True)
class TreatmentArm:
    arm_id: str
    tools: str
    options: dict[str, object]


@dataclasses.dataclass(frozen=True)
class SuiteManifest:
    suite_id: str
    driver: str
    positions: tuple[str, ...]
    models: tuple[str, ...]
    arms: tuple[TreatmentArm, ...]
    seeds: tuple[int, ...]
    order: str
    sampling: SamplingConfig
    max_steps: int
    result_char_cap: int
    audit_indices: tuple[int, ...]


def fingerprint(value: object) -> str:
    """Canonical-JSON sha256 of `value`. Key order never affects the digest
    (sort_keys=True); any change to prompt/rubric/sampling/arm content does."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_keys(raw: Mapping[str, object], required: set[str], context: str) -> None:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{context}: expected a mapping, got {type(raw).__name__}")
    got = set(raw.keys())
    missing = required - got
    extra = got - required
    if missing:
        raise ValueError(f"{context}: missing required keys: {sorted(missing)}")
    if extra:
        raise ValueError(f"{context}: unknown keys: {sorted(extra)}")


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _require_list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_int(value: object, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return value


def _require_optional_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number or null")
    return value


def _require_tile(value: object, field: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field} must be a two-element [x, y] list")
    x, y = value
    x = _require_int(x, f"{field}.x")
    y = _require_int(y, f"{field}.y")
    return (x, y)


_POSITION_FIELDS = {f.name for f in dataclasses.fields(PositionManifest)}
_SUITE_FIELDS = {f.name for f in dataclasses.fields(SuiteManifest)}
_ARM_FIELDS = {f.name for f in dataclasses.fields(TreatmentArm)}
_SAMPLING_FIELDS = {f.name for f in dataclasses.fields(SamplingConfig)}


def _load_yaml_mapping(path: str | Path, context: str) -> dict[str, object]:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{context}: expected a YAML mapping at top level")
    return raw


def load_position_manifest(path: str | Path) -> PositionManifest:
    raw = _load_yaml_mapping(path, "position manifest")
    _require_keys(raw, _POSITION_FIELDS, "position manifest")

    relevant_tiles = tuple(
        _require_tile(tile, f"position manifest.relevant_tiles[{i}]")
        for i, tile in enumerate(_require_list(raw["relevant_tiles"], "position manifest.relevant_tiles"))
    )
    objectives = tuple(
        dict(_require_mapping(o, f"position manifest.objectives[{i}]"))
        for i, o in enumerate(_require_list(raw["objectives"], "position manifest.objectives"))
    )
    rubric = tuple(
        dict(_require_mapping(r, f"position manifest.rubric[{i}]"))
        for i, r in enumerate(_require_list(raw["rubric"], "position manifest.rubric"))
    )

    return PositionManifest(
        position_id=_require_str(raw["position_id"], "position manifest.position_id"),
        version=_require_int(raw["version"], "position manifest.version"),
        archive=_require_str(raw["archive"], "position manifest.archive"),
        archive_sha256=_require_str(raw["archive_sha256"], "position manifest.archive_sha256"),
        game_save_name=_require_str(raw["game_save_name"], "position manifest.game_save_name"),
        player_id=_require_int(raw["player_id"], "position manifest.player_id"),
        expected_state=dict(_require_mapping(raw["expected_state"], "position manifest.expected_state")),
        expected_state_sha256=_require_str(
            raw["expected_state_sha256"], "position manifest.expected_state_sha256"
        ),
        relevant_tiles=relevant_tiles,
        objectives=objectives,
        rubric=rubric,
        split=_require_str(raw["split"], "position manifest.split"),
    )


def _load_treatment_arm(raw: object, context: str) -> TreatmentArm:
    mapping = _require_mapping(raw, context)
    _require_keys(mapping, _ARM_FIELDS, context)
    return TreatmentArm(
        arm_id=_require_str(mapping["arm_id"], f"{context}.arm_id"),
        tools=_require_str(mapping["tools"], f"{context}.tools"),
        options=dict(_require_mapping(mapping["options"], f"{context}.options")),
    )


def _load_sampling(raw: object, context: str) -> SamplingConfig:
    mapping = _require_mapping(raw, context)
    _require_keys(mapping, _SAMPLING_FIELDS, context)
    return SamplingConfig(
        temperature=_require_optional_number(mapping["temperature"], f"{context}.temperature"),
        top_p=_require_optional_number(mapping["top_p"], f"{context}.top_p"),
        seed=_require_optional_number(mapping["seed"], f"{context}.seed"),
        max_tokens=_require_int(mapping["max_tokens"], f"{context}.max_tokens"),
    )


def load_suite_manifest(path: str | Path) -> SuiteManifest:
    raw = _load_yaml_mapping(path, "suite manifest")
    _require_keys(raw, _SUITE_FIELDS, "suite manifest")

    positions = tuple(
        _require_str(p, f"suite manifest.positions[{i}]")
        for i, p in enumerate(_require_list(raw["positions"], "suite manifest.positions"))
    )
    models = tuple(
        _require_str(m, f"suite manifest.models[{i}]")
        for i, m in enumerate(_require_list(raw["models"], "suite manifest.models"))
    )
    arms = tuple(
        _load_treatment_arm(a, f"suite manifest.arms[{i}]")
        for i, a in enumerate(_require_list(raw["arms"], "suite manifest.arms"))
    )
    seeds = tuple(
        _require_int(s, f"suite manifest.seeds[{i}]")
        for i, s in enumerate(_require_list(raw["seeds"], "suite manifest.seeds"))
    )
    sampling = _load_sampling(raw["sampling"], "suite manifest.sampling")
    audit_indices = tuple(
        _require_int(a, f"suite manifest.audit_indices[{i}]")
        for i, a in enumerate(_require_list(raw["audit_indices"], "suite manifest.audit_indices"))
    )

    return SuiteManifest(
        suite_id=_require_str(raw["suite_id"], "suite manifest.suite_id"),
        driver=_require_str(raw["driver"], "suite manifest.driver"),
        positions=positions,
        models=models,
        arms=arms,
        seeds=seeds,
        order=_require_str(raw["order"], "suite manifest.order"),
        sampling=sampling,
        max_steps=_require_int(raw["max_steps"], "suite manifest.max_steps"),
        result_char_cap=_require_int(raw["result_char_cap"], "suite manifest.result_char_cap"),
        audit_indices=audit_indices,
    )
