"""Command-line contract for the Windows Civ VI launcher."""

from __future__ import annotations

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
    "result",
    [
        "Save 'DOES_NOT_EXIST' not found. Available: none",
        "Save loading (42s). Steps: WARNING: FireTuner port not open after load",
    ],
)
def test_load_command_returns_failure_for_unusable_result(
    monkeypatch, capsys, result
):
    async def unusable_result(_save_name):
        return result

    monkeypatch.setattr(launcher_cli.sys, "platform", "win32")
    monkeypatch.setattr(
        launcher_cli.game_launcher, "load_save_from_menu", unusable_result
    )

    assert launcher_cli.main(["load", "DOES_NOT_EXIST"]) == 1
    assert capsys.readouterr().err == result + "\n"
