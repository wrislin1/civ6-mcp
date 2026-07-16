import dataclasses
import json

import pytest

from civ_mcp.arena.channel_protocol import (
    ChannelTurnContext,
    StagedChannelAction,
    parse_channel_action,
)
from civ_mcp.arena.channel_runtime import (
    ChannelRuntime,
    ChannelStateError,
    grievance_base_magnitude,
)
from civ_mcp.arena.config import ChannelRules
from civ_mcp.arena.channels import DealState, FavorStatus, PaymentStatus
from civ_mcp.arena.channel_terms import (
    ChannelObservation,
    ObservationFamily,
    ObservedUnit,
)


class FakeGameState:
    pass


class ObservingGameState:
    def __init__(self, observations: list[ChannelObservation]) -> None:
        self.observations = list(observations)
        self.requests = []

    async def get_channel_observation(self, player_id, turn, request):
        self.requests.append((player_id, turn, request))
        result = self.observations.pop(0)
        assert result.player_id == player_id
        assert result.turn == turn
        return result


@pytest.fixture
def fake_gs() -> FakeGameState:
    return FakeGameState()


def runtime(tmp_path) -> ChannelRuntime:
    return ChannelRuntime.open(
        tmp_path,
        "run-a",
        frozenset({1, 2}),
        ChannelRules(),
    )


def stage(
    source_id: str,
    actor: int,
    name: str,
    args: dict,
    *,
    rules: ChannelRules | None = None,
) -> StagedChannelAction:
    enabled_players = frozenset({1, 2})
    action = parse_channel_action(
        name,
        args,
        actor=actor,
        enabled_players=enabled_players,
        rules=rules or ChannelRules(),
    )
    return StagedChannelAction(source_id, actor, action)


def observation(player_id: int, turn: int, **changes) -> ChannelObservation:
    base = ChannelObservation(
        player_id=player_id,
        turn=turn,
        families_present=frozenset(ObservationFamily),
        treasury_gold=500,
    )
    return dataclasses.replace(base, **changes)


def proposal(
    source_id: str = "proposal-1",
    *,
    timing: str = "on_delivery",
    favor: dict | None = None,
    within: int = 3,
    text: str = "clear the northern camp",
) -> StagedChannelAction:
    return stage(
        source_id,
        1,
        "propose_deal",
        {
            "to_player": 2,
            "text": text,
            "favor": favor
            or {
                "term_type": "destroy_camp",
                "params": {"x": 12, "y": 7},
            },
            "payment_gold": 100,
            "timing": timing,
            "within": within,
        },
    )


def test_open_creates_owner_only_journal_and_snapshot(tmp_path):
    runtime(tmp_path)
    assert (tmp_path / "channels" / "events.jsonl").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "channels" / "state.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "channels").stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
async def test_pure_action_replay_is_a_noop(tmp_path, fake_gs):
    rt = runtime(tmp_path)
    staged = stage(
        "src-1",
        1,
        "send_message",
        {"to_player": 2, "text": "hello"},
    )
    await rt.apply_staged(fake_gs, staged, turn=4, observation=None)
    sequence = rt.state.last_event_sequence
    await rt.apply_staged(fake_gs, staged, turn=4, observation=None)
    assert len(rt.state.messages) == 1
    assert rt.state.last_event_sequence == sequence
    assert "src-1" in rt.state.applied_source_ids


def test_resume_replays_events_newer_than_snapshot(tmp_path):
    rt = runtime(tmp_path)
    rt._commit(
        "message_sent",
        {
            "id": "msg-000001",
            "from_player": 1,
            "to_player": 2,
            "turn": 1,
            "text": "persist",
            "deal_id": None,
        },
    )
    state_path = tmp_path / "channels" / "state.json"
    snapshot = json.loads(state_path.read_text())
    snapshot["last_event_sequence"] = 0
    snapshot["messages"] = []
    state_path.write_text(json.dumps(snapshot))

    reopened = runtime(tmp_path)

    assert reopened.state.messages[0].text == "persist"


def test_base_magnitude_is_fixed_and_bounded():
    assert grievance_base_magnitude(1) == 0.25
    assert grievance_base_magnitude(100) == 1.0
    assert grievance_base_magnitude(50_000) == 10.0


