"""Version-2 signed scorer over recorded benchmark evidence.

Credit is endpoint-only: each objective earns the points of its highest
satisfied rung on the episode's (initial, final) states. Harm is separate and
signed: `final` harms compare the episode endpoints, `event` harms inspect
every recorded step transition (rejection-shaped results included), so a loss
persists even when the credited outcome is later overwritten. Fired harms may
be compensated (no deduction, evidence kept) and are deduplicated per
`loss_key` so one loss is charged once. The primary score is
`(gross - harm) / maximum_credit`, neither clipped nor shifted, and missed
progress is never debited. `attribute_progress` is audit-only and never feeds
the primary score.

The whole rubric is validated before any predicate is evaluated. Rubric and
contract violations raise `ValueError`; missing evidence raises
`BenchmarkStateError` from the predicate layer.
"""
from __future__ import annotations

import copy
import math
from typing import Any

from civ_mcp.arena.benchmark_predicates_v2 import evaluate_predicate, validate_predicate

_RUBRIC_KEYS = {"objectives", "harms"}
_OBJECTIVE_KEYS = {"id", "rungs"}
_RUNG_KEYS = {"points", "predicate"}
_HARM_KEYS = {"id", "loss_key", "objective_id", "weight", "weight_reason", "timing",
              "predicate", "compensation", "priority"}
_COMPENSATION_KEYS = {"timing", "predicate"}
_TIMINGS = ("final", "event")
# Kinds that need a recorded transition; they are the only event-capable kinds.
# Exposure is final-only: temporary exposure followed by recovery is no debit.
_EVENT_ONLY_KINDS = frozenset({"unit_lost", "asset_displaced"})
_EVENT_CAPABLE_KINDS = _EVENT_ONLY_KINDS


# ---------------------------------------------------------------------------
# Signed totals
# ---------------------------------------------------------------------------

def _is_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def signed_totals(credits, deductions, maximum):
    for value in credits:
        if not _is_number(value) or value < 0:
            raise ValueError(f"credit must be a finite non-negative number, got {value!r}")
    for value in deductions:
        if not _is_number(value) or value <= 0:
            raise ValueError(f"deduction must be a finite positive number, got {value!r}")
    if not _is_number(maximum) or maximum <= 0:
        raise ValueError(f"maximum must be a finite positive number, got {maximum!r}")
    gross = sum(credits)
    harm = sum(deductions)
    return {"gross_credit": gross, "harm_total": harm, "net_credit": gross - harm,
            "maximum_credit": maximum, "primary_score": (gross - harm) / maximum}


# ---------------------------------------------------------------------------
# Rubric validation
# ---------------------------------------------------------------------------

def _keys(record: Any, keys: set[str], where: str) -> None:
    if not isinstance(record, dict):
        raise ValueError(f"{where} must be a mapping")
    if missing := keys - set(record):
        raise ValueError(f"{where} missing keys {sorted(missing)}")
    if extra := set(record) - keys:
        raise ValueError(f"{where} has unknown keys {sorted(extra)}")


def _name(value: Any, where: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where} must be a non-empty string")


def _leaf_kinds(predicate: dict[str, Any]) -> list[str]:
    if predicate["kind"] in ("all", "any"):
        return [k for child in predicate["predicates"] for k in _leaf_kinds(child)]
    return [predicate["kind"]]


def _leaves(predicate: dict[str, Any]) -> list[dict[str, Any]]:
    if predicate["kind"] in ("all", "any"):
        return [leaf for child in predicate["predicates"] for leaf in _leaves(child)]
    return [copy.deepcopy(predicate)]


def _timed_predicate(predicate: Any, timing: Any, where: str) -> None:
    if timing not in _TIMINGS:
        raise ValueError(f"{where}.timing must be one of {list(_TIMINGS)}, got {timing!r}")
    validate_predicate(predicate)
    kinds = _leaf_kinds(predicate)
    if timing == "event" and not all(k in _EVENT_CAPABLE_KINDS for k in kinds):
        raise ValueError(f"{where}: event timing needs event-capable kinds, got {kinds}")
    if timing == "final" and any(k in _EVENT_ONLY_KINDS for k in kinds):
        raise ValueError(f"{where}: final timing cannot use event-only kinds, got {kinds}")


def _objective_maxima(rubric: dict[str, Any]) -> dict[str, int]:
    return {o["id"]: max(r["points"] for r in o["rungs"]) for o in rubric["objectives"]}


def _group_maxima(rubric: dict[str, Any]) -> dict[str, float]:
    groups: dict[str, float] = {}
    for h in rubric["harms"]:
        groups[h["loss_key"]] = max(groups.get(h["loss_key"], 0), h["weight"])
    return groups


