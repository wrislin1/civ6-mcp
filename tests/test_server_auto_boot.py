"""Tests for civ_mcp.server._auto_boot's wrong-save recovery block (F16a).

Since the frontend-Lua Tier-0/1 engaged path (game_launcher.
continue_after_lua_load) now blocks until the world is genuinely ready
before load_game_save ever returns, the wrong-save recovery block's
unconditional "sleep, then positional-click the leader screen" sequence is
a stray click landing inside an already-loaded world whenever the reload
already reports "world ready". These tests drive _auto_boot with fakes
(no live game) through the wrong-save recovery branch and assert the
positional click is skipped exactly when the reload result already
indicates the world is ready.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import civ_mcp.server as server
from civ_mcp import game_launcher, heartbeat


class _FakeConn:
    """Minimal GameConnection-shaped fake: gamecore_index flips truthy the
    moment connect()/reconnect() is called, and execute_read() returns a
    scripted VERIFY| line per call (wrong turn first, correct turn second)."""

    def __init__(self, verify_turns):
        self.gamecore_index = None
        self._verify_turns = iter(verify_turns)

    async def connect(self):
        self.gamecore_index = 1

    async def reconnect(self):
        self.gamecore_index = 1

    async def execute_read(self, lua_code):
        turn = next(self._verify_turns)
        return [f"VERIFY|{turn}", "---END---"]


def _patch_common(monkeypatch, *, load_result: str):
    """Patch every side-effecting dependency _auto_boot touches before it
    ever reaches the wrong-save recovery block, so the test runs instantly
    and touches no real filesystem/process/live game."""
    monkeypatch.setattr(heartbeat, "write", MagicMock())
    monkeypatch.setattr(game_launcher, "_launch_game_sync", MagicMock(return_value="ok"))
    monkeypatch.setattr(game_launcher, "_click_text", MagicMock(return_value=True))
    click_positional = MagicMock()
    monkeypatch.setattr(game_launcher, "_click_continue_positional", click_positional)

    load_game_save = AsyncMock(return_value=load_result)
    monkeypatch.setattr("civ_mcp.game_lifecycle.load_game_save", load_game_save)

    real_sleep = asyncio.sleep
    monkeypatch.setattr(server.asyncio, "sleep", lambda _s: real_sleep(0))

    return click_positional, load_game_save


@pytest.mark.asyncio
async def test_wrong_save_recovery_skips_stray_click_when_reload_reports_world_ready(monkeypatch):
    """F16a repro: a Lua reload result that already says "world ready" means
    the frontend-Lua engaged path already carried the load all the way to
    a playable world -- the subsequent sleep+positional-click+reconnect
    dance must be skipped, not fired as a stray click into the live
    world."""
    click_positional, _load = _patch_common(
        monkeypatch,
        load_result="Loaded scenario: world ready, FireTuner port is open.",
    )
    # First VERIFY (step 5) reports a wrong turn (>5) to enter the
    # recovery branch; the post-recovery VERIFY reports a correct turn.
    conn = _FakeConn(verify_turns=[157, 1])

    await server._auto_boot(conn, "scenario")

    click_positional.assert_not_called()


@pytest.mark.asyncio
async def test_wrong_save_recovery_skips_stray_click_for_unverified_world_ready(monkeypatch):
    """G3: the F16(b) stable-open-port fallback reports success text
    carrying an UNVERIFIED marker but still says "world ready" -- the
    wrong-save recovery's substring check must still treat it as
    reconnect-only (no positional click into an already-loaded world)."""
    click_positional, _load = _patch_common(
        monkeypatch,
        load_result=(
            "Loaded scenario (UNVERIFIED: port drop not observed -- likely "
            "faster than the poll interval): world ready, FireTuner port "
            "is open."
        ),
    )
    conn = _FakeConn(verify_turns=[157, 1])

    await server._auto_boot(conn, "scenario")

    click_positional.assert_not_called()


@pytest.mark.asyncio
async def test_wrong_save_recovery_still_clicks_when_reload_does_not_report_world_ready(monkeypatch):
    """The legacy quick-return path (a reload result that does NOT already
    say the world is ready) still needs the positional click through the
    leader screen -- this branch must be unaffected."""
    click_positional, _load = _patch_common(
        monkeypatch,
        load_result="Loaded scenario via OCR menu navigation.",
    )
    conn = _FakeConn(verify_turns=[157, 1])

    await server._auto_boot(conn, "scenario")

    click_positional.assert_called_once()
