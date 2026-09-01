"""`civ-arena-benchmark-position capture|verify` -- author and freeze one
controlled-position benchmark save purely through the SAME production
deploy/reload/popup-hygiene/canonical-state path the counted benchmark
runner uses (`benchmark_deploy.deploy_via_windows`,
`benchmark_runner.reload_position`, `popups.dismiss_blocking_popups`,
`benchmark_state.capture_canonical_state`/`state_digest`).

This module exists so a position's `expected_state_sha256` is never
hand-derived or captured through some ad-hoc, one-off script: `capture`
produces the post-reload canonical state/digest an author bakes into a new
position manifest, and `verify` re-deploys and re-reloads that manifest
`REQUIRED_CYCLES` (12) times to prove the position reloads to the exact
same digest every time -- the reproducibility guarantee a whole calibration
campaign depends on -- before it is trusted as a frozen, counted position.

Neither command ever advances a turn: both stop after the canonical-state
capture, never calling `end_turn` or any mutating game action.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

from civ_mcp.arena.benchmark_deploy import deploy_via_windows
from civ_mcp.arena.benchmark_manifest import PositionManifest, load_position_manifest
from civ_mcp.arena.benchmark_runner import reload_position
from civ_mcp.arena.benchmark_state import capture_canonical_state, state_digest
from civ_mcp.arena.popups import dismiss_blocking_popups
from civ_mcp.connection import GameConnection

__all__ = [
    "REQUIRED_CYCLES",
    "PositionCLIError",
    "UnverifiedReloadError",
    "capture_position",
    "verify_position",
    "main",
]

# `verify` freeze-mode is defined as exactly twelve fresh deploy+reload
# cycles -- not "at least" or "up to" twelve. A partial run proves nothing
# about reproducibility; --cycles exists as an explicit, checked parameter
# (rather than a hardcoded literal with no CLI surface) so an operator
# cannot silently under-run it and mistake a short, unchecked pass for the
# real freeze guarantee.
REQUIRED_CYCLES = 12

_PROVENANCE_REQUIRED_FIELDS = {
    "archive",
    "archive_sha256",
    "game_save_name",
    "player_id",
    "relevant_tiles",
    "game_build",
    "ruleset",
    "dlc",
    "mods",
    "base_save_identity",
    "mutation_journal",
}


class PositionCLIError(Exception):
    """A CLI-level input/validation failure (bad provenance JSON, wrong
    --cycles, an unreadable position manifest) -- never a live-game
    failure. `main()` converts this to a stderr message and exit code 1."""


class UnverifiedReloadError(Exception):
    """`reload_position` reported `verified=False` for a `capture`/`verify`
    cycle -- the F16(b) stable-open-port case, structurally
    indistinguishable from an inert Network.LoadGame that never actually
    reloaded anything (see `benchmark_runner.reload_position`'s
    docstring). Both commands must treat this as a hard stop: `capture`
    would otherwise bake a "post-reload" state that was never preceded by
    a confirmed reload, and `verify` would let every cycle silently skip
    the one thing (an actual reload) its 12-digest match is supposed to
    prove happened -- freezing a position as "reproducible" on zero
    evidence. Raised before popup hygiene or state capture ever run for
    the offending cycle; the CLI writes no output when this propagates."""


def _load_provenance(path: str | Path) -> dict[str, object]:
    """Load and strictly validate an authoring-provenance JSON file.

    Required fields: the archive path/digest, game save name, player ID,
    relevant tile coordinates, game build, ruleset, DLC, mods, base-save
    identity, and mutation journal -- the full authoring record a `capture`
    output must be traceable back to. Unlike the position-manifest YAML
    loader this does not reject unknown extra keys (provenance is a raw,
    free-form authoring record, not a strict schema) -- only missing
    required fields are rejected.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PositionCLIError(
            f"authoring provenance {path}: expected a JSON object at top level, got "
            f"{type(raw).__name__}"
        )
    missing = _PROVENANCE_REQUIRED_FIELDS - set(raw)
    if missing:
        raise PositionCLIError(
            f"authoring provenance {path}: missing required field(s): {sorted(missing)}"
        )
    return raw



