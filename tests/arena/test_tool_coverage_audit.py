import ast
import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "audit_arena_tool_coverage.py"


def _audit_module():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import audit_arena_tool_coverage

        return audit_arena_tool_coverage
    finally:
        sys.path.pop(0)


def test_unit_action_scan_reads_or_patterns():
    # An or-pattern would otherwise drop its verbs silently, and the audit's
    # headline claim is an empty "absent from CLAUDE.md" list.
    audit = _audit_module()
    tree = ast.parse(
        "async def unit_action(ctx, unit_id, action):\n"
        "    match action.lower():\n"
        '        case "sleep" | "alert":\n'
        "            pass\n"
        '        case "skip":\n'
        "            pass\n"
    )

    assert audit._unit_actions(tree) == {"sleep", "alert", "skip"}


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


def test_tier_action_verbs_are_raw_and_reachability_aware():
    evidence = _audit_module().collect_evidence()
    absent = evidence["tier_action_verbs_absent"]

    assert "improve" in absent["minimal"]
    assert "activate_great_person" in absent["minimal"]
    assert "improve" not in absent["standard"]
    assert "activate_great_person" not in absent["standard"]
    assert "activate" not in absent["minimal"]


def test_checked_in_audit_tracks_gp_tier_rows():
    text = (
        REPO_ROOT / "docs" / "research" / "arena-tool-coverage-audit.md"
    ).read_text(encoding="utf-8")

    assert "standard=28" in text
    assert (
        "| `get_great_people` | no | intentionally-excluded | "
        "yes | present | yes |"
    ) in text
    assert (
        "| `activate_great_person` | no | intentionally-excluded | "
        "yes | present | yes |"
    ) in text
