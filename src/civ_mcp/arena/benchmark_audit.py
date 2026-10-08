"""Audit layer: uncredited-mutation classification and an all-action loss audit.

`audit_losses` examines every recorded step (credited, uncredited and
error-returning) for unit lifecycle changes and improvement removals or
replacements, marking each as declared when a fired rubric harm references it
at that step. `mutation_records` selects successful state-changing steps that
earned no objective-progress attribution and builds self-contained records;
`classify_mutation` reads only such a record (so historically normalised
records can use it too) and assigns ONE category by `mutation_category`
precedence plus descriptive tags. Nothing here grants credit or deducts: the
primary deduction is always 0. `undercredited_completions` is the separate
under-credit audit for attained objective credit that the endpoint lost.
"""
from __future__ import annotations

from typing import Any

from civ_mcp.arena.action_metrics import _hex_distance
from civ_mcp.arena.benchmark_ledger import build_ledger
from civ_mcp.arena.benchmark_lifecycle import classify_lifecycle, complete_rows, player_id

_ERROR_PREFIX = "Error:"
_NO_IMPROVEMENT = (None, "NONE")


# ---------------------------------------------------------------------------
# Loss audit
# ---------------------------------------------------------------------------

def _result_shape(step: dict[str, Any]) -> str:
    result = step.get("tool_result_full")
    return "error" if isinstance(result, str) and result.startswith(_ERROR_PREFIX) else "ok"


def _tiles_by_xy(state: dict[str, Any]) -> dict[tuple[Any, Any], dict[str, Any]]:
    return {(row.get("x"), row.get("y")): row for row in complete_rows(state, "tiles")}


def _improvement_losses(step: dict[str, Any]) -> list[list[Any]]:
    """Tiles whose existing improvement was removed or replaced in this step."""
    before, after = _tiles_by_xy(step["state_before"]), _tiles_by_xy(step["state_after"])
    return [list(xy) for xy in sorted(set(before) & set(after))
            if before[xy].get("improvement") not in _NO_IMPROVEMENT
            and after[xy].get("improvement") != before[xy].get("improvement")]


def _references(leaf: dict[str, Any], kind: str, entity: list[Any]) -> bool:
    if kind == "unit":
        return leaf.get("kind") == "unit_lost" and list(leaf.get("unit", ())) == entity
    return leaf.get("kind") == "asset_displaced" and entity in [
        list(xy) for xy in leaf.get("tiles", ())]


def _declared_harm_ids(harms: list[dict[str, Any]], k: Any, kind: str,
                       entity: list[Any]) -> list[str]:
    """Fired harms (any status) that reference this entity/tile at step `k`."""
    ids = []
    for h in harms:
        if not h.get("fired"):
            continue
        if h.get("timing") == "event" and k not in h.get("fired_steps", ()):
            continue
        if any(_references(leaf, kind, entity) for leaf in h.get("reference", ())):
            ids.append(h["id"])
    return sorted(ids)