def test_channel_turn_context_is_the_bound_source_of_api_actions():
    context = ChannelTurnContext(
        "run-a",
        1,
        4,
        frozenset({1, 2}),
        ChannelRules(),
    )
    context.dispatch("send_message", {"to_player": 2, "text": "hello"})
    assert context.staged_actions[0].actor == 1


@pytest.mark.asyncio
async def test_proposal_is_one_atomic_linked_event_and_replays_both_records(
    tmp_path,
    fake_gs,
):
    rt = runtime(tmp_path)
    proposal_observation = observation(
        1,
        4,
        camps=frozenset({(12, 7)}),
    )

    ack = await rt.apply_staged(
        fake_gs,
        proposal(),
        turn=4,
        observation=proposal_observation,
    )

    assert ack.status == "applied"
    assert rt.state.deals[0].favor.baseline == {
        "proposal_turn": 4,
        "camp_present": True,
    }
    assert rt.state.messages[0].deal_id == rt.state.deals[0].id
    assert rt.state.messages[0].text == "clear the northern camp"
    proposal_events = [
        json.loads(line)
        for line in (tmp_path / "channels" / "events.jsonl").read_text().splitlines()
        if json.loads(line)["kind"] == "deal_proposed"
    ]
    assert len(proposal_events) == 1

    state_path = tmp_path / "channels" / "state.json"
    snapshot = json.loads(state_path.read_text())
    snapshot["last_event_sequence"] = 0
    snapshot["messages"] = []
    snapshot["deals"] = []
    state_path.write_text(json.dumps(snapshot))
    reopened = runtime(tmp_path)
    assert reopened.state.messages[0].deal_id == reopened.state.deals[0].id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timing, expected_favor, expected_payment",
    [
        ("on_delivery", FavorStatus.DUE, PaymentStatus.NOT_DUE),
        ("up_front", FavorStatus.NOT_DUE, PaymentStatus.DUE),
    ],
)
async def test_acceptance_activates_only_the_first_timing_obligation(
    tmp_path,
    fake_gs,
    timing,
    expected_favor,
    expected_payment,
):
    rt = runtime(tmp_path)
    favor = {
        "term_type": "forbid_unit_acquisition",
        "params": {"category": "military"},
    }
    await rt.apply_staged(
        fake_gs,
        proposal(timing=timing, favor=favor, within=5),
        turn=4,
        observation=observation(1, 4),
    )
    deal = rt.state.deals[0]

    ack = await rt.apply_staged(
        fake_gs,
        stage(
            f"accept-{timing}",
            2,
            "respond_to_deal",
            {"deal_id": deal.id, "accept": True},
        ),
        turn=5,
        observation=observation(
            2,
            5,
            units=(
                ObservedUnit(
                    2,
                    10,
                    "UNIT_WARRIOR",
                    "FORMATION_CLASS_LAND_COMBAT",
                    0,
                    3,
                    4,
                ),
            ),
        ),
    )

    accepted = rt.deal(deal.id)
    assert ack.status == "applied"
    assert accepted.state is DealState.ACTIVE
    assert accepted.favor_status is expected_favor
    assert accepted.payment_status is expected_payment
    if timing == "on_delivery":
        assert accepted.favor_due_turn == 10
        assert accepted.fund_by_turn is None
        assert accepted.favor.baseline["unit_ids"] == ((2, 10),)
        assert accepted.favor.baseline["favor_started_turn"] == 5
    else:
        assert accepted.fund_by_turn == 7
        assert accepted.favor_due_turn is None
        assert accepted.favor.baseline == {}


@pytest.mark.asyncio
async def test_decline_and_inclusive_expiry_are_terminal_without_grievances(
    tmp_path,
    fake_gs,
):
    rt = runtime(tmp_path)
    proposal_observation = observation(
        1,
        1,
        camps=frozenset({(12, 7)}),
    )
    await rt.apply_staged(
        fake_gs,
        proposal("decline-proposal"),
        turn=1,
        observation=proposal_observation,
    )
    declined_id = rt.state.deals[0].id
    await rt.apply_staged(
        fake_gs,
        stage(
            "decline",
            2,
            "respond_to_deal",
            {"deal_id": declined_id, "accept": False},
        ),
        turn=4,
        observation=observation(2, 4),
    )
    assert rt.deal(declined_id).state is DealState.DECLINED

    await rt.apply_staged(
        fake_gs,
        proposal("expire-proposal"),
        turn=5,
        observation=observation(1, 5, camps=frozenset({(12, 7)})),
    )
    expiring_id = rt.state.deals[-1].id
    await rt.poll_unseated(fake_gs, turn=9, local_player_id=None)

    assert rt.deal(expiring_id).state is DealState.EXPIRED
    assert rt.state.grievances == ()


