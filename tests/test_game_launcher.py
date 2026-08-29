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


def test_windows_startup_cinematic_retries_transient_blank_frame(monkeypatch):
    window = SimpleNamespace(window_id=123, x=10, y=20, w=800, h=600)
    blank = SimpleNamespace(getextrema=lambda: ((0, 0), (0, 0), (0, 0)))
    cinematic = SimpleNamespace(
        getextrema=lambda: ((0, 255), (0, 240), (0, 250))
    )
    frames = iter([blank, cinematic])
    captures: list[object] = []
    escape_presses: list[bool] = []

    def capture(_window_id):
        image = next(frames)
        captures.append(image)
        return image

    monkeypatch.setattr(game_launcher.sys, "platform", "win32")
    monkeypatch.setattr(game_launcher, "_find_game_window_win32", lambda: window)
    monkeypatch.setattr(game_launcher, "_winrt_ocr_available", lambda: True)
    monkeypatch.setattr(game_launcher, "_capture_window_win32", capture)
    monkeypatch.setattr(game_launcher, "_ocr_winrt", lambda *_args: [])
    monkeypatch.setattr(
        game_launcher,
        "_press_escape_win32",
        lambda: escape_presses.append(True) or True,
    )
    monkeypatch.setattr(game_launcher.time, "sleep", lambda _seconds: None)

    assert game_launcher._dismiss_startup_cinematic_win32(launched_now=True) is True
    assert captures == [blank, cinematic]
    assert escape_presses == [True]


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
    monkeypatch.setattr(
        game_launcher,
        "_click_text",
        lambda text, **_kwargs: calls.append(text) or False,
    )

    result = game_launcher._navigate_to_save_sync("ANY_SAVE", tab=None)

    assert result.startswith("FAILED: Could not find 'Single Player'")
    assert calls == ["cinematic:True", "Single Player"]


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
    monkeypatch.setattr(
        game_launcher,
        "_click_text",
        lambda text, **_kwargs: calls.append(text) or False,
    )

    result = game_launcher._navigate_to_save_sync("ANY_SAVE", tab=None)

    assert result.startswith("FAILED: Could not find 'Single Player'")
    assert calls == ["cinematic:False", "Single Player"]
