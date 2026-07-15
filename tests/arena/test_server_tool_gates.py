from civ_mcp.server import _tools_removed_for_env


def test_disable_lua_env_removes_only_run_lua():
    assert _tools_removed_for_env({"CIV_MCP_DISABLE_LUA": "1"}) == ("run_lua",)


def test_arena_puppet_env_removes_lifecycle_and_lua_tools():
    removed = set(_tools_removed_for_env({"CIV_MCP_ARENA_PUPPET": "1"}))
    assert {
        "end_turn",
        "kill_game",
        "load_game_save",
        "restart_and_load",
        "load_save",
        "load_save_from_menu",
        "launch_game",
        "run_lua",
    } <= removed


# ---------------------------------------------------------------------------
# Task 4 — the focused blocker-repair pass reuses the SAME CLI environment as
# a normal turn (no weaker toolset). Reassert both lockdown layers this task
# must not touch: the CLIAgentPolicy denylist still names end_turn, and the
# exact env dict CLIAgentPolicy injects into the spawned subprocess still
# drives the server-side removal of end_turn/run_lua/lifecycle tools.
# ---------------------------------------------------------------------------

def test_cli_agent_denylist_still_names_end_turn():
    from civ_mcp.arena.cli_agent import _DENIED_CIV6_TOOLS

    assert "mcp__civ6__end_turn" in _DENIED_CIV6_TOOLS


def test_cli_agent_server_env_still_drives_server_side_removal():
    from civ_mcp.arena.cli_agent import _SERVER_ENV

    removed = set(_tools_removed_for_env(_SERVER_ENV))
    assert {
        "end_turn",
        "kill_game",
        "load_game_save",
        "restart_and_load",
        "load_save",
        "load_save_from_menu",
        "launch_game",
        "run_lua",
    } <= removed
