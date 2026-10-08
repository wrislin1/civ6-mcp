"""Finite endpoint/event predicate vocabulary over canonical v2 benchmark states.

There is no expression evaluator: every predicate is one of the `_SPECS` kinds
below with strictly validated literal arguments. `evaluate_predicate`
validates the whole tree (every `all`/`any` branch) before evaluating any of
it, so an invalid branch can never hide behind short-circuiting.

Missing required facts raise `BenchmarkStateError`; absence of evidence is
never read as False/safety. Civilian safety uses exactly one geometry
(`civilian_covered` / `civilian_exposed`) built on the established offset-grid
`_hex_distance`; there is no distance-credit predicate.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from civ_mcp.arena.action_metrics import _hex_distance
from civ_mcp.arena.benchmark_lifecycle import classify_lifecycle, complete_rows, player_id
from civ_mcp.arena.benchmark_state import BenchmarkStateError

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_TARGET_STATUSES = frozenset({"alive_visible", "alive_not_visible", "destroyed"})


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_num(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _check_pair(value: Any, name: str) -> None:
    if not (isinstance(value, list) and len(value) == 2 and all(map(_is_int, value))):
        raise ValueError(f"{name} must be an [int, int] pair, got {value!r}")


def _literal_set(check: Callable[[Any], bool], what: str) -> Callable[[Any, str], None]:
    def validate(value: Any, name: str) -> None:
        if not isinstance(value, list) or not value:
            raise ValueError(f"{name} must be a nonempty list of {what}")
        for item in value:
            if not check(item):
                raise ValueError(f"{name} entries must be {what}, got {item!r}")
    return validate


_ints = _literal_set(_is_int, "integers")
_names = _literal_set(_is_name, "nonempty strings")


def _tiles(value: Any, name: str) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a nonempty list of [x, y] pairs")
    for item in value:
        _check_pair(item, name)


def _bool(value: Any, name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a bool")


def _finite(value: Any, name: str) -> None:
    if not _is_num(value):
        raise ValueError(f"{name} must be a finite number")


def _positive(value: Any, name: str) -> None:
    _finite(value, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive")


_FIELD_CHECKS: dict[str, Callable[[Any], bool]] = {
    "owner": _is_int,
    "resource": _is_name,
    "improvement": _is_name,
    "pillaged": lambda v: isinstance(v, bool),
}


def _fields(value: Any, name: str) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{name} must be a nonempty dict")
    for key, want in value.items():
        if key == "food":
            if isinstance(want, dict):
                if not want or set(want) - {"min", "max"} or not all(map(_is_num, want.values())):
                    raise ValueError(f"{name}.food bounds must be finite min/max numbers")
            elif not _is_num(want):
                raise ValueError(f"{name}.food must be a finite number or bounds")
        elif key in _FIELD_CHECKS:
            if not _FIELD_CHECKS[key](want):
                raise ValueError(f"{name}.{key} has invalid value {want!r}")
        else:
            raise ValueError(f"{name} has unsupported field {key!r}")


def _children(value: Any, name: str) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a nonempty list of predicates")
    for child in value:
        validate_predicate(child)


_Spec = tuple[dict[str, Callable[[Any, str], None]], dict[str, Callable[[Any, str], None]]]

_SPECS: dict[str, _Spec] = {
    "all": ({"predicates": _children}, {}),
    "any": ({"predicates": _children}, {}),
    "tile_matches": ({"tiles": _tiles, "fields": _fields}, {}),
    "charged_builder_at": ({"tiles": _tiles}, {}),
    "active_production": ({"cities": _ints, "items": _names, "repair": _bool},
                          {"tiles": _tiles}),
    "housing_resolved": ({"cities": _ints, "remedy_buildings": _names,
                          "minimum_surplus": _finite}, {}),
    "district_committed": ({"cities": _ints, "district_types": _names, "tiles": _tiles}, {}),
    "target_damaged": ({"target": _check_pair, "minimum_damage": _positive}, {}),
    "target_neutralised": ({"target": _check_pair}, {}),
    "civilian_covered": ({"unit": _check_pair, "tiles": _tiles}, {}),
    "unit_in_area": ({"unit_types": _names, "tiles": _tiles}, {}),
    "unit_lost": ({"unit": _check_pair}, {}),
    "asset_displaced": ({"tiles": _tiles, "asset_fields": _fields}, {}),
    "new_civilian_exposure": ({"unit": _check_pair}, {}),
}


def validate_predicate(predicate: dict[str, Any]) -> None:
    """Raise `ValueError` unless `predicate` (and every child) is well formed."""
    if not isinstance(predicate, dict):
        raise ValueError(f"predicate must be a dict, got {type(predicate).__name__}")
    kind = predicate.get("kind")
    if kind not in _SPECS:
        raise ValueError(f"unknown predicate kind {kind!r}")
    required, optional = _SPECS[kind]
    args = set(predicate) - {"kind"}
    if missing := set(required) - args:
        raise ValueError(f"{kind} missing arguments {sorted(missing)}")
    if extra := args - set(required) - set(optional):
        raise ValueError(f"{kind} has unknown arguments {sorted(extra)}")
    for key in args:
        (required.get(key) or optional[key])(predicate[key], f"{kind}.{key}")


# ---------------------------------------------------------------------------
# Shared fact lookups
# ---------------------------------------------------------------------------

def _xy(row: dict[str, Any]) -> tuple[Any, Any]:
    return row.get("x"), row.get("y")


def _tile_set(pairs: list[list[int]]) -> set[tuple[int, int]]:
    return {(x, y) for x, y in pairs}


def _unit(state: dict[str, Any], ref: tuple[int, int]) -> dict[str, Any] | None:
    for row in complete_rows(state, "units"):
        if (row.get("owner"), row.get("id")) == ref:
            return row
    return None


def _target(state: dict[str, Any], ref: tuple[int, int]) -> dict[str, Any]:
    for row in complete_rows(state, "targets"):
        if (row.get("owner"), row.get("id")) == ref:
            if row.get("status") not in _TARGET_STATUSES:
                raise BenchmarkStateError(f"target {list(ref)} has unknown status")
            return row
    raise BenchmarkStateError(f"tracked target {list(ref)} has no row")


def _tile(state: dict[str, Any], xy: tuple[int, int]) -> dict[str, Any]:
    for row in complete_rows(state, "tiles"):
        if _xy(row) == xy:
            return row
    raise BenchmarkStateError(f"tile {list(xy)} was not captured")


def _fact(row: dict[str, Any], key: str, where: str) -> Any:
    value = row.get(key)
    if value is None:
        raise BenchmarkStateError(f"{where} lacks {key}")
    return value


def _owned_cities(state: dict[str, Any], ids: list[int]) -> list[dict[str, Any]]:
    pid = player_id(state)
    wanted = set(ids)
    return [c for c in complete_rows(state, "cities")
            if c.get("owner") == pid and c.get("id") in wanted]


# ---------------------------------------------------------------------------
# The one civilian cover/exposure geometry
# ---------------------------------------------------------------------------

def _living_civilian(state: dict[str, Any], ref: tuple[int, int]) -> dict[str, Any]:
    unit = _unit(state, tuple(ref))
    if unit is None:
        raise BenchmarkStateError(f"civilian {list(ref)} is not in the capture")
    role = _fact(unit, "role", f"unit {list(ref)}")
    hp = _fact(unit, "hp", f"unit {list(ref)}")
    if role != "civilian" or hp <= 0:
        raise BenchmarkStateError(f"unit {list(ref)} is not a living civilian")
    return unit


def civilian_covered(state: dict[str, Any], ref: tuple[int, int]) -> bool:
    """Owned-city occupancy, or an owned combat unit at hex distance <= 1."""
    unit = _living_civilian(state, ref)
    pid, here = player_id(state), _xy(unit)
    if any(c.get("owner") == pid and _xy(c) == here for c in complete_rows(state, "cities")):
        return True
    for other in complete_rows(state, "units"):
        if other is unit or other.get("owner") != pid:
            continue
        if _hex_distance(here, _xy(other)) <= 1:
            if _fact(other, "role", f"unit {[other.get('owner'), other.get('id')]}") == "combat":
                return True
    return False


def civilian_exposed(state: dict[str, Any], ref: tuple[int, int]) -> bool:
    """Uncovered, and a visible hostile combat target is exactly adjacent."""
    unit = _living_civilian(state, ref)
    if civilian_covered(state, ref):
        return False
    here = _xy(unit)
    for target in complete_rows(state, "targets"):
        where = f"target {[target.get('owner'), target.get('id')]}"
        status = target.get("status")
        if status not in _TARGET_STATUSES:
            raise BenchmarkStateError(f"{where} has unknown status")
        if status != "alive_visible" or target.get("visible") is False:
            continue
        x, y = _fact(target, "x", where), _fact(target, "y", where)
        if _hex_distance(here, (x, y)) != 1:
            continue
        visible = _fact(target, "visible", where)
        hostile = _fact(target, "hostile", where)
        role = _fact(target, "role", where)
        if visible is True and hostile is True and role == "combat":
            return True
    return False


def require_living_civilian(state: dict[str, Any], ref: tuple[int, int]) -> bool:
    unit = _unit(state, tuple(ref))
    if unit is None:
        return False
    role = _fact(unit, "role", f"unit {list(ref)}")
    hp = _fact(unit, "hp", f"unit {list(ref)}")
    return role == "civilian" and hp > 0


def newly_exposed(initial, final, ref):
    # Complete own-unit evidence establishes whether the civilian still exists;
    # the cause of absence is handled separately by lifecycle classification.
    if not require_living_civilian(final, ref):
        return False
    return not civilian_exposed(initial, ref) and civilian_exposed(final, ref)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _fields_match(tile: dict[str, Any], fields: dict[str, Any]) -> bool:
    where = f"tile {list(_xy(tile))}"
    for key, want in fields.items():
        if key == "food":
            yields = _fact(tile, "yields", where)
            got = _fact(yields, "food", where)
            if isinstance(want, dict):
                if "min" in want and got < want["min"]:
                    return False
                if "max" in want and got > want["max"]:
                    return False
            elif got != want:
                return False
        elif _fact(tile, key, where) != want:
            return False
    return True


def _transition(transition: dict[str, Any] | None, kind: str) -> dict[str, Any]:
    if not isinstance(transition, dict):
        raise BenchmarkStateError(f"{kind} requires a recorded transition")
    return transition


def _eval(p: dict[str, Any], initial: dict[str, Any], final: dict[str, Any],
          transition: dict[str, Any] | None) -> bool:
    kind = p["kind"]
    if kind == "all":
        return all(_eval(c, initial, final, transition) for c in p["predicates"])
    if kind == "any":
        return any(_eval(c, initial, final, transition) for c in p["predicates"])

    if kind == "tile_matches":
        tiles = [_tile(final, xy) for xy in sorted(_tile_set(p["tiles"]))]
        return any(_fields_match(t, p["fields"]) for t in tiles)

    if kind == "charged_builder_at":
        pid, tiles = player_id(final), _tile_set(p["tiles"])
        for u in complete_rows(final, "units"):
            if u.get("owner") != pid or u.get("type") != "UNIT_BUILDER" or _xy(u) not in tiles:
                continue
            where = f"unit {[u.get('owner'), u.get('id')]}"
            role, hp = _fact(u, "role", where), _fact(u, "hp", where)
            charges = _fact(u, "charges", where)
            if (role == "civilian" and hp > 0 and charges > 0
                    and _fact(_tile(final, _xy(u)), "owner", "tile") == pid):
                return True
        return False

    if kind == "active_production":
        tiles = _tile_set(p["tiles"]) if "tiles" in p else None
        for c in _owned_cities(final, p["cities"]):
            q = _fact(c, "queue", f"city {c.get('id')}")
            if (q.get("item_type") in p["items"]
                    and _fact(q, "repair", f"city {c.get('id')} queue") == p["repair"]
                    and (tiles is None or (q.get("target_x"), q.get("target_y")) in tiles)):
                return True
        return False

    if kind == "housing_resolved":
        for c in _owned_cities(final, p["cities"]):
            where = f"city {c.get('id')}"
            surplus = _fact(c, "housing", where) - _fact(c, "population", where)
            remedied = any(b.get("building_type") in p["remedy_buildings"]
                           and b.get("present") is True and b.get("pillaged") is False
                           for b in _fact(c, "buildings", where))
            if remedied and surplus >= p["minimum_surplus"]:
                return True
        return False

    if kind == "district_committed":
        tiles, types = _tile_set(p["tiles"]), set(p["district_types"])
        for c in _owned_cities(final, p["cities"]):
            where = f"city {c.get('id')}"
            q = _fact(c, "queue", where)
            target = (q.get("target_x"), q.get("target_y"))
            if q.get("item_kind") != "DISTRICT" or q.get("item_type") not in types \
                    or target not in tiles:
                continue
            if any(d.get("district_type") == q["item_type"] and _xy(d) == target
                   for d in _fact(c, "districts", where)):
                return True
        return False

    if kind == "target_damaged":
        ref = tuple(p["target"])
        before, after = _target(initial, ref), _target(final, ref)
        if before["status"] != "alive_visible" or after["status"] != "alive_visible":
            return False
        where = f"target {list(ref)}"
        return _fact(before, "hp", where) - _fact(after, "hp", where) >= p["minimum_damage"]

    if kind == "target_neutralised":
        ref = tuple(p["target"])
        before, after = _target(initial, ref), _target(final, ref)
        return before["status"] == "alive_visible" and after["status"] == "destroyed"

    if kind == "civilian_covered":
        ref = tuple(p["unit"])
        if not require_living_civilian(final, ref):
            return False
        start = _unit(initial, ref)
        if start is None:
            raise BenchmarkStateError(f"civilian {list(ref)} has no initial position")
        unit = _unit(final, ref)
        # Rescue credit needs relocation: staying put on an accepted tile earns nothing.
        return (_xy(unit) != _xy(start) and _xy(unit) in _tile_set(p["tiles"])
                and civilian_covered(final, ref))

    if kind == "unit_in_area":
        pid, tiles = player_id(final), _tile_set(p["tiles"])
        return any(u.get("owner") == pid and u.get("type") in p["unit_types"]
                   and _xy(u) in tiles for u in complete_rows(final, "units"))

    if kind == "unit_lost":
        step = _transition(transition, kind)
        for record in classify_lifecycle(step):
            if record["entity"] == list(p["unit"]):
                if record["status"] == "unresolved":
                    raise BenchmarkStateError(
                        f"unit {p['unit']} disappeared with unresolved lifecycle")
                return record["status"] == "lost"
        return False

    if kind == "asset_displaced":
        step = _transition(transition, kind)
        before, after = step.get("state_before"), step.get("state_after")
        for xy in sorted(_tile_set(p["tiles"])):
            had = _fields_match(_tile(before, xy), p["asset_fields"])
            has = _fields_match(_tile(after, xy), p["asset_fields"])
            if had and not has:
                return True
        return False

    # new_civilian_exposure
    return newly_exposed(initial, final, tuple(p["unit"]))


def evaluate_predicate(predicate: dict[str, Any], *, initial: dict[str, Any],
                       final: dict[str, Any],
                       transition: dict[str, Any] | None = None) -> bool:
    """Validate the whole predicate tree, then evaluate it over the endpoints."""
    validate_predicate(predicate)
    return _eval(predicate, initial, final, transition)
