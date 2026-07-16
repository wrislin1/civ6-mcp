import asyncio
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
from civ_mcp.lua.channel_payments import ExactPaymentOffer


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


@dataclasses.dataclass(frozen=True)
class PaymentStateView:
    status: str
    offer: ExactPaymentOffer | None = None


class PaymentGameState:
    def __init__(self) -> None:
        self.local_player = 1
        self.pending: dict[tuple[int, int], ExactPaymentOffer | str] = {}
        self.observations: list[ChannelObservation] = []
        self.observation_requests = []
        self.request_aware_observations = False
        self.offer_results: list[str | BaseException] = []
        self.response_results: list[str | BaseException] = []
        self.offer_calls = 0
        self.response_calls: list[bool] = []
        self.snapshot_at_response: str | None = None
        self.query_calls = 0
        self.state_queries: list[tuple[int, int, int, int]] = []
        self.intent_was_durable = False
        self.snapshot_at_offer: str | None = None
        self.runtime: ChannelRuntime | None = None

    async def get_channel_observation(self, player_id, turn, request):
        self.observation_requests.append((player_id, turn, request))
        result = self.observations.pop(0)
        assert (result.player_id, result.turn) == (player_id, turn)
        if self.request_aware_observations:
            result = dataclasses.replace(
                result,
                families_present=request.families,
            )
        return result

    async def offer_channel_payment(self, payee: int, gold: int) -> str:
        self.offer_calls += 1
        if self.runtime is not None:
            self.intent_was_durable = journal_events(self.runtime)[-1]["kind"] == (
                "payment_fund_intent"
            )
            self.snapshot_at_offer = self.runtime.state_path.read_text()
        result = (
            self.offer_results.pop(0)
            if self.offer_results
            else "CHANNEL_PAYMENT_PROPOSED"
        )
        if isinstance(result, BaseException):
            raise result
        pair = (self.local_player, payee)
        if pair in self.pending:
            return "Error: CHANNEL_PAYMENT_PENDING_DEAL"
        if result == "CHANNEL_PAYMENT_PROPOSED":
            self.install_exact_offer(self.local_player, payee, gold)
        return result

    async def get_channel_payment_offer(
        self,
        payer: int,
        gold: int,
    ) -> ExactPaymentOffer | None:
        del gold
        self.query_calls += 1
        pending = self.pending.get((payer, self.local_player))
        return pending if isinstance(pending, ExactPaymentOffer) else None

    async def get_channel_payment_state(
        self,
        payer: int,
        payee: int,
        gold: int,
    ) -> PaymentStateView:
        self.state_queries.append((self.local_player, payer, payee, gold))
        pending = self.pending.get((payer, payee))
        expected = ExactPaymentOffer(payer, payee, gold)
        if pending is None:
            return PaymentStateView("absent")
        if pending == expected:
            return PaymentStateView("exact", expected)
        return PaymentStateView("conflicting")

    async def respond_to_channel_payment(
        self,
        payer: int,
        gold: int,
        accept: bool,
    ) -> str:
        self.response_calls.append(accept)
        if self.runtime is not None:
            self.snapshot_at_response = self.runtime.state_path.read_text()
        result = (
            self.response_results.pop(0)
            if self.response_results
            else (
                "CHANNEL_PAYMENT_ACCEPTED"
                if accept
                else "CHANNEL_PAYMENT_REJECTED"
            )
        )
        if isinstance(result, BaseException):
            raise result
        expected = ExactPaymentOffer(payer, self.local_player, gold)
        if self.pending.get((payer, self.local_player)) != expected:
            return "Error: NO_EXACT_CHANNEL_PAYMENT"
        if result in {"CHANNEL_PAYMENT_ACCEPTED", "CHANNEL_PAYMENT_REJECTED"}:
            del self.pending[(payer, self.local_player)]
        return result

    def install_exact_offer(self, payer: int, payee: int, gold: int) -> None:
        self.pending[(payer, payee)] = ExactPaymentOffer(payer, payee, gold)


@pytest.fixture
def payment_gs() -> PaymentGameState:
    return PaymentGameState()


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


def journal_events(rt: ChannelRuntime) -> list[dict]:
    return [json.loads(line) for line in rt.events_path.read_text().splitlines()]


def append_complete_event(rt: ChannelRuntime, kind: str, payload: dict) -> None:
    sequence = rt.state.next_event
    event = {
        "schema_version": 1,
        "id": f"evt-{sequence:06d}",
        "sequence": sequence,
        "kind": kind,
        "payload": payload,
    }
    with rt.events_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event) + "\n")


def raw_proposal_payload(
    rt: ChannelRuntime,
    *,
    proposer: int = 1,
    counterparty: int = 2,
    created_turn: int = 4,
    accept_by_turn: int | None = None,
    completion_window_turns: int = 3,
    favor: dict | None = None,
    payment_gold: int = 100,
    timing: str = "on_delivery",
    text: str = "maintain the reserve",
) -> dict:
    deal_id = f"deal-{rt.state.next_deal:06d}"
    return {
        "deal": {
            "id": deal_id,
            "proposer": proposer,
            "counterparty": counterparty,
            "created_turn": created_turn,
            "accepted_turn": None,
            "accept_by_turn": (
                created_turn + rt.rules.acceptance_turns
                if accept_by_turn is None
                else accept_by_turn
            ),
            "completion_window_turns": completion_window_turns,
            "favor": {
                **(
                    favor
                    or {
                        "term_type": "maintain_gold_reserve",
                        "params": {"min_gold": 400},
                    }
                ),
                "baseline": {},
                "monitor": {},
            },
            "payment_gold": payment_gold,
            "timing": timing,
            "state": "proposed",
            "favor_status": "not_due",
            "payment_status": "not_due",
            "fund_by_turn": None,
            "payment_response_by_turn": None,
            "favor_due_turn": None,
            "terminal": None,
        },
        "message": {
            "id": f"msg-{rt.state.next_message:06d}",
            "from_player": proposer,
            "to_player": counterparty,
            "turn": created_turn,
            "text": text,
            "deal_id": deal_id,
        },
    }


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
    lagging_snapshot = rt.state_path.read_text()
    before_events = len(journal_events(rt))
    acknowledgement = await rt.apply_staged(
        fake_gs, staged, turn=4, observation=None
    )
    added = journal_events(rt)[before_events:]
    assert [event["kind"] for event in added] == ["staged_action_applied"]

    rt.state_path.write_text(lagging_snapshot)
    rt = runtime(tmp_path)
    assert rt.state.acknowledgements[-1] == acknowledgement
    sequence = rt.state.last_event_sequence
    await rt.apply_staged(fake_gs, staged, turn=4, observation=None)
    assert len(rt.state.messages) == 1
    assert rt.state.last_event_sequence == sequence
    assert "src-1" in rt.state.applied_source_ids


