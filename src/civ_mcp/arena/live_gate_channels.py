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
SCENARIO_REVISION = 3

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
_PUBLIC_FAILURE_CODES = frozenset(
    {
        "acknowledgement_missing",
        "acknowledgement_rejected",
        "action_recovery_failed",
        "admission_failed",
        "channel_finish_failed",
        "gate_invariant_failed",
        "observer_capture_failed",
        "official_payment_not_enacted",
        "official_payment_unexpectedly_pending",
        "payment_checkpoint_failed",
        "payment_state_failed",
        "preflight_failed",
        "privacy_assertion_failed",
        "restart_checkpoint_failed",
        "restart_verification_failed",
        "unexpected_acknowledgement",
    }
)


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

    Revision 2 handshake rounds: R1 canary+propose+accept, R2 fund +
    same-round payment settlement (restart boundary) — 2 rounds. Then
    UPFRONT_WITHIN rounds to the up-front favor's inclusive deadline (the
    first post-resume round), 2 rounds for the on-delivery proposal +
    acceptance, ON_DELIVERY_WITHIN rounds to its favor deadline, then
    funding_turns withheld rounds through the inclusive funding deadline.
    8 rounds x 3 seats = 24 with the checked-in rules.
    """

    rounds = (
        2
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
        self._payment_fingerprint: dict | None = None

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
        if state.capture_turn is not None:
            self._captured_this_turn[state.capture_turn] = set(
                state.captured_players
            )
        if state.status in (GATE_FAILED, GATE_PASSED):
            raise RuntimeError(f"gate already terminal: {state.status} ({state.reason})")
        if not await self._reconcile_payment_checkpoint():
            return
        if self._journal.state.capture_started_turn is None:
            await self._reconcile_verified_capture(allow_settlement_read=False)
            if self._signal is not None:
                return
        if self._journal.state.capture_started_turn is not None:
            await self._reconcile_started_capture()
            if self._signal is not None:
                return
        state = self._journal.state
        if state.status == GATE_RESTART_REQUIRED:
            await self._verify_restart()
        elif state.phase == PHASE_RESTART_REQUIRED:
            self._restart_armed = (
                self._journal.state.data.get("settlement_digest") is not None
            )
            if state.capture_turn is not None and self._round_complete(
                state.capture_turn
            ):
                await self._request_restart(state.capture_turn)

    def policy_for(self, player_id: int) -> _GateSeatPolicy:
        return self._policies[player_id]

    def note_admission(self, player_id, turn, admission, error) -> None:
        if self._signal is not None or player_id not in self.gate_pids:
            return
        if admission is None:
            self._fail(
                "admission_failed",
                detail={"player_id": player_id, "turn": turn, "error": error},
            )
            return
        if self._journal.state.pending_actions and (
            player_id,
            turn,
        ) not in self._admissions:
            try:
                self._recover_pending_actions(player_id, turn)
            except Exception as exc:
                # A reducer/journal invariant raised mid-recovery (e.g. round
                # monotonicity on the recovered started append) must still
                # honor the durable-failure contract: sanitized gate_failed +
                # result.json, then the coordinator's pending_signal check
                # performs the same safe deactivation as any admission
                # failure.
                self._fail(
                    "action_recovery_failed",
                    detail={
                        "failure": "recovery_exception",
                        "player_id": player_id,
                        "turn": turn,
                        "error": repr(exc),
                    },
                )
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
                "reason": "gate_not_attached",
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
                "observer_capture_failed",
                detail={"turn": turn, "failure": "missing_pending_capture"},
            )
            return False
        if record.get("player_id") != player_id or record.get("turn") != turn:
            self._fail(
                "observer_capture_failed",
                detail={
                    "turn": turn,
                    "failure": "pending_identity_mismatch",
                    "player_id": player_id,
                    "record_player_id": record.get("player_id"),
                    "record_turn": record.get("turn"),
                },
            )
            return False
        admission, policy_result = capture
        try:
            self._observer_assertions(admission, policy_result, record, turn)
        except Exception as exc:
            self._fail(
                "privacy_assertion_failed",
                detail={"turn": turn, "failure": "inspection_error", "error": repr(exc)},
            )
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
        fingerprint = self._payment_fingerprint
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
        fingerprint = self._payment_fingerprint
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
            "privacy_assertion_failed",
            detail={"turn": turn, "failure": "forbidden_observer_artifact"},
        )

    @staticmethod
    def _digest_mapping(value: Mapping) -> str:
        encoded = json.dumps(
            ChannelsCoreDriver._jsonable(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def _update_payment_checkpoint(self, **updates) -> None:
        checkpoint = self._journal.read_private_json(
            "payment_checkpoint.json", required=False
        ) or {}
        checkpoint.update(updates)
        self._journal.write_private_json("payment_checkpoint.json", checkpoint)
        recorded = checkpoint.get("recorded")
        if isinstance(recorded, Mapping):
            self._payment_fingerprint = dict(recorded)

    async def _reconcile_payment_checkpoint(self) -> bool:
        """Repair either half of the private/public payment checkpoint pair."""

        journal = self._journal
        assert journal is not None
        state = journal.state
        public_digest = state.data.get("payment_checkpoint_digest")
        try:
            checkpoint = journal.read_private_json(
                "payment_checkpoint.json", required=False
            )
        except Exception as exc:
            self._fail(
                "payment_checkpoint_failed",
                detail={"failure": "private_checkpoint_unreadable", "error": repr(exc)},
            )
            return False
        recorded = None if checkpoint is None else checkpoint.get("recorded")
        if checkpoint is not None and not isinstance(recorded, Mapping):
            self._fail(
                "payment_checkpoint_failed",
                detail={"failure": "private_checkpoint_invalid", "checkpoint": checkpoint},
            )
            return False
        if public_digest is not None and (
            not isinstance(public_digest, str) or len(public_digest) != 16
        ):
            self._fail(
                "payment_checkpoint_failed",
                detail={"failure": "public_digest_invalid", "digest": public_digest},
            )
            return False
        if recorded is None and public_digest is None:
            return True
        if recorded is not None and public_digest is not None:
            try:
                # json.loads accepts NaN/Infinity, which the allow_nan=False
                # digest dump then rejects; a hostile-but-parseable sidecar
                # must fail closed here, not raise past the reconciler.
                recorded_digest = self._digest_mapping(recorded)
            except Exception as exc:
                self._fail(
                    "payment_checkpoint_failed",
                    detail={
                        "failure": "private_checkpoint_invalid",
                        "checkpoint": checkpoint,
                        "error": repr(exc),
                    },
                )
                return False
            if recorded_digest != public_digest:
                self._fail(
                    "payment_checkpoint_failed",
                    detail={
                        "failure": "checkpoint_pair_mismatch",
                        "recorded": recorded,
                        "digest": public_digest,
                    },
                )
                return False
            self._payment_fingerprint = dict(recorded)
            return True

        deal_id = state.data.get("upfront_deal_id")
        if not isinstance(deal_id, str):
            self._fail(
                "payment_checkpoint_failed",
                detail={"failure": "missing_upfront_deal_id"},
            )
            return False
        deal = self._deal(deal_id)
        if deal is None:
            return False
        canonical = {
            "payer": deal.proposer,
            "payee": deal.counterparty,
            "gold": deal.payment_gold,
            "duration": 0,
            "item_count": 1,
        }
        canonical_digest = self._digest_mapping(canonical)
        if recorded is not None:
            if dict(recorded) != canonical:
                self._fail(
                    "payment_checkpoint_failed",
                    detail={
                        "failure": "private_checkpoint_channel_mismatch",
                        "recorded": recorded,
                        "canonical": canonical,
                    },
                )
                return False
            if not self._record_data_once(
                {"payment_checkpoint_digest": canonical_digest},
                reason_code="payment_checkpoint_failed",
                failure="payment_checkpoint_digest_mismatch",
            ):
                return False
        else:
            if public_digest != canonical_digest:
                self._fail(
                    "payment_checkpoint_failed",
                    detail={
                        "failure": "public_checkpoint_channel_mismatch",
                        "digest": public_digest,
                        "canonical": canonical,
                    },
                )
                return False
            repaired_checkpoint = {"recorded": canonical}
            settlement_digest = state.data.get("settlement_digest")
            if settlement_digest is not None:
                repaired_checkpoint["settlement_digest"] = settlement_digest
            journal.write_private_json(
                "payment_checkpoint.json", repaired_checkpoint
            )
        self._payment_fingerprint = dict(canonical)
        return True

    def _fail(self, reason_code: str, *, detail=None) -> None:
        if self._signal == GATE_FAILED:
            return
        self._signal = GATE_FAILED
        if self._journal is not None and self._journal.state.status not in (
            GATE_FAILED,
            GATE_PASSED,
        ):
            if reason_code not in _PUBLIC_FAILURE_CODES:
                if detail is None:
                    detail = {"message": reason_code}
                reason_code = "gate_invariant_failed"
            if detail is not None:
                sequence = self._journal.state.last_event_sequence + 1
                try:
                    self._journal.write_private_json(
                        f"failure_{sequence:06d}_{reason_code}.json",
                        {
                            "reason_code": reason_code,
                            "detail": self._jsonable(detail),
                        },
                    )
                except Exception:
                    # Forensics are best effort. Their failure must neither leak
                    # the private diagnostic nor prevent the sanitized terminal
                    # event/result from becoming durable.
                    pass
            self._journal.append("gate_failed", {"reason": reason_code})
            self._journal.write_result()

    async def _verify_restart(self) -> None:
        """Reconcile the persisted restart checkpoint with live state.

        ChannelRuntime.open has already replayed its journal before attach()
        reaches this method. Only durable settlement evidence, an absent
        official offer, and an untouched post-checkpoint channel journal may
        reactivate the gate.
        """

        state = self._journal.state
        if state.restart_count != 1:
            self._fail(
                "restart_verification_failed",
                detail={
                    "failure": "restart_count",
                    "actual": state.restart_count,
                    "expected": 1,
                },
            )
            return
        checkpoint_sequence = state.data.get("restart_channel_sequence")
        live_sequence = self._runtime.state.last_event_sequence
        if (
            type(checkpoint_sequence) is not int
            or live_sequence != checkpoint_sequence
        ):
            self._fail(
                "restart_verification_failed",
                detail={
                    "failure": "channel_sequence_mismatch",
                    "live": live_sequence,
                    "recorded": checkpoint_sequence,
                },
            )
            return
        from civ_mcp.arena.channels import PaymentStatus

        deal_id = state.data.get("upfront_deal_id")
        if not isinstance(deal_id, str):
            self._fail(
                "restart_verification_failed",
                detail={"failure": "missing_upfront_deal_id"},
            )
            return
        deal = self._deal(deal_id)
        if deal is None:
            return
        if deal.payment_status is not PaymentStatus.SETTLED:
            self._fail(
                "restart_verification_failed",
                detail={
                    "failure": "payment_not_settled_at_resume",
                    "payment_status": str(deal.payment_status),
                },
            )
            return
        recorded = self._payment_fingerprint
        digest = state.data.get("settlement_digest")
        try:
            recomputed = self._digest_mapping(
                {
                    "fingerprint": recorded,
                    "baseline": state.data.get("settlement_baseline"),
                    "result": state.data.get("settlement_result"),
                }
            )
        except Exception as exc:
            self._fail(
                "restart_verification_failed",
                detail={
                    "failure": "settlement_evidence_invalid",
                    "error": repr(exc),
                },
            )
            return
        if not digest or recomputed != digest:
            self._fail(
                "restart_verification_failed",
                detail={
                    "failure": "settlement_digest_mismatch",
                    "recorded": digest,
                    "recomputed": recomputed,
                },
            )
            return
        try:
            payment_state = await self._gs.get_channel_payment_state(
                self.role_pid[ROLE_API], self.role_pid[ROLE_CLI], PAYMENT_GOLD
            )
        except Exception as exc:
            self._fail(
                "restart_verification_failed",
                detail={
                    "failure": "payment_state_unreadable",
                    "error": repr(exc),
                },
            )
            return
        status = getattr(payment_state, "status", None)
        status = getattr(status, "value", status)
        if status != "absent":
            self._fail(
                "restart_verification_failed",
                detail={"failure": "stray_official_offer", "status": status},
            )
            return
        restart_turn = state.data.get("restart_turn")
        if type(restart_turn) is not int:
            self._fail(
                "restart_verification_failed",
                detail={"failure": "invalid_restart_turn"},
            )
            return
        settlement_source_ids = {
            entry["source_id"]
            for entry in state.verified_actions
            if entry.get("name") == "respond_to_payment"
            and entry.get("player_id") == self.role_pid[ROLE_CLI]
            and entry.get("turn", restart_turn + 1) <= restart_turn
        }
        settled_acks = [
            acknowledgement
            for acknowledgement in self._runtime.state.acknowledgements
            if acknowledgement.source_id in settlement_source_ids
            and acknowledgement.deal_id == deal_id
            and acknowledgement.player_id == self.role_pid[ROLE_CLI]
            and acknowledgement.turn <= restart_turn
        ]
        if len(settled_acks) != 1:
            self._fail(
                "restart_verification_failed",
                detail={
                    "failure": "settlement_acknowledgement_count",
                    "count": len(settled_acks),
                },
            )
            return
        response_acks = [
            acknowledgement
            for acknowledgement in self._runtime.state.acknowledgements
            if acknowledgement.deal_id == deal_id
            and acknowledgement.player_id == self.role_pid[ROLE_CLI]
            and acknowledgement.turn > restart_turn
        ]
        if response_acks:
            self._fail(
                "restart_verification_failed",
                detail={
                    "failure": "payment_response_already_acknowledged",
                    "acknowledgements": response_acks,
                },
            )
            return
        self._journal.append(
            "restart_verified",
            {
                "turn": state.data.get("restart_turn"),
                "next_phase": PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE,
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
        await self._reconcile_verified_capture(allow_settlement_read=True)
        if self._signal is not None:
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
            if (
                phase == PHASE_FUND_UPFRONT
                and "settlement_baseline" not in self._journal.state.data
            ):
                baseline = await self._read_settlement_treasuries(gs, turn)
                if baseline is None:
                    return base_result
                if not self._record_data_once(
                    {"settlement_baseline": baseline},
                    reason_code="payment_checkpoint_failed",
                    failure="settlement_baseline_mismatch",
                ):
                    return base_result
            self._emit_api(admission, player_id, turn, phase, plans)
            return base_result
        if role == ROLE_CLI and phase == PHASE_ACCEPT_UPFRONT_PAYMENT:
            deal = self._deal(self._journal.state.data["upfront_deal_id"])
            if deal is None:
                return base_result
            from civ_mcp.arena.channels import PaymentStatus

            if deal.payment_status is not PaymentStatus.SETTLED:
                state = self._journal.state
                if "pre_acceptance_payment_status" in state.data:
                    status = state.data["pre_acceptance_payment_status"]
                else:
                    try:
                        payment_state = await gs.get_channel_payment_state(
                            self.role_pid[ROLE_API],
                            self.role_pid[ROLE_CLI],
                            PAYMENT_GOLD,
                        )
                    except Exception as exc:
                        self._fail(
                            "payment_state_failed",
                            detail={
                                "failure": (
                                    "pre_acceptance_payment_state_unreadable"
                                ),
                                "error": repr(exc),
                            },
                        )
                        return base_result
                    status = getattr(payment_state, "status", None)
                    status = getattr(status, "value", status)
                    if not self._record_data_once(
                        {"pre_acceptance_payment_status": status},
                        reason_code="payment_state_failed",
                        failure="pre_acceptance_payment_status_mismatch",
                    ):
                        return base_result
                if status != "exact":
                    self._fail(
                        "official_payment_auto_resolved",
                        detail={
                            "payer": self.role_pid[ROLE_API],
                            "payee": self.role_pid[ROLE_CLI],
                            "status": status,
                        },
                    )
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
        if channel_fields.get("error"):
            self._fail(
                "channel_finish_failed",
                detail={
                    "player_id": player_id,
                    "turn": turn,
                    "error": channel_fields["error"],
                },
            )
            return
        if not self._verify_planned_actions(player_id, turn):
            return
        if not self._check_no_unexpected_acknowledgements(player_id, turn):
            return
        journal = self._journal
        assert journal is not None
        if not await self._record_settlement_result(player_id, turn):
            return
        journal.append(
            "seat_capture_started",
            {
                "turn": turn,
                "player_id": player_id,
                "expected_player_ids": sorted(self.gate_pids),
                "phase": journal.state.phase,
            },
        )
        await self._reconcile_started_capture()

    async def _record_settlement_result(self, player_id: int, turn: int) -> bool:
        journal = self._journal
        assert journal is not None
        if (
            self.pid_role.get(player_id) != ROLE_API
            or journal.state.phase != PHASE_FUND_UPFRONT
        ):
            return True
        if (
            "settlement_result" in journal.state.data
            and "post_send_payment_status" in journal.state.data
        ):
            return True
        result = await self._read_settlement_treasuries(self._gs, turn)
        if result is None:
            return False
        try:
            payment_state = await self._gs.get_channel_payment_state(
                self.role_pid[ROLE_API],
                self.role_pid[ROLE_CLI],
                PAYMENT_GOLD,
            )
        except Exception as exc:
            self._fail(
                "payment_state_failed",
                detail={
                    "failure": "post_send_payment_state_unreadable",
                    "error": repr(exc),
                },
            )
            return False
        status = getattr(payment_state, "status", None)
        status = getattr(status, "value", status)
        return self._record_data_once(
            {"settlement_result": result, "post_send_payment_status": status},
            reason_code="payment_checkpoint_failed",
            failure="settlement_result_or_post_send_status_mismatch",
        )

    async def _reconcile_verified_capture(
        self, *, allow_settlement_read: bool
    ) -> None:
        state = self._journal.state
        identities = {
            (entry.get("player_id"), entry.get("turn"))
            for entry in state.verified_actions
            if entry.get("phase") == state.phase
        }
        identities = {
            (player_id, turn)
            for player_id, turn in identities
            if type(player_id) is int
            and type(turn) is int
            and not any(
                entry.get("player_id") == player_id
                and entry.get("turn") == turn
                and entry.get("phase") == state.phase
                for entry in state.pending_actions
            )
            and not (
                state.capture_turn == turn
                and player_id in state.captured_players
            )
        }
        if not identities:
            return
        if len(identities) != 1:
            self._fail(
                "action_recovery_failed",
                detail={
                    "failure": "ambiguous_verified_capture",
                    "identities": sorted(identities),
                },
            )
            return
        player_id, turn = next(iter(identities))
        if not self._check_no_unexpected_acknowledgements(player_id, turn):
            return
        needs_settlement_read = (
            self.pid_role.get(player_id) == ROLE_API
            and state.phase == PHASE_FUND_UPFRONT
            and (
                "settlement_result" not in state.data
                or "post_send_payment_status" not in state.data
            )
        )
        if needs_settlement_read and not allow_settlement_read:
            return
        if not await self._record_settlement_result(player_id, turn):
            return
        try:
            self._journal.append(
                "seat_capture_started",
                {
                    "turn": turn,
                    "player_id": player_id,
                    "expected_player_ids": sorted(self.gate_pids),
                    "phase": self._journal.state.phase,
                },
            )
            await self._reconcile_started_capture()
        except Exception as exc:
            self._fail(
                "action_recovery_failed",
                detail={
                    "failure": "recovery_exception",
                    "player_id": player_id,
                    "turn": turn,
                    "error": repr(exc),
                },
            )

    async def _reconcile_started_capture(self) -> None:
        """Finish a write-ahead seat capture after a process crash."""

        journal = self._journal
        assert journal is not None
        state = journal.state
        turn = state.capture_started_turn
        player_id = state.capture_started_player
        original_phase = state.capture_started_phase
        if turn is None or player_id is None:
            return
        # Ordered phase_advanced chains one capture may journal from each
        # started phase. Single-advance phases have one-element chains; the
        # deadline-satisfaction and funding-breach boundaries are dual
        # advances (_advance_after_capture recurses once), so every non-final
        # chain element is a legitimate crash-between-hops state.
        successors = {
            PHASE_CANARY_AND_UPFRONT_PROPOSAL: (PHASE_ACCEPT_UPFRONT,),
            PHASE_ACCEPT_UPFRONT: (PHASE_FUND_UPFRONT,),
            PHASE_FUND_UPFRONT: (PHASE_ACCEPT_UPFRONT_PAYMENT,),
            PHASE_ACCEPT_UPFRONT_PAYMENT: (PHASE_RESTART_REQUIRED,),
            PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE: (
                PHASE_VERIFY_UPFRONT_HONORED,
                PHASE_PROPOSE_ON_DELIVERY,
            ),
            PHASE_VERIFY_UPFRONT_HONORED: (PHASE_PROPOSE_ON_DELIVERY,),
            PHASE_PROPOSE_ON_DELIVERY: (PHASE_ACCEPT_ON_DELIVERY,),
            PHASE_ACCEPT_ON_DELIVERY: (PHASE_AWAIT_ON_DELIVERY_FAVOR,),
            PHASE_AWAIT_ON_DELIVERY_FAVOR: (
                PHASE_WITHHOLD_ON_DELIVERY_FUNDING,
            ),
            PHASE_WITHHOLD_ON_DELIVERY_FUNDING: (
                PHASE_VERIFY_FUNDING_BREACH,
                PHASE_VERIFY_TERMINAL_GATE,
            ),
            PHASE_VERIFY_FUNDING_BREACH: (PHASE_VERIFY_TERMINAL_GATE,),
        }
        current_phase = state.phase
        chain = successors.get(original_phase, ())
        if current_phase == original_phase or current_phase in chain[:-1]:
            # Crash before the transition, or between the two hops of a dual
            # advance: re-run the remaining verification from the current
            # phase. This is read-only against the game — the advance
            # branches only read canonical channel state and append journal
            # events — and it re-journals exactly the phase_advanced events
            # the uncrashed capture would have written.
            self._advance_after_capture(player_id, turn)
            if self._signal is not None:
                return
        elif not chain or current_phase != chain[-1]:
            self._fail(
                "gate_invariant_failed",
                detail={
                    "failure": "started_capture_phase_mismatch",
                    "started_phase": original_phase,
                    "current_phase": current_phase,
                },
            )
            return
        journal.append(
            "seat_captured",
            {
                "turn": turn,
                "player_id": player_id,
                "expected_player_ids": list(
                    state.capture_started_expected_players
                ),
            },
        )
        self._captured_this_turn[turn] = set(
            journal.state.captured_players
        )
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
                    "acknowledgement_missing",
                    detail={
                        "source_id": entry["source_id"],
                        "match_count": len(matches),
                    },
                )
                return False
            acknowledgement = matches[0]
            if acknowledgement.status != "applied":
                self._fail(
                    "acknowledgement_rejected",
                    detail={
                        "source_id": entry["source_id"],
                        "status": acknowledgement.status,
                        "message": acknowledgement.message,
                    },
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
                    "unexpected_acknowledgement",
                    detail={
                        "source_id": acknowledgement.source_id,
                        "player_id": player_id,
                        "turn": turn,
                        "status": acknowledgement.status,
                        "message": acknowledgement.message,
                    },
                )
                return False
        return True

    def _deal(self, deal_id: str):
        for deal in self._runtime.state.deals:
            if deal.id == deal_id:
                return deal
        self._fail(f"deal {deal_id!r} is missing from canonical state")
        return None

    def _record_data_once(
        self,
        data: Mapping,
        *,
        reason_code: str = "gate_invariant_failed",
        failure: str = "durable_data_mismatch",
    ) -> bool:
        recorded = self._journal.state.data
        present = {key: key in recorded for key in data}
        if any(present.values()):
            mismatches = {
                key: {"recorded": recorded.get(key), "expected": value}
                for key, value in data.items()
                if key in recorded and recorded.get(key) != value
            }
            missing = [key for key, exists in present.items() if not exists]
            if mismatches or missing:
                self._fail(
                    reason_code,
                    detail={
                        "failure": failure,
                        "mismatches": mismatches,
                        "missing": missing,
                    },
                )
                return False
            return True
        self._journal.append("data_recorded", {"data": dict(data)})
        return True

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
            if not self._record_data_once(
                {
                    "upfront_deal_id": deal_ids[0],
                    "canary_message_source": (
                        canary_sources[0] if canary_sources else ""
                    ),
                }
            ):
                return
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
            self._update_payment_checkpoint(recorded=fingerprint)
            if not self._record_data_once(
                {
                    "payment_checkpoint_digest": self._digest_mapping(
                        fingerprint
                    )
                },
                reason_code="payment_checkpoint_failed",
                failure="payment_checkpoint_digest_mismatch",
            ):
                return
            data = state.data
            status = data.get("post_send_payment_status")
            baseline = data.get("settlement_baseline")
            result = data.get("settlement_result")
            recorded = self._payment_fingerprint
            gold = PAYMENT_GOLD
            baseline_payer_gold = (
                baseline.get("payer_gold")
                if isinstance(baseline, Mapping)
                else None
            )
            baseline_payee_gold = (
                baseline.get("payee_gold")
                if isinstance(baseline, Mapping)
                else None
            )
            result_payer_gold = (
                result.get("payer_gold")
                if isinstance(result, Mapping)
                else None
            )
            result_payee_gold = (
                result.get("payee_gold")
                if isinstance(result, Mapping)
                else None
            )
            ok = (
                status == "absent"
                and isinstance(baseline, Mapping)
                and isinstance(result, Mapping)
                and recorded is not None
                and baseline.get("turn") == turn
                and result.get("turn") == turn
                and type(baseline_payer_gold) is int
                and type(baseline_payee_gold) is int
                and type(result_payer_gold) is int
                and type(result_payee_gold) is int
                and result_payer_gold == baseline_payer_gold - gold
                and result_payee_gold == baseline_payee_gold + gold
            )
            if not ok:
                self._fail(
                    "official_payment_not_enacted",
                    detail={
                        "status": status,
                        "baseline": baseline,
                        "result": result,
                    },
                )
                return
            if not self._record_data_once(
                {
                    "settlement_digest": self._digest_mapping(
                        {
                            "fingerprint": recorded,
                            "baseline": baseline,
                            "result": result,
                        }
                    )
                },
                reason_code="payment_checkpoint_failed",
                failure="settlement_digest_mismatch",
            ):
                return
            self._journal.append(
                "phase_advanced",
                {"phase": PHASE_ACCEPT_UPFRONT_PAYMENT, "turn": turn},
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
            if not self._record_data_once(
                {"upfront_favor_due_turn": deal.favor_due_turn},
                reason_code="payment_checkpoint_failed",
                failure="upfront_favor_due_turn_mismatch",
            ):
                return
            if "settlement_digest" not in state.data:
                self._fail(
                    "payment_checkpoint_failed",
                    detail={"failure": "settlement_digest_missing_at_response"},
                )
                return
            self._update_payment_checkpoint(
                settlement_digest=state.data["settlement_digest"]
            )
            self._restart_armed = True
            self._journal.append(
                "phase_advanced",
                {"phase": PHASE_RESTART_REQUIRED, "turn": turn},
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
            if not self._record_data_once({"on_delivery_deal_id": deal_id}):
                return
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
            if not self._record_data_once(
                {"on_delivery_favor_due_turn": deal.favor_due_turn}
            ):
                return
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
                if not self._record_data_once(
                    {"on_delivery_fund_by_turn": deal.fund_by_turn}
                ):
                    return
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
            self._fail(
                "privacy_assertion_failed",
                detail={"failure": "incomplete_observer_privacy_coverage"},
            )
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
        data = self._journal.state.data
        recorded = self._payment_fingerprint
        baseline = data.get("settlement_baseline")
        result = data.get("settlement_result")
        digest = data.get("settlement_digest")
        if not recorded or not digest:
            self._fail(
                "restart_checkpoint_failed",
                detail={"failure": "missing_settlement_evidence"},
            )
            return
        try:
            recomputed = self._digest_mapping(
                {"fingerprint": recorded, "baseline": baseline, "result": result}
            )
        except Exception as exc:
            self._fail(
                "restart_checkpoint_failed",
                detail={
                    "failure": "settlement_evidence_invalid",
                    "error": repr(exc),
                },
            )
            return
        if recomputed != digest:
            self._fail(
                "restart_checkpoint_failed",
                detail={
                    "failure": "settlement_digest_mismatch",
                    "recorded": digest,
                    "recomputed": recomputed,
                },
            )
            return
        channel_state = self._runtime.state
        if not self._record_data_once(
            {
                "restart_channel_sequence": channel_state.last_event_sequence,
                "restart_turn": turn,
            },
            reason_code="restart_checkpoint_failed",
            failure="restart_metadata_mismatch",
        ):
            return
        self._journal.append("restart_required", {"turn": turn})
        self._journal.write_result()
        self._restart_armed = False
        self._signal = GATE_RESTART_REQUIRED

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
                    "action_recovery_failed",
                    detail={
                        "failure": "duplicate_acknowledgements",
                        "source_id": entry["source_id"],
                        "match_count": len(matches),
                    },
                )
                return
            if not matches:
                missing.append(entry)
                continue
            acknowledgement = matches[0]
            if acknowledgement.status != "applied":
                self._fail(
                    "acknowledgement_rejected",
                    detail={
                        "failure": "recovered_acknowledgement",
                        "source_id": entry["source_id"],
                        "status": acknowledgement.status,
                        "message": acknowledgement.message,
                    },
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
                    "action_recovery_failed",
                    detail={
                        "failure": "source_identity_cannot_recur",
                        "source_id": entry["source_id"],
                        "bound_player_id": entry["player_id"],
                        "bound_turn": entry["turn"],
                        "current_player_id": player_id,
                        "current_turn": current_turn,
                    },
                )
                return
        if not missing:
            for recovered_player, recovered_turn in sorted(recovered_captures):
                if not self._check_no_unexpected_acknowledgements(
                    recovered_player, recovered_turn
                ):
                    return

    async def _read_settlement_treasuries(self, gs, turn) -> dict | None:
        values = {}
        for key, pid in (
            ("payer_gold", self.role_pid[ROLE_API]),
            ("payee_gold", self.role_pid[ROLE_CLI]),
        ):
            request = ObservationRequest(
                families=frozenset({ObservationFamily.TREASURY})
            )
            observed = await gs.get_channel_observation(pid, turn, request)
            if observed.errors or (
                ObservationFamily.TREASURY not in observed.families_present
            ):
                self._fail(
                    "payment_state_failed",
                    detail={
                        "failure": "settlement_treasury_unreadable",
                        "player_id": pid,
                        "errors": list(observed.errors),
                    },
                )
                return None
            values[key] = observed.treasury_gold
        return {"turn": turn, **values}

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
                    "preflight_failed",
                    detail={
                        "failure": "observation_error",
                        "player_id": pid,
                        "errors": observed.errors,
                    },
                )
                return
            missing = families - observed.families_present
            if missing:
                self._fail(
                    "preflight_failed",
                    detail={
                        "failure": "missing_observation_families",
                        "player_id": pid,
                        "families": sorted(f.value for f in missing),
                    },
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
                "preflight_failed",
                detail={
                    "failure": "insufficient_treasury",
                    "player_id": api,
                    "treasury_gold": summaries[0]["treasury_gold"],
                    "required_gold": PAYMENT_GOLD,
                },
            )
            return
        payment_state = await gs.get_channel_payment_state(
            api, cli, PAYMENT_GOLD
        )
        status = getattr(payment_state, "status", None)
        status = getattr(status, "value", status)
        if status != "absent":
            self._fail(
                "preflight_failed",
                detail={
                    "failure": "ambiguous_pending_payment",
                    "payer": api,
                    "payee": cli,
                    "status": status,
                },
            )
            return
        self._journal.append(
            "observation_recorded",
            {
                "kind": "preflight",
                "turn": turn,
                "players": [
                    {
                        "player_id": summary["player_id"],
                        "families": summary["families"],
                        "observation_digest": hashlib.sha256(
                            self._json_text(summary, compact=True).encode("utf-8")
                        ).hexdigest()[:16],
                    }
                    for summary in summaries
                ],
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