def validate_rubric_structure(rubric: dict[str, Any]) -> None:
    """Validate everything about `rubric` that needs no game state."""
    _keys(rubric, _RUBRIC_KEYS, "rubric")
    objectives, harms = rubric["objectives"], rubric["harms"]
    if not isinstance(objectives, list) or not objectives:
        raise ValueError("rubric.objectives must be a non-empty list")
    if not isinstance(harms, list):
        raise ValueError("rubric.harms must be a list")

    seen: set[str] = set()
    for oi, obj in enumerate(objectives):
        where = f"rubric.objectives[{oi}]"
        _keys(obj, _OBJECTIVE_KEYS, where)
        _name(obj["id"], f"{where}.id")
        if obj["id"] in seen:
            raise ValueError(f"duplicate objective id {obj['id']!r}")
        seen.add(obj["id"])
        rungs = obj["rungs"]
        if not isinstance(rungs, list) or not rungs:
            raise ValueError(f"{where}.rungs must be a non-empty list")
        for ri, rung in enumerate(rungs):
            rwhere = f"{where}.rungs[{ri}]"
            _keys(rung, _RUNG_KEYS, rwhere)
            if type(rung["points"]) is not int or rung["points"] <= 0:
                raise ValueError(f"{rwhere}.points must be a positive integer")
            validate_predicate(rung["predicate"])
    maxima = _objective_maxima(rubric)

    seen = set()
    priorities: dict[tuple[str, int], Any] = {}
    for hi, h in enumerate(harms):
        where = f"rubric.harms[{hi}]"
        _keys(h, _HARM_KEYS, where)
        _name(h["id"], f"{where}.id")
        if h["id"] in seen:
            raise ValueError(f"duplicate harm id {h['id']!r}")
        seen.add(h["id"])
        _name(h["loss_key"], f"{where}.loss_key")
        if h["objective_id"] not in maxima:
            raise ValueError(f"{where}.objective_id references unknown objective "
                             f"{h['objective_id']!r}")
        if not _is_number(h["weight"]) or h["weight"] <= 0:
            raise ValueError(f"{where}.weight must be a finite positive number")
        if not isinstance(h["weight_reason"], str):
            raise ValueError(f"{where}.weight_reason must be a string")
        if h["weight"] != maxima[h["objective_id"]] and not h["weight_reason"]:
            raise ValueError(f"{where}.weight_reason is required when weight differs "
                             f"from the objective maximum")
        if type(h["priority"]) is not int:
            raise ValueError(f"{where}.priority must be an integer")
        _timed_predicate(h["predicate"], h["timing"], where)
        if not isinstance(h["compensation"], list):
            raise ValueError(f"{where}.compensation must be a list")
        for ci, comp in enumerate(h["compensation"]):
            cwhere = f"{where}.compensation[{ci}]"
            _keys(comp, _COMPENSATION_KEYS, cwhere)
            _timed_predicate(comp["predicate"], comp["timing"], cwhere)
            if comp["timing"] == "event" and h["timing"] != "event":
                raise ValueError(f"{cwhere}: event compensation needs an event-timed harm")
        slot = (h["loss_key"], h["priority"])
        if slot in priorities and priorities[slot] != h["weight"]:
            raise ValueError(f"{where}: loss_key {h['loss_key']!r} has equal priority "
                             f"{h['priority']} with different weights")
        priorities[slot] = h["weight"]

    maximum_credit = sum(maxima.values())
    maximum_harm = sum(_group_maxima(rubric).values())
    if maximum_harm > maximum_credit:
        raise ValueError(f"declared harm maximum {maximum_harm} exceeds maximum credit "
                         f"{maximum_credit}")


def validate_rubric(rubric: dict[str, Any], initial: dict[str, Any]) -> None:
    """Structure plus: no positive rung may already hold at the initial state."""
    validate_rubric_structure(rubric)
    for obj in rubric["objectives"]:
        for ri, rung in enumerate(obj["rungs"]):
            if evaluate_predicate(rung["predicate"], initial=initial, final=initial):
                raise ValueError(f"objective {obj['id']!r} rung {ri} is initially true")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _objective_credit(obj: dict[str, Any], initial: dict[str, Any],
                      final: dict[str, Any]) -> tuple[int, int | None]:
    best, index = 0, None
    for ri, rung in enumerate(obj["rungs"]):
        # Every rung is evaluated so missing evidence surfaces regardless of order.
        if evaluate_predicate(rung["predicate"], initial=initial, final=final) \
                and rung["points"] > best:
            best, index = rung["points"], ri
    return best, index


def _on_step(predicate: dict[str, Any], step: dict[str, Any]) -> bool:
    return evaluate_predicate(predicate, initial=step["state_before"],
                              final=step["state_after"], transition=step)


