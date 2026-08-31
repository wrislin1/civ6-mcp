"""Shared action-quality classifier for arena transcripts.

Distinguishes three failure modes that ``analyze.py``'s existing rates and
rubric conflate or omit entirely:

- **invalid call**: the model asked for a tool that doesn't exist / isn't
  wired up (already tracked as ``invalid_tool_calls`` elsewhere).
- **domain rejection**: the tool call reached the game engine but the game
  rejected it (``Error: ...`` / ``...|BLOCKED``) — a legal call, an illegal
  move.
- **loop / repetition**: the same call issued again against unchanged state,
  producing no progress.

``evaluate_predicate`` is the shared, fail-closed vocabulary for scoring
whether an action made progress toward an objective. It is deliberately a
clean public function: the controlled-position benchmark scorer (a later
task) reuses it verbatim rather than re-implementing progress checks.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence


class PredicateError(Exception):
    """A progress predicate could not be evaluated safely.

    Raised for an unknown predicate ``kind`` or a typed ``path`` that does
    not resolve against the given state. An evaluator that cannot resolve a
    predicate must never fall back to False/0 — that reports "did not
    happen" when the true answer is "couldn't tell", which would silently
    under-score every scenario where a state shape assumption is wrong.
    """


def classify_result(result: str) -> str:
    """Classify a raw tool-result string as "success", "domain_rejection",
    or "not_dispatched".

    Mirrors ``analyze._is_error_result``'s real ``game_state.py``
    conventions exactly: title-case ``Error: ...`` and pipe-delimited
    ``...|BLOCKED`` both mean the call reached the game engine and the game
    rejected it.

    An ``UNAVAILABLE: ...`` / ``MALFORMED_ARGUMENTS: ...`` prefix means the
    call was intercepted before it ever reached the game engine (gated /
    out-of-tier / unknown-tool / bad-argument calls from ``agent.py``'s
    ``_unavailable_result`` and malformed-arguments path). Such a call is
    neither a domain rejection (the game never saw it) nor a success (it
    was never dispatched) — treating it as "success" would let a
    never-dispatched call satisfy a ``successful_tool_call`` predicate,
    scoring an out-of-tier tool call as a treatment success. The same event
    is already recorded separately in ``invalid_tool_calls``.
    """
    normalized = (result or "").strip().lower()
    if normalized.startswith("unavailable") or normalized.startswith("malformed_arguments"):
        return "not_dispatched"
    if normalized.startswith("error") or "|blocked" in normalized:
        return "domain_rejection"
    return "success"


def _resolve_path(state: object, path: Sequence[object]) -> object:
    """Navigate a typed path (dict keys / list indices) through ``state``.

    Raises ``PredicateError`` the moment the path fails to resolve — missing
    state, a missing key, or an out-of-range index — rather than returning a
    sentinel a caller might mistake for a real value.
    """
    if state is None:
        raise PredicateError(f"cannot resolve path {list(path)!r}: state is missing")
    current = state
    for key in path:
        if isinstance(current, Mapping):
            if key not in current:
                raise PredicateError(f"path {list(path)!r} has no key {key!r}")
            current = current[key]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            if not isinstance(key, int) or key < 0 or key >= len(current):
                raise PredicateError(f"path {list(path)!r} has no index {key!r}")
            current = current[key]
        else:
            raise PredicateError(
                f"path {list(path)!r} cannot descend into {type(current).__name__}"
            )
    return current


def _find_unit(state: object, unit_index: object) -> Mapping[str, object]:
    units = _resolve_path(state, ["units"])
    if not isinstance(units, Sequence) or isinstance(units, (str, bytes)):
        raise PredicateError("state['units'] is not a list")
    for unit in units:
        if isinstance(unit, Mapping) and unit.get("id") == unit_index:
            return unit
    raise PredicateError(f"unit {unit_index!r} not found in state['units']")


def _find_unit_optional(state: object, unit_index: object) -> Mapping[str, object] | None:
    """Same lookup as `_find_unit`, but returns `None` instead of raising
    when the unit is absent from `state`.

    Used for the FINAL-state half of every unit predicate: a
    `persistent_unit_ids`-declared unit is expected to survive the whole
    trial, but a live episode can still capture/kill it. That runtime
    disappearance must score the predicate `False` -- the model failed to
    keep the unit alive/at-position/progressing -- never raise
    `PredicateError`, which would abort a whole trial's scoring over an
    entirely legitimate (if unwanted) outcome. The INITIAL-state lookup
    stays on `_find_unit` (raises): a unit missing from canonical initial
    state is an authoring/manifest error, not a runtime outcome.
    """
    units = _resolve_path(state, ["units"])
    if not isinstance(units, Sequence) or isinstance(units, (str, bytes)):
        raise PredicateError("state['units'] is not a list")
    for unit in units:
        if isinstance(unit, Mapping) and unit.get("id") == unit_index:
            return unit
    return None


def _find_tile(state: object, x: object, y: object) -> Mapping[str, object]:
    """Locate the tile at `(x, y)` in `state['tiles']` by coordinate
    equality -- never by list position/offset, since capture order is not
    meaningful (see `benchmark_state.normalize_state`)."""
    tiles = _resolve_path(state, ["tiles"])
    if not isinstance(tiles, Sequence) or isinstance(tiles, (str, bytes)):
        raise PredicateError("state['tiles'] is not a list")
    for tile in tiles:
        if isinstance(tile, Mapping) and tile.get("x") == x and tile.get("y") == y:
            return tile
    raise PredicateError(f"tile ({x!r}, {y!r}) not found in state['tiles']")


def _offset_to_cube(x: int, y: int) -> tuple[int, int, int]:
    """Convert Civ6 offset hex coordinates (x=column, y=row, +y = south --
    see repo CLAUDE.md) to cube coordinates, using an odd-r offset layout
    (odd rows shifted right). No existing Python-side hex distance helper
    exists in this repo to reuse (in-game distance is computed by the Lua
    engine's ``Map.GetPlotDistance``, which is not callable here) -- this
    is a from-scratch implementation, verified against the brief's
    counterexample: (5,5)->(6,6) approaching (5,8) is hex distance 3->2."""
    cube_x = x - (y - (y & 1)) // 2
    cube_z = y
    cube_y = -cube_x - cube_z
    return cube_x, cube_y, cube_z


def _hex_distance(a: Sequence[object], b: Sequence[object]) -> int:
    ax, ay, az = _offset_to_cube(int(a[0]), int(a[1]))
    bx, by, bz = _offset_to_cube(int(b[0]), int(b[1]))
    return max(abs(ax - bx), abs(ay - by), abs(az - bz))


def _unit_distance(unit: Mapping[str, object], target: Sequence[object]) -> float:
    x, y = unit.get("x"), unit.get("y")
    if x is None or y is None:
        raise PredicateError(f"unit {unit!r} is missing x/y")
    if not isinstance(target, Sequence) or len(target) != 2:
        raise PredicateError(f"target {target!r} must be an [x, y] pair")
    return _hex_distance((x, y), target)


def evaluate_predicate(
    predicate: Mapping[str, object],
    *,
    initial_state: object = None,
    final_state: object = None,
    steps: Sequence[Mapping[str, object]] = (),
) -> bool:
    """Fail-closed evaluation of a controlled-position progress predicate.

    Supports exactly: ``always``, ``all``, ``any``, ``successful_tool_call``
    (tool plus exact argument subset), ``final_state_equals`` (typed path
    plus value), ``state_changed_to``, ``unit_distance_decreased``,
    ``unit_at``, ``unit_exists_final``, and ``tile_state_equals``. Unknown
    kinds and typed paths that don't resolve raise ``PredicateError`` —
    never a false/zero score.

    The three unit-referencing kinds (``unit_distance_decreased``,
    ``unit_at``, ``unit_exists_final``) share one safety rule: the unit
    MUST be present in ``initial_state`` (a `PredicateError` there is a
    manifest/authoring bug — see
    ``benchmark_manifest.validate_position_contract``, which is meant to
    catch this before any trial runs) but its absence from ``final_state``
    is a legitimate runtime outcome (the unit was captured/killed/consumed
    mid-episode) and always scores the predicate ``False``, never raises.
    ``tile_state_equals`` locates its tile by ``(x, y)`` coordinates, never
    list offset, since capture order is not meaningful.
    """
    if not isinstance(predicate, Mapping):
        raise PredicateError(f"predicate must be a mapping, got {type(predicate).__name__}")
    kind = predicate.get("kind")

    if kind == "always":
        return True

    if kind in ("all", "any"):
        sub_predicates = predicate.get("predicates")
        if not isinstance(sub_predicates, Sequence) or isinstance(sub_predicates, (str, bytes)):
            raise PredicateError(f"'{kind}' predicate requires a 'predicates' list")
        results = (
            evaluate_predicate(
                sub, initial_state=initial_state, final_state=final_state, steps=steps
            )
            for sub in sub_predicates
        )
        return all(results) if kind == "all" else any(results)

    if kind == "successful_tool_call":
        tool = predicate.get("tool")
        wanted_args = predicate.get("args") or {}
        if not isinstance(wanted_args, Mapping):
            raise PredicateError("'successful_tool_call' requires 'args' to be a mapping")
        for step in steps:
            if step.get("tool_name") != tool:
                continue
            step_args = step.get("tool_args")
            if not isinstance(step_args, Mapping):
                continue
            if not all(step_args.get(k) == v for k, v in wanted_args.items()):
                continue
            if classify_result(str(step.get("tool_result_full", ""))) == "success":
                return True
        return False

    if kind == "final_state_equals":
        path = predicate.get("path")
        if path is None:
            raise PredicateError("'final_state_equals' requires a 'path'")
        return _resolve_path(final_state, path) == predicate.get("value")

    if kind == "state_changed_to":
        path = predicate.get("path")
        if path is None:
            raise PredicateError("'state_changed_to' requires a 'path'")
        target = predicate.get("value")
        after = _resolve_path(final_state, path)
        before = _resolve_path(initial_state, path)
        return after == target and before != target

    if kind == "unit_distance_decreased":
        unit_index = predicate.get("unit_index")
        target = predicate.get("target")
        if target is None:
            raise PredicateError("'unit_distance_decreased' requires a 'target'")
        before_unit = _find_unit(initial_state, unit_index)
        after_unit = _find_unit_optional(final_state, unit_index)
        if after_unit is None:
            # The tracked unit disappeared (captured/killed) before the
            # final capture -- a legitimate runtime outcome for a
            # `persistent_unit_ids` unit, not an unresolved predicate.
            return False
        return _unit_distance(after_unit, target) < _unit_distance(before_unit, target)

    if kind == "unit_at":
        unit_index = predicate.get("unit_index")
        x = predicate.get("x")
        y = predicate.get("y")
        if x is None or y is None:
            raise PredicateError("'unit_at' requires 'x' and 'y'")
        _find_unit(initial_state, unit_index)  # contract: must exist initially
        unit = _find_unit_optional(final_state, unit_index)
        if unit is None:
            return False
        return unit.get("x") == x and unit.get("y") == y

    if kind == "unit_exists_final":
        unit_index = predicate.get("unit_index")
        _find_unit(initial_state, unit_index)  # contract: must exist initially
        return _find_unit_optional(final_state, unit_index) is not None

    if kind == "tile_state_equals":
        x = predicate.get("x")
        y = predicate.get("y")
        field = predicate.get("field")
        if x is None or y is None or field is None:
            raise PredicateError("'tile_state_equals' requires 'x', 'y', and 'field'")
        tile = _find_tile(final_state, x, y)
        return tile.get(field) == predicate.get("value")

    raise PredicateError(f"unknown predicate kind: {kind!r}")


def _call_key(step: Mapping[str, object]) -> str:
    """Identity of a tool call for repetition detection: tool + args + result."""
    payload = {
        "tool": step.get("tool_name"),
        "args": step.get("tool_args") if isinstance(step.get("tool_args"), dict) else {},
        "result": step.get("tool_result_full"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def classify_action_quality(
    *,
    steps: Sequence[Mapping[str, object]],
    invalid_tool_calls: Sequence[Mapping[str, object]],
    objectives: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Classify one transcript record's steps into the action-quality vocabulary.

    Returns invalid/domain-rejection/successful-mutation/repetition counts
    that are always available, plus ``useful_actions`` /
    ``useful_action_coverage`` which require an objective mapping (a later
    controlled-position benchmark concept). Historical records with no
    ``objectives`` report ``useful_actions: None`` and
    ``useful_action_coverage: "unavailable"`` rather than silently treating
    every successful mutation as useful.
    """
    domain_rejections = 0
    successful_mutations = 0
    useful_actions = 0
    repetitions = 0
    last_digest_by_call: dict[str, object] = {}
    # G14: historical arena records never carried state_digest_before/after
    # at all (key absent, not merely None) -- comparing absent-vs-absent
    # digests silently reports "no mutation" for data that was never
    # measured. Digest-dependent counts (successful_mutations, and
    # repetitions/loop_excess since their loop-detection also compares
    # digests) must report None rather than a fabricated 0 in that case. An
    # empty step list has nothing to be uncertain about, so it stays a real
    # 0 rather than None.
    digest_fields_present = not steps or any(
        "state_digest_before" in step or "state_digest_after" in step for step in steps
    )

    for step in steps:
        result_kind = classify_result(str(step.get("tool_result_full", "")))
        if result_kind == "domain_rejection":
            domain_rejections += 1

        changed = step.get("state_digest_before") != step.get("state_digest_after")
        is_successful_mutation = result_kind == "success" and changed
        successful_mutations += is_successful_mutation

        if objectives and is_successful_mutation:
            tool_name = step.get("tool_name")
            made_progress = any(
                tool_name in objective.get("tools", ())
                and evaluate_predicate(
                    objective["progress_predicate"],
                    initial_state=step.get("state_before"),
                    final_state=step.get("state_after"),
                    steps=[step],
                )
                for objective in objectives
            )
            useful_actions += made_progress

        call_key = _call_key(step)
        if call_key in last_digest_by_call and last_digest_by_call[call_key] == step.get(
            "state_digest_before"
        ):
            repetitions += 1
        last_digest_by_call[call_key] = step.get("state_digest_after")

    return {
        "invalid_calls": len(invalid_tool_calls),
        "domain_rejections": domain_rejections,
        "successful_mutations": successful_mutations if digest_fields_present else None,
        "useful_actions": useful_actions if objectives else None,
        "useful_action_coverage": "objective_verified" if objectives else "unavailable",
        "repetitions": repetitions if digest_fields_present else None,
        "loop_excess": repetitions if digest_fields_present else None,
    }