def audit_losses(trial: dict[str, Any],
                 declared_losses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per observed unit lifecycle change or improvement loss, all steps."""
    out = []
    for step in trial["steps"]:
        k = step["idx"]
        observed = [("unit", r["entity"], r["status"]) for r in classify_lifecycle(step)]
        observed += [("improvement", xy, "lost") for xy in _improvement_losses(step)]
        for kind, entity, lifecycle in observed:
            ids = _declared_harm_ids(declared_losses, k, kind, entity)
            out.append({"step": k, "kind": kind, "entity": entity, "lifecycle": lifecycle,
                        "declared": bool(ids), "declared_harm_ids": ids,
                        "tool_name": step["tool_name"], "result_shape": _result_shape(step)})
    return out


# ---------------------------------------------------------------------------
# Mutation records
# ---------------------------------------------------------------------------

def _actor(step: dict[str, Any]) -> dict[str, Any] | None:
    """The owned unit the recorded tool call targets, from `state_before`."""
    args, state = step["tool_args"], step["state_before"]
    pid = player_id(state)
    for key, field in (("unit_index", "unit_index"), ("unit_id", "id")):
        value = args.get(key)
        if type(value) is int:
            for unit in complete_rows(state, "units"):
                if unit.get("owner") == pid and unit.get(field) == value:
                    return unit
    return None


def _nearest(xy: list[Any], task_tiles: list[tuple[int, int]]) -> int | None:
    return min((_hex_distance(xy, t) for t in task_tiles), default=None)


def _movement(step: dict[str, Any], actor: dict[str, Any] | None,
              task_tiles: list[tuple[int, int]]) -> dict[str, Any] | None:
    if actor is None:
        return None
    ref = (actor["owner"], actor["id"])
    after = next((u for u in complete_rows(step["state_after"], "units")
                  if (u.get("owner"), u.get("id")) == ref), None)
    if after is None:
        return None
    start, end = [actor.get("x"), actor.get("y")], [after.get("x"), after.get("y")]
    if start == end:
        return None
    return {"builder": actor.get("type") == "UNIT_BUILDER", "unit": list(ref),
            "from": start, "to": end, "distance_before": _nearest(start, task_tiles),
            "distance_after": _nearest(end, task_tiles)}


def _improvement(step: dict[str, Any], actor: dict[str, Any] | None,
                 task_tiles: list[tuple[int, int]]) -> dict[str, Any] | None:
    before, after = _tiles_by_xy(step["state_before"]), _tiles_by_xy(step["state_after"])
    for xy in sorted(set(before) & set(after)):
        new = after[xy].get("improvement")
        if new not in _NO_IMPROVEMENT and new != before[xy].get("improvement"):
            on_unit = actor is not None and (actor.get("x"), actor.get("y")) == xy
            return {"type": new, "tile": list(xy), "on_unit_tile": on_unit,
                    "on_task_tile": xy in {tuple(t) for t in task_tiles}}
    return None


def mutation_records(trial: dict[str, Any], progress: list[dict[str, Any]], *,
                     task_tiles: list[tuple[int, int]],
                     declared_losses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Self-contained records for successful, state-changing, uncredited steps.

    `task_tiles` are public task coordinates from archived tool observations
    (never the rubric's targets); `declared_losses` is the scorer's `harms`.
    """
    credited = {p["step"] for p in progress}
    ledger = {s["step"]: s["entries"] for s in build_ledger(trial)["steps"]}
    losses: dict[Any, list[dict[str, Any]]] = {}
    for loss in audit_losses(trial, list(declared_losses)):
        losses.setdefault(loss["step"], []).append(loss)

    out = []
    for step in trial["steps"]:
        k = step["idx"]
        if (_result_shape(step) == "error" or k in credited
                or step.get("state_digest_before") == step.get("state_digest_after")):
            continue
        step_losses = losses.get(k, [])
        actor = _actor(step)
        out.append({
            "step": k, "tool_name": step["tool_name"], "tool_args": step["tool_args"],
            "ok": True,
            "declared_harm_ids": sorted({i for loss in step_losses
                                         for i in loss["declared_harm_ids"]}),
            "objective_ids": [],
            "losses": [{"kind": loss["kind"], "entity": loss["entity"],
                        "lifecycle": loss["lifecycle"], "declared": loss["declared"]}
                       for loss in step_losses],
            "economic_changes": ledger.get(k, []),
            "improvement": _improvement(step, actor, task_tiles),
            "movement": _movement(step, actor, task_tiles),
            "coverage": {"loss": "complete"},
        })
    return out


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def mutation_category(record):
    if record["declared_harm_ids"]:
        return "declared_harm"
    if any(loss["lifecycle"] == "lost" and not loss["declared"]
           for loss in record["losses"]):
        return "undeclared_loss"
    if record["economic_changes"]:
        return "economic_change"
    movement = record["movement"]
    if movement is not None:
        return "builder_positioning" if movement["builder"] else "non_builder_movement"
    return "insufficient_evidence"


def _tags(record: dict[str, Any]) -> list[str]:
    tags = set()
    improvement = record.get("improvement")
    if improvement and improvement.get("type") == "IMPROVEMENT_FARM" \
            and improvement.get("on_unit_tile"):
        tags.add("farm_on_own_tile")
    movement = record["movement"]
    if movement is not None:
        if not movement["builder"]:
            tags.add("non_builder_move")
        else:
            before, after = movement.get("distance_before"), movement.get("distance_after")
            if before is not None and after is not None:
                tags.add("closer_to_public_task" if after < before else
                         "same_distance_to_public_task" if after == before else
                         "farther_from_public_task")
    return sorted(tags)


def classify_mutation(record: dict[str, Any]) -> dict[str, Any]:
    """One category by precedence; secondary effects stay in tags and evidence."""
    return {"category": mutation_category(record), "tags": _tags(record),
            "primary_deduction": 0,
            "evidence": {"step": record["step"], "tool_name": record.get("tool_name"),
                         "losses": record["losses"],
                         "economic_paths": [e["path"] for e in record["economic_changes"]],
                         "movement": record["movement"],
                         "improvement": record.get("improvement")}}


# ---------------------------------------------------------------------------
# Under-credit audit
# ---------------------------------------------------------------------------

def undercredited_completions(progress: list[dict[str, Any]],
                              score: dict[str, Any]) -> list[dict[str, Any]]:
    """Objectives whose attained step credit exceeds their final endpoint credit."""
    final = {o["id"]: o["credit"] for o in score["objectives"]}
    attained: dict[str, list[dict[str, Any]]] = {}
    for p in progress:
        attained.setdefault(p["objective_id"], []).append(p)
    out = []
    for oid in sorted(attained):
        best = max(p["credit_after"] for p in attained[oid])
        if best > final.get(oid, 0):
            out.append({"objective_id": oid, "max_attained": best,
                        "final_credit": final.get(oid, 0),
                        "steps": sorted(p["step"] for p in attained[oid])})
    return out
