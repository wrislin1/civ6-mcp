"""Position/suite manifests for the arena controlled-position benchmark runner.

Manifests are the immutable inputs to a benchmark run: a `PositionManifest`
pins one starting game state (archive + expected state + rubric); a
`SuiteManifest` pins the treatment design (positions x models x arms x seeds)
that will be expanded into a schedule by `benchmark_schedule.compile_schedule`.

YAML loaders are strict on purpose: an unknown key silently ignored, or a
missing key silently defaulted, would make a benchmark run's inputs
ambiguous after the fact. Every key in the source YAML must match exactly
one field on the target dataclass.
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

    relevant_tiles = tuple(tuple(pair) for pair in raw["relevant_tiles"])
    objectives = tuple(dict(o) for o in raw["objectives"])
    rubric = tuple(dict(r) for r in raw["rubric"])

    return PositionManifest(
        position_id=raw["position_id"],
        version=raw["version"],
        archive=raw["archive"],
        archive_sha256=raw["archive_sha256"],
        game_save_name=raw["game_save_name"],
        player_id=raw["player_id"],
        expected_state=dict(raw["expected_state"]),
        expected_state_sha256=raw["expected_state_sha256"],
        relevant_tiles=relevant_tiles,
        objectives=objectives,
        rubric=rubric,
        split=raw["split"],
    )


def _load_treatment_arm(raw: object) -> TreatmentArm:
    _require_keys(raw, _ARM_FIELDS, "treatment arm")
    options = raw["options"]
    if not isinstance(options, dict):
        raise ValueError("treatment arm: options must be a mapping")
    return TreatmentArm(arm_id=raw["arm_id"], tools=raw["tools"], options=dict(options))


def _load_sampling(raw: object) -> SamplingConfig:
    _require_keys(raw, _SAMPLING_FIELDS, "sampling")
    return SamplingConfig(
        temperature=raw["temperature"],
        top_p=raw["top_p"],
        seed=raw["seed"],
        max_tokens=raw["max_tokens"],
    )


def load_suite_manifest(path: str | Path) -> SuiteManifest:
    raw = _load_yaml_mapping(path, "suite manifest")
    _require_keys(raw, _SUITE_FIELDS, "suite manifest")

    arms = tuple(_load_treatment_arm(a) for a in raw["arms"])
    sampling = _load_sampling(raw["sampling"])

    return SuiteManifest(
        suite_id=raw["suite_id"],
        driver=raw["driver"],
        positions=tuple(raw["positions"]),
        models=tuple(raw["models"]),
        arms=arms,
        seeds=tuple(raw["seeds"]),
        order=raw["order"],
        sampling=sampling,
        max_steps=raw["max_steps"],
        result_char_cap=raw["result_char_cap"],
        audit_indices=tuple(raw["audit_indices"]),
    )
