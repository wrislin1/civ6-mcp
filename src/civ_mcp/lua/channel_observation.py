"""Authoritative union observations for unofficial-channel verification."""

from __future__ import annotations

from typing import Any

from civ_mcp.arena.channel_terms import (
    ChannelObservation,
    ObservationFamily,
    ObservationRequest,
    ObservedCity,
    ObservedRoute,
    ObservedUnit,
)
from civ_mcp.lua._helpers import SENTINEL, _int


def _require_int(
    value: Any,
    field: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Return a strict integer safe for direct Lua interpolation."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be at most {maximum}")
    return value


def _validated_request(
    request: ObservationRequest,
) -> tuple[frozenset[ObservationFamily], tuple[int, ...], tuple[tuple[int, int], ...]]:
    if not isinstance(request, ObservationRequest):
        raise TypeError("request must be an ObservationRequest")

    try:
        families = frozenset(ObservationFamily(family) for family in request.families)
    except (TypeError, ValueError) as exc:
        raise ValueError("request contains an invalid observation family") from exc

    protected = tuple(
        dict.fromkeys(
            _require_int(value, "protected player", minimum=0, maximum=63)
            for value in request.protected_players
        )
    )
    centers: list[tuple[int, int]] = []
    for center in request.zone_centers:
        if not isinstance(center, tuple) or len(center) != 2:
            raise TypeError("zone center must be an (x, y) tuple")
        x = _require_int(center[0], "zone center x", minimum=0)
        y = _require_int(center[1], "zone center y", minimum=0)
        if (x, y) not in centers:
            centers.append((x, y))
    return families, protected, tuple(centers)


