import math
from dataclasses import FrozenInstanceError, replace

import pytest

from civ_mcp.arena.channels import (
    CHANNEL_GUIDANCE_TEXT,
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
    deal_action_hint,
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


def _message_payload(
    message_id: str, sender: int = 1, recipient: int = 2, turn: int = 4
) -> dict:
    return {
        "id": message_id,
        "from_player": sender,
        "to_player": recipient,
        "turn": turn,
        "text": f"message {message_id}",
        "deal_id": None,
    }


def _proposal_payload(deal: dict | None = None) -> dict:
    deal = deal or _deal_payload()
    return {
        "deal": deal,
        "message": {
            "id": "msg-000001",
            "from_player": deal["proposer"],
            "to_player": deal["counterparty"],
            "turn": deal["created_turn"],
            "text": "clear the northern camp",
            "deal_id": deal["id"],
        },
    }


def _grievance_payload(
    *, wronged: int = 1, offender: int = 2, deal_id: str = "deal-000001"
) -> dict:
    return {
        "id": "grv-000001",
        "wronged": wronged,
        "offender": offender,
        "deal_id": deal_id,
        "turn": 8,
        "reason": "paid favor was not delivered",
        "payment_gold": 100,
        "base_magnitude": 1.0,
        "half_life_turns": 30,
        "adjudication_source": "deterministic",
        "adjudication_metadata": None,
    }


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


def test_deal_proposal_atomically_creates_linked_message_and_deal():
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())

    state = reduce_event(
        state,
        _event(1, "deal_proposed", _proposal_payload()),
    )

    assert state.deals[0].id == "deal-000001"
    assert state.messages[0].deal_id == state.deals[0].id
    assert state.messages[0].text == "clear the northern camp"
    assert state.next_deal == state.next_message == 2


def test_staged_action_event_atomically_applies_effect_source_and_acknowledgement():
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    acknowledgement = {
        "player_id": 1,
        "turn": 4,
        "source_id": "src-1",
        "status": "applied",
        "message": "sent private message msg-000001 to player 2",
        "deal_id": None,
    }

    state = reduce_event(
        state,
        _event(
            1,
            "staged_action_applied",
            {
                "source_id": "src-1",
                "acknowledgement": acknowledgement,
                "effect": {
                    "kind": "message_sent",
                    "payload": _message_payload("msg-000001"),
                },
            },
        ),
    )

    assert state.messages[0].id == "msg-000001"
    assert state.applied_source_ids == frozenset({"src-1"})
    assert state.acknowledgements == (ChannelAcknowledgement(**acknowledgement),)
    assert state.last_event_sequence == 1


