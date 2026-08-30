"""Pure-Python canonicalizer for benchmark-captured game state.

`civ_mcp.lua.benchmark.parse_benchmark_state` returns whatever order the
game's ``Members()`` iteration happens to hand back for units/cities, and
the manifest's declared tile list in whatever order the manifest author
wrote it. None of that order is meaningful — two captures of the identical
game state must hash identically regardless of iteration order, while any
actual field-level change (including on a single manifest-declared tile)
must change the hash. That's the whole job of this module:

- `normalize_state` sorts the three declared-list fields (units/cities by
  id, tiles by (x, y)) so iteration order can never leak into a digest.
- `state_digest` hashes the normalized, canonical-JSON-encoded state.
- `diff_state` reports a path -> [old, new] mapping between two captures,
  matching list rows by id/(x, y) rather than position.
- `capture_canonical_state` ties a live FireTuner connection to the two
  above: query, parse, normalize.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable, Mapping, Sequence

from civ_mcp import lua as lq
from civ_mcp.connection import GameConnection

# List-valued fields whose row order is not meaningful, and how to key a row
# for order-independent comparison.
_LIST_KEYS: dict[str, Callable[[Mapping[str, object]], object]] = {
    "units": lambda row: row.get("id"),
    "cities": lambda row: row.get("id"),
    "tiles": lambda row: (row.get("x"), row.get("y")),
}


def _sorted_rows(
    rows: Sequence[Mapping[str, object]], key: Callable[[Mapping[str, object]], object]
) -> list[dict[str, object]]:
    return sorted((dict(row) for row in rows), key=key)


def normalize_state(state: Mapping[str, object]) -> dict[str, object]:
    """Copy `state` with units/cities sorted by id and tiles sorted by (x, y).

    Every other key is passed through untouched — normalize_state only
    knows about the three declared-list fields whose row order is an
    artifact of iteration, not part of the state itself.
    """
    normalized = dict(state)
    for key, key_fn in _LIST_KEYS.items():
        rows = normalized.get(key)
        if rows is not None:
            normalized[key] = _sorted_rows(rows, key=key_fn)
    return normalized


def state_digest(state: Mapping[str, object]) -> str:
    normalized = normalize_state(state)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row_label(list_key: str, row_id: object) -> str:
    if list_key == "tiles":
        x, y = row_id  # type: ignore[misc]
        return f"{list_key}[{x},{y}]"
    return f"{list_key}[{row_id}]"


def diff_state(
    left: Mapping[str, object], right: Mapping[str, object]
) -> dict[str, list[object]]:
    """Path -> [old, new] diff between two (not-yet-normalized) states.

    Declared-list rows are matched by id (units/cities) or (x, y) (tiles),
    never by position, so reordering never produces a spurious diff entry.
    A row present on only one side is reported as `[row, None]` or
    `[None, row]` under its own label (no per-field breakdown, since there
    is nothing on the other side to compare a field against).
    """
    left_n = normalize_state(left)
    right_n = normalize_state(right)
    diffs: dict[str, list[object]] = {}

    for key in sorted(set(left_n) | set(right_n)):
        if key in _LIST_KEYS:
            key_fn = _LIST_KEYS[key]
            left_rows = {key_fn(row): row for row in (left_n.get(key) or [])}
            right_rows = {key_fn(row): row for row in (right_n.get(key) or [])}
            for row_id in sorted(set(left_rows) | set(right_rows), key=str):
                label = _row_label(key, row_id)
                lrow, rrow = left_rows.get(row_id), right_rows.get(row_id)
                if lrow is None or rrow is None:
                    diffs[label] = [lrow, rrow]
                    continue
                for field in sorted(set(lrow) | set(rrow)):
                    lv, rv = lrow.get(field), rrow.get(field)
                    if lv != rv:
                        diffs[f"{label}.{field}"] = [lv, rv]
        else:
            lv, rv = left_n.get(key), right_n.get(key)
            if lv != rv:
                diffs[key] = [lv, rv]

    return diffs


async def capture_canonical_state(
    connection: GameConnection,
    player_id: int,
    tile_coords: Sequence[tuple[int, int]],
) -> dict[str, object]:
    """Query the live game for `player_id`'s state and return it normalized.

    Sends `build_benchmark_state_query`, parses the response, and sorts the
    declared-list fields before returning — the result is ready to feed
    straight to `state_digest` / `diff_state`.
    """
    query = lq.build_benchmark_state_query(player_id, tile_coords)
    lines = await connection.execute_read(query)
    state = lq.parse_benchmark_state(lines)
    return normalize_state(state)
