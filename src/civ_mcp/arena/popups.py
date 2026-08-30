"""Engine popup dismissal for blocking popups."""
from civ_mcp import lua as lq


_POPUP_CONTEXT_CLOSERS = {
    "NaturalDisasterPopup": "Close()",
    "HistoricMoments": "Close()",
    "BoostUnlockedPopup": "OnClose()",
}


async def dismiss_blocking_popups(conn) -> str:
    """Best-effort: close known engine popups (disaster cinematics, moment
    timelines, boost popups) that block turn processing while the blocker
    radar reads empty. Never raises into the poll loop.

    Prefer each context's own close function, executed IN that context:
    hiding NaturalDisasterPopup from InGame leaks its PopupManager engine
    hold (UI.ReferenceCurrentEvent) and wedges the interturn at PLEASE WAIT
    (observed live: v8 T159, event id 1640). The context's Close() releases
    the engine hold, pops the Reveal input context, and re-shows bulk-hidden
    UI. Falls back to the InGame dequeue+hide+restore Lua when per-state
    execution or matching contexts are unavailable.
    """
    states = dict(getattr(conn, "lua_states", None) or {})
    execute_in_state = getattr(conn, "execute_in_state", None)
    closed: list[str] = []
    if execute_in_state is not None and states:
        for idx in sorted(states):
            closer = _POPUP_CONTEXT_CLOSERS.get(states[idx])
            if closer is None:
                continue
            lua = (
                'if ContextPtr:IsHidden() then print("HIDDEN") '
                f'else {closer}; print("CLOSED") end; print("{lq.SENTINEL}")'
            )
            try:
                lines = await execute_in_state(idx, lua)
            except Exception:
                continue
            if any("CLOSED" in line for line in lines):
                closed.append(states[idx])
        if closed:
            return "POPUPS|" + ",".join(closed)
    try:
        lines = await conn.execute_write(lq.build_dismiss_blocking_popups())
    except Exception:
        return "err"
    for line in lines:
        if line.startswith("POPUPS|"):
            names = [
                name
                for name in line.removeprefix("POPUPS|").split(",")
                if name and name != "none" and not name.startswith("SKIPPED:")
            ]
            return "POPUPS|" + (",".join(names) if names else "none")
    return "?"