async def capture_position(provenance: Mapping[str, object]) -> dict[str, object]:
    """Deploy `provenance`'s archive and capture the post-reload canonical
    state through the exact production path -- never advances a turn.

    Call order (identical to the counted runner's own pre-episode sequence,
    and to `verify_position`'s per-cycle sequence below):
    `deploy_via_windows` -> production `reload_position` ->
    `dismiss_blocking_popups` -> `capture_canonical_state` -> `state_digest`.

    Returns the deployment evidence, reload/popup-hygiene evidence, and the
    captured canonical state + its digest -- everything an author needs to
    bake a new position manifest's `expected_state` /
    `expected_state_sha256` fields. Writes nothing itself; the CLI's
    `capture` subcommand decides where the result goes.
    """
    archive = str(provenance["archive"])
    archive_sha256 = str(provenance["archive_sha256"])
    game_save_name = str(provenance["game_save_name"])
    player_id = int(provenance["player_id"])  # type: ignore[arg-type]
    relevant_tiles = [tuple(t) for t in provenance["relevant_tiles"]]  # type: ignore[union-attr]

    deployment = deploy_via_windows(archive, game_save_name, archive_sha256)

    # reload_position only ever reads `.game_save_name` off its `position`
    # argument -- a full PositionManifest doesn't exist yet at capture time
    # (this call is what produces the evidence a manifest's expected_state
    # is built from), so a minimal stand-in carrying just that one
    # attribute is exactly what the production function needs.
    position_stub = SimpleNamespace(game_save_name=game_save_name)

    connection = GameConnection()
    await connection.connect()
    try:
        verified = await reload_position(connection, position_stub)
        if not verified:
            # C1: an unverified reload is structurally indistinguishable
            # from an inert Network.LoadGame -- proceeding would capture a
            # "post-reload" state with no confirmed reload behind it.
            raise UnverifiedReloadError(
                f"capture for {game_save_name!r}: reload_position reported "
                "verified=False (F16(b) stable-open-port case) -- aborting "
                "before popup hygiene/state capture; no output produced"
            )
        popup_status = await dismiss_blocking_popups(connection)
        state = await capture_canonical_state(connection, player_id, relevant_tiles)
    finally:
        await connection.disconnect()

    digest = state_digest(state)
    return {
        "provenance": dict(provenance),
        "deployment": dataclasses.asdict(deployment),
        "reload": {"verified": verified},
        "popup_hygiene": {"status": popup_status},
        "captured_state": state,
        "captured_state_sha256": digest,
    }


