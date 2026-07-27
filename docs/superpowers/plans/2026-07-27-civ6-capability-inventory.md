# Civ 6 Capability Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Capture Civ 6's engine-owned action tables, classify every action against real tool surfaces, and fail offline tests whenever an action is unclassified or stale.

**Architecture:** One script owns both the attended FireTuner capture and the offline report. A committed JSON snapshot isolates CI from the game, while `capability_map.py` owns the hand-maintained classification and a reusable validator; tests inject synthetic maps into that validator before enforcing the real 133-action snapshot.

**Tech Stack:** Python 3.12, `asyncio`, existing `GameConnection`, `argparse`, JSON, pytest via `uv run --extra test pytest`.

## Global Constraints

- Snapshot schema version is exactly `1`.
- Required tables are exactly `UnitOperations`, `UnitCommands`, and `DiplomaticActions`.
- Capture records use exactly `CAPABILITY|<table>|<action>`.
- Capture must validate the complete result before atomically replacing `docs/research/civ6-action-space.json`.
- The committed snapshot contains no game build or ruleset stamp.
- Coverage statuses are exactly `covered`, `missing`, and `excluded`.
- Missing priorities are exactly `high`, `medium`, and `low`.
- `--capture` is live-only, mutually exclusive with `--report`, and incompatible with `--json`.
- Running with no mode is the same as `--report`.
- No missing gameplay verb is implemented in this plan.
- `EMBARK` and `DISEMBARK` are excluded only with recorded live evidence that `move_unit` auto-embarks; otherwise they are `missing` at `medium`.
- Run the full arena suite before each task commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/audit_civ6_capabilities.py` | Build and parse the FireTuner capture, write the snapshot atomically, validate the real coverage map, and render human/JSON reports. |
| `docs/research/civ6-action-space.json` | Committed, sorted snapshot used by every offline path. |
| `src/civ_mcp/capability_map.py` | Define `Coverage`, `ACTION_COVERAGE`, `validate_coverage`, and deterministic report evidence. |
| `tests/arena/test_capability_coverage.py` | Exercise capture parsing/writes, validation rules, the real snapshot/map, CLI modes, and report output. |
| `scripts/audit_arena_tool_coverage.py` | Read only; its `collect_evidence()["mcp_unit_actions"]` supplies the executable MCP unit-action verb set. |

### Task 1: Capture protocol and committed action-space snapshot

**Files:**
- Create: `scripts/audit_civ6_capabilities.py`
- Create: `docs/research/civ6-action-space.json`
- Create: `tests/arena/test_capability_coverage.py`

**Interfaces:**
- Produces: `build_capture_lua() -> str`
- Produces: `parse_capture_lines(lines: Sequence[str]) -> dict[str, object]`
- Produces: `write_snapshot_atomic(snapshot: Mapping[str, object], path: Path) -> None`
- Produces: `async capture_action_space() -> dict[str, object]`
- Produces: `main(argv: Sequence[str] | None = None) -> int`
- Consumes later: Task 2 imports `parse_capture_lines`, `SNAPSHOT_PATH`, and the committed JSON.

- [x] **Step 1: Write failing parser and CLI-contract tests**

Add these imports and tests to `tests/arena/test_capability_coverage.py`:

```python
import json
from pathlib import Path

import pytest

from scripts.audit_civ6_capabilities import (
    parse_capture_lines,
    write_snapshot_atomic,
)


COMPLETE_CAPTURE = [
    "CAPABILITY|UnitOperations|UNITOPERATION_MOVE_TO",
    "CAPABILITY|UnitCommands|UNITCOMMAND_UPGRADE",
    "CAPABILITY|DiplomaticActions|DIPLOACTION_RESIDENT_EMBASSY",
]


def test_parse_capture_lines_builds_sorted_schema_v1_snapshot():
    snapshot = parse_capture_lines(reversed(COMPLETE_CAPTURE))

    assert snapshot == {
        "schema_version": 1,
        "tables": {
            "UnitOperations": ["UNITOPERATION_MOVE_TO"],
            "UnitCommands": ["UNITCOMMAND_UPGRADE"],
            "DiplomaticActions": ["DIPLOACTION_RESIDENT_EMBASSY"],
        },
    }


