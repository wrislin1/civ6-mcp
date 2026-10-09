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
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any

from civ_mcp import game_launcher

log = logging.getLogger(__name__)

# Headroom the WSL-side subprocess waits beyond the native poll's own
# deadline, so a slow-but-honest native timeout is reported by the native
# side's structured failure rather than getting killed by the bridge first.
_DEPLOY_BRIDGE_TIMEOUT_S = 60.0
# The native export waits up to 30 s for a stable save before copying.
_EXPORT_BRIDGE_TIMEOUT_S = 90.0
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
    baseline_offset: int | None
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

    A relative path is returned unchanged: the bridge runs with the Windows
    checkout as its working directory, so it resolves there. Any other
    absolute POSIX path (e.g. ``/home/...``) has no Windows meaning and raises
    ``ValueError`` instead of being handed to the Windows side verbatim.
    """
    text = str(path)
    if _MOUNTED_ABS.match(text):
        return text[5].upper() + ":" + text[6:].replace("/", "\\")
    if text.startswith("/"):
        raise ValueError(f"{text!r} is an absolute POSIX path outside /mnt/<drive>/; "
                         "pass a repository-relative path or a /mnt/<drive>/ path")
    return text


_MOUNTED_ABS = re.compile(r"^/mnt/[A-Za-z]/.+")
_WINDOWS_ABS = re.compile(r"^[A-Za-z]:\\.+")


def _mounted_path(path: str) -> str:
    """Translate a Windows ``X:\\…`` path to its WSL ``/mnt/x/…`` mount."""
    if _WINDOWS_ABS.match(path):
        return f"/mnt/{path[0].lower()}/" + path[3:].replace("\\", "/")
    return path


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
        # Repository-relative paths in `argv` resolve against the Windows
        # checkout, whichever directory this process was started from.
        proc = subprocess.run(
            [python_exe, win_bootstrap, *argv],
            capture_output=True,
            timeout=timeout,
            cwd=game_launcher.WSL_WINDOWS_REPO,
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


def export_via_windows(
    name: str,
    destination: str,
    expected_sha256: str | None = None,
    *,
    timeout: float = _EXPORT_BRIDGE_TIMEOUT_S,
) -> dict[str, Any]:
    """Archive a native save through the native ``export-save`` command.

    ``destination`` must be a Windows-visible absolute path (``/mnt/<drive>/…``
    or ``X:\\…``), e.g. under ``game_launcher.WSL_WINDOWS_REPO``; a WSL ext4 or
    relative path would resolve to an unrelated location on the native side.
    The native side never overwrites ``destination``; an identical existing
    archive comes back with ``existed: True``. After a successful export the
    archive is re-hashed here through its mounted path and must match the
    native digest (and ``expected_sha256`` when given). Use
    ``publish_archive_copy`` to place a verified copy into the WSL repo store.
    """
    if not (_MOUNTED_ABS.match(destination) or _WINDOWS_ABS.match(destination)):
        raise ValueError(
            f"destination {destination!r} must be a Windows-visible absolute path "
            f"such as one under {game_launcher.WSL_WINDOWS_REPO} (/mnt/<drive>/... or X:\\...)"
        )
    argv = [
        "export-save",
        "--name", name,
        "--destination", _windows_path(destination),
        "--json",
    ]
    if expected_sha256 is not None:
        argv += ["--sha256", expected_sha256]
    payload = _run_bridge(argv, timeout=timeout)

    if not payload.get("ok"):
        raise DeploymentVerificationError(
            f"export_benchmark_save failed on Windows: {payload.get('error')}"
        )
    if expected_sha256 is not None and payload.get("sha256") != expected_sha256:
        raise DeploymentVerificationError(
            f"exported sha256 {payload.get('sha256')} != expected {expected_sha256}"
        )

    mounted = _mounted_path(destination)
    try:
        mounted_sha256 = game_launcher._sha256_file(mounted)
    except OSError as exc:
        raise DeploymentVerificationError(
            f"exported archive unreadable at mounted path {mounted}: {exc}"
        ) from exc
    if mounted_sha256 != payload.get("sha256"):
        raise DeploymentVerificationError(
            f"mounted archive {mounted} sha256 {mounted_sha256} != "
            f"native sha256 {payload.get('sha256')}"
        )
    return payload


def publish_archive_copy(source_path: str, destination: str, *, expected_sha256: str) -> dict[str, Any]:
    """Copy a verified archive into the WSL repo store without overwriting.

    Copies to a temp file in ``destination``'s directory, verifies the source
    and the copy both hash to ``expected_sha256``, then publishes with
    ``os.link`` (fails rather than replacing an existing path). An existing
    destination is accepted only when its digest is identical
    (``existed: True``); a differing one raises and is left untouched.
    """
    source_sha256 = game_launcher._sha256_file(source_path)
    if source_sha256 != expected_sha256:
        raise DeploymentVerificationError(
            f"source archive {source_path} sha256 {source_sha256} != expected {expected_sha256}"
        )

    tmp = tempfile.NamedTemporaryFile(
        dir=os.path.dirname(os.path.abspath(destination)),
        prefix=".publish.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with open(source_path, "rb") as src:
            shutil.copyfileobj(src, tmp)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        copy_sha256 = game_launcher._sha256_file(tmp.name)
        if copy_sha256 != expected_sha256:
            raise DeploymentVerificationError(
                f"copied archive sha256 {copy_sha256} != expected {expected_sha256}"
            )
        size = os.path.getsize(tmp.name)

        existed = False
        try:
            os.link(tmp.name, destination)
        except FileExistsError:
            existed = True
        if existed:
            archived_sha256 = game_launcher._sha256_file(destination)
            if archived_sha256 != expected_sha256:
                raise DeploymentVerificationError(
                    f"archive at {destination} differs: existing {archived_sha256}, "
                    f"new {expected_sha256}"
                )
    finally:
        tmp.close()
        if os.path.exists(tmp.name):
            os.remove(tmp.name)

    return {
        "source_path": source_path,
        "dest_path": destination,
        "sha256": expected_sha256,
        "size": size,
        "existed": existed,
    }


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
        # Never default to 0 here -- an absent or null baseline_offset means
        # no baseline was ever established (e.g. Profile.csv missing), which
        # must stay distinguishable from a genuine zero-byte-file baseline.
        baseline_offset=payload.get("baseline_offset"),
        last_frame=payload.get("last_frame"),
        elapsed_s=payload.get("elapsed_s", 0.0),
        file_identity=payload.get("file_identity"),
        profile_path=payload.get("profile_path"),
        reason=payload.get("reason"),
        raw=payload,
    )
