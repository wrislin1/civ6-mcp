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
    # Unit-lifecycle declarations (see `validate_position_contract`): every
    # unit id an `objectives`/`rubric` predicate references must appear in
    # exactly one of these two lists, and that id must be present in
    # `expected_state["units"]` (the canonical initial state). A
    # `persistent_unit_id` is expected to survive the whole trial (its
    # runtime disappearance scores its predicates `False`, never raises); a
    # `consumable_unit_id` is expected to be consumed by an in-episode
    # action (e.g. `found_city`) and must be scored through a tile/state
    # predicate, never `unit_distance_decreased`. Defaulted to `()` here
    # (not by the strict YAML loader, which requires both keys present in
    # every position manifest file -- see `load_position_manifest`) purely
    # so positions constructed directly in Python (tests fixtures elsewhere
    # in this repo predating this task) do not need updating.
    persistent_unit_ids: tuple[int, ...] = ()
    consumable_unit_ids: tuple[int, ...] = ()


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


def _require_optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer or null")
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
    persistent_unit_ids = tuple(
        _require_int(u, f"position manifest.persistent_unit_ids[{i}]")
        for i, u in enumerate(
            _require_list(raw["persistent_unit_ids"], "position manifest.persistent_unit_ids")
        )
    )
    consumable_unit_ids = tuple(
        _require_int(u, f"position manifest.consumable_unit_ids[{i}]")
        for i, u in enumerate(
            _require_list(raw["consumable_unit_ids"], "position manifest.consumable_unit_ids")
        )
    )

    manifest = PositionManifest(
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
        persistent_unit_ids=persistent_unit_ids,
        consumable_unit_ids=consumable_unit_ids,
    )
    # Authoring-time safety net: a position manifest is only ever admitted
    # into the pipeline through this loader -- validating the unit-lifecycle
    # contract here (rather than leaving it to whichever caller happens to
    # remember) means every downstream consumer (the runner, the
    # position-freeze CLI) always sees an already-validated manifest.
    validate_position_contract(manifest)
    return manifest


# Predicate kinds (see `civ_mcp.arena.action_metrics.evaluate_predicate`)
# that reference a single unit by `unit_index` and therefore fall under the
# unit-lifecycle contract this module enforces.
_UNIT_PREDICATE_KINDS = {"unit_distance_decreased", "unit_at", "unit_exists_final"}


def _iter_predicates(predicate: object):
    """Yield `predicate` and, recursively, every sub-predicate nested under
    an `all`/`any` combinator -- so a lifecycle-contract scan finds a unit
    predicate no matter how deeply it is nested."""
    if not isinstance(predicate, Mapping):
        return
    yield predicate
    if predicate.get("kind") in ("all", "any"):
        sub_predicates = predicate.get("predicates")
        if isinstance(sub_predicates, (list, tuple)):
            for sub in sub_predicates:
                yield from _iter_predicates(sub)


def _iter_position_predicates(position: PositionManifest):
    """Yield every predicate declared anywhere in `position` -- each
    objective's `progress_predicate`, and each rubric level's `predicate`.
    Missing/malformed shapes are skipped rather than raised on here: this
    scan only cares about finding unit predicates, not re-validating the
    generic manifest shape (already enforced by `_require_*` at load time
    for manifests loaded through `load_position_manifest`; a manifest built
    directly in Python -- e.g. a test fixture -- may use a different,
    predicate-free objectives/rubric shape entirely)."""
    for objective in position.objectives:
        if not isinstance(objective, Mapping):
            continue
        predicate = objective.get("progress_predicate")
        if predicate is not None:
            yield from _iter_predicates(predicate)
    for rubric_item in position.rubric:
        if not isinstance(rubric_item, Mapping):
            continue
        levels = rubric_item.get("levels")
        if not isinstance(levels, (list, tuple)):
            continue
        for level in levels:
            if not isinstance(level, Mapping):
                continue
            predicate = level.get("predicate")
            if predicate is not None:
                yield from _iter_predicates(predicate)


