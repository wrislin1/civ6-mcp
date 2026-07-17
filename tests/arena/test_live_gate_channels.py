import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from civ_mcp.arena.channel_runtime import ChannelRuntime
from civ_mcp.arena.channels import (
    ChannelAcknowledgement,
    DealState,
    FavorStatus,
    PaymentStatus,
)
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
from civ_mcp.arena.live_gate import (
    GATE_ACTIVE,
    GATE_FAILED,
    GATE_PASSED,
    GATE_RESTART_REQUIRED,
    resolve_scenario,
)
from civ_mcp.arena import live_gate_channels as lgc
from civ_mcp.arena.transcript import TranscriptSink, serialize_transcript_record
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


async def drive_to_restart(tmp_path, gs=None):
    gs = gs or GateGameState()
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_round(driver, runtime, gs, 11)
    return driver, runtime, gs


@pytest.mark.asyncio
async def test_restart_defers_to_round_boundary(tmp_path):
    gs = GateGameState()
    driver, runtime, _ = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)

    await run_gate_seat(driver, runtime, gs, 1, 11)
    assert driver.pending_signal() is None
    await run_gate_seat(driver, runtime, gs, 2, 11)
    assert driver.pending_signal() is None
    await run_gate_seat(driver, runtime, gs, 3, 11)
    assert driver.pending_signal() == GATE_RESTART_REQUIRED


@pytest.mark.asyncio
async def test_restart_checkpoint_persists_fingerprint_and_result(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)

    state = driver._journal.state
    assert state.status == GATE_RESTART_REQUIRED
    assert state.restart_count == 1
    canonical = dict(state.data["upfront_payment_fingerprint"])
    assert canonical["gold"] == 1
    assert dict(state.data["restart_offer_fingerprint_before"]) == canonical
    assert "restart_offer_fingerprint_after" not in state.data
    result = json.loads(driver._journal.result_path.read_text())
    assert result["status"] == GATE_RESTART_REQUIRED


@pytest.mark.asyncio
async def test_restart_live_fingerprint_mismatch_fails(tmp_path):
    gs = GateGameState()
    driver, runtime, _ = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_seat(driver, runtime, gs, 1, 11)

    gs.pending.clear()
    await run_gate_seat(driver, runtime, gs, 2, 11)
    await run_gate_seat(driver, runtime, gs, 3, 11)

    assert driver.pending_signal() == GATE_FAILED
    assert (
        "pending" in driver._journal.state.reason
        or "fingerprint" in driver._journal.state.reason
    )


@pytest.mark.asyncio
async def test_resume_verifies_offer_and_continues(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id

    runtime2 = ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)

    assert driver2.pending_signal() is None
    state = driver2._journal.state
    assert state.status == GATE_ACTIVE
    assert state.phase == lgc.PHASE_ACCEPT_UPFRONT_PAYMENT
    assert state.restart_count == 1
    canonical = dict(state.data["upfront_payment_fingerprint"])
    assert dict(state.data["restart_offer_fingerprint_before"]) == canonical
    assert dict(state.data["restart_offer_fingerprint_after"]) == canonical

    await run_gate_round(driver2, runtime2, gs, 12)
    deal = deals(runtime2)[state.data["upfront_deal_id"]]
    assert deal.payment_status is PaymentStatus.SETTLED
    assert (1, 2) not in gs.pending
    assert gs.treasury[2] == 501


@pytest.mark.asyncio
async def test_resume_with_changed_offer_fails(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    from civ_mcp.lua.channel_payments import ExactPaymentOffer

    gs.pending[(1, 2)] = ExactPaymentOffer(1, 2, 5)
    runtime2 = ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)

    assert driver2.pending_signal() == GATE_FAILED


@pytest.mark.asyncio
async def test_resume_with_absent_offer_fails(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id

    gs.pending.clear()
    runtime2 = ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)

    assert driver2.pending_signal() == GATE_FAILED


@pytest.mark.asyncio
async def test_resume_fails_if_same_turn_channel_sequence_changed(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    checkpoint_sequence = driver._journal.state.data[
        "restart_channel_sequence"
    ]
    runtime2 = ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )

    gs.active_player = 1
    admission = await runtime2.admit_player(gs, 1, 11)
    admission.context.dispatch(
        "send_message",
        {"to_player": 3, "text": "same-turn restart-gap event"},
    )
    acknowledgements = await runtime2.finish_player(
        gs,
        admission,
        {"transcript": {"steps": [], "final_summary": ""}},
    )
    assert len(acknowledgements) == 1
    assert acknowledgements[0].status == "applied"
    assert runtime2.state.last_event_sequence > checkpoint_sequence
    payment_state = await gs.get_channel_payment_state(1, 2, 1)
    assert payment_state.status == "exact"

    runtime3 = ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )
    resumed = lgc.ChannelsCoreDriver(cfg)
    await resumed.attach(gs=gs, channel_runtime=runtime3, run_dir=run_dir)

    assert resumed.pending_signal() == GATE_FAILED
    assert "sequence" in resumed._journal.state.reason
    assert resumed._journal.result_path.exists()


