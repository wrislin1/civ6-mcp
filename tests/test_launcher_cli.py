"""Command-line contract for the Windows Civ VI launcher."""

from __future__ import annotations

import json

import pytest

from civ_mcp import launcher_cli


def test_preflight_reports_windows_launcher_state(monkeypatch, tmp_path, capsys):
    regular_saves = tmp_path / "Single"
    autosaves = regular_saves / "auto"
    autosaves.mkdir(parents=True)

    monkeypatch.setattr(launcher_cli.sys, "platform", "win32")
    monkeypatch.setattr(
        launcher_cli.game_launcher, "SINGLE_SAVE_DIR", str(regular_saves)
    )
    monkeypatch.setattr(launcher_cli.game_launcher, "SAVE_DIR", str(autosaves))
    monkeypatch.setattr(launcher_cli.game_launcher, "_require_gui_deps", lambda: None)
    monkeypatch.setattr(launcher_cli.game_launcher, "is_game_running", lambda: True)
    monkeypatch.setattr(
        launcher_cli.game_launcher, "_is_tuner_port_open", lambda: False
    )

    assert launcher_cli.main(["preflight"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "platform: win32",
        f"single_save_dir: {regular_saves}",
        f"autosave_dir: {autosaves}",
        "gui_dependencies: ok",
        "game_running: yes",
        "firetuner_port_open: no",
    ]


def test_preflight_rejects_non_windows_host(monkeypatch, capsys):
    monkeypatch.setattr(launcher_cli.sys, "platform", "linux")

    assert launcher_cli.main(["preflight"]) == 1
    assert capsys.readouterr().err == (
        "civ6-launcher requires native Windows Python; current platform is linux\n"
    )


@pytest.mark.parametrize(
    ("command", "launcher_name", "result"),
    [
        ("load", "load_save_from_menu", "Save loading (42s). Steps: loaded."),
        (
            "restart-and-load",
            "restart_and_load",
            "Kill: Game killed. | Launch: Game launched. | Load: Save loading.",
        ),
    ],
)
def test_load_commands_dispatch_named_save(
    monkeypatch, capsys, command, launcher_name, result
):
    calls: list[str] = []

    async def fake_launcher(save_name):
        calls.append(save_name)
        return result

    monkeypatch.setattr(launcher_cli.sys, "platform", "win32")
    monkeypatch.setattr(launcher_cli.game_launcher, launcher_name, fake_launcher)

    assert launcher_cli.main([command, "CHANNELS_GATE_V1_T157"]) == 0
    assert calls == ["CHANNELS_GATE_V1_T157"]
    assert capsys.readouterr().out == result + "\n"


@pytest.mark.parametrize(
    ("command", "launcher_name", "result"),
    [
        (
            "load",
            "load_save_from_menu",
            "Save 'DOES_NOT_EXIST' not found. Available: none",
        ),
        (
            "load",
            "load_save_from_menu",
            "Save loading (42s). Steps: WARNING: FireTuner port not open after load",
        ),
        (
            "restart-and-load",
            "restart_and_load",
            "Kill: Game killed. | Launch: Game launched. | "
            "Load: Save 'DOES_NOT_EXIST' not found. Available: none",
        ),
    ],
)
def test_load_command_returns_failure_for_unusable_result(
    monkeypatch, capsys, command, launcher_name, result
):
    async def unusable_result(_save_name):
        return result

    monkeypatch.setattr(launcher_cli.sys, "platform", "win32")
    monkeypatch.setattr(launcher_cli.game_launcher, launcher_name, unusable_result)

    assert launcher_cli.main([command, "DOES_NOT_EXIST"]) == 1
    assert capsys.readouterr().err == result + "\n"


@pytest.mark.parametrize(("pressed", "code"), [(True, 0), (False, 1)])
def test_press_escape_command_dispatches_focused_escape(
    monkeypatch, capsys, pressed, code
):
    monkeypatch.setattr(launcher_cli.sys, "platform", "win32")
    monkeypatch.setattr(
        launcher_cli.game_launcher,
        "_press_escape_win32",
        lambda: pressed,
        raising=False,
    )

    assert launcher_cli.main(["press-escape"]) == code


def test_install_save_command_prints_json_result_on_success(monkeypatch, capsys):
    result = {
        "source": "archive.Civ6Save",
        "save_name": "NAME",
        "dest_path": "/saves/NAME.Civ6Save",
        "archive_sha256": "abc",
        "deployed_sha256": "abc",
        "expected_sha256": "abc",
    }
    calls = []

    def fake_deploy(source, name, sha256):
        calls.append((source, name, sha256))
        return result

    monkeypatch.setattr(launcher_cli.sys, "platform", "win32")
    monkeypatch.setattr(launcher_cli.game_launcher, "deploy_benchmark_save", fake_deploy)

    code = launcher_cli.main(
        [
            "install-save",
            "--archive", "archive.Civ6Save",
            "--name", "NAME",
            "--sha256", "abc",
            "--json",
        ]
    )

    assert code == 0
    assert calls == [("archive.Civ6Save", "NAME", "abc")]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, **result}


