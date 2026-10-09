"""Strict loaders for version-2 benchmark inputs (toolset and documents).

Version-2 documents are separate from the version-1 campaign loaders in
`benchmark_manifest.py` / `benchmark_contract.py`, which are untouched.
Unknown keys are rejected for every kind. Relative path fields are resolved
against the document's directory; referenced files are neither required to
exist nor hashed here (later lock/validation steps do that).
"""
from __future__ import annotations

import copy
import math
import os
from pathlib import Path
from typing import Any

import yaml

from civ_mcp.arena.benchmark_agent import FINISH_TRIAL_TOOL_NAME, resolved_benchmark_tools
from civ_mcp.arena.benchmark_contract_v2 import document_digest
from civ_mcp.arena.registry import TOOL_REGISTRY

SCHEMA_VERSION = "2.0.0"

_TOOLSET_KEYS = {"toolset_id", "game_tools"}

_KIND_KEYS: dict[str, set[str]] = {
    "position": {
        "schema_version", "position_id", "version", "family", "split", "archive",
        "game_save_name", "player_id", "expected_state", "expected_state_sha256",
        "coverage", "toolset", "contract_identity", "rubric", "provenance",
        "public_observation", "public_task_tiles", "pilot_informed",
    },
    "script": {"schema_version", "script_id", "batches"},
    "case": {"schema_version", "case_id", "position", "script", "tags", "expected"},
    "validation_suite": {
        "schema_version", "suite_id", "cases", "position", "toolset_identity",
        "contract_identity", "max_steps", "episode_wall_s", "result_char_cap",
        "actor_kind", "counting",
    },
    "lock": {
        "schema_version", "lock_id", "suite", "scripts", "cases", "archive_identity",
        "state_identity", "provenance_identity", "schedule", "code_identity",
        "schema_identity", "limits", "actor", "model", "seed", "token_budget",
        "cost", "latency",
    },
}

# Dotted paths of relative-path fields resolved against the document directory.
# "[]" marks a list of mappings each holding the field.
_PATH_FIELDS: dict[str, tuple[str, ...]] = {
    "position": ("archive.path", "toolset.path", "provenance.path", "public_observation.path"),
    "script": (),
    "case": ("position.path", "script.path"),
    "validation_suite": ("cases[].path", "position.path"),
    "lock": ("suite.path", "scripts[].path", "cases[].path"),
}

# Keys a kind may carry beyond its required `_KIND_KEYS`.
_OPTIONAL_KEYS: dict[str, set[str]] = {"case": {"declared_rejections"}}

_LOCK_NULL_FIELDS = ("model", "seed", "token_budget", "cost", "latency")


def _check_keys(raw: Any, expected: set[str], context: str) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{context} must be a mapping")
    missing = sorted(expected - raw.keys())
    extra = sorted(str(k) for k in raw.keys() - expected)
    if missing or extra:
        raise ValueError(f"{context}: missing keys {missing}, unexpected keys {extra}")


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ValueError(message)


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _load_yaml_document(path: Path, context: str) -> Any:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{context} {path}: invalid YAML: {exc}") from exc


def load_toolset(path: Path) -> dict[str, Any]:
    """Load an explicit-list toolset and resolve it against the registry."""
    path = Path(path)
    raw = _load_yaml_document(path, "toolset")
    _check_keys(raw, _TOOLSET_KEYS, "toolset")
    toolset_id = raw["toolset_id"]
    _require(_is_str(toolset_id), "toolset.toolset_id must be a non-empty string")
    names = raw["game_tools"]
    if not isinstance(names, list):
        raise ValueError(
            "toolset.game_tools must be an explicit list of tool names, "
            f"not {type(names).__name__} {names!r} (tier aliases are mutable)"
        )
    _require(all(_is_str(n) for n in names), "toolset.game_tools entries must be strings")
    duplicates = sorted({n for n in names if names.count(n) > 1})
    _require(not duplicates, f"toolset.game_tools has duplicate names: {duplicates}")
    _require("end_turn" not in names, "toolset.game_tools must not include end_turn")
    _require(
        FINISH_TRIAL_TOOL_NAME not in names,
        f"toolset.game_tools must not include {FINISH_TRIAL_TOOL_NAME} (appended automatically)",
    )
    unknown = [n for n in names if n not in TOOL_REGISTRY]
    _require(not unknown, f"toolset.game_tools has unknown tool names: {unknown}")
    schemas = resolved_benchmark_tools(list(names))
    return {
        "toolset_id": toolset_id,
        "game_tools": list(names),
        "schemas": schemas,
        "identity": {
            "source_sha256": document_digest(raw),
            "schemas_sha256": document_digest(schemas),
        },
    }


