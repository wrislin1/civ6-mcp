import asyncio
from civ_mcp.connection import GameConnection
from civ_mcp.game_lifecycle import list_saves
async def main():
    conn = GameConnection(); await conn.connect()
    try:
        print(await list_saves(conn))
    finally:
        await conn.disconnect()
asyncio.run(main())
