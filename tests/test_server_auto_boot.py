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


async def test_kill_and_ocr_fallback_uses_the_complete_menu_load_path(monkeypatch):
    """Review of the 2026-09-26 loader commits: _navigate_to_save_sync now
    returns right after selecting the save, so the kill-and-OCR fallback
    must go through load_save_from_menu (navigation + load waiter) rather
    than call the sync navigator directly and hope the world appears
    within its 30 reconnect attempts."""
    _patch_common(monkeypatch, load_result="Loaded scenario: world ready, FireTuner port is open.")
    monkeypatch.setattr(game_launcher, "kill_game", AsyncMock(return_value="killed"))

    def boom(*_a, **_k):
        raise AssertionError("fallback must not call _navigate_to_save_sync directly")

    monkeypatch.setattr(game_launcher, "_navigate_to_save_sync", boom)
    menu_loads: list[tuple] = []

    async def fake_menu_load(save_name, *, launched_now=False):
        menu_loads.append((save_name, launched_now))
        return f"Loaded {save_name}: world ready, FireTuner port is open. Steps: ..."

    monkeypatch.setattr(game_launcher, "load_save_from_menu", fake_menu_load)
    # Wrong turn on first verify, wrong turn again after the Lua reload ->
    # the kill-and-OCR fallback fires.
    conn = _FakeConn(verify_turns=[157, 157])

    await server._auto_boot(conn, "scenario")

    assert menu_loads == [("scenario", True)]
