"""Descriptive measured benefit ledger over a recorded benchmark trial.

`build_ledger` compares stable entity/tile keys in canonical v2 snapshots: per
step (`state_before` -> `state_after`) and net (`initial_state` ->
`final_state`, never a sum of steps). It records raw before/after vectors with
explicit coverage; a value absent on either side is `unavailable`, never zero.
Disappearing units consult `classify_lifecycle`: only a proven `consumed` unit
records its charges going to 0. The ledger is descriptive only: it carries no
utility scale and never feeds primary scoring, compensation or advancement.
"""
from __future__ import annotations

from typing import Any

from civ_mcp.arena.benchmark_lifecycle import classify_lifecycle, complete_rows, player_id
from civ_mcp.arena.benchmark_state import BenchmarkStateError

_YIELDS = ("food", "production", "gold", "science", "culture", "faith")
_RECEIPT_TOOL = "remove_feature"

# path tuple -> (value, units, refs)
_Values = dict[tuple[str, ...], tuple[Any, str, dict[str, Any]]]


def measured_delta(before, after):
    if before is None or after is None:
        return {"before": before, "after": after, "delta": None,
                "coverage": "unavailable"}
    return {"before": before, "after": after, "delta": after - before,
            "coverage": "measured"}


def _values(state: dict[str, Any]) -> _Values:
    """Every tracked scalar of one snapshot, keyed by its stable path."""
    out: _Values = {
        ("gold",): (state.get("gold"), "gold", {}),
        ("faith",): (state.get("faith"), "faith", {}),
    }
    for row in complete_rows(state, "tiles"):
        x, y = row["x"], row["y"]
        yields = row.get("yields") or {}
        for key in _YIELDS:
            out[("tiles", f"{x},{y}", key)] = (yields.get(key), "yield", {"tile": [x, y]})
    resources = state.get("resources")
    if not isinstance(resources, list):
        raise BenchmarkStateError("state lacks resources evidence")
    for row in resources:
        rtype = row["resource_type"]
        access = row.get("access")
        out[("resources", rtype, "access")] = (
            None if access is None else int(access), "access", {"resource": rtype})
    pid = player_id(state)
    for unit in complete_rows(state, "units"):
        if unit.get("owner") == pid:
            ref = [unit["owner"], unit["id"]]
            out[("units", f"{ref[0]}:{ref[1]}", "charges")] = (
                unit.get("charges"), "charges", {"unit": ref})
    return out


def _changed(before: _Values, after: _Values) -> set[tuple[str, ...]]:
    """Paths whose value differs. Units only count when they existed before."""
    paths = set(before) | {p for p in after if p[0] != "units"}
    return {p for p in paths
            if before.get(p, (None,))[0] != after.get(p, (None,))[0]
            or (p in before) != (p in after)}


def _lifecycles(step: dict[str, Any]) -> dict[str, str]:
    return {f"{r['entity'][0]}:{r['entity'][1]}": r["status"]
            for r in classify_lifecycle(step)}


def _entry(path, before: _Values, after: _Values, sources: list[int],
           lifecycle: dict[str, str]) -> dict[str, Any]:
    _, units, refs = before.get(path) or after[path]
    old = before[path][0] if path in before else None
    new = after[path][0] if path in after else None
    extra: dict[str, Any] = {}
    if path[0] == "units" and path not in after:
        status = lifecycle.get(path[1], "unresolved")
        extra["lifecycle"] = status
        new = 0 if status == "consumed" else None
    entry = {"path": list(path), **measured_delta(old, new), "units": units,
             "refs": refs, "source_steps": sources}
    entry.update(extra)
    return entry


def build_ledger(trial: dict[str, Any]) -> dict[str, Any]:
    steps_out: list[dict[str, Any]] = []
    sources: dict[tuple[str, ...], list[int]] = {}
    vanished_in: dict[str, str] = {}
    for step in trial["steps"]:
        k, tool = step["idx"], step["tool_name"]
        before, after = _values(step["state_before"]), _values(step["state_after"])
        changed = _changed(before, after)
        lifecycle = _lifecycles(step)
        entries = []
        for path in sorted(changed):
            sources.setdefault(path, []).append(k)
            entry = _entry(path, before, after, [k], lifecycle)
            if "lifecycle" in entry:
                vanished_in[path[1]] = entry["lifecycle"]
            entry["tool_name"] = tool
            if tool == _RECEIPT_TOOL and path in (("gold",), ("faith",)):
                entry["receipt"] = True
            entries.append(entry)
        steps_out.append({"step": k, "tool_name": tool, "entries": entries})

    initial, final = _values(trial["initial_state"]), _values(trial["final_state"])
    net_paths = _changed(initial, final) | {("gold",), ("faith",)}
    net = [_entry(p, initial, final, sources.get(p, []), vanished_in)
           for p in sorted(net_paths)]
    return {"net": net, "steps": steps_out}
