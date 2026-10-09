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

    export = commands.add_parser(
        "export-save",
        help="archive a stable native save to a new path without overwriting",
    )
    export.add_argument("--name", required=True, help="save basename in the save directory")
    export.add_argument("--destination", required=True, help="archive path to create")
    export.add_argument("--sha256", default=None, help="optional expected sha256 of the save")
    export.add_argument(
        "--previous-signature", default=None, metavar="SIZE:MTIME_NS",
        help="signature of the same-named save before the save request (from stat-save); "
             "a file still carrying it is the stale predecessor, never the new save",
    )
    export.add_argument("--json", action="store_true", help="emit a single JSON result object")

    stat = commands.add_parser(
        "stat-save",
        help="report whether a native save exists and its size/mtime signature",
    )
    stat.add_argument("--name", required=True, help="save basename in the save directory")
    stat.add_argument("--json", action="store_true", help="emit a single JSON result object")

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

    classify_frontend = commands.add_parser(
        "classify-frontend",
        help="classify the frontend load screen (continue/leader/in-world/unknown) for the Escape waiter",
    )
    classify_frontend.add_argument(
        "--json", action="store_true", help="emit a single JSON result object"
    )

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


def _parse_signature(text: str | None) -> tuple[int, int] | None:
    """``SIZE:MTIME_NS`` -> ``(size, mtime_ns)``; ``None`` when absent."""
    if text is None:
        return None
    try:
        size, mtime_ns = text.split(":")
        return int(size), int(mtime_ns)
    except ValueError:
        raise ValueError(f"--previous-signature must be SIZE:MTIME_NS, got {text!r}") from None


def _export_save(args: argparse.Namespace) -> int:
    """Export a native save; same one-JSON-object contract as ``_install_save``."""
    try:
        result = game_launcher.export_benchmark_save(
            args.name, args.destination, expected_sha256=args.sha256,
            previous_signature=_parse_signature(args.previous_signature),
        )
        payload: dict = {"ok": True, **result}
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}

    if args.json:
        print(json.dumps(payload))
    elif payload["ok"]:
        print(f"Exported {payload['save_name']} -> {payload['dest_path']}")
    else:
        print(payload["error"], file=sys.stderr)

    return 0 if payload["ok"] else 1


def _stat_save(args: argparse.Namespace) -> int:
    """Report a native save's existence and signature; one JSON object with ``--json``."""
    try:
        payload: dict = {"ok": True, **game_launcher.stat_benchmark_save(args.name)}
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}

    if args.json:
        print(json.dumps(payload))
    elif payload["ok"]:
        state = (f"{payload['size']} bytes, mtime_ns {payload['mtime_ns']}"
                 if payload["exists"] else "absent")
        print(f"{payload['save_name']}: {state}")
    else:
        print(payload["error"], file=sys.stderr)

    return 0 if payload["ok"] else 1


def _boot_health_error(result: dict) -> str:
    """Derive an actionable error string for a failed boot-health result."""
    reason = result.get("reason")
    if reason == "profile_missing":
        base = (
            f"Profile.csv not found or unreadable at "
            f"{result.get('profile_path')!r} -- verify Civ VI has been "
            f"launched at least once (or that LOCALAPPDATA resolves "
            f"correctly) before polling boot health."
        )
        detail = result.get("detail")
        return f"{base} ({detail})" if detail else base
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
    try:
        start_offset = (
            os.path.getsize(profile_path) if os.path.exists(profile_path) else None
        )
    except OSError as exc:
        # C4: Profile.csv can exist (exists() True) but still fail to
        # stat -- a permissions error, or a race where it's
        # deleted/rotated between the exists() and getsize() calls. That
        # must never escape as a raw traceback; report the same
        # structured profile_missing-style failure a genuinely absent
        # file gets, with the real OSError text in `error`/`detail`.
        result = {
            "ok": False,
            "reason": "profile_missing",
            "detail": f"No readable baseline for {profile_path!r}: {exc}",
            "baseline_offset": None,
            "last_frame": None,
            "elapsed_s": 0.0,
            "file_identity": None,
            "profile_path": str(profile_path),
        }
    else:
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


def _classify_frontend(args: argparse.Namespace) -> int:
    """Classify the frontend load screen and report the result as JSON.

    This is the native-Windows half of the WSL bridge used by
    ``game_launcher._classify_frontend_load_state_windows_bridge``: it must
    never crash or raise -- any failure to classify (OCR unavailable, no
    game window, an unexpected exception) is reported as ``state:
    "unknown"`` with an ``error`` field, exactly like ``_boot_health``
    fails closed rather than propagating an exception the WSL side would
    have to guess about.
    """
    try:
        state = game_launcher._classify_frontend_load_state_native()
        payload: dict = {"state": state.value}
    except Exception as exc:
        payload = {
            "state": game_launcher.FrontendLoadState.UNKNOWN.value,
            "error": str(exc),
        }

    if args.json:
        print(json.dumps(payload))
    else:
        print(payload["state"])

    return 0


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

        if args.command == "export-save":
            return _export_save(args)

        if args.command == "stat-save":
            return _stat_save(args)

        if args.command == "boot-health":
            return _boot_health(args)

        if args.command == "classify-frontend":
            return _classify_frontend(args)

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
