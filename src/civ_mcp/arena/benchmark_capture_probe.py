"""Positive-control timing probe for the bounded v2 capture (Plan 3 Part 1, Task 17).

Measures `capture_state_v2` through `capture_bounded` against the existing
non-library positive control `builder-posctrl-v1` before the capture budget
is frozen. No model backend, no library archive and no game mutation are
involved: the probe deploys and reloads the positive control through the
production path, confirms its version-1 identity digest, freezes a
representative v2 scope from that untimed v1 state (plus one untimed grid
survey and one untimed v2 discovery capture that freezes visible hostiles as
tracked targets), runs exactly `samples` bounded v2 captures, and re-confirms the v1
identity afterwards.

Every sample (including failures and their reasons) is written to
`output_dir/samples/NNN.json`; `summary.json` carries the verdict and
`evidence-index.json` hashes every file written. The probe proves timing
only for the recorded scope; it cannot prove performance for a larger one.

CLI::

    uv run python -m civ_mcp.arena.benchmark_capture_probe \
        --position benchmarks/positions/builder-posctrl-v1.yaml \
        --samples 20 --output-dir benchmark_runs/plan3-part1/capture-probe

Exit codes: 0 gate passed, 1 gate failed (evidence still written),
2 refused (not the identified positive control, bad input, or an output
directory that already holds evidence).
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import dataclasses
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Sequence

from civ_mcp.arena.benchmark_capture import CaptureTelemetry, capture_bounded
from civ_mcp.arena.benchmark_contract_v2 import (
    canonical_bytes,
    document_digest,
    implementation_fingerprint,
)
from civ_mcp.arena.benchmark_manifest import PositionManifest, load_position_manifest
from civ_mcp.arena.benchmark_state import (
    BenchmarkStateError,
    state_digest,
    verify_expected_state_digest,
)

__all__ = [
    "BenchmarkStateError", "CAPTURE_IMPLEMENTATION_FILES", "CAPTURE_IMPLEMENTATION_FUNCTIONS",
    "POSITIVE_CONTROL_ID", "ProbeOps", "ProbeRefused", "REQUIRED_SAMPLES", "build_probe_scope",
    "capture_implementation_digest", "discover_tracked_targets", "gate_reasons", "main",
    "probe_capture", "production_ops", "timing_probe_passes",
]

_REPO_ROOT = Path(__file__).resolve().parents[3]

POSITIVE_CONTROL_ID = "builder-posctrl-v1"
POSITIVE_CONTROL_SPLIT = "calibration"
REQUIRED_SAMPLES = 20
CAPTURE_LIMIT_S = 2.0
SCOPE_RADIUS = 3
_IDENTITY_KEYS = ("turn", "player_id", "active_player")
_IO_KEYS = ("connect_s", "lock_wait_s", "pre_drain_s", "response_wait_s", "post_drain_s")

# Query, parser, capture wrapper, connection + tuner transport and numeric
# normalisation: the code whose timing this probe measures. The canonical
# hash lives in `benchmark_contract_v2.py`, which also holds the growing
# `FINGERPRINT_DEPENDENCIES` list, so only its two hash functions are hashed
# (see `CAPTURE_IMPLEMENTATION_FUNCTIONS`); appending a fingerprint
# dependency must not invalidate a timing measurement.
CAPTURE_IMPLEMENTATION_FILES: tuple[str, ...] = tuple(sorted((
    "src/civ_mcp/arena/benchmark_capture.py",
    "src/civ_mcp/arena/benchmark_state.py",
    "src/civ_mcp/arena/benchmark_state_v2.py",
    "src/civ_mcp/connection.py",
    "src/civ_mcp/lua/benchmark_v2.py",
    "src/civ_mcp/tuner_client.py",
)))
_CONTRACT_V2_PATH = "src/civ_mcp/arena/benchmark_contract_v2.py"
CAPTURE_IMPLEMENTATION_FUNCTIONS: tuple[str, ...] = (
    "benchmark_contract_v2.canonical_bytes",
    "benchmark_contract_v2.document_digest",
)


class _Abort(Exception):
    """Stop the probe early with a named verdict reason."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ProbeRefused(ValueError):
    """The probe will not run: not the identified positive control, invalid
    input, or an output directory that already holds evidence."""


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def timing_probe_passes(rows):
    return (len(rows) == 20 and len({row["digest"] for row in rows}) == 1
            and all(row["complete"] and row["duration_s"] <= 2.0
                    and row["lua_executions"] == 1
                    and row["pre_drain_s"] is not None
                    and row["post_drain_s"] is not None for row in rows))


