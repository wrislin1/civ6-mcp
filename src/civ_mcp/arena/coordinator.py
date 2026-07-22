from __future__ import annotations
import asyncio
import copy
import inspect
import sys
from dataclasses import replace as _dc_replace
from datetime import datetime, timezone
from pathlib import Path
from civ_mcp import lua as lq
from civ_mcp.arena import autoresolve, hook, seat0
from civ_mcp.arena.agent import load_playbook
from civ_mcp.arena.attention import (
    AttentionState,
    DIGEST_MAX_CHARS,
    Decision,
    build_attention_query,
    cancel_remainder,
    evaluate,
    has_directive_lines,
    load_attention_state,
    note_sleep,
    note_wake,
    parse_attention_scan,
    parse_directive,
    render_digest,
    save_attention_state,
    scan_scalars,
)
from civ_mcp.arena.budget import explicit_n_ctx
from civ_mcp.arena.capabilities import build_caps_query, parse_caps
from civ_mcp.arena.channel_runtime import ChannelRuntime
from civ_mcp.arena.channel_protocol import CHANNEL_ACTION_NAMES
from civ_mcp.arena.config import CivOptions, resolved_puppet_ids, validate_arena_config
from civ_mcp.arena.memory import (
    extract_standing_plan,
    format_memory_block,
    load_memory,
    save_memory,
)
from civ_mcp.arena.prompt_context import maybe_build_briefing
from civ_mcp.arena.scripted_policy import ScriptedPolicy
from civ_mcp.arena.seat0 import Seat0Phase, Seat0Poll, Seat0TurnState
from civ_mcp.arena.task_tracker import (
    format_task_block,
    load_task_state,
    merge_tasks,
    parse_task_lines,
    run_pre_model_tasks,
    save_task_state,
)


async def _reconnect_with_retry(conn, attempts=5, delay=0.5):
    last = None
    for i in range(attempts):
        try:
            # Close any half-open writer from a prior failed attempt before reconnecting,
            # so repeated tries do not leak a socket/fd (connect() reassigns the writer).
            await conn.disconnect()
            await conn.connect(); return True
        except Exception as e:
            last = e
            if i < attempts - 1:          # no point sleeping after the final failed attempt
                await asyncio.sleep(delay)
    print(f"[arena] WARNING: reclaim connect failed after {attempts} attempts: {last!r}", file=sys.stderr)
    return False


_STATE_DELTA_NUM_FIELDS = ("score", "gold", "science", "culture", "faith", "cities", "units")


def _state_delta(state_before, state_after):
    """The only transcript delta contract for slept, puppet, and seat-0 records.

    Return numeric before→after deltas plus the after-side research/civic
    strings, or None when either snapshot is missing or malformed.
    """
    if state_before is None or state_after is None:
        return None
    try:
        delta = {k: state_after[k] - state_before[k] for k in _STATE_DELTA_NUM_FIELDS}
        delta["research"] = state_after["research"]
        delta["civic"] = state_after["civic"]
    except (KeyError, TypeError):
        return None
    return delta


async def _overview_snapshot(gs):
    """Bootstrap-free lightweight overview snapshot; returns dict or None on failure."""
    try:
        lines = await gs.conn.execute_write(lq.build_overview_query())
        ov = lq.parse_overview_response(lines)
        return {
            "score":    ov.score,
            "gold":     ov.gold,
            "science":  ov.science_yield,
            "culture":  ov.culture_yield,
            "faith":    ov.faith,
            "research": ov.current_research,
            "civic":    ov.current_civic,
            "cities":   ov.num_cities,
            "units":    ov.num_units,
        }
    except Exception:
        return None


# Reactive-only recovery for an orphaned first-meet greeting (the puppet local-player
# switch can leave one on screen — a session, or a view with no locatable session).
# NOT run automatically from the poll loop: it cannot tell an orphaned greeting from a
# leader scene the human is actively using, and force-hiding the latter blacks out the
# map. Invoked manually when a stuck greeting is actually reported.
async def _clear_blocking_diplomacy(conn) -> str:
    """Best-effort: if a diplomacy modal is blocking the idle human, clear it
    (close any real session, hide orphaned views, restore the in-game UI). Only
    acts when a view is actually visible; never raises into the poll loop."""
    try:
        lines = await conn.execute_write(lq.build_clear_blocking_diplomacy())
    except Exception:
        return "err"
    for line in lines:
        if line.startswith("CLEAR|"):
            return line
    return "?"


# Consecutive idle polls (~1s each) before the orphan-session sweep fires. An
# orphaned puppet greeting wedges the AI phase indefinitely, so a human seat
# idle this long with no capture is the wedge signature; a normal human turn
# is unaffected either way because the sweep skips the local player's own
# sessions entirely. Observed live (2026-07-07): session 1<->3 (two puppets
# first-meeting) froze turn 27 for minutes until closed by hand.
ORPHAN_SWEEP_IDLE_POLLS = 45

# Full-LLM-control (riz 2026-07-15): an AI-initiated deal/session with the
# HUMAN seat halts the whole turn cycle until answered, and holds seat 0
# turn-inactive so normal admission never happens. The coordinator never
# auto-answers (a real offer deserves a real judgement) -- instead it hands
# the session to the seat-0 policy's own diplomacy tools
# (get_pending_diplomacy / respond_to_diplomacy / respond_to_trade), on the
# cadences below, bounded per wedged turn. Exhausting the bound logs one
# CRITICAL and falls back to waiting for a human -- the escape hatch is only
# for what the pilot genuinely cannot do itself.
SEAT0_DIPLO_IDLE_POLLS = 10    # idle polls between probes while seat 0 is inactive
SEAT0_DIPLO_DRAIN_POLLS = 45   # drain polls between probes during the AI phase
SEAT0_DIPLO_ATTEMPT_LIMIT = 3  # policy passes per wedged turn before CRITICAL

# WC session blockers clear only INSIDE ACTION_ENDTURN (the congress runs as
# a turn segment), so persisting after the pilot's voting chance is not
# failure -- ensure a voter handler and fire.
_WC_SESSION_TYPES = frozenset({
    "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION",
})


async def _sweep_orphan_sessions(conn) -> str:
    """Best-effort: close open diplomacy sessions not involving the local
    player (orphaned puppet greetings that wedge turn processing). Sessions
    involving the human are never touched; never raises into the poll loop."""
    try:
        lines = await conn.execute_write(lq.build_close_orphan_sessions())
    except Exception:
        return "err"
    for line in lines:
        if line.startswith("ORPHANS|"):
            return line
    return "?"



def _transcript_driver(pol) -> str:
    """Transcript `driver` label for a policy, aligned with
    PlayerSpec.driver_kind(): 'cli', 'scripted', or 'in_process'."""
    provider = str(getattr(pol, "provider", "local"))
    if provider.startswith("cli"):
        return "cli"
    if provider == "scripted":
        return "scripted"
    return "in_process"




def _policy_accepts_kwarg(policy, name: str) -> bool:
    try:
        # Introspect the callable itself, not policy.__call__: for a plain
        # function policy, `.__call__` is a method-wrapper whose signature is
        # (*args, **kwargs), which would spuriously report every kwarg as
        # accepted and then raise TypeError at the call site. inspect.signature
        # on the object unwraps bound methods / functions / partials correctly.
        signature = inspect.signature(policy)
    except (TypeError, ValueError):
        return False
    return any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        or (
            param.name == name
            and param.kind in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        )
        for param in signature.parameters.values()
    )


def _repair_kwargs(policy, blocker_block: str, caps: dict | None) -> dict | None:
    if not _policy_accepts_kwarg(policy, "blocker_block"):
        return None
    kwargs = {"blocker_block": blocker_block}
    if caps is not None and _policy_accepts_kwarg(policy, "caps"):
        kwargs["caps"] = caps
    return kwargs


def _channel_finish_input(*policy_results: dict | None) -> dict:
    """Build the private, unclamped capture view for one admitted turn.

    Seat 0 can make normal, repair, WC, delayed-RECHECK, and diplomacy policy
    calls under one admission.  Its public transcript merge intentionally keeps
    one final summary, but the channel runtime must see every raw CHANNEL line
    and action-audit step in source order.  This value is passed only to
    ``ChannelRuntime.finish_player``.
    """

    raw_results = [result for result in policy_results if isinstance(result, dict)]
    if len(raw_results) == 1:
        # Preserve the exact original object for the common single-pass turn.
        # Public projection happens only after this private handoff.
        return raw_results[0]

    steps: list = []
    summaries: list[str] = []
    for result in raw_results:
        payload = result.get("transcript")
        if not isinstance(payload, dict):
            continue
        raw_steps = payload.get("steps")
        if isinstance(raw_steps, list):
            steps.extend(raw_steps)
        summary = payload.get("final_summary")
        if isinstance(summary, str):
            summaries.append(summary)
    return {
        "transcript": {
            "steps": steps,
            "final_summary": "\n".join(summaries),
        }
    }


_PUBLIC_CHANNEL_DROP = object()
_CHANNEL_RESULT_MISSING = object()
_PRIVATE_CHANNEL_RESULT_FIELDS = frozenset({
    "channel_action",
    "channel_actions",
    "channel_block",
    "channel_context",
    "master_block",
    "staged_action",
    "staged_actions",
    "staged_channel_action",
    "staged_channel_actions",
})


def _channel_action_mapping(
    value: dict, channel_tool_call_ids: set[str]
) -> bool:
    """Whether a result mapping represents a private channel action/call."""

    for key in ("tool_call_id", "tool_use_id", "call_id"):
        referenced_id = value.get(key)
        if (
            isinstance(referenced_id, str)
            and referenced_id in channel_tool_call_ids
        ):
            return True
    for key in ("tool_name", "tool", "name", "action", "type"):
        if value.get(key) in CHANNEL_ACTION_NAMES:
            return True
    function = value.get("function")
    if isinstance(function, dict) and function.get("name") in CHANNEL_ACTION_NAMES:
        return True
    action = value.get("action")
    return (
        isinstance(action, dict)
        and any(
            action.get(key) in CHANNEL_ACTION_NAMES
            for key in ("name", "type", "action", "tool")
        )
    )


