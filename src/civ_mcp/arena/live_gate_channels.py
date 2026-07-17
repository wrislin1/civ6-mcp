"""unofficial_channels_core_v1 — the first registered live-gate scenario.

Design: docs/superpowers/specs/2026-07-17-arena-live-gate-driver-design.md

This module plans deterministic per-seat channel inputs and verifies every
transition against canonical ChannelRuntime state. It depends only on the
public channel runtime/protocol/term/projection/prompt interfaces; it never
calls apply_staged or parse_cli_channel_lines, never edits the channel
ledger, and never injects evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from civ_mcp.arena.channel_terms import ObservationFamily, ObservationRequest
from civ_mcp.arena.live_gate import (
    GATE_FAILED,
    GATE_PASSED,
    GATE_RESTART_REQUIRED,
    LiveGateJournal,
    ScenarioMeta,
    register_scenario,
)
from civ_mcp.arena.scripted_policy import ScriptedPolicy

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
        self._admissions: dict[tuple[int, int], object] = {}
        self._captured_this_turn: dict[int, set[int]] = {}
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
        raise NotImplementedError("Task 8")

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
        return base_result

    async def after_seat_capture(self, *, player_id, turn, channel_fields) -> None:
        if player_id not in self.gate_pids or self._signal is not None:
            return
        self._captured_this_turn.setdefault(turn, set()).add(player_id)
        self._admissions.pop((player_id, turn), None)

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