@pytest.mark.parametrize(
    "lines, message",
    [
        (COMPLETE_CAPTURE[:-1], "missing tables: DiplomaticActions"),
        (COMPLETE_CAPTURE + [COMPLETE_CAPTURE[0]], "duplicate action"),
        (
            COMPLETE_CAPTURE
            + ["CAPABILITY|CityOperations|CITYOPERATION_RANGE_ATTACK"],
            "unknown table",
        ),
        (COMPLETE_CAPTURE + ["CAPABILITY|UnitOperations"], "malformed record"),
        (
            [
                "CAPABILITY|UnitOperations|",
                COMPLETE_CAPTURE[1],
                COMPLETE_CAPTURE[2],
            ],
            "empty action",
        ),
    ],
)
def test_parse_capture_lines_rejects_partial_or_malformed_capture(lines, message):
    with pytest.raises(ValueError, match=message):
        parse_capture_lines(lines)


def test_write_snapshot_atomic_replaces_only_after_serialization(tmp_path):
    target = tmp_path / "snapshot.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    snapshot = parse_capture_lines(COMPLETE_CAPTURE)

    write_snapshot_atomic(snapshot, target)

    assert json.loads(target.read_text(encoding="utf-8")) == snapshot
    assert not list(tmp_path.glob("*.tmp"))
```

- [x] **Step 2: Run the focused tests and verify the expected import failure**

Run:

```bash
uv run --extra test pytest tests/arena/test_capability_coverage.py -q
```

Expected: collection fails because `scripts.audit_civ6_capabilities` does not exist.

- [x] **Step 3: Implement the capture parser and atomic writer**

Create `scripts/audit_civ6_capabilities.py` with these constants and core functions:

```python
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
from typing import Any

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
        "tables": {
            name: sorted(found[name])
            for name in TABLE_FIELDS
        },
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
```

The parser intentionally fails on unrelated output. A noisy or partial tuner read must preserve the old snapshot instead of silently accepting uncertain evidence.

- [x] **Step 4: Add the exact CLI mode contract**

In the same script, add:

```python
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
        counts = {
            name: len(actions)
            for name, actions in snapshot["tables"].items()
        }
        print("captured:", " ".join(f"{k}={v}" for k, v in counts.items()))
        return 0
    raise SystemExit(
        "offline report is added in Task 2; use --capture for the capture task"
    )


if __name__ == "__main__":
    raise SystemExit(main())
```

Add a test that `_build_parser().parse_args(["--capture", "--report"])` raises `SystemExit(2)` and `main(["--capture", "--json"])` raises `SystemExit(2)`.

- [x] **Step 5: Run parser tests**

Run:

```bash
uv run --extra test pytest tests/arena/test_capability_coverage.py -q
```

Expected: all Task 1 offline tests pass.

- [x] **Step 6: Perform the attended live capture**

Prerequisite: Civ 6 is running in a loaded game with FireTuner enabled and no other process owns the single tuner connection.

Run:

```bash
uv run python scripts/audit_civ6_capabilities.py --capture
```

Expected count summary:

```text
captured: UnitOperations=63 UnitCommands=28 DiplomaticActions=42
```

Inspect `git diff -- docs/research/civ6-action-space.json`. Confirm the three arrays are sorted, contain exactly 63/28/42 unique values, and the file has only `schema_version` plus `tables`.

- [x] **Step 7: Record `EMBARK` / `DISEMBARK` evidence**

Use an embarked-capable unit and the existing `move_unit` path for one land-to-water move and one water-to-land move. Record the exact observed result for Task 2:

- If both moves succeed through `build_move_unit` /
  `UnitManager.RequestOperation(unit, UnitOperationTypes.MOVE_TO, params)`,
  classify both as excluded with that evidence in each note.
- If either fails, or the live verification cannot be completed, classify both as missing at medium priority.

Do not add a dedicated embark tool in this plan.

- [x] **Step 8: Run the full arena suite**

Run:

```bash
uv run --extra test pytest tests/arena -q
```

Expected: all tests pass.

- [x] **Step 9: Commit Task 1**

```bash
git add scripts/audit_civ6_capabilities.py docs/research/civ6-action-space.json tests/arena/test_capability_coverage.py
git commit -m "feat(audit): capture Civ 6 action-space snapshot"
```

### Task 2: Hand-maintained coverage map, enforcement, and offline report

**Files:**
- Create: `src/civ_mcp/capability_map.py`
- Modify: `scripts/audit_civ6_capabilities.py`
- Modify: `tests/arena/test_capability_coverage.py`

**Interfaces:**
- Consumes: Task 1's schema-v1 snapshot and `SNAPSHOT_PATH`.
- Consumes: `TOOL_REGISTRY` and `collect_evidence()["mcp_unit_actions"]`.
- Produces: `CoverageStatus`, `MissingPriority`, `Coverage`, `ACTION_COVERAGE`.
- Produces: `validate_coverage(snapshot, coverage, *, arena_tools, unit_action_verbs) -> None`.
- Produces: `build_report_evidence(snapshot, coverage) -> dict[str, object]`.

- [x] **Step 1: Write failing synthetic validation tests**

Append:

```python
from civ_mcp.capability_map import Coverage, validate_coverage