@pytest.mark.asyncio
async def test_resume_rejects_same_turn_payment_response_acknowledgement(
    tmp_path,
):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    runtime2 = ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )
    deal_id = driver._journal.state.data["upfront_deal_id"]

    gs.active_player = 2
    admission = await runtime2.admit_player(gs, 2, 11)
    acknowledgements = await runtime2.finish_player(
        gs,
        admission,
        {
            "transcript": {
                "steps": [],
                "final_summary": (
                    'CHANNEL {"action":"respond_to_payment",'
                    f'"deal_id":"{deal_id}","accept":true}}'
                ),
            }
        },
    )
    assert len(acknowledgements) == 1
    assert acknowledgements[0].status == "applied"

    from civ_mcp.lua.channel_payments import ExactPaymentOffer

    gs.pending[(1, 2)] = ExactPaymentOffer(1, 2, 1)
    runtime3 = ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )
    resumed = lgc.ChannelsCoreDriver(cfg)
    await resumed.attach(gs=gs, channel_runtime=runtime3, run_dir=run_dir)

    assert resumed.pending_signal() == GATE_FAILED
    assert "sequence" in resumed._journal.state.reason
    assert runtime3.deal(deal_id).payment_status is PaymentStatus.SETTLED


@pytest.mark.asyncio
async def test_resume_config_fingerprint_mismatch_fails(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    cfg = gate_config(channel_rules=ChannelRules(funding_turns=3))
    run_dir = Path(tmp_path) / gate_config().run_id
    runtime2 = ChannelRuntime.open(
        run_dir,
        cfg.run_id,
        frozenset({1, 2, 3}),
        gate_config().channel_rules,
    )
    driver2 = lgc.ChannelsCoreDriver(cfg)

    with pytest.raises(Exception, match="fingerprint"):
        await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)


async def full_run(tmp_path, *, stop_before_round=None):
    """Drive both invocations of the expected nine-round gate path."""

    gs = GateGameState()
    driver, runtime, _ = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)  # R1
    await run_gate_round(driver, runtime, gs, 11)  # R2 -> restart
    assert driver.pending_signal() == GATE_RESTART_REQUIRED

    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    runtime2 = ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)
    for offset, turn in enumerate(range(12, 19), start=3):  # R3..R9
        if stop_before_round is not None and offset >= stop_before_round:
            break
        await run_gate_round(driver2, runtime2, gs, turn)
        if driver2.pending_signal() is not None:
            break
    return driver2, runtime2, gs


@pytest.mark.asyncio
async def test_upfront_deal_honored_on_inclusive_deadline(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=5)
    # R3 settled at turn 12; favor due turn 13 (R4) — honored there, not earlier.
    deal = deals(runtime)[driver._journal.state.data["upfront_deal_id"]]
    assert deal.state is DealState.HONORED
    assert deal.favor_status is FavorStatus.SATISFIED
    assert deal.favor_due_turn == 13
    assert deal.favor.baseline
    assert all(
        value is True
        for key, value in deal.favor.baseline.items()
        if key.endswith("baseline_complete")
    )


@pytest.mark.asyncio
async def test_upfront_not_terminal_before_deadline(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=4)
    # After R3 (turn 12) the deal must be nonterminal: favor window still open.
    deal = deals(runtime)[driver._journal.state.data["upfront_deal_id"]]
    assert deal.state is DealState.ACTIVE
    assert driver.pending_signal() is None


@pytest.mark.asyncio
async def test_existing_routes_are_baseline_exempt(tmp_path):
    from civ_mcp.arena.channel_terms import ObservedRoute

    gs = GateGameState()
    # This route predates payment settlement, so the settlement baseline exempts it.
    gs.routes[2] = (
        ObservedRoute(
            owner_id=2,
            trader_unit_id=77,
            destination_player=3,
            destination_is_city_state=False,
        ),
    )
    driver, runtime, _ = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_round(driver, runtime, gs, 11)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    runtime2 = ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)
    await run_gate_round(driver2, runtime2, gs, 12)
    await run_gate_round(driver2, runtime2, gs, 13)
    deal = deals(runtime2)[driver2._journal.state.data["upfront_deal_id"]]
    assert deal.state is DealState.HONORED