def test_deal_broken_event_atomically_changes_deal_and_creates_grievance():
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    state = reduce_event(state, _event(1, "deal_proposed", _proposal_payload()))
    active = {
        **_deal_payload(
            state="active", favor_status="not_due", payment_status="due"
        ),
        "accepted_turn": 5,
        "fund_by_turn": 7,
    }
    state = reduce_event(state, _event(2, "deal_changed", active))
    broken = {
        **active,
        "state": "broken",
        "favor_status": "released",
        "payment_status": "failed",
        "terminal": {
            "wronged": 2,
            "offender": 1,
            "reason": "payment not funded",
            "adjudication_source": "deterministic",
        },
    }

    state = reduce_event(
        state,
        _event(
            3,
            "deal_broken",
            {
                "deal": broken,
                "grievance": _grievance_payload(wronged=2, offender=1),
            },
        ),
    )

    assert state.deals[0].state is DealState.BROKEN
    assert state.grievances[0].deal_id == state.deals[0].id
    assert state.next_grievance == 2
    assert state.last_event_sequence == 3


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
    state = reduce_event(state, _event(1, "deal_proposed", _proposal_payload()))
    changed = {
        **_deal_payload(
            state="active", favor_status="not_due", payment_status="due"
        ),
        "accepted_turn": 5,
        "fund_by_turn": 7,
    }
    state = reduce_event(state, _event(2, "deal_changed", changed))
    broken = {
        **changed,
        "state": "broken",
        "favor_status": "released",
        "payment_status": "failed",
        "terminal": {
            "wronged": 2,
            "offender": 1,
            "reason": "payment not funded",
            "adjudication_source": "deterministic",
        },
    }
    state = reduce_event(state, _event(3, "deal_changed", broken))
    state = reduce_event(
        state,
        _event(
            4,
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
            5,
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
    state = reduce_event(state, _event(6, "observation_recorded", observation))
    state = reduce_event(
        state, _event(7, "source_applied", {"source_id": "src-1"})
    )
    state = reduce_event(
        state,
        _event(
            8,
            "queue_advanced",
            {
                "cursor": 128,
                "reservation": {"request_id": "req-1", "end_cursor": 128},
                "request_id": "req-1",
            },
        ),
    )
    state = reduce_event(state, _event(9, "privacy_contaminated", {}))

    assert state.deals[0].state is DealState.BROKEN
    assert state.deals[0].payment_status is PaymentStatus.FAILED
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
    assert state.next_event == 10


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
    state = reduce_event(state, _event(1, "deal_proposed", _proposal_payload()))
    with pytest.raises(ValueError, match="duplicate deal id"):
        reduce_event(state, _event(2, "deal_proposed", _proposal_payload()))

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


def test_proposal_rejects_acceptance_and_active_lifecycle_fields():
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    incoherent = {
        **_deal_payload(),
        "accepted_turn": 4,
        "fund_by_turn": 6,
    }
    with pytest.raises(ValueError, match="proposed deal cannot be accepted"):
        reduce_event(
            state,
            _event(1, "deal_proposed", _proposal_payload(incoherent)),
        )

    accepted = reduce_event(
        state, _event(1, "deal_proposed", _proposal_payload())
    )
    assert accepted.deals[0].state is DealState.PROPOSED


def test_active_deal_requires_acceptance_and_honored_requires_completion():
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    state = reduce_event(state, _event(1, "deal_proposed", _proposal_payload()))

    active_without_acceptance = {
        **_deal_payload(
            state="active", favor_status="not_due", payment_status="due"
        ),
        "fund_by_turn": 7,
    }
    with pytest.raises(ValueError, match="active deal requires accepted_turn"):
        reduce_event(
            state, _event(2, "deal_changed", active_without_acceptance)
        )

    active = {
        **active_without_acceptance,
        "accepted_turn": 5,
    }
    state = reduce_event(state, _event(2, "deal_changed", active))
    incomplete_honored = {
        **active,
        "state": "honored",
        "terminal": {"reason": "complete"},
    }
    with pytest.raises(
        ValueError, match="honored deal requires satisfied favor and settled payment"
    ):
        reduce_event(state, _event(3, "deal_changed", incomplete_honored))

    offered = {
        **active,
        "payment_status": "offered",
        "payment_response_by_turn": 7,
    }
    state = reduce_event(state, _event(3, "deal_changed", offered))
    favor_due = {
        **offered,
        "favor_status": "due",
        "payment_status": "settled",
        "favor_due_turn": 15,
    }
    state = reduce_event(state, _event(4, "deal_changed", favor_due))
    honored = {
        **favor_due,
        "state": "honored",
        "terminal": {"reason": "complete"},
        "favor_status": "satisfied",
    }
    state = reduce_event(state, _event(5, "deal_changed", honored))
    assert state.deals[0].state is DealState.HONORED


def test_proposed_change_rejects_due_legs_but_allows_baseline_updates():
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    state = reduce_event(state, _event(1, "deal_proposed", _proposal_payload()))
    injected = _deal_payload(favor_status="due")
    with pytest.raises(ValueError, match="proposed deal must have non-due legs"):
        reduce_event(state, _event(2, "deal_changed", injected))

    baseline_update = _deal_payload()
    baseline_update["favor"]["baseline"] = {"camp_present": True}
    baseline_update["favor"]["monitor"] = {"last_observation": "obs-000001"}
    state = reduce_event(state, _event(2, "deal_changed", baseline_update))
    assert state.deals[0].favor.baseline == {"camp_present": True}
    assert state.deals[0].favor.monitor == {"last_observation": "obs-000001"}


@pytest.mark.parametrize("terminal_state", ["declined", "expired"])
def test_unaccepted_terminal_deals_reject_due_legs_and_deadlines(terminal_state):
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    state = reduce_event(state, _event(1, "deal_proposed", _proposal_payload()))
    injected = {
        **_deal_payload(
            state=terminal_state,
            favor_status="due",
            payment_status="waived",
            terminal={"reason": terminal_state},
        ),
        "favor_due_turn": 10,
    }
    with pytest.raises(
        ValueError, match=f"{terminal_state} deal must have non-due legs"
    ):
        reduce_event(state, _event(2, "deal_changed", injected))

    valid = {
        **_deal_payload(
            state=terminal_state,
            favor_status="released",
            payment_status="waived",
            terminal={"reason": terminal_state},
        )
    }
    state = reduce_event(state, _event(2, "deal_changed", valid))
    assert state.deals[0].accepted_turn is None
    assert state.deals[0].favor_due_turn is None


def test_preacceptance_unverifiable_cannot_invent_acceptance_or_obligations():
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    state = reduce_event(state, _event(1, "deal_proposed", _proposal_payload()))
    accepted_injection = {
        **_deal_payload(state="unverifiable", terminal={"reason": "missing"}),
        "accepted_turn": 5,
    }
    with pytest.raises(
        ValueError, match="cannot introduce accepted_turn before activation"
    ):
        reduce_event(state, _event(2, "deal_changed", accepted_injection))

    due_injection = {
        **_deal_payload(
            state="unverifiable",
            payment_status="due",
            terminal={"reason": "missing"},
        ),
        "fund_by_turn": 7,
    }
    with pytest.raises(
        ValueError, match="pre-acceptance unverifiable deal must have non-due legs"
    ):
        reduce_event(state, _event(2, "deal_changed", due_injection))

    valid = _deal_payload(
        state="unverifiable", terminal={"reason": "missing proposal evidence"}
    )
    state = reduce_event(state, _event(2, "deal_changed", valid))
    assert state.deals[0].accepted_turn is None
    assert state.deals[0].payment_status is PaymentStatus.NOT_DUE


@pytest.mark.parametrize("accepted_turn", [None, 6])
def test_postacceptance_unverifiable_preserves_exact_acceptance(accepted_turn):
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    state = reduce_event(state, _event(1, "deal_proposed", _proposal_payload()))
    active = {
        **_deal_payload(
            state="active", favor_status="not_due", payment_status="due"
        ),
        "accepted_turn": 5,
        "fund_by_turn": 7,
    }
    state = reduce_event(state, _event(2, "deal_changed", active))
    mutated = {
        **active,
        "state": "unverifiable",
        "accepted_turn": accepted_turn,
        "terminal": {"reason": "missing payment evidence"},
    }
    with pytest.raises(ValueError, match="accepted_turn is immutable after acceptance"):
        reduce_event(state, _event(3, "deal_changed", mutated))

    valid = {
        **mutated,
        "accepted_turn": 5,
    }
    state = reduce_event(state, _event(3, "deal_changed", valid))
    assert state.deals[0].accepted_turn == 5
    assert state.deals[0].state is DealState.UNVERIFIABLE


def test_grievance_requires_existing_broken_deal():
    state = initial_channel_state("run-a", frozenset({1, 2}), ChannelRules())
    with pytest.raises(ValueError, match="unknown grievance deal"):
        reduce_event(
            state,
            _event(1, "grievance_created", _grievance_payload()),
        )

    state = reduce_event(state, _event(1, "deal_proposed", _proposal_payload()))
    active = {
        **_deal_payload(
            state="active", favor_status="not_due", payment_status="due"
        ),
        "accepted_turn": 5,
        "fund_by_turn": 7,
    }
    state = reduce_event(state, _event(2, "deal_changed", active))
    unverifiable = {
        **active,
        "state": "unverifiable",
        "terminal": {"reason": "missing evidence"},
    }
    state = reduce_event(state, _event(3, "deal_changed", unverifiable))
    with pytest.raises(ValueError, match="grievances require a broken deal"):
        reduce_event(
            state,
            _event(4, "grievance_created", _grievance_payload()),
        )


def test_grievance_parties_match_broken_deal_and_terminal_breach():
    state = initial_channel_state("run-a", frozenset({1, 2, 3}), ChannelRules())
    state = reduce_event(state, _event(1, "deal_proposed", _proposal_payload()))
    active = {
        **_deal_payload(
            state="active", favor_status="not_due", payment_status="due"
        ),
        "accepted_turn": 5,
        "fund_by_turn": 7,
    }
    state = reduce_event(state, _event(2, "deal_changed", active))
    offered = {
        **active,
        "payment_status": "offered",
        "payment_response_by_turn": 7,
    }
    state = reduce_event(state, _event(3, "deal_changed", offered))
    favor_due = {
        **offered,
        "favor_status": "due",
        "payment_status": "settled",
        "favor_due_turn": 15,
    }
    state = reduce_event(state, _event(4, "deal_changed", favor_due))
    broken = {
        **favor_due,
        "state": "broken",
        "favor_status": "failed",
        "terminal": {
            "wronged": 1,
            "offender": 2,
            "reason": "paid favor was not delivered",
            "adjudication_source": "deterministic",
        },
    }
    state = reduce_event(state, _event(5, "deal_changed", broken))

    with pytest.raises(ValueError, match="grievance parties must be deal participants"):
        reduce_event(
            state,
            _event(
                6,
                "grievance_created",
                _grievance_payload(wronged=1, offender=3),
            ),
        )
    with pytest.raises(ValueError, match="grievance parties do not match terminal breach"):
        reduce_event(
            state,
            _event(
                6,
                "grievance_created",
                _grievance_payload(wronged=2, offender=1),
            ),
        )

    state = reduce_event(
        state,
        _event(6, "grievance_created", _grievance_payload()),
    )
    assert state.grievances[0].wronged == 1
    assert state.grievances[0].offender == 2


def test_message_cap_is_hard_per_ordered_pair_at_the_valid_boundary():
    rules = replace(ChannelRules(), max_messages_per_pair=2)
    state = initial_channel_state("run-a", frozenset({1, 2}), rules)
    state = reduce_event(
        state, _event(1, "message_sent", _message_payload("msg-000001"))
    )
    state = reduce_event(
        state, _event(2, "message_sent", _message_payload("msg-000002"))
    )
    state = reduce_event(
        state,
        _event(
            3,
            "message_sent",
            _message_payload("msg-000003", sender=2, recipient=1),
        ),
    )
    assert len(state.messages) == 3

    with pytest.raises(ValueError, match="message limit reached for ordered pair 1->2"):
        reduce_event(
            state,
            _event(4, "message_sent", _message_payload("msg-000004")),
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


def test_channel_guidance_renders_immediately_after_header_only_when_enabled():
    unguided = format_channel_block(ChannelProjection(player_id=1))
    guided = format_channel_block(ChannelProjection(player_id=1, guidance=True))

    assert unguided == "== PRIVATE UNOFFICIAL CHANNELS =="
    assert CHANNEL_GUIDANCE_TEXT not in unguided

    lines = guided.splitlines()
    assert lines[0] == "== PRIVATE UNOFFICIAL CHANNELS =="
    assert lines[1] == CHANNEL_GUIDANCE_TEXT


def test_channel_guidance_names_binding_deal_actions():
    assert "messages alone are NOT binding" in CHANNEL_GUIDANCE_TEXT
    assert "propose_deal action" in CHANNEL_GUIDANCE_TEXT
    assert "respond_to_deal before it expires" in CHANNEL_GUIDANCE_TEXT
    assert "send the gold with fund_deal when payment is due" in CHANNEL_GUIDANCE_TEXT
    assert "take it with respond_to_payment before its deadline" in CHANNEL_GUIDANCE_TEXT
    # v3 broke both LLM-side deals on the payment step: the old wording
    # ("fund deals you owe with fund_deal") drew six wrong-role fund_deal
    # attempts, all from non-proposers, and never named respond_to_payment.
    assert "fund deals you owe" not in CHANNEL_GUIDANCE_TEXT
    assert "you are the payer" in CHANNEL_GUIDANCE_TEXT
    assert "you are the payee" in CHANNEL_GUIDANCE_TEXT


def _hint_deal(**overrides):
    """Deal fixture for deal_action_hint; overrides drive the state under test."""
    base = dict(
        id="deal-000001",
        proposer=3,
        counterparty=1,
        created_turn=7,
        accepted_turn=None,
        accept_by_turn=10,
        completion_window_turns=5,
        favor=FavorTerm("keep_units_away", {"player_id": 3, "min_distance": 3}),
        payment_gold=50,
        timing="on_delivery",
        state=DealState.PROPOSED,
        favor_status=FavorStatus.NOT_DUE,
        payment_status=PaymentStatus.NOT_DUE,
        fund_by_turn=None,
        payment_response_by_turn=None,
        favor_due_turn=None,
        terminal=None,
    )
    base.update(overrides)
    return Deal(**base)


def test_deal_action_hint_names_role_and_only_the_currently_legal_action():
    proposed = _hint_deal()
    # Counterparty may respond; proposer must wait. v3 lost six actions to
    # players reaching for the other side's verb.
    counterparty_hint = deal_action_hint(proposed, 1)
    # Not yet accepted, so the role line must be conditional, not past-tense.
    assert "YOU ACCEPTED THIS" not in counterparty_hint
    assert "PROPOSED TO YOU — accept and you become the payee" in counterparty_hint
    assert "respond_to_deal (accept or decline) by turn 10" in counterparty_hint
    proposer_hint = deal_action_hint(proposed, 3)
    assert "YOU PROPOSED THIS — you are the payer, you owe 50 gold" in proposer_hint
    assert "waiting for Player 1 to respond" in proposer_hint
    assert "respond_to_deal" not in proposer_hint.split("AVAILABLE NOW:")[1]


def test_deal_action_hint_payment_due_is_proposer_only():
    due = _hint_deal(
        state=DealState.ACTIVE,
        accepted_turn=8,
        favor_status=FavorStatus.SATISFIED,
        payment_status=PaymentStatus.DUE,
        fund_by_turn=12,
    )
    assert "fund_deal — send the 50 gold by turn 12" in deal_action_hint(due, 3)
    payee = deal_action_hint(due, 1)
    assert "waiting for Player 3 to fund the payment" in payee
    assert "fund_deal" not in payee.split("AVAILABLE NOW:")[1]


def test_deal_action_hint_offered_payment_tells_payee_it_is_claimable_now():
    # v4's whole failure: both models fired respond_to_payment before the
    # payment existed, then went silent in the two turns it was valid.
    offered = _hint_deal(
        state=DealState.ACTIVE,
        accepted_turn=8,
        favor_status=FavorStatus.SATISFIED,
        payment_status=PaymentStatus.OFFERED,
        payment_response_by_turn=14,
    )
    payee = deal_action_hint(offered, 1)
    assert "respond_to_payment — the 50 gold is waiting for you" in payee
    assert "accept it by turn 14" in payee
    assert "waiting for Player 1 to accept the payment you sent" in deal_action_hint(offered, 3)


def test_deal_action_hint_before_payment_exists_says_nothing_is_available():
    pending_favor = _hint_deal(
        state=DealState.ACTIVE,
        accepted_turn=8,
        favor_status=FavorStatus.DUE,
        payment_status=PaymentStatus.NOT_DUE,
        favor_due_turn=13,
    )
    payee = deal_action_hint(pending_favor, 1)
    assert "AVAILABLE NOW: nothing — you owe the favor, due turn 13" in payee
    assert "respond_to_payment" not in payee


def test_deal_action_hint_closed_deal_offers_nothing():
    broken = _hint_deal(
        state=DealState.BROKEN,
        payment_status=PaymentStatus.FAILED,
        terminal={"reason": "exact linked payment was not accepted by the deadline"},
    )
    assert "AVAILABLE NOW: nothing — this deal is closed (broken)" in deal_action_hint(broken, 1)


def test_channel_block_renders_the_action_hint_under_each_deal():
    offered = _hint_deal(
        state=DealState.ACTIVE,
        accepted_turn=8,
        favor_status=FavorStatus.SATISFIED,
        payment_status=PaymentStatus.OFFERED,
        payment_response_by_turn=14,
    )
    block = format_channel_block(ChannelProjection(player_id=1, deals=(offered,)))
    lines = block.splitlines()
    deal_line = next(i for i, line in enumerate(lines) if line.startswith("- [deal-000001]"))
    assert lines[deal_line + 1].startswith("  YOU ACCEPTED THIS")
    assert "respond_to_payment" in lines[deal_line + 1]
