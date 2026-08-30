"""Decisive forensic: create a 1-gold p1->p2 deal, poll until the engine
resolves it, and measure whether the gold actually moves.

Mirrors build_channel_payment_offer but with explicit player ids (the
operative DealManager calls accept them; the seat-independent query already
relies on that). Read/write probe on a terminal-failure scratch save.
"""

import asyncio
import sys
import time

sys.path.insert(0, "src")

from civ_mcp.connection import GameConnection

SENT = 'print("---END---")'

CREATE = f"""
local payer, payee = 1, 2
if DealManager.HasPendingDeal(payer, payee) then
    print("ERR:ALREADY_PENDING")
    {SENT}
    return
end
DealManager.ClearWorkingDeal(DealDirection.OUTGOING, payer, payee)
local deal = DealManager.GetWorkingDeal(DealDirection.OUTGOING, payer, payee)
if not deal then
    print("ERR:NO_WORKING_DEAL")
    {SENT}
    return
end
-- stale-buffer hazard (observed 2026-07-18): clear then re-check count
if deal:GetItemCount() ~= 0 then
    print("ERR:DIRTY_BUFFER|" .. tostring(deal:GetItemCount()))
    {SENT}
    return
end
local goldItem = deal:AddItemOfType(DealItemTypes.GOLD, payer)
if not goldItem then
    print("ERR:ADD_GOLD_FAILED")
    {SENT}
    return
end
goldItem:SetAmount(1)
goldItem:SetDuration(0)
DealManager.SendWorkingDeal(DealProposalAction.PROPOSED, payer, payee)
print("OK:SENT")
{SENT}
"""

POLL = f"""
local t1 = Players[1]:GetTreasury()
local t2 = Players[2]:GetTreasury()
print(string.format("STATE|pending=%s|p1=%.3f|p2=%.3f|turn=%d",
    tostring(DealManager.HasPendingDeal(1, 2)),
    t1:GetGoldBalance(), t2:GetGoldBalance(),
    Game.GetCurrentGameTurn()))
{SENT}
"""


async def poll(conn: GameConnection) -> tuple[bool, float, float, int]:
    lines = await conn.execute_read(POLL)
    for ln in lines:
        if ln.startswith("STATE|"):
            parts = dict(p.split("=", 1) for p in ln.split("|")[1:])
            return (
                parts["pending"] == "true",
                float(parts["p1"]),
                float(parts["p2"]),
                int(parts["turn"]),
            )
    raise RuntimeError(f"no STATE line: {lines}")


async def main() -> None:
    conn = GameConnection()
    await conn.connect()
    try:
        pending, p1, p2, turn = await poll(conn)
        print(f"PRE  | pending={pending} p1={p1:.3f} p2={p2:.3f} turn={turn}")
        if pending:
            print("ABORT: a pending deal already exists")
            return

        lines = await conn.execute_read(CREATE)
        status = next((ln for ln in lines if ln.startswith(("OK:", "ERR:"))), "NO_STATUS")
        print(f"CREATE | {status}")
        if not status.startswith("OK:"):
            return

        t0 = time.monotonic()
        pending, c1, c2, turn = await poll(conn)
        print(f"T+0.0s | pending={pending} p1={c1:.3f} p2={c2:.3f}")
        if not pending:
            print("NOTE: deal never became pending (resolved instantly or send refused)")

        while time.monotonic() - t0 < 300:
            await asyncio.sleep(2)
            was_pending = pending
            pending, c1, c2, turn = await poll(conn)
            el = time.monotonic() - t0
            if pending != was_pending or abs(c1 - p1) > 0.001 or abs(c2 - p2) > 0.001:
                print(
                    f"T+{el:.1f}s | pending={pending} "
                    f"p1={c1:.3f} (Δ{c1 - p1:+.3f}) p2={c2:.3f} (Δ{c2 - p2:+.3f}) turn={turn}"
                )
            if was_pending and not pending:
                print(f"RESOLVED after {el:.1f}s")
                # settle-out reads
                for wait in (2, 5):
                    await asyncio.sleep(wait)
                    _, f1, f2, _ = await poll(conn)
                    print(
                        f"POST+{wait}s | p1={f1:.3f} (Δ{f1 - p1:+.3f}) "
                        f"p2={f2:.3f} (Δ{f2 - p2:+.3f})"
                    )
                verdict = "AUTO-ACCEPTED (gold moved)" if f1 < p1 - 0.5 and f2 > p2 + 0.5 else (
                    "AUTO-DECLINED (no transfer)" if abs(f1 - p1) < 0.5 and abs(f2 - p2) < 0.5
                    else "AMBIGUOUS"
                )
                print(f"VERDICT: {verdict}")
                return
        print("TIMEOUT: deal still pending after 300s (or never pending)")
    finally:
        await conn.disconnect()


asyncio.run(main())
