"""Replayable, recipe-driven authoring of version-2 benchmark positions.

A recipe (`load_recipe`) declares how one scenario is built from a hashed
organic base: raw setup Lua with readback, survey queries through the frozen
registry tools, binding selectors, a coverage rule, the proposed rubric,
legality probes, the archive name/path, output paths and script/case
templates. `run_authoring_stage` runs one stage of the ordered machine

    survey -> apply -> probe -> archive -> capture -> verify -> menu-check
           -> validate -> finish

against FireTuner through injected `LiveOps` (production wiring when `ops`
is None). Every stage opens the attempt's `AuthoringJournal` and calls
`begin(...)` before its first live command, so the three-hour clock starts
before anything touches the game; an expired clock refuses the stage.

Stage outputs live in the attempt directory and are never overwritten:

    authoring-journal.json        persistent clock (Task 14)
    stages/NNN-<stage>.json       one record per run or invalidation
    observations/NNN-survey-II.json  full + capped public tool results (base)
    observations/archived-NNN-II.json  the same queries at the archived start
    samples/NNN-<stage>-state.json   private v2 captures (never observations)
    mutations/NNN-<label>.json    base replay, setup requests/readback, bindings
    probes/NNN-<probe-id>.json    legality probes, including failures
    validation/<position-id>/<suite-id>/   Task 13 run directory
    evidence-index.json           written by `finish` after the journal closes,
                                  or by `abandon` for a failed/abandoned attempt

Every base load is confirmed: a failed loader result is refused, the
connection is re-established, and the loaded identity (turn, seed, civ,
player) must equal the recipe's `base_save_identity` before any setup Lua.
`menu-check` uses the crash-recovery `restart_and_load` path and requires the
post-load identity and v2 digest to equal the position's expected state.

`survey`, `apply` and `probe` may repeat under the same clock; repeating one
records an `invalidated` record for every downstream stage. Every archive
after a published one takes the next version (`{version}` in the recipe's
archive/output paths), so changed bindings never reuse an archive identity.

Setup Lua conventions: a setup operation's write result must contain no line
starting with ``ERR``/``Error``, and its readback query must print at least
one line starting with ``OK`` and none starting with ``ERR``.

A probe may also require its result to match `expect_pattern` and may
`measure_target` (a tracked target's hp delta across the probe). A recipe's
`measured_parameters` mark provisional rubric literals (every damage threshold
must be one); `archive` refuses to run unless this attempt's measuring probes
satisfy each parameter's rule, then freezes the value into the position rubric
and records the measurements in the authoring provenance.

Offline commands (never connect to the game):

    uv run python -m civ_mcp.arena.benchmark_authoring STAGE --recipe PATH --attempt-dir PATH [--predecessor-journal PATH]
    uv run python -m civ_mcp.arena.benchmark_authoring preflight --recipes PATH... --output PATH [--probe PATH]
    uv run python -m civ_mcp.arena.benchmark_authoring gate --packets PATH... --output PATH [--preflight PATH]
    uv run python -m civ_mcp.arena.benchmark_authoring evidence-files --root PATH --output PATH
    uv run python -m civ_mcp.arena.benchmark_authoring abandon --recipe PATH --attempt-dir PATH --reason TEXT
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import dataclasses
import hashlib
import inspect
import json
import os
import re
import sys
import time
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import yaml

from civ_mcp.arena import registry
from civ_mcp.arena.action_metrics import classify_result
from civ_mcp.arena.benchmark_agent import FINISH_TRIAL_TOOL_NAME
from civ_mcp.arena.benchmark_audit import reproduce_audit
from civ_mcp.arena.benchmark_authoring_journal import AuthoringJournal
from civ_mcp.arena.benchmark_capture import CaptureTelemetry
from civ_mcp.arena.benchmark_contract_v2 import (
    document_digest,
    implementation_fingerprint,
    toolkit_fingerprint,
)
from civ_mcp.arena.benchmark_manifest_v2 import (
    SCHEMA_VERSION,
    load_toolset,
    load_v2_document,
    validate_case_expected,
    validate_v2_document,
)
from civ_mcp.arena.benchmark_part1_evidence import PREFLIGHT_OUTPUT, PROBE_PROVENANCE
from civ_mcp.arena.benchmark_part1_gate import (
    check_part1_gate,
    check_part1_packet,
    probe_problems,
)
from civ_mcp.arena.benchmark_position import REQUIRED_CYCLES
from civ_mcp.arena.benchmark_predicates_v2 import evaluate_predicate, validate_predicate
from civ_mcp.arena.benchmark_scoring_v2 import (
    _group_maxima,
    _objective_maxima,
    validate_rubric,
    validate_rubric_structure,
)
from civ_mcp.arena.benchmark_state_v2 import digest_state_v2
from civ_mcp.game_launcher import WSL_WINDOWS_REPO

__all__ = [
    "STAGES",
    "STAGE_PREREQUISITES",
    "LiveOps",
    "abandon_attempt",
    "check_part1_gate",
    "check_part1_packet",
    "evidence_files",
    "load_recipe",
    "main",
    "preflight",
    "production_ops",
    "run_authoring_stage",
    "stage_status",
    "validate_stage_transition",
]

_REPO_ROOT = Path(__file__).resolve().parents[3]

STAGE_PREREQUISITES = {
    "survey": (), "apply": ("survey",), "probe": ("apply",),
    "archive": ("probe",), "capture": ("archive",), "verify": ("capture",),
    "menu-check": ("verify",), "validate": ("menu-check",), "finish": ("validate",),
}
STAGES: tuple[str, ...] = tuple(STAGE_PREREQUISITES)
REPEATABLE_STAGES = frozenset({"survey", "apply", "probe"})
# One sentence per stage: precondition -> what it does -> where outputs land.
STAGE_HELP = {
    "survey": "LIVE, no precondition (opens/resumes the 3h clock): load and verify the base "
              "save, record its private state and survey query results under "
              "ATTEMPT/samples and ATTEMPT/observations.",
    "apply": "LIVE, needs survey passed: reload the base, run setup Lua with readback, resolve "
             "bindings and setup assertions into ATTEMPT/mutations.",
    "probe": "LIVE, needs apply passed: run each legality probe from a fresh base replay into "
             "ATTEMPT/probes.",
    "archive": "LIVE, needs probe passed: replay setup, require every survey fact within the "
               "result cap, then save, export and publish the archive to the recipe's "
               "benchmarks/saves path.",
    "capture": "LIVE, needs archive passed: deploy and capture the archived position and write "
               "the position, authoring provenance, scripts, cases and suite under benchmarks/.",
    "verify": "LIVE, needs capture passed: reload the archive repeatedly and require every "
              "digest to equal the position's expected state.",
    "menu-check": "LIVE, needs verify passed: restart the game via restart_and_load and require "
                  "the loaded identity and digest to match.",
    "validate": "LIVE, needs menu-check passed: run the scripted validation suite into "
                "ATTEMPT/validation and rebuild its reports identically.",
    "finish": "OFFLINE, needs every stage passed: close the journal as passed, then write "
              "ATTEMPT/evidence-index.json and the provenance packet (re-run to recover a "
              "failed index/packet write).",
}
ABANDON = "abandon"

JOURNAL_FILE = "authoring-journal.json"
INDEX_FILE = "evidence-index.json"
EVIDENCE_SCOPES = ("benchmark_runs/plan3-part1/", "benchmarks/")
BASE_EXPORT_DIR = "benchmark_runs/plan3-part1/bases"
AUDIT_FIXTURE = "tests/arena/fixtures/builder_uncredited_audit_v1.json"
PREFLIGHT_PYTEST = "benchmark_runs/plan3-part1/preflight/pytest.txt"
PREFLIGHT_PYTEST_RESULT = "benchmark_runs/plan3-part1/preflight/pytest-result.json"
EPISODE_WALL_S = 300


def validate_stage_transition(stage, completed):
    for prerequisite in STAGE_PREREQUISITES[stage]:
        if completed.get(prerequisite) != "passed":
            raise ValueError(f"{stage} requires successful {prerequisite}")


class StageFailure(Exception):
    """A gate inside a stage failed; the stage is recorded as failed."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _json_bytes(doc: Any) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _rel(path: Path, root: Path) -> str:
    try:
        return Path(os.path.abspath(path)).relative_to(root).as_posix()
    except ValueError:
        raise ValueError(f"{path} is outside the repository root {root}") from None


