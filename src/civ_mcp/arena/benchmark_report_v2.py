"""Deterministic version-2 trial reports over persisted raw evidence.

`build_trial_report` composes the v2 scorer, the measured ledger and the audit
layer over one persisted v2 trial and its position document. Everything is
derived from the trial's recorded states and the position's frozen inputs; no
expected-case data is read, so a report can never be shaped by expectations.
Identity and turn checks are recomputed from the states (never trusted from
`validation_status`); counters are the trial's recorded counters (never
inferred from step rows); capture cost is surfaced as recorded with
unavailable values kept `None`. `render_report` is a pure function of the
report with a fixed section order and canonical number formatting.

`validate_model_inputs` keeps scripted/non-counting and non-v2 evidence out of
model comparisons and rejects mixed identities within one position before
`aggregate_model_reports` computes per-model statistics. Nothing here touches
the version-1 report path.
"""
from __future__ import annotations

import json
import math
import statistics
from typing import Any

from civ_mcp.arena.benchmark_audit import (
    audit_losses,
    classify_mutation,
    mutation_records,
    undercredited_completions,
)
from civ_mcp.arena.benchmark_contract_v2 import canonical_bytes, document_digest
from civ_mcp.arena.benchmark_ledger import build_ledger
from civ_mcp.arena.benchmark_scoring_v2 import attribute_progress, score_trial
from civ_mcp.arena.benchmark_state_v2 import digest_state_v2

EVIDENCE_VERSION = "2.0.0"
_IDENTITY_FIELDS = ("turn", "active_player", "player_id")
_TRIAL_PROVENANCE = ("position_id", "script_id", "script_sha256", "case_id", "case_sha256",
                     "toolset_id", "toolset_identity", "contract_fingerprint",
                     "session_fingerprint", "evidence_version", "actor_kind", "counting",
                     "coverage")
_CAPTURE_FIELDS = ("total_s", "in_episode_total_s", "in_episode_share",
                   "pre_drain_total_s", "post_drain_total_s", "non_drain_residual_s")
_MODEL_USAGE = ("model", "prompt_tokens", "completion_tokens", "cost_usd",
                "model_latency_s")
_COMPARISON_IDENTITIES = ("contract_fingerprint", "toolset_identity", "coverage")
_AGGREGATED = ("primary_score", "tool_call_attempts", "round_trips",
               "attempts_per_round", "dispatched_calls")
_SECTIONS = (("provenance", "Provenance"), ("identity", "Identity"),
             ("mechanics", "Mechanics"), ("terminal", "Terminal"),
             ("counters", "Counters"), ("score", "Score"), ("ledger", "Ledger"),
             ("audits", "Audits"), ("capture", "Capture"),
             ("model_usage", "Model usage"), ("digests", "Digests"))


def _require_v2(trial: dict[str, Any], where: str) -> None:
    version = trial.get("evidence_version")
    if version != EVIDENCE_VERSION:
        raise ValueError(f"{where}: evidence_version must be {EVIDENCE_VERSION!r}, "
                         f"got {version!r}")


# ---------------------------------------------------------------------------
# Trial report
# ---------------------------------------------------------------------------

def _identity(trial: dict[str, Any]) -> dict[str, Any]:
    initial = trial["initial_state"]
    checkpoints = []
    for step in trial["steps"]:
        checkpoints.append((f"step {step['idx']} before", step["state_before"]))
        checkpoints.append((f"step {step['idx']} after", step["state_after"]))
    checkpoints.append(("final", trial["final_state"]))
    drift = [{"where": where, "field": field, "expected": initial.get(field),
              "actual": state.get(field)}
             for where, state in checkpoints for field in _IDENTITY_FIELDS
             if state.get(field) != initial.get(field)]
    return {"identity_ok": not drift, "drift": drift}


def _terminal(trial: dict[str, Any]) -> dict[str, Any]:
    steps = trial["steps"]
    return {"terminal": trial["terminal"],
            "truncated_steps": [s["idx"] for s in steps if s.get("truncated") is True],
            "truncation_unavailable_steps": [s["idx"] for s in steps
                                             if not isinstance(s.get("truncated"), bool)]}


