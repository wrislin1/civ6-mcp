"""Notifications domain — Lua builders and parsers."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.models import GameNotification


def build_end_turn_blocking_query() -> str:
    """Check for ALL EndTurnBlocking notifications (InGame context).

    Iterates every notification and emits a BLOCKING| line for each unique
    blocking type found.  This lets the caller see everything that needs
    resolving in a single round-trip instead of peeling one blocker at a time.
    """
    return """
local me = Game.GetLocalPlayer()
local list = NotificationManager.GetList(me)
local seen = {}
local found = 0
if list then
    for _, nid in ipairs(list) do
        local entry = NotificationManager.Find(me, nid)
        if entry and not entry:IsDismissed() then
            local bt = entry:GetEndTurnBlocking()
            if bt and bt ~= 0 then
                local typeName = "UNKNOWN"
                for k, v in pairs(EndTurnBlockingTypes) do
                    if v == bt then typeName = k; break end
                end
                local notificationType = entry:GetTypeName() or "UNKNOWN"
                if not seen[typeName] then
                    seen[typeName] = true
                    local msg = (entry:GetMessage() or ""):gsub("|", "/")
                    print("BLOCKING|" .. typeName .. "|" .. msg .. "|" ..
                          notificationType .. "|" .. tostring(bt))
                    found = found + 1
                end
            end
        end
    end
end
if found == 0 then print("NONE") end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_end_turn() -> str:
    return """
UI.RequestAction(ActionTypes.ACTION_ENDTURN)
print("OK:TURN_ENDED")
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


_STALE_BLOCKER_STATE_CHECK: dict[str, str] = {
    "ENDTURN_BLOCKING_RESEARCH": "Players[me]:GetTechs():GetResearchingTech() >= 0",
    "ENDTURN_BLOCKING_CIVIC": "Players[me]:GetCulture():GetProgressingCivic() >= 0",
    # Every owned city that can trigger the blocker must have a live,
    # nonzero current production hash; an empty or zero-hash queue in any
    # city means the choice is not actually set, so nothing is dismissed.
    "ENDTURN_BLOCKING_PRODUCTION": """(function()
        for _, c in Players[me]:GetCities():Members() do
            local bq = c:GetBuildQueue()
            if bq:GetSize() == 0 or bq:GetCurrentProductionTypeHash() == 0 then
                return false
            end
        end
        return true
    end)()""",
}


def build_clear_stale_end_turn_blocker(blocking_type: str) -> str:
    """Closed-list Lua builder: dismiss a stale EndTurnBlocking notification
    only after proving its underlying choice is already set (InGame context).

    Accepts only ENDTURN_BLOCKING_RESEARCH, ENDTURN_BLOCKING_CIVIC, and
    ENDTURN_BLOCKING_PRODUCTION. Any other value raises ValueError, keeping
    untrusted strings out of the generated Lua entirely.
    """
    state_check = _STALE_BLOCKER_STATE_CHECK.get(blocking_type)
    if state_check is None:
        raise ValueError(f"Unsupported stale blocker type: {blocking_type!r}")

    return f"""
local me = Game.GetLocalPlayer()
local isSet = {state_check}
if isSet then
    local list = NotificationManager.GetList(me)
    if list then
        for _, nid in ipairs(list) do
            local entry = NotificationManager.Find(me, nid)
            if entry and not entry:IsDismissed() then
                local bt = entry:GetEndTurnBlocking()
                if bt and bt == EndTurnBlockingTypes.{blocking_type} then
                    pcall(function() NotificationManager.SendActivated(me, nid) end)
                    pcall(function() NotificationManager.Dismiss(me, nid) end)
                end
            end
        end
    end
    print("STALE_CLEARED")
else
    print("NOT_SET")
end
print("{SENTINEL}")
"""


def build_mark_end_turn_prompt_seen(blocking_type: str) -> str:
    """Closed-list Lua builder: mark a purely informational EndTurnBlocking
    prompt as reviewed (InGame context), without touching any strategic
    choice.

    Accepts only ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE (marks the
    government-change consideration seen), ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK
    (marks World Congress results looked-at and dismisses only the matching
    notification), and ENDTURN_BLOCKING_GOVERNOR_IDLE (activates and dismisses
    the idle-governor reminder). Any other value raises ValueError.
    """
    if blocking_type == "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE":
        return f"""
