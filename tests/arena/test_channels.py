import math
from dataclasses import FrozenInstanceError, replace

import pytest

from civ_mcp.arena.channels import (
    ChannelAcknowledgement,
    ChannelEvent,
    ChannelProjection,
    ChannelState,
    Deal,
    DealState,
    FavorTerm,
    FavorStatus,
    Grievance,
    Message,
    PaymentStatus,
    effective_magnitude,
    event_from_dict,
    event_to_dict,
    format_channel_block,
    initial_channel_state,
    project_all,
    project_for_player,
    reduce_event,
    state_from_dict,
    state_to_dict,
)
from civ_mcp.arena.config import ChannelRules


def _event(seq: int, kind: str, payload: dict) -> ChannelEvent:
    return ChannelEvent(1, f"evt-{seq:06d}", seq, kind, payload)


def _deal_payload(
    deal_id: str = "deal-000001",
    *,
    proposer: int = 1,
    counterparty: int = 2,
    state: str = "proposed",
    favor_status: str = "not_due",
    payment_status: str = "not_due",
    terminal: dict | None = None,
) -> dict:
    return {
        "id": deal_id,
        "proposer": proposer,
        "counterparty": counterparty,
        "created_turn": 4,
        "accepted_turn": None,
        "accept_by_turn": 7,
        "completion_window_turns": 10,
        "favor": {
            "term_type": "destroy_camp",
            "params": {"x": 12, "y": 7},
            "baseline": {},
            "monitor": {},
        },
        "payment_gold": 100,
        "timing": "up_front",
        "state": state,
        "favor_status": favor_status,
        "payment_status": payment_status,
        "fund_by_turn": None,
        "payment_response_by_turn": None,
        "favor_due_turn": None,
        "terminal": terminal,
    }


def _deal(*args, **kwargs) -> Deal:
    payload = _deal_payload(*args, **kwargs)
    return Deal(
        **{
            **payload,
            "favor": FavorTerm(**payload["favor"]),
            "state": DealState(payload["state"]),
            "favor_status": FavorStatus(payload["favor_status"]),
            "payment_status": PaymentStatus(payload["payment_status"]),
        }
    )


def test_reducer_assigns_records_from_event_payload_and_round_trips():
    state = initial_channel_state("run-a", frozenset({1, 2, 3}), ChannelRules())
    state = reduce_event(
        state,
        _event(
            1,
            "message_sent",
            {
                "id": "msg-000001",
                "from_player": 1,
                "to_player": 2,
                "turn": 4,
                "text": "canary-a-b",
                "deal_id": None,
            },
        ),
    )
    restored = state_from_dict(state_to_dict(state))
    assert restored == state
    assert restored.next_message == 2
    assert restored.last_event_sequence == 1


def test_projection_filters_typed_records_before_rendering():
    state = initial_channel_state("run-a", frozenset({1, 2, 3}), ChannelRules())
    for seq, sender, recipient, text in [
        (1, 1, 2, "secret-12"),
        (2, 2, 3, "secret-23"),
        (3, 3, 1, "secret-31"),
    ]:
        state = reduce_event(
            state,
            _event(
                seq,
                "message_sent",
                {
                    "id": f"msg-{seq:06d}",
                    "from_player": sender,
                    "to_player": recipient,
                    "turn": 10,
                    "text": text,
                    "deal_id": None,
                },
            ),
        )
    p1 = project_for_player(state, 1, current_turn=10)
    rendered = format_channel_block(p1)
    assert "secret-12" in rendered and "secret-31" in rendered
    assert "secret-23" not in rendered


def test_grievance_magnitude_has_fixed_formula_and_threshold():
    assert (
        effective_magnitude(
            1.0, created_turn=10, current_turn=40, half_life_turns=30
        )
        == 0.5
    )
    assert math.isclose(
        effective_magnitude(
            10.0, created_turn=10, current_turn=70, half_life_turns=30
        ),
        2.5,
    )
    with pytest.raises(ValueError, match="half_life_turns must be positive"):
        effective_magnitude(1.0, 0, 1, 0)