def _public_channel_result(result: dict) -> dict:
    """Build the sole deep public view of a channel-enabled policy result.

    Raw result objects belong exclusively to ``finish_player``.  This explicit
    recursive reconstruction removes all channel protocol/action shapes from
    logs and transcript records without mutating the original: exact protocol
    lines in text, channel tool calls/results, invalid calls, and staged action
    fields. Ordinary game tools and prose retain their source order.
    """

    channel_tool_call_ids: set[str] = set()

    def collect_channel_tool_call_ids(value) -> None:
        if isinstance(value, dict):
            if _channel_action_mapping(value, set()):
                for key in ("id", "call_id"):
                    producer_id = value.get(key)
                    if isinstance(producer_id, str):
                        channel_tool_call_ids.add(producer_id)
            for item in value.values():
                collect_channel_tool_call_ids(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect_channel_tool_call_ids(item)

    collect_channel_tool_call_ids(result)

    def project(value):
        if isinstance(value, str):
            return "\n".join(
                line
                for line in value.splitlines()
                if not line.startswith("CHANNEL ")
            )
        if isinstance(value, dict):
            if _channel_action_mapping(value, channel_tool_call_ids):
                return _PUBLIC_CHANNEL_DROP
            public = {}
            for key, item in value.items():
                if key in _PRIVATE_CHANNEL_RESULT_FIELDS:
                    continue
                projected = project(item)
                if projected is not _PUBLIC_CHANNEL_DROP:
                    public[copy.deepcopy(key)] = projected
            return public
        if isinstance(value, list):
            public = []
            for item in value:
                projected = project(item)
                if projected is not _PUBLIC_CHANNEL_DROP:
                    public.append(projected)
            return public
        if isinstance(value, tuple):
            public = []
            for item in value:
                projected = project(item)
                if projected is not _PUBLIC_CHANNEL_DROP:
                    public.append(projected)
            return tuple(public)
        return copy.deepcopy(value)

    public = project(result)
    return public if isinstance(public, dict) else {}


async def run_arena(
    conn,
    gs,
    config,
    policy=None,
    policy_for=None,
    transcript=None,
    channel_runtime=None,
    live_gate_driver=None,
) -> dict:
    # Validate at entry so programmatic callers cannot bypass the YAML/CLI
    # boundary checks (seat 0 in puppet_ids, unknown/duplicate seats, seat-0
    # attention). resolved_puppet_ids keeps an explicit empty list EMPTY —
    # the old truthy `or` fallback silently re-derived every player from [].
    validate_arena_config(config)
    if policy_for is None:
        if policy is None:
            raise ValueError("run_arena needs policy or policy_for")
        policy_for = lambda _pid: policy
    puppet_ids = set(resolved_puppet_ids(config))
    seat0_spec = next((spec for spec in config.players if spec.player_id == 0), None)
    seat0_state = Seat0TurnState()
    run_id = getattr(config, "run_id", "")
    if not run_id:
        # Memory/task state is keyed by run_id; an empty one collapses the
        # per-run directory onto transcript_dir itself, silently sharing
        # standing plans and tasks across unrelated runs.
        from civ_mcp.run_id import generate_run_id

        # A fresh id per call is intentional: an empty run_id means "isolated
        # run", so two calls must NOT share a memory/task directory. This id is
        # used for the state paths AND the transcript record below (via the
        # local run_id), so records stay joinable to the state dir without
        # mutating config.
        run_id = generate_run_id()
    played, slept, game_turns, log = 0, 0, 0, []
    seat0_played, seat0_failed, seat0_pending = 0, 0, 0
    _tx_on = transcript is not None and getattr(transcript, "enabled", True)
    enabled_channel_players = frozenset(
        spec.player_id
        for spec in config.players
        if spec.options.channels.enabled
    )
    channel_guidance_by_player = {
        spec.player_id: spec.options.channels.guidance
        for spec in config.players
        if spec.options.channels.enabled
    }
    channel_runtime_error = ""
    if enabled_channel_players:
        try:
            if channel_runtime is None:
                channel_runtime = ChannelRuntime.open(
                    Path(config.transcript_dir) / run_id,
                    run_id,
                    enabled_channel_players,
                    config.channel_rules,
                )
            else:
                ChannelRuntime._validate_identity(
                    channel_runtime.state,
                    run_id,
                    enabled_channel_players,
                    config.channel_rules,
                )
        except Exception as e:
            channel_runtime_error = repr(e)
            channel_runtime = None
            detail = (
                "channel_operation_failed"
                if live_gate_driver is not None
                else repr(e)
            )
            print(f"[arena] channel runtime unavailable: {detail}", file=sys.stderr)
    else:
        # Preserve the pre-channel path exactly when no seat opts in, including
        # ignoring an optional test/dependency injection that has no configured
        # participant identity to validate against.
        channel_runtime = None
    # Seat 0 is one logical policy turn even when its coordinator-owned work is
    # split across later poll iterations (RECHECK repair, WC recovery, or a
    # diplomacy-wedge pass).  Keep that one admission outside the loop-local
    # capture variables so a later puppet/player cannot replace its private
    # context or policy-result stream before the turn is terminalized.
    seat0_channel_capture: dict | None = None
    puppet_channel_capture: dict | None = None
    seat0_channel_admission_attempts: set[int] = set()
    seat0_channel_errors: dict[int, str] = {}
    channel_reconciled_key: tuple[int, int] | None = None
    channel_reconcile_error = ""

    # Gate-mode stderr/log privacy sweep — declared scope: the channel,
    # runtime, policy, and pending-record exception families, which can carry
    # channel-private values (canary, proposal text, payment fingerprints).
    # Game/tool-layer exception families (promotion sweep, attention state
    # saves, seat-0 mechanical/end-turn and seat-0 policy errors) still print
    # raw reprs and are deliberately outside this sweep: they cannot carry
    # channel-private values.
    def _public_channel_error(error: str) -> str:
        if live_gate_driver is not None and error:
            return "channel_operation_failed"
        return error

    def _public_channel_fields(fields: dict) -> dict:
        if live_gate_driver is None:
            # Preserve the legacy shared-reference behavior: seat-0 records
            # are assembled before finish and observe the finish mutation.
            return fields
        public = dict(fields)
        public["error"] = _public_channel_error(public.get("error", ""))
        return public

    def _print_private_error(prefix: str, exc: BaseException) -> None:
        detail = (
            "channel_operation_failed"
            if live_gate_driver is not None
            else repr(exc)
        )
        print(f"{prefix}: {detail}", file=sys.stderr)

    def _gate_result_field(*, status=None, reason=None) -> dict:
        assert live_gate_driver is not None
        summary = dict(live_gate_driver.result_summary())
        if status is not None:
            summary["status"] = status
        if reason is not None and not summary.get("reason"):
            summary["reason"] = reason
        return {"live_gate": summary}

    def _puppet_capture_for(player_id: int, turn: int) -> dict | None:
        capture = puppet_channel_capture
        if (
            capture is None
            or capture["player_id"] != player_id
            or capture["turn"] != turn
            or capture["finished"]
        ):
            return None
        return capture

    async def _finish_puppet_channel_capture(
        policy_result=_CHANNEL_RESULT_MISSING,
        *,
        synthesize_if_missing: bool = False,
    ) -> None:
        """Finish and clear an admitted puppet capture exactly once.

        Unlike ordinary Exceptions, BaseException leaves the captured loop
        immediately. Keeping ownership at run scope lets outer cleanup perform
        the private post observation before human handback. The synthetic
        private input is used only when no policy result exists; staged API
        actions remain authoritative on the bound admission context.
        """

        nonlocal puppet_channel_capture
        capture = puppet_channel_capture
        if capture is None:
            return
        if capture["finished"]:
            puppet_channel_capture = None
            return
        if policy_result is not _CHANNEL_RESULT_MISSING:
            capture["policy_result"] = policy_result
        capture["finished"] = True
        finish_input = capture["policy_result"]
        if finish_input is _CHANNEL_RESULT_MISSING:
            if synthesize_if_missing:
                finish_input = {
                    "transcript": {"steps": [], "final_summary": ""},
                    "staged_actions": tuple(
                        capture["admission"].context.staged_actions
                    ),
                }
            else:
                finish_input = None
        try:
            acknowledgements = await channel_runtime.finish_player(
                gs, capture["admission"], finish_input
            )
            capture["fields"]["acknowledgements"] = len(acknowledgements)
        except Exception as e:
            error = repr(e)
            prior = capture["fields"]["error"]
            capture["fields"]["error"] = f"{prior}; {error}" if prior else error
            _print_private_error(
                f"[arena] channel finish failed for seat "
                f"{capture['player_id']} turn {capture['turn']}",
                e,
            )
        finally:
            puppet_channel_capture = None

    def _seat0_capture_for(turn: int) -> dict | None:
        capture = seat0_channel_capture
        if (
            capture is None
            or capture["player_id"] != 0
            or capture["turn"] != turn
            or capture["finished"]
        ):
            return None
        return capture

    def _seat0_channel_policy_kwargs(turn: int) -> dict:
        capture = _seat0_capture_for(turn)
        return dict(capture["policy_kwargs"]) if capture is not None else {}

    def _capture_seat0_policy_result(turn: int, result: dict | None) -> None:
        capture = _seat0_capture_for(turn)
        if capture is not None:
            capture["policy_results"].append(result)

    async def _finish_seat0_channel_capture() -> None:
        """Finish and clear the active seat-0 admission exactly once.

        The reservation happens before awaiting and the reference is cleared in
        ``finally`` so an Exception, cancellation, rollback, or cleanup path can
        never leak this turn's private context into a later player/turn.
        """
        nonlocal seat0_channel_capture
        capture = seat0_channel_capture
        if capture is None:
            return
        if capture["finished"]:
            seat0_channel_capture = None
            return
        capture["finished"] = True
        try:
            acknowledgements = await channel_runtime.finish_player(
                gs,
                capture["admission"],
                _channel_finish_input(*capture["policy_results"]),
            )
            capture["fields"]["acknowledgements"] = len(acknowledgements)
        except Exception as e:
            error = repr(e)
            prior = capture["fields"]["error"]
            capture["fields"]["error"] = f"{prior}; {error}" if prior else error
            _print_private_error(
                f"[arena] channel finish failed for seat 0 turn "
                f"{capture['turn']}",
                e,
            )
        finally:
            seat0_channel_admission_attempts.discard(capture["turn"])
            seat0_channel_errors.pop(capture["turn"], None)
            seat0_channel_capture = None

    async def ensure_seat0_channel_capture(turn: int) -> dict | None:
        """Admit one persistent private capture before any seat-0 policy pass.

        Idle diplomacy can run before the normal seat-0 state-machine
        admission, while repair/WC/drain passes can run afterward.  This seam
        gives all of them the same context and one finish.  Reconciliation and
        admission failures are fail-open: the policy pass still runs without
        private kwargs, and a later public record carries counts/error only.
        """

        nonlocal seat0_channel_capture, channel_reconciled_key
        nonlocal channel_reconcile_error
        capture = _seat0_capture_for(turn)
        if capture is not None:
            return capture
        if 0 not in enabled_channel_players or channel_runtime is None:
            return None
        if seat0_channel_capture is not None:
            await _finish_seat0_channel_capture()
        if turn in seat0_channel_admission_attempts:
            return None
        if channel_reconciled_key == (turn, 0) and channel_reconcile_error:
            seat0_channel_errors[turn] = channel_reconcile_error
            return None
        if channel_reconciled_key != (turn, 0):
            try:
                await channel_runtime.reconcile_payment_intents(
                    gs,
                    current_turn=turn,
                    current_player_id=0,
                )
                channel_reconciled_key = (turn, 0)
                channel_reconcile_error = ""
            except Exception as e:
                error = repr(e)
                channel_reconciled_key = (turn, 0)
                channel_reconcile_error = error
                seat0_channel_errors[turn] = error
                _print_private_error(
                    f"[arena] channel payment reconciliation failed before "
                    f"seat 0 turn {turn}",
                    e,
                )
                log.append({
                    "event": "channel_error",
                    "stage": "reconcile",
                    "turn": turn,
                    "player_id": 0,
                    "error": _public_channel_error(error),
                })
                return None
        seat0_channel_admission_attempts.add(turn)
        try:
            admission = await channel_runtime.admit_player(
                gs,
                0,
                turn,
                guidance=channel_guidance_by_player.get(0, False),
            )
        except Exception as e:
            error = repr(e)
            seat0_channel_errors[turn] = error
            _print_private_error(
                f"[arena] channel admission failed for seat 0 turn {turn}", e
            )
            return None
        pol = policy_for(0)
        policy_kwargs = {
            name: value
            for name, value in (
                ("channel_context", admission.context),
                ("channel_block", admission.block),
                ("master_block", ""),
            )
            if _policy_accepts_kwarg(pol, name)
        }
        fields = {
            "enabled": True,
            "acknowledgements": 0,
            "error": seat0_channel_errors.get(turn, ""),
        }
        seat0_channel_capture = {
            "player_id": 0,
            "turn": turn,
            "admission": admission,
            "policy_kwargs": policy_kwargs,
            "policy_results": [],
            "fields": fields,
            "finished": False,
        }
        return seat0_channel_capture

    try:
        # Attach inside the human-safety scope: a fingerprint/configuration
        # failure must still restore player 0 and disable the puppet hook.
        if live_gate_driver is not None:
            try:
                if channel_runtime is None:
                    raise RuntimeError(
                        "live gate requires the channel runtime: "
                        + (channel_runtime_error or "no channel-enabled players")
                    )
                await live_gate_driver.attach(
                    gs=gs,
                    channel_runtime=channel_runtime,
                    run_dir=Path(config.transcript_dir) / run_id,
                )
                if live_gate_driver.pending_signal() is not None:
                    return {
                        "puppet_turns_played": 0,
                        "turns_slept": 0,
                        "seat0_turns_played": 0,
                        "seat0_turns_failed": 0,
                        "seat0_human_pending": 0,
                        "log": log,
                        **_gate_result_field(),
                    }
            except Exception as e:
                _print_private_error("[arena] live gate attach failed", e)
                return {
                    "puppet_turns_played": 0,
                    "turns_slept": 0,
                    "seat0_turns_played": 0,
                    "seat0_turns_failed": 0,
                    "seat0_human_pending": 0,
                    "log": log,
                    **_gate_result_field(
                        status="failed", reason="live gate attach failed"
                    ),
                }
        await hook.inject(conn, sorted(puppet_ids))
        hook_enabled = True  # flips to False once disable_hook_for_drain() fires
        remaining = config.max_puppet_turns
        deadline_polls = config.idle_poll_limit  # consecutive-idle poll budget; refilled on every captured turn
        # Per-turn seat-0 drain budgets, distinct from deadline_polls:
        # drain_polls counts quiet end-fired/AI-processing waits for the
        # CURRENT admitted seat-0 turn; human_polls counts human-pending waits.
        # Both reset at admission.
        drain_polls = 0
        human_polls = 0
        idle_streak = 0  # consecutive idle polls since the last puppet capture
        # Diplomacy-wedge pass state, keyed to the wedged game turn.
        diplo_turn = -1
        diplo_attempts = 0
        diplo_unresolved_logged = False
        max_game_turns = getattr(config, "max_game_turns", 0)  # tolerate old test-stub configs

        def admission_open() -> bool:
            return (
                remaining > 0
                and (max_game_turns <= 0 or game_turns < max_game_turns)
            )

        async def disable_hook_for_drain() -> None:
            """Idempotently disable the puppet hook once, so a budget-exhausted
            seat-0 turn (or a puppet that spent the final slot while seat 0
            drains) cannot release into AI with the hook still capturing. The
            `finally` block disables again unconditionally -- safety cleanup
            must not depend on this flag."""
            nonlocal hook_enabled
            if hook_enabled:
                await hook.disable(conn)
                hook_enabled = False

        def _write_seat0_record_once() -> None:
            if seat0_state.record is None or seat0_state.record_written:
                return
            if _tx_on:
                transcript.write(seat0_state.record)
            seat0_state.record_written = True

        async def _terminalize_seat0_advanced() -> None:
            """Write the pending seat-0 record exactly once with terminal
            `advanced`, count the played turn, and reset for the next
            admission. Only ever reached on an observed turn-number change —
            never at ai_processing (append-once contract)."""
            nonlocal seat0_played
            await _finish_seat0_channel_capture()
            if seat0_state.record is not None and not seat0_state.record_written:
                if (
                    seat0_state.record["seat0"]["terminal_state"]
                    != "human_pending"
                ):
                    seat0_state.record["seat0"]["terminal_state"] = "advanced"
                    seat0_state.record["seat0"]["end_turn_requests"] = (
                        seat0_state.end_turn_requests
                    )
                    seat0_played += 1
                _write_seat0_record_once()
            seat0_channel_admission_attempts.discard(seat0_state.turn)
            seat0_channel_errors.pop(seat0_state.turn, None)
            seat0_state.reset()

        async def _terminalize_seat0_regressed(*, observed_turn: int) -> None:
            """The game state rolled back under an in-flight seat-0 turn (a
            human loaded an earlier save): write the pending record exactly
            once with terminal `regressed`, count it as failed (its outcome
            no longer exists in the timeline), emit one CRITICAL event, and
            reset for re-admission at the rolled-back turn. A record already
            written (human_pending) is never rewritten -- only the reset and
            the CRITICAL apply."""
            nonlocal seat0_failed
            await _finish_seat0_channel_capture()
            regressed_from = seat0_state.turn
            if seat0_state.record is not None and not seat0_state.record_written:
                if (
                    seat0_state.record["seat0"]["terminal_state"]
                    != "human_pending"
                ):
                    seat0_state.record["turn_kind"] = "failed"
                    seat0_state.record["seat0"]["terminal_state"] = "regressed"
                    seat0_state.record["seat0"]["end_turn_requests"] = (
                        seat0_state.end_turn_requests
                    )
                    seat0_failed += 1
                _write_seat0_record_once()
            seat0_channel_admission_attempts.discard(seat0_state.turn)
            seat0_channel_errors.pop(seat0_state.turn, None)
            log.append({
                "level": "CRITICAL",
                "event": "seat0_turn_regressed",
                "turn": regressed_from,
                "observed_turn": observed_turn,
            })
            print(
                f"[arena] CRITICAL seat0_turn_regressed: in-flight turn "
                f"{regressed_from} rolled back to {observed_turn}; the turn "
                f"will be re-piloted when seat 0 polls active",
                file=sys.stderr,
            )
            seat0_state.reset()

        def _seat0_enter_human_pending(
            *, turn: int, blockers: list, record: dict, turn_kind: str,
            normal_error: str, repair_error: str,
        ) -> None:
            """Terminalize an admitted seat-0 turn that the pilot could not
            finish: transition to `human_pending`, fill and stage the record,
            emit exactly one structured CRITICAL event, and hand local seat 0
            to the human untouched. An active channel capture is finished
            before the staged record is serialized; a disabled/no-admission
            turn retains the immediate pre-channel write ordering. Chooses NO
            strategic default — the unresolved blockers are left for the
            human to decide."""
            nonlocal seat0_pending, seat0_failed
            seat0_state.mark_human_pending()
            record["turn_kind"] = turn_kind
            record["seat0"]["terminal_state"] = "human_pending"
            record["seat0"]["end_turn_requests"] = seat0_state.end_turn_requests
            seat0_state.record = record
            if _seat0_capture_for(turn) is None:
                _write_seat0_record_once()
            if seat0_state.mark_critical_emitted():
                blocker_types = [b["type"] for b in blockers]
                log.append({
                    "level": "CRITICAL",
                    "event": "seat0_human_pending",
                    "turn": turn,
                    "blockers": blocker_types,
                    "policy_errors": {"normal": normal_error, "repair": repair_error},
                })
                print(
                    f"[arena] CRITICAL seat0_human_pending turn {turn}: "
                    f"blockers={blocker_types} normal_error={normal_error!r} "
                    f"repair_error={repair_error!r}",
                    file=sys.stderr,
                )
            seat0_pending += 1
            if turn_kind == "failed":
                seat0_failed += 1

        async def _mech_pass_once(prefix):
            """Query -> mechanical-only cleanup -> requery. A second query is
            issued only when the first found blockers (an empty snapshot cannot
            change under cleanup). Returns (post_blockers, cleanup_records,
            snapshots, groups)."""
            first = await seat0.query_blockers(conn)
            snaps = [{"stage": prefix, "blockers": first}]
            records: list = []
            if first:
                records = await seat0.apply_mechanical_cleanup(conn, first)
                after = await seat0.query_blockers(conn)
                snaps.append({"stage": prefix + "_cleanup", "blockers": after})
            else:
                after = first
            return after, records, snaps, seat0.classify_blockers(after)

        async def _mech_pass(prefix):
            errors: list[dict] = []
            for attempt in (1, 2):
                try:
                    result = await _mech_pass_once(prefix)
                    return (*result, errors)
                except Exception as exc:
                    errors.append({
                        "stage": prefix,
                        "attempt": attempt,
                        "error": repr(exc),
                    })
                    # Retry only when the reconnect actually restored the
                    # tuner -- a second attempt against a connection that
                    # just failed to reconnect is guaranteed to fail and
                    # would bury the original error. No reconnect after the
                    # final attempt: the human-pending path and the finally
                    # block do their own reclaim.
                    if attempt == 2 or not await _reconnect_with_retry(conn):
                        break

            blocker = seat0.automation_failure_blocker(
                prefix, errors[-1]["error"]
            )
            blockers = [blocker]
            snapshots = [{"stage": prefix + "_error", "blockers": blockers}]
            return (
                blockers,
                [],
                snapshots,
                seat0.classify_blockers(blockers),
                errors,
            )

        async def _fire_seat0_end(record: dict) -> None:
            seat0_state.mark_end_fired()
            try:
                await hook.end_turn(conn)
            except Exception as exc:
                error = {
                    "request": seat0_state.end_turn_requests,
                    "error": repr(exc),
                }
                record["seat0"]["end_turn_errors"].append(error)
                log.append({
                    "level": "WARNING",
                    "event": "seat0_end_turn_uncertain",
                    "turn": record["turn"],
                    **error,
                })
                await _reconnect_with_retry(conn)

        async def _attempt_seat0_repair(
            pol, repair, after_blockers, *, prior_error, caps_kwarg,
            exclusive, turn, channel_kwargs=None,
        ):
            """One-shot focused repair shared by the played branch and the
            RECHECK path. Mutates `repair` (the record's seat0.repair
            sub-dict) in place so an interruption mid-repair still leaves
            attempted=True in the terminal record. Returns
            (repair_result, mech) where mech is the _mech_pass("after_repair")
            5-tuple when the repair returned, else None."""
            blocker_block = seat0.build_blocker_block(
                after_blockers, prior_error=prior_error
            )
            # Set BEFORE awaiting so a cancellation/exception can never
            # permit a second repair.
            seat0_state.repair_used = True
            repair_kwargs = _repair_kwargs(pol, blocker_block, caps_kwarg)
            if repair_kwargs is None:
                repair["error"] = (
                    "policy does not accept required blocker_block keyword"
                )
                return None, None
            if 0 in enabled_channel_players:
                await ensure_seat0_channel_capture(turn)
                repair_kwargs.update(_seat0_channel_policy_kwargs(turn))
            elif channel_kwargs:
                repair_kwargs.update(channel_kwargs)
            repair["attempted"] = True
            if exclusive and conn.is_connected:
                await conn.disconnect()   # repair owns the tuner
            repair_result = None
            try:
                repair_result = await pol(gs, 0, turn, **repair_kwargs)
                timeout_error = seat0.attempt_timeout_error(repair_result)
                if timeout_error:
                    # Timed-out repair: zero usable work -- account like a
                    # raised repair, never as a completed one.
                    repair["error"] = timeout_error
                    print(f"[arena] seat-0 turn {turn} repair timed out: "
                          f"{timeout_error}", file=sys.stderr)
                    log.append({"turn": turn, "player_id": 0,
                                "skipped": True, "repair_error": timeout_error})
                else:
                    repair["completed"] = True
                    public_repair_result = (
                        _public_channel_result(repair_result or {})
                        if 0 in enabled_channel_players
                        else repair_result or {}
                    )
                    repair["summary"] = public_repair_result.get("summary", "")
            except Exception as e:
                repair["error"] = repr(e)
                print(f"[arena] seat-0 turn {turn} repair failed: {e!r}",
                      file=sys.stderr)
                log.append({"turn": turn, "player_id": 0,
                            "skipped": True, "repair_error": repair["error"]})
            _capture_seat0_policy_result(turn, repair_result)
            # Reclaim the tuner regardless of outcome: the post-repair pass
            # and the human-pending drain both need a live connection.
            if exclusive and not conn.is_connected:
                await _reconnect_with_retry(conn)
            if repair["error"] == "":
                return repair_result, await _mech_pass("after_repair")
            return repair_result, None

        async def _seat0_diplomacy_pass_if_wedged(turn: int) -> bool:
            """Probe for an open human-seat diplomacy session and, when one is
            found, run one bounded policy pass so the pilot answers it with
            its own tools. Returns True when a pass actually ran (activity).

            Never auto-answers and never raises into the poll loop; after
            SEAT0_DIPLO_ATTEMPT_LIMIT failed passes on the same turn it logs
            one CRITICAL entry and leaves the session to a human."""
            nonlocal diplo_turn, diplo_attempts, diplo_unresolved_logged
            if seat0_spec is None:
                return False
            if turn != diplo_turn:
                diplo_turn = turn
                diplo_attempts = 0
                diplo_unresolved_logged = False
            sessions = await seat0.query_local_player_sessions(conn)
            if not sessions:
                return False
            if diplo_attempts >= SEAT0_DIPLO_ATTEMPT_LIMIT:
                if not diplo_unresolved_logged:
                    diplo_unresolved_logged = True
                    log.append({
                        "level": "CRITICAL",
                        "event": "seat0_diplomacy_unresolved",
                        "turn": turn,
                        "sessions": sessions,
                        "attempts": diplo_attempts,
                    })
                    print(f"[arena] CRITICAL seat0_diplomacy_unresolved: "
                          f"sessions {sessions} still open after "
                          f"{diplo_attempts} policy passes on turn {turn}; "
                          f"waiting for a human", file=sys.stderr)
                return False
            diplo_attempts += 1
            pol = policy_for(0)
            entry = {
                "event": "seat0_diplomacy_pass",
                "turn": turn,
                "player_id": 0,
                "sessions": sessions,
                "attempt": diplo_attempts,
                "completed": False,
                "error": "",
            }
            kwargs = _repair_kwargs(pol, seat0.build_diplomacy_block(sessions), None)
            if kwargs is None:
                # An incompatible policy can never answer; don't burn the
                # remaining attempts discovering that three times.
                diplo_attempts = SEAT0_DIPLO_ATTEMPT_LIMIT
                entry["error"] = (
                    "policy does not accept required blocker_block keyword"
                )
                log.append(entry)
                return False
            await ensure_seat0_channel_capture(turn)
            kwargs.update(_seat0_channel_policy_kwargs(turn))
            exclusive = bool(getattr(pol, "needs_exclusive_tuner", False))
            if exclusive and conn.is_connected:
                await conn.disconnect()   # the pass owns the tuner
            result = None
            try:
                result = await pol(gs, 0, turn, **kwargs)
                timeout_error = seat0.attempt_timeout_error(result)
                if timeout_error:
                    entry["error"] = timeout_error
                else:
                    entry["completed"] = True
                    public_result = (
                        _public_channel_result(result or {})
                        if 0 in enabled_channel_players
                        else result or {}
                    )
                    entry["summary"] = public_result.get("summary", "")
                    print(f"[arena] seat-0 diplomacy pass completed on turn "
                          f"{turn} (sessions {sessions}, attempt "
                          f"{diplo_attempts})", file=sys.stderr)
            except Exception as e:
                entry["error"] = repr(e)
                print(f"[arena] seat-0 diplomacy pass failed on turn {turn}: "
                      f"{e!r}", file=sys.stderr)
            _capture_seat0_policy_result(turn, result)
            # Reclaim the tuner regardless of outcome: the poll loop needs a
            # live connection either way.
            if exclusive and not conn.is_connected:
                await _reconnect_with_retry(conn)
            log.append(entry)
            return True

        async def _seat0_ensure_wc_voter(turn: int) -> None:
            """Ensure a WC voter handler exists before (re)firing into a live
            congress: keep the pilot's handler when registered, register the
            default voter otherwise (logged). Never raises."""
            if await seat0.wc_handler_registered(conn):
                return
            defaulted = await seat0.register_default_wc_voter(conn)
            log.append({
                "event": "seat0_wc_default_vote",
                "turn": turn,
                "player_id": 0,
                "registered": defaulted,
            })
            print(f"[arena] seat-0 WC default voter registered on turn "
                  f"{turn}: {defaulted}", file=sys.stderr)

        async def _seat0_wc_gate(
            pol, turn: int, channel_kwargs=None
        ) -> dict | None:
            """Solo-path parity (observed live, T303): the World Congress
            opens and closes synchronously INSIDE ACTION_ENDTURN, so votes
            must be registered BEFORE the coordinator fires. When the WC
            fires this turn with resolutions and no voter handler is
            registered, give the policy one focused voting pass; if it still
            has not registered votes, register the default voter -- a default
            vote keeps the game moving, stuck-free beats optimal.

            A 0-resolution congress (including the stale in_session=true
            shape seen post-bounce) has nothing to vote on and never gates.
            Never raises into the turn flow."""
            try:
                status = await gs.get_world_congress()
            except Exception:
                return None
            fires = status.turns_until_next <= 0 or status.is_in_session
            n_res = len(status.resolutions or [])
            if not fires or n_res == 0:
                return None
            if await seat0.wc_handler_registered(conn):
                return None
            entry = {
                "event": "seat0_wc_vote_pass",
                "turn": turn,
                "player_id": 0,
                "resolutions": n_res,
                "completed": False,
                "defaulted": False,
                "error": "",
            }
            block = seat0.build_blocker_block([{
                "type": "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION",
                "message": (
                    f"World Congress fires this turn ({n_res} resolution(s), "
                    f"{status.favor} favor available). Review with "
                    f"get_world_congress() and register votes with "
                    f"queue_wc_votes() NOW -- they deploy automatically when "
                    f"the coordinator ends the turn."
                ),
            }])
            kwargs = _repair_kwargs(pol, block, None)
            result = None
            if kwargs is None:
                entry["error"] = (
                    "policy does not accept required blocker_block keyword"
                )
            else:
                if 0 in enabled_channel_players:
                    await ensure_seat0_channel_capture(turn)
                    kwargs.update(_seat0_channel_policy_kwargs(turn))
                elif channel_kwargs:
                    kwargs.update(channel_kwargs)
                exclusive = bool(getattr(pol, "needs_exclusive_tuner", False))
                if exclusive and conn.is_connected:
                    await conn.disconnect()   # the voting pass owns the tuner
                try:
                    result = await pol(gs, 0, turn, **kwargs)
                    timeout_error = seat0.attempt_timeout_error(result)
                    if timeout_error:
                        entry["error"] = timeout_error
                    else:
                        entry["completed"] = True
                        public_result = (
                            _public_channel_result(result or {})
                            if 0 in enabled_channel_players
                            else result or {}
                        )
                        entry["summary"] = public_result.get("summary", "")
                except Exception as e:
                    entry["error"] = repr(e)
                if exclusive and not conn.is_connected:
                    await _reconnect_with_retry(conn)
            if not await seat0.wc_handler_registered(conn):
                entry["defaulted"] = await seat0.register_default_wc_voter(conn)
            _capture_seat0_policy_result(turn, result)
            log.append(entry)
            print(f"[arena] seat-0 WC vote pass on turn {turn}: "
                  f"completed={entry['completed']} "
                  f"defaulted={entry['defaulted']}", file=sys.stderr)
            return result

        async def _recheck_cleanup_repair_or_refire() -> None:
            """RECHECK: the previous end request may not have taken (seat 0
            still active after the grace window). Re-run the mechanical pass
            on the still-open turn; if a decision blocker newly surfaced and
            the one repair is still unused, attempt it. A re-fire needs PROOF
            the previous request bounced -- an open blocker in the recheck's
            first query (the engine refuses to end the turn while one is
            open). With proof and a clear post-cleanup/repair state, re-save
            the recovery anchor (same 0_MCP_NNNN name) and re-fire; without
            proof, the request is most likely accepted-but-latent, and a
            duplicate ACTION_ENDTURN would risk the multi-turn skip
            documented in end_turn.py -- wait out another grace window
            instead, bounded by the quiet-recheck budget. Escalate to
            human_pending when a blocker persists, the three-request budget
            is spent, or the quiet-recheck budget is exhausted. Seat 0 is
            local+active throughout (observe only returns RECHECK while
            active), so the InGame work here is legal."""
            ctx = seat0_state.resume_context
            if ctx is None:
                raise RuntimeError("seat-0 recheck missing resume context")
            pol = ctx.policy
            caps_kwarg = ctx.caps
            exclusive = ctx.exclusive
            record = seat0_state.record
            turn = record["turn"]
            s0 = record["seat0"]

            (
                after_blockers,
                cleanup_records,
                snaps,
                groups,
                pass_errors,
            ) = await _mech_pass("after_refire")
            s0["automation_errors"] = s0["automation_errors"] + pass_errors
            s0["blocker_snapshots"] = s0["blocker_snapshots"] + snaps
            s0["mechanical_cleanup"] = s0["mechanical_cleanup"] + cleanup_records

            if (
                not seat0_state.repair_used
                and not groups.hard
                and groups.decision
            ):
                # A supported decision blocker surfaced after the end request;
                # spend the one-shot repair on it. No prior-error line -- the
                # end request did not raise, the engine simply did not advance.
                repair_result, repair_mech = await _attempt_seat0_repair(
                    pol, s0["repair"], after_blockers,
                    prior_error="", caps_kwarg=caps_kwarg,
                    exclusive=exclusive, turn=turn,
                    channel_kwargs=_seat0_channel_policy_kwargs(turn),
                )
                if repair_mech is not None:
                    (
                        after_blockers,
                        rep_cleanup,
                        rep_snaps,
                        groups,
                        pass_errors,
                    ) = repair_mech
                    s0["automation_errors"] = s0["automation_errors"] + pass_errors
                    s0["blocker_snapshots"] = s0["blocker_snapshots"] + rep_snaps
                    s0["mechanical_cleanup"] = s0["mechanical_cleanup"] + rep_cleanup

            remaining_blockers = list(groups.hard) + list(groups.decision)
            # Proof of a bounce: the recheck's FIRST query (before cleanup or
            # repair mutated anything) found an open blocker. An empty first
            # query proves nothing -- the request may be accepted but latent.
            bounced = bool(snaps and snaps[0]["blockers"])
            if not remaining_blockers and not bounced:
                # A deal/session with the human seat absorbs turn processing
                # without ever appearing as a blocker (observed live, T301):
                # probe for one and hand it to the pilot BEFORE spending the
                # quiet-recheck budget on it.
                if await _seat0_diplomacy_pass_if_wedged(turn):
                    seat0_state.grace_polls = 0
                    return
                if seat0_state.note_idle_recheck():
                    return
                if (
                    not seat0_state.guarded_refire_used
                    and seat0_state.may_fire_end_turn
                ):
                    # Full-LLM-control: quiet budget exhausted, seat 0 still
                    # active on the same turn, no blocker, no session -- the
                    # strongest available evidence that the original request
                    # was dropped rather than accepted-but-latent. Spend ONE
                    # guarded refire before any human escalation; the
                    # multi-turn-skip hazard stays bounded by this flag and
                    # the request cap.
                    seat0_state.guarded_refire_used = True
                    log.append({
                        "event": "seat0_guarded_refire",
                        "turn": turn,
                        "player_id": 0,
                        "requests_before": seat0_state.end_turn_requests,
                    })
                    print(f"[arena] seat-0 guarded refire on turn {turn}: "
                          f"quiet radar clear after idle-recheck budget",
                          file=sys.stderr)
                    anchor = await seat0.save_recovery_anchor(conn, turn)
                    s0["autosave"]["attempts"].append(anchor)
                    if anchor.get("ok", False) and not s0["autosave"].get("name"):
                        s0["autosave"]["name"] = anchor.get("name", "")
                    if not admission_open():
                        await disable_hook_for_drain()
                    await _fire_seat0_end(record)
                    return
                # Quiet-recheck budget exhausted with seat 0 still active,
                # no observable cause, and the guarded refire already spent:
                # hand the turn to the human below.
            # Post-fire WC bounce (the T303 shape): the WC blocker cannot
            # clear BEFORE an end request -- the congress only processes
            # inside ACTION_ENDTURN. When every remaining blocker is a WC
            # session type, ensure a voter handler exists (the repair pass
            # above was the pilot's chance to register one; default
            # otherwise) and treat the state as clear-to-refire.
            wc_only = bool(remaining_blockers) and all(
                b["type"] in _WC_SESSION_TYPES for b in remaining_blockers
            )
            if wc_only and seat0_state.may_fire_end_turn:
                await _seat0_ensure_wc_voter(turn)
                remaining_blockers = []
                bounced = True
            if not remaining_blockers and bounced and seat0_state.may_fire_end_turn:
                # Cleared and the retry budget survives: re-save the anchor under
                # the SAME name so it reflects the repaired state, then re-fire.
                anchor = await seat0.save_recovery_anchor(conn, turn)
                s0["autosave"]["attempts"].append(anchor)
                # Adopt the re-save as the recovery point only when it
                # actually succeeded -- ok is authoritative.
                if anchor.get("ok", False) and not s0["autosave"].get("name"):
                    s0["autosave"]["name"] = anchor.get("name", "")
                if not admission_open():
                    await disable_hook_for_drain()
                await _fire_seat0_end(record)
            else:
                # A blocker persists, or the three end requests are spent with
                # seat 0 still active -> hand the turn to the human. turn_kind is
                # preserved (a call returned to reach the played path at all).
                if not admission_open():
                    await disable_hook_for_drain()
                _seat0_enter_human_pending(
                    turn=turn,
                    blockers=remaining_blockers,
                    record=record,
                    turn_kind=record.get("turn_kind", "played"),
                    normal_error=s0["normal"]["error"],
                    repair_error=s0["repair"]["error"],
                )

        # Admission is bounded by the shared budgets; an in-flight seat-0
        # drain (end fired / AI processing) is never aborted by them.
        while deadline_polls > 0 and (admission_open() or seat0_state.needs_drain):
            st = await hook.poll(conn)
            if st.turn >= 0:
                for attempted_turn in tuple(seat0_channel_admission_attempts):
                    if attempted_turn != st.turn:
                        seat0_channel_admission_attempts.discard(attempted_turn)
                        seat0_channel_errors.pop(attempted_turn, None)
            # Close the prior seat-0 capture before any channel runtime work is
            # attributed to a newly observed game turn.  The state-machine
            # terminalizer below remains the authoritative record/reset seam;
            # its finish call is idempotent after this early boundary close.
            active_capture = seat0_channel_capture
            if (
                active_capture is not None
                and st.turn >= 0
                and st.turn != active_capture["turn"]
            ):
                await _finish_seat0_channel_capture()
            poll_channel_error = ""
            channel_reconciled_key = None
            channel_reconcile_error = ""
            if channel_runtime is not None and st.turn >= 0:
                try:
                    await channel_runtime.reconcile_payment_intents(
                        gs,
                        current_turn=st.turn,
                        current_player_id=st.local,
                    )
                    channel_reconciled_key = (st.turn, st.local)
                except Exception as e:
                    poll_channel_error = repr(e)
                    channel_reconciled_key = (st.turn, st.local)
                    channel_reconcile_error = poll_channel_error
                    _print_private_error(
                        "[arena] channel payment reconciliation failed", e
                    )
                    log.append({
                        "event": "channel_error",
                        "stage": "reconcile",
                        "turn": st.turn,
                        "player_id": st.local,
                        "error": _public_channel_error(poll_channel_error),
                    })
            # First observe/finalize an in-flight seat-0 turn (the turn number
            # must move strictly forward to signal advance), then give an
            # actually captured puppet priority, then consider a new seat-0
            # admission.
            if seat0_state.needs_drain:
                poll_action = seat0_state.observe(
                    turn=st.turn, seat0_active=st.seat0_active
                )
                if poll_action is Seat0Poll.ADVANCED:
                    await _terminalize_seat0_advanced()
                    # Fall through: this same poll may re-admit the next turn.
                elif poll_action is Seat0Poll.REGRESSED:
                    await _terminalize_seat0_regressed(observed_turn=st.turn)
                    # Fall through: seat 0 re-admits at the rolled-back turn
                    # once it polls active again.
                elif poll_action is Seat0Poll.RECHECK:
                    # The end request did not take (seat 0 still active after
                    # the grace window). observe only returns RECHECK while
                    # seat0_active is true, so the InGame recheck work is legal.
                    await _recheck_cleanup_repair_or_refire()
                    deadline_polls -= 1
                    continue
                # WAIT falls through to the quiet drain-wait branch below
                # (sleep only, no InGame call); a captured puppet is serviced
                # first if one holds the capture on this poll.
            captured_puppet = (
                st.turn >= 0
                and st.active
                and st.local in puppet_ids
                and admission_open()
            )
            local_seat0 = (
                st.turn >= 0
                and seat0_spec is not None
                and st.local == 0
                and st.seat0_active
                and seat0_state.can_admit(turn=st.turn, seat0_active=True)
                and admission_open()
            )
            if captured_puppet or local_seat0:
                is_seat0 = local_seat0
                if is_seat0:
                    seat0_state.admit(st.turn)
                    drain_polls = 0
                    human_polls = 0
                idle_streak = 0
                # A captured turn (puppet or seat-0 admission) is ACTIVITY:
                # refill the idle budget.
                # deadline_polls means "consecutive polls with nothing to do",
                # not a whole-run cap that slept turns burn through without
                # consuming max_puppet_turns (review-2 f8).
                deadline_polls = config.idle_poll_limit
                pol = policy_for(st.local)
                exclusive = bool(getattr(pol, "needs_exclusive_tuner", False))
                opts = getattr(pol, "options", CivOptions())
                transcript_dir = config.transcript_dir
                attention_mode = opts.attention.mode
                # Seat 0 is piloted directly: attention/sleep semantics never
                # apply. validate_arena_config already rejects a seat-0 spec
                # with mode != "off"; forcing here also covers policy objects
                # whose own options were built programmatically.
                attention_on = (
                    not is_seat0
                ) and attention_mode in ("auto", "model", "hybrid")
                state_before = (
                    await _overview_snapshot(gs) if (_tx_on or attention_on) else None
                )

                # --- Load standing memory / task tracker state and run deterministic
                # pre-model task follow-through. This MUST happen before the exclusive
                # disconnect below: it uses `gs`, which is backed by the live `conn` —
                # a CLI turn's exclusive disconnect leaves no connection for these reads.
                memory_error = ""
                task_tracker_error = ""
                try:
                    memory = load_memory(transcript_dir, run_id, st.local) if opts.memory.enabled else None
                    memory_block = format_memory_block(
                        memory,
                        current_turn=st.turn,
                        max_age_turns=opts.memory.max_age_turns,
                    )
                except Exception as e:
                    memory = None
                    memory_block = ""
                    memory_error = repr(e)
                    print(f"[arena] standing memory load failed: {e!r}", file=sys.stderr)

                active_tasks_before: tuple = ()
                updated_tasks: tuple = ()
                task_results: list = []
                active_tasks_after: tuple = ()
                task_block = ""
                # Latest task set that is safe to merge captured TASK lines onto.
                # None only when the load itself failed (no trustworthy base) --
                # a later pre-model failure (save, formatting) must not cost us
                # the TASK/CANCEL lines the model emits this turn.
                task_capture_base: tuple | None = None
                if opts.task_tracker.enabled:
                    try:
                        task_state = load_task_state(transcript_dir, run_id, st.local)
                        # Loaded state carries failed tombstones alongside active
                        # tasks; both must reach run_pre_model_tasks (which skips
                        # non-active) and the capture merge, or the restatement
                        # guard loses its memory of exhausted tasks.
                        loaded_tasks = task_state.tasks
                        active_tasks_before = tuple(
                            t for t in loaded_tasks if t.status == "active"
                        )
                        task_capture_base = loaded_tasks
                        updated_tasks, task_results = await run_pre_model_tasks(
                            gs, loaded_tasks, turn=st.turn
                        )
                        task_capture_base = updated_tasks
                        pre_model_state = save_task_state(
                            transcript_dir, run_id, st.local, updated_tasks
                        )
                        active_tasks_after = tuple(
                            t for t in pre_model_state.tasks if t.status == "active"
                        )
                        task_block = format_task_block(
                            updated_tasks,
                            task_results,
                            max_tasks=opts.task_tracker.max_tasks,
                        )
                    except Exception as e:
                        updated_tasks = ()
                        task_results = []
                        active_tasks_after = ()
                        task_block = ""
                        task_tracker_error = repr(e)
                        print(f"[arena] task tracker pre-model failed: {e!r}", file=sys.stderr)

                # --- Unofficial-channel admission. The deterministic runtime
                # owns projection, observation, staging, and deadlines; the
                # coordinator only brackets this logical turn. Admission is
                # before the final attention decision so due obligations can
                # wake a seat that would otherwise sleep.
                channel_turn_enabled = st.local in enabled_channel_players
                channel_admission = None
                channel_acknowledgements = 0
                channel_finished = False
                channel_error = channel_runtime_error or poll_channel_error
                channel_fields_state = {
                    "enabled": True,
                    "acknowledgements": 0,
                    "error": channel_error,
                }
                if is_seat0 and channel_turn_enabled:
                    capture = await ensure_seat0_channel_capture(st.turn)
                    if capture is not None:
                        channel_admission = capture["admission"]
                        channel_fields_state = capture["fields"]
                        channel_error = channel_fields_state["error"]
                    else:
                        channel_error = (
                            seat0_channel_errors.get(st.turn)
                            or channel_runtime_error
                            or poll_channel_error
                        )
                        channel_fields_state["error"] = channel_error
                elif (
                    channel_turn_enabled
                    and channel_runtime is not None
                    and not poll_channel_error
                ):
                    try:
                        channel_admission = await channel_runtime.admit_player(
                            gs,
                            st.local,
                            st.turn,
                            guidance=channel_guidance_by_player.get(st.local, False),
                        )
                    except Exception as e:
                        channel_error = repr(e)
                        _print_private_error(
                            f"[arena] channel admission failed for seat "
                            f"{st.local} turn {st.turn}",
                            e,
                        )
                    channel_fields_state["error"] = channel_error
                if not is_seat0 and channel_admission is not None:
                    if puppet_channel_capture is not None:
                        await _finish_puppet_channel_capture(
                            synthesize_if_missing=True
                        )
                    puppet_channel_capture = {
                        "player_id": st.local,
                        "turn": st.turn,
                        "admission": channel_admission,
                        "policy_result": _CHANNEL_RESULT_MISSING,
                        "fields": channel_fields_state,
                        "finished": False,
                    }
                if live_gate_driver is not None and not is_seat0:
                    live_gate_driver.note_admission(
                        st.local, st.turn, channel_admission, channel_error
                    )
                    if live_gate_driver.pending_signal() is not None:
                        # Admission is the fail-stop boundary. Close the exact
                        # canonical capture we already own, but do not invoke
                        # any driver/ordinary policy or game action afterward.
                        # Human restore here plus the run-scope finally's
                        # disable leaves no later seat available for admission.
                        await _finish_puppet_channel_capture(None)
                        await hook.restore_local(conn, 0)
                        break

                async def _finish_channel_turn(policy_result) -> None:
                    nonlocal channel_acknowledgements, channel_error, channel_finished
                    if channel_admission is None or channel_finished:
                        return
                    # Reserve the one finish attempt before awaiting so an
                    # interrupt can never cause replay inside this run.
                    channel_finished = True
                    if not is_seat0:
                        await _finish_puppet_channel_capture(policy_result)
                        channel_acknowledgements = channel_fields_state[
                            "acknowledgements"
                        ]
                        channel_error = channel_fields_state["error"]
                        return
                    try:
                        acknowledgements = await channel_runtime.finish_player(
                            gs, channel_admission, policy_result
                        )
                        channel_acknowledgements = len(acknowledgements)
                        channel_fields_state["acknowledgements"] = len(
                            acknowledgements
                        )
                    except Exception as e:
                        error = repr(e)
                        channel_error = (
                            f"{channel_error}; {error}" if channel_error else error
                        )
                        channel_fields_state["error"] = channel_error
                        _print_private_error(
                            f"[arena] channel finish failed for seat "
                            f"{st.local} turn {st.turn}",
                            e,
                        )

                def _channel_fields() -> dict:
                    return _public_channel_fields(channel_fields_state)

                def _private_channel_fields() -> dict:
                    return dict(channel_fields_state)

                # --- Attention skip-evaluation (spec §2-4): once per captured puppet
                # turn, decide whether this civ can sleep through it. Every failure
                # here degrades toward MORE model turns (fail-open), never a blind
                # skip -- see attention.py module docstring.
                att_state = None
                att_scan = None
                digest_block = ""
                decision = None
                scan_error_detail = ""
                if attention_on:
                    try:
                        att_state = load_attention_state(transcript_dir, run_id, st.local)
                        # InGame (execute_write), NOT GameCore: DefenseTypes,
                        # GetCulturalIdentity, Game.GetWorldCongress and
                        # DiplomacyManager are nil in GameCore, so a GameCore
                        # scan ATTN_ERRs 4 families every turn -> perpetual
                        # SCAN_PARTIAL wakes (live-probe P1, turn 155).
                        scan_lines = await conn.execute_write(
                            build_attention_query(st.local, opts.attention.threat_radius)
                        )
                        att_scan = parse_attention_scan(scan_lines)
                        if att_scan is None:
                            # Parse-None was a silent SCAN_ERROR (live-probe P3,
                            # turns 190/212: empty detail, empty stderr). Carry a
                            # raw-line preview so it is diagnosable post-run,
                            # like SCAN_PARTIAL carries Lua error text.
                            scan_error_detail = (
                                "scan parse returned None; lines="
                                + repr(scan_lines)[:200]
                            )
                        elif state_before is None:
                            scan_error_detail = "overview snapshot missing"
                    except Exception as e:
                        att_scan = None
                        scan_error_detail = f"scan raised: {e!r}"[:200]
                        print(f"[arena] attention scan failed; waking: {e!r}", file=sys.stderr)
                    if att_state is None:
                        # first load raised: fresh state (== load's own failure
                        # result) without touching disk again -- fail-open, the
                        # SCAN_ERROR wake path takes it from here (the scan
                        # never ran in this branch) (review catch)
                        att_state = AttentionState(run_id=run_id, player_id=st.local)
                    task_event = any(
                        r.get("status") not in (None, "active") for r in task_results
                    )
                    try:
                        decision = evaluate(
                            attention_mode, att_state, att_scan, state_before,
                            max_streak=opts.attention.max_streak, task_event=task_event,
                            channel_wake_reasons=(
                                channel_admission.wake_reasons
                                if channel_admission is not None else ()
                            ),
                        )
                    except Exception as e:
                        # Corrupt persisted values (dict-shaped but wrong-typed,
                        # e.g. last_snapshot={"units":"5"} or directive
                        # wake_if=5) pass load's shape check and explode inside
                        # evaluate's comparisons. Contract (attention.py module
                        # docstring): state corrupt -> reset + wake, never
                        # abort (review-2 finding 1). note_wake on the fresh
                        # state rewrites the baselines and its save self-heals
                        # the file.
                        att_state = AttentionState(run_id=run_id, player_id=st.local)
                        decision = Decision("wake", "STATE_CORRUPT", repr(e)[:200])
                        print(f"[arena] attention evaluate failed; reset + wake: {e!r}",
                              file=sys.stderr)
                    if channel_turn_enabled and channel_admission is None and channel_error:
                        # Projection/runtime failures degrade toward a model
                        # turn, never a blind skip with missing private state.
                        decision = Decision("wake", "CHANNEL_ERROR", channel_error[:200])
                    if (
                        decision is not None
                        and decision.wake_cause == "SCAN_ERROR"
                        and not decision.wake_detail
                        and scan_error_detail
                    ):
                        decision = _dc_replace(decision, wake_detail=scan_error_detail)
                if decision is not None and decision.action == "sleep":
                    # A captured-but-slept channel participant still closes
                    # its admission: this supplies the second authoritative
                    # observation and finalizes any inclusive deadline only
                    # after the seat had its chance to wake.
                    await _finish_channel_turn(None)
                    prev_snapshot = att_state.last_snapshot
                    task_notes = [
                        f"{r.get('kind', '?')} {r.get('action', '')}: {r.get('result', '')}"
                        for r in task_results
                    ]
                    att_state = note_sleep(
                        att_state, turn=st.turn, snapshot=state_before,
                        scan_scalars=scan_scalars(att_scan),
                        task_notes=task_notes, notifications=list(att_scan.notifications),
                    )
                    try:
                        save_attention_state(transcript_dir, run_id, st.local, att_state)
                    except Exception as e:
                        print(f"[arena] attention state save failed: {e!r}", file=sys.stderr)
                    attention_fields = {
                        "mode": attention_mode, "decision": "slept",
                        "directive": att_state.directive,
                        "skips_remaining": att_state.skips_remaining,
                        "streak": att_state.streak, "wake_cause": None,
                    }
                    log.append({
                        "player": st.local, "turn": st.turn,
                        "slept": True, "attention": attention_fields,
                        **(
                            {"channels": _channel_fields()}
                            if channel_turn_enabled else {}
                        ),
                    })
                    if _tx_on:
                        state_delta = _state_delta(prev_snapshot, state_before)
                        _pol_backend = getattr(pol, "backend", None)
                        transcript.write({
                            "schema_version": 1,
                            "run_id": run_id,
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "player_id": st.local,
                            "turn": st.turn,
                            "provider": getattr(pol, "provider", "local"),
                            "model": getattr(_pol_backend, "model", getattr(pol, "model", "")),
                            "driver": _transcript_driver(pol),
                            "turn_kind": "slept",
                            "slept": True,
                            "step_count": 0,
                            "usd": 0.0,
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "state_before": prev_snapshot,
                            "state_after": state_before,
                            "state_delta": state_delta,
                            "standing_memory": {
                                "loaded": bool(memory), "injected": False,
                                "injected_chars": 0, "captured_chars": 0,
                                "error": memory_error,
                            },
                            "task_tracker": {
                                "active_before": len(active_tasks_before),
                                "pre_model_results": task_results,
                                "active_after": len(active_tasks_after),
                                "error": task_tracker_error,
                            },
                            "attention": attention_fields,
                            **(
                                {"channels": _channel_fields()}
                                if channel_turn_enabled else {}
                            ),
                        })
                    await hook.finish_units(conn, st.local)
                    slept += 1
                    game_turns += 1
                    if seat0_state.needs_drain and not admission_open():
                        # This slept turn spent the final slot while a seat-0
                        # turn is still draining; disable the hook after
                        # servicing the puppet and before releasing it, so
                        # nothing is captured once the budget is gone.
                        await disable_hook_for_drain()
                    await hook.restore_local(conn, 0)
                    deadline_polls -= 1
                    continue
                if decision is not None and att_state.slept:
                    try:
                        digest_block = render_digest(
                            att_state, wake_turn=st.turn,
                            wake_cause=decision.wake_cause or "",
                            wake_detail=decision.wake_detail,
                        )
                    except Exception as e:
                        # A tampered slept record (e.g. missing "turn") or a
                        # render regression must cost the digest DETAIL, not
                        # the run -- and not the FACT of the sleep: an empty
                        # block silently erased the whole recap (review-3 f6).
                        digest_block = (
                            f"== WHILE YOU SLEPT ({len(att_state.slept)} turns; "
                            f"digest unavailable: {e!r}) =="
                        )[:DIGEST_MAX_CHARS]
                        print(f"[arena] wake digest render failed: {e!r}", file=sys.stderr)

                # Gate every injected kwarg on the policy's signature (the
                # briefing precedent): a pre-slice-3 policy with a bare
                # (gs, player_id, turn) __call__ must keep working.
                policy_kwargs = {
                    name: value
                    for name, value in (
                        ("memory_block", memory_block),
                        ("task_block", task_block),
                        ("digest_block", digest_block),
                    )
                    if _policy_accepts_kwarg(pol, name)
                }
                channel_policy_kwargs = {}
                if channel_turn_enabled:
                    for name, value in (
                        (
                            "channel_context",
                            channel_admission.context
                            if channel_admission is not None else None,
                        ),
                        (
                            "channel_block",
                            channel_admission.block
                            if channel_admission is not None else "",
                        ),
                        ("master_block", ""),
                    ):
                        if _policy_accepts_kwarg(pol, name):
                            policy_kwargs[name] = value
                            channel_policy_kwargs[name] = value
                if is_seat0:
                    capture = _seat0_capture_for(st.turn)
                    if capture is not None:
                        capture["policy_kwargs"] = dict(channel_policy_kwargs)
                # Capability snapshot (spec §1): once per puppet turn, cheap
                # GameCore read. Signature-gated like every injected kwarg;
                # ANY failure fails open (no kwarg -> agent uses full tier).
                if _policy_accepts_kwarg(pol, "caps"):
                    caps = None
                    try:
                        cap_lines = await conn.execute_read(
                            build_caps_query(st.local)
                        )
                        caps = parse_caps(cap_lines)
                        if caps is None:
                            print(
                                "[arena] capability snapshot unparseable; "
                                "fail-open full toolset",
                                file=sys.stderr,
                            )
                    except Exception as e:
                        print(
                            f"[arena] capability snapshot failed; "
                            f"fail-open full toolset: {e!r}",
                            file=sys.stderr,
                        )
                    if caps is not None:
                        policy_kwargs["caps"] = caps
                if (
                    exclusive
                    and opts.briefing.enabled
                    and _policy_accepts_kwarg(pol, "briefing")
                ):
                    try:
                        playbook_chars = (
                            len(load_playbook()) if opts.playbook == "condensed" else 0
                        )
                        policy_kwargs["briefing"] = await maybe_build_briefing(
                            gs,
                            opts,
                            n_ctx=explicit_n_ctx(opts.context_budget),
                            playbook_chars=playbook_chars,
                            tool_schema_chars=0,
                        )
                    except Exception as e:
                        # A per-civ briefing-build failure (a missing playbook
                        # file, a budget-calc raise) must degrade THIS civ to no
                        # briefing, never abort the whole multi-civ run --
                        # mirroring the memory/task-tracker load guards above and
                        # the promotion-sweep guard below. Omitting the kwarg is
                        # the same state a non-exclusive turn uses, so the policy
                        # already tolerates its absence.
                        print(f"[arena] briefing build failed: {e!r}", file=sys.stderr)

                if exclusive and conn.is_connected:
                    await conn.disconnect()       # free the single tuner slot for the CLI

                if is_seat0:
                    # ===== SEAT-0 AUTHORITY FLOW (Tasks 5-6) ===============
                    # A normal policy call plus an optional one-shot focused
                    # repair are ONE logical turn and ONE shared budget charge.
                    # The seat stays local throughout (no restore_local(0) in
                    # this body); the coordinator owns finish_units / mechanical
                    # cleanup / recovery save / end_turn. The policy keeps full
                    # strategic authority — the coordinator chooses nothing.
                    # (`_mech_pass` is defined once before the loop so the
                    # RECHECK re-fire path can reuse it.)

                    # Interruption-safe record skeleton: assigned BEFORE the
                    # first await of the logical turn so a BaseException at
                    # ANY point (the long CLI policy call, the mechanical
                    # pass, the recovery save) leaves a record the finally
                    # block can terminalize as `interrupted`. The played and
                    # human_pending paths overwrite this with the fully-built
                    # record; the skeleton is only ever written on interrupt.
                    seat0_state.record = {
                        "schema_version": 1,
                        "run_id":   run_id,
                        "ts":       datetime.now(timezone.utc).isoformat(),
                        "player_id": 0,
                        "turn":     st.turn,
                        "provider": getattr(pol, "provider", "local"),
                        "model":    getattr(pol, "model", ""),
                        "driver":   _transcript_driver(pol),
                        "steps": [],
                        "step_count": 0,
                        "usd": 0.0,
                        "state_before": state_before,
                        "state_after": None,
                        "state_delta": None,
                        "turn_kind": "failed",
                        "seat0": {
                            "normal": {"completed": False, "summary": "", "error": ""},
                            "repair": {"attempted": False, "completed": False,
                                       "summary": "", "error": ""},
                            "blocker_snapshots": [],
                            "mechanical_cleanup": [],
                            "automation_errors": [],
                            "end_turn_errors": [],
                            "autosave": {"name": "", "attempts": []},
                            "end_turn_requests": 0,
                            "terminal_state": "",
                        },
                    }
                    if channel_turn_enabled:
                        seat0_state.record["channels"] = _channel_fields()

                    # --- Normal attempt (tuner already released if exclusive). --
                    normal_result = None
                    normal_error = ""       # repr for the record
                    normal_error_msg = ""   # str for the pilot-facing repair block
                    try:
                        normal_result = await pol(gs, 0, st.turn, **policy_kwargs)
                        timeout_error = seat0.attempt_timeout_error(normal_result)
                        if timeout_error:
                            # A timed-out CLI attempt returns the timeout shape
                            # instead of raising: zero usable work happened, so
                            # account it exactly like a raised attempt (the
                            # repair still gets its one shot).
                            normal_error = timeout_error
                            normal_error_msg = timeout_error
                            print(f"[arena] seat-0 turn {st.turn} normal policy "
                                  f"timed out: {timeout_error}", file=sys.stderr)
                            log.append({"turn": st.turn, "player_id": 0,
                                        "skipped": True, "error": normal_error})
                    except Exception as e:
                        normal_error = repr(e)
                        normal_error_msg = str(e)
                        print(f"[arena] seat-0 turn {st.turn} normal policy failed: {e!r}",
                              file=sys.stderr)
                        log.append({"turn": st.turn, "player_id": 0,
                                    "skipped": True, "error": normal_error})
                    _capture_seat0_policy_result(st.turn, normal_result)
                    # ONE shared budget charge for the whole logical turn.
                    seat0_state.mark_policy_played()
                    remaining -= 1
                    game_turns += 1
                    caps_kwarg = policy_kwargs.get("caps")

                    blocker_snapshots: list = []
                    cleanup_records: list = []
                    automation_errors: list[dict] = []
                    after_blockers: list = []
                    groups = seat0.classify_blockers([])
                    repair_attempted = False
                    repair_result = None
                    repair_error = ""

                    # A RETURNED normal attempt declares itself complete: run the
                    # mechanical pass. A RAISED attempt did not — skip straight to
                    # repair (never finish_units on a failed attempt's behalf).
                    if normal_error == "":
                        if exclusive and not conn.is_connected:
                            await _reconnect_with_retry(conn)
                        (
                            after_blockers,
                            cleanup_records,
                            blocker_snapshots,
                            groups,
                            pass_errors,
                        ) = await _mech_pass("after_normal")
                        automation_errors.extend(pass_errors)

                    need_repair = (
                        not seat0_state.repair_used
                        and not groups.hard
                        and (normal_error != "" or bool(groups.decision))
                    )
                    if need_repair:
                        s0_repair = seat0_state.record["seat0"]["repair"]
                        repair_result, repair_mech = await _attempt_seat0_repair(
                            pol, s0_repair, after_blockers,
                            prior_error=normal_error_msg,
                            caps_kwarg=caps_kwarg, exclusive=exclusive,
                            turn=st.turn,
                            channel_kwargs=channel_policy_kwargs,
                        )
                        repair_attempted = s0_repair["attempted"]
                        repair_error = s0_repair["error"]
                        if repair_mech is not None:
                            (
                                after_blockers,
                                repair_cleanup,
                                repair_snaps,
                                groups,
                                pass_errors,
                            ) = repair_mech
                            automation_errors.extend(pass_errors)
                            cleanup_records = cleanup_records + repair_cleanup
                            blocker_snapshots = blocker_snapshots + repair_snaps

                    # --- Terminal decision -------------------------------------
                    normal_returned = normal_error == ""
                    repair_returned = repair_attempted and repair_error == ""
                    any_returned = normal_returned or repair_returned
                    turn_kind = "played" if any_returned else "failed"
                    remaining_blockers = list(groups.hard) + list(groups.decision)

                    if (
                        channel_turn_enabled
                        and any_returned
                        and not remaining_blockers
                    ):
                        # Keep the WC policy pass inside this admission. Its
                        # channel tools share the bound context, and its raw
                        # summary joins the persistent private result stream.
                        await _seat0_wc_gate(
                            pol, st.turn, channel_kwargs=channel_policy_kwargs
                        )
                    merged = seat0.merge_policy_attempts(normal_result, repair_result)
                    if channel_turn_enabled:
                        public_merged = _public_channel_result(merged)
                        public_normal_result = _public_channel_result(
                            normal_result or {}
                        )
                        public_repair_result = _public_channel_result(
                            repair_result or {}
                        )
                    else:
                        public_merged = merged
                        public_normal_result = normal_result or {}
                        public_repair_result = repair_result or {}
                    payload = public_merged["transcript"]
                    _pol_backend = getattr(pol, "backend", None)

                    # Standing-plan / task capture from the completed turn's
                    # summary (played path only; a failed/unfinished turn has no
                    # authoritative plan to persist).
                    captured_plan = ""
                    if any_returned and not remaining_blockers and opts.standing_plan_enabled:
                        final_summary = (
                            payload.get("final_summary") or merged.get("summary", "")
                        )
                        captured_plan = extract_standing_plan(
                            final_summary, opts.standing_plan_capture_chars
                        )
                        if opts.memory.enabled and captured_plan:
                            try:
                                save_memory(transcript_dir, run_id, 0, st.turn,
                                            captured_plan, opts.memory.max_chars)
                            except Exception as e:
                                memory_error = repr(e)
                                print(f"[arena] standing memory save failed: {e!r}",
                                      file=sys.stderr)
                        if opts.task_tracker.enabled and task_capture_base is not None:
                            try:
                                new_tasks = parse_task_lines(final_summary, st.turn)
                                merged_tasks = merge_tasks(
                                    task_capture_base, new_tasks, opts.task_tracker.max_tasks
                                )
                                captured_state = save_task_state(
                                    transcript_dir, run_id, 0, merged_tasks
                                )
                                active_tasks_after = tuple(
                                    t for t in captured_state.tasks if t.status == "active"
                                )
                            except Exception as e:
                                task_tracker_error = repr(e)
                                print(f"[arena] task tracker capture failed: {e!r}",
                                      file=sys.stderr)

                    injected_block = policy_kwargs.get("memory_block", "")
                    _standing_memory_fields = {
                        "loaded": bool(memory),
                        "injected": bool(injected_block),
                        "injected_chars": len(injected_block),
                        "captured_chars": len(captured_plan) if opts.memory.enabled else 0,
                        "error": memory_error,
                    }
                    _task_tracker_fields = {
                        "active_before": len(active_tasks_before),
                        "pre_model_results": task_results,
                        "active_after": len(active_tasks_after),
                        "error": task_tracker_error,
                    }
                    state_after = await _overview_snapshot(gs) if _tx_on else None

                    def _base_seat0_record(autosave):
                        record = {
                            **payload,
                            "schema_version": 1,
                            "run_id":   run_id,
                            "ts":       datetime.now(timezone.utc).isoformat(),
                            "player_id": 0,
                            "turn":     st.turn,
                            "provider": getattr(pol, "provider", "local"),
                            "model":    getattr(_pol_backend, "model", getattr(pol, "model", "")),
                            "driver":   _transcript_driver(pol),
                            "step_count": len(payload.get("steps", [])),
                            "usd":      float(merged.get("usage", {}).get("usd", 0.0)),
                            "state_before": state_before,
                            "state_after":  state_after,
                            "state_delta":  _state_delta(state_before, state_after),
                            "standing_memory": _standing_memory_fields,
                            "task_tracker": _task_tracker_fields,
                            "turn_kind": turn_kind,
                            "seat0": {
                                "normal": {
                                    "completed": normal_returned,
                                    "summary": public_normal_result.get("summary", ""),
                                    "error": normal_error,
                                },
                                "repair": {
                                    "attempted": repair_attempted,
                                    "completed": repair_returned,
                                    "summary": public_repair_result.get("summary", ""),
                                    "error": repair_error,
                                },
                                "blocker_snapshots": blocker_snapshots,
                                "mechanical_cleanup": cleanup_records,
                                "automation_errors": list(automation_errors),
                                "end_turn_errors": [],
                                "autosave": autosave,
                                "end_turn_requests": 0,  # refreshed terminally
                                "terminal_state": "",    # set exactly once
                            },
                        }
                        if channel_turn_enabled:
                            record["channels"] = _channel_fields()
                        return record

                    if (
                        any_returned
                        and remaining_blockers
                        and all(
                            b["type"] in _WC_SESSION_TYPES
                            for b in remaining_blockers
                        )
                    ):
                        # A live WC session blocking BEFORE any end request
                        # (a resumed session with real resolutions): the
                        # repair pass above was the pilot's voting chance,
                        # and the blocker itself only clears INSIDE
                        # ACTION_ENDTURN -- ensure a voter and fire.
                        await _seat0_ensure_wc_voter(st.turn)
                        remaining_blockers = []
                    if any_returned and not remaining_blockers:
                        # PLAYED: WC gate first (votes must exist BEFORE the
                        # fire -- the congress runs inside ACTION_ENDTURN),
                        # then best-effort recovery anchor, then one end
                        # request. The record is written when the turn advances.
                        if not channel_turn_enabled:
                            # Keep the exact pre-channel ordering for disabled
                            # seats. Enabled seats ran this pass before channel
                            # terminalization while their one capture remained
                            # active across any later recovery passes.
                            await _seat0_wc_gate(pol, st.turn)
                        anchor = await seat0.save_recovery_anchor(conn, st.turn)
                        autosave_attempts = []
                        if not anchor.get("ok", False):
                            autosave_attempts.append(anchor)
                        seat0_state.record = _base_seat0_record(
                            {
                                # A failed save is never adopted as the
                                # recovery point -- ok is authoritative.
                                "name": anchor.get("name", "")
                                if anchor.get("ok", False) else "",
                                "attempts": autosave_attempts,
                            }
                        )
                        # Carry the resume context for a possible RECHECK re-fire.
                        seat0_state.resume_context = seat0.Seat0ResumeContext(
                            policy=pol,
                            caps=caps_kwarg,
                            exclusive=exclusive,
                        )
                        if not admission_open():
                            # Final admission: disable the hook while seat 0 is
                            # still active, before the turn releases into AI.
                            await disable_hook_for_drain()
                        if seat0_state.may_fire_end_turn:
                            await _fire_seat0_end(seat0_state.record)
                    else:
                        # HUMAN_PENDING: a hard/inaccessible blocker, a decision
                        # blocker still open after the one repair, or a fully
                        # failed attempt. No recovery save, no end request.
                        record = _base_seat0_record({"name": "", "attempts": []})
                        if not admission_open():
                            # Final admission handed to the human: disable first;
                            # the human may advance into AI immediately.
                            await disable_hook_for_drain()
                        _seat0_enter_human_pending(
                            turn=st.turn, blockers=remaining_blockers, record=record,
                            turn_kind=turn_kind, normal_error=normal_error,
                            repair_error=repair_error,
                        )
                    continue

                try:
                    result = await pol(gs, st.local, st.turn, **policy_kwargs)
                    puppet_capture = _puppet_capture_for(st.local, st.turn)
                    if puppet_capture is not None:
                        puppet_capture["policy_result"] = result
                except Exception as e:
                    # A single failed LLM turn -- e.g. the gateway 500s on a malformed/
                    # truncated tool call (openai.InternalServerError) -- must degrade THIS
                    # puppet turn, never abort the whole multi-turn run. Mirrors the
                    # sweep/memory/task/briefing guards below and the human-safety invariant:
                    # reclaim the tuner (an exclusive CLI turn released it), hand the seat
                    # back to the human, consume the puppet-turn budget, and continue.
                    # Exception (not BaseException) so a CancelledError/Ctrl-C still unwinds
                    # to the finally's guarded handback.
                    raw_policy_error = repr(e)
                    if live_gate_driver is not None:
                        prior_error = channel_fields_state["error"]
                        channel_fields_state["error"] = (
                            f"{prior_error}; {raw_policy_error}"
                            if prior_error
                            else raw_policy_error
                        )
                    policy_detail = (
                        "live_gate_policy_failed"
                        if live_gate_driver is not None
                        else raw_policy_error
                    )
                    print(f"[arena] puppet turn seat {st.local} turn {st.turn} failed, "
                          f"skipping: {policy_detail}", file=sys.stderr)
                    failed_entry = {
                        "turn": st.turn,
                        "player_id": st.local,
                        "skipped": True,
                        "error": policy_detail,
                    }
                    log.append(failed_entry)
                    if attention_on and att_state is not None and att_state.skips_remaining > 0:
                        # Spec section 3: ANY wake cancels the directive remainder.
                        # This failed turn WAS a wake decision -- note_wake never
                        # runs on this path, so cancel here or the seat resumes a
                        # stale sleep right after the system misbehaved
                        # (final-review Important 2). Keeps the slept accumulator
                        # so the digest survives to the eventual successful wake.
                        att_state = cancel_remainder(att_state)
                        try:
                            save_attention_state(transcript_dir, run_id, st.local, att_state)
                        except Exception as save_exc:
                            print(f"[arena] attention state save failed: {save_exc!r}",
                                  file=sys.stderr)
                    if not conn.is_connected:
                        await _reconnect_with_retry(conn)
                    await _finish_channel_turn(None)
                    if channel_turn_enabled:
                        failed_entry["channels"] = _channel_fields()
                    gate_signal_after_capture = False
                    if live_gate_driver is not None:
                        await live_gate_driver.after_seat_capture(
                            player_id=st.local,
                            turn=st.turn,
                            channel_fields=_private_channel_fields(),
                        )
                        gate_signal_after_capture = (
                            live_gate_driver.pending_signal() is not None
                        )
                    await hook.finish_units(conn, st.local)
                    await hook.restore_local(conn, 0)
                    remaining -= 1
                    game_turns += 1
                    deadline_polls -= 1
                    if gate_signal_after_capture:
                        break
                    continue
                if exclusive and not conn.is_connected:
                    await _reconnect_with_retry(conn)   # reclaim before we end the turn
                await _finish_channel_turn(result)
                public_result = (
                    _public_channel_result(result)
                    if channel_turn_enabled
                    else result
                )
                # Seat 0 handled its own turn above (repair/end-turn/human-pending)
                # and continued; only puppet turns reach here.
                try:
                    swept = await autoresolve.sweep_promotions(gs)
                except Exception as e:
                    swept = []
                    print(f"[arena] promotion sweep failed: {e!r}", file=sys.stderr)

                # --- Capture this turn's standing plan / tasks from the final summary.
                # Runs whenever standing-plan capture is enabled, since memory and
                # task tracking both parse the same STANDING PLAN block.
                captured_plan = ""
                final_summary = ""
                if opts.standing_plan_enabled or opts.attention_directives_enabled:
                    final_summary = (
                        public_result.get("transcript", {}).get("final_summary")
                        or public_result.get("summary", "")
                    )
                if opts.standing_plan_enabled:
                    captured_plan = extract_standing_plan(
                        final_summary,
                        opts.standing_plan_capture_chars,
                    )
                # Save even when the turn-start load/format failed: save_memory
                # is a full atomic overwrite with no dependence on the loaded
                # object, so persisting the model's fresh plan both keeps it and
                # self-heals a poison file. Gating on `not memory_error` instead
                # discarded the new plan and left the bad file to fail every
                # subsequent turn.
                if opts.memory.enabled and captured_plan:
                    try:
                        save_memory(
                            transcript_dir, run_id, st.local, st.turn, captured_plan,
                            opts.memory.max_chars,
                        )
                    except Exception as e:
                        memory_error = repr(e)
                        print(f"[arena] standing memory save failed: {e!r}", file=sys.stderr)
                if opts.task_tracker.enabled and task_capture_base is not None:
                    try:
                        # Parse from the raw summary, not the captured plan: the
                        # capture clamp must never cost us a trailing TASK line.
                        new_tasks = parse_task_lines(final_summary, st.turn)
                        merged = merge_tasks(task_capture_base, new_tasks, opts.task_tracker.max_tasks)
                        captured_state = save_task_state(transcript_dir, run_id, st.local, merged)
                        active_tasks_after = tuple(
                            t for t in captured_state.tasks if t.status == "active"
                        )
                    except Exception as e:
                        task_tracker_error = repr(e)
                        print(f"[arena] task tracker capture failed: {e!r}", file=sys.stderr)

                # Attention needs the POST-play snapshot as the next wake
                # baseline even with transcripts off (review-2 finding 2) --
                # note_wake's state_before fallback would otherwise bake the
                # puppet's own turn into the next quiet-turn delta.
                state_after = (
                    await _overview_snapshot(gs) if (_tx_on or attention_on) else None
                )
                directive = None
                directive_ack = ""
                wake_attention_fields = None
                if attention_on and att_state is not None:
                    if opts.attention_directives_enabled:
                        directive = parse_directive(final_summary, opts.attention.max_skip)
                        if directive is not None:
                            note = " (clamped)" if directive.clamped else ""
                            directive_ack = f"SKIP {directive.skip} accepted{note}"
                            if directive.unknown_tokens:
                                directive_ack += (
                                    f"; unknown tokens dropped: {','.join(directive.unknown_tokens)}"
                                )
                        elif has_directive_lines(final_summary):
                            directive_ack = "directive not recognized"
                    wake_cause = decision.wake_cause if decision is not None else None
                    wake_attention_fields = {
                        "mode": attention_mode, "decision": "woke",
                        "wake_cause": wake_cause,
                        "wake_detail": (
                            decision.wake_detail if decision is not None else ""
                        ),
                        "directive": (
                            {"skip": directive.skip, "wake_if": list(directive.wake_if)}
                            if directive else None
                        ),
                        "digest_chars": len(digest_block),
                        "directive_ack": directive_ack,
                    }
                    att_state = note_wake(
                        att_state, turn=st.turn,
                        wake_cause=wake_cause or "", directive=directive,
                        directive_ack=directive_ack,
                        snapshot=state_after if state_after is not None else state_before,
                        scan_scalars=scan_scalars(att_scan) if att_scan is not None else None,
                    )
                    try:
                        save_attention_state(transcript_dir, run_id, st.local, att_state)
                    except Exception as e:
                        print(f"[arena] attention state save failed: {e!r}", file=sys.stderr)
                _log_entry = {
                    k: v
                    for k, v in public_result.items()
                    if k not in ("transcript", "promotion_sweep")
                }
                # Report what actually reached the model, not what was loaded:
                # the kwarg gate strips memory_block for a policy whose __call__
                # doesn't accept it, and analyze.behavior_metrics counts these
                # as standing-memory turns -- so a stripped block must read as
                # not injected.
                injected_block = policy_kwargs.get("memory_block", "")
                # Same rule on the capture side: extraction also feeds the task
                # tracker, so with memory disabled captured_plan can be non-empty
                # while nothing is ever saved or injectable -- report 0 or a
                # tracker-only civ reads as a standing-memory-captured turn.
                _standing_memory_fields = {
                    "loaded": bool(memory),
                    "injected": bool(injected_block),
                    "injected_chars": len(injected_block),
                    "captured_chars": len(captured_plan) if opts.memory.enabled else 0,
                    "error": memory_error,
                }
                _task_tracker_fields = {
                    "active_before": len(active_tasks_before),
                    "pre_model_results": task_results,
                    "active_after": len(active_tasks_after),
                    "error": task_tracker_error,
                }
                log.append({
                    "player": st.local,
                    "turn": st.turn,
                    **_log_entry,
                    "promotion_sweep": swept,
                    "standing_memory": _standing_memory_fields,
                    "task_tracker": _task_tracker_fields,
                    **(
                        {"channels": _channel_fields()}
                        if channel_turn_enabled else {}
                    ),
                })
                # Puppet-only transcript + handback (seat 0 returned above).
                if _tx_on and public_result.get("transcript"):
                    payload = public_result["transcript"]
                    steps = payload.get("steps", [])
                    state_delta = _state_delta(state_before, state_after)
                    _pol_backend = getattr(pol, "backend", None)
                    record = {
                        **payload,
                        "schema_version": 1,
                        "run_id":   run_id,
                        "ts":       datetime.now(timezone.utc).isoformat(),
                        "player_id": st.local,
                        "turn":     st.turn,
                        "provider": getattr(pol, "provider", "local"),
                        "model":    getattr(_pol_backend, "model", getattr(pol, "model", "")),
                        "driver":   _transcript_driver(pol),
                        "step_count": len(steps),
                        "usd":      float(public_result.get("usage", {}).get("usd", 0.0)),
                        "state_before": state_before,
                        "state_after":  state_after,
                        "state_delta":  state_delta,
                        "promotion_sweep": swept,
                        "standing_memory": _standing_memory_fields,
                        "task_tracker": _task_tracker_fields,
                        "turn_kind": "played",
                    }
                    if wake_attention_fields is not None:
                        record["attention"] = wake_attention_fields
                    if channel_turn_enabled:
                        record["channels"] = _channel_fields()
                    write_record = True
                    if live_gate_driver is not None:
                        try:
                            write_record = live_gate_driver.inspect_pending_transcript_record(
                                st.local, st.turn, record
                            )
                        except Exception as exc:
                            raw_inspection_error = repr(exc)
                            prior_error = channel_fields_state["error"]
                            channel_fields_state["error"] = (
                                f"{prior_error}; {raw_inspection_error}"
                                if prior_error
                                else raw_inspection_error
                            )
                            _print_private_error(
                                "[arena] live gate pending-record inspection failed",
                                exc,
                            )
                            write_record = False
                    if write_record:
                        transcript.write(record)
                gate_signal_after_capture = False
                if live_gate_driver is not None:
                    await live_gate_driver.after_seat_capture(
                        player_id=st.local,
                        turn=st.turn,
                        channel_fields=_private_channel_fields(),
                    )
                    gate_signal_after_capture = (
                        live_gate_driver.pending_signal() is not None
                    )
                # End this puppet's turn and hand control back toward the human.
                # DESIGN NOTE — the turn-end method is validated by the live dry-run gate (Task 9).
                # Primary (verified in the feasibility spike): finish_units(K) + restore_local(0).
                # If the live gate shows the engine does NOT advance / hand back cleanly, add an
                # InGame `UI.RequestAction(ActionTypes.ACTION_ENDTURN)` HERE — while local == K,
                # before restore_local. NEVER add it in the finally block (local is already 0 there).
                await hook.finish_units(conn, st.local)
                remaining -= 1
                game_turns += 1
                if seat0_state.needs_drain and not admission_open():
                    # This puppet spent the final slot while a seat-0 turn is
                    # still draining; disable the hook after servicing the
                    # puppet and before releasing it, so nothing is captured
                    # once the budget is gone.
                    await disable_hook_for_drain()
                await hook.restore_local(conn, 0)
                played += 1
                if gate_signal_after_capture:
                    break
            else:
                if seat0_state.needs_drain:
                    # An in-flight seat-0 turn is draining (end request fired /
                    # AI processing / human pending): wait quietly for the turn
                    # number to flip. The end-fired and AI-processing waits are
                    # GameCore-only polling; the human-pending arm below may
                    # additionally issue InGame calls (the orphan-session
                    # sweep) because seat 0 is local while that phase holds.
                    # Each wait charges the budget matching WHY we are
                    # waiting -- never the puppet-era idle budget.
                    await asyncio.sleep(1.0)
                    if seat0_state.phase is Seat0Phase.HUMAN_PENDING:
                        # A human-idle window: keep the orphan-session sweep
                        # cadence alive. Sessions involving the local player
                        # are skipped by the sweep by construction, so this
                        # never touches a leader scene the human is using.
                        # Accepted hazard (same as the outer idle-path sweep
                        # below): once the human resolves the blocker and
                        # ends the turn, phase stays HUMAN_PENDING until the
                        # turn number flips, so a sweep can still land during
                        # that AI window -- the sweep never raises, so this is
                        # harmless if it does.
                        idle_streak += 1
                        if idle_streak % ORPHAN_SWEEP_IDLE_POLLS == 0:
                            swept_sessions = await _sweep_orphan_sessions(conn)
                            if swept_sessions not in ("ORPHANS|none", "?", "err"):
                                print(f"[arena] orphan diplomacy sessions closed "
                                      f"after {idle_streak} idle polls: "
                                      f"{swept_sessions}", file=sys.stderr)
                                log.append({
                                    "turn": st.turn,
                                    "orphan_sweep": swept_sessions,
                                })
                        human_polls += 1
                        if human_polls % SEAT0_DIPLO_IDLE_POLLS == 0:
                            # Safety net: a deal/session can be what is
                            # actually holding a human_pending turn (or can
                            # arrive while one is held). Seat 0 is local in
                            # this phase, so the InGame probe is legal; the
                            # pass shares the per-turn attempt bound.
                            await _seat0_diplomacy_pass_if_wedged(
                                seat0_state.turn
                            )
                        if human_polls >= config.seat0_human_pending_poll_limit:
                            await _finish_seat0_channel_capture()
                            _write_seat0_record_once()
                            log.append({
                                "level": "CRITICAL",
                                "event": "seat0_human_pending_deadline",
                                "turn": seat0_state.turn,
                                "polls": human_polls,
                            })
                            print(
                                f"[arena] CRITICAL seat0_human_pending_deadline: "
                                f"turn {seat0_state.turn} unresolved after "
                                f"{human_polls} polls; ending the run",
                                file=sys.stderr,
                            )
                            break
                    elif poll_action is Seat0Poll.DEGRADED:
                        deadline_polls -= 1
                    else:
                        drain_polls += 1
                        if drain_polls % SEAT0_DIPLO_DRAIN_POLLS == 0:
                            # A deal can also arrive DURING the post-end-turn
                            # AI phase and wedge it (observed live: player 1
                            # opened a session with seat 0 and the whole
                            # interturn froze). One InGame probe on this slow
                            # cadence is the accepted trade-off against the
                            # otherwise-guaranteed drain deadline.
                            if await _seat0_diplomacy_pass_if_wedged(
                                seat0_state.turn
                            ):
                                drain_polls = 0
                                continue
                        if drain_polls >= config.seat0_drain_poll_limit:
                            await _finish_seat0_channel_capture()
                            log.append({
                                "level": "CRITICAL",
                                "event": "seat0_drain_deadline",
                                "turn": seat0_state.turn,
                                "phase": str(seat0_state.phase),
                                "polls": drain_polls,
                            })
                            print(
                                f"[arena] CRITICAL seat0_drain_deadline: turn "
                                f"{seat0_state.turn} stuck in {seat0_state.phase} "
                                f"after {drain_polls} polls; game presumed hung",
                                file=sys.stderr,
                            )
                            break
                    continue
                if (
                    0 in enabled_channel_players
                    and seat0_spec is not None
                    and st.turn >= 0
                    and not st.seat0_active
                    and admission_open()
                    and (idle_streak + 1) % SEAT0_DIPLO_IDLE_POLLS == 0
                ):
                    # A channel-enabled idle diplomacy pass is itself seat-0
                    # policy work. Admit its persistent capture before an
                    # unseated observation so the turn gets exactly one
                    # private pre/post bracket shared with a later normal or
                    # drain pass.
                    if await _seat0_diplomacy_pass_if_wedged(st.turn):
                        idle_streak = 0
                        deadline_polls = config.idle_poll_limit
                        continue
                if (
                    channel_runtime is not None
                    and st.turn >= 0
                    and not poll_channel_error
                    and _seat0_capture_for(st.turn) is None
                ):
                    try:
                        await channel_runtime.poll_unseated(
                            gs, st.turn, st.local
                        )
                    except Exception as e:
                        error = repr(e)
                        _print_private_error(
                            "[arena] channel unseated poll failed", e
                        )
                        log.append({
                            "event": "channel_error",
                            "stage": "poll_unseated",
                            "turn": st.turn,
                            "player_id": st.local,
                            "error": _public_channel_error(error),
                        })
                # Human seat is idle. Do NOT auto-clear VIEW-level diplomacy here:
                # _clear_blocking_diplomacy cannot distinguish an orphaned first-meet
                # greeting from a leader scene the human is actively using (declaring
                # war, denouncing, trading), and force-hiding the latter mid-transition
                # can black out the map — it stays a reactive/manual tool.
                #
                # SESSION-level orphans are different: an open session between two
                # non-local players (a greeting queued for/between puppet seats) can
                # never be clicked by the human and wedges the AI phase indefinitely,
                # so after a long idle streak sweep those closed. The sweep skips
                # every session involving the local player by construction.
                idle_streak += 1
                if (
                    seat0_spec is not None
                    and 0 not in enabled_channel_players
                    and st.turn >= 0
                    and not st.seat0_active
                    and admission_open()
                    and idle_streak % SEAT0_DIPLO_IDLE_POLLS == 0
                ):
                    # Seat 0 held inactive this long is the deal-wedge
                    # signature: an AI opened a session with the human seat
                    # and the turn cycle is stopped until it is answered.
                    if await _seat0_diplomacy_pass_if_wedged(st.turn):
                        idle_streak = 0
                        deadline_polls = config.idle_poll_limit
                        continue
                if idle_streak % ORPHAN_SWEEP_IDLE_POLLS == 0:
                    swept_sessions = await _sweep_orphan_sessions(conn)
                    if swept_sessions not in ("ORPHANS|none", "?", "err"):
                        print(f"[arena] orphan diplomacy sessions closed after "
                              f"{idle_streak} idle polls: {swept_sessions}",
                              file=sys.stderr)
                        log.append({"turn": st.turn, "orphan_sweep": swept_sessions})
                await asyncio.sleep(1.0)
            deadline_polls -= 1
        return {
            "puppet_turns_played": played,
            "turns_slept": slept,
            "seat0_turns_played": seat0_played,
            "seat0_turns_failed": seat0_failed,
            "seat0_human_pending": seat0_pending,
            "log": log,
            **(
                {}
                if live_gate_driver is None
                else _gate_result_field()
            ),
        }
    finally:
        # Human safety invariant: ALWAYS hand control back. Reclaim a released connection first,
        # then restore the human, then disable — run all three best-effort so a failure in one
        # never skips the others. Each step is guarded against BaseException (not just Exception)
        # so an asyncio.CancelledError mid-handback (e.g. Ctrl-C during connect/restore) cannot
        # skip a later step.
        #
        # Re-raise policy: only interrupts (BaseException that is NOT a plain Exception —
        # CancelledError, KeyboardInterrupt, SystemExit) are re-raised; ordinary cleanup failures
        # (a dead-socket ConnectionError from reclaim, a transient hook.disable blip) are logged
        # and swallowed, matching the original best-effort contract. This is load-bearing: when
        # cancellation originates in the TRY BODY (Ctrl-C during the long CLI turn) it is already
        # in flight as we run cleanup; re-raising an ordinary cleanup Exception here would REPLACE
        # that in-flight CancelledError and swallow the cancellation. Swallowing best-effort
        # Exceptions lets the body's CancelledError keep propagating; re-raising a cleanup-origin
        # interrupt still surfaces it. Either way cancellation is propagated, never swallowed.
        #
        # Append-only seat-0 interruption record: a turn in flight with an
        # unwritten record (cancellation or error mid-turn) is terminalized
        # `interrupted` best-effort. A record already written (advanced /
        # human_pending) is never rewritten or duplicated. This runs BEFORE the
        # tuner handback and handles its own failures, so a transcript error can
        # neither mask an in-flight CancelledError nor skip the human-safety
        # cleanup below. Ordinary write failures are swallowed; interrupts are
        # retained until every human-safety cleanup step has been attempted.
        channel_interrupt = None
        if puppet_channel_capture is not None:
            try:
                await _finish_puppet_channel_capture(synthesize_if_missing=True)
            except BaseException as e:
                if not isinstance(e, Exception):
                    channel_interrupt = e
                _print_private_error(
                    "[arena] WARNING: puppet channel finish failed in cleanup",
                    e,
                )
        if seat0_channel_capture is not None:
            try:
                await _finish_seat0_channel_capture()
            except BaseException as e:
                if not isinstance(e, Exception) and channel_interrupt is None:
                    channel_interrupt = e
                _print_private_error(
                    "[arena] WARNING: seat-0 channel finish failed in cleanup",
                    e,
                )
        record_interrupt = None
        if seat0_state.record is not None and not seat0_state.record_written:
            try:
                _s0 = seat0_state.record["seat0"]
                if _s0["terminal_state"] != "human_pending":
                    seat0_state.mark_interrupted()
                    # The turn's outcome never materialized (mirrors
                    # `regressed`): an interrupted turn must not be counted as
                    # played by analyze(), nor double-count with recovery.
                    seat0_state.record["turn_kind"] = "failed"
                    _s0["terminal_state"] = "interrupted"
                    _s0["end_turn_requests"] = seat0_state.end_turn_requests
                if _tx_on:
                    transcript.write(seat0_state.record)
                seat0_state.record_written = True
            except BaseException as e:
                if not isinstance(e, Exception):
                    record_interrupt = e
                print(f"[arena] WARNING: seat-0 interrupted-record write failed: "
                      f"{e!r}", file=sys.stderr)
        cleanup_interrupt = None
        steps = []
        if not conn.is_connected:
            steps.append(("reclaim-retry", lambda: _reconnect_with_retry(conn)))
        steps.append(("restore_local(0)", lambda: hook.restore_local(conn, 0)))
        steps.append(("hook.disable", lambda: hook.disable(conn)))
        for label, step in steps:
            try:
                await step()
            except BaseException as e:
                if not isinstance(e, Exception) and cleanup_interrupt is None:
                    cleanup_interrupt = e
                print(f"[arena] WARNING: {label} failed in cleanup: {e!r}", file=sys.stderr)
        if record_interrupt is not None:
            raise record_interrupt
        if channel_interrupt is not None:
            raise channel_interrupt
        if cleanup_interrupt is not None:
            raise cleanup_interrupt
