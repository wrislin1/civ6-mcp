from __future__ import annotations

import copy
import sys

from civ_mcp.arena.channel_protocol import ChannelTurnContext
from civ_mcp.arena.channels import ChannelProjection, DealState, PaymentStatus
from civ_mcp.arena.config import CivOptions


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

    def __init__(self, options=None):
        # The coordinator reads shared knobs (memory, task tracker, briefing,
        # ...) via getattr(pol, "options", CivOptions()) -- a policy without
        # the attribute silently drops every validated YAML knob.
        self.options = options if options is not None else CivOptions()

    async def __call__(
        self,
        gs,
        player_id: int,
        turn: int,
        *,
        blocker_block: str = "",
        channel_context: ChannelTurnContext | None = None,
        channel_block: str = "",
        channel_projection: ChannelProjection | None = None,
        **_ignored,
    ) -> dict:
        if blocker_block:
            return await self._repair(gs, blocker_block)
        # NORMAL / dry-run: observe, skip unit 0, choose nothing strategic.
        await gs.get_game_overview()
        await gs.get_units()
        actions: list[dict] = []
        summary_parts: list[str] = ["scripted: observed"]
        try:
            await gs.skip_unit(0)
            actions.append({"tool": "skip_unit"})
            summary_parts.append("skipped unit 0")
        except Exception as e:
            summary_parts.append(f"skip failed {e!r}")

        if channel_context is not None:
            channel_actions, channel_summaries = self._run_channel_actions(
                player_id=player_id,
                turn=turn,
                channel_context=channel_context,
                channel_projection=channel_projection,
            )
            actions.extend(channel_actions)
            summary_parts.extend(channel_summaries)
        elif self.options.channels.script:
            # A configured script exists but this seat has no channel context
            # this turn (e.g. channel admission failed) -- the script cannot
            # run at all. This is the case a live-run operator most needs to
            # see tailing stderr: silence here means the treatment never
            # fires, indistinguishable from an armed-too-late watcher.
            print(
                f"[scripted-policy] channel script present but no "
                f"channel_context: player={player_id} turn={turn} "
                f"cannot dispatch",
                file=sys.stderr,
            )

        return {"summary": "; ".join(summary_parts), "actions": actions}

    def _run_channel_actions(
        self,
        *,
        player_id: int,
        turn: int,
        channel_context: ChannelTurnContext,
        channel_projection: ChannelProjection | None,
    ) -> tuple[list[dict], list[str]]:
        actions: list[dict] = []
        summaries: list[str] = []

        for step in self.options.channels.script:
            if step.turn != turn:
                continue
            action, summary = self._dispatch_channel_action(
                channel_context,
                step.action,
                copy.deepcopy(step.args),
            )
            if "error" in action:
                print(
                    f"[scripted-policy] channel dispatch FAILED: "
                    f"player={player_id} turn={turn} action={step.action} "
                    f"error={action['error']}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[scripted-policy] channel dispatch OK: "
                    f"player={player_id} turn={turn} action={step.action}",
                    file=sys.stderr,
                )
            actions.append(action)
            summaries.append(summary)

        if self.options.channels.auto_accept:
            for action_name, args in self._auto_accept_dispatches(
                player_id=player_id,
                turn=turn,
                channel_projection=channel_projection,
            ):
                action, summary = self._dispatch_channel_action(
                    channel_context, action_name, dict(args)
                )
                action["deal_id"] = args["deal_id"]
                actions.append(action)
                summaries.append(summary)

        for deal_id in self._auto_fund_deal_ids(
            player_id=player_id,
            turn=turn,
            channel_projection=channel_projection,
        ):
            action, summary = self._dispatch_channel_action(
                channel_context,
                "fund_deal",
                {"deal_id": deal_id},
            )
            action["deal_id"] = deal_id
            actions.append(action)
            summaries.append(summary)

        return actions, summaries

    @staticmethod
    def _auto_accept_dispatches(
        *,
        player_id: int,
        turn: int,
        channel_projection: ChannelProjection | None,
    ) -> tuple[tuple[str, dict], ...]:
        """Accept deals proposed to this seat and payments offered to it.

        A scripted seat has no respond path of its own, so an LLM-initiated
        deal aimed at one expires unanswered (v4 `deal-000003`). Accepting is
        deterministic and unconditional by design: the scripted seat is a
        fixture for exercising the lifecycle, not a strategic agent.
        """
        if channel_projection is None:
            return ()
        dispatches: list[tuple[str, dict]] = []
        for deal in channel_projection.deals:
            if deal.counterparty != player_id or deal.terminal is not None:
                continue
            if deal.state is DealState.PROPOSED and turn <= deal.accept_by_turn:
                dispatches.append(
                    ("respond_to_deal", {"deal_id": deal.id, "accept": True})
                )
            elif (
                deal.state is DealState.ACTIVE
                and deal.payment_status is PaymentStatus.OFFERED
                and deal.payment_response_by_turn is not None
                and turn <= deal.payment_response_by_turn
            ):
                dispatches.append(
                    ("respond_to_payment", {"deal_id": deal.id, "accept": True})
                )
        return tuple(dispatches)

    @staticmethod
    def _dispatch_channel_action(
        channel_context: ChannelTurnContext,
        action_name: str,
        args: dict,
    ) -> tuple[dict, str]:
        try:
            result = channel_context.dispatch(action_name, args)
        except Exception as exc:
            error = repr(exc)
            return (
                {"tool": f"channel:{action_name}", "error": error},
                f"channel {action_name} failed {error}",
            )
        return (
            {"tool": f"channel:{action_name}", "result": result},
            f"channel {action_name} queued",
        )

    @staticmethod
    def _auto_fund_deal_ids(
        *,
        player_id: int,
        turn: int,
        channel_projection: ChannelProjection | None,
    ) -> tuple[str, ...]:
        if channel_projection is None:
            return ()
        return tuple(
            deal.id
            for deal in channel_projection.deals
            if deal.proposer == player_id
            and deal.state is DealState.ACTIVE
            and deal.payment_status is PaymentStatus.DUE
            and deal.fund_by_turn is not None
            and turn <= deal.fund_by_turn
        )

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
        want_diplomacy = "== PENDING DIPLOMACY ==" in blocker_block

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
        if want_diplomacy:
            diplomacy_actions, diplomacy_errors = await self._answer_diplomacy(gs)
            actions.extend(diplomacy_actions)
            errors.extend(diplomacy_errors)

        if actions:
            body = ", ".join(f"{a['tool']}={a['item']}" for a in actions)
        else:
            body = "no eligible scripted choices"
        summary = f"scripted repair: {body}"
        if errors:
            summary += " | errors: " + "; ".join(errors)
        return {"summary": summary, "actions": actions}

    async def _answer_diplomacy(self, gs):
        """Advance each open seat-0 diplomacy session by one positive round.

        The coordinator gives a wedged session up to three focused passes, so
        one response per session per pass is sufficient for ordinary 2-3 round
        encounters. A positive response is the least disruptive deterministic
        default for a pilot whose strategy is intentionally out of scope.
        """
        actions: list[dict] = []
        errors: list[str] = []
        try:
            sessions = await gs.get_diplomacy_sessions()
        except Exception as e:
            return actions, [f"get_diplomacy_sessions failed {e!r}"]
        for session in sorted(sessions, key=lambda item: item.other_player_id):
            try:
                result = await gs.diplomacy_respond(
                    session.other_player_id, "POSITIVE"
                )
                actions.append({
                    "tool": "respond_to_diplomacy",
                    "item": f"player {session.other_player_id}",
                    "result": result,
                })
            except Exception as e:
                errors.append(
                    f"respond_to_diplomacy({session.other_player_id}) failed {e!r}"
                )
        return actions, errors

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
            if current not in ("", "NONE", "NOTHING"):
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
