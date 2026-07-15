from __future__ import annotations
import asyncio
import inspect
import sys
from dataclasses import replace as _dc_replace
from datetime import datetime, timezone
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
from civ_mcp.arena.config import CivOptions, resolved_puppet_ids, validate_arena_config
from civ_mcp.arena.memory import (
    extract_standing_plan,
    format_memory_block,
    load_memory,
    save_memory,
)
from civ_mcp.arena.prompt_context import maybe_build_briefing
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
    """Numeric before→after delta plus the after-side research/civic strings;
    None when either snapshot is missing or malformed (unknown delta, degrade
    not abort — same contract as the inline puppet/slept delta sites)."""
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


# Deterministic production preference for the scripted repair pass, in priority
# order after any repair. All are tile-free (no policy-chosen placement).
_SCRIPTED_PREFERRED_PRODUCTION = (
    "BUILDING_MONUMENT",
    "BUILDING_GRANARY",
    "UNIT_SCOUT",
    "UNIT_WARRIOR",
)


class ScriptedPolicy:
    """Deterministic no-LLM policy with two roles (Task 9).

    * Every NORMAL call -- the global ``--dry-run`` compatibility mode AND each
      seat-0 normal attempt (empty ``blocker_block``): observe overview/units,
      skip unit 0, and deliberately choose NO research/production. Leaving the
      probe blocker is what makes the mixed stage-1 gate exercise the
      coordinator's real focused-repair path.
    * The seat-0 REPAIR call (non-empty ``blocker_block``): make deterministic
      research/civic/production choices for exactly the blocker types named in
      the repair block, using GameState methods only. It never ends the turn
      and never raises -- each read/action exception is folded into the
      returned summary so the coordinator makes no fallback of its own.

    ``provider``/``model`` are fixed identity for transcripts and fingerprints.
    ``blocker_block`` is keyword-only, mirroring the real policies (Task 4); the
    coordinator's signature gate then treats it exactly like ``caps`` et al.
    """

    provider = "scripted"
    model = "seat0-smoke"

    async def __call__(
        self, gs, player_id: int, turn: int, *, blocker_block: str = "", **kwargs
    ) -> dict:
        if blocker_block:
            return await self._repair(gs, blocker_block)
        # NORMAL / dry-run: observe, skip unit 0, choose nothing strategic.
        await gs.get_game_overview()
        await gs.get_units()
        try:
            await gs.skip_unit(0)
        except Exception as e:
            return {"summary": f"scripted: skip failed {e!r}", "actions": []}
        return {"summary": "scripted: observed + skipped unit 0", "actions": [{"tool": "skip_unit"}]}

    async def _repair(self, gs, blocker_block: str) -> dict:
        """Resolve only the blocker types named in ``blocker_block``. Any type
        without a scripted resolver (a governor/pantheon/etc. strategic choice)
        is left untouched, so the coordinator reaches human_pending after this
        single pass."""
        actions: list[dict] = []
        errors: list[str] = []
        want_tech = "ENDTURN_BLOCKING_RESEARCH" in blocker_block
        want_civic = "ENDTURN_BLOCKING_CIVIC" in blocker_block
        want_production = "ENDTURN_BLOCKING_PRODUCTION" in blocker_block

        if want_tech or want_civic:
            research_actions, research_errors = await self._choose_research(
                gs, tech=want_tech, civic=want_civic
            )
            actions.extend(research_actions)
            errors.extend(research_errors)
        if want_production:
            prod_actions, prod_errors = await self._choose_production(gs)
            actions.extend(prod_actions)
            errors.extend(prod_errors)

        if actions:
            body = ", ".join(f"{a['tool']}={a['item']}" for a in actions)
        else:
            body = "no eligible scripted choices"
        summary = f"scripted repair: {body}"
        if errors:
            summary += " | errors: " + "; ".join(errors)
        return {"summary": summary, "actions": actions}

    async def _choose_research(self, gs, *, tech: bool, civic: bool):
        """Pick the available tech/civic with key ``(turns, type_name)`` from a
        single ``get_tech_civics`` fetch."""
        actions: list[dict] = []
        errors: list[str] = []
        try:
            status = await gs.get_tech_civics()
        except Exception as e:
            return actions, [f"get_tech_civics failed {e!r}"]
        if tech:
            techs = list(status.available_techs or [])
            if techs:
                best = min(techs, key=lambda t: (t.turns, t.tech_type))
                try:
                    result = await gs.set_research(best.tech_type)
                    actions.append(
                        {"tool": "set_research", "item": best.tech_type, "result": result}
                    )
                except Exception as e:
                    errors.append(f"set_research({best.tech_type}) failed {e!r}")
            else:
                errors.append("no available techs to choose")
        if civic:
            civics = list(status.available_civics or [])
            if civics:
                best = min(civics, key=lambda c: (c.turns, c.civic_type))
                try:
                    result = await gs.set_civic(best.civic_type)
                    actions.append(
                        {"tool": "set_civic", "item": best.civic_type, "result": result}
                    )
                except Exception as e:
                    errors.append(f"set_civic({best.civic_type}) failed {e!r}")
            else:
                errors.append("no available civics to choose")
        return actions, errors

    async def _choose_production(self, gs):
        """Set production for every empty-queue city, preferring repairs, then
        the named tile-free items, then ``(turns, item_name)`` among UNIT/BUILDING
        options. Never picks a new district/wonder needing a tile target."""
        actions: list[dict] = []
        errors: list[str] = []
        try:
            cities, _warnings = await gs.get_cities()
        except Exception as e:
            return actions, [f"get_cities failed {e!r}"]
        for city in cities:
            current = str(getattr(city, "currently_building", "NONE") or "NONE").upper()
            if current not in ("", "NONE"):
                continue  # queue is already set; nothing to repair here
            try:
                options = await gs.list_city_production(city.city_id)
            except Exception as e:
                errors.append(f"list_city_production({city.city_id}) failed {e!r}")
                continue
            picked = self._pick_production(options)
            if picked is None:
                continue
            option, target_x, target_y = picked
            try:
                result = await gs.set_city_production(
                    city.city_id, option.category, option.item_name, target_x, target_y
                )
                actions.append({
                    "tool": "set_city_production",
                    "item": option.item_name,
                    "city_id": city.city_id,
                    "result": result,
                })
            except Exception as e:
                errors.append(
                    f"set_city_production({city.city_id},{option.item_name}) failed {e!r}"
                )
        return actions, errors

    @staticmethod
    def _pick_production(options):
        """Choose one tile-free production option (or None). Repairs carry their
        own coords; new districts (needing a policy-chosen tile) and projects are
        never selectable. Returns ``(option, target_x, target_y)``."""
        # A repair carries its own coords; a new UNIT/BUILDING needs no tile. A
        # non-repair DISTRICT needs a placement tile and a PROJECT is not a
        # buildable item here -- both are excluded. Wonders surface as BUILDING
        # with no distinguishing flag, but the named tile-free items below win
        # first (always present for a fresh city); the fallback never passes a
        # target, so a wonder that needs a plot simply fails to commit and the
        # coordinator reaches human_pending -- never a policy-chosen tile.
        candidates = [
            o for o in options
            if getattr(o, "is_repair", False) or o.category in ("UNIT", "BUILDING")
        ]
        if not candidates:
            return None
        # 1. Repairs first (deterministic by item name); pass their own coords.
        repairs = sorted(
            (o for o in candidates if getattr(o, "is_repair", False)),
            key=lambda o: o.item_name,
        )
        if repairs:
            best = repairs[0]
            return best, best.repair_x, best.repair_y
        # 2. Named tile-free items, in priority order.
        by_name = {o.item_name: o for o in candidates}
        for name in _SCRIPTED_PREFERRED_PRODUCTION:
            if name in by_name:
                return by_name[name], None, None
        # 3. Fallback: (turns, item_name) among the remaining UNIT/BUILDING options.
        best = min(candidates, key=lambda o: (o.turns, o.item_name))
        return best, None, None


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
        param.kind == inspect.Parameter.VAR_KEYWORD or param.name == name
        for param in signature.parameters.values()
    )