def _counters(trial: dict[str, Any]) -> dict[str, Any]:
    rounds, attempts = trial["round_trips"], trial["tool_call_attempts"]
    return {"round_trips": rounds, "round_trips_completed": trial["round_trips_completed"],
            "tool_call_attempts": attempts, "dispatched_calls": trial["dispatched_calls"],
            "attempts_per_round": attempts / rounds if rounds else None}


def _capture(trial: dict[str, Any]) -> dict[str, Any]:
    summary = trial.get("capture_summary")
    if not isinstance(summary, dict):
        return {"available": False, **{k: None for k in _CAPTURE_FIELDS},
                "unavailable": None, "summary": None}
    return {"available": True, **{k: summary.get(k) for k in _CAPTURE_FIELDS},
            "unavailable": summary.get("unavailable"), "summary": summary}


def _provenance(trial: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]:
    tiles = position["public_task_tiles"]
    return {**{k: trial[k] for k in _TRIAL_PROVENANCE},
            "position_version": position["version"],
            "contract_identity": position["contract_identity"],
            "public_observation_sha256": position["public_observation"]["sha256"],
            "public_task_tiles": tiles,
            "public_task_tiles_sha256": document_digest(tiles),
            "rubric_sha256": document_digest(position["rubric"])}


def _loss_coverage(trial: dict[str, Any], losses: list[dict[str, Any]]) -> dict[str, int]:
    declared = sum(loss["declared"] for loss in losses)
    return {"steps_examined": len(trial["steps"]), "observed": len(losses),
            "declared": declared, "undeclared": len(losses) - declared}


def build_trial_report(trial: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]:
    """Derived, deterministic report of one persisted v2 trial."""
    _require_v2(trial, "trial")
    rubric = position["rubric"]
    score = score_trial(trial, rubric)
    progress = attribute_progress(trial, rubric)
    task_tiles = [tuple(t) for t in position["public_task_tiles"]]
    records = mutation_records(trial, progress, task_tiles=task_tiles,
                               declared_losses=score["harms"])
    losses = audit_losses(trial, score["harms"])
    report = {
        "provenance": _provenance(trial, position),
        "identity": _identity(trial),
        "mechanics": {"validation_status": trial["validation_status"],
                      "validation_failures": trial["validation_failures"],
                      "invalid_tool_calls": trial["invalid_tool_calls"]},
        "terminal": _terminal(trial),
        "counters": _counters(trial),
        "score": score,
        "ledger": build_ledger(trial),
        "audits": {"uncredited": [classify_mutation(r) for r in records],
                   "losses": losses,
                   "loss_coverage": _loss_coverage(trial, losses),
                   "undercredit": undercredited_completions(progress, score)},
        "capture": _capture(trial),
        "model_usage": {k: trial[k] for k in _MODEL_USAGE},
        "digests": {"initial": digest_state_v2(trial["initial_state"]),
                    "final": digest_state_v2(trial["final_state"]),
                    "steps": [{"step": s["idx"], "before": s["state_digest_before"],
                               "after": s["state_digest_after"]} for s in trial["steps"]]},
    }
    # Detach from the inputs: the report is a plain canonical JSON value.
    return json.loads(canonical_bytes(report))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _block(value: Any) -> list[str]:
    return ["```json", json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                                  allow_nan=False), "```"]


def _bullets(mapping: dict[str, Any], *, none: str | None = None) -> list[str]:
    return [f"- {k}: {none if v is None and none else _value(v)}"
            for k, v in sorted(mapping.items())]


def _table(header: tuple[str, ...], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_value(c) for c in row) + " |" for row in rows]
    return lines


def _render_score(score: dict[str, Any]) -> list[str]:
    totals = {k: score[k] for k in ("gross_credit", "harm_total", "net_credit",
                                    "maximum_credit", "maximum_harm", "primary_score")}
    lines = _bullets(totals) + ["", "### Objectives"]
    lines += _table(("id", "credit", "maximum", "rung_index"),
                    [[o["id"], o["credit"], o["maximum"], o["rung_index"]]
                     for o in score["objectives"]])
    lines += ["", "### Harms"]
    lines += _table(("id", "loss_key", "status", "timing", "weight", "deduction",
                     "fired_steps", "charged_harm"),
                    [[h["id"], h["loss_key"], h["status"], h["timing"], h["weight"],
                      h["deduction"], h["fired_steps"], h["charged_harm"]]
                     for h in score["harms"]])
    return lines + ["", "### Scored evidence", *_block(score)]


