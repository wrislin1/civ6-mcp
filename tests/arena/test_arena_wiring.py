# tests/arena/test_arena_wiring.py
import asyncio
import json
import os
import os.path
import shutil
import pytest

from civ_mcp.arena import arena as arena_module
from civ_mcp.arena import live_gate as live_gate_module
from civ_mcp.arena.arena import build_args, build_policies, resolve_config, _run
from civ_mcp.arena.config import (
    PlayerSpec,
    ArenaConfig,
    ChannelOptions,
    CivOptions,
    DEFAULT_GATEWAY_URL,
    LiveGateOptions,
    parse_player_spec,
)
from civ_mcp.arena.agent import LLMPolicy
from civ_mcp.arena.cli_agent import CLIAgentPolicy
from civ_mcp.arena.cost import CostLog
from civ_mcp.arena.transcript import NullSink

class FakeCost:
    def record(self, **kw): pass

def test_build_policies_routes_by_provider():
    specs = [
        PlayerSpec(1, "local", "qwen3-coder:30b"),
        PlayerSpec(2, "cli-claude", ""),
        PlayerSpec(3, "cli-codex", "gpt-5.5"),
    ]
    cfg = ArenaConfig(players=specs)
    policies, local_backends = build_policies(specs, FakeCost(), cfg)
    assert isinstance(policies[1], LLMPolicy)        # local → in-process LLM
    assert isinstance(policies[2], CLIAgentPolicy)   # cli-claude → CLI subprocess
    assert isinstance(policies[3], CLIAgentPolicy)   # cli-codex → CLI subprocess
    assert len(local_backends) == 1                  # one local spec → one backend


def test_build_policies_two_local_specs_two_backends():
    """Two local players must each get their own backend (old code silently dropped the first)."""
    specs = [
        PlayerSpec(1, "local", "model-a"),
        PlayerSpec(2, "local", "model-b"),
    ]
    cfg = ArenaConfig(players=specs)
    policies, local_backends = build_policies(specs, FakeCost(), cfg)
    assert isinstance(policies[1], LLMPolicy)
    assert isinstance(policies[2], LLMPolicy)
    assert len(local_backends) == 2


def test_build_policies_cli_only_empty_local_backends():
    specs = [PlayerSpec(1, "cli-claude", ""), PlayerSpec(2, "cli-codex", "gpt-5.5")]
    cfg = ArenaConfig(players=specs)
    policies, local_backends = build_policies(specs, FakeCost(), cfg)
    assert local_backends == []


def test_build_policies_scripted_seat0_with_real_local_puppets():
    """Task 9: `provider: scripted` yields a ScriptedPolicy for that seat only,
    builds NO backend for it, and leaves the nonzero seats as real local
    policies each with their own backend."""
    from civ_mcp.arena.coordinator import ScriptedPolicy

    specs = [
        PlayerSpec(0, "scripted", "seat0-smoke"),
        PlayerSpec(1, "local", "gemma4-26b", "http://gw:11440/v1"),
        PlayerSpec(2, "local", "gemma4-26b", "http://gw:11440/v1"),
    ]
    cfg = ArenaConfig(players=specs, gateway_url="http://gw:11444/v1")
    policies, local_backends = build_policies(specs, FakeCost(), cfg)

    assert isinstance(policies[0], ScriptedPolicy)
    assert policies[0].provider == "scripted"
    assert policies[0].model == "seat0-smoke"
    assert isinstance(policies[1], LLMPolicy)
    assert isinstance(policies[2], LLMPolicy)
    # The scripted seat contributes no backend; each local puppet contributes one.
    assert len(local_backends) == 2
    assert all(b.base_url == "http://gw:11440/v1" for b in local_backends)


def test_build_policies_scripted_seat0_needs_no_exclusive_tuner():
    """A ScriptedPolicy must not request the exclusive tuner handoff."""
    from civ_mcp.arena.coordinator import ScriptedPolicy

    specs = [PlayerSpec(0, "scripted", "seat0-smoke")]
    cfg = ArenaConfig(players=specs)
    policies, local_backends = build_policies(specs, FakeCost(), cfg)
    assert isinstance(policies[0], ScriptedPolicy)
    assert getattr(policies[0], "needs_exclusive_tuner", False) is False
    assert local_backends == []


