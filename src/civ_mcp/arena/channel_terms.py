"""Typed observations and pure verifiers for deterministic channel terms."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Iterable, Literal


class ObservationFamily(StrEnum):
    UNITS = "units"
    TERRITORY = "territory"
    CITIES = "cities"
    TREASURY = "treasury"
    DIPLOMACY = "diplomacy"
    TRADE_ROUTES = "trade_routes"
    CAMPS = "camps"
    ACTION_AUDIT = "action_audit"


@dataclass(frozen=True)
class ObservedUnit:
    owner_id: int
    unit_id: int
    unit_type: str
    formation_class: str
    religious_strength: int
    x: int
    y: int


MILITARY_FORMATIONS = frozenset(
    {
        "FORMATION_CLASS_LAND_COMBAT",
        "FORMATION_CLASS_NAVAL",
        "FORMATION_CLASS_AIR",
        "FORMATION_CLASS_SUPPORT",
    }
)


@dataclass(frozen=True)
class ObservedCity:
    owner_id: int
    city_id: int
    x: int
    y: int
    component_id: int | None = None
    original_owner: int | None = None


@dataclass(frozen=True)
class ObservedRoute:
    owner_id: int
    trader_unit_id: int
    destination_player: int
    destination_is_city_state: bool


def _freeze_action_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenDict(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_action_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_action_value(item) for item in value)
    return value


class _FrozenDict(dict):
    """Immutable dict compatible with equality, JSON, and dataclasses.asdict."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        source = dict(*args, **kwargs)
        dict.__init__(
            self,
            (
                (key, _freeze_action_value(value))
                for key, value in source.items()
            ),
        )

    @staticmethod
    def _immutable(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("action tool arguments are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


@dataclass(frozen=True)
class ObservedAction:
    actor_id: int
    turn: int
    tool_name: str
    tool_args: dict[str, Any]
    tool_result_full: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_args", _FrozenDict(self.tool_args))


@dataclass(frozen=True)
class ObservationRequest:
    families: frozenset[ObservationFamily] = frozenset()
    protected_players: tuple[int, ...] = ()
    zone_centers: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class ChannelObservation:
    player_id: int
    turn: int
    families_present: frozenset[ObservationFamily] = frozenset()
    units: tuple[ObservedUnit, ...] = ()
    cities: tuple[ObservedCity, ...] = ()
    camps: frozenset[tuple[int, int]] = frozenset()
    territory: frozenset[tuple[int, int]] = frozenset()
    wars: frozenset[tuple[int, int]] = frozenset()
    treasury_gold: int = 0
    trade_routes: tuple[ObservedRoute, ...] = ()
    action_audit: tuple[ObservedAction, ...] = ()
    unit_distances: dict[tuple[int, int, int], int] = field(default_factory=dict)
    zone_distances: dict[tuple[int, int, int], int] = field(default_factory=dict)
    errors: tuple[str, ...] = ()


class TermMode(StrEnum):
    ENDPOINT = "endpoint"
    CONTINUOUS = "continuous"


@dataclass(frozen=True)
class Verification:
    status: Literal["pending", "satisfied", "failed", "unverifiable"]
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()
    monitor: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TermSpec:
    term_type: str
    mode: TermMode
    baseline_phase: Literal["proposal", "favor_start"]
    families: frozenset[ObservationFamily]
    validate_params: Callable[[dict, TermValidationContext], dict]
    capture_baseline: Callable[[dict, ChannelObservation], dict]
    verify: Callable[
        [dict, dict, dict, ChannelObservation, int], Verification
    ]
    render_evidence: Callable[[Verification], str]


@dataclass(frozen=True)
class TermValidationContext:
    obligated_player: int
    enabled_players: frozenset[int]
    city_state_players: frozenset[int] = frozenset()
    instrumented_trade_players: frozenset[int] = frozenset()


def _has_result_prefix(result: str, prefix: str) -> bool:
    return result == prefix or result.startswith(f"{prefix}|")


def _is_successful_trade_action(
    tool_name: object, tool_args: object, tool_result_full: object
) -> bool:
    if not isinstance(tool_args, dict) or not isinstance(tool_result_full, str):
        return False
    target = tool_args.get("other_player_id")
    if isinstance(target, bool) or not isinstance(target, int):
        return False
    if tool_name == "propose_trade":
        mode = tool_args.get("mode")
        if isinstance(mode, str) and mode.lower() == "test":
            return False
        return any(
            _has_result_prefix(tool_result_full, prefix)
            for prefix in ("OK:PROPOSED", "OK:ACCEPTED")
        )
    if tool_name == "respond_to_trade":
        return (
            tool_args.get("accept") is True
            and _has_result_prefix(tool_result_full, "OK:DEAL_ACCEPTED")
        )
    return False


def normalize_action_audit(
    policy_result: dict | None, actor: int, turn: int
) -> tuple[ObservedAction, ...]:
    if not isinstance(policy_result, dict):
        return ()
    transcript = policy_result.get("transcript")
    if not isinstance(transcript, dict):
        return ()
    steps = transcript.get("steps")
    if not isinstance(steps, list):
        return ()

    actions: list[ObservedAction] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = step.get("tool_name")
        tool_args = step.get("tool_args")
        tool_result_full = step.get("tool_result_full")
        if tool_name == "propose_trade" and (
            not isinstance(tool_args, dict)
            or not isinstance(tool_args.get("mode"), str)
            or tool_args["mode"].lower() != "send"
        ):
            continue
        if not _is_successful_trade_action(
            tool_name, tool_args, tool_result_full
        ):
            continue
        actions.append(
            ObservedAction(
                actor_id=actor,
                turn=turn,
                tool_name=tool_name,
                tool_args=dict(tool_args),
                tool_result_full=tool_result_full,
            )
        )
    return tuple(actions)


def _require_exact_fields(value: object, label: str, fields: frozenset[str]) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - fields)
    if unknown:
        raise ValueError(f"unknown field(s) in {label}: {', '.join(unknown)}")
    missing = sorted(fields - set(value))
    if missing:
        raise ValueError(f"missing field(s) in {label}: {', '.join(missing)}")
    return value


def _require_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _validate_coordinates(params: dict, _: TermValidationContext) -> dict:
    params = _require_exact_fields(params, "term params", frozenset({"x", "y"}))
    return {
        "x": _require_integer(params["x"], "x"),
        "y": _require_integer(params["y"], "y"),
    }


def _validate_zone(params: dict, _: TermValidationContext) -> dict:
    params = _require_exact_fields(
        params, "term params", frozenset({"x", "y", "radius"})
    )
    radius = _require_integer(params["radius"], "radius")
    if not 1 <= radius <= 10:
        raise ValueError("radius must be 1..10")
    return {
        "x": _require_integer(params["x"], "x"),
        "y": _require_integer(params["y"], "y"),
        "radius": radius,
    }


def _validate_player(params: dict, context: TermValidationContext) -> dict:
    params = _require_exact_fields(params, "term params", frozenset({"player_id"}))
    player_id = _require_integer(params["player_id"], "player_id")
    if player_id == context.obligated_player:
        raise ValueError("term cannot target the obligated player")
    legal_players = context.enabled_players | context.city_state_players
    if player_id not in legal_players:
        raise ValueError(f"player {player_id} is not a legal target")
    return {"player_id": player_id}


def _validate_trade_target(
    params: dict, context: TermValidationContext
) -> int:
    target_player = _require_integer(params["target_player"], "target_player")
    if target_player == context.obligated_player:
        raise ValueError("term cannot target the obligated player")
    legal_players = context.enabled_players | context.city_state_players
    if target_player not in legal_players:
        raise ValueError(f"player {target_player} is not a legal target")
    return target_player


def _validate_dont_trade(
    params: dict, context: TermValidationContext
) -> dict:
    params = _require_exact_fields(
        params,
        "term params",
        frozenset({"target_player", "trade_kinds"}),
    )
    target_player = _validate_trade_target(params, context)
    trade_kinds = params["trade_kinds"]
    supported = {"diplomatic_deal", "trade_route"}
    if (
        not isinstance(trade_kinds, list)
        or not trade_kinds
        or any(not isinstance(kind, str) or kind not in supported for kind in trade_kinds)
    ):
        raise ValueError(
            "trade_kinds must be a non-empty list containing diplomatic_deal or trade_route"
        )
    if len(set(trade_kinds)) != len(trade_kinds):
        raise ValueError("trade_kinds must not contain duplicates")
    if (
        target_player in context.city_state_players
        and "diplomatic_deal" in trade_kinds
    ):
        raise ValueError("city-state targets support trade_route only")
    if (
        "diplomatic_deal" in trade_kinds
        and context.obligated_player not in context.instrumented_trade_players
    ):
        raise ValueError(
            "diplomatic_deal requires an instrumented obligated trade player"
        )
    return {"target_player": target_player, "trade_kinds": list(trade_kinds)}


def _validate_trade_route_target(
    params: dict, context: TermValidationContext
) -> dict:
    params = _require_exact_fields(
        params, "term params", frozenset({"target_player"})
    )
    return {"target_player": _validate_trade_target(params, context)}


def _validate_gold(params: dict, _: TermValidationContext) -> dict:
    params = _require_exact_fields(params, "term params", frozenset({"min_gold"}))
    min_gold = _require_integer(params["min_gold"], "min_gold")
    if not 0 <= min_gold <= 10_000:
        raise ValueError("min_gold must be 0..10000")
    return {"min_gold": min_gold}


def _validate_unit_category(params: dict, _: TermValidationContext) -> dict:
    params = _require_exact_fields(
        params, "term params", frozenset({"category"})
    )
    category = params["category"]
    if not isinstance(category, str) or category not in {
        "military",
        "civilian",
        "religious",
    }:
        raise ValueError("category must be military, civilian, or religious")
    return {"category": category}


def _validate_military_unit_cap(params: dict, _: TermValidationContext) -> dict:
    params = _require_exact_fields(
        params, "term params", frozenset({"max_units"})
    )
    max_units = _require_integer(params["max_units"], "max_units")
    if max_units < 0:
        raise ValueError("max_units must be non-negative")
    return {"max_units": max_units}


def _validate_unit_distance(
    params: dict, context: TermValidationContext
) -> dict:
    params = _require_exact_fields(
        params,
        "term params",
        frozenset({"player_id", "min_distance", "unit_scope"}),
    )
    player_id = _require_integer(params["player_id"], "player_id")
    if player_id == context.obligated_player:
        raise ValueError("term cannot target the obligated player")
    legal_players = context.enabled_players | context.city_state_players
    if player_id not in legal_players:
        raise ValueError(f"player {player_id} is not a legal target")
    min_distance = _require_integer(params["min_distance"], "min_distance")
    if not 1 <= min_distance <= 10:
        raise ValueError("min_distance must be 1..10")
    unit_scope = params["unit_scope"]
    if not isinstance(unit_scope, str) or unit_scope not in {"military", "all"}:
        raise ValueError("unit_scope must be military or all")
    return {
        "player_id": player_id,
        "min_distance": min_distance,
        "unit_scope": unit_scope,
    }


def _observation_ref(observation: ChannelObservation, monitor: dict) -> str:
    supplied = monitor.get("current_observation_id", monitor.get("observation_id"))
    if isinstance(supplied, str) and supplied:
        return supplied
    return f"turn:{observation.turn}"


def _evidence_refs(reference: object) -> tuple[str, ...]:
    if isinstance(reference, str) and reference.startswith("obs-"):
        return (reference,)
    return ()


def _persistent_violation(
    term_type: str, monitor: dict
) -> Verification | None:
    reference = monitor.get("violation_observation_id")
    if reference is None:
        return None
    persisted_monitor = {
        key: value
        for key, value in monitor.items()
        if key not in {"current_observation_id", "observation_id"}
    }
    return Verification(
        "failed",
        f"{term_type} was violated in an earlier observation",
        _evidence_refs(reference),
        persisted_monitor,
    )


def _violation(
    reason: str, observation: ChannelObservation, monitor: dict
) -> Verification:
    reference = _observation_ref(observation, monitor)
    return Verification(
        "failed",
        reason,
        _evidence_refs(reference),
        {"violation_observation_id": reference},
    )


def _violation_at(reason: str, reference: str) -> Verification:
    return Verification(
        "failed",
        reason,
        _evidence_refs(reference),
        {"violation_observation_id": reference},
    )


def _mark_incomplete(observation: ChannelObservation, monitor: dict) -> dict:
    updated = {
        key: value
        for key, value in monitor.items()
        if key not in {"current_observation_id", "observation_id"}
    }
    updated.setdefault(
        "incomplete_observation_id", _observation_ref(observation, monitor)
    )
    return updated


def _deadline_result(
    observation: ChannelObservation,
    due_turn: int,
    *,
    terminal_status: Literal["satisfied", "failed"],
    reason: str,
    monitor: dict,
) -> Verification:
    if observation.turn < due_turn:
        return Verification("pending", monitor=monitor)
    if "incomplete_observation_id" in monitor:
        return Verification(
            "unverifiable",
            "a required observation was incomplete before the deadline",
            monitor=monitor,
        )
    return Verification(terminal_status, reason, monitor=monitor)


def _capture_destroy_camp(params: dict, observation: ChannelObservation) -> dict:
    if ObservationFamily.CAMPS not in observation.families_present:
        raise ValueError("camp observation incomplete at proposal")
    coordinate = (params["x"], params["y"])
    if coordinate not in observation.camps:
        raise ValueError("camp must exist at proposal")
    return {"proposal_turn": observation.turn, "camp_present": True}


def _capture_city_identities(params: dict, observation: ChannelObservation) -> dict:
    del params
    return {
        "baseline_complete": ObservationFamily.CITIES
        in observation.families_present,
        "city_component_ids": tuple(
            sorted(
                city.component_id
                for city in observation.cities
                if city.component_id is not None
            )
        ),
        "city_ids": tuple(
            sorted(
                (city.owner_id, city.city_id)
                for city in observation.cities
                if city.component_id is None
                and city.owner_id == observation.player_id
            )
        ),
        "favor_started_turn": observation.turn,
    }


def _capture_empty(params: dict, observation: ChannelObservation) -> dict:
    del params
    return {"favor_started_turn": observation.turn}


def _route_identity(route: ObservedRoute) -> tuple[int, int, int, bool]:
    return (
        route.owner_id,
        route.trader_unit_id,
        route.destination_player,
        route.destination_is_city_state,
    )


def _action_identity(action: ObservedAction) -> tuple[int, int, str, int] | None:
    target = action.tool_args.get("other_player_id")
    if isinstance(target, bool) or not isinstance(target, int):
        return None
    return (action.actor_id, action.turn, action.tool_name, target)


def _observed_action_is_successful(action: ObservedAction) -> bool:
    return _is_successful_trade_action(
        action.tool_name, action.tool_args, action.tool_result_full
    )


def _capture_trade_identities(
    params: dict, observation: ChannelObservation
) -> dict:
    kinds = set(params["trade_kinds"])
    route_complete = ObservationFamily.TRADE_ROUTES in observation.families_present
    audit_complete = ObservationFamily.ACTION_AUDIT in observation.families_present
    return {
        "route_baseline_complete": "trade_route" not in kinds or route_complete,
        "route_ids": tuple(
            sorted(_route_identity(route) for route in observation.trade_routes)
        )
        if route_complete
        else (),
        "audit_baseline_complete": (
            "diplomatic_deal" not in kinds or audit_complete
        ),
        "action_ids": tuple(
            sorted(
                identity
                for action in observation.action_audit
                if _observed_action_is_successful(action)
                if (identity := _action_identity(action)) is not None
            )
        )
        if audit_complete
        else (),
        "favor_started_turn": observation.turn,
    }


def _capture_peace(params: dict, observation: ChannelObservation) -> dict:
    war = (observation.player_id, params["player_id"])
    complete = ObservationFamily.DIPLOMACY in observation.families_present
    return {
        "baseline_complete": complete,
        "initial_violation_turn": observation.turn
        if complete and war in observation.wars
        else None,
        "initial_violation_observation_id": f"turn:{observation.turn}"
        if complete and war in observation.wars
        else None,
        "favor_started_turn": observation.turn,
    }


def _capture_gold(params: dict, observation: ChannelObservation) -> dict:
    complete = ObservationFamily.TREASURY in observation.families_present
    return {
        "baseline_complete": complete,
        "initial_violation_turn": observation.turn
        if complete and observation.treasury_gold < params["min_gold"]
        else None,
        "initial_violation_observation_id": f"turn:{observation.turn}"
        if complete and observation.treasury_gold < params["min_gold"]
        else None,
        "favor_started_turn": observation.turn,
    }


def _capture_unit_identities(
    params: dict, observation: ChannelObservation
) -> dict:
    del params
    return {
        "baseline_complete": ObservationFamily.UNITS
        in observation.families_present,
        "unit_ids": tuple(
            sorted(
                (unit.owner_id, unit.unit_id)
                for unit in observation.units
                if unit.owner_id == observation.player_id
            )
        ),
        "favor_started_turn": observation.turn,
    }


def _military_unit_count(observation: ChannelObservation) -> int:
    return sum(
        unit.owner_id == observation.player_id
        and "military" in unit_categories(unit)
        for unit in observation.units
    )


def _capture_military_unit_cap(
    params: dict, observation: ChannelObservation
) -> dict:
    complete = ObservationFamily.UNITS in observation.families_present
    count = _military_unit_count(observation) if complete else None
    exceeds_cap = count is not None and count > params["max_units"]
    return {
        "baseline_complete": complete,
        "initial_violation_turn": observation.turn if exceeds_cap else None,
        "initial_violation_observation_id": f"turn:{observation.turn}"
        if exceeds_cap
        else None,
        "initial_violation_count": count if exceeds_cap else None,
        "favor_started_turn": observation.turn,
    }


def _verify_destroy_camp(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    if observation.turn <= baseline["proposal_turn"]:
        return Verification("pending", monitor=monitor)
    if (params["x"], params["y"]) not in observation.camps:
        return Verification(
            "satisfied",
            f"camp at ({params['x']}, {params['y']}) was absent on turn {observation.turn}",
            monitor=monitor,
        )
    return _deadline_result(
        observation,
        due_turn,
        terminal_status="failed",
        reason=f"camp at ({params['x']}, {params['y']}) remained at the deadline",
        monitor=monitor,
    )


def _baseline_city_ids(baseline: dict) -> frozenset[tuple[int, int]]:
    return frozenset(tuple(identity) for identity in baseline.get("city_ids", ()))


def _baseline_city_component_ids(baseline: dict) -> frozenset[int]:
    return frozenset(baseline.get("city_component_ids", ()))


def _new_owned_cities(
    baseline: dict, observation: ChannelObservation
) -> tuple[ObservedCity, ...]:
    existing_components = _baseline_city_component_ids(baseline)
    existing_legacy = _baseline_city_ids(baseline)
    return tuple(
        city
        for city in observation.cities
        if city.owner_id == observation.player_id
        and (city.original_owner if city.original_owner is not None else city.owner_id)
        == observation.player_id
        and (
            (
                city.component_id is not None
                and city.component_id not in existing_components
            )
            or (
                city.component_id is None
                and (city.owner_id, city.city_id) not in existing_legacy
            )
        )
    )


def _zone_distance(
    city: ObservedCity, params: dict, observation: ChannelObservation
) -> int | None:
    return observation.zone_distances.get((city.city_id, params["x"], params["y"]))


def _verify_found_city(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    if not baseline.get("baseline_complete", False):
        monitor = dict(monitor)
        monitor.setdefault("incomplete_observation_id", "favor_start")
        return _deadline_result(
            observation,
            due_turn,
            terminal_status="failed",
            reason="no qualifying new city was observed by the deadline",
            monitor=monitor,
        )
    new_cities = _new_owned_cities(baseline, observation)
    distances = tuple(_zone_distance(city, params, observation) for city in new_cities)
    if any(distance is not None and distance <= params["radius"] for distance in distances):
        return Verification(
            "satisfied",
            "a new city was founded within "
            f"{params['radius']} hexes of ({params['x']}, {params['y']}) "
            f"by turn {observation.turn}",
            monitor=monitor,
        )
    if any(distance is None for distance in distances):
        monitor = _mark_incomplete(observation, monitor)
    return _deadline_result(
        observation,
        due_turn,
        terminal_status="failed",
        reason="no qualifying new city was observed by the deadline",
        monitor=monitor,
    )


def _verify_dont_settle(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    if not baseline.get("baseline_complete", False):
        monitor = dict(monitor)
        monitor.setdefault("incomplete_observation_id", "favor_start")
        return _deadline_result(
            observation,
            due_turn,
            terminal_status="satisfied",
            reason="no settlement boundary violation was observed",
            monitor=monitor,
        )
    new_cities = _new_owned_cities(baseline, observation)
    distances = tuple(_zone_distance(city, params, observation) for city in new_cities)
    if any(distance is not None and distance <= params["radius"] for distance in distances):
        return _violation(
            "the accepted settlement boundary around "
            f"({params['x']}, {params['y']}) was violated on turn {observation.turn}",
            observation,
            monitor,
        )
    if any(distance is None for distance in distances):
        monitor = _mark_incomplete(observation, monitor)
    return _deadline_result(
        observation,
        due_turn,
        terminal_status="satisfied",
        reason="no settlement boundary violation was observed",
        monitor=monitor,
    )


def _verify_declare_war(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    del baseline
    if (observation.player_id, params["player_id"]) in observation.wars:
        return Verification(
            "satisfied",
            f"war with player {params['player_id']} was observed on turn {observation.turn}",
            monitor=monitor,
        )
    return _deadline_result(
        observation,
        due_turn,
        terminal_status="failed",
        reason=f"war with player {params['player_id']} was not observed by the deadline",
        monitor=monitor,
    )


def _verify_keep_peace(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    initial_violation_turn = baseline.get("initial_violation_turn")
    if initial_violation_turn is not None:
        return _violation_at(
            f"war with player {params['player_id']} was observed on turn {initial_violation_turn}",
            baseline["initial_violation_observation_id"],
        )
    if not baseline.get("baseline_complete", True):
        monitor = dict(monitor)
        monitor.setdefault("incomplete_observation_id", "favor_start")
    if (observation.player_id, params["player_id"]) in observation.wars:
        return _violation(
            f"war with player {params['player_id']} was observed on turn {observation.turn}",
            observation,
            monitor,
        )
    return _deadline_result(
        observation,
        due_turn,
        terminal_status="satisfied",
        reason=f"peace with player {params['player_id']} was maintained",
        monitor=monitor,
    )


def _verify_gold(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    initial_violation_turn = baseline.get("initial_violation_turn")
    if initial_violation_turn is not None:
        return _violation_at(
            f"gold fell below {params['min_gold']} on turn {initial_violation_turn}",
            baseline["initial_violation_observation_id"],
        )
    if not baseline.get("baseline_complete", True):
        monitor = dict(monitor)
        monitor.setdefault("incomplete_observation_id", "favor_start")
    if observation.treasury_gold < params["min_gold"]:
        return _violation(
            f"gold fell below {params['min_gold']} on turn {observation.turn}",
            observation,
            monitor,
        )
    return _deadline_result(
        observation,
        due_turn,
        terminal_status="satisfied",
        reason=f"gold remained at or above {params['min_gold']}",
        monitor=monitor,
    )


def _baseline_unit_ids(baseline: dict) -> frozenset[tuple[int, int]]:
    return frozenset(tuple(identity) for identity in baseline.get("unit_ids", ()))


def _seen_unit_ids(monitor: dict) -> frozenset[tuple[int, int]]:
    return frozenset(tuple(identity) for identity in monitor.get("seen_unit_ids", ()))


def _new_owned_units(
    baseline: dict, monitor: dict, observation: ChannelObservation
) -> tuple[ObservedUnit, ...]:
    existing = _baseline_unit_ids(baseline) | _seen_unit_ids(monitor)
    return tuple(
        unit
        for unit in observation.units
        if unit.owner_id == observation.player_id
        and (unit.owner_id, unit.unit_id) not in existing
    )


def _remember_owned_unit_ids(
    monitor: dict, observation: ChannelObservation
) -> dict:
    updated = dict(monitor)
    seen = _seen_unit_ids(monitor) | frozenset(
        (unit.owner_id, unit.unit_id)
        for unit in observation.units
        if unit.owner_id == observation.player_id
    )
    updated["seen_unit_ids"] = tuple(sorted(seen))
    return updated


def _verify_unit_acquisition(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
    *,
    allow_only: bool,
) -> Verification:
    if not baseline.get("baseline_complete", False):
        monitor = dict(monitor)
        monitor.setdefault("incomplete_observation_id", "favor_start")
        return _deadline_result(
            observation,
            due_turn,
            terminal_status="satisfied",
            reason="no prohibited unit acquisition was observed",
            monitor=monitor,
        )
    category = params["category"]
    new_units = _new_owned_units(baseline, monitor, observation)
    if allow_only:
        violated = any(category not in unit_categories(unit) for unit in new_units)
        reason = (
            f"a newly acquired unit omitted the accepted {category} category "
            f"on turn {observation.turn}"
        )
    else:
        violated = any(category in unit_categories(unit) for unit in new_units)
        reason = (
            f"a new {category} unit was acquired on turn {observation.turn}"
        )
    if violated:
        return _violation(reason, observation, monitor)
    monitor = _remember_owned_unit_ids(monitor, observation)
    return _deadline_result(
        observation,
        due_turn,
        terminal_status="satisfied",
        reason="no prohibited unit acquisition was observed",
        monitor=monitor,
    )


def _verify_forbid_unit_acquisition(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    return _verify_unit_acquisition(
        params,
        baseline,
        monitor,
        observation,
        due_turn,
        allow_only=False,
    )


def _verify_allow_only_unit_category(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    return _verify_unit_acquisition(
        params,
        baseline,
        monitor,
        observation,
        due_turn,
        allow_only=True,
    )


def _military_cap_violation(
    params: dict,
    count: int,
    turn: int,
    observation_reference: str,
) -> Verification:
    return Verification(
        "failed",
        f"the military unit cap of {params['max_units']} was exceeded "
        f"by a count of {count} on turn {turn}",
        _evidence_refs(observation_reference),
        {
            "violation_observation_id": observation_reference,
            "violation_count": count,
        },
    )


def _verify_military_unit_cap(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    initial_violation_turn = baseline.get("initial_violation_turn")
    if initial_violation_turn is not None:
        return _military_cap_violation(
            params,
            baseline["initial_violation_count"],
            initial_violation_turn,
            baseline["initial_violation_observation_id"],
        )
    if not baseline.get("baseline_complete", True):
        monitor = dict(monitor)
        monitor.setdefault("incomplete_observation_id", "favor_start")
    count = _military_unit_count(observation)
    if count > params["max_units"]:
        return _military_cap_violation(
            params,
            count,
            observation.turn,
            _observation_ref(observation, monitor),
        )
    return _deadline_result(
        observation,
        due_turn,
        terminal_status="satisfied",
        reason=f"the military unit count remained at or below {params['max_units']}",
        monitor=monitor,
    )


def _in_scope_units(
    params: dict, observation: ChannelObservation
) -> tuple[ObservedUnit, ...]:
    return tuple(
        unit
        for unit in observation.units
        if unit.owner_id == observation.player_id
        and (
            params["unit_scope"] == "all"
            or "military" in unit_categories(unit)
        )
    )


def _unit_territory_distances(
    params: dict, observation: ChannelObservation
) -> tuple[int | None, ...]:
    return tuple(
        observation.unit_distances.get(
            (unit.owner_id, unit.unit_id, params["player_id"])
        )
        for unit in _in_scope_units(params, observation)
    )


def _distance_summary(params: dict) -> str:
    return (
        f"player {params['player_id']} for {params['unit_scope']} units at "
        f"a minimum distance of {params['min_distance']} hexes"
    )


def _verify_withdraw_units(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    del baseline
    distances = _unit_territory_distances(params, observation)
    if any(
        distance is not None and distance < params["min_distance"]
        for distance in distances
    ):
        if observation.turn < due_turn:
            return Verification("pending", monitor=monitor)
        return Verification(
            "failed",
            f"the withdrawal from {_distance_summary(params)} was incomplete "
            f"on turn {observation.turn}",
            monitor=monitor,
        )
    if any(distance is None for distance in distances):
        if observation.turn >= due_turn:
            return Verification(
                "unverifiable",
                f"distance data was incomplete for {_distance_summary(params)} "
                "at the deadline",
                monitor=_mark_incomplete(observation, monitor),
            )
        return Verification("pending", monitor=monitor)
    return Verification(
        "satisfied",
        f"the withdrawal from {_distance_summary(params)} was observed "
        f"on turn {observation.turn}",
        monitor=monitor,
    )


def _verify_keep_units_away(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    if observation.turn <= baseline["favor_started_turn"] + 1:
        return _deadline_result(
            observation,
            due_turn,
            terminal_status="satisfied",
            reason=f"the exclusion from {_distance_summary(params)} was maintained",
            monitor=monitor,
        )
    distances = _unit_territory_distances(params, observation)
    if any(
        distance is not None and distance < params["min_distance"]
        for distance in distances
    ):
        return _violation(
            f"the exclusion from {_distance_summary(params)} was violated "
            f"on turn {observation.turn}",
            observation,
            monitor,
        )
    if any(distance is None for distance in distances):
        monitor = _mark_incomplete(observation, monitor)
    return _deadline_result(
        observation,
        due_turn,
        terminal_status="satisfied",
        reason=f"the exclusion from {_distance_summary(params)} was maintained",
        monitor=monitor,
    )


def _baseline_route_ids(
    baseline: dict,
) -> frozenset[tuple[int, int, int, bool]]:
    return frozenset(tuple(identity) for identity in baseline.get("route_ids", ()))


def _baseline_action_ids(
    baseline: dict,
) -> frozenset[tuple[int, int, str, int]]:
    return frozenset(tuple(identity) for identity in baseline.get("action_ids", ()))


def _new_prohibited_route(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
) -> bool:
    if "active_baseline_route_ids" in monitor:
        existing = frozenset(
            tuple(identity)
            for identity in monitor["active_baseline_route_ids"]
        )
    else:
        existing = _baseline_route_ids(baseline)
    return any(
        route.owner_id == observation.player_id
        and route.destination_player == params["target_player"]
        and _route_identity(route) not in existing
        for route in observation.trade_routes
    )


def _new_prohibited_action(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
) -> bool:
    del monitor
    existing = _baseline_action_ids(baseline)
    for action in observation.action_audit:
        identity = _action_identity(action)
        authoritative_after_incomplete_start = (
            not baseline.get("audit_baseline_complete", False)
            and action.turn > baseline["favor_started_turn"]
        )
        if (
            action.actor_id == observation.player_id
            and identity is not None
            and identity[-1] == params["target_player"]
            and identity not in existing
            and _observed_action_is_successful(action)
            and (
                baseline.get("audit_baseline_complete", False)
                or authoritative_after_incomplete_start
            )
        ):
            return True
    return False


def _verify_dont_trade(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    kinds = set(params["trade_kinds"])
    checks = (
        (
            "diplomatic_deal",
            "audit_baseline_complete",
            ObservationFamily.ACTION_AUDIT,
            _new_prohibited_action,
        ),
        (
            "trade_route",
            "route_baseline_complete",
            ObservationFamily.TRADE_ROUTES,
            _new_prohibited_route,
        ),
    )
    for kind, baseline_key, family, violation_check in checks:
        if (
            kind in kinds
            and (
                baseline.get(baseline_key, False)
                or kind == "diplomatic_deal"
            )
            and family in observation.families_present
            and violation_check(params, baseline, monitor, observation)
        ):
            kind_label = kind.replace("_", " ")
            return _violation(
                f"a {kind_label} with player {params['target_player']} "
                f"was observed on turn {observation.turn}",
                observation,
                monitor,
            )

    updated = dict(monitor)
    if (
        "trade_route" in kinds
        and baseline.get("route_baseline_complete", False)
        and ObservationFamily.TRADE_ROUTES in observation.families_present
    ):
        if "active_baseline_route_ids" in monitor:
            exempt_routes = frozenset(
                tuple(identity)
                for identity in monitor["active_baseline_route_ids"]
            )
        else:
            exempt_routes = _baseline_route_ids(baseline)
        current_routes = frozenset(
            _route_identity(route) for route in observation.trade_routes
        )
        updated["active_baseline_route_ids"] = tuple(
            sorted(exempt_routes & current_routes)
        )
    for kind, baseline_key, family, _ in checks:
        if kind not in kinds:
            continue
        if not baseline.get(baseline_key, False):
            updated.setdefault("incomplete_observation_id", "favor_start")
        elif family not in observation.families_present:
            updated = _mark_incomplete(observation, updated)
    return _deadline_result(
        observation,
        due_turn,
        terminal_status="satisfied",
        reason=f"no prohibited trade with player {params['target_player']} was observed",
        monitor=updated,
    )


def _verify_send_trade_route(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    del baseline
    if observation.turn <= due_turn and any(
        route.owner_id == observation.player_id
        and route.destination_player == params["target_player"]
        for route in observation.trade_routes
    ):
        return Verification(
            "satisfied",
            f"a trade route to player {params['target_player']} was active "
            f"on turn {observation.turn}",
            monitor=monitor,
        )
    return _deadline_result(
        observation,
        due_turn,
        terminal_status="failed",
        reason=f"no trade route to player {params['target_player']} was active by the deadline",
        monitor=monitor,
    )


def _render_evidence(verification: Verification) -> str:
    return verification.reason


TERM_REGISTRY: dict[str, TermSpec] = {
    "destroy_camp": TermSpec(
        "destroy_camp",
        TermMode.ENDPOINT,
        "proposal",
        frozenset({ObservationFamily.CAMPS}),
        _validate_coordinates,
        _capture_destroy_camp,
        _verify_destroy_camp,
        _render_evidence,
    ),
    "dont_settle_within": TermSpec(
        "dont_settle_within",
        TermMode.CONTINUOUS,
        "favor_start",
        frozenset({ObservationFamily.CITIES}),
        _validate_zone,
        _capture_city_identities,
        _verify_dont_settle,
        _render_evidence,
    ),
    "found_city_within": TermSpec(
        "found_city_within",
        TermMode.ENDPOINT,
        "favor_start",
        frozenset({ObservationFamily.CITIES}),
        _validate_zone,
        _capture_city_identities,
        _verify_found_city,
        _render_evidence,
    ),
    "declare_war_on": TermSpec(
        "declare_war_on",
        TermMode.ENDPOINT,
        "favor_start",
        frozenset({ObservationFamily.DIPLOMACY}),
        _validate_player,
        _capture_empty,
        _verify_declare_war,
        _render_evidence,
    ),
    "keep_peace_with": TermSpec(
        "keep_peace_with",
        TermMode.CONTINUOUS,
        "favor_start",
        frozenset({ObservationFamily.DIPLOMACY}),
        _validate_player,
        _capture_peace,
        _verify_keep_peace,
        _render_evidence,
    ),
    "maintain_gold_reserve": TermSpec(
        "maintain_gold_reserve",
        TermMode.CONTINUOUS,
        "favor_start",
        frozenset({ObservationFamily.TREASURY}),
        _validate_gold,
        _capture_gold,
        _verify_gold,
        _render_evidence,
    ),
    "forbid_unit_acquisition": TermSpec(
        "forbid_unit_acquisition",
        TermMode.CONTINUOUS,
        "favor_start",
        frozenset({ObservationFamily.UNITS}),
        _validate_unit_category,
        _capture_unit_identities,
        _verify_forbid_unit_acquisition,
        _render_evidence,
    ),
    "allow_only_unit_category": TermSpec(
        "allow_only_unit_category",
        TermMode.CONTINUOUS,
        "favor_start",
        frozenset({ObservationFamily.UNITS}),
        _validate_unit_category,
        _capture_unit_identities,
        _verify_allow_only_unit_category,
        _render_evidence,
    ),
    "military_unit_cap": TermSpec(
        "military_unit_cap",
        TermMode.CONTINUOUS,
        "favor_start",
        frozenset({ObservationFamily.UNITS}),
        _validate_military_unit_cap,
        _capture_military_unit_cap,
        _verify_military_unit_cap,
        _render_evidence,
    ),
    "withdraw_units_from": TermSpec(
        "withdraw_units_from",
        TermMode.ENDPOINT,
        "favor_start",
        frozenset({ObservationFamily.UNITS}),
        _validate_unit_distance,
        _capture_empty,
        _verify_withdraw_units,
        _render_evidence,
    ),
    "keep_units_away": TermSpec(
        "keep_units_away",
        TermMode.CONTINUOUS,
        "favor_start",
        frozenset({ObservationFamily.UNITS}),
        _validate_unit_distance,
        _capture_empty,
        _verify_keep_units_away,
        _render_evidence,
    ),
    "dont_trade_with": TermSpec(
        "dont_trade_with",
        TermMode.CONTINUOUS,
        "favor_start",
        frozenset(
            {ObservationFamily.TRADE_ROUTES, ObservationFamily.ACTION_AUDIT}
        ),
        _validate_dont_trade,
        _capture_trade_identities,
        _verify_dont_trade,
        _render_evidence,
    ),
    "send_trade_route_to": TermSpec(
        "send_trade_route_to",
        TermMode.ENDPOINT,
        "favor_start",
        frozenset({ObservationFamily.TRADE_ROUTES}),
        _validate_trade_route_target,
        _capture_empty,
        _verify_send_trade_route,
        _render_evidence,
    ),
}


def _term_parts(term: object) -> tuple[TermSpec, dict]:
    envelope = _require_exact_fields(
        term, "term", frozenset({"term_type", "params"})
    )
    term_type = envelope["term_type"]
    if not isinstance(term_type, str):
        raise ValueError("term_type must be a string")
    if term_type == "narrative":
        raise ValueError("narrative terms require an active game master")
    spec = TERM_REGISTRY.get(term_type)
    if spec is None:
        raise ValueError(f"unknown favor term {term_type!r}")
    params = envelope["params"]
    if not isinstance(params, dict):
        raise ValueError("term params must be an object")
    return spec, params


def capture_baseline(term: dict, observation: ChannelObservation) -> dict:
    spec, params = _term_parts(term)
    return spec.capture_baseline(params, observation)


def validate_term(term: dict, context: TermValidationContext) -> dict:
    spec, params = _term_parts(term)
    return {"term_type": spec.term_type, "params": spec.validate_params(params, context)}


def _required_families(
    spec: TermSpec, params: dict
) -> frozenset[ObservationFamily]:
    if spec.term_type != "dont_trade_with":
        return spec.families
    families: set[ObservationFamily] = set()
    kinds = set(params["trade_kinds"])
    if "diplomatic_deal" in kinds:
        families.add(ObservationFamily.ACTION_AUDIT)
    if "trade_route" in kinds:
        families.add(ObservationFamily.TRADE_ROUTES)
    return frozenset(families)


def verify_term(
    term: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    spec, params = _term_parts(term)
    persistent = _persistent_violation(spec.term_type, monitor)
    if persistent is not None:
        return persistent
    if baseline.get("initial_violation_turn") is not None:
        return spec.verify(params, baseline, monitor, observation, due_turn)
    if spec.term_type == "dont_trade_with":
        return spec.verify(params, baseline, monitor, observation, due_turn)
    if (
        spec.term_type == "keep_units_away"
        and observation.turn <= baseline["favor_started_turn"] + 1
    ):
        return spec.verify(params, baseline, monitor, observation, due_turn)
    required_families = _required_families(spec, params)
    if not required_families.issubset(observation.families_present):
        monitor = _mark_incomplete(observation, monitor)
        if observation.turn >= due_turn:
            missing = sorted(
                family.value
                for family in required_families - observation.families_present
            )
            return Verification(
                "unverifiable",
                f"missing required observation families: {', '.join(missing)}",
                monitor=monitor,
            )
        return Verification("pending", monitor=monitor)
    return spec.verify(params, baseline, monitor, observation, due_turn)


def compile_observation_request(terms: Iterable[dict] | dict) -> ObservationRequest:
    if isinstance(terms, dict):
        terms = (terms,)
    families: set[ObservationFamily] = set()
    protected_players: set[int] = set()
    zone_centers: set[tuple[int, int]] = set()
    for term in terms:
        spec, params = _term_parts(term)
        families.update(_required_families(spec, params))
        if spec.term_type in {"dont_settle_within", "found_city_within"}:
            zone_centers.add((params["x"], params["y"]))
        if spec.term_type in {"withdraw_units_from", "keep_units_away"}:
            protected_players.add(params["player_id"])
    return ObservationRequest(
        families=frozenset(families),
        protected_players=tuple(sorted(protected_players)),
        zone_centers=tuple(sorted(zone_centers)),
    )


def unit_categories(unit: ObservedUnit) -> frozenset[str]:
    values: set[str] = set()
    if unit.formation_class in MILITARY_FORMATIONS:
        values.add("military")
    if unit.formation_class == "FORMATION_CLASS_CIVILIAN":
        values.add("civilian")
        if unit.religious_strength > 0:
            values.add("religious")
    return frozenset(values)


__all__ = [
    "ChannelObservation",
    "ObservationFamily",
    "ObservationRequest",
    "ObservedAction",
    "ObservedCity",
    "ObservedRoute",
    "ObservedUnit",
    "TERM_REGISTRY",
    "TermMode",
    "TermSpec",
    "TermValidationContext",
    "Verification",
    "capture_baseline",
    "compile_observation_request",
    "normalize_action_audit",
    "unit_categories",
    "validate_term",
    "verify_term",
]
