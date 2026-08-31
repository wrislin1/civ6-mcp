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
    for task in rubric:
        if not isinstance(task, Mapping):
            raise MalformedRubricError(f"rubric task must be a mapping, got {type(task).__name__}")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise MalformedRubricError(f"rubric task is missing a non-empty 'task_id': {task!r}")
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


def _usd_cost(model: object, prompt_tokens: object, completion_tokens: object) -> float:
    pin, pout = _PRICE_PER_1K_USD.get(str(model), (0.0, 0.0))
    pt = prompt_tokens if isinstance(prompt_tokens, (int, float)) else 0
    ct = completion_tokens if isinstance(completion_tokens, (int, float)) else 0
    return round(pt / 1000 * pin + ct / 1000 * pout, 6)


def _position_summary(position_id: str, scored: Sequence[Mapping[str, object]]) -> dict[str, object]:
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
    costs = [_usd_cost(s.get("model"), s.get("prompt_tokens"), s.get("completion_tokens")) for s in scored]

    action_quality_totals: dict[str, object] = {
        "invalid_calls": 0,
        "domain_rejections": 0,
        "successful_mutations": 0,
        "repetitions": 0,
        "useful_actions": 0,
    }
    any_useful_available = False
    for s in scored:
        aq = s["action_quality"]
        action_quality_totals["invalid_calls"] += aq["invalid_calls"]
        action_quality_totals["domain_rejections"] += aq["domain_rejections"]
        action_quality_totals["successful_mutations"] += aq["successful_mutations"]
        action_quality_totals["repetitions"] += aq["repetitions"]
        if aq["useful_actions"] is not None:
            any_useful_available = True
            action_quality_totals["useful_actions"] += aq["useful_actions"]
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
            "usd_total": round(sum(costs), 6),
            "usd_mean": round(sum(costs) / len(costs), 6) if costs else 0.0,
        },
        "action_quality": action_quality_totals,
    }


def _calibration_section(scored_by_pair: Mapping[str, list[Mapping[str, object]]]) -> dict[str, object] | None:
    """Pairs trials that share a `pair_id` from `schedule.json` and compare
    two distinct arms' raw rubric scores. Ties count for neither arm (design
    doc: "ties count as non-wins"). Pairs with anything other than exactly
    two distinct-arm trials are skipped (an in-progress/partial run).
    """
    pairs: list[dict[str, object]] = []
    win_counts: dict[str, int] = {}
    tie_count = 0
    deltas: list[float] = []

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
        delta = abs(score_a - score_b)
        deltas.append(delta)
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
                "delta": delta,
            }
        )

    if not pairs:
        return None

    return {
        "pairs": pairs,
        "win_counts": win_counts,
        "tie_count": tie_count,
        "pair_count": len(pairs),
        "median_delta": statistics.median(deltas) if deltas else 0,
    }


def build_report(run_dir: str | Path) -> dict[str, object]:
    """Derive a full report purely from `<run_dir>/session.json`,
    `<run_dir>/schedule.json`, and `<run_dir>/trials/*.json`. Never reads
    `<run_dir>/attempts/` -- see module docstring.

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

    schedule = _read_json(run_dir / "schedule.json")
    schedule_trials = schedule.get("trials") if isinstance(schedule, Mapping) else None
    if not isinstance(schedule_trials, Sequence):
        raise ReportError("schedule.json is missing a 'trials' list")

    scored_by_position: dict[str, list[dict[str, object]]] = {}
    scored_by_pair: dict[str, list[dict[str, object]]] = {}

    for entry in schedule_trials:
        if not isinstance(entry, Mapping):
            raise ReportError(f"schedule.json trial entry must be a mapping, got {entry!r}")
        index = entry.get("index")
        position_id = entry.get("position_id")
        if index is None or position_id is None:
            raise ReportError(f"schedule.json trial entry missing 'index'/'position_id': {entry!r}")

        trial_path = run_dir / "trials" / f"trial-{int(index):03d}.json"
        if not trial_path.exists():
            # Not yet committed -- a report over an in-progress run only
            # covers what has actually been committed so far.
            continue
        trial = _read_json(trial_path)
        if not isinstance(trial, Mapping):
            raise ReportError(f"{trial_path} must be a JSON object, got {type(trial).__name__}")

        position_lock = positions_lock.get(position_id)
        if not isinstance(position_lock, Mapping) or "rubric" not in position_lock:
            raise ReportError(
                f"session.json has no rubric recorded for position {position_id!r}"
            )
        rubric = position_lock["rubric"]
        objectives = position_lock.get("objectives") or ()

        scored = score_trial(trial, rubric, objectives=objectives)
        scored_by_position.setdefault(str(position_id), []).append(scored)

        pair_id = scored.get("pair_id")
        if pair_id is not None:
            scored_by_pair.setdefault(str(pair_id), []).append(scored)

    positions_report: dict[str, object] = {}
    position_medians: dict[str, float] = {}
    for position_id in sorted(scored_by_position):
        summary = _position_summary(position_id, scored_by_position[position_id])
        positions_report[position_id] = summary
        position_medians[position_id] = summary["rubric"]["normalized_median"]

    if position_medians:
        equal_weight_mean = sum(position_medians.values()) / len(position_medians)
        worst_position_id = min(
            position_medians, key=lambda pid: (position_medians[pid], pid)
        )
        worst_position_median = position_medians[worst_position_id]
    else:
        equal_weight_mean = 0.0
        worst_position_id = None
        worst_position_median = 0.0

    report: dict[str, object] = {
        "session": {
            "session_fingerprint": lock.get("session_fingerprint"),
            "scorer_fingerprint": scorer_fingerprint,
            "ungated_smoke": bool(lock.get("ungated_smoke", False)),
        },
        "scorer": {
            "fingerprint": scorer_fingerprint,
            "evaluator": _EVALUATOR_QUALNAME,
        },
        "positions": positions_report,
        "aggregate": {
            "equal_weight_mean": equal_weight_mean,
            "worst_position_id": worst_position_id,
            "worst_position_median": worst_position_median,
        },
    }

    calibration = _calibration_section(scored_by_pair)
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


def _render_position(position_id: str, summary: Mapping[str, object]) -> list[str]:
    lines = [f"## Position: {position_id}", ""]
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
    lines.append(f"- Cost (USD): total={_fmt(summary['cost']['usd_total'])}")
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

    for position_id in sorted(report["positions"]):
        lines.extend(_render_position(position_id, report["positions"][position_id]))

    aggregate = report["aggregate"]
    lines.append("## Aggregate")
    lines.append("")
    lines.append(f"- Equal-weight mean (normalized rubric median across positions): {_fmt(aggregate['equal_weight_mean'])}")
    lines.append(
        f"- Worst position: {_fmt(aggregate['worst_position_id'])} "
        f"(median {_fmt(aggregate['worst_position_median'])})"
    )
    lines.append("")

    calibration = report.get("calibration")
    if calibration is not None:
        lines.append("## Calibration")
        lines.append("")
        lines.append(f"- Pairs compared: {calibration['pair_count']}")
        lines.append(f"- Win counts: {calibration['win_counts']}")
        lines.append(f"- Ties (count for neither arm): {calibration['tie_count']}")
        lines.append(f"- Median paired delta: {_fmt(calibration['median_delta'])}")
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
