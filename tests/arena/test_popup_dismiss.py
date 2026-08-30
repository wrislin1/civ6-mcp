"""Blocking engine-popup dismissal.

Live failure (2026-08-30, run arena-channels-behavior-v7): Gathering Storm
disaster cinematics and Historic Moment / Inspiration popups block the end
turn while the blocker radar reads empty, so seat 0 went `human_pending` with
`blockers=[]` at T165/T169 (and T176 mid-queue) until an operator pressed ESC.
The dismiss pass closes those known popup contexts from Lua instead.
"""
import asyncio

from civ_mcp import lua as lq
from civ_mcp.arena.coordinator import _dismiss_blocking_popups


class PopupRecordingConn:
    def __init__(self, lines=None):
        self.write_calls = []
        self._lines = lines or ["POPUPS|none"]

    async def execute_write(self, lua, timeout=5.0):
        self.write_calls.append(lua)
        return self._lines


def test_build_dismiss_blocking_popups_lua_shape():
    """The dismiss lua targets exactly the popup contexts observed blocking
    live runs, dequeues and hides them, and reports under POPUPS|."""
    lua = lq.build_dismiss_blocking_popups()
    for context in (
        "NaturalDisasterPopup",   # XP2 disaster cinematic (v7 T165/T169/T175)
        "HistoricMoments",        # era moment timeline (v7 T165 queue)
        "BoostUnlockedPopup",     # Inspiration/Eureka popup (v7 T166 queue)
    ):
        assert context in lua
    assert "DequeuePopup" in lua
    assert "SetHide" in lua
    assert "POPUPS|" in lua
    # Force-hiding NaturalDisasterPopup mid-cinematic orphans the disaster
    # camera (observed live: v8 T159 — black world, dead ESC, end turn
    # suppressed). The dismisser must replicate the popup's own Close()
    # restore path, not merely hide the context.
    for restore_call in (
        "StopAllCameraAnimations",
        "InterfaceModeTypes.SELECTION",
        "UILens.RestoreActiveLens",
        "NaturalDisasterPopup_Closed",
        "ClearTemporaryPlotVisibility",
    ):
        assert restore_call in lua, restore_call


def test_dismiss_blocking_popups_reports_and_swallows_errors():
    """Best-effort contract, mirroring _sweep_orphan_sessions."""

    class _Boom:
        async def execute_write(self, lua, timeout=5.0):
            raise ConnectionError("dead socket")

    assert asyncio.run(_dismiss_blocking_popups(_Boom())) == "err"

    conn = PopupRecordingConn(lines=["POPUPS|NaturalDisasterPopup"])
    assert asyncio.run(_dismiss_blocking_popups(conn)) == "POPUPS|NaturalDisasterPopup"


class ContextCloseConn(PopupRecordingConn):
    """Conn whose tuner state list exposes popup contexts (live: v8 T159
    showed 135 in-game states with NaturalDisasterPopup at index 73)."""

    def __init__(self, states, in_state_lines=None):
        super().__init__()
        self.lua_states = dict(states)
        self.state_calls = []
        self._in_state_lines = in_state_lines or {}

    async def execute_in_state(self, state_index, lua, timeout=5.0):
        self.state_calls.append((state_index, lua))
        return self._in_state_lines.get(state_index, ["HIDDEN"])


def test_dismiss_prefers_context_native_close():
    """Hiding NaturalDisasterPopup from InGame leaks its PopupManager engine
    hold (UI.ReferenceCurrentEvent) and wedges the interturn at PLEASE WAIT
    (observed live: v8 T159, event id 1640). The dismisser must call each
    visible popup context's own close function in that context instead."""
    conn = ContextCloseConn(
        states={5: "InGame", 73: "NaturalDisasterPopup", 90: "BoostUnlockedPopup"},
        in_state_lines={73: ["PopupManager.Unlock 'NaturalDisasterPopup'", "CLOSED"],
                        90: ["HIDDEN"]},
    )
    result = asyncio.run(_dismiss_blocking_popups(conn))
    assert result == "POPUPS|NaturalDisasterPopup"
    assert conn.write_calls == []          # never fell back to the InGame hide
    by_idx = dict(conn.state_calls)
    assert "Close()" in by_idx[73]
    assert "OnClose()" in by_idx[90]       # boost popup's closer is OnClose
    assert "IsHidden" in by_idx[73]        # only closes visible contexts
    assert 5 not in by_idx                 # InGame itself is never a target


def test_dismiss_falls_back_to_ingame_hide_without_states():
    """A conn without per-state execution (or with no matching contexts)
    keeps the v7-era InGame dequeue+hide+restore path."""
    conn = PopupRecordingConn(lines=["POPUPS|HistoricMoments"])
    assert asyncio.run(_dismiss_blocking_popups(conn)) == "POPUPS|HistoricMoments"
    assert len(conn.write_calls) == 1

    empty = ContextCloseConn(states={5: "InGame"})
    empty._lines = ["POPUPS|none"]
    assert asyncio.run(_dismiss_blocking_popups(empty)) == "POPUPS|none"
    assert len(empty.write_calls) == 1     # fallback ran the builder