@pytest.mark.asyncio
async def test_on_delivery_proposed_accepted_and_treasury_satisfied(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=8)
    # R5 (turn 14) CLI proposes; R6 (turn 15) API accepts; R7 (turn 16) favor due.
    deal_id = driver._journal.state.data["on_delivery_deal_id"]
    deal = deals(runtime)[deal_id]
    assert deal.proposer == 2 and deal.counterparty == 1
    assert deal.timing == "on_delivery"
    assert deal.favor.term_type == "maintain_gold_reserve"
    assert deal.favor_status is FavorStatus.SATISFIED
    assert deal.payment_status is PaymentStatus.DUE
    assert deal.fund_by_turn == 18


@pytest.mark.asyncio
async def test_withholding_does_not_breach_early(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=9)
    # After R8 (turn 17), before fund_by (18), the deal remains nonterminal.
    deal = deals(runtime)[driver._journal.state.data["on_delivery_deal_id"]]
    assert deal.state is DealState.ACTIVE
    assert driver.pending_signal() is None


@pytest.mark.asyncio
async def test_full_run_breaches_grieves_and_passes(tmp_path):
    driver, runtime, gs = await full_run(tmp_path)
    assert driver.pending_signal() == GATE_PASSED

    state = runtime.state
    on_delivery = deals(runtime)[driver._journal.state.data["on_delivery_deal_id"]]
    assert on_delivery.state is DealState.BROKEN
    assert on_delivery.payment_status is PaymentStatus.FAILED

    assert len(state.grievances) == 1
    grievance = state.grievances[0]
    assert grievance.offender == 2
    assert grievance.wronged == 1
    assert grievance.deal_id == on_delivery.id

    upfront = deals(runtime)[driver._journal.state.data["upfront_deal_id"]]
    assert upfront.state is DealState.HONORED
    assert upfront.payment_status is PaymentStatus.SETTLED

    gate_state = driver._journal.state
    assert gate_state.status == GATE_PASSED
    result = json.loads(driver._journal.result_path.read_text())
    assert result["status"] == GATE_PASSED


@pytest.mark.asyncio
async def test_premature_terminal_state_fails_gate(tmp_path):
    gs = GateGameState()
    driver, runtime, _ = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_round(driver, runtime, gs, 11)
    cfg = gate_config()
    run_dir = Path(tmp_path) / cfg.run_id
    runtime2 = ChannelRuntime.open(
        run_dir, cfg.run_id, frozenset({1, 2, 3}), cfg.channel_rules
    )
    driver2 = lgc.ChannelsCoreDriver(cfg)
    await driver2.attach(gs=gs, channel_runtime=runtime2, run_dir=run_dir)
    await run_gate_round(driver2, runtime2, gs, 12)
    # A new route after settlement violates the continuous term on the due turn.
    from civ_mcp.arena.channel_terms import ObservedRoute

    gs.routes[2] = (
        ObservedRoute(
            owner_id=2,
            trader_unit_id=99,
            destination_player=3,
            destination_is_city_state=False,
        ),
    )
    await run_gate_round(driver2, runtime2, gs, 13)
    assert driver2.pending_signal() == GATE_FAILED