def gate_reasons(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Name each way `rows` fails `timing_probe_passes` (empty when it passes)."""
    reasons: list[dict[str, Any]] = []

    def add(code: str, indices: list[int], detail: str) -> None:
        if indices:
            reasons.append({"code": code, "samples": indices, "detail": detail})

    if len(rows) != REQUIRED_SAMPLES:
        reasons.append({"code": "sample_count", "samples": [],
                        "detail": f"{len(rows)} samples, exactly {REQUIRED_SAMPLES} required"})
    add("incomplete", [i for i, r in enumerate(rows) if not r["complete"]],
        "sample did not complete a valid capture")
    digests = sorted({r["digest"] for r in rows if r["digest"] is not None})
    if len(digests) > 1:
        first = next(r["digest"] for r in rows if r["digest"] is not None)
        reasons.append({"code": "digest_drift",
                        "samples": [i for i, r in enumerate(rows)
                                    if r["digest"] is not None and r["digest"] != first],
                        "detail": f"{len(digests)} distinct v2 digests"})
    add("overrun", [i for i, r in enumerate(rows)
                    if r["duration_s"] is not None and r["duration_s"] > CAPTURE_LIMIT_S],
        f"duration above {CAPTURE_LIMIT_S}s")
    add("two_queries", [i for i, r in enumerate(rows)
                        if r["lua_executions"] is not None and r["lua_executions"] != 1],
        "capture did not use exactly one Lua execution")
    add("missing_execution_count", [i for i, r in enumerate(rows) if r["lua_executions"] is None],
        "Lua execution count not measured")
    add("missing_drain", [i for i, r in enumerate(rows)
                          if r["pre_drain_s"] is None or r["post_drain_s"] is None],
        "pre- or post-drain duration not measured")
    if not reasons and not timing_probe_passes(rows):
        reasons.append({"code": "timing_gate", "samples": [], "detail": "gate rule failed"})
    return reasons


# ---------------------------------------------------------------------------
# Capture implementation identity
# ---------------------------------------------------------------------------

def _function_source(path: Path, name: str) -> str:
    """Source of top-level function `name` in `path`: the same whole lines
    `inspect.getsource` returns for it, read from `path` (not the imported
    module) so a copied tree under another root is hashed as itself."""
    text = path.read_text(encoding="utf-8")
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            lines = text.splitlines(keepends=True)
            return "".join(lines[start - 1:node.end_lineno])
    raise ValueError(f"capture implementation function missing: {path.name}:{name}")


def capture_implementation_digest(root: Path) -> str:
    """sha256 of the canonical sorted `[name, sha256]` list covering every
    `CAPTURE_IMPLEMENTATION_FILES` file and the source of every
    `CAPTURE_IMPLEMENTATION_FUNCTIONS` function under `root`."""
    entries = []
    for rel in CAPTURE_IMPLEMENTATION_FILES:
        path = Path(root) / rel
        if not path.is_file():
            raise ValueError(f"capture implementation file missing: {rel}")
        entries.append([rel, hashlib.sha256(path.read_bytes()).hexdigest()])
    contract = Path(root) / _CONTRACT_V2_PATH
    if not contract.is_file():
        raise ValueError(f"capture implementation file missing: {_CONTRACT_V2_PATH}")
    for qualified in CAPTURE_IMPLEMENTATION_FUNCTIONS:
        source = _function_source(contract, qualified.rsplit(".", 1)[1])
        entries.append([qualified, hashlib.sha256(source.encode("utf-8")).hexdigest()])
    return hashlib.sha256(canonical_bytes(sorted(entries))).hexdigest()


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def clipped_square_area(
    anchors: Iterable[Sequence[int]],
    radius: int,
    *,
    grid: Sequence[int],
) -> set[tuple[int, int]]:
    """Every grid tile within an offset-coordinate square of `radius` around
    each anchor -- a superset of the hex radius, clipped to ``grid`` (width,
    height) on all four edges.

    The v2 query errors (``AREA_PLOT_NOT_FOUND``) on any area tile the map
    does not have, so every coverage builder (timing probe, authoring) must
    clip through this one function: an anchor near the east or south edge
    otherwise produces tiles past the grid and no capture can succeed.
    """
    width, height = int(grid[0]), int(grid[1])
    area: set[tuple[int, int]] = set()
    for ax, ay in anchors:
        ax, ay = int(ax), int(ay)
        for x in range(ax - radius, ax + radius + 1):
            for y in range(ay - radius, ay + radius + 1):
                if 0 <= x < width and 0 <= y < height:
                    area.add((x, y))
    return area


def build_probe_scope(
    v1_state: dict[str, Any],
    relevant_tiles: Sequence[Sequence[int]],
    *,
    grid: Sequence[int],
    radius: int = SCOPE_RADIUS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Representative v2 coverage frozen from an (untimed) v1 state.

    Area: every in-grid tile within an offset-coordinate square of `radius`
    (a superset of the hex radius) around every owned city and unit, plus
    every relevant tile (never clipped). `tracked_targets` starts empty:
    the v1 state carries no hostile units, so `probe_capture` fills it from
    one untimed discovery capture (`discover_tracked_targets`).
    """
    width, height = int(grid[0]), int(grid[1])
    anchors = sorted({(int(row["x"]), int(row["y"]))
                      for key in ("cities", "units") for row in v1_state.get(key, [])})
    area = {(int(x), int(y)) for x, y in relevant_tiles}
    area |= clipped_square_area(anchors, radius, grid=(width, height))
    scope = {
        "include_owned_tiles": True,
        "area": [list(p) for p in sorted(area)],
        "tracked_targets": [],
    }
    info = {
        "radius": radius,
        "neighbourhood": "offset-coordinate square (superset of hex radius), clipped to grid",
        "grid": [width, height],
        "anchor_count": len(anchors),
        "relevant_tiles": sorted([int(x), int(y)] for x, y in relevant_tiles),
        "area_count": len(area),
    }
    return scope, info


def discover_tracked_targets(discovery_state: dict[str, Any]) -> list[list[int]]:
    """Sorted `[owner, id]` of every visible hostile TARGET row in a v2 state
    captured with `tracked_targets: []` (the v2 query emits visible hostiles
    near the scope as untracked TARGET rows)."""
    pairs = {(int(t["owner"]), int(t["id"])) for t in discovery_state.get("targets", [])
             if t.get("hostile") is True and t.get("visible") is True}
    return [list(p) for p in sorted(pairs)]


_LIMIT_SCOPE = "timing proven only for the recorded scope"
_LIMIT_NO_TARGETS = "no visible hostile targets at the positive control: TARGET rows not timed"
_LIMIT_NO_DISCOVERY = "target discovery did not run: TARGET rows not timed"


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class ProbeOps:
    """Injectable read-only operations (production wiring: `production_ops`)."""

    connect: Callable[[], Awaitable[Any]]
    deploy: Callable[[PositionManifest, Path], Awaitable[dict[str, Any]]]
    reload: Callable[[Any, PositionManifest], Awaitable[bool]]
    dismiss_popups: Callable[[Any], Awaitable[str]]
    capture_v1: Callable[[Any, int, list[tuple[int, int]]], Awaitable[dict[str, Any]]]
    capture_v2: Callable[..., Awaitable[dict[str, Any]]]
    grid_size: Callable[[Any], Awaitable[tuple[int, int]]]
    disconnect: Callable[[Any], Awaitable[None]]


async def query_grid_size(conn: Any) -> tuple[int, int]:
    """One untimed GameCore read of ``Map.GetGridSize()`` as ``(width, height)``."""
    from civ_mcp.lua._helpers import SENTINEL
    lines = await conn.execute_read(
        f'local w, h = Map.GetGridSize()\nprint("GRID|" .. w .. "|" .. h)\nprint("{SENTINEL}")'
    )
    for line in lines:
        parts = line.split("|")
        if parts[0] == "GRID" and len(parts) == 3:
            return int(parts[1]), int(parts[2])
    raise BenchmarkStateError(f"grid size query returned no GRID row: {lines!r}")


def _bridge_archive(archive: Path) -> str:
    """Repository-relative archive path for the Windows bridge (absolute otherwise)."""
    try:
        return Path(os.path.abspath(archive)).relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return str(archive)


def production_ops() -> ProbeOps:
    """The production deploy/reload/capture path; read-only after deploy."""
    from civ_mcp.arena.benchmark_deploy import deploy_via_windows
    from civ_mcp.arena.benchmark_runner import reload_position
    from civ_mcp.arena.benchmark_state import capture_canonical_state
    from civ_mcp.arena.benchmark_state_v2 import capture_state_v2
    from civ_mcp.arena.popups import dismiss_blocking_popups
    from civ_mcp.connection import GameConnection

    async def connect() -> Any:
        conn = GameConnection()
        await conn.connect()
        return conn

    async def deploy(position: PositionManifest, archive: Path) -> dict[str, Any]:
        # The bridge runs in the Windows checkout: hand it the repo-relative path.
        evidence = await asyncio.to_thread(
            deploy_via_windows, _bridge_archive(archive), position.game_save_name,
            position.archive_sha256)
        return dataclasses.asdict(evidence)

    async def disconnect(conn: Any) -> None:
        await conn.disconnect()

    return ProbeOps(
        connect=connect, deploy=deploy, reload=reload_position,
        dismiss_popups=dismiss_blocking_popups, capture_v1=capture_canonical_state,
        capture_v2=capture_state_v2, grid_size=query_grid_size, disconnect=disconnect,
    )


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _valid_seconds(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _clean_record(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Copy a telemetry record with every non-finite/negative timing removed
    (so it reads as unavailable) and name the removed fields."""
    invalid: list[str] = []
    duration = record.get("duration_s")
    if not _valid_seconds(duration):
        invalid.append("duration_s")
        duration = None
    io: dict[str, Any] = {}
    for key, value in record.get("io", {}).items():
        if key in _IO_KEYS and not _valid_seconds(value):
            invalid.append(f"io.{key}")
            continue
        io[key] = value
    return dict(record, duration_s=duration, io=io), invalid


def _resolve_archive(archive: str) -> Path:
    path = Path(archive)
    return path if path.is_absolute() else _REPO_ROOT / path


def _admit(position_path: Path, samples: int, output_dir: Path) -> tuple[PositionManifest, Path]:
    try:
        position = load_position_manifest(position_path)
    except (OSError, ValueError) as exc:
        raise ProbeRefused(f"cannot load position manifest {position_path}: {exc}") from exc
    if position.position_id != POSITIVE_CONTROL_ID or position.split != POSITIVE_CONTROL_SPLIT:
        raise ProbeRefused(
            f"only the positive control {POSITIVE_CONTROL_ID!r} (split "
            f"{POSITIVE_CONTROL_SPLIT!r}) is admitted; got {position.position_id!r} "
            f"(split {position.split!r})")
    archive = _resolve_archive(position.archive)
    if not archive.is_file():
        raise ProbeRefused(f"positive-control archive missing: {archive}")
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != position.archive_sha256:
        raise ProbeRefused(f"archive sha256 {actual} != manifest {position.archive_sha256}")
    try:
        verify_expected_state_digest(position.expected_state, position.expected_state_sha256)
    except BenchmarkStateError as exc:
        raise ProbeRefused(str(exc)) from exc
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        raise ProbeRefused(f"samples must be a positive integer, got {samples!r}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ProbeRefused(
            f"output directory {output_dir} already holds evidence; use a new directory "
            "so earlier attempts are retained")
    return position, archive


def _write_json(path: Path, value: Any, written: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                               allow_nan=False) + "\n", encoding="utf-8")
    written.append(path)


def _index_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


async def probe_capture(
    position_path: Path,
    *,
    samples: int = REQUIRED_SAMPLES,
    output_dir: Path,
    ops: ProbeOps | None = None,
) -> dict[str, Any]:
    """Run the positive-control timing probe and write its evidence.

    Raises `ProbeRefused` (a `ValueError`) before any deploy when the input
    is not the identified positive control. Every later failure is recorded
    in the summary verdict instead of raised.
    """
    position_path = Path(position_path)
    output_dir = Path(output_dir)
    position, archive = _admit(position_path, samples, output_dir)
    ops = ops if ops is not None else production_ops()

    started = _utc_now()
    attempt_id = "capture-probe-" + started.strftime("%Y%m%dT%H%M%S%fZ")
    written: list[Path] = []
    reasons: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    setup: dict[str, Any] = {}
    scope: dict[str, Any] | None = None
    scope_info: dict[str, Any] | None = None
    row_counts: dict[str, Any] | None = None
    telemetry = CaptureTelemetry()
    loop_wall_s = 0.0
    tiles = [tuple(t) for t in position.relevant_tiles]

    def fail(code: str, detail: str) -> None:
        reasons.append({"code": code, "samples": [], "detail": detail})

    conn: Any = None
    try:
        setup["deployment"] = await ops.deploy(position, archive)
        conn = await ops.connect()
        verified = await ops.reload(conn, position)
        setup["reload_verified"] = bool(verified)
        if not verified:
            raise _Abort("reload_unverified", "reload_position reported verified=False")
        setup["popups"] = await ops.dismiss_popups(conn)
        v1_initial = await ops.capture_v1(conn, position.player_id, tiles)
        initial_digest = state_digest(v1_initial)
        setup["v1_initial_digest"] = initial_digest
        if initial_digest != position.expected_state_sha256:
            raise _Abort("v1_digest_mismatch",
                         f"v1 digest {initial_digest} != expected {position.expected_state_sha256}")
        identity = {k: v1_initial.get(k) for k in _IDENTITY_KEYS}
        setup["identity"] = identity
        grid = await ops.grid_size(conn)  # untimed survey
        scope, scope_info = build_probe_scope(v1_initial, tiles, grid=grid)

        # Untimed target discovery: one v2 capture with no tracked targets,
        # never a sample and never inside capture_bounded.
        survey_io: dict[str, Any] = {}
        survey_started = time.monotonic()
        try:
            discovery = await ops.capture_v2(conn, position.player_id, scope,
                                             io_timing=survey_io)
        except Exception as exc:
            raise _Abort("survey_failed",
                         f"target discovery capture failed: {type(exc).__name__}: {exc}") from exc
        tracked = discover_tracked_targets(discovery)
        setup["survey"] = {
            "op": "capture_v2_target_discovery",
            "timed_sample": False,
            "duration_s": time.monotonic() - survey_started,
            "lua_executions": survey_io.get("lua_executions"),
            "row_counts": discovery.get("row_counts"),
            "tracked_targets": tracked,
        }
        scope = dict(scope, tracked_targets=tracked)
        scope_info = dict(scope_info, tracked_count=len(tracked),
                          tracked_targets_source="untimed v2 discovery capture: visible hostiles")

        invalid_rows: list[int] = []
        drifted: list[int] = []
        loop_started = time.monotonic()

        async def read() -> dict[str, Any]:
            # capture_bounded installs a fresh current_io before each call.
            return await ops.capture_v2(conn, position.player_id, scope,
                                        io_timing=telemetry.current_io)

        for index in range(samples):
            state: dict[str, Any] | None = None
            digest: str | None = None
            error: dict[str, str] | None = None
            try:
                state, digest = await capture_bounded(
                    read, phase="probe", telemetry=telemetry, limit_s=CAPTURE_LIMIT_S)
            except Exception as exc:  # recorded per sample; cancellation propagates
                error = {"type": type(exc).__name__, "message": str(exc)}
            record, invalid = _clean_record(telemetry.records[-1])
            sample_identity = (None if state is None
                               else {k: state.get(k) for k in _IDENTITY_KEYS})
            identity_ok = sample_identity == identity
            if state is not None and row_counts is None:
                row_counts = state.get("row_counts")
            row = {
                "complete": bool(record["complete"]) and not invalid and identity_ok,
                "duration_s": record["duration_s"],
                "lua_executions": record["io"].get("lua_executions"),
                "pre_drain_s": record["io"].get("pre_drain_s"),
                "post_drain_s": record["io"].get("post_drain_s"),
                "digest": digest,
                "phase": record["phase"],
            }
            rows.append(row)
            if invalid:
                invalid_rows.append(index)
            if state is not None and not identity_ok:
                drifted.append(index)
            _write_json(output_dir / "samples" / f"{index + 1:03d}.json", {
                "attempt_id": attempt_id, "index": index, "row": row, "record": record,
                "invalid_fields": invalid, "identity": sample_identity,
                "identity_matches": identity_ok, "error": error,
                "row_counts": None if state is None else state.get("row_counts"),
            }, written)
        loop_wall_s = time.monotonic() - loop_started

        if invalid_rows:
            reasons.append({"code": "invalid_duration", "samples": invalid_rows,
                            "detail": "non-finite or negative timing rejected"})
        if drifted:
            reasons.append({"code": "identity_drift", "samples": drifted,
                            "detail": f"sample turn/player identity differs from {identity}"})

        v1_final = await ops.capture_v1(conn, position.player_id, tiles)
        final_digest = state_digest(v1_final)
        setup["v1_final_digest"] = final_digest
        if final_digest != initial_digest:
            fail("identity_drift",
                 f"final v1 digest {final_digest} != initial {initial_digest}")
    except _Abort as exc:
        fail(exc.code, exc.detail)
    except Exception as exc:  # setup/teardown failure: recorded, never a pass
        fail("probe_error", f"{type(exc).__name__}: {exc}")
    finally:
        if conn is not None:
            await ops.disconnect(conn)

    reasons = gate_reasons(rows) + reasons
    passed = timing_probe_passes(rows) and not reasons

    # Summarise only records with valid timings; invalid ones are named.
    clean = CaptureTelemetry()
    excluded = []
    for i, raw in enumerate(telemetry.records):
        record, invalid = _clean_record(raw)
        if record["duration_s"] is None:
            excluded.append(i)
            continue
        clean.records.append(record)
    telemetry_summary = clean.summary(episode_wall_s=loop_wall_s)
    telemetry_summary["excluded_invalid_records"] = excluded

    limitations = [_LIMIT_SCOPE]
    if "survey" not in setup:
        limitations.append(_LIMIT_NO_DISCOVERY)
    elif not setup["survey"]["tracked_targets"]:
        limitations.append(_LIMIT_NO_TARGETS)

    summary = {
        "position_id": position.position_id,
        "archive_sha256": position.archive_sha256,
        "expected_state_sha256": position.expected_state_sha256,
        "scope": scope,
        "scope_sha256": None if scope is None else document_digest(scope),
        "scope_info": scope_info,
        "row_counts": row_counts,
        "samples": len(rows),
        "samples_requested": samples,
        "rows": rows,
        "telemetry_summary": telemetry_summary,
        "setup": setup,
        "verdict": {"passed": passed, "reasons": reasons},
        "limitations": limitations,
        "capture_limit_s": CAPTURE_LIMIT_S,
        "capture_implementation_sha256": capture_implementation_digest(_REPO_ROOT),
        "code_identity": implementation_fingerprint(_REPO_ROOT),
        "attempt_id": attempt_id,
        "started": started.isoformat(),
        "finished": _utc_now().isoformat(),
    }
    summary = json.loads(json.dumps(summary, allow_nan=False))
    _write_json(output_dir / "summary.json", summary, written)
    index = {"attempt_id": attempt_id, "files": [
        {"path": _index_path(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in written]}
    _write_json(output_dir / "evidence-index.json", index, [])
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m civ_mcp.arena.benchmark_capture_probe",
        description="Time the bounded v2 capture on the positive control (no mutation).")
    parser.add_argument("--position", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=REQUIRED_SAMPLES)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--write-provenance", type=Path, default=None,
                        help="also write the summary (minus rows) plus the evidence-index digest")
    args = parser.parse_args(argv)
    try:
        summary = asyncio.run(probe_capture(args.position, samples=args.samples,
                                            output_dir=args.output_dir))
    except ProbeRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if args.write_provenance is not None:
        index_path = args.output_dir / "evidence-index.json"
        record = {k: v for k, v in summary.items() if k != "rows"}
        record["evidence_index_path"] = _index_path(index_path)
        record["evidence_index_sha256"] = hashlib.sha256(index_path.read_bytes()).hexdigest()
        _write_json(args.write_provenance, record, [])
    verdict = summary["verdict"]
    print(json.dumps({"passed": verdict["passed"], "reasons": verdict["reasons"],
                      "attempt_id": summary["attempt_id"]}, indent=2))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
