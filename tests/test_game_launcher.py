"""Focused tests for the cross-platform Civ VI launcher."""

from __future__ import annotations

import builtins
import ctypes
import hashlib
import json
import os
import sys
import uuid
from types import ModuleType, SimpleNamespace

import pytest

from civ_mcp import game_launcher


class _FakeCFunction:
    """Callable that also accepts ctypes ``argtypes``/``restype`` metadata."""

    def __init__(self, callback):
        self._callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._callback(*args)


def test_windows_documents_dir_uses_redirected_known_folder(monkeypatch):
    requested_folder_ids: list[uuid.UUID] = []
    freed_paths: list[object] = []

    def known_folder_path(folder_id, _flags, _token, path_out):
        requested_folder_ids.append(
            uuid.UUID(bytes_le=ctypes.string_at(folder_id, 16))
        )
        path_out._obj.value = r"C:\Users\wrisl\OneDrive\Documents"
        return 0

    fake_windll = SimpleNamespace(
        shell32=SimpleNamespace(
            SHGetKnownFolderPath=_FakeCFunction(known_folder_path)
        ),
        ole32=SimpleNamespace(
            CoTaskMemFree=_FakeCFunction(lambda path: freed_paths.append(path))
        ),
    )
    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)

    assert game_launcher._get_windows_documents_dir() == (
        r"C:\Users\wrisl\OneDrive\Documents"
    )
    assert requested_folder_ids == [
        uuid.UUID("fdd39ad0-238f-46af-adb4-6c85480369c7")
    ]
    assert len(freed_paths) == 1


def test_windows_documents_dir_falls_back_when_known_folder_lookup_fails(
    monkeypatch,
):
    failing_lookup = _FakeCFunction(lambda *_args: 0x80004005)
    fake_windll = SimpleNamespace(
        shell32=SimpleNamespace(SHGetKnownFolderPath=failing_lookup),
        ole32=SimpleNamespace(CoTaskMemFree=_FakeCFunction(lambda _path: None)),
    )
    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)
    monkeypatch.setattr(
        game_launcher.os.path,
        "expanduser",
        lambda path: r"C:\Users\wrisl\Documents" if path == "~/Documents" else path,
    )

    assert game_launcher._get_windows_documents_dir() == r"C:\Users\wrisl\Documents"


def test_windows_save_base_is_built_below_known_documents(monkeypatch):
    monkeypatch.setattr(
        game_launcher,
        "_get_windows_documents_dir",
        lambda: r"C:\Users\wrisl\OneDrive\Documents",
    )

    assert game_launcher._windows_save_base() == (
        r"C:\Users\wrisl\OneDrive\Documents\My Games\Sid Meier's Civilization VI"
        r"\Saves\Single"
    )


def test_windows_gui_preflight_requires_pillow(monkeypatch):
    winrt_ocr = ModuleType("winrt.windows.media.ocr")
    winrt_ocr.OcrEngine = object
    for module_name in ("winrt", "winrt.windows", "winrt.windows.media"):
        monkeypatch.setitem(sys.modules, module_name, ModuleType(module_name))
    monkeypatch.setitem(sys.modules, "winrt.windows.media.ocr", winrt_ocr)
    monkeypatch.setitem(sys.modules, "win32gui", ModuleType("win32gui"))

    real_import = builtins.__import__

    def import_without_pillow(name, *args, **kwargs):
        if name == "PIL":
            raise ImportError("Pillow deliberately absent")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(game_launcher.sys, "platform", "win32")
    monkeypatch.setattr(builtins, "__import__", import_without_pillow)

    with pytest.raises(RuntimeError, match="Pillow"):
        game_launcher._require_gui_deps()


def test_windows_startup_cinematic_is_dismissed_after_fresh_launch(monkeypatch):
    window = SimpleNamespace(window_id=123, x=10, y=20, w=800, h=600)
    image = SimpleNamespace(getextrema=lambda: ((0, 255), (0, 240), (0, 250)))
    escape_presses: list[bool] = []

    monkeypatch.setattr(game_launcher.sys, "platform", "win32")
    monkeypatch.setattr(game_launcher, "_find_game_window_win32", lambda: window)
    monkeypatch.setattr(game_launcher, "_winrt_ocr_available", lambda: True, raising=False)
    monkeypatch.setattr(
        game_launcher, "_capture_window_win32", lambda _window_id: image
    )
    monkeypatch.setattr(game_launcher, "_ocr_winrt", lambda *_args: [])
    monkeypatch.setattr(
        game_launcher,
        "_press_escape_win32",
        lambda: escape_presses.append(True) or True,
        raising=False,
    )
    monkeypatch.setattr(game_launcher.time, "sleep", lambda _seconds: None)

    assert game_launcher._dismiss_startup_cinematic_win32(launched_now=True) is True
    assert escape_presses == [True]