async def run_arena(conn, gs, config, policy=None, policy_for=None, transcript=None) -> dict:
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
    try:
        await hook.inject(conn, sorted(puppet_ids))
        hook_enabled = True  # flips to False once disable_hook_for_drain() fires
        remaining = config.max_puppet_turns
        deadline_polls = config.idle_poll_limit  # consecutive-idle poll budget; refilled on every captured turn
        idle_streak = 0  # consecutive idle polls since the last puppet capture
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

        def _terminalize_seat0_advanced() -> None:
            """Write the pending seat-0 record exactly once with terminal
            `advanced`, count the played turn, and reset for the next
            admission. Only ever reached on an observed turn-number change —
            never at ai_processing (append-once contract)."""
            nonlocal seat0_played
            if seat0_state.record is not None and not seat0_state.record_written:
                seat0_state.record["seat0"]["terminal_state"] = "advanced"
                seat0_state.record["seat0"]["end_turn_requests"] = (
                    seat0_state.end_turn_requests
                )
                if _tx_on:
                    transcript.write(seat0_state.record)
                seat0_state.record_written = True
                seat0_played += 1
            seat0_state.reset()

        def _seat0_enter_human_pending(
            *, turn: int, blockers: list, record: dict, turn_kind: str,
            normal_error: str, repair_error: str,
        ) -> None:
            """Terminalize an admitted seat-0 turn that the pilot could not
            finish: transition to `human_pending`, fill and write the record
            exactly once, emit exactly one structured CRITICAL event, and hand
            local seat 0 to the human untouched. Chooses NO strategic default —
            the unresolved blockers are left for the human to decide."""
            nonlocal seat0_pending, seat0_failed, deadline_polls
            seat0_state.mark_human_pending()
            deadline_polls = config.idle_poll_limit
            record["turn_kind"] = turn_kind
            record["seat0"]["terminal_state"] = "human_pending"
            record["seat0"]["end_turn_requests"] = seat0_state.end_turn_requests
            seat0_state.record = record
            if not seat0_state.record_written:
                if _tx_on:
                    transcript.write(record)
                seat0_state.record_written = True
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

        # Resume context for a RECHECK after an unsuccessful end request: the
        # in-flight turn's locals are gone once the played branch `continue`s,
        # so the policy / caps / exclusive flag it needs to re-fire or repair
        # are carried here. Set by the played branch, read by the RECHECK path.
        seat0_ctx: dict | None = None

        async def _mech_pass(prefix):
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

        async def _recheck_cleanup_repair_or_refire() -> None:
            """RECHECK: the previous end request did not take (seat 0 still
            active after the grace window). Re-run the mechanical pass on the
            still-open turn; if a decision blocker newly surfaced and the one
            repair is still unused, attempt it; then re-save the recovery
            anchor (same 0_MCP_NNNN name) and re-fire, or escalate to
            human_pending when a blocker persists or the three-request budget
            is spent. Seat 0 is local+active throughout (observe only returns
            RECHECK while active), so the InGame work here is legal."""
            record = seat0_state.record
            turn = record["turn"]
            s0 = record["seat0"]
            ctx = seat0_ctx or {}
            pol = ctx.get("pol") or policy_for(0)
            caps_kwarg = ctx.get("caps")
            exclusive = bool(ctx.get("exclusive"))

            after_blockers, cleanup_records, snaps, groups = await _mech_pass(
                "after_refire"
            )
            s0["blocker_snapshots"] = s0["blocker_snapshots"] + snaps
            s0["mechanical_cleanup"] = s0["mechanical_cleanup"] + cleanup_records

            repair_error = ""
            if (
                not seat0_state.repair_used
                and not groups.hard
                and groups.decision
            ):
                # A supported decision blocker surfaced after the end request;
                # spend the one-shot repair on it. No prior-error line -- the
                # end request did not raise, the engine simply did not advance.
                blocker_block = seat0.build_blocker_block(after_blockers)
                repair_kwargs = {"blocker_block": blocker_block}
                if caps_kwarg is not None:
                    repair_kwargs["caps"] = caps_kwarg
                seat0_state.repair_used = True
                s0["repair"]["attempted"] = True
                if exclusive and conn.is_connected:
                    await conn.disconnect()   # repair owns the tuner
                try:
                    repair_result = await pol(gs, 0, turn, **repair_kwargs)
                    s0["repair"]["completed"] = True
                    s0["repair"]["summary"] = (repair_result or {}).get("summary", "")
                except Exception as e:
                    repair_error = repr(e)
                    s0["repair"]["error"] = repair_error
                    print(f"[arena] seat-0 turn {turn} recheck repair failed: {e!r}",
                          file=sys.stderr)
                    log.append({"turn": turn, "player_id": 0,
                                "skipped": True, "repair_error": repair_error})
                # Reclaim regardless of outcome (post-repair query / drain need it).
                if exclusive and not conn.is_connected:
                    await _reconnect_with_retry(conn)
                if repair_error == "":
                    after_blockers, rep_cleanup, rep_snaps, groups = await _mech_pass(
                        "after_repair"
                    )
                    s0["blocker_snapshots"] = s0["blocker_snapshots"] + rep_snaps
                    s0["mechanical_cleanup"] = s0["mechanical_cleanup"] + rep_cleanup

            remaining_blockers = list(groups.hard) + list(groups.decision)
            if not remaining_blockers and seat0_state.may_fire_end_turn:
                # Cleared and the retry budget survives: re-save the anchor under
                # the SAME name so it reflects the repaired state, then re-fire.
                anchor = await seat0.save_recovery_anchor(conn, turn)
                s0["autosave"]["attempts"].append(anchor)
                if not s0["autosave"].get("name"):
                    s0["autosave"]["name"] = anchor.get("name", "")
                if not admission_open():
                    await disable_hook_for_drain()
                seat0_state.mark_end_fired()
                await hook.end_turn(conn)
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
            # First observe/finalize an in-flight seat-0 turn (the turn number
            # must move strictly forward to signal advance), then give an
            # actually captured puppet priority, then consider a new seat-0
            # admission.
            if seat0_state.needs_drain:
                poll_action = seat0_state.observe(
                    turn=st.turn, seat0_active=st.seat0_active
                )
                if poll_action is Seat0Poll.ADVANCED:
                    _terminalize_seat0_advanced()
                    # Fall through: this same poll may re-admit the next turn.
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
                    if (
                        decision is not None
                        and decision.wake_cause == "SCAN_ERROR"
                        and not decision.wake_detail
                        and scan_error_detail
                    ):
                        decision = _dc_replace(decision, wake_detail=scan_error_detail)
                if decision is not None and decision.action == "sleep":
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
                    })
                    if _tx_on:
                        _num = ("score", "gold", "science", "culture", "faith", "cities", "units")
                        if prev_snapshot is not None and state_before is not None:
                            # A partial snapshot in the arena-owned state file
                            # (dict-shaped but missing a numeric key, or a
                            # wrong-typed value) means the delta is unknowable
                            # -- record None, degrade not abort (review catch:
                            # load validates dict shape, not key presence or
                            # value types).
                            try:
                                state_delta = {
                                    k: state_before[k] - prev_snapshot[k] for k in _num
                                }
                                state_delta["research"] = state_before["research"]
                                state_delta["civic"] = state_before["civic"]
                            except (KeyError, TypeError):
                                state_delta = None
                        else:
                            state_delta = None
                        _pol_backend = getattr(pol, "backend", None)
                        transcript.write({
                            "schema_version": 1,
                            "run_id": run_id,
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "player_id": st.local,
                            "turn": st.turn,
                            "provider": getattr(pol, "provider", "local"),
                            "model": getattr(_pol_backend, "model", getattr(pol, "model", "")),
                            "driver": "cli" if str(getattr(pol, "provider", "local")).startswith("cli") else "in_process",
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
                        })
                    await hook.finish_units(conn, st.local)
                    await hook.restore_local(conn, 0)
                    slept += 1
                    game_turns += 1
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

                    # --- Normal attempt (tuner already released if exclusive). --
                    normal_result = None
                    normal_error = ""       # repr for the record
                    normal_error_msg = ""   # str for the pilot-facing repair block
                    try:
                        normal_result = await pol(gs, 0, st.turn, **policy_kwargs)
                    except Exception as e:
                        normal_error = repr(e)
                        normal_error_msg = str(e)
                        print(f"[arena] seat-0 turn {st.turn} normal policy failed: {e!r}",
                              file=sys.stderr)
                        log.append({"turn": st.turn, "player_id": 0,
                                    "skipped": True, "error": normal_error})
                    # ONE shared budget charge for the whole logical turn.
                    seat0_state.mark_policy_played()
                    remaining -= 1
                    game_turns += 1
                    caps_kwarg = policy_kwargs.get("caps")

                    blocker_snapshots: list = []
                    cleanup_records: list = []
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
                        after_blockers, cleanup_records, blocker_snapshots, groups = (
                            await _mech_pass("after_normal")
                        )

                    need_repair = (
                        not seat0_state.repair_used
                        and not groups.hard
                        and (normal_error != "" or bool(groups.decision))
                    )
                    if need_repair:
                        blocker_block = seat0.build_blocker_block(
                            after_blockers, prior_error=normal_error_msg
                        )
                        repair_kwargs = {"blocker_block": blocker_block}
                        if caps_kwarg is not None:
                            repair_kwargs["caps"] = caps_kwarg
                        # Set BEFORE awaiting so a cancellation/exception can
                        # never permit a second repair.
                        seat0_state.repair_used = True
                        repair_attempted = True
                        if exclusive and conn.is_connected:
                            await conn.disconnect()   # repair owns the tuner
                        try:
                            repair_result = await pol(gs, 0, st.turn, **repair_kwargs)
                        except Exception as e:
                            repair_error = repr(e)
                            print(f"[arena] seat-0 turn {st.turn} repair failed: {e!r}",
                                  file=sys.stderr)
                            log.append({"turn": st.turn, "player_id": 0,
                                        "skipped": True, "repair_error": repair_error})
                        # Reclaim the tuner regardless of outcome: the post-repair
                        # mechanical pass (on success) and the human-pending drain
                        # (on failure) both need a live GameCore/InGame connection.
                        if exclusive and not conn.is_connected:
                            await _reconnect_with_retry(conn)
                        if repair_error == "":
                            after_blockers, repair_cleanup, repair_snaps, groups = (
                                await _mech_pass("after_repair")
                            )
                            cleanup_records = cleanup_records + repair_cleanup
                            blocker_snapshots = blocker_snapshots + repair_snaps

                    # --- Terminal decision -------------------------------------
                    normal_returned = normal_error == ""
                    repair_returned = repair_attempted and repair_error == ""
                    any_returned = normal_returned or repair_returned
                    turn_kind = "played" if any_returned else "failed"
                    remaining_blockers = list(groups.hard) + list(groups.decision)

                    merged = seat0.merge_policy_attempts(normal_result, repair_result)
                    payload = merged["transcript"]
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
                        return {
                            **payload,
                            "schema_version": 1,
                            "run_id":   run_id,
                            "ts":       datetime.now(timezone.utc).isoformat(),
                            "player_id": 0,
                            "turn":     st.turn,
                            "provider": getattr(pol, "provider", "local"),
                            "model":    getattr(_pol_backend, "model", getattr(pol, "model", "")),
                            "driver":   "cli" if str(getattr(pol, "provider", "local")).startswith("cli") else "in_process",
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
                                    "summary": (normal_result or {}).get("summary", ""),
                                    "error": normal_error,
                                },
                                "repair": {
                                    "attempted": repair_attempted,
                                    "completed": repair_returned,
                                    "summary": (repair_result or {}).get("summary", ""),
                                    "error": repair_error,
                                },
                                "blocker_snapshots": blocker_snapshots,
                                "mechanical_cleanup": cleanup_records,
                                "autosave": autosave,
                                "end_turn_requests": 0,  # refreshed terminally
                                "terminal_state": "",    # set exactly once
                            },
                        }

                    if any_returned and not remaining_blockers:
                        # PLAYED: best-effort recovery anchor, then one end
                        # request. The record is written when the turn advances.
                        anchor = await seat0.save_recovery_anchor(conn, st.turn)
                        autosave_attempts = []
                        if not anchor.get("ok", False) or (
                            "Save may have failed" in str(anchor.get("result", ""))
                        ):
                            autosave_attempts.append(anchor)
                        seat0_state.record = _base_seat0_record(
                            {"name": anchor.get("name", ""), "attempts": autosave_attempts}
                        )
                        # Carry the resume context for a possible RECHECK re-fire.
                        seat0_ctx = {
                            "pol": pol, "caps": caps_kwarg, "exclusive": exclusive,
                        }
                        if not admission_open():
                            # Final admission: disable the hook while seat 0 is
                            # still active, before the turn releases into AI.
                            await disable_hook_for_drain()
                        if seat0_state.may_fire_end_turn:
                            seat0_state.mark_end_fired()
                            await hook.end_turn(conn)
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
                except Exception as e:
                    # A single failed LLM turn -- e.g. the gateway 500s on a malformed/
                    # truncated tool call (openai.InternalServerError) -- must degrade THIS
                    # puppet turn, never abort the whole multi-turn run. Mirrors the
                    # sweep/memory/task/briefing guards below and the human-safety invariant:
                    # reclaim the tuner (an exclusive CLI turn released it), hand the seat
                    # back to the human, consume the puppet-turn budget, and continue.
                    # Exception (not BaseException) so a CancelledError/Ctrl-C still unwinds
                    # to the finally's guarded handback.
                    print(f"[arena] puppet turn seat {st.local} turn {st.turn} failed, "
                          f"skipping: {e!r}", file=sys.stderr)
                    log.append({"turn": st.turn, "player_id": st.local,
                                "skipped": True, "error": repr(e)})
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
                    await hook.finish_units(conn, st.local)
                    await hook.restore_local(conn, 0)
                    remaining -= 1
                    game_turns += 1
                    deadline_polls -= 1
                    continue
                if exclusive and not conn.is_connected:
                    await _reconnect_with_retry(conn)   # reclaim before we end the turn
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
                        result.get("transcript", {}).get("final_summary")
                        or result.get("summary", "")
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
                    for k, v in result.items()
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
                })
                # Puppet-only transcript + handback (seat 0 returned above).
                if _tx_on and result.get("transcript"):
                    payload = result["transcript"]
                    steps = payload.get("steps", [])
                    if state_before is not None and state_after is not None:
                        _num = ("score", "gold", "science", "culture", "faith", "cities", "units")
                        state_delta = {k: state_after[k] - state_before[k] for k in _num}
                        state_delta["research"] = state_after["research"]
                        state_delta["civic"]    = state_after["civic"]
                    else:
                        state_delta = None
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
                        "driver":   "cli" if str(getattr(pol, "provider", "local")).startswith("cli") else "in_process",
                        "step_count": len(steps),
                        "usd":      float(result.get("usage", {}).get("usd", 0.0)),
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
                    transcript.write(record)
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
            else:
                if seat0_state.needs_drain:
                    # An in-flight seat-0 turn is draining (end request fired /
                    # AI processing): quiet GameCore-only polling until the
                    # turn number flips. No InGame call is issued from here,
                    # and the idle/orphan bookkeeping below stays puppet-era
                    # human-idle semantics only.
                    await asyncio.sleep(1.0)
                    if (
                        seat0_state.phase is Seat0Phase.HUMAN_PENDING
                        or poll_action is Seat0Poll.DEGRADED
                    ):
                        deadline_polls -= 1
                    continue
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
        # tuner handback and swallows its own failures, so a transcript error
        # can neither mask an in-flight CancelledError nor skip the human-safety
        # cleanup below.
        if seat0_state.record is not None and not seat0_state.record_written:
            try:
                seat0_state.mark_interrupted()
                _s0 = seat0_state.record["seat0"]
                _s0["terminal_state"] = "interrupted"
                _s0["end_turn_requests"] = seat0_state.end_turn_requests
                if _tx_on:
                    transcript.write(seat0_state.record)
                seat0_state.record_written = True
            except Exception as e:
                print(f"[arena] WARNING: seat-0 interrupted-record write failed: "
                      f"{e!r}", file=sys.stderr)
        first_exc = None
        steps = []
        if not conn.is_connected:
            steps.append(("reclaim-retry", lambda: _reconnect_with_retry(conn)))
        steps.append(("restore_local(0)", lambda: hook.restore_local(conn, 0)))
        steps.append(("hook.disable", lambda: hook.disable(conn)))
        for label, step in steps:
            try:
                await step()
            except BaseException as e:
                if first_exc is None:
                    first_exc = e
                print(f"[arena] WARNING: {label} failed in cleanup: {e!r}", file=sys.stderr)
        if first_exc is not None and not isinstance(first_exc, Exception):
            raise first_exc
