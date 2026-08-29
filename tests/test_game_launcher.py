"""Focused tests for the cross-platform Civ VI launcher."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
import uuid

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
