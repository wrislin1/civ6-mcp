"""Lifecycle classification of owned units across one recorded benchmark step.

`classify_lifecycle` combines the complete own-unit lists of a step's canonical
v2 `state_before`/`state_after` with the recorded tool name, arguments and
result text. It never infers destruction from a missing row alone: a unit that
disappears is `consumed`, `lost` or `transformed` only when the recorded
operation and observed outcome prove it; every other change stays
`unresolved`. Rejection-shaped result text never erases an observed mutation.
These records are derived evidence and stay outside canonical state/digests.
"""
from __future__ import annotations

from typing import Any

from civ_mcp.arena.benchmark_state import BenchmarkStateError

_COUNT_KEYS = {"units": "unit", "targets": "target", "cities": "city", "tiles": "tile"}

# Tools whose successful final-charge use removes the builder.
_IMPROVEMENT_TOOLS = frozenset({"improve_tile", "remove_feature", "repair_improvement"})
# `GameState.attack_unit` returns the narrated combat estimate followed by the
# Lua `OK:MELEE_ATTACK|...` line with `OK:` stripped, so an executed melee
# battle is a result line starting with this marker (rejections are
# `Error: ...` and carry no such line). Ranged attackers take no damage, so
# only melee can kill the attacker.
_MELEE_BATTLE_MARKER = "MELEE_ATTACK|"


def _melee_battle_line(result: str) -> str | None:
    for line in result.splitlines():
        if line.startswith(_MELEE_BATTLE_MARKER):
            return line
    return None


def complete_rows(state: Any, family: str) -> list[dict[str, Any]]:
    """Return `state[family]`, raising unless the capture declares it complete."""
    if not isinstance(state, dict):
        raise BenchmarkStateError("state snapshot is missing")
    rows = state.get(family)
    counts = state.get("row_counts")
    if not isinstance(rows, list) or not isinstance(counts, dict):
        raise BenchmarkStateError(f"state lacks complete {family} evidence")
    declared = counts.get(_COUNT_KEYS[family])
    if type(declared) is not int or declared != len(rows):
        raise BenchmarkStateError(
            f"{family} capture is incomplete: declared {declared!r}, have {len(rows)}")
    return rows


def player_id(state: dict[str, Any]) -> int:
    pid = state.get("player_id")
    if type(pid) is not int:
        raise BenchmarkStateError("state lacks an integer player_id")
    return pid


def _tile(state: dict[str, Any], xy: tuple[Any, Any]) -> dict[str, Any] | None:
    for row in complete_rows(state, "tiles"):
        if (row.get("x"), row.get("y")) == xy:
            return row
    return None


def _intended_outcome(step: dict[str, Any], unit: dict[str, Any]) -> bool:
    """Whether the builder's tile shows the targeted tool's intended outcome."""
    xy = (unit.get("x"), unit.get("y"))
    before, after = _tile(step["state_before"], xy), _tile(step["state_after"], xy)
    if before is None or after is None:
        return False
    tool, args = step["tool_name"], step["tool_args"]
    if tool == "improve_tile":
        wanted = args.get("improvement_name")
        return (isinstance(wanted, str) and before.get("improvement") != wanted
                and after.get("improvement") == wanted and after.get("pillaged") is False)
    if tool == "remove_feature":
        return before.get("feature") not in (None, "NONE") and after.get("feature") == "NONE"
    # repair_improvement
    return (before.get("pillaged") is True and after.get("pillaged") is False
            and after.get("improvement") not in (None, "NONE"))


def _targets_index(args: dict[str, Any], unit: dict[str, Any]) -> bool:
    value = args.get("unit_index")
    return type(value) is int and value == unit.get("unit_index")


def _targets_id(args: dict[str, Any], unit: dict[str, Any]) -> bool:
    value = args.get("unit_id")
    return type(value) is int and value == unit.get("id")


def _record(unit: dict[str, Any], status: str, **facts: Any) -> dict[str, Any]:
    return {"entity": [unit["owner"], unit["id"]], "status": status, "facts": facts}


def classify_lifecycle(step: dict[str, Any]) -> list[dict[str, Any]]:
    """Classify every owned unit of `state_before` that changed identity/type."""
    before, after = step.get("state_before"), step.get("state_after")
    tool, args = step.get("tool_name"), step.get("tool_args")
    if not isinstance(tool, str) or not isinstance(args, dict):
        raise BenchmarkStateError("step lacks recorded tool_name/tool_args")
    result = step.get("tool_result_full")
    result = result if isinstance(result, str) else ""
    before_units = complete_rows(before, "units")
    after_units = complete_rows(after, "units")
    pid = player_id(before)
    if player_id(after) != pid:
        raise BenchmarkStateError("step snapshots disagree on player_id")

    after_by_ref = {(u.get("owner"), u.get("id")): u for u in after_units}
    before_refs = {(u.get("owner"), u.get("id")) for u in before_units}
    records: list[dict[str, Any]] = []
    for unit in before_units:
        if unit.get("owner") != pid:
            continue
        ref = (unit["owner"], unit["id"])
        base = dict(tool_name=tool, tool_args=args, before_xy=[unit.get("x"), unit.get("y")],
                    before_type=unit.get("type"))
        upgrade = tool == "upgrade_unit" and _targets_id(args, unit)
        survivor = after_by_ref.get(ref)
        if survivor is not None:
            if survivor.get("type") == unit.get("type"):
                continue
            status = "transformed" if upgrade else "unresolved"
            records.append(_record(unit, status, **base, replacement=list(ref),
                                   after_type=survivor.get("type")))
            continue

        if upgrade:
            replacements = [
                u for u in after_units
                if u.get("owner") == pid and (u.get("owner"), u.get("id")) not in before_refs
                and (u.get("x"), u.get("y")) == (unit.get("x"), unit.get("y"))
                and u.get("type") != unit.get("type")
            ]
            if len(replacements) == 1:
                new = replacements[0]
                records.append(_record(unit, "transformed", **base,
                                       replacement=[new["owner"], new["id"]],
                                       after_type=new.get("type")))
                continue
        elif (tool in _IMPROVEMENT_TOOLS and _targets_index(args, unit)
              and unit.get("charges") == 1 and _intended_outcome(step, unit)):
            records.append(_record(unit, "consumed", **base, charges_before=1))
            continue
        elif (tool == "attack_unit" and _targets_index(args, unit)
              and (battle := _melee_battle_line(result)) is not None):
            records.append(_record(unit, "lost", **base, cause="combat", battle=battle))
            continue
        elif tool == "delete_unit" and (_targets_index(args, unit) or _targets_id(args, unit)):
            records.append(_record(unit, "lost", **base, cause="disband"))
            continue
        records.append(_record(unit, "unresolved", **base))
    return records
