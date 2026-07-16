"""Coordinator-owned persistence and lifecycle for unofficial channels."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from civ_mcp.arena.channel_protocol import (
    ChannelTurnContext,
    FundDeal,
    ProposeDeal,
    RespondToDeal,
    RespondToPayment,
    SendMessage,
    StagedChannelAction,
    parse_cli_channel_lines,
)
from civ_mcp.arena.channel_terms import (
    ChannelObservation,
    ObservationFamily,
    ObservedAction,
    ObservedCity,
    ObservedRoute,
    ObservedUnit,
    TERM_REGISTRY,
    TermValidationContext,
    capture_baseline,
    compile_observation_request,
    normalize_action_audit,
    validate_term,
    verify_term,
)
from civ_mcp.arena.channels import (
    SCHEMA_VERSION,
    ChannelAcknowledgement,
    ChannelEvent,
    ChannelProjection,
    ChannelState,
    Deal,
    DealState,
    FavorStatus,
    FavorTerm,
    PaymentStatus,
    event_from_dict,
    event_to_dict,
    format_channel_block,
    initial_channel_state,
    reduce_event,
    project_for_player as build_player_projection,
    state_from_dict,
    state_to_dict,
)
from civ_mcp.arena.config import ChannelRules


class ChannelStateError(RuntimeError):
    """Canonical channel state cannot be opened safely."""


class _ActionRejected(ValueError):
    """A staged request is invalid without corrupting canonical state."""


class _IncompleteFavorObservation(_ActionRejected):
    """Favor start cannot be made authoritative from the supplied observation."""


@dataclass(frozen=True)
class ChannelAdmission:
    player_id: int
    turn: int
    observation_id: str | None
    projection: ChannelProjection
    block: str
    context: ChannelTurnContext
    wake_reasons: tuple[str, ...]


def grievance_base_magnitude(payment_gold: int) -> float:
    """Return the schema-1 bounded grievance magnitude for a promised payment."""

    return min(10.0, max(0.25, payment_gold / 100.0))


class ChannelRuntime:
    """The sole writer for one run's channel journal and snapshot."""

    def __init__(
        self,
        channels_dir: Path,
        state: ChannelState,
        rules: ChannelRules,
    ) -> None:
        self.channels_dir = channels_dir
        self.events_path = channels_dir / "events.jsonl"
        self.state_path = channels_dir / "state.json"
        self.state = state
        self.rules = rules
        self._observation_ids: dict[
            int,
            tuple[ChannelObservation, str],
        ] = {}

    @classmethod
    def open(
        cls,
        run_dir: Path,
        run_id: str,
        enabled_players: frozenset[int],
        rules: ChannelRules,
    ) -> ChannelRuntime:
        channels_dir = Path(run_dir) / "channels"
        cls._ensure_private_directory(channels_dir)
        events_path = channels_dir / "events.jsonl"
        state_path = channels_dir / "state.json"
        cls._ensure_private_file(events_path)

        if state_path.exists() or state_path.is_symlink():
            cls._require_regular_file(state_path)
            try:
                snapshot = state_from_dict(
                    json.loads(state_path.read_text(encoding="utf-8"))
                )
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                raise ChannelStateError(f"invalid channel snapshot: {exc}") from exc
            os.chmod(state_path, 0o600)
            cls._validate_identity(snapshot, run_id, enabled_players, rules)
        else:
            if events_path.stat().st_size:
                raise ChannelStateError(
                    "channel identity snapshot is missing for a nonempty journal"
                )
            snapshot = initial_channel_state(run_id, enabled_players, rules)

        try:
            events = cls._read_journal(events_path)
            cls._validate_payment_journal(events)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise ChannelStateError(f"invalid channel journal: {exc}") from exc

        journal_sequence = events[-1].sequence if events else 0
        if snapshot.last_event_sequence > journal_sequence:
            raise ChannelStateError(
                "channel snapshot is newer than the write-ahead journal"
            )

        prefix = initial_channel_state(run_id, enabled_players, rules)
        try:
            for event in events:
                if event.sequence > snapshot.last_event_sequence:
                    break
                prefix = cls._reduce_persisted_event(prefix, event)
        except (KeyError, TypeError, ValueError) as exc:
            raise ChannelStateError(f"invalid channel journal: {exc}") from exc
        if snapshot != prefix:
            raise ChannelStateError(
                "channel snapshot does not match journal at snapshot sequence"
            )

        replayed = snapshot
        try:
            for event in events:
                if event.sequence > snapshot.last_event_sequence:
                    replayed = cls._reduce_persisted_event(replayed, event)
        except (KeyError, TypeError, ValueError) as exc:
            raise ChannelStateError(f"invalid channel journal: {exc}") from exc

        runtime = cls(channels_dir, replayed, rules)
        runtime._write_snapshot()
        return runtime

    @staticmethod
    def _validate_identity(
        state: ChannelState,
        run_id: str,
        enabled_players: frozenset[int],
        rules: ChannelRules,
    ) -> None:
        if state.run_id != run_id:
            raise ChannelStateError(
                f"channel run id mismatch: expected {run_id!r}, got {state.run_id!r}"
            )
        if state.enabled_players != frozenset(enabled_players):
            raise ChannelStateError("channel enabled-player set does not match snapshot")
        if state.rules_fingerprint != rules.fingerprint():
            raise ChannelStateError("channel rules fingerprint does not match snapshot")

    @staticmethod
    def _ensure_private_directory(path: Path) -> None:
        try:
            if path.exists() or path.is_symlink():
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise ChannelStateError(
                        f"channel directory is not a regular directory: {path}"
                    )
            else:
                path.mkdir(parents=True, mode=0o700)
            os.chmod(path, 0o700)
        except OSError as exc:
            raise ChannelStateError(
                f"cannot create private channel directory: {exc}"
            ) from exc

    @staticmethod
    def _require_regular_file(path: Path) -> None:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ChannelStateError(f"channel path is not a regular file: {path}")

    @classmethod
    def _ensure_private_file(cls, path: Path) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
            os.close(fd)
            cls._require_regular_file(path)
            os.chmod(path, 0o600)
        except OSError as exc:
            raise ChannelStateError(f"cannot create private channel file: {exc}") from exc

    @staticmethod
    def _read_journal(path: Path) -> tuple[ChannelEvent, ...]:
        data = path.read_bytes()
        records = data.splitlines(keepends=True)
        valid_length = len(data)
        truncated_final = False
        if records and not records[-1].endswith((b"\n", b"\r")):
            valid_length -= len(records.pop())
            truncated_final = True
        events: list[ChannelEvent] = []
        for record in records:
            if not record.strip():
                raise ValueError("empty channel journal record")
            payload = json.loads(record.decode("utf-8"))
            events.append(event_from_dict(payload))
        if truncated_final:
            flags = os.O_WRONLY
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
            try:
                os.ftruncate(fd, valid_length)
                os.fsync(fd)
            finally:
                os.close(fd)
        return tuple(events)

    @staticmethod
    def _validate_payment_journal(events: tuple[ChannelEvent, ...]) -> None:
        unfinished: dict[str, tuple[str, dict]] = {}
        unfinished_pairs: dict[tuple[int, int], str] = {}
        completed: set[str] = set()
        for event in events:
            if event.kind in {"payment_fund_intent", "payment_response_intent"}:
                payload = event.payload
                source_id = payload.get("source_id") if isinstance(payload, dict) else None
                if (
                    not isinstance(source_id, str)
                    or source_id in unfinished
                    or source_id in completed
                ):
                    raise ValueError("duplicate or invalid payment intent source")
                pair = (payload.get("payer"), payload.get("payee"))
                if pair in unfinished_pairs:
                    raise ValueError("multiple unfinished payment intents for pair")
                unfinished[source_id] = (event.kind, payload)
                unfinished_pairs[pair] = source_id
                continue
            if event.kind not in {"payment_fund_result", "payment_response_result"}:
                continue
            result_intent = event.payload.get("intent")
            source_id = (
                result_intent.get("source_id")
                if isinstance(result_intent, dict)
                else None
            )
            prior = unfinished.get(source_id) if isinstance(source_id, str) else None
            expected_kind = (
                "payment_fund_intent"
                if event.kind == "payment_fund_result"
                else "payment_response_intent"
            )
            if (
                prior is None
                or prior[0] != expected_kind
                or prior[1] != result_intent
            ):
                raise ValueError("payment result does not match an unfinished intent")
            del unfinished[source_id]
            pair = (result_intent.get("payer"), result_intent.get("payee"))
            if unfinished_pairs.get(pair) != source_id:
                raise ValueError("payment intent pair index is inconsistent")
            del unfinished_pairs[pair]
            completed.add(source_id)

    @classmethod
    def _reduce_persisted_event(
        cls,
        state: ChannelState,
        event: ChannelEvent,
    ) -> ChannelState:
        if event.kind in {"payment_fund_intent", "payment_response_intent"}:
            cls._validate_payment_intent(state, event.kind, event.payload)
            reduced = reduce_event(
                state,
                replace(
                    event,
                    kind="queue_advanced",
                    payload={
                        "cursor": state.queue_cursor,
                        "reservation": state.queue_reservation,
                    },
                ),
            )
        elif event.kind in {"payment_fund_result", "payment_response_result"}:
            reduced = cls._reduce_payment_result(state, event)
        elif event.kind == "deal_broken":
            cls._validate_deal_broken_event(state, event.payload)
            reduced = reduce_event(state, event)
        else:
            reduced = reduce_event(state, event)
        cls._validate_payment_lifecycle(state, reduced, event)
        return reduced

    @classmethod
    def _validate_payment_intent(
        cls,
        state: ChannelState,
        kind: str,
        payload: dict,
    ) -> None:
        common_fields = {
            "source_id",
            "deal_id",
            "actor",
            "turn",
            "payer",
            "payee",
            "gold",
            "deadline",
            "preflight_status",
            "preflight_player",
            "fingerprint",
        }
        required = (
            common_fields
            if kind == "payment_fund_intent"
            else common_fields | {"accept"}
        )
        allowed = required | (
            {"success_deal", "observation_id"}
            if kind == "payment_response_intent"
            else set()
        )
        if not isinstance(payload, dict) or not required <= set(payload) <= allowed:
            raise ValueError(f"invalid {kind} payload")
        source_id = payload["source_id"]
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("payment intent requires a non-empty source_id")
        if source_id in state.applied_source_ids:
            raise ValueError(f"duplicate source id {source_id!r}")
        deal_id = payload["deal_id"]
        deal = next(
            (candidate for candidate in state.deals if candidate.id == deal_id),
            None,
        )
        if deal is None:
            raise ValueError(f"unknown deal id {deal_id!r}")
        if deal.state is not DealState.ACTIVE:
            raise ValueError("payment intent requires an active deal")
        for field in ("actor", "turn", "payer", "payee", "gold"):
            if type(payload[field]) is not int:
                raise ValueError(f"payment intent {field} must be an integer")
        if payload["turn"] < 0 or (
            deal.accepted_turn is None or payload["turn"] < deal.accepted_turn
        ):
            raise ValueError("payment intent turn precedes the active deal phase")
        expected_deadline = (
            deal.fund_by_turn
            if kind == "payment_fund_intent"
            else deal.payment_response_by_turn
        )
        if (
            expected_deadline is None
            or payload["deadline"] != expected_deadline
            or type(payload["deadline"]) is not int
            or payload["turn"] > expected_deadline
        ):
            raise ValueError("payment intent deadline is invalid")
        expected_actor = (
            deal.proposer
            if kind == "payment_fund_intent"
            else deal.counterparty
        )
        if (
            payload["actor"] != expected_actor
            or payload["payer"] != deal.proposer
            or payload["payee"] != deal.counterparty
            or payload["gold"] != deal.payment_gold
        ):
            raise ValueError("payment intent does not match deal parties and terms")
        expected_status = (
            PaymentStatus.DUE
            if kind == "payment_fund_intent"
            else PaymentStatus.OFFERED
        )
        if deal.payment_status is not expected_status:
            raise ValueError("payment intent does not match the payment lifecycle")
        expected_preflight = (
            ("absent", deal.proposer)
            if kind == "payment_fund_intent"
            else ("exact", deal.counterparty)
        )
        if (
            not isinstance(payload["preflight_status"], str)
            or type(payload["preflight_player"]) is not int
            or payload["preflight_player"] < 0
            or not cls._exactly_equal(
                (
                    payload["preflight_status"],
                    payload["preflight_player"],
                ),
                expected_preflight,
            )
        ):
            raise ValueError("payment intent preflight is invalid")
        if any(
            candidate.id != deal.id
            and candidate.proposer == deal.proposer
            and candidate.counterparty == deal.counterparty
            and candidate.payment_status is PaymentStatus.OFFERED
            for candidate in state.deals
        ):
            raise ValueError("payment intent conflicts with an offered pair payment")
        fingerprint = payload["fingerprint"]
        if (
            not isinstance(fingerprint, dict)
            or set(fingerprint)
            != {"payer", "payee", "gold", "duration", "item_count"}
            or any(type(value) is not int for value in fingerprint.values())
            or fingerprint != cls._payment_fingerprint(deal)
        ):
            raise ValueError("payment intent fingerprint does not match the deal")
        if kind == "payment_response_intent":
            if type(payload["accept"]) is not bool:
                raise ValueError("payment response intent accept must be a boolean")
            if payload["accept"] != ("success_deal" in payload):
                raise ValueError(
                    "accepted payment response requires exactly one success deal"
                )
        success_deal = payload.get("success_deal")
        if success_deal is not None and not isinstance(success_deal, dict):
            raise ValueError("payment response success_deal must be an object")
        observation_id = payload.get("observation_id")
        if observation_id is not None and (
            not isinstance(observation_id, str) or not observation_id
        ):
            raise ValueError("payment response observation_id must be a string")
        if kind == "payment_response_intent" and payload["accept"]:
            cls._validated_success_deal(state, deal, payload)

    @classmethod
    def _validate_payment_lifecycle(
        cls,
        before: ChannelState,
        after: ChannelState,
        event: ChannelEvent,
    ) -> None:
        rules = after.rules_fingerprint
        before_deals = {deal.id: deal for deal in before.deals}
        for deal in after.deals:
            cls._validate_deal_exact_types(deal)
            prior = before_deals.get(deal.id)
            if prior is not None:
                settlement_started = (
                    prior.payment_status is not PaymentStatus.SETTLED
                    and deal.payment_status is PaymentStatus.SETTLED
                )
                honored_by_settlement = (
                    prior.state is not DealState.HONORED
                    and deal.state is DealState.HONORED
                    and prior.payment_status is not PaymentStatus.SETTLED
                )
                if (
                    settlement_started or honored_by_settlement
                ) and event.kind != "payment_response_result":
                    raise ValueError(
                        "only a payment response result may settle linked payment"
                    )
                newly_broken = (
                    prior.state is DealState.ACTIVE
                    and deal.state is DealState.BROKEN
                )
                if newly_broken and event.kind not in {
                    "deal_broken",
                    "payment_fund_result",
                    "payment_response_result",
                }:
                    raise ValueError(
                        "active payment breach requires an atomic canonical event"
                    )
                if (
                    event.kind == "staged_action_applied"
                    and prior.state is DealState.ACTIVE
                    and prior.payment_status is PaymentStatus.OFFERED
                    and deal.state is DealState.UNVERIFIABLE
                ):
                    cls._validate_incomplete_payment_aggregate(
                        before,
                        prior,
                        deal,
                        event.payload,
                    )
                if (
                    prior.fund_by_turn is not None
                    and deal.fund_by_turn != prior.fund_by_turn
                ):
                    raise ValueError("payment funding deadline is immutable")
                if (
                    prior.payment_response_by_turn is not None
                    and deal.payment_response_by_turn
                    != prior.payment_response_by_turn
                ):
                    raise ValueError("payment response deadline is immutable")
                if (
                    prior.payment_response_by_turn is None
                    and deal.payment_response_by_turn is not None
                    and event.kind != "payment_fund_result"
                ):
                    raise ValueError(
                        "only a funding result may start the payment response phase"
                    )
                if (
                    prior.favor_due_turn is not None
                    and deal.favor_due_turn != prior.favor_due_turn
                ):
                    raise ValueError("favor deadline is immutable once established")
            if deal.state is not DealState.ACTIVE:
                continue
            if deal.accepted_turn is None:
                raise ValueError("active payment lifecycle requires acceptance")
            if deal.timing == "up_front":
                expected_funding_deadline = (
                    deal.accepted_turn + int(rules["funding_turns"])
                )
                if deal.fund_by_turn != expected_funding_deadline:
                    raise ValueError("up-front funding deadline is not phase-bound")
                if deal.payment_status in {
                    PaymentStatus.DUE,
                    PaymentStatus.OFFERED,
                }:
                    if (
                        deal.favor_status is not FavorStatus.NOT_DUE
                        or deal.favor_due_turn is not None
                    ):
                        raise ValueError(
                            "up-front favor cannot start before payment settlement"
                        )
                elif deal.payment_status is PaymentStatus.SETTLED:
                    if (
                        deal.favor_status is not FavorStatus.DUE
                        or deal.favor_due_turn is None
                    ):
                        raise ValueError(
                            "settled up-front payment requires an active favor"
                        )
                else:
                    raise ValueError("invalid active up-front payment phase")
                if (
                    deal.payment_status is PaymentStatus.DUE
                    and deal.payment_response_by_turn is not None
                ):
                    raise ValueError("due payment cannot have a response deadline")
                if (
                    deal.payment_status
                    in {PaymentStatus.OFFERED, PaymentStatus.SETTLED}
                    and deal.payment_response_by_turn is None
                ):
                    raise ValueError("offered payment requires a response deadline")
                continue

            expected_favor_deadline = (
                deal.accepted_turn + deal.completion_window_turns
            )
            if deal.favor_status is FavorStatus.DUE:
                if (
                    deal.payment_status is not PaymentStatus.NOT_DUE
                    or deal.fund_by_turn is not None
                    or deal.payment_response_by_turn is not None
                    or deal.favor_due_turn != expected_favor_deadline
                ):
                    raise ValueError(
                        "on-delivery payment cannot start before favor satisfaction"
                    )
                continue
            if deal.favor_status is not FavorStatus.SATISFIED:
                raise ValueError("invalid active on-delivery favor phase")
            satisfaction_turn = cls._satisfaction_turn(after, deal)
            expected_funding_deadline = (
                satisfaction_turn + int(rules["funding_turns"])
            )
            if (
                deal.payment_status
                not in {PaymentStatus.DUE, PaymentStatus.OFFERED}
                or deal.fund_by_turn != expected_funding_deadline
            ):
                raise ValueError(
                    "on-delivery funding is not bound to favor satisfaction"
                )
            if (
                deal.payment_status is PaymentStatus.DUE
                and deal.payment_response_by_turn is not None
            ):
                raise ValueError("due payment cannot have a response deadline")
            if (
                deal.payment_status is PaymentStatus.OFFERED
                and deal.payment_response_by_turn is None
            ):
                raise ValueError("offered payment requires a response deadline")

    @classmethod
    def _validate_incomplete_payment_aggregate(
        cls,
        state: ChannelState,
        before: Deal,
        after: Deal,
        payload: dict,
    ) -> None:
        acknowledgement = payload.get("acknowledgement")
        effect = payload.get("effect")
        if not isinstance(acknowledgement, dict) or not isinstance(effect, dict):
            raise ValueError("incomplete payment aggregate is malformed")
        turn = acknowledgement.get("turn")
        source_id = acknowledgement.get("source_id")
        if type(turn) is not int or not isinstance(source_id, str) or not source_id:
            raise ValueError("incomplete payment acknowledgement is malformed")
        expected_acknowledgement = {
            "player_id": before.counterparty,
            "turn": turn,
            "source_id": source_id,
            "status": "applied",
            "message": f"payment acceptance became unverifiable for {before.id}",
            "deal_id": before.id,
        }
        if not cls._exactly_equal(acknowledgement, expected_acknowledgement):
            raise ValueError("incomplete payment acknowledgement is not canonical")
        terminal = after.terminal
        evidence_refs = terminal.get("evidence_refs") if isinstance(terminal, dict) else None
        if not isinstance(evidence_refs, list) or len(evidence_refs) > 1:
            raise ValueError("incomplete payment evidence is malformed")
        for observation_id in evidence_refs:
            observation = next(
                (
                    candidate
                    for candidate in state.observations
                    if candidate.get("id") == observation_id
                ),
                None,
            )
            if (
                not isinstance(observation, dict)
                or observation.get("player_id") != before.counterparty
                or observation.get("turn") != turn
            ):
                raise ValueError("incomplete payment evidence is not actor-turn bound")
        expected = cls._unverifiable_deal_record(
            before,
            turn=turn,
            reason="payment acceptance observation baseline was incomplete",
            evidence_refs=tuple(evidence_refs),
        )
        expected_effect = {"kind": "deal_changed", "payload": cls._deal_payload(expected)}
        if not cls._exactly_equal(effect, expected_effect):
            raise ValueError("incomplete payment deal transition is not canonical")

    @staticmethod
    def _validate_deal_exact_types(deal: Deal) -> None:
        if not isinstance(deal.id, str) or not deal.id:
            raise ValueError("deal id must be a non-empty string")
        for field in (
            "proposer",
            "counterparty",
            "created_turn",
            "accept_by_turn",
            "completion_window_turns",
            "payment_gold",
        ):
            if type(getattr(deal, field)) is not int:
                raise ValueError(f"deal {field} must be an exact integer")
        for field in (
            "accepted_turn",
            "fund_by_turn",
            "payment_response_by_turn",
            "favor_due_turn",
        ):
            value = getattr(deal, field)
            if value is not None and type(value) is not int:
                raise ValueError(f"deal {field} must be an exact integer or null")
        if deal.terminal is not None:
            if type(deal.terminal) is not dict:
                raise ValueError("deal terminal must be an object or null")
            for field in ("wronged", "offender", "turn"):
                value = deal.terminal.get(field)
                if value is not None and type(value) is not int:
                    raise ValueError(
                        f"deal terminal {field} must be an exact integer"
                    )

    @classmethod
    def _validate_deal_broken_event(
        cls,
        state: ChannelState,
        payload: dict,
    ) -> None:
        if not isinstance(payload, dict) or set(payload) != {"deal", "grievance"}:
            raise ValueError("deal_broken payload requires deal and grievance")
        deal_payload = payload["deal"]
        grievance = payload["grievance"]
        if not isinstance(deal_payload, dict) or not isinstance(grievance, dict):
            raise ValueError("deal_broken aggregate requires object payloads")
        terminal = deal_payload.get("terminal")
        if type(terminal) is not dict:
            raise ValueError("broken deal terminal must be an object")
        deal_id = deal_payload.get("id")
        prior = next(
            (
                candidate
                for candidate in state.deals
                if candidate.id == deal_id
            ),
            None,
        )
        if prior is None or prior.state is not DealState.ACTIVE:
            raise ValueError("deal_broken requires an active deal")
        turn = grievance.get("turn")
        if type(turn) is not int or turn < 0:
            raise ValueError("deal_broken grievance turn must be an exact integer")

        if prior.payment_status is PaymentStatus.DUE:
            if prior.fund_by_turn is None or turn < prior.fund_by_turn:
                raise ValueError("funding breach precedes its deadline")
            expected_deal, expected_grievance = cls._canonical_breach_records(
                state,
                prior,
                turn=turn,
                breach="funding",
                reason="promised payment was not funded by the deadline",
            )
        elif prior.payment_status is PaymentStatus.OFFERED:
            if (
                prior.payment_response_by_turn is None
                or turn < prior.payment_response_by_turn
            ):
                raise ValueError("payment response breach precedes its deadline")
            expected_deal, expected_grievance = cls._canonical_breach_records(
                state,
                prior,
                turn=turn,
                breach="payment_response",
                reason=(
                    "exact linked payment was not accepted by the deadline"
                ),
            )
        elif (
            prior.favor_status is FavorStatus.DUE
            and prior.payment_status
            in {PaymentStatus.NOT_DUE, PaymentStatus.SETTLED}
        ):
            expected_deal, expected_grievance = (
                cls._expected_favor_breach_records(
                    state,
                    prior,
                    turn=turn,
                )
            )
        else:
            raise ValueError("deal_broken does not match an active obligation")

        if not cls._exactly_equal(
            deal_payload,
            cls._deal_payload(expected_deal),
        ) or not cls._exactly_equal(grievance, expected_grievance):
            raise ValueError("deal_broken aggregate is not canonical")

    @classmethod
    def _expected_favor_breach_records(
        cls,
        state: ChannelState,
        deal: Deal,
        *,
        turn: int,
    ) -> tuple[Deal, dict]:
        if deal.favor_due_turn is None:
            raise ValueError("favor breach requires an established deadline")
        observation_payload = next(
            (
                candidate
                for candidate in reversed(state.observations)
                if isinstance(candidate, dict)
                and type(candidate.get("player_id")) is int
                and candidate.get("player_id") == deal.counterparty
                and type(candidate.get("turn")) is int
                and candidate.get("turn") == turn
            ),
            None,
        )
        if observation_payload is None:
            raise ValueError("favor breach requires an actor-turn observation")
        observation_id = observation_payload.get("id")
        if not isinstance(observation_id, str) or not observation_id:
            raise ValueError("favor breach observation id is invalid")
        observation = cls._observation_from_payload(observation_payload)
        monitor = dict(deal.favor.monitor)
        monitor["current_observation_id"] = observation_id
        verification = verify_term(
            {
                "term_type": deal.favor.term_type,
                "params": deal.favor.params,
            },
            deal.favor.baseline,
            monitor,
            observation,
            deal.favor_due_turn,
        )
        if verification.status != "failed":
            raise ValueError("favor observation does not prove a breach")
        persisted_monitor = {
            key: value
            for key, value in verification.monitor.items()
            if key not in {"current_observation_id", "observation_id"}
        }
        updated = replace(
            deal,
            favor=replace(deal.favor, monitor=persisted_monitor),
        )
        evidence_refs = tuple(verification.evidence_refs) or (observation_id,)
        return cls._canonical_breach_records(
            state,
            updated,
            turn=turn,
            breach="favor",
            reason=verification.reason,
            evidence_refs=evidence_refs,
        )

    @staticmethod
    def _satisfaction_turn(state: ChannelState, deal: Deal) -> int:
        observation_id = deal.favor.monitor.get("satisfaction_observation_id")
        observation = next(
            (
                candidate
                for candidate in state.observations
                if candidate.get("id") == observation_id
            ),
            None,
        )
        turn = observation.get("turn") if isinstance(observation, dict) else None
        if type(turn) is not int:
            raise ValueError("favor satisfaction has no exact observation turn")
        return turn

    @classmethod
    def _exactly_equal(cls, left: Any, right: Any) -> bool:
        if type(left) is not type(right):
            return False
        if isinstance(left, dict):
            return set(left) == set(right) and all(
                cls._exactly_equal(left[key], right[key]) for key in left
            )
        if isinstance(left, (list, tuple)):
            return len(left) == len(right) and all(
                cls._exactly_equal(left_item, right_item)
                for left_item, right_item in zip(left, right, strict=True)
            )
        return left == right

    @classmethod
    def _reduce_payment_result(
        cls,
        state: ChannelState,
        event: ChannelEvent,
    ) -> ChannelState:
        payload = event.payload
        required = {
            "intent",
            "fingerprint",
            "engine_result",
            "recovery",
            "acknowledgement",
            "deal",
            "grievance",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError(f"invalid {event.kind} payload")
        intent_kind = (
            "payment_fund_intent"
            if event.kind == "payment_fund_result"
            else "payment_response_intent"
        )
        intent = payload["intent"]
        cls._validate_payment_intent(state, intent_kind, intent)
        result_fingerprint = payload["fingerprint"]
        if (
            not isinstance(result_fingerprint, dict)
            or set(result_fingerprint)
            != {"payer", "payee", "gold", "duration", "item_count"}
            or any(type(value) is not int for value in result_fingerprint.values())
            or result_fingerprint != intent["fingerprint"]
        ):
            raise ValueError("payment result fingerprint must match its intent")
        if (
            not isinstance(payload["engine_result"], str)
            or not payload["engine_result"]
        ):
            raise ValueError("payment result requires a non-empty engine result")
        if payload["recovery"] is not None and not isinstance(
            payload["recovery"], str
        ):
            raise ValueError("payment recovery marker must be a string or null")
        cls._validate_payment_result_semantics(
            state,
            event.kind,
            intent,
            payload,
        )
        source_id = intent["source_id"]
        acknowledgement = payload["acknowledgement"]
        deal_payload = payload["deal"]
        effect = None
        if deal_payload is not None:
            if not isinstance(deal_payload, dict):
                raise ValueError("payment result deal must be an object or null")
            effect = {"kind": "deal_changed", "payload": deal_payload}
        staged_event = replace(
            event,
            kind="staged_action_applied",
            payload={
                "source_id": source_id,
                "acknowledgement": acknowledgement,
                "effect": effect,
            },
        )
        applied = reduce_event(state, staged_event)
        grievance = payload["grievance"]
        if grievance is None:
            return applied
        if deal_payload is None or not isinstance(grievance, dict):
            raise ValueError("payment grievance requires a changed deal")
        broken = reduce_event(
            state,
            replace(
                event,
                kind="deal_broken",
                payload={"deal": deal_payload, "grievance": grievance},
            ),
        )
        return replace(
            applied,
            grievances=broken.grievances,
            next_grievance=broken.next_grievance,
        )

    @classmethod
    def _validate_payment_result_semantics(
        cls,
        state: ChannelState,
        kind: str,
        intent: dict,
        payload: dict,
    ) -> None:
        acknowledgement = payload["acknowledgement"]
        if not isinstance(acknowledgement, dict) or set(acknowledgement) != {
            "player_id",
            "turn",
            "source_id",
            "status",
            "message",
            "deal_id",
        }:
            raise ValueError("payment result acknowledgement is invalid")
        deal_payload = payload["deal"]
        expected_status = "applied" if deal_payload is not None else "rejected"
        expected_deal_id = intent["deal_id"] if deal_payload is not None else None
        if (
            type(acknowledgement["player_id"]) is not int
            or type(acknowledgement["turn"]) is not int
            or type(acknowledgement["source_id"]) is not str
            or type(acknowledgement["status"]) is not str
            or type(acknowledgement["deal_id"]) is not type(expected_deal_id)
            or acknowledgement["player_id"] != intent["actor"]
            or acknowledgement["turn"] != intent["turn"]
            or acknowledgement["source_id"] != intent["source_id"]
            or acknowledgement["status"] != expected_status
            or acknowledgement["deal_id"] != expected_deal_id
            or not isinstance(acknowledgement["message"], str)
            or not acknowledgement["message"]
        ):
            raise ValueError("payment acknowledgement does not match its intent")

        deal = next(
            candidate
            for candidate in state.deals
            if candidate.id == intent["deal_id"]
        )
        engine_result = payload["engine_result"]
        recovery = payload["recovery"]
        expected_deal: Deal | None
        expected_grievance: dict | None = None

        if kind == "payment_fund_result":
            offered = replace(
                deal,
                payment_status=PaymentStatus.OFFERED,
                payment_response_by_turn=(
                    intent["turn"]
                    + int(state.rules_fingerprint["payment_response_turns"])
                ),
            )
            if recovery is None:
                if cls._funding_succeeded(engine_result):
                    expected_deal = offered
                elif cls._authoritative_payment_failure(engine_result):
                    expected_deal = None
                else:
                    raise ValueError("non-authoritative funding result was completed")
            elif recovery == "observed_exact_offer":
                if engine_result != "RECOVERED_EXACT_CHANNEL_PAYMENT":
                    raise ValueError("invalid exact-offer funding recovery")
                expected_deal = offered
            elif recovery == "conflicting_offer":
                if engine_result != "RECOVERY_CONFLICTING_PAYMENT":
                    raise ValueError("invalid conflicting funding recovery")
                expected_deal = None
            elif recovery == "conflicting_offer_after_deadline":
                if engine_result != "RECOVERY_CONFLICTING_PAYMENT_LATE":
                    raise ValueError("invalid late-conflict funding recovery")
                grievance = payload["grievance"]
                if not isinstance(grievance, dict):
                    raise ValueError("late funding conflict requires a grievance")
                recovery_turn = grievance.get("turn")
                if (
                    type(recovery_turn) is not int
                    or recovery_turn <= intent["deadline"]
                ):
                    raise ValueError("late funding conflict turn is invalid")
                expected_deal, expected_grievance = cls._expected_breach_records(
                    state,
                    deal,
                    turn=recovery_turn,
                    breach="funding",
                    reason="linked payment was not funded by the deadline",
                )
            elif recovery == "offer_absent":
                if engine_result != "RECOVERY_PAYMENT_ABSENT":
                    raise ValueError("invalid absent-offer funding recovery")
                expected_deal = cls._expected_unverifiable_result(
                    deal,
                    deal_payload,
                    intent,
                    "funding intent outcome is ambiguous because the exact offer is absent",
                )
            else:
                raise ValueError("unknown funding recovery outcome")
        else:
            accept = intent["accept"]
            if recovery in {None, "response_retried"}:
                if cls._response_succeeded(engine_result, accept):
                    if accept:
                        expected_deal = cls._validated_success_deal(
                            state, deal, intent
                        )
                    else:
                        expected_deal, expected_grievance = (
                            cls._expected_breach_records(
                                state,
                                deal,
                                turn=intent["turn"],
                                breach="payment_response",
                                reason="exact linked payment was rejected",
                            )
                        )
                elif cls._authoritative_payment_failure(engine_result):
                    expected_deal = None
                else:
                    raise ValueError("non-authoritative payment response was completed")
            elif recovery in {"offer_absent", "conflicting_offer"}:
                if engine_result != "RECOVERY_PAYMENT_ABSENT":
                    raise ValueError("invalid payment-response preflight recovery")
                status = "absent" if recovery == "offer_absent" else "conflicting"
                expected_deal = cls._expected_unverifiable_result(
                    deal,
                    deal_payload,
                    intent,
                    "payment response intent outcome is ambiguous because "
                    f"the exact offer is {status}",
                )
            elif recovery == "response_retry_query_failed":
                if not cls._authoritative_payment_failure(engine_result):
                    raise ValueError("invalid response query-failure recovery")
                expected_deal = cls._expected_unverifiable_result(
                    deal,
                    deal_payload,
                    intent,
                    "payment response recovery could not verify the engine outcome after retry",
                )
            elif recovery == "response_retry_ambiguous":
                if not cls._authoritative_payment_failure(engine_result):
                    raise ValueError("invalid ambiguous response recovery")
                reasons = {
                    "payment response recovery remained ambiguous after an engine exception",
                    "payment response recovery returned no authoritative engine result",
                }
                expected_deal = cls._expected_unverifiable_result(
                    deal,
                    deal_payload,
                    intent,
                    reasons,
                )
            elif recovery in {
                "response_retry_exact_ambiguous",
                "response_retry_absent_ambiguous",
                "response_retry_conflicting_ambiguous",
            }:
                if engine_result != "RECOVERY_AMBIGUOUS_RESPONSE":
                    raise ValueError("invalid canonical ambiguous response recovery")
                status = recovery.removeprefix("response_retry_").removesuffix(
                    "_ambiguous"
                )
                expected_deal = cls._expected_unverifiable_result(
                    deal,
                    deal_payload,
                    intent,
                    "payment response retry returned no authoritative result; "
                    f"the post-retry offer was {status}",
                )
            elif recovery in {
                "response_retry_post_query_failed",
                "response_retry_post_state_invalid",
            }:
                if engine_result != "RECOVERY_AMBIGUOUS_RESPONSE":
                    raise ValueError("invalid post-retry query recovery")
                reasons = {
                    "response_retry_post_query_failed": (
                        "payment response retry returned no authoritative result "
                        "and the post-retry offer query failed"
                    ),
                    "response_retry_post_state_invalid": (
                        "payment response retry returned no authoritative result "
                        "and the post-retry offer state was invalid"
                    ),
                }
                expected_deal = cls._expected_unverifiable_result(
                    deal,
                    deal_payload,
                    intent,
                    reasons[recovery],
                )
            else:
                raise ValueError("unknown payment-response recovery outcome")

        expected_payload = (
            cls._deal_payload(expected_deal) if expected_deal is not None else None
        )
        if not cls._exactly_equal(
            deal_payload, expected_payload
        ) or not cls._exactly_equal(payload["grievance"], expected_grievance):
            raise ValueError("payment result effects do not match the engine outcome")

    @classmethod
    def _validated_success_deal(
        cls,
        state: ChannelState,
        deal: Deal,
        intent: dict,
    ) -> Deal:
        payload = intent.get("success_deal")
        if not isinstance(payload, dict):
            raise ValueError("accepted response has no canonical success deal")
        changed = cls._deal_from_payload(state, payload)
        settled = replace(deal, payment_status=PaymentStatus.SETTLED)
        if deal.timing == "on_delivery":
            expected = cls._honor_settled_deal(settled)
            if not cls._exactly_equal(payload, cls._deal_payload(expected)):
                raise ValueError("payment success deal is not the canonical settlement")
            return expected

        spec = TERM_REGISTRY[deal.favor.term_type]
        if spec.baseline_phase == "proposal":
            expected = replace(
                settled,
                favor=FavorTerm(
                    deal.favor.term_type,
                    deal.favor.params,
                    deal.favor.baseline,
                    {},
                ),
                favor_status=FavorStatus.DUE,
                favor_due_turn=intent["turn"] + deal.completion_window_turns,
            )
            if not cls._exactly_equal(payload, cls._deal_payload(expected)):
                raise ValueError("proposal baseline was not preserved at payment acceptance")
            return expected

        observation_id = intent.get("observation_id")
        observation_payload = next(
            (
                candidate
                for candidate in state.observations
                if candidate.get("id") == observation_id
            ),
            None,
        )
        if not isinstance(observation_payload, dict):
            raise ValueError("favor-start payment acceptance has no observation")
        observation = cls._observation_from_payload(observation_payload)
        if (
            observation.player_id != intent["actor"]
            or observation.turn != intent["turn"]
        ):
            raise ValueError("payment acceptance observation is not actor-turn bound")
        baseline = capture_baseline(
            {
                "term_type": deal.favor.term_type,
                "params": deal.favor.params,
            },
            observation,
        )
        baseline = cls._attach_baseline_observation(baseline, observation_id)
        if any(
            key.endswith("baseline_complete") and value is not True
            for key, value in baseline.items()
        ):
            raise ValueError("favor-start payment baseline is incomplete")
        expected = replace(
            settled,
            favor=FavorTerm(
                deal.favor.term_type,
                deal.favor.params,
                baseline,
                {},
            ),
            favor_status=FavorStatus.DUE,
            favor_due_turn=intent["turn"] + deal.completion_window_turns,
        )
        if (
            changed.payment_status is not PaymentStatus.SETTLED
            or changed.favor_status is not FavorStatus.DUE
            or changed.favor_due_turn
            != intent["turn"] + deal.completion_window_turns
            or changed.favor.term_type != deal.favor.term_type
            or changed.favor.params != deal.favor.params
            or changed.favor.monitor
            or not cls._exactly_equal(payload, cls._deal_payload(expected))
        ):
            raise ValueError("favor-start payment success deal is not canonical")
        return expected

    @staticmethod
    def _observation_from_payload(payload: dict) -> ChannelObservation:
        def pairs(name: str) -> frozenset[tuple[int, int]]:
            return frozenset(tuple(item) for item in payload[name])

        return ChannelObservation(
            player_id=payload["player_id"],
            turn=payload["turn"],
            families_present=frozenset(
                ObservationFamily(value) for value in payload["families_present"]
            ),
            units=tuple(ObservedUnit(**item) for item in payload["units"]),
            cities=tuple(ObservedCity(**item) for item in payload["cities"]),
            camps=pairs("camps"),
            territory=pairs("territory"),
            wars=pairs("wars"),
            treasury_gold=payload["treasury_gold"],
            trade_routes=tuple(
                ObservedRoute(**item) for item in payload["trade_routes"]
            ),
            action_audit=tuple(
                ObservedAction(**item) for item in payload["action_audit"]
            ),
            unit_distances={
                tuple(item[:3]): item[3] for item in payload["unit_distances"]
            },
            zone_distances={
                tuple(item[:3]): item[3] for item in payload["zone_distances"]
            },
            errors=tuple(payload["errors"]),
        )

    @classmethod
    def _deal_from_payload(cls, state: ChannelState, payload: dict) -> Deal:
        event = ChannelEvent(
            SCHEMA_VERSION,
            f"evt-{state.next_event:06d}",
            state.next_event,
            "deal_changed",
            payload,
        )
        changed = reduce_event(state, event)
        result = next(
            (deal for deal in changed.deals if deal.id == payload.get("id")),
            None,
        )
        if result is None:
            raise ValueError("payment success deal does not identify an existing deal")
        return result

    @staticmethod
    def _honor_settled_deal(deal: Deal) -> Deal:
        if deal.favor_status is not FavorStatus.SATISFIED:
            return deal
        evidence = deal.favor.monitor.get("satisfaction_observation_id")
        return replace(
            deal,
            state=DealState.HONORED,
            terminal={
                "reason": "favor and payment completed",
                "evidence_refs": [evidence] if isinstance(evidence, str) else [],
                "adjudication_source": "deterministic",
            },
        )

    @classmethod
    def _expected_breach_records(
        cls,
        state: ChannelState,
        deal: Deal,
        *,
        turn: int,
        breach: str,
        reason: str,
    ) -> tuple[Deal, dict]:
        if breach not in {"funding", "payment_response"}:
            raise ValueError("invalid payment breach type")
        return cls._canonical_breach_records(
            state,
            deal,
            turn=turn,
            breach=breach,
            reason=reason,
        )

    @classmethod
    def _canonical_breach_records(
        cls,
        state: ChannelState,
        deal: Deal,
        *,
        turn: int,
        breach: str,
        reason: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[Deal, dict]:
        if breach == "favor":
            wronged, offender = deal.proposer, deal.counterparty
            favor_status = FavorStatus.FAILED
            payment_status = (
                PaymentStatus.WAIVED
                if deal.payment_status is PaymentStatus.NOT_DUE
                else deal.payment_status
            )
        elif breach == "funding":
            wronged, offender = deal.counterparty, deal.proposer
            favor_status = (
                FavorStatus.RELEASED
                if deal.favor_status is FavorStatus.NOT_DUE
                else deal.favor_status
            )
            payment_status = PaymentStatus.FAILED
        elif breach == "payment_response":
            wronged, offender = deal.proposer, deal.counterparty
            favor_status = (
                FavorStatus.RELEASED
                if deal.favor_status is FavorStatus.NOT_DUE
                else deal.favor_status
            )
            payment_status = PaymentStatus.FAILED
        else:
            raise ValueError(f"unknown deterministic breach {breach!r}")
        broken = replace(
            deal,
            state=DealState.BROKEN,
            favor_status=favor_status,
            payment_status=payment_status,
            terminal={
                "wronged": wronged,
                "offender": offender,
                "reason": reason,
                "evidence_refs": list(evidence_refs),
                "adjudication_source": "deterministic",
            },
        )
        grievance = {
            "id": f"grv-{state.next_grievance:06d}",
            "wronged": wronged,
            "offender": offender,
            "deal_id": deal.id,
            "turn": turn,
            "reason": reason,
            "payment_gold": deal.payment_gold,
            "base_magnitude": grievance_base_magnitude(deal.payment_gold),
            "half_life_turns": int(
                state.rules_fingerprint["grievance_half_life_turns"]
            ),
            "adjudication_source": "deterministic",
            "adjudication_metadata": None,
        }
        return broken, grievance

    @classmethod
    def _expected_unverifiable_result(
        cls,
        deal: Deal,
        payload: object,
        intent: dict,
        reason: str | set[str],
    ) -> Deal:
        if not isinstance(payload, dict):
            raise ValueError("unverifiable payment result requires a deal")
        terminal = payload.get("terminal")
        if not isinstance(terminal, dict):
            raise ValueError("unverifiable payment result requires terminal evidence")
        turn = terminal.get("turn")
        actual_reason = terminal.get("reason")
        allowed_reasons = {reason} if isinstance(reason, str) else reason
        if (
            type(turn) is not int
            or turn < intent["turn"]
            or actual_reason not in allowed_reasons
        ):
            raise ValueError("unverifiable payment result is not canonical")
        return cls._unverifiable_deal_record(
            deal,
            turn=turn,
            reason=actual_reason,
        )

    def _commit(self, kind: str, payload: dict) -> ChannelEvent:
        sequence = self.state.last_event_sequence + 1
        event = ChannelEvent(
            SCHEMA_VERSION,
            f"evt-{sequence:06d}",
            sequence,
            kind,
            payload,
        )
        reduced = self._reduce_persisted_event(self.state, event)
        encoded = (
            json.dumps(
                event_to_dict(event),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        flags = os.O_WRONLY | os.O_APPEND
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.events_path, flags)
        with os.fdopen(fd, "ab", buffering=0) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        self.state = reduced
        self._write_snapshot()
        return event

    def _write_snapshot(self) -> None:
        encoded = json.dumps(
            state_to_dict(self.state),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        temporary = self.channels_dir / ".state.json.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temporary, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", buffering=0) as stream:
                fd = -1
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
            directory_fd = os.open(
                self.channels_dir,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    async def apply_staged(
        self,
        gs: Any,
        staged: StagedChannelAction,
        *,
        turn: int,
        observation: ChannelObservation | None,
    ) -> ChannelAcknowledgement:
        if staged.source_id in self.state.applied_source_ids:
            existing = next(
                (
                    acknowledgement
                    for acknowledgement in self.state.acknowledgements
                    if acknowledgement.source_id == staged.source_id
                ),
                None,
            )
            return existing or ChannelAcknowledgement(
                staged.actor,
                turn,
                staged.source_id,
                "duplicate",
                "channel action already applied",
            )
        if any(
            intent["source_id"] == staged.source_id
            for _, intent in self._unfinished_payment_intents()
        ):
            return ChannelAcknowledgement(
                staged.actor,
                turn,
                staged.source_id,
                "duplicate",
                "payment action is awaiting intent reconciliation",
            )
        if staged.actor not in self.state.enabled_players:
            raise ValueError(f"actor {staged.actor} is not channel-enabled")
        observation_id = (
            self._ensure_observation_recorded(observation)
            if observation is not None
            else None
        )
        if isinstance(staged.action, FundDeal):
            try:
                return await self._fund_deal(gs, staged, turn=turn)
            except _ActionRejected as exc:
                return self._finish_source(
                    staged,
                    turn=turn,
                    status="rejected",
                    message=str(exc),
                    effect=None,
                )
        if isinstance(staged.action, RespondToPayment):
            try:
                return await self._respond_to_payment(
                    gs,
                    staged,
                    turn=turn,
                    observation=observation,
                    observation_id=observation_id,
                )
            except _ActionRejected as exc:
                return self._finish_source(
                    staged,
                    turn=turn,
                    status="rejected",
                    message=str(exc),
                    effect=None,
                )
        try:
            effect, message, deal_id = self._apply_pure_action(
                staged,
                turn=turn,
                observation=observation,
                observation_id=observation_id,
            )
        except _ActionRejected as exc:
            return self._finish_source(
                staged,
                turn=turn,
                status="rejected",
                message=str(exc),
                effect=None,
            )
        return self._finish_source(
            staged,
            turn=turn,
            status="applied",
            message=message,
            deal_id=deal_id,
            effect=effect,
        )

    def _apply_pure_action(
        self,
        staged: StagedChannelAction,
        *,
        turn: int,
        observation: ChannelObservation | None,
        observation_id: str | None,
    ) -> tuple[dict, str, str | None]:
        action = staged.action
        if isinstance(action, SendMessage):
            self._require_message_capacity(staged.actor, action.to_player)
            message_id = f"msg-{self.state.next_message:06d}"
            return (
                {
                    "kind": "message_sent",
                    "payload": {
                        "id": message_id,
                        "from_player": staged.actor,
                        "to_player": action.to_player,
                        "turn": turn,
                        "text": action.text,
                        "deal_id": None,
                    },
                },
                f"sent private message {message_id} to player {action.to_player}",
                None,
            )

        if isinstance(action, ProposeDeal):
            self._require_message_capacity(staged.actor, action.to_player)
            active_count = sum(
                deal.proposer == staged.actor
                and deal.counterparty == action.to_player
                and deal.state in (DealState.PROPOSED, DealState.ACTIVE)
                for deal in self.state.deals
            )
            if active_count >= self.rules.max_active_deals_per_pair:
                raise _ActionRejected(
                    "active deal limit reached for ordered pair "
                    f"{staged.actor}->{action.to_player}"
                )
            try:
                favor = self._validate_favor(action.favor, action.to_player)
            except (KeyError, TypeError, ValueError) as exc:
                raise _ActionRejected(str(exc)) from exc
            term_type = favor.get("term_type")
            if not isinstance(term_type, str):
                raise _ActionRejected("favor term_type must be a string")
            spec = TERM_REGISTRY.get(term_type)
            if spec is None:
                raise _ActionRejected("unknown deterministic favor term")
            baseline: dict = {}
            if spec.baseline_phase == "proposal":
                if observation is None:
                    raise _ActionRejected(
                        "proposal requires a post-policy observation"
                    )
                try:
                    baseline = capture_baseline(favor, observation)
                except (KeyError, TypeError, ValueError) as exc:
                    raise _ActionRejected(str(exc)) from exc
                baseline = self._attach_baseline_observation(
                    baseline,
                    observation_id,
                )
            deal_id = f"deal-{self.state.next_deal:06d}"
            message_id = f"msg-{self.state.next_message:06d}"
            deal_payload = {
                "id": deal_id,
                "proposer": staged.actor,
                "counterparty": action.to_player,
                "created_turn": turn,
                "accepted_turn": None,
                "accept_by_turn": turn + self.rules.acceptance_turns,
                "completion_window_turns": action.within,
                "favor": {
                    "term_type": favor["term_type"],
                    "params": favor["params"],
                    "baseline": baseline,
                    "monitor": {},
                },
                "payment_gold": action.payment_gold,
                "timing": action.timing,
                "state": DealState.PROPOSED.value,
                "favor_status": FavorStatus.NOT_DUE.value,
                "payment_status": PaymentStatus.NOT_DUE.value,
                "fund_by_turn": None,
                "payment_response_by_turn": None,
                "favor_due_turn": None,
                "terminal": None,
            }
            return (
                {
                    "kind": "deal_proposed",
                    "payload": {
                        "deal": deal_payload,
                        "message": {
                            "id": message_id,
                            "from_player": staged.actor,
                            "to_player": action.to_player,
                            "turn": turn,
                            "text": action.text,
                            "deal_id": deal_id,
                        },
                    },
                },
                f"proposed unofficial deal {deal_id}",
                deal_id,
            )

        if isinstance(action, RespondToDeal):
            try:
                deal = self.deal(action.deal_id)
            except ValueError as exc:
                raise _ActionRejected(str(exc)) from exc
            if staged.actor != deal.counterparty:
                raise _ActionRejected("only the counterparty may respond to a deal")
            if deal.state is not DealState.PROPOSED:
                raise _ActionRejected("deal is no longer awaiting a response")
            if turn > deal.accept_by_turn:
                raise _ActionRejected("deal acceptance deadline has passed")
            if not action.accept:
                declined = replace(
                    deal,
                    state=DealState.DECLINED,
                    favor_status=FavorStatus.RELEASED,
                    payment_status=PaymentStatus.WAIVED,
                    terminal={
                        "reason": "proposal declined",
                        "decisive_event_refs": [],
                        "adjudication_source": "deterministic",
                    },
                )
                return (
                    {
                        "kind": "deal_changed",
                        "payload": self._deal_payload(declined),
                    },
                    f"declined unofficial deal {deal.id}",
                    deal.id,
                )

            accepted = replace(deal, accepted_turn=turn, state=DealState.ACTIVE)
            if deal.timing == "up_front":
                accepted = replace(
                    accepted,
                    payment_status=PaymentStatus.DUE,
                    fund_by_turn=turn + self.rules.funding_turns,
                )
            else:
                if observation is None:
                    observation = ChannelObservation(staged.actor, turn)
                accepted = self._start_favor(
                    accepted,
                    observation,
                    observation_id=observation_id,
                    turn=turn,
                )
            return (
                {
                    "kind": "deal_changed",
                    "payload": self._deal_payload(accepted),
                },
                f"accepted unofficial deal {deal.id}",
                deal.id,
            )

        raise _ActionRejected("unknown staged channel action")

    async def _fund_deal(
        self,
        gs: Any,
        staged: StagedChannelAction,
        *,
        turn: int,
    ) -> ChannelAcknowledgement:
        action = staged.action
        if not isinstance(action, FundDeal):
            raise TypeError("funding requires a FundDeal action")
        try:
            deal = self.deal(action.deal_id)
        except ValueError as exc:
            raise _ActionRejected(str(exc)) from exc
        if staged.actor != deal.proposer:
            raise _ActionRejected("only the proposer may fund a deal")
        if deal.state is not DealState.ACTIVE:
            raise _ActionRejected("deal is not active")
        if deal.payment_status is not PaymentStatus.DUE:
            raise _ActionRejected("deal payment is not due for funding")
        if deal.fund_by_turn is None or turn > deal.fund_by_turn:
            raise _ActionRejected("deal funding deadline has passed")
        if any(
            candidate.id != deal.id
            and candidate.proposer == deal.proposer
            and candidate.counterparty == deal.counterparty
            and candidate.payment_status is PaymentStatus.OFFERED
            for candidate in self.state.deals
        ):
            raise _ActionRejected(
                "an unresolved channel payment already exists for this ordered pair"
            )
        unfinished = self._unfinished_payment_intents()
        if any(
            intent["deal_id"] == deal.id
            or (
                intent["payer"] == deal.proposer
                and intent["payee"] == deal.counterparty
            )
            for _, intent in unfinished
        ):
            raise _ActionRejected(
                "an unresolved payment intent requires reconciliation"
            )
        try:
            payment_state = await gs.get_channel_payment_state(
                deal.proposer,
                deal.counterparty,
                deal.payment_gold,
            )
        except Exception as exc:
            raise _ActionRejected(
                "could not verify the ordered-pair payment state: "
                f"{type(exc).__name__}"
            ) from exc
        if self._payment_state_status(payment_state, deal) != "absent":
            raise _ActionRejected(
                "the ordered pair already has a pending or conflicting deal"
            )
        intent = self._payment_intent_payload(
            deal,
            staged,
            turn=turn,
            deadline=deal.fund_by_turn,
        )
        self._commit("payment_fund_intent", intent)
        try:
            engine_result = await gs.offer_channel_payment(
                deal.counterparty,
                deal.payment_gold,
            )
        except Exception as exc:
            return ChannelAcknowledgement(
                staged.actor,
                turn,
                staged.source_id,
                "rejected",
                "payment funding outcome requires recovery after "
                f"{type(exc).__name__}",
            )
        if not isinstance(engine_result, str) or not engine_result:
            return ChannelAcknowledgement(
                staged.actor,
                turn,
                staged.source_id,
                "rejected",
                "payment funding returned no authoritative engine result",
            )
        if self._funding_succeeded(engine_result):
            offered = replace(
                deal,
                payment_status=PaymentStatus.OFFERED,
                payment_response_by_turn=(
                    turn + self.rules.payment_response_turns
                ),
            )
            return self._commit_payment_result(
                "payment_fund_result",
                intent,
                engine_result=engine_result,
                recovery=None,
                deal=offered,
                grievance=None,
                message=f"funded unofficial deal {deal.id}",
            )
        if not self._authoritative_payment_failure(engine_result):
            return ChannelAcknowledgement(
                staged.actor,
                turn,
                staged.source_id,
                "rejected",
                "payment funding outcome requires intent reconciliation",
            )
        return self._commit_payment_result(
            "payment_fund_result",
            intent,
            engine_result=engine_result,
            recovery=None,
            deal=None,
            grievance=None,
            message=f"linked payment funding failed: {engine_result}",
        )

    async def _respond_to_payment(
        self,
        gs: Any,
        staged: StagedChannelAction,
        *,
        turn: int,
        observation: ChannelObservation | None,
        observation_id: str | None,
    ) -> ChannelAcknowledgement:
        action = staged.action
        if not isinstance(action, RespondToPayment):
            raise TypeError("payment response requires RespondToPayment")
        try:
            deal = self.deal(action.deal_id)
        except ValueError as exc:
            raise _ActionRejected(str(exc)) from exc
        if staged.actor != deal.counterparty:
            raise _ActionRejected(
                "only the counterparty may respond to a linked payment"
            )
        if deal.state is not DealState.ACTIVE:
            raise _ActionRejected("deal is not active")
        if deal.payment_status is not PaymentStatus.OFFERED:
            raise _ActionRejected("deal has no linked payment awaiting response")
        if (
            deal.payment_response_by_turn is None
            or turn > deal.payment_response_by_turn
        ):
            raise _ActionRejected("deal payment response deadline has passed")
        if any(
            intent["deal_id"] == deal.id
            or (
                intent["payer"] == deal.proposer
                and intent["payee"] == deal.counterparty
            )
            for _, intent in self._unfinished_payment_intents()
        ):
            raise _ActionRejected(
                "an unresolved payment intent requires reconciliation"
            )
        success_deal = None
        if action.accept:
            try:
                success_deal = self._accepted_payment_deal(
                    deal,
                    turn=turn,
                    observation=observation,
                    observation_id=observation_id,
                )
            except _IncompleteFavorObservation:
                unverifiable = self._unverifiable_deal_record(
                    deal,
                    turn=turn,
                    reason="payment acceptance observation baseline was incomplete",
                    evidence_refs=(
                        (observation_id,) if observation_id is not None else ()
                    ),
                )
                return self._finish_source(
                    staged,
                    turn=turn,
                    status="applied",
                    message=f"payment acceptance became unverifiable for {deal.id}",
                    deal_id=deal.id,
                    effect={
                        "kind": "deal_changed",
                        "payload": self._deal_payload(unverifiable),
                    },
                )
        try:
            payment_state = await gs.get_channel_payment_state(
                deal.proposer,
                deal.counterparty,
                deal.payment_gold,
            )
        except Exception as exc:
            raise _ActionRejected(
                "could not verify the exact linked payment: "
                f"{type(exc).__name__}"
            ) from exc
        if self._payment_state_status(payment_state, deal) != "exact":
            raise _ActionRejected("the exact linked payment offer is not pending")
        intent = self._payment_intent_payload(
            deal,
            staged,
            turn=turn,
            deadline=deal.payment_response_by_turn,
            accept=action.accept,
            success_deal=success_deal,
            observation_id=observation_id,
        )
        self._commit("payment_response_intent", intent)
        try:
            engine_result = await gs.respond_to_channel_payment(
                deal.proposer,
                deal.payment_gold,
                action.accept,
            )
        except Exception as exc:
            await self.reconcile_payment_intents(
                gs,
                current_turn=turn,
                current_player_id=staged.actor,
            )
            recovered = next(
                (
                    acknowledgement
                    for acknowledgement in self.state.acknowledgements
                    if acknowledgement.source_id == staged.source_id
                ),
                None,
            )
            if recovered is not None:
                return recovered
            return ChannelAcknowledgement(
                staged.actor,
                turn,
                staged.source_id,
                "rejected",
                "payment response outcome requires recovery after "
                f"{type(exc).__name__}",
            )
        if not isinstance(engine_result, str) or not engine_result:
            return ChannelAcknowledgement(
                staged.actor,
                turn,
                staged.source_id,
                "rejected",
                "payment response returned no authoritative engine result",
            )
        if not self._response_succeeded(engine_result, action.accept):
            if not self._authoritative_payment_failure(engine_result):
                return ChannelAcknowledgement(
                    staged.actor,
                    turn,
                    staged.source_id,
                    "rejected",
                    "payment response outcome requires intent reconciliation",
                )
            return self._commit_payment_result(
                "payment_response_result",
                intent,
                engine_result=engine_result,
                recovery=None,
                deal=None,
                grievance=None,
                message=f"linked payment response failed: {engine_result}",
            )
        if action.accept:
            return self._commit_payment_result(
                "payment_response_result",
                intent,
                engine_result=engine_result,
                recovery=None,
                deal=success_deal,
                grievance=None,
                message=f"accepted linked payment for {deal.id}",
            )
        broken, grievance = self._broken_deal_records(
            deal,
            turn=turn,
            breach="payment_response",
            reason="exact linked payment was rejected",
        )
        return self._commit_payment_result(
            "payment_response_result",
            intent,
            engine_result=engine_result,
            recovery=None,
            deal=broken,
            grievance=grievance,
            message=f"rejected linked payment for {deal.id}",
        )

    @staticmethod
    def _funding_succeeded(engine_result: str) -> bool:
        return engine_result in {
            "CHANNEL_PAYMENT_PROPOSED",
            "PAYMENT_PROPOSED",
        }

    @staticmethod
    def _response_succeeded(engine_result: str, accept: bool) -> bool:
        expected = (
            {"CHANNEL_PAYMENT_ACCEPTED", "PAYMENT_ACCEPTED"}
            if accept
            else {"CHANNEL_PAYMENT_REJECTED", "PAYMENT_REJECTED"}
        )
        return engine_result in expected

    @staticmethod
    def _authoritative_payment_failure(engine_result: str) -> bool:
        return engine_result.startswith("Error:")

    @staticmethod
    def _payment_fingerprint(deal: Deal) -> dict[str, int]:
        return {
            "payer": deal.proposer,
            "payee": deal.counterparty,
            "gold": deal.payment_gold,
            "duration": 0,
            "item_count": 1,
        }

    @staticmethod
    def _offer_fingerprint(offer: Any) -> dict[str, int] | None:
        if offer is None:
            return None
        try:
            fingerprint = offer.fingerprint()
        except Exception:
            return None
        if not isinstance(fingerprint, dict):
            return None
        expected_fields = {"payer", "payee", "gold", "duration", "item_count"}
        if set(fingerprint) != expected_fields or any(
            type(value) is not int for value in fingerprint.values()
        ):
            return None
        return fingerprint

    @classmethod
    def _payment_state_status(cls, payment_state: Any, deal: Deal) -> str | None:
        if payment_state is None:
            return None
        status = getattr(payment_state, "status", None)
        status = getattr(status, "value", status)
        if status not in {"absent", "exact", "conflicting"}:
            return None
        offer = getattr(payment_state, "offer", None)
        if status == "exact":
            if cls._offer_fingerprint(offer) != cls._payment_fingerprint(deal):
                return None
        elif offer is not None:
            return None
        return status

    def _payment_intent_payload(
        self,
        deal: Deal,
        staged: StagedChannelAction,
        *,
        turn: int,
        deadline: int,
        accept: bool | None = None,
        success_deal: Deal | None = None,
        observation_id: str | None = None,
    ) -> dict:
        payload = {
            "source_id": staged.source_id,
            "deal_id": deal.id,
            "actor": staged.actor,
            "turn": turn,
            "payer": deal.proposer,
            "payee": deal.counterparty,
            "gold": deal.payment_gold,
            "deadline": deadline,
            "preflight_status": "absent" if accept is None else "exact",
            "preflight_player": (
                deal.proposer if accept is None else deal.counterparty
            ),
            "fingerprint": self._payment_fingerprint(deal),
        }
        if accept is not None:
            payload["accept"] = accept
        if success_deal is not None:
            payload["success_deal"] = self._deal_payload(success_deal)
        if accept is not None and observation_id is not None:
            payload["observation_id"] = observation_id
        return payload

    def _accepted_payment_deal(
        self,
        deal: Deal,
        *,
        turn: int,
        observation: ChannelObservation | None,
        observation_id: str | None,
    ) -> Deal:
        settled = replace(deal, payment_status=PaymentStatus.SETTLED)
        if deal.timing == "up_front":
            settled = self._start_favor(
                settled,
                observation or ChannelObservation(deal.counterparty, turn),
                observation_id=observation_id,
                turn=turn,
            )
        if settled.favor_status is FavorStatus.SATISFIED:
            evidence = settled.favor.monitor.get("satisfaction_observation_id")
            settled = replace(
                settled,
                state=DealState.HONORED,
                terminal={
                    "reason": "favor and payment completed",
                    "evidence_refs": [evidence] if isinstance(evidence, str) else [],
                    "adjudication_source": "deterministic",
                },
            )
        return settled

    def _commit_payment_result(
        self,
        kind: str,
        intent: dict,
        *,
        engine_result: str,
        recovery: str | None,
        deal: Deal | None,
        grievance: dict | None,
        message: str,
    ) -> ChannelAcknowledgement:
        acknowledgement = ChannelAcknowledgement(
            intent["actor"],
            intent["turn"],
            intent["source_id"],
            "applied" if deal is not None else "rejected",
            message,
            deal.id if deal is not None else None,
        )
        self._commit(
            kind,
            {
                "intent": intent,
                "fingerprint": intent["fingerprint"],
                "engine_result": engine_result,
                "recovery": recovery,
                "acknowledgement": asdict(acknowledgement),
                "deal": self._deal_payload(deal) if deal is not None else None,
                "grievance": grievance,
            },
        )
        return acknowledgement

    def _unfinished_payment_intents(self) -> tuple[tuple[str, dict], ...]:
        unfinished: dict[str, tuple[str, dict]] = {}
        for event in self._read_journal(self.events_path):
            if event.kind in {"payment_fund_intent", "payment_response_intent"}:
                source_id = event.payload.get("source_id")
                if isinstance(source_id, str):
                    unfinished[source_id] = (event.kind, event.payload)
            elif event.kind in {"payment_fund_result", "payment_response_result"}:
                intent = event.payload.get("intent")
                source_id = intent.get("source_id") if isinstance(intent, dict) else None
                if isinstance(source_id, str):
                    unfinished.pop(source_id, None)
        return tuple(unfinished.values())

    async def reconcile_payment_intents(
        self,
        gs: Any,
        *,
        current_turn: int,
        current_player_id: int,
    ) -> None:
        if type(current_turn) is not int or current_turn < 0:
            raise ValueError("current_turn must be a non-negative integer")
        if type(current_player_id) is not int:
            raise ValueError("current_player_id must be an integer")
        unfinished = self._unfinished_payment_intents()
        for _, intent in unfinished:
            if (
                intent["source_id"] not in self.state.applied_source_ids
                and intent["payee"] == current_player_id
                and current_turn < intent["turn"]
            ):
                raise ValueError("current turn precedes payment intent turn")
        for kind, intent in unfinished:
            if intent["source_id"] in self.state.applied_source_ids:
                continue
            if intent["payee"] != current_player_id:
                continue
            try:
                deal = self.deal(intent["deal_id"])
            except ValueError:
                continue
            try:
                payment_state = await gs.get_channel_payment_state(
                    intent["payer"],
                    intent["payee"],
                    intent["gold"],
                )
            except Exception:
                continue
            status = self._payment_state_status(payment_state, deal)
            if status is None:
                continue
            if kind == "payment_fund_intent":
                if status == "exact":
                    offered = replace(
                        deal,
                        payment_status=PaymentStatus.OFFERED,
                        payment_response_by_turn=(
                            intent["turn"] + self.rules.payment_response_turns
                        ),
                    )
                    self._commit_payment_result(
                        "payment_fund_result",
                        intent,
                        engine_result="RECOVERED_EXACT_CHANNEL_PAYMENT",
                        recovery="observed_exact_offer",
                        deal=offered,
                        grievance=None,
                        message=f"recovered linked payment offer for {deal.id}",
                    )
                elif status == "conflicting":
                    if current_turn <= intent["deadline"]:
                        self._commit_payment_result(
                            "payment_fund_result",
                            intent,
                            engine_result="RECOVERY_CONFLICTING_PAYMENT",
                            recovery="conflicting_offer",
                            deal=None,
                            grievance=None,
                            message=(
                                "conflicting pending payment left funding due for "
                                f"{deal.id}"
                            ),
                        )
                    else:
                        broken, grievance = self._broken_deal_records(
                            deal,
                            turn=current_turn,
                            breach="funding",
                            reason="linked payment was not funded by the deadline",
                        )
                        self._commit_payment_result(
                            "payment_fund_result",
                            intent,
                            engine_result="RECOVERY_CONFLICTING_PAYMENT_LATE",
                            recovery="conflicting_offer_after_deadline",
                            deal=broken,
                            grievance=grievance,
                            message=f"payment funding breached for {deal.id}",
                        )
                else:
                    unverifiable = self._unverifiable_deal_record(
                        deal,
                        turn=current_turn,
                        reason=(
                            "funding intent outcome is ambiguous because the "
                            "exact offer is absent"
                        ),
                    )
                    self._commit_payment_result(
                        "payment_fund_result",
                        intent,
                        engine_result="RECOVERY_PAYMENT_ABSENT",
                        recovery="offer_absent",
                        deal=unverifiable,
                        grievance=None,
                        message=f"payment funding became unverifiable for {deal.id}",
                    )
                continue

            if status != "exact":
                unverifiable = self._unverifiable_deal_record(
                    deal,
                    turn=current_turn,
                    reason=(
                        "payment response intent outcome is ambiguous because "
                        f"the exact offer is {status}"
                    ),
                )
                self._commit_payment_result(
                    "payment_response_result",
                    intent,
                    engine_result="RECOVERY_PAYMENT_ABSENT",
                    recovery=(
                        "offer_absent"
                        if status == "absent"
                        else "conflicting_offer"
                    ),
                    deal=unverifiable,
                    grievance=None,
                    message=f"payment response became unverifiable for {deal.id}",
                )
                continue
            accept = intent["accept"]
            try:
                engine_result = await gs.respond_to_channel_payment(
                    intent["payer"],
                    intent["gold"],
                    accept,
                )
            except Exception as exc:
                try:
                    remaining = await gs.get_channel_payment_state(
                        intent["payer"],
                        intent["payee"],
                        intent["gold"],
                    )
                except Exception:
                    unverifiable = self._unverifiable_deal_record(
                        deal,
                        turn=current_turn,
                        reason=(
                            "payment response recovery could not verify the "
                            "engine outcome after retry"
                        ),
                    )
                    self._commit_payment_result(
                        "payment_response_result",
                        intent,
                        engine_result=f"Error: {type(exc).__name__}",
                        recovery="response_retry_query_failed",
                        deal=unverifiable,
                        grievance=None,
                        message=f"payment response became unverifiable for {deal.id}",
                    )
                    continue
                if self._payment_state_status(remaining, deal) != "exact":
                    unverifiable = self._unverifiable_deal_record(
                        deal,
                        turn=current_turn,
                        reason=(
                            "payment response recovery remained ambiguous "
                            "after an engine exception"
                        ),
                    )
                    self._commit_payment_result(
                        "payment_response_result",
                        intent,
                        engine_result=f"Error: {type(exc).__name__}",
                        recovery="response_retry_ambiguous",
                        deal=unverifiable,
                        grievance=None,
                        message=f"payment response became unverifiable for {deal.id}",
                    )
                    continue
                engine_result = f"Error: {type(exc).__name__}"
            if self._response_succeeded(engine_result, accept):
                if accept:
                    success_payload = intent.get("success_deal")
                    accepted = (
                        self._deal_from_changed_payload(success_payload)
                        if isinstance(success_payload, dict)
                        else self._accepted_payment_deal(
                            deal,
                            turn=intent["turn"],
                            observation=None,
                            observation_id=None,
                        )
                    )
                    self._commit_payment_result(
                        "payment_response_result",
                        intent,
                        engine_result=engine_result,
                        recovery="response_retried",
                        deal=accepted,
                        grievance=None,
                        message=f"recovered accepted payment for {deal.id}",
                    )
                else:
                    broken, grievance = self._broken_deal_records(
                        deal,
                        turn=intent["turn"],
                        breach="payment_response",
                        reason="exact linked payment was rejected",
                    )
                    self._commit_payment_result(
                        "payment_response_result",
                        intent,
                        engine_result=engine_result,
                        recovery="response_retried",
                        deal=broken,
                        grievance=grievance,
                        message=f"recovered rejected payment for {deal.id}",
                    )
            elif self._authoritative_payment_failure(engine_result):
                self._commit_payment_result(
                    "payment_response_result",
                    intent,
                    engine_result=engine_result,
                    recovery="response_retried",
                    deal=None,
                    grievance=None,
                    message=f"recovered payment response failed for {deal.id}",
                )
            else:
                try:
                    post_retry = await gs.get_channel_payment_state(
                        intent["payer"],
                        intent["payee"],
                        intent["gold"],
                    )
                except Exception:
                    post_status = None
                    recovery = "response_retry_post_query_failed"
                    reason = (
                        "payment response retry returned no authoritative result "
                        "and the post-retry offer query failed"
                    )
                else:
                    post_status = self._payment_state_status(post_retry, deal)
                    recovery = "response_retry_post_state_invalid"
                    reason = (
                        "payment response retry returned no authoritative result "
                        "and the post-retry offer state was invalid"
                    )
                if post_status is not None:
                    recovery = f"response_retry_{post_status}_ambiguous"
                    reason = (
                        "payment response retry returned no authoritative result; "
                        f"the post-retry offer was {post_status}"
                    )
                unverifiable = self._unverifiable_deal_record(
                    deal,
                    turn=current_turn,
                    reason=reason,
                )
                self._commit_payment_result(
                    "payment_response_result",
                    intent,
                    engine_result="RECOVERY_AMBIGUOUS_RESPONSE",
                    recovery=recovery,
                    deal=unverifiable,
                    grievance=None,
                    message=f"payment response became unverifiable for {deal.id}",
                )

    def _deal_from_changed_payload(self, payload: dict) -> Deal:
        event = ChannelEvent(
            SCHEMA_VERSION,
            f"evt-{self.state.next_event:06d}",
            self.state.next_event,
            "deal_changed",
            payload,
        )
        changed = reduce_event(self.state, event)
        return next(deal for deal in changed.deals if deal.id == payload["id"])

    def _require_message_capacity(self, sender: int, recipient: int) -> None:
        pair_count = sum(
            message.from_player == sender and message.to_player == recipient
            for message in self.state.messages
        )
        if pair_count >= self.rules.max_messages_per_pair:
            raise _ActionRejected(
                f"message limit reached for ordered pair {sender}->{recipient}"
            )

    def _validate_favor(self, favor: dict, obligated_player: int) -> dict:
        return validate_term(
            favor,
            TermValidationContext(
                obligated_player=obligated_player,
                enabled_players=self.state.enabled_players,
            ),
        )

    def _start_favor(
        self,
        deal: Deal,
        observation: ChannelObservation,
        *,
        observation_id: str | None,
        turn: int,
    ) -> Deal:
        spec = TERM_REGISTRY[deal.favor.term_type]
        if spec.baseline_phase == "proposal":
            if not deal.favor.baseline:
                raise _ActionRejected("proposal-phase favor baseline is missing")
            baseline = deal.favor.baseline
        else:
            try:
                baseline = capture_baseline(
                    {
                        "term_type": deal.favor.term_type,
                        "params": deal.favor.params,
                    },
                    observation,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise _ActionRejected(str(exc)) from exc
            baseline = self._attach_baseline_observation(baseline, observation_id)
            if any(
                key.endswith("baseline_complete") and value is not True
                for key, value in baseline.items()
            ):
                raise _IncompleteFavorObservation(
                    "favor-start observation baseline is incomplete"
                )
        return replace(
            deal,
            favor=FavorTerm(
                deal.favor.term_type,
                deal.favor.params,
                baseline,
                {},
            ),
            favor_status=FavorStatus.DUE,
            favor_due_turn=turn + deal.completion_window_turns,
        )

    @staticmethod
    def _attach_baseline_observation(
        baseline: dict,
        observation_id: str | None,
    ) -> dict:
        if observation_id is None:
            return baseline
        attached = dict(baseline)
        for key, value in tuple(attached.items()):
            if key.endswith("_observation_id") and isinstance(value, str):
                attached[key] = observation_id
        return attached

    def _finish_source(
        self,
        staged: StagedChannelAction,
        *,
        turn: int,
        status: str,
        message: str,
        effect: dict | None,
        deal_id: str | None = None,
    ) -> ChannelAcknowledgement:
        acknowledgement = ChannelAcknowledgement(
            staged.actor,
            turn,
            staged.source_id,
            status,
            message,
            deal_id,
        )
        self._commit(
            "staged_action_applied",
            {
                "source_id": staged.source_id,
                "acknowledgement": asdict(acknowledgement),
                "effect": effect,
            },
        )
        return acknowledgement

    def _ensure_observation_recorded(
        self,
        observation: ChannelObservation,
    ) -> str:
        existing = self._observation_ids.get(id(observation))
        if existing is not None and existing[0] is observation:
            return existing[1]
        observation_id = f"obs-{self.state.next_observation:06d}"
        payload = self._observation_payload(observation_id, observation)
        self._commit("observation_recorded", payload)
        self._observation_ids[id(observation)] = (observation, observation_id)
        return observation_id

    @staticmethod
    def _observation_payload(
        observation_id: str,
        observation: ChannelObservation,
    ) -> dict:
        return {
            "id": observation_id,
            "player_id": observation.player_id,
            "turn": observation.turn,
            "families_present": sorted(
                family.value for family in observation.families_present
            ),
            "units": [asdict(unit) for unit in observation.units],
            "cities": [asdict(city) for city in observation.cities],
            "camps": [list(coordinate) for coordinate in sorted(observation.camps)],
            "territory": [
                list(coordinate) for coordinate in sorted(observation.territory)
            ],
            "wars": [list(pair) for pair in sorted(observation.wars)],
            "treasury_gold": observation.treasury_gold,
            "trade_routes": [asdict(route) for route in observation.trade_routes],
            "action_audit": [asdict(action) for action in observation.action_audit],
            "unit_distances": [
                [*key, distance]
                for key, distance in sorted(observation.unit_distances.items())
            ],
            "zone_distances": [
                [*key, distance]
                for key, distance in sorted(observation.zone_distances.items())
            ],
            "errors": list(observation.errors),
        }

    @staticmethod
    def _deal_payload(deal: Deal) -> dict:
        payload = asdict(deal)
        payload["state"] = deal.state.value
        payload["favor_status"] = deal.favor_status.value
        payload["payment_status"] = deal.payment_status.value
        return payload

    def deal(self, deal_id: str) -> Deal:
        deal = next(
            (candidate for candidate in self.state.deals if candidate.id == deal_id),
            None,
        )
        if deal is None:
            raise ValueError(f"unknown deal id {deal_id!r}")
        return deal

    def project_for_player(
        self,
        player_id: int,
        turn: int,
    ) -> ChannelProjection:
        return build_player_projection(self.state, player_id, turn)

    async def admit_player(
        self,
        gs: Any,
        player_id: int,
        turn: int,
    ) -> ChannelAdmission:
        if player_id not in self.state.enabled_players:
            raise ValueError(f"player {player_id} is not channel-enabled")
        request = compile_observation_request(self._favor_terms_for(player_id))
        observation = await gs.get_channel_observation(player_id, turn, request)
        observation_id = self._ensure_observation_recorded(observation)
        self._evaluate_favors(
            player_id,
            observation,
            observation_id,
            finalize_deadline=False,
        )
        projection = self.project_for_player(player_id, turn)
        return ChannelAdmission(
            player_id=player_id,
            turn=turn,
            observation_id=observation_id,
            projection=projection,
            block=format_channel_block(projection),
            context=ChannelTurnContext(
                self.state.run_id,
                player_id,
                turn,
                self.state.enabled_players,
                self.rules,
            ),
            wake_reasons=self._wake_reasons(player_id),
        )

    async def finish_player(
        self,
        gs: Any,
        admission: ChannelAdmission,
        policy_result: dict | None,
    ) -> tuple[ChannelAcknowledgement, ...]:
        if (
            admission.player_id not in self.state.enabled_players
            or admission.turn != admission.context.turn
            or admission.player_id != admission.context.player_id
        ):
            raise ValueError("channel admission does not match its bound context")

        api_actions = list(admission.context.staged_actions)
        parsed_lines = parse_cli_channel_lines(
            self._raw_final_summary(policy_result),
            run_id=self.state.run_id,
            actor=admission.player_id,
            turn=admission.turn,
            enabled_players=self.state.enabled_players,
            rules=self.rules,
        )
        observation_actions = api_actions + [
            parsed.staged_action
            for parsed in parsed_lines
            if parsed.staged_action is not None
        ]

        terms = list(self._favor_terms_for(admission.player_id))
        terms.extend(self._observation_terms_for_actions(observation_actions))
        request = compile_observation_request(terms)
        observation = await gs.get_channel_observation(
            admission.player_id,
            admission.turn,
            request,
        )
        observation = self._attach_action_audit(
            observation,
            request.families,
            policy_result,
            admission.player_id,
            admission.turn,
        )
        observation_id = self._ensure_observation_recorded(observation)

        acknowledgements: list[ChannelAcknowledgement] = []
        for staged in api_actions:
            acknowledgements.append(
                await self.apply_staged(
                    gs,
                    staged,
                    turn=admission.turn,
                    observation=observation,
                )
            )
        for parsed in parsed_lines:
            if parsed.staged_action is not None:
                acknowledgements.append(
                    await self.apply_staged(
                        gs,
                        parsed.staged_action,
                        turn=admission.turn,
                        observation=observation,
                    )
                )
            else:
                acknowledgements.append(
                    self._finish_invalid_source(
                        parsed.actor,
                        parsed.source_id,
                        admission.turn,
                        parsed.error or "invalid CHANNEL action",
                    )
                )

        self._finalize_player(
            admission.player_id,
            admission.turn,
            observation,
            observation_id,
        )
        return tuple(acknowledgements)

    @staticmethod
    def _raw_final_summary(policy_result: dict | None) -> str:
        if not isinstance(policy_result, dict):
            return ""
        transcript = policy_result.get("transcript")
        if not isinstance(transcript, dict):
            return ""
        summary = transcript.get("final_summary")
        return summary if isinstance(summary, str) else ""

    def _favor_terms_for(self, player_id: int) -> tuple[dict, ...]:
        return tuple(
            {
                "term_type": deal.favor.term_type,
                "params": deal.favor.params,
            }
            for deal in self.state.deals
            if deal.state is DealState.ACTIVE
            and deal.counterparty == player_id
            and deal.favor_status is FavorStatus.DUE
        )

    def _observation_terms_for_actions(
        self,
        staged_actions: list[StagedChannelAction],
    ) -> tuple[dict, ...]:
        terms: list[dict] = []
        for staged in staged_actions:
            action = staged.action
            if isinstance(action, ProposeDeal):
                try:
                    favor = self._validate_favor(
                        action.favor,
                        action.to_player,
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                term_type = favor.get("term_type")
                if not isinstance(term_type, str):
                    continue
                spec = TERM_REGISTRY.get(term_type)
                if spec is not None and spec.baseline_phase == "proposal":
                    terms.append(favor)
            elif isinstance(action, RespondToDeal) and action.accept:
                try:
                    deal = self.deal(action.deal_id)
                except ValueError:
                    continue
                if deal.timing == "on_delivery":
                    terms.append(
                        {
                            "term_type": deal.favor.term_type,
                            "params": deal.favor.params,
                        }
                    )
            elif isinstance(action, RespondToPayment) and action.accept:
                try:
                    deal = self.deal(action.deal_id)
                except ValueError:
                    continue
                if (
                    deal.timing == "up_front"
                    and deal.state is DealState.ACTIVE
                    and deal.payment_status is PaymentStatus.OFFERED
                    and staged.actor == deal.counterparty
                ):
                    terms.append(
                        {
                            "term_type": deal.favor.term_type,
                            "params": deal.favor.params,
                        }
                    )
        return tuple(terms)

    @staticmethod
    def _attach_action_audit(
        observation: ChannelObservation,
        requested_families: frozenset[ObservationFamily],
        policy_result: dict | None,
        player_id: int,
        turn: int,
    ) -> ChannelObservation:
        if ObservationFamily.ACTION_AUDIT not in requested_families:
            return observation
        transcript = (
            policy_result.get("transcript")
            if isinstance(policy_result, dict)
            else None
        )
        steps = transcript.get("steps") if isinstance(transcript, dict) else None
        if not isinstance(steps, list):
            return observation
        return replace(
            observation,
            families_present=observation.families_present
            | {ObservationFamily.ACTION_AUDIT},
            action_audit=normalize_action_audit(policy_result, player_id, turn),
        )

    def _finish_invalid_source(
        self,
        actor: int,
        source_id: str,
        turn: int,
        message: str,
    ) -> ChannelAcknowledgement:
        existing = next(
            (
                acknowledgement
                for acknowledgement in self.state.acknowledgements
                if acknowledgement.source_id == source_id
            ),
            None,
        )
        if source_id in self.state.applied_source_ids:
            return existing or ChannelAcknowledgement(
                actor,
                turn,
                source_id,
                "duplicate",
                "channel action already applied",
            )
        acknowledgement = ChannelAcknowledgement(
            actor,
            turn,
            source_id,
            "rejected",
            message,
        )
        self._commit(
            "staged_action_applied",
            {
                "source_id": source_id,
                "acknowledgement": asdict(acknowledgement),
                "effect": None,
            },
        )
        return acknowledgement

    def _evaluate_favors(
        self,
        player_id: int,
        observation: ChannelObservation,
        observation_id: str,
        *,
        finalize_deadline: bool,
    ) -> None:
        for original in tuple(self.state.deals):
            deal = self.deal(original.id)
            if (
                deal.state is not DealState.ACTIVE
                or deal.counterparty != player_id
                or deal.favor_status is not FavorStatus.DUE
                or deal.favor_due_turn is None
            ):
                continue
            due_turn = deal.favor_due_turn
            persisted_violation = (
                deal.favor.monitor.get("violation_observation_id") is not None
                or deal.favor.baseline.get("initial_violation_turn") is not None
            )
            if observation.turn > due_turn and not persisted_violation:
                self._make_unverifiable(
                    deal,
                    turn=observation.turn,
                    reason=(
                        "the first decisive favor observation arrived after "
                        "the inclusive deadline"
                    ),
                    evidence_refs=(observation_id,),
                )
                continue
            if (
                not finalize_deadline
                and observation.turn == due_turn
            ):
                due_turn += 1
            monitor = dict(deal.favor.monitor)
            monitor["current_observation_id"] = observation_id
            verification = verify_term(
                {
                    "term_type": deal.favor.term_type,
                    "params": deal.favor.params,
                },
                deal.favor.baseline,
                monitor,
                observation,
                due_turn,
            )
            persisted_monitor = {
                key: value
                for key, value in verification.monitor.items()
                if key not in {"current_observation_id", "observation_id"}
            }
            updated_favor = replace(deal.favor, monitor=persisted_monitor)
            if verification.status == "pending":
                if updated_favor != deal.favor:
                    self._commit(
                        "deal_changed",
                        self._deal_payload(replace(deal, favor=updated_favor)),
                    )
                continue
            evidence_refs = tuple(verification.evidence_refs)
            if verification.status == "satisfied":
                persisted_monitor.setdefault(
                    "satisfaction_observation_id",
                    observation_id,
                )
                updated_favor = replace(deal.favor, monitor=persisted_monitor)
                satisfied = replace(
                    deal,
                    favor=updated_favor,
                    favor_status=FavorStatus.SATISFIED,
                )
                if satisfied.payment_status is PaymentStatus.NOT_DUE:
                    satisfied = replace(
                        satisfied,
                        payment_status=PaymentStatus.DUE,
                        fund_by_turn=observation.turn + self.rules.funding_turns,
                    )
                if satisfied.payment_status is PaymentStatus.SETTLED:
                    satisfied = replace(
                        satisfied,
                        state=DealState.HONORED,
                        terminal={
                            "reason": "favor and payment completed",
                            "evidence_refs": list(
                                evidence_refs or (observation_id,)
                            ),
                            "adjudication_source": "deterministic",
                        },
                    )
                self._commit("deal_changed", self._deal_payload(satisfied))
                continue
            if verification.status == "failed":
                self._break_deal(
                    replace(deal, favor=updated_favor),
                    turn=observation.turn,
                    breach="favor",
                    reason=verification.reason,
                    evidence_refs=evidence_refs or (observation_id,),
                )
                continue
            self._make_unverifiable(
                replace(deal, favor=updated_favor),
                turn=observation.turn,
                reason=verification.reason,
                evidence_refs=evidence_refs,
            )

    def _finalize_player(
        self,
        player_id: int,
        turn: int,
        observation: ChannelObservation,
        observation_id: str,
    ) -> None:
        self._evaluate_favors(
            player_id,
            observation,
            observation_id,
            finalize_deadline=True,
        )
        unfinished_payment_deals = {
            intent["deal_id"] for _, intent in self._unfinished_payment_intents()
        }
        for original in tuple(self.state.deals):
            deal = self.deal(original.id)
            if (
                deal.state is DealState.PROPOSED
                and deal.counterparty == player_id
                and turn >= deal.accept_by_turn
            ):
                self._expire_deal(deal)
                continue
            if deal.state is not DealState.ACTIVE:
                continue
            if deal.id in unfinished_payment_deals:
                continue
            if (
                deal.proposer == player_id
                and deal.payment_status is PaymentStatus.DUE
                and deal.fund_by_turn is not None
                and turn >= deal.fund_by_turn
            ):
                self._break_deal(
                    deal,
                    turn=turn,
                    breach="funding",
                    reason="promised payment was not funded by the deadline",
                )
            elif (
                deal.counterparty == player_id
                and deal.payment_status is PaymentStatus.OFFERED
                and deal.payment_response_by_turn is not None
                and turn >= deal.payment_response_by_turn
            ):
                self._break_deal(
                    deal,
                    turn=turn,
                    breach="payment_response",
                    reason="exact linked payment was not accepted by the deadline",
                )

    def _break_deal(
        self,
        deal: Deal,
        *,
        turn: int,
        breach: str,
        reason: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> None:
        broken, grievance = self._broken_deal_records(
            deal,
            turn=turn,
            breach=breach,
            reason=reason,
            evidence_refs=evidence_refs,
        )
        self._commit(
            "deal_broken",
            {"deal": self._deal_payload(broken), "grievance": grievance},
        )

    def _broken_deal_records(
        self,
        deal: Deal,
        *,
        turn: int,
        breach: str,
        reason: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[Deal, dict]:
        return self._canonical_breach_records(
            self.state,
            deal,
            turn=turn,
            breach=breach,
            reason=reason,
            evidence_refs=evidence_refs,
        )

    def _make_unverifiable(
        self,
        deal: Deal,
        *,
        turn: int,
        reason: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> None:
        unverifiable = self._unverifiable_deal_record(
            deal,
            turn=turn,
            reason=reason,
            evidence_refs=evidence_refs,
        )
        self._commit("deal_changed", self._deal_payload(unverifiable))

    @staticmethod
    def _unverifiable_deal_record(
        deal: Deal,
        *,
        turn: int,
        reason: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> Deal:
        favor_status = (
            FavorStatus.RELEASED
            if deal.favor_status in (FavorStatus.NOT_DUE, FavorStatus.DUE)
            else deal.favor_status
        )
        if deal.payment_status in (PaymentStatus.NOT_DUE, PaymentStatus.DUE):
            payment_status = PaymentStatus.WAIVED
        elif deal.payment_status is PaymentStatus.OFFERED:
            payment_status = PaymentStatus.FAILED
        else:
            payment_status = deal.payment_status
        return replace(
            deal,
            state=DealState.UNVERIFIABLE,
            favor_status=favor_status,
            payment_status=payment_status,
            terminal={
                "turn": turn,
                "reason": reason,
                "evidence_refs": list(evidence_refs),
                "adjudication_source": "deterministic",
            },
        )

    def _expire_deal(self, deal: Deal) -> None:
        expired = replace(
            deal,
            state=DealState.EXPIRED,
            favor_status=FavorStatus.RELEASED,
            payment_status=PaymentStatus.WAIVED,
            terminal={
                "reason": "proposal expired",
                "decisive_event_refs": [],
                "adjudication_source": "deterministic",
            },
        )
        self._commit("deal_changed", self._deal_payload(expired))

    def _wake_reasons(self, player_id: int) -> tuple[str, ...]:
        reasons: set[str] = set()
        for deal in self.state.deals:
            if deal.state is DealState.PROPOSED and deal.counterparty == player_id:
                reasons.add("deal response due")
            elif deal.state is DealState.ACTIVE:
                if (
                    deal.counterparty == player_id
                    and deal.favor_status is FavorStatus.DUE
                ):
                    reasons.add("favor due")
                if (
                    deal.proposer == player_id
                    and deal.payment_status is PaymentStatus.DUE
                ):
                    reasons.add("payment funding due")
                if (
                    deal.counterparty == player_id
                    and deal.payment_status is PaymentStatus.OFFERED
                ):
                    reasons.add("payment response due")
        return tuple(sorted(reasons))

    async def poll_unseated(
        self,
        gs: Any,
        turn: int,
        local_player_id: int | None,
    ) -> None:
        if local_player_id in self.state.enabled_players and any(
            deal.state is DealState.ACTIVE
            and deal.counterparty == local_player_id
            and deal.favor_status is FavorStatus.DUE
            for deal in self.state.deals
        ):
            request = compile_observation_request(
                self._favor_terms_for(local_player_id)
            )
            observation = await gs.get_channel_observation(
                local_player_id,
                turn,
                request,
            )
            observation_id = self._ensure_observation_recorded(observation)
            self._evaluate_favors(
                local_player_id,
                observation,
                observation_id,
                finalize_deadline=True,
            )

        unfinished_payment_deals = {
            intent["deal_id"] for _, intent in self._unfinished_payment_intents()
        }
        for original in tuple(self.state.deals):
            deal = self.deal(original.id)
            if deal.state is DealState.PROPOSED and self._unseated_deadline_reached(
                deal.accept_by_turn,
                deal.counterparty,
                turn,
                local_player_id,
            ):
                self._expire_deal(deal)
                continue
            if deal.state is not DealState.ACTIVE:
                continue
            if deal.id in unfinished_payment_deals:
                continue
            if (
                deal.payment_status is PaymentStatus.DUE
                and deal.fund_by_turn is not None
                and self._unseated_deadline_reached(
                    deal.fund_by_turn,
                    deal.proposer,
                    turn,
                    local_player_id,
                )
            ):
                self._break_deal(
                    deal,
                    turn=turn,
                    breach="funding",
                    reason="promised payment was not funded by the deadline",
                )
                continue
            if (
                deal.payment_status is PaymentStatus.OFFERED
                and deal.payment_response_by_turn is not None
                and self._unseated_deadline_reached(
                    deal.payment_response_by_turn,
                    deal.counterparty,
                    turn,
                    local_player_id,
                )
            ):
                self._break_deal(
                    deal,
                    turn=turn,
                    breach="payment_response",
                    reason="exact linked payment was not accepted by the deadline",
                )
                continue
            if (
                deal.favor_status is FavorStatus.DUE
                and deal.favor_due_turn is not None
                and turn > deal.favor_due_turn
            ):
                self._make_unverifiable(
                    deal,
                    turn=turn,
                    reason=(
                        "favor deadline passed without a required post-action "
                        "observation"
                    ),
                )

    @staticmethod
    def _unseated_deadline_reached(
        deadline: int,
        responsible_player: int,
        turn: int,
        local_player_id: int | None,
    ) -> bool:
        return turn > deadline or (
            turn == deadline and local_player_id == responsible_player
        )


__all__ = [
    "ChannelAdmission",
    "ChannelRuntime",
    "ChannelStateError",
    "grievance_base_magnitude",
]
