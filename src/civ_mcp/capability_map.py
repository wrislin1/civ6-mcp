from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Literal


CoverageStatus = Literal["covered", "missing", "excluded"]
MissingPriority = Literal["high", "medium", "low"]
_STATUSES = {"covered", "missing", "excluded"}
_PRIORITIES = {"high", "medium", "low"}
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class Coverage:
    status: CoverageStatus
    tool: str | None = None
    priority: MissingPriority | None = None
    note: str | None = None


ACTION_COVERAGE: dict[str, Coverage] = {
    "DIPLOACTION_ALLIANCE": Coverage("covered", tool="form_alliance"),
    "DIPLOACTION_ALLIANCE_CULTURAL": Coverage("covered", tool="form_alliance"),
    "DIPLOACTION_ALLIANCE_ECONOMIC": Coverage("covered", tool="form_alliance"),
    "DIPLOACTION_ALLIANCE_MILITARY": Coverage("covered", tool="form_alliance"),
    "DIPLOACTION_ALLIANCE_RELIGIOUS": Coverage("covered", tool="form_alliance"),
    "DIPLOACTION_ALLIANCE_RESEARCH": Coverage("covered", tool="form_alliance"),
    "DIPLOACTION_DECLARE_COLONIAL_WAR": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_FORMAL_WAR": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_FRIENDSHIP": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_GOLDEN_AGE_WAR": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_HOLY_WAR": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_IDEOLOGICAL_WAR": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_LIBERATION_WAR": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_PROTECTORATE_WAR": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_RECONQUEST_WAR": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_SURPRISE_WAR": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_TERRITORIAL_WAR": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_WAR_MINOR_CIV": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DECLARE_WAR_OF_RETRIBUTION": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DEMAND_TRIBUTE": Coverage(
        "missing",
        priority="low",
        note="No tool issues a city-state tribute demand.",
    ),
    "DIPLOACTION_DENOUNCE": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_DIPLOMATIC_DELEGATION": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_GIFT_UNIT": Coverage(
        "missing",
        priority="medium",
        note="The seat cannot transfer a unit to another player.",
    ),
    "DIPLOACTION_GRANT_INFLUENCE_TOKEN": Coverage(
        "covered", tool="send_envoy"
    ),
    "DIPLOACTION_JOINT_WAR": Coverage("covered", tool="propose_trade"),
    "DIPLOACTION_KEEP_PROMISE_DONT_CONVERT": Coverage(
        "missing",
        priority="low",
        note="No tool explicitly records this diplomatic promise.",
    ),
    "DIPLOACTION_KEEP_PROMISE_DONT_DIG_ARTIFACTS": Coverage(
        "missing",
        priority="low",
        note="No tool explicitly records this diplomatic promise.",
    ),
    "DIPLOACTION_KEEP_PROMISE_DONT_SETTLE_TOO_NEAR": Coverage(
        "missing",
        priority="low",
        note="No tool explicitly records this diplomatic promise.",
    ),
    "DIPLOACTION_KEEP_PROMISE_DONT_SPY": Coverage(
        "missing",
        priority="low",
        note="No tool explicitly records this diplomatic promise.",
    ),
    "DIPLOACTION_LIBERATE_CITY": Coverage(
        "covered", tool="resolve_city_capture"
    ),
    "DIPLOACTION_MAKE_PEACE": Coverage("covered", tool="propose_peace"),
    "DIPLOACTION_MILITARY_REQUEST": Coverage(
        "missing",
        priority="low",
        note="No tool sends a military assistance request.",
    ),
    "DIPLOACTION_OPEN_BORDERS": Coverage("covered", tool="propose_trade"),
    "DIPLOACTION_PROPOSE_PEACE_DEAL": Coverage(
        "covered", tool="propose_peace"
    ),
    "DIPLOACTION_PROPOSE_TRADE": Coverage("covered", tool="propose_trade"),
    "DIPLOACTION_RENEW_ALLIANCE": Coverage("covered", tool="form_alliance"),
    "DIPLOACTION_REQUEST_ASSISTANCE": Coverage(
        "missing",
        priority="low",
        note="No tool sends a diplomatic assistance request.",
    ),
    "DIPLOACTION_RESIDENT_EMBASSY": Coverage(
        "covered", tool="send_diplomatic_action"
    ),
    "DIPLOACTION_THIRD_PARTY_WAR": Coverage(
        "covered", tool="propose_trade"
    ),
    "DIPLOACTION_USE_NUCLEAR_WEAPON": Coverage(
        "missing",
        priority="low",
        note="No tool can authorize a nuclear strike.",
    ),
    "DIPLOACTION_VIEW_DEMAND_TRIBUTE": Coverage(
        "excluded", note="Engine-internal UI action for opening the tribute view."
    ),
    "DIPLOACTION_VIEW_TRADE": Coverage(
        "excluded", note="Engine-internal UI action for opening the trade view."
    ),
    "UNITCOMMAND_ACTIVATE_GREAT_PERSON": Coverage(
        "covered", tool="activate_great_person"
    ),
    "UNITCOMMAND_AIRLIFT": Coverage(
        "missing",
        priority="low",
        note="No tool can airlift a unit between Aerodromes.",
    ),
    "UNITCOMMAND_AUTOMATE": Coverage("covered", tool="unit_action:automate"),
    "UNITCOMMAND_BUILDING_PRODUCTION": Coverage(
        "missing",
        priority="low",
        note="No tool issues the targeted Great Person building-production command.",
    ),
    "UNITCOMMAND_CANCEL": Coverage(
        "missing",
        priority="low",
        note="No tool cancels a unit's current operation.",
    ),
    "UNITCOMMAND_CONDEMN_HERETIC": Coverage(
        "missing",
        priority="medium",
        note="Military units cannot condemn rival religious units.",
    ),
    "UNITCOMMAND_DELETE": Coverage("covered", tool="unit_action:delete"),
    "UNITCOMMAND_DISTRICT_PRODUCTION": Coverage(
        "missing",
        priority="low",
        note="No tool issues the targeted Great Person district-production command.",
    ),
    "UNITCOMMAND_ENTER_FORMATION": Coverage(
        "missing",
        priority="medium",
        note="No tool attaches a civilian support unit to an escort formation.",
    ),
    "UNITCOMMAND_EXECUTE_SCRIPT": Coverage(
        "excluded", note="Debug-engine hook, not a player capability."
    ),
    "UNITCOMMAND_EXIT_FORMATION": Coverage(
        "missing",
        priority="medium",
        note="No tool detaches a unit from an escort formation.",
    ),
    "UNITCOMMAND_FORM_ARMY": Coverage("covered", tool="form_army"),
    "UNITCOMMAND_FORM_CORPS": Coverage("covered", tool="form_corps"),
    "UNITCOMMAND_GIFT": Coverage(
        "missing",
        priority="medium",
        note="The seat cannot gift a unit to another player.",
    ),
    "UNITCOMMAND_HARVEST_WONDER": Coverage(
        "missing",
        priority="low",
        note="No tool issues the targeted Great Person wonder-production command.",
    ),
    "UNITCOMMAND_MOVE_JUMP": Coverage(
        "excluded", note="Debug-engine teleport command, not normal player movement."
    ),
    "UNITCOMMAND_NAME_UNIT": Coverage(
        "excluded", note="Cosmetic unit naming does not affect gameplay capability."
    ),
    "UNITCOMMAND_PARADROP": Coverage(
        "missing",
        priority="low",
        note="No tool can paradrop an eligible unit.",
    ),
    "UNITCOMMAND_PET_THE_DOG": Coverage(
        "excluded", note="Cosmetic Scout animation does not affect gameplay."
    ),
    "UNITCOMMAND_PLUNDER_TRADE_ROUTE": Coverage(
        "missing",
        priority="high",
        note="The seat cannot destroy an enemy trade route for wartime yields.",
    ),
    "UNITCOMMAND_PRIORITY_TARGET": Coverage(
        "excluded", note="Engine-internal targeting command, not a standalone order."
    ),
    "UNITCOMMAND_PROJECT_PRODUCTION": Coverage(
        "missing",
        priority="low",
        note="No tool issues the targeted Great Person project-production command.",
    ),
    "UNITCOMMAND_PROMOTE": Coverage("covered", tool="promote_unit"),
    "UNITCOMMAND_SPREAD_DISSENT": Coverage(
        "missing",
        priority="medium",
        note="No tool can use a Rock Band's loyalty-pressure command.",
    ),
    "UNITCOMMAND_STOP_AUTOMATION": Coverage(
        "missing",
        priority="low",
        note="No tool explicitly stops an automated unit.",
    ),
    "UNITCOMMAND_UPGRADE": Coverage("covered", tool="upgrade_unit"),
    "UNITCOMMAND_WAKE": Coverage(
        "missing",
        priority="medium",
        note="No tool explicitly wakes a sleeping unit without another order.",
    ),
    "UNITCOMMAND_WONDER_PRODUCTION": Coverage(
        "missing",
        priority="low",
        note="No tool issues the targeted Great Person wonder-production command.",
    ),
    "UNITOPERATION_AIR_ATTACK": Coverage("covered", tool="attack_unit"),
    "UNITOPERATION_ALERT": Coverage("covered", tool="alert_unit"),
    "UNITOPERATION_AUTOMATE_EXPLORE": Coverage(
        "covered", tool="automate_explore"
    ),
    "UNITOPERATION_BUILD_IMPROVEMENT": Coverage(
        "covered", tool="improve_tile"
    ),
    "UNITOPERATION_BUILD_IMPROVEMENT_ADJACENT": Coverage(
        "missing",
        priority="low",
        note="No tool builds an improvement on an adjacent tile.",
    ),
    "UNITOPERATION_BUILD_ROUTE": Coverage(
        "covered", tool="unit_action:build_route"
    ),
    "UNITOPERATION_CLEAR_CONTAMINATION": Coverage(
        "missing",
        priority="medium",
        note="No tool can clear nuclear or reactor contamination.",
    ),
    "UNITOPERATION_COASTAL_RAID": Coverage(
        "missing",
        priority="high",
        note="Naval raiders cannot attack coastal improvements for wartime yields.",
    ),
    "UNITOPERATION_CONVERT_BARBARIANS": Coverage(
        "missing",
        priority="low",
        note="No tool can expend a charge to convert adjacent barbarians.",
    ),
    "UNITOPERATION_DEPLOY": Coverage(
        "missing",
        priority="low",
        note="No tool can deploy an air unit on patrol.",
    ),
    "UNITOPERATION_DESIGNATE_PARK": Coverage(
        "missing",
        priority="high",
        note="Naturalists cannot create National Parks for a culture victory.",
    ),
    "UNITOPERATION_DISEMBARK": Coverage(
        "excluded",
        note=(
            "Verified implicit behavior: GameState.move_unit auto-disembarked "
            "during the Task 1 live land-water movement probe."
        ),
    ),
    "UNITOPERATION_EMBARK": Coverage(
        "excluded",
        note=(
            "Verified implicit behavior: GameState.move_unit auto-embarked "
            "during the Task 1 live land-water movement probe."
        ),
    ),
    "UNITOPERATION_EVANGELIZE_BELIEF": Coverage(
        "missing",
        priority="medium",
        note="Apostles cannot spend a charge to add a religious belief.",
    ),
    "UNITOPERATION_EXCAVATE": Coverage(
        "covered", tool="excavate_artifact"
    ),
    "UNITOPERATION_EXECUTE_SCRIPT": Coverage(
        "excluded", note="Debug-engine hook, not a player capability."
    ),
    "UNITOPERATION_FORTIFY": Coverage("covered", tool="fortify_unit"),
    "UNITOPERATION_FOUND_CITY": Coverage("covered", tool="found_city"),
    "UNITOPERATION_FOUND_RELIGION": Coverage(
        "covered", tool="found_religion"
    ),
    "UNITOPERATION_HARVEST_RESOURCE": Coverage(
        "missing",
        priority="medium",
        note="Builders cannot harvest a bonus resource for immediate yields.",
    ),
    "UNITOPERATION_HEAL": Coverage("covered", tool="heal_unit"),
    "UNITOPERATION_LAUNCH_INQUISITION": Coverage(
        "missing",
        priority="medium",
        note="Apostles cannot launch an Inquisition.",
    ),
    "UNITOPERATION_MAKE_TRADE_ROUTE": Coverage(
        "covered", tool="start_trade_route"
    ),
    "UNITOPERATION_MOVE_TO": Coverage("covered", tool="move_unit"),
    "UNITOPERATION_MOVE_TO_UNIT": Coverage(
        "excluded", note="Engine-internal pathing operation, not a standalone order."
    ),
    "UNITOPERATION_PILLAGE": Coverage(
        "missing",
        priority="high",
        note="Military units cannot pillage districts or improvements in war.",
    ),
    "UNITOPERATION_PILLAGE_ROUTE": Coverage(
        "missing",
        priority="high",
        note="Military units cannot pillage roads or railroads in war.",
    ),
    "UNITOPERATION_PLANT_FOREST": Coverage(
        "missing",
        priority="medium",
        note="Builders cannot plant woods for production or appeal.",
    ),
    "UNITOPERATION_RANGE_ATTACK": Coverage("covered", tool="attack_unit"),
    "UNITOPERATION_REBASE": Coverage("covered", tool="rebase_unit"),
    "UNITOPERATION_RELIGIOUS_HEAL": Coverage(
        "missing",
        priority="low",
        note="No tool issues the religious-unit heal operation.",
    ),
    "UNITOPERATION_REMOVE_FEATURE": Coverage(
        "covered", tool="remove_feature"
    ),
    "UNITOPERATION_REMOVE_HERESY": Coverage(
        "missing",
        priority="medium",
        note="Inquisitors cannot remove foreign religious pressure.",
    ),
    "UNITOPERATION_REMOVE_IMPROVEMENT": Coverage(
        "covered", tool="unit_action:remove_improvement"
    ),
    "UNITOPERATION_REPAIR": Coverage(
        "covered", tool="repair_improvement"
    ),
    "UNITOPERATION_REPAIR_ROUTE": Coverage(
        "missing",
        priority="low",
        note="No tool explicitly repairs a pillaged road or railroad.",
    ),
    "UNITOPERATION_REST_REPAIR": Coverage(
        "missing",
        priority="low",
        note="No tool issues the air-unit rest-and-repair operation.",
    ),
    "UNITOPERATION_RETRAIN": Coverage(
        "missing",
        priority="low",
        note="No tool issues the unit retraining operation.",
    ),
    "UNITOPERATION_ROUTE_TO": Coverage(
        "missing",
        priority="medium",
        note="Military Engineers cannot build a route along a multi-tile path.",
    ),
    "UNITOPERATION_SKIP_TURN": Coverage("covered", tool="skip_unit"),
    "UNITOPERATION_SLEEP": Coverage("covered", tool="unit_action:sleep"),
    "UNITOPERATION_SPREAD_RELIGION": Coverage(
        "covered", tool="spread_religion"
    ),
    "UNITOPERATION_SPY_BREACH_DAM": Coverage(
        "missing",
        priority="medium",
        note="The espionage tool has no Breach Dam mission hash.",
    ),
    "UNITOPERATION_SPY_COUNTERSPY": Coverage("covered", tool="spy_action"),
    "UNITOPERATION_SPY_DISRUPT_ROCKETRY": Coverage(
        "missing",
        priority="medium",
        note="The espionage tool has no Disrupt Rocketry mission hash.",
    ),
    "UNITOPERATION_SPY_FABRICATE_SCANDAL": Coverage(
        "covered", tool="spy_action"
    ),
    "UNITOPERATION_SPY_FOMENT_UNREST": Coverage(
        "missing",
        priority="medium",
        note="The espionage tool has no Foment Unrest mission hash.",
    ),
    "UNITOPERATION_SPY_GAIN_SOURCES": Coverage(
        "covered", tool="spy_action"
    ),
    "UNITOPERATION_SPY_GREAT_WORK_HEIST": Coverage(
        "covered", tool="spy_action"
    ),
    "UNITOPERATION_SPY_LISTENING_POST": Coverage(
        "missing",
        priority="medium",
        note="The espionage tool has no Listening Post mission hash.",
    ),
    "UNITOPERATION_SPY_NEUTRALIZE_GOVERNOR": Coverage(
        "covered", tool="spy_action"
    ),
    "UNITOPERATION_SPY_RECRUIT_PARTISANS": Coverage(
        "covered", tool="spy_action"
    ),
    "UNITOPERATION_SPY_SABOTAGE_PRODUCTION": Coverage(
        "covered", tool="spy_action"
    ),
    "UNITOPERATION_SPY_SIPHON_FUNDS": Coverage(
        "covered", tool="spy_action"
    ),
    "UNITOPERATION_SPY_STEAL_TECH_BOOST": Coverage(
        "covered", tool="spy_action"
    ),
    "UNITOPERATION_SPY_TRAVEL_NEW_CITY": Coverage(
        "covered", tool="spy_action"
    ),
    "UNITOPERATION_SWAP_UNITS": Coverage(
        "missing",
        priority="medium",
        note="No tool can explicitly swap two friendly units.",
    ),
    "UNITOPERATION_TELEPORT_TO": Coverage(
        "excluded",
        note="Engine-internal generic teleport operation, not a player order.",
    ),
    "UNITOPERATION_TELEPORT_TO_CITY": Coverage(
        "covered", tool="teleport_trader"
    ),
    "UNITOPERATION_TOURISM_BOMB": Coverage(
        "missing",
        priority="high",
        note="No tool can trigger a Tourism Bomb for a culture victory.",
    ),
    "UNITOPERATION_UPGRADE": Coverage("covered", tool="upgrade_unit"),
    "UNITOPERATION_WAIT_FOR": Coverage(
        "excluded", note="Engine-internal operation used to coordinate queued orders."
    ),
    "UNITOPERATION_WMD_STRIKE": Coverage(
        "missing",
        priority="low",
        note="No tool can launch a weapon-of-mass-destruction strike.",
    ),
}


