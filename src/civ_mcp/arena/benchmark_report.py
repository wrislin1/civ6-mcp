"""Derived scoring and deterministic reports for the controlled-position
benchmark runner.

This module is a pure evidence-to-report transform. It reads exactly three
kinds of committed evidence from a run directory --

    <run_dir>/session.json     immutable lock: fingerprints + per-position rubric
    <run_dir>/schedule.json    the ordered (index, position_id, ...) schedule
    <run_dir>/trials/*.json    immutable raw scoreable evidence per trial

-- and derives every score, aggregate, and rendered report from them. It
NEVER reads `<run_dir>/attempts/`: infrastructure-attempt records are not
scoreable evidence (see `benchmark_store`'s module docstring), and scanning
them here would let retry noise leak into a report that is supposed to be a
pure function of committed trials.

Scoring reuses `action_metrics.evaluate_predicate` (and
`classify_action_quality`) verbatim. This module deliberately does not
reimplement predicate semantics: an unknown predicate ``kind`` or an
unresolvable typed ``path`` raises `action_metrics.PredicateError`, and a
structurally broken rubric raises `MalformedRubricError`. Both ABORT report
generation -- neither is ever caught here to silently score a task zero,
which would misreport "couldn't tell" as "did not happen".

Each task is scored at the highest rubric level whose predicate is
satisfied (`score_rubric`). Each position is normalized to [0, 1]
independently of every other position, and the aggregate is the equal-weight
mean of each position's median normalized score across its trials --
so one strong position can never hide a regression in another, and a
position with more scheduled trials is never weighted more heavily than one
with fewer.

`report.json` is written as canonical JSON (sorted keys, no incidental
whitespace); `report.md` is rendered purely from that same mapping.
Regenerating a report from an unchanged run directory is byte-identical --
there is no wall-clock timestamp or other non-deterministic value anywhere
in the output.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from civ_mcp.arena.action_metrics import (
    PredicateError,
    classify_action_quality,
    evaluate_predicate,
)
from civ_mcp.arena.benchmark_manifest import fingerprint
from civ_mcp.arena.benchmark_store import trial_filename

__all__ = [
    "MalformedRubricError",
    "ReportError",
    "score_rubric",
    "score_trial",
    "build_report",
    "render_markdown",
    "write_reports",
    "main",
]

# Literal, human-checkable proof that scoring reuses the shared evaluator
# rather than a private reimplementation -- surfaced verbatim in every
# report under report["scorer"]["evaluator"].
_EVALUATOR_QUALNAME = "civ_mcp.arena.action_metrics.evaluate_predicate"

# USD per 1k tokens (prompt, completion), fixed at module scope so a report
# never depends on process environment -- byte-identical regeneration must
# hold regardless of what CIV_ARENA_*_PROMPT_USD_PER_1K happens to be set to
# in whichever shell runs the CLI. Extend this table as real provider
# pricing is confirmed; unknown models default to free (0.0, 0.0).
_PRICE_PER_1K_USD: dict[str, tuple[float, float]] = {
    "local": (0.0, 0.0),
}


class ReportError(Exception):
    """Base class for a report-generation abort. Never caught anywhere in
    this module to fall back to a zero/partial score -- see module
    docstring."""


class MalformedRubricError(ReportError):
    """A rubric (or one of its tasks/levels) is not shaped as this module
    requires. Raised instead of skipping the task or scoring it zero."""


def _canonical_bytes(value: object) -> bytes:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return encoded.encode("utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# score_rubric
# ---------------------------------------------------------------------------


def _validate_rubric_shape(rubric: object) -> Sequence[Mapping[str, object]]:
    if not isinstance(rubric, Sequence) or isinstance(rubric, (str, bytes)):
        raise MalformedRubricError(f"rubric must be a list of tasks, got {type(rubric).__name__}")
    tasks: list[Mapping[str, object]] = []
    seen_task_ids: set[str] = set()
    for task in rubric:
        if not isinstance(task, Mapping):
            raise MalformedRubricError(f"rubric task must be a mapping, got {type(task).__name__}")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise MalformedRubricError(f"rubric task is missing a non-empty 'task_id': {task!r}")
        if task_id in seen_task_ids:
            raise MalformedRubricError(f"rubric has a duplicate task_id: {task_id!r}")
        seen_task_ids.add(task_id)
        levels = task.get("levels")
        if not isinstance(levels, Sequence) or isinstance(levels, (str, bytes)) or not levels:
            raise MalformedRubricError(
                f"rubric task {task_id!r} must have a non-empty 'levels' list"
            )
        for level in levels:
            if not isinstance(level, Mapping):
                raise MalformedRubricError(
                    f"rubric task {task_id!r} has a level that is not a mapping: {level!r}"
                )
            if "score" not in level:
                raise MalformedRubricError(
                    f"rubric task {task_id!r} has a level missing 'score': {level!r}"
                )
            score = level["score"]
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                raise MalformedRubricError(
                    f"rubric task {task_id!r} has a non-numeric level score: {score!r}"
                )
            if "predicate" not in level:
                raise MalformedRubricError(
                    f"rubric task {task_id!r} has a level missing 'predicate': {level!r}"
                )
        tasks.append(task)
    return tasks


def score_rubric(
    rubric: object,
    *,
    initial_state: object = None,
    final_state: object = None,
    steps: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Score one position's rubric against one trial's raw evidence.

    Each task is scored at the highest level whose predicate is satisfied
    (`action_metrics.evaluate_predicate`, called verbatim -- this function
    never reimplements predicate semantics). A task with no satisfied level
    scores 0 for that task (a legitimate "no progress" outcome, distinct
    from an abort).

    Raises `MalformedRubricError` for a structurally broken rubric, and lets
    `action_metrics.PredicateError` propagate unchanged for an unknown
    predicate ``kind`` or an unresolvable typed ``path`` -- both abort
    report generation rather than silently scoring the task zero.
    """
    tasks = _validate_rubric_shape(rubric)

    task_scores: dict[str, dict[str, object]] = {}
    raw_total = 0.0
    max_total = 0.0
    for task in tasks:
        task_id = task["task_id"]
        levels = task["levels"]
        max_level_score = max(level["score"] for level in levels)
        satisfied_scores = [
            level["score"]
            for level in levels
            if evaluate_predicate(
                level["predicate"],
                initial_state=initial_state,
                final_state=final_state,
                steps=steps,
            )
        ]
        achieved = max(satisfied_scores) if satisfied_scores else 0
        task_scores[task_id] = {"score": achieved, "max_score": max_level_score}
        raw_total += achieved
        max_total += max_level_score

    normalized = (raw_total / max_total) if max_total else 0.0
    return {
        "tasks": task_scores,
        "raw_total": raw_total,
        "max_total": max_total,
        "normalized": normalized,
    }


