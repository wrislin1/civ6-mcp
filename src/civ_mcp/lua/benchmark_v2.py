"""Version-2 benchmark capture query: one GameCore program, every record family.

`build_benchmark_state_query_v2` returns a single Lua program for one
`GameConnection.execute_read` call. It emits the Plan 3 Part 1 Task 2 wire
grammar (parsed by `civ_mcp.arena.benchmark_state_v2.parse_state_v2`):
BEGIN, IDENTITY, UNIT, TARGET, CITY, BUILDING, DISTRICT, QUEUE, TILE,
RESOURCE, END, then the transport SENTINEL. Rows are buffered while the
capture runs under `pcall`; a read error prints one `ERR:V2_CAPTURE|...`
line and never a successful END. There is no per-entity follow-up query.
"""
from __future__ import annotations

from typing import Any

from civ_mcp.arena.benchmark_contract_v2 import document_digest
from civ_mcp.arena.benchmark_state_v2 import WIRE_VERSION, validate_coverage
from civ_mcp.lua._helpers import SENTINEL


def _lua_pairs(pairs: list[list[int]]) -> str:
    return "{" + ", ".join(f"{{{a}, {b}}}" for a, b in pairs) + "}"


def build_benchmark_state_query_v2(player_id: int, coverage: dict[str, Any]) -> str:
    """GameCore context: complete v2 capture for ``player_id`` and ``coverage``."""
    if isinstance(player_id, bool) or not isinstance(player_id, int):
        raise TypeError("player_id must be an integer")
    if not 0 <= player_id <= 63:
        raise ValueError("player_id must be in 0..63")
    scope = validate_coverage(coverage)
    digest = document_digest(coverage)
    include_owned = "true" if scope["include_owned_tiles"] else "false"
    return f"""
local PID = {player_id}
local AREA = {_lua_pairs(scope["area"])}
local TRACKED = {_lua_pairs(scope["tracked_targets"])}
local INCLUDE_OWNED_TILES = {include_owned}
local ORDER = {{"IDENTITY", "UNIT", "TARGET", "CITY", "BUILDING", "DISTRICT", "QUEUE", "TILE", "RESOURCE"}}
local rows = {{}}
for _, tag in ipairs(ORDER) do rows[tag] = {{}} end
local NULL = "~"
local function esc(v)
    local s = tostring(v)
    s = string.gsub(s, "%%", "%%25")
    s = string.gsub(s, "|", "%%7C")
    s = string.gsub(s, "\\r", "%%0D")
    s = string.gsub(s, "\\n", "%%0A")
    s = string.gsub(s, "~", "%%7E")
    return s
end
local function num(v)
    if type(v) ~= "number" or v ~= v or v == math.huge or v == -math.huge then
        error("non-finite number")
    end
    return string.format("%.17g", v)
end
local function int(v)
    if type(v) ~= "number" or v ~= math.floor(v) then error("non-integer value") end
    return string.format("%.0f", v)
end
local function bool(v) if v then return "1" end return "0" end
local function emit(tag, fields)
    local list = rows[tag]
    list[#list + 1] = tag .. "|" .. table.concat(fields, "|")
end
local function typeName(tbl, idx, field)
    if idx == nil or idx < 0 then return "NONE" end
    local row = tbl[idx]
    if row == nil then error("unknown GameInfo index " .. tostring(idx)) end
    return row[field]
end
local function role(entry)
    local fc = entry.FormationClass or ""
    if fc == "FORMATION_CLASS_CIVILIAN" then return "civilian" end
    if fc == "FORMATION_CLASS_SUPPORT" then return "support" end
    return "combat"
end

local function capture()
    local p = Players[PID]
    if p == nil then error("PLAYER_NOT_FOUND") end
    local vis = PlayersVisibility[PID]
    local diplo = p:GetDiplomacy()
    local function hostile(owner) return owner == 63 or diplo:IsAtWarWith(owner) end
    local function plotVisible(x, y)
        local plot = Map.GetPlot(x, y)
        return plot ~= nil and vis:IsVisible(plot:GetIndex())
    end

    -- IDENTITY
    local cfg = PlayerConfigurations[PID]
    local gold = p:GetTreasury():GetGoldBalance()
    local faith = p:GetReligion():GetFaithBalance()
    emit("IDENTITY", {{esc(cfg:GetCivilizationTypeName()),
        int(tonumber(GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"))),
        int(Game.GetCurrentGameTurn()), int(Game.GetLocalPlayer()), int(PID),
        num(gold), num(faith)}})

    -- UNIT: every owned unit
    local ownedXY = {{}}
    for _, u in p:GetUnits():Members() do
        local entry = GameInfo.Units[u:GetType()]
        if entry == nil then error("unknown unit type") end
        local uid = u:GetID()
        ownedXY[#ownedXY + 1] = {{u:GetX(), u:GetY()}}
        emit("UNIT", {{int(PID), int(uid), int(uid % 65536), esc(entry.UnitType),
            role(entry), int(u:GetX()), int(u:GetY()),
            num(u:GetMaxDamage() - u:GetDamage()), num(u:GetMaxDamage()),
            num(u:GetMovesRemaining()), int(u:GetBuildCharges() or 0)}})
    end

    -- TARGET: every frozen tracked target, then visible nearby hostiles
    local seen = {{}}
    local function visibleRow(owner, u, tracked)
        local entry = GameInfo.Units[u:GetType()]
        if entry == nil then error("unknown unit type") end
        emit("TARGET", {{int(owner), int(u:GetID()), bool(tracked), role(entry),
            bool(hostile(owner)), "1", "alive_visible", int(u:GetX()), int(u:GetY()),
            num(u:GetMaxDamage() - u:GetDamage()), num(u:GetMaxDamage())}})
    end
    for _, ref in ipairs(TRACKED) do
        local owner, id = ref[1], ref[2]
        local op = Players[owner]
        if op == nil then error("TRACKED_OWNER_NOT_FOUND") end
        local found = nil
        for _, u in op:GetUnits():Members() do
            if u:GetID() == id then found = u end
        end
        seen[owner .. ":" .. id] = true
        if found == nil then
            emit("TARGET", {{int(owner), int(id), "1", NULL, bool(hostile(owner)), "0",
                "destroyed", NULL, NULL, NULL, NULL}})
        elseif plotVisible(found:GetX(), found:GetY()) then
            visibleRow(owner, found, true)
        else
            emit("TARGET", {{int(owner), int(id), "1", NULL, bool(hostile(owner)), "0",
                "alive_not_visible", NULL, NULL, NULL, NULL}})
        end
    end
    local areaKey = {{}}
    for _, xy in ipairs(AREA) do areaKey[xy[1] .. "," .. xy[2]] = true end
    for i = 0, 63 do
        if i ~= PID and Players[i] and Players[i]:IsAlive() and hostile(i) then
            for _, u in Players[i]:GetUnits():Members() do
                local key = i .. ":" .. u:GetID()
                local ux, uy = u:GetX(), u:GetY()
                if not seen[key] and plotVisible(ux, uy) then
                    local near = areaKey[ux .. "," .. uy] == true
                    for _, xy in ipairs(ownedXY) do
                        if not near and Map.GetPlotDistance(ux, uy, xy[1], xy[2]) <= 1 then
                            near = true
                        end
                    end
                    if near then
                        seen[key] = true
                        visibleRow(i, u, false)
                    end
                end
            end
        end
    end

    -- CITY, BUILDING, DISTRICT, QUEUE: every owned city
    for _, c in p:GetCities():Members() do
        local cid = c:GetID()
        emit("CITY", {{int(PID), int(cid), esc(Locale.Lookup(c:GetName())),
            int(c:GetX()), int(c:GetY()), int(c:GetPopulation()),
            num(c:GetGrowth():GetHousing())}})
        local blds = c:GetBuildings()
        for b in GameInfo.Buildings() do
            if blds:HasBuilding(b.Index) then
                emit("BUILDING", {{int(PID), int(cid), esc(b.BuildingType), "1",
                    bool(blds:IsPillaged(b.Index))}})
            end
        end
        local districts = {{}}
        -- GameCore's CityDistricts has no Members() iterator (live 2026-10-09):
        -- it exposes GetNumDistricts() and the zero-based GetDistrictByIndex(i).
        local cds = c:GetDistricts()
        for i = 0, cds:GetNumDistricts() - 1 do
            local d = cds:GetDistrictByIndex(i)
            if d == nil then error("district index " .. i .. " missing") end
            local dInfo = GameInfo.Districts[d:GetType()]
            if dInfo == nil then error("unknown district type") end
            districts[#districts + 1] = {{d = d, t = dInfo.DistrictType}}
            emit("DISTRICT", {{int(PID), int(cid), int(d:GetID()), esc(dInfo.DistrictType),
                int(d:GetX()), int(d:GetY()), bool(d:IsComplete()), bool(d:IsPillaged())}})
        end
        local cur = c:GetBuildQueue():CurrentlyBuilding()
        local kind, item, repair, tx, ty = "NONE", "NONE", false, NULL, NULL
        -- GameCore's BuildQueue:CurrentlyBuilding() answers the string "NONE" for
        -- an empty queue (live 2026-10-09) and the type name otherwise.
        if cur ~= nil and cur ~= "" and cur ~= "NONE" then
            item = cur
            if GameInfo.Units[cur] ~= nil then kind = "UNIT"
            elseif GameInfo.Buildings[cur] ~= nil then
                kind = "BUILDING"
                local idx = GameInfo.Buildings[cur].Index
                repair = blds:HasBuilding(idx) and blds:IsPillaged(idx)
            elseif GameInfo.Districts[cur] ~= nil then
                kind = "DISTRICT"
                for _, entry in ipairs(districts) do
                    if entry.t == cur and entry.d:IsPillaged() then
                        repair = true
                        tx, ty = int(entry.d:GetX()), int(entry.d:GetY())
                    end
                end
                if not repair then
                    for _, entry in ipairs(districts) do
                        if entry.t == cur and not entry.d:IsComplete() then
                            tx, ty = int(entry.d:GetX()), int(entry.d:GetY())
                        end
                    end
                end
            elseif GameInfo.Projects[cur] ~= nil then kind = "PROJECT"
            else error("unknown production item " .. tostring(cur)) end
        end
        emit("QUEUE", {{int(PID), int(cid), kind, esc(item), bool(repair), tx, ty}})
    end

    -- TILE: all owned tiles (if requested) plus the frozen area
    local tileSeen = {{}}
    local function tileRow(plot)
        local x, y = plot:GetX(), plot:GetY()
        local key = x .. "," .. y
        if tileSeen[key] then return end
        tileSeen[key] = true
        local impIdx = plot:GetImprovementType()
        local pillaged = impIdx >= 0 and plot:IsImprovementPillaged()
        emit("TILE", {{int(x), int(y), int(plot:GetOwner()),
            esc(typeName(GameInfo.Terrains, plot:GetTerrainType(), "TerrainType")),
            esc(typeName(GameInfo.Features, plot:GetFeatureType(), "FeatureType")),
            esc(typeName(GameInfo.Resources, plot:GetResourceType(), "ResourceType")),
            esc(typeName(GameInfo.Improvements, impIdx, "ImprovementType")),
            bool(pillaged),
            esc(typeName(GameInfo.Districts, plot:GetDistrictType(), "DistrictType")),
            bool(vis:IsVisible(plot:GetIndex())),
            num(plot:GetYield(0)), num(plot:GetYield(1)), num(plot:GetYield(2)),
            num(plot:GetYield(3)), num(plot:GetYield(4)), num(plot:GetYield(5))}})
    end
    if INCLUDE_OWNED_TILES then
        for idx = 0, Map.GetPlotCount() - 1 do
            local plot = Map.GetPlotByIndex(idx)
            if plot and plot:GetOwner() == PID then tileRow(plot) end
        end
    end
    for _, xy in ipairs(AREA) do
        local plot = Map.GetPlot(xy[1], xy[2])
        if plot == nil then error("AREA_PLOT_NOT_FOUND " .. xy[1] .. "," .. xy[2]) end
        tileRow(plot)
    end

    -- RESOURCE: every resource type's access status
    local pRes = p:GetResources()
    for row in GameInfo.Resources() do
        local amt = pRes:GetResourceAmount(row.Index)
        local stock, flow = NULL, NULL
        if row.ResourceClassType == "RESOURCECLASS_STRATEGIC" then
            stock = num(amt)
            local okF, f = pcall(function() return pRes:GetResourceAccumulationPerTurn(row.Index) end)
            if okF and type(f) == "number" and f == f and f ~= math.huge and f ~= -math.huge then
                flow = num(f)
            end
        end
        emit("RESOURCE", {{esc(row.ResourceType), bool(amt > 0), stock, flow}})
    end
end

local ok, err = pcall(capture)
if ok then
    print("BEGIN|{WIRE_VERSION}|{digest}")
    local counts = {{}}
    for _, tag in ipairs(ORDER) do
        for _, line in ipairs(rows[tag]) do print(line) end
        counts[#counts + 1] = tostring(#rows[tag])
    end
    print("END|{WIRE_VERSION}|" .. table.concat(counts, "|"))
else
    print("ERR:V2_CAPTURE|" .. esc(err))
end
print("{SENTINEL}")
"""