def validate_position_contract(position: PositionManifest) -> None:
    """Authoring-time validation of `position`'s unit-lifecycle contract.

    A live smoke run aborted report generation because
    `unit_distance_decreased` raised `PredicateError` when its tracked unit
    had been consumed (`found_city`) mid-episode. This validator catches
    the authoring mistakes that lead there, before any trial ever runs:

    - `persistent_unit_ids` and `consumable_unit_ids` must not overlap --
      a unit's lifecycle expectation must be unambiguous.
    - Every id in either list must be present in
      `expected_state["units"]` (the canonical initial state) -- a
      lifecycle declaration for a unit the position doesn't actually have
      is a manifest authoring error, not a runtime concern.
    - Every unit predicate (`unit_distance_decreased`, `unit_at`,
      `unit_exists_final`) found anywhere in `objectives`/`rubric` must
      reference a declared unit id (persistent or consumable) -- an
      undeclared id is rejected.
    - No unit predicate (`unit_distance_decreased`, `unit_at`,
      `unit_exists_final`) may reference a consumable unit id: a
      consumable unit is EXPECTED to vanish from final state on a correct
      trial (e.g. a settler consumed by `found_city`), which is
      indistinguishable, to any unit predicate, from that unit having been
      lost to a barbarian. `unit_distance_decreased`/`unit_at` are
      meaningless once the unit is gone, and `unit_exists_final` would
      silently score a CORRECT trial `False` with no error raised --
      worse than the `PredicateError` abort this validator exists to
      prevent. A consumable unit's outcome must be scored through a
      tile/state predicate (`tile_state_equals`, `final_state_equals`,
      `state_changed_to`) instead.

    Raises `ValueError` (matching every other authoring-validation failure
    in this module) on the first violation found.
    """
    persistent = set(position.persistent_unit_ids)
    consumable = set(position.consumable_unit_ids)

    overlap = persistent & consumable
    if overlap:
        raise ValueError(
            f"position manifest {position.position_id!r}: unit id(s) {sorted(overlap)} "
            "declared in both persistent_unit_ids and consumable_unit_ids -- a unit's "
            "lifecycle expectation must be unambiguous"
        )

    units = position.expected_state.get("units") or []
    canonical_ids = {unit.get("id") for unit in units if isinstance(unit, Mapping)}

    for unit_id in sorted(persistent | consumable):
        if unit_id not in canonical_ids:
            raise ValueError(
                f"position manifest {position.position_id!r}: lifecycle declared for unit "
                f"id {unit_id} which is not present in expected_state['units'] (the "
                "canonical initial state)"
            )

    declared = persistent | consumable
    for predicate in _iter_position_predicates(position):
        kind = predicate.get("kind")
        if kind not in _UNIT_PREDICATE_KINDS:
            continue
        unit_id = predicate.get("unit_index")
        if unit_id not in declared:
            raise ValueError(
                f"position manifest {position.position_id!r}: predicate kind {kind!r} "
                f"references undeclared unit id {unit_id!r} -- every unit id used in a "
                "unit predicate must appear in persistent_unit_ids or consumable_unit_ids"
            )
        if kind in _UNIT_PREDICATE_KINDS and unit_id in consumable:
            # A consumable unit is EXPECTED to vanish from final state on a
            # correct trial (e.g. a settler consumed by found_city) -- that
            # looks identical, to any unit predicate, to the unit having
            # been lost to a barbarian. unit_at/unit_exists_final score
            # that vanish as `False` (never raise -- see
            # action_metrics.evaluate_predicate), which would silently mark
            # a CORRECT trial as a failure with no error anywhere. That is
            # worse than the PredicateError abort this validator exists to
            # prevent, so ALL THREE unit-referencing kinds are barred for a
            # consumable id, not just unit_distance_decreased.
            raise ValueError(
                f"position manifest {position.position_id!r}: {kind!r} references "
                f"consumable unit id {unit_id} -- consumable units must be scored "
                "through a tile/state predicate (tile_state_equals, final_state_equals, "
                "state_changed_to), never a unit predicate"
            )

    _validate_minimal_observability(position)


def _rubric_task_reaches_level_1_or_2(levels: object) -> bool:
    """True when any of `levels` is a `{"score": ..., "predicate": ...}`
    mapping whose score is 1 or 2 -- the minimal-arm-reachable levels (see
    `benchmark_gates.check_treatment_can_fire`'s own docstring for the same
    definition). A non-mapping level (e.g. the old bare-int rubric shape)
    is never treated as reaching 1/2 here -- `check_treatment_can_fire`
    itself still rejects that shape outright via `malformed_rubric_level`;
    this function only needs to not silently miscount it as trivial or
    nontrivial."""
    for level in levels or ():
        if isinstance(level, Mapping) and level.get("score") in (1, 2):
            return True
    return False