def test_records_are_frozen_and_enums_are_string_valued():
    message = Message("msg-000001", 1, 2, 4, "hello", None)
    with pytest.raises(FrozenInstanceError):
        message.text = "changed"  # type: ignore[misc]
    assert DealState.ACTIVE == "active"
    assert FavorStatus.SATISFIED == "satisfied"
    assert PaymentStatus.SETTLED == "settled"


def test_reducer_handles_every_canonical_event_and_preserves_payloads():
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    state = reduce_event(state, _event(1, "deal_proposed", _deal_payload()))
    changed = {
        **_deal_payload(
            state="active", favor_status="not_due", payment_status="due"
        ),
        "accepted_turn": 5,
        "fund_by_turn": 7,
    }
    state = reduce_event(state, _event(2, "deal_changed", changed))
    state = reduce_event(
        state,
        _event(
            3,
            "grievance_created",
            {
                "id": "grv-000001",
                "wronged": 2,
                "offender": 1,
                "deal_id": "deal-000001",
                "turn": 8,
                "reason": "payment not funded",
                "payment_gold": 100,
                "base_magnitude": 1.0,
                "half_life_turns": 30,
                "adjudication_source": "deterministic",
                "adjudication_metadata": None,
            },
        ),
    )
    state = reduce_event(
        state,
        _event(
            4,
            "acknowledged",
            {
                "player_id": 1,
                "turn": 8,
                "source_id": "api:run-a:1:8:0:abc",
                "status": "applied",
                "message": "deal proposed",
                "deal_id": "deal-000001",
            },
        ),
    )
    observation = {"id": "obs-000001", "player_id": 1, "turn": 8}
    state = reduce_event(state, _event(5, "observation_recorded", observation))
    state = reduce_event(
        state, _event(6, "source_applied", {"source_id": "src-1"})
    )
    state = reduce_event(
        state,
        _event(
            7,
            "queue_advanced",
            {
                "cursor": 128,
                "reservation": {"request_id": "req-1", "end_cursor": 128},
                "request_id": "req-1",
            },
        ),
    )
    state = reduce_event(state, _event(8, "privacy_contaminated", {}))

    assert state.deals[0].state is DealState.ACTIVE
    assert state.deals[0].payment_status is PaymentStatus.DUE
    assert state.grievances[0].reason == "payment not funded"
    assert state.acknowledgements[0].source_id.endswith(":abc")
    assert state.observations == (observation,)
    assert state.applied_source_ids == frozenset({"src-1"})
    assert state.queue_cursor == 128
    assert state.queue_reservation == {
        "request_id": "req-1",
        "end_cursor": 128,
    }
    assert state.applied_request_ids == frozenset({"req-1"})
    assert state.privacy_contaminated is True
    assert state.next_deal == state.next_grievance == state.next_observation == 2
    assert state.next_event == 9


@pytest.mark.parametrize(
    "events, match",
    [
        ((_event(2, "privacy_contaminated", {}),), "non-consecutive"),
        (
            (
                ChannelEvent(1, "evt-999999", 1, "privacy_contaminated", {}),
            ),
            "event id",
        ),
        ((_event(1, "not_real", {}),), "unknown channel event kind"),
        (
            (
                _event(
                    1,
                    "message_sent",
                    {
                        "id": "msg-000002",
                        "from_player": 1,
                        "to_player": 2,
                        "turn": 1,
                        "text": "x",
                        "deal_id": None,
                    },
                ),
            ),
            "message id",
        ),
    ],
)
def test_reducer_rejects_invalid_sequences_kinds_and_counter_ids(events, match):
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    with pytest.raises(ValueError, match=match):
        for event in events:
            state = reduce_event(state, event)


