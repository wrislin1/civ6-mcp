"""load_game_save regressions from the 2026-07-15 live hang recovery.

Two live failures on the gaming PC (WSL Python driving the WINDOWS build):
1. The Lua tier was gated on `sys.platform != "linux"` -- where PYTHON runs,
   not where the game runs -- so the working Lua load path was skipped and
   the filesystem tier died on a WSL path that does not exist.
2. The engine's file list reports names WITH the .Civ6Save extension
   (observed: 205 entries, all `*.Civ6Save`), and the matcher compared
   against the bare name only, so every lookup returned NOT_FOUND.
"""

import asyncio

import pytest

from civ_mcp.game_lifecycle import load_game_save


class LoadConn:
    """Scripted conn for the Lua load tier: query registration, then check
    polls, then post-FOUND verification polls."""

    def __init__(self, write_results):
        self.writes: list[str] = []
        self._results = list(write_results)

    async def execute_write(self, lua, timeout=5.0):
        self.writes.append(lua)
        if not self._results:
            return []
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def execute_read(self, lua, timeout=5.0):
        return []


@pytest.mark.asyncio
async def test_lua_tier_matches_extensioned_names_even_on_linux(monkeypatch):
    """The registration Lua must accept both the bare save name and the
    .Civ6Save-extensioned form, and must run regardless of sys.platform."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _t: real_sleep(0))

    async def fake_continue(name):
        return f"Loaded {name}: world ready, FireTuner port is open."

    from civ_mcp import game_launcher
    monkeypatch.setattr(game_launcher, "continue_after_lua_load", fake_continue)
    conn = LoadConn([
        ["QUERY_SENT"],           # handler registration
        ["RESULT|FOUND"],         # first check poll
        ["WIPED"],                # verification: Lua state wiped -> load real
    ])

    result = await load_game_save(conn, "0_MCP_0306")

    assert "world ready" in result
    registration = conn.writes[0]
    assert '"0_MCP_0306"' in registration
    assert '"0_MCP_0306.Civ6Save"' in registration


@pytest.mark.asyncio
async def test_ingame_lua_tier_hands_engaged_load_to_continue_helper(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _t: real_sleep(0))
    continued: list[str] = []

    async def fake_continue(name):
        continued.append(name)
        return f"Loaded {name}: world ready, FireTuner port is open."

    from civ_mcp import game_launcher
    monkeypatch.setattr(game_launcher, "continue_after_lua_load", fake_continue)
    conn = LoadConn([["QUERY_SENT"], ["RESULT|FOUND"], ["WIPED"]])

    result = await load_game_save(conn, "0_MCP_0306")

    assert continued == ["0_MCP_0306"]
    assert "world ready" in result


@pytest.mark.asyncio
async def test_ingame_lua_tier_connection_drop_after_found_hands_to_continue_helper(
    monkeypatch,
):
    """Contexts tearing down after FOUND means the load is genuinely in
    progress -- that counts as engagement and must hand off to the continue
    helper exactly once, rather than reporting a raw success string."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _t: real_sleep(0))
    continued: list[str] = []

    async def fake_continue(name):
        continued.append(name)
        return f"Loaded {name}: world ready, FireTuner port is open."

    from civ_mcp import game_launcher
    monkeypatch.setattr(game_launcher, "continue_after_lua_load", fake_continue)
    conn = LoadConn([
        ["QUERY_SENT"],
        ["RESULT|FOUND"],
        ConnectionError("contexts gone"),   # verification poll
    ])

    result = await load_game_save(conn, "0_MCP_0306")

    assert continued == ["0_MCP_0306"]
    assert "world ready" in result


@pytest.mark.asyncio
async def test_lua_tier_found_but_inert_falls_through(monkeypatch):
    """The Aspyr Linux port prints FOUND but Network.LoadGame silently does
    nothing: the Lua state is never wiped, so the loader must NOT claim
    success -- it falls through to the tier-2 path (which reports the save
    unfindable in this test environment), and must never invoke the
    continue helper."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _t: real_sleep(0))
    continued: list[str] = []

    async def fake_continue(name):
        continued.append(name)
        return "should not run"

    from civ_mcp import game_launcher
    monkeypatch.setattr(game_launcher, "continue_after_lua_load", fake_continue)
    conn = LoadConn(
        [["QUERY_SENT"], ["RESULT|FOUND"]]
        + [["STILL_SET"]] * 80          # verification never wipes
    )

    result = await load_game_save(conn, "0_MCP_0306")

    assert "Loading save" not in result
    assert continued == []


class MenuConn(LoadConn):
    """LoadConn that is sitting at the main menu: no GameCore/InGame states,
    frontend states only, with execute_in_state scripted separately."""

    def __init__(self, state_results, write_results=()):
        super().__init__(write_results)
        self.gamecore_index = None
        self.ingame_index = None
        self.lua_states = {0: "Main State", 5: "LoadGameMenu", 30: "FrontEnd"}
        self.state_calls: list[tuple[int, str]] = []
        self._state_results = list(state_results)

    async def execute_in_state(self, state_index, lua, timeout=5.0):
        self.state_calls.append((state_index, lua))
        if not self._state_results:
            return []
        result = self._state_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_frontend_tier_loads_from_main_menu(monkeypatch):
    """At the main menu the InGame tier cannot run (no GameCore/InGame
    states), but the LoadGameMenu frontend state can query and fire
    Network.LoadGame directly (proven live 2026-08-29). The loader must use
    it instead of OCR menu navigation, then hand off to the continue helper
    that dismisses the leader screen."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _t: real_sleep(0))
    continues: list[str] = []

    async def fake_continue(save_name):
        continues.append(save_name)
        return "Loaded CHANNELS_GATE_V1_T157: world ready, FireTuner port open."

    from civ_mcp import game_launcher
    monkeypatch.setattr(
        game_launcher, "continue_after_lua_load", fake_continue, raising=False
    )
    conn = MenuConn(state_results=[
        ["QUERY_SENT"],          # frontend handler registration + query
        ["RESULT|FOUND"],        # poll: matched and Network.LoadGame fired
    ])

    result = await load_game_save(conn, "CHANNELS_GATE_V1_T157")

    assert continues == ["CHANNELS_GATE_V1_T157"]
    assert "world ready" in result
    assert conn.writes == []                 # never touched the InGame tier
    assert conn.state_calls and conn.state_calls[0][0] == 5
    registration = conn.state_calls[0][1]
    assert "Network.LoadGame" in registration
    assert '"CHANNELS_GATE_V1_T157"' in registration
    assert '"CHANNELS_GATE_V1_T157.Civ6Save"' in registration


