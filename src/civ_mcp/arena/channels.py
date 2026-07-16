from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from typing import Any

from civ_mcp.arena.config import ChannelRules


SCHEMA_VERSION = 1


class DealState(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    HONORED = "honored"
    BROKEN = "broken"
    DECLINED = "declined"
    EXPIRED = "expired"
    UNVERIFIABLE = "unverifiable"


class FavorStatus(StrEnum):
    NOT_DUE = "not_due"
    DUE = "due"
    SATISFIED = "satisfied"
    FAILED = "failed"
    RELEASED = "released"


class PaymentStatus(StrEnum):
    NOT_DUE = "not_due"
    DUE = "due"
    OFFERED = "offered"
    SETTLED = "settled"
    FAILED = "failed"
    WAIVED = "waived"


@dataclass(frozen=True)
class Message:
    id: str
    from_player: int
    to_player: int
    turn: int
    text: str
    deal_id: str | None


@dataclass(frozen=True)
class FavorTerm:
    term_type: str
    params: dict
    baseline: dict = field(default_factory=dict)
    monitor: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Deal:
    id: str
    proposer: int
    counterparty: int
    created_turn: int
    accepted_turn: int | None
    accept_by_turn: int
    completion_window_turns: int
    favor: FavorTerm
    payment_gold: int
    timing: str
    state: DealState
    favor_status: FavorStatus
    payment_status: PaymentStatus
    fund_by_turn: int | None
    payment_response_by_turn: int | None
    favor_due_turn: int | None
    terminal: dict | None

    @property
    def is_terminal(self) -> bool:
        return self.state not in (DealState.PROPOSED, DealState.ACTIVE)


@dataclass(frozen=True)
class Grievance:
    id: str
    wronged: int
    offender: int
    deal_id: str
    turn: int
    reason: str
    payment_gold: int
    base_magnitude: float
    half_life_turns: int
    adjudication_source: str
    adjudication_metadata: dict | None


@dataclass(frozen=True)
class ChannelAcknowledgement:
    player_id: int
    turn: int
    source_id: str
    status: str
    message: str
    deal_id: str | None = None


@dataclass(frozen=True)
class ChannelEvent:
    schema_version: int
    id: str
    sequence: int
    kind: str
    payload: dict


@dataclass(frozen=True)
class ChannelState:
    schema_version: int
    run_id: str
    enabled_players: frozenset[int]
    rules_fingerprint: dict[str, int | float]
    messages: tuple[Message, ...] = ()
    deals: tuple[Deal, ...] = ()
    grievances: tuple[Grievance, ...] = ()
    acknowledgements: tuple[ChannelAcknowledgement, ...] = ()
    observations: tuple[dict, ...] = ()
    applied_source_ids: frozenset[str] = frozenset()
    queue_cursor: int = 0
    queue_reservation: dict | None = None
    applied_request_ids: frozenset[str] = frozenset()
    privacy_contaminated: bool = False
    next_message: int = 1
    next_deal: int = 1
    next_grievance: int = 1
    next_observation: int = 1
    next_event: int = 1
    last_event_sequence: int = 0


@dataclass(frozen=True)
class ChannelProjection:
    player_id: int
    messages: tuple[Message, ...] = ()
    deals: tuple[Deal, ...] = ()
    grievances: tuple[Grievance, ...] = ()
    acknowledgements: tuple[ChannelAcknowledgement, ...] = ()
    cli_instructions: bool = False


_DEAL_TRANSITIONS = {
    DealState.PROPOSED: frozenset(
        {
            DealState.PROPOSED,
            DealState.ACTIVE,
            DealState.DECLINED,
            DealState.EXPIRED,
            DealState.UNVERIFIABLE,
        }
    ),
    DealState.ACTIVE: frozenset(
        {
            DealState.ACTIVE,
            DealState.HONORED,
            DealState.BROKEN,
            DealState.UNVERIFIABLE,
        }
    ),
    DealState.HONORED: frozenset({DealState.HONORED}),
    DealState.BROKEN: frozenset({DealState.BROKEN}),
    DealState.DECLINED: frozenset({DealState.DECLINED}),
    DealState.EXPIRED: frozenset({DealState.EXPIRED}),
    DealState.UNVERIFIABLE: frozenset({DealState.UNVERIFIABLE}),
}

_FAVOR_TRANSITIONS = {
    FavorStatus.NOT_DUE: frozenset(
        {FavorStatus.NOT_DUE, FavorStatus.DUE, FavorStatus.RELEASED}
    ),
    FavorStatus.DUE: frozenset(
        {
            FavorStatus.DUE,
            FavorStatus.SATISFIED,
            FavorStatus.FAILED,
            FavorStatus.RELEASED,
        }
    ),
    FavorStatus.SATISFIED: frozenset({FavorStatus.SATISFIED}),
    FavorStatus.FAILED: frozenset({FavorStatus.FAILED}),
    FavorStatus.RELEASED: frozenset({FavorStatus.RELEASED}),
}

_PAYMENT_TRANSITIONS = {
    PaymentStatus.NOT_DUE: frozenset(
        {PaymentStatus.NOT_DUE, PaymentStatus.DUE, PaymentStatus.WAIVED}
    ),
    PaymentStatus.DUE: frozenset(
        {
            PaymentStatus.DUE,
            PaymentStatus.OFFERED,
            PaymentStatus.FAILED,
            PaymentStatus.WAIVED,
        }
    ),
    PaymentStatus.OFFERED: frozenset(
        {PaymentStatus.OFFERED, PaymentStatus.SETTLED, PaymentStatus.FAILED}
    ),
    PaymentStatus.SETTLED: frozenset({PaymentStatus.SETTLED}),
    PaymentStatus.FAILED: frozenset({PaymentStatus.FAILED}),
    PaymentStatus.WAIVED: frozenset({PaymentStatus.WAIVED}),
}


def initial_channel_state(
    run_id: str, enabled_players: frozenset[int], rules: ChannelRules
) -> ChannelState:
    return ChannelState(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        enabled_players=frozenset(enabled_players),
        rules_fingerprint=rules.fingerprint(),
    )


def _expected_id(kind: str, counter: int) -> str:
    prefixes = {
        "message": "msg",
        "deal": "deal",
        "grievance": "grv",
        "observation": "obs",
        "event": "evt",
    }
    return f"{prefixes[kind]}-{counter:06d}"


def _require_counter_id(kind: str, record_id: Any, counter: int) -> str:
    expected = _expected_id(kind, counter)
    if not isinstance(record_id, str) or record_id != expected:
        raise ValueError(f"{kind} id must be {expected!r}")
    return record_id


def _require_payload(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("channel event payload must be an object")
    return payload


def _construct(record_type: type, payload: dict, label: str):
    try:
        return record_type(**payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label} payload: {exc}") from exc


def _favor_from_dict(payload: Any) -> FavorTerm:
    if not isinstance(payload, dict):
        raise ValueError("invalid favor payload: expected object")
    return _construct(FavorTerm, copy.deepcopy(payload), "favor")


def _deal_from_dict(payload: dict) -> Deal:
    data = copy.deepcopy(payload)
    try:
        data["favor"] = _favor_from_dict(data["favor"])
        data["state"] = DealState(data["state"])
        data["favor_status"] = FavorStatus(data["favor_status"])
        data["payment_status"] = PaymentStatus(data["payment_status"])
    except KeyError as exc:
        raise ValueError(f"invalid deal payload: missing field {exc.args[0]!r}") from exc
    except ValueError as exc:
        raise ValueError(f"invalid deal enum value: {exc}") from exc
    return _construct(Deal, data, "deal")


def _deal_to_dict(deal: Deal) -> dict:
    data = asdict(deal)
    data["state"] = deal.state.value
    data["favor_status"] = deal.favor_status.value
    data["payment_status"] = deal.payment_status.value
    return data


def _assert_unique(existing: tuple, record_id: str, label: str) -> None:
    if any(record.id == record_id for record in existing):
        raise ValueError(f"duplicate {label} id {record_id!r}")


def _validate_initial_deal(deal: Deal) -> None:
    if deal.state is not DealState.PROPOSED:
        raise ValueError("new deal state must be proposed")
    if deal.favor_status is not FavorStatus.NOT_DUE:
        raise ValueError("new deal favor status must be not_due")
    if deal.payment_status is not PaymentStatus.NOT_DUE:
        raise ValueError("new deal payment status must be not_due")
    if deal.terminal is not None:
        raise ValueError("new deal cannot contain terminal data")
    _validate_deal_coherence(deal)


def _validate_deal_coherence(deal: Deal) -> None:
    accepted = deal.accepted_turn is not None
    if deal.state is DealState.PROPOSED:
        if accepted:
            raise ValueError("proposed deal cannot be accepted")
        if any(
            deadline is not None
            for deadline in (
                deal.fund_by_turn,
                deal.payment_response_by_turn,
                deal.favor_due_turn,
            )
        ):
            raise ValueError("proposed deal cannot have active lifecycle deadlines")
        return

    if deal.state in (DealState.ACTIVE, DealState.HONORED, DealState.BROKEN):
        if not accepted:
            raise ValueError(f"{deal.state.value} deal requires accepted_turn")
        if isinstance(deal.accepted_turn, bool) or not isinstance(
            deal.accepted_turn, int
        ):
            raise ValueError("accepted_turn must be an integer")
        if deal.accepted_turn < deal.created_turn:
            raise ValueError("accepted_turn cannot precede created_turn")

    if deal.state in (DealState.DECLINED, DealState.EXPIRED) and accepted:
        raise ValueError(f"{deal.state.value} deal cannot be accepted")

    if deal.state is DealState.ACTIVE:
        if deal.favor_status is FavorStatus.DUE and deal.favor_due_turn is None:
            raise ValueError("due favor requires favor_due_turn")
        if (
            deal.payment_status in (PaymentStatus.DUE, PaymentStatus.OFFERED)
            and deal.fund_by_turn is None
        ):
            raise ValueError("due or offered payment requires fund_by_turn")
        if (
            deal.payment_status is PaymentStatus.OFFERED
            and deal.payment_response_by_turn is None
        ):
            raise ValueError("offered payment requires payment_response_by_turn")
        if (
            deal.favor_status is FavorStatus.SATISFIED
            and deal.payment_status is PaymentStatus.SETTLED
        ):
            raise ValueError("completed active deal must be honored")

    if deal.state is DealState.HONORED and not (
        deal.favor_status is FavorStatus.SATISFIED
        and deal.payment_status is PaymentStatus.SETTLED
    ):
        raise ValueError(
            "honored deal requires satisfied favor and settled payment"
        )


def _validate_deal_transition(before: Deal, after: Deal) -> None:
    if (before.proposer, before.counterparty) != (
        after.proposer,
        after.counterparty,
    ):
        raise ValueError("deal parties are immutable")
    if before.created_turn != after.created_turn:
        raise ValueError("deal created_turn is immutable")
    if before.completion_window_turns != after.completion_window_turns:
        raise ValueError("deal completion window is immutable")
    if before.favor.term_type != after.favor.term_type:
        raise ValueError("deal favor term type is immutable")
    if before.favor.params != after.favor.params:
        raise ValueError("deal favor parameters are immutable")
    if before.payment_gold != after.payment_gold or before.timing != after.timing:
        raise ValueError("deal payment terms are immutable")
    if after.state not in _DEAL_TRANSITIONS[before.state]:
        raise ValueError(
            f"illegal deal state transition {before.state.value}->{after.state.value}"
        )
    if after.favor_status not in _FAVOR_TRANSITIONS[before.favor_status]:
        raise ValueError(
            "illegal favor status transition "
            f"{before.favor_status.value}->{after.favor_status.value}"
        )
    if after.payment_status not in _PAYMENT_TRANSITIONS[before.payment_status]:
        raise ValueError(
            "illegal payment status transition "
            f"{before.payment_status.value}->{after.payment_status.value}"
        )
    if after.is_terminal and after.terminal is None:
        raise ValueError("terminal deal must contain terminal data")
    if not after.is_terminal and after.terminal is not None:
        raise ValueError("unresolved deal cannot contain terminal data")
    _validate_deal_coherence(after)


def _validate_grievance_link(state: ChannelState, grievance: Grievance) -> None:
    deal = next(
        (candidate for candidate in state.deals if candidate.id == grievance.deal_id),
        None,
    )
    if deal is None:
        raise ValueError(f"unknown grievance deal {grievance.deal_id!r}")
    if deal.state is not DealState.BROKEN:
        raise ValueError("grievances require a broken deal")
    if (grievance.wronged, grievance.offender) not in (
        (deal.proposer, deal.counterparty),
        (deal.counterparty, deal.proposer),
    ):
        raise ValueError("grievance parties must be deal participants")
    terminal = deal.terminal or {}
    if (
        terminal.get("wronged") != grievance.wronged
        or terminal.get("offender") != grievance.offender
    ):
        raise ValueError("grievance parties do not match terminal breach")


def reduce_event(state: ChannelState, event: ChannelEvent) -> ChannelState:
    if state.schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported channel schema {state.schema_version}")
    if event.schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported channel event schema {event.schema_version}")
    expected_sequence = state.last_event_sequence + 1
    if event.sequence != expected_sequence or event.sequence != state.next_event:
        raise ValueError(
            "non-consecutive channel event sequence: "
            f"expected {expected_sequence}, got {event.sequence}"
        )
    _require_counter_id("event", event.id, state.next_event)
    payload = _require_payload(event.payload)
    changes: dict[str, Any]

    match event.kind:
        case "message_sent":
            record_id = payload.get("id")
            if isinstance(record_id, str):
                _assert_unique(state.messages, record_id, "message")
            _require_counter_id("message", record_id, state.next_message)
            message = _construct(Message, copy.deepcopy(payload), "message")
            pair_count = sum(
                existing.from_player == message.from_player
                and existing.to_player == message.to_player
                for existing in state.messages
            )
            limit = int(state.rules_fingerprint["max_messages_per_pair"])
            if pair_count >= limit:
                raise ValueError(
                    "message limit reached for ordered pair "
                    f"{message.from_player}->{message.to_player}"
                )
            changes = {
                "messages": state.messages + (message,),
                "next_message": state.next_message + 1,
            }

        case "deal_proposed":
            record_id = payload.get("id")
            if isinstance(record_id, str):
                _assert_unique(state.deals, record_id, "deal")
            _require_counter_id("deal", record_id, state.next_deal)
            deal = _deal_from_dict(payload)
            _validate_initial_deal(deal)
            changes = {
                "deals": state.deals + (deal,),
                "next_deal": state.next_deal + 1,
            }

        case "deal_changed":
            record_id = payload.get("id")
            if not isinstance(record_id, str):
                raise ValueError("deal_changed payload requires deal id")
            index = next(
                (i for i, deal in enumerate(state.deals) if deal.id == record_id),
                None,
            )
            if index is None:
                raise ValueError(f"unknown deal id {record_id!r}")
            before = state.deals[index]
            if "changes" in payload:
                if set(payload) != {"id", "changes"} or not isinstance(
                    payload["changes"], dict
                ):
                    raise ValueError("invalid deal_changed patch payload")
                updated_payload = {
                    **_deal_to_dict(before),
                    **copy.deepcopy(payload["changes"]),
                    "id": record_id,
                }
            else:
                updated_payload = payload
            after = _deal_from_dict(updated_payload)
            if after.id != before.id:
                raise ValueError("deal id is immutable")
            _validate_deal_transition(before, after)
            deals = list(state.deals)
            deals[index] = after
            changes = {"deals": tuple(deals)}

        case "grievance_created":
            record_id = payload.get("id")
            if isinstance(record_id, str):
                _assert_unique(state.grievances, record_id, "grievance")
            _require_counter_id("grievance", record_id, state.next_grievance)
            grievance = _construct(
                Grievance, copy.deepcopy(payload), "grievance"
            )
            _validate_grievance_link(state, grievance)
            changes = {
                "grievances": state.grievances + (grievance,),
                "next_grievance": state.next_grievance + 1,
            }

        case "acknowledged":
            acknowledgement = _construct(
                ChannelAcknowledgement,
                copy.deepcopy(payload),
                "acknowledgement",
            )
            if any(
                existing.source_id == acknowledgement.source_id
                for existing in state.acknowledgements
            ):
                raise ValueError(
                    f"duplicate acknowledgement source id {acknowledgement.source_id!r}"
                )
            changes = {
                "acknowledgements": state.acknowledgements + (acknowledgement,)
            }

        case "observation_recorded":
            record_id = payload.get("id")
            if not isinstance(record_id, str):
                raise ValueError("observation payload requires id")
            if any(obs.get("id") == record_id for obs in state.observations):
                raise ValueError(f"duplicate observation id {record_id!r}")
            _require_counter_id("observation", record_id, state.next_observation)
            changes = {
                "observations": state.observations + (copy.deepcopy(payload),),
                "next_observation": state.next_observation + 1,
            }

        case "source_applied":
            source_id = payload.get("source_id")
            if not isinstance(source_id, str) or not source_id:
                raise ValueError("source_applied requires a non-empty source_id")
            if source_id in state.applied_source_ids:
                raise ValueError(f"duplicate source id {source_id!r}")
            changes = {
                "applied_source_ids": state.applied_source_ids | {source_id}
            }

        case "queue_advanced":
            cursor = payload.get("cursor")
            if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
                raise ValueError("queue cursor must be a non-negative integer")
            if cursor < state.queue_cursor:
                raise ValueError("queue cursor cannot move backwards")
            reservation = payload.get("reservation")
            if reservation is not None and not isinstance(reservation, dict):
                raise ValueError("queue reservation must be an object or null")
            request_id = payload.get("request_id")
            request_ids = state.applied_request_ids
            if request_id is not None:
                if not isinstance(request_id, str) or not request_id:
                    raise ValueError("queue request_id must be a non-empty string")
                if request_id in request_ids:
                    raise ValueError(f"duplicate request id {request_id!r}")
                request_ids = request_ids | {request_id}
            unknown = set(payload) - {"cursor", "reservation", "request_id"}
            if unknown:
                raise ValueError(
                    f"unknown queue_advanced fields: {', '.join(sorted(unknown))}"
                )
            changes = {
                "queue_cursor": cursor,
                "queue_reservation": copy.deepcopy(reservation),
                "applied_request_ids": request_ids,
            }

        case "privacy_contaminated":
            if set(payload) - {"value"} or payload.get("value", True) is not True:
                raise ValueError("privacy_contaminated can only set value=true")
            changes = {"privacy_contaminated": True}

        case _:
            raise ValueError(f"unknown channel event kind {event.kind!r}")

    return replace(
        state,
        **changes,
        next_event=state.next_event + 1,
        last_event_sequence=event.sequence,
    )


def _message_to_dict(message: Message) -> dict:
    return asdict(message)


def _grievance_to_dict(grievance: Grievance) -> dict:
    return asdict(grievance)


def _acknowledgement_to_dict(acknowledgement: ChannelAcknowledgement) -> dict:
    return asdict(acknowledgement)


def state_to_dict(state: ChannelState) -> dict:
    return {
        "schema_version": state.schema_version,
        "run_id": state.run_id,
        "enabled_players": sorted(state.enabled_players),
        "rules_fingerprint": copy.deepcopy(state.rules_fingerprint),
        "messages": [_message_to_dict(message) for message in state.messages],
        "deals": [_deal_to_dict(deal) for deal in state.deals],
        "grievances": [
            _grievance_to_dict(grievance) for grievance in state.grievances
        ],
        "acknowledgements": [
            _acknowledgement_to_dict(acknowledgement)
            for acknowledgement in state.acknowledgements
        ],
        "observations": copy.deepcopy(list(state.observations)),
        "applied_source_ids": sorted(state.applied_source_ids),
        "queue_cursor": state.queue_cursor,
        "queue_reservation": copy.deepcopy(state.queue_reservation),
        "applied_request_ids": sorted(state.applied_request_ids),
        "privacy_contaminated": state.privacy_contaminated,
        "next_message": state.next_message,
        "next_deal": state.next_deal,
        "next_grievance": state.next_grievance,
        "next_observation": state.next_observation,
        "next_event": state.next_event,
        "last_event_sequence": state.last_event_sequence,
    }


_STATE_FIELDS = frozenset(ChannelState.__dataclass_fields__)


def state_from_dict(payload: dict) -> ChannelState:
    if not isinstance(payload, dict):
        raise ValueError("channel state must be an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported channel schema {payload.get('schema_version')!r}"
        )
    missing = _STATE_FIELDS - set(payload)
    unknown = set(payload) - _STATE_FIELDS
    if missing:
        raise ValueError(f"channel state missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"channel state has unknown fields: {', '.join(sorted(unknown))}")
    try:
        return ChannelState(
            schema_version=payload["schema_version"],
            run_id=payload["run_id"],
            enabled_players=frozenset(payload["enabled_players"]),
            rules_fingerprint=copy.deepcopy(payload["rules_fingerprint"]),
            messages=tuple(
                _construct(Message, copy.deepcopy(item), "message")
                for item in payload["messages"]
            ),
            deals=tuple(_deal_from_dict(item) for item in payload["deals"]),
            grievances=tuple(
                _construct(Grievance, copy.deepcopy(item), "grievance")
                for item in payload["grievances"]
            ),
            acknowledgements=tuple(
                _construct(
                    ChannelAcknowledgement,
                    copy.deepcopy(item),
                    "acknowledgement",
                )
                for item in payload["acknowledgements"]
            ),
            observations=tuple(copy.deepcopy(payload["observations"])),
            applied_source_ids=frozenset(payload["applied_source_ids"]),
            queue_cursor=payload["queue_cursor"],
            queue_reservation=copy.deepcopy(payload["queue_reservation"]),
            applied_request_ids=frozenset(payload["applied_request_ids"]),
            privacy_contaminated=payload["privacy_contaminated"],
            next_message=payload["next_message"],
            next_deal=payload["next_deal"],
            next_grievance=payload["next_grievance"],
            next_observation=payload["next_observation"],
            next_event=payload["next_event"],
            last_event_sequence=payload["last_event_sequence"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid channel state: {exc}") from exc


def event_to_dict(event: ChannelEvent) -> dict:
    return {
        "schema_version": event.schema_version,
        "id": event.id,
        "sequence": event.sequence,
        "kind": event.kind,
        "payload": copy.deepcopy(event.payload),
    }


def event_from_dict(payload: dict) -> ChannelEvent:
    if not isinstance(payload, dict):
        raise ValueError("channel event must be an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported channel event schema {payload.get('schema_version')!r}"
        )
    if set(payload) != set(ChannelEvent.__dataclass_fields__):
        raise ValueError("channel event fields do not match schema")
    return _construct(ChannelEvent, copy.deepcopy(payload), "channel event")


def effective_magnitude(
    base_magnitude: float,
    created_turn: int,
    current_turn: int,
    half_life_turns: int,
) -> float:
    if half_life_turns <= 0:
        raise ValueError("half_life_turns must be positive")
    age_turns = max(0, current_turn - created_turn)
    return base_magnitude * 0.5 ** (age_turns / half_life_turns)


def _bound_projection(
    projection: ChannelProjection,
    rules_fingerprint: dict[str, int | float],
    current_turn: int,
) -> ChannelProjection:
    message_limit = int(rules_fingerprint["prompt_messages_per_counterpart"])
    kept_message_indices: set[int] = set()
    per_counterpart: dict[int, int] = {}
    for index in range(len(projection.messages) - 1, -1, -1):
        message = projection.messages[index]
        counterpart = (
            message.to_player
            if message.from_player == projection.player_id
            else message.from_player
        )
        count = per_counterpart.get(counterpart, 0)
        if count < message_limit:
            kept_message_indices.add(index)
            per_counterpart[counterpart] = count + 1
    messages = tuple(
        message
        for index, message in enumerate(projection.messages)
        if index in kept_message_indices
    )

    unresolved = tuple(deal for deal in projection.deals if not deal.is_terminal)
    terminal = tuple(deal for deal in projection.deals if deal.is_terminal)
    terminal_limit = int(rules_fingerprint["recent_terminal_deals"])
    deals = unresolved + terminal[-terminal_limit:] if terminal_limit else unresolved

    threshold = float(rules_fingerprint["prompt_grievance_threshold"])
    grievances = tuple(
        grievance
        for grievance in projection.grievances
        if effective_magnitude(
            grievance.base_magnitude,
            grievance.turn,
            current_turn,
            grievance.half_life_turns,
        )
        >= threshold
    )

    return replace(
        projection,
        messages=messages,
        deals=deals,
        grievances=grievances,
        acknowledgements=projection.acknowledgements[-20:],
    )


def project_for_player(
    state: ChannelState, player_id: int, current_turn: int
) -> ChannelProjection:
    if player_id not in state.enabled_players:
        return ChannelProjection(player_id=player_id)
    messages = tuple(
        message
        for message in state.messages
        if player_id in (message.from_player, message.to_player)
    )
    deals = tuple(
        deal
        for deal in state.deals
        if player_id in (deal.proposer, deal.counterparty)
    )
    grievances = tuple(
        grievance
        for grievance in state.grievances
        if player_id in (grievance.wronged, grievance.offender)
    )
    acknowledgements = tuple(
        acknowledgement
        for acknowledgement in state.acknowledgements
        if acknowledgement.player_id == player_id
    )
    return _bound_projection(
        ChannelProjection(
            player_id, messages, deals, grievances, acknowledgements
        ),
        state.rules_fingerprint,
        current_turn,
    )


def project_all(state: ChannelState) -> ChannelProjection:
    return ChannelProjection(
        player_id=-1,
        messages=state.messages,
        deals=state.deals,
        grievances=state.grievances,
        acknowledgements=state.acknowledgements,
    )


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def format_channel_block(projection: ChannelProjection) -> str:
    lines = ["== PRIVATE UNOFFICIAL CHANNELS =="]
    if projection.messages:
        lines.append("Messages:")
        lines.extend(
            f"- [{message.id} turn {message.turn}] Player {message.from_player} -> "
            f"Player {message.to_player}: {message.text}"
            + (f" (deal {message.deal_id})" if message.deal_id else "")
            for message in projection.messages
        )
    if projection.deals:
        lines.append("Deals:")
        for deal in projection.deals:
            deadline_parts = [f"accept by {deal.accept_by_turn}"]
            if deal.fund_by_turn is not None:
                deadline_parts.append(f"fund by {deal.fund_by_turn}")
            if deal.payment_response_by_turn is not None:
                deadline_parts.append(
                    f"payment response by {deal.payment_response_by_turn}"
                )
            if deal.favor_due_turn is not None:
                deadline_parts.append(f"favor due {deal.favor_due_turn}")
            lines.append(
                f"- [{deal.id}] Player {deal.proposer} -> Player {deal.counterparty}: "
                f"{deal.state.value}; favor={deal.favor.term_type}"
                f"{_compact_json(deal.favor.params)} ({deal.favor_status.value}); "
                f"payment={deal.payment_gold} gold/{deal.timing} "
                f"({deal.payment_status.value}); {', '.join(deadline_parts)}"
            )
    if projection.grievances:
        lines.append("Grievances:")
        lines.extend(
            f"- [{grievance.id} turn {grievance.turn}] Player {grievance.wronged} "
            f"against Player {grievance.offender}: {grievance.reason} "
            f"(deal {grievance.deal_id}, base magnitude "
            f"{grievance.base_magnitude:g}, source "
            f"{grievance.adjudication_source})"
            for grievance in projection.grievances
        )
    if projection.acknowledgements:
        lines.append("Action results:")
        lines.extend(
            f"- [turn {acknowledgement.turn}] {acknowledgement.status}: "
            f"{acknowledgement.message} ({acknowledgement.source_id})"
            + (
                f" (deal {acknowledgement.deal_id})"
                if acknowledgement.deal_id
                else ""
            )
            for acknowledgement in projection.acknowledgements
        )
    if projection.cli_instructions:
        lines.extend(
            [
                "CLI actions (emit exact one-line JSON):",
                'CHANNEL {"action":"send_message","to_player":2,"text":"..."}',
                'CHANNEL {"action":"propose_deal","to_player":2,"text":"...","favor":{"term_type":"destroy_camp","params":{"x":12,"y":7}},"payment_gold":100,"timing":"on_delivery","within":10}',
                'CHANNEL {"action":"respond_to_deal","deal_id":"deal-000001","accept":true}',
                'CHANNEL {"action":"fund_deal","deal_id":"deal-000001"}',
                'CHANNEL {"action":"respond_to_payment","deal_id":"deal-000001","accept":true}',
            ]
        )
    return "\n".join(lines)