def test_build_policies_scripted_seat_preserves_spec_options():
    """Review fix: shared knobs validated for a scripted seat (memory,
    task_tracker, ...) must reach the policy -- the coordinator reads them
    via getattr(pol, 'options', CivOptions()), so a bare ScriptedPolicy()
    silently dropped every validated YAML knob."""
    from civ_mcp.arena.config import MemoryOptions
    from civ_mcp.arena.coordinator import ScriptedPolicy

    opts = CivOptions(memory=MemoryOptions(enabled=True))
    specs = [PlayerSpec(0, "scripted", "seat0-smoke", options=opts)]
    cfg = ArenaConfig(players=specs)
    policies, _ = build_policies(specs, FakeCost(), cfg)

    assert isinstance(policies[0], ScriptedPolicy)
    assert policies[0].options is opts
    assert policies[0].options.memory.enabled is True


def test_build_policies_per_civ_gateway_pins_backend():
    """Each local civ's backend targets its own gateway when the spec pins one;
    civs without a pin fall back to the global cfg.gateway_url."""
    specs = [
        PlayerSpec(3, "local", "gemma4-26b", "http://192.168.20.196:11440/v1"),
        PlayerSpec(4, "local", "qwen3.6-27b", "http://192.168.20.196:11441/v1"),
        PlayerSpec(5, "local", "gemma4-26b"),  # no pin → global default
    ]
    cfg = ArenaConfig(players=specs, gateway_url="http://192.168.20.196:11444/v1")
    _policies, local_backends = build_policies(specs, FakeCost(), cfg)
    by_model_gw = {(b.model, b.base_url) for b in local_backends}
    assert ("gemma4-26b", "http://192.168.20.196:11440/v1") in by_model_gw
    assert ("qwen3.6-27b", "http://192.168.20.196:11441/v1") in by_model_gw
    # the un-pinned civ uses the global gateway
    assert ("gemma4-26b", "http://192.168.20.196:11444/v1") in by_model_gw


def test_build_args_accepts_idle_poll_limit():
    args = build_args(["--idle-poll-limit", "12"])
    assert args.idle_poll_limit == 12


def test_resolve_config_non_config_uses_arena_defaults():
    cfg = resolve_config(build_args(["--player", "3:local:m"]))
    assert cfg.max_puppet_turns == 1
    assert cfg.idle_poll_limit == 600
    assert cfg.gateway_url == DEFAULT_GATEWAY_URL
    assert cfg.max_agent_steps == 6


def test_build_args_accepts_config():
    a = build_args(["--config", "experiments/x.yaml"])
    assert a.config == "experiments/x.yaml"


def test_config_and_player_are_mutually_exclusive(tmp_path, capsys):
    with pytest.raises(SystemExit):
        resolve_config(build_args(["--config", "x.yaml", "--player", "1:local:m"]))


def test_resolve_config_from_file(tmp_path):
    p = tmp_path / "e.yaml"
    p.write_text(
        "max_puppet_turns: 12\ncivs:\n  - {player: 3, provider: local, model: m, max_steps: 9}\n"
    )
    cfg = resolve_config(build_args(["--config", str(p)]))
    assert cfg.max_puppet_turns == 12
    assert cfg.players[0].options.max_steps == 9


@pytest.mark.parametrize(
    ("argv_tail", "flag"),
    [
        (["--max-puppet-turns", "2"], "--max-puppet-turns"),
        (["--gateway-url", "http://example.invalid/v1"], "--gateway-url"),
        (["--idle-poll-limit", "601"], "--idle-poll-limit"),
        (["--max-agent-steps", "7"], "--max-agent-steps"),
    ],
)
def test_resolve_config_rejects_non_default_config_owned_flags(tmp_path, argv_tail, flag):
    p = tmp_path / "e.yaml"
    p.write_text("civs:\n  - {player: 3, provider: local, model: m}\n")
    with pytest.raises(SystemExit, match=flag):
        resolve_config(build_args(["--config", str(p), *argv_tail]))


