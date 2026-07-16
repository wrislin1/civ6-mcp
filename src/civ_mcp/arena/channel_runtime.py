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
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise ChannelStateError(f"invalid channel snapshot: {exc}") from exc
            os.chmod(state_path, 0o600)
            cls._validate_identity(snapshot, run_id, enabled_players, rules)
        else:
            if events_path.stat().st_size:
                raise ChannelStateError(
                    "channel identity snapshot is missing for a nonempty journal"
                )
            snapshot = initial_channel_state(run_id, enabled_players, rules)

        replayed = initial_channel_state(run_id, enabled_players, rules)
        try:
            events = cls._read_journal(events_path)
            for event in events:
                replayed = reduce_event(replayed, event)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ChannelStateError(f"invalid channel journal: {exc}") from exc

        if snapshot.last_event_sequence > replayed.last_event_sequence:
            raise ChannelStateError(
                "channel snapshot is newer than the write-ahead journal"
            )
        if (
            snapshot.last_event_sequence == replayed.last_event_sequence
            and snapshot != replayed
        ):
            raise ChannelStateError(
                "channel snapshot does not match journal at the same sequence"
            )

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

    def _commit(self, kind: str, payload: dict) -> ChannelEvent:
        sequence = self.state.last_event_sequence + 1
        event = ChannelEvent(
            SCHEMA_VERSION,
            f"evt-{sequence:06d}",
            sequence,
            kind,
            payload,
        )
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
        self.state = reduce_event(self.state, event)
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
        del gs
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
        if staged.actor not in self.state.enabled_players:
            raise ValueError(f"actor {staged.actor} is not channel-enabled")
        observation_id = (
            self._ensure_observation_recorded(observation)
            if observation is not None
            else None
        )
        try:
            message, deal_id = self._apply_pure_action(
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
            )
        return self._finish_source(
            staged,
            turn=turn,
            status="applied",
            message=message,
            deal_id=deal_id,
        )

    def _apply_pure_action(
        self,
        staged: StagedChannelAction,
        *,
        turn: int,
        observation: ChannelObservation | None,
        observation_id: str | None,
    ) -> tuple[str, str | None]:
        action = staged.action
        if isinstance(action, SendMessage):
            self._require_message_capacity(staged.actor, action.to_player)
            message_id = f"msg-{self.state.next_message:06d}"
            self._commit(
                "message_sent",
                {
                    "id": message_id,
                    "from_player": staged.actor,
                    "to_player": action.to_player,
                    "turn": turn,
                    "text": action.text,
                    "deal_id": None,
                },
            )
            return (
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
            self._commit(
                "deal_proposed",
                {
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
            )
            return f"proposed unofficial deal {deal_id}", deal_id

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
                self._commit("deal_changed", self._deal_payload(declined))
                return f"declined unofficial deal {deal.id}", deal.id

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
            self._commit("deal_changed", self._deal_payload(accepted))
            return f"accepted unofficial deal {deal.id}", deal.id

        if isinstance(action, (FundDeal, RespondToPayment)):
            raise _ActionRejected("linked payment actions are not available yet")
        raise _ActionRejected("unknown staged channel action")

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
        deal_id: str | None = None,
    ) -> ChannelAcknowledgement:
        self._commit("source_applied", {"source_id": staged.source_id})
        acknowledgement = ChannelAcknowledgement(
            staged.actor,
            turn,
            staged.source_id,
            status,
            message,
            deal_id,
        )
        self._commit("acknowledged", asdict(acknowledgement))
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
        self._commit("source_applied", {"source_id": source_id})
        acknowledgement = ChannelAcknowledgement(
            actor,
            turn,
            source_id,
            "rejected",
            message,
        )
        self._commit("acknowledged", asdict(acknowledgement))
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
        self._commit("deal_changed", self._deal_payload(broken))
        self._commit(
            "grievance_created",
            {
                "id": f"grv-{self.state.next_grievance:06d}",
                "wronged": wronged,
                "offender": offender,
                "deal_id": deal.id,
                "turn": turn,
                "reason": reason,
                "payment_gold": deal.payment_gold,
                "base_magnitude": grievance_base_magnitude(deal.payment_gold),
                "half_life_turns": self.rules.grievance_half_life_turns,
                "adjudication_source": "deterministic",
                "adjudication_metadata": None,
            },
        )

    def _make_unverifiable(
        self,
        deal: Deal,
        *,
        turn: int,
        reason: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> None:
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
        unverifiable = replace(
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
        self._commit("deal_changed", self._deal_payload(unverifiable))

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