def test_resume_replays_events_newer_than_snapshot(tmp_path):
    rt = runtime(tmp_path)
    state_path = tmp_path / "channels" / "state.json"
    lagging_snapshot = state_path.read_text()
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
    state_path.write_text(lagging_snapshot)

    reopened = runtime(tmp_path)

    assert reopened.state.messages[0].text == "persist"


def test_incomplete_staged_action_record_replays_none_of_its_atomic_state(tmp_path):
    rt = runtime(tmp_path)
    event = {
        "schema_version": 1,
        "id": "evt-000001",
        "sequence": 1,
        "kind": "staged_action_applied",
        "payload": {
            "source_id": "crashed-source",
            "acknowledgement": {
                "player_id": 1,
                "turn": 1,
                "source_id": "crashed-source",
                "status": "applied",
                "message": "sent private message msg-000001 to player 2",
                "deal_id": None,
            },
            "effect": {
                "kind": "message_sent",
                "payload": {
                    "id": "msg-000001",
                    "from_player": 1,
                    "to_player": 2,
                    "turn": 1,
                    "text": "not durable",
                    "deal_id": None,
                },
            },
        },
    }
    with rt.events_path.open("ab") as stream:
        stream.write(json.dumps(event).encode())

    reopened = runtime(tmp_path)

    assert reopened.state.messages == ()
    assert reopened.state.acknowledgements == ()
    assert reopened.state.applied_source_ids == frozenset()


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
    lagging_snapshot = rt.state_path.read_text()

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
        event
        for event in journal_events(rt)
        if event["kind"] == "staged_action_applied"
        and event["payload"]["effect"]["kind"] == "deal_proposed"
    ]
    assert len(proposal_events) == 1

    rt.state_path.write_text(lagging_snapshot)
    reopened = runtime(tmp_path)
    assert reopened.state.messages[0].deal_id == reopened.state.deals[0].id
    assert reopened.state.acknowledgements[-1] == ack
    sequence = reopened.state.last_event_sequence
    replayed = await reopened.apply_staged(
        fake_gs,
        proposal(),
        turn=4,
        observation=proposal_observation,
    )
    assert replayed == ack
    assert reopened.state.last_event_sequence == sequence


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

    lagging_snapshot = rt.state_path.read_text()
    before_events = len(journal_events(rt))
    rejected = await rt.apply_staged(fake_gs, second, turn=2, observation=None)
    added = journal_events(rt)[before_events:]
    assert [event["kind"] for event in added] == ["staged_action_applied"]

    rt.state_path.write_text(lagging_snapshot)
    rt = ChannelRuntime.open(tmp_path, "run-a", frozenset({1, 2}), rules)
    assert rt.state.acknowledgements[-1] == rejected
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
async def test_deal_response_effect_source_and_ack_replay_atomically(
    tmp_path,
    fake_gs,
):
    rt = runtime(tmp_path)
    await rt.apply_staged(
        fake_gs,
        proposal(
            favor={
                "term_type": "maintain_gold_reserve",
                "params": {"min_gold": 400},
            }
        ),
        turn=1,
        observation=observation(1, 1, camps=frozenset({(12, 7)})),
    )
    deal = rt.state.deals[0]
    accept = stage(
        "accept-1",
        2,
        "respond_to_deal",
        {"deal_id": deal.id, "accept": True},
    )
    accept_observation = observation(2, 2)
    rt._ensure_observation_recorded(accept_observation)
    lagging_snapshot = rt.state_path.read_text()
    before_events = len(journal_events(rt))

    acknowledgement = await rt.apply_staged(
        fake_gs,
        accept,
        turn=2,
        observation=accept_observation,
    )

    added = journal_events(rt)[before_events:]
    assert [event["kind"] for event in added] == ["staged_action_applied"]
    assert added[0]["payload"]["effect"]["kind"] == "deal_changed"
    rt.state_path.write_text(lagging_snapshot)
    reopened = runtime(tmp_path)
    assert reopened.deal(deal.id).state is DealState.ACTIVE
    assert reopened.state.acknowledgements[-1] == acknowledgement
    sequence = reopened.state.last_event_sequence
    assert (
        await reopened.apply_staged(
            fake_gs,
            accept,
            turn=2,
            observation=accept_observation,
        )
        == acknowledgement
    )
    assert reopened.state.last_event_sequence == sequence


@pytest.mark.asyncio
async def test_broken_deal_and_grievance_replay_from_one_event(tmp_path, fake_gs):
    rt = runtime(tmp_path)
    deal = await accepted_deal(
        rt,
        fake_gs,
        favor={
            "term_type": "maintain_gold_reserve",
            "params": {"min_gold": 400},
        },
        timing="up_front",
    )
    lagging_snapshot = rt.state_path.read_text()
    before_events = len(journal_events(rt))

    rt._break_deal(
        deal,
        turn=5,
        breach="funding",
        reason="promised payment was not funded by the deadline",
    )

    added = journal_events(rt)[before_events:]
    assert [event["kind"] for event in added] == ["deal_broken"]
    rt.state_path.write_text(lagging_snapshot)
    reopened = runtime(tmp_path)
    assert reopened.deal(deal.id).state is DealState.BROKEN
    assert reopened.state.grievances[0].deal_id == deal.id


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


@pytest.mark.parametrize(
    "field, value",
    [
        ("schema_version", True),
        ("schema_version", "1"),
        ("schema_version", 1.0),
        ("sequence", True),
        ("sequence", "1"),
        ("sequence", 1.0),
        ("sequence", 0),
        ("sequence", -1),
        ("id", True),
        ("kind", True),
        ("kind", ""),
        ("payload", []),
    ],
    ids=[
        "bool-schema",
        "string-schema",
        "float-schema",
        "bool-sequence",
        "string-sequence",
        "float-sequence",
        "zero-sequence",
        "negative-sequence",
        "non-string-id",
        "non-string-kind",
        "empty-kind",
        "non-object-payload",
    ],
)
def test_open_rejects_noncanonical_complete_event_metadata(tmp_path, field, value):
    rt = runtime(tmp_path)
    event = {
        "schema_version": 1,
        "id": "evt-000001",
        "sequence": 1,
        "kind": "source_applied",
        "payload": {"source_id": "malformed-metadata"},
    }
    event[field] = value
    with rt.events_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event) + "\n")

    with pytest.raises(ChannelStateError, match="invalid channel journal"):
        runtime(tmp_path)