def test_wait_for_text_retries_empty_results_hook_until_it_acts(monkeypatch):
    window = SimpleNamespace(pid=123)
    ocr_results = iter(
        [
            [],
            [],
            [],
            [("Single Player", 100, 100, 50, 20)],
        ]
    )
    hook_calls: list[bool] = []
    clock = [0.0]

    def now():
        clock[0] += 0.1
        return clock[0]

    def on_empty_results():
        hook_calls.append(True)
        return len(hook_calls) == 2

    monkeypatch.setattr(game_launcher.time, "time", now)
    monkeypatch.setattr(game_launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(game_launcher, "_require_gui_deps", lambda: None)
    monkeypatch.setattr(game_launcher, "_find_game_window", lambda: window)
    monkeypatch.setattr(game_launcher, "_bring_to_front", lambda **_kwargs: None)
    monkeypatch.setattr(
        game_launcher, "_ocr_game_window", lambda _window: next(ocr_results)
    )

    match = game_launcher._wait_for_text(
        "Single Player",
        timeout=5,
        exact=True,
        interval=0,
        on_empty_results=on_empty_results,
    )

    assert match == ("Single Player", 100, 100, 50, 20)
    assert hook_calls == [True, True]


def test_windows_startup_cinematic_is_not_dismissed_when_text_is_visible(
    monkeypatch,
):
    window = SimpleNamespace(window_id=123, x=10, y=20, w=800, h=600)
    image = SimpleNamespace(getextrema=lambda: ((0, 255), (0, 240), (0, 250)))
    escape_presses: list[bool] = []

    monkeypatch.setattr(game_launcher.sys, "platform", "win32")
    monkeypatch.setattr(game_launcher, "_find_game_window_win32", lambda: window)
    monkeypatch.setattr(game_launcher, "_winrt_ocr_available", lambda: True, raising=False)
    monkeypatch.setattr(
        game_launcher, "_capture_window_win32", lambda _window_id: image
    )
    monkeypatch.setattr(
        game_launcher,
        "_ocr_winrt",
        lambda *_args: [("Single Player", 100, 100, 50, 20)],
    )
    monkeypatch.setattr(
        game_launcher,
        "_press_escape_win32",
        lambda: escape_presses.append(True),
        raising=False,
    )

    assert game_launcher._dismiss_startup_cinematic_win32(launched_now=True) is False
    assert escape_presses == []


def test_windows_startup_cinematic_is_not_dismissed_for_existing_game(monkeypatch):
    window = SimpleNamespace(window_id=123, x=10, y=20, w=800, h=600)
    image = SimpleNamespace(getextrema=lambda: ((0, 255), (0, 240), (0, 250)))
    escape_presses: list[bool] = []

    monkeypatch.setattr(game_launcher.sys, "platform", "win32")
    monkeypatch.setattr(game_launcher, "_winrt_ocr_available", lambda: True, raising=False)
    monkeypatch.setattr(game_launcher, "_find_game_window_win32", lambda: window)
    monkeypatch.setattr(
        game_launcher, "_capture_window_win32", lambda _window_id: image
    )
    monkeypatch.setattr(game_launcher, "_ocr_winrt", lambda *_args: [])
    monkeypatch.setattr(
        game_launcher,
        "_press_escape_win32",
        lambda: escape_presses.append(True) or True,
        raising=False,
    )

    assert game_launcher._dismiss_startup_cinematic_win32(launched_now=False) is False
    assert escape_presses == []


def test_windows_startup_cinematic_is_not_dismissed_without_ocr_engine(monkeypatch):
    escape_presses: list[bool] = []

    monkeypatch.setattr(game_launcher.sys, "platform", "win32")
    monkeypatch.setattr(game_launcher, "_winrt_ocr_available", lambda: False, raising=False)
    monkeypatch.setattr(
        game_launcher,
        "_press_escape_win32",
        lambda: escape_presses.append(True) or True,
        raising=False,
    )
    monkeypatch.setattr(game_launcher.time, "sleep", lambda _seconds: None)

    assert game_launcher._dismiss_startup_cinematic_win32(launched_now=True) is False
    assert escape_presses == []


def test_windows_startup_cinematic_is_not_dismissed_for_blank_capture(monkeypatch):
    window = SimpleNamespace(window_id=123, x=10, y=20, w=800, h=600)
    image = SimpleNamespace(getextrema=lambda: ((0, 0), (0, 0), (0, 0)))
    escape_presses: list[bool] = []

    monkeypatch.setattr(game_launcher.sys, "platform", "win32")
    monkeypatch.setattr(game_launcher, "_find_game_window_win32", lambda: window)
    monkeypatch.setattr(game_launcher, "_winrt_ocr_available", lambda: True, raising=False)
    monkeypatch.setattr(
        game_launcher, "_capture_window_win32", lambda _window_id: image
    )
    monkeypatch.setattr(game_launcher, "_ocr_winrt", lambda *_args: [])
    monkeypatch.setattr(
        game_launcher,
        "_press_escape_win32",
        lambda: escape_presses.append(True) or True,
        raising=False,
    )
    monkeypatch.setattr(game_launcher.time, "sleep", lambda _seconds: None)

    assert game_launcher._dismiss_startup_cinematic_win32(launched_now=True) is False
    assert escape_presses == []


def test_windows_escape_is_not_injected_without_verified_game_focus(monkeypatch):
    key_events: list[tuple[int, int, int, int]] = []
    win32api = ModuleType("win32api")
    win32api.keybd_event = lambda *args: key_events.append(args)
    win32con = ModuleType("win32con")
    win32con.VK_ESCAPE = 27
    win32con.KEYEVENTF_KEYUP = 2

    monkeypatch.setitem(sys.modules, "win32api", win32api)
    monkeypatch.setitem(sys.modules, "win32con", win32con)
    monkeypatch.setattr(game_launcher, "_bring_to_front_win32", lambda: False)

    assert game_launcher._press_escape_win32() is False
    assert key_events == []


def test_windows_focus_uses_kernel_thread_id_and_verifies_foreground(monkeypatch):
    window = SimpleNamespace(window_id=123)
    attached: list[tuple[int, int, bool]] = []
    kernel_calls: list[bool] = []

    class FakeUser32:
        foreground = 456

        def GetForegroundWindow(self):
            return self.foreground

        def GetWindowThreadProcessId(self, _hwnd, _process_id):
            return 77

        def AttachThreadInput(self, current, foreground, attach):
            attached.append((current, foreground, bool(attach)))
            return True

    class FakeKernel32:
        def GetCurrentThreadId(self):
            kernel_calls.append(True)
            return 88

    user32 = FakeUser32()
    win32gui = ModuleType("win32gui")
    win32gui.IsIconic = lambda _hwnd: False
    win32gui.SetForegroundWindow = lambda hwnd: setattr(user32, "foreground", hwnd)

    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    monkeypatch.setattr(game_launcher, "_find_game_window_win32", lambda: window)
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=user32, kernel32=FakeKernel32()),
        raising=False,
    )

    assert game_launcher._bring_to_front_win32() is True
    assert kernel_calls == [True]
    assert attached == [(88, 77, True), (88, 77, False)]


