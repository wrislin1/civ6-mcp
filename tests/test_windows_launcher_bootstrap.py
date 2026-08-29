"""Tests for the App-Control-safe Windows launcher bootstrap."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


BOOTSTRAP_PATH = (
    Path(__file__).parents[1] / "tools" / "windows" / "civ6_launcher_bootstrap.py"
)


def _load_bootstrap_module():
    spec = importlib.util.spec_from_file_location("civ6_launcher_bootstrap", BOOTSTRAP_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_processes_venv_site_packages_before_importing_launcher(
    monkeypatch, tmp_path
):
    module = _load_bootstrap_module()
    repo_root = tmp_path / "civ6-mcp"
    site_packages = repo_root / ".venv" / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    (repo_root / "src").mkdir()
    source_dir = str(repo_root / "src")
    win32_dir = str(site_packages / "win32")
    global_site = str(tmp_path / "global-site-packages")
    calls: list[tuple[str, object]] = []

    def fake_addsitedir(path):
        calls.append(("site", Path(path)))
        # Model the real pywin32 and editable-install .pth effects. The source
        # path deliberately already exists to prove the bootstrap reorders it.
        sys.path.extend([str(site_packages), win32_dir, source_dir])

    monkeypatch.setattr(module.site, "addsitedir", fake_addsitedir)
    monkeypatch.setattr(
        module,
        "_run_launcher",
        lambda argv: calls.append(("launcher", argv)) or 0,
        raising=False,
    )

    original_path = sys.path.copy()
    try:
        sys.path[:] = [global_site, source_dir, *original_path]
        assert module.main(["preflight"], repo_root=repo_root) == 0
        path_after_bootstrap = sys.path.copy()
    finally:
        sys.path[:] = original_path

    assert calls == [
        ("site", site_packages),
        ("launcher", ["preflight"]),
    ]
    assert path_after_bootstrap[:3] == [source_dir, str(site_packages), win32_dir]
    assert path_after_bootstrap.index(global_site) > path_after_bootstrap.index(win32_dir)


def test_bootstrap_fails_clearly_when_windows_dependencies_are_not_synced(
    capsys, tmp_path
):
    module = _load_bootstrap_module()

    assert module.main(["preflight"], repo_root=tmp_path) == 1
    assert "uv sync --extra launcher-windows" in capsys.readouterr().err
