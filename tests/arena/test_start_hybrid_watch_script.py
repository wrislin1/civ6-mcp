from __future__ import annotations

import subprocess
from pathlib import Path


SCRIPT = Path("tools/skills/civ6-arena-live/scripts/start-hybrid-watch.sh")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_config_run_id_owns_transcript_identity(tmp_path):
    config = tmp_path / "experiment.yaml"
    config.write_text(
        "run_id: config-owned-run\n"
        "civs:\n"
        "  - {player: 0, provider: scripted, model: seat0-smoke}\n"
    )

    result = _run("--config", str(config), "--dry-run-args")

    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    assert args[:2] == ["--config", str(config)]
    assert "--run-id" not in args


def test_config_rejects_cli_run_id_override(tmp_path):
    config = tmp_path / "experiment.yaml"
    config.write_text("run_id: config-owned-run\ncivs: []\n")

    result = _run(
        "--config",
        str(config),
        "--run-id",
        "different-run",
        "--dry-run-args",
    )

    assert result.returncode == 1
    assert "--config cannot be combined with --run-id" in result.stderr


def test_config_requires_explicit_run_id_for_detached_logs(tmp_path):
    config = tmp_path / "experiment.yaml"
    config.write_text("civs: []\n")

    result = _run("--config", str(config), "--dry-run-args")

    assert result.returncode == 1
    assert "config must declare a top-level run_id" in result.stderr


def test_ad_hoc_run_still_forwards_cli_run_id():
    result = _run(
        "--player",
        "0:scripted:seat0-smoke",
        "--run-id",
        "ad-hoc-run",
        "--dry-run-args",
    )

    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    index = args.index("--run-id")
    assert args[index + 1] == "ad-hoc-run"
