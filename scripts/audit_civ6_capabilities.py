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
from typing import cast

from civ_mcp.connection import GameConnection


REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = REPO_ROOT / "docs" / "research" / "civ6-action-space.json"
SCHEMA_VERSION = 1
TABLE_FIELDS = {
    "UnitOperations": "OperationType",
    "UnitCommands": "CommandType",
    "DiplomaticActions": "DiplomaticActionType",
}
RECORD_PREFIX = "CAPABILITY"


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
    return "\n".join(blocks)


def parse_capture_lines(lines: Sequence[str]) -> dict[str, object]:
    found = {name: set() for name in TABLE_FIELDS}
    for line in lines:
        parts = line.split("|")
        if len(parts) != 3 or parts[0] != RECORD_PREFIX:
            raise ValueError(f"malformed record: {line!r}")
        _, table_name, action = parts
        if table_name not in TABLE_FIELDS:
            raise ValueError(f"unknown table: {table_name}")
        if not action:
            raise ValueError(f"empty action in {table_name}")
        if action in found[table_name]:
            raise ValueError(f"duplicate action: {table_name}/{action}")
        found[table_name].add(action)

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
    raise SystemExit(
        "offline report is added in Task 2; use --capture for the capture task"
    )


if __name__ == "__main__":
    raise SystemExit(main())
