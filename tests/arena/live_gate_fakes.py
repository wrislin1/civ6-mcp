"""Shared fakes for live-gate tests: a typed game-state fake plus a
coordinator-shaped harness that drives a scenario driver through
admissions/finishes exactly the way run_arena does (admit -> note_admission
-> gate policy -> finish_player -> pending-transcript privacy hook ->
transcript write -> after_seat_capture -> signal check)."""

import dataclasses

from civ_mcp.arena.channel_terms import ChannelObservation, ObservationFamily
from civ_mcp.arena.transcript import TranscriptSink
from civ_mcp.lua.channel_payments import ExactPaymentOffer


def observation(player_id, turn, **changes):
    base = ChannelObservation(
        player_id=player_id,
        turn=turn,
        families_present=frozenset(ObservationFamily),
        treasury_gold=500,
    )
    return dataclasses.replace(base, **changes)


@dataclasses.dataclass(frozen=True)
class PaymentStateView:
    status: str
    offer: ExactPaymentOffer | None = None


class GateGameState:
    """Complete observations + synchronous payment enactment + the minimal
    overview/units surface ScriptedPolicy needs. Result strings mirror the
    live engine wrappers."""

    def __init__(self):
        self.active_player = 0
        self.game_turn = 0
        self.treasury = {1: 500, 2: 500, 3: 500}
        self.routes = {}
        self.missing_families = {}
        self.observation_errors = {}
        self.pending = {}
        self.skipped = 0

    async def get_game_overview(self):
        return "OV"

    async def get_current_game_turn(self):
        return self.game_turn

    async def get_units(self):
        return []

    async def skip_unit(self, index):
        self.skipped += 1
        return "SKIP"

    async def get_channel_observation(self, player_id, turn, request):
        present = frozenset(ObservationFamily) - self.missing_families.get(
            player_id, frozenset()
        )
        return observation(
            player_id,
            turn,
            families_present=present,
            treasury_gold=self.treasury.get(player_id, 500),
            trade_routes=self.routes.get(player_id, ()),
            errors=self.observation_errors.get(player_id, ()),
        )

    async def offer_channel_payment(self, payee, gold):
        # Observed engine truth (2026-07-20 lifecycle probe): an AI->AI
        # PROPOSED deal is enacted synchronously at send -- the gold moves
        # before the first observable poll and nothing is ever pending.
        payer = self.active_player
        if (payer, payee) in self.pending:
            return "Error: CHANNEL_PAYMENT_PENDING_DEAL"
        self.treasury[payer] -= gold
        self.treasury[payee] += gold
        return "CHANNEL_PAYMENT_PROPOSED"

    async def get_channel_payment_state(self, payer, payee, gold):
        pending = self.pending.get((payer, payee))
        expected = ExactPaymentOffer(payer, payee, gold)
        if pending is None:
            return PaymentStateView("absent")
        if pending == expected:
            return PaymentStateView("exact", expected)
        return PaymentStateView("conflicting")

async def run_gate_seat(
    driver, runtime, gs, pid, turn, *, pending_record_overrides=None
):
    """One coordinator-shaped capture for one gate seat."""
    gs.active_player = pid
    gs.game_turn = turn
    admission = await runtime.admit_player(gs, pid, turn)
    driver.note_admission(pid, turn, admission, "")
    if driver.pending_signal() is not None:
        return None
    policy = driver.policy_for(pid)
    result = await policy(gs, pid, turn)
    acknowledgements = await runtime.finish_player(gs, admission, result)
    transcript_payload = result.get("transcript") if isinstance(result, dict) else None
    if isinstance(transcript_payload, dict):
        record = {
            **transcript_payload,
            "schema_version": 1,
            "run_id": driver.config.run_id,
            "player_id": pid,
            "turn": turn,
            "provider": getattr(policy, "provider", "scripted"),
            "model": "",
            "driver": "scripted",
            "step_count": len(transcript_payload.get("steps", [])),
            "usd": 0.0,
            "turn_kind": "played",
        }
        if pending_record_overrides:
            record.update(pending_record_overrides)
        if driver.inspect_pending_transcript_record(pid, turn, record):
            TranscriptSink(str(driver._run_dir / "transcript.jsonl")).write(record)
    await driver.after_seat_capture(
        player_id=pid,
        turn=turn,
        channel_fields={
            "enabled": True,
            "acknowledgements": len(acknowledgements),
            "error": "",
        },
    )
    return result


async def run_gate_round(driver, runtime, gs, turn, seats=(1, 2, 3)):
    for pid in seats:
        if driver.pending_signal() is not None:
            return
        await run_gate_seat(driver, runtime, gs, pid, turn)