def _write_new(path: Path, data: bytes) -> None:
    """Create `path`; never replaces an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as fh:
        fh.write(data)


def _write_immutable(path: Path, data: bytes) -> None:
    """Create `path`, or accept an existing file with identical bytes."""
    if path.exists():
        if path.read_bytes() != data:
            raise StageFailure(f"refusing to overwrite immutable output {path} with different bytes")
        return
    _write_new(path, data)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _is_transient(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    name = parts[-1] if parts else rel
    return ("__pycache__" in parts or name.endswith((".lock", ".tmp", ".pyc"))
            or name.startswith(".publish."))


# ---------------------------------------------------------------------------
# Recipe schema
# ---------------------------------------------------------------------------

_RECIPE_KEYS = {
    "schema_version", "recipe_id", "family", "scenario_id", "version", "predecessor",
    "substitution_reason", "material_change", "toolset_id", "toolset_path", "max_steps",
    "positive_maximum", "harm_maximum", "base_save_identity", "player_id", "result_char_cap",
    "setup", "survey", "bindings", "coverage_rule", "objectives", "harms", "probes",
    "archive", "outputs", "scripts", "cases",
}
_SUBSTITUTE_FIELDS = ("predecessor", "substitution_reason", "material_change")
_OUTPUT_KEYS = {"position", "provenance_authoring", "provenance_packet", "scripts_dir",
                "validation_dir"}
_BINDING_FAMILIES = {"unit": "units", "tile": "tiles", "city": "cities", "target": "targets"}
_TEMPLATE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_-]*)\.([a-z_]+)\}")
_BINDING_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DUMMY_BINDING = {"owner": 0, "id": 0, "unit_index": 0, "x": 0, "y": 0, "xy": [0, 0],
                  "pair": [0, 0]}


def _check_keys(raw: Any, expected: set[str], context: str, optional: set[str] = frozenset()) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{context} must be a mapping")
    missing = sorted(expected - raw.keys())
    extra = sorted(str(k) for k in raw.keys() - expected - optional)
    if missing or extra:
        raise ValueError(f"{context}: missing keys {missing}, unexpected keys {extra}")


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ValueError(message)


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _lua_keys(node: Any, where: str) -> list[str]:
    if isinstance(node, dict):
        found = [f"{where}.{k}" for k in node if k == "lua"]
        for key, value in node.items():
            found += _lua_keys(value, f"{where}.{key}")
        return found
    if isinstance(node, list):
        return [p for i, item in enumerate(node) for p in _lua_keys(item, f"{where}[{i}]")]
    return []


def substitute_bindings(node: Any, bindings: dict[str, dict[str, Any]]) -> Any:
    """Replace every whole-string ``${name.field}`` with the resolved value."""
    if isinstance(node, str):
        if "${" not in node:
            return node
        match = _TEMPLATE.fullmatch(node)
        if match is None:
            raise ValueError(f"binding template {node!r} must be a whole string ${{name.field}}")
        name, field = match.groups()
        if name not in bindings:
            raise ValueError(f"unknown binding {name!r} in {node!r}")
        if field not in bindings[name]:
            raise ValueError(f"binding {name!r} has no field {field!r}")
        return copy.deepcopy(bindings[name][field])
    if isinstance(node, list):
        return [substitute_bindings(item, bindings) for item in node]
    if isinstance(node, dict):
        return {key: substitute_bindings(value, bindings) for key, value in node.items()}
    return node


def _format_version(template: Any, context: str) -> None:
    _require(_is_str(template) and "{version}" in template,
             f"{context} must be a path/name containing '{{version}}'")
    try:
        template.format(version=1)
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"{context}: invalid template {template!r}: {exc}") from exc


def _validate_assertions(assertions: Any, dummy: dict, context: str) -> None:
    _require(isinstance(assertions, list), f"{context} must be a list")
    ids = []
    for i, item in enumerate(assertions):
        ctx = f"{context}[{i}]"
        _check_keys(item, {"id", "predicate", "value"}, ctx)
        _require(_is_str(item["id"]), f"{ctx}.id must be a non-empty string")
        _require(isinstance(item["value"], bool), f"{ctx}.value must be a boolean")
        validate_predicate(substitute_bindings(item["predicate"], dummy))
        ids.append(item["id"])
    _require(len(ids) == len(set(ids)), f"{context} has duplicate ids")


_PROBE_OPTIONAL_KEYS = {"expect_pattern", "measure_target"}
_MEASURED_KEYS = {"name", "provisional", "used_by", "source_probes", "rule"}
_MEASURED_RULES = {"strictly_between", "survives"}


def _rubric_path_value(raw: dict[str, Any], path: Any, context: str) -> Any:
    """Follow a ``used_by`` path (rooted at the rubric: objectives/harms)."""
    _require(isinstance(path, list) and len(path) >= 2 and path[0] in ("objectives", "harms"),
             f"{context} must be a list path starting at 'objectives' or 'harms'")
    node: Any = raw
    for step in path:
        if isinstance(node, dict) and isinstance(step, str) and step in node:
            node = node[step]
        elif isinstance(node, list) and _is_int(step) and 0 <= step < len(node):
            node = node[step]
        else:
            raise ValueError(f"{context}: path {path} does not resolve in the rubric")
    return node


def _threshold_paths(node: Any, path: list[Any]) -> list[list[Any]]:
    """Every ``target_damaged.minimum_damage`` literal under `node`."""
    if isinstance(node, dict):
        found = [path + ["minimum_damage"]] if node.get("kind") == "target_damaged" else []
        return found + [p for k, v in node.items() for p in _threshold_paths(v, path + [k])]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in _threshold_paths(v, path + [i])]
    return []


def _validate_measured_parameters(raw: dict[str, Any], probes: dict[str, dict[str, Any]]) -> None:
    """Measured parameters: provisional rubric literals that the archive stage
    may freeze only when this attempt's measuring probes satisfy the rule.
    Every damage threshold in the rubric must be one of them."""
    params = raw.get("measured_parameters", [])
    _require(isinstance(params, list), "recipe.measured_parameters must be a list")
    covered: list[list[Any]] = []
    names = []
    for i, param in enumerate(params):
        ctx = f"recipe.measured_parameters[{i}]"
        _check_keys(param, _MEASURED_KEYS, ctx)
        _require(_is_str(param["name"]), f"{ctx}.name must be a non-empty string")
        _require(param["provisional"] is True,
                 f"{ctx}.provisional must be true: the rubric literal is a placeholder until "
                 "the archive stage freezes it from measured probe evidence")
        used = param["used_by"]
        _require(isinstance(used, list) and bool(used), f"{ctx}.used_by must be a non-empty list")
        values = []
        for j, path in enumerate(used):
            value = _rubric_path_value({"objectives": raw["objectives"], "harms": raw["harms"]},
                                       path, f"{ctx}.used_by[{j}]")
            _require(type(value) in (int, float), f"{ctx}.used_by[{j}] must name a number")
            values.append(value)
            covered.append(list(path))
        _require(len(set(values)) == 1, f"{ctx}.used_by literals must share one value")
        sources = param["source_probes"]
        _require(isinstance(sources, list) and bool(sources) and all(
            s in probes and "measure_target" in probes[s] for s in sources),
            f"{ctx}.source_probes must name probes that declare measure_target")
        rule = param["rule"]
        _require(isinstance(rule, dict) and bool(rule) and set(rule) <= _MEASURED_RULES,
                 f"{ctx}.rule must use only {sorted(_MEASURED_RULES)}")
        if "strictly_between" in rule:
            pair = rule["strictly_between"]
            _require(isinstance(pair, list) and len(pair) == 2 and all(p in sources for p in pair),
                     f"{ctx}.rule.strictly_between must be [low_probe, high_probe] from "
                     "source_probes")
        if "survives" in rule:
            _require(isinstance(rule["survives"], list) and bool(rule["survives"]) and all(
                p in sources for p in rule["survives"]),
                f"{ctx}.rule.survives must list probes from source_probes")
        names.append(param["name"])
    _require(len(names) == len(set(names)), "recipe.measured_parameters has duplicate names")
    for path in _threshold_paths(raw["objectives"], ["objectives"]) + \
            _threshold_paths(raw["harms"], ["harms"]):
        _require(path in covered, f"damage threshold at {path} must be a measured parameter "
                 "(frozen from probe readback, never a fixed constant)")


def _validate_recipe(raw: dict[str, Any], *, root: Path) -> None:
    _check_keys(raw, _RECIPE_KEYS, "recipe", optional={"measured_parameters"})
    lua = [p for key, value in raw.items() if key != "setup" for p in _lua_keys(value, key)]
    lua += _lua_keys(raw["setup"].get("assertions") if isinstance(raw["setup"], dict) else None,
                     "setup.assertions")
    _require(not lua, f"raw Lua is only allowed under setup.operations; found {lua}")
    _require(raw["schema_version"] == SCHEMA_VERSION,
             f"recipe.schema_version must be exactly {SCHEMA_VERSION!r}")
    for key in ("recipe_id", "family", "scenario_id", "toolset_id", "toolset_path"):
        _require(_is_str(raw[key]), f"recipe.{key} must be a non-empty string")
    _require(_is_int(raw["version"]) and raw["version"] >= 1,
             "recipe.version must be a positive integer")
    substitute = [raw[k] for k in _SUBSTITUTE_FIELDS]
    if any(v is not None for v in substitute):
        for key in _SUBSTITUTE_FIELDS:
            _require(_is_str(raw[key]), f"substitute recipe requires non-empty {key}")
    _require(raw["max_steps"] == 15 and _is_int(raw["max_steps"]), "recipe.max_steps must be 15")
    _require(raw["positive_maximum"] == 12 and _is_int(raw["positive_maximum"]),
             "recipe.positive_maximum must be 12")
    harm_max = raw["harm_maximum"]
    _require(_is_int(harm_max) and 0 <= harm_max <= 12,
             "recipe.harm_maximum must be an integer in [0, 12]")
    _require(_is_int(raw["player_id"]), "recipe.player_id must be an integer")
    _require(_is_int(raw["result_char_cap"]) and raw["result_char_cap"] > 0,
             "recipe.result_char_cap must be a positive integer")
    base = raw["base_save_identity"]
    _check_keys(base, {"name", "sha256", "turn", "seed", "civ_type"}, "recipe.base_save_identity")
    _require(_is_str(base["name"]), "recipe.base_save_identity.name must be a non-empty string")
    _require(_is_int(base["turn"]) and base["turn"] >= 0,
             "recipe.base_save_identity.turn must be a non-negative integer")
    _require(_is_int(base["seed"]), "recipe.base_save_identity.seed must be an integer")
    _require(_is_str(base["civ_type"]), "recipe.base_save_identity.civ_type must be a non-empty string")
    _require(isinstance(base["sha256"], str) and bool(_HEX64.match(base["sha256"])),
             "recipe.base_save_identity.sha256 must be 64 lowercase hex characters")

    toolset = load_toolset(root / raw["toolset_path"])
    _require(toolset["toolset_id"] == raw["toolset_id"],
             f"recipe.toolset_id {raw['toolset_id']!r} does not match the toolset file "
             f"({toolset['toolset_id']!r})")
    tools = set(toolset["game_tools"])

    # Bindings first: every template elsewhere refers to them.
    bindings = raw["bindings"]
    _require(isinstance(bindings, list), "recipe.bindings must be a list")
    names: list[str] = []
    for i, binding in enumerate(bindings):
        ctx = f"recipe.bindings[{i}]"
        _check_keys(binding, {"name", "selector", "resolves"}, ctx)
        _require(isinstance(binding["name"], str) and bool(_BINDING_NAME.match(binding["name"])),
                 f"{ctx}.name must be an identifier")
        _require(binding["resolves"] in _BINDING_FAMILIES,
                 f"{ctx}.resolves must be one of {sorted(_BINDING_FAMILIES)}")
        selector = binding["selector"]
        _require(isinstance(selector, dict) and bool(selector), f"{ctx}.selector must be a non-empty mapping")
        for key, value in selector.items():
            if key == "area":
                _require(isinstance(value, list) and bool(value) and all(
                    isinstance(p, list) and len(p) == 2 and all(_is_int(v) for v in p)
                    for p in value), f"{ctx}.selector.area must be a list of integer pairs")
            else:
                _require(isinstance(value, (str, int, bool)) and value is not None,
                         f"{ctx}.selector.{key} must be a scalar")
        names.append(binding["name"])
    _require(len(names) == len(set(names)), "recipe.bindings has duplicate names")
    rules: dict[str, str] = {}
    for binding in bindings:
        rule = json.dumps([binding["resolves"], binding["selector"]], sort_keys=True)
        _require(rule not in rules,
                 f"ambiguous binding rule: {binding['name']!r} and {rules.get(rule)!r} "
                 "share the same selector")
        rules[rule] = binding["name"]
    dummy ={name: dict(_DUMMY_BINDING) for name in names}
    kinds = {b["name"]: b["resolves"] for b in bindings}

    setup = raw["setup"]
    _check_keys(setup, {"operations", "assertions"}, "recipe.setup")
    _require(isinstance(setup["operations"], list), "recipe.setup.operations must be a list")
    for i, op in enumerate(setup["operations"]):
        ctx = f"recipe.setup.operations[{i}]"
        _check_keys(op, {"kind", "lua", "readback"}, ctx)
        _require(op["kind"] == "lua", f"{ctx}.kind must be 'lua'")
        _require(_is_str(op["lua"]) and _is_str(op["readback"]),
                 f"{ctx}.lua and .readback must be non-empty strings")
    _validate_assertions(setup["assertions"], dummy, "recipe.setup.assertions")

    survey = raw["survey"]
    _check_keys(survey, {"queries", "required_facts"}, "recipe.survey")
    _require(isinstance(survey["queries"], list) and bool(survey["queries"]),
             "recipe.survey.queries must be a non-empty list")
    for i, query in enumerate(survey["queries"]):
        ctx = f"recipe.survey.queries[{i}]"
        _check_keys(query, {"tool", "arguments"}, ctx)
        _require(query["tool"] in tools, f"{ctx}.tool {query['tool']!r} is not in the frozen toolset")
        _require(registry.TOOL_REGISTRY[query["tool"]].verb == "",
                 f"{ctx}.tool {query['tool']!r} is an action tool; survey queries must be "
                 "read-only (they also run on the archived start before save)")
        _require(isinstance(query["arguments"], dict), f"{ctx}.arguments must be a mapping")
        # Binding references resolve at archive; their names must be declared.
        substitute_bindings(query["arguments"], dummy)
    _require(isinstance(survey["required_facts"], list) and bool(survey["required_facts"]),
             "recipe.survey.required_facts must be a non-empty list")
    query_tools = {query["tool"] for query in survey["queries"]}
    for i, fact in enumerate(survey["required_facts"]):
        ctx = f"recipe.survey.required_facts[{i}]"
        _check_keys(fact, {"id", "pattern", "source"}, ctx)
        _require(_is_str(fact["id"]) and _is_str(fact["pattern"]), f"{ctx} fields must be strings")
        _require(fact["source"] in query_tools,
                 f"{ctx}.source {fact['source']!r} must name a survey query tool "
                 f"({sorted(query_tools)}) as its discoverability source")
        try:
            re.compile(fact["pattern"])
        except re.error as exc:
            raise ValueError(f"{ctx}.pattern is not a valid regex: {exc}") from exc

    rule = raw["coverage_rule"]
    _check_keys(rule, {"include_owned_tiles", "area_radius", "tracked_target_bindings"},
                "recipe.coverage_rule")
    _require(isinstance(rule["include_owned_tiles"], bool),
             "recipe.coverage_rule.include_owned_tiles must be a boolean")
    _require(_is_int(rule["area_radius"]) and rule["area_radius"] >= 0,
             "recipe.coverage_rule.area_radius must be a non-negative integer")
    _require(isinstance(rule["tracked_target_bindings"], list),
             "recipe.coverage_rule.tracked_target_bindings must be a list")
    for name in rule["tracked_target_bindings"]:
        _require(kinds.get(name) in ("unit", "target"),
                 f"tracked target binding {name!r} must name a unit or target binding")

    rubric = substitute_bindings({"objectives": raw["objectives"], "harms": raw["harms"]}, dummy)
    validate_rubric_structure(rubric)
    positive = sum(_objective_maxima(rubric).values())
    _require(positive == raw["positive_maximum"],
             f"objective maxima sum to {positive}, not positive_maximum {raw['positive_maximum']}")
    harm = sum(_group_maxima(rubric).values())
    _require(harm == harm_max, f"harm group maxima sum to {harm}, not harm_maximum {harm_max}")
    _check_compatible_objectives(raw["objectives"])
    _check_rungs_nontrivial(rubric)

    probes = raw["probes"]
    _require(isinstance(probes, list) and bool(probes), "recipe.probes must be a non-empty list")
    probe_ids = []
    for i, probe in enumerate(probes):
        ctx = f"recipe.probes[{i}]"
        _check_keys(probe, {"id", "tool", "arguments_from_bindings", "expect", "restore"}, ctx,
                    optional=_PROBE_OPTIONAL_KEYS)
        _require(_is_str(probe["id"]), f"{ctx}.id must be a non-empty string")
        _require(probe["tool"] in tools, f"{ctx}.tool {probe['tool']!r} is not in the frozen toolset")
        _require(isinstance(probe["arguments_from_bindings"], dict),
                 f"{ctx}.arguments_from_bindings must be a mapping")
        substitute_bindings(probe["arguments_from_bindings"], dummy)
        _require(probe["expect"] in ("ok", "rejected"), f"{ctx}.expect must be 'ok' or 'rejected'")
        _require(probe["restore"] is True, f"{ctx}.restore must be true")
        if "expect_pattern" in probe:
            _require(_is_str(probe["expect_pattern"]), f"{ctx}.expect_pattern must be a regex")
            try:
                re.compile(probe["expect_pattern"])
            except re.error as exc:
                raise ValueError(f"{ctx}.expect_pattern is not a valid regex: {exc}") from exc
        if "measure_target" in probe:
            pair = substitute_bindings(probe["measure_target"], dummy)
            _require(isinstance(pair, list) and len(pair) == 2 and all(_is_int(v) for v in pair),
                     f"{ctx}.measure_target must be a ${{binding.pair}} template")
        probe_ids.append(probe["id"])
    _require(len(probe_ids) == len(set(probe_ids)), "recipe.probes has duplicate ids")
    _validate_measured_parameters(raw, {p["id"]: p for p in probes})

    archive = raw["archive"]
    _check_keys(archive, {"name", "path"}, "recipe.archive")
    _format_version(archive["name"], "recipe.archive.name")
    _format_version(archive["path"], "recipe.archive.path")
    _require(archive["path"].endswith(".Civ6Save"), "recipe.archive.path must end with .Civ6Save")
    outputs = raw["outputs"]
    _check_keys(outputs, _OUTPUT_KEYS, "recipe.outputs")
    for key in sorted(_OUTPUT_KEYS):
        _format_version(outputs[key], f"recipe.outputs.{key}")
    for key in ("toolset_path", "archive.path", *(f"outputs.{k}" for k in sorted(_OUTPUT_KEYS))):
        value = raw[key] if "." not in key else raw[key.split(".")[0]][key.split(".")[1]]
        _require(not Path(value).is_absolute() and ".." not in PurePosixPath(value).parts,
                 f"recipe.{key} must be a repository-relative path")

    scripts = raw["scripts"]
    _require(isinstance(scripts, list) and bool(scripts), "recipe.scripts must be a non-empty list")
    script_ids = []
    for i, script in enumerate(scripts):
        ctx = f"recipe.scripts[{i}]"
        _check_keys(script, {"script_id", "batches"}, ctx)
        doc = {"schema_version": SCHEMA_VERSION, **substitute_bindings(script, dummy)}
        validate_v2_document(doc, kind="script")
        for batch in script["batches"]:
            for call in batch["calls"]:
                _require(call["name"] == FINISH_TRIAL_TOOL_NAME or call["name"] in tools,
                         f"{ctx}: tool {call['name']!r} is not in the frozen toolset")
        script_ids.append(script["script_id"])
    _require(len(script_ids) == len(set(script_ids)), "recipe.scripts has duplicate script_id")

    cases = raw["cases"]
    _require(isinstance(cases, list) and bool(cases), "recipe.cases must be a non-empty list")
    case_ids = []
    for i, case in enumerate(cases):
        ctx = f"recipe.cases[{i}]"
        _check_keys(case, {"case_id", "script_id", "tags", "expected"}, ctx,
                    optional={"declared_rejections"})
        _require(_is_str(case["case_id"]), f"{ctx}.case_id must be a non-empty string")
        _require(case["script_id"] in script_ids, f"{ctx}.script_id {case['script_id']!r} is unknown")
        _require(isinstance(case["tags"], list) and all(_is_str(t) for t in case["tags"]),
                 f"{ctx}.tags must be a list of strings")
        validate_case_expected(substitute_bindings(case["expected"], dummy))
        case_ids.append(case["case_id"])
    _require(len(case_ids) == len(set(case_ids)), "recipe.cases has duplicate case_id")


def _tile_claims(predicate: dict[str, Any]) -> list[tuple[str, str]]:
    """(tile key, improvement) for every ``tile_matches`` leaf naming an improvement."""
    if predicate.get("kind") in ("all", "any"):
        return [c for child in predicate["predicates"] for c in _tile_claims(child)]
    if predicate.get("kind") != "tile_matches" or "improvement" not in predicate["fields"]:
        return []
    return [(json.dumps(tile), predicate["fields"]["improvement"]) for tile in predicate["tiles"]]


def _check_compatible_objectives(objectives: list[dict[str, Any]]) -> None:
    """No tile (template or literal) may be claimed by two objectives with
    different improvements: such maxima are individually feasible but
    mutually exclusive."""
    claims: dict[str, tuple[str, str]] = {}
    for obj in objectives:
        for rung in obj["rungs"]:
            for tile, improvement in _tile_claims(rung["predicate"]):
                other = claims.setdefault(tile, (obj["id"], improvement))
                _require(other[0] == obj["id"] or other[1] == improvement,
                         f"incompatible objectives {other[0]!r} and {obj['id']!r} claim tile "
                         f"{json.loads(tile)} with {other[1]} and {improvement}")


def _dummy_initial_state() -> dict[str, Any]:
    """A neutral complete state at the dummy binding (every entity is id 0 at
    0,0): a civilian unit, a visible hostile target, a city with an empty
    queue and no buildings, and an unimproved zero-yield tile."""
    yields = {"food": 0, "production": 0, "gold": 0, "science": 0, "culture": 0, "faith": 0}
    return {
        "player_id": 0,
        "units": [{"owner": 0, "id": 0, "unit_index": 0, "type": "UNIT_DUMMY",
                   "role": "civilian", "x": 0, "y": 0, "hp": 100, "max_hp": 100,
                   "moves": 0, "charges": 0}],
        "targets": [{"owner": 0, "id": 0, "tracked": True, "role": "combat", "hostile": True,
                     "visible": True, "status": "alive_visible", "x": 0, "y": 0,
                     "hp": 100, "max_hp": 100}],
        "cities": [{"owner": 0, "id": 0, "name": "DUMMY", "x": 0, "y": 0, "population": 1,
                    "housing": 0, "buildings": [], "districts": [],
                    "queue": {"item_kind": "NONE", "item_type": "NONE", "repair": False,
                              "target_x": None, "target_y": None}}],
        "tiles": [{"x": 0, "y": 0, "owner": 0, "terrain": "NONE", "feature": "NONE",
                   "resource": "NONE", "improvement": "NONE", "pillaged": False,
                   "district": "NONE", "visible": True, "yields": yields}],
        "resources": [],
        "row_counts": {"identity": 1, "unit": 1, "target": 1, "city": 1, "building": 0,
                       "district": 0, "queue": 1, "tile": 1, "resource": 0},
    }


def _check_rungs_nontrivial(rubric: dict[str, Any]) -> None:
    """Offline counterpart of `validate_rubric`'s live check: no positive rung
    (under dummy bindings) may already hold at a neutral initial state."""
    from civ_mcp.arena.benchmark_state import BenchmarkStateError
    state = _dummy_initial_state()
    for obj in rubric["objectives"]:
        for ri, rung in enumerate(obj["rungs"]):
            try:
                held = evaluate_predicate(rung["predicate"], initial=state, final=state)
            except BenchmarkStateError as exc:
                raise ValueError(f"objective {obj['id']!r} rung {ri} cannot be evaluated "
                                 f"offline: {exc}") from exc
            _require(not held, f"objective {obj['id']!r} rung {ri} is initially true at the "
                     "neutral dummy state (structurally trivial)")


def load_recipe(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    """Load and strictly validate an authoring recipe (YAML or JSON).

    Repository-relative paths (toolset, archive, outputs) resolve against
    `root` (the repository by default)."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"recipe {path}: invalid YAML: {exc}") from exc
    _validate_recipe(raw, root=Path(root or _REPO_ROOT))
    return raw


