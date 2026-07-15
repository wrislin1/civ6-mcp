"""Pure seat-0 logic core: per-turn phase machine, blocker-authority boundary,
mechanical cleanup, recovery save, and policy-attempt merge.

This module owns zero strategic judgment. It never chooses research, civics,
production, promotions, policies, governors, beliefs, envoys, dedications,
World Congress votes, city-capture outcomes, stacked-unit moves, or spy
escape routes -- that authority belongs entirely to the arena policy. The
only cleanup performed here is: finishing already-ordered unit moves,
dismissing a stale notification once Lua proves its underlying choice is
already set, and acknowledging a purely informational prompt.

Execution context is load-bearing and mirrors the plan's global
constraints: `query_blockers` and the notification-cleanup calls in
`apply_mechanical_cleanup` use `execute_write` (InGame) because
NotificationManager's blocking state is only meaningful there; the
UNITS branch reuses `hook.finish_units`, which stays on `execute_read`
(GameCore) as `hook.py` already implements it.

No coordinator imports and no polling loops live here -- this is pure logic
plus thin async helpers over a connection the coordinator (Tasks 5-7) owns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import civ_mcp.arena.hook as hook
import civ_mcp.lua as lq
from civ_mcp.game_lifecycle import save_game


class Seat0Phase(StrEnum):
    READY = "ready"
    POLICY_PLAYED = "policy_played"
    END_FIRED = "end_fired"
    AI_PROCESSING = "ai_processing"
    ADVANCED = "advanced"
    HUMAN_PENDING = "human_pending"
    INTERRUPTED = "interrupted"


class Seat0Poll(StrEnum):
    """Poll outcome, distinct from `phase`: what the coordinator should do
    next, not what state seat 0 is in."""

    WAIT = "wait"
    RECHECK = "recheck"
    ADVANCED = "advanced"
    DEGRADED = "degraded"


# Grace polls allowed after an end-turn request before the coordinator is
# told to recheck blockers instead of continuing to wait quietly.
_GRACE_POLL_LIMIT = 5

# End-turn requests allowed for a single admitted turn before the
# coordinator must stop re-firing and escalate instead.
_MAX_END_TURN_REQUESTS = 3


@dataclass(frozen=True)
class Seat0ResumeContext:
    policy: object
    caps: dict | None
    exclusive: bool


@dataclass
class Seat0TurnState:
    """Per-turn phase machine for one admitted seat-0 turn.

    A single instance tracks exactly one turn from `admit()` through a
    terminal outcome (`advanced`, `human_pending`, or `interrupted`).
    `reset()` returns it to `ready` for the next admission and is only
    valid once the phase has actually advanced.
    """

    phase: Seat0Phase = Seat0Phase.READY
    turn: int | None = None
    repair_used: bool = False
    end_turn_requests: int = 0
    grace_polls: int = 0
    critical_emitted: bool = False
    record: dict | None = None
    record_written: bool = False
    resume_context: Seat0ResumeContext | None = None

    def can_admit(self, *, turn: int, seat0_active: bool) -> bool:
        return self.phase is Seat0Phase.READY and seat0_active

    @property
    def needs_drain(self) -> bool:
        return self.phase in {
            Seat0Phase.POLICY_PLAYED,
            Seat0Phase.END_FIRED,
            Seat0Phase.AI_PROCESSING,
            Seat0Phase.HUMAN_PENDING,
        }

    @property
    def may_fire_end_turn(self) -> bool:
        return self.end_turn_requests < _MAX_END_TURN_REQUESTS

    def admit(self, turn: int) -> None:
        self.turn = turn

    def mark_policy_played(self) -> None:
        self.phase = Seat0Phase.POLICY_PLAYED

    def mark_end_fired(self) -> None:
        # Increment before any dispatch happens (the coordinator calls this
        # immediately before awaiting hook.end_turn) so an exception during
        # the request itself cannot create an unbounded retry loop.
        self.end_turn_requests += 1
        self.grace_polls = 0
        self.phase = Seat0Phase.END_FIRED

    def mark_human_pending(self) -> None:
        self.phase = Seat0Phase.HUMAN_PENDING

    def mark_interrupted(self) -> None:
        self.phase = Seat0Phase.INTERRUPTED

    def mark_critical_emitted(self) -> bool:
        """Idempotent latch for the one structured CRITICAL event per hard
        stop. Returns True the first time (caller should emit the event)
        and False on every later call, so a re-poll of the same terminal
        turn never logs it twice."""
        if self.critical_emitted:
            return False
        self.critical_emitted = True
        return True

    def observe(self, *, turn: int, seat0_active: bool) -> Seat0Poll:
        """Advance the phase machine for one poll and report what the
        coordinator should do. A strictly newer turn is the authoritative
        advance signal, independent of local phase."""
        if self.turn is None or turn < 0 or turn < self.turn:
            return Seat0Poll.DEGRADED
        if turn > self.turn:
            self.phase = Seat0Phase.ADVANCED
            return Seat0Poll.ADVANCED

        if self.phase is Seat0Phase.END_FIRED:
            if not seat0_active:
                self.phase = Seat0Phase.AI_PROCESSING
                return Seat0Poll.WAIT
            self.grace_polls += 1
            if self.grace_polls > _GRACE_POLL_LIMIT:
                return Seat0Poll.RECHECK
            return Seat0Poll.WAIT

        # AI_PROCESSING / HUMAN_PENDING / any other same-turn phase: keep
        # polling GameCore quietly until the turn number itself changes.
        return Seat0Poll.WAIT

    def reset(self) -> None:
        """Return to READY for the next admission. Only valid once the
        phase has actually advanced -- resetting a live turn would let the
        coordinator silently drop an in-flight policy/repair/save attempt."""
        if self.phase is not Seat0Phase.ADVANCED:
            raise RuntimeError(
                "Seat0TurnState.reset() is only valid after the phase has "
                f"advanced; current phase is {self.phase!r}"
            )
        self.phase = Seat0Phase.READY
        self.turn = None
        self.repair_used = False
        self.end_turn_requests = 0
        self.grace_polls = 0
        self.critical_emitted = False
        self.record = None
        self.record_written = False
        self.resume_context = None


# ---------------------------------------------------------------------------
# Blocker authority boundary
# ---------------------------------------------------------------------------

AUTOMATION_FAILURE_TYPE = "ARENA_SEAT0_AUTOMATION_FAILURE"


def automation_failure_blocker(stage: str, error: str) -> dict:
    return {
        "type": AUTOMATION_FAILURE_TYPE,
        "message": f"Seat-0 automation failed during {stage}: {error}",
    }


# Closed list: the only blocker types this module ever resolves itself, and
# only mechanically (finish already-ordered moves; acknowledge a purely
# informational prompt). Every other type is a strategic choice.
MECHANICAL_BLOCKERS = frozenset({
    "ENDTURN_BLOCKING_UNITS",
    "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK",
})

# Decision-blocker types whose lingering *notification* may still be
# mechanically dismissed once Task 2's state-proving Lua confirms the
# underlying choice is already set. The choice itself is still the
# policy's -- these never move to `MECHANICAL_BLOCKERS`.
_STALE_CLEARABLE = frozenset({
    "ENDTURN_BLOCKING_RESEARCH",
    "ENDTURN_BLOCKING_CIVIC",
    "ENDTURN_BLOCKING_PRODUCTION",
})

# Explicit hard types: no pilot-accessible resolver exists in the arena
# registry regardless of `lq.BLOCKING_TOOL_MAP` (a hint string alone is not
# a resolver). Any type absent from that map is hard for the same reason.
_HARD_BLOCKER_TYPES = frozenset({
    "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE",
    "UNKNOWN",
})


@dataclass
class BlockerGroups:
    mechanical: list[dict] = field(default_factory=list)
    decision: list[dict] = field(default_factory=list)
    hard: list[dict] = field(default_factory=list)


def classify_blockers(blockers: list[dict]) -> BlockerGroups:
    """Sort an ordered blocker snapshot into mechanical / decision / hard
    groups, preserving each group's relative order.

    Only `MECHANICAL_BLOCKERS` members are mechanical. A type with no
    registered resolution hint (or an explicit hard type such as the spy
    escape route) is a hard block -- there is no pilot-accessible way to
    resolve it, so it must go straight to human_pending. Everything else is
    a decision blocker the policy must resolve itself.
    """
    groups = BlockerGroups()
    for blocker in blockers:
        blocking_type = blocker["type"]
        if blocking_type in MECHANICAL_BLOCKERS:
            groups.mechanical.append(blocker)
        elif blocking_type in _HARD_BLOCKER_TYPES or blocking_type not in lq.BLOCKING_TOOL_MAP:
            groups.hard.append(blocker)
        else:
            groups.decision.append(blocker)
    return groups


def build_blocker_block(blockers: list[dict], *, prior_error: str = "") -> str:
    """Build the focused repair-prompt text: every still-open blocker with
    its registered resolution hint, the prior call's error when supplied,
    and an explicit reminder that this is the one repair pass and
    `end_turn` is not available to the policy.
    """
    lines = ["== END-TURN REPAIR =="]
    if prior_error:
        lines.append(f"Prior policy error: {prior_error}")
    if blockers:
        lines.append("Blockers still requiring your decision:")
        for blocker in blockers:
            hint = lq.BLOCKING_TOOL_MAP.get(
                blocker["type"], "No automated resolution hint is registered for this blocker."
            )
            lines.append(f"- [{blocker['type']}] {blocker['message']} -- {hint}")
    else:
        lines.append(
            "No blockers were detected, but the previous attempt did not "
            "complete. Inspect game state and finish the turn."
        )
    lines.append(
        "This is the one focused repair pass for this turn; end_turn is not "
        "available to you -- the coordinator ends the turn once you finish."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Async cleanup / autosave
# ---------------------------------------------------------------------------


async def query_blockers(conn) -> list[dict]:
    """Query every active EndTurnBlocking notification.

    InGame/`execute_write` only: NotificationManager's blocking state is
    only meaningful in that context while seat 0 is local and active.
    Returns an ordered list of `{"type": ..., "message": ...}`.
    """
    lines = await conn.execute_write(lq.build_end_turn_blocking_query())
    return [
        {"type": blocking_type, "message": message}
        for blocking_type, message in lq.parse_end_turn_blocking(lines)
    ]


async def apply_mechanical_cleanup(conn, blockers: list[dict]) -> list[dict]:
    """Apply only non-strategic cleanup for the given blocker snapshot.

    - `ENDTURN_BLOCKING_UNITS` -> `hook.finish_units(conn, 0)` (GameCore).
    - `ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE` /
      `ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK` -> acknowledge the purely
      informational prompt (InGame).
    - `ENDTURN_BLOCKING_RESEARCH` / `_CIVIC` / `_PRODUCTION` -> dismiss the
      stale notification only if Lua proves the underlying choice is
      already set; recorded as `NOT_SET` otherwise, never claimed cleared.
    - Any other type is left untouched -- it is a live strategic choice,
      not this module's to resolve.

    Returns an ordered list of cleanup records, one per blocker actually
    acted on.
    """
    cleanup: list[dict] = []
    for blocker in blockers:
        blocking_type = blocker["type"]
        if blocking_type == "ENDTURN_BLOCKING_UNITS":
            await hook.finish_units(conn, 0)
            cleanup.append({
                "type": blocking_type,
                "action": "finish_units",
                "result": "requested",
            })
        elif blocking_type in (
            "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE",
            "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK",
        ):
            lines = await conn.execute_write(lq.build_mark_end_turn_prompt_seen(blocking_type))
            result = "PROMPT_SEEN" if any("PROMPT_SEEN" in ln for ln in lines) else "UNCONFIRMED"
            cleanup.append({
                "type": blocking_type,
                "action": "acknowledge_informational",
                "result": result,
            })
        elif blocking_type in _STALE_CLEARABLE:
            lines = await conn.execute_write(lq.build_clear_stale_end_turn_blocker(blocking_type))
            result = "STALE_CLEARED" if any("STALE_CLEARED" in ln for ln in lines) else "NOT_SET"
            cleanup.append({
                "type": blocking_type,
                "action": "clear_stale_notification",
                "result": result,
            })
        # else: a live decision/hard blocker -- not this function's concern.
    return cleanup


async def save_recovery_anchor(conn, turn: int) -> dict:
    """Best-effort `0_MCP_NNNN` recovery save before the end-turn request.

    Never raises: not gated on any host operating-system check (WSL may be
    fronting a Windows-hosted game, so checking which OS Python itself runs
    on would silently skip the save on the exact host that needs it), and a
    save failure is returned as structured failure data rather than
    propagated -- this must never block turn progression.
    """
    name = f"0_MCP_{turn:04d}"
    try:
        result = await save_game(conn, name)
        return {"name": name, "ok": True, "result": result}
    except Exception as exc:  # best-effort: failure is data, not a raise
        return {"name": name, "ok": False, "error": repr(exc)}


# ---------------------------------------------------------------------------
# Policy-result merge
# ---------------------------------------------------------------------------

_TRANSCRIPT_SUM_FIELDS = ("prompt_tokens", "completion_tokens", "wall_clock_s")
_USAGE_SUM_FIELDS = ("prompt_tokens", "completion_tokens", "usd")


def merge_policy_attempts(normal: dict | None, repair: dict | None) -> dict:
    """Merge a normal seat-0 policy attempt with its optional one-shot
    repair attempt into a single generic policy payload for the arena
    transcript pipeline.

    - The first available transcript (normal, then repair) is the metadata
      base for every non-summed field (civ_options, briefing_*, n_ctx, ...).
    - `steps` and `invalid_tool_calls` concatenate, normal first.
    - Token/usage/wall-clock numeric fields sum across both attempts.
    - The merged transcript's `final_summary` is the normal attempt's when
      present; the repair's own summary is recorded separately by the
      caller under `seat0.repair` and is not folded in here.

    Neither input dict (nor its nested `transcript`/`usage`) is mutated.
    """
    normal = normal or {}
    repair = repair or {}
    normal_t = normal.get("transcript") or {}
    repair_t = repair.get("transcript") or {}

    base_t = normal_t if normal_t else repair_t
    merged_t = dict(base_t)
    merged_t["steps"] = list(normal_t.get("steps") or []) + list(repair_t.get("steps") or [])
    merged_t["invalid_tool_calls"] = (
        list(normal_t.get("invalid_tool_calls") or [])
        + list(repair_t.get("invalid_tool_calls") or [])
    )
    for f in _TRANSCRIPT_SUM_FIELDS:
        merged_t[f] = (normal_t.get(f) or 0) + (repair_t.get(f) or 0)
    if "final_summary" in normal_t:
        merged_t["final_summary"] = normal_t["final_summary"]
    elif "final_summary" in repair_t:
        merged_t["final_summary"] = repair_t["final_summary"]

    merged: dict = {
        "summary": normal.get("summary", repair.get("summary", "")),
        "actions": list(normal.get("actions") or []) + list(repair.get("actions") or []),
        "transcript": merged_t,
    }

    normal_usage = normal.get("usage") or {}
    repair_usage = repair.get("usage") or {}
    if normal_usage or repair_usage:
        usage = dict(normal_usage) if normal_usage else dict(repair_usage)
        for f in _USAGE_SUM_FIELDS:
            usage[f] = (normal_usage.get(f) or 0) + (repair_usage.get(f) or 0)
        merged["usage"] = usage

    return merged