SYNTHETIC_SNAPSHOT = {
    "schema_version": 1,
    "tables": {
        "UnitOperations": ["UNITOPERATION_MOVE_TO"],
        "UnitCommands": ["UNITCOMMAND_WAKE"],
        "DiplomaticActions": ["DIPLOACTION_RESIDENT_EMBASSY"],
    },
}


def test_validate_coverage_accepts_all_three_statuses():
    coverage = {
        "UNITOPERATION_MOVE_TO": Coverage("covered", tool="move_unit"),
        "UNITCOMMAND_WAKE": Coverage(
            "missing", priority="medium", note="Sleeping units need a wake action."
        ),
        "DIPLOACTION_RESIDENT_EMBASSY": Coverage(
            "excluded", note="Synthetic exclusion."
        ),
    }
    validate_coverage(
        SYNTHETIC_SNAPSHOT,
        coverage,
        arena_tools={"move_unit"},
        unit_action_verbs=set(),
    )


@pytest.mark.parametrize(
    "coverage, message",
    [
        ({}, "unclassified.*UNITOPERATION_MOVE_TO"),
        (
            {
                **{
                    action: Coverage("excluded", note="classified")
                    for actions in SYNTHETIC_SNAPSHOT["tables"].values()
                    for action in actions
                },
                "UNITCOMMAND_STALE": Coverage("excluded", note="stale"),
            },
            "stale.*UNITCOMMAND_STALE",
        ),
        (
            {
                "UNITOPERATION_MOVE_TO": Coverage(
                    "covered", tool="not_a_real_tool"
                ),
                "UNITCOMMAND_WAKE": Coverage(
                    "missing", priority="medium", note="wake"
                ),
                "DIPLOACTION_RESIDENT_EMBASSY": Coverage(
                    "excluded", note="synthetic"
                ),
            },
            "unknown covered tool",
        ),
    ],
)
def test_validate_coverage_rejects_invalid_maps(coverage, message):
    with pytest.raises(ValueError, match=message):
        validate_coverage(
            SYNTHETIC_SNAPSHOT,
            coverage,
            arena_tools={"move_unit"},
            unit_action_verbs={"repair"},
        )
```

Also add focused cases proving:

- `unit_action:repair` is accepted only when `repair` is in `unit_action_verbs`;
- missing entries require a valid priority and nonblank note;
- excluded entries require a nonblank note;
- covered entries require `tool` and reject `priority`;
- snapshot `schema_version != 1` is rejected.

- [x] **Step 2: Run the focused tests and verify the expected import failure**

Run:

```bash
uv run --extra test pytest tests/arena/test_capability_coverage.py -q
```

Expected: collection fails because `civ_mcp.capability_map` does not exist.

- [x] **Step 3: Implement the coverage model and validator**

Create `src/civ_mcp/capability_map.py`:

```python
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Literal


CoverageStatus = Literal["covered", "missing", "excluded"]
MissingPriority = Literal["high", "medium", "low"]
_STATUSES = {"covered", "missing", "excluded"}
_PRIORITIES = {"high", "medium", "low"}
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class Coverage:
    status: CoverageStatus
    tool: str | None = None
    priority: MissingPriority | None = None
    note: str | None = None


def _snapshot_actions(snapshot: Mapping[str, object]) -> set[str]:
    if snapshot.get("schema_version") != 1:
        raise ValueError("unsupported snapshot schema_version")
    tables = snapshot.get("tables")
    if not isinstance(tables, Mapping):
        raise ValueError("snapshot tables must be an object")
    return {
        action
        for values in tables.values()
        if isinstance(values, list)
        for action in values
        if isinstance(action, str)
    }