# ---------------------------------------------------------------------------
# Stage records
# ---------------------------------------------------------------------------

_STAGE_FILE = re.compile(r"(\d{3,})-([a-z-]+)\.json\Z")


def _stage_records(attempt_dir: Path) -> list[dict[str, Any]]:
    directory = Path(attempt_dir) / "stages"
    if not directory.is_dir():
        return []
    records = []
    for path in directory.iterdir():
        match = _STAGE_FILE.match(path.name)
        if match and (match.group(2) in STAGE_PREREQUISITES or match.group(2) == ABANDON):
            records.append(json.loads(path.read_text(encoding="utf-8")))
    return sorted(records, key=lambda r: r["sequence"])


def _latest(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        latest[record["stage"]] = record
    return latest


def stage_status(attempt_dir: Path) -> dict[str, str]:
    """Latest status per stage: ``passed``, ``failed`` or ``invalidated``."""
    return {stage: rec["status"] for stage, rec in _latest(_stage_records(attempt_dir)).items()}


# ---------------------------------------------------------------------------
# Live operations
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class LiveOps:
    """Every live operation a stage may perform (fakes in tests).

    `connect()` returns a connected FireTuner connection. `deploy(archive,
    save_name, sha256)` installs a repo-relative archive; `reload(conn,
    save_name) -> bool` is the production confirmed reload; `load_game_save`
    is the tiered in-session loader (base replays); `restart_and_load(name)`
    is the crash-recovery path (kill, relaunch, frontend menu load);
    `reconnect(conn)` re-establishes a connection after any load.
    `export_save`/`publish_archive` are the native export and WSL copy;
    `capture_position`/`verify_position` are `benchmark_position`'s and own
    their connection. `clocks` is ``(wall_clock, monotonic)`` for the journal.
    """

    connect: Callable[[], Awaitable[Any]]
    deploy: Callable[[str, str, str], Any]
    reload: Callable[[Any, str], Awaitable[bool]]
    dismiss_popups: Callable[[Any], Awaitable[str]]
    export_save: Callable[..., Any]
    publish_archive: Callable[..., Any]
    save_game: Callable[[Any, str], Awaitable[tuple[bool, str]]]
    load_game_save: Callable[[Any, str], Awaitable[str]]
    run_validation: Callable[[Path, Path], Awaitable[dict[str, Any]]]
    build_reports: Callable[[Path], Any]
    capture_state_v2: Callable[..., Awaitable[dict[str, Any]]]
    capture_position: Callable[..., Awaitable[dict[str, Any]]]
    verify_position: Callable[..., Awaitable[dict[str, Any]]]
    restart_and_load: Callable[[str], Awaitable[str]]
    reconnect: Callable[[Any], Awaitable[Any]]
    clocks: tuple[Callable[[], float], Callable[[], float]] = (time.time, time.monotonic)


def production_ops() -> LiveOps:
    from civ_mcp import game_lifecycle
    from civ_mcp.arena import benchmark_position, benchmark_validation
    from civ_mcp.arena.benchmark_deploy import (
        deploy_via_windows,
        export_via_windows,
        publish_archive_copy,
    )
    from civ_mcp.arena.benchmark_runner import reload_position
    from civ_mcp.arena.benchmark_state_v2 import capture_state_v2
    from civ_mcp.arena.popups import dismiss_blocking_popups
    from civ_mcp.connection import GameConnection

    async def connect() -> Any:
        connection = GameConnection()
        await connection.connect()
        return connection

    async def reload(connection: Any, save_name: str) -> bool:
        return await reload_position(connection, SimpleNamespace(game_save_name=save_name))

    async def run_validation(suite_path: Path, run_dir: Path) -> dict[str, Any]:
        return await benchmark_validation.run_validation(suite_path, run_dir)

    async def reconnect(connection: Any) -> None:
        await connection.reconnect()

    from civ_mcp.game_launcher import restart_and_load

    return LiveOps(
        restart_and_load=restart_and_load, reconnect=reconnect,
        connect=connect, deploy=deploy_via_windows, reload=reload,
        dismiss_popups=dismiss_blocking_popups, export_save=export_via_windows,
        publish_archive=publish_archive_copy, save_game=game_lifecycle.save_game,
        load_game_save=game_lifecycle.load_game_save, run_validation=run_validation,
        build_reports=benchmark_validation.build_reports, capture_state_v2=capture_state_v2,
        capture_position=benchmark_position.capture_position,
        verify_position=benchmark_position.verify_position,
    )


# ---------------------------------------------------------------------------
# Stage context
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _Context:
    recipe: dict[str, Any]
    recipe_path: Path
    attempt_dir: Path
    root: Path
    ops: LiveOps
    stage: str
    sequence: int
    records: list[dict[str, Any]]
    journal: AuthoringJournal | None = None
    evidence: dict[str, Any] = dataclasses.field(default_factory=dict)
    files: list[dict[str, str]] = dataclasses.field(default_factory=list)

    @property
    def scenario_id(self) -> str:
        return self.recipe["scenario_id"]

    def rel(self, path: Path) -> str:
        return _rel(path, self.root)

    def check(self, label: str) -> None:
        """Advance the clock; raises ``authoring clock expired`` before a live command."""
        assert self.journal is not None
        self.journal.checkpoint(scenario_id=self.scenario_id, label=f"{self.stage}:{label}")

    def latest(self, stage: str) -> dict[str, Any]:
        record = _latest(self.records).get(stage)
        if record is None or record["status"] != "passed":
            raise StageFailure(f"{stage} has no passing record")
        return record

    def note_file(self, path: Path) -> None:
        self.files.append({"path": self.rel(path), "sha256": _sha256_file(path)})

    def write_evidence(self, kind: str, name: str, doc: Any, *,
                       filename: str | None = None) -> Path:
        path = self.attempt_dir / kind / (filename or f"{self.sequence:03d}-{name}.json")
        _write_new(path, _json_bytes(doc))
        self.note_file(path)
        return path

    def recipe_ref(self) -> dict[str, Any]:
        return {"path": self.rel(self.recipe_path), "sha256": _sha256_file(self.recipe_path),
                "recipe_id": self.recipe["recipe_id"], "version": self.recipe["version"]}

    def build_record(self, stage: str, status: str, *, sequence: int | None = None,
                     error: str | None = None, evidence: dict[str, Any] | None = None,
                     files: list[dict[str, str]] | None = None,
                     extra: dict[str, Any] | None = None) -> tuple[dict[str, Any], bytes, Path]:
        sequence = self.sequence if sequence is None else sequence
        record = {
            "schema_version": SCHEMA_VERSION, "sequence": sequence, "stage": stage,
            "status": status, "scenario_id": self.scenario_id, "recipe": self.recipe_ref(),
            "error": error, "evidence": evidence if evidence is not None else {},
            "files": files if files is not None else [], **(extra or {}),
        }
        path = self.attempt_dir / "stages" / f"{sequence:03d}-{stage}.json"
        return record, _json_bytes(record), path

    def persist(self, record: dict[str, Any], data: bytes, path: Path) -> dict[str, Any]:
        _write_new(path, data)
        self.records.append(record)
        return record

    def write_record(self, stage: str, status: str, **kwargs: Any) -> dict[str, Any]:
        return self.persist(*self.build_record(stage, status, **kwargs))


def _next_sequence(records: list[dict[str, Any]]) -> int:
    return 1 + max((r["sequence"] for r in records), default=0)


def _invalidate_downstream(ctx: _Context) -> None:
    latest = _latest(ctx.records)
    downstream = [latest[s] for s in STAGES[STAGES.index(ctx.stage) + 1:]
                  if s in latest and latest[s]["status"] != "invalidated"]
    # The repeating stage's own record follows its invalidation records.
    own_sequence = ctx.sequence + len(downstream)
    for record in downstream:
        ctx.write_record(record["stage"], "invalidated", extra={
            "invalidated_by": {"stage": ctx.stage, "sequence": own_sequence},
            "invalidates_sequence": record["sequence"]})
        ctx.sequence += 1


# ---------------------------------------------------------------------------
# Base replay, bindings and coverage
# ---------------------------------------------------------------------------

def _lines_have_error(lines: list[str]) -> bool:
    return any(str(line).strip().upper().startswith(("ERR", "ERROR")) for line in lines)


def _readback_ok(lines: list[str]) -> bool:
    return (any(str(line).strip().startswith("OK") for line in lines)
            and not _lines_have_error(lines))


def _resolution_coverage(recipe: dict[str, Any]) -> dict[str, Any]:
    area: set[tuple[int, int]] = set()
    for binding in recipe["bindings"]:
        selector = binding["selector"]
        area.update(tuple(p) for p in selector.get("area", []))
        if _is_int(selector.get("x")) and _is_int(selector.get("y")):
            area.add((selector["x"], selector["y"]))
    return {"include_owned_tiles": True, "area": [list(p) for p in sorted(area)],
            "tracked_targets": []}


def _row_matches(row: dict[str, Any], selector: dict[str, Any]) -> bool:
    for key, value in selector.items():
        if key == "area":
            if [row.get("x"), row.get("y")] not in value:
                return False
        elif key not in row or row[key] != value:
            return False
    return True


def resolve_bindings(bindings: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    """Resolve each selector to exactly one row; fail with candidate evidence."""
    resolved: dict[str, Any] = {}
    for binding in bindings:
        rows = state.get(_BINDING_FAMILIES[binding["resolves"]], [])
        matches = [row for row in rows if _row_matches(row, binding["selector"])]
        if len(matches) != 1:
            near = matches or [row for row in rows if any(
                k != "area" and row.get(k) == v for k, v in binding["selector"].items())]
            raise StageFailure(
                f"binding {binding['name']!r} resolved to {len(matches)} rows "
                f"(exactly one required); candidates: {json.dumps(near[:20], sort_keys=True)}")
        row = matches[0]
        value = {k: row[k] for k in ("owner", "id", "unit_index", "x", "y") if row.get(k) is not None}
        if "x" in value and "y" in value:
            value["xy"] = [value["x"], value["y"]]
        if binding["resolves"] != "tile" and "owner" in value and "id" in value:
            value["pair"] = [value["owner"], value["id"]]
        resolved[binding["name"]] = value
    return resolved


def _coverage(recipe: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    rule = recipe["coverage_rule"]
    radius = rule["area_radius"]
    area: set[tuple[int, int]] = set()
    for value in bindings.values():
        if "x" in value and "y" in value:
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    x, y = value["x"] + dx, value["y"] + dy
                    if x >= 0 and y >= 0:
                        area.add((x, y))
    tracked = []
    for name in rule["tracked_target_bindings"]:
        pair = bindings[name].get("pair")
        if pair is None:
            raise StageFailure(f"tracked target binding {name!r} has no owner/id")
        tracked.append(list(pair))
    return {"include_owned_tiles": rule["include_owned_tiles"],
            "area": [list(p) for p in sorted(area)], "tracked_targets": sorted(tracked)}


async def _capture(ctx: _Context, connection: Any, coverage: dict[str, Any]) -> dict[str, Any]:
    return await ctx.ops.capture_state_v2(connection, ctx.recipe["player_id"], coverage,
                                          io_timing={})


async def _load_and_verify_base(ctx: _Context, connection: Any, out: dict[str, Any]) -> None:
    """Load the base, confirm the load, and prove the loaded game is the base.

    A failed load string, a failed reconnect, a base file whose digest differs,
    or a loaded identity (turn/seed/civ/player) that differs from the recipe's
    base identity fails the stage before any setup mutation."""
    base = ctx.recipe["base_save_identity"]
    ctx.check("load-base")
    result = await ctx.ops.load_game_save(connection, base["name"])
    out["base_load"] = result
    if _load_failed(result):
        raise StageFailure(f"base load of {base['name']!r} failed: {result}")
    await ctx.ops.reconnect(connection)
    out["base_reconnect"] = {"reconnected": True}
    destination = f"{WSL_WINDOWS_REPO}/{BASE_EXPORT_DIR}/{base['sha256']}.Civ6Save"
    export = await _maybe_await(ctx.ops.export_save(base["name"], destination,
                                                    expected_sha256=base["sha256"]))
    out["base_export"] = export
    if not isinstance(export, dict) or export.get("sha256") != base["sha256"]:
        raise StageFailure(f"base save {base['name']!r} does not hash to {base['sha256']}")
    out["base_popups"] = await ctx.ops.dismiss_popups(connection)
    identity_state = await _capture(ctx, connection, _IDENTITY_COVERAGE)
    observed = _identity(identity_state)
    expected = {"turn": base["turn"], "seed": base["seed"], "civ_type": base["civ_type"],
                "player_id": ctx.recipe["player_id"]}
    out["base_identity"] = {"observed": observed, "expected": expected,
                            "matches": observed == expected}
    if observed != expected:
        raise StageFailure(f"loaded game identity {observed} is not the base identity {expected}")


_IDENTITY_COVERAGE = {"include_owned_tiles": False, "area": [], "tracked_targets": []}
_LOAD_FAILURE_MARKERS = ("FAILED", "ABORTED", "WARNING:", "Error:", "not found")


def _load_failed(result: Any) -> bool:
    text = str(result or "").strip()
    return (not text or text.lower().startswith("error")
            or any(marker in text for marker in _LOAD_FAILURE_MARKERS))


def _identity(state: dict[str, Any]) -> dict[str, Any]:
    return {k: state.get(k) for k in ("turn", "seed", "civ_type", "player_id")}


async def _replay(ctx: _Context, connection: Any, out: dict[str, Any]) -> None:
    """Reload the hashed base and reapply the recipe; fills `out` as it goes."""
    recipe = ctx.recipe
    out["base_save_identity"] = dict(recipe["base_save_identity"])
    await _load_and_verify_base(ctx, connection, out)
    operations = out.setdefault("operations", [])
    for index, op in enumerate(recipe["setup"]["operations"]):
        ctx.check(f"setup-{index}")
        result = list(await connection.execute_write(op["lua"]))
        readback = list(await connection.execute_read(op["readback"]))
        ok = not _lines_have_error(result) and _readback_ok(readback)
        operations.append({"index": index, "lua": op["lua"], "result": result,
                           "readback_lua": op["readback"], "readback": readback,
                           "readback_ok": ok})
        if not ok:
            raise StageFailure(f"setup operation {index} readback failed: {readback}")
    resolution = _resolution_coverage(recipe)
    out["resolution_coverage"] = resolution
    candidates = await _capture(ctx, connection, resolution)
    out["resolution_state_sha256"] = digest_state_v2(candidates)
    bindings = resolve_bindings(recipe["bindings"], candidates)
    out["bindings"] = bindings
    coverage = _coverage(recipe, bindings)
    out["coverage"] = coverage
    state = await _capture(ctx, connection, coverage)
    out["state"] = state
    out["state_sha256"] = digest_state_v2(state)
    out["assertions"] = _assertions(recipe, bindings, state)
    out["assertions_passed"] = all(a["passed"] for a in out["assertions"])


def _assertions(recipe: dict[str, Any], bindings: dict[str, Any],
                state: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for item in recipe["setup"]["assertions"]:
        predicate = substitute_bindings(item["predicate"], bindings)
        actual = evaluate_predicate(predicate, initial=state, final=state)
        results.append({"id": item["id"], "predicate": predicate, "expected": item["value"],
                        "actual": actual, "passed": actual == item["value"]})
    return results


async def _connect(ctx: _Context) -> Any:
    ctx.check("connect")
    return await ctx.ops.connect()


async def _disconnect(connection: Any) -> None:
    disconnect = getattr(connection, "disconnect", None)
    if disconnect is not None:
        await _maybe_await(disconnect())


async def _dispatch(ctx: _Context, connection: Any, tool: str, arguments: dict[str, Any]) -> str:
    from civ_mcp.game_state import GameState
    toolset = load_toolset(ctx.root / ctx.recipe["toolset_path"])
    return await registry.dispatch(GameState(connection), tool, arguments,
                                   allowed=tuple(toolset["game_tools"]))


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def query_uses_bindings(query: dict[str, Any]) -> bool:
    """True when a survey query's arguments reference ``${name.field}`` bindings."""
    return "${" in json.dumps(query["arguments"])


async def _observe(ctx: _Context, connection: Any, filename: Callable[[int], str],
                   bindings: dict[str, Any] | None = None
                   ) -> list[tuple[str, dict[str, Any]]]:
    """Run `survey.queries` through registry dispatch; keep full and capped text.

    A query whose arguments reference bindings (e.g. a radius-1 map centred on
    a spawned threat, so the threat line lands inside the result cap) runs only
    once bindings are resolved (archive); on the unmodified base (survey) the
    entity does not exist yet, so the query is not dispatched there."""
    cap = ctx.recipe["result_char_cap"]
    observations = []
    for index, query in enumerate(ctx.recipe["survey"]["queries"]):
        if bindings is None and query_uses_bindings(query):
            continue
        ctx.check(f"query-{index}")
        arguments = substitute_bindings(dict(query["arguments"]), bindings or {})
        full = await _dispatch(ctx, connection, query["tool"], arguments)
        capped = full[:cap]
        doc = {"index": index, "tool": query["tool"], "arguments": query["arguments"],
               "resolved_arguments": arguments, "result_char_cap": cap, "result_full": full, "result_capped": capped,
               "full_sha256": _sha256_bytes(full.encode()),
               "capped_sha256": _sha256_bytes(capped.encode())}
        path = ctx.write_evidence("observations", "", doc, filename=filename(index))
        observations.append((ctx.rel(path), doc))
    return observations


def _required_facts(recipe: dict[str, Any], observations: list[tuple[str, dict[str, Any]]]
                    ) -> list[dict[str, Any]]:
    """Each fact is discoverable only if it matches within the CAPPED text."""
    facts = []
    for fact in recipe["survey"]["required_facts"]:
        pattern = re.compile(fact["pattern"])
        sources = [(p, d) for p, d in observations if d["tool"] == fact["source"]]
        in_cap = [p for p, d in sources if pattern.search(d["result_capped"])]
        in_full = [p for p, d in sources if pattern.search(d["result_full"])]
        facts.append({"id": fact["id"], "pattern": fact["pattern"], "source": fact["source"],
                      "discoverable": bool(in_cap),
                      "observations": in_cap, "beyond_cap_only": bool(in_full) and not in_cap})
    return facts


async def _stage_survey(ctx: _Context) -> None:
    """Candidate discovery on the unmodified base.

    Required facts are recorded here for the base but gate only in `archive`,
    where the queries run on the replayed scenario start (setup-introduced
    facts cannot exist in the base)."""
    recipe = ctx.recipe
    connection = await _connect(ctx)
    try:
        await _load_and_verify_base(ctx, connection, ctx.evidence)
        private = await _capture(ctx, connection, _resolution_coverage(recipe))
        path = ctx.write_evidence("samples", "survey-state", private)
        ctx.evidence["private_state"] = {"path": ctx.rel(path),
                                         "sha256": digest_state_v2(private)}
        observations = await _observe(
            ctx, connection, lambda i: f"{ctx.sequence:03d}-survey-{i:02d}.json")
        ctx.evidence["observations"] = [p for p, _ in observations]
    finally:
        await _disconnect(connection)
    ctx.evidence["base_required_facts"] = _required_facts(recipe, observations)


async def _replay_stage(ctx: _Context, label: str) -> dict[str, Any]:
    connection = await _connect(ctx)
    replay: dict[str, Any] = {}
    try:
        await _replay(ctx, connection, replay)
    finally:
        await _disconnect(connection)
        ctx.write_evidence("mutations", label, replay)
        ctx.evidence["replay"] = {k: replay[k] for k in
                                  ("base_load", "base_export", "state_sha256", "assertions_passed")
                                  if k in replay}
    return replay


async def _stage_apply(ctx: _Context) -> None:
    replay = await _replay_stage(ctx, "apply")
    ctx.evidence["bindings"] = replay["bindings"]
    ctx.evidence["coverage"] = replay["coverage"]
    if not replay["assertions_passed"]:
        failed = [a["id"] for a in replay["assertions"] if not a["passed"]]
        raise StageFailure(f"setup assertions failed: {failed}")


def _measure_target(before: dict[str, Any], after: dict[str, Any], ref: list[int]
                    ) -> dict[str, Any]:
    """Measured hp delta of one tracked target across a probe (a destroyed
    target counts its whole remaining hp as damage)."""
    def row(state: dict[str, Any]) -> dict[str, Any] | None:
        return next((t for t in state.get("targets", [])
                     if [t.get("owner"), t.get("id")] == list(ref)), None)
    start, end = row(before), row(after)
    measured: dict[str, Any] = {"target": list(ref), "hp_before": None, "hp_after": None,
                                "status_after": None, "delta": None}
    if start is None or end is None or start.get("hp") is None:
        return measured
    measured.update(hp_before=start["hp"], hp_after=end.get("hp"), status_after=end["status"])
    if end["status"] == "destroyed":
        measured["delta"] = start["hp"]
    elif end["status"] == "alive_visible" and end.get("hp") is not None:
        measured["delta"] = start["hp"] - end["hp"]
    return measured


def _freeze_measured_parameters(recipe: dict[str, Any],
                                probe_outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Freeze each provisional parameter only if this attempt's probe
    measurements satisfy its rule; otherwise the archive stage fails."""
    by_id = {o["id"]: o for o in probe_outcomes}
    frozen = []
    for param in recipe.get("measured_parameters", []):
        value = _rubric_path_value({"objectives": recipe["objectives"], "harms": recipe["harms"]},
                                   param["used_by"][0], param["name"])
        measurements = {}
        for probe_id in param["source_probes"]:
            outcome = by_id.get(probe_id)
            measurement = (outcome or {}).get("measurement")
            if not outcome or not outcome.get("passed") or measurement is None \
                    or measurement.get("delta") is None:
                raise StageFailure(f"measured parameter {param['name']!r} has no passing "
                                   f"probe measurement from {probe_id!r} in this attempt")
            measurements[probe_id] = measurement
        rule = param["rule"]
        if "strictly_between" in rule:
            low, high = (measurements[p]["delta"] for p in rule["strictly_between"])
            if not low < value < high:
                raise StageFailure(f"measured parameter {param['name']!r} = {value} is not "
                                   f"strictly between measured deltas {low} and {high}")
        for probe_id in rule.get("survives", []):
            if measurements[probe_id]["status_after"] != "alive_visible":
                raise StageFailure(f"measured parameter {param['name']!r}: target did not "
                                   f"survive probe {probe_id!r}")
        frozen.append({"name": param["name"], "value": value,
                       "used_by": copy.deepcopy(param["used_by"]), "rule": copy.deepcopy(rule),
                       "measurements": measurements,
                       "evidence": [by_id[p]["path"] for p in param["source_probes"]]})
    return frozen


def _apply_frozen(rubric: dict[str, Any], frozen: list[dict[str, Any]]) -> None:
    for param in frozen:
        for path in param["used_by"]:
            node: Any = rubric
            for step in path[:-1]:
                node = node[step]
            node[path[-1]] = param["value"]


async def _stage_probe(ctx: _Context) -> None:
    expected = ctx.latest("apply")["evidence"]["bindings"]
    connection = await _connect(ctx)
    outcomes = []
    try:
        for probe in ctx.recipe["probes"]:
            record: dict[str, Any] = {"probe": probe}
            try:
                replay: dict[str, Any] = {}
                record["restore"] = replay
                await _replay(ctx, connection, replay)
                if replay["bindings"] != expected:
                    raise StageFailure(f"bindings drifted from apply: {replay['bindings']}")
                if not replay["assertions_passed"]:
                    raise StageFailure("setup assertions failed on restore")
                arguments = substitute_bindings(probe["arguments_from_bindings"], expected)
                record["arguments"] = arguments
                ctx.check(f"probe-{probe['id']}")
                result = await _dispatch(ctx, connection, probe["tool"], arguments)
                outcome = {"success": "ok", "domain_rejection": "rejected"}.get(
                    classify_result(result), "not_dispatched")
                passed = outcome == probe["expect"]
                if "expect_pattern" in probe:
                    matched = re.search(probe["expect_pattern"], str(result)) is not None
                    record["pattern_matched"] = matched
                    passed = passed and matched
                if "measure_target" in probe:
                    ref = substitute_bindings(probe["measure_target"], expected)
                    ctx.check(f"probe-{probe['id']}-measure")
                    after = await _capture(ctx, connection, replay["coverage"])
                    record["measurement"] = _measure_target(replay["state"], after, ref)
                record.update(result=result, outcome=outcome, passed=passed)
            except StageFailure as exc:
                record.update(error=str(exc), passed=False)
            finally:
                if "state" in record.get("restore", {}):
                    record["restore"] = {**record["restore"], "state": None}
                path = ctx.write_evidence("probes", probe["id"], record)
            outcomes.append({"id": probe["id"], "expect": probe["expect"],
                             "outcome": record.get("outcome"), "passed": record["passed"],
                             "path": ctx.rel(path),
                             **({"measurement": record["measurement"]}
                                if "measurement" in record else {})})
    finally:
        await _disconnect(connection)
    ctx.evidence["probes"] = outcomes
    failed = [o["id"] for o in outcomes if not o["passed"]]
    if failed:
        raise StageFailure(f"legality probes failed: {failed}")


def _archive_names(recipe: dict[str, Any], version: int) -> dict[str, str]:
    outputs = {k: v.format(version=version) for k, v in recipe["outputs"].items()}
    return {"name": recipe["archive"]["name"].format(version=version),
            "path": recipe["archive"]["path"].format(version=version),
            "position_id": PurePosixPath(outputs["position"]).stem, **outputs}


async def _stage_archive(ctx: _Context) -> None:
    recipe = ctx.recipe
    expected = ctx.latest("apply")["evidence"]["bindings"]
    published = [r for r in ctx.records if r["stage"] == "archive"
                 and r["evidence"].get("published")]
    version = recipe["version"] + len(published)
    names = _archive_names(recipe, version)
    ctx.evidence.update(version=version, archive_name=names["name"], archive_path=names["path"],
                        position_id=names["position_id"], published=False)
    ctx.evidence["bindings_changed"] = bool(published) and \
        published[-1]["evidence"].get("bindings") != expected
    local = ctx.root / names["path"]
    if local.exists():
        raise StageFailure(f"archive {names['path']} already exists; refusing to reuse it")
    # Provisional rubric parameters freeze only from this attempt's probe readback.
    ctx.evidence["measured_parameters"] = _freeze_measured_parameters(
        recipe, ctx.latest("probe")["evidence"]["probes"])
    connection = await _connect(ctx)
    replay: dict[str, Any] = {}
    try:
        try:
            await _replay(ctx, connection, replay)
        finally:
            ctx.write_evidence("mutations", "archive", replay)
        if replay["bindings"] != expected:
            raise StageFailure(f"bindings drifted from apply: {replay['bindings']}")
        if not replay["assertions_passed"]:
            raise StageFailure("setup assertions failed before archive")
        ctx.evidence.update(bindings=replay["bindings"], coverage=replay["coverage"],
                            state_sha256=replay["state_sha256"])
        # Observe the archived start exactly as an actor would, before saving.
        observations = await _observe(
            ctx, connection, lambda i: f"archived-{ctx.sequence:03d}-{i:02d}.json",
            bindings=replay["bindings"])
        ctx.evidence["archived_observations"] = [p for p, _ in observations]
        facts = _required_facts(recipe, observations)
        ctx.evidence["required_facts"] = facts
        missing = [f["id"] for f in facts if not f["discoverable"]]
        if missing:
            raise StageFailure(f"required facts not discoverable at the archived start within "
                               f"the {recipe['result_char_cap']}-character result cap: {missing}")
        ctx.check("save")
        ok, message = await ctx.ops.save_game(connection, names["name"])
        ctx.evidence["save_ack"] = {"ok": ok, "message": message}
        if not ok:
            raise StageFailure(f"save_game was not acknowledged: {message}")
    finally:
        await _disconnect(connection)
    mounted = f"{WSL_WINDOWS_REPO}/{names['path']}"
    export = await _maybe_await(ctx.ops.export_save(names["name"], mounted))
    ctx.evidence["export"] = export
    export_sha = export.get("sha256") if isinstance(export, dict) else None
    if not export_sha:
        raise StageFailure("native export returned no sha256")
    publish = await _maybe_await(ctx.ops.publish_archive(mounted, str(local),
                                                         expected_sha256=export_sha))
    ctx.evidence["published"] = True
    ctx.evidence.update(export_sha256=export_sha, publish_sha256=publish.get("sha256"))
    local_sha = _sha256_file(local)
    ctx.evidence["local_sha256"] = local_sha
    if not (export_sha == publish.get("sha256") == local_sha):
        raise StageFailure(f"archive digests disagree: export {export_sha}, publish "
                           f"{publish.get('sha256')}, local {local_sha}")
    ctx.note_file(local)


def _capture_closure(ctx: _Context, coverage: dict[str, Any],
                     telemetry: CaptureTelemetry) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def capture(connection: Any, player_id: int, _tiles: Any) -> dict[str, Any]:
        telemetry.current_io = {}
        started = time.monotonic()
        state = await ctx.ops.capture_state_v2(connection, player_id, coverage,
                                               io_timing=telemetry.current_io)
        telemetry.records.append({"phase": "authoring", "complete": True,
                                  "duration_s": time.monotonic() - started,
                                  "io": dict(telemetry.current_io)})
        return state
    return capture


def _ref(path: Path, base_dir: Path) -> dict[str, str]:
    return {"path": os.path.relpath(path, base_dir), "sha256": _sha256_file(path)}


def _public_observation(ctx: _Context, archive: dict[str, Any]) -> list[dict[str, Any]]:
    """The capped archived-start observations: what an actor sees at the start."""
    docs = []
    for rel in archive["archived_observations"]:
        doc = json.loads((ctx.root / rel).read_text(encoding="utf-8"))
        docs.append({k: doc[k] for k in ("tool", "arguments", "result_char_cap", "result_capped")})
    return docs


def _public_task_tiles(recipe: dict[str, Any], bindings: dict[str, Any],
                       observation: list[dict[str, Any]]) -> list[list[int]]:
    """Tile bindings whose ``x,y`` appears in the capped archived observations."""
    text = "\n".join(doc["result_capped"] for doc in observation)
    tiles = []
    for name, value in sorted(bindings.items()):
        if _binding_kind(recipe, name) != "tile":
            continue
        if re.search(rf"(?<!\d){value['x']}\s*,\s*{value['y']}(?!\d)", text):
            tiles.append(list(value["xy"]))
    return tiles


def _materialise(ctx: _Context, names: dict[str, str], archive: dict[str, Any],
                 state: dict[str, Any], state_sha: str, authoring_path: Path) -> dict[str, Any]:
    recipe, root = ctx.recipe, ctx.root
    bindings = archive["bindings"]
    toolset_path = root / recipe["toolset_path"]
    toolset = load_toolset(toolset_path)
    position_path = root / names["position"]
    scripts_dir = root / names["scripts_dir"]
    validation_dir = root / names["validation_dir"]
    contract_identity = implementation_fingerprint(_REPO_ROOT)
    rubric = substitute_bindings({"objectives": recipe["objectives"], "harms": recipe["harms"]},
                                 bindings)
    _apply_frozen(rubric, archive.get("measured_parameters", []))
    validate_rubric(rubric, state)

    observation = _public_observation(ctx, archive)
    observation_path = validation_dir / "public-observation.json"
    _write_immutable(observation_path, _json_bytes(observation))
    position_dir = position_path.parent
    position = {
        "schema_version": SCHEMA_VERSION, "position_id": names["position_id"],
        "version": archive["version"], "family": recipe["family"], "split": "development",
        "archive": {"path": os.path.relpath(root / names["path"], position_dir),
                    "sha256": archive["export_sha256"]},
        "game_save_name": names["name"], "player_id": recipe["player_id"],
        "expected_state": state, "expected_state_sha256": state_sha,
        "coverage": archive["coverage"],
        "toolset": {"path": os.path.relpath(toolset_path, position_dir),
                    "identity": toolset["identity"]},
        "contract_identity": contract_identity, "rubric": rubric,
        "provenance": _ref(authoring_path, position_dir),
        "public_observation": _ref(observation_path, position_dir),
        "public_task_tiles": _public_task_tiles(recipe, bindings, observation),
        "pilot_informed": False,
    }
    validate_v2_document(position, kind="position")
    _write_immutable(position_path, _json_bytes(position))

    script_paths = {}
    for template in recipe["scripts"]:
        doc = {"schema_version": SCHEMA_VERSION,
               **substitute_bindings(template, bindings)}
        validate_v2_document(doc, kind="script")
        path = scripts_dir / f"{template['script_id']}.json"
        _write_immutable(path, _json_bytes(doc))
        script_paths[template["script_id"]] = path

    case_paths = []
    for template in recipe["cases"]:
        path = validation_dir / "cases" / f"{template['case_id']}.json"
        doc = {"schema_version": SCHEMA_VERSION, "case_id": template["case_id"],
               "position": _ref(position_path, path.parent),
               "script": _ref(script_paths[template["script_id"]], path.parent),
               "tags": list(template["tags"]),
               "expected": substitute_bindings(template["expected"], bindings)}
        if "declared_rejections" in template:
            doc["declared_rejections"] = copy.deepcopy(template["declared_rejections"])
        validate_v2_document(doc, kind="case")
        _write_immutable(path, _json_bytes(doc))
        case_paths.append(path)

    suite_id = f"{names['position_id']}-validation"
    suite_path = validation_dir / "suite.json"
    suite = {"schema_version": SCHEMA_VERSION, "suite_id": suite_id,
             "cases": [_ref(p, validation_dir) for p in case_paths],
             "position": _ref(position_path, validation_dir),
             "toolset_identity": toolset["identity"], "contract_identity": contract_identity,
             "max_steps": recipe["max_steps"], "episode_wall_s": EPISODE_WALL_S,
             "result_char_cap": recipe["result_char_cap"], "actor_kind": "scripted",
             "counting": False}
    validate_v2_document(suite, kind="validation_suite")
    _write_immutable(suite_path, _json_bytes(suite))

    for path in (observation_path, position_path, *script_paths.values(), *case_paths, suite_path):
        ctx.note_file(path)
    return {"position_path": ctx.rel(position_path), "position_sha256": _sha256_file(position_path),
            "suite_path": ctx.rel(suite_path), "suite_id": suite_id,
            "scripts": [ctx.rel(p) for p in script_paths.values()],
            "cases": [ctx.rel(p) for p in case_paths],
            "public_observation": ctx.rel(observation_path),
            "toolset_identity": toolset["identity"], "contract_identity": contract_identity}


def _binding_kind(recipe: dict[str, Any], name: str) -> str:
    return next(b["resolves"] for b in recipe["bindings"] if b["name"] == name)


async def _stage_capture(ctx: _Context) -> None:
    recipe = ctx.recipe
    archive = ctx.latest("archive")["evidence"]
    names = _archive_names(recipe, archive["version"])
    coverage = archive["coverage"]
    draft = {"archive": archive["archive_path"], "archive_sha256": archive["export_sha256"],
             "game_save_name": archive["archive_name"], "player_id": recipe["player_id"],
             "relevant_tiles": [list(p) for p in coverage["area"]]}
    telemetry = CaptureTelemetry()
    ctx.check("capture")
    result = await ctx.ops.capture_position(draft, capture_state=_capture_closure(
        ctx, coverage, telemetry), digest=digest_state_v2)
    state = result["captured_state"]
    state_sha = result["captured_state_sha256"]
    ctx.evidence.update(captured_state_sha256=state_sha, coverage=coverage,
                        row_counts=state.get("row_counts"), reload=result.get("reload"),
                        popup_hygiene=result.get("popup_hygiene"),
                        telemetry=telemetry.summary(episode_wall_s=float(EPISODE_WALL_S)))
    if digest_state_v2(state) != state_sha:
        raise StageFailure("captured state does not digest to the reported sha256")
    assertions = _assertions(recipe, archive["bindings"], state)
    ctx.evidence["assertions"] = assertions
    if not all(a["passed"] for a in assertions):
        raise StageFailure("setup assertions do not hold on the captured archive")

    mutation = next(f for f in ctx.latest("archive")["files"] if "/mutations/" in f["path"])
    setup = json.loads((ctx.root / mutation["path"]).read_text(encoding="utf-8"))
    probe_record = ctx.latest("probe")
    authoring_input = {
        "schema_version": SCHEMA_VERSION, "position_id": names["position_id"],
        "version": archive["version"], "scenario_id": recipe["scenario_id"],
        "family": recipe["family"], "recipe": ctx.recipe_ref(),
        "base_save_identity": dict(recipe["base_save_identity"]),
        "player_id": recipe["player_id"],
        "setup": {"operations": setup["operations"], "assertions": setup["assertions"],
                  "mutation_record": mutation},
        "bindings": archive["bindings"],
        "probes": {"results": probe_record["evidence"]["probes"],
                   "files": [f for f in probe_record["files"] if "/probes/" in f["path"]]},
        "measured_parameters": archive.get("measured_parameters", []),
        "archive": archive["archive_path"], "archive_sha256": archive["export_sha256"],
        "archive_digests": {"export": archive["export_sha256"],
                            "publish": archive["publish_sha256"],
                            "local": archive["local_sha256"]},
        "game_save_name": archive["archive_name"],
        "coverage": coverage, "relevant_tiles": draft["relevant_tiles"],
        "capture": {"digest": state_sha, "row_counts": state.get("row_counts"),
                    "reload": result.get("reload"), "popup_hygiene": result.get("popup_hygiene")},
    }
    authoring_path = ctx.root / names["provenance_authoring"]
    _write_immutable(authoring_path, _json_bytes(authoring_input))
    ctx.note_file(authoring_path)
    ctx.evidence["authoring_input"] = {"path": ctx.rel(authoring_path),
                                       "sha256": _sha256_file(authoring_path)}
    ctx.evidence.update(_materialise(ctx, names, archive, state, state_sha, authoring_path))
    ctx.evidence.update(position_id=names["position_id"], version=archive["version"],
                        archive_path=archive["archive_path"],
                        archive_sha256=archive["export_sha256"],
                        game_save_name=archive["archive_name"])


def _position_stub(ctx: _Context, capture: dict[str, Any]) -> SimpleNamespace:
    path = ctx.root / capture["position_path"]
    if _sha256_file(path) != capture["position_sha256"]:
        raise StageFailure(f"position {capture['position_path']} changed since capture")
    position = load_v2_document(path, kind="position")
    return SimpleNamespace(
        position_id=position["position_id"], archive=capture["archive_path"],
        archive_sha256=capture["archive_sha256"], game_save_name=position["game_save_name"],
        player_id=position["player_id"],
        relevant_tiles=[tuple(p) for p in position["coverage"]["area"]],
        expected_state_sha256=position["expected_state_sha256"], coverage=position["coverage"],
        expected_identity=_identity(position["expected_state"]))


async def _stage_verify(ctx: _Context) -> None:
    capture = ctx.latest("capture")["evidence"]
    stub = _position_stub(ctx, capture)
    telemetry = CaptureTelemetry()
    ctx.check("verify")
    result = await ctx.ops.verify_position(
        stub, REQUIRED_CYCLES, capture_state=_capture_closure(ctx, stub.coverage, telemetry),
        digest=digest_state_v2)
    ctx.evidence.update(cycles=REQUIRED_CYCLES, result=result,
                        expected_state_sha256=stub.expected_state_sha256)
    digests = result.get("digests", [])
    if not (result.get("ok") and result.get("cycles_completed") == REQUIRED_CYCLES
            and len(digests) == REQUIRED_CYCLES
            and all(d == stub.expected_state_sha256 for d in digests)):
        raise StageFailure(f"twelve-cycle verification failed: {result}")


async def _stage_menu_check(ctx: _Context) -> None:
    """Crash-recovery load of the exact archive, positively confirmed.

    `restart_and_load` kills and relaunches the game and loads through the
    frontend menu; the stage then needs a non-failure loader result, a fresh
    re-established connection, and a post-load state whose identity and v2
    digest equal the position's expected state."""
    capture = ctx.latest("capture")["evidence"]
    stub = _position_stub(ctx, capture)
    ctx.check("deploy")
    ctx.evidence["deploy"] = _plain(await _maybe_await(ctx.ops.deploy(
        stub.archive, stub.game_save_name, stub.archive_sha256)))
    ctx.check("restart-and-load")
    result = await ctx.ops.restart_and_load(stub.game_save_name)
    ctx.evidence["loader"] = "restart_and_load"
    ctx.evidence["loader_result"] = result
    if _load_failed(result):
        raise StageFailure(f"crash-recovery load failed: {result}")
    connection = await _connect(ctx)
    try:
        await ctx.ops.reconnect(connection)
        ctx.evidence["reconnect"] = {"fresh_connection": True, "reconnected": True}
        ctx.evidence["popups"] = await ctx.ops.dismiss_popups(connection)
        state = await _capture(ctx, connection, stub.coverage)
    finally:
        await _disconnect(connection)
    digest = digest_state_v2(state)
    observed_identity = _identity(state)
    ctx.evidence.update(observed_state_sha256=digest,
                        expected_state_sha256=stub.expected_state_sha256,
                        digest_matches=digest == stub.expected_state_sha256,
                        observed_identity=observed_identity,
                        expected_identity=stub.expected_identity,
                        identity_matches=observed_identity == stub.expected_identity)
    if not (ctx.evidence["digest_matches"] and ctx.evidence["identity_matches"]):
        raise StageFailure("crash-recovery load did not reproduce the expected state")


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return value if isinstance(value, (dict, list, str, int, float, bool, type(None))) else repr(value)


def _snapshot(directory: Path) -> dict[str, str]:
    if not directory.is_dir():
        return {}
    return {p.relative_to(directory).as_posix(): _sha256_file(p)
            for p in sorted(directory.rglob("*")) if p.is_file()}


async def _stage_validate(ctx: _Context) -> None:
    capture = ctx.latest("capture")["evidence"]
    stub = _position_stub(ctx, capture)
    run_dir = ctx.attempt_dir / "validation" / capture["position_id"] / capture["suite_id"]
    ctx.evidence["run_dir"] = ctx.rel(run_dir)
    ctx.check("validation")
    result = await ctx.ops.run_validation(ctx.root / capture["suite_path"], run_dir)
    ctx.evidence["validation"] = {"passed": result.get("passed"), "errored": result.get("errored")}
    first = _snapshot(run_dir / "reports")
    await _maybe_await(ctx.ops.build_reports(run_dir))
    second = _snapshot(run_dir / "reports")
    ctx.evidence["reports"] = first
    ctx.evidence["reports_identical"] = bool(first) and first == second
    validation_file = run_dir / "validation.json"
    if validation_file.is_file():
        ctx.evidence["validation_sha256"] = _sha256_file(validation_file)

    ctx.check("restore")
    ctx.evidence["restore_deploy"] = _plain(await _maybe_await(ctx.ops.deploy(
        stub.archive, stub.game_save_name, stub.archive_sha256)))
    restore: dict[str, Any] = {"reloaded": None, "reconnect": None, "digest": None}
    ctx.evidence["restore"] = restore
    connection = await _connect(ctx)
    try:
        reloaded = await ctx.ops.reload(connection, stub.game_save_name)
        restore["reloaded"] = reloaded is True
        ctx.evidence["restore_reload_verified"] = reloaded is True
        if reloaded is not True:
            raise StageFailure(f"restore reload of {stub.game_save_name!r} was not verified "
                               f"(reload returned {reloaded!r})")
        # The in-place load invalidates the connection; the capture below
        # never retries on disconnect, so re-establish it explicitly.
        await ctx.ops.reconnect(connection)
        restore["reconnect"] = {"reconnected": True}
        await ctx.ops.dismiss_popups(connection)
        state = await _capture(ctx, connection, stub.coverage)
    finally:
        await _disconnect(connection)
    digest = digest_state_v2(state)
    restore["digest"] = digest
    ctx.evidence.update(restored_digest=digest, expected_state_sha256=stub.expected_state_sha256,
                        restored_digest_matches=digest == stub.expected_state_sha256)
    problems = [name for name, ok in (
        ("validation", result.get("passed") is True and not result.get("errored")),
        ("report regeneration", ctx.evidence["reports_identical"]),
        ("restored digest", ctx.evidence["restored_digest_matches"])) if not ok]
    if problems:
        raise StageFailure(f"validate failed: {problems}")


async def _stage_finish(ctx: _Context) -> None:
    validate = ctx.latest("validate")["evidence"]
    capture = ctx.latest("capture")["evidence"]
    ctx.evidence.update(final_restored_digest=validate.get("restored_digest"),
                        expected_state_sha256=capture.get("captured_state_sha256"))
    if validate.get("restored_digest") != capture.get("captured_state_sha256"):
        raise StageFailure("final restored digest does not equal the expected state")
    ctx.check("finish")


_STAGE_FUNCS = {
    "survey": _stage_survey, "apply": _stage_apply, "probe": _stage_probe,
    "archive": _stage_archive, "capture": _stage_capture, "verify": _stage_verify,
    "menu-check": _stage_menu_check, "validate": _stage_validate, "finish": _stage_finish,
}


def _write_index(ctx: _Context, external: list[Path], *,
                 position_id: str | None) -> dict[str, str]:
    """Inventory every attempt file plus referenced inputs; never itself."""
    index_path = ctx.attempt_dir / INDEX_FILE
    entries: dict[str, str] = {}
    for path in sorted(ctx.attempt_dir.rglob("*")):
        rel = ctx.rel(path)
        if path.is_file() and path != index_path and not _is_transient(rel):
            entries[rel] = _sha256_file(path)
    referenced = [ctx.root / ref["path"] for record in ctx.records
                  for ref in record.get("files", [])]
    for path in [ctx.recipe_path, ctx.root / ctx.recipe["toolset_path"], *external, *referenced]:
        if path.is_file():
            entries[ctx.rel(path)] = _sha256_file(path)
    index = {"schema_version": SCHEMA_VERSION, "scenario_id": ctx.scenario_id,
             "position_id": position_id, "attempt_dir": ctx.rel(ctx.attempt_dir),
             "files": [{"path": p, "sha256": s} for p, s in sorted(entries.items())]}
    data = _json_bytes(index)
    _write_new(index_path, data)
    return {"path": ctx.rel(index_path), "sha256": _sha256_bytes(data)}


def _complete(ctx: _Context) -> dict[str, Any]:
    """After the journal closes: write the evidence index, then the packet."""
    capture = ctx.latest("capture")["evidence"]
    validate = ctx.latest("validate")["evidence"]
    names = _archive_names(ctx.recipe, capture["version"])
    external = [ctx.root / capture["archive_path"], ctx.root / capture["position_path"],
                ctx.root / capture["authoring_input"]["path"],
                ctx.root / capture["public_observation"], ctx.root / capture["suite_path"],
                *(ctx.root / p for p in capture["scripts"]),
                *(ctx.root / p for p in capture["cases"])]
    index_ref = _write_index(ctx, external, position_id=capture["position_id"])
    journal_path = ctx.attempt_dir / JOURNAL_FILE
    packet = {
        "schema_version": SCHEMA_VERSION, "position_id": capture["position_id"],
        "scenario_id": ctx.scenario_id, "family": ctx.recipe["family"],
        "authoring_input": capture["authoring_input"],
        "position": {"path": capture["position_path"], "sha256": capture["position_sha256"]},
        "validation_runs": [{"suite_id": capture["suite_id"], "run_dir": validate["run_dir"],
                             "validation_sha256": validate.get("validation_sha256")}],
        "journal": {"path": ctx.rel(journal_path), "sha256": _sha256_file(journal_path)},
        "evidence_index": index_ref,
    }
    packet_path = ctx.root / names["provenance_packet"]
    _write_immutable(packet_path, _json_bytes(packet))
    return {"evidence_index": index_ref,
            "packet": {"path": ctx.rel(packet_path), "sha256": _sha256_file(packet_path)}}


def _prepare(recipe_path: Path, attempt_dir: Path, root: Path | None, *,
             unindexed_abandon_ok: bool = False
             ) -> tuple[dict[str, Any], Path, Path, Path, list[dict[str, Any]]]:
    root = Path(os.path.abspath(root or _REPO_ROOT))
    recipe_path = Path(os.path.abspath(recipe_path))
    attempt_dir = Path(os.path.abspath(attempt_dir))
    recipe = load_recipe(recipe_path, root=root)
    _rel(attempt_dir, root)
    _rel(recipe_path, root)
    records = _stage_records(attempt_dir)
    abandoned = any(r["stage"] == ABANDON for r in records)
    if (attempt_dir / INDEX_FILE).exists() or (abandoned and not unindexed_abandon_ok):
        raise ValueError(f"attempt {attempt_dir} is closed (indexed or abandoned)")
    return recipe, root, recipe_path, attempt_dir, records


def _sibling_journals(attempt_dir: Path) -> list[Path]:
    """Every other attempt journal of the run (sibling attempt directories)."""
    own = (attempt_dir / JOURNAL_FILE).resolve()
    parent = attempt_dir.parent
    if not parent.is_dir():
        return []
    return [p for p in sorted(parent.glob(f"*/{JOURNAL_FILE}")) if p.resolve() != own]


def find_predecessor_journal(attempt_dir: Path, family: str, predecessor: str) -> Path | None:
    """The sibling journal holding `predecessor` of `family` as terminal-failed."""
    found = []
    for path in _sibling_journals(attempt_dir):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        scenarios = ((doc.get("families") or {}).get(family) or {}).get("scenarios") or []
        if any(isinstance(s, dict) and s.get("scenario_id") == predecessor
               and s.get("status") == "failed" and not s.get("imported_from")
               for s in scenarios):
            found.append(path)
    if len(found) > 1:
        raise ValueError(f"predecessor {predecessor!r} is journaled in several attempt "
                         f"directories {[str(p) for p in found]}; pass --predecessor-journal")
    return found[0] if found else None


def _recover_completion(ctx: _Context, journal: AuthoringJournal,
                        latest: dict[str, Any]) -> dict[str, Any] | None:
    """A passed journal whose index/packet write failed: redo only `_complete`."""
    if journal.record(ctx.scenario_id)["status"] != "passed":
        return None
    completion = _complete(ctx)
    return {**latest, "completion": completion, "recovered": True}


async def run_authoring_stage(recipe_path: Path, *, stage: str, attempt_dir: Path,
                              ops: LiveOps | None = None,
                              root: Path | None = None,
                              predecessor_journal: Path | None = None) -> dict[str, Any]:
    """Run one stage; returns its (immutable) stage record.

    Raises `ValueError` without writing anything when the transition is not
    allowed. A stage whose gate fails, or whose clock expired, is recorded
    as ``failed`` with every piece of evidence gathered so far. The journal
    records the stage before the stage record is written, so a clock that
    expires at the end turns the record into ``failed`` (``clock_expired``).

    A substitute recipe begun in a fresh attempt directory binds its failed
    predecessor's journal: `predecessor_journal`, or by default the sibling
    attempt journal holding the predecessor as terminal-failed. Re-running
    `finish` on a passed attempt whose evidence index is missing (the write
    failed after the journal closed) re-runs only the index/packet writes.
    """
    if stage not in STAGE_PREREQUISITES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {list(STAGES)}")
    recipe, root, recipe_path, attempt_dir, records = _prepare(recipe_path, attempt_dir, root)
    statuses = {s: r["status"] for s, r in _latest(records).items()}
    validate_stage_transition(stage, statuses)
    if stage == "finish":
        for required in STAGES[:-1]:
            if statuses.get(required) != "passed":
                raise ValueError(f"finish requires successful {required}")
    if stage == "finish" and statuses.get(stage) == "passed" \
            and (attempt_dir / JOURNAL_FILE).is_file():
        ctx = _Context(recipe=recipe, recipe_path=recipe_path, attempt_dir=attempt_dir,
                       root=root, ops=ops, stage=stage, sequence=_next_sequence(records),
                       records=records)
        wall, monotonic = ops.clocks if ops is not None else (time.time, time.monotonic)
        with AuthoringJournal(attempt_dir / JOURNAL_FILE, wall_clock=wall,
                              monotonic=monotonic) as journal:
            recovered = _recover_completion(ctx, journal, _latest(records)["finish"])
        if recovered is not None:
            return recovered
    if stage not in REPEATABLE_STAGES and statuses.get(stage) == "passed":
        raise ValueError(f"{stage} already passed; repeat survey/apply/probe to revise it")

    ops = ops or production_ops()
    ctx = _Context(recipe=recipe, recipe_path=recipe_path, attempt_dir=attempt_dir, root=root,
                   ops=ops, stage=stage, sequence=_next_sequence(records), records=records)
    if recipe["predecessor"] is not None and predecessor_journal is None:
        predecessor_journal = find_predecessor_journal(attempt_dir, recipe["family"],
                                                       recipe["predecessor"])
    predecessor_ref = None
    if predecessor_journal is not None:
        predecessor_journal = Path(os.path.abspath(predecessor_journal))
        predecessor_ref = _rel(predecessor_journal, root)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    if stage in REPEATABLE_STAGES:
        _invalidate_downstream(ctx)
    wall, monotonic = ops.clocks
    with AuthoringJournal(attempt_dir / JOURNAL_FILE, wall_clock=wall,
                          monotonic=monotonic) as journal:
        ctx.journal = journal
        try:
            journal.begin(family=recipe["family"], scenario_id=recipe["scenario_id"],
                          predecessor=recipe["predecessor"],
                          reason=recipe["substitution_reason"],
                          material_change=recipe["material_change"],
                          predecessor_journal=predecessor_journal,
                          predecessor_journal_ref=predecessor_ref,
                          sibling_journals=_sibling_journals(attempt_dir))
        except ValueError as exc:
            if "expired" not in str(exc):
                raise
            return ctx.write_record(stage, "failed", error=f"clock_expired: {exc}")
        error = None
        try:
            await _STAGE_FUNCS[stage](ctx)
        except Exception as exc:  # noqa: BLE001 -- every failure is retained evidence
            error = f"{type(exc).__name__}: {exc}"
        record = _journal_then_persist(ctx, error)
        if stage == "finish" and record["status"] == "passed":
            journal.finish(scenario_id=recipe["scenario_id"], passed=True)
            completion = _complete(ctx)
            return {**record, "completion": completion}
        return record


def _journal_then_persist(ctx: _Context, error: str | None) -> dict[str, Any]:
    """Journal the stage first, then write the matching stage record."""
    passed = error is None
    built = ctx.build_record(ctx.stage, "passed" if passed else "failed", error=error,
                             evidence=ctx.evidence, files=ctx.files)
    evidence = {"record": ctx.rel(built[2]), "sha256": _sha256_bytes(built[1])}
    try:
        ctx.journal.record_stage(scenario_id=ctx.scenario_id, stage=ctx.stage,
                                 evidence=evidence, passed=passed)
    except ValueError as exc:
        if "expired" not in str(exc):
            raise
        if passed:
            # The journal has flagged the clock expired: the stage cannot
            # count, but every piece of its evidence is retained.
            built = ctx.build_record(ctx.stage, "failed", error=f"clock_expired: {exc}",
                                     evidence=ctx.evidence, files=ctx.files,
                                     extra={"journal_stage_entry": {**evidence, "passed": True}})
    return ctx.persist(*built)


def abandon_attempt(recipe_path: Path, *, attempt_dir: Path, reason: str,
                    ops: LiveOps | None = None, root: Path | None = None) -> dict[str, Any]:
    """Close a failed or abandoned attempt: terminal record, failed journal, index.

    Never connects to the game. An attempt whose journal is already
    terminal-failed (e.g. an expired clock) is indexed as well; a passed
    or never-started attempt is refused. Idempotent: an attempt already
    abandoned whose index write failed is indexed now (no second record)."""
    if not (isinstance(reason, str) and reason.strip()):
        raise ValueError("abandon requires a non-empty reason")
    recipe, root, recipe_path, attempt_dir, records = _prepare(
        recipe_path, attempt_dir, root, unindexed_abandon_ok=True)
    if not (attempt_dir / JOURNAL_FILE).is_file():
        raise ValueError(f"attempt {attempt_dir} has no authoring journal to abandon")
    wall, monotonic = ops.clocks if ops is not None else (time.time, time.monotonic)
    ctx = _Context(recipe=recipe, recipe_path=recipe_path, attempt_dir=attempt_dir, root=root,
                   ops=ops, stage=ABANDON, sequence=_next_sequence(records), records=records)
    previous = _latest(records).get(ABANDON)
    if previous is not None:
        index_ref = _write_index(ctx, [], position_id=None)
        return {**previous, "evidence_index": index_ref, "recovered": True}
    with AuthoringJournal(attempt_dir / JOURNAL_FILE, wall_clock=wall,
                          monotonic=monotonic) as journal:
        ctx.journal = journal
        before = journal.record(recipe["scenario_id"])
        if before["status"] == "passed":
            raise ValueError(f"scenario {recipe['scenario_id']!r} already passed")
        evidence = {"reason": reason, "journal_status_before": before["status"],
                    "expired_before": before["expired"],
                    "latest_status": stage_status(attempt_dir)}
        built = ctx.build_record(ABANDON, "abandoned", error=reason, evidence=evidence)
        if before["status"] == "open":
            journal_evidence = {"record": ctx.rel(built[2]), "sha256": _sha256_bytes(built[1])}
            for close in (
                lambda: journal.record_stage(scenario_id=ctx.scenario_id, stage=ABANDON,
                                             evidence=journal_evidence, passed=False),
                lambda: journal.finish(scenario_id=ctx.scenario_id, passed=False),
            ):
                try:
                    close()
                except ValueError as exc:
                    if "expired" not in str(exc):
                        raise
        record = ctx.persist(*built)
    index_ref = _write_index(ctx, [], position_id=None)
    return {**record, "evidence_index": index_ref}


# ---------------------------------------------------------------------------
# Offline: evidence-files
# ---------------------------------------------------------------------------

def _safe_rel(rel: Any) -> bool:
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
        return False
    parts = PurePosixPath(rel).parts
    return ".." not in parts and "." not in parts


def evidence_files(root: Path, *, repo_root: Path | None = None) -> list[str]:
    """Union every `evidence-index.json` under `root` and check its closure.

    Returns sorted repository-relative paths (listed files plus the index
    files). Raises `ValueError` listing every missing, altered, untracked,
    unsafe, transient or out-of-scope entry."""
    repo_root = Path(os.path.abspath(repo_root or _REPO_ROOT))
    root = Path(os.path.abspath(root))
    root_rel = _rel(root, repo_root)
    indices = sorted(root.rglob(INDEX_FILE))
    problems: list[str] = []
    attempts = sorted({p.parent.parent for p in root.rglob("stages/*.json")})
    for attempt in attempts:
        if not (attempt / INDEX_FILE).is_file():
            problems.append(f"unindexed_attempt: {_rel(attempt, repo_root)} has stage records "
                            f"but no {INDEX_FILE} (finish or abandon it)")
    if not indices and not problems:
        raise ValueError(f"no {INDEX_FILE} under {root}")
    listed: dict[str, str] = {}
    index_rels = []
    for index_path in indices:
        index_rel = _rel(index_path, repo_root)
        index_rels.append(index_rel)
        if not index_rel.startswith(EVIDENCE_SCOPES):
            problems.append(f"{index_rel}: index outside approved scope {EVIDENCE_SCOPES}")
        doc = json.loads(index_path.read_text(encoding="utf-8"))
        own: set[str] = set()
        for entry in doc.get("files", []):
            rel, sha = entry.get("path"), entry.get("sha256")
            if not _safe_rel(rel):
                problems.append(f"{index_rel}: unsafe path {rel!r}")
                continue
            if not rel.startswith(EVIDENCE_SCOPES):
                problems.append(f"{rel}: outside approved scope {EVIDENCE_SCOPES}")
                continue
            if not (rel.startswith(root_rel + "/") or rel.startswith("benchmarks/")):
                problems.append(f"{rel}: neither under {root_rel} nor under benchmarks/")
                continue
            if _is_transient(rel):
                problems.append(f"{rel}: transient file listed in {index_rel}")
                continue
            path = repo_root / rel
            if not path.is_file():
                problems.append(f"{rel}: missing (listed in {index_rel})")
                continue
            actual = _sha256_file(path)
            if actual != sha:
                problems.append(f"{rel}: sha256 {actual} != recorded {sha}")
                continue
            if listed.get(rel, sha) != sha:
                problems.append(f"{rel}: conflicting digests across indices")
            listed[rel] = sha
            own.add(rel)
        for path in sorted(index_path.parent.rglob("*")):
            rel = _rel(path, repo_root)
            if path.is_file() and path != index_path and not _is_transient(rel) and rel not in own:
                problems.append(f"{rel}: untracked attempt file (not in {index_rel})")
    for rel in sorted(listed):
        if "/stages/" not in rel or not rel.endswith(".json"):
            continue
        record = json.loads((repo_root / rel).read_text(encoding="utf-8"))
        for ref in record.get("files", []):
            if ref.get("path") not in listed:
                problems.append(f"{ref.get('path')}: untracked reference from {rel}")
            elif listed[ref["path"]] != ref.get("sha256"):
                problems.append(f"{ref['path']}: sha256 differs from the reference in {rel}")
    if problems:
        raise ValueError("evidence closure failed:\n  " + "\n  ".join(problems))
    return sorted(set(listed) | set(index_rels))


# ---------------------------------------------------------------------------
# Offline: preflight
# ---------------------------------------------------------------------------

def _portable(path: Path, root: Path) -> str:
    try:
        return _rel(path, root)
    except ValueError:
        return os.path.abspath(path)


def _bind_probe(path: Path, root: Path) -> tuple[dict[str, Any], list[str]]:
    """Bind the Task 17 probe provenance; problems name why it cannot be relied on."""
    if not path.is_file():
        return {"present": False}, ["probe provenance file missing"]
    doc = json.loads(path.read_text(encoding="utf-8"))
    problems = probe_problems(doc, code_root=root)
    return {"present": True, "path": _portable(path, root), "sha256": _sha256_file(path),
            "verdict": doc.get("verdict"), "samples": doc.get("samples"),
            "capture_implementation_sha256": doc.get("capture_implementation_sha256"),
            "evidence_index_path": doc.get("evidence_index_path"),
            "evidence_index_sha256": doc.get("evidence_index_sha256"),
            "limitations": doc.get("limitations"), "problems": problems}, problems


def _bind_full_suite(path: Path, root: Path, code_identity: str) -> dict[str, Any]:
    """Bind the retained full-suite result; it must pass under this code identity."""
    if not path.is_file():
        return {"present": False, "passed": False, "problems": ["pytest result missing"]}
    doc = json.loads(path.read_text(encoding="utf-8"))
    output = path.parent / Path(PREFLIGHT_PYTEST).name
    problems = []
    if not (doc.get("exit_code") == 0 and doc.get("failed") == 0 and doc.get("errors") == 0):
        problems.append(f"suite did not pass: exit {doc.get('exit_code')}, "
                        f"{doc.get('failed')} failed, {doc.get('errors')} errors")
    if doc.get("code_identity") != code_identity:
        problems.append("suite ran under a different code identity")
    if not output.is_file():
        problems.append(f"{output.name} missing")
    return {"present": True, "passed": not problems, "problems": problems,
            "result": {"path": _portable(path, root), "sha256": _sha256_file(path)},
            "output": ({"path": _portable(output, root), "sha256": _sha256_file(output)}
                       if output.is_file() else None),
            "tests_passed": doc.get("passed"),
            **{k: doc.get(k) for k in ("command", "exit_code", "failed", "errors", "duration_s",
                                       "started", "finished", "code_identity", "git_head")}}


def preflight(recipe_paths: list[Path], *, root: Path | None = None,
              probe_path: Path | None = None,
              pytest_result_path: Path | None = None) -> dict[str, Any]:
    """Offline identity binding and historical regression; never connects.

    Binds code/schema/toolset identities, recipe versions, the historical
    audit's input and result digests, the full-suite result and the Task 17
    positive-control timing probe. `passed` is false, with the reasons named
    in `failed_requirements`, unless every binding holds."""
    root = Path(os.path.abspath(root or _REPO_ROOT))
    recipes = []
    for path in recipe_paths:
        recipe = load_recipe(Path(path), root=root)
        toolset = load_toolset(root / recipe["toolset_path"])
        recipes.append({"path": _portable(Path(path), root), "sha256": _sha256_file(Path(path)),
                        "recipe_id": recipe["recipe_id"], "scenario_id": recipe["scenario_id"],
                        "family": recipe["family"], "version": recipe["version"],
                        "toolset_id": toolset["toolset_id"],
                        "toolset_identity": toolset["identity"]})
    arena = root / "src" / "civ_mcp" / "arena"
    code_identity = implementation_fingerprint(root)
    fixture_path = root / AUDIT_FIXTURE
    audit = reproduce_audit(fixture_path, root=root)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    matches = audit.get("membership_matches") is True
    probe, probe_failures = _bind_probe(Path(probe_path or root / PROBE_PROVENANCE), root)
    suite = _bind_full_suite(Path(pytest_result_path or root / PREFLIGHT_PYTEST_RESULT), root,
                             code_identity)
    failed = [name for name, ok in (("historical_audit", matches),
                                    ("full_suite_result", suite["passed"]),
                                    ("positive_control_timing_probe", not probe_failures))
              if not ok]
    return {
        "schema_version": SCHEMA_VERSION,
        "code_identity": code_identity,
        "toolkit_identity": toolkit_fingerprint(root),  # informational, never gated
        "schema_identity": {
            "schema_version": SCHEMA_VERSION,
            "manifest_sha256": _sha256_file(arena / "benchmark_manifest_v2.py"),
            "scorer_sha256": _sha256_file(arena / "benchmark_scoring_v2.py"),
            "predicates_sha256": _sha256_file(arena / "benchmark_predicates_v2.py"),
            "state_sha256": _sha256_file(arena / "benchmark_state_v2.py"),
        },
        "recipes": recipes,
        "historical_audit": {
            "fixture": AUDIT_FIXTURE, "membership_matches": matches,
            "fixture_sha256": _sha256_file(fixture_path),
            "inputs_sha256": document_digest(fixture.get("inputs", [])),
            "result_sha256": document_digest(audit),
            **{k: audit[k] for k in ("trial_count", "uncredited_count",
                                     "affected_trial_count") if k in audit}},
        "full_suite_result": suite,
        "probe": probe,
        "passed": not failed,
        "failed_requirements": failed,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m civ_mcp.arena.benchmark_authoring",
        description="Replayable benchmark position authoring.")
    sub = parser.add_subparsers(dest="command", required=True)
    for stage in STAGES:
        stage_parser = sub.add_parser(stage, help=STAGE_HELP[stage])
        stage_parser.add_argument("--recipe", type=Path, required=True)
        stage_parser.add_argument("--attempt-dir", type=Path, required=True)
        stage_parser.add_argument(
            "--predecessor-journal", type=Path, default=None,
            help="a substitute recipe's failed predecessor journal (default: the sibling "
                 "attempt directory journal holding the predecessor as terminal-failed)")
    pre = sub.add_parser(
        "preflight", help="OFFLINE, needs the committed full-suite run and (to pass) the "
                          "positive-control probe: bind code, toolkit, recipe, toolset and "
                          "probe identities into --output; never connects.")
    pre.add_argument("--recipes", type=Path, nargs="+", required=True)
    pre.add_argument("--output", type=Path, required=True)
    pre.add_argument("--probe", type=Path, default=None,
                     help=f"probe provenance (default {PROBE_PROVENANCE}); its path is "
                          "recorded so the gate reads the same file")
    gate = sub.add_parser(
        "gate", help="OFFLINE, needs the three finish packets and a passed preflight: "
                     "re-derive every requirement from raw files and write the verdict to "
                     "--output.")
    gate.add_argument("--packets", type=Path, nargs="+", required=True)
    gate.add_argument("--preflight", type=Path, default=None)
    gate.add_argument("--output", type=Path, required=True)
    files = sub.add_parser(
        "evidence-files", help="OFFLINE, needs every attempt finished or abandoned: check "
                               "each evidence index's closure and list the files to "
                               "force-add into --output.")
    files.add_argument("--root", type=Path, required=True)
    files.add_argument("--output", type=Path, required=True)
    abandon = sub.add_parser(
        ABANDON, help="OFFLINE, needs a journaled, not-passed attempt: record the abandon, "
                      "close the journal as failed and write ATTEMPT/evidence-index.json "
                      "(re-run to recover a failed index write).")
    abandon.add_argument("--recipe", type=Path, required=True)
    abandon.add_argument("--attempt-dir", type=Path, required=True)
    abandon.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == ABANDON:
            record = abandon_attempt(args.recipe, attempt_dir=args.attempt_dir,
                                     reason=args.reason)
            print(json.dumps({"stage": record["stage"], "status": record["status"],
                              "evidence_index": record["evidence_index"]}))
            return 0
        if args.command == "preflight":
            result = preflight(args.recipes, probe_path=args.probe)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(_json_bytes(result))
            print(args.output)
            return 0 if result["passed"] else 1
        if args.command == "gate":
            pre_path = args.preflight or _REPO_ROOT / PREFLIGHT_OUTPUT
            pre_doc = (json.loads(pre_path.read_text(encoding="utf-8")) if pre_path.is_file()
                       else {"passed": False, "failed_requirements": ["preflight_missing"]})
            result = check_part1_gate(list(args.packets), pre_doc, root=_REPO_ROOT)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(_json_bytes(result))
            print(args.output)
            return 0 if result["passed"] else 1
        if args.command == "evidence-files":
            paths = evidence_files(args.root, repo_root=_REPO_ROOT)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text("".join(f"{p}\n" for p in paths), encoding="utf-8")
            print(args.output)
            return 0
        record = asyncio.run(run_authoring_stage(args.recipe, stage=args.command,
                                                 attempt_dir=args.attempt_dir,
                                                 predecessor_journal=args.predecessor_journal))
        print(json.dumps({"stage": record["stage"], "sequence": record["sequence"],
                          "status": record["status"], "error": record["error"]}))
        return 0 if record["status"] == "passed" else 1
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
