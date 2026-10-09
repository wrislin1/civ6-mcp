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
`reproduce_audit` is the offline historical regression: it recomputes v1
attribution over digest-pinned tracked calibration trials with the unchanged
v1 evaluators, normalises each uncredited step into a record and classifies it
with the same `classify_mutation`.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from civ_mcp.arena.action_metrics import _advances_objective, _hex_distance, classify_result
from civ_mcp.arena.benchmark_ledger import build_ledger, measured_delta
from civ_mcp.arena.benchmark_report import score_trial
from civ_mcp.arena.benchmark_lifecycle import classify_lifecycle, complete_rows, player_id

_NO_IMPROVEMENT = (None, "NONE")


# ---------------------------------------------------------------------------
# Loss audit
# ---------------------------------------------------------------------------

def _result_shape(step: dict[str, Any]) -> str:
    """``ok`` for a result the shared classifier calls a success; ``error`` for
    every other shape -- the game's title-case ``Error:``/``ERR:``/``|BLOCKED``
    rejections and the agent's upper-case ``ERROR: ...`` wrapper for a
    dispatch that raised after changing state. A non-string result (never a
    dispatched call) is ``error`` too: it is not a successful mutation."""
    result = step.get("tool_result_full")
    if not isinstance(result, str):
        return "error"
    return "ok" if classify_result(result) == "success" else "error"


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


# ---------------------------------------------------------------------------
# Historical audit regression (version-1 calibration trials)
# ---------------------------------------------------------------------------

_HISTORICAL_CAMPAIGNS = ("builder-economy-cal-v1", "builder-economy-cal-v2")
# One bucket per uncredited record, by this precedence over classifier tags.
_HISTORICAL_BUCKETS = ("farm_on_own_tile", "closer_to_public_task",
                       "same_distance_to_public_task", "farther_from_public_task",
                       "non_builder_move")