local me = Game.GetLocalPlayer()
Players[me]:GetCulture():SetGovernmentChangeConsidered(true)
print("PROMPT_SEEN")
print("{SENTINEL}")
"""

    if blocking_type == "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK":
        return f"""
local me = Game.GetLocalPlayer()
UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_LOOKED_AT_AVAILABLE, {{}})
local list = NotificationManager.GetList(me)
if list then
    for _, nid in ipairs(list) do
        local entry = NotificationManager.Find(me, nid)
        if entry and not entry:IsDismissed() then
            local bt = entry:GetEndTurnBlocking()
            if bt and bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK then
                pcall(function() NotificationManager.Dismiss(me, nid) end)
            end
        end
    end
end
local i = ContextPtr:LookUpControl("/InGame/WorldCongressIntro")
if i then i:SetHide(true) end
local p = ContextPtr:LookUpControl("/InGame/WorldCongressPopup")
if p then p:SetHide(true) end
print("PROMPT_SEEN")
print("{SENTINEL}")
"""

    if blocking_type == "ENDTURN_BLOCKING_GOVERNOR_IDLE":
        return f"""
local me = Game.GetLocalPlayer()
local list = NotificationManager.GetList(me)
if list then
    for _, nid in ipairs(list) do
        local entry = NotificationManager.Find(me, nid)
        if entry and not entry:IsDismissed() then
            local bt = entry:GetEndTurnBlocking()
            if bt and bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_GOVERNOR_IDLE then
                pcall(function() NotificationManager.SendActivated(me, nid) end)
                pcall(function() NotificationManager.Dismiss(me, nid) end)
            end
        end
    end
end
print("PROMPT_SEEN")
print("{SENTINEL}")
"""

    raise ValueError(f"Unsupported prompt-seen blocker type: {blocking_type!r}")


def build_notifications_query() -> str:
    """Query NotificationManager for active notifications (InGame context)."""
    return """
local me = Game.GetLocalPlayer()
local nm = NotificationManager
local list = nm.GetList(me)
local total = 0
local emitted = 0
if list then
    total = #list
    for _, nID in ipairs(list) do
        pcall(function()
            local entry = nm.Find(me, nID)
            if entry and not entry:IsDismissed() then
                local typeName = entry:GetTypeName() or "UNKNOWN"
                local msg = (entry:GetMessage() or ""):gsub("|", "/")
                if typeName:find("WONDER") then
                    pcall(function()
                        local wx, wy = entry:GetLocation()
                        if wx and wx >= 0 then
                            local wPlot = Map.GetPlot(wx, wy)
                            if wPlot then
                                local wOwner = wPlot:GetOwner()
                                if wOwner and wOwner >= 0 and PlayerConfigurations[wOwner] then
                                    local civShort = Locale.Lookup(PlayerConfigurations[wOwner]:GetCivilizationShortDescription())
                                    if civShort then msg = msg .. " [" .. civShort .. "]" end
                                end
                                local dt = wPlot:GetDistrictType()
                                if dt and dt >= 0 and GameInfo.Districts[dt] then
                                    local dName = Locale.Lookup(GameInfo.Districts[dt].Name)
                                    if dName then msg = msg .. " (" .. dName .. ")" end
                                end
                            end
                        end
                    end)
                end
                local turn = entry:GetAddedTurn() or -1
                local x, y = -1, -1
                pcall(function() x, y = entry:GetLocation() end)
                if x == nil then x = -1 end
                if y == nil then y = -1 end
                print("NOTIF|" .. typeName .. "|" .. msg .. "|" .. turn .. "|" .. x .. "," .. y)
                emitted = emitted + 1
            end
        end)
    end