@pytest.mark.parametrize(
    ("argv_tail", "flag"),
    [
        (["--max-puppet-turns", "1"], "--max-puppet-turns"),
        (["--gateway-url", DEFAULT_GATEWAY_URL], "--gateway-url"),
        (["--idle-poll-limit", "600"], "--idle-poll-limit"),
        (["--max-agent-steps", "6"], "--max-agent-steps"),
    ],
)
def test_resolve_config_rejects_config_owned_flags_even_when_default_value_passed(
    tmp_path, argv_tail, flag
):
    p = tmp_path / "e.yaml"
    p.write_text("civs:\n  - {player: 3, provider: local, model: m}\n")
    with pytest.raises(SystemExit, match=flag):
        resolve_config(build_args(["--config", str(p), *argv_tail]))


def test_build_policies_threads_options(tmp_path):
    spec = parse_player_spec("3:local:m")
    object.__setattr__(spec, "options", CivOptions(max_steps=11, tools="standard"))
    cfg = ArenaConfig(players=[spec])
    cost = CostLog(str(tmp_path / "c.jsonl"))
    policies, backends = build_policies([spec], cost, cfg)
    pol = policies[3]
    assert pol.max_steps == 11
    assert any(t["function"]["name"] == "get_map_area" for t in pol._tools)


def test_build_policies_uses_arena_max_agent_steps_for_default_local_options(tmp_path):
    spec = parse_player_spec("3:local:m")
    cfg = ArenaConfig(players=[spec], max_agent_steps=2)
    cost = CostLog(str(tmp_path / "c.jsonl"))

    policies, _backends = build_policies([spec], cost, cfg)
    pol = policies[3]

    assert pol.max_steps == 2
    assert pol.options.max_steps == 2


def test_player_shorthand_honors_max_agent_steps():
    cfg = resolve_config(build_args(["--player", "3:local:m", "--max-agent-steps", "12"]))
    assert cfg.players[0].options.max_steps == 12


def test_run_uses_file_run_id_for_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "e.yaml"
    cfg_path.write_text(
        "run_id: file-run\nmax_puppet_turns: 1\ncivs:\n  - {player: 3, provider: local, model: m}\n"
    )
    run_root = tmp_path / "runs"
    captured = {}

    class FakeConn:
        async def connect(self):
            captured["connected"] = True

    def fake_game_state(conn):
        captured["gs_conn"] = conn
        return {"conn": conn}

    async def fake_run_arena(
        conn, gs, cfg, policy_for, transcript, live_gate_driver=None
    ):
        captured["conn"] = conn
        captured["gs"] = gs
        captured["cfg"] = cfg
        captured["transcript"] = transcript
        captured["live_gate_driver"] = live_gate_driver
        captured["policy"] = policy_for(3)
        return {"ok": True}

    monkeypatch.setattr("civ_mcp.arena.arena.GameConnection", FakeConn)
    monkeypatch.setattr("civ_mcp.arena.arena.GameState", fake_game_state)
    monkeypatch.setattr("civ_mcp.arena.arena.run_arena", fake_run_arena)

    asyncio.run(
        _run(build_args(["--config", str(cfg_path), "--dry-run", "--transcript-dir", str(run_root)]))
    )

    cfg = captured["cfg"]
    assert cfg.run_id == "file-run"
    assert cfg.cost_path == str(run_root / "file-run" / "arena_cost.jsonl")
    assert cfg.transcript_dir == str(run_root)
    assert captured["transcript"].path == str(run_root / "file-run" / "transcript.jsonl")
    assert captured["live_gate_driver"] is None
    assert os.path.isdir(run_root / "file-run")


def test_config_yaml_run_id_survives_when_cli_run_id_absent(tmp_path):
    p = tmp_path / "e.yaml"
    p.write_text("run_id: file-run\ncivs:\n  - {player: 3, provider: local, model: m}\n")

    args = build_args(["--config", str(p)])
    cfg = resolve_config(args)

    assert args.run_id is None
    assert cfg.run_id == "file-run"