# ---------------------------------------------------------------------------
# score_trial
# ---------------------------------------------------------------------------


def score_trial(
    trial: Mapping[str, object],
    rubric: object,
    *,
    objectives: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Score one raw committed trial against one position's rubric.

    Every non-scorer field on the returned record (`attempt_count`,
    `terminal`, `seed`, `model`, `arm_id`, `pair_id`, `wall_clock_s`,
    `prompt_tokens`, `completion_tokens`) is copied verbatim from the raw
    trial -- never re-derived -- so retry counts, terminal conditions, and
    latency/token evidence in a report always trace back to exactly what
    the runner committed. `rubric_score` / `passed` / any other
    scorer-shaped field a raw trial might carry (it never should, but this
    function does not trust that) is ignored entirely: predicate truth is
    derived solely from `steps`, `initial_state`, and `final_state`.
    """
    rubric_score = score_rubric(
        rubric,
        initial_state=trial.get("initial_state"),
        final_state=trial.get("final_state"),
        steps=trial.get("steps") or (),
    )
    action_quality = classify_action_quality(
        steps=trial.get("steps") or (),
        invalid_tool_calls=trial.get("invalid_tool_calls") or (),
        objectives=objectives,
    )
    return {
        "index": trial.get("index"),
        "position_id": trial.get("position_id"),
        "terminal": trial.get("terminal"),
        "attempt_count": trial.get("attempt_count"),
        "seed": trial.get("seed"),
        "model": trial.get("model"),
        "arm_id": trial.get("arm_id"),
        "pair_id": trial.get("pair_id"),
        "wall_clock_s": trial.get("wall_clock_s"),
        "prompt_tokens": trial.get("prompt_tokens"),
        "completion_tokens": trial.get("completion_tokens"),
        "rubric": rubric_score,
        "action_quality": action_quality,
    }


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


def _usd_cost(model: object, prompt_tokens: object, completion_tokens: object) -> float | None:
    """USD cost for one trial, or `None` if `model` has no price-table entry.

    G13: `.get(str(model), (0.0, 0.0))` conflated "this model is free" (a
    real priced entry of (0.0, 0.0), e.g. "local") with "no price data
    exists for this model" -- both produced an identical $0.00
    contribution, so a $0 total could mean either and there was no way to
    tell which. `None` here (and `unpriced_models` in `_group_summary`)
    makes that distinction visible instead of silently wrong.
    """
    key = str(model)
    if key not in _PRICE_PER_1K_USD:
        return None
    pin, pout = _PRICE_PER_1K_USD[key]
    pt = prompt_tokens if isinstance(prompt_tokens, (int, float)) else 0
    ct = completion_tokens if isinstance(completion_tokens, (int, float)) else 0
    return round(pt / 1000 * pin + ct / 1000 * pout, 6)


def _group_label(model: object, arm_id: object) -> str:
    """Key identifying one (model, arm) group within a position. Trials
    with no model/arm recorded (single-endpoint fixtures/runs) all fall
    into one "unknown::unknown" group, which is exactly one group -- so a
    single-model/single-arm run's per-group summary is numerically
    identical to the old pooled-position summary."""
    model_label = str(model) if model is not None else "unknown"
    arm_label = str(arm_id) if arm_id is not None else "unknown"
    return f"{model_label}::{arm_label}"


def _group_summary(scored: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Per-(model, arm)-group scoring/evidence summary within one position.
    This is the unit F1 requires: pooling across models/arms here would
    hide a per-model regression behind an unrelated model's strong score,
    and mixing A/B arms here would corrupt the exact comparison the
    calibration section exists to make."""
    normalized_scores = [s["rubric"]["normalized"] for s in scored]
    raw_scores = [s["rubric"]["raw_total"] for s in scored]
    attempt_counts = [s["attempt_count"] for s in scored if isinstance(s["attempt_count"], (int, float))]

    terminal_conditions: dict[str, int] = {}
    for s in scored:
        terminal = s.get("terminal")
        key = str(terminal) if terminal is not None else "unknown"
        terminal_conditions[key] = terminal_conditions.get(key, 0) + 1

    seeds = sorted(
        {s["seed"] for s in scored if s.get("seed") is not None},
        key=lambda v: (str(type(v)), v),
    )
    models = sorted({str(s["model"]) for s in scored if s.get("model") is not None})
    arms = sorted({str(s["arm_id"]) for s in scored if s.get("arm_id") is not None})

    latencies = [s["wall_clock_s"] for s in scored if isinstance(s.get("wall_clock_s"), (int, float))]
    prompt_tokens = [s["prompt_tokens"] for s in scored if isinstance(s.get("prompt_tokens"), (int, float))]
    completion_tokens = [
        s["completion_tokens"] for s in scored if isinstance(s.get("completion_tokens"), (int, float))
    ]
    # G13: aggregate USD totals over priced trials only; a model with no
    # price-table entry contributes nothing to usd_total/usd_mean and is
    # named in unpriced_models instead of silently reading as free.
    priced_costs: list[float] = []
    unpriced_models: set[str] = set()
    for s in scored:
        cost = _usd_cost(s.get("model"), s.get("prompt_tokens"), s.get("completion_tokens"))
        if cost is None:
            unpriced_models.add(str(s.get("model")) if s.get("model") is not None else "unknown")
        else:
            priced_costs.append(cost)

    action_quality_totals: dict[str, object] = {
        "invalid_calls": 0,
        "domain_rejections": 0,
        "successful_mutations": 0,
        "repetitions": 0,
        "useful_actions": 0,
    }
    any_useful_available = False
    any_digest_available = False
    for s in scored:
        aq = s["action_quality"]
        action_quality_totals["invalid_calls"] += aq["invalid_calls"]
        action_quality_totals["domain_rejections"] += aq["domain_rejections"]
        # classify_action_quality reports the digest-dependent counts as None
        # when a trial's steps carry no state-digest fields at all (a
        # hand-authored/migrated trial file) -- an unavailable measurement,
        # not a 0. Sum only measured trials, mirroring useful_actions.
        if aq["successful_mutations"] is not None:
            any_digest_available = True
            action_quality_totals["successful_mutations"] += aq["successful_mutations"]
            action_quality_totals["repetitions"] += aq["repetitions"]
        if aq["useful_actions"] is not None:
            any_useful_available = True
            action_quality_totals["useful_actions"] += aq["useful_actions"]
    if not any_digest_available:
        action_quality_totals["successful_mutations"] = None
        action_quality_totals["repetitions"] = None
    if not any_useful_available:
        action_quality_totals["useful_actions"] = None
        action_quality_totals["useful_action_coverage"] = "unavailable"
    else:
        action_quality_totals["useful_action_coverage"] = "objective_verified"

    return {
        "trial_count": len(scored),
        "trials": [
            {
                "index": s["index"],
                "attempt_count": s["attempt_count"],
                "terminal": s["terminal"],
                "seed": s["seed"],
                "model": s["model"],
                "arm_id": s["arm_id"],
                "pair_id": s["pair_id"],
                "rubric": s["rubric"],
                "action_quality": s["action_quality"],
            }
            for s in scored
        ],
        "rubric": {
            "normalized_median": statistics.median(normalized_scores) if normalized_scores else 0.0,
            "raw_median": statistics.median(raw_scores) if raw_scores else 0.0,
        },
        "terminal_conditions": terminal_conditions,
        "attempts": {
            "total_attempt_count": sum(attempt_counts),
            "max_attempt_count": max(attempt_counts) if attempt_counts else 0,
            "trials_with_retries": sum(1 for c in attempt_counts if c > 1),
        },
        "seeds": {"distinct": seeds, "count": len(seeds)},
        "endpoint_topology": {"models": models, "arms": arms},
        "latency": {
            "count": len(latencies),
            "mean_s": (sum(latencies) / len(latencies)) if latencies else None,
            "median_s": statistics.median(latencies) if latencies else None,
            "max_s": max(latencies) if latencies else None,
        },
        "tokens": {
            "prompt_total": sum(prompt_tokens),
            "completion_total": sum(completion_tokens),
            "prompt_mean": (sum(prompt_tokens) / len(prompt_tokens)) if prompt_tokens else 0.0,
            "completion_mean": (sum(completion_tokens) / len(completion_tokens)) if completion_tokens else 0.0,
        },
        "cost": {
            "usd_total": round(sum(priced_costs), 6),
            "usd_mean": round(sum(priced_costs) / len(priced_costs), 6) if priced_costs else 0.0,
            "unpriced_models": sorted(unpriced_models),
        },
        "action_quality": action_quality_totals,
    }


def _position_summary(position_id: str, scored: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """One position's report section: `trial_count` is the raw total across
    every model/arm scheduled at this position (informational only -- it is
    never used to compute a score), and `by_group` holds one `_group_summary`
    per distinct (model, arm) pair actually observed at this position. F1:
    a model/arm screen must be able to read a per-model, per-arm score for
    each position -- a single pooled median across every model and arm would
    hide a regression in one model behind another model's strong score, and
    would corrupt A/B arm comparisons by mixing the two arms' trials."""
    groups: dict[str, list[Mapping[str, object]]] = {}
    for s in scored:
        label = _group_label(s.get("model"), s.get("arm_id"))
        groups.setdefault(label, []).append(s)
    return {
        "trial_count": len(scored),
        "by_group": {label: _group_summary(group_scored) for label, group_scored in groups.items()},
    }


def _declared_arm_order(schedule_trials: Sequence[Mapping[str, object]]) -> tuple[str, str] | None:
    """The suite's declared two-arm order (arms[0]=baseline, arms[1]=treatment),
    derived from `schedule.json` alone (this module never reads the suite
    manifest). `compile_schedule` always emits the first (position, model)
    group's first seed-block in unreversed `suite.arms` order (index 1 is
    arms[0], index 2 is arms[1]) -- every later block may reverse for ABBA
    balancing, but the very first two distinct arm_ids encountered walking
    the schedule in ascending index order are always arms[0] then arms[1].
    Returns `None` if fewer than two distinct arm_ids appear at all.
    """
    ordered_entries = sorted(
        (e for e in schedule_trials if isinstance(e, Mapping) and e.get("index") is not None),
        key=lambda e: e["index"],
    )
    seen: list[str] = []
    for entry in ordered_entries:
        arm_id = entry.get("arm_id")
        if arm_id is None:
            continue
        arm_id = str(arm_id)
        if arm_id not in seen:
            seen.append(arm_id)
        if len(seen) == 2:
            return (seen[0], seen[1])
    return None


def _calibration_section(
    scored_by_pair: Mapping[str, list[Mapping[str, object]]],
    arm_order: tuple[str, str] | None,
) -> dict[str, object] | None:
    """Pairs trials that share a `pair_id` from `schedule.json` and compare
    two distinct arms' raw rubric scores. Ties count for neither arm (design
    doc: "ties count as non-wins"). Pairs with anything other than exactly
    two distinct-arm trials are skipped (an in-progress/partial run).

    Two delta statistics are reported: `median_abs_delta` (the magnitude of
    each pair's score difference, blind to which arm won -- feeds "how far
    apart do the arms land") and `median_signed_delta` (each pair's delta
    oriented as second-arm-minus-first-arm in `arm_order`, i.e.
    treatment-minus-baseline when `arm_order` is (baseline, treatment) --
    this is the statistic the preregistered "median paired improvement >= 4"
    rule actually needs, since a pair the baseline wins must pull it DOWN,
    not up). A pair whose two arm_ids don't both appear in `arm_order`
    (more than two arms declared in the suite) contributes to
    `median_abs_delta` but is excluded from the signed statistic, since its
    orientation is undefined.
    """
    pairs: list[dict[str, object]] = []
    win_counts: dict[str, int] = {}
    tie_count = 0
    abs_deltas: list[float] = []
    signed_deltas: list[float] = []

    for pair_id in sorted(scored_by_pair):
        members = scored_by_pair[pair_id]
        if len(members) != 2:
            continue
        arm_ids = [m.get("arm_id") for m in members]
        if any(a is None for a in arm_ids) or arm_ids[0] == arm_ids[1]:
            continue
        (arm_a, score_a), (arm_b, score_b) = (
            (members[0]["arm_id"], members[0]["rubric"]["raw_total"]),
            (members[1]["arm_id"], members[1]["rubric"]["raw_total"]),
        )
        abs_delta = abs(score_a - score_b)
        abs_deltas.append(abs_delta)

        signed_delta = None
        if arm_order is not None and {str(arm_a), str(arm_b)} == set(arm_order):
            baseline_id, treatment_id = arm_order
            by_arm = {str(arm_a): score_a, str(arm_b): score_b}
            signed_delta = by_arm[treatment_id] - by_arm[baseline_id]
            signed_deltas.append(signed_delta)

        if score_a == score_b:
            tie_count += 1
            winner = None
        elif score_a > score_b:
            winner = arm_a
        else:
            winner = arm_b
        if winner is not None:
            win_counts[winner] = win_counts.get(winner, 0) + 1
        pairs.append(
            {
                "pair_id": pair_id,
                "scores": {str(arm_a): score_a, str(arm_b): score_b},
                "winner": winner,
                "delta": abs_delta,
                "signed_delta": signed_delta,
            }
        )

    if not pairs:
        return None

    return {
        "pairs": pairs,
        "win_counts": win_counts,
        "tie_count": tie_count,
        "pair_count": len(pairs),
        "median_abs_delta": statistics.median(abs_deltas) if abs_deltas else 0,
        "median_signed_delta": statistics.median(signed_deltas) if signed_deltas else 0,
    }


def _completeness_section(
    *,
    positions_lock: Mapping[str, object],
    expected_by_position: Mapping[str, int],
    committed_by_position: Mapping[str, int],
) -> dict[str, object]:
    """Expected-vs-committed trial counts per position, covering every
    position declared in `session.json["positions"]` and/or scheduled in
    `schedule.json` -- including a position with zero committed trials,
    which must never simply be absent from the report. Absence there would
    be indistinguishable from "scored 0", which is exactly the aggregate-
    hiding failure mode this section exists to prevent.
    """
    all_position_ids = set(positions_lock) | set(expected_by_position) | set(committed_by_position)
    by_position = {
        pid: {
            "expected": expected_by_position.get(pid, 0),
            "committed": committed_by_position.get(pid, 0),
        }
        for pid in sorted(str(p) for p in all_position_ids)
    }
    positions_missing = sorted(pid for pid, counts in by_position.items() if counts["committed"] == 0)
    return {"by_position": by_position, "positions_missing": positions_missing}


# ---------------------------------------------------------------------------
# session.json canonical schema
# ---------------------------------------------------------------------------
#
# This module is the only consumer of `<run_dir>/session.json`, so its
# reading contract here IS the canonical schema every writer of session.json
# must satisfy -- `benchmark_gates.build_session_lock` and the
# `civ-arena-benchmark --ungated-smoke` CLI lock construction both cite this
# block rather than duplicating it. `build_report` requires, at minimum:
#
#     {
#       "scorer_fingerprint": "<non-empty str>",
#       "positions": {
#         "<position_id>": {
#           "rubric": [...],       # a benchmark_report.score_rubric-shaped rubric
#           "objectives": [...],   # optional; defaults to () if absent
#         },
#         ...
#       },
#       "session_fingerprint": "<non-empty str>",  # required (G8: BenchmarkStore
#                                                    # refuses to create/open a run
#                                                    # without one); echoed into the
#                                                    # report and cross-checked
#                                                    # against every trial's own stamp
#       "campaign_fingerprint": "<non-empty str>",  # REQUIRED whenever
#                                                    # "ungated_smoke" is not
#                                                    # true (Task 3/4, round-1
#                                                    # review): under Plan 2
#                                                    # every non-smoke session
#                                                    # IS a dual-stamped
#                                                    # counted campaign block
#                                                    # -- there is no
#                                                    # legitimate non-smoke,
#                                                    # non-campaign lock.
#                                                    # Absent for an
#                                                    # --ungated-smoke lock.
#                                                    # When required, cross-
#                                                    # checked against every
#                                                    # trial's own stamp
#                                                    # exactly like
#                                                    # session_fingerprint
#                                                    # above.
#       "ungated_smoke": <bool>,          # optional; defaults to False
#       ...                                # any other writer-specific evidence is ignored here
#     }
#
# A session.json missing `scorer_fingerprint`, missing `positions` entirely,
# or missing a `positions[<position_id>]["rubric"]` for any position a
# committed trial references, aborts report generation (`ReportError`) --
# this module never silently treats absent rubric evidence as "score 0".
# G8: whenever session.json carries a session_fingerprint, a committed trial
# with no session_fingerprint stamp of its own is ALSO a hard ReportError --
# not merely a mismatch check that no-ops when either side is missing. A
# session.json whose "ungated_smoke" is not true MUST also carry a non-empty
# "campaign_fingerprint" (and every committed trial must be stamped to
# match) -- a non-smoke lock missing campaign_fingerprint entirely is itself
# a hard ReportError, not a silent pass (see Task 3/4).


def build_report(run_dir: str | Path) -> dict[str, object]:
    """Derive a full report purely from `<run_dir>/session.json`,
    `<run_dir>/schedule.json`, and `<run_dir>/trials/*.json`. Never reads
    `<run_dir>/attempts/` -- see module docstring.

    `session.json` must satisfy the canonical schema documented in the
    comment block immediately above this function.

    Raises `MalformedRubricError` / `action_metrics.PredicateError` /
    `ReportError` for any structural or predicate problem, aborting report
    generation rather than silently under-scoring a task.
    """
    run_dir = Path(run_dir)
    lock = _read_json(run_dir / "session.json")
    if not isinstance(lock, Mapping):
        raise ReportError(f"session.json must be a JSON object, got {type(lock).__name__}")

    scorer_fingerprint = lock.get("scorer_fingerprint")
    if not scorer_fingerprint:
        raise ReportError("session.json is missing a non-empty 'scorer_fingerprint'")

    positions_lock = lock.get("positions")
    if not isinstance(positions_lock, Mapping):
        raise ReportError("session.json is missing a 'positions' mapping")

    # Computed once, up front, so both the per-trial campaign_fingerprint
    # cross-check below and the `report["session"]` echo at the bottom of
    # this function agree on the same value.
    ungated_smoke = bool(lock.get("ungated_smoke", False))

    schedule = _read_json(run_dir / "schedule.json")
    schedule_trials = schedule.get("trials") if isinstance(schedule, Mapping) else None
    if not isinstance(schedule_trials, Sequence):
        raise ReportError("schedule.json is missing a 'trials' list")

    # G11: the runner re-verifies schedule.json against session.json's
    # schedule_fingerprint on resume (see benchmark_runner._run_async) --
    # build_report read schedule.json completely unverified, so a swapped
    # arm order (or any other post-hoc edit) would silently flip
    # calibration deltas. Recompute the fingerprint exactly as the runner
    # does (same `fingerprint` helper, over the whole parsed schedule.json
    # mapping) and hard-fail on a mismatch. Only activates when the lock
    # actually declares a schedule_fingerprint.
    lock_schedule_fingerprint = lock.get("schedule_fingerprint")
    if lock_schedule_fingerprint:
        actual_schedule_fingerprint = fingerprint(schedule)
        if actual_schedule_fingerprint != lock_schedule_fingerprint:
            raise ReportError(
                "schedule.json does not match session.json's schedule_fingerprint "
                f"(expected {lock_schedule_fingerprint!r}, found "
                f"{actual_schedule_fingerprint!r}) -- refusing to score a run whose "
                "schedule may have been tampered with"
            )

    scored_by_position: dict[str, list[dict[str, object]]] = {}
    scored_by_pair: dict[str, list[dict[str, object]]] = {}
    # Completeness bookkeeping: expected-vs-committed per position, computed
    # from the schedule itself so a position that was scheduled but never
    # produced a single committed trial still shows up in the report as
    # "never ran" -- distinct from a position that ran and scored 0. See
    # `_completeness_section`.
    expected_by_position: dict[str, int] = {}
    committed_by_position: dict[str, int] = {}

    for entry in schedule_trials:
        if not isinstance(entry, Mapping):
            raise ReportError(f"schedule.json trial entry must be a mapping, got {entry!r}")
        index = entry.get("index")
        position_id = entry.get("position_id")
        if index is None or position_id is None:
            raise ReportError(f"schedule.json trial entry missing 'index'/'position_id': {entry!r}")
        position_id = str(position_id)
        expected_by_position[position_id] = expected_by_position.get(position_id, 0) + 1

        trial_path = run_dir / "trials" / trial_filename(index)
        if not trial_path.exists():
            # Not yet committed -- a report over an in-progress run only
            # covers what has actually been committed so far.
            continue
        committed_by_position[position_id] = committed_by_position.get(position_id, 0) + 1
        trial = _read_json(trial_path)
        if not isinstance(trial, Mapping):
            raise ReportError(f"{trial_path} must be a JSON object, got {type(trial).__name__}")

        # F8/G8: a stale/copied trial-NNN.json is indistinguishable from
        # current-lock evidence by filename alone. session_fingerprint is
        # a required session.json field (BenchmarkStore refuses to create
        # or open a run without one, see benchmark_store.G8) -- so whenever
        # the lock carries one, every trial must be stamped and must
        # agree. The old `lock_fp and trial_fp and ...` check was a no-op
        # whenever either side was missing/falsy, which let an unstamped
        # trial under a stamped lock pass silently; a missing stamp is now
        # itself a hard failure, not just a mismatch.
        lock_session_fingerprint = lock.get("session_fingerprint")
        trial_session_fingerprint = trial.get("session_fingerprint")
        if lock_session_fingerprint:
            if not trial_session_fingerprint:
                raise ReportError(
                    f"{trial_path}: has no session_fingerprint stamp, but session.json "
                    f"carries one ({lock_session_fingerprint!r}) -- refusing to score "
                    "unstamped evidence under a stamped lock"
                )
            if trial_session_fingerprint != lock_session_fingerprint:
                raise ReportError(
                    f"{trial_path}: session_fingerprint {trial_session_fingerprint!r} does not "
                    f"match session.json's session_fingerprint {lock_session_fingerprint!r} -- "
                    "refusing to score evidence that does not belong to this session's current lock"
                )

        # Counted-campaign provenance (Task 3/4, tightened per round-1
        # review): the SECOND stamp a counted block's trials carry alongside
        # session_fingerprint (see BenchmarkStore.is_trial_complete /
        # BenchmarkRunner._finalize_trial). Under Plan 2 there is no
        # legitimate non-smoke, non-campaign session -- evidence is either
        # explicitly `ungated_smoke: true` (benchmark_runner._run_async
        # always stamps this) or dual-stamped counted (build_campaign_lock /
        # build_session_lock always stamp both fingerprints together). So
        # this activates on the SAME condition as "is this evidence counted
        # at all" -- `not ungated_smoke` -- not merely "the lock happens to
        # declare a campaign_fingerprint". A lock that is not explicitly
        # smoke but is ALSO missing campaign_fingerprint entirely (a stale
        # or hand-crafted session.json) is exactly the ambiguous case this
        # must fail closed on, not silently treat as smoke-shaped evidence.
        if not ungated_smoke:
            lock_campaign_fingerprint = lock.get("campaign_fingerprint")
            if not lock_campaign_fingerprint:
                raise ReportError(
                    "session.json is not marked ungated_smoke, but is missing a "
                    "non-empty 'campaign_fingerprint' -- under Plan 2 every non-smoke "
                    "session must be a dual-stamped counted campaign block; refusing "
                    "to score ambiguous evidence that is neither"
                )
            trial_campaign_fingerprint = trial.get("campaign_fingerprint")
            if not trial_campaign_fingerprint:
                raise ReportError(
                    f"{trial_path}: has no campaign_fingerprint stamp, but session.json "
                    f"carries one ({lock_campaign_fingerprint!r}) -- refusing to score "
                    "unstamped counted evidence under a stamped campaign lock"
                )
            if trial_campaign_fingerprint != lock_campaign_fingerprint:
                raise ReportError(
                    f"{trial_path}: campaign_fingerprint {trial_campaign_fingerprint!r} does not "
                    f"match session.json's campaign_fingerprint {lock_campaign_fingerprint!r} -- "
                    "refusing to score evidence that does not belong to this counted "
                    "campaign block's current lock"
                )

        position_lock = positions_lock.get(position_id)
        if not isinstance(position_lock, Mapping) or "rubric" not in position_lock:
            raise ReportError(
                f"session.json has no rubric recorded for position {position_id!r}"
            )
        rubric = position_lock["rubric"]
        objectives = position_lock.get("objectives") or ()

        scored = score_trial(trial, rubric, objectives=objectives)
        scored_by_position.setdefault(position_id, []).append(scored)

        pair_id = scored.get("pair_id")
        if pair_id is not None:
            scored_by_pair.setdefault(str(pair_id), []).append(scored)

    positions_report: dict[str, object] = {}
    # F1: the equal-weight aggregate and worst-position stats must be
    # computed per (model, arm) group, never pooled across models/arms --
    # a single mixed-pool number would hide a per-model regression and
    # corrupt A/B arm comparisons. `group_position_medians[group_label]` is
    # that group's {position_id: normalized_median} map, built only from
    # positions where the group actually has committed trials.
    group_position_medians: dict[str, dict[str, float]] = {}
    for position_id in sorted(scored_by_position):
        summary = _position_summary(position_id, scored_by_position[position_id])
        positions_report[position_id] = summary
        for label, group_summary in summary["by_group"].items():
            group_position_medians.setdefault(label, {})[position_id] = group_summary["rubric"][
                "normalized_median"
            ]

    aggregate_by_group: dict[str, object] = {}
    for label in sorted(group_position_medians):
        medians = group_position_medians[label]
        equal_weight_mean = sum(medians.values()) / len(medians)
        worst_position_id = min(medians, key=lambda pid: (medians[pid], pid))
        aggregate_by_group[label] = {
            "equal_weight_mean": equal_weight_mean,
            "worst_position_id": worst_position_id,
            "worst_position_median": medians[worst_position_id],
        }

    report: dict[str, object] = {
        "session": {
            "session_fingerprint": lock.get("session_fingerprint"),
            # None for every --ungated-smoke run (that lock never declares
            # one) and for every pre-campaign fixture -- nothing marks such
            # evidence as belonging to any counted campaign block, so a
            # future campaign-level report (Task 11) has no fingerprint to
            # match against it and must reject it outright.
            "campaign_fingerprint": lock.get("campaign_fingerprint"),
            "scorer_fingerprint": scorer_fingerprint,
            "ungated_smoke": ungated_smoke,
        },
        "scorer": {
            "fingerprint": scorer_fingerprint,
            "evaluator": _EVALUATOR_QUALNAME,
        },
        "positions": positions_report,
        "completeness": _completeness_section(
            positions_lock=positions_lock,
            expected_by_position=expected_by_position,
            committed_by_position=committed_by_position,
        ),
        # Keyed by "<model>::<arm>" group label -- see _group_label. There is
        # deliberately no pooled/combined entry mixing groups together: F1
        # requires every equal-weight/worst-position statistic to be scoped
        # to one (model, arm) group.
        "aggregate": aggregate_by_group,
    }

    calibration = _calibration_section(scored_by_pair, _declared_arm_order(schedule_trials))
    if calibration is not None:
        report["calibration"] = calibration

    return report


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _render_group(summary: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    lines.append(f"- Trials: {summary['trial_count']}")
    lines.append(
        f"- Normalized rubric median: {_fmt(summary['rubric']['normalized_median'])}"
        f" (raw median: {_fmt(summary['rubric']['raw_median'])})"
    )
    lines.append(f"- Terminal conditions: {summary['terminal_conditions']}")
    lines.append(
        "- Attempts: total={total_attempt_count}, max={max_attempt_count}, "
        "trials_with_retries={trials_with_retries}".format(**summary["attempts"])
    )
    lines.append(
        f"- Seeds: {summary['seeds']['distinct']} (count={summary['seeds']['count']})"
    )
    lines.append(
        "- Endpoint topology: models={models}, arms={arms}".format(**summary["endpoint_topology"])
    )
    lines.append(
        "- Latency (s): mean={mean_s}, median={median_s}, max={max_s}".format(
            mean_s=_fmt(summary["latency"]["mean_s"]),
            median_s=_fmt(summary["latency"]["median_s"]),
            max_s=_fmt(summary["latency"]["max_s"]),
        )
    )
    lines.append(
        "- Tokens: prompt_total={prompt_total}, completion_total={completion_total}".format(
            **summary["tokens"]
        )
    )
    cost = summary["cost"]
    if cost["unpriced_models"]:
        lines.append(
            f"- Cost (USD): total={_fmt(cost['usd_total'])} (UNPRICED, excluded from "
            f"total: {', '.join(cost['unpriced_models'])})"
        )
    else:
        lines.append(f"- Cost (USD): total={_fmt(cost['usd_total'])}")
    aq = summary["action_quality"]
    lines.append(
        "- Action quality: invalid_calls={invalid_calls}, domain_rejections={domain_rejections}, "
        "successful_mutations={successful_mutations}, repetitions={repetitions}, "
        "useful_actions={useful_actions} ({useful_action_coverage})".format(
            invalid_calls=aq["invalid_calls"],
            domain_rejections=aq["domain_rejections"],
            successful_mutations=aq["successful_mutations"],
            repetitions=aq["repetitions"],
            useful_actions=_fmt(aq["useful_actions"]),
            useful_action_coverage=aq["useful_action_coverage"],
        )
    )
    lines.append("")
    lines.append("| trial | attempt_count | terminal | seed | model | arm | rubric_normalized |")
    lines.append("|---|---|---|---|---|---|---|")
    for trial in summary["trials"]:
        lines.append(
            "| {index} | {attempt_count} | {terminal} | {seed} | {model} | {arm_id} | {normalized} |".format(
                index=trial["index"],
                attempt_count=_fmt(trial["attempt_count"]),
                terminal=_fmt(trial["terminal"]),
                seed=_fmt(trial["seed"]),
                model=_fmt(trial["model"]),
                arm_id=_fmt(trial["arm_id"]),
                normalized=_fmt(trial["rubric"]["normalized"]),
            )
        )
    lines.append("")
    return lines


def _render_position(position_id: str, summary: Mapping[str, object]) -> list[str]:
    lines = [f"## Position: {position_id}", ""]
    lines.append(f"- Total trials (all models/arms): {summary['trial_count']}")
    lines.append("")
    for label in sorted(summary["by_group"]):
        lines.append(f"### {position_id} / {label}")
        lines.append("")
        lines.extend(_render_group(summary["by_group"][label]))
    return lines


def render_markdown(report: Mapping[str, object]) -> str:
    """Render `report` (a `build_report` mapping) to Markdown. Every
    position section is rendered before the aggregate section; positions
    are rendered in sorted order so rendering is deterministic regardless
    of dict insertion order."""
    lines: list[str] = ["# Controlled-position benchmark report", ""]

    session = report["session"]
    if session.get("ungated_smoke"):
        lines.append(
            "> **WARNING: UNGATED SMOKE EVIDENCE** -- this run was produced with "
            "`ungated_smoke=true` (no live admission gate pipeline). It is NOT a "
            "counted session and must never be treated as calibration or "
            "screening evidence."
        )
        lines.append("")

    lines.append(f"- Session fingerprint: {_fmt(session.get('session_fingerprint'))}")
    lines.append(f"- Scorer fingerprint: {_fmt(session.get('scorer_fingerprint'))}")
    lines.append(f"- Scorer evaluator: {report['scorer']['evaluator']}")
    lines.append("")

    completeness = report.get("completeness")
    if completeness is not None:
        lines.append("## Run completeness")
        lines.append("")
        positions_missing = completeness["positions_missing"]
        if positions_missing:
            lines.append(
                "> **WARNING: INCOMPLETE RUN** -- zero committed trials for: "
                + ", ".join(positions_missing)
                + ". These positions never ran; their absence from the position "
                "sections and aggregate below means 'never ran', not 'scored 0'."
            )
            lines.append("")
        lines.append("| position | expected trials | committed trials |")
        lines.append("|---|---|---|")
        for position_id in sorted(completeness["by_position"]):
            counts = completeness["by_position"][position_id]
            lines.append(f"| {position_id} | {counts['expected']} | {counts['committed']} |")
        lines.append("")

    for position_id in sorted(report["positions"]):
        lines.extend(_render_position(position_id, report["positions"][position_id]))

    aggregate = report["aggregate"]
    lines.append("## Aggregate (per model::arm group -- never pooled across groups)")
    lines.append("")
    for label in sorted(aggregate):
        group_aggregate = aggregate[label]
        lines.append(f"### {label}")
        lines.append("")
        lines.append(
            "- Equal-weight mean (normalized rubric median across positions): "
            f"{_fmt(group_aggregate['equal_weight_mean'])}"
        )
        lines.append(
            f"- Worst position: {_fmt(group_aggregate['worst_position_id'])} "
            f"(median {_fmt(group_aggregate['worst_position_median'])})"
        )
        lines.append("")

    calibration = report.get("calibration")
    if calibration is not None:
        lines.append("## Calibration")
        lines.append("")
        lines.append(f"- Pairs compared: {calibration['pair_count']}")
        lines.append(f"- Win counts: {calibration['win_counts']}")
        lines.append(f"- Ties (count for neither arm): {calibration['tie_count']}")
        lines.append(f"- Median paired absolute delta: {_fmt(calibration['median_abs_delta'])}")
        lines.append(
            f"- Median paired signed delta (treatment - baseline): "
            f"{_fmt(calibration['median_signed_delta'])}"
        )
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# write_reports + CLI
# ---------------------------------------------------------------------------


def write_reports(run_dir: str | Path) -> dict[str, object]:
    """Build the report for `run_dir` and write `report.json` (canonical
    JSON) and `report.md` (rendered from that same mapping) into it.
    Returns the report mapping."""
    run_dir = Path(run_dir)
    report = build_report(run_dir)
    (run_dir / "report.json").write_bytes(_canonical_bytes(report))
    (run_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="civ-arena-benchmark-report")
    parser.add_argument("run_dir", help="path to a committed benchmark run directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    try:
        write_reports(run_dir)
    except (ReportError, PredicateError, OSError, ValueError) as exc:
        print(f"civ-arena-benchmark-report: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {run_dir / 'report.json'} and {run_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