def test_install_save_command_reports_failure_as_json(monkeypatch, capsys):
    def boom(source, name, sha256):
        raise ValueError("source hash mismatch for archive.Civ6Save")

    monkeypatch.setattr(launcher_cli.sys, "platform", "win32")
    monkeypatch.setattr(launcher_cli.game_launcher, "deploy_benchmark_save", boom)

    code = launcher_cli.main(
        [
            "install-save",
            "--archive", "archive.Civ6Save",
            "--name", "NAME",
            "--sha256", "abc",
            "--json",
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": False,
        "error": "source hash mismatch for archive.Civ6Save",
    }


def test_install_save_command_without_json_prints_plain_text(monkeypatch, capsys):
    result = {
        "source": "archive.Civ6Save",
        "save_name": "NAME",
        "dest_path": "/saves/NAME.Civ6Save",
        "archive_sha256": "abc",
        "deployed_sha256": "abc",
        "expected_sha256": "abc",
    }
    monkeypatch.setattr(launcher_cli.sys, "platform", "win32")
    monkeypatch.setattr(
        launcher_cli.game_launcher, "deploy_benchmark_save", lambda *a: result
    )

    code = launcher_cli.main(
        ["install-save", "--archive", "archive.Civ6Save", "--name", "NAME", "--sha256", "abc"]
    )

    assert code == 0
    assert capsys.readouterr().out == "Installed NAME -> /saves/NAME.Civ6Save\n"


def test_boot_health_command_records_offset_and_prints_json_result(
    monkeypatch, capsys, tmp_path
):
    profile = tmp_path / "Profile.csv"
    # Real native Profile.csv row grammar -- wait_for_boot_health is monkeypatched below
    # so this content is functionally inert (only its byte length feeds
    # os.path.getsize), but it must still reflect the real schema, not an
    # invented one.
    profile.write_text(
        "[2026-08-30 10:00:57]\t,----- FRAME: 0 time: 159.87ms "
        "Moving avg: 2.50ms 1 frames since last \r\n"
    )
    monkeypatch.setattr(launcher_cli.sys, "platform", "win32")
    monkeypatch.setattr(
        launcher_cli.game_launcher, "_profile_csv_path", lambda: str(profile)
    )
    captured = {}

    def fake_wait(path, start_offset, min_frame, timeout_s):
        captured["args"] = (path, start_offset, min_frame, timeout_s)
        return {
            "ok": True,
            "reason": None,
            "baseline_offset": start_offset,
            "last_frame": 150,
            "elapsed_s": 1.2,
            "file_identity": {"dev": 1, "ino": 2},
            "profile_path": path,
        }

    monkeypatch.setattr(launcher_cli.game_launcher, "wait_for_boot_health", fake_wait)

    code = launcher_cli.main(["boot-health", "--json"])

    assert code == 0
    assert captured["args"] == (
        str(profile),
        profile.stat().st_size,
        launcher_cli.game_launcher._BOOT_HEALTH_MIN_FRAME,
        launcher_cli.game_launcher._BOOT_HEALTH_TIMEOUT_S,
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["last_frame"] == 150


def test_boot_health_command_returns_failure_exit_code_and_json(
    monkeypatch, capsys, tmp_path
):
    profile = tmp_path / "Profile.csv"
    profile.write_text("")
    monkeypatch.setattr(launcher_cli.sys, "platform", "win32")
    monkeypatch.setattr(
        launcher_cli.game_launcher, "_profile_csv_path", lambda: str(profile)
    )
    monkeypatch.setattr(
        launcher_cli.game_launcher,
        "wait_for_boot_health",
        lambda *a, **k: {
            "ok": False,
            "reason": "timeout",
            "baseline_offset": 0,
            "last_frame": 5,
            "elapsed_s": 240.0,
            "file_identity": {"dev": 1, "ino": 2},
            "profile_path": str(profile),
        },
    )

    code = launcher_cli.main(["boot-health", "--json", "--min-frame", "100", "--timeout", "5"])

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["reason"] == "timeout"


def test_boot_health_command_without_json_prints_plain_text(monkeypatch, capsys, tmp_path):
    profile = tmp_path / "Profile.csv"
    profile.write_text("")
    monkeypatch.setattr(launcher_cli.sys, "platform", "win32")
    monkeypatch.setattr(
        launcher_cli.game_launcher, "_profile_csv_path", lambda: str(profile)
    )
    monkeypatch.setattr(
        launcher_cli.game_launcher,
        "wait_for_boot_health",
        lambda *a, **k: {
            "ok": False,
            "reason": "timeout",
            "baseline_offset": 0,
            "last_frame": 5,
            "elapsed_s": 240.0,
            "file_identity": None,
            "profile_path": str(profile),
        },
    )

    code = launcher_cli.main(["boot-health"])

    assert code == 1
    assert capsys.readouterr().out == "FAILED: frame=5 elapsed=240.0s reason=timeout\n"