def test_config_rejects_unsafe_cli_run_id_at_resolve_boundary(tmp_path):
    p = tmp_path / "e.yaml"
    p.write_text("civs:\n  - {player: 3, provider: local, model: m}\n")

    with pytest.raises(SystemExit, match="invalid run_id"):
        resolve_config(build_args(["--config", str(p), "--run-id", "../../evil"]))


def test_config_resolve_threads_runtime_fields_into_cfg(tmp_path):
    p = tmp_path / "e.yaml"
    cost_path = tmp_path / "cost.jsonl"
    transcript_dir = tmp_path / "runs"
    p.write_text("civs:\n  - {player: 3, provider: local, model: m}\n")

    cfg = resolve_config(
        build_args(
            [
                "--config",
                str(p),
                "--api-key-env",
                "LOCAL_ARENA_KEY",
                "--dry-run",
                "--cost-path",
                str(cost_path),
                "--transcript-dir",
                str(transcript_dir),
            ]
        )
    )

    assert cfg.api_key_env == "LOCAL_ARENA_KEY"
    assert cfg.dry_run is True
    assert cfg.cost_path == str(cost_path)
    assert cfg.transcript_dir == str(transcript_dir)


def test_config_rejects_cli_run_id_when_yaml_run_id_present(tmp_path):
    p = tmp_path / "e.yaml"
    p.write_text("run_id: file-run\ncivs:\n  - {player: 3, provider: local, model: m}\n")

    with pytest.raises(SystemExit, match="--run-id"):
        resolve_config(build_args(["--config", str(p), "--run-id", "cli-run"]))


def test_config_rejects_empty_cli_run_id_when_yaml_run_id_present(tmp_path):
    p = tmp_path / "e.yaml"
    p.write_text("run_id: file-run\ncivs:\n  - {player: 3, provider: local, model: m}\n")

    with pytest.raises(SystemExit, match="--run-id"):
        resolve_config(build_args(["--config", str(p), "--run-id", ""]))


def test_cli_preflight_raises_when_claude_not_on_path(monkeypatch, tmp_path):
    """_run raises SystemExit before driving any turns if cli spec present but claude missing."""
    monkeypatch.setattr(shutil, "which", lambda name: None)

    class Args:
        player = ["1:cli-claude:"]
        max_puppet_turns = 1
        gateway_url = "http://localhost:11430/v1"
        api_key_env = "LITELLM_OPENAI_API_KEY"
        cost_path = str(tmp_path / "cost.jsonl")
        max_agent_steps = 6
        dry_run = False
        run_id = ""
        transcript_dir = str(tmp_path / "runs")
        no_transcript = True

    with pytest.raises(SystemExit, match="claude"):
        asyncio.run(_run(Args()))


def test_cli_preflight_raises_when_codex_not_on_path(monkeypatch, tmp_path):
    """_run raises SystemExit before driving turns if a cli-codex spec is present but codex is missing."""
    monkeypatch.setattr(shutil, "which", lambda name: None)

    class Args:
        player = ["1:cli-codex:gpt-5.5"]
        max_puppet_turns = 1
        gateway_url = "http://localhost:11430/v1"
        api_key_env = "LITELLM_OPENAI_API_KEY"
        cost_path = str(tmp_path / "cost.jsonl")
        max_agent_steps = 6
        dry_run = False
        run_id = ""
        transcript_dir = str(tmp_path / "runs")
        no_transcript = True

    with pytest.raises(SystemExit, match="codex"):
        asyncio.run(_run(Args()))