end
print("TOTAL|" .. total .. "|" .. emitted)
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def parse_notifications_response(lines: list[str]) -> list[GameNotification]:
    """Parse NOTIF| lines from build_notifications_query."""
    notifs = []
    for line in lines:
        if not line.startswith("NOTIF|"):
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        x_str, y_str = parts[4].split(",")
        type_name = parts[1]
        is_action = any(kw in type_name.upper() for kw in _ACTION_KEYWORDS)
        hint = NOTIFICATION_TOOL_MAP.get(type_name)
        notifs.append(
            GameNotification(
                type_name=type_name,
                message=parts[2],
                turn=int(parts[3]),
                x=int(x_str),
                y=int(y_str),
                is_action_required=is_action,
                resolution_hint=hint,
            )
        )
    return notifs


def parse_end_turn_blocking(lines: list[str]) -> list[tuple[str, str]]:
    """Parse blocking query response.

    Returns a list of (blocking_type, message) tuples — one per unique blocker.
    Empty list means no blockers.
    """
    blockers: list[tuple[str, str]] = []
    for line in lines:
        if line == "NONE":
            return []
        if line.startswith("BLOCKING|"):
            parts = line.split("|")
            blocking_type = parts[1] if len(parts) > 1 else "UNKNOWN"
            msg = parts[2] if len(parts) > 2 else ""
            notification_type = parts[3] if len(parts) > 3 else ""
            if (
                blocking_type == "UNKNOWN"
                and notification_type == "NOTIFICATION_WORLD_CONGRESS_RESULTS"
            ):
                blocking_type = "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK"
            blockers.append((blocking_type, msg))
    return blockers


NOTIFICATION_TOOL_MAP: dict[str, str] = {
    "NOTIFICATION_CHOOSE_TECH": "set_research(tech_or_civic=..., category='tech')",
    "NOTIFICATION_CHOOSE_CIVIC": "set_research(tech_or_civic=..., category='civic')",
    "NOTIFICATION_CHOOSE_CITY_PRODUCTION": "set_city_production(city_id=..., item_type=..., item_name=...)",
    "NOTIFICATION_FILL_CIVIC_SLOT": "get_policies() then set_policies(assignments='...')",
    "NOTIFICATION_CONSIDER_GOVERNMENT_CHANGE": "get_policies() then set_policies()",
    "NOTIFICATION_CHOOSE_PANTHEON": "get_pantheon_beliefs() then choose_pantheon(belief_type=...)",
    "NOTIFICATION_CHOOSE_RELIGION": "get_religion_beliefs() then found_religion(religion_type=..., follower_belief=..., founder_belief=...)",
    "NOTIFICATION_CHOOSE_BELIEF": "get_religion_beliefs() then found_religion(religion_type=..., follower_belief=..., founder_belief=...)",
    "NOTIFICATION_DIPLOMACY_SESSION": "get_pending_diplomacy() then respond_to_diplomacy()",
    "NOTIFICATION_UNIT_PROMOTION_AVAILABLE": "get_unit_promotions(unit_id=...) then promote_unit()",
    "NOTIFICATION_CLAIM_GREAT_PERSON": "get_great_people() then recruit_great_person(individual_id=...) or patronize_great_person(individual_id=...) or reject_great_person(individual_id=...)",
    "NOTIFICATION_GIVE_INFLUENCE_TOKEN": "get_city_states() then send_envoy(player_id=...)",
    "NOTIFICATION_GOVERNOR_APPOINTMENT_AVAILABLE": "get_governors() then appoint_governor()",
    "NOTIFICATION_GOVERNOR_PROMOTION_AVAILABLE": "get_governors() then appoint_governor()",
    "NOTIFICATION_COMMEMORATION_AVAILABLE": "get_dedications() then choose_dedication(dedication_index=...)",
    "NOTIFICATION_WORLD_CONGRESS_BLOCKING": "get_world_congress() then queue_wc_votes()",
    "NOTIFICATION_WORLD_CONGRESS_RESULTS": "get_world_congress() (review results)",
    "NOTIFICATION_WORLD_CONGRESS_SPECIAL_SESSION_BLOCKING": "get_world_congress() then queue_wc_votes()",
    "NOTIFICATION_COMMAND_UNITS": "Units have moves remaining — move them or use skip_remaining_units()",
}


