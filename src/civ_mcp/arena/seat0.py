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
    REGRESSED = "regressed"
    HUMAN_PENDING = "human_pending"
    INTERRUPTED = "interrupted"


class Seat0Poll(StrEnum):
    """Poll outcome, distinct from `phase`: what the coordinator should do
    next, not what state seat 0 is in."""

    WAIT = "wait"
    RECHECK = "recheck"
    ADVANCED = "advanced"
    DEGRADED = "degraded"
    REGRESSED = "regressed"


# Grace polls allowed after an end-turn request before the coordinator is
# told to recheck blockers instead of continuing to wait quietly.
_GRACE_POLL_LIMIT = 5

# End-turn requests allowed for a single admitted turn before the
# coordinator must stop re-firing and escalate instead.
_MAX_END_TURN_REQUESTS = 3

# Consecutive VALID backward turn samples required before a rollback is
# declared. One sample may be a transient misread; a run of them means a
# human loaded an earlier save. Malformed polls (turn < 0) never count.
_REGRESSION_POLL_LIMIT = 3
# Consecutive same-turn ACTIVE polls while AI_PROCESSING before concluding the
# end request did not take after all (the phase was latched by a flickered
# inactive sample, or the request bounced on a late blocker) and returning to
# END_FIRED so the grace/recheck machinery can recover the turn.
_REACTIVATION_POLL_LIMIT = 3
# Bounded budget of "quiet" rechecks -- rechecks whose blocker query found
# nothing and therefore no proof the end request bounced. Each quiet recheck
# waits out one more grace window instead of re-firing (a duplicate
# ACTION_ENDTURN while the first is latent skips multiple turns); once spent,
# the coordinator escalates to human_pending.
_IDLE_RECHECK_LIMIT = 3


@dataclass(frozen=True)
class Seat0ResumeContext:
    policy: object
    caps: dict | None
    exclusive: bool


