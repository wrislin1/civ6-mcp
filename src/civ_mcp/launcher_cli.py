"""Windows-native command-line interface for Civ VI lifecycle automation."""

from __future__ import annotations

import argparse
import asyncio
import json
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
    commands.add_parser(
        "press-escape",
        help="press Escape in the focused game (dismiss load screen/modals)",
    )
    for command, help_text in (
        ("load", "load a named save from the main menu"),
        ("restart-and-load", "kill Civ VI, relaunch it, and load a named save"),
    ):
        command_parser = commands.add_parser(command, help=help_text)
        command_parser.add_argument("save_name", help="save name without .Civ6Save")

    install = commands.add_parser(
        "install-save",
        help="atomically deploy a hash-verified benchmark save into the save directory",
    )
    install.add_argument("--archive", required=True, help="path to the source .Civ6Save archive")
    install.add_argument("--name", required=True, help="destination save name (no extension)")
    install.add_argument("--sha256", required=True, help="expected sha256 of the archive")
    install.add_argument("--json", action="store_true", help="emit a single JSON result object")

    boot_health = commands.add_parser(
        "boot-health",
        help="poll the native Profile.csv for evidence the game booted cleanly",
    )
    boot_health.add_argument(
        "--min-frame",
        type=int,
        default=game_launcher._BOOT_HEALTH_MIN_FRAME,
        help="frame counter that must be exceeded to count as healthy",
    )
    boot_health.add_argument(
        "--timeout",
        type=float,
        default=game_launcher._BOOT_HEALTH_TIMEOUT_S,
        help="seconds to poll before failing closed",
    )
    boot_health.add_argument("--json", action="store_true", help="emit a single JSON result object")

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


def _install_save(args: argparse.Namespace) -> int:
    """Deploy a benchmark save and report the outcome as JSON or text.

    Failures (bad name, hash mismatch at either checkpoint) are caught here
    rather than the generic top-level handler so a ``--json`` caller always
    gets exactly one JSON object on stdout, success or failure.
    """
    try:
        result = game_launcher.deploy_benchmark_save(args.archive, args.name, args.sha256)
        payload: dict = {"ok": True, **result}
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}

    if args.json:
        print(json.dumps(payload))
    elif payload["ok"]:
        print(f"Installed {payload['save_name']} -> {payload['dest_path']}")
    else:
        print(payload["error"], file=sys.stderr)

    return 0 if payload["ok"] else 1


def _boot_health_error(result: dict) -> str:
    """Derive an actionable error string for a failed boot-health result."""
    reason = result.get("reason")
    if reason == "profile_missing":
        return (
            f"Profile.csv not found or unreadable at "
            f"{result.get('profile_path')!r} -- verify Civ VI has been "
            f"launched at least once (or that LOCALAPPDATA resolves "
            f"correctly) before polling boot health."
        )
    if reason == "log_rotated":
        return "Profile.csv identity changed mid-poll (log rotated) -- boot health could not be verified."
    if reason == "log_truncated":
        return "Profile.csv was truncated mid-poll -- boot health could not be verified."
    if reason == "timeout":
        return (
            f"No frame beyond min_frame observed within the timeout window "
            f"(last_frame={result.get('last_frame')})."
        )
    return result.get("detail") or reason or "boot health check failed"


def _boot_health(args: argparse.Namespace) -> int:
    """Poll boot health from a freshly recorded offset and report the result.

    Never kills or relaunches the game -- this only observes and reports;
    the caller decides what to do with a failure. ``start_offset`` is
    ``None`` (never a fabricated ``0``) when ``Profile.csv`` is absent, so a
    missing profile fails closed as an explicit error rather than silently
    treating "no baseline" like a legitimate zero-byte-file baseline.
    """
    profile_path = game_launcher._profile_csv_path()
    start_offset = (
        os.path.getsize(profile_path) if os.path.exists(profile_path) else None
    )
    result = dict(
        game_launcher.wait_for_boot_health(
            profile_path, start_offset, min_frame=args.min_frame, timeout_s=args.timeout
        )
    )
    if not result.get("ok"):
        result.setdefault("error", _boot_health_error(result))

    if args.json:
        print(json.dumps(result))
    else:
        status = "OK" if result.get("ok") else "FAILED"
        print(
            f"{status}: frame={result.get('last_frame')} "
            f"elapsed={result.get('elapsed_s', 0.0):.1f}s reason={result.get('reason')}"
        )

    return 0 if result.get("ok") else 1


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

        if args.command == "press-escape":
            return 0 if game_launcher._press_escape_win32() else 1

        if args.command == "install-save":
            return _install_save(args)

        if args.command == "boot-health":
            return _boot_health(args)

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
