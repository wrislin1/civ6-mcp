"""Focused tests for the cross-platform Civ VI launcher."""

from __future__ import annotations

import builtins
import ctypes
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
        "_click_save_in_scrollable_list",
        lambda save_name: searched.append(save_name) or False,
        raising=False,
    )

    result = game_launcher._navigate_to_save_sync("CHANNELS_GATE_V1_T157", tab=None)

    assert result.startswith("FAILED: Save 'CHANNELS_GATE_V1_T157' not found")
    assert searched == ["CHANNELS_GATE_V1_T157"]


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
