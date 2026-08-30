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
    """Classify a raw tool-result string as "success" or "domain_rejection".

    Mirrors ``analyze._is_error_result``'s real ``game_state.py``
    conventions exactly: title-case ``Error: ...`` and pipe-delimited
    ``...|BLOCKED`` both mean the call reached the game engine and the game
    rejected it.

    Deliberately does NOT treat an ``UNAVAILABLE: ...`` prefix as a domain
    rejection. ``agent.py``'s ``_unavailable_result`` emits that string for
    gated / out-of-tier / unknown-tool calls that never reach the game
    engine at all — the call is intercepted before dispatch, and the same
    event is already recorded in ``invalid_tool_calls``. Counting it here
    too would both double-count it and mislabel an agent-level gating
    outcome as a legal-but-rejected game action.
    """
    normalized = (result or "").strip().lower()
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


def _unit_distance(unit: Mapping[str, object], target: Sequence[object]) -> float:
    x, y = unit.get("x"), unit.get("y")
    if x is None or y is None:
        raise PredicateError(f"unit {unit!r} is missing x/y")
    if not isinstance(target, Sequence) or len(target) != 2:
        raise PredicateError(f"target {target!r} must be an [x, y] pair")
    return abs(x - target[0]) + abs(y - target[1])


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
    plus value), ``state_changed_to``, and ``unit_distance_decreased``.
    Unknown kinds and typed paths that don't resolve raise
    ``PredicateError`` — never a false/zero score.
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
        after_unit = _find_unit(final_state, unit_index)
        return _unit_distance(after_unit, target) < _unit_distance(before_unit, target)

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
        "successful_mutations": successful_mutations,
        "useful_actions": useful_actions if objectives else None,
        "useful_action_coverage": "objective_verified" if objectives else "unavailable",
        "repetitions": repetitions,
        "loop_excess": repetitions,
    }
