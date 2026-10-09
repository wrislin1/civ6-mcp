"""Scripted validation trials through the production benchmark runner.

`run_scripted_suite` binds a loaded version-2 validation suite (position,
toolset, scripts and case digests) into an immutable lock, opens the
`BenchmarkStore` with it, and drives the unmodified `BenchmarkRunner` over an
ordered schedule of `ScriptedTrialSpec`s. The runner keeps its authority over
reload, popup hygiene, initial/final captures, attempt limits and atomic
commits; `make_scripted_agent` (via `RunnerDependencies.make_agent`) builds a
fresh `ScriptedBackend` + `SingleTurnAgent` per attempt, so scripted calls go
through the same allowed-tool check, registry dispatch, result caps and
bounded v2 captures as a model's.

Expected cases are read here only for their identity and digest; their
expectations never reach the backend, the agent or the factory. This module
returns committed raw records -- scoring and case comparison happen later.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from civ_mcp.arena.benchmark_agent import SingleTurnAgent
from civ_mcp.arena.benchmark_capture import CaptureTelemetry
from civ_mcp.arena.benchmark_contract_v2 import document_digest, implementation_fingerprint
from civ_mcp.arena.benchmark_deploy import deploy_via_windows
from civ_mcp.arena.benchmark_manifest_v2 import (
    SCHEMA_VERSION,
    load_toolset,
    load_v2_document,
    validate_v2_document,
)
from civ_mcp.arena.benchmark_runner import BenchmarkRunner, RunnerDependencies, reload_position
from civ_mcp.arena.benchmark_schedule import ScriptedTrialSpec
from civ_mcp.arena.benchmark_scripted import ScriptedBackend
from civ_mcp.arena.benchmark_state_v2 import capture_state_v2, digest_state_v2
from civ_mcp.arena.benchmark_store import BenchmarkStore, compute_session_fingerprint
from civ_mcp.arena.popups import dismiss_blocking_popups
from civ_mcp.connection import GameConnection

__all__ = [
    "SCRIPTED_ARM_ID",
    "ScriptedTransport",
    "make_scripted_agent",
    "run_scripted_suite",
]

SCRIPTED_ARM_ID = "validation"
_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclasses.dataclass(frozen=True)
class ScriptedTransport:
    """The only injectable seam: the game transport and save lifecycle.

    `connection` is what `GameState` and the v2 capture talk to; `deploy`
    installs the position archive once per suite run; `reload` reloads the
    position save and reports whether it was confirmed; `dismiss_popups`
    returns the popup-hygiene status string. Registry dispatch, the agent,
    the scripted backend and the runner are never injectable here.
    """

    connection: Any
    deploy: Callable[[], object]
    reload: Callable[[], Awaitable[bool]]
    dismiss_popups: Callable[[], Awaitable[str]]


def make_scripted_agent(spec, *, script, toolset, capture_state, telemetry,
                        tile_coords, wall_s, char_cap):
    from civ_mcp.arena.benchmark_agent import SingleTurnAgent
    from civ_mcp.arena.benchmark_scripted import ScriptedBackend
    backend = ScriptedBackend(script, game_tools=tuple(toolset["game_tools"]))
    return SingleTurnAgent(backend, tuple(toolset["game_tools"]),
                           episode_wall_s=wall_s, max_steps=15, char_cap=char_cap,
                           tile_coords=tile_coords, capture_state=capture_state,
                           capture_telemetry=telemetry, evidence_version="2.0.0")


# ---------------------------------------------------------------------------
# Loading and verification
# ---------------------------------------------------------------------------

def _file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _check_ref(ref: dict[str, Any], context: str) -> str:
    """Verify a `{path, sha256}` reference against the file's bytes."""
    actual = _file_sha256(ref["path"])
    if actual != ref["sha256"]:
        raise ValueError(
            f"{context} {ref['path']}: recorded sha256 {ref['sha256']} does not match "
            f"file bytes {actual}"
        )
    return actual


def _portable(path: str | Path) -> str:
    """Repository-relative path when under the repository, else absolute."""
    absolute = os.path.abspath(path)
    try:
        return Path(absolute).relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return absolute


def _portable_ref(ref: dict[str, Any]) -> dict[str, Any]:
    return {"path": _portable(ref["path"]), "sha256": ref["sha256"]}


@dataclasses.dataclass(frozen=True)
class _ScriptedCase:
    case_id: str
    case_ref: dict[str, Any]
    script_ref: dict[str, Any]
    script: dict[str, Any]