def test_windows_focus_falls_back_to_wscript_app_activate(monkeypatch):
    window = SimpleNamespace(window_id=123, pid=7052)
    activated: list[int] = []

    class FakeUser32:
        foreground = 456

        def GetForegroundWindow(self):
            return self.foreground

        def GetWindowThreadProcessId(self, _hwnd, _process_id):
            return 77

        def AttachThreadInput(self, _current, _foreground, _attach):
            return True

    class FakeKernel32:
        def GetCurrentThreadId(self):
            return 88

    user32 = FakeUser32()
    win32gui = ModuleType("win32gui")
    win32gui.IsIconic = lambda _hwnd: False
    win32gui.SetForegroundWindow = lambda _hwnd: None
    win32com = ModuleType("win32com")
    win32com_client = ModuleType("win32com.client")

    def dispatch(name):
        assert name == "WScript.Shell"

        def app_activate(pid):
            activated.append(pid)
            user32.foreground = window.window_id
            return True

        return SimpleNamespace(AppActivate=app_activate)

    win32com_client.Dispatch = dispatch
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", win32com_client)
    monkeypatch.setattr(game_launcher, "_find_game_window_win32", lambda: window)
    monkeypatch.setattr(game_launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=user32, kernel32=FakeKernel32()),
        raising=False,
    )

    assert game_launcher._bring_to_front_win32() is True
    assert activated == [7052]


