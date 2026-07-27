import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "audit_arena_tool_coverage.py"


def test_coverage_audit_accounts_for_callbacks_and_executable_unit_actions():
    result = subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT), "--json"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert {"dismiss_popup", "list_saves"} <= set(evidence["mcp_gamestate_methods"])
    assert evidence["mcp_unit_actions"] == [
        "activate",
        "alert",
        "attack",
        "automate",
        "build_route",
        "delete",
        "fortify",
        "found_city",
        "heal",
        "improve",
        "move",
        "remove_feature",
        "remove_improvement",
        "repair",
        "sacrifice_charges",
        "skip",
        "sleep",
        "spread_religion",
        "teleport",
        "trade_route",
    ]
    assert evidence["mcp_gamestate_methods_absent_from_arena"] == [
        "build_route",
        "check_game_over",
        "delete_unit",
        "dismiss_popup",
        "end_turn",
        "execute_lua",
        "get_diary_snapshot",
        "get_game_identity",
        "get_threat_scan",
        "list_saves",
        "load_game_save",
        "load_save",
        "remove_improvement",
        "sacrifice_builder_charges",
        "sleep_unit",
        "submit_congress",
    ]