@pytest.mark.asyncio
async def test_message_bound_rejection_is_persisted_and_idempotent(tmp_path, fake_gs):
    rules = ChannelRules(max_messages_per_pair=1)
    rt = ChannelRuntime.open(tmp_path, "run-a", frozenset({1, 2}), rules)
    first = stage(
        "msg-1",
        1,
        "send_message",
        {"to_player": 2, "text": "first"},
        rules=rules,
    )
    second = stage(
        "msg-2",
        1,
        "send_message",
        {"to_player": 2, "text": "second"},
        rules=rules,
    )
    await rt.apply_staged(fake_gs, first, turn=1, observation=None)

    rejected = await rt.apply_staged(fake_gs, second, turn=2, observation=None)
    sequence = rt.state.last_event_sequence
    replayed = await rt.apply_staged(fake_gs, second, turn=2, observation=None)

    assert rejected.status == replayed.status == "rejected"
    assert len(rt.state.messages) == 1
    assert rt.state.last_event_sequence == sequence
    assert "msg-2" in rt.state.applied_source_ids


async def accepted_deal(
    rt: ChannelRuntime,
    fake_gs,
    *,
    favor: dict,
    timing: str = "on_delivery",
    within: int = 3,
    acceptance_observation: ChannelObservation | None = None,
):
    await rt.apply_staged(
        fake_gs,
        proposal(
            favor=favor,
            timing=timing,
            within=within,
        ),
        turn=1,
        observation=observation(1, 1, camps=frozenset({(12, 7)})),
    )
    deal = rt.state.deals[-1]
    await rt.apply_staged(
        fake_gs,
        stage(
            f"accept-{deal.id}",
            2,
            "respond_to_deal",
            {"deal_id": deal.id, "accept": True},
        ),
        turn=2,
        observation=acceptance_observation or observation(2, 2),
    )
    return rt.deal(deal.id)


@pytest.mark.asyncio
async def test_admission_and_finish_make_two_union_observations_and_apply_context(
    tmp_path,
):
    rt = runtime(tmp_path)
    await accepted_deal(
        rt,
        FakeGameState(),
        favor={
            "term_type": "maintain_gold_reserve",
            "params": {"min_gold": 400},
        },
        acceptance_observation=observation(2, 2, treasury_gold=500),
    )
    gs = ObservingGameState(
        [
            observation(2, 3, treasury_gold=500),
            observation(2, 3, treasury_gold=500),
        ]
    )

    admission = await rt.admit_player(gs, 2, 3)
    admission.context.dispatch(
        "send_message",
        {"to_player": 1, "text": "working on it"},
    )
    acknowledgements = await rt.finish_player(
        gs,
        admission,
        {"transcript": {"steps": [], "final_summary": ""}},
    )

    assert len(gs.requests) == 2
    assert all(
        ObservationFamily.TREASURY in request.families
        for _, _, request in gs.requests
    )
    assert admission.observation_id.startswith("obs-")
    assert admission.projection.player_id == 2
    assert admission.block.startswith("== PRIVATE UNOFFICIAL CHANNELS ==")
    assert "favor due" in admission.wake_reasons
    assert acknowledgements[0].status == "applied"
    assert rt.state.messages[-1].text == "working on it"