def test_reducer_rejects_duplicate_ids_and_illegal_deal_transitions():
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    state = reduce_event(state, _event(1, "deal_proposed", _deal_payload()))
    with pytest.raises(ValueError, match="duplicate deal id"):
        reduce_event(state, _event(2, "deal_proposed", _deal_payload()))

    terminal = _deal_payload(
        state="declined",
        favor_status="released",
        payment_status="waived",
        terminal={"reason": "declined"},
    )
    terminal_state = reduce_event(state, _event(2, "deal_changed", terminal))
    with pytest.raises(ValueError, match="illegal deal state transition"):
        reduce_event(
            terminal_state,
            _event(3, "deal_changed", _deal_payload(state="active")),
        )


def test_state_and_event_serialization_are_json_shaped_and_strict():
    rules = ChannelRules()
    state = initial_channel_state("run-a", frozenset({3, 1}), rules)
    payload = state_to_dict(state)
    assert payload["enabled_players"] == [1, 3]
    assert payload["rules_fingerprint"] == rules.fingerprint()
    assert state_from_dict(payload) == state

    event = _event(1, "privacy_contaminated", {})
    assert event_from_dict(event_to_dict(event)) == event
    with pytest.raises(ValueError, match="unsupported channel schema"):
        state_from_dict({**payload, "schema_version": 2})


def test_projection_bounds_per_counterpart_and_keeps_unresolved_deals():
    rules = replace(
        ChannelRules(),
        prompt_messages_per_counterpart=2,
        recent_terminal_deals=1,
        prompt_grievance_threshold=0.5,
    )
    messages = tuple(
        Message(f"msg-{i:06d}", 1, counterpart, i, f"m-{counterpart}-{i}", None)
        for counterpart in (2, 3)
        for i in range(1, 4)
    )
    proposed = _deal()
    old_terminal = _deal(
        "deal-000002",
        state="declined",
        favor_status="released",
        payment_status="waived",
        terminal={"reason": "no"},
    )
    new_terminal = replace(
        _deal(
            "deal-000003",
            state="honored",
            favor_status="satisfied",
            payment_status="settled",
            terminal={"reason": "complete"},
        ),
        created_turn=6,
    )
    state = replace(
        initial_channel_state("run-a", frozenset({1, 2, 3}), rules),
        messages=messages,
        deals=(proposed, old_terminal, new_terminal),
        grievances=(
            # At turn 40 this decays from 0.5 to 0.25 and is omitted.
            Grievance(
                "grv-000001",
                1,
                2,
                "deal-000002",
                10,
                "old",
                50,
                0.5,
                30,
                "deterministic",
                None,
            ),
        ),
        acknowledgements=tuple(
            ChannelAcknowledgement(1, i, f"src-{i}", "applied", f"ack-{i}")
            for i in range(25)
        ),
    )

    projection = project_for_player(state, 1, current_turn=40)
    assert [m.text for m in projection.messages] == [
        "m-2-2",
        "m-2-3",
        "m-3-2",
        "m-3-3",
    ]
    assert [d.id for d in projection.deals] == ["deal-000001", "deal-000003"]
    assert projection.grievances == ()
    assert [a.message for a in projection.acknowledgements] == [
        f"ack-{i}" for i in range(5, 25)
    ]

    full = project_all(state)
    assert full.messages == messages
    assert full.deals == state.deals
    assert full.grievances == state.grievances


def test_disabled_projection_is_empty_and_cli_examples_are_exact():
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    assert project_for_player(state, 9, current_turn=1) == ChannelProjection(player_id=9)

    rendered = format_channel_block(ChannelProjection(player_id=1, cli_instructions=True))
    assert 'CHANNEL {"action":"send_message","to_player":2,"text":"..."}' in rendered
    assert 'CHANNEL {"action":"respond_to_payment","deal_id":"deal-000001","accept":true}' in rendered