@pytest.mark.asyncio
async def test_frontend_tier_not_found_falls_through(monkeypatch):
    """A save the frontend query cannot match must fall through to the
    filesystem tier (which reports it unfindable here), never to the
    continue helper."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _t: real_sleep(0))
    continues: list[str] = []

    async def fake_continue(save_name):
        continues.append(save_name)
        return "should not run"

    from civ_mcp import game_launcher
    monkeypatch.setattr(
        game_launcher, "continue_after_lua_load", fake_continue, raising=False
    )
    import os.path
    monkeypatch.setattr(os.path, "exists", lambda _p: False)
    conn = MenuConn(state_results=[["QUERY_SENT"], ["RESULT|NOT_FOUND"]])

    result = await load_game_save(conn, "NO_SUCH_SAVE")

    assert continues == []
    assert "not found" in result


# ---------------------------------------------------------------------------
# 2026-09-26 live authoring session (BUILDER_ECONOMY_CAL_V1): a cold
# UI.QuerySaveGameList over a 283-save OneDrive library took longer than the
# 5s poll window. load_game_save reported "not found", but the Lua handler
# stayed registered and fired Network.LoadGame ~1 minute later -- a stray
# reload the caller never learned about. A warm re-query took ~1s.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingame_lua_tier_waits_through_a_slow_file_list_query(monkeypatch):
    """A file-list result that lands after the old 20-poll window (5s) but
    within 30s must still be honoured as FOUND."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _t: real_sleep(0))
    continued: list[str] = []

    async def fake_continue(name):
        continued.append(name)
        return f"Loaded {name}: world ready, FireTuner port is open."

    from civ_mcp import game_launcher
    monkeypatch.setattr(game_launcher, "continue_after_lua_load", fake_continue)
    conn = LoadConn(
        [["QUERY_SENT"]] + [["PENDING"]] * 60 + [["RESULT|FOUND"], ["WIPED"]]
    )

    result = await load_game_save(conn, "BUILDER_ECONOMY_CAL_V1")

    assert continued == ["BUILDER_ECONOMY_CAL_V1"]
    assert "world ready" in result


@pytest.mark.asyncio
async def test_ingame_lua_tier_cancels_the_handler_when_the_window_expires(
    monkeypatch,
):
    """If no result arrives inside the window, the loader must disarm the Lua
    handler before falling through, so a late result cannot fire
    Network.LoadGame behind the caller's back."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _t: real_sleep(0))

    async def fake_continue(name):
        raise AssertionError("continue helper must not run on a timed-out query")

    from civ_mcp import game_launcher
    monkeypatch.setattr(game_launcher, "continue_after_lua_load", fake_continue)
    import os.path
    monkeypatch.setattr(os.path, "exists", lambda _p: False)
    conn = LoadConn([["QUERY_SENT"]] + [["PENDING"]] * 500)

    result = await load_game_save(conn, "BUILDER_ECONOMY_CAL_V1")

    assert "not found" in result
    registration = conn.writes[0]
    # The handler must consult a cancel flag before acting on a late result.
    assert "ExposedMembers.MCPLoadCancelled" in registration
    assert "MCPLoadCancelled = false" in registration
    # And the loader must raise that flag once it gives up waiting.
    assert any("MCPLoadCancelled = true" in w for w in conn.writes[1:])
    # Bounded: 120 polls (30s at 0.25s) + registration + cancel, not unbounded.
    assert len(conn.writes) <= 123


@pytest.mark.asyncio
async def test_frontend_tier_cancels_the_handler_when_the_window_expires(
    monkeypatch,
):
    """Same disarm rule for the main-menu (LoadGameMenu) tier."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _t: real_sleep(0))

    async def fake_continue(save_name):
        raise AssertionError("continue helper must not run on a timed-out query")

    from civ_mcp import game_launcher
    monkeypatch.setattr(
        game_launcher, "continue_after_lua_load", fake_continue, raising=False
    )
    import os.path
    monkeypatch.setattr(os.path, "exists", lambda _p: False)
    conn = MenuConn(state_results=[["QUERY_SENT"]] + [["PENDING"]] * 500)

    result = await load_game_save(conn, "BUILDER_ECONOMY_CAL_V1")

    assert "not found" in result
    registration = conn.state_calls[0][1]
    assert "MCP_FE_CANCELLED" in registration
    assert any("MCP_FE_CANCELLED = true" in lua for _, lua in conn.state_calls[1:])
    assert len(conn.state_calls) <= 123