@pytest.mark.asyncio
async def test_inclusive_favor_failure_uses_persisted_observation_and_grievance(
    tmp_path,
):
    rt = runtime(tmp_path)
    deal = await accepted_deal(
        rt,
        FakeGameState(),
        favor={
            "term_type": "maintain_gold_reserve",
            "params": {"min_gold": 400},
        },
        within=3,
        acceptance_observation=observation(2, 2, treasury_gold=500),
    )
    gs = ObservingGameState(
        [
            observation(2, 5, treasury_gold=500),
            observation(2, 5, treasury_gold=399),
        ]
    )

    admission = await rt.admit_player(gs, 2, 5)
    assert rt.deal(deal.id).state is DealState.ACTIVE
    await rt.finish_player(
        gs,
        admission,
        {"transcript": {"steps": [], "final_summary": ""}},
    )

    broken = rt.deal(deal.id)
    grievance = rt.state.grievances[0]
    assert broken.state is DealState.BROKEN
    assert broken.favor_status is FavorStatus.FAILED
    assert broken.terminal["wronged"] == grievance.wronged == 1
    assert broken.terminal["offender"] == grievance.offender == 2
    assert broken.terminal["evidence_refs"] == [
        broken.favor.monitor["violation_observation_id"]
    ]
    assert broken.terminal["evidence_refs"][0].startswith("obs-")
    assert grievance.base_magnitude == 1.0


@pytest.mark.asyncio
async def test_missing_deadline_evidence_is_unverifiable_without_grievance(tmp_path):
    rt = runtime(tmp_path)
    deal = await accepted_deal(
        rt,
        FakeGameState(),
        favor={
            "term_type": "maintain_gold_reserve",
            "params": {"min_gold": 400},
        },
        within=2,
        acceptance_observation=observation(2, 2, treasury_gold=500),
    )
    gs = ObservingGameState(
        [
            observation(2, 4, treasury_gold=500),
            observation(
                2,
                4,
                families_present=frozenset(),
                treasury_gold=0,
                errors=("treasury query failed",),
            ),
        ]
    )

    admission = await rt.admit_player(gs, 2, 4)
    await rt.finish_player(gs, admission, None)

    assert rt.deal(deal.id).state is DealState.UNVERIFIABLE
    assert rt.state.grievances == ()


@pytest.mark.asyncio
async def test_favor_success_starts_on_delivery_funding_obligation(tmp_path):
    rt = runtime(tmp_path)
    deal = await accepted_deal(
        rt,
        FakeGameState(),
        favor={
            "term_type": "destroy_camp",
            "params": {"x": 12, "y": 7},
        },
        within=5,
        acceptance_observation=observation(
            2,
            2,
            camps=frozenset({(12, 7)}),
        ),
    )
    gs = ObservingGameState(
        [
            observation(2, 3),
            observation(2, 3),
        ]
    )

    admission = await rt.admit_player(gs, 2, 3)
    await rt.finish_player(gs, admission, None)

    satisfied = rt.deal(deal.id)
    assert satisfied.state is DealState.ACTIVE
    assert satisfied.favor_status is FavorStatus.SATISFIED
    assert satisfied.payment_status is PaymentStatus.DUE
    assert satisfied.fund_by_turn == 5
    assert satisfied.favor.monitor["satisfaction_observation_id"].startswith("obs-")


@pytest.mark.asyncio
async def test_inclusive_missing_funding_breaks_after_proposer_finish(tmp_path):
    rt = runtime(tmp_path)
    deal = await accepted_deal(
        rt,
        FakeGameState(),
        favor={
            "term_type": "maintain_gold_reserve",
            "params": {"min_gold": 400},
        },
        timing="up_front",
        within=3,
    )
    assert deal.fund_by_turn == 4
    gs = ObservingGameState([observation(1, 4), observation(1, 4)])

    admission = await rt.admit_player(gs, 1, 4)
    assert rt.deal(deal.id).state is DealState.ACTIVE
    await rt.finish_player(gs, admission, None)

    broken = rt.deal(deal.id)
    assert broken.state is DealState.BROKEN
    assert broken.payment_status is PaymentStatus.FAILED
    assert broken.terminal["wronged"] == 2
    assert broken.terminal["offender"] == 1
    assert rt.state.grievances[0].wronged == 2


@pytest.mark.asyncio
async def test_proposal_expires_only_after_counterparty_final_turn_actions(tmp_path):
    rt = runtime(tmp_path)
    await rt.apply_staged(
        FakeGameState(),
        proposal(),
        turn=1,
        observation=observation(1, 1, camps=frozenset({(12, 7)})),
    )
    deal = rt.state.deals[0]
    gs = ObservingGameState([observation(2, 4), observation(2, 4)])

    admission = await rt.admit_player(gs, 2, 4)
    assert rt.deal(deal.id).state is DealState.PROPOSED
    await rt.finish_player(gs, admission, None)

    assert rt.deal(deal.id).state is DealState.EXPIRED
    assert rt.state.grievances == ()