@pytest.mark.parametrize(
    "field, value",
    [
        ("schema_version", True),
        ("schema_version", "1"),
        ("schema_version", 1.0),
        ("last_event_sequence", True),
        ("last_event_sequence", "0"),
        ("last_event_sequence", 0.0),
        ("last_event_sequence", -1),
        ("queue_cursor", False),
        ("queue_cursor", "0"),
        ("queue_cursor", 0.0),
        ("queue_cursor", -1),
        ("next_message", True),
        ("next_deal", True),
        ("next_grievance", True),
        ("next_observation", True),
        ("next_event", True),
        ("next_event", "1"),
        ("next_event", 1.0),
        ("next_event", 0),
        ("next_event", 2),
    ],
    ids=[
        "bool-schema",
        "string-schema",
        "float-schema",
        "bool-last-sequence",
        "string-last-sequence",
        "float-last-sequence",
        "negative-last-sequence",
        "bool-queue-cursor",
        "string-queue-cursor",
        "float-queue-cursor",
        "negative-queue-cursor",
        "bool-next-message",
        "bool-next-deal",
        "bool-next-grievance",
        "bool-next-observation",
        "bool-next-event",
        "string-next-event",
        "float-next-event",
        "zero-next-event",
        "incoherent-next-event",
    ],
)
def test_open_rejects_noncanonical_snapshot_metadata(tmp_path, field, value):
    rt = runtime(tmp_path)
    snapshot = json.loads(rt.state_path.read_text())
    snapshot[field] = value
    rt.state_path.write_text(json.dumps(snapshot))

    with pytest.raises(ChannelStateError, match="invalid channel snapshot"):
        runtime(tmp_path)


@pytest.mark.parametrize(
    "enabled_players",
    [
        [True, 2],
        [1.0, 2],
        ["1", 2],
        [1, 1, 2],
        {"1": True, "2": True},
        [-1, 2],
    ],
    ids=[
        "bool-id",
        "float-id",
        "string-id",
        "duplicate-id",
        "non-array",
        "negative-id",
    ],
)
def test_open_rejects_noncanonical_enabled_player_identity(
    tmp_path,
    enabled_players,
):
    rt = runtime(tmp_path)
    snapshot = json.loads(rt.state_path.read_text())
    snapshot["enabled_players"] = enabled_players
    rt.state_path.write_text(json.dumps(snapshot))

    with pytest.raises(ChannelStateError, match="invalid channel snapshot"):
        runtime(tmp_path)


@pytest.mark.parametrize("run_id", ["", True], ids=["empty", "non-string"])
def test_open_rejects_noncanonical_run_identity(tmp_path, run_id):
    rt = runtime(tmp_path)
    snapshot = json.loads(rt.state_path.read_text())
    snapshot["run_id"] = run_id
    rt.state_path.write_text(json.dumps(snapshot))

    with pytest.raises(ChannelStateError, match="invalid channel snapshot"):
        runtime(tmp_path)


@pytest.mark.parametrize(
    "case, value",
    [
        ("bool-integer", True),
        ("float-integer", 3.0),
        ("string-integer", "3"),
        ("nested-integer", {"value": 3}),
        ("zero-integer", 0),
        ("above-bound-integer", 31),
        ("bool-threshold", True),
        ("integer-threshold", 0),
        ("string-threshold", "0.05"),
        ("nonfinite-threshold", float("nan")),
        ("zero-threshold", 0.0),
        ("above-bound-threshold", 0.06),
        ("non-object", []),
        ("missing-key", None),
        ("extra-key", None),
    ],
)
def test_open_rejects_noncanonical_rules_fingerprint(tmp_path, case, value):
    rt = runtime(tmp_path)
    snapshot = json.loads(rt.state_path.read_text())
    if case == "non-object":
        snapshot["rules_fingerprint"] = value
    else:
        fingerprint = snapshot["rules_fingerprint"]
        if case == "missing-key":
            fingerprint.pop("funding_turns")
        elif case == "extra-key":
            fingerprint["nested"] = {"funding_turns": 2}
        elif case.endswith("threshold"):
            fingerprint["prompt_grievance_threshold"] = value
        elif case == "above-bound-integer":
            fingerprint["max_completion_turns"] = value
        else:
            fingerprint["acceptance_turns"] = value
    rt.state_path.write_text(json.dumps(snapshot))

    with pytest.raises(ChannelStateError, match="invalid channel snapshot"):
        runtime(tmp_path)


def test_open_preserves_valid_custom_identity_through_lagging_replay(tmp_path):
    rules = ChannelRules(
        acceptance_turns=2,
        funding_turns=1,
        max_completion_turns=12,
        max_active_deals_per_pair=2,
        max_payment_gold=750,
        max_message_chars=500,
        prompt_grievance_threshold=0.01,
    )
    enabled_players = frozenset({0, 2, 7})
    rt = ChannelRuntime.open(tmp_path, "custom-run", enabled_players, rules)
    rt._commit("source_applied", {"source_id": "prefix"})
    lagging_snapshot = json.loads(rt.state_path.read_text())
    lagging_snapshot["enabled_players"] = [7, 0, 2]
    rt._commit("source_applied", {"source_id": "later"})
    rt.state_path.write_text(json.dumps(lagging_snapshot))

    reopened = ChannelRuntime.open(
        tmp_path,
        "custom-run",
        enabled_players,
        rules,
    )

    assert reopened.state.enabled_players == enabled_players
    assert reopened.state.rules_fingerprint == rules.fingerprint()
    assert reopened.state.applied_source_ids == frozenset({"prefix", "later"})


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


def test_open_rejects_corrupt_lagging_snapshot_prefix(tmp_path):
    rt = runtime(tmp_path)
    rt._commit("source_applied", {"source_id": "prefix-source"})
    corrupt_prefix = json.loads(rt.state_path.read_text())
    rt._commit(
        "message_sent",
        {
            "id": "msg-000001",
            "from_player": 1,
            "to_player": 2,
            "turn": 2,
            "text": "later event",
            "deal_id": None,
        },
    )
    corrupt_prefix["applied_source_ids"] = []
    rt.state_path.write_text(json.dumps(corrupt_prefix))

    with pytest.raises(ChannelStateError, match="does not match journal"):
        runtime(tmp_path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"proposer": 3},
        {"counterparty": 1},
        {"favor": {"term_type": "narrative", "params": {"text": "trust me"}}},
        {"favor": {"term_type": "unknown", "params": {}}},
        {
            "favor": {
                "term_type": "maintain_gold_reserve",
                "params": {"min_gold": -1},
            }
        },
        {
            "favor": {
                "term_type": "maintain_gold_reserve",
                "params": {"min_gold": 400, "extra": True},
            }
        },
        {"payment_gold": 0},
        {"payment_gold": True},
        {"completion_window_turns": 31},
        {"accept_by_turn": 8},
        {"text": "   "},
    ],
    ids=[
        "disabled-party",
        "self-party",
        "narrative-term",
        "unknown-term",
        "invalid-term-value",
        "noncanonical-term-params",
        "gold-bound",
        "gold-type",
        "completion-window-bound",
        "acceptance-window",
        "message-bound",
    ],
)
def test_open_rejects_complete_malicious_proposal_wal(tmp_path, overrides):
    rt = runtime(tmp_path)
    append_complete_event(rt, "deal_proposed", raw_proposal_payload(rt, **overrides))

    with pytest.raises(ChannelStateError, match="invalid channel journal"):
        runtime(tmp_path)