def test_navigation_checks_for_startup_cinematic_after_launch(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(game_launcher.sys, "platform", "win32")
    monkeypatch.setattr(game_launcher, "_require_gui_deps", lambda: None)
    monkeypatch.setattr(game_launcher, "is_game_running", lambda: False)
    monkeypatch.setattr(game_launcher, "_launch_game_sync", lambda: "Game launched")
    monkeypatch.setattr(
        game_launcher,
        "_dismiss_startup_cinematic_win32",
        lambda *, launched_now: calls.append(f"cinematic:{launched_now}"),
        raising=False,
    )
    def fake_click(text, **kwargs):
        calls.append(text)
        hook = kwargs.get("on_empty_results")
        if hook is not None:
            hook()
        return False

    monkeypatch.setattr(game_launcher, "_click_text", fake_click)

    result = game_launcher._navigate_to_save_sync("ANY_SAVE", tab=None)

    assert result.startswith("FAILED: Could not find 'Single Player'")
    assert calls == ["Single Player", "cinematic:True"]


def test_navigation_does_not_dismiss_cinematic_for_existing_game(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(game_launcher.sys, "platform", "win32")
    monkeypatch.setattr(game_launcher, "_require_gui_deps", lambda: None)
    monkeypatch.setattr(game_launcher, "is_game_running", lambda: True)
    monkeypatch.setattr(game_launcher, "_dismiss_crash_dialog", lambda: None)
    monkeypatch.setattr(game_launcher, "_click_aspyr_launcher_sync", lambda: None)
    monkeypatch.setattr(
        game_launcher,
        "_dismiss_startup_cinematic_win32",
        lambda *, launched_now: calls.append(f"cinematic:{launched_now}"),
        raising=False,
    )
    def fake_click(text, **kwargs):
        calls.append(text)
        hook = kwargs.get("on_empty_results")
        if hook is not None:
            hook()
        return False

    monkeypatch.setattr(game_launcher, "_click_text", fake_click)

    result = game_launcher._navigate_to_save_sync("ANY_SAVE", tab=None)

    assert result.startswith("FAILED: Could not find 'Single Player'")
    assert calls == ["Single Player"]


def test_navigation_uses_scrollable_save_search(monkeypatch):
    searched: list[str] = []

    monkeypatch.setattr(game_launcher, "_require_gui_deps", lambda: None)
    monkeypatch.setattr(game_launcher, "is_game_running", lambda: True)
    monkeypatch.setattr(game_launcher, "_dismiss_crash_dialog", lambda: None)
    monkeypatch.setattr(game_launcher, "_click_aspyr_launcher_sync", lambda: None)
    monkeypatch.setattr(game_launcher, "_click_text", lambda _text, **_kwargs: True)
    monkeypatch.setattr(
        game_launcher,
        "_wait_for_text",
        lambda _text, **_kwargs: ("Autosaves", 1, 1, 1, 1),
    )
    monkeypatch.setattr(
        game_launcher,
        "_click_save_in_scrollable_list",
        lambda save_name: searched.append(save_name) or False,
        raising=False,
    )

    result = game_launcher._navigate_to_save_sync("CHANNELS_GATE_V1_T157", tab=None)

    assert result.startswith("FAILED: Save 'CHANNELS_GATE_V1_T157' not found")
    assert searched == ["CHANNELS_GATE_V1_T157"]


def test_navigation_stops_when_load_game_screen_did_not_open(monkeypatch):
    searched: list[str] = []

    monkeypatch.setattr(game_launcher, "_require_gui_deps", lambda: None)
    monkeypatch.setattr(game_launcher, "is_game_running", lambda: True)
    monkeypatch.setattr(game_launcher, "_dismiss_crash_dialog", lambda: None)
    monkeypatch.setattr(game_launcher, "_click_aspyr_launcher_sync", lambda: None)
    monkeypatch.setattr(game_launcher, "_click_text", lambda _text, **_kwargs: True)
    monkeypatch.setattr(game_launcher, "_wait_for_text", lambda _text, **_kwargs: None)
    monkeypatch.setattr(
        game_launcher,
        "_click_save_in_scrollable_list",
        lambda save_name: searched.append(save_name) or False,
    )

    result = game_launcher._navigate_to_save_sync(
        "CHANNELS_GATE_V1_T157", tab=None
    )

    assert result.startswith("FAILED: Load Game screen did not open")
    assert searched == []


def test_windows_save_search_scrolls_until_target_is_visible(monkeypatch):
    clicks = iter([False, False, True])
    scrolls: list[bool] = []

    monkeypatch.setattr(game_launcher.sys, "platform", "win32")
    monkeypatch.setattr(
        game_launcher,
        "_click_text",
        lambda _text, **_kwargs: next(clicks),
    )
    monkeypatch.setattr(
        game_launcher,
        "_scroll_save_list_down_win32",
        lambda: scrolls.append(True) or True,
        raising=False,
    )

    assert game_launcher._click_save_in_scrollable_list("CHANNELS_GATE_V1_T157")
    assert scrolls == [True, True]


def test_windows_save_scroll_targets_list_and_sends_wheel_step(monkeypatch):
    cursor_positions: list[tuple[int, int]] = []
    wheel_events: list[tuple[int, int, int, int, int]] = []
    fake_api = ModuleType("win32api")
    fake_api.SetCursorPos = cursor_positions.append
    fake_api.mouse_event = lambda *args: wheel_events.append(args)
    fake_con = ModuleType("win32con")
    fake_con.MOUSEEVENTF_WHEEL = 0x0800
    fake_con.WHEEL_DELTA = 120

    monkeypatch.setattr(game_launcher.sys, "platform", "win32")
    monkeypatch.setattr(
        game_launcher,
        "_find_game_window_win32",
        lambda: game_launcher.WindowInfo(42, 10, 20, 1000, 800, 99),
    )
    monkeypatch.setattr(game_launcher, "_bring_to_front_win32", lambda: True)
    monkeypatch.setattr(game_launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setitem(sys.modules, "win32api", fake_api)
    monkeypatch.setitem(sys.modules, "win32con", fake_con)

    assert game_launcher._scroll_save_list_down_win32()
    assert cursor_positions == [(460, 516)]
    assert wheel_events == [(0x0800, 0, 0, -600, 0)]


@pytest.mark.asyncio
async def test_restart_and_load_preserves_fresh_launch_context(monkeypatch):
    load_calls: list[tuple[str | None, bool]] = []

    async def fake_dismiss():
        return []

    async def fake_kill():
        return "Game killed"

    async def fake_launch():
        return "Game launched"

    async def fake_load(save_name, *, launched_now=False):
        load_calls.append((save_name, launched_now))
        return "Save loading"

    monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", fake_dismiss)
    monkeypatch.setattr(game_launcher, "kill_game", fake_kill)
    monkeypatch.setattr(game_launcher, "launch_game", fake_launch)
    monkeypatch.setattr(game_launcher, "load_save_from_menu", fake_load)

    result = await game_launcher.restart_and_load("CHANNELS_GATE_V1_T157")

    assert result.endswith("Load: Save loading")
    assert load_calls == [("CHANNELS_GATE_V1_T157", True)]


def test_win32_input_struct_is_windows_abi_sized():
    """SendInput rejects any INPUT whose cbSize is not the Win64 ABI's 40
    bytes (error 87) — observed live 2026-08-29 when a KEYBDINPUT-only
    union was used. The shared definition must carry the full union."""
    assert hasattr(game_launcher, "_INPUT")
    assert ctypes.sizeof(game_launcher._INPUT) == 40
    fields = dict(game_launcher._KEYBDINPUT._fields_)
    assert "wVk" in fields and "dwExtraInfo" in fields


def test_windows_escape_sends_keydown_and_keyup_via_sendinput(monkeypatch):
    sendinput_calls: list[tuple[int, int]] = []

    class FakeUser32:
        def SendInput(self, n, _events, size):
            sendinput_calls.append((n, size))
            return n

        def MapVirtualKeyW(self, _vk, _map_type):
            return 1

    monkeypatch.setattr(
        ctypes, "windll",
        SimpleNamespace(user32=FakeUser32()),
        raising=False,
    )
    win32api = ModuleType("win32api")
    win32api.keybd_event = lambda *args: (_ for _ in ()).throw(
        AssertionError("legacy keybd_event must not be used")
    )
    win32con = ModuleType("win32con")
    win32con.VK_ESCAPE = 27
    win32con.KEYEVENTF_KEYUP = 2
    monkeypatch.setitem(sys.modules, "win32api", win32api)
    monkeypatch.setitem(sys.modules, "win32con", win32con)
    monkeypatch.setattr(game_launcher, "_bring_to_front_win32", lambda: True)
    monkeypatch.setattr(game_launcher.time, "sleep", lambda _s: None)

    assert game_launcher._press_escape_win32() is True
    assert sendinput_calls == [(2, 40)]   # keydown + keyup, correct cbSize


@pytest.mark.asyncio
async def test_continue_after_lua_load_presses_escape_until_world_ready(monkeypatch):
    """After a frontend Network.LoadGame the tuner port drops for the whole
    load and reopens only once the leader screen is dismissed (observed live
    2026-08-29). The helper must wait for the drop, press Escape while the
    port stays closed, stop the moment it reopens, and report success."""
    port_states = iter([True, False, False, False, True])
    seen: list[bool] = []

    def fake_port():
        state = next(port_states, True)
        seen.append(state)
        return state

    presses: list[bool] = []
    monkeypatch.setattr(game_launcher, "_is_tuner_port_open", fake_port)
    monkeypatch.setattr(
        game_launcher, "_press_escape", lambda: presses.append(True) or True,
        raising=False,
    )
    # Positive evidence of the leader/continue screen is what now gates a
    # press -- without it (e.g. UNKNOWN under WSL with no OCR access) the
    # waiter must never press blind on a poll-count cadence alone.
    monkeypatch.setattr(
        game_launcher,
        "_classify_frontend_load_state",
        lambda: game_launcher.FrontendLoadState.CONTINUE_SCREEN,
    )
    import asyncio as _asyncio
    real_sleep = _asyncio.sleep
    monkeypatch.setattr(_asyncio, "sleep", lambda _t: real_sleep(0))

    result = await game_launcher.continue_after_lua_load(
        "CHANNELS_GATE_V1_T157", press_every=1
    )

    assert presses          # pressed while the port was closed
    assert "FireTuner port is open" in result
    assert seen[-1] is True
    # The observed-drop path is a confirmed success -- no UNVERIFIED marker.
    assert "UNVERIFIED" not in result


@pytest.mark.asyncio
async def test_continue_after_lua_load_treats_a_stable_open_port_as_world_ready(monkeypatch):
    """F16(b) repro: GameConnection's own auto-reconnect can re-open the
    FireTuner port faster than continue_after_lua_load's poll interval, so
    the drop is never actually observed even though the load genuinely
    succeeded. A verified-open, STABLE port (confirmed by one more spaced
    check) must be treated as world-ready success rather than a false
    "port never dropped" warning."""
    monkeypatch.setattr(game_launcher, "_is_tuner_port_open", lambda: True)
    import asyncio as _asyncio
    real_sleep = _asyncio.sleep
    monkeypatch.setattr(_asyncio, "sleep", lambda _t: real_sleep(0))

    result = await game_launcher.continue_after_lua_load(
        "CHANNELS_GATE_V1_T157", engage_polls=3
    )

    assert "world ready" in result
    assert "WARNING" not in result
    # G3 ruling: this fallback is success-shaped text the launcher cannot
    # itself verify (no game connection) -- it must carry an UNVERIFIED
    # marker so the runner's reload_position can surface verified=False,
    # distinguishing it from the observed-drop success path.
    assert "UNVERIFIED" in result


@pytest.mark.asyncio
async def test_continue_after_lua_load_warns_when_load_never_engages(monkeypatch):
    """If the port never drops AND does not settle open either (it
    flickers closed again on the stability recheck), the world is
    genuinely not ready -- report a warning instead of pressing Escape at
    a live game."""
    presses: list[bool] = []
    # engage_polls=3 samples (all open, never observed dropping), then the
    # stability recheck's first sample (open) and second sample (closed
    # again -- a flicker, not a settled reopen).
    port_states = iter([True, True, True, True, False])
    monkeypatch.setattr(game_launcher, "_is_tuner_port_open", lambda: next(port_states, False))
    monkeypatch.setattr(
        game_launcher, "_press_escape", lambda: presses.append(True) or True,
        raising=False,
    )
    import asyncio as _asyncio
    real_sleep = _asyncio.sleep
    monkeypatch.setattr(_asyncio, "sleep", lambda _t: real_sleep(0))

    result = await game_launcher.continue_after_lua_load(
        "CHANNELS_GATE_V1_T157", engage_polls=3
    )

    assert presses == []
    assert "WARNING" in result


@pytest.mark.asyncio
async def test_continue_after_lua_load_presses_escape_only_on_recognized_continue_screen(
    monkeypatch,
):
    """Live-observed regression: the old waiter pressed Escape purely on a
    poll-count cadence, with no idea what was actually on screen. It must
    now press only when an injected classifier gives positive evidence of
    the continue/leader screen."""
    port_states = iter([True, False, False, False, True])
    presses: list[bool] = []
    monkeypatch.setattr(
        game_launcher, "_is_tuner_port_open", lambda: next(port_states, True)
    )
    monkeypatch.setattr(
        game_launcher, "_press_escape", lambda: presses.append(True) or True,
        raising=False,
    )
    monkeypatch.setattr(
        game_launcher,
        "_classify_frontend_load_state",
        lambda: game_launcher.FrontendLoadState.CONTINUE_SCREEN,
    )
    import asyncio as _asyncio
    real_sleep = _asyncio.sleep
    monkeypatch.setattr(_asyncio, "sleep", lambda _t: real_sleep(0))

    result = await game_launcher.continue_after_lua_load(
        "CHANNELS_GATE_V1_T157", press_every=1
    )

    assert presses  # pressed while the classifier reported CONTINUE_SCREEN
    assert "world ready" in result


@pytest.mark.asyncio
async def test_continue_after_lua_load_never_presses_escape_in_world(monkeypatch):
    """An IN_WORLD classification must permanently disarm the waiter, even
    if the FireTuner port itself has not yet reopened (the classifier's
    own evidence is independent proof)."""
    presses: list[bool] = []
    monkeypatch.setattr(game_launcher, "_is_tuner_port_open", lambda: False)
    monkeypatch.setattr(
        game_launcher, "_press_escape", lambda: presses.append(True) or True,
        raising=False,
    )
    monkeypatch.setattr(
        game_launcher,
        "_classify_frontend_load_state",
        lambda: game_launcher.FrontendLoadState.IN_WORLD,
    )
    import asyncio as _asyncio
    real_sleep = _asyncio.sleep
    monkeypatch.setattr(_asyncio, "sleep", lambda _t: real_sleep(0))

    result = await game_launcher.continue_after_lua_load(
        "CHANNELS_GATE_V1_T157", engage_polls=2, world_polls=3, press_every=1
    )

    assert presses == []
    assert "WARNING" in result


@pytest.mark.asyncio
async def test_continue_after_lua_load_never_presses_escape_when_tuner_is_open(
    monkeypatch,
):
    """An open FireTuner port must win over a stale/wrong classifier
    answer -- it is checked, and can return success, before the
    classifier is ever consulted."""
    import itertools

    port_states = itertools.chain([True, False], itertools.repeat(True))
    presses: list[bool] = []
    classify_calls: list[None] = []
    monkeypatch.setattr(game_launcher, "_is_tuner_port_open", lambda: next(port_states))
    monkeypatch.setattr(
        game_launcher, "_press_escape", lambda: presses.append(True) or True,
        raising=False,
    )

    def fake_classify():
        classify_calls.append(None)
        return game_launcher.FrontendLoadState.CONTINUE_SCREEN

    monkeypatch.setattr(game_launcher, "_classify_frontend_load_state", fake_classify)
    import asyncio as _asyncio
    real_sleep = _asyncio.sleep
    monkeypatch.setattr(_asyncio, "sleep", lambda _t: real_sleep(0))

    result = await game_launcher.continue_after_lua_load(
        "CHANNELS_GATE_V1_T157", press_every=1
    )

    assert presses == []
    assert classify_calls == []
    assert "world ready" in result
    assert "WARNING" not in result


@pytest.mark.asyncio
async def test_continue_after_lua_load_waits_on_unknown_screen(monkeypatch):
    """UNKNOWN carries no positive evidence either way -- it must only
    poll until timeout, never pressing Escape at a live game it cannot
    confirm the state of."""
    presses: list[bool] = []
    monkeypatch.setattr(game_launcher, "_is_tuner_port_open", lambda: False)
    monkeypatch.setattr(
        game_launcher, "_press_escape", lambda: presses.append(True) or True,
        raising=False,
    )
    monkeypatch.setattr(
        game_launcher,
        "_classify_frontend_load_state",
        lambda: game_launcher.FrontendLoadState.UNKNOWN,
    )
    import asyncio as _asyncio
    real_sleep = _asyncio.sleep
    monkeypatch.setattr(_asyncio, "sleep", lambda _t: real_sleep(0))

    result = await game_launcher.continue_after_lua_load(
        "CHANNELS_GATE_V1_T157", engage_polls=2, world_polls=3, press_every=1
    )

    assert presses == []
    assert "WARNING" in result
    assert "unknown" in result


def test_press_escape_uses_windows_bridge_from_wsl(monkeypatch):
    """Under WSL (sys.platform == 'linux') the game runs on Windows: Escape
    must be delivered by the Windows companion checkout's signed Python via
    the launcher bootstrap, and only when both are present."""
    runs: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        runs.append(list(cmd))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(game_launcher.sys, "platform", "linux")
    monkeypatch.setattr(game_launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(game_launcher.os.path, "exists", lambda _p: True)

    assert game_launcher._press_escape() is True
    assert len(runs) == 1
    assert runs[0][-1] == "press-escape"

    runs.clear()
    monkeypatch.setattr(game_launcher.os.path, "exists", lambda _p: False)
    assert game_launcher._press_escape() is False
    assert runs == []


@pytest.mark.parametrize(
    "bridge_state,expected",
    [
        ("continue_screen", game_launcher.FrontendLoadState.CONTINUE_SCREEN),
        ("leader_screen", game_launcher.FrontendLoadState.LEADER_SCREEN),
        ("in_world", game_launcher.FrontendLoadState.IN_WORLD),
        ("unknown", game_launcher.FrontendLoadState.UNKNOWN),
    ],
)
def test_classify_frontend_load_state_bridges_through_windows_companion_on_linux(
    monkeypatch, bridge_state, expected
):
    """Under WSL the game window is not visible to this process -- the
    classifier must delegate to the same Windows companion checkout used
    by `_press_escape_windows_bridge`, running WinRT OCR natively and
    reporting the result back as JSON."""
    runs: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        runs.append(list(cmd))
        return SimpleNamespace(
            returncode=0, stdout=(json.dumps({"state": bridge_state}) + "\n").encode("utf-8"), stderr=b""
        )

    monkeypatch.setattr(game_launcher.sys, "platform", "linux")
    monkeypatch.setattr(game_launcher, "_is_tuner_port_open", lambda: False)
    monkeypatch.setattr(game_launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(game_launcher.os.path, "exists", lambda _p: True)

    assert game_launcher._classify_frontend_load_state() is expected
    [cmd] = runs
    assert cmd[-2] == "classify-frontend"
    assert cmd[-1] == "--json"


@pytest.mark.parametrize(
    "break_bridge",
    [
        "missing",       # bridge python/bootstrap not present
        "raises",        # subprocess.run itself raises
        "nonzero_exit",  # bridge ran but reported failure
        "bad_json",      # stdout isn't parseable JSON
        "bad_state",     # JSON has an unrecognized state string
    ],
)
def test_classify_frontend_load_state_bridge_failure_maps_to_unknown(
    monkeypatch, break_bridge
):
    """Every bridge failure mode must fail closed to UNKNOWN -- never to a
    pressable state. A broken or unreachable bridge must never be
    mistaken for evidence that it's safe to press Escape."""
    monkeypatch.setattr(game_launcher.sys, "platform", "linux")
    monkeypatch.setattr(game_launcher, "_is_tuner_port_open", lambda: False)

    if break_bridge == "missing":
        monkeypatch.setattr(game_launcher.os.path, "exists", lambda _p: False)
        assert game_launcher._classify_frontend_load_state() is game_launcher.FrontendLoadState.UNKNOWN
        return

    monkeypatch.setattr(game_launcher.os.path, "exists", lambda _p: True)

    def fake_run(cmd, **kwargs):
        if break_bridge == "raises":
            raise TimeoutError("bridge timed out")
        if break_bridge == "nonzero_exit":
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom")
        if break_bridge == "bad_json":
            return SimpleNamespace(returncode=0, stdout=b"not json\n", stderr=b"")
        if break_bridge == "bad_state":
            return SimpleNamespace(
                returncode=0,
                stdout=(json.dumps({"state": "on_fire"}) + "\n").encode("utf-8"),
                stderr=b"",
            )
        raise AssertionError(break_bridge)

    monkeypatch.setattr(game_launcher.subprocess, "run", fake_run)

    assert game_launcher._classify_frontend_load_state() is game_launcher.FrontendLoadState.UNKNOWN


@pytest.mark.asyncio
async def test_continue_after_lua_load_bridged_continue_screen_presses_escape_exactly_once(
    monkeypatch,
):
    """End-to-end proof through the real WSL bridge plumbing (fake
    subprocess only): a bridged CONTINUE_SCREEN answer must lead to
    exactly one Escape press before the port reopens."""
    port_states = iter([True, False, False, True])
    presses: list[bool] = []
    monkeypatch.setattr(game_launcher, "_is_tuner_port_open", lambda: next(port_states, True))
    monkeypatch.setattr(
        game_launcher, "_press_escape", lambda: presses.append(True) or True,
        raising=False,
    )
    monkeypatch.setattr(game_launcher.sys, "platform", "linux")
    monkeypatch.setattr(game_launcher.os.path, "exists", lambda _p: True)

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(json.dumps({"state": "continue_screen"}) + "\n").encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(game_launcher.subprocess, "run", fake_run)
    import asyncio as _asyncio
    real_sleep = _asyncio.sleep
    monkeypatch.setattr(_asyncio, "sleep", lambda _t: real_sleep(0))

    result = await game_launcher.continue_after_lua_load(
        "CHANNELS_GATE_V1_T157", press_every=1
    )

    assert presses == [True]
    assert "world ready" in result


@pytest.mark.asyncio
async def test_continue_after_lua_load_bridge_failure_never_presses_escape(monkeypatch):
    """A broken bridge must never be treated as evidence -- the waiter
    should time out with no Escape sent rather than press blind."""
    presses: list[bool] = []
    monkeypatch.setattr(game_launcher, "_is_tuner_port_open", lambda: False)
    monkeypatch.setattr(
        game_launcher, "_press_escape", lambda: presses.append(True) or True,
        raising=False,
    )
    monkeypatch.setattr(game_launcher.sys, "platform", "linux")
    monkeypatch.setattr(game_launcher.os.path, "exists", lambda _p: True)

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(game_launcher.subprocess, "run", fake_run)
    import asyncio as _asyncio
    real_sleep = _asyncio.sleep
    monkeypatch.setattr(_asyncio, "sleep", lambda _t: real_sleep(0))

    result = await game_launcher.continue_after_lua_load(
        "CHANNELS_GATE_V1_T157", engage_polls=2, world_polls=3, press_every=1
    )

    assert presses == []
    assert "WARNING" in result
    assert "unknown" in result


# ---------------------------------------------------------------------------
# Benchmark save deployment
# ---------------------------------------------------------------------------


def test_deploy_benchmark_save_atomically_replaces_and_verifies(monkeypatch, tmp_path):
    source = tmp_path / "archive.Civ6Save"
    source.write_bytes(b"canonical")
    saves = tmp_path / "Single"
    saves.mkdir()
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(saves))
    digest = hashlib.sha256(b"canonical").hexdigest()

    result = game_launcher.deploy_benchmark_save(source, "BUILDER_ECONOMY_CAL_V1", digest)

    assert (saves / "BUILDER_ECONOMY_CAL_V1.Civ6Save").read_bytes() == b"canonical"
    assert result["deployed_sha256"] == digest
    assert result["archive_sha256"] == digest
    # No stray temp file left behind after the atomic replace.
    assert list(saves.iterdir()) == [saves / "BUILDER_ECONOMY_CAL_V1.Civ6Save"]


def test_deploy_benchmark_save_rejects_source_hash_mismatch(monkeypatch, tmp_path):
    source = tmp_path / "archive.Civ6Save"
    source.write_bytes(b"canonical")
    saves = tmp_path / "Single"
    saves.mkdir()
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(saves))
    wrong_digest = hashlib.sha256(b"not canonical").hexdigest()

    with pytest.raises(ValueError, match="source hash mismatch"):
        game_launcher.deploy_benchmark_save(source, "NAME", wrong_digest)

    assert list(saves.iterdir()) == []


@pytest.mark.parametrize(
    "unsafe_name",
    ["../escape", "with space", "trailing/slash", "semi;colon", "dotted.name"],
)
def test_deploy_benchmark_save_rejects_unsafe_save_names(
    monkeypatch, tmp_path, unsafe_name
):
    source = tmp_path / "archive.Civ6Save"
    source.write_bytes(b"canonical")
    saves = tmp_path / "Single"
    saves.mkdir()
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(saves))
    digest = hashlib.sha256(b"canonical").hexdigest()

    with pytest.raises(ValueError, match="unsafe save name"):
        game_launcher.deploy_benchmark_save(source, unsafe_name, digest)

    assert list(saves.iterdir()) == []


def test_deploy_benchmark_save_fails_closed_on_post_copy_mismatch(monkeypatch, tmp_path):
    source = tmp_path / "archive.Civ6Save"
    source.write_bytes(b"canonical")
    saves = tmp_path / "Single"
    saves.mkdir()
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(saves))
    digest = hashlib.sha256(b"canonical").hexdigest()

    real_hash = game_launcher._sha256_file
    calls = {"n": 0}

    def flaky_hash(path):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_hash(path)  # source hash checks out
        return "0" * 64  # simulate corruption introduced by the copy itself

    monkeypatch.setattr(game_launcher, "_sha256_file", flaky_hash)

    with pytest.raises(ValueError, match="deployed hash mismatch"):
        game_launcher.deploy_benchmark_save(source, "NAME", digest)

    # The atomic replace must never have happened, and no temp file survives.
    assert list(saves.iterdir()) == []


# ---------------------------------------------------------------------------
# Boot health
# ---------------------------------------------------------------------------


class _FakeMonotonicClock:
    """Deterministic stand-in for time.monotonic(), advanced by fake sleeps."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _install_fake_clock(monkeypatch):
    clock = _FakeMonotonicClock()
    monkeypatch.setattr(game_launcher.time, "monotonic", clock)
    return clock


def _frame_row(frame: int) -> str:
    """A real native Profile.csv frame-summary row, e.g.:

    ``[2026-08-30 10:00:57]\t,----- FRAME: 0 time: 159.87ms Moving avg: 2.50ms 1 frames since last ``

    Sampled from the live file at
    ``AppData/Local/Firaxis Games/Sid Meier's Civilization VI/Logs/Profile.csv``.
    """
    return (
        "[2026-08-30 10:00:57]\t,----- FRAME: "
        f"{frame} time: 159.87ms Moving avg: 2.50ms 1 frames since last \r\n"
    )


def _profiling_row(name: str = "UIManager_Update", ms: str = "144.91") -> str:
    """A real native Profile.csv per-call profiling row -- no frame counter
    (most rows in the real file look like this); must be skipped for frame
    evidence but still consumed as bytes."""
    return f"[2026-08-30 10:00:57]\t,            {name}, {ms} ms\r\n"


def test_wait_for_boot_health_passes_once_a_fresh_row_exceeds_min_frame(
    monkeypatch, tmp_path
):
    profile = tmp_path / "Profile.csv"
    profile.write_text("")
    clock = _install_fake_clock(monkeypatch)
    writes = [_frame_row(3), _frame_row(5), _frame_row(150)]

    def fake_sleep(_seconds):
        clock.advance(game_launcher._BOOT_HEALTH_POLL_INTERVAL_S)
        if writes:
            with open(profile, "a") as fh:
                fh.write(writes.pop(0))

    monkeypatch.setattr(game_launcher.time, "sleep", fake_sleep)

    result = game_launcher.wait_for_boot_health(
        str(profile), start_offset=0, min_frame=100, timeout_s=60
    )

    assert result["ok"] is True
    assert result["reason"] is None
    assert result["last_frame"] == 150
    assert result["baseline_offset"] == 0
    assert result["profile_path"] == str(profile)
    assert result["file_identity"] is not None


def test_wait_for_boot_health_ignores_stale_pre_offset_rows(monkeypatch, tmp_path):
    """Counterfactual: a pre-existing healthy row from a *previous* session
    must never count. If the implementation ignored start_offset and read
    from byte 0 instead, this would incorrectly pass with last_frame=500."""
    profile = tmp_path / "Profile.csv"
    profile.write_text(_frame_row(500))
    start_offset = profile.stat().st_size
    clock = _install_fake_clock(monkeypatch)

    def fake_sleep(_seconds):
        clock.advance(game_launcher._BOOT_HEALTH_POLL_INTERVAL_S)

    monkeypatch.setattr(game_launcher.time, "sleep", fake_sleep)

    result = game_launcher.wait_for_boot_health(
        str(profile), start_offset=start_offset, min_frame=100, timeout_s=6
    )

    assert result["ok"] is False
    assert result["reason"] == "timeout"
    assert result["last_frame"] is None


def test_wait_for_boot_health_times_out_on_low_frame_stall(monkeypatch, tmp_path):
    profile = tmp_path / "Profile.csv"
    profile.write_text("")
    clock = _install_fake_clock(monkeypatch)
    writes = [_frame_row(3), _frame_row(5)]

    def fake_sleep(_seconds):
        clock.advance(game_launcher._BOOT_HEALTH_POLL_INTERVAL_S)
        if writes:
            with open(profile, "a") as fh:
                fh.write(writes.pop(0))

    monkeypatch.setattr(game_launcher.time, "sleep", fake_sleep)

    result = game_launcher.wait_for_boot_health(
        str(profile), start_offset=0, min_frame=100, timeout_s=6
    )

    assert result["ok"] is False
    assert result["reason"] == "timeout"
    assert result["last_frame"] == 5


def test_wait_for_boot_health_fails_closed_on_malformed_rows(monkeypatch, tmp_path):
    profile = tmp_path / "Profile.csv"
    profile.write_text("")
    clock = _install_fake_clock(monkeypatch)

    def fake_sleep(_seconds):
        clock.advance(game_launcher._BOOT_HEALTH_POLL_INTERVAL_S)
        with open(profile, "a") as fh:
            fh.write(_profiling_row("SerialEvent::QuerySaveGames", "32.64") + "garbled \xff not a frame row\r\n")

    monkeypatch.setattr(game_launcher.time, "sleep", fake_sleep)

    result = game_launcher.wait_for_boot_health(
        str(profile), start_offset=0, min_frame=100, timeout_s=4
    )

    assert result["ok"] is False
    assert result["reason"] == "timeout"
    assert result["last_frame"] is None


def test_wait_for_boot_health_fails_closed_on_log_rotation(monkeypatch, tmp_path):
    profile = tmp_path / "Profile.csv"
    profile.write_text(_frame_row(3))
    clock = _install_fake_clock(monkeypatch)
    rotated = {"done": False}

    def fake_sleep(_seconds):
        clock.advance(game_launcher._BOOT_HEALTH_POLL_INTERVAL_S)
        if not rotated["done"]:
            # Simulate rotation the way real log rotation works: a distinct
            # file is created and swapped into place, guaranteeing a fresh
            # inode (a plain unlink+recreate can reuse the just-freed inode
            # number on some filesystems, masking the rotation).
            replacement = tmp_path / "Profile.csv.new"
            replacement.write_text(_frame_row(0))
            os.replace(replacement, profile)
            rotated["done"] = True

    monkeypatch.setattr(game_launcher.time, "sleep", fake_sleep)

    result = game_launcher.wait_for_boot_health(
        str(profile), start_offset=0, min_frame=100, timeout_s=60
    )

    assert result["ok"] is False
    assert result["reason"] == "log_rotated"
    # Detected well before the 60s deadline -- fails closed immediately.
    assert result["elapsed_s"] < 10


def test_wait_for_boot_health_fails_closed_on_log_truncation(monkeypatch, tmp_path):
    profile = tmp_path / "Profile.csv"
    profile.write_text(_frame_row(3) + _frame_row(5))
    start_offset = profile.stat().st_size
    clock = _install_fake_clock(monkeypatch)

    def fake_sleep(_seconds):
        clock.advance(game_launcher._BOOT_HEALTH_POLL_INTERVAL_S)
        with open(profile, "r+b") as fh:
            fh.truncate(0)  # in-place truncation: same inode, smaller size

    monkeypatch.setattr(game_launcher.time, "sleep", fake_sleep)

    result = game_launcher.wait_for_boot_health(
        str(profile), start_offset=start_offset, min_frame=100, timeout_s=60
    )

    assert result["ok"] is False
    assert result["reason"] == "log_truncated"
    assert result["elapsed_s"] < 10


def test_wait_for_boot_health_does_not_let_a_later_regressed_row_in_the_same_batch_undo_a_pass(
    monkeypatch, tmp_path
):
    """Counterfactual for an implementation that inspects only the last row
    of a newly-appended batch rather than scanning in order: two complete
    rows can land in a single read (150 then a lower 3, e.g. a counter reset
    logged in the same flush). The frame that already exceeded min_frame
    must decide the outcome, not whatever the batch ends with -- an
    implementation that tracked only the batch's last frame would time out
    here instead of passing, since ``3`` never exceeds ``100``."""
    profile = tmp_path / "Profile.csv"
    profile.write_text("")
    clock = _install_fake_clock(monkeypatch)

    def fake_sleep(_seconds):
        clock.advance(game_launcher._BOOT_HEALTH_POLL_INTERVAL_S)
        with open(profile, "a") as fh:
            fh.write(_frame_row(150) + _frame_row(3))

    monkeypatch.setattr(game_launcher.time, "sleep", fake_sleep)

    result = game_launcher.wait_for_boot_health(
        str(profile), start_offset=0, min_frame=100, timeout_s=10
    )

    assert result["ok"] is True
    assert result["last_frame"] == 150


def test_profile_csv_path_uses_local_appdata_firaxis_logs(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\wrisl\AppData\Local")

    path = game_launcher._profile_csv_path()

    assert path == os.path.join(
        r"C:\Users\wrisl\AppData\Local",
        "Firaxis Games",
        "Sid Meier's Civilization VI",
        "Logs",
        "Profile.csv",
    )