def checked_bytes(root, item):
    import hashlib
    data = (root / item["path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError(f"historical input changed: {item['path']}")
    return data


def _public_task_tiles(data: bytes, item: dict[str, Any]) -> list[tuple[int, int]]:
    """Task coordinates from the archived player-facing `get_builder_tasks` text."""
    step = next(s for s in json.loads(data)["steps"] if s["idx"] == item["step"])
    text = step["tool_result_full"]
    if step["tool_name"] != "get_builder_tasks" or \
            hashlib.sha256(text.encode()).hexdigest() != item["text_sha256"]:
        raise ValueError(f"historical input changed: {item['path']} step {item['step']}")
    listing = text.split("IDLE BUILDERS")[0]
    return [(int(x), int(y)) for x, y in re.findall(r"^\s+\((\d+),(\d+)\):", listing, re.M)]


def _v1_record(step: dict[str, Any], task_tiles: list[tuple[int, int]]) -> dict[str, Any]:
    """Task 8 record from a v1 step. v1 units carry no owner/role/hp and v1
    tiles carry no yields, so loss coverage is unavailable and the only
    economic facts are gold, faith and unit charges. The acting unit is the
    one whose position (move) or charges (improvement) changed."""
    state_before, state_after = step["state_before"], step["state_after"]
    before = {u["id"]: u for u in state_before["units"]}
    after = {u["id"]: u for u in state_after["units"]}
    shared = sorted(set(before) & set(after))
    economic = [{"path": [key], **measured_delta(state_before.get(key), state_after.get(key))}
                for key in ("gold", "faith") if state_before.get(key) != state_after.get(key)]
    economic += [{"path": ["units", str(i), "charges"],
                  **measured_delta(before[i]["charges"], after[i]["charges"])}
                 for i in shared if before[i]["charges"] != after[i]["charges"]]
    moved = [i for i in shared
             if (before[i]["x"], before[i]["y"]) != (after[i]["x"], after[i]["y"])]
    movement = None
    if len(moved) == 1:
        u, v = before[moved[0]], after[moved[0]]
        start, end = [u["x"], u["y"]], [v["x"], v["y"]]
        movement = {"builder": u["type"] == "UNIT_BUILDER", "unit": u["id"],
                    "from": start, "to": end,
                    "distance_before": _nearest(start, task_tiles),
                    "distance_after": _nearest(end, task_tiles)}
    tiles_before = {(t["x"], t["y"]): t for t in state_before["tiles"]}
    tiles_after = {(t["x"], t["y"]): t for t in state_after["tiles"]}
    built = [xy for xy in sorted(set(tiles_before) & set(tiles_after))
             if tiles_after[xy].get("improvement") not in _NO_IMPROVEMENT
             and tiles_after[xy].get("improvement") != tiles_before[xy].get("improvement")]
    spent = [i for i in shared if after[i]["charges"] < before[i]["charges"]]
    improvement = None
    if len(built) == 1:
        xy = built[0]
        actor = before[spent[0]] if len(spent) == 1 else None
        improvement = {"type": tiles_after[xy]["improvement"], "tile": list(xy),
                       "on_unit_tile": actor is not None and (actor["x"], actor["y"]) == xy,
                       "on_task_tile": xy in set(task_tiles)}
    return {"step": step["idx"], "tool_name": step["tool_name"],
            "tool_args": step["tool_args"], "ok": True, "declared_harm_ids": [],
            "objective_ids": [], "losses": [], "economic_changes": economic,
            "improvement": improvement, "movement": movement,
            "coverage": {"loss": "unavailable"}}


def _v1_attribution(trial: dict[str, Any], rubric: Any,
                    objectives: list[dict[str, Any]]) -> tuple[list, list]:
    """Uncredited successful mutations by the unchanged v1 definition
    (`classify_action_quality`: success + digest change + no objective
    advanced), cross-checked against `score_trial`'s counts, plus credited
    sub-predicate flips the v1 rubric never scores (under-credited)."""
    scored = score_trial(trial, rubric, objectives=objectives)
    final = scored["rubric"]["tasks"]
    levels = {task["task_id"]: [lv["predicate"] for lv in task["levels"]] for task in rubric}
    uncredited, under, credited = [], [], 0
    for step in trial["steps"]:
        if classify_result(str(step.get("tool_result_full", ""))) != "success" \
                or step.get("state_digest_before") == step.get("state_digest_after"):
            continue
        advanced = [o for o in objectives if step.get("tool_name") in o.get("tools", ())
                    and _advances_objective(o["progress_predicate"], step)]
        if not advanced:
            uncredited.append(step)
            continue
        credited += 1
        for o in advanced:
            task = final[o["task_id"]]
            subs = o["progress_predicate"].get("predicates", [o["progress_predicate"]])
            if task["score"] < task["max_score"] and any(
                    sub not in levels[o["task_id"]] and _advances_objective(sub, step)
                    for sub in subs):
                under.append({"step": step["idx"], "objective_id": o["task_id"],
                              "rubric_score": task["score"],
                              "rubric_max": task["max_score"]})
    quality = scored["action_quality"]
    if quality["useful_actions"] != credited or \
            quality["successful_mutations"] != credited + len(uncredited):
        raise ValueError(f"v1 attribution disagrees with score_trial at {trial['index']}")
    return uncredited, under


def _lock_path(trial_path: str) -> str:
    return (PurePosixPath(trial_path).parent.parent / "session.json").as_posix()


def reproduce_audit(fixture_path: Path, *, root: Path) -> dict[str, Any]:
    """Recompute the historical uncredited-actions audit from pinned tracked inputs.

    Every input is digest-checked and the raw trial set must equal all trials
    of both campaigns before anything is scored; actual membership is then
    computed from the raw steps alone and only afterwards compared."""
    fixture = json.loads(Path(fixture_path).read_text())
    for item in fixture["evaluator_files"]:
        checked_bytes(root, item)
    data = {item["path"]: checked_bytes(root, item)
            for item in fixture["inputs"] + fixture["supplements"]}
    raw = sorted(i["path"] for i in fixture["inputs"] if i["kind"] == "raw_trial")
    on_disk = sorted(p.relative_to(root).as_posix() for c in _HISTORICAL_CAMPAIGNS
                     for p in (root / "benchmark_runs" / c / "blocks").glob("*/trials/*.json"))
    if raw != on_disk:
        raise ValueError("raw trial set does not equal all trials of both campaigns")
    locks = {i["path"] for i in fixture["inputs"] if i["kind"] == "objective_lock"}
    if {_lock_path(p) for p in raw} != locks:
        raise ValueError("objective locks do not match the raw trial blocks")
    source = next(i for i in fixture["inputs"] if i["kind"] == "public_task_list")
    task_tiles = _public_task_tiles(data[source["path"]], source)
    supplement = {s["applies_to_tag"]: s["id"] for s in fixture["supplements"]}

    records, undercredits = [], []
    for path in raw:
        parts = PurePosixPath(path).parts
        ident = {"campaign": parts[1], "block": parts[3], "trial": PurePosixPath(path).stem}
        trial = json.loads(data[path])
        position = json.loads(data[_lock_path(path)])["positions"][trial["position_id"]]
        uncredited, under = _v1_attribution(trial, position["rubric"], position["objectives"])
        undercredits += [{**ident, **u} for u in under]
        for step in uncredited:
            result = classify_mutation(_v1_record(step, task_tiles))
            bucket = next((t for t in _HISTORICAL_BUCKETS if t in result["tags"]), None)
            records.append({**ident, "step": step["idx"], "tool_name": step["tool_name"],
                            "tag": bucket, "category": result["category"],
                            "tags": result["tags"], "supplement": supplement.get(bucket)})

    def keys(rows: list[dict[str, Any]], *fields: str) -> set[tuple[Any, ...]]:
        return {tuple(r[f] for f in fields) for r in rows}

    member = ("campaign", "block", "trial", "step", "tag")
    credit = ("campaign", "block", "trial", "step", "objective_id")
    actual, expected = keys(records, *member), keys(fixture["expected_membership"], *member)
    actual_u = keys(undercredits, *credit)
    expected_u = keys(fixture["expected_undercredits"], *credit)
    diff = {"missing": sorted(expected - actual, key=str),
            "extra": sorted(actual - expected, key=str),
            "undercredits_missing": sorted(expected_u - actual_u, key=str),
            "undercredits_extra": sorted(actual_u - expected_u, key=str)}
    return {
        "membership_matches": not any(diff.values()),
        "trial_count": len(raw),
        "affected_trial_count": len(keys(records, "campaign", "block", "trial")),
        "uncredited_count": len(records),
        "tag_counts": {t: sum(r["tag"] == t for r in records) for t in _HISTORICAL_BUCKETS},
        "declared_harm_count": sum(r["category"] == "declared_harm" for r in records),
        "undercredited_completion_count": len(undercredits),
        "records": records,
        "membership_diff": diff,
    }
