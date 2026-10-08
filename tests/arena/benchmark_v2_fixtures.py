"""Hand-built canonical v2 benchmark states for predicate and scoring tests.

`state_v2` writes the canonical (already normalised) root shape directly. Its
default fields mirror the explicit `EXPECTED` dict in
`tests/arena/test_benchmark_state_v2.py`; it never round-trips the parser and
never invents per-entity fields (health, hostility, visibility, role). Pass
entities in canonical order (units/targets/cities by (owner, id), tiles by
(x, y)) when the result must equal its own normalisation.
"""
from __future__ import annotations

from typing import Any

FIXTURE_CIV_TYPE = "CIVILIZATION_KOREA"
FIXTURE_SEED = 7


def state_v2(*, units=(), cities=(), tiles=(), targets=(), gold=100,
             faith=0) -> dict[str, Any]:
    units, cities, tiles, targets = list(units), list(cities), list(tiles), list(targets)
    return {
        "wire_version": "2.0.0",
        "civ_type": FIXTURE_CIV_TYPE,
        "seed": FIXTURE_SEED,
        "turn": 100,
        "active_player": 0,
        "player_id": 0,
        "gold": gold,
        "faith": faith,
        "units": units,
        "targets": targets,
        "cities": cities,
        "tiles": tiles,
        "resources": [],
        # The scope is exactly the passed entities: every tile is an area tile
        # and every target not explicitly untracked is a tracked target.
        "coverage": {
            "include_owned_tiles": False,
            "area": [[tile["x"], tile["y"]] for tile in tiles],
            "tracked_targets": [[t["owner"], t["id"]] for t in targets
                                if t.get("tracked", True)],
        },
        "row_counts": {
            "identity": 1,
            "unit": len(units),
            "target": len(targets),
            "city": len(cities),
            "building": sum(len(c.get("buildings", ())) for c in cities),
            "district": sum(len(c.get("districts", ())) for c in cities),
            "queue": sum("queue" in c for c in cities),
            "tile": len(tiles),
            "resource": 0,
        },
    }
