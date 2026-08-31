"""WSL-side bridge contract for native benchmark deployment and boot health.

No real Windows call is ever made here: ``os.path.exists`` and
``subprocess.run`` are faked so the bridge is exercised end-to-end (path
translation, JSON parsing, hash-chain re-verification) without a Windows
host present.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from civ_mcp.arena import benchmark_deploy


def _fake_bridge_available(monkeypatch):
    monkeypatch.setattr(benchmark_deploy.os.path, "exists", lambda _p: True)


def _fake_subprocess_returning(monkeypatch, payload: dict, *, returncode: int = 0, stdout_extra: str = ""):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        stdout = (stdout_extra + json.dumps(payload) + "\n").encode("utf-8")
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=b"")

    monkeypatch.setattr(benchmark_deploy.subprocess, "run", fake_run)
    return calls


# ---------------------------------------------------------------------------
# deploy_via_windows
# ---------------------------------------------------------------------------


def test_deploy_via_windows_verifies_hash_chain_and_returns_evidence(monkeypatch):
    _fake_bridge_available(monkeypatch)
    payload = {
        "ok": True,
        "source": "/mnt/c/tmp/archive.Civ6Save",
        "save_name": "BUILDER_ECONOMY_CAL_V1",
        "dest_path": r"C:\Saves\Single\BUILDER_ECONOMY_CAL_V1.Civ6Save",
        "archive_sha256": "deadbeef",
        "deployed_sha256": "deadbeef",
        "expected_sha256": "deadbeef",
    }
    calls = _fake_subprocess_returning(monkeypatch, payload)

    evidence = benchmark_deploy.deploy_via_windows(
        "/mnt/c/tmp/archive.Civ6Save", "BUILDER_ECONOMY_CAL_V1", "deadbeef"
    )

    assert evidence.ok is True
    assert evidence.save_name == "BUILDER_ECONOMY_CAL_V1"
    assert evidence.dest_path == payload["dest_path"]
    assert evidence.archive_sha256 == "deadbeef"
    assert evidence.deployed_sha256 == "deadbeef"
    assert evidence.raw == payload

    [(cmd, kwargs)] = calls
    assert cmd[2] == "install-save"
    # The /mnt/c path was translated to a native Windows path for the archive arg.
    archive_arg = cmd[cmd.index("--archive") + 1]
    assert archive_arg == r"C:\tmp\archive.Civ6Save"
    assert cmd[cmd.index("--name") + 1] == "BUILDER_ECONOMY_CAL_V1"
    assert cmd[cmd.index("--sha256") + 1] == "deadbeef"
    assert "--json" in cmd


def test_deploy_via_windows_raises_when_native_side_reports_failure(monkeypatch):
    _fake_bridge_available(monkeypatch)
    _fake_subprocess_returning(
        monkeypatch,
        {"ok": False, "error": "unsafe save name 'bad name': must match ..."},
    )

    with pytest.raises(benchmark_deploy.DeploymentVerificationError):
        benchmark_deploy.deploy_via_windows("/mnt/c/tmp/archive.Civ6Save", "bad name", "deadbeef")


def test_deploy_via_windows_raises_on_hash_chain_mismatch_even_if_native_side_claims_ok(
    monkeypatch,
):
    """Counterfactual: a native side that lies about ok=True with a broken
    hash chain must still be caught here -- the bridge re-verifies
    independently rather than trusting the native ``ok`` flag alone."""
    _fake_bridge_available(monkeypatch)
    _fake_subprocess_returning(
        monkeypatch,
        {
            "ok": True,
            "save_name": "NAME",
            "dest_path": r"C:\Saves\Single\NAME.Civ6Save",
            "archive_sha256": "deadbeef",
            "deployed_sha256": "0000000000000000",  # corrupted copy
            "expected_sha256": "deadbeef",
        },
    )

    with pytest.raises(benchmark_deploy.DeploymentVerificationError, match="hash chain"):
        benchmark_deploy.deploy_via_windows("/mnt/c/tmp/archive.Civ6Save", "NAME", "deadbeef")


def test_deploy_via_windows_raises_bridge_error_when_bootstrap_missing(monkeypatch):
    monkeypatch.setattr(benchmark_deploy.os.path, "exists", lambda _p: False)

    with pytest.raises(benchmark_deploy.BridgeError):
        benchmark_deploy.deploy_via_windows("/mnt/c/tmp/archive.Civ6Save", "NAME", "deadbeef")


def test_deploy_via_windows_raises_bridge_error_on_non_json_output(monkeypatch):
    _fake_bridge_available(monkeypatch)

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout=b"Traceback...\n", stderr=b"boom")

    monkeypatch.setattr(benchmark_deploy.subprocess, "run", fake_run)

    with pytest.raises(benchmark_deploy.BridgeError):
        benchmark_deploy.deploy_via_windows("/mnt/c/tmp/archive.Civ6Save", "NAME", "deadbeef")


# ---------------------------------------------------------------------------
# check_boot_health_via_windows
# ---------------------------------------------------------------------------


def test_check_boot_health_via_windows_returns_pass_evidence(monkeypatch):
    _fake_bridge_available(monkeypatch)
    payload = {
        "ok": True,
        "reason": None,
        "baseline_offset": 4096,
        "last_frame": 150,
        "elapsed_s": 12.4,
        "file_identity": {"dev": 1, "ino": 2},
        "profile_path": r"C:\Users\wrisl\AppData\Local\Firaxis Games\Sid Meier's Civilization VI\Logs\Profile.csv",
    }
    calls = _fake_subprocess_returning(monkeypatch, payload)

    evidence = benchmark_deploy.check_boot_health_via_windows(min_frame=100, timeout=240)

    assert evidence.ok is True
    assert evidence.last_frame == 150
    assert evidence.baseline_offset == 4096
    assert evidence.raw == payload

    [(cmd, kwargs)] = calls
    assert cmd[2] == "boot-health"
    assert cmd[cmd.index("--min-frame") + 1] == "100"
    assert cmd[cmd.index("--timeout") + 1] == "240"
    assert "--json" in cmd
    # Subprocess timeout gives the native poll headroom to fail closed itself.
    assert kwargs["timeout"] > 240


def test_check_boot_health_via_windows_returns_failure_evidence_without_raising(monkeypatch):
    """A boot-health failure (timeout/rotation/truncation) is an expected,
    structured outcome -- the runner decides what to do with it. Only a
    broken bridge itself should raise."""
    _fake_bridge_available(monkeypatch)
    payload = {
        "ok": False,
        "reason": "timeout",
        "baseline_offset": 0,
        "last_frame": 5,
        "elapsed_s": 240.0,
        "file_identity": {"dev": 1, "ino": 2},
        "profile_path": "C:\\Profile.csv",
    }
    _fake_subprocess_returning(monkeypatch, payload)

    evidence = benchmark_deploy.check_boot_health_via_windows()

    assert evidence.ok is False
    assert evidence.reason == "timeout"
    assert evidence.last_frame == 5


def test_boot_health_preserves_absent_baseline_as_none(monkeypatch):
    """A null (or absent-key) baseline_offset in the native JSON payload
    must parse to None, never the bogus 0 that a plain ``.get(..., 0)``
    default would produce -- 0 is a legitimate empty-file baseline and
    must never be confused with "no baseline at all"."""
    _fake_bridge_available(monkeypatch)
    payload = {
        "ok": False,
        "reason": "profile_missing",
        "baseline_offset": None,
        "last_frame": None,
        "elapsed_s": 0.0,
        "file_identity": None,
        "profile_path": r"C:\Users\wrisl\AppData\Local\Firaxis Games\Sid Meier's Civilization VI\Logs\Profile.csv",
        "error": "Profile.csv not found",
    }
    _fake_subprocess_returning(monkeypatch, payload)

    evidence = benchmark_deploy.check_boot_health_via_windows()

    assert evidence.ok is False
    assert evidence.baseline_offset is None
    assert evidence.reason == "profile_missing"
    assert evidence.raw == payload


def test_check_boot_health_via_windows_raises_bridge_error_when_bootstrap_missing(monkeypatch):
    monkeypatch.setattr(benchmark_deploy.os.path, "exists", lambda _p: False)

    with pytest.raises(benchmark_deploy.BridgeError):
        benchmark_deploy.check_boot_health_via_windows()


# ---------------------------------------------------------------------------
# Path translation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wsl_path", "windows_path"),
    [
        ("/mnt/c/Users/wrisl/dev/civ6-mcp/archive.Civ6Save", r"C:\Users\wrisl\dev\civ6-mcp\archive.Civ6Save"),
        ("/mnt/d/saves/archive.Civ6Save", r"D:\saves\archive.Civ6Save"),
        (r"C:\already\native\path.Civ6Save", r"C:\already\native\path.Civ6Save"),
    ],
)
def test_windows_path_translates_mnt_paths(wsl_path, windows_path):
    assert benchmark_deploy._windows_path(wsl_path) == windows_path