def validate_coverage(
    snapshot: Mapping[str, object],
    coverage: Mapping[str, Coverage],
    *,
    arena_tools: Set[str],
    unit_action_verbs: Set[str],
) -> None:
    actions = _snapshot_actions(snapshot)
    errors: list[str] = []
    unclassified = sorted(actions - coverage.keys())
    stale = sorted(coverage.keys() - actions)
    if unclassified:
        errors.append(
            "unclassified actions (edit capability_map.py): "
            + ", ".join(unclassified)
        )
    if stale:
        errors.append("stale coverage entries: " + ", ".join(stale))

    for action, item in sorted(coverage.items()):
        if item.status not in _STATUSES:
            errors.append(f"{action}: invalid status {item.status!r}")
        elif item.status == "covered":
            if not item.tool:
                errors.append(f"{action}: covered entry requires tool")
            elif item.tool.startswith("unit_action:"):
                verb = item.tool.partition(":")[2]
                if verb not in unit_action_verbs:
                    errors.append(f"{action}: unknown covered tool {item.tool}")
            elif item.tool not in arena_tools:
                errors.append(f"{action}: unknown covered tool {item.tool}")
            if item.priority is not None:
                errors.append(f"{action}: covered entry cannot have priority")
        elif item.status == "missing":
            if item.tool is not None:
                errors.append(f"{action}: missing entry cannot have tool")
            if item.priority not in _PRIORITIES:
                errors.append(f"{action}: missing entry requires valid priority")
            if not item.note or not item.note.strip():
                errors.append(f"{action}: missing entry requires note")
        elif item.status == "excluded":
            if item.tool is not None:
                errors.append(f"{action}: excluded entry cannot have tool")
            if not item.note or not item.note.strip():
                errors.append(f"{action}: excluded entry requires note")
            if item.priority is not None:
                errors.append(f"{action}: excluded entry cannot have priority")

    if errors:
        raise ValueError("\n".join(errors))
```

- [x] **Step 4: Populate the complete hand-maintained map**

In the same module, define one literal `ACTION_COVERAGE` entry for every action in the committed snapshot. Do not generate defaults or derive statuses from prefixes.

The following decisions are mandatory:

| Action | Classification |
|---|---|
| `UNITOPERATION_PILLAGE` | missing/high |
| `UNITOPERATION_DESIGNATE_PARK` | missing/high |
| `UNITOPERATION_TOURISM_BOMB` | missing/high |
| `UNITOPERATION_HARVEST_RESOURCE` | missing/medium |
| `UNITCOMMAND_GIFT` | missing/medium |
| `UNITOPERATION_WMD_STRIKE` | missing/low |
| `UNITCOMMAND_AIRLIFT` | missing/low |
| `UNITOPERATION_EXECUTE_SCRIPT` | excluded/debug-engine hook |
| `UNITCOMMAND_EXECUTE_SCRIPT` | excluded/debug-engine hook |
| `UNITOPERATION_WAIT_FOR` | excluded/engine-internal |
| `UNITOPERATION_MOVE_TO_UNIT` | excluded/engine-internal |
| `UNITCOMMAND_NAME_UNIT` | excluded/cosmetic |
| `UNITCOMMAND_PET_THE_DOG` | excluded/cosmetic |
| `UNITOPERATION_EMBARK`, `UNITOPERATION_DISEMBARK` | use Task 1's recorded evidence; missing/medium if unverified |

Map covered actions to the exact arena registry name when one exists, for example:

```python
ACTION_COVERAGE = {
    "UNITOPERATION_MOVE_TO": Coverage("covered", tool="move_unit"),
    "UNITOPERATION_BUILD_IMPROVEMENT": Coverage(
        "covered", tool="improve_tile"
    ),
    "UNITOPERATION_REPAIR": Coverage(
        "covered", tool="repair_improvement"
    ),
    "UNITCOMMAND_ACTIVATE_GREAT_PERSON": Coverage(
        "covered", tool="activate_great_person"
    ),
    "UNITCOMMAND_DELETE": Coverage(
        "covered", tool="unit_action:delete"
    ),
}
```

For every other action, inspect the actual registry/MCP implementation before marking it covered. Similar names are insufficient evidence: record `missing` when no existing call performs the action, and use `excluded` only for engine-internal, debug, cosmetic, or verified implicit behavior.

- [x] **Step 5: Add real-map enforcement and report-evidence tests**

Append:

```python
from civ_mcp.arena.registry import TOOL_REGISTRY
from civ_mcp.capability_map import ACTION_COVERAGE, build_report_evidence
from scripts.audit_arena_tool_coverage import collect_evidence
from scripts.audit_civ6_capabilities import SNAPSHOT_PATH