def test_open_rejects_complete_fourth_unresolved_proposal_wal(tmp_path):
    rt = runtime(tmp_path)
    for _ in range(3):
        rt._commit("deal_proposed", raw_proposal_payload(rt))
    append_complete_event(rt, "deal_proposed", raw_proposal_payload(rt))

    with pytest.raises(ChannelStateError, match="invalid channel journal"):
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


async def accepted_payment_deal(
    tmp_path,
    payment_gs: PaymentGameState,
    *,
    timing: str,
) -> tuple[ChannelRuntime, object]:
    rt = runtime(tmp_path)
    payment_gs.runtime = rt
    deal = await accepted_deal(
        rt,
        payment_gs,
        favor={
            "term_type": "destroy_camp",
            "params": {"x": 12, "y": 7},
        },
        timing=timing,
        within=5,
        acceptance_observation=observation(
            2,
            2,
            camps=frozenset({(12, 7)}),
        ),
    )
    return rt, deal


async def apply_payment_action(
    rt: ChannelRuntime,
    payment_gs: PaymentGameState,
    actor: int,
    name: str,
    args: dict,
    *,
    turn: int,
    source_id: str | None = None,
    action_observation: ChannelObservation | None = None,
):
    payment_gs.local_player = actor
    return await rt.apply_staged(
        payment_gs,
        stage(
            source_id or f"{name}-{args['deal_id']}-{turn}",
            actor,
            name,
            args,
        ),
        turn=turn,
        observation=action_observation,
    )


async def satisfy_payment_favor(
    rt: ChannelRuntime,
    payment_gs: PaymentGameState,
    deal,
    *,
    turn: int,
) -> None:
    payment_gs.local_player = deal.counterparty
    payment_gs.observations.extend(
        [
            observation(deal.counterparty, turn),
            observation(deal.counterparty, turn),
        ]
    )
    admission = await rt.admit_player(payment_gs, deal.counterparty, turn)
    await rt.finish_player(payment_gs, admission, None)


def payment_fund_intent_payload(
    deal,
    source_id: str,
    *,
    turn: int = 3,
) -> dict:
    return {
        "source_id": source_id,
        "deal_id": deal.id,
        "actor": deal.proposer,
        "turn": turn,
        "payer": deal.proposer,
        "payee": deal.counterparty,
        "gold": deal.payment_gold,
        "deadline": deal.fund_by_turn,
        "preflight_status": "absent",
        "preflight_player": deal.proposer,
        "fingerprint": ExactPaymentOffer(
            deal.proposer,
            deal.counterparty,
            deal.payment_gold,
        ).fingerprint(),
    }