def _snapshot_actions(snapshot: Mapping[str, object]) -> set[str]:
    if snapshot.get("schema_version") != 1:
        raise ValueError("unsupported snapshot schema_version")
    tables = snapshot.get("tables")
    if not isinstance(tables, Mapping):
        raise ValueError("snapshot tables must be an object")
    return {
        action
        for values in tables.values()
        if isinstance(values, list)
        for action in values
        if isinstance(action, str)
    }


def validate_coverage(
    snapshot: Mapping[str, object],
    coverage: Mapping[str, Coverage],
    *,
    arena_tools: Set[str],
    unit_action_verbs: Set[str],
) -> None:
    actions = _snapshot_actions(snapshot)
    errors: list[str] = []
    unclassified = sorted(actions - coverage.keys())
    stale = sorted(coverage.keys() - actions)
    if unclassified:
        errors.append(
            "unclassified actions (edit capability_map.py): "
            + ", ".join(unclassified)
        )
    if stale:
        errors.append("stale coverage entries: " + ", ".join(stale))

    for action, item in sorted(coverage.items()):
        if item.status not in _STATUSES:
            errors.append(f"{action}: invalid status {item.status!r}")
        elif item.status == "covered":
            if not item.tool:
                errors.append(f"{action}: covered entry requires tool")
            elif item.tool.startswith("unit_action:"):
                verb = item.tool.partition(":")[2]
                if verb not in unit_action_verbs:
                    errors.append(f"{action}: unknown covered tool {item.tool}")
            elif item.tool not in arena_tools:
                errors.append(f"{action}: unknown covered tool {item.tool}")
            if item.priority is not None:
                errors.append(f"{action}: covered entry cannot have priority")
        elif item.status == "missing":
            if item.tool is not None:
                errors.append(f"{action}: missing entry cannot have tool")
            if item.priority not in _PRIORITIES:
                errors.append(f"{action}: missing entry requires valid priority")
            if not item.note or not item.note.strip():
                errors.append(f"{action}: missing entry requires note")
        elif item.status == "excluded":
            if item.tool is not None:
                errors.append(f"{action}: excluded entry cannot have tool")
            if not item.note or not item.note.strip():
                errors.append(f"{action}: excluded entry requires note")
            if item.priority is not None:
                errors.append(f"{action}: excluded entry cannot have priority")

    if errors:
        raise ValueError("\n".join(errors))


def build_report_evidence(
    snapshot: Mapping[str, object],
    coverage: Mapping[str, Coverage],
) -> dict[str, object]:
    actions = _snapshot_actions(snapshot)
    counts = Counter(
        coverage[action].status for action in actions if action in coverage
    )
    missing = sorted(
        (
            {
                "action": action,
                "priority": item.priority,
                "note": item.note,
            }
            for action, item in coverage.items()
            if item.status == "missing"
        ),
        key=lambda row: (
            _PRIORITY_ORDER[row["priority"]],
            row["action"],
        ),
    )
    return {
        "counts": {
            "covered": counts["covered"],
            "missing": counts["missing"],
            "excluded": counts["excluded"],
            "total": len(actions),
        },
        "missing": missing,
    }
