"""Version-2 benchmark state: strict wire parser, canonical form and digest.

`civ_mcp.lua.benchmark_v2.build_benchmark_state_query_v2` emits one
GameCore program whose output is one UTF-8 record per line, `|`-separated,
in the families and field orders of `_FAMILIES` below (the Plan 3 Part 1
Task 2 grammar). This module turns that output into a canonical state:

- `parse_state_v2` validates framing (one BEGIN/IDENTITY/END, family order,
  nothing after END), every field's syntax, keys, city references, target
  visibility semantics and coverage, then cross-checks the counts it built
  from successfully parsed rows against the END record (`require_complete`).
  A truncated or partially failed capture is an error, never a smaller world.
- `normalize_state_v2` sorts every semantic set and canonicalises integral
  numbers so iteration order and `100` versus `100.0` never reach a digest.
- `digest_state_v2` hashes the normalised state with Task 1's canonical bytes.

Timing and transport data never enter the state. Version-1 parsing
(`civ_mcp.lua.benchmark`, `benchmark_state`) is untouched.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any

from civ_mcp.arena.benchmark_contract_v2 import document_digest
from civ_mcp.arena.benchmark_state import BenchmarkStateError, _canonicalize_numerics

WIRE_VERSION = "2.0.0"

# Wire family -> ((field, kind, optional), ...). Kinds: i integer, n finite
# number, b 0/1, s escaped string. Order is the wire order of fields.
_FAMILIES: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "IDENTITY": (
        ("civ_type", "s", False), ("seed", "i", False), ("turn", "i", False),
        ("active_player", "i", False), ("player_id", "i", False),
        ("gold", "n", False), ("faith", "n", False),
    ),
    "UNIT": (
        ("owner", "i", False), ("id", "i", False), ("unit_index", "i", False),
        ("unit_type", "s", False), ("role", "s", False), ("x", "i", False),
        ("y", "i", False), ("hp", "n", False), ("max_hp", "n", False),
        ("moves", "n", False), ("charges", "i", False),
    ),
    "TARGET": (
        ("owner", "i", False), ("id", "i", False), ("tracked", "b", False),
        ("role", "s", True), ("hostile", "b", False), ("visible", "b", False),
        ("status", "s", False), ("x", "i", True), ("y", "i", True),
        ("hp", "n", True), ("max_hp", "n", True),
    ),
    "CITY": (
        ("owner", "i", False), ("id", "i", False), ("name", "s", False),
        ("x", "i", False), ("y", "i", False), ("population", "i", False),
        ("housing", "n", False),
    ),
    "BUILDING": (
        ("owner", "i", False), ("city_id", "i", False),
        ("building_type", "s", False), ("present", "b", False),
        ("pillaged", "b", False),
    ),
    "DISTRICT": (
        ("owner", "i", False), ("city_id", "i", False), ("district_id", "i", False),
        ("district_type", "s", False), ("x", "i", False), ("y", "i", False),
        ("complete", "b", False), ("pillaged", "b", False),
    ),
    "QUEUE": (
        ("owner", "i", False), ("city_id", "i", False), ("item_kind", "s", False),
        ("item_type", "s", False), ("repair", "b", False),
        ("target_x", "i", True), ("target_y", "i", True),
    ),
    "TILE": (
        ("x", "i", False), ("y", "i", False), ("owner", "i", False),
        ("terrain", "s", False), ("feature", "s", False), ("resource", "s", False),
        ("improvement", "s", False), ("pillaged", "b", False),
        ("district", "s", False), ("visible", "b", False),
        ("food", "n", False), ("production", "n", False), ("gold", "n", False),
        ("science", "n", False), ("culture", "n", False), ("faith", "n", False),
    ),
    "RESOURCE": (
        ("resource_type", "s", False), ("access", "b", False),
        ("stock", "n", True), ("flow", "n", True),
    ),
}
_ORDER = tuple(_FAMILIES)  # IDENTITY .. RESOURCE; BEGIN precedes, END follows
_COUNT_KEYS = tuple(family.lower() for family in _ORDER)
_YIELDS = ("food", "production", "gold", "science", "culture", "faith")
_TARGET_STATUSES = frozenset({"alive_visible", "alive_not_visible", "destroyed"})
_COVERAGE_KEYS = frozenset({"include_owned_tiles", "area", "tracked_targets"})
_ROOT_KEYS = (
    "wire_version", "civ_type", "seed", "turn", "active_player", "player_id",
    "gold", "faith", "units", "targets", "cities", "tiles", "resources",
    "coverage", "row_counts",
)
_CITY_NESTED = ("buildings", "districts", "queue")

_INT_RE = re.compile(r"-?[0-9]+\Z")
_NUM_RE = re.compile(r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_ESCAPES = {"%25": "%", "%7C": "|", "%0D": "\r", "%0A": "\n", "%7E": "~"}
_ESCAPE_RE = re.compile(r"%(?:25|7C|0D|0A|7E)")


def require_complete(actual: dict[str, int], declared: dict[str, int]) -> None:
    from civ_mcp.arena.benchmark_state import BenchmarkStateError
    if actual != declared or declared.get("identity") != 1:
        raise BenchmarkStateError("incomplete v2 capture: row counts disagree")


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def _strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _pairs(value: Any, field: str) -> list[list[int]]:
    if not isinstance(value, (list, tuple)):
        raise BenchmarkStateError(f"coverage {field} must be a list")
    pairs: list[list[int]] = []
    for item in value:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or not all(_strict_int(v) for v in item)):
            raise BenchmarkStateError(f"coverage {field} entries must be integer pairs")
        pair = [item[0], item[1]]
        if pair in pairs:
            raise BenchmarkStateError(f"coverage {field} has duplicate entry {pair}")
        pairs.append(pair)
    return pairs


def validate_coverage(coverage: Any) -> dict[str, Any]:
    """Return `coverage` as plain lists after strict validation.

    Shape: ``{"include_owned_tiles": bool, "area": [[x, y], ...],
    "tracked_targets": [[owner, id], ...]}``; no other keys.
    """
    if not isinstance(coverage, dict):
        raise BenchmarkStateError("coverage must be a dict")
    keys = set(coverage)
    if keys != _COVERAGE_KEYS:
        raise BenchmarkStateError(
            f"coverage keys must be {sorted(_COVERAGE_KEYS)}: "
            f"unknown {sorted(keys - _COVERAGE_KEYS)}, missing {sorted(_COVERAGE_KEYS - keys)}"
        )
    if not isinstance(coverage["include_owned_tiles"], bool):
        raise BenchmarkStateError("coverage include_owned_tiles must be a bool")
    return {
        "include_owned_tiles": coverage["include_owned_tiles"],
        "area": _pairs(coverage["area"], "area"),
        "tracked_targets": _pairs(coverage["tracked_targets"], "tracked_targets"),
    }


# ---------------------------------------------------------------------------
# Field decoding
# ---------------------------------------------------------------------------

def _decode_string(text: str, where: str) -> str:
    if "\r" in text or "~" in text:
        raise BenchmarkStateError(f"{where}: unescaped CR or '~' in string {text!r}")
    for match in re.finditer("%", text):
        if _ESCAPE_RE.match(text, match.start()) is None:
            raise BenchmarkStateError(f"{where}: unknown escape in {text!r}")
    return _ESCAPE_RE.sub(lambda m: _ESCAPES[m.group(0)], text)


def _decode_field(text: str, kind: str, optional: bool, where: str) -> Any:
    if text == "~":
        if not optional:
            raise BenchmarkStateError(f"{where}: unavailable marker in required field")
        return None
    if kind == "i":
        if _INT_RE.match(text) is None:
            raise BenchmarkStateError(f"{where}: expected decimal integer, got {text!r}")
        return int(text)
    if kind == "n":
        if _NUM_RE.match(text) is None:
            raise BenchmarkStateError(f"{where}: expected finite decimal number, got {text!r}")
        value = float(text)
        if not math.isfinite(value):
            raise BenchmarkStateError(f"{where}: expected finite decimal number, got {text!r}")
        return int(value) if value.is_integer() else value
    if kind == "b":
        if text not in ("0", "1"):
            raise BenchmarkStateError(f"{where}: expected boolean 0/1, got {text!r}")
        return text == "1"
    return _decode_string(text, where)


def _decode_row(family: str, parts: list[str], lineno: int) -> dict[str, Any]:
    spec = _FAMILIES[family]
    if len(parts) != len(spec):
        raise BenchmarkStateError(
            f"line {lineno}: {family} has {len(parts)} fields, expected {len(spec)}"
        )
    return {
        name: _decode_field(text, kind, optional, f"line {lineno} {family}.{name}")
        for (name, kind, optional), text in zip(spec, parts)
    }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _check_target(row: dict[str, Any], tracked_set: set[tuple[int, int]]) -> None:
    key = (row["owner"], row["id"])
    status = row["status"]
    where = f"target {list(key)}"
    if status not in _TARGET_STATUSES:
        raise BenchmarkStateError(f"{where}: unknown target status {status!r}")
    located = [row[f] for f in ("x", "y", "hp", "max_hp")]
    if status == "alive_visible":
        if not row["visible"] or row["role"] is None or any(v is None for v in located):
            raise BenchmarkStateError(
                f"{where}: visible target requires visible=1, role, coordinates and health"
            )
    elif row["visible"] or any(v is not None for v in located):
        raise BenchmarkStateError(
            f"{where}: {status} target requires visible=0 and null position/health"
        )
    if row["tracked"] != (key in tracked_set):
        raise BenchmarkStateError(f"{where}: tracked flag disagrees with coverage tracked_targets")
    if not row["tracked"] and not (status == "alive_visible" and row["hostile"]):
        raise BenchmarkStateError(f"{where}: untracked target must be a visible hostile")


def _unique(index: dict[Any, Any], key: Any, value: Any, family: str) -> None:
    if key in index:
        raise BenchmarkStateError(f"duplicate {family} key {key}")
    index[key] = value


def parse_state_v2(raw: str, *, coverage: dict[str, Any]) -> dict[str, Any]:
    """Parse one complete v2 capture into a (not yet normalised) state dict."""
    scope = validate_coverage(coverage)
    lines = raw.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    for line in lines:
        if line.startswith("ERR:"):
            raise BenchmarkStateError(f"v2 capture reported an error: {line}")
    if not lines or not lines[0].startswith("BEGIN|"):
        raise BenchmarkStateError("v2 capture must start with a BEGIN row")
    begin = lines[0].split("|")
    if len(begin) != 3 or begin[1] != WIRE_VERSION:
        raise BenchmarkStateError(f"unsupported BEGIN version/shape: {lines[0]!r}")
    if begin[2] != document_digest(coverage):
        raise BenchmarkStateError("BEGIN coverage digest does not match the requested coverage")

    rows: dict[str, list[dict[str, Any]]] = {family: [] for family in _ORDER}
    declared: dict[str, int] | None = None
    position = -1
    for lineno, line in enumerate(lines[1:], start=2):
        if declared is not None:
            raise BenchmarkStateError(f"line {lineno}: output after END: {line!r}")
        tag, *parts = line.split("|")
        if tag == "END":
            if len(parts) != 1 + len(_COUNT_KEYS) or parts[0] != WIRE_VERSION:
                raise BenchmarkStateError(f"line {lineno}: bad END version/shape: {line!r}")
            declared = {
                key: _decode_field(text, "i", False, f"line {lineno} END.{key}_count")
                for key, text in zip(_COUNT_KEYS, parts[1:])
            }
            continue
        if tag not in _FAMILIES:
            raise BenchmarkStateError(f"line {lineno}: unknown row tag {tag!r}")
        family_position = _ORDER.index(tag)
        if family_position < position:
            raise BenchmarkStateError(f"line {lineno}: {tag} row out of family order")
        position = family_position
        if tag == "IDENTITY" and rows["IDENTITY"]:
            raise BenchmarkStateError(f"line {lineno}: duplicate IDENTITY row")
        rows[tag].append(_decode_row(tag, parts, lineno))

    if declared is None:
        raise BenchmarkStateError("incomplete v2 capture: missing END row")
    require_complete({key: len(rows[f]) for key, f in zip(_COUNT_KEYS, _ORDER)}, declared)

    identity = rows["IDENTITY"][0]
    player_id = identity["player_id"]

    def owned(row: dict[str, Any], family: str) -> None:
        if row["owner"] != player_id:
            raise BenchmarkStateError(
                f"{family} owner {row['owner']} is not the captured player {player_id}"
            )

    units: dict[Any, dict[str, Any]] = {}
    for row in rows["UNIT"]:
        owned(row, "UNIT")
        unit = dict(row)
        unit["type"] = unit.pop("unit_type")
        _unique(units, (row["owner"], row["id"]), unit, "UNIT")

    tracked_set = {tuple(pair) for pair in scope["tracked_targets"]}
    targets: dict[Any, dict[str, Any]] = {}
    for row in rows["TARGET"]:
        _check_target(row, tracked_set)
        _unique(targets, (row["owner"], row["id"]), dict(row), "TARGET")
    missing_targets = tracked_set - set(targets)
    if missing_targets:
        raise BenchmarkStateError(f"tracked targets without a TARGET row: {sorted(missing_targets)}")

    cities: dict[Any, dict[str, Any]] = {}
    for row in rows["CITY"]:
        owned(row, "CITY")
        city = dict(row, buildings=[], districts=[])
        _unique(cities, (row["owner"], row["id"]), city, "CITY")

    def city_of(row: dict[str, Any], family: str) -> dict[str, Any]:
        owned(row, family)
        key = (row["owner"], row["city_id"])
        if key not in cities:
            raise BenchmarkStateError(f"{family} references unknown city {list(key)}")
        return cities[key]

    seen_buildings: dict[Any, None] = {}
    for row in rows["BUILDING"]:
        city = city_of(row, "BUILDING")
        _unique(seen_buildings, (row["owner"], row["city_id"], row["building_type"]),
                None, "BUILDING")
        city["buildings"].append(
            {k: row[k] for k in ("building_type", "present", "pillaged")})

    seen_districts: dict[Any, None] = {}
    for row in rows["DISTRICT"]:
        city = city_of(row, "DISTRICT")
        _unique(seen_districts, (row["owner"], row["district_id"]), None, "DISTRICT")
        city["districts"].append({k: v for k, v in row.items() if k not in ("owner", "city_id")})

    for row in rows["QUEUE"]:
        city = city_of(row, "QUEUE")
        if "queue" in city:
            raise BenchmarkStateError(f"duplicate QUEUE key {[row['owner'], row['city_id']]}")
        city["queue"] = {k: v for k, v in row.items() if k not in ("owner", "city_id")}
    for key, city in cities.items():
        if "queue" not in city:
            raise BenchmarkStateError(f"city {list(key)} has no QUEUE row")

    area = {tuple(pair) for pair in scope["area"]}
    tiles: dict[Any, dict[str, Any]] = {}
    for row in rows["TILE"]:
        key = (row["x"], row["y"])
        in_scope = key in area or (scope["include_owned_tiles"] and row["owner"] == player_id)
        if not in_scope:
            raise BenchmarkStateError(f"TILE {list(key)} is outside the coverage scope")
        tile = {k: v for k, v in row.items() if k not in _YIELDS}
        tile["yields"] = {name: row[name] for name in _YIELDS}
        _unique(tiles, key, tile, "TILE")
    missing_area = area - set(tiles)
    if missing_area:
        raise BenchmarkStateError(f"coverage area tiles without a TILE row: {sorted(missing_area)}")

    resources: dict[Any, dict[str, Any]] = {}
    for row in rows["RESOURCE"]:
        _unique(resources, row["resource_type"], dict(row), "RESOURCE")

    state: dict[str, Any] = {"wire_version": WIRE_VERSION, **identity}
    state.update(
        units=list(units.values()),
        targets=list(targets.values()),
        cities=list(cities.values()),
        tiles=list(tiles.values()),
        resources=list(resources.values()),
        coverage=scope,
        row_counts=declared,
    )
    return state


# ---------------------------------------------------------------------------
# Canonical form and digest
# ---------------------------------------------------------------------------

def _owner_id(row: dict[str, Any]) -> tuple[Any, Any]:
    return (row["owner"], row["id"])


def _xy(row: dict[str, Any]) -> tuple[Any, Any]:
    return (row["x"], row["y"])


def normalize_state_v2(state: dict[str, Any]) -> dict[str, Any]:
    """Sort every semantic set and canonicalise integral numbers (idempotent)."""
    missing = [key for key in _ROOT_KEYS if key not in state]
    if missing:
        raise BenchmarkStateError(f"v2 state is missing essential keys: {missing}")
    out = copy.deepcopy(dict(state))
    for city in out["cities"]:
        absent = [key for key in _CITY_NESTED if key not in city]
        if absent:
            raise BenchmarkStateError(f"v2 city {city.get('id')!r} is missing {absent}")
        city["buildings"] = sorted(city["buildings"], key=lambda b: b["building_type"])
        city["districts"] = sorted(city["districts"], key=lambda d: d["district_id"])
    out["units"] = sorted(out["units"], key=_owner_id)
    out["targets"] = sorted(out["targets"], key=_owner_id)
    out["cities"] = sorted(out["cities"], key=_owner_id)
    out["tiles"] = sorted(out["tiles"], key=_xy)
    out["resources"] = sorted(out["resources"], key=lambda r: r["resource_type"])
    out["coverage"] = dict(
        out["coverage"],
        area=sorted([list(p) for p in out["coverage"]["area"]]),
        tracked_targets=sorted([list(p) for p in out["coverage"]["tracked_targets"]]),
    )
    return _canonicalize_numerics(out)  # type: ignore[return-value]


def digest_state_v2(state: dict[str, Any]) -> str:
    """`document_digest` of the normalised v2 state."""
    return document_digest(normalize_state_v2(state))
