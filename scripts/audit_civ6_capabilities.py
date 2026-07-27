#!/usr/bin/env python3
"""Capture and report Civ 6 engine action coverage."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, cast

from civ_mcp.connection import GameConnection

if TYPE_CHECKING:
    from civ_mcp.capability_map import ActionSnapshot, ReportEvidence


REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = REPO_ROOT / "docs" / "research" / "civ6-action-space.json"
SCHEMA_VERSION = 1
TABLE_FIELDS = {
    "UnitOperations": "OperationType",
    "UnitCommands": "CommandType",
    "DiplomaticActions": "DiplomaticActionType",
}
RECORD_PREFIX = "CAPABILITY"
CAPTURE_COMPLETE_ACTION = "__MCP_CAPTURE_COMPLETE__"
CAPTURE_COMPLETE_TABLE = "DiplomaticActions"


def build_capture_lua() -> str:
    blocks = []
    for table_name, field_name in TABLE_FIELDS.items():
        blocks.append(
            "for row in GameInfo.{table}() do "
            'print("{prefix}|{table}|" .. tostring(row.{field})) end'.format(
                prefix=RECORD_PREFIX,
                table=table_name,
                field=field_name,
            )
        )
    blocks.append(
        f'print("{RECORD_PREFIX}|{CAPTURE_COMPLETE_TABLE}|'
        f'{CAPTURE_COMPLETE_ACTION}")'
    )
    return "\n".join(blocks)


def parse_capture_lines(lines: Sequence[str]) -> dict[str, object]:
    # LuaEvent callbacks (e.g. ShowIngameUI -> BulkHide debug prints) inject
    # spurious output into tuner responses; game_state._action_result scans
    # past them for the same reason. Anything that does not claim to be one of
    # our records is noise and is dropped -- a line that does claim to be one
    # and is malformed is still an error.
    records = [line for line in lines if line.startswith(f"{RECORD_PREFIX}|")]
    found = {name: set() for name in TABLE_FIELDS}
    completion_count = 0
    for index, line in enumerate(records):
        parts = line.split("|")
        if len(parts) != 3 or parts[0] != RECORD_PREFIX:
            raise ValueError(f"malformed record: {line!r}")
        _, table_name, action = parts
        if table_name not in TABLE_FIELDS:
            raise ValueError(f"unknown table: {table_name}")
        if not action:
            raise ValueError(f"empty action in {table_name}")
        if (
            table_name == CAPTURE_COMPLETE_TABLE
            and action == CAPTURE_COMPLETE_ACTION
        ):
            completion_count += 1
            if index != len(records) - 1:
                raise ValueError(
                    "capture completion marker must be the final record"
                )
            continue
        if action in found[table_name]:
            raise ValueError(f"duplicate action: {table_name}/{action}")
        found[table_name].add(action)

    if completion_count != 1:
        raise ValueError(
            "capture completion marker must appear exactly once"
        )
    missing = [name for name, actions in found.items() if not actions]
    if missing:
        raise ValueError(f"missing tables: {', '.join(missing)}")
    return {
        "schema_version": SCHEMA_VERSION,
        "tables": {name: sorted(found[name]) for name in TABLE_FIELDS},
    }


def write_snapshot_atomic(
    snapshot: Mapping[str, object],
    path: Path = SNAPSHOT_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    temp_name: str | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name is not None and Path(temp_name).exists():
            Path(temp_name).unlink()


async def capture_action_space() -> dict[str, object]:
    conn = GameConnection()
    await conn.connect()
    try:
        lines = await conn.execute_read(build_capture_lua())
    finally:
        await conn.disconnect()
    return parse_capture_lines(lines)


def _load_snapshot(path: Path = SNAPSHOT_PATH) -> ActionSnapshot:
    return cast(
        "ActionSnapshot",
        json.loads(path.read_text(encoding="utf-8")),
    )


def _arena_audit_evidence() -> dict[str, object]:
    import importlib.util

    path = REPO_ROOT / "scripts" / "audit_arena_tool_coverage.py"
    spec = importlib.util.spec_from_file_location(
        "_arena_tool_coverage_audit",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load arena audit: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.collect_evidence()


def _validated_report() -> ReportEvidence:
    from civ_mcp.arena.registry import TOOL_REGISTRY
    from civ_mcp.capability_map import (
        ACTION_COVERAGE,
        build_report_evidence,
        validate_coverage,
    )

    snapshot = _load_snapshot()
    raw_unit_actions = _arena_audit_evidence()["mcp_unit_actions"]
    if not isinstance(raw_unit_actions, list):
        raise RuntimeError("arena audit mcp_unit_actions must be a list")
    unit_actions: set[str] = set()
    for action in raw_unit_actions:
        if not isinstance(action, str):
            raise RuntimeError(
                "arena audit mcp_unit_actions must contain only strings"
            )
        unit_actions.add(action)
    validate_coverage(
        snapshot,
        ACTION_COVERAGE,
        arena_tools=set(TOOL_REGISTRY),
        unit_action_verbs=unit_actions,
    )
    return build_report_evidence(snapshot, ACTION_COVERAGE)


def _print_human(evidence: ReportEvidence) -> None:
    counts = evidence["counts"]
    print(
        "counts:",
        f"covered={counts['covered']}",
        f"missing={counts['missing']}",
        f"excluded={counts['excluded']}",
        f"total={counts['total']}",
    )
    for row in evidence["missing"]:
        print(f"{row['priority']:>6}  {row['action']}  {row['note']}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--capture", action="store_true")
    modes.add_argument("--report", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.capture and args.as_json:
        _build_parser().error("--json cannot be used with --capture")
    if args.capture:
        snapshot = asyncio.run(capture_action_space())
        write_snapshot_atomic(snapshot)
        tables = cast(Mapping[str, Sequence[str]], snapshot["tables"])
        counts = {
            name: len(actions) for name, actions in tables.items()
        }
        print("captured:", " ".join(f"{k}={v}" for k, v in counts.items()))
        return 0
    evidence = _validated_report()
    if args.as_json:
        print(json.dumps(evidence, sort_keys=True))
    else:
        _print_human(evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
