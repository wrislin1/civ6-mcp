"""Windows-native command-line interface for Civ VI lifecycle automation."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

from civ_mcp import game_launcher


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="civ6-launcher",
        description="Launch Civilization VI and load saves through the Windows UI.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "preflight",
        help="validate Windows GUI dependencies and report launcher state",
    )
    for command, help_text in (
        ("load", "load a named save from the main menu"),
        ("restart-and-load", "kill Civ VI, relaunch it, and load a named save"),
    ):
        command_parser = commands.add_parser(command, help=help_text)
        command_parser.add_argument("save_name", help="save name without .Civ6Save")
    return parser


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError(
            "civ6-launcher requires native Windows Python; "
            f"current platform is {sys.platform}"
        )


def _preflight() -> None:
    game_launcher._require_gui_deps()
    if not os.path.isdir(game_launcher.SINGLE_SAVE_DIR):
        raise RuntimeError(
            f"single-player save directory not found: {game_launcher.SINGLE_SAVE_DIR}"
        )

    print("platform: win32")
    print(f"single_save_dir: {game_launcher.SINGLE_SAVE_DIR}")
    print(f"autosave_dir: {game_launcher.SAVE_DIR}")
    print("gui_dependencies: ok")
    print(f"game_running: {'yes' if game_launcher.is_game_running() else 'no'}")
    print(
        "firetuner_port_open: "
        f"{'yes' if game_launcher._is_tuner_port_open() else 'no'}"
    )


def _launcher_failed(result: str) -> bool:
    return (
        "FAILED:" in result
        or "ABORTED:" in result
        or "WARNING:" in result
        or " not found." in result
        or "No autosaves found" in result
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Windows launcher command and return a process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        _require_windows()
        if args.command == "preflight":
            _preflight()
            return 0

        launcher = (
            game_launcher.load_save_from_menu
            if args.command == "load"
            else game_launcher.restart_and_load
        )
        result = asyncio.run(launcher(args.save_name))
        stream = sys.stderr if _launcher_failed(result) else sys.stdout
        print(result, file=stream)
        return 1 if stream is sys.stderr else 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