def test_cli_preflight_raises_when_mcp_config_missing(monkeypatch, tmp_path):
    """_run fails loudly if a cli spec is present but .mcp.json is not in CWD.

    The CLI civ uses project auto-discovery; without the project config, the headless
    subprocess silently starts without the civ6 MCP server.
    """
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(os.path, "isfile", lambda p: False)

    class Args:
        player = ["1:cli-claude:"]
        max_puppet_turns = 1
        gateway_url = "http://localhost:11430/v1"
        api_key_env = "LITELLM_OPENAI_API_KEY"
        cost_path = str(tmp_path / "cost.jsonl")
        max_agent_steps = 6
        dry_run = False
        run_id = ""
        transcript_dir = str(tmp_path / "runs")
        no_transcript = True

    with pytest.raises(SystemExit, match=".mcp.json"):
        asyncio.run(_run(Args()))


def test_run_rejects_path_traversal_run_id(tmp_path):
    """A CLI --run-id must not escape the transcript dir (the YAML loader already
    guards this; _run applies the same check at the single choke point)."""
    class Args:
        player = ["1:local:m"]
        max_puppet_turns = 1
        gateway_url = "http://localhost:11430/v1"
        api_key_env = "LITELLM_OPENAI_API_KEY"
        cost_path = str(tmp_path / "cost.jsonl")
        max_agent_steps = 6
        dry_run = True
        run_id = "../../evil"
        transcript_dir = str(tmp_path / "runs")
        no_transcript = True

    with pytest.raises(SystemExit, match="invalid run_id"):
        asyncio.run(_run(Args()))
    # Nothing should have been created outside the transcript dir.
    assert not (tmp_path.parent / "evil").exists()


def test_max_game_turns_cli_flag():
    args = build_args(["--player", "1:local:m", "--max-game-turns", "150"])
    cfg = resolve_config(args)
    assert cfg.max_game_turns == 150


def test_max_game_turns_defaults_uncapped():
    args = build_args(["--player", "1:local:m"])
    assert resolve_config(args).max_game_turns == 0


def test_max_game_turns_rejected_with_config(tmp_path):
    exp = tmp_path / "e.yaml"
    exp.write_text("run_id: t1\ncivs:\n  - {player: 1, provider: local, model: m}\n")
    args = build_args(["--config", str(exp), "--max-game-turns", "5"])
    with pytest.raises(SystemExit, match="config-owned"):
        resolve_config(args)


def test_negative_max_game_turns_rejected_on_cli():
    """YAML validates max_game_turns >= 0; the CLI path must too -- a
    negative silently means 'uncapped' via the `<= 0` loop guard
    (review-2 scope note)."""
    args = build_args(["--player", "1:local:m", "--max-game-turns", "-3"])
    with pytest.raises(SystemExit):
        resolve_config(args)


def test_resolve_config_cli_player_shorthand_excludes_seat_zero_from_puppets():
    args = build_args(["--player", "0:local:m", "--player", "2:local:m"])
    cfg = resolve_config(args)
    assert cfg.puppet_ids == [2]


class _StubDriver:
    def __init__(self, config):
        self.config = config

    def policy_for(self, player_id):
        async def policy(gs, pid, turn, **kwargs):
            return {"summary": "stub"}

        return policy


def _gate_cfg(run_id="run-gate"):
    def civ(pid, provider, model=""):
        return PlayerSpec(
            pid,
            provider,
            model,
            options=CivOptions(channels=ChannelOptions(enabled=True)),
        )

    return ArenaConfig(
        players=[
            civ(1, "local", "m"),
            civ(2, "cli-codex"),
            civ(3, "scripted"),
        ],
        max_puppet_turns=36,
        max_game_turns=36,
        run_id=run_id,
        live_gate=LiveGateOptions(
            enabled=True,
            scenario="stub_gate_v1",
            roles=(("api_actor", 1), ("cli_actor", 2), ("privacy_observer", 3)),
        ),
    )


@pytest.fixture
def stub_gate(monkeypatch):
    meta = live_gate_module.ScenarioMeta(
        name="stub_gate_v1",
        revision=1,
        role_contracts=(
            ("api_actor", "in_process"),
            ("cli_actor", "cli"),
            ("privacy_observer", "scripted"),
        ),
        minimum_captures=lambda config: 1,
        create_driver=_StubDriver,
    )
    monkeypatch.setattr(live_gate_module, "_SCENARIOS", {meta.name: meta})
    return meta