def payment_response_intent_payload(
    deal,
    source_id: str,
    *,
    accept: bool,
    turn: int = 4,
) -> dict:
    payload = {
        "source_id": source_id,
        "deal_id": deal.id,
        "actor": deal.counterparty,
        "turn": turn,
        "payer": deal.proposer,
        "payee": deal.counterparty,
        "gold": deal.payment_gold,
        "deadline": deal.payment_response_by_turn,
        "preflight_status": "exact",
        "preflight_player": deal.counterparty,
        "accept": accept,
        "fingerprint": ExactPaymentOffer(
            deal.proposer,
            deal.counterparty,
            deal.payment_gold,
        ).fingerprint(),
    }
    if accept:
        settled = dataclasses.replace(
            deal,
            payment_status=PaymentStatus.SETTLED,
        )
        if settled.favor_status is FavorStatus.SATISFIED:
            evidence = settled.favor.monitor.get("satisfaction_observation_id")
            settled = dataclasses.replace(
                settled,
                state=DealState.HONORED,
                terminal={
                    "reason": "favor and payment completed",
                    "evidence_refs": (
                        [evidence] if isinstance(evidence, str) else []
                    ),
                    "adjudication_source": "deterministic",
                },
            )
        payload["success_deal"] = ChannelRuntime._deal_payload(settled)
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ["up_front", "on_delivery"])
async def test_deal_honored_only_after_favor_and_exact_payment(
    tmp_path,
    payment_gs,
    timing,
):
    rt, deal = await accepted_payment_deal(tmp_path, payment_gs, timing=timing)
    if timing == "on_delivery":
        await satisfy_payment_favor(rt, payment_gs, deal, turn=3)
    fund = await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    assert fund.status == "applied"
    assert rt.deal(deal.id).payment_status is PaymentStatus.OFFERED
    response = await apply_payment_action(
        rt,
        payment_gs,
        deal.counterparty,
        "respond_to_payment",
        {"deal_id": deal.id, "accept": True},
        turn=4,
        action_observation=observation(
            deal.counterparty,
            4,
            camps=frozenset({(12, 7)}),
        ),
    )
    assert response.status == "applied"
    if timing == "up_front":
        assert rt.deal(deal.id).state is DealState.ACTIVE
        assert rt.deal(deal.id).favor_status is FavorStatus.DUE
        await satisfy_payment_favor(rt, payment_gs, deal, turn=5)
    assert rt.deal(deal.id).state is DealState.HONORED
    assert rt.deal(deal.id).payment_status is PaymentStatus.SETTLED


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_path", ["api", "cli"])
async def test_up_front_payment_accept_collects_action_term_and_preserves_proposal_baseline(
    tmp_path,
    payment_gs,
    entry_path,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    payment_gs.request_aware_observations = True
    payment_gs.local_player = deal.counterparty
    payment_gs.observations.extend(
        [observation(2, 5), observation(2, 5, camps=frozenset())]
    )
    admission = await rt.admit_player(payment_gs, deal.counterparty, 5)
    if entry_path == "api":
        admission.context.dispatch(
            "respond_to_payment",
            {"deal_id": deal.id, "accept": True},
        )
        policy_result = {"transcript": {"steps": [], "final_summary": ""}}
    else:
        policy_result = {
            "transcript": {
                "steps": [],
                "final_summary": (
                    'CHANNEL {"action":"respond_to_payment",'
                    f'"deal_id":"{deal.id}","accept":true}}'
                ),
            }
        }

    acknowledgements = await rt.finish_player(
        payment_gs,
        admission,
        policy_result,
    )

    completed = rt.deal(deal.id)
    assert acknowledgements[0].status == "applied"
    assert ObservationFamily.CAMPS in payment_gs.observation_requests[-1][2].families
    assert completed.state is DealState.HONORED
    assert completed.favor.baseline == {
        "proposal_turn": 1,
        "camp_present": True,
    }
    assert rt.state.grievances == ()


@pytest.mark.asyncio
async def test_up_front_payment_accept_collects_complete_favor_start_baseline(
    tmp_path,
    payment_gs,
):
    rt = runtime(tmp_path)
    payment_gs.runtime = rt
    deal = await accepted_deal(
        rt,
        payment_gs,
        favor={
            "term_type": "maintain_gold_reserve",
            "params": {"min_gold": 400},
        },
        timing="up_front",
    )
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    payment_gs.request_aware_observations = True
    payment_gs.local_player = deal.counterparty
    payment_gs.observations.extend(
        [
            observation(2, 5, treasury_gold=500),
            observation(2, 5, treasury_gold=500),
        ]
    )
    admission = await rt.admit_player(payment_gs, deal.counterparty, 5)
    admission.context.dispatch(
        "respond_to_payment",
        {"deal_id": deal.id, "accept": True},
    )

    acknowledgements = await rt.finish_player(
        payment_gs,
        admission,
        {"transcript": {"steps": [], "final_summary": ""}},
    )

    started = rt.deal(deal.id)
    assert acknowledgements[0].status == "applied"
    assert ObservationFamily.TREASURY in (
        payment_gs.observation_requests[-1][2].families
    )
    assert started.state is DealState.ACTIVE
    assert started.favor_status is FavorStatus.DUE
    assert started.favor.baseline["baseline_complete"] is True
    assert started.favor.baseline["favor_started_turn"] == 5
    assert started.favor.baseline["initial_violation_turn"] is None


@pytest.mark.asyncio
async def test_up_front_payment_accept_rejects_incomplete_favor_start_observation(
    tmp_path,
    payment_gs,
):
    rt = runtime(tmp_path)
    payment_gs.runtime = rt
    deal = await accepted_deal(
        rt,
        payment_gs,
        favor={
            "term_type": "maintain_gold_reserve",
            "params": {"min_gold": 400},
        },
        timing="up_front",
    )
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )

    response = await apply_payment_action(
        rt,
        payment_gs,
        deal.counterparty,
        "respond_to_payment",
        {"deal_id": deal.id, "accept": True},
        turn=4,
        action_observation=ChannelObservation(deal.counterparty, 4),
    )

    assert response.status == "rejected"
    assert "baseline is incomplete" in response.message
    assert rt.deal(deal.id).payment_status is PaymentStatus.OFFERED
    assert journal_events(rt)[-1]["kind"] != "payment_response_intent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, actor, turn, message",
    [
        ("fund_deal", 2, 3, "only the proposer"),
        ("fund_deal", 1, 5, "funding deadline"),
    ],
)
async def test_funding_validates_actor_and_inclusive_deadline(
    tmp_path,
    payment_gs,
    name,
    actor,
    turn,
    message,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    ack = await apply_payment_action(
        rt,
        payment_gs,
        actor,
        name,
        {"deal_id": deal.id},
        turn=turn,
    )
    assert ack.status == "rejected"
    assert message in ack.message
    assert payment_gs.offer_calls == 0
    assert rt.deal(deal.id).payment_status is PaymentStatus.DUE


@pytest.mark.asyncio
async def test_funding_refuses_ambiguous_preexisting_pending_trade(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    payment_gs.pending[(deal.proposer, deal.counterparty)] = "unrelated"
    before_events = len(journal_events(rt))
    ack = await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    assert ack.status == "rejected"
    assert rt.deal(deal.id).payment_status is PaymentStatus.DUE
    assert payment_gs.offer_calls == 0
    assert payment_gs.state_queries == [
        (
            deal.proposer,
            deal.proposer,
            deal.counterparty,
            deal.payment_gold,
        )
    ]
    assert all(
        event["kind"] != "payment_fund_intent"
        for event in journal_events(rt)[before_events:]
    )


@pytest.mark.asyncio
async def test_funding_intent_is_durable_before_engine_call_and_result_is_atomic(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    ack = await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
        source_id="src-fund-atomic",
    )
    assert payment_gs.intent_was_durable is True
    assert payment_gs.snapshot_at_offer is not None
    assert [event["kind"] for event in journal_events(rt)[-2:]] == [
        "payment_fund_intent",
        "payment_fund_result",
    ]
    intent = journal_events(rt)[-2]["payload"]
    assert intent["preflight_status"] == "absent"
    assert intent["preflight_player"] == deal.proposer

    rt.state_path.write_text(payment_gs.snapshot_at_offer)
    reopened = runtime(tmp_path)
    assert reopened.deal(deal.id).payment_status is PaymentStatus.OFFERED
    assert reopened.state.acknowledgements[-1] == ack
    assert "src-fund-atomic" in reopened.state.applied_source_ids


@pytest.mark.asyncio
async def test_failed_funding_call_stays_due_and_stable_source_does_not_resend(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    payment_gs.offer_results.append("Error: CHANNEL_PAYMENT_ADD_GOLD_FAILED")
    staged = stage(
        "src-fund-failed",
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
    )
    payment_gs.local_player = deal.proposer
    first = await rt.apply_staged(payment_gs, staged, turn=3, observation=None)
    replay = await rt.apply_staged(payment_gs, staged, turn=3, observation=None)
    assert first == replay
    assert first.status == "rejected"
    assert rt.deal(deal.id).payment_status is PaymentStatus.DUE
    assert payment_gs.offer_calls == 1


@pytest.mark.asyncio
async def test_ambiguous_funding_result_keeps_unfinished_intent_without_resend(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    payment_gs.offer_results.append("Action completed (no response).")
    staged = stage(
        "src-fund-no-response",
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
    )
    payment_gs.local_player = deal.proposer

    first = await rt.apply_staged(payment_gs, staged, turn=3, observation=None)
    retry = await rt.apply_staged(payment_gs, staged, turn=3, observation=None)

    assert first.status == "rejected"
    assert retry.status == "duplicate"
    assert journal_events(rt)[-1]["kind"] == "payment_fund_intent"
    assert "src-fund-no-response" not in rt.state.applied_source_ids
    assert rt.deal(deal.id).payment_status is PaymentStatus.DUE
    assert payment_gs.offer_calls == 1


@pytest.mark.asyncio
async def test_only_one_unresolved_offer_is_allowed_per_ordered_pair(
    tmp_path,
    payment_gs,
):
    rt, first = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    await apply_payment_action(
        rt,
        payment_gs,
        first.proposer,
        "fund_deal",
        {"deal_id": first.id},
        turn=3,
    )
    await rt.apply_staged(
        payment_gs,
        proposal(
            "proposal-second-payment",
            timing="up_front",
            favor={
                "term_type": "destroy_camp",
                "params": {"x": 12, "y": 7},
            },
        ),
        turn=3,
        observation=observation(1, 3, camps=frozenset({(12, 7)})),
    )
    second = rt.state.deals[-1]
    await rt.apply_staged(
        payment_gs,
        stage(
            "accept-second-payment",
            2,
            "respond_to_deal",
            {"deal_id": second.id, "accept": True},
        ),
        turn=4,
        observation=observation(2, 4),
    )
    ack = await apply_payment_action(
        rt,
        payment_gs,
        second.proposer,
        "fund_deal",
        {"deal_id": second.id},
        turn=5,
    )
    assert ack.status == "rejected"
    assert "unresolved" in ack.message
    assert payment_gs.offer_calls == 1
    assert rt.deal(second.id).payment_status is PaymentStatus.DUE


@pytest.mark.asyncio
async def test_payment_response_validates_actor_deadline_and_exact_offer(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    wrong_actor = await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "respond_to_payment",
        {"deal_id": deal.id, "accept": True},
        turn=4,
    )
    assert wrong_actor.status == "rejected"
    assert "only the counterparty" in wrong_actor.message
    payment_gs.pending[(deal.proposer, deal.counterparty)] = "unrelated"
    mismatch = await apply_payment_action(
        rt,
        payment_gs,
        deal.counterparty,
        "respond_to_payment",
        {"deal_id": deal.id, "accept": True},
        turn=4,
        source_id="response-mismatch",
    )
    assert mismatch.status == "rejected"
    assert "exact linked payment" in mismatch.message
    assert payment_gs.response_calls == []
    payment_gs.install_exact_offer(
        deal.proposer,
        deal.counterparty,
        deal.payment_gold,
    )
    late = await apply_payment_action(
        rt,
        payment_gs,
        deal.counterparty,
        "respond_to_payment",
        {"deal_id": deal.id, "accept": True},
        turn=6,
        source_id="response-late",
    )
    assert late.status == "rejected"
    assert "response deadline" in late.message
    assert payment_gs.response_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ["up_front", "on_delivery"])
async def test_rejecting_exact_payment_is_counterparty_breach(
    tmp_path,
    payment_gs,
    timing,
):
    rt, deal = await accepted_payment_deal(tmp_path, payment_gs, timing=timing)
    if timing == "on_delivery":
        await satisfy_payment_favor(rt, payment_gs, deal, turn=3)
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    ack = await apply_payment_action(
        rt,
        payment_gs,
        deal.counterparty,
        "respond_to_payment",
        {"deal_id": deal.id, "accept": False},
        turn=4,
    )
    broken = rt.deal(deal.id)
    assert ack.status == "applied"
    assert broken.state is DealState.BROKEN
    assert broken.payment_status is PaymentStatus.FAILED
    assert broken.terminal["wronged"] == deal.proposer
    assert broken.terminal["offender"] == deal.counterparty
    assert rt.state.grievances[-1].wronged == deal.proposer


@pytest.mark.asyncio
async def test_failed_payment_response_remains_offered_until_deadline(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    payment_gs.response_results.append("Error: CHANNEL_PAYMENT_ENGINE_FAILED")
    ack = await apply_payment_action(
        rt,
        payment_gs,
        deal.counterparty,
        "respond_to_payment",
        {"deal_id": deal.id, "accept": True},
        turn=4,
    )
    assert ack.status == "rejected"
    assert rt.deal(deal.id).payment_status is PaymentStatus.OFFERED
    assert rt.deal(deal.id).state is DealState.ACTIVE


@pytest.mark.asyncio
async def test_ignored_exact_payment_defaults_against_counterparty(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    assert rt.deal(deal.id).payment_response_by_turn == 5
    await rt.poll_unseated(payment_gs, turn=5, local_player_id=None)
    assert rt.deal(deal.id).state is DealState.ACTIVE
    await rt.poll_unseated(payment_gs, turn=6, local_player_id=None)
    assert rt.deal(deal.id).state is DealState.BROKEN
    assert rt.state.grievances[-1].wronged == deal.proposer


@pytest.mark.asyncio
async def test_recovery_observed_offer_records_offered_without_resend(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    rt._commit(
        "payment_fund_intent",
        payment_fund_intent_payload(deal, "src-fund-recovery"),
    )
    payment_gs.install_exact_offer(
        deal.proposer,
        deal.counterparty,
        deal.payment_gold,
    )
    payment_gs.local_player = deal.counterparty
    reopened = runtime(tmp_path)
    payment_gs.runtime = reopened
    await reopened.reconcile_payment_intents(
        payment_gs, current_turn=3, current_player_id=deal.counterparty
    )
    assert reopened.deal(deal.id).payment_status is PaymentStatus.OFFERED
    assert payment_gs.offer_calls == 0
    assert "src-fund-recovery" in reopened.state.applied_source_ids
    sequence = reopened.state.last_event_sequence
    replay = await apply_payment_action(
        reopened,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
        source_id="src-fund-recovery",
    )
    assert replay == reopened.state.acknowledgements[-1]
    assert reopened.state.last_event_sequence == sequence
    assert payment_gs.offer_calls == 0


@pytest.mark.asyncio
async def test_ambiguous_missing_offer_becomes_unverifiable_without_retry(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    rt._commit(
        "payment_fund_intent",
        payment_fund_intent_payload(deal, "src-fund-missing"),
    )
    payment_gs.local_player = deal.counterparty
    reopened = runtime(tmp_path)
    await reopened.reconcile_payment_intents(
        payment_gs, current_turn=3, current_player_id=deal.counterparty
    )
    assert reopened.deal(deal.id).state is DealState.UNVERIFIABLE
    assert payment_gs.offer_calls == 0
    assert reopened.state.grievances == ()


@pytest.mark.asyncio
async def test_recovery_conflicting_offer_retains_open_funding_obligation(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    rt._commit(
        "payment_fund_intent",
        payment_fund_intent_payload(deal, "src-fund-conflict"),
    )
    payment_gs.pending[(deal.proposer, deal.counterparty)] = ExactPaymentOffer(
        deal.proposer,
        deal.counterparty,
        deal.payment_gold + 1,
    )
    payment_gs.local_player = deal.counterparty
    reopened = runtime(tmp_path)
    await reopened.reconcile_payment_intents(
        payment_gs, current_turn=3, current_player_id=deal.counterparty
    )
    assert reopened.deal(deal.id).state is DealState.ACTIVE
    assert reopened.deal(deal.id).payment_status is PaymentStatus.DUE
    assert "src-fund-conflict" in reopened.state.applied_source_ids
    assert reopened.state.grievances == ()


@pytest.mark.asyncio
async def test_recovery_only_queries_intents_for_the_current_payee(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    rt._commit(
        "payment_fund_intent",
        payment_fund_intent_payload(deal, "src-fund-seat-filter"),
    )
    payment_gs.local_player = deal.proposer
    reopened = runtime(tmp_path)

    await reopened.reconcile_payment_intents(
        payment_gs,
        current_turn=3,
        current_player_id=deal.proposer,
    )

    assert payment_gs.state_queries == []
    assert reopened.deal(deal.id).payment_status is PaymentStatus.DUE
    assert "src-fund-seat-filter" not in reopened.state.applied_source_ids


@pytest.mark.asyncio
async def test_recovery_conflicting_offer_after_deadline_is_funding_breach(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    rt._commit(
        "payment_fund_intent",
        payment_fund_intent_payload(deal, "src-fund-conflict-late"),
    )
    payment_gs.pending[(deal.proposer, deal.counterparty)] = "unrelated"
    payment_gs.local_player = deal.counterparty
    reopened = runtime(tmp_path)

    await reopened.reconcile_payment_intents(
        payment_gs,
        current_turn=deal.fund_by_turn + 1,
        current_player_id=deal.counterparty,
    )

    broken = reopened.deal(deal.id)
    assert broken.state is DealState.BROKEN
    assert broken.payment_status is PaymentStatus.FAILED
    assert broken.terminal["wronged"] == deal.counterparty
    assert broken.terminal["offender"] == deal.proposer
    assert reopened.state.grievances[-1].wronged == deal.counterparty
    assert "src-fund-conflict-late" in reopened.state.applied_source_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("accept", [True, False])
async def test_response_recovery_retries_journaled_boolean_exactly_once(
    tmp_path,
    payment_gs,
    accept,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="on_delivery",
    )
    await satisfy_payment_favor(rt, payment_gs, deal, turn=3)
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    offered = rt.deal(deal.id)
    rt._commit(
        "payment_response_intent",
        payment_response_intent_payload(
            offered,
            "src-response-recovery",
            accept=accept,
        ),
    )
    payment_gs.local_player = deal.counterparty
    reopened = runtime(tmp_path)
    await reopened.reconcile_payment_intents(
        payment_gs, current_turn=4, current_player_id=deal.counterparty
    )
    assert payment_gs.response_calls == [accept]
    assert reopened.deal(deal.id).state is (
        DealState.HONORED if accept else DealState.BROKEN
    )
    assert "src-response-recovery" in reopened.state.applied_source_ids
    await reopened.reconcile_payment_intents(
        payment_gs, current_turn=4, current_player_id=deal.counterparty
    )
    assert payment_gs.response_calls == [accept]


@pytest.mark.asyncio
async def test_response_recovery_records_authoritative_failure_without_repeating(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="on_delivery",
    )
    await satisfy_payment_favor(rt, payment_gs, deal, turn=3)
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    offered = rt.deal(deal.id)
    rt._commit(
        "payment_response_intent",
        payment_response_intent_payload(
            offered,
            "src-response-recovery-failed",
            accept=True,
        ),
    )
    payment_gs.response_results.append("Error: CHANNEL_PAYMENT_ENGINE_FAILED")
    payment_gs.local_player = deal.counterparty
    reopened = runtime(tmp_path)

    await reopened.reconcile_payment_intents(
        payment_gs, current_turn=4, current_player_id=deal.counterparty
    )
    await reopened.reconcile_payment_intents(
        payment_gs, current_turn=4, current_player_id=deal.counterparty
    )

    assert reopened.deal(deal.id).payment_status is PaymentStatus.OFFERED
    assert payment_gs.response_calls == [True]
    assert "src-response-recovery-failed" in reopened.state.applied_source_ids


@pytest.mark.asyncio
async def test_response_recovery_missing_offer_is_unverifiable_without_retry(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="on_delivery",
    )
    await satisfy_payment_favor(rt, payment_gs, deal, turn=3)
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    offered = rt.deal(deal.id)
    rt._commit(
        "payment_response_intent",
        payment_response_intent_payload(
            offered,
            "src-response-missing",
            accept=True,
        ),
    )
    payment_gs.pending.clear()
    payment_gs.local_player = deal.counterparty
    reopened = runtime(tmp_path)
    await reopened.reconcile_payment_intents(
        payment_gs, current_turn=4, current_player_id=deal.counterparty
    )
    assert reopened.deal(deal.id).state is DealState.UNVERIFIABLE
    assert payment_gs.response_calls == []
    assert reopened.state.grievances == ()


@pytest.mark.asyncio
async def test_unfinished_response_intent_blocks_alternate_source_retry(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="on_delivery",
    )
    await satisfy_payment_favor(rt, payment_gs, deal, turn=3)
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    offered = rt.deal(deal.id)
    rt._commit(
        "payment_response_intent",
        payment_response_intent_payload(
            offered,
            "src-response-crashed",
            accept=True,
        ),
    )

    retry = await apply_payment_action(
        rt,
        payment_gs,
        deal.counterparty,
        "respond_to_payment",
        {"deal_id": deal.id, "accept": True},
        turn=4,
        source_id="src-response-alternate",
    )

    assert retry.status == "rejected"
    assert "requires reconciliation" in retry.message
    assert payment_gs.response_calls == []
    assert rt.deal(deal.id).payment_status is PaymentStatus.OFFERED


@pytest.mark.asyncio
async def test_open_rejects_non_integer_payment_result_fingerprint(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    assert payment_gs.snapshot_at_offer is not None
    events = journal_events(rt)
    result = next(
        event for event in events if event["kind"] == "payment_fund_result"
    )
    result["payload"]["fingerprint"]["duration"] = False
    rt.events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    rt.state_path.write_text(payment_gs.snapshot_at_offer)

    with pytest.raises(ChannelStateError, match="invalid channel journal"):
        runtime(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformation",
    ["successful-without-deal", "failure-with-offered-deal", "wrong-ack-actor"],
)
async def test_open_rejects_semantically_malformed_funding_result(
    tmp_path,
    payment_gs,
    malformation,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    assert payment_gs.snapshot_at_offer is not None
    events = journal_events(rt)
    result = next(
        event for event in events if event["kind"] == "payment_fund_result"
    )
    if malformation == "successful-without-deal":
        result["payload"]["deal"] = None
        result["payload"]["acknowledgement"].update(
            {"status": "rejected", "deal_id": None}
        )
    elif malformation == "failure-with-offered-deal":
        result["payload"]["engine_result"] = (
            "Error: CHANNEL_PAYMENT_ADD_GOLD_FAILED"
        )
    else:
        result["payload"]["acknowledgement"]["player_id"] = deal.counterparty
    rt.events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    rt.state_path.write_text(payment_gs.snapshot_at_offer)

    with pytest.raises(ChannelStateError, match="invalid channel journal"):
        runtime(tmp_path)


@pytest.mark.asyncio
async def test_open_rejects_accepted_response_fabricated_as_breach(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="on_delivery",
    )
    await satisfy_payment_favor(rt, payment_gs, deal, turn=3)
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    await apply_payment_action(
        rt,
        payment_gs,
        deal.counterparty,
        "respond_to_payment",
        {"deal_id": deal.id, "accept": True},
        turn=4,
    )
    assert payment_gs.snapshot_at_response is not None
    events = journal_events(rt)
    result = next(
        event for event in events if event["kind"] == "payment_response_result"
    )
    result["payload"]["deal"].update(
        {
            "state": "broken",
            "payment_status": "failed",
            "terminal": {
                "wronged": deal.proposer,
                "offender": deal.counterparty,
                "reason": "exact linked payment was rejected",
                "evidence_refs": [],
                "adjudication_source": "deterministic",
            },
        }
    )
    result["payload"]["grievance"] = {
        "id": "grv-000001",
        "wronged": deal.proposer,
        "offender": deal.counterparty,
        "deal_id": deal.id,
        "turn": 4,
        "reason": "exact linked payment was rejected",
        "payment_gold": deal.payment_gold,
        "base_magnitude": grievance_base_magnitude(deal.payment_gold),
        "half_life_turns": 30,
        "adjudication_source": "deterministic",
        "adjudication_metadata": None,
    }
    rt.events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    rt.state_path.write_text(payment_gs.snapshot_at_response)

    with pytest.raises(ChannelStateError, match="invalid channel journal"):
        runtime(tmp_path)


@pytest.mark.asyncio
async def test_open_rejects_accept_intent_without_exact_success_deal(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="on_delivery",
    )
    await satisfy_payment_favor(rt, payment_gs, deal, turn=3)
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    intent = payment_response_intent_payload(
        rt.deal(deal.id),
        "src-response-no-success",
        accept=True,
    )
    intent.pop("success_deal")
    append_complete_event(rt, "payment_response_intent", intent)

    with pytest.raises(ChannelStateError, match="invalid channel journal"):
        runtime(tmp_path)


@pytest.mark.asyncio
async def test_open_rejects_semantically_inconsistent_success_deal(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="on_delivery",
    )
    await satisfy_payment_favor(rt, payment_gs, deal, turn=3)
    await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    intent = payment_response_intent_payload(
        rt.deal(deal.id),
        "src-response-wrong-success",
        accept=True,
    )
    intent["success_deal"]["payment_status"] = "offered"
    append_complete_event(rt, "payment_response_intent", intent)

    with pytest.raises(ChannelStateError, match="invalid channel journal"):
        runtime(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("intent_turn", [-1, 1])
async def test_open_rejects_funding_intent_before_active_phase(
    tmp_path,
    payment_gs,
    intent_turn,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    append_complete_event(
        rt,
        "payment_fund_intent",
        payment_fund_intent_payload(
            deal,
            f"src-fund-turn-{intent_turn}",
            turn=intent_turn,
        ),
    )

    with pytest.raises(ChannelStateError, match="invalid channel journal"):
        runtime(tmp_path)


async def add_second_up_front_deal(
    rt: ChannelRuntime,
    payment_gs: PaymentGameState,
):
    await rt.apply_staged(
        payment_gs,
        proposal(
            "proposal-second-unfinished",
            timing="up_front",
            favor={
                "term_type": "destroy_camp",
                "params": {"x": 12, "y": 7},
            },
        ),
        turn=2,
        observation=observation(1, 2, camps=frozenset({(12, 7)})),
    )
    second = rt.state.deals[-1]
    await rt.apply_staged(
        payment_gs,
        stage(
            "accept-second-unfinished",
            2,
            "respond_to_deal",
            {"deal_id": second.id, "accept": True},
        ),
        turn=3,
        observation=observation(2, 3),
    )
    return rt.deal(second.id)


@pytest.mark.asyncio
async def test_open_rejects_two_unfinished_intents_for_one_ordered_pair(
    tmp_path,
    payment_gs,
):
    rt, first = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    second = await add_second_up_front_deal(rt, payment_gs)
    rt._commit(
        "payment_fund_intent",
        payment_fund_intent_payload(first, "src-pair-first"),
    )
    rt._commit(
        "payment_fund_intent",
        payment_fund_intent_payload(second, "src-pair-second", turn=4),
    )

    with pytest.raises(ChannelStateError, match="invalid channel journal"):
        runtime(tmp_path)


@pytest.mark.asyncio
async def test_open_rejects_intent_when_pair_already_has_offered_deal(
    tmp_path,
    payment_gs,
):
    rt, first = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    second = await add_second_up_front_deal(rt, payment_gs)
    await apply_payment_action(
        rt,
        payment_gs,
        first.proposer,
        "fund_deal",
        {"deal_id": first.id},
        turn=3,
    )
    append_complete_event(
        rt,
        "payment_fund_intent",
        payment_fund_intent_payload(second, "src-pair-offered", turn=4),
    )

    with pytest.raises(ChannelStateError, match="invalid channel journal"):
        runtime(tmp_path)


@pytest.mark.asyncio
async def test_payment_side_effect_does_not_catch_base_exception(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    payment_gs.offer_results.append(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await apply_payment_action(
            rt,
            payment_gs,
            deal.proposer,
            "fund_deal",
            {"deal_id": deal.id},
            turn=3,
            source_id="src-fund-cancelled",
        )
    assert journal_events(rt)[-1]["kind"] == "payment_fund_intent"
    assert "src-fund-cancelled" not in rt.state.applied_source_ids