@pytest.mark.asyncio
async def test_cli_proposal_is_registry_validated_before_journaling(tmp_path):
    rt = runtime(tmp_path)
    gs = ObservingGameState([observation(1, 3), observation(1, 3)])
    admission = await rt.admit_player(gs, 1, 3)
    invalid = (
        'CHANNEL {"action":"propose_deal","to_player":2,'
        '"text":"invalid reserve","favor":{"term_type":'
        '"maintain_gold_reserve","params":{"min_gold":-1}},'
        '"payment_gold":100,"timing":"on_delivery","within":3}'
    )

    acknowledgements = await rt.finish_player(
        gs,
        admission,
        {"transcript": {"steps": [], "final_summary": invalid}},
    )

    assert acknowledgements[0].status == "rejected"
    assert "min_gold must be 0..10000" in acknowledgements[0].message
    assert rt.state.deals == ()


@pytest.mark.asyncio
async def test_cli_acknowledgements_preserve_source_line_order(tmp_path):
    rt = runtime(tmp_path)
    gs = ObservingGameState([observation(1, 3), observation(1, 3)])
    admission = await rt.admit_player(gs, 1, 3)
    summary = "\n".join(
        [
            "CHANNEL {invalid}",
            'CHANNEL {"action":"send_message","to_player":2,"text":"hello"}',
        ]
    )

    acknowledgements = await rt.finish_player(
        gs,
        admission,
        {"transcript": {"steps": [], "final_summary": summary}},
    )

    assert [ack.status for ack in acknowledgements] == ["rejected", "applied"]
    assert [ack.source_id.split(":")[4] for ack in acknowledgements] == ["0", "1"]


def test_open_rejects_incompatible_snapshot_identity_and_rules(tmp_path):
    runtime(tmp_path)
    with pytest.raises(ChannelStateError, match="run id mismatch"):
        ChannelRuntime.open(
            tmp_path,
            "other-run",
            frozenset({1, 2}),
            ChannelRules(),
        )
    with pytest.raises(ChannelStateError, match="enabled-player set"):
        ChannelRuntime.open(
            tmp_path,
            "run-a",
            frozenset({1, 2, 3}),
            ChannelRules(),
        )
    with pytest.raises(ChannelStateError, match="rules fingerprint"):
        ChannelRuntime.open(
            tmp_path,
            "run-a",
            frozenset({1, 2}),
            ChannelRules(acceptance_turns=4),
        )


def test_open_rejects_malformed_or_nonconsecutive_complete_journal_records(tmp_path):
    rt = runtime(tmp_path)
    with rt.events_path.open("ab") as stream:
        stream.write(b"{malformed}\n")
    with pytest.raises(ChannelStateError, match="invalid channel journal"):
        runtime(tmp_path)

    other = tmp_path / "other"
    other_rt = ChannelRuntime.open(
        other,
        "run-a",
        frozenset({1, 2}),
        ChannelRules(),
    )
    event = {
        "schema_version": 1,
        "id": "evt-000002",
        "sequence": 2,
        "kind": "source_applied",
        "payload": {"source_id": "gap"},
    }
    with other_rt.events_path.open("a") as stream:
        stream.write(json.dumps(event) + "\n")
    with pytest.raises(ChannelStateError, match="non-consecutive"):
        ChannelRuntime.open(
            other,
            "run-a",
            frozenset({1, 2}),
            ChannelRules(),
        )


def test_open_ignores_a_truncated_final_journal_record(tmp_path):
    rt = runtime(tmp_path)
    rt._commit("source_applied", {"source_id": "complete"})
    with rt.events_path.open("ab") as stream:
        stream.write(b'{"schema_version":1,"id":"evt-000002"')

    reopened = runtime(tmp_path)

    assert reopened.state.applied_source_ids == frozenset({"complete"})
    reopened._commit("source_applied", {"source_id": "after-recovery"})
    recovered_again = runtime(tmp_path)
    assert recovered_again.state.applied_source_ids == frozenset(
        {"complete", "after-recovery"}
    )