@pytest.mark.asyncio
async def test_upfront_responsible_capture_requires_deadline_transition(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=3)
    await run_gate_seat(driver, runtime, gs, 1, 12)
    await run_gate_seat(driver, runtime, gs, 2, 12)
    deal = deals(runtime)[driver._journal.state.data["upfront_deal_id"]]
    assert deal.counterparty == 2
    assert deal.state is DealState.ACTIVE
    assert deal.favor_status is FavorStatus.DUE
    assert deal.payment_status is PaymentStatus.SETTLED

    # The gate's persisted deadline is deliberately earlier than the runtime's
    # canonical deadline. At that persisted boundary the responsible actor's
    # real capture leaves the exact pending tuple in place, so the driver must
    # fail immediately instead of waiting for a later round.
    driver._journal.append(
        "data_recorded", {"data": {"upfront_favor_due_turn": 12}}
    )
    await run_gate_seat(driver, runtime, gs, deal.counterparty, 12)

    assert driver.pending_signal() == GATE_FAILED
    assert "deadline" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_on_delivery_responsible_capture_requires_deadline_transition(
    tmp_path,
):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=7)
    deal = deals(runtime)[driver._journal.state.data["on_delivery_deal_id"]]
    assert deal.counterparty == 1
    assert deal.state is DealState.ACTIVE
    assert deal.favor_status is FavorStatus.DUE
    assert deal.payment_status is PaymentStatus.NOT_DUE

    driver._journal.append(
        "data_recorded", {"data": {"on_delivery_favor_due_turn": 15}}
    )
    await run_gate_seat(driver, runtime, gs, deal.counterparty, 15)

    assert driver.pending_signal() == GATE_FAILED
    assert "deadline" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_funding_responsible_capture_requires_deadline_transition(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=8)
    deal = deals(runtime)[driver._journal.state.data["on_delivery_deal_id"]]
    assert deal.proposer == 2
    assert deal.state is DealState.ACTIVE
    assert deal.favor_status is FavorStatus.SATISFIED
    assert deal.payment_status is PaymentStatus.DUE

    driver._journal.append(
        "data_recorded", {"data": {"on_delivery_fund_by_turn": 17}}
    )
    await run_gate_seat(driver, runtime, gs, deal.proposer, 17)

    assert driver.pending_signal() == GATE_FAILED
    assert "deadline" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_upfront_completed_transition_after_deadline_fails(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=4)
    deal = deals(runtime)[driver._journal.state.data["upfront_deal_id"]]
    driver._journal.append(
        "data_recorded", {"data": {"upfront_favor_due_turn": 12}}
    )

    await run_gate_seat(driver, runtime, gs, deal.counterparty, 13)

    changed = deals(runtime)[deal.id]
    assert changed.state is DealState.HONORED
    assert driver.pending_signal() == GATE_FAILED
    assert "after" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_on_delivery_completed_transition_after_deadline_fails(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=7)
    deal = deals(runtime)[driver._journal.state.data["on_delivery_deal_id"]]
    driver._journal.append(
        "data_recorded", {"data": {"on_delivery_favor_due_turn": 15}}
    )

    await run_gate_seat(driver, runtime, gs, deal.counterparty, 16)

    changed = deals(runtime)[deal.id]
    assert changed.favor_status is FavorStatus.SATISFIED
    assert changed.payment_status is PaymentStatus.DUE
    assert driver.pending_signal() == GATE_FAILED
    assert "after" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_funding_completed_transition_after_deadline_fails(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=8)
    deal = deals(runtime)[driver._journal.state.data["on_delivery_deal_id"]]
    driver._journal.append(
        "data_recorded", {"data": {"on_delivery_fund_by_turn": 17}}
    )

    await run_gate_seat(driver, runtime, gs, deal.proposer, 18)

    changed = deals(runtime)[deal.id]
    assert changed.state is DealState.BROKEN
    assert changed.payment_status is PaymentStatus.FAILED
    assert driver.pending_signal() == GATE_FAILED
    assert "after" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_on_delivery_premature_nonterminal_success_fails(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=7)
    deal = deals(runtime)[driver._journal.state.data["on_delivery_deal_id"]]
    assert deal.favor_due_turn == 16
    driver._journal.append(
        "data_recorded", {"data": {"on_delivery_favor_due_turn": 17}}
    )

    await run_gate_seat(driver, runtime, gs, deal.counterparty, 16)
    changed = deals(runtime)[deal.id]
    assert changed.state is DealState.ACTIVE
    assert changed.favor_status is FavorStatus.SATISFIED
    assert changed.payment_status is PaymentStatus.DUE
    assert driver.pending_signal() == GATE_FAILED
    assert "before" in driver._journal.state.reason


@pytest.mark.asyncio
async def test_on_delivery_id_is_bound_to_current_cli_capture(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=5)
    assert driver._journal.state.phase == lgc.PHASE_PROPOSE_ON_DELIVERY

    stale_payload = {
        "action": "propose_deal",
        "to_player": 1,
        "text": "Historical verified CLI deal",
        "favor": {
            "term_type": "maintain_gold_reserve",
            "params": {"min_gold": 0},
        },
        "payment_gold": 1,
        "timing": "on_delivery",
        "within": 1,
    }
    stale_line = "CHANNEL " + json.dumps(
        stale_payload, sort_keys=True, separators=(",", ":")
    )
    gs.active_player = 2
    stale_admission = await runtime.admit_player(gs, 2, 13)
    stale_acknowledgements = await runtime.finish_player(
        gs,
        stale_admission,
        {"transcript": {"steps": [], "final_summary": stale_line}},
    )
    assert len(stale_acknowledgements) == 1
    stale_ack = stale_acknowledgements[0]
    assert stale_ack.status == "applied"
    assert stale_ack.deal_id
    stale_digest = stale_ack.source_id.rsplit(":", 1)[1]
    driver._journal.append(
        "action_planned",
        {
            "turn": 13,
            "player_id": 2,
            "phase": lgc.PHASE_PROPOSE_ON_DELIVERY,
            "name": "propose_deal",
            "source_id": stale_ack.source_id,
            "payload_digest": stale_digest,
            "line_index": 0,
        },
    )
    driver._journal.append(
        "action_verified",
        {
            "source_id": stale_ack.source_id,
            "turn": 13,
            "deal_id": stale_ack.deal_id,
        },
    )

    await run_gate_round(driver, runtime, gs, 14)

    current = [
        acknowledgement
        for acknowledgement in runtime.state.acknowledgements
        if acknowledgement.player_id == 2
        and acknowledgement.turn == 14
        and acknowledgement.status == "applied"
        and acknowledgement.deal_id
    ]
    assert len(current) == 1
    assert current[0].deal_id != stale_ack.deal_id
    assert driver.pending_signal() is None
    assert driver._journal.state.data["on_delivery_deal_id"] == current[0].deal_id


