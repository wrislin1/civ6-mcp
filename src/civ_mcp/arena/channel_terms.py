"""Typed observations and pure verifiers for deterministic channel terms."""

from __future__ import annotations

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


@dataclass(frozen=True)
class ObservedCity:
    owner_id: int
    city_id: int
    x: int
    y: int


@dataclass(frozen=True)
class ObservedRoute:
    owner_id: int
    trader_unit_id: int
    destination_player: int
    destination_is_city_state: bool


@dataclass(frozen=True)
class ObservedAction:
    actor_id: int
    turn: int
    tool_name: str
    tool_args: dict[str, Any]
    tool_result_full: str


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


def _validate_gold(params: dict, _: TermValidationContext) -> dict:
    params = _require_exact_fields(params, "term params", frozenset({"min_gold"}))
    min_gold = _require_integer(params["min_gold"], "min_gold")
    if not 0 <= min_gold <= 10_000:
        raise ValueError("min_gold must be 0..10000")
    return {"min_gold": min_gold}


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
    return Verification(
        "failed",
        f"{term_type} was violated in an earlier observation",
        _evidence_refs(reference),
        {"violation_observation_id": reference},
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
        "city_ids": tuple(
            sorted(
                (city.owner_id, city.city_id)
                for city in observation.cities
                if city.owner_id == observation.player_id
            )
        ),
        "favor_started_turn": observation.turn,
    }


def _capture_empty(params: dict, observation: ChannelObservation) -> dict:
    del params
    return {"favor_started_turn": observation.turn}


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


def _verify_destroy_camp(
    params: dict,
    baseline: dict,
    monitor: dict,
    observation: ChannelObservation,
    due_turn: int,
) -> Verification:
    del baseline
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


def _new_owned_cities(
    baseline: dict, observation: ChannelObservation
) -> tuple[ObservedCity, ...]:
    existing = _baseline_city_ids(baseline)
    return tuple(
        city
        for city in observation.cities
        if city.owner_id == observation.player_id
        and (city.owner_id, city.city_id) not in existing
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
    if not spec.families.issubset(observation.families_present):
        monitor = _mark_incomplete(observation, monitor)
        if observation.turn >= due_turn:
            missing = sorted(
                family.value
                for family in spec.families - observation.families_present
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
        families.update(spec.families)
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
    del unit
    return frozenset()


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
    "unit_categories",
    "validate_term",
    "verify_term",
]
