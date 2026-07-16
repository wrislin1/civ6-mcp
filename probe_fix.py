import asyncio
from civ_mcp.connection import GameConnection
from civ_mcp.lua import cities as C

FIND = r'''
for _, city in Players[1]:GetCities():Members() do
  print("CITY|" .. (city:GetID() + city:GetOwner()*65536))
end
print("---END---")
'''

async def main():
    conn = GameConnection()
    await conn.connect()
    try:
        lines = await conn.execute_read(FIND)
        cids = [int(l.split("|")[1]) for l in lines if l.startswith("CITY|")]
        print("puppet(p1) city ids:", cids)
        if not cids:
            print("NO CITY FOUND"); return
        cid = cids[0]
        for name in ("Scout", "Monument"):
            itype = "UNIT" if name == "Scout" else "BUILDING"
            w = await conn.execute_write(C.build_produce_item(cid, itype, name))
            r = await conn.execute_read(C.build_verify_production(cid, name))
            print(f"\n[{name}] produce -> {[x for x in w if x and 'END' not in x]}")
            print(f"[{name}] verify  -> {[x for x in r if x and 'END' not in x]}")
    finally:
        await conn.disconnect()

asyncio.run(main())