def _validate_script(raw: dict[str, Any]) -> None:
    _require(_is_str(raw["script_id"]), "script.script_id must be a non-empty string")
    batches = raw["batches"]
    _require(isinstance(batches, list) and batches, "script.batches must be a non-empty list")
    for bi, batch in enumerate(batches):
        _check_keys(batch, {"calls"}, f"script.batches[{bi}]")
        calls = batch["calls"]
        _require(isinstance(calls, list) and calls, f"script.batches[{bi}].calls must be a non-empty list")
        for ci, call in enumerate(calls):
            ctx = f"script.batches[{bi}].calls[{ci}]"
            _check_keys(call, {"name", "arguments"}, ctx)
            _require(_is_str(call["name"]), f"{ctx}.name must be a non-empty string")
            _require(isinstance(call["arguments"], dict), f"{ctx}.arguments must be a mapping")
            is_finish = call["name"] == FINISH_TRIAL_TOOL_NAME
            last_batch = bi == len(batches) - 1
            _require(
                not is_finish or last_batch,
                f"{ctx}: {FINISH_TRIAL_TOOL_NAME} may only appear in the last batch",
            )
    last = batches[-1]["calls"]
    _require(
        any(c["name"] == FINISH_TRIAL_TOOL_NAME for c in last),
        f"script last batch must contain {FINISH_TRIAL_TOOL_NAME}",
    )


def _validate_position(raw: dict[str, Any]) -> None:
    for key in ("position_id", "family", "game_save_name", "expected_state_sha256", "contract_identity"):
        _require(_is_str(raw[key]), f"position.{key} must be a non-empty string")
    _require(_is_int(raw["version"]), "position.version must be an integer")
    _require(_is_int(raw["player_id"]), "position.player_id must be an integer")
    _require(raw["split"] in ("development", "held_out"), "position.split must be 'development' or 'held_out'")
    _require(isinstance(raw["pilot_informed"], bool), "position.pilot_informed must be a boolean")
    for key in ("expected_state", "coverage"):
        _require(isinstance(raw[key], dict), f"position.{key} must be a mapping")
    _check_keys(raw["archive"], {"path", "sha256"}, "position.archive")
    _check_keys(raw["toolset"], {"path", "identity"}, "position.toolset")
    _check_keys(raw["provenance"], {"path", "sha256"}, "position.provenance")
    _check_keys(raw["public_observation"], {"path", "sha256"}, "position.public_observation")
    _require(isinstance(raw["public_task_tiles"], list), "position.public_task_tiles must be a list")
    rubric = raw["rubric"]
    _check_keys(rubric, {"objectives", "harms"}, "position.rubric")
    for key in ("objectives", "harms"):
        _require(
            isinstance(rubric[key], list) and all(isinstance(i, dict) for i in rubric[key]),
            f"position.rubric.{key} must be a list of mappings",
        )
    from civ_mcp.arena.benchmark_scoring_v2 import validate_rubric_structure

    validate_rubric_structure(rubric)


_SCORE_ASSERTION_KEYS = frozenset({
    "gross_credit", "harm_total", "net_credit", "maximum_credit", "maximum_harm",
    "primary_score",
})
_ENDPOINT_KEYS = {"id", "predicate", "value"}
_LEDGER_KEYS = {"id", "scope", "step", "path", "coverage", "delta"}
_REJECTION_KEYS = {"step", "tool_name"}
# Mismatch keys the comparison itself uses; assertion ids must not shadow them.
_RESERVED_ASSERTION_IDS = frozenset({"mechanics"})


def _is_finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _predicate_kinds(predicate: dict[str, Any]) -> list[str]:
    if predicate["kind"] in ("all", "any"):
        return [k for child in predicate["predicates"] for k in _predicate_kinds(child)]
    return [predicate["kind"]]


