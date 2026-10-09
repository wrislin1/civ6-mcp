#!/usr/bin/env python3
"""Report which GameCore accessors exist on the live game.

Needs the free FireTuner slot and an in-world game. Run it before a scenario clock
opens, for every accessor the recipe relies on, and compare with
references/gamecore-api-surface.md (regenerate that table with --markdown).

usage: uv run python tools/skills/civ6-arena-live/scripts/probe-gamecore-api.py
           [--probe 'LUA_EXPR' METHOD[,METHOD...]]... [--markdown]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from civ_mcp.connection import GameConnection
from civ_mcp.lua._helpers import SENTINEL

FIRST_CITY = "(function() for _, c in Players[0]:GetCities():Members() do return c end end)()"
FIRST_UNIT = "(function() for _, u in Players[0]:GetUnits():Members() do return u end end)()"
CITY_PLOT = (f"(function() local c = {FIRST_CITY}; "
             "return c and Map.GetPlot(c:GetX(), c:GetY()) end)()")

DEFAULT_PROBES: list[tuple[str, str, list[str]]] = [
    ("Players[0]", "Players[0]", ["AttachModifierByID", "GetTreasury", "GetResources"]),
    ("Players[0]:GetTreasury()", "Players[0]:GetTreasury()",
     ["GetGoldBalance", "ChangeGoldBalance"]),
    ("Players[0]:GetResources()", "Players[0]:GetResources()",
     ["GetResourceAmount", "GetResourceAccumulationPerTurn"]),
    ("city", FIRST_CITY, ["GetDistricts", "GetBuildings", "GetBuildQueue", "GetGrowth"]),
    ("city:GetDistricts()", f"{FIRST_CITY}:GetDistricts()",
     ["Members", "GetNumDistricts", "GetDistrictByIndex"]),
    ("city:GetBuildings()", f"{FIRST_CITY}:GetBuildings()",
     ["HasBuilding", "IsPillaged", "SetPillaged", "RemoveBuilding"]),
    ("city:GetBuildQueue()", f"{FIRST_CITY}:GetBuildQueue()",
     ["CurrentlyBuilding", "GetCurrentProductionTypeHash"]),
    ("unit", FIRST_UNIT, ["GetBuildCharges", "ChangeBuildCharges", "SetActionCharges",
                          "ChangeActionCharges", "SetDamage", "IsDead", "IsDelayedDeath"]),
    ("UnitManager", "UnitManager", ["InitUnit", "PlaceUnit", "RestoreMovement", "Kill"]),
    ("ImprovementBuilder", "ImprovementBuilder",
     ["SetImprovementPillaged", "SetImprovementType"]),
    ("plot", CITY_PLOT, ["SetOwner", "GetYield", "GetDistrictType", "IsImprovementPillaged"]),
]


def build_lua(probes: list[tuple[str, str, list[str]]]) -> str:
    parts = []
    for label, expr, methods in probes:
        names = ", ".join(f'"{m}"' for m in methods)
        parts.append(f"""do
  local ok, obj = pcall(function() return {expr} end)
  if not ok or obj == nil then
    print("{label}|<object>|UNAVAILABLE " .. tostring(obj))
  else
    for _, m in ipairs({{{names}}}) do print("{label}|" .. m .. "|" .. type(obj[m])) end
  end
end""")
    parts.append(f'print("{SENTINEL}")')
    return "\n".join(parts)


async def run(probes: list[tuple[str, str, list[str]]]) -> list[str]:
    conn = GameConnection()
    await conn.connect()
    try:
        return await conn.execute_read(build_lua(probes), timeout=20)
    finally:
        await conn.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", nargs=2, action="append", default=[],
                    metavar=("LUA_EXPR", "METHODS"),
                    help="extra object expression and comma-separated method names")
    ap.add_argument("--markdown", action="store_true", help="emit a markdown table")
    args = ap.parse_args()
    probes = DEFAULT_PROBES + [(expr, expr, methods.split(",")) for expr, methods in args.probe]
    rows = [line.split("|", 2) for line in asyncio.run(run(probes)) if line.count("|") == 2]
    if args.markdown:
        print("| Object | Method | GameCore |\n|---|---|---|")
        for label, method, kind in rows:
            print(f"| `{label}` | `{method}` | {'present' if kind == 'function' else kind} |")
    else:
        for label, method, kind in rows:
            print(f"{label:28} {method:32} {'present' if kind == 'function' else kind.upper()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