def _render_ledger(ledger: dict[str, Any]) -> list[str]:
    lines = ["### Net"]
    lines += _table(("path", "before", "after", "delta", "coverage", "source_steps"),
                    [[e["path"], e["before"], e["after"], e["delta"], e["coverage"],
                      e["source_steps"]] for e in ledger["net"]])
    return lines + ["", "### Steps", *_block(ledger["steps"])]


def _render_capture(capture: dict[str, Any]) -> list[str]:
    surfaced = {k: capture[k] for k in ("available", *_CAPTURE_FIELDS)}
    return (_bullets(surfaced, none="unavailable")
            + ["", "### Unavailable", *_block(capture["unavailable"]),
               "", "### Summary", *_block(capture["summary"])])


def _render_section(key: str, body: Any) -> list[str]:
    if key == "score":
        return _render_score(body)
    if key == "ledger":
        return _render_ledger(body)
    if key == "capture":
        return _render_capture(body)
    if key in ("identity", "audits", "digests"):
        return [line for k in sorted(body) for line in (f"### {k}", *_block(body[k]), "")][:-1]
    return _bullets(body)


def render_report(report: dict[str, Any]) -> str:
    """Markdown with a fixed section order; byte-identical for equal reports."""
    lines = ["# Version-2 trial report", ""]
    for key, title in _SECTIONS:
        lines += [f"## {title}", "", *_render_section(key, report[key]), ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Model comparisons
# ---------------------------------------------------------------------------

def validate_model_inputs(trials: list[dict[str, Any]]) -> None:
    """Reject scripted, non-counting, non-v2 or identity-mixed comparison inputs."""
    for i, trial in enumerate(trials):
        if trial.get("actor_kind") == "scripted" or trial.get("counting") is not True:
            raise ValueError(f"trial {i}: scripted or non-counting evidence cannot enter "
                             f"model comparisons")
        _require_v2(trial, f"trial {i}")
    seen: dict[str, dict[str, bytes]] = {}
    for i, trial in enumerate(trials):
        identity = {k: canonical_bytes(trial.get(k)) for k in _COMPARISON_IDENTITIES}
        first = seen.setdefault(trial["position_id"], identity)
        for key in _COMPARISON_IDENTITIES:
            if identity[key] != first[key]:
                raise ValueError(f"trial {i}: mixed {key} within position "
                                 f"{trial['position_id']!r}")


def _stats(values: list[Any]) -> dict[str, Any]:
    present = sorted(v for v in values if v is not None)
    n = len(present)
    if not n:
        return {"n": 0, "min": None, "median": None, "p95": None, "max": None}
    return {"n": n, "min": present[0], "median": statistics.median(present),
            "p95": present[math.ceil(0.95 * n) - 1], "max": present[-1]}


def aggregate_model_reports(trials: list[dict[str, Any]],
                            positions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per-model n/min/median/p95/max over eligible counting model trials."""
    validate_model_inputs(trials)
    by_model: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        report = build_trial_report(trial, positions[trial["position_id"]])
        by_model.setdefault(trial["model"], []).append({
            "position_id": trial["position_id"], "index": trial["index"],
            "primary_score": report["score"]["primary_score"],
            "counters": report["counters"], "model_usage": report["model_usage"]})
    models = {}
    for model in sorted(by_model):
        rows = sorted(by_model[model],
                      key=lambda r: (r["position_id"], r["index"], canonical_bytes(r)))
        metrics = {"primary_score": [r["primary_score"] for r in rows]}
        for key in _AGGREGATED[1:]:
            metrics[key] = [r["counters"][key] for r in rows]
        models[model] = {"n": len(rows), **{k: _stats(v) for k, v in metrics.items()},
                         "trials": rows}
    return {"models": models}