def _fail_gate_side_effects(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("gate conflict must fail before arena side effects")

    monkeypatch.setattr(arena_module, "GameConnection", fail)
    monkeypatch.setattr(arena_module, "build_policies", fail)
    monkeypatch.setattr(arena_module.shutil, "which", fail)
    monkeypatch.setattr("civ_mcp.arena.backends.OpenAICompatBackend", fail)


def test_gate_mode_skips_backend_and_cli_preflight(monkeypatch, tmp_path, stub_gate):
    """Gate mode uses only driver-owned policies and still captures transcripts."""
    seen = {}

    async def fake_run_arena(
        conn,
        gs,
        cfg,
        policy_for=None,
        transcript=None,
        live_gate_driver=None,
    ):
        seen["driver"] = live_gate_driver
        seen["policy_for"] = policy_for
        seen["transcript"] = transcript
        return {
            "puppet_turns_played": 0,
            "turns_slept": 0,
            "seat0_turns_played": 0,
            "seat0_turns_failed": 0,
            "seat0_human_pending": 0,
            "log": [],
            "live_gate": {
                "status": "passed",
                "phase": "verify_terminal_gate",
                "reason": "",
                "restart_count": 1,
                "run_id": cfg.run_id,
            },
        }

    class FakeConn:
        async def connect(self):
            seen["connected"] = True

    def fail_which(cmd):
        raise AssertionError(f"CLI preflight must not run in gate mode: {cmd}")

    def fail_policy_build(*args, **kwargs):
        raise AssertionError("ordinary policies must not be built in gate mode")

    def fail_backend(*args, **kwargs):
        raise AssertionError("no local backend may be constructed in gate mode")

    monkeypatch.setattr(arena_module, "run_arena", fake_run_arena)
    monkeypatch.setattr(arena_module, "GameConnection", FakeConn)
    monkeypatch.setattr(arena_module, "resolve_config", lambda args: _gate_cfg())
    monkeypatch.setattr(arena_module, "build_policies", fail_policy_build)
    monkeypatch.setattr("civ_mcp.arena.backends.OpenAICompatBackend", fail_backend)
    monkeypatch.setattr(arena_module.shutil, "which", fail_which)

    run_root = tmp_path / "runs"
    args = arena_module.build_args(["--transcript-dir", str(run_root)])
    gate = asyncio.run(arena_module._run(args))

    assert gate == {
        "status": "passed",
        "phase": "verify_terminal_gate",
        "reason": "",
        "restart_count": 1,
        "run_id": "run-gate",
    }
    assert seen["connected"] is True
    assert isinstance(seen["driver"], _StubDriver)
    assert seen["policy_for"] == seen["driver"].policy_for
    assert seen["policy_for"](1) is not None
    assert seen["transcript"].path == str(run_root / "run-gate" / "transcript.jsonl")


def test_gate_mode_rejects_no_transcript_before_side_effects(
    monkeypatch, tmp_path, stub_gate
):
    run_root = tmp_path / "runs"
    monkeypatch.setattr(arena_module, "resolve_config", lambda args: _gate_cfg())
    _fail_gate_side_effects(monkeypatch)

    args = arena_module.build_args(
        ["--transcript-dir", str(run_root), "--no-transcript"]
    )
    with pytest.raises(SystemExit, match="transcript"):
        asyncio.run(arena_module._run(args))

    assert not run_root.exists()


def test_gate_mode_rejects_dry_run_before_side_effects(monkeypatch, tmp_path, stub_gate):
    run_root = tmp_path / "runs"
    monkeypatch.setattr(arena_module, "resolve_config", lambda args: _gate_cfg())
    _fail_gate_side_effects(monkeypatch)

    args = arena_module.build_args(
        ["--transcript-dir", str(run_root), "--dry-run"]
    )
    with pytest.raises(SystemExit, match="dry-run"):
        asyncio.run(arena_module._run(args))

    assert not run_root.exists()


def test_disabled_gate_preserves_no_transcript_behavior(monkeypatch, tmp_path):
    seen = {}

    async def fake_run_arena(
        conn,
        gs,
        cfg,
        policy_for=None,
        transcript=None,
        live_gate_driver=None,
    ):
        seen["transcript"] = transcript
        seen["driver"] = live_gate_driver
        return {"live_gate": None}

    class FakeConn:
        async def connect(self):
            pass

    monkeypatch.setattr(arena_module, "run_arena", fake_run_arena)
    monkeypatch.setattr(arena_module, "GameConnection", FakeConn)

    gate = asyncio.run(
        arena_module._run(
            arena_module.build_args(
                [
                    "--player",
                    "1:local:m",
                    "--dry-run",
                    "--no-transcript",
                    "--run-id",
                    "ordinary-run",
                    "--transcript-dir",
                    str(tmp_path / "runs"),
                ]
            )
        )
    )

    assert gate is None
    assert seen["driver"] is None
    assert isinstance(seen["transcript"], NullSink)


@pytest.mark.parametrize(
    ("status", "code"),
    [("restart_required", 75), ("failed", 1), ("active", 1)],
)
def test_main_exit_codes_for_gate_outcomes(monkeypatch, capsys, status, code):
    gate = {
        "status": status,
        "phase": "p",
        "reason": "r",
        "restart_count": 1,
        "run_id": "run-gate",
    }

    async def fake_run(args):
        return gate

    monkeypatch.setattr(arena_module, "_run", fake_run)
    monkeypatch.setattr(arena_module, "build_args", lambda argv=None: object())

    with pytest.raises(SystemExit) as exc_info:
        arena_module.main()

    assert exc_info.value.code == code
    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("LIVE_GATE ")
    ]
    assert len(lines) == 1
    assert json.loads(lines[0][len("LIVE_GATE ") :]) == gate