async def verify_position(position: PositionManifest, cycles: int) -> dict[str, object]:
    """Freshly deploy and reload `position` `cycles` times through the
    exact production path, stopping at the first digest mismatch against
    `position.expected_state_sha256`.

    Per-cycle call order (identical to `capture_position`'s):
    `deploy_via_windows` -> production `reload_position` ->
    `dismiss_blocking_popups` -> `capture_canonical_state` -> digest
    compare. Never advances a turn.

    Returns `{"ok": True, "cycles_completed": cycles, "digests": [...]}`
    with all `cycles` successful digests when every cycle matched, or
    `{"ok": False, "cycles_completed": <n>, "mismatch_at_cycle": <n>, ...}`
    the moment a cycle's digest disagrees -- no further cycles run past a
    mismatch, and the twelve-digest list is only ever written by the CLI
    when `ok` is True.
    """
    if cycles != REQUIRED_CYCLES:
        raise PositionCLIError(
            f"--cycles must be exactly {REQUIRED_CYCLES} for freeze-mode verification "
            f"(got {cycles})"
        )

    digests: list[str] = []
    for cycle in range(1, cycles + 1):
        deploy_via_windows(position.archive, position.game_save_name, position.archive_sha256)

        connection = GameConnection()
        await connection.connect()
        try:
            verified = await reload_position(connection, position)
            if not verified:
                # C1: every cycle must be backed by a confirmed reload --
                # otherwise a run that never actually reloads the game
                # would trivially match its own digest 12 times and freeze
                # as "reproducible" on zero evidence.
                raise UnverifiedReloadError(
                    f"verify {position.position_id!r} cycle {cycle}/{cycles}: "
                    "reload_position reported verified=False (F16(b) "
                    "stable-open-port case) -- aborting before popup "
                    "hygiene/state capture; no output produced"
                )
            await dismiss_blocking_popups(connection)
            state = await capture_canonical_state(
                connection, position.player_id, position.relevant_tiles
            )
        finally:
            await connection.disconnect()

        digest = state_digest(state)
        if digest != position.expected_state_sha256:
            return {
                "ok": False,
                "position_id": position.position_id,
                "cycles_completed": cycle,
                "mismatch_at_cycle": cycle,
                "expected_state_sha256": position.expected_state_sha256,
                "observed_state_sha256": digest,
                "digests": digests,
            }
        digests.append(digest)

    return {
        "ok": True,
        "position_id": position.position_id,
        "cycles_completed": cycles,
        "digests": digests,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="civ-arena-benchmark-position")
    sub = parser.add_subparsers(dest="command", required=True)

    capture_parser = sub.add_parser(
        "capture", help="deploy + reload a position and capture its post-reload canonical state"
    )
    capture_parser.add_argument("--authoring-provenance", required=True)
    capture_parser.add_argument("--output", required=True)

    verify_parser = sub.add_parser(
        "verify", help="freshly deploy + reload a frozen position N times and compare digests"
    )
    verify_parser.add_argument("--position", required=True)
    verify_parser.add_argument("--cycles", type=int, default=REQUIRED_CYCLES)
    verify_parser.add_argument("--output", required=True)

    return parser


def _write_json(path: str | Path, payload: Mapping[str, object]) -> None:
    Path(path).write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _run_capture(args: argparse.Namespace) -> int:
    try:
        provenance = _load_provenance(args.authoring_provenance)
    except (OSError, ValueError, PositionCLIError) as exc:
        print(f"civ-arena-benchmark-position: {exc}", file=sys.stderr)
        return 1

    try:
        result = asyncio.run(capture_position(provenance))
    except UnverifiedReloadError as exc:
        print(f"civ-arena-benchmark-position: {exc}", file=sys.stderr)
        return 1
    _write_json(args.output, result)
    print(f"civ-arena-benchmark-position: capture written to {args.output}")
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    # Fail closed before any deploy/reload/live-game call: an operator
    # under-running the freeze guarantee (e.g. --cycles 3) must never
    # silently produce partial evidence that looks like a full verification.
    if args.cycles != REQUIRED_CYCLES:
        print(
            f"civ-arena-benchmark-position: --cycles must be exactly {REQUIRED_CYCLES} "
            f"for freeze-mode verification (got {args.cycles})",
            file=sys.stderr,
        )
        return 2

    try:
        position = load_position_manifest(args.position)
    except (OSError, ValueError) as exc:
        print(f"civ-arena-benchmark-position: {exc}", file=sys.stderr)
        return 1

    try:
        result = asyncio.run(verify_position(position, args.cycles))
    except UnverifiedReloadError as exc:
        print(f"civ-arena-benchmark-position: {exc}", file=sys.stderr)
        return 1
    if not result["ok"]:
        print(
            "civ-arena-benchmark-position: verification failed at cycle "
            f"{result['mismatch_at_cycle']}: expected {result['expected_state_sha256']}, "
            f"observed {result['observed_state_sha256']}",
            file=sys.stderr,
        )
        return 1

    _write_json(args.output, result)
    print(f"civ-arena-benchmark-position: verification written to {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "capture":
        return _run_capture(args)
    if args.command == "verify":
        return _run_verify(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover - argparse enforces choices
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
