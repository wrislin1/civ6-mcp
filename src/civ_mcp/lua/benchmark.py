"""Benchmark domain — canonical queried-state capture for controlled-position runs.

One sentinel-framed query captures everything a benchmark trial needs to
compare "expected" vs "actual" game state after a scripted turn: game
identity (civ type + sync seed), turn, active player, gold/faith, every unit
and city owned by ``player_id``, and ONLY the tiles the position manifest
declares (``tile_coords``) — never a radius scan, so a capture never grows
or shrinks silently as the manifest author's declared tile list changes.

The parser returns JSON-safe primitives only (str/int/float/bool/None/list/
dict). See civ_mcp.arena.benchmark_state for the pure-Python canonicalizer
(sort + hash + diff) that consumes this parser's output.
"""
from __future__ import annotations

from typing import Sequence

from civ_mcp.lua._helpers import SENTINEL, _int


def build_benchmark_state_query(
    player_id: int, tile_coords: Sequence[tuple[int, int]]
) -> str:
    """InGame context: canonical benchmark state for ``player_id``.

    ``tile_coords`` is the position manifest's declared tile list (Python
    ints, not LLM-supplied free text) — cast to int here defensively so a
    malformed manifest fails fast instead of splicing garbage into Lua.
    """
    pid = int(player_id)
    coords = [(int(x), int(y)) for x, y in tile_coords]
    tiles_lua = "{" + ",".join(f"{{{x},{y}}}" for x, y in coords) + "}"
    return f"""
local pid = {pid}
local p = Players[pid]
if p == nil then print("ERR:PLAYER_NOT_FOUND"); print("{SENTINEL}"); return end
local cfg = PlayerConfigurations[pid]
local civType = cfg and cfg:GetCivilizationTypeName() or "UNKNOWN"
local seed = tostring(GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"))
local turn = Game.GetCurrentGameTurn()
local activePlayer = Game.GetLocalPlayer()
local gold, faith = 0, 0
local ok_tr, tr = pcall(function() return p:GetTreasury() end)
if ok_tr and tr then gold = tr:GetGoldBalance() end
pcall(function() faith = p:GetReligion():GetFaithBalance() end)
print("IDENTITY|" .. civType .. "|" .. seed .. "|" .. turn .. "|" .. activePlayer
      .. "|" .. pid .. "|" .. string.format("%.1f", gold) .. "|" .. string.format("%.1f", faith))
for i, u in p:GetUnits():Members() do
    local uid = u:GetID()
    local entry = GameInfo.Units[u:GetType()]
    local ut = entry and entry.UnitType or "UNKNOWN"
    local charges = u:GetBuildCharges() or 0
    print("UNIT|" .. uid .. "|" .. ut .. "|" .. u:GetX() .. "|" .. u:GetY() .. "|" .. charges)
end
for i, c in p:GetCities():Members() do
    local cid = c:GetID()
    local nm = Locale.Lookup(c:GetName()):gsub("|", "/")
    print("CITY|" .. cid .. "|" .. nm .. "|" .. c:GetX() .. "|" .. c:GetY() .. "|" .. c:GetPopulation())
end
local tiles = {tiles_lua}
for _, t in ipairs(tiles) do
    local tx, ty = t[1], t[2]
    local imp, feat, res, pillaged = "NONE", "NONE", "NONE", false
    local plot = Map.GetPlot(tx, ty)
    if plot then
        local featIdx = plot:GetFeatureType()
        if featIdx >= 0 then
            local fi = GameInfo.Features[featIdx]
            if fi then feat = fi.FeatureType end
        end
        local resIdx = plot:GetResourceType()
        if resIdx >= 0 then
            local ri = GameInfo.Resources[resIdx]
            if ri then res = ri.ResourceType end
        end
        local impIdx = plot:GetImprovementType()
        if impIdx >= 0 then
            local ii = GameInfo.Improvements[impIdx]
            if ii then imp = ii.ImprovementType end
            local okP, pil = pcall(function() return plot:IsImprovementPillaged() end)
            if okP and pil then pillaged = true end
        end
    end
    print("TILE|" .. tx .. "|" .. ty .. "|" .. imp .. "|" .. feat .. "|" .. res
          .. "|" .. tostring(pillaged))
end
print("{SENTINEL}")
"""


def _none_if_sentinel(value: str) -> str | None:
    return None if value == "NONE" else value


def parse_benchmark_state(lines: list[str]) -> dict[str, object]:
    """Parse captured lines into a JSON-safe canonical state dict.

    Returns primitives only, so the result can be handed straight to
    civ_mcp.arena.benchmark_state.normalize_state / state_digest / diff_state
    with no further conversion.
    """
    state: dict[str, object] = {
        "civ_type": None,
        "seed": None,
        "turn": None,
        "active_player": None,
        "player_id": None,
        "gold": 0.0,
        "faith": 0.0,
        "units": [],
        "cities": [],
        "tiles": [],
    }

    for line in lines:
        parts = line.split("|")
        try:
            if parts[0] == "IDENTITY" and len(parts) >= 8:
                state["civ_type"] = parts[1]
                state["seed"] = int(parts[2])
                state["turn"] = _int(parts[3])
                state["active_player"] = _int(parts[4])
                state["player_id"] = _int(parts[5])
                state["gold"] = float(parts[6])
                state["faith"] = float(parts[7])
            elif parts[0] == "UNIT" and len(parts) >= 6:
                state["units"].append(  # type: ignore[union-attr]
                    {
                        "id": _int(parts[1]),
                        "type": parts[2],
                        "x": _int(parts[3]),
                        "y": _int(parts[4]),
                        "charges": _int(parts[5]),
                    }
                )
            elif parts[0] == "CITY" and len(parts) >= 6:
                state["cities"].append(  # type: ignore[union-attr]
                    {
                        "id": _int(parts[1]),
                        "name": parts[2],
                        "x": _int(parts[3]),
                        "y": _int(parts[4]),
                        "population": _int(parts[5]),
                    }
                )
            elif parts[0] == "TILE" and len(parts) >= 7:
                state["tiles"].append(  # type: ignore[union-attr]
                    {
                        "x": _int(parts[1]),
                        "y": _int(parts[2]),
                        "improvement": _none_if_sentinel(parts[3]),
                        "feature": _none_if_sentinel(parts[4]),
                        "resource": _none_if_sentinel(parts[5]),
                        "pillaged": parts[6] == "true",
                    }
                )
        except (ValueError, IndexError):
            continue

    return state
