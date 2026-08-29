"""Run ``civ6-launcher`` with a signed Windows Python under App Control.

Windows Application Control can block the copied interpreter in a uv virtual
environment while still allowing the machine's signed Python installation.
This bootstrap runs under that signed interpreter, processes the checkout's
virtual-environment site directory (including pywin32's ``.pth`` hooks), then
dispatches the normal launcher CLI from the checkout source tree.
"""

from __future__ import annotations

import site
import sys
from pathlib import Path
from typing import Sequence


def _run_launcher(argv: Sequence[str]) -> int:
    from civ_mcp.launcher_cli import main as launcher_main

    return launcher_main(list(argv))


def main(
    argv: Sequence[str] | None = None,
    *,
    repo_root: Path | None = None,
) -> int:
    root = repo_root or Path(__file__).resolve().parents[2]
    site_packages = root / ".venv" / "Lib" / "site-packages"
    if not site_packages.is_dir():
        print(
            "Windows launcher dependencies are missing. Run "
            "`uv sync --extra launcher-windows` in this checkout first.",
            file=sys.stderr,
        )
        return 1

    # addsitedir is essential here: plain sys.path insertion does not process
    # pywin32.pth, so modules such as win32gui remain unavailable.
    path_before = set(sys.path)
    site.addsitedir(str(site_packages))

    # Move every path added by the venv and its .pth files ahead of globally
    # installed packages. Preserve their relative order so pywin32's win32,
    # win32/lib, and Pythonwin hooks keep working.
    managed_paths = [str(site_packages)]
    managed_paths.extend(path for path in sys.path if path not in path_before)
    managed_paths = list(dict.fromkeys(managed_paths))
    for path in managed_paths:
        while path in sys.path:
            sys.path.remove(path)
    sys.path[:0] = managed_paths

    source_dir = str(root / "src")
    while source_dir in sys.path:
        sys.path.remove(source_dir)
    sys.path.insert(0, source_dir)

    return _run_launcher(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