def test_main_gate_passed_exits_zero(monkeypatch, capsys):
    gate = {
        "status": "passed",
        "phase": "verify_terminal_gate",
        "reason": "",
        "restart_count": 1,
        "run_id": "run-gate",
    }

    async def fake_run(args):
        return gate

    monkeypatch.setattr(arena_module, "_run", fake_run)
    monkeypatch.setattr(arena_module, "build_args", lambda argv=None: object())

    arena_module.main()

    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("LIVE_GATE ")
    ]
    assert len(lines) == 1
    assert json.loads(lines[0][len("LIVE_GATE ") :]) == gate


@pytest.mark.parametrize(
    ("coordinator_result", "expected_reason"),
    [
        (
            "PRIVATE_COORDINATOR_VALUE",
            "coordinator returned a non-mapping result for enabled live_gate",
        ),
        (
            {"log": []},
            "coordinator result omitted live_gate summary",
        ),
        (
            {"live_gate": None},
            "coordinator returned an invalid live_gate summary",
        ),
        (
            {"live_gate": ["PRIVATE_GATE_VALUE"]},
            "coordinator returned an invalid live_gate summary",
        ),
        (
            {
                "live_gate": {
                    "run_id": "PRIVATE_UNTRUSTED_GATE_RUN_ID",
                    "private_marker": "PRIVATE_GATE_MAPPING_VALUE",
                }
            },
            "coordinator returned an invalid live_gate summary",
        ),
    ],
)
def test_enabled_gate_malformed_coordinator_result_fails_closed(
    monkeypatch,
    tmp_path,
    stub_gate,
    capsys,
    coordinator_result,
    expected_reason,
):
    async def fake_run_arena(*args, **kwargs):
        return coordinator_result

    class FakeConn:
        async def connect(self):
            pass

    args = arena_module.build_args(
        ["--transcript-dir", str(tmp_path / "runs")]
    )
    monkeypatch.setattr(arena_module, "run_arena", fake_run_arena)
    monkeypatch.setattr(arena_module, "GameConnection", FakeConn)
    monkeypatch.setattr(arena_module, "resolve_config", lambda parsed: _gate_cfg())
    monkeypatch.setattr(arena_module, "build_args", lambda argv=None: args)

    with pytest.raises(SystemExit) as exc_info:
        arena_module.main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    lines = [line for line in output.splitlines() if line.startswith("LIVE_GATE ")]
    assert len(lines) == 1
    summary = json.loads(lines[0][len("LIVE_GATE ") :])
    assert summary == {
        "phase": "arena_result_validation",
        "reason": expected_reason,
        "restart_count": 0,
        "run_id": "run-gate",
        "status": "failed",
    }
    assert "PRIVATE_" not in output