@dataclass
class Seat0TurnState:
    """Per-turn phase machine for one admitted seat-0 turn.

    A single instance tracks exactly one turn from `admit()` through a
    terminal outcome (`advanced`, `regressed`, `human_pending`, or
    `interrupted`). `reset()` returns it to `ready` for the next admission
    and is only valid once the phase has actually advanced or regressed.
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
    regression_polls: int = 0
    reactivation_polls: int = 0
    idle_rechecks: int = 0
    # One guarded refire per admitted turn (quiet-recheck exhaustion with no
    # blocker and no open session). Survives mark_end_fired so the refire
    # cannot re-arm itself; cleared only by reset().
    guarded_refire_used: bool = False

    def can_admit(self, *, turn: int, seat0_active: bool) -> bool:
        return self.phase is Seat0Phase.READY and seat0_active

    @property
    def needs_drain(self) -> bool:
        # REGRESSED is intentionally absent: observe() only runs while
        # needs_drain is true, and the coordinator terminalizes a REGRESSED
        # result synchronously on the same poll -- the phase never persists
        # across polls.
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
        self.reactivation_polls = 0
        self.idle_rechecks = 0
        self.phase = Seat0Phase.END_FIRED

    def note_idle_recheck(self) -> bool:
        """A RECHECK found no open blocker: there is no proof the previous
        end request bounced, so re-firing risks the engine's documented
        duplicate-ACTION_ENDTURN multi-turn skip. Restart the grace window
        instead and report whether the bounded quiet-recheck budget still
        allows waiting (False -> the coordinator escalates to
        human_pending)."""
        self.idle_rechecks += 1
        self.grace_polls = 0
        return self.idle_rechecks <= _IDLE_RECHECK_LIMIT

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
        advance signal; a PERSISTENTLY older valid turn is a rollback (a
        human loaded an earlier save) and terminalizes the turn as
        REGRESSED so the coordinator can re-admit at the older turn."""
        if self.turn is None or turn < 0:
            return Seat0Poll.DEGRADED
        if turn < self.turn:
            self.regression_polls += 1
            if self.regression_polls >= _REGRESSION_POLL_LIMIT:
                self.phase = Seat0Phase.REGRESSED
                return Seat0Poll.REGRESSED
            return Seat0Poll.DEGRADED
        self.regression_polls = 0
        if turn > self.turn:
            self.phase = Seat0Phase.ADVANCED
            return Seat0Poll.ADVANCED

        if self.phase is Seat0Phase.END_FIRED:
            if not seat0_active:
                self.phase = Seat0Phase.AI_PROCESSING
                self.reactivation_polls = 0
                return Seat0Poll.WAIT
            self.grace_polls += 1
            if self.grace_polls > _GRACE_POLL_LIMIT:
                return Seat0Poll.RECHECK
            return Seat0Poll.WAIT

        if self.phase is Seat0Phase.AI_PROCESSING:
            # AI_PROCESSING must not be absorbing: persistent same-turn
            # ACTIVE polls mean the end request did not take after all (the
            # inactive sample that latched this phase was a flicker, or the
            # request bounced on a late blocker). Return to END_FIRED with a
            # fresh grace window so the recheck machinery can recover the
            # turn. A single active sample is flicker-tolerant.
            if seat0_active:
                self.reactivation_polls += 1
                if self.reactivation_polls >= _REACTIVATION_POLL_LIMIT:
                    self.phase = Seat0Phase.END_FIRED
                    self.grace_polls = 0
                    self.reactivation_polls = 0
            else:
                self.reactivation_polls = 0
            return Seat0Poll.WAIT

        # HUMAN_PENDING / any other same-turn phase: keep polling GameCore
        # quietly until the turn number itself changes.
        return Seat0Poll.WAIT

    def reset(self) -> None:
        """Return to READY for the next admission. Only valid once the
        phase has actually advanced or regressed -- resetting a live turn
        would let the coordinator silently drop an in-flight
        policy/repair/save attempt."""
        if self.phase not in (Seat0Phase.ADVANCED, Seat0Phase.REGRESSED):
            raise RuntimeError(
                "Seat0TurnState.reset() is only valid after the phase has "
                f"advanced or regressed; current phase is {self.phase!r}"
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
        self.regression_polls = 0
        self.reactivation_polls = 0
        self.idle_rechecks = 0
        self.guarded_refire_used = False


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
# informational prompt; apply a resolution the solo end_turn path already
# auto-applies, like the fastest spy escape route). Every other type is a
# strategic choice. Full-LLM-control directive (riz 2026-07-15): anything the
# solo path auto-resolves belongs here; human_pending is only for blockers
# with no automation or tool path at all.
MECHANICAL_BLOCKERS = frozenset({
    "ENDTURN_BLOCKING_UNITS",
    "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK",
    "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE",
    "ENDTURN_BLOCKING_GOVERNOR_IDLE",
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

# The seat0-owned authority table: blocker types the pilot can resolve with
# arena tools, and therefore the only types worth spending the one-shot
# focused repair on. Deliberately NOT derived from `lq.BLOCKING_TOOL_MAP` --
# that map is a prompt-hint table maintained for end_turn error messages,
# and a hint string alone is not a resolver (adding a hint for an
# unresolvable blocker must never silently promote it to decision; the spy
# escape route already demonstrated that drift). Any type absent from this
# table AND from `MECHANICAL_BLOCKERS` is a hard block: it goes straight to
# human_pending. Unknown/future types default hard by construction.
DECISION_BLOCKERS = frozenset({
    "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT",
    "ENDTURN_BLOCKING_UNIT_PROMOTION",
    "ENDTURN_BLOCKING_FILL_CIVIC_SLOT",
    "ENDTURN_BLOCKING_PRODUCTION",
    "ENDTURN_BLOCKING_RESEARCH",
    "ENDTURN_BLOCKING_CIVIC",
    "ENDTURN_BLOCKING_PANTHEON",
    "ENDTURN_BLOCKING_STACKED_UNITS",
    "ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION",
    "ENDTURN_BLOCKING_CONSIDER_RAZE_CITY",
    "ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY",
    "ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN",
    "ENDTURN_BLOCKING_CLAIM_GREAT_PERSON",
})


@dataclass
class BlockerGroups:
    mechanical: list[dict] = field(default_factory=list)
    decision: list[dict] = field(default_factory=list)
    hard: list[dict] = field(default_factory=list)


def classify_blockers(blockers: list[dict]) -> BlockerGroups:
    """Sort an ordered blocker snapshot into mechanical / decision / hard
    groups, preserving each group's relative order.

    Only `MECHANICAL_BLOCKERS` members are mechanical, only
    `DECISION_BLOCKERS` members are decisions the policy must resolve
    itself, and everything else -- including unknown/future types and any
    blocker without a pilot-accessible resolver (spy escape route) -- is a
    hard block that goes straight to human_pending.
    """
    groups = BlockerGroups()
    for blocker in blockers:
        blocking_type = blocker["type"]
        if blocking_type in MECHANICAL_BLOCKERS:
            groups.mechanical.append(blocker)
        elif blocking_type in DECISION_BLOCKERS:
            groups.decision.append(blocker)
        else:
            groups.hard.append(blocker)
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


async def wc_handler_registered(conn) -> bool:
    """True when queue_wc_votes' Lua voter handler is registered (InGame).

    The World Congress opens and closes synchronously INSIDE ACTION_ENDTURN,
    so votes must be registered (as the `__civmcp_wc_handler` event handler)
    before the coordinator fires the end request -- the same contract the
    solo end_turn path enforces. Never raises.
    """
    try:
        lines = await conn.execute_write(
            f'print(__civmcp_wc_handler and "HANDLER_SET" or "NO_HANDLER") '
            f'print("{lq.SENTINEL}")'
        )
    except Exception:
        return False
    return any("HANDLER_SET" in ln for ln in lines)


async def register_default_wc_voter(conn) -> bool:
    """Register the default WC voting strategy (spread favor, option A).

    Full-LLM-control fallback: when the policy fails to queue votes during
    its focused pass, a default vote keeps the game moving -- stuck-free
    beats optimal. Never raises.
    """
    try:
        lines = await conn.execute_write(lq.build_register_wc_voter(None))
    except Exception:
        return False
    return any("OK:WC_VOTER_REGISTERED" in ln for ln in lines)


async def query_local_player_sessions(conn) -> str:
    """Report open diplomacy sessions involving the local (human) player.

    InGame/`execute_write` only. Returns the raw session list
    (`"<other>#<sid>,..."`) or `""` when none are open. Never raises: this is
    called from the coordinator's poll loop, where a transient connection
    error must read as "nothing detected", not kill the run.
    """
    try:
        lines = await conn.execute_write(lq.build_find_local_player_sessions())
    except Exception:
        return ""
    for line in lines:
        if line.startswith("LOCAL_SESSIONS|"):
            payload = line.split("|", 1)[1].strip().rstrip(",")
            return "" if payload == "none" else payload
    return ""


def build_diplomacy_block(sessions: str) -> str:
    """Build the focused prompt for a diplomacy-wedged seat 0.

    An AI civ has opened a deal/session with the human seat and the whole
    turn cycle is stopped until it is answered. Full-LLM-control: the pilot
    answers it with its own tools; the coordinator never auto-responds.
    """
    return "\n".join([
        "== PENDING DIPLOMACY ==",
        f"Another civilization is waiting on your diplomatic response "
        f"(open sessions: {sessions}). The game cannot proceed until you "
        f"answer.",
        "Use get_pending_diplomacy() and get_pending_trades() to inspect "
        "what is being proposed, then answer with respond_to_diplomacy(...) "
        "or respond_to_trade(...). Judge offers on their merits.",
        "This pass is only for answering the pending diplomacy; end_turn is "
        "not available to you and no other actions are needed -- the "
        "coordinator resumes normal play once the session closes.",
    ])


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
      `ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK` /
      `ENDTURN_BLOCKING_GOVERNOR_IDLE` -> acknowledge the purely
      informational prompt (InGame).
    - `ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE` -> apply the solo path's
      auto-resolution (fastest escape route, InGame).
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
            "ENDTURN_BLOCKING_GOVERNOR_IDLE",
        ):
            lines = await conn.execute_write(lq.build_mark_end_turn_prompt_seen(blocking_type))
            result = "PROMPT_SEEN" if any("PROMPT_SEEN" in ln for ln in lines) else "UNCONFIRMED"
            cleanup.append({
                "type": blocking_type,
                "action": "acknowledge_informational",
                "result": result,
            })
        elif blocking_type in (
            "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION",
            "ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION",
        ):
            # 'Resume Congress' shape: a 0-resolution session has no
            # strategic content and just needs submitting. The Lua proves
            # the 0-resolution condition atomically; a session with real
            # votes prints WC_HAS_RESOLUTIONS and stays with the policy.
            lines = await conn.execute_write(lq.build_wc_resume_submit())
            if any("WC_RESUMED" in ln for ln in lines):
                result = "WC_RESUMED"
            elif any("WC_HAS_RESOLUTIONS" in ln for ln in lines):
                result = "WC_HAS_RESOLUTIONS"
            else:
                result = "UNCONFIRMED"
            cleanup.append({
                "type": blocking_type,
                "action": "wc_resume_submit",
                "result": result,
            })
        elif blocking_type == "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE":
            # Same resolution the solo end_turn path applies (end_turn.py):
            # the spy takes the fastest escape route.
            lines = await conn.execute_write(lq.build_spy_escape_route())
            result = (
                "resolved"
                if any("OK:ESCAPE_ROUTE" in ln for ln in lines)
                else "UNCONFIRMED"
            )
            cleanup.append({
                "type": blocking_type,
                "action": "spy_escape_route",
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
        ok, result = await save_game(conn, name)
        return {"name": name, "ok": ok, "result": result}
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


def attempt_timeout_error(result) -> str:
    """Non-empty error string when a policy attempt returned the CLI
    timeout shape (``transcript.reason == "timeout"``) instead of raising.
    A timed-out attempt did zero usable work, so the coordinator must
    account it exactly like a raised attempt -- never as a completed one
    (a zero-step turn transcribed as `played` corrupts every played-turn
    metric and can fire an end request nobody earned)."""
    if not isinstance(result, dict):
        return ""
    transcript = result.get("transcript")
    if isinstance(transcript, dict) and transcript.get("reason") == "timeout":
        return str(result.get("summary") or "policy attempt timed out")
    return ""
