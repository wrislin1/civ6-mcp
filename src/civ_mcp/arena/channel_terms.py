"""Typed evidence records for deterministic unofficial-channel terms.

The closed term registry is added in later implementation slices.  This module
starts with the immutable observation vocabulary shared by the Lua boundary and
the pure verifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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


__all__ = [
    "ChannelObservation",
    "ObservationFamily",
    "ObservationRequest",
    "ObservedAction",
    "ObservedCity",
    "ObservedRoute",
    "ObservedUnit",
]
