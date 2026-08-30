"""Forensic probe: did the engine's auto-resolution of deal-000001 move the gold?

Read-only. Baselines (pre-fund, turn 158 seat-1 window, from
arena_runs/arena-channels-core-gate-v3/live_gate/state.json):
  payer  (player 1) gold = 216
  payee  (player 2) gold = 304
"""

import asyncio
import sys

sys.path.insert(0, "src")

from civ_mcp.connection import GameConnection
from civ_mcp.lua.channel_payments import build_channel_payment_state_query

SENTINEL_PRINT = 'print("---END---")'


async def main() -> None:
    conn = GameConnection()
    await conn.connect()
    try:
        # 1. Current treasuries + per-turn gold yield for players 0..3
        lines = await conn.execute_read(
            "for pid=0,3 do "
            "  local p = Players[pid]; "
            "  if p then "
            "    local t = p:GetTreasury(); "
            '    print(string.format("P%d|gold=%.1f|net=%.1f|maint=%.1f", pid, '
            "      t:GetGoldBalance(), t:GetGoldYield() - t:GetTotalMaintenance(), "
            "      t:GetTotalMaintenance())) "
            "  end "
            "end; "
            'print("turn=" .. tostring(Game.GetCurrentGameTurn())); '
            + SENTINEL_PRINT
        )
        for ln in lines:
            print(ln)

        # 2. Seat-independent pending-deal classification for the ordered pair
        lines = await conn.execute_read(build_channel_payment_state_query(1, 2, 1))
        for ln in lines:
            print(ln)

        # 3. Any pending deals in either direction, raw
        lines = await conn.execute_read(
            "local dm = DealManager; "
            "for _, pair in ipairs({{1,2},{2,1}}) do "
            "  local a, b = pair[1], pair[2]; "
            '  print(string.format("HasPendingDeal(%d,%d)=%s", a, b, '
            "    tostring(dm.HasPendingDeal(a, b)))) "
            "end; "
            + SENTINEL_PRINT
        )
        for ln in lines:
            print(ln)
    finally:
        await conn.disconnect()


asyncio.run(main())