def validate_case_expected(expected: Any) -> None:
    """Strict literal assertions: no expressions, comparators or callbacks."""
    from civ_mcp.arena.benchmark_predicates_v2 import validate_predicate
    from civ_mcp.arena.benchmark_scoring_v2 import _EVENT_ONLY_KINDS

    _check_keys(expected, {"score", "endpoints", "ledger"}, "case.expected")
    score = expected["score"]
    _require(isinstance(score, dict), "case.expected.score must be a mapping")
    for key, value in score.items():
        _require(key in _SCORE_ASSERTION_KEYS,
                 f"case.expected.score.{key} is not an assertable score field; "
                 f"expected one of {sorted(_SCORE_ASSERTION_KEYS)}")
        _require(_is_finite_number(value),
                 f"case.expected.score.{key} must be a finite number, got {value!r}")
    for key in ("endpoints", "ledger"):
        _require(isinstance(expected[key], list), f"case.expected.{key} must be a list")
    ids: list[str] = []
    for i, assertion in enumerate(expected["endpoints"]):
        ctx = f"case.expected.endpoints[{i}]"
        _check_keys(assertion, _ENDPOINT_KEYS, ctx)
        _require(_is_str(assertion["id"]), f"{ctx}.id must be a non-empty string")
        try:
            validate_predicate(assertion["predicate"])
        except ValueError as exc:
            raise ValueError(f"{ctx}.predicate: {exc}") from exc
        event_only = sorted(set(_predicate_kinds(assertion["predicate"])) & _EVENT_ONLY_KINDS)
        _require(not event_only,
                 f"{ctx}.predicate uses event-only kinds {event_only}; endpoint "
                 "assertions cannot evaluate them")
        _require(isinstance(assertion["value"], bool), f"{ctx}.value must be a boolean")
        ids.append(assertion["id"])
    paths: set[tuple[Any, ...]] = set()
    for i, assertion in enumerate(expected["ledger"]):
        ctx = f"case.expected.ledger[{i}]"
        _check_keys(assertion, _LEDGER_KEYS, ctx)
        _require(_is_str(assertion["id"]), f"{ctx}.id must be a non-empty string")
        scope, step = assertion["scope"], assertion["step"]
        _require(scope in ("net", "step"), f"{ctx}.scope must be 'net' or 'step'")
        if scope == "net":
            _require(step is None, f"{ctx}.step must be null for net scope")
        else:
            _require(_is_int(step) and step >= 0,
                     f"{ctx}.step must be a non-negative integer for step scope")
        path = assertion["path"]
        _require(isinstance(path, list) and path and all(_is_str(p) for p in path),
                 f"{ctx}.path must be a non-empty list of non-empty strings")
        coverage, delta = assertion["coverage"], assertion["delta"]
        _require(coverage in ("measured", "unavailable"),
                 f"{ctx}.coverage must be 'measured' or 'unavailable'")
        if coverage == "unavailable":
            _require(delta is None, f"{ctx}.delta must be null when coverage is unavailable")
        else:
            _require(_is_finite_number(delta),
                     f"{ctx}.delta must be a finite number when coverage is measured")
        key = (scope, step, *path)
        _require(key not in paths, f"{ctx}: duplicate path {path} in {scope} scope")
        paths.add(key)
        ids.append(assertion["id"])
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    _require(not duplicates, f"case.expected has duplicate id(s) {duplicates}")
    reserved = sorted(i for i in ids if i in _RESERVED_ASSERTION_IDS or i.startswith("score:"))
    _require(not reserved, f"case.expected uses reserved id(s) {reserved}")


def _validate_declared_rejections(rejections: Any) -> None:
    _require(isinstance(rejections, list), "case.declared_rejections must be a list")
    steps: list[int] = []
    for i, entry in enumerate(rejections):
        ctx = f"case.declared_rejections[{i}]"
        _check_keys(entry, _REJECTION_KEYS, ctx)
        _require(_is_int(entry["step"]) and entry["step"] >= 0,
                 f"{ctx}.step must be a non-negative integer")
        _require(_is_str(entry["tool_name"]), f"{ctx}.tool_name must be a non-empty string")
        steps.append(entry["step"])
    duplicates = sorted({s for s in steps if steps.count(s) > 1})
    _require(not duplicates, f"case.declared_rejections has duplicate step(s) {duplicates}")


def _validate_case(raw: dict[str, Any]) -> None:
    _require(_is_str(raw["case_id"]), "case.case_id must be a non-empty string")
    _check_keys(raw["position"], {"path", "sha256"}, "case.position")
    _check_keys(raw["script"], {"path", "sha256"}, "case.script")
    tags = raw["tags"]
    _require(isinstance(tags, list) and all(_is_str(t) for t in tags), "case.tags must be a list of strings")
    validate_case_expected(raw["expected"])
    if "declared_rejections" in raw:
        _validate_declared_rejections(raw["declared_rejections"])