def _real_snapshot():
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def test_committed_snapshot_has_complete_valid_coverage():
    mcp_actions = set(collect_evidence()["mcp_unit_actions"])
    validate_coverage(
        _real_snapshot(),
        ACTION_COVERAGE,
        arena_tools=set(TOOL_REGISTRY),
        unit_action_verbs=mcp_actions,
    )


def test_report_evidence_is_counted_and_ranked():
    evidence = build_report_evidence(_real_snapshot(), ACTION_COVERAGE)

    assert evidence["counts"]["total"] == 133
    assert sum(
        evidence["counts"][status]
        for status in ("covered", "missing", "excluded")
    ) == 133
    priorities = {"high": 0, "medium": 1, "low": 2}
    assert evidence["missing"] == sorted(
        evidence["missing"],
        key=lambda row: (priorities[row["priority"]], row["action"]),
    )
```

Implement `build_report_evidence` with the stable `counts`/`missing` shape from the spec. It must not validate real tool names itself; `scripts/audit_civ6_capabilities.py` validates first with current registry evidence.

Use this implementation:

```python
def build_report_evidence(
    snapshot: Mapping[str, object],
    coverage: Mapping[str, Coverage],
) -> dict[str, object]:
    actions = _snapshot_actions(snapshot)
    counts = Counter(
        coverage[action].status
        for action in actions
        if action in coverage
    )
    missing = sorted(
        (
            {
                "action": action,
                "priority": item.priority,
                "note": item.note,
            }
            for action, item in coverage.items()
            if item.status == "missing"
        ),
        key=lambda row: (
            _PRIORITY_ORDER[row["priority"]],
            row["action"],
        ),
    )
    return {
        "counts": {
            "covered": counts["covered"],
            "missing": counts["missing"],
            "excluded": counts["excluded"],
            "total": len(actions),
        },
        "missing": missing,
    }
```

- [x] **Step 6: Implement offline human and JSON reporting**

Replace Task 1's offline `SystemExit` with:

```python
def _load_snapshot(path: Path = SNAPSHOT_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _validated_report() -> dict[str, object]:
    from civ_mcp.arena.registry import TOOL_REGISTRY
    from civ_mcp.capability_map import (
        ACTION_COVERAGE,
        build_report_evidence,
        validate_coverage,
    )

    snapshot = _load_snapshot()
    unit_actions = set(_arena_audit_evidence()["mcp_unit_actions"])
    validate_coverage(
        snapshot,
        ACTION_COVERAGE,
        arena_tools=set(TOOL_REGISTRY),
        unit_action_verbs=unit_actions,
    )
    return build_report_evidence(snapshot, ACTION_COVERAGE)


def _print_human(evidence: Mapping[str, object]) -> None:
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
```

In `main`, make no mode and `--report` call `_validated_report`; emit `json.dumps(evidence, sort_keys=True)` for `--json`, otherwise call `_print_human`.

- [x] **Step 7: Add subprocess CLI tests**

Add tests using `subprocess.run` from the repository root:

```python
import subprocess

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_report_cli_human_and_json():
    human = subprocess.run(
        ["uv", "run", "python", "scripts/audit_civ6_capabilities.py"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    assert "counts:" in human.stdout
    assert "UNITOPERATION_PILLAGE" in human.stdout

    machine = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/audit_civ6_capabilities.py",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    evidence = json.loads(machine.stdout)
    assert evidence["counts"]["total"] == 133
    assert evidence["missing"][0]["priority"] == "high"
```

- [x] **Step 8: Run focused tests and inspect the report**

Run:

```bash
uv run --extra test pytest tests/arena/test_capability_coverage.py -q
uv run python scripts/audit_civ6_capabilities.py --json
```

Expected: tests pass; JSON has `total=133`, internally consistent counts, and priority-sorted missing entries.

- [x] **Step 9: Run the full arena suite**

Run:

```bash
uv run --extra test pytest tests/arena -q
```

Expected: all tests pass.

- [x] **Step 10: Commit Task 2**

```bash
git add src/civ_mcp/capability_map.py scripts/audit_civ6_capabilities.py tests/arena/test_capability_coverage.py
git commit -m "feat(audit): enforce Civ 6 action coverage"
```

## Final Verification

- [x] Run:

```bash
uv run --extra test pytest tests/arena -q
uv run python scripts/audit_civ6_capabilities.py --report
uv run python scripts/audit_civ6_capabilities.py --json
git diff --check
git status --short
```

- [x] Confirm no missing gameplay verb was implemented, the snapshot has 133 unique actions, every action has exactly one explicit classification, and the tracked worktree is clean.