def _validate_minimal_observability(position: PositionManifest) -> None:
    """B5 (external review wave B): every rubric task whose levels include
    score 1 or 2 must reference only entities present in the manifest's own
    declared observable state -- a tile in `relevant_tiles`, or a unit
    index within `expected_state["units"]` -- i.e. observable through the
    minimal read-tool tier (every read/query tool ships on every tier; see
    `civ_mcp.arena.registry.resolve_tools`).

    This replaces a tautological live-derived reachability check: the old
    admission-time `discoverable_task_ids` was always computed AS the
    rubric's own task ids, so the "minimal arm can reach levels 1-2" gate
    could never fail. This authoring-time assertion is the only thing that
    can actually catch a rubric task referencing something the minimal tier
    cannot see (see `benchmark_admission._minimal_observation`, which now
    records `{"source": "authoring_validation"}` instead of re-deriving an
    answer set, and `benchmark_gates.check_treatment_can_fire`, which now
    only confirms this authoring-time check actually ran).

    Scoped to predicate kinds that reference a concrete tile or unit
    (`tile_state_equals`; a `final_state_equals`/`state_changed_to` whose
    `path` indexes into `tiles`/`units`) -- the three unit-referencing
    kinds (`unit_distance_decreased`, `unit_at`, `unit_exists_final`) are
    already fully covered by this function's caller's own unit-lifecycle
    checks above (every declared unit id is already required to be present
    in `expected_state["units"]`), so they are not re-checked here. A
    predicate over some other, non-entity state field (e.g. `turn`) is out
    of scope -- it is not an "entity" this check has a declared-observable
    list for.
    """
    relevant_tiles = set(position.relevant_tiles)
    units = position.expected_state.get("units")
    unit_count = len(units) if isinstance(units, (list, tuple)) else 0

    for rubric_item in position.rubric:
        if not isinstance(rubric_item, Mapping):
            continue
        levels = rubric_item.get("levels")
        if not _rubric_task_reaches_level_1_or_2(levels):
            continue
        task_id = rubric_item.get("task_id")
        for level in levels:
            if not isinstance(level, Mapping) or level.get("score") not in (1, 2):
                continue
            predicate = level.get("predicate")
            if predicate is None:
                continue
            for sub in _iter_predicates(predicate):
                kind = sub.get("kind")
                if kind == "tile_state_equals":
                    tile = (sub.get("x"), sub.get("y"))
                    if tile not in relevant_tiles:
                        raise ValueError(
                            f"position manifest {position.position_id!r}: rubric task "
                            f"{task_id!r} level {level.get('score')} predicate {kind!r} "
                            f"references tile {tile} which is not in relevant_tiles -- "
                            "not observable through the minimal read-tool tier"
                        )
                elif kind in ("final_state_equals", "state_changed_to"):
                    path = sub.get("path")
                    if not isinstance(path, (list, tuple)) or len(path) < 2:
                        continue
                    head, index = path[0], path[1]
                    if head == "tiles" and isinstance(index, int) and not isinstance(index, bool):
                        if not (0 <= index < len(relevant_tiles)):
                            raise ValueError(
                                f"position manifest {position.position_id!r}: rubric task "
                                f"{task_id!r} level {level.get('score')} predicate {kind!r} "
                                f"path {list(path)!r} indexes a tile outside relevant_tiles "
                                "-- not observable through the minimal read-tool tier"
                            )
                    elif head == "units" and isinstance(index, int) and not isinstance(index, bool):
                        if not (0 <= index < unit_count):
                            raise ValueError(
                                f"position manifest {position.position_id!r}: rubric task "
                                f"{task_id!r} level {level.get('score')} predicate {kind!r} "
                                f"path {list(path)!r} indexes a unit outside "
                                "expected_state['units'] -- not observable through the "
                                "minimal read-tool tier"
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
        seed=_require_optional_int(mapping["seed"], f"{context}.seed"),
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
