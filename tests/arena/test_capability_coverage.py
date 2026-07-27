import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.audit_civ6_capabilities import (
    SNAPSHOT_PATH,
    _build_parser,
    build_capture_lua,
    main,
    parse_capture_lines,
    write_snapshot_atomic,
)
from civ_mcp.arena.registry import TOOL_REGISTRY
from civ_mcp.capability_map import (
    ACTION_COVERAGE,
    Coverage,
    build_report_evidence,
    validate_coverage,
)
from scripts.audit_arena_tool_coverage import collect_evidence


COMPLETE_CAPTURE = [
    "CAPABILITY|UnitOperations|UNITOPERATION_MOVE_TO",
    "CAPABILITY|UnitCommands|UNITCOMMAND_UPGRADE",
    "CAPABILITY|DiplomaticActions|DIPLOACTION_RESIDENT_EMBASSY",
]

SYNTHETIC_SNAPSHOT = {
    "schema_version": 1,
    "tables": {
        "UnitOperations": ["UNITOPERATION_MOVE_TO"],
        "UnitCommands": ["UNITCOMMAND_WAKE"],
        "DiplomaticActions": ["DIPLOACTION_RESIDENT_EMBASSY"],
    },
}


def test_build_capture_lua_queries_all_action_tables_and_fields():
    lua = build_capture_lua()

    assert "GameInfo.UnitOperations()" in lua
    assert "row.OperationType" in lua
    assert "GameInfo.UnitCommands()" in lua
    assert "row.CommandType" in lua
    assert "GameInfo.DiplomaticActions()" in lua
    assert "row.DiplomaticActionType" in lua
    assert lua.count("CAPABILITY|") == 3


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


def test_cli_rejects_conflicting_capture_and_report_modes():
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["--capture", "--report"])

    assert exc_info.value.code == 2


def test_cli_rejects_json_capture_mode():
    with pytest.raises(SystemExit) as exc_info:
        main(["--capture", "--json"])

    assert exc_info.value.code == 2


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


def test_validate_coverage_accepts_known_unit_action_verb():
    coverage = {
        "UNITOPERATION_MOVE_TO": Coverage(
            "covered", tool="unit_action:repair"
        ),
        "UNITCOMMAND_WAKE": Coverage("excluded", note="synthetic"),
        "DIPLOACTION_RESIDENT_EMBASSY": Coverage(
            "excluded", note="synthetic"
        ),
    }

    validate_coverage(
        SYNTHETIC_SNAPSHOT,
        coverage,
        arena_tools=set(),
        unit_action_verbs={"repair"},
    )


def test_validate_coverage_rejects_unknown_unit_action_verb():
    coverage = {
        "UNITOPERATION_MOVE_TO": Coverage(
            "covered", tool="unit_action:repair"
        ),
        "UNITCOMMAND_WAKE": Coverage("excluded", note="synthetic"),
        "DIPLOACTION_RESIDENT_EMBASSY": Coverage(
            "excluded", note="synthetic"
        ),
    }

    with pytest.raises(ValueError, match="unknown covered tool"):
        validate_coverage(
            SYNTHETIC_SNAPSHOT,
            coverage,
            arena_tools=set(),
            unit_action_verbs=set(),
        )


@pytest.mark.parametrize(
    "item, message",
    [
        (Coverage("missing", note="reason"), "valid priority"),
        (Coverage("missing", priority="urgent", note="reason"), "valid priority"),
        (Coverage("missing", priority="low", note=" "), "requires note"),
        (Coverage("excluded"), "requires note"),
        (Coverage("excluded", note="reason", priority="low"), "cannot have priority"),
        (Coverage("covered"), "requires tool"),
        (
            Coverage("covered", tool="move_unit", priority="low"),
            "cannot have priority",
        ),
    ],
)
def test_validate_coverage_enforces_status_fields(item, message):
    coverage = {
        "UNITOPERATION_MOVE_TO": item,
        "UNITCOMMAND_WAKE": Coverage("excluded", note="synthetic"),
        "DIPLOACTION_RESIDENT_EMBASSY": Coverage(
            "excluded", note="synthetic"
        ),
    }

    with pytest.raises(ValueError, match=message):
        validate_coverage(
            SYNTHETIC_SNAPSHOT,
            coverage,
            arena_tools={"move_unit"},
            unit_action_verbs=set(),
        )


def test_validate_coverage_rejects_unknown_snapshot_schema():
    snapshot = {**SYNTHETIC_SNAPSHOT, "schema_version": 2}

    with pytest.raises(ValueError, match="unsupported snapshot schema_version"):
        validate_coverage(
            snapshot,
            {},
            arena_tools=set(),
            unit_action_verbs=set(),
        )


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
