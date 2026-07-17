import json
from pathlib import Path

import pytest

from civ_mcp.arena.channel_runtime import ChannelRuntime
from civ_mcp.arena.channels import DealState, PaymentStatus
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
from .live_gate_fakes import GateGameState, run_gate_round, run_gate_seat


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


def deals(runtime):
    return {deal.id: deal for deal in runtime.state.deals}


async def seed_capacity_deal(tmp_path, cfg, gs):
    run_dir, runtime = open_runtime(tmp_path, cfg)
    gs.active_player = 1
    admission = await runtime.admit_player(gs, 1, 9)
    admission.context.dispatch(
        "propose_deal",
        {
            "to_player": 2,
            "text": "Capacity seed deal",
            "favor": {
                "term_type": "maintain_gold_reserve",
                "params": {"min_gold": 0},
            },
            "payment_gold": 1,
            "timing": "on_delivery",
            "within": 1,
        },
    )
    acknowledgements = await runtime.finish_player(
        gs,
        admission,
        {"transcript": {"steps": [], "final_summary": ""}},
    )
    assert [acknowledgement.status for acknowledgement in acknowledgements] == [
        "applied"
    ]
    return run_dir, runtime


@pytest.mark.asyncio
async def test_round1_canary_proposal_and_acceptance(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    assert driver.pending_signal() is None

    state = runtime.state
    api_acks = [
        acknowledgement
        for acknowledgement in state.acknowledgements
        if acknowledgement.source_id.startswith("api:")
    ]
    assert len(api_acks) == 2
    assert all(acknowledgement.status == "applied" for acknowledgement in api_acks)
    run_id = driver.config.run_id
    assert all(
        acknowledgement.source_id.startswith(f"api:{run_id}:1:10:")
        for acknowledgement in api_acks
    )

    assert any(message.text == driver.canary for message in state.messages)
    projection_cli = runtime.project_for_player(2, 10)
    assert any(message.text == driver.canary for message in projection_cli.messages)

    cli_acks = [
        acknowledgement
        for acknowledgement in state.acknowledgements
        if acknowledgement.source_id.startswith("cli:")
    ]
    assert len(cli_acks) == 1
    assert cli_acks[0].status == "applied"
    assert cli_acks[0].source_id.startswith(f"cli:{run_id}:2:10:")

    deal_id = driver._journal.state.data["upfront_deal_id"]
    deal = deals(runtime)[deal_id]
    assert deal.state is DealState.ACTIVE
    assert deal.timing == "up_front"
    assert deal.payment_status is PaymentStatus.DUE
    assert deal.favor.term_type == "dont_trade_with"
    assert deal.favor.params["target_player"] == 3
    assert deal.proposer == 1 and deal.counterparty == 2
    assert driver._journal.state.phase == lgc.PHASE_FUND_UPFRONT


@pytest.mark.asyncio
async def test_round2_funding_offers_exact_payment(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_seat(driver, runtime, gs, 1, 11)

    deal_id = driver._journal.state.data["upfront_deal_id"]
    deal = deals(runtime)[deal_id]
    assert deal.payment_status is PaymentStatus.OFFERED
    fingerprint = driver._journal.state.data["upfront_payment_fingerprint"]
    assert fingerprint == {
        "payer": 1,
        "payee": 2,
        "gold": 1,
        "duration": 0,
        "item_count": 1,
    }
    from civ_mcp.lua.channel_payments import ExactPaymentOffer

    assert gs.pending[(1, 2)] == ExactPaymentOffer(1, 2, 1)


@pytest.mark.asyncio
async def test_cli_line_is_exact_single_channel_line(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    gs.active_player = 1
    admission1 = await runtime.admit_player(gs, 1, 10)
    driver.note_admission(1, 10, admission1, "")
    result1 = await driver.policy_for(1)(gs, 1, 10)
    await runtime.finish_player(gs, admission1, result1)
    await driver.after_seat_capture(
        player_id=1,
        turn=10,
        channel_fields={"enabled": True, "acknowledgements": 2, "error": ""},
    )

    gs.active_player = 2
    admission2 = await runtime.admit_player(gs, 2, 10)
    driver.note_admission(2, 10, admission2, "")
    result2 = await driver.policy_for(2)(gs, 2, 10)
    summary = result2["transcript"]["final_summary"]
    channel_lines = [line for line in summary.splitlines() if line.startswith("CHANNEL ")]
    assert len(channel_lines) == 1
    payload = json.loads(channel_lines[0][len("CHANNEL ") :])
    assert payload["action"] == "respond_to_deal"
    assert payload["accept"] is True
    assert payload["deal_id"] == driver._journal.state.data["upfront_deal_id"]
    assert admission2.context.staged_actions == []


@pytest.mark.asyncio
async def test_write_ahead_action_planned_before_dispatch(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    state = driver._journal.state
    verified = {entry["source_id"]: entry for entry in state.verified_actions}
    assert len(verified) == 3
    api_sources = [source for source in verified if source.startswith("api:")]
    cli_sources = [source for source in verified if source.startswith("cli:")]
    assert len(api_sources) == 2 and len(cli_sources) == 1
    assert all(entry["turn"] == 10 for entry in verified.values())
    assert state.pending_actions == ()


@pytest.mark.asyncio
async def test_rejected_acknowledgement_fails_closed_and_persists_result(tmp_path):
    rules = ChannelRules(
        acceptance_turns=3,
        funding_turns=2,
        payment_response_turns=2,
        max_active_deals_per_pair=1,
    )
    cfg = gate_config(channel_rules=rules)
    gs = GateGameState()
    run_dir, runtime = await seed_capacity_deal(tmp_path, cfg, gs)

    driver = lgc.ChannelsCoreDriver(cfg)
    await driver.attach(gs=gs, channel_runtime=runtime, run_dir=run_dir)
    await run_gate_seat(driver, runtime, gs, 1, 10)

    planned_rejections = [
        acknowledgement
        for acknowledgement in runtime.state.acknowledgements
        if acknowledgement.source_id.startswith(f"api:{cfg.run_id}:1:10:1:")
    ]
    assert len(planned_rejections) == 1
    assert planned_rejections[0].status == "rejected"
    assert driver.pending_signal() == GATE_FAILED
    assert "rejected" in driver._journal.state.reason
    assert driver._journal.result_path.exists()


@pytest.mark.asyncio
async def test_recovery_fails_closed_on_rejected_canonical_acknowledgement(tmp_path):
    rules = ChannelRules(
        acceptance_turns=3,
        funding_turns=2,
        payment_response_turns=2,
        max_active_deals_per_pair=1,
    )
    cfg = gate_config(channel_rules=rules)
    gs = GateGameState()
    run_dir, runtime = await seed_capacity_deal(tmp_path, cfg, gs)
    driver = lgc.ChannelsCoreDriver(cfg)
    await driver.attach(gs=gs, channel_runtime=runtime, run_dir=run_dir)

    gs.active_player = 1
    admission = await runtime.admit_player(gs, 1, 10)
    driver.note_admission(1, 10, admission, "")
    result = await driver.policy_for(1)(gs, 1, 10)
    acknowledgements = await runtime.finish_player(gs, admission, result)
    assert [acknowledgement.status for acknowledgement in acknowledgements] == [
        "applied",
        "rejected",
    ]
    assert len(driver._journal.state.pending_actions) == 2

    resumed = lgc.ChannelsCoreDriver(cfg)
    await resumed.attach(gs=gs, channel_runtime=runtime, run_dir=run_dir)
    gs.active_player = 2
    next_admission = await runtime.admit_player(gs, 2, 10)
    resumed.note_admission(2, 10, next_admission, "")

    assert resumed.pending_signal() == GATE_FAILED
    assert "recovered acknowledgement" in resumed._journal.state.reason
    assert "rejected" in resumed._journal.state.reason
    assert resumed._journal.result_path.exists()


@pytest.mark.asyncio
async def test_unexpected_channel_action_from_awaiting_role_fails(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_seat(driver, runtime, gs, 1, 11)

    gs.active_player = 2
    admission = await runtime.admit_player(gs, 2, 11)
    driver.note_admission(2, 11, admission, "")
    result = await driver.policy_for(2)(gs, 2, 11)
    summary = result.get("transcript", {}).get(
        "final_summary", result.get("summary", "")
    )
    result = {
        "transcript": {
            "steps": [],
            "final_summary": summary
            + '\nCHANNEL {"action": "send_message", "to_player": 1, "text": "rogue"}',
        }
    }
    acknowledgements = await runtime.finish_player(gs, admission, result)
    await driver.after_seat_capture(
        player_id=2,
        turn=11,
        channel_fields={
            "enabled": True,
            "acknowledgements": len(acknowledgements),
            "error": "",
        },
    )
    assert driver.pending_signal() == GATE_FAILED
    assert "unexpected" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_pending_actions_reissue_same_bound_player_and_turn(tmp_path):
    cfg = gate_config()
    driver, runtime, gs = await attached_driver(tmp_path, cfg=cfg)
    gs.active_player = 1
    admission = await runtime.admit_player(gs, 1, 10)
    driver.note_admission(1, 10, admission, "")
    await driver.policy_for(1)(gs, 1, 10)
    assert len(driver._journal.state.pending_actions) == 2

    resumed = lgc.ChannelsCoreDriver(cfg)
    await resumed.attach(gs=gs, channel_runtime=runtime, run_dir=driver._run_dir)
    replay_admission = await runtime.admit_player(gs, 1, 10)
    resumed.note_admission(1, 10, replay_admission, "")
    replay_result = await resumed.policy_for(1)(gs, 1, 10)
    assert len(resumed._journal.state.pending_actions) == 2
    acknowledgements = await runtime.finish_player(
        gs, replay_admission, replay_result
    )
    await resumed.after_seat_capture(
        player_id=1,
        turn=10,
        channel_fields={
            "enabled": True,
            "acknowledgements": len(acknowledgements),
            "error": "",
        },
    )

    assert resumed.pending_signal() is None
    assert resumed._journal.state.pending_actions == ()
    assert len(resumed._journal.state.verified_actions) == 2
    assert resumed._journal.state.phase == lgc.PHASE_ACCEPT_UPFRONT


@pytest.mark.asyncio
async def test_pending_applied_acknowledgements_recover_and_advance(tmp_path):
    cfg = gate_config()
    driver, runtime, gs = await attached_driver(tmp_path, cfg=cfg)
    gs.active_player = 1
    admission = await runtime.admit_player(gs, 1, 10)
    driver.note_admission(1, 10, admission, "")
    result = await driver.policy_for(1)(gs, 1, 10)
    acknowledgements = await runtime.finish_player(gs, admission, result)
    assert [acknowledgement.status for acknowledgement in acknowledgements] == [
        "applied",
        "applied",
    ]
    assert len(driver._journal.state.pending_actions) == 2

    resumed = lgc.ChannelsCoreDriver(cfg)
    await resumed.attach(gs=gs, channel_runtime=runtime, run_dir=driver._run_dir)
    gs.active_player = 2
    cli_admission = await runtime.admit_player(gs, 2, 10)
    resumed.note_admission(2, 10, cli_admission, "")

    assert resumed.pending_signal() is None
    assert resumed._journal.state.pending_actions == ()
    assert len(resumed._journal.state.verified_actions) == 2
    assert resumed._journal.state.phase == lgc.PHASE_ACCEPT_UPFRONT
    cli_result = await resumed.policy_for(2)(gs, 2, 10)
    channel_lines = [
        line
        for line in cli_result["transcript"]["final_summary"].splitlines()
        if line.startswith("CHANNEL ")
    ]
    assert len(channel_lines) == 1


@pytest.mark.asyncio
async def test_recovery_rejects_unexpected_ack_after_expected_actions_applied(
    tmp_path,
):
    cfg = gate_config()
    driver, runtime, gs = await attached_driver(tmp_path, cfg=cfg)
    gs.active_player = 1
    admission = await runtime.admit_player(gs, 1, 10)
    driver.note_admission(1, 10, admission, "")
    result = await driver.policy_for(1)(gs, 1, 10)
    admission.context.dispatch(
        "send_message", {"to_player": 3, "text": "rogue recovery action"}
    )
    acknowledgements = await runtime.finish_player(gs, admission, result)
    assert [acknowledgement.status for acknowledgement in acknowledgements] == [
        "applied",
        "applied",
        "applied",
    ]
    rogue = acknowledgements[-1]
    assert rogue.source_id.startswith(f"api:{cfg.run_id}:1:10:2:")
    assert len(driver._journal.state.pending_actions) == 2

    resumed = lgc.ChannelsCoreDriver(cfg)
    await resumed.attach(gs=gs, channel_runtime=runtime, run_dir=driver._run_dir)
    gs.active_player = 2
    next_admission = await runtime.admit_player(gs, 2, 10)
    resumed.note_admission(2, 10, next_admission, "")

    assert resumed.pending_signal() == GATE_FAILED
    assert "unexpected" in resumed._journal.state.reason
    assert rogue.source_id in resumed._journal.state.reason
    assert resumed._journal.result_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_player,resume_turn", [(1, 11), (2, 10)])
async def test_pending_actions_fail_closed_if_source_identity_cannot_recur(
    tmp_path, resume_player, resume_turn
):
    cfg = gate_config()
    driver, runtime, gs = await attached_driver(tmp_path, cfg=cfg)
    gs.active_player = 1
    admission = await runtime.admit_player(gs, 1, 10)
    driver.note_admission(1, 10, admission, "")
    await driver.policy_for(1)(gs, 1, 10)
    assert len(driver._journal.state.pending_actions) == 2

    resumed = lgc.ChannelsCoreDriver(cfg)
    await resumed.attach(gs=gs, channel_runtime=runtime, run_dir=driver._run_dir)
    gs.active_player = resume_player
    resume_admission = await runtime.admit_player(gs, resume_player, resume_turn)
    resumed.note_admission(
        resume_player, resume_turn, resume_admission, ""
    )

    assert resumed.pending_signal() == GATE_FAILED
    assert "cannot be reissued" in resumed._journal.state.reason
    assert resumed._journal.result_path.exists()