PRIVACY_KINDS = {
    "projection",
    "channel_block",
    "opening_prompt",
    "acknowledgements",
    "policy_result",
    "pending_transcript_record",
    "transcript_records",
}


def privacy_assertions(driver):
    return driver._journal.state.privacy_assertions


@pytest.mark.asyncio
async def test_observer_assertions_cover_all_artifacts_every_admission(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)

    await run_gate_round(driver, runtime, gs, 10)
    first = privacy_assertions(driver)
    assert {item["artifact_kind"] for item in first} == PRIVACY_KINDS
    assert all(item["result"] == "PASS" for item in first)

    await run_gate_round(driver, runtime, gs, 11)
    second = privacy_assertions(driver)[len(first) :]
    assert {item["artifact_kind"] for item in second} == PRIVACY_KINDS
    assert all(item["result"] == "PASS" for item in second)


@pytest.mark.asyncio
async def test_observer_projection_is_empty_of_participants(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)

    await run_gate_round(driver, runtime, gs, 10)

    projection = runtime.project_for_player(3, 10)
    assert projection.messages == ()
    assert projection.deals == ()
    assert projection.grievances == ()
    assert projection.acknowledgements == ()
    assert driver.canary not in str(projection)


@pytest.mark.asyncio
async def test_pending_transcript_leak_fails_before_fake_persists(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    await run_gate_seat(driver, runtime, gs, 2, 10)

    await run_gate_seat(
        driver,
        runtime,
        gs,
        3,
        10,
        pending_record_overrides={"planted": driver.canary},
    )

    assert driver.pending_signal() == GATE_FAILED
    assertions = privacy_assertions(driver)
    assert {item["artifact_kind"] for item in assertions} == PRIVACY_KINDS
    pending = next(
        item for item in assertions if item["artifact_kind"] == "pending_transcript_record"
    )
    assert pending["result"] == "FAIL"
    transcript_path = driver._run_dir / "transcript.jsonl"
    assert not transcript_path.exists() or driver.canary not in transcript_path.read_text()
    assert driver.canary not in driver._journal.state.reason
    assert driver.canary not in driver._journal.result_path.read_text()
    assert driver.canary not in json.dumps(driver.result_summary())


@pytest.mark.asyncio
async def test_pending_transcript_reordered_payment_fingerprint_fails(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_seat(driver, runtime, gs, 1, 11)
    await run_gate_seat(driver, runtime, gs, 2, 11)
    fingerprint = dict(driver._journal.state.data["upfront_payment_fingerprint"])
    reordered = {key: fingerprint[key] for key in reversed(sorted(fingerprint))}

    await run_gate_seat(
        driver,
        runtime,
        gs,
        3,
        11,
        pending_record_overrides={"planted_fingerprint": reordered},
    )

    pending = next(
        assertion
        for assertion in reversed(privacy_assertions(driver))
        if assertion["artifact_kind"] == "pending_transcript_record"
    )
    assert pending["result"] == "FAIL"
    assert driver.pending_signal() == GATE_FAILED


async def ready_for_fingerprint_observer(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_seat(driver, runtime, gs, 1, 11)
    await run_gate_seat(driver, runtime, gs, 2, 11)
    fingerprint = dict(driver._journal.state.data["upfront_payment_fingerprint"])
    return driver, runtime, gs, fingerprint


def observer_records(driver):
    path = driver._run_dir / "transcript.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if json.loads(line).get("player_id") == 3
    ]


@pytest.mark.asyncio
async def test_flattened_outer_pending_payment_fingerprint_fails_closed(tmp_path):
    driver, runtime, gs, fingerprint = await ready_for_fingerprint_observer(tmp_path)

    await run_gate_seat(
        driver,
        runtime,
        gs,
        3,
        11,
        pending_record_overrides=fingerprint,
    )

    pending = next(
        assertion
        for assertion in reversed(privacy_assertions(driver))
        if assertion["artifact_kind"] == "pending_transcript_record"
    )
    assert pending["result"] == "FAIL"
    assert driver.pending_signal() == GATE_FAILED
    assert all(record.get("turn") != 11 for record in observer_records(driver))
    assert driver.canary not in driver._journal.state.reason
    assert json.dumps(fingerprint, sort_keys=True) not in driver._journal.state.reason
    assert (
        json.dumps(fingerprint, sort_keys=True)
        not in driver._journal.result_path.read_text()
    )
    assert list(driver._journal.gate_dir.glob("privacy_fail_11_*.json"))


@pytest.mark.asyncio
async def test_nested_payment_fingerprint_with_extra_key_fails(tmp_path):
    driver, runtime, gs, fingerprint = await ready_for_fingerprint_observer(tmp_path)
    with_extra = {**fingerprint, "zz_public_extra": "allowed-surrounding-field"}

    await run_gate_seat(
        driver,
        runtime,
        gs,
        3,
        11,
        pending_record_overrides={"nested": with_extra},
    )

    pending = next(
        assertion
        for assertion in reversed(privacy_assertions(driver))
        if assertion["artifact_kind"] == "pending_transcript_record"
    )
    assert pending["result"] == "FAIL"
    assert all(record.get("turn") != 11 for record in observer_records(driver))


@pytest.mark.asyncio
async def test_pretty_reordered_json_fingerprint_in_raw_observer_text_fails(
    tmp_path, monkeypatch
):
    driver, runtime, gs, fingerprint = await ready_for_fingerprint_observer(tmp_path)
    reordered = {key: fingerprint[key] for key in reversed(sorted(fingerprint))}
    planted = json.dumps(reordered, indent=2)
    original = runtime.admit_player

    async def tainted(gs_arg, player_id, turn):
        admission = await original(gs_arg, player_id, turn)
        if player_id == 3:
            return dataclasses.replace(
                admission,
                block=admission.block + "\nPRIVATE JSON\n" + planted,
            )
        return admission

    monkeypatch.setattr(runtime, "admit_player", tainted)
    await run_gate_seat(driver, runtime, gs, 3, 11)

    failures = {
        assertion["artifact_kind"]
        for assertion in privacy_assertions(driver)
        if assertion["turn"] == 11 and assertion["result"] == "FAIL"
    }
    assert {"channel_block", "opening_prompt"} <= failures
    assert driver.pending_signal() == GATE_FAILED
    assert all(record.get("turn") != 11 for record in observer_records(driver))


@pytest.mark.asyncio
async def test_json_string_leaf_payment_fingerprint_fails(tmp_path):
    driver, runtime, gs, fingerprint = await ready_for_fingerprint_observer(tmp_path)
    stringified = json.dumps(fingerprint, indent=2)

    await run_gate_seat(
        driver,
        runtime,
        gs,
        3,
        11,
        pending_record_overrides={"string_leaf": stringified},
    )

    pending = next(
        assertion
        for assertion in reversed(privacy_assertions(driver))
        if assertion["artifact_kind"] == "pending_transcript_record"
    )
    assert pending["result"] == "FAIL"


@pytest.mark.asyncio
async def test_double_encoded_json_string_leaf_payment_fingerprint_fails(tmp_path):
    driver, runtime, gs, fingerprint = await ready_for_fingerprint_observer(tmp_path)
    double_encoded = json.dumps(json.dumps(fingerprint))

    await run_gate_seat(
        driver,
        runtime,
        gs,
        3,
        11,
        pending_record_overrides={"double_encoded": double_encoded},
    )

    pending = next(
        assertion
        for assertion in reversed(privacy_assertions(driver))
        if assertion["artifact_kind"] == "pending_transcript_record"
    )
    assert pending["result"] == "FAIL"
    assert driver.pending_signal() == GATE_FAILED
    assert all(record.get("turn") != 11 for record in observer_records(driver))
    journal = driver._journal
    assert journal is not None
    forensic = journal.gate_dir / "privacy_fail_11_pending_transcript_record.json"
    preserved_record = json.loads(json.loads(forensic.read_text())["input"])
    assert preserved_record["double_encoded"] == double_encoded


@pytest.mark.asyncio
async def test_double_encoded_json_fingerprint_in_raw_observer_text_fails(
    tmp_path, monkeypatch
):
    driver, runtime, gs, fingerprint = await ready_for_fingerprint_observer(tmp_path)
    planted = json.dumps(json.dumps(fingerprint))
    original = runtime.admit_player

    async def tainted(gs_arg, player_id, turn):
        admission = await original(gs_arg, player_id, turn)
        if player_id == 3:
            return dataclasses.replace(
                admission,
                block=admission.block + "\nPRIVATE DOUBLE JSON\n" + planted,
            )
        return admission

    monkeypatch.setattr(runtime, "admit_player", tainted)
    await run_gate_seat(driver, runtime, gs, 3, 11)

    failures = {
        assertion["artifact_kind"]
        for assertion in privacy_assertions(driver)
        if assertion["turn"] == 11 and assertion["result"] == "FAIL"
    }
    assert {"channel_block", "opening_prompt"} <= failures
    assert driver.pending_signal() == GATE_FAILED
    assert all(record.get("turn") != 11 for record in observer_records(driver))
    journal = driver._journal
    assert journal is not None
    for kind in ("channel_block", "opening_prompt"):
        forensic = journal.gate_dir / f"privacy_fail_11_{kind}.json"
        assert planted in json.loads(forensic.read_text())["input"]


@pytest.mark.asyncio
@pytest.mark.parametrize("budget_case", ["characters", "attempts", "depth"])
async def test_privacy_scan_budget_exhaustion_fails_closed(
    tmp_path, budget_case
):
    driver, runtime, gs, _fingerprint = await ready_for_fingerprint_observer(tmp_path)
    marker = f"PRIVATE-SCAN-BUDGET-{budget_case}"
    if budget_case == "characters":
        planted = marker + ("x" * 1_000_001)
    elif budget_case == "attempts":
        planted = marker + ("{" * 257)
    else:
        planted = marker
        for _ in range(10):
            planted = [planted]

    await run_gate_seat(
        driver,
        runtime,
        gs,
        3,
        11,
        pending_record_overrides={"scan_budget": planted},
    )

    assert driver.pending_signal() == GATE_FAILED
    journal = driver._journal
    assert journal is not None
    assert journal.state.reason == "observer privacy inspection failed at turn 11"
    assert marker not in journal.result_path.read_text()
    assert marker not in json.dumps(driver.result_summary())
    assert all(record.get("turn") != 11 for record in observer_records(driver))


@pytest.mark.asyncio
async def test_payment_fingerprint_near_match_and_type_mismatch_are_not_leaks(tmp_path):
    driver, runtime, gs, fingerprint = await ready_for_fingerprint_observer(tmp_path)
    missing_key = dict(fingerprint)
    missing_key.pop("item_count")
    type_mismatch = dict(fingerprint)
    type_mismatch["payer"] = str(type_mismatch["payer"])

    await run_gate_seat(
        driver,
        runtime,
        gs,
        3,
        11,
        pending_record_overrides={
            "near_match": missing_key,
            "type_mismatch": type_mismatch,
        },
    )

    turn_assertions = [
        assertion
        for assertion in privacy_assertions(driver)
        if assertion["turn"] == 11
    ]
    assert {assertion["artifact_kind"] for assertion in turn_assertions} == PRIVACY_KINDS
    assert all(assertion["result"] == "PASS" for assertion in turn_assertions)
    assert driver.pending_signal() == GATE_RESTART_REQUIRED
    assert any(record.get("turn") == 11 for record in observer_records(driver))


@pytest.mark.asyncio
async def test_planted_canary_in_observer_view_fails_all_tainted_artifacts(
    tmp_path, monkeypatch
):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    await run_gate_seat(driver, runtime, gs, 2, 10)
    original = runtime.admit_player

    async def tainted(gs_arg, player_id, turn):
        admission = await original(gs_arg, player_id, turn)
        if player_id == 3:
            return dataclasses.replace(
                admission, block=admission.block + "\n" + driver.canary
            )
        return admission

    monkeypatch.setattr(runtime, "admit_player", tainted)
    await run_gate_seat(driver, runtime, gs, 3, 10)

    assert driver.pending_signal() == GATE_FAILED
    assertions = privacy_assertions(driver)
    assert {item["artifact_kind"] for item in assertions} == PRIVACY_KINDS
    failed = {item["artifact_kind"] for item in assertions if item["result"] == "FAIL"}
    assert {
        "channel_block",
        "opening_prompt",
        "policy_result",
        "pending_transcript_record",
    } <= failed
    forensic = list(driver._journal.gate_dir.glob("privacy_fail_10_*.json"))
    assert forensic
    assert any(driver.canary in path.read_text() for path in forensic)
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in forensic)
    assert driver.canary not in driver._journal.state.reason
    assert driver.canary not in driver._journal.result_path.read_text()
    transcript_path = driver._run_dir / "transcript.jsonl"
    assert not transcript_path.exists() or driver.canary not in transcript_path.read_text()


@pytest.mark.asyncio
async def test_structured_acknowledgement_has_its_own_privacy_failure(
    tmp_path, monkeypatch
):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_seat(driver, runtime, gs, 1, 10)
    await run_gate_seat(driver, runtime, gs, 2, 10)
    original = runtime.admit_player

    async def tainted(gs_arg, player_id, turn):
        admission = await original(gs_arg, player_id, turn)
        if player_id != 3:
            return admission
        acknowledgement = ChannelAcknowledgement(
            player_id=1,
            turn=turn,
            source_id="observer-safe-source",
            status="applied",
            message="observer-safe-message",
        )
        projection = dataclasses.replace(
            admission.projection, acknowledgements=(acknowledgement,)
        )
        return dataclasses.replace(admission, projection=projection)

    monkeypatch.setattr(runtime, "admit_player", tainted)
    await run_gate_seat(driver, runtime, gs, 3, 10)

    by_kind = {item["artifact_kind"]: item for item in privacy_assertions(driver)}
    assert by_kind["acknowledgements"]["result"] == "FAIL"
    assert driver.pending_signal() == GATE_FAILED


@pytest.mark.asyncio
async def test_deal_id_text_alone_is_not_a_privacy_failure(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)

    upfront_id = driver._journal.state.data["upfront_deal_id"]
    assert upfront_id not in driver._forbidden_values()
    assert all(item["result"] == "PASS" for item in privacy_assertions(driver))


@pytest.mark.asyncio
async def test_pending_transcript_digest_uses_public_serializer(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    transcript_path = driver._run_dir / "transcript.jsonl"
    observer_line = next(
        line
        for line in transcript_path.read_text().splitlines()
        if json.loads(line).get("player_id") == 3
    )
    record = json.loads(observer_line)
    expected_digest = hashlib.sha256(
        serialize_transcript_record(record).encode("utf-8")
    ).hexdigest()[:16]
    pending = next(
        item
        for item in privacy_assertions(driver)
        if item["artifact_kind"] == "pending_transcript_record"
    )
    assert pending["input_digest"] == expected_digest


@pytest.mark.asyncio
async def test_persisted_observer_transcript_leak_fails_next_capture(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    transcript_path = driver._run_dir / "transcript.jsonl"
    TranscriptSink(str(transcript_path)).write(
        {"player_id": 3, "turn": 10, "planted": driver.canary}
    )

    await run_gate_round(driver, runtime, gs, 11)

    failures = [
        assertion
        for assertion in privacy_assertions(driver)
        if assertion["result"] == "FAIL"
    ]
    assert failures
    assert failures[0]["artifact_kind"] == "transcript_records"
    assert driver.pending_signal() == GATE_FAILED


@pytest.mark.asyncio
async def test_terminal_pass_requires_full_privacy_coverage_for_every_turn(tmp_path):
    driver, runtime, gs = await full_run(tmp_path)

    assert driver.pending_signal() == GATE_PASSED
    per_turn = {}
    for assertion in privacy_assertions(driver):
        per_turn.setdefault(assertion["turn"], set()).add(assertion["artifact_kind"])
        assert assertion["result"] == "PASS"
    assert set(per_turn) == set(range(10, 19))
    assert all(kinds == PRIVACY_KINDS for kinds in per_turn.values())


@pytest.mark.asyncio
async def test_terminal_gate_fails_if_pending_transcript_hook_was_skipped(tmp_path):
    driver, runtime, gs = await full_run(tmp_path, stop_before_round=9)
    await run_gate_seat(driver, runtime, gs, 1, 18)
    await run_gate_seat(driver, runtime, gs, 2, 18)

    gs.active_player = 3
    admission = await runtime.admit_player(gs, 3, 18)
    driver.note_admission(3, 18, admission, "")
    result = await driver.policy_for(3)(gs, 3, 18)
    acknowledgements = await runtime.finish_player(gs, admission, result)
    await driver.after_seat_capture(
        player_id=3,
        turn=18,
        channel_fields={
            "enabled": True,
            "acknowledgements": len(acknowledgements),
            "error": "",
        },
    )

    assert driver.pending_signal() == GATE_FAILED
    assert "privacy" in driver._journal.state.reason
