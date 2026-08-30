"""WSL-side bridge to the native Windows benchmark-save deployment and boot
health CLI.

Civ VI's save directory and its ``Profile.csv`` boot/frame log live under
Windows -- often a OneDrive-redirected Documents folder WSL cannot reliably
see (see CLAUDE.md's Gaming PC environment note). The functions here shell
out to the signed Windows Python bootstrap
(``tools/windows/civ6_launcher_bootstrap.py``), reusing the same
subprocess/path-translation pattern already proven by
``civ_mcp.game_launcher._press_escape_windows_bridge``, to run
``civ6-launcher install-save`` / ``civ6-launcher boot-health`` natively and
parse the single JSON object each prints on stdout with ``--json``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

from civ_mcp import game_launcher

log = logging.getLogger(__name__)

# Headroom the WSL-side subprocess waits beyond the native poll's own
# deadline, so a slow-but-honest native timeout is reported by the native
# side's structured failure rather than getting killed by the bridge first.
_DEPLOY_BRIDGE_TIMEOUT_S = 60.0
_BOOT_HEALTH_BRIDGE_MARGIN_S = 30.0


class BridgeError(RuntimeError):
    """The Windows bootstrap bridge itself is unreachable or misbehaved."""


class DeploymentVerificationError(RuntimeError):
    """A deployed save's hash chain did not verify end-to-end."""


@dataclass(frozen=True)
class DeploymentEvidence:
    """Verified outcome of deploying a benchmark save via the Windows bridge."""

    ok: bool
    save_name: str
    dest_path: str | None
    archive_sha256: str | None
    deployed_sha256: str | None
    expected_sha256: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BootHealthEvidence:
    """Outcome of a native boot-health poll via the Windows bridge.

    A failed poll is returned here, never raised -- the runner decides what
    to do with it. Only a broken bridge (unreachable Windows side, garbled
    output) raises ``BridgeError``.
    """

    ok: bool
    baseline_offset: int
    last_frame: int | None
    elapsed_s: float
    file_identity: dict[str, Any] | None
    profile_path: str | None
    reason: str | None
    raw: dict[str, Any] = field(default_factory=dict)


def _windows_path(path: str) -> str:
    """Translate a WSL-visible ``/mnt/<drive>/...`` path to a native one.

    Mirrors ``civ_mcp.game_launcher._press_escape_windows_bridge``'s
    translation exactly (same drive-letter / backslash convention) since the
    signed Windows interpreter cannot resolve ``/mnt`` paths.
    """
    text = str(path)
    if text.startswith("/mnt/") and len(text) > 7:
        return text[5].upper() + ":" + text[6:].replace("/", "\\")
    return text


def _run_bridge(argv: list[str], *, timeout: float) -> dict[str, Any]:
    """Invoke the signed Windows bootstrap with ``argv`` and parse its JSON.

    Fakeable in tests via monkeypatching ``os.path.exists`` and
    ``subprocess.run`` on this module -- no real Windows call is ever made
    by the test suite.
    """
    python_exe = os.environ.get("CIV6_WINDOWS_PYTHON", game_launcher._WSL_WINDOWS_PYTHON)
    bootstrap = os.environ.get("CIV6_WINDOWS_BOOTSTRAP", game_launcher._WSL_WINDOWS_BOOTSTRAP)
    if not (os.path.exists(python_exe) and os.path.exists(bootstrap)):
        raise BridgeError(f"Windows bridge unavailable ({python_exe}, {bootstrap})")

    win_bootstrap = _windows_path(bootstrap)
    try:
        proc = subprocess.run(
            [python_exe, win_bootstrap, *argv],
            capture_output=True,
            timeout=timeout,
        )
    except Exception as exc:
        raise BridgeError(f"Windows bridge invocation failed: {exc}") from exc

    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()
    if not stdout:
        raise BridgeError(
            f"Windows bridge produced no output (exit {proc.returncode}); stderr={stderr!r}"
        )

    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise BridgeError(f"Windows bridge produced non-JSON output: {stdout!r}") from exc
    if not isinstance(payload, dict):
        raise BridgeError(f"Windows bridge JSON was not an object: {payload!r}")
    return payload


def deploy_via_windows(
    source: str,
    save_name: str,
    expected_sha256: str,
    *,
    timeout: float = _DEPLOY_BRIDGE_TIMEOUT_S,
) -> DeploymentEvidence:
    """Deploy a benchmark save through the native ``install-save`` command.

    Re-verifies ``archive_sha256 == deployed_sha256 == expected_sha256`` on
    the parsed evidence in addition to the native side's own check --
    ``DeploymentVerificationError`` is raised rather than returning evidence
    that claims success, so a caller can never mistake a broken chain for a
    verified deployment.
    """
    payload = _run_bridge(
        [
            "install-save",
            "--archive", _windows_path(source),
            "--name", save_name,
            "--sha256", expected_sha256,
            "--json",
        ],
        timeout=timeout,
    )

    if not payload.get("ok"):
        raise DeploymentVerificationError(
            f"deploy_benchmark_save failed on Windows: {payload.get('error')}"
        )

    archive_sha256 = payload.get("archive_sha256")
    deployed_sha256 = payload.get("deployed_sha256")
    if not (archive_sha256 == deployed_sha256 == expected_sha256):
        raise DeploymentVerificationError(
            "hash chain did not verify end-to-end: "
            f"archive={archive_sha256} deployed={deployed_sha256} expected={expected_sha256}"
        )

    return DeploymentEvidence(
        ok=True,
        save_name=save_name,
        dest_path=payload.get("dest_path"),
        archive_sha256=archive_sha256,
        deployed_sha256=deployed_sha256,
        expected_sha256=expected_sha256,
        raw=payload,
    )


def check_boot_health_via_windows(
    *,
    min_frame: int = 100,
    timeout: float = 240.0,
    bridge_timeout: float | None = None,
) -> BootHealthEvidence:
    """Run the native boot-health poll through the Windows bridge.

    Never kills or relaunches the game itself -- a timeout, truncation, or
    rotation comes back as ``BootHealthEvidence(ok=False, ...)`` for the
    caller (the runner, at session startup) to act on.
    """
    resolved_bridge_timeout = (
        bridge_timeout if bridge_timeout is not None else timeout + _BOOT_HEALTH_BRIDGE_MARGIN_S
    )
    payload = _run_bridge(
        [
            "boot-health",
            "--min-frame", str(min_frame),
            "--timeout", str(timeout),
            "--json",
        ],
        timeout=resolved_bridge_timeout,
    )

    return BootHealthEvidence(
        ok=bool(payload.get("ok")),
        baseline_offset=payload.get("baseline_offset", 0),
        last_frame=payload.get("last_frame"),
        elapsed_s=payload.get("elapsed_s", 0.0),
        file_identity=payload.get("file_identity"),
        profile_path=payload.get("profile_path"),
        reason=payload.get("reason"),
        raw=payload,
    )
