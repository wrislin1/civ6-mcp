import asyncio
from civ_mcp.connection import GameConnection
from civ_mcp.game_lifecycle import dismiss_popup

SWEEP = r"""
local me = Game.GetLocalPlayer()
local closed = 0
for target = 0, 7 do
  if target ~= me then
    for r = 1, 6 do
      local sid = DiplomacyManager.FindOpenSessionID(me, target)
      if not sid or sid < 0 then break end
      pcall(function() DiplomacyManager.CloseSession(sid) end)
      closed = closed + 1
    end
  end
end
pcall(function() LuaEvents.DiplomacyActionView_ShowIngameUI() end)
pcall(function() Events.HideLeaderScreen() end)
print("CLOSED_SESSIONS|" .. closed)
print("LOCAL|" .. tostring(Game.GetLocalPlayer()))
print("TURN|" .. tostring(Game.GetCurrentGameTurn()))
print("---END---")
"""

async def main():
    conn = GameConnection(); await conn.connect()
    try:
        r = await dismiss_popup(conn)
        print("DISMISS_POPUP ->", r)
        for ln in await conn.execute_read(SWEEP):
            if ln and "END" not in ln: print(ln)
    finally:
        await conn.disconnect()
asyncio.run(main())