def test_enabled_gate_exact_summary_with_wrong_run_id_fails_closed(
    monkeypatch, tmp_path, stub_gate, capsys
):
    private_run_id = "PRIVATE_WRONG_BUT_SCHEMA_VALID_RUN_ID"

    async def fake_run_arena(*args, **kwargs):
        return {
            "live_gate": {
                "status": "passed",
                "phase": "verify_terminal_gate",
                "reason": "",
                "restart_count": 1,
                "run_id": private_run_id,
            }
        }

    class FakeConn:
        async def connect(self):
            pass

    args = arena_module.build_args(["--transcript-dir", str(tmp_path / "runs")])
    monkeypatch.setattr(arena_module, "run_arena", fake_run_arena)
    monkeypatch.setattr(arena_module, "GameConnection", FakeConn)
    monkeypatch.setattr(arena_module, "resolve_config", lambda parsed: _gate_cfg())
    monkeypatch.setattr(arena_module, "build_args", lambda argv=None: args)

    with pytest.raises(SystemExit) as exc_info:
        arena_module.main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    lines = [line for line in output.splitlines() if line.startswith("LIVE_GATE ")]
    assert len(lines) == 1
    assert json.loads(lines[0][len("LIVE_GATE ") :]) == {
        "phase": "arena_result_validation",
        "reason": "coordinator returned a live_gate summary for another run",
        "restart_count": 0,
        "run_id": "run-gate",
        "status": "failed",
    }
    assert private_run_id not in output


def test_main_non_mapping_gate_outcome_fails_closed(monkeypatch, capsys):
    async def fake_run(args):
        return ["PRIVATE_MAIN_VALUE"]

    monkeypatch.setattr(arena_module, "_run", fake_run)
    monkeypatch.setattr(arena_module, "build_args", lambda argv=None: object())

    with pytest.raises(SystemExit) as exc_info:
        arena_module.main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    lines = [line for line in output.splitlines() if line.startswith("LIVE_GATE ")]
    assert len(lines) == 1
    assert json.loads(lines[0][len("LIVE_GATE ") :]) == {
        "phase": "arena_result_validation",
        "reason": "arena returned an invalid live_gate summary",
        "restart_count": 0,
        "run_id": "unknown",
        "status": "failed",
    }
    assert "PRIVATE_MAIN_VALUE" not in output


def test_main_malformed_mapping_gate_outcome_fails_closed(monkeypatch, capsys):
    async def fake_run(args):
        return {
            "run_id": "PRIVATE_UNTRUSTED_MAIN_RUN_ID",
            "private_marker": "PRIVATE_MAPPING_VALUE",
        }

    monkeypatch.setattr(arena_module, "_run", fake_run)
    monkeypatch.setattr(arena_module, "build_args", lambda argv=None: object())

    with pytest.raises(SystemExit) as exc_info:
        arena_module.main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    lines = [line for line in output.splitlines() if line.startswith("LIVE_GATE ")]
    assert len(lines) == 1
    assert json.loads(lines[0][len("LIVE_GATE ") :]) == {
        "phase": "arena_result_validation",
        "reason": "arena returned an invalid live_gate summary",
        "restart_count": 0,
        "run_id": "unknown",
        "status": "failed",
    }
    assert "PRIVATE_" not in output


def test_main_without_gate_prints_no_gate_line(monkeypatch, capsys):
    async def fake_run(args):
        return None

    monkeypatch.setattr(arena_module, "_run", fake_run)
    monkeypatch.setattr(arena_module, "build_args", lambda argv=None: object())

    arena_module.main()

    assert "LIVE_GATE" not in capsys.readouterr().out