def build_channel_observation_query(
    player_id: int, request: ObservationRequest
) -> str:
    """Build one InGame query for the union of requested evidence families."""
    player_id = _require_int(player_id, "player_id", minimum=0, maximum=63)
    families, protected_players, zone_centers = _validated_request(request)
    sections: list[str] = [f"local observedPlayer = Players[{player_id}]"]

    if ObservationFamily.UNITS in families:
        distance_lua = ""
        if protected_players:
            protected_lua = ", ".join(str(value) for value in protected_players)
            distance_lua = f"""
        for _, protectedPlayer in ipairs({{{protected_lua}}}) do
            local minDistance = nil
            for plotIndex = 0, Map.GetPlotCount() - 1 do
                local protectedPlot = Map.GetPlotByIndex(plotIndex)
                if protectedPlot and protectedPlot:GetOwner() == protectedPlayer then
                    local distance = Map.GetPlotDistance(
                        unit:GetX(), unit:GetY(),
                        protectedPlot:GetX(), protectedPlot:GetY()
                    )
                    if minDistance == nil or distance < minDistance then
                        minDistance = distance
                    end
                end
            end
            if minDistance ~= nil then
                print("DIST|" .. unit:GetOwner() .. "|" .. unit:GetID()
                    .. "|" .. protectedPlayer .. "|" .. minDistance)
            end
        end"""
        sections.append(
            f"""
if observedPlayer then
    for _, unit in Players[{player_id}]:GetUnits():Members() do
        local unitInfo = GameInfo.Units[unit:GetType()]
        if unitInfo then
            local formationClass = GameInfo.Units[unit:GetType()].FormationClass or ""
            local religiousStrength = GameInfo.Units[unit:GetType()].ReligiousStrength or 0
            print("UNIT|" .. unit:GetOwner() .. "|" .. unit:GetID()
                .. "|" .. unitInfo.UnitType .. "|" .. formationClass
                .. "|" .. religiousStrength .. "|" .. unit:GetX() .. "|" .. unit:GetY())
{distance_lua}
        end
    end
else
    print("ERROR|units")
end
print("COMPLETE|units")"""
        )

    if ObservationFamily.CITIES in families:
        zone_lua = ""
        if zone_centers:
            center_values = ", ".join(
                f"{{{x}, {y}}}" for x, y in zone_centers
            )
            zone_lua = f"""
        for _, center in ipairs({{{center_values}}}) do
            local distance = Map.GetPlotDistance(
                city:GetX(), city:GetY(), center[1], center[2]
            )
            print("ZONE|" .. city:GetID() .. "|" .. center[1]
                .. "|" .. center[2] .. "|" .. distance)
        end"""
        sections.append(
            f"""
if observedPlayer then
    for _, city in Players[{player_id}]:GetCities():Members() do
        print("CITY|" .. city:GetOwner() .. "|" .. city:GetID()
            .. "|" .. city:GetX() .. "|" .. city:GetY())
{zone_lua}
    end
else
    print("ERROR|cities")
end
print("COMPLETE|cities")"""
        )

    map_lines: list[str] = []
    if ObservationFamily.TERRITORY in families:
        map_lines.append(
            f"""if plot:GetOwner() == {player_id} then
            print("TERRITORY|" .. plot:GetX() .. "|" .. plot:GetY())
        end"""
        )
    if ObservationFamily.CAMPS in families:
        map_lines.append(
            """if barbarianCamp and plot:GetImprovementType() == barbarianCamp.Index then
            print("CAMP|" .. plot:GetX() .. "|" .. plot:GetY())
        end"""
        )
    if map_lines:
        camp_setup = (
            'local barbarianCamp = GameInfo.Improvements["IMPROVEMENT_BARBARIAN_CAMP"]'
            if ObservationFamily.CAMPS in families
            else ""
        )
        sections.append(
            f"""
{camp_setup}
for plotIndex = 0, Map.GetPlotCount() - 1 do
    local plot = Map.GetPlotByIndex(plotIndex)
    if plot then
        {chr(10).join(map_lines)}
    end
end
{"" if ObservationFamily.TERRITORY not in families else 'print("COMPLETE|territory")'}
{"" if ObservationFamily.CAMPS not in families else 'print("COMPLETE|camps")'}"""
        )

    if ObservationFamily.TREASURY in families:
        sections.append(
            f"""
if observedPlayer then
    print("GOLD|" .. math.floor(Players[{player_id}]:GetTreasury():GetGoldBalance()))
else
    print("ERROR|treasury")
end
print("COMPLETE|treasury")"""
        )

    if ObservationFamily.DIPLOMACY in families:
        sections.append(
            f"""
if observedPlayer then
    local diplomacy = Players[{player_id}]:GetDiplomacy()
    for otherPlayer = 0, 63 do
        if otherPlayer ~= {player_id} and Players[otherPlayer]
                and diplomacy:IsAtWarWith(otherPlayer) then
            print("WAR|{player_id}|" .. otherPlayer)
        end
    end
else
    print("ERROR|diplomacy")
end
print("COMPLETE|diplomacy")"""
        )

    if ObservationFamily.TRADE_ROUTES in families:
        sections.append(
            f"""
if observedPlayer then
    local seenRoutes = {{}}
    for _, city in Players[{player_id}]:GetCities():Members() do
        local routes = city:GetTrade():GetOutgoingRoutes()
        if routes then
            for _, r in ipairs(routes) do
                local routeKey = tostring(r.TraderUnitID) .. ":"
                    .. tostring(r.DestinationCityPlayer) .. ":"
                    .. tostring(r.DestinationCityID)
                if not seenRoutes[routeKey] then
                    seenRoutes[routeKey] = true
                    local destinationIsCityState = false
                    local destinationPlayer = Players[r.DestinationCityPlayer]
                    if destinationPlayer then
                        local influence = destinationPlayer:GetInfluence()
                        if influence then
                            destinationIsCityState = influence:CanReceiveInfluence()
                        end
                    end
                    print("ROUTE|" .. r.OriginCityPlayer .. "|" .. r.TraderUnitID
                        .. "|" .. r.DestinationCityPlayer .. "|"
                        .. (destinationIsCityState and "1" or "0"))
                end
            end
        end
    end
else
    print("ERROR|trade_routes")
end
print("COMPLETE|trade_routes")"""
        )

    sections.append(f'print("{SENTINEL}")')
    return "\n".join(sections)


_TAG_FAMILY = {
    "UNIT": ObservationFamily.UNITS,
    "DIST": ObservationFamily.UNITS,
    "CITY": ObservationFamily.CITIES,
    "ZONE": ObservationFamily.CITIES,
    "TERRITORY": ObservationFamily.TERRITORY,
    "GOLD": ObservationFamily.TREASURY,
    "WAR": ObservationFamily.DIPLOMACY,
    "ROUTE": ObservationFamily.TRADE_ROUTES,
    "CAMP": ObservationFamily.CAMPS,
}


