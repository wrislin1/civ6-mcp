"""unofficial_channels_core_v1 — the first registered live-gate scenario.

Design: docs/superpowers/specs/2026-07-17-arena-live-gate-driver-design.md

This module plans deterministic per-seat channel inputs and verifies every
transition against canonical ChannelRuntime state. It depends only on the
public channel runtime/protocol/term/projection/prompt interfaces; it never
calls apply_staged or parse_cli_channel_lines, never edits the channel
ledger, and never injects evidence.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from civ_mcp.arena.channel_runtime import ChannelAdmission
from civ_mcp.arena.channel_terms import ObservationFamily, ObservationRequest
from civ_mcp.arena.live_gate import (
    GATE_FAILED,
    GATE_PASSED,
    GATE_RESTART_REQUIRED,
    LiveGateJournal,
    ScenarioMeta,
    register_scenario,
)
from civ_mcp.arena.prompting import build_opening_prompt
from civ_mcp.arena.scripted_policy import ScriptedPolicy
from civ_mcp.arena.transcript import serialize_transcript_record

SCENARIO_NAME = "unofficial_channels_core_v1"
SCENARIO_REVISION = 1

ROLE_API = "api_actor"
ROLE_CLI = "cli_actor"
ROLE_OBSERVER = "privacy_observer"
ROLE_CONTRACTS = (
    (ROLE_API, "in_process"),
    (ROLE_CLI, "cli"),
    (ROLE_OBSERVER, "scripted"),
)

# Fixed scenario parameters (part of the configuration fingerprint).
PAYMENT_GOLD = 1
UPFRONT_WITHIN = 1  # up-front favor window: settlement turn + 1
ON_DELIVERY_WITHIN = 1  # on-delivery favor window: acceptance turn + 1
TRADE_KINDS = ("trade_route",)
MIN_GOLD = 0
UPFRONT_PROPOSAL_TEXT = (
    "Gate up-front deal: 1 gold now for a one-turn trade-route freeze."
)
ON_DELIVERY_PROPOSAL_TEXT = (
    "Gate on-delivery deal: hold your reserve one turn, then 1 gold."
)

PHASE_PREFLIGHT = "preflight"
PHASE_CANARY_AND_UPFRONT_PROPOSAL = "canary_and_upfront_proposal"
PHASE_ACCEPT_UPFRONT = "accept_upfront"
PHASE_FUND_UPFRONT = "fund_upfront"
PHASE_RESTART_REQUIRED = "restart_required"
PHASE_RESTART_VERIFY = "restart_verify"
PHASE_ACCEPT_UPFRONT_PAYMENT = "accept_upfront_payment"
PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE = "await_upfront_favor_deadline"
PHASE_VERIFY_UPFRONT_HONORED = "verify_upfront_honored"
PHASE_PROPOSE_ON_DELIVERY = "propose_on_delivery"
PHASE_ACCEPT_ON_DELIVERY = "accept_on_delivery"
PHASE_AWAIT_ON_DELIVERY_FAVOR = "await_on_delivery_favor"
PHASE_WITHHOLD_ON_DELIVERY_FUNDING = "withhold_on_delivery_funding"
PHASE_VERIFY_FUNDING_BREACH = "verify_funding_breach"
PHASE_VERIFY_TERMINAL_GATE = "verify_terminal_gate"

PRIVACY_ARTIFACT_KINDS = (
    "projection",
    "channel_block",
    "opening_prompt",
    "acknowledgements",
    "policy_result",
    "pending_transcript_record",
    "transcript_records",
)
_PRIVACY_SCAN_MAX_DEPTH = 8
_PRIVACY_SCAN_MAX_CHARS = 1_000_000
_PRIVACY_SCAN_MAX_JSON_ATTEMPTS = 256


class _PrivacyScanLimitExceeded(RuntimeError):
    """Raised when observer privacy inspection cannot finish within bounds."""


class _PrivacyScanBudget:
    """One shared recursive JSON-scan budget for a single privacy artifact."""

    def __init__(self) -> None:
        self.remaining_chars = _PRIVACY_SCAN_MAX_CHARS
        self.remaining_attempts = _PRIVACY_SCAN_MAX_JSON_ATTEMPTS

    def check_depth(self, depth: int) -> None:
        if depth > _PRIVACY_SCAN_MAX_DEPTH:
            raise _PrivacyScanLimitExceeded("privacy scan depth limit exceeded")

    def consume_text(self, text: str) -> None:
        if len(text) > self.remaining_chars:
            raise _PrivacyScanLimitExceeded("privacy scan character limit exceeded")
        self.remaining_chars -= len(text)

    def consume_json_attempt(self) -> None:
        if self.remaining_attempts <= 0:
            raise _PrivacyScanLimitExceeded("privacy JSON attempt limit exceeded")
        self.remaining_attempts -= 1


def minimum_captures(config) -> int:
    """Seat captures for the expected deterministic path.

    Handshake rounds: R1 canary+propose+accept, R2 fund (restart boundary),
    R3 restart-verify + payment response — 3 rounds. Then UPFRONT_WITHIN
    rounds to the up-front favor's inclusive deadline, 2 rounds for the
    on-delivery proposal + acceptance, ON_DELIVERY_WITHIN rounds to its favor
    deadline, then funding_turns withheld rounds through the inclusive
    funding deadline. 9 rounds x 3 seats = 27 with the checked-in rules.
    """

    rounds = (
        3
        + UPFRONT_WITHIN
        + 2
        + ON_DELIVERY_WITHIN
        + config.channel_rules.funding_turns
    )
    return rounds * len(ROLE_CONTRACTS)


def gate_config_fingerprint(config) -> dict:
    roles = dict(config.live_gate.roles)
    specs = {spec.player_id: spec for spec in config.players}
    role_pids = sorted(roles.values())
    return {
        "scenario": SCENARIO_NAME,
        "scenario_revision": SCENARIO_REVISION,
        "run_id": config.run_id,
        "roles": {name: roles[name] for name in sorted(roles)},
        "driver_kinds": {str(pid): specs[pid].driver_kind() for pid in role_pids},
        "channels_enabled": {
            str(pid): specs[pid].options.channels.enabled for pid in role_pids
        },
        "channel_rules": config.channel_rules.fingerprint(),
        "parameters": {
            "payment_gold": PAYMENT_GOLD,
            "upfront_within": UPFRONT_WITHIN,
            "on_delivery_within": ON_DELIVERY_WITHIN,
            "trade_kinds": list(TRADE_KINDS),
            "min_gold": MIN_GOLD,
        },
    }


def canary_text(run_id: str, fingerprint: dict) -> str:
    """Return the deterministic canary bound to run and gate configuration."""

    canonical = json.dumps(
        {"run_id": run_id, "fingerprint": fingerprint},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "GATE-CANARY-" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


class _GateSeatPolicy:
    """Deterministic gate policy for one bound seat.

    The configured player identity remains available to transcripts and
    fingerprints while the gate driver plans the deterministic turn.
    ScriptedPolicy supplies the scenario's sanctioned minimal-turn and
    blocker-repair behavior.
    """

    def __init__(self, driver: "ChannelsCoreDriver", spec) -> None:
        self.provider = spec.provider
        self.model = spec.model
        self.options = spec.options
        self.player_id = spec.player_id
        self._driver = driver
        self._scripted = ScriptedPolicy(options=spec.options)

    async def __call__(
        self, gs, player_id: int, turn: int, *, blocker_block: str = "", **kwargs
    ) -> dict:
        if blocker_block:
            return await self._scripted._repair(gs, blocker_block)
        base = await self._scripted(gs, player_id, turn)
        return await self._driver.seat_turn(gs, player_id, turn, base)


class ChannelsCoreDriver:
    """Coordinator-owned deterministic driver for the first scenario.

    Lifecycle: arena.py constructs it from the validated config; run_arena
    calls attach() once (after ChannelRuntime.open), note_admission() per
    admitted gate seat, and after_seat_capture() once per finished capture;
    pending_signal()/result_summary() report restart/terminal outcomes.
    """

    def __init__(self, config) -> None:
        self.config = config
        self.role_pid: dict[str, int] = dict(config.live_gate.roles)
        self.pid_role: dict[int, str] = {
            pid: role for role, pid in self.role_pid.items()
        }
        self.gate_pids: frozenset[int] = frozenset(self.role_pid.values())
        self.fingerprint = gate_config_fingerprint(config)
        self.canary = canary_text(config.run_id, self.fingerprint)
        # Filled by attach() (Task 6):
        self._journal = None
        self._runtime = None
        self._gs = None
        self._run_dir = None
        self._signal: str | None = None
        self._policies = {
            spec.player_id: _GateSeatPolicy(self, spec)
            for spec in config.players
            if spec.player_id in self.gate_pids
        }
        self._admissions: dict[tuple[int, int], ChannelAdmission] = {}
        self._captured_this_turn: dict[int, set[int]] = {}
        self._observer_captures: dict[int, tuple[ChannelAdmission, dict]] = {}
        self._restart_armed = False

    async def attach(self, *, gs, channel_runtime, run_dir) -> None:
        if channel_runtime is None:
            raise RuntimeError("live gate requires the channel runtime")
        self._gs = gs
        self._runtime = channel_runtime
        self._run_dir = Path(run_dir)
        self._journal = LiveGateJournal.open(
            self._run_dir,
            run_id=self.config.run_id,
            scenario=SCENARIO_NAME,
            scenario_revision=SCENARIO_REVISION,
            roles=dict(self.role_pid),
            config_fingerprint=self.fingerprint,
            initial_phase=PHASE_PREFLIGHT,
        )
        state = self._journal.state
        if state.status in (GATE_FAILED, GATE_PASSED):
            raise RuntimeError(f"gate already terminal: {state.status} ({state.reason})")
        if state.status == GATE_RESTART_REQUIRED:
            await self._verify_restart()

    def policy_for(self, player_id: int) -> _GateSeatPolicy:
        return self._policies[player_id]

    def note_admission(self, player_id, turn, admission, error) -> None:
        if self._signal is not None or player_id not in self.gate_pids:
            return
        if admission is None:
            self._fail(
                f"gate seat {player_id} turn {turn} has no channel admission: {error}"
            )
            return
        if self._journal.state.pending_actions and (
            player_id,
            turn,
        ) not in self._admissions:
            self._recover_pending_actions(player_id, turn)
            if self._signal is not None:
                return
        self._admissions[(player_id, turn)] = admission

    def pending_signal(self) -> str | None:
        return self._signal

    def result_summary(self) -> dict:
        state = self._journal.state if self._journal is not None else None
        if state is None:
            return {
                "status": GATE_FAILED,
                "phase": "",
                "reason": "gate never attached",
                "restart_count": 0,
                "run_id": self.config.run_id,
            }
        return {
            "status": state.status,
            "phase": state.phase,
            "reason": state.reason,
            "restart_count": state.restart_count,
            "run_id": state.run_id,
        }

    def inspect_pending_transcript_record(
        self, player_id: int, turn: int, record: dict
    ) -> bool:
        """Inspect the exact observer record immediately before persistence.

        The coordinator calls this hook with the same record object it will
        pass to ``TranscriptSink.write``. A false result means the write must
        be skipped.
        """

        if self._signal is not None:
            return False
        if player_id != self.role_pid[ROLE_OBSERVER]:
            return True
        capture = self._observer_captures.pop(turn, None)
        if capture is None:
            self._fail(
                f"observer pending transcript hook lacked capture at turn {turn}"
            )
            return False
        if record.get("player_id") != player_id or record.get("turn") != turn:
            self._fail(f"observer pending transcript identity mismatch at turn {turn}")
            return False
        admission, policy_result = capture
        try:
            self._observer_assertions(admission, policy_result, record, turn)
        except Exception:
            self._fail(f"observer privacy inspection failed at turn {turn}")
        return self._signal is None

    @staticmethod
    def _jsonable(value):
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: ChannelsCoreDriver._jsonable(getattr(value, field.name))
                for field in dataclasses.fields(value)
            }
        if isinstance(value, Mapping):
            return {
                str(key): ChannelsCoreDriver._jsonable(item)
                for key, item in value.items()
            }
        if isinstance(value, (tuple, list)):
            return [ChannelsCoreDriver._jsonable(item) for item in value]
        if isinstance(value, (frozenset, set)):
            return sorted(
                (ChannelsCoreDriver._jsonable(item) for item in value), key=str
            )
        return str(value)

    @classmethod
    def _json_text(cls, value, *, compact: bool = False) -> str:
        kwargs = {"sort_keys": True, "default": cls._jsonable}
        if compact:
            kwargs["separators"] = (",", ":")
        return json.dumps(value, **kwargs)

    def _forbidden_values(self) -> tuple[str, ...]:
        """Private raw-text values forbidden from every observer artifact."""

        journal = self._journal
        assert journal is not None
        data = journal.state.data
        values = [
            self.canary,
            self.canary.removeprefix("GATE-CANARY-"),
            UPFRONT_PROPOSAL_TEXT,
            ON_DELIVERY_PROPOSAL_TEXT,
            f"api:{self.config.run_id}:{self.role_pid[ROLE_API]}:",
            f"cli:{self.config.run_id}:{self.role_pid[ROLE_CLI]}:",
        ]
        fingerprint = data.get("upfront_payment_fingerprint")
        if fingerprint:
            values.extend(
                (
                    self._json_text(fingerprint),
                    self._json_text(fingerprint, compact=True),
                )
            )
        return tuple(dict.fromkeys(value for value in values if value))

    @classmethod
    def _mapping_is_typed_subset(cls, candidate: Mapping, target: Mapping) -> bool:
        """Return whether ``target`` appears in ``candidate`` without type coercion."""

        for target_key, target_value in target.items():
            for candidate_key in candidate:
                if (
                    type(candidate_key) is type(target_key)
                    and candidate_key == target_key
                ):
                    break
            else:
                return False
            if not cls._typed_value_matches(candidate[candidate_key], target_value):
                return False
        return True

    @classmethod
    def _typed_value_matches(cls, candidate, target) -> bool:
        if isinstance(target, Mapping):
            return isinstance(candidate, Mapping) and cls._mapping_is_typed_subset(
                candidate, target
            )
        if isinstance(target, (tuple, list)):
            return (
                isinstance(candidate, (tuple, list))
                and len(candidate) == len(target)
                and all(
                    cls._typed_value_matches(candidate_item, target_item)
                    for candidate_item, target_item in zip(candidate, target)
                )
            )
        return type(candidate) is type(target) and candidate == target

    @classmethod
    def _embedded_json_values(cls, text: str, budget: _PrivacyScanBudget):
        """Yield containers/strings safely decoded from arbitrary observer text."""

        decoder = json.JSONDecoder()
        index = 0
        while index < len(text):
            mapping_start = text.find("{", index)
            sequence_start = text.find("[", index)
            string_start = text.find('"', index)
            starts = tuple(
                start
                for start in (mapping_start, sequence_start, string_start)
                if start >= 0
            )
            if not starts:
                return
            start = min(starts)
            budget.consume_json_attempt()
            try:
                value, end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                index = start + 1
                continue
            if isinstance(value, (Mapping, list, str)):
                yield value
            index = max(end, start + 1)

    @classmethod
    def _contains_mapping(
        cls,
        value,
        target: Mapping,
        budget: _PrivacyScanBudget,
        *,
        depth: int = 0,
    ) -> bool:
        budget.check_depth(depth)
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            value = cls._jsonable(value)
        if isinstance(value, Mapping):
            if cls._mapping_is_typed_subset(value, target):
                return True
            return any(
                cls._contains_mapping(item, target, budget, depth=depth + 1)
                for item in value.values()
            )
        if isinstance(value, (tuple, list, frozenset, set)):
            return any(
                cls._contains_mapping(item, target, budget, depth=depth + 1)
                for item in value
            )
        if isinstance(value, str):
            budget.consume_text(value)
            return any(
                cls._contains_mapping(item, target, budget, depth=depth + 1)
                for item in cls._embedded_json_values(value, budget)
            )
        return False

    def _player3_transcript_artifact(self) -> tuple[str, tuple[dict, ...]]:
        run_dir = self._run_dir
        assert run_dir is not None
        path = run_dir / "transcript.jsonl"
        if not path.exists():
            return "", ()
        observer = self.role_pid[ROLE_OBSERVER]
        lines = []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("invalid persisted transcript JSONL") from exc
            if isinstance(record, dict) and record.get("player_id") == observer:
                lines.append(line)
                records.append(record)
        return "\n".join(lines), tuple(records)

    def _observer_assertions(
        self, admission, policy_result: dict, pending_record: dict, turn: int
    ) -> None:
        observer = self.role_pid[ROLE_OBSERVER]
        projection = admission.projection
        pending_text = serialize_transcript_record(pending_record)
        journal = self._journal
        assert journal is not None
        transcript_text, transcript_records = self._player3_transcript_artifact()
        artifacts = (
            ("projection", self._json_text(projection), projection),
            ("channel_block", admission.block, None),
            (
                "opening_prompt",
                build_opening_prompt(
                    player_id=observer,
                    turn=turn,
                    channel_block=admission.block,
                ),
                None,
            ),
            (
                "acknowledgements",
                self._json_text(projection.acknowledgements),
                projection.acknowledgements,
            ),
            ("policy_result", self._json_text(policy_result), policy_result),
            ("pending_transcript_record", pending_text, pending_record),
            ("transcript_records", transcript_text, transcript_records),
        )
        participants = {self.role_pid[ROLE_API], self.role_pid[ROLE_CLI]}
        projection_ok = (
            projection.player_id == observer
            and not any(
                message.from_player in participants
                or message.to_player in participants
                for message in projection.messages
            )
            and not any(
                deal.proposer in participants or deal.counterparty in participants
                for deal in projection.deals
            )
            and not any(
                grievance.offender in participants
                or grievance.wronged in participants
                for grievance in projection.grievances
            )
        )
        acknowledgements_ok = not projection.acknowledgements
        forbidden = self._forbidden_values()
        forbidden_digests = [
            hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
            for value in forbidden
        ]
        fingerprint = journal.state.data.get("upfront_payment_fingerprint")
        results = []
        for kind, text, structured in artifacts:
            leaked = tuple(value for value in forbidden if value in text)
            fingerprint_leaked = False
            if isinstance(fingerprint, Mapping):
                scan_budget = _PrivacyScanBudget()
                fingerprint_leaked = (
                    structured is not None
                    and self._contains_mapping(
                        structured, fingerprint, scan_budget
                    )
                ) or self._contains_mapping(text, fingerprint, scan_budget)
            structure_ok = (
                projection_ok
                if kind == "projection"
                else acknowledgements_ok
                if kind == "acknowledgements"
                else True
            )
            failed = bool(leaked) or fingerprint_leaked or not structure_ok
            results.append((kind, text, leaked, failed))
            journal.append(
                "privacy_asserted",
                {
                    "turn": turn,
                    "player_id": observer,
                    "artifact_kind": kind,
                    "capture_artifact_kinds": PRIVACY_ARTIFACT_KINDS,
                    "input_digest": hashlib.sha256(
                        text.encode("utf-8", errors="surrogatepass")
                    ).hexdigest()[:16],
                    "forbidden_digests": forbidden_digests,
                    "result": "FAIL" if failed else "PASS",
                },
            )

        failures = [item for item in results if item[3]]
        if not failures:
            return
        for kind, text, leaked, _failed in failures:
            journal.write_private_json(
                f"privacy_fail_{turn}_{kind}.json",
                {
                    "turn": turn,
                    "player_id": observer,
                    "artifact_kind": kind,
                    "input": text,
                    "leaked_digests": [
                        hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
                        for value in leaked
                    ],
                },
            )
        self._fail(
            f"privacy assertion failed at observer turn {turn} "
            "(private forensic input preserved)"
        )

    def _fail(self, reason: str) -> None:
        if self._signal == GATE_FAILED:
            return
        self._signal = GATE_FAILED
        if self._journal is not None and self._journal.state.status not in (
            GATE_FAILED,
            GATE_PASSED,
        ):
            self._journal.append("gate_failed", {"reason": reason})
            self._journal.write_result()

    async def _verify_restart(self) -> None:
        """Reconcile the persisted restart checkpoint with live state.

        ChannelRuntime.open has already replayed its journal before attach()
        reaches this method.  Only an unchanged exact official offer and an
        otherwise untouched payment response boundary may reactivate the gate.
        """

        state = self._journal.state
        if state.restart_count != 1:
            self._fail(
                f"restart count {state.restart_count} at resume; expected exactly 1"
            )
            return
        recorded = state.data.get("upfront_payment_fingerprint")
        live = await self._live_offer_fingerprint(
            self.role_pid[ROLE_API], self.role_pid[ROLE_CLI]
        )
        if live is None:
            return
        self._journal.append(
            "data_recorded",
            {"data": {"restart_offer_fingerprint_after": live}},
        )
        if live != recorded:
            self._fail(
                f"resumed offer fingerprint {live} does not equal the recorded "
                f"pre-restart fingerprint {recorded}"
            )
            return
        checkpoint_sequence = state.data.get("restart_channel_sequence")
        live_sequence = self._runtime.state.last_event_sequence
        if (
            type(checkpoint_sequence) is not int
            or live_sequence != checkpoint_sequence
        ):
            self._fail(
                f"resumed channel sequence {live_sequence} does not equal the "
                f"restart checkpoint sequence {checkpoint_sequence!r}"
            )
            return
        from civ_mcp.arena.channels import PaymentStatus

        deal = self._deal(state.data.get("upfront_deal_id"))
        if deal is None:
            return
        if deal.payment_status is not PaymentStatus.OFFERED:
            self._fail(
                "up-front deal payment state changed across restart: "
                f"{deal.payment_status}"
            )
            return
        response_acks = [
            acknowledgement
            for acknowledgement in self._runtime.state.acknowledgements
            if acknowledgement.deal_id == state.data.get("upfront_deal_id")
            and acknowledgement.player_id == self.role_pid[ROLE_CLI]
            and acknowledgement.turn > state.data.get("restart_turn", -1)
        ]
        if response_acks:
            self._fail(
                "a payment-response acknowledgement already exists at resume"
            )
            return
        self._journal.append(
            "restart_verified", {"turn": state.data.get("restart_turn")}
        )
        self._journal.append(
            "phase_advanced",
            {
                "phase": PHASE_ACCEPT_UPFRONT_PAYMENT,
                "turn": state.data.get("restart_turn"),
            },
        )

    async def seat_turn(self, gs, player_id, turn, base_result) -> dict:
        try:
            return await self._seat_turn_inner(gs, player_id, turn, base_result)
        except Exception as exc:
            self._fail(f"seat_turn seat {player_id} turn {turn}: {exc!r}")
            return base_result

    async def _seat_turn_inner(self, gs, player_id, turn, base_result) -> dict:
        if self._signal is not None:
            return base_result
        admission = self._admissions.get((player_id, turn))
        if admission is None:
            self._fail(f"gate seat {player_id} turn {turn} acted without an admission")
            return base_result
        if self._journal.state.phase == PHASE_PREFLIGHT:
            await self._run_preflight(gs, turn)
            if self._signal is not None:
                return base_result
        role = self.pid_role[player_id]
        if role == ROLE_OBSERVER:
            opening_prompt = build_opening_prompt(
                player_id=player_id,
                turn=turn,
                channel_block=admission.block,
            )
            result = {
                "summary": (
                    base_result.get("summary", "live-gate observer capture")
                    if isinstance(base_result, dict)
                    else "live-gate observer capture"
                ),
                "actions": (
                    base_result.get("actions", [])
                    if isinstance(base_result, dict)
                    else []
                ),
                "transcript": {"steps": [], "final_summary": opening_prompt},
            }
            self._observer_captures[turn] = (admission, result)
            return result
        phase = self._journal.state.phase
        plans = self._planned_channel_input(role, phase, turn)
        if not plans:
            return base_result
        if role == ROLE_API:
            self._emit_api(admission, player_id, turn, phase, plans)
            return base_result
        return self._emit_cli(base_result, player_id, turn, phase, plans)

    def _planned_channel_input(
        self, role: str, phase: str, turn: int
    ) -> tuple[tuple[str, dict], ...]:
        """Return the exact channel inputs this role owns in this phase."""

        del turn
        data = self._journal.state.data
        api = self.role_pid[ROLE_API]
        cli = self.role_pid[ROLE_CLI]
        observer = self.role_pid[ROLE_OBSERVER]
        if phase == PHASE_CANARY_AND_UPFRONT_PROPOSAL and role == ROLE_API:
            return (
                ("send_message", {"to_player": cli, "text": self.canary}),
                (
                    "propose_deal",
                    {
                        "to_player": cli,
                        "text": UPFRONT_PROPOSAL_TEXT,
                        "favor": {
                            "term_type": "dont_trade_with",
                            "params": {
                                "target_player": observer,
                                "trade_kinds": list(TRADE_KINDS),
                            },
                        },
                        "payment_gold": PAYMENT_GOLD,
                        "timing": "up_front",
                        "within": UPFRONT_WITHIN,
                    },
                ),
            )
        if phase == PHASE_ACCEPT_UPFRONT and role == ROLE_CLI:
            return (
                (
                    "respond_to_deal",
                    {"deal_id": data["upfront_deal_id"], "accept": True},
                ),
            )
        if phase == PHASE_FUND_UPFRONT and role == ROLE_API:
            return (("fund_deal", {"deal_id": data["upfront_deal_id"]}),)
        if phase == PHASE_ACCEPT_UPFRONT_PAYMENT and role == ROLE_CLI:
            return (
                (
                    "respond_to_payment",
                    {"deal_id": data["upfront_deal_id"], "accept": True},
                ),
            )
        if phase == PHASE_PROPOSE_ON_DELIVERY and role == ROLE_CLI:
            return (
                (
                    "propose_deal",
                    {
                        "to_player": api,
                        "text": ON_DELIVERY_PROPOSAL_TEXT,
                        "favor": {
                            "term_type": "maintain_gold_reserve",
                            "params": {"min_gold": MIN_GOLD},
                        },
                        "payment_gold": PAYMENT_GOLD,
                        "timing": "on_delivery",
                        "within": ON_DELIVERY_WITHIN,
                    },
                ),
            )
        if phase == PHASE_ACCEPT_ON_DELIVERY and role == ROLE_API:
            return (
                (
                    "respond_to_deal",
                    {"deal_id": data["on_delivery_deal_id"], "accept": True},
                ),
            )
        return ()

    @staticmethod
    def _canonical_args(args: dict) -> str:
        return json.dumps(args, sort_keys=True, separators=(",", ":"))

    def _record_action_plan(self, payload: dict) -> bool:
        """Persist a new plan, or validate an identical recovery replay."""

        source_id = payload["source_id"]
        pending = tuple(self._journal.state.pending_actions)
        for entry in pending:
            if entry["source_id"] == source_id:
                if any(
                    entry.get(key) != value
                    for key, value in payload.items()
                ):
                    self._fail(
                        f"pending action {source_id} does not match its recovery plan"
                    )
                    return False
                return True
        if any(
            entry["source_id"] == source_id
            for entry in self._journal.state.verified_actions
        ):
            return True
        same_slot = [
            entry
            for entry in pending
            if entry["player_id"] == payload["player_id"]
            and entry["turn"] == payload["turn"]
            and entry["phase"] == payload["phase"]
            and entry["name"] == payload["name"]
        ]
        if same_slot:
            self._fail(
                f"reissued action source mismatch: expected "
                f"{same_slot[0]['source_id']}, got {source_id}"
            )
            return False
        self._journal.append("action_planned", payload)
        return True

    def _emit_api(self, admission, player_id, turn, phase, plans) -> None:
        """Write ahead, then stage through the bound production API context."""

        context = admission.context
        for name, args in plans:
            canonical = self._canonical_args(args)
            digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
            index = len(context.staged_actions)
            source_id = (
                f"api:{self.config.run_id}:{player_id}:{turn}:{index}:{digest}"
            )
            if not self._record_action_plan(
                {
                    "turn": turn,
                    "player_id": player_id,
                    "phase": phase,
                    "name": name,
                    "source_id": source_id,
                    "payload_digest": digest,
                }
            ):
                return
            queued = context.dispatch(name, args)
            if source_id not in queued:
                self._fail(
                    f"dispatch source mismatch: planned {source_id}, got {queued!r}"
                )
                return

    def _emit_cli(self, base_result, player_id, turn, phase, plans) -> dict:
        """Write ahead and return exact lines for production CLI parsing."""

        summary = ""
        actions = []
        if isinstance(base_result, dict):
            actions = base_result.get("actions", [])
            transcript = base_result.get("transcript")
            if isinstance(transcript, dict) and isinstance(
                transcript.get("final_summary"), str
            ):
                summary = transcript["final_summary"]
            elif isinstance(base_result.get("summary"), str):
                summary = base_result["summary"]
        lines = summary.splitlines()
        for name, args in plans:
            payload = {"action": name, **args}
            line = "CHANNEL " + json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            )
            line_index = len(lines)
            digest = hashlib.sha256(
                line.encode("utf-8", errors="surrogatepass")
            ).hexdigest()[:16]
            source_id = (
                f"cli:{self.config.run_id}:{player_id}:{turn}:"
                f"{line_index}:{digest}"
            )
            if not self._record_action_plan(
                {
                    "turn": turn,
                    "player_id": player_id,
                    "phase": phase,
                    "name": name,
                    "source_id": source_id,
                    "payload_digest": digest,
                    "line_index": line_index,
                }
            ):
                return base_result
            lines.append(line)
        return {
            "summary": summary or "live-gate deterministic turn",
            "actions": actions,
            "transcript": {"steps": [], "final_summary": "\n".join(lines)},
        }

    async def after_seat_capture(self, *, player_id, turn, channel_fields) -> None:
        try:
            await self._after_seat_capture_inner(
                player_id=player_id,
                turn=turn,
                channel_fields=channel_fields,
            )
        except Exception as exc:
            self._fail(
                f"after_seat_capture seat {player_id} turn {turn}: {exc!r}"
            )

    async def _after_seat_capture_inner(
        self, *, player_id, turn, channel_fields
    ) -> None:
        if player_id not in self.gate_pids or self._signal is not None:
            return
        self._admissions.pop((player_id, turn), None)
        self._captured_this_turn.setdefault(turn, set()).add(player_id)
        if channel_fields.get("error"):
            self._fail(
                f"channel finish error for seat {player_id} turn {turn}: "
                f"{channel_fields['error']}"
            )
            return
        if not self._verify_planned_actions(player_id, turn):
            return
        if not self._check_no_unexpected_acknowledgements(player_id, turn):
            return
        self._advance_after_capture(player_id, turn)
        if self._signal is None:
            await self._maybe_finish_round(turn)

    def _verify_planned_actions(self, player_id, turn) -> bool:
        pending = [
            entry
            for entry in self._journal.state.pending_actions
            if entry["player_id"] == player_id and entry["turn"] == turn
        ]
        for entry in pending:
            matches = [
                acknowledgement
                for acknowledgement in self._runtime.state.acknowledgements
                if acknowledgement.source_id == entry["source_id"]
            ]
            if len(matches) != 1:
                self._fail(
                    f"expected exactly one acknowledgement for planned "
                    f"{entry['source_id']}, got {len(matches)}"
                )
                return False
            acknowledgement = matches[0]
            if acknowledgement.status != "applied":
                self._fail(
                    f"acknowledgement for {entry['source_id']} is "
                    f"{acknowledgement.status!r}: {acknowledgement.message}"
                )
                return False
            self._journal.append(
                "action_verified",
                {
                    "source_id": entry["source_id"],
                    "turn": turn,
                    "player_id": entry["player_id"],
                    "phase": entry["phase"],
                    "name": entry["name"],
                    "deal_id": acknowledgement.deal_id,
                },
            )
        return True

    def _check_no_unexpected_acknowledgements(self, player_id, turn) -> bool:
        verified = {
            entry["source_id"] for entry in self._journal.state.verified_actions
        }
        for acknowledgement in self._runtime.state.acknowledgements:
            if (
                acknowledgement.player_id != player_id
                or acknowledgement.turn != turn
            ):
                continue
            if acknowledgement.source_id not in verified:
                self._fail(
                    f"unexpected channel acknowledgement "
                    f"{acknowledgement.source_id} from seat {player_id} "
                    f"turn {turn}"
                )
                return False
        return True

    def _deal(self, deal_id: str):
        for deal in self._runtime.state.deals:
            if deal.id == deal_id:
                return deal
        self._fail(f"deal {deal_id!r} is missing from canonical state")
        return None

    def _advance_after_capture(self, player_id, turn) -> None:
        from civ_mcp.arena.channels import DealState, FavorStatus, PaymentStatus

        state = self._journal.state
        phase = state.phase
        role = self.pid_role[player_id]
        if phase == PHASE_CANARY_AND_UPFRONT_PROPOSAL and role == ROLE_API:
            acknowledgement_by_source = {
                acknowledgement.source_id: acknowledgement
                for acknowledgement in self._runtime.state.acknowledgements
            }
            verified = [
                entry
                for entry in state.verified_actions
                if entry["turn"] == turn
                and entry["source_id"].startswith(
                    f"api:{self.config.run_id}:{player_id}:{turn}:"
                )
            ]
            deal_ids = [
                acknowledgement_by_source[entry["source_id"]].deal_id
                for entry in verified
                if acknowledgement_by_source[entry["source_id"]].deal_id
            ]
            if len(deal_ids) != 1:
                self._fail(
                    f"expected exactly one captured up-front deal id, got "
                    f"{deal_ids}"
                )
                return
            canary_sources = [
                entry["source_id"]
                for entry in verified
                if not acknowledgement_by_source[entry["source_id"]].deal_id
            ]
            if not any(
                message.text == self.canary
                for message in self._runtime.state.messages
            ):
                self._fail("canary message is missing from canonical state")
                return
            self._journal.append(
                "data_recorded",
                {
                    "data": {
                        "upfront_deal_id": deal_ids[0],
                        "canary_message_source": (
                            canary_sources[0] if canary_sources else ""
                        ),
                    }
                },
            )
            self._journal.append(
                "phase_advanced",
                {"phase": PHASE_ACCEPT_UPFRONT, "turn": turn},
            )
        elif phase == PHASE_ACCEPT_UPFRONT and role == ROLE_CLI:
            deal = self._deal(state.data["upfront_deal_id"])
            if deal is None:
                return
            if (
                deal.state is not DealState.ACTIVE
                or deal.payment_status is not PaymentStatus.DUE
            ):
                self._fail(
                    "up-front deal not active/payment-due after acceptance: "
                    f"{deal.state}/{deal.payment_status}"
                )
                return
            self._journal.append(
                "phase_advanced", {"phase": PHASE_FUND_UPFRONT, "turn": turn}
            )
        elif phase == PHASE_FUND_UPFRONT and role == ROLE_API:
            deal = self._deal(state.data["upfront_deal_id"])
            if deal is None:
                return
            if deal.payment_status is not PaymentStatus.OFFERED:
                self._fail(f"up-front payment not offered: {deal.payment_status}")
                return
            fingerprint = {
                "payer": deal.proposer,
                "payee": deal.counterparty,
                "gold": deal.payment_gold,
                "duration": 0,
                "item_count": 1,
            }
            self._journal.append(
                "data_recorded",
                {"data": {"upfront_payment_fingerprint": fingerprint}},
            )
            self._restart_armed = True
            self._journal.append(
                "phase_advanced",
                {"phase": PHASE_RESTART_REQUIRED, "turn": turn},
            )
        elif phase == PHASE_ACCEPT_UPFRONT_PAYMENT and role == ROLE_CLI:
            deal = self._deal(state.data["upfront_deal_id"])
            if deal is None:
                return
            if deal.payment_status is not PaymentStatus.SETTLED:
                self._fail(
                    f"up-front payment not settled: {deal.payment_status}"
                )
                return
            baseline = deal.favor.baseline
            if not baseline or any(
                key.endswith("baseline_complete") and value is not True
                for key, value in baseline.items()
            ):
                self._fail("up-front favor baseline is missing or incomplete")
                return
            self._journal.append(
                "data_recorded",
                {"data": {"upfront_favor_due_turn": deal.favor_due_turn}},
            )
            self._journal.append(
                "phase_advanced",
                {
                    "phase": PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE,
                    "turn": turn,
                },
            )
        elif phase == PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE:
            deal = self._deal(state.data["upfront_deal_id"])
            if deal is None:
                return
            due = state.data.get("upfront_favor_due_turn")
            actual = (deal.state, deal.favor_status, deal.payment_status)
            pending = (
                DealState.ACTIVE,
                FavorStatus.DUE,
                PaymentStatus.SETTLED,
            )
            completed = (
                DealState.HONORED,
                FavorStatus.SATISFIED,
                PaymentStatus.SETTLED,
            )
            if turn < due:
                if actual != pending:
                    self._fail(
                        f"up-front deal state/status drift {actual} before its "
                        f"inclusive deadline turn {due}; expected {pending}"
                    )
                return
            if turn > due:
                self._fail(
                    f"up-front deal is {actual} after its inclusive deadline "
                    f"turn {due}; expected {completed}"
                )
                return
            if actual == completed:
                self._journal.append(
                    "phase_advanced",
                    {"phase": PHASE_VERIFY_UPFRONT_HONORED, "turn": turn},
                )
                self._advance_after_capture(player_id, turn)
                return
            if actual != pending:
                self._fail(
                    f"up-front deal invalid at inclusive deadline turn {due}: "
                    f"{actual}; expected {pending} or {completed}"
                )
                return
            if player_id == deal.counterparty:
                self._fail(
                    f"up-front deal missed its inclusive deadline turn {due} "
                    f"after responsible player {deal.counterparty} capture; "
                    f"expected {completed}, got {actual}"
                )
        elif phase == PHASE_VERIFY_UPFRONT_HONORED:
            deal = self._deal(state.data["upfront_deal_id"])
            if deal is None:
                return
            if (
                deal.state is not DealState.HONORED
                or deal.favor_status is not FavorStatus.SATISFIED
            ):
                self._fail(
                    f"up-front deal not honored: "
                    f"{deal.state}/{deal.favor_status}"
                )
                return
            self._journal.append(
                "phase_advanced",
                {"phase": PHASE_PROPOSE_ON_DELIVERY, "turn": turn},
            )
        elif phase == PHASE_PROPOSE_ON_DELIVERY and role == ROLE_CLI:
            acknowledgements = {
                acknowledgement.source_id: acknowledgement
                for acknowledgement in self._runtime.state.acknowledgements
            }
            source_prefix = (
                f"cli:{self.config.run_id}:{player_id}:{turn}:"
            )
            current = [
                acknowledgements[entry["source_id"]]
                for entry in state.verified_actions
                if entry["source_id"].startswith(source_prefix)
                and entry.get("player_id") == player_id
                and entry.get("turn") == turn
                and entry.get("phase") == phase
                and entry.get("name") == "propose_deal"
                and entry["source_id"] in acknowledgements
                and acknowledgements[entry["source_id"]].player_id == player_id
                and acknowledgements[entry["source_id"]].turn == turn
                and acknowledgements[entry["source_id"]].status == "applied"
                and acknowledgements[entry["source_id"]].deal_id
                and acknowledgements[entry["source_id"]].deal_id
                != state.data["upfront_deal_id"]
            ]
            if len(current) != 1:
                self._fail(
                    "expected exactly one applied current-capture on-delivery "
                    f"deal acknowledgement, got {len(current)}"
                )
                return
            deal_id = current[0].deal_id
            self._journal.append(
                "data_recorded", {"data": {"on_delivery_deal_id": deal_id}}
            )
            self._journal.append(
                "phase_advanced",
                {"phase": PHASE_ACCEPT_ON_DELIVERY, "turn": turn},
            )
        elif phase == PHASE_ACCEPT_ON_DELIVERY and role == ROLE_API:
            deal = self._deal(state.data["on_delivery_deal_id"])
            if deal is None:
                return
            if deal.state is not DealState.ACTIVE:
                self._fail(
                    f"on-delivery deal not active after acceptance: {deal.state}"
                )
                return
            baseline = deal.favor.baseline
            if not baseline or any(
                key.endswith("baseline_complete") and value is not True
                for key, value in baseline.items()
            ):
                self._fail(
                    "on-delivery treasury baseline is missing or incomplete"
                )
                return
            self._journal.append(
                "data_recorded",
                {"data": {"on_delivery_favor_due_turn": deal.favor_due_turn}},
            )
            self._journal.append(
                "phase_advanced",
                {"phase": PHASE_AWAIT_ON_DELIVERY_FAVOR, "turn": turn},
            )
        elif phase == PHASE_AWAIT_ON_DELIVERY_FAVOR:
            deal = self._deal(state.data["on_delivery_deal_id"])
            if deal is None:
                return
            due = state.data.get("on_delivery_favor_due_turn")
            actual = (deal.state, deal.favor_status, deal.payment_status)
            pending = (
                DealState.ACTIVE,
                FavorStatus.DUE,
                PaymentStatus.NOT_DUE,
            )
            completed = (
                DealState.ACTIVE,
                FavorStatus.SATISFIED,
                PaymentStatus.DUE,
            )
            if turn < due:
                if actual != pending:
                    self._fail(
                        f"on-delivery deal state/status drift {actual} before "
                        f"its inclusive deadline turn {due}; expected {pending}"
                    )
                return
            if turn > due:
                self._fail(
                    f"on-delivery deal is {actual} after its inclusive "
                    f"deadline turn {due}; expected {completed}"
                )
                return
            if actual == completed:
                self._journal.append(
                    "data_recorded",
                    {"data": {"on_delivery_fund_by_turn": deal.fund_by_turn}},
                )
                self._journal.append(
                    "phase_advanced",
                    {
                        "phase": PHASE_WITHHOLD_ON_DELIVERY_FUNDING,
                        "turn": turn,
                    },
                )
                return
            if actual != pending:
                self._fail(
                    f"on-delivery deal invalid at inclusive deadline turn "
                    f"{due}: {actual}; expected {pending} or {completed}"
                )
                return
            if player_id == deal.counterparty:
                self._fail(
                    f"on-delivery favor missed its inclusive deadline turn "
                    f"{due} after responsible player {deal.counterparty} "
                    f"capture; expected {completed}, got {actual}"
                )
        elif phase == PHASE_WITHHOLD_ON_DELIVERY_FUNDING:
            deal = self._deal(state.data["on_delivery_deal_id"])
            if deal is None:
                return
            fund_by = state.data.get("on_delivery_fund_by_turn")
            actual = (deal.state, deal.favor_status, deal.payment_status)
            pending = (
                DealState.ACTIVE,
                FavorStatus.SATISFIED,
                PaymentStatus.DUE,
            )
            completed = (
                DealState.BROKEN,
                FavorStatus.SATISFIED,
                PaymentStatus.FAILED,
            )
            if turn < fund_by:
                if actual != pending:
                    self._fail(
                        f"on-delivery funding state/status drift {actual} "
                        f"before the inclusive funding deadline turn "
                        f"{fund_by}; expected {pending}"
                    )
                return
            if turn > fund_by:
                self._fail(
                    f"on-delivery deal is {actual} after its inclusive funding "
                    f"deadline turn {fund_by}; expected {completed}"
                )
                return
            if actual == completed:
                self._journal.append(
                    "phase_advanced",
                    {"phase": PHASE_VERIFY_FUNDING_BREACH, "turn": turn},
                )
                self._advance_after_capture(player_id, turn)
                return
            if actual != pending:
                self._fail(
                    f"on-delivery deal invalid at inclusive funding deadline "
                    f"turn {fund_by}: {actual}; expected {pending} or "
                    f"{completed}"
                )
                return
            if player_id == deal.proposer:
                self._fail(
                    f"on-delivery funding missed its inclusive deadline turn "
                    f"{fund_by} after responsible player {deal.proposer} "
                    f"capture; expected {completed}, got {actual}"
                )
        elif phase == PHASE_VERIFY_FUNDING_BREACH:
            deal = self._deal(state.data["on_delivery_deal_id"])
            if deal is None:
                return
            if (
                deal.state is not DealState.BROKEN
                or deal.payment_status is not PaymentStatus.FAILED
            ):
                self._fail(
                    f"breach not canonical: {deal.state}/{deal.payment_status}"
                )
                return
            grievances = [
                grievance
                for grievance in self._runtime.state.grievances
                if grievance.deal_id == deal.id
            ]
            if len(grievances) != 1:
                self._fail(
                    "expected exactly one deterministic grievance, got "
                    f"{len(grievances)}"
                )
                return
            grievance = grievances[0]
            if (
                grievance.offender != deal.proposer
                or grievance.wronged != deal.counterparty
            ):
                self._fail(
                    f"grievance mapping wrong: offender {grievance.offender} "
                    f"wronged {grievance.wronged}; expected proposer "
                    f"{deal.proposer} offender / counterparty "
                    f"{deal.counterparty} wronged"
                )
                return
            self._journal.append(
                "phase_advanced",
                {"phase": PHASE_VERIFY_TERMINAL_GATE, "turn": turn},
            )

    def _round_complete(self, turn: int) -> bool:
        return self._captured_this_turn.get(turn, set()) >= self.gate_pids

    async def _maybe_finish_round(self, turn: int) -> None:
        """Run round-boundary restart or terminal verification work."""

        if not self._round_complete(turn):
            return
        if (
            self._restart_armed
            and self._journal.state.phase == PHASE_RESTART_REQUIRED
        ):
            await self._request_restart(turn)
        if self._journal.state.phase == PHASE_VERIFY_TERMINAL_GATE:
            self._verify_terminal_evidence(turn)

    def _verify_terminal_evidence(self, turn: int) -> None:
        from civ_mcp.arena.channels import DealState, PaymentStatus

        journal = self._journal
        runtime = self._runtime
        assert journal is not None and runtime is not None
        state = journal.state
        channel_state = runtime.state
        by_id = {deal.id: deal for deal in channel_state.deals}
        upfront = by_id.get(state.data.get("upfront_deal_id"))
        broken = by_id.get(state.data.get("on_delivery_deal_id"))
        checks = (
            (
                upfront is not None and upfront.state is DealState.HONORED,
                "an honored deal",
            ),
            (
                upfront is not None
                and upfront.payment_status is PaymentStatus.SETTLED,
                "a settled payment",
            ),
            (
                broken is not None and broken.state is DealState.BROKEN,
                "a broken deal",
            ),
            (bool(channel_state.grievances), "a deterministic grievance"),
        )
        for ok, label in checks:
            if not ok:
                self._fail(f"terminal evidence missing: {label}")
                return
        if upfront is None or broken is None:
            self._fail("terminal evidence deal lookup failed")
            return
        for pid in (self.role_pid[ROLE_API], self.role_pid[ROLE_CLI]):
            projection = runtime.project_for_player(pid, turn)
            if not any(
                message.text == self.canary for message in projection.messages
            ):
                self._fail(
                    f"canary absent from authorized player {pid} projection — "
                    "the canary was not actually exercised"
                )
                return
        expected_kinds = set(PRIVACY_ARTIFACT_KINDS)
        observer = self.role_pid[ROLE_OBSERVER]
        by_capture: dict[tuple[int, int], list[Mapping]] = {}
        invalid_capture_identity = False
        for assertion in state.privacy_assertions:
            asserted_player = assertion.get("player_id")
            asserted_turn = assertion.get("turn")
            if not isinstance(asserted_player, int) or not isinstance(
                asserted_turn, int
            ):
                invalid_capture_identity = True
                continue
            key = (asserted_player, asserted_turn)
            by_capture.setdefault(key, []).append(assertion)
        expected_captures = minimum_captures(self.config) // len(self.gate_pids)
        complete = (
            not invalid_capture_identity
            and len(by_capture) == expected_captures
            and all(player_id == observer for player_id, _turn in by_capture)
            and all(
                len(assertions) == len(expected_kinds)
                and {
                    assertion.get("artifact_kind") for assertion in assertions
                }
                == expected_kinds
                and all(
                    assertion.get("result") == "PASS"
                    for assertion in assertions
                )
                for assertions in by_capture.values()
            )
        )
        if not complete:
            self._fail("terminal evidence missing: complete observer privacy coverage")
            return
        journal.append(
            "gate_passed",
            {
                "evidence": {
                    "honored_deal": upfront.id,
                    "broken_deal": broken.id,
                    "grievances": len(channel_state.grievances),
                    "privacy_assertions": len(state.privacy_assertions),
                }
            },
        )
        journal.write_result()
        self._signal = GATE_PASSED

    async def _request_restart(self, turn: int) -> None:
        api = self.role_pid[ROLE_API]
        cli = self.role_pid[ROLE_CLI]
        recorded = self._journal.state.data.get("upfront_payment_fingerprint")
        if not recorded:
            self._fail(
                "restart requested without a recorded payment fingerprint"
            )
            return
        live = await self._live_offer_fingerprint(api, cli)
        if live is None:
            return
        if live != recorded:
            self._fail(
                f"live pending trade fingerprint {live} does not equal the "
                f"recorded canonical fingerprint {recorded}"
            )
            return
        channel_state = self._runtime.state
        self._journal.append(
            "data_recorded",
            {
                "data": {
                    "restart_channel_sequence": channel_state.last_event_sequence,
                    "restart_offer_fingerprint_before": live,
                    "restart_turn": turn,
                }
            },
        )
        self._journal.append("restart_required", {"turn": turn})
        self._journal.write_result()
        self._restart_armed = False
        self._signal = GATE_RESTART_REQUIRED

    async def _live_offer_fingerprint(
        self, payer: int, payee: int
    ) -> dict | None:
        payment_state = await self._gs.get_channel_payment_state(
            payer, payee, PAYMENT_GOLD
        )
        status = getattr(payment_state, "status", None)
        status = getattr(status, "value", status)
        if status != "exact":
            self._fail(
                f"official pending trade for ({payer},{payee}) is {status!r}; "
                "expected exactly one exact offer"
            )
            return None
        offer = getattr(payment_state, "offer", None)
        try:
            fingerprint = offer.fingerprint()
        except Exception as exc:
            self._fail(f"live offer fingerprint unavailable: {exc!r}")
            return None
        return fingerprint

    def _recover_pending_actions(self, player_id: int, current_turn: int) -> None:
        """Reconcile durable plans with canonical acknowledgements on resume."""

        pending = tuple(self._journal.state.pending_actions)
        recovered_captures: set[tuple[int, int]] = set()
        missing: list[dict] = []
        for entry in pending:
            matches = [
                acknowledgement
                for acknowledgement in self._runtime.state.acknowledgements
                if acknowledgement.source_id == entry["source_id"]
            ]
            if len(matches) > 1:
                self._fail(
                    f"duplicate recovered acknowledgements for {entry['source_id']}"
                )
                return
            if not matches:
                missing.append(entry)
                continue
            acknowledgement = matches[0]
            if acknowledgement.status != "applied":
                self._fail(
                    f"recovered acknowledgement for {entry['source_id']} is "
                    f"{acknowledgement.status!r}"
                )
                return
            self._journal.append(
                "action_verified",
                {
                    "source_id": entry["source_id"],
                    "turn": entry["turn"],
                    "player_id": entry["player_id"],
                    "phase": entry["phase"],
                    "name": entry["name"],
                    "deal_id": acknowledgement.deal_id,
                    "recovered": True,
                },
            )
            recovered_captures.add((entry["player_id"], entry["turn"]))
        for entry in missing:
            if (
                entry["player_id"] != player_id
                or entry["turn"] != current_turn
            ):
                self._fail(
                    f"planned action {entry['source_id']} cannot be reissued: "
                    f"bound seat/turn is ({entry['player_id']}, {entry['turn']}), "
                    f"current is ({player_id}, {current_turn})"
                )
                return
        if not missing:
            for recovered_player, recovered_turn in sorted(recovered_captures):
                if not self._check_no_unexpected_acknowledgements(
                    recovered_player, recovered_turn
                ):
                    return
                self._advance_after_capture(recovered_player, recovered_turn)
                if self._signal is not None:
                    return

    async def _run_preflight(self, gs, turn) -> None:
        api = self.role_pid[ROLE_API]
        cli = self.role_pid[ROLE_CLI]
        checks = (
            (api, frozenset({ObservationFamily.TREASURY})),
            (
                cli,
                frozenset(
                    {ObservationFamily.TREASURY, ObservationFamily.TRADE_ROUTES}
                ),
            ),
        )
        summaries = []
        for pid, families in checks:
            request = ObservationRequest(families=families)
            observed = await gs.get_channel_observation(pid, turn, request)
            if observed.errors:
                self._fail(
                    f"preflight observation error for player {pid}: {observed.errors}"
                )
                return
            missing = families - observed.families_present
            if missing:
                self._fail(
                    f"preflight missing observation families for player {pid}: "
                    f"{sorted(f.value for f in missing)}"
                )
                return
            summaries.append(
                {
                    "player_id": pid,
                    "families": sorted(f.value for f in families),
                    "treasury_gold": observed.treasury_gold,
                    "route_count": len(observed.trade_routes),
                }
            )
        if summaries[0]["treasury_gold"] < PAYMENT_GOLD:
            self._fail(
                f"preflight: player {api} gold {summaries[0]['treasury_gold']} "
                f"cannot fund the fixed {PAYMENT_GOLD}-gold official payment"
            )
            return
        payment_state = await gs.get_channel_payment_state(
            api, cli, PAYMENT_GOLD
        )
        status = getattr(payment_state, "status", None)
        status = getattr(status, "value", status)
        if status != "absent":
            self._fail(
                f"preflight: pending official trade for pair ({api},{cli}) is "
                f"{status!r}; linkage would be ambiguous"
            )
            return
        self._journal.append(
            "observation_recorded",
            {
                "kind": "preflight",
                "turn": turn,
                "players": summaries,
                "payment_pair_status": "absent",
            },
        )
        self._journal.append(
            "phase_advanced",
            {"phase": PHASE_CANARY_AND_UPFRONT_PROPOSAL, "turn": turn},
        )


register_scenario(
    ScenarioMeta(
        name=SCENARIO_NAME,
        revision=SCENARIO_REVISION,
        role_contracts=ROLE_CONTRACTS,
        minimum_captures=minimum_captures,
        create_driver=ChannelsCoreDriver,
    )
)
