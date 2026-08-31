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
  above: query, parse, normalize. It checks for an `ERR:`-prefixed line
  before parsing — `parse_benchmark_state` only recognizes IDENTITY/UNIT/
  CITY/TILE prefixes and silently drops anything else, so an unguarded
  caller would hash a near-empty "default" state instead of learning the
  query failed (e.g. a stale/wrong manifest `player_id`). Same anti-pattern
  guard as `GameState.get_district_advisor` in `game_state.py`.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable, Mapping, Sequence

from civ_mcp import lua as lq
from civ_mcp.connection import GameConnection


class BenchmarkStateError(RuntimeError):
    """Raised when the benchmark-state Lua query reports an error line
    (e.g. ``ERR:PLAYER_NOT_FOUND``) instead of returning parseable state."""

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


def _canonicalize_numerics(value: object) -> object:
    """Coerce every numerically-integral float (e.g. ``24.0``) to the
    equivalent int (``24``), recursively through dicts and lists.

    `json.dumps` distinguishes ``24`` from ``24.0`` by design (they really
    are different JSON values) -- without this, a hand-authored YAML int
    (e.g. a position manifest's ``expected_state["turn"]: 24``) permanently
    mismatches a live-captured float (``24.0``) that represents the
    identical game state, digesting to two different hashes forever. This
    applies the same canonical rule everywhere in the state, not just at
    the top level, so a nested unit/city/tile field (x/y/charges/etc.)
    normalizes the same way.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Mapping):
        return {key: _canonicalize_numerics(v) for key, v in value.items()}
    if isinstance(value, list):
        return [_canonicalize_numerics(v) for v in value]
    return value


def normalize_state(state: Mapping[str, object]) -> dict[str, object]:
    """Copy `state` with units/cities sorted by id and tiles sorted by
    (x, y), and every numerically-integral float canonicalized to an int
    (see `_canonicalize_numerics`).

    Every other key is passed through untouched (beyond that numeric
    canonicalization) — normalize_state only knows about the three
    declared-list fields whose row order is an artifact of iteration, not
    part of the state itself.
    """
    normalized = dict(state)
    for key, key_fn in _LIST_KEYS.items():
        rows = normalized.get(key)
        if rows is not None:
            normalized[key] = _sorted_rows(rows, key=key_fn)
    return _canonicalize_numerics(normalized)


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

    Raises `BenchmarkStateError` if the query reports an `ERR:`-prefixed
    line (e.g. the manifest's `player_id` doesn't resolve in-game) — this
    must surface as a clear failure, never as a near-empty state that later
    just looks like "everything differs" in a digest mismatch.
    """
    query = lq.build_benchmark_state_query(player_id, tile_coords)
    lines = await connection.execute_read(query)
    for line in lines:
        if line.startswith("ERR:"):
            raise BenchmarkStateError(line)
    state = lq.parse_benchmark_state(lines)

    # F13: execute_read swallows read timeouts and returns whatever lines it
    # collected so far. A truncated/empty response with no ERR: line and no
    # IDENTITY row parses to parse_benchmark_state's all-None/empty default
    # state -- hashing that as real game state would turn a retryable
    # harness failure into a session-killing checksum abort. Parsing here is
    # all-or-nothing: the identity row's core fields (turn, player_id) must
    # both be present, or this is treated as an incomplete/truncated
    # response, never a "the state really looks like this" answer.
    if state.get("turn") is None or state.get("player_id") is None:
        raise BenchmarkStateError(
            "incomplete or truncated benchmark-state response: missing identity "
            f"row (turn={state.get('turn')!r}, player_id={state.get('player_id')!r}); "
            f"received {len(lines)} line(s)"
        )

    return normalize_state(state)
