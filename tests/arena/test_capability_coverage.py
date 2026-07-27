import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.audit_civ6_capabilities import (
    _build_parser,
    main,
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


def test_cli_rejects_conflicting_capture_and_report_modes():
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["--capture", "--report"])

    assert exc_info.value.code == 2


def test_cli_rejects_json_capture_mode():
    with pytest.raises(SystemExit) as exc_info:
        main(["--capture", "--json"])

    assert exc_info.value.code == 2
