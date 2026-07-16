import asyncio
from civ_mcp.connection import GameConnection
from civ_mcp.arena import hook
from civ_mcp import lua as lq

STATE = r"""
print("TURN|"..tostring(Game.GetCurrentGameTurn()))
print("P0_ACTIVE|"..tostring(Players[0]:IsTurnActive()))
local a="" for i=1,63 do if Players[i] and Players[i]:IsAlive() and Players[i]:IsTurnActive() then a=a..i.."," end end
print("ACTIVE_AI|"..a)
print("---END---")
"""
async def state(conn):
    d = {}
    for l in await conn.execute_read(STATE):
        if "|" in l: k,v=l.split("|",1); d[k]=v
    return d.get("TURN"), d.get("P0_ACTIVE")=="true", [x for x in d.get("ACTIVE_AI","").split(",") if x]

async def main():
    conn = GameConnection(); await conn.connect()
    try:
        for it in range(40):
            turn, p0, ai = await state(conn)
            if p0:
                print(f"[it{it}] turn={turn} -> HUMAN ACTIVE, unstuck"); break
            if not ai:
                print(f"[it{it}] turn={turn} no active AI, P0 not active; wait")
                await asyncio.sleep(2); continue
            pid = int(ai[0])
            await conn.execute_read(hook.build_restore_local_lua(pid))
            await conn.execute_read(hook.build_finish_units_lua(pid))
            await conn.execute_write(lq.build_end_turn())
            await asyncio.sleep(1.0)
            await conn.execute_read(hook.build_restore_local_lua(0))
            if it % 5 == 0: print(f"[it{it}] turn={turn} ended p{pid}, remaining_ai={ai[:6]}")
        turn, p0, ai = await state(conn)
        print(f"FINAL turn={turn} human_active={p0} active_ai={ai}")
    finally:
        await conn.disconnect()
asyncio.run(main())