def parse_channel_observation_response(
    player_id: int,
    turn: int,
    request: ObservationRequest,
    lines: list[str],
) -> ChannelObservation:
    """Parse the targeted union query without inventing unrequested evidence."""
    requested, _, _ = _validated_request(request)
    game_requested = requested - {ObservationFamily.ACTION_AUDIT}
    completed_families: set[ObservationFamily] = set()
    failed_families: set[ObservationFamily] = set()
    units: list[ObservedUnit] = []
    cities: list[ObservedCity] = []
    routes: list[ObservedRoute] = []
    camps: set[tuple[int, int]] = set()
    territory: set[tuple[int, int]] = set()
    wars: set[tuple[int, int]] = set()
    unit_distances: dict[tuple[int, int, int], int] = {}
    zone_distances: dict[tuple[int, int, int], int] = {}
    treasury_gold = 0
    unknown_errors: list[str] = []

    for line in lines:
        parts = line.split("|")
        tag = parts[0]
        if tag == "ERROR" and len(parts) >= 2:
            try:
                family = ObservationFamily(parts[1])
            except ValueError:
                unknown_errors.append(parts[1])
            else:
                if family in game_requested:
                    failed_families.add(family)
            continue
        if tag == "COMPLETE" and len(parts) >= 2:
            try:
                family = ObservationFamily(parts[1])
            except ValueError:
                continue
            if family in game_requested:
                completed_families.add(family)
            continue
        family = _TAG_FAMILY.get(tag)
        if family is None or family not in requested:
            continue
        try:
            if tag == "UNIT" and len(parts) >= 8:
                units.append(
                    ObservedUnit(
                        owner_id=_int(parts[1]),
                        unit_id=_int(parts[2]),
                        unit_type=parts[3],
                        formation_class=parts[4],
                        religious_strength=_int(parts[5]),
                        x=_int(parts[6]),
                        y=_int(parts[7]),
                    )
                )
            elif tag == "DIST" and len(parts) >= 5:
                key = (_int(parts[1]), _int(parts[2]), _int(parts[3]))
                unit_distances[key] = _int(parts[4])
            elif tag == "CITY" and len(parts) >= 5:
                cities.append(
                    ObservedCity(
                        owner_id=_int(parts[1]),
                        city_id=_int(parts[2]),
                        x=_int(parts[3]),
                        y=_int(parts[4]),
                    )
                )
            elif tag == "ZONE" and len(parts) >= 5:
                key = (_int(parts[1]), _int(parts[2]), _int(parts[3]))
                zone_distances[key] = _int(parts[4])
            elif tag == "TERRITORY" and len(parts) >= 3:
                territory.add((_int(parts[1]), _int(parts[2])))
            elif tag == "GOLD" and len(parts) >= 2:
                treasury_gold = _int(parts[1])
            elif tag == "WAR" and len(parts) >= 3:
                wars.add((_int(parts[1]), _int(parts[2])))
            elif tag == "ROUTE" and len(parts) >= 5:
                routes.append(
                    ObservedRoute(
                        owner_id=_int(parts[1]),
                        trader_unit_id=_int(parts[2]),
                        destination_player=_int(parts[3]),
                        destination_is_city_state=parts[4] == "1",
                    )
                )
            elif tag == "CAMP" and len(parts) >= 3:
                camps.add((_int(parts[1]), _int(parts[2])))
            else:
                raise ValueError("malformed observation line")
        except (TypeError, ValueError):
            failed_families.add(family)

    families_present: set[ObservationFamily] = set()
    errors = list(unknown_errors)
    for family in ObservationFamily:
        if family not in game_requested:
            continue
        if family in completed_families and family not in failed_families:
            families_present.add(family)
        else:
            errors.append(family.value)

    if ObservationFamily.UNITS not in families_present:
        units.clear()
        unit_distances.clear()
    if ObservationFamily.CITIES not in families_present:
        cities.clear()
        zone_distances.clear()
    if ObservationFamily.CAMPS not in families_present:
        camps.clear()
    if ObservationFamily.TERRITORY not in families_present:
        territory.clear()
    if ObservationFamily.DIPLOMACY not in families_present:
        wars.clear()
    if ObservationFamily.TREASURY not in families_present:
        treasury_gold = 0
    if ObservationFamily.TRADE_ROUTES not in families_present:
        routes.clear()

    return ChannelObservation(
        player_id=_require_int(player_id, "player_id", minimum=0, maximum=63),
        turn=_require_int(turn, "turn", minimum=0),
        families_present=frozenset(families_present),
        units=tuple(units),
        cities=tuple(cities),
        camps=frozenset(camps),
        territory=frozenset(territory),
        wars=frozenset(wars),
        treasury_gold=treasury_gold,
        trade_routes=tuple(routes),
        action_audit=(),
        unit_distances=unit_distances,
        zone_distances=zone_distances,
        errors=tuple(errors),
    )


__all__ = [
    "build_channel_observation_query",
    "parse_channel_observation_response",
]