def _harm_record(h: dict[str, Any], trial: dict[str, Any]) -> dict[str, Any]:
    initial, final = trial["initial_state"], trial["final_state"]
    fired_steps: list[Any] = []
    firing: dict[str, Any] | None = None
    if h["timing"] == "event":
        for step in trial["steps"]:
            if _on_step(h["predicate"], step):
                fired_steps.append(step["idx"])
                firing = firing or step
        fired = firing is not None
    else:
        fired = evaluate_predicate(h["predicate"], initial=initial, final=final)

    compensation = []
    if fired:
        for comp in h["compensation"]:
            if comp["timing"] == "event":
                satisfied = _on_step(comp["predicate"], firing)
                at = firing["idx"]
            else:
                satisfied = evaluate_predicate(comp["predicate"], initial=initial, final=final)
                at = None
            compensation.append({"timing": comp["timing"],
                                 "predicate": copy.deepcopy(comp["predicate"]),
                                 "satisfied": satisfied, "step": at})
    if not fired:
        status = "not_fired"
    elif any(c["satisfied"] for c in compensation):
        status = "compensated"
    else:
        status = "fired"  # resolved to charged/deduplicated below
    return {"id": h["id"], "loss_key": h["loss_key"], "objective_id": h["objective_id"],
            "timing": h["timing"], "weight": h["weight"], "priority": h["priority"],
            "reference": _leaves(h["predicate"]), "fired": fired,
            "first_step": firing["idx"] if firing is not None else None,
            "fired_steps": fired_steps, "compensation": compensation,
            "status": status, "deduction": 0, "charged_harm": None}


def _attainable(rubric: dict[str, Any], maximum: int) -> list[float]:
    credits = {0}
    for obj in rubric["objectives"]:
        options = {0} | {r["points"] for r in obj["rungs"]}
        credits = {c + o for c in credits for o in options}
    harms = {0}
    by_group: dict[str, set[Any]] = {}
    for h in rubric["harms"]:
        by_group.setdefault(h["loss_key"], {0}).add(h["weight"])
    for options in by_group.values():
        harms = {a + b for a in harms for b in options}
    return sorted({(c - d) / maximum for c in credits for d in harms})


def _scales(rubric: dict[str, Any]) -> dict[str, Any]:
    maxima = _objective_maxima(rubric)
    maximum = sum(maxima.values())
    points = {r["points"] for o in rubric["objectives"] for r in o["rungs"]}
    return {"maximum_credit": maximum,
            "maximum_harm": sum(_group_maxima(rubric).values()),
            "smallest_increment": (2 if 2 in points else min(points)) / maximum,
            "one_objective": max(maxima.values()) / maximum,
            "attainable_values": _attainable(rubric, maximum)}


def score_trial(trial: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
    """Signed endpoint credit minus deduplicated harm, from recorded evidence."""
    initial, final = trial["initial_state"], trial["final_state"]
    validate_rubric(rubric, initial)

    scales_maxima = _objective_maxima(rubric)
    objectives = []
    for obj in rubric["objectives"]:
        credit, index = _objective_credit(obj, initial, final)
        objectives.append({
            "id": obj["id"], "credit": credit, "maximum": scales_maxima[obj["id"]],
            "rung_index": index,
            "predicate": copy.deepcopy(obj["rungs"][index]["predicate"])
            if index is not None else None,
            "rungs": [{"points": r["points"], "predicate": copy.deepcopy(r["predicate"])}
                      for r in obj["rungs"]],
        })

    records = [_harm_record(h, trial) for h in rubric["harms"]]
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record["fired"]:
            groups.setdefault(record["loss_key"], []).append(record)
    for group in groups.values():
        # Precedence is resolved over every fired member before compensation:
        # the highest priority wins (equal-priority members share one weight,
        # validated, so declaration order breaks the tie harmlessly) and is
        # charged only when uncompensated; the rest point at the winner.
        winner = max(group, key=lambda r: r["priority"])
        for record in group:
            if record is not winner:
                record["status"], record["charged_harm"] = "deduplicated", winner["id"]
            elif record["status"] == "fired":
                record["status"], record["deduction"] = "charged", record["weight"]

    scales = _scales(rubric)
    totals = signed_totals([o["credit"] for o in objectives],
                           [r["deduction"] for r in records if r["status"] == "charged"],
                           scales["maximum_credit"])
    return {"objectives": objectives, "harms": records, **totals,
            "maximum_harm": scales["maximum_harm"], "scales": scales}


def attribute_progress(trial: dict[str, Any], rubric: dict[str, Any]) -> list[dict[str, Any]]:
    """Steps whose recorded mutation raised an objective's attained credit.

    Audit-only: never aggregated into the primary score.
    """
    initial = trial["initial_state"]
    validate_rubric(rubric, initial)
    out = []
    for step in trial["steps"]:
        for obj in rubric["objectives"]:
            before, _ = _objective_credit(obj, initial, step["state_before"])
            after, _ = _objective_credit(obj, initial, step["state_after"])
            if after > before:
                out.append({"step": step["idx"], "objective_id": obj["id"],
                            "credit_before": before, "credit_after": after})
    return out