def test_open_rejects_nonempty_journal_when_identity_snapshot_is_missing(tmp_path):
    rt = runtime(tmp_path)
    rt._commit("source_applied", {"source_id": "persisted"})
    rt.state_path.unlink()

    with pytest.raises(ChannelStateError, match="snapshot is missing"):
        runtime(tmp_path)


def test_open_rejects_snapshot_that_disagrees_with_same_sequence_journal(tmp_path):
    rt = runtime(tmp_path)
    rt._commit(
        "message_sent",
        {
            "id": "msg-000001",
            "from_player": 1,
            "to_player": 2,
            "turn": 1,
            "text": "persisted",
            "deal_id": None,
        },
    )
    snapshot = json.loads(rt.state_path.read_text())
    snapshot["messages"] = []
    rt.state_path.write_text(json.dumps(snapshot))

    with pytest.raises(ChannelStateError, match="does not match journal"):
        runtime(tmp_path)


@pytest.mark.asyncio
async def test_active_deal_bound_rejects_before_a_fourth_proposal(tmp_path, fake_gs):
    rt = runtime(tmp_path)
    for index in range(3):
        acknowledgement = await rt.apply_staged(
            fake_gs,
            proposal(f"proposal-{index}"),
            turn=1,
            observation=observation(
                1,
                1,
                camps=frozenset({(12, 7)}),
            ),
        )
        assert acknowledgement.status == "applied"

    rejected = await rt.apply_staged(
        fake_gs,
        proposal("proposal-4"),
        turn=1,
        observation=observation(1, 1, camps=frozenset({(12, 7)})),
    )

    assert rejected.status == "rejected"
    assert len(rt.state.deals) == len(rt.state.messages) == 3


@pytest.mark.asyncio
async def test_poll_unseated_finalizes_overdue_funding_without_early_default(tmp_path):
    rt = runtime(tmp_path)
    deal = await accepted_deal(
        rt,
        FakeGameState(),
        favor={
            "term_type": "maintain_gold_reserve",
            "params": {"min_gold": 400},
        },
        timing="up_front",
    )

    await rt.poll_unseated(FakeGameState(), turn=4, local_player_id=None)
    assert rt.deal(deal.id).state is DealState.ACTIVE
    await rt.poll_unseated(FakeGameState(), turn=5, local_player_id=None)

    assert rt.deal(deal.id).state is DealState.BROKEN
    assert rt.state.grievances[0].wronged == deal.counterparty


@pytest.mark.asyncio
async def test_favor_start_violation_keeps_its_persisted_observation_reference(
    tmp_path,
):
    rt = runtime(tmp_path)
    deal = await accepted_deal(
        rt,
        FakeGameState(),
        favor={
            "term_type": "maintain_gold_reserve",
            "params": {"min_gold": 400},
        },
        acceptance_observation=observation(2, 2, treasury_gold=399),
    )
    baseline_reference = deal.favor.baseline[
        "initial_violation_observation_id"
    ]
    gs = ObservingGameState(
        [
            observation(
                2,
                3,
                families_present=frozenset(),
                treasury_gold=0,
            )
        ]
    )

    await rt.admit_player(gs, 2, 3)

    broken = rt.deal(deal.id)
    assert broken.state is DealState.BROKEN
    assert baseline_reference.startswith("obs-")
    assert broken.terminal["evidence_refs"] == [baseline_reference]


@pytest.mark.asyncio
async def test_first_observation_after_endpoint_deadline_cannot_prove_timely_success(
    tmp_path,
):
    rt = runtime(tmp_path)
    deal = await accepted_deal(
        rt,
        FakeGameState(),
        favor={
            "term_type": "destroy_camp",
            "params": {"x": 12, "y": 7},
        },
        within=1,
        acceptance_observation=observation(
            2,
            2,
            camps=frozenset({(12, 7)}),
        ),
    )
    gs = ObservingGameState([observation(2, 4)])

    await rt.admit_player(gs, 2, 4)

    assert rt.deal(deal.id).state is DealState.UNVERIFIABLE
    assert rt.state.grievances == ()
