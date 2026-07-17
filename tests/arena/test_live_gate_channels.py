from pathlib import Path

import pytest

from civ_mcp.arena.channel_runtime import ChannelRuntime
from civ_mcp.arena.config import (
    ArenaConfig,
    AttentionOptions,
    ChannelOptions,
    ChannelRules,
    CivOptions,
    LiveGateOptions,
    PlayerSpec,
    validate_arena_config,
)
from civ_mcp.arena.live_gate import GATE_FAILED, resolve_scenario
from civ_mcp.arena import live_gate_channels as lgc
from .live_gate_fakes import GateGameState, run_gate_seat


def gate_spec(player_id, provider, model=""):
    return PlayerSpec(
        player_id,
        provider,
        model,
        options=CivOptions(channels=ChannelOptions(enabled=True)),
    )


def gate_config(**overrides):
    kwargs = dict(
        players=[
            gate_spec(1, "local", "m"),
            gate_spec(2, "cli-codex"),
            gate_spec(3, "scripted"),
        ],
        max_puppet_turns=36,
        max_game_turns=36,
        run_id="arena-channels-core-gate-v1",
        channel_rules=ChannelRules(
            acceptance_turns=3,
            funding_turns=2,
            payment_response_turns=2,
        ),
        live_gate=LiveGateOptions(
            enabled=True,
            scenario=lgc.SCENARIO_NAME,
            roles=(("api_actor", 1), ("cli_actor", 2), ("privacy_observer", 3)),
        ),
    )
    kwargs.update(overrides)
    return ArenaConfig(**kwargs)


def test_scenario_registered_with_contracts():
    meta = resolve_scenario("unofficial_channels_core_v1")
    assert meta.revision == lgc.SCENARIO_REVISION
    assert meta.role_contracts == (
        ("api_actor", "in_process"),
        ("cli_actor", "cli"),
        ("privacy_observer", "scripted"),
    )


def test_minimum_captures_is_27_for_smoke_rules():
    # 9 expected rounds x 3 seats with the checked-in rules (spec budget note).
    assert lgc.minimum_captures(gate_config()) == 27


def test_minimum_captures_tracks_funding_turns():
    cfg = gate_config(
        channel_rules=ChannelRules(
            acceptance_turns=3,
            funding_turns=4,
            payment_response_turns=2,
        )
    )
    assert lgc.minimum_captures(cfg) == 33  # two extra withheld rounds x 3 seats


def test_gate_config_validates_end_to_end():
    validate_arena_config(gate_config())  # real registry entry, no fakes


def test_fingerprint_covers_identity_and_rules():
    cfg = gate_config()
    fp = lgc.gate_config_fingerprint(cfg)
    assert fp["scenario"] == "unofficial_channels_core_v1"
    assert fp["scenario_revision"] == lgc.SCENARIO_REVISION
    assert fp["run_id"] == "arena-channels-core-gate-v1"
    assert fp["roles"] == {"api_actor": 1, "cli_actor": 2, "privacy_observer": 3}
    assert fp["driver_kinds"] == {"1": "in_process", "2": "cli", "3": "scripted"}
    assert fp["channel_rules"] == cfg.channel_rules.fingerprint()
    assert fp["parameters"]["payment_gold"] == 1
    other_rules = gate_config(channel_rules=ChannelRules(funding_turns=3))
    assert lgc.gate_config_fingerprint(other_rules) != fp


def test_canary_deterministic_and_fingerprint_bound():
    cfg = gate_config()
    fp = lgc.gate_config_fingerprint(cfg)
    text = lgc.canary_text(cfg.run_id, fp)
    assert text == lgc.canary_text(cfg.run_id, fp)
    assert text.startswith("GATE-CANARY-")
    assert len(text) > len("GATE-CANARY-") + 16
    assert lgc.canary_text("other-run", fp) != text


def test_create_driver_binds_roles():
    driver = resolve_scenario(lgc.SCENARIO_NAME).create_driver(gate_config())
    assert isinstance(driver, lgc.ChannelsCoreDriver)
    assert driver.role_pid == {"api_actor": 1, "cli_actor": 2, "privacy_observer": 3}


def open_runtime(tmp_path, cfg):
    run_dir = Path(tmp_path) / cfg.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )


async def attached_driver(tmp_path, cfg=None, gs=None):
    cfg = cfg or gate_config()
    gs = gs or GateGameState()
    run_dir, runtime = open_runtime(tmp_path, cfg)
    driver = lgc.ChannelsCoreDriver(cfg)
    await driver.attach(gs=gs, channel_runtime=runtime, run_dir=run_dir)
    return driver, runtime, gs


@pytest.mark.asyncio
async def test_attach_opens_gate_journal_in_preflight(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    state = driver._journal.state
    assert state.phase == lgc.PHASE_PREFLIGHT
    assert state.scenario == lgc.SCENARIO_NAME
    assert state.config_fingerprint == driver.fingerprint
    assert (Path(tmp_path) / gate_config().run_id / "live_gate" / "events.jsonl").exists()


@pytest.mark.asyncio
async def test_attach_requires_channel_runtime(tmp_path):
    driver = lgc.ChannelsCoreDriver(gate_config())
    with pytest.raises(Exception):
        await driver.attach(
            gs=GateGameState(),
            channel_runtime=None,
            run_dir=Path(tmp_path) / "r",
        )


def test_policy_for_returns_role_policies_with_spec_identity():
    driver = lgc.ChannelsCoreDriver(gate_config())
    api = driver.policy_for(1)
    cli = driver.policy_for(2)
    observer = driver.policy_for(3)
    assert api.provider == "local" and api.model == "m"
    assert cli.provider == "cli-codex"
    assert observer.provider == "scripted"
    with pytest.raises(KeyError):
        driver.policy_for(9)


@pytest.mark.asyncio
async def test_preflight_runs_once_and_advances_phase(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    state = driver._journal.state
    assert driver.pending_signal() is None
    assert state.phase != lgc.PHASE_PREFLIGHT
    assert any(entry.get("kind") == "preflight" for entry in state.observations)
    assert gs.skipped >= 1


@pytest.mark.asyncio
async def test_preflight_missing_family_fails_closed(tmp_path):
    from civ_mcp.arena.channel_terms import ObservationFamily

    gs = GateGameState()
    gs.missing_families[2] = frozenset({ObservationFamily.TRADE_ROUTES})
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    assert driver.pending_signal() == GATE_FAILED
    assert "trade_routes" in driver._journal.state.reason
    assert driver._journal.result_path.exists()


@pytest.mark.asyncio
async def test_preflight_observation_error_fails_closed(tmp_path):
    gs = GateGameState()
    gs.observation_errors[1] = ("LUA_ERROR|boom",)
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    assert driver.pending_signal() == GATE_FAILED


@pytest.mark.asyncio
async def test_preflight_insufficient_gold_fails_closed(tmp_path):
    gs = GateGameState()
    gs.treasury[1] = 0
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    assert driver.pending_signal() == GATE_FAILED
    assert "gold" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_preflight_conflicting_pending_trade_fails_closed(tmp_path):
    from civ_mcp.lua.channel_payments import ExactPaymentOffer

    gs = GateGameState()
    gs.pending[(1, 2)] = ExactPaymentOffer(1, 2, 1)
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    assert driver.pending_signal() == GATE_FAILED
    assert "pending" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_missing_admission_fails_closed(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    driver.note_admission(1, 10, None, "admission exploded")
    assert driver.pending_signal() == GATE_FAILED
    assert "admission" in driver._journal.state.reason