def _load_cases(suite: dict[str, Any], toolset: dict[str, Any]) -> list[_ScriptedCase]:
    position_ref = suite["position"]
    cases: list[_ScriptedCase] = []
    for case_ref in suite["cases"]:
        _check_ref(case_ref, "validation_suite case")
        case = load_v2_document(Path(case_ref["path"]), kind="case")
        if (os.path.abspath(case["position"]["path"]) != os.path.abspath(position_ref["path"])
                or case["position"]["sha256"] != position_ref["sha256"]):
            raise ValueError(
                f"case {case['case_id']!r} references a different position than the suite"
            )
        _check_ref(case["position"], f"case {case['case_id']!r} position")
        _check_ref(case["script"], f"case {case['case_id']!r} script")
        script = load_v2_document(Path(case["script"]["path"]), kind="script")
        # Fail before any trial: a script naming a tool outside the frozen
        # toolset is an invalid script, never something to repair or drop.
        ScriptedBackend(copy.deepcopy(script), game_tools=tuple(toolset["game_tools"]))
        cases.append(_ScriptedCase(
            case_id=case["case_id"],
            case_ref={"path": case_ref["path"], "sha256": case_ref["sha256"]},
            script_ref={"path": case["script"]["path"], "sha256": case["script"]["sha256"]},
            script=script,
        ))
    ids = [c.case_id for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError(f"validation_suite has duplicate case ids: {ids}")
    return cases


def _load_position(suite: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    _check_ref(suite["position"], "validation_suite position")
    position = load_v2_document(Path(suite["position"]["path"]), kind="position")
    expected_sha = digest_state_v2(position["expected_state"])
    if expected_sha != position["expected_state_sha256"]:
        raise ValueError(
            f"position {position['position_id']!r}: expected_state digests to {expected_sha}, "
            f"not the recorded expected_state_sha256 {position['expected_state_sha256']}"
        )
    toolset = load_toolset(Path(position["toolset"]["path"]))
    if toolset["identity"] != position["toolset"]["identity"]:
        raise ValueError("position toolset identity does not match the loaded toolset")
    if toolset["identity"] != suite["toolset_identity"]:
        raise ValueError("validation_suite toolset_identity does not match the position toolset")
    if suite["contract_identity"] != position["contract_identity"]:
        raise ValueError("validation_suite contract_identity does not match the position")
    _check_ref(position["archive"], "position archive")
    _check_ref(position["provenance"], "position provenance")
    if not position["coverage"].get("area"):
        raise ValueError("scripted validation requires a non-empty coverage.area for step captures")
    return position, toolset


def _build_lock(
    suite: dict[str, Any],
    position: dict[str, Any],
    toolset: dict[str, Any],
    cases: list[_ScriptedCase],
    schedule: list[ScriptedTrialSpec],
    code_identity: str,
    suite_path: Path | None = None,
) -> dict[str, Any]:
    if suite_path is not None:
        # Bound to the suite file itself: its path and the sha256 of its bytes.
        suite_ref = {"path": _portable(suite_path), "sha256": _file_sha256(suite_path),
                     "bound": "bytes"}
    else:
        portable_suite = copy.deepcopy(suite)
        portable_suite["position"] = _portable_ref(suite["position"])
        portable_suite["cases"] = [_portable_ref(ref) for ref in suite["cases"]]
        suite_ref = {"path": suite["suite_id"], "sha256": document_digest(portable_suite),
                     "bound": "portable"}
    lock = {
        "schema_version": SCHEMA_VERSION,
        "lock_id": f"{suite['suite_id']}:scripted",
        "suite": suite_ref,
        "scripts": [_portable_ref(c.script_ref) for c in cases],
        "cases": [_portable_ref(c.case_ref) for c in cases],
        "archive_identity": {
            **_portable_ref(position["archive"]),
            "game_save_name": position["game_save_name"],
        },
        "state_identity": {
            "position_id": position["position_id"],
            "position_sha256": suite["position"]["sha256"],
            "expected_state_sha256": position["expected_state_sha256"],
            "player_id": position["player_id"],
            "coverage": position["coverage"],
        },
        "provenance_identity": _portable_ref(position["provenance"]),
        "schedule": [dataclasses.asdict(spec) for spec in schedule],
        "code_identity": code_identity,
        "schema_identity": SCHEMA_VERSION,
        "limits": {
            "max_steps": suite["max_steps"],
            "episode_wall_s": suite["episode_wall_s"],
            "result_char_cap": suite["result_char_cap"],
        },
        "actor": {
            "actor_kind": "scripted",
            "counting": False,
            "toolset_id": toolset["toolset_id"],
            "toolset_identity": toolset["identity"],
            "contract_identity": position["contract_identity"],
        },
        "model": None,
        "seed": None,
        "token_budget": None,
        "cost": None,
        "latency": None,
    }
    validate_v2_document(lock, kind="lock")
    return lock


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------

async def run_scripted_suite(
    suite: dict[str, Any],
    run_dir: Path,
    *,
    suite_path: Path | None = None,
    dependencies: ScriptedTransport | None = None,
) -> list[dict[str, Any]]:
    """Run every case of a loaded validation suite; return committed trials.

    `suite_path` is the file `suite` was loaded from: the lock then binds its
    repository-relative path and the sha256 of its bytes (``bound: bytes``);
    without it the lock records the suite id and a portable digest of the
    loaded document (``bound: portable``).
    `dependencies` replaces only the game transport/save lifecycle (tests);
    by default the archive is deployed through the Windows bridge, the live
    `GameConnection` is used, and every reload must be confirmed.
    """
    validate_v2_document(suite, kind="validation_suite")
    position, toolset = _load_position(suite)
    cases = _load_cases(suite, toolset)
    schedule = [
        ScriptedTrialSpec(index=i, position_id=position["position_id"], arm_id=SCRIPTED_ARM_ID,
                          script_id=c.script["script_id"], case_id=c.case_id)
        for i, c in enumerate(cases, start=1)
    ]
    contract_fingerprint = implementation_fingerprint(_REPO_ROOT)
    lock = _build_lock(suite, position, toolset, cases, schedule, contract_fingerprint,
                       suite_path=Path(suite_path) if suite_path is not None else None)
    lock["session_fingerprint"] = compute_session_fingerprint(lock)
    store = BenchmarkStore.create(run_dir, lock)

    by_case = {c.case_id: c for c in cases}
    coverage = position["coverage"]
    player_id = position["player_id"]
    tile_coords = tuple(tuple(pair) for pair in coverage["area"])
    telemetry = CaptureTelemetry()

    owns_connection = dependencies is None
    if dependencies is None:
        dependencies = _production_transport(position)
    evidence = dependencies.deploy()
    store.append_event("scripted_deploy", details={
        "archive": _portable(position["archive"]["path"]),
        "game_save_name": position["game_save_name"],
        "evidence": dataclasses.asdict(evidence) if dataclasses.is_dataclass(evidence) else None,
    })
    connection = dependencies.connection
    if owns_connection:
        await connection.connect()
    try:
        async def capture(conn: Any, pid: int, _tile_coords: Any) -> dict[str, Any]:
            return await capture_state_v2(conn, pid, coverage, io_timing=telemetry.current_io)

        async def capture_runner_state() -> dict[str, Any]:
            return await capture(connection, player_id, tile_coords)

        async def confirmed_reload(_position_id: str) -> bool:
            if not await dependencies.reload():
                # Every script needs a fresh, confirmed archive reload; an
                # unconfirmable reload is an infrastructure attempt.
                raise RuntimeError("scripted reload was not confirmed")
            return True

        async def no_model_canary() -> Any:
            raise RuntimeError("scripted trials have no model health canary")

        def make_agent(spec: ScriptedTrialSpec) -> SingleTurnAgent:
            # A deep copy per attempt: a fresh backend never shares round state.
            return make_scripted_agent(
                spec, script=copy.deepcopy(by_case[spec.case_id].script), toolset=toolset,
                capture_state=capture, telemetry=telemetry, tile_coords=tile_coords,
                wall_s=float(suite["episode_wall_s"]), char_cap=suite["result_char_cap"])

        def trial_identity(spec: ScriptedTrialSpec) -> dict[str, Any]:
            case = by_case[spec.case_id]
            return {
                "script_id": spec.script_id,
                "script_sha256": case.script_ref["sha256"],
                "case_id": spec.case_id,
                "case_sha256": case.case_ref["sha256"],
                "toolset_id": toolset["toolset_id"],
                "toolset_identity": toolset["identity"],
                "contract_fingerprint": contract_fingerprint,
                "coverage": coverage,
            }

        runner = BenchmarkRunner(
            store=store,
            dependencies=RunnerDependencies(
                reload_position=confirmed_reload,
                dismiss_popups=dependencies.dismiss_popups,
                capture_state=capture_runner_state,
                make_agent=make_agent,
                probe_health=no_model_canary,
                connection=connection,
                capture_telemetry=telemetry,
                trial_identity=trial_identity,
            ),
            expected_state=position["expected_state"],
            player_id=player_id,
        )
        await runner.run(schedule)
    finally:
        if owns_connection:
            await connection.disconnect()

    trials_dir = Path(run_dir) / BenchmarkStore.TRIALS_DIR
    return [
        json.loads((trials_dir / f"trial-{spec.index:03d}.json").read_text(encoding="utf-8"))
        for spec in schedule
    ]


def _production_transport(position: dict[str, Any]) -> ScriptedTransport:
    connection = GameConnection()
    archive = position["archive"]
    save = SimpleNamespace(game_save_name=position["game_save_name"])

    # `load_v2_document` resolved the archive to an absolute local path; the
    # bridge runs in the Windows checkout, so it gets the repo-relative path.
    archive_rel = _portable(archive["path"])

    def deploy() -> object:
        return deploy_via_windows(archive_rel, position["game_save_name"], archive["sha256"])

    async def reload() -> bool:
        return await reload_position(connection, save)

    async def dismiss_popups() -> str:
        return await dismiss_blocking_popups(connection)

    return ScriptedTransport(connection=connection, deploy=deploy, reload=reload,
                             dismiss_popups=dismiss_popups)