def _validate_suite(raw: dict[str, Any]) -> None:
    _require(_is_str(raw["suite_id"]), "validation_suite.suite_id must be a non-empty string")
    cases = raw["cases"]
    _require(isinstance(cases, list) and cases, "validation_suite.cases must be a non-empty list")
    for i, ref in enumerate(cases):
        _check_keys(ref, {"path", "sha256"}, f"validation_suite.cases[{i}]")
    _check_keys(raw["position"], {"path", "sha256"}, "validation_suite.position")
    _check_keys(
        raw["toolset_identity"], {"source_sha256", "schemas_sha256"}, "validation_suite.toolset_identity"
    )
    _require(_is_str(raw["contract_identity"]), "validation_suite.contract_identity must be a non-empty string")
    _require(raw["max_steps"] == 15 and _is_int(raw["max_steps"]), "validation_suite.max_steps must be 15")
    _require(
        raw["episode_wall_s"] == 300 and _is_int(raw["episode_wall_s"]),
        "validation_suite.episode_wall_s must be 300",
    )
    cap = raw["result_char_cap"]
    _require(_is_int(cap) and cap > 0, "validation_suite.result_char_cap must be a positive integer")
    _require(raw["actor_kind"] == "scripted", "validation_suite.actor_kind must be 'scripted'")
    _require(raw["counting"] is False, "validation_suite.counting must be false")


def _validate_lock(raw: dict[str, Any]) -> None:
    _require(_is_str(raw["lock_id"]), "lock.lock_id must be a non-empty string")
    suite = raw["suite"]
    # `bound` says what suite.sha256 binds: the suite file's bytes (suite.path is
    # its repo-relative path) or a portable digest of the loaded document
    # (suite.path is the suite id). Locks written before `bound` omit it.
    _check_keys(suite, {"path", "sha256", "bound"} if isinstance(suite, dict) and "bound" in suite
                else {"path", "sha256"}, "lock.suite")
    _require(suite.get("bound", "portable") in ("bytes", "portable"),
             "lock.suite.bound must be 'bytes' or 'portable'")
    for key in ("scripts", "cases"):
        refs = raw[key]
        _require(isinstance(refs, list) and refs, f"lock.{key} must be a non-empty list")
        for i, ref in enumerate(refs):
            _check_keys(ref, {"path", "sha256"}, f"lock.{key}[{i}]")
    for key in ("archive_identity", "state_identity", "provenance_identity", "limits", "actor"):
        _require(isinstance(raw[key], dict), f"lock.{key} must be a mapping")
    _require(isinstance(raw["schedule"], list) and raw["schedule"], "lock.schedule must be a non-empty list")
    for key in ("code_identity", "schema_identity"):
        _require(_is_str(raw[key]), f"lock.{key} must be a non-empty string")
    for key in _LOCK_NULL_FIELDS:
        _require(raw[key] is None, f"lock.{key} must be null")


_VALIDATORS = {
    "position": _validate_position,
    "script": _validate_script,
    "case": _validate_case,
    "validation_suite": _validate_suite,
    "lock": _validate_lock,
}


def validate_v2_document(raw: dict[str, Any], *, kind: str) -> None:
    """Validate a parsed v2 document of `kind`; raises `ValueError` on any defect."""
    if kind not in _KIND_KEYS:
        raise ValueError(f"unknown v2 document kind {kind!r}; expected one of {sorted(_KIND_KEYS)}")
    if isinstance(raw, dict):
        optional = _OPTIONAL_KEYS.get(kind, set()) & raw.keys()
    else:
        optional = set()
    _check_keys(raw, _KIND_KEYS[kind] | optional, kind)
    _require(
        raw["schema_version"] == SCHEMA_VERSION,
        f"{kind}.schema_version must be exactly {SCHEMA_VERSION!r}",
    )
    _VALIDATORS[kind](raw)


def _absolute(value: str, base: Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else base / p


def load_v2_document(path: Path, *, kind: str) -> dict[str, Any]:
    """Load, validate, and path-resolve a v2 document.

    Relative path fields become absolute against the document's directory.
    The referenced files are not required to exist.
    """
    path = Path(path)
    raw = _load_yaml_document(path, kind)
    validate_v2_document(raw, kind=kind)
    doc = copy.deepcopy(raw)
    base = Path(os.path.abspath(path)).parent
    for dotted in _PATH_FIELDS[kind]:
        parts = dotted.split(".")
        _resolve_path(doc, parts, base)
    return doc


def _resolve_path(node: Any, parts: list[str], base: Path) -> None:
    head, rest = parts[0], parts[1:]
    is_list = head.endswith("[]")
    key = head[:-2] if is_list else head
    if not isinstance(node, dict) or key not in node:
        return
    if is_list:
        for item in node[key]:
            if rest:
                _resolve_path(item, rest, base)
        return
    if rest:
        _resolve_path(node[key], rest, base)
    elif isinstance(node[key], str):
        node[key] = str(_absolute(node[key], base))
