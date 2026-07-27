import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import scripts.audit_civ6_capabilities as capability_audit
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
from civ_mcp.lua.units import (
    build_automate_explore,
    build_sacrifice_builder_charges,
)
from scripts.audit_arena_tool_coverage import collect_evidence


CAPTURE_COMPLETE_ACTION = "__MCP_CAPTURE_COMPLETE__"
COMPLETE_CAPTURE = [
    "CAPABILITY|UnitOperations|UNITOPERATION_MOVE_TO",
    "CAPABILITY|UnitCommands|UNITCOMMAND_UPGRADE",
    "CAPABILITY|DiplomaticActions|DIPLOACTION_RESIDENT_EMBASSY",
    f"CAPABILITY|DiplomaticActions|{CAPTURE_COMPLETE_ACTION}",
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
    assert lua.count("CAPABILITY|") == 4
    assert lua.rstrip().endswith(
        f'print("CAPABILITY|DiplomaticActions|{CAPTURE_COMPLETE_ACTION}")'
    )


def test_parse_capture_lines_builds_sorted_schema_v1_snapshot():
    snapshot = parse_capture_lines(
        [*reversed(COMPLETE_CAPTURE[:-1]), COMPLETE_CAPTURE[-1]]
    )

    assert snapshot == {
        "schema_version": 1,
        "tables": {
            "UnitOperations": ["UNITOPERATION_MOVE_TO"],
            "UnitCommands": ["UNITCOMMAND_UPGRADE"],
            "DiplomaticActions": ["DIPLOACTION_RESIDENT_EMBASSY"],
        },
    }


def test_parse_capture_lines_drops_injected_tuner_noise():
    """Debug output from LuaEvent callbacks must not abort a live capture.

    game_state._action_result documents the same injection and scans past it;
    build_capture_lua emits no sentinel, so execute_read always waits out its
    full timeout, widening the window for noise to arrive.
    """
    noisy = [
        "BulkHide: true",
        COMPLETE_CAPTURE[0],
        "[ShowIngameUI] hiding",
        *COMPLETE_CAPTURE[1:-1],
        COMPLETE_CAPTURE[-1],
        "LuaEvents.trailing debug",
    ]

    assert parse_capture_lines(noisy) == parse_capture_lines(COMPLETE_CAPTURE)


@pytest.mark.parametrize(
    "lines, message",
    [
        (
            COMPLETE_CAPTURE[:-2] + COMPLETE_CAPTURE[-1:],
            "missing tables: DiplomaticActions",
        ),
        (COMPLETE_CAPTURE[:-1], "capture completion marker"),
        (COMPLETE_CAPTURE + [COMPLETE_CAPTURE[-1]], "capture completion marker"),
        (
            COMPLETE_CAPTURE[:-1]
            + [
                f"CAPABILITY|DiplomaticActions|{CAPTURE_COMPLETE_ACTION}",
                "CAPABILITY|DiplomaticActions|DIPLOACTION_DECLARE_WAR",
            ],
            "final record",
        ),
        (
            COMPLETE_CAPTURE[:-1]
            + [COMPLETE_CAPTURE[0]]
            + COMPLETE_CAPTURE[-1:],
            "duplicate action",
        ),
        (
            COMPLETE_CAPTURE[:-1]
            + ["CAPABILITY|CityOperations|CITYOPERATION_RANGE_ATTACK"]
            + COMPLETE_CAPTURE[-1:],
            "unknown table",
        ),
        (
            COMPLETE_CAPTURE[:-1]
            + ["CAPABILITY|UnitOperations"]
            + COMPLETE_CAPTURE[-1:],
            "malformed record",
        ),
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


def test_capture_cli_leaves_existing_snapshot_untouched_without_completion(
    tmp_path, monkeypatch
):
    target = tmp_path / "snapshot.json"
    prior = '{"authoritative": true}\n'
    target.write_text(prior, encoding="utf-8")

    class PartialConnection:
        async def connect(self):
            pass

        async def execute_read(self, _lua):
            return COMPLETE_CAPTURE[:-1]

        async def disconnect(self):
            pass

    monkeypatch.setattr(capability_audit, "GameConnection", PartialConnection)
    original_writer = capability_audit.write_snapshot_atomic
    monkeypatch.setattr(
        capability_audit,
        "write_snapshot_atomic",
        lambda snapshot: original_writer(snapshot, target),
    )

    with pytest.raises(ValueError, match="capture completion marker"):
        main(["--capture"])

    assert target.read_text(encoding="utf-8") == prior


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


@pytest.mark.parametrize(
    "tables",
    [
        {
            "UnitOperations": ["UNITOPERATION_MOVE_TO"],
            "UnitCommands": ["UNITCOMMAND_WAKE"],
        },
        {
            **SYNTHETIC_SNAPSHOT["tables"],
            "CityOperations": ["CITYOPERATION_RANGE_ATTACK"],
        },
    ],
)
def test_validate_coverage_requires_exact_snapshot_tables(tables):
    snapshot = {**SYNTHETIC_SNAPSHOT, "tables": tables}

    with pytest.raises(ValueError, match="snapshot tables must contain exactly"):
        validate_coverage(
            snapshot,
            {},
            arena_tools=set(),
            unit_action_verbs=set(),
        )


@pytest.mark.parametrize(
    "unit_operations",
    [
        ("UNITOPERATION_MOVE_TO",),
        ["UNITOPERATION_MOVE_TO", 42],
    ],
)
def test_validate_coverage_requires_lists_of_string_actions(unit_operations):
    snapshot = {
        **SYNTHETIC_SNAPSHOT,
        "tables": {
            **SYNTHETIC_SNAPSHOT["tables"],
            "UnitOperations": unit_operations,
        },
    }

    with pytest.raises(
        ValueError,
        match="snapshot table UnitOperations must be a list of strings",
    ):
        validate_coverage(
            snapshot,
            {},
            arena_tools=set(),
            unit_action_verbs=set(),
        )


def _real_snapshot():
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "action, priority",
    [
        ("DIPLOACTION_DECLARE_GOLDEN_AGE_WAR", "low"),
        ("DIPLOACTION_DECLARE_IDEOLOGICAL_WAR", "low"),
        ("DIPLOACTION_DECLARE_WAR_MINOR_CIV", "low"),
        ("DIPLOACTION_DECLARE_WAR_OF_RETRIBUTION", "low"),
        ("DIPLOACTION_JOINT_WAR", "medium"),
        ("DIPLOACTION_RENEW_ALLIANCE", "medium"),
        ("DIPLOACTION_THIRD_PARTY_WAR", "medium"),
    ],
)
def test_unsupported_diplomatic_paths_are_classified_missing(action, priority):
    item = ACTION_COVERAGE[action]

    assert item.status == "missing"
    assert item.priority == priority
    assert item.tool is None
    assert item.note and item.note.strip()


def test_found_religion_operation_names_activating_tool():
    item = ACTION_COVERAGE["UNITOPERATION_FOUND_RELIGION"]

    assert item.status == "covered"
    assert item.tool == "activate_great_person"


def test_project_production_names_exact_royal_society_backend():
    item = ACTION_COVERAGE["UNITCOMMAND_PROJECT_PRODUCTION"]
    lua = build_sacrifice_builder_charges(7)

    assert item.status == "covered"
    assert item.tool == "unit_action:sacrifice_charges"
    assert 'GameInfo.UnitCommands["UNITCOMMAND_PROJECT_PRODUCTION"]' in lua
    assert "UnitManager.RequestCommand(unit, cmdHash" in lua


def test_automate_command_is_not_conflated_with_auto_explore_operation():
    item = ACTION_COVERAGE["UNITCOMMAND_AUTOMATE"]
    lua = build_automate_explore(7)

    assert item.status == "missing"
    assert item.priority == "low"
    assert item.tool is None
    assert item.note and "AUTOMATE_EXPLORE" in item.note
    assert 'GameInfo.UnitOperations["UNITOPERATION_AUTOMATE_EXPLORE"]' in lua
    assert "UNITCOMMAND_AUTOMATE" not in lua


@pytest.mark.parametrize(
    "action, priority",
    [
        ("UNITCOMMAND_MOVE_JUMP", "low"),
        ("UNITCOMMAND_PRIORITY_TARGET", "high"),
    ],
)
def test_player_visible_commands_are_ranked_missing(action, priority):
    item = ACTION_COVERAGE[action]

    assert item.status == "missing"
    assert item.priority == priority
    assert item.tool is None
    assert item.note and item.note.strip()


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

    assert evidence["counts"] == {
        "covered": 63,
        "missing": 59,
        "excluded": 11,
        "total": 133,
    }
    assert sum(
        evidence["counts"][status]
        for status in ("covered", "missing", "excluded")
    ) == 133
    priorities = {"high": 0, "medium": 1, "low": 2}
    assert evidence["missing"] == sorted(
        evidence["missing"],
        key=lambda row: (priorities[row["priority"]], row["action"]),
    )


def test_report_evidence_counts_and_rows_stay_in_the_same_scope():
    """Counts and the missing list must describe the same set of actions.

    build_report_evidence is public and does not validate, so a stale map
    entry (an action a game update removed from the snapshot) must not be
    counted in one place and listed in the other.
    """
    snapshot = {
        "schema_version": 1,
        "tables": {
            "UnitOperations": ["UNITOPERATION_PILLAGE"],
            "UnitCommands": [],
            "DiplomaticActions": [],
        },
    }
    coverage = {
        "UNITOPERATION_PILLAGE": Coverage(
            status="missing", priority="high", note="in snapshot"
        ),
        "UNITOPERATION_GONE": Coverage(
            status="missing", priority="low", note="removed by a game update"
        ),
    }

    evidence = build_report_evidence(snapshot, coverage)

    assert evidence["counts"]["missing"] == len(evidence["missing"]) == 1
    assert [row["action"] for row in evidence["missing"]] == [
        "UNITOPERATION_PILLAGE"
    ]


def test_report_evidence_rejects_an_unknown_priority():
    snapshot = {
        "schema_version": 1,
        "tables": {
            "UnitOperations": ["UNITOPERATION_PILLAGE"],
            "UnitCommands": [],
            "DiplomaticActions": [],
        },
    }
    coverage = {
        "UNITOPERATION_PILLAGE": Coverage(
            status="missing", priority="urgent", note="bad priority"
        )
    }

    with pytest.raises(ValueError, match="incomplete missing coverage entry"):
        build_report_evidence(snapshot, coverage)


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

    explicit = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/audit_civ6_capabilities.py",
            "--report",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    assert explicit.stdout == human.stdout

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