_ACTION_KEYWORDS = (
    "CHOOSE",
    "FILL",
    "CONSIDER",
    "GOVERNOR",
    "PANTHEON",
    "PROMOTION",
    "CLAIM",
    "INFLUENCE_TOKEN",
    "COMMEMORATION",
    "WORLD_CONGRESS",
    "ESCAPE",
)


BLOCKING_TOOL_MAP: dict[str, str] = {
    "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT": "Use get_governors() then appoint_governor()",
    "ENDTURN_BLOCKING_UNIT_PROMOTION": "Use get_unit_promotions(unit_id=...) then promote_unit()",
    "ENDTURN_BLOCKING_FILL_CIVIC_SLOT": "Use get_policies() then set_policies()",
    "ENDTURN_BLOCKING_PRODUCTION": "Use set_city_production()",
    "ENDTURN_BLOCKING_RESEARCH": "Use set_research()",
    "ENDTURN_BLOCKING_CIVIC": "Use set_research(category='civic')",
    "ENDTURN_BLOCKING_UNITS": "Move or skip remaining units",
    "ENDTURN_BLOCKING_PANTHEON": "Use get_pantheon_beliefs() then choose_pantheon(belief_type=...)",
    "ENDTURN_BLOCKING_STACKED_UNITS": "Move units — cannot stack military units",
    "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE": "Consider Changing Governments",
    "ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE": "Use get_dedications() then choose_dedication(dedication_index=...)",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION": "MUST VOTE: Use get_world_congress() to see resolutions, then queue_wc_votes() to register all votes, then end_turn(). Deploy ALL diplomatic favor!",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK": "Use get_world_congress() to review results (auto-resolved)",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION": "World Congress special session (auto-resolved)",
    "ENDTURN_BLOCKING_CONSIDER_RAZE_CITY": "Use city_action(city_id=..., action='keep') or 'raze'/'liberate'",
    "ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY": "Use city_action(city_id=..., action='keep') or 'reject'",
    "ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN": "Use get_city_states() then send_envoy(player_id=...)",
    "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE": "Auto-resolved: spy chooses fastest escape route",
}


def build_dismiss_blocking_popups() -> str:
    """Close known engine popups that block turn processing (InGame context).

    Observed live (2026-08-30, arena-channels-behavior-v7): the Historic
    Moments timeline and the Inspiration/Eureka popup (`BoostUnlockedPopup`)
    hold the end turn while the EndTurnBlocking query reads empty. Dequeue
    and hide each one that is currently visible.

    `NaturalDisasterPopup` is deliberately NOT hidden here: hiding it leaks
    its PopupManager engine hold (`UI.ReferenceCurrentEvent`), which is
    context-local and unreachable from InGame -- the interturn then wedges at
    PLEASE WAIT even after a full Close()-replication restore (proven live:
    v8 T159, event id 1640). A visible disaster popup is only REPORTED as
    skipped; dismissal belongs to the context-native path in
    `coordinator._dismiss_blocking_popups`, which runs the popup's own
    Close() in its Lua state. Reports 'POPUPS|none' or 'POPUPS|<name>,...'
    (with 'SKIPPED:NaturalDisasterPopup' when the disaster popup is up).
    """
    return """
local closed = {}
local names = {"HistoricMoments", "BoostUnlockedPopup"}
for _, name in ipairs(names) do
    local ctl = ContextPtr:LookUpControl("/InGame/" .. name)
    if ctl ~= nil and not ctl:IsHidden() then
        pcall(function() UIManager:DequeuePopup(ctl) end)
        pcall(function() ctl:SetHide(true) end)
        closed[#closed+1] = name
    end
end
local disaster = ContextPtr:LookUpControl("/InGame/NaturalDisasterPopup")
if disaster ~= nil and not disaster:IsHidden() then
    print("POPUP_SKIPPED|NaturalDisasterPopup")
end
if #closed > 0 then
    print("POPUPS|" .. table.concat(closed, ","))
else
    print("POPUPS|none")
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)
