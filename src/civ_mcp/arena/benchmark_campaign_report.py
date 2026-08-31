"""Deterministic campaign-level reporting and verdict attribution for the
arena two-model counted calibration campaign (Plan 2).

`benchmark_report.py` derives a report for exactly one model block's run
directory. This module is the layer above it: it reads one campaign
directory (`benchmark_campaign.CampaignStore`'s on-disk layout --
`campaign.json`, `schedule.json`, `blocks/<block-id>/...`, and
`admissions/`), calls the existing per-block `build_report`/`score_trial`
machinery verbatim for each block, and derives the campaign-wide sensitivity/
separation arithmetic and the final campaign verdict from that.

Exactly three kinds of evidence are read, mirroring `benchmark_report.py`'s
own contract:

    <campaign_dir>/campaign.json                the immutable campaign lock
    <campaign_dir>/schedule.json                 {"blocks": {block_id: {"trials": [...]}}}
    <campaign_dir>/blocks/<block-id>/...          an ordinary per-block run
                                                   directory (session.json,
                                                   schedule.json, trials/)
    <campaign_dir>/blocks/<block-id>/audit.json          human metric-fidelity review
    <campaign_dir>/blocks/<block-id>/tie_attribution.json  human tie-attribution review
    <campaign_dir>/admissions/<block-id>-attempt-NNN.json  typed admission
                                                            dispositions (Task 8)

This module NEVER reads `<campaign_dir>/blocks/<block-id>/attempts/` --
infrastructure-attempt records are not scoreable evidence, exactly as
`benchmark_report.py` already refuses to read `<run_dir>/attempts/` (see that
module's docstring). `admissions/` IS read, but only to detect a typed
`REPLICATION_DEFERRED_ADMISSION` disposition -- that disposition is never
passed to scoring, only used to decide whether an incomplete Qwen schedule is
a legitimate deferral rather than a refused report.

## Sensitivity, direction, and effect (per completed block)

For each committed pair (matched by `pair_id`, one baseline-arm trial and one
treatment-arm trial per pair -- arm order taken from `campaign.json["arms"]`,
index 0 is baseline, index 1 is treatment), the signed normalized delta is
`treatment_normalized - baseline_normalized`, where each trial's normalized
score is `score_rubric`'s own `normalized` field (raw score divided by that
block's frozen rubric maximum -- never re-derived here). All of the block's
declared pairs (`rules["pairs_per_model"]`) contribute one delta each,
including ties (`delta == 0`); a pair whose two arm trials don't have exactly
one baseline and one treatment trial is a hard error, never silently skipped.

    decided = sum(delta != 0 for delta in deltas)
    standard_wins = sum(delta > 0 for delta in deltas)
    median_delta = statistics.median(deltas)   # over ALL declared pairs

A block passes when `decided >= rules["minimum_decided_pairs"]` AND
`standard_wins >= rules["minimum_standard_wins"]` AND
`median_delta >= rules["minimum_median_normalized_delta"]`. All three
thresholds come from the frozen `campaign.json["rules"]` -- never hardcoded
here.

## Block outcomes (binding taxonomy)

    PASS                    sensitivity/direction/effect gates all satisfied
    MODEL_NULL              sensitivity satisfied, direction or effect failed
    MODEL_FLOOR_NULL        tie-heavy; every tied pair reviewed and attributed
                             "model_floor"
    MODEL_TIE_NULL          tie-heavy; every tied pair reviewed and attributed
                             "model_floor"/"same_progress" (at least one
                             "same_progress")
    NONDISCRIMINATIVE       tie-heavy; at least one tied pair reviewed and
                             attributed "rubric_nondiscriminative" (mixed
                             attribution is ALWAYS this outcome, never a
                             partial pass -- see design doc)
    TIE_ATTRIBUTION_REQUIRED  tie-heavy and not (yet) fully reviewed --
                             pending, not a final verdict; forces the
                             campaign outcome to BLOCKED
    METRIC_FIDELITY_FAILED  the block's audit.json is missing, disagrees, or
                             is hash-stale against the actually committed
                             trials -- forces the campaign outcome to BLOCKED

Mechanical zero/nonzero tie labels (`mechanical_label` on each resolved
attribution) are recorded purely for legibility; they never by themselves
decide `MODEL_FLOOR_NULL` vs `MODEL_TIE_NULL` vs `NONDISCRIMINATIVE` -- only
a reviewed `tie_attribution.json` entry does (see module docstring section
above and `_tie_attribution_section`).

## Campaign outcomes

    CALIBRATED                       at least one admitted block PASSes, and
                                       no block is blocking (see below)
    CALIBRATED_REPLICATION_DEFERRED  Gemma (the mandatory primary block)
                                       PASSes and Qwen has a valid
                                       REPLICATION_DEFERRED_ADMISSION record
    BLOCKED                          Gemma incomplete; Gemma didn't pass and
                                       Qwen couldn't be admitted; any
                                       evaluated block is
                                       TIE_ATTRIBUTION_REQUIRED or
                                       METRIC_FIDELITY_FAILED; or neither
                                       block PASSes
    RUBRIC_NONDISCRIMINATIVE         any evaluated block is NONDISCRIMINATIVE

Gemma (`campaign.json["models"][0]`) is always the mandatory primary block --
an incomplete Gemma schedule always refuses report generation outright
(`CampaignReportError`), regardless of Qwen. Qwen
(`campaign.json["models"][1]`) may be incomplete ONLY when a valid
`REPLICATION_DEFERRED_ADMISSION` record exists for it in `admissions/`.

## Determinism

Every value in the returned report is a pure function of the evidence listed
above. No `datetime.now()`, no filesystem mtime, and no non-canonical
ordering anywhere -- `write_campaign_reports` regenerates
`campaign_report.json`/`campaign_report.md` byte-identically over an
unchanged campaign directory.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Mapping, Sequence

from civ_mcp.arena import benchmark_report
from civ_mcp.arena.benchmark_manifest import fingerprint
from civ_mcp.arena.benchmark_report import ReportError
from civ_mcp.arena.benchmark_store import trial_filename

__all__ = [
    "CampaignReportError",
    "REPLICATION_DEFERRED_ADMISSION",
    "build_campaign_report",
    "render_campaign_markdown",
    "write_campaign_reports",
    "main",
]

# Mirrors benchmark_admission.REPLICATION_DEFERRED_ADMISSION exactly (kept as
# an independent constant, not an import, to avoid pulling this pure
# evidence-to-report module into benchmark_admission's much heavier import
# graph -- backends/live-evidence/gates/runner -- for one string literal).
REPLICATION_DEFERRED_ADMISSION = "REPLICATION_DEFERRED_ADMISSION"

# -- block outcomes -----------------------------------------------------
BLOCK_OUTCOME_PASS = "PASS"
BLOCK_OUTCOME_MODEL_NULL = "MODEL_NULL"
BLOCK_OUTCOME_MODEL_FLOOR_NULL = "MODEL_FLOOR_NULL"
BLOCK_OUTCOME_MODEL_TIE_NULL = "MODEL_TIE_NULL"
BLOCK_OUTCOME_NONDISCRIMINATIVE = "NONDISCRIMINATIVE"
BLOCK_OUTCOME_TIE_ATTRIBUTION_REQUIRED = "TIE_ATTRIBUTION_REQUIRED"
BLOCK_OUTCOME_METRIC_FIDELITY_FAILED = "METRIC_FIDELITY_FAILED"

# Outcomes that pending-review or a broken metric-fidelity audit fold into --
# neither is a final verdict, and either one alone forces the campaign
# outcome to BLOCKED regardless of the other block's own outcome.
_BLOCKING_BLOCK_OUTCOMES = frozenset(
    {BLOCK_OUTCOME_TIE_ATTRIBUTION_REQUIRED, BLOCK_OUTCOME_METRIC_FIDELITY_FAILED}
)

BLOCK_STATUS_COMPLETE = "COMPLETE"
BLOCK_STATUS_ADMISSION_DEFERRED = "ADMISSION_DEFERRED"

# -- campaign outcomes ----------------------------------------------------
CAMPAIGN_OUTCOME_CALIBRATED = "CALIBRATED"
CAMPAIGN_OUTCOME_CALIBRATED_REPLICATION_DEFERRED = "CALIBRATED_REPLICATION_DEFERRED"
CAMPAIGN_OUTCOME_BLOCKED = "BLOCKED"
CAMPAIGN_OUTCOME_RUBRIC_NONDISCRIMINATIVE = "RUBRIC_NONDISCRIMINATIVE"

# The only three attribution values a reviewed tie_attribution.json entry may
# declare -- see the module docstring's "Block outcomes" section and
# `_tie_attribution_section`.
ALLOWED_TIE_ATTRIBUTIONS = frozenset({"model_floor", "same_progress", "rubric_nondiscriminative"})

_REQUIRED_CALIBRATION_RULES_FIELDS = (
    "pairs_per_model",
    "minimum_decided_pairs",
    "minimum_standard_wins",
    "minimum_median_normalized_delta",
    "required_audits_per_arm",
)


class CampaignReportError(Exception):
    """Base class for a campaign-report-generation abort. Raised for a
    structurally broken/tampered campaign directory or an incomplete
    schedule with no valid replication deferral -- never caught anywhere in
    this module to fall back to a partial or silently-wrong verdict."""


def _canonical_bytes(value: object) -> bytes:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return encoded.encode("utf-8")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


# ---------------------------------------------------------------------------
# admissions/ -- read-only, disposition-only (never passed to scoring)
# ---------------------------------------------------------------------------


def _admission_dispositions(campaign_dir: Path, block_id: str) -> list[dict]:
    """Every admission-attempt record for `block_id` that declares a typed
    `disposition` field, in on-disk (numbered) order. Tolerates a missing
    `admissions/` directory and skips any file that fails to parse as JSON --
    admission evidence is diagnostic, not scoreable, so a corrupt record here
    must never abort report generation the way corrupt trial evidence does."""
    admissions_dir = campaign_dir / "admissions"
    if not admissions_dir.is_dir():
        return []
    prefix = f"{block_id}-attempt-"
    records: list[dict] = []
    for path in sorted(admissions_dir.iterdir()):
        if not (path.is_file() and path.name.startswith(prefix) and path.name.endswith(".json")):
            continue
        try:
            record = _read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(record, Mapping) and "disposition" in record:
            records.append(dict(record))
    return records


def _has_valid_replication_deferred_admission(campaign_dir: Path, block_id: str) -> bool:
    return any(
        record.get("disposition") == REPLICATION_DEFERRED_ADMISSION and record.get("block_id") == block_id
        for record in _admission_dispositions(campaign_dir, block_id)
    )


# ---------------------------------------------------------------------------
# Per-block calibration arithmetic
# ---------------------------------------------------------------------------


def _scored_by_index_from_block_report(block_report: Mapping[str, object], position_id: str) -> dict[int, Mapping]:
    """Every committed trial at `position_id`, keyed by trial index, pulled
    from an already-built `benchmark_report.build_report` result -- never
    rescored here, so this campaign layer never re-implements
    `score_trial`/`evaluate_predicate`."""
    positions = block_report.get("positions")
    position_summary = positions.get(position_id) if isinstance(positions, Mapping) else None
    if position_summary is None:
        raise CampaignReportError(
            f"block report has no committed trials for position {position_id!r} -- a "
            "complete Plan 2 block schedule must produce every trial at its declared "
            "position"
        )
    scored: dict[int, Mapping] = {}
    for group in position_summary["by_group"].values():
        for trial in group["trials"]:
            scored[trial["index"]] = trial
    return scored


def _require_calibration_rules(rules: object) -> dict[str, object]:
    if not isinstance(rules, Mapping):
        raise CampaignReportError("campaign.json is missing a 'rules' mapping")
    missing = sorted(field for field in _REQUIRED_CALIBRATION_RULES_FIELDS if field not in rules)
    if missing:
        raise CampaignReportError(f"campaign.json 'rules' is missing required field(s): {missing}")
    return dict(rules)


def _calibration_section(
    scored_by_index: Mapping[int, Mapping[str, object]],
    *,
    expected_pair_count: int,
    baseline_arm_id: str,
    treatment_arm_id: str,
    rules: Mapping[str, object],
) -> dict[str, object]:
    """Pair every committed trial by `pair_id`, retain every declared pair's
    signed normalized delta (baseline vs treatment, oriented
    treatment-minus-baseline so a positive delta always means "treatment
    scored higher"), and evaluate the frozen sensitivity/direction/effect
    gates. Every one of the block's declared pairs must resolve to exactly
    one baseline-arm and one treatment-arm trial -- a partial or malformed
    pair is a hard `CampaignReportError`, never silently skipped or
    under-counted (skipping would let a missing pair masquerade as "fewer
    decided pairs" instead of the structural defect it actually is)."""
    by_pair: dict[str, dict[str, Mapping[str, object]]] = {}
    for trial in scored_by_index.values():
        pair_id = trial.get("pair_id")
        if pair_id is None:
            raise CampaignReportError(
                f"trial {trial.get('index')!r} has no pair_id -- every counted campaign "
                "trial must be paired"
            )
        arm_id = str(trial.get("arm_id"))
        bucket = by_pair.setdefault(str(pair_id), {})
        if arm_id in bucket:
            raise CampaignReportError(
                f"pair {pair_id!r} has more than one trial for arm {arm_id!r} (indices "
                f"{bucket[arm_id].get('index')!r} and {trial.get('index')!r}) -- refusing to "
                "silently drop one"
            )
        bucket[arm_id] = trial

    if len(by_pair) != expected_pair_count:
        raise CampaignReportError(
            f"expected {expected_pair_count} pairs (rules['pairs_per_model']) but found "
            f"{len(by_pair)} distinct pair_id(s) among this block's committed trials"
        )

    pairs: list[dict[str, object]] = []
    deltas: list[float] = []
    for pair_id in sorted(by_pair):
        members = by_pair[pair_id]
        if baseline_arm_id not in members or treatment_arm_id not in members:
            raise CampaignReportError(
                f"pair {pair_id!r} does not have exactly one {baseline_arm_id!r} trial and "
                f"one {treatment_arm_id!r} trial (found arms: {sorted(members)})"
            )
        baseline_trial = members[baseline_arm_id]
        treatment_trial = members[treatment_arm_id]
        baseline_normalized = baseline_trial["rubric"]["normalized"]
        treatment_normalized = treatment_trial["rubric"]["normalized"]
        signed_delta = treatment_normalized - baseline_normalized
        decided = signed_delta != 0
        deltas.append(signed_delta)
        pairs.append(
            {
                "pair_id": pair_id,
                "baseline_arm_id": baseline_arm_id,
                "treatment_arm_id": treatment_arm_id,
                "baseline_normalized": baseline_normalized,
                "treatment_normalized": treatment_normalized,
                "signed_delta": signed_delta,
                "decided": decided,
                "trial_indices": {
                    baseline_arm_id: baseline_trial["index"],
                    treatment_arm_id: treatment_trial["index"],
                },
            }
        )

    decided_count = sum(1 for pair in pairs if pair["decided"])
    standard_wins = sum(1 for delta in deltas if delta > 0)
    median_delta = statistics.median(deltas) if deltas else 0.0

    return {
        "pairs": pairs,
        "pair_count": len(pairs),
        "decided_count": decided_count,
        "tie_count": len(pairs) - decided_count,
        "standard_wins": standard_wins,
        "median_signed_delta": median_delta,
        "thresholds": {
            "minimum_decided_pairs": rules["minimum_decided_pairs"],
            "minimum_standard_wins": rules["minimum_standard_wins"],
            "minimum_median_normalized_delta": rules["minimum_median_normalized_delta"],
        },
        "sensitivity_ok": decided_count >= rules["minimum_decided_pairs"],
        "direction_ok": standard_wins >= rules["minimum_standard_wins"],
        "effect_ok": median_delta >= rules["minimum_median_normalized_delta"],
    }


# ---------------------------------------------------------------------------
# Metric-fidelity audit
# ---------------------------------------------------------------------------


def _metric_fidelity_section(
    block_dir: Path,
    block_session: Mapping[str, object],
    audit_indices: Sequence[int],
    raw_by_index: Mapping[int, Mapping[str, object]],
    scored_by_index: Mapping[int, Mapping[str, object]],
) -> dict[str, object]:
    """Cross-check `<block_dir>/audit.json` (see module docstring for the
    schema) against the block's actually-committed trials.

    Every frozen audit index must have an entry whose `trial_sha256` matches
    a live `fingerprint()` of that raw committed trial (hash-binding a stale
    or edited audit to the trial it no longer describes), and whose `manual`
    field exactly equals a FRESH recompute of the same automatic metrics from
    the CURRENT scorer -- never the audit's own recorded `automatic` field,
    which may legitimately go stale after a scorer-fingerprint-only fix that
    doesn't touch raw trial evidence (see design doc: "A pure implementation
    correction ... may regenerate reports over unchanged raw evidence").
    `manual` is the human's independent read and is the actual ground truth
    this gate is checking automatic scoring against.
    """
    audit_path = block_dir / "audit.json"
    if not audit_path.is_file():
        return {
            "ok": False,
            "reason": f"{audit_path} is missing",
            "audit_indices": list(audit_indices),
            "mismatches": [],
        }
    audit = _read_json(audit_path)
    if not isinstance(audit, Mapping):
        return {
            "ok": False,
            "reason": f"{audit_path} must be a JSON object",
            "audit_indices": list(audit_indices),
            "mismatches": [],
        }

    lock_session_fingerprint = block_session.get("session_fingerprint")
    audit_session_fingerprint = audit.get("session_fingerprint")
    if not audit_session_fingerprint or audit_session_fingerprint != lock_session_fingerprint:
        return {
            "ok": False,
            "reason": (
                f"{audit_path} session_fingerprint {audit_session_fingerprint!r} does not "
                f"match this block's session_fingerprint {lock_session_fingerprint!r}"
            ),
            "audit_indices": list(audit_indices),
            "mismatches": [],
        }

    entries_raw = audit.get("trials")
    entries: dict[int, Mapping[str, object]] = {}
    if isinstance(entries_raw, Sequence) and not isinstance(entries_raw, (str, bytes)):
        for entry in entries_raw:
            if isinstance(entry, Mapping) and isinstance(entry.get("index"), int):
                entries[entry["index"]] = entry

    missing = sorted(index for index in audit_indices if index not in entries)
    if missing:
        return {
            "ok": False,
            "reason": f"{audit_path} is missing audit entries for index(es) {missing}",
            "audit_indices": list(audit_indices),
            "mismatches": [],
        }

    mismatches: list[dict[str, object]] = []
    for index in audit_indices:
        entry = entries[index]
        raw_trial = raw_by_index.get(index)
        scored_trial = scored_by_index.get(index)
        if raw_trial is None or scored_trial is None:
            mismatches.append({"index": index, "reason": "audited index has no committed trial evidence"})
            continue

        expected_sha256 = fingerprint(raw_trial)
        recorded_sha256 = entry.get("trial_sha256")
        if recorded_sha256 != expected_sha256:
            mismatches.append(
                {
                    "index": index,
                    "reason": "trial_sha256 mismatch -- audited evidence no longer matches "
                    "the committed trial",
                    "recorded": recorded_sha256,
                    "expected": expected_sha256,
                }
            )
            continue

        live_automatic = {
            "task_scores": {task_id: task["score"] for task_id, task in scored_trial["rubric"]["tasks"].items()},
            "useful_actions": scored_trial["action_quality"]["useful_actions"],
            "domain_rejections": scored_trial["action_quality"]["domain_rejections"],
            "repetitions": scored_trial["action_quality"]["repetitions"],
        }
        manual = entry.get("manual")
        if manual != live_automatic:
            mismatches.append(
                {
                    "index": index,
                    "reason": "manual review disagrees with the current scorer's live "
                    "automatic result",
                    "manual": manual,
                    "live_automatic": live_automatic,
                }
            )

    return {
        "ok": not mismatches,
        "reason": None if not mismatches else "one or more audited trials disagree",
        "audit_indices": list(audit_indices),
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# Tie attribution
# ---------------------------------------------------------------------------


def _tie_attribution_section(
    block_dir: Path,
    block_session: Mapping[str, object],
    tied_pairs: Sequence[Mapping[str, object]],
    raw_by_index: Mapping[int, Mapping[str, object]],
) -> dict[str, object]:
    """Cross-check `<block_dir>/tie_attribution.json` against every tied
    pair (`decided is False`). Not required at all when there are no tied
    pairs. Every tied pair must have its own reviewed entry, hash-bound
    (`trial_sha256`) to BOTH of that pair's raw committed trials, declaring
    exactly one of `ALLOWED_TIE_ATTRIBUTIONS`. Missing the file, a missing
    per-pair entry, a hash mismatch, or an invalid attribution value all
    leave this section unresolved (`resolved: False`) -- never silently
    treated as any particular attribution."""
    if not tied_pairs:
        return {"required": False, "resolved": True, "attributions": {}, "reason": None}

    path = block_dir / "tie_attribution.json"
    if not path.is_file():
        return {
            "required": True,
            "resolved": False,
            "attributions": {},
            "reason": f"{len(tied_pairs)} tied pair(s) but {path} does not exist",
        }
    payload = _read_json(path)
    if not isinstance(payload, Mapping):
        return {
            "required": True,
            "resolved": False,
            "attributions": {},
            "reason": f"{path} must be a JSON object",
        }

    lock_session_fingerprint = block_session.get("session_fingerprint")
    if payload.get("session_fingerprint") != lock_session_fingerprint:
        return {
            "required": True,
            "resolved": False,
            "attributions": {},
            "reason": (
                f"{path} session_fingerprint {payload.get('session_fingerprint')!r} does not "
                f"match this block's session_fingerprint {lock_session_fingerprint!r}"
            ),
        }

    raw_entries = payload.get("attributions")
    by_pair: dict[str, Mapping[str, object]] = {}
    if isinstance(raw_entries, Sequence) and not isinstance(raw_entries, (str, bytes)):
        for entry in raw_entries:
            if isinstance(entry, Mapping) and isinstance(entry.get("pair_id"), str):
                by_pair[entry["pair_id"]] = entry

    resolved: dict[str, dict[str, object]] = {}
    for pair in tied_pairs:
        pair_id = pair["pair_id"]
        entry = by_pair.get(pair_id)
        if entry is None:
            return {
                "required": True,
                "resolved": False,
                "attributions": resolved,
                "reason": f"{path} has no attribution entry for tied pair {pair_id!r}",
            }

        attribution = entry.get("attribution")
        if attribution not in ALLOWED_TIE_ATTRIBUTIONS:
            return {
                "required": True,
                "resolved": False,
                "attributions": resolved,
                "reason": (
                    f"{path} entry for pair {pair_id!r} has an invalid attribution "
                    f"{attribution!r} (expected one of {sorted(ALLOWED_TIE_ATTRIBUTIONS)})"
                ),
            }

        baseline_index = pair["trial_indices"][pair["baseline_arm_id"]]
        treatment_index = pair["trial_indices"][pair["treatment_arm_id"]]
        expected_hashes = {
            str(baseline_index): fingerprint(raw_by_index[baseline_index]),
            str(treatment_index): fingerprint(raw_by_index[treatment_index]),
        }
        recorded_hashes = entry.get("trial_sha256")
        normalized_recorded = (
            {str(key): value for key, value in recorded_hashes.items()}
            if isinstance(recorded_hashes, Mapping)
            else None
        )
        if normalized_recorded != expected_hashes:
            return {
                "required": True,
                "resolved": False,
                "attributions": resolved,
                "reason": (
                    f"{path} entry for pair {pair_id!r} has a trial_sha256 mismatch against "
                    "the committed trials"
                ),
            }

        mechanical_label = (
            "zero_tie"
            if pair["baseline_normalized"] == 0 and pair["treatment_normalized"] == 0
            else "nonzero_tie"
        )
        resolved[pair_id] = {
            "attribution": attribution,
            "mechanical_label": mechanical_label,
            "transcript_finding": entry.get("transcript_finding"),
            "final_state_finding": entry.get("final_state_finding"),
            "counterfactual_fixture_result": entry.get("counterfactual_fixture_result"),
        }

    return {"required": True, "resolved": True, "attributions": resolved, "reason": None}


# ---------------------------------------------------------------------------
# Block outcome
# ---------------------------------------------------------------------------


def _block_outcome(
    calibration: Mapping[str, object],
    metric_fidelity: Mapping[str, object],
    tie_attribution: Mapping[str, object],
) -> str:
    if not metric_fidelity["ok"]:
        return BLOCK_OUTCOME_METRIC_FIDELITY_FAILED

    if calibration["sensitivity_ok"]:
        if calibration["direction_ok"] and calibration["effect_ok"]:
            return BLOCK_OUTCOME_PASS
        return BLOCK_OUTCOME_MODEL_NULL

    if not tie_attribution["resolved"]:
        return BLOCK_OUTCOME_TIE_ATTRIBUTION_REQUIRED

    values = {entry["attribution"] for entry in tie_attribution["attributions"].values()}
    # Mixed attribution -- even one consequential tie caused by rubric
    # insensitivity -- is ALWAYS nondiscriminative, never a selective
    # discard of just that pair (see module docstring / design doc).
    if "rubric_nondiscriminative" in values:
        return BLOCK_OUTCOME_NONDISCRIMINATIVE
    if values <= {"model_floor"}:
        return BLOCK_OUTCOME_MODEL_FLOOR_NULL
    return BLOCK_OUTCOME_MODEL_TIE_NULL


# ---------------------------------------------------------------------------
# Campaign verdict
# ---------------------------------------------------------------------------


def _campaign_verdict(blocks_report: Mapping[str, Mapping[str, object]], gemma_id: str, qwen_id: str) -> dict[str, object]:
    gemma = blocks_report[gemma_id]

    if gemma["status"] != BLOCK_STATUS_COMPLETE:
        return {
            "outcome": CAMPAIGN_OUTCOME_BLOCKED,
            "reason": f"primary block {gemma_id!r} is not complete",
        }

    gemma_outcome = gemma["outcome"]
    if gemma_outcome == BLOCK_OUTCOME_NONDISCRIMINATIVE:
        return {
            "outcome": CAMPAIGN_OUTCOME_RUBRIC_NONDISCRIMINATIVE,
            "reason": f"block {gemma_id!r} is {gemma_outcome}",
        }
    if gemma_outcome in _BLOCKING_BLOCK_OUTCOMES:
        return {"outcome": CAMPAIGN_OUTCOME_BLOCKED, "reason": f"block {gemma_id!r} is {gemma_outcome}"}

    qwen = blocks_report[qwen_id]
    if qwen["status"] != BLOCK_STATUS_COMPLETE:
        if gemma_outcome == BLOCK_OUTCOME_PASS:
            return {
                "outcome": CAMPAIGN_OUTCOME_CALIBRATED_REPLICATION_DEFERRED,
                "reason": f"{gemma_id!r} passed and {qwen_id!r} admission is validly deferred",
            }
        return {
            "outcome": CAMPAIGN_OUTCOME_BLOCKED,
            "reason": (
                f"{gemma_id!r} did not pass ({gemma_outcome}) and {qwen_id!r} admission "
                "could not be completed -- Qwen execution is required before any verdict "
                "is possible when the primary block does not pass"
            ),
        }

    qwen_outcome = qwen["outcome"]
    if qwen_outcome == BLOCK_OUTCOME_NONDISCRIMINATIVE:
        return {
            "outcome": CAMPAIGN_OUTCOME_RUBRIC_NONDISCRIMINATIVE,
            "reason": f"block {qwen_id!r} is {qwen_outcome}",
        }
    if qwen_outcome in _BLOCKING_BLOCK_OUTCOMES:
        return {"outcome": CAMPAIGN_OUTCOME_BLOCKED, "reason": f"block {qwen_id!r} is {qwen_outcome}"}

    if BLOCK_OUTCOME_PASS in (gemma_outcome, qwen_outcome):
        return {"outcome": CAMPAIGN_OUTCOME_CALIBRATED, "reason": "at least one admitted model block passed"}

    return {
        "outcome": CAMPAIGN_OUTCOME_BLOCKED,
        "reason": f"neither admitted model block passed ({gemma_id}={gemma_outcome}, {qwen_id}={qwen_outcome})",
    }


# ---------------------------------------------------------------------------
# build_campaign_report
# ---------------------------------------------------------------------------


def build_campaign_report(campaign_dir: str | Path) -> dict[str, object]:
    """Derive a full campaign report purely from `<campaign_dir>/campaign.json`,
    `<campaign_dir>/schedule.json`, each block's `blocks/<block-id>/...` run
    directory, `blocks/<block-id>/audit.json`,
    `blocks/<block-id>/tie_attribution.json`, and `admissions/` (disposition
    scan only). Never reads any `attempts/` directory.

    Raises `CampaignReportError` for a structurally broken/tampered campaign
    directory, or an incomplete schedule with no valid Qwen replication
    deferral. Per-block fingerprint/rubric/predicate failures propagate from
    the underlying `benchmark_report.build_report` call, wrapped in
    `CampaignReportError`.
    """
    campaign_dir = Path(campaign_dir)
    lock = _read_json(campaign_dir / "campaign.json")
    if not isinstance(lock, Mapping):
        raise CampaignReportError(f"campaign.json must be a JSON object, got {type(lock).__name__}")

    campaign_fingerprint = lock.get("campaign_fingerprint")
    if not campaign_fingerprint:
        raise CampaignReportError("campaign.json is missing a non-empty 'campaign_fingerprint'")

    models = lock.get("models")
    if not isinstance(models, Sequence) or isinstance(models, (str, bytes)) or len(models) != 2:
        raise CampaignReportError("campaign.json must declare exactly two model blocks under 'models'")
    block_ids = [str(model["block_id"]) for model in models]
    gemma_id, qwen_id = block_ids[0], block_ids[1]

    arms = lock.get("arms")
    if not isinstance(arms, Sequence) or isinstance(arms, (str, bytes)) or len(arms) != 2:
        raise CampaignReportError("campaign.json must declare exactly two arms under 'arms'")
    baseline_arm_id = str(arms[0]["arm_id"])
    treatment_arm_id = str(arms[1]["arm_id"])

    rules = _require_calibration_rules(lock.get("rules"))

    audit_indices_raw = lock.get("audit_indices")
    if (
        not isinstance(audit_indices_raw, Sequence)
        or isinstance(audit_indices_raw, (str, bytes))
        or not audit_indices_raw
    ):
        raise CampaignReportError("campaign.json is missing a non-empty 'audit_indices' list")
    audit_indices = [int(i) for i in audit_indices_raw]

    position_id = lock.get("position_id")

    schedule = _read_json(campaign_dir / "schedule.json")
    if not isinstance(schedule, Mapping) or not isinstance(schedule.get("blocks"), Mapping):
        raise CampaignReportError("schedule.json is missing a 'blocks' mapping")

    lock_schedule_fingerprint = (lock.get("digests") or {}).get("schedule")
    if lock_schedule_fingerprint:
        actual_schedule_fingerprint = fingerprint(schedule)
        if actual_schedule_fingerprint != lock_schedule_fingerprint:
            raise CampaignReportError(
                "schedule.json does not match campaign.json's declared digests.schedule "
                f"(expected {lock_schedule_fingerprint!r}, found "
                f"{actual_schedule_fingerprint!r}) -- refusing to score a campaign whose "
                "schedule may have been tampered with"
            )

    contracts = lock.get("contracts")
    campaign_scorer_fingerprint = contracts.get("scorer_fingerprint") if isinstance(contracts, Mapping) else None

    blocks_report: dict[str, object] = {}
    for block_index, block_id in enumerate(block_ids):
        is_primary = block_index == 0
        block_schedule = schedule["blocks"].get(block_id)
        if not isinstance(block_schedule, Mapping) or not isinstance(block_schedule.get("trials"), Sequence):
            raise CampaignReportError(f"schedule.json has no trial list for block {block_id!r}")
        expected_indices = sorted(int(entry["index"]) for entry in block_schedule["trials"])

        block_dir = campaign_dir / "blocks" / block_id
        trials_dir = block_dir / "trials"
        committed_indices = {index for index in expected_indices if (trials_dir / trial_filename(index)).is_file()}
        complete = committed_indices == set(expected_indices)

        if not complete:
            if is_primary:
                raise CampaignReportError(
                    f"block {block_id!r} (the mandatory primary block) has an incomplete "
                    f"schedule ({len(committed_indices)}/{len(expected_indices)} trials "
                    "committed) -- refusing to build an official campaign report over an "
                    "incomplete primary schedule"
                )
            if not _has_valid_replication_deferred_admission(campaign_dir, block_id):
                raise CampaignReportError(
                    f"block {block_id!r} has an incomplete schedule "
                    f"({len(committed_indices)}/{len(expected_indices)} trials committed) and "
                    f"no valid {REPLICATION_DEFERRED_ADMISSION!r} admission record is present "
                    "in admissions/ -- refusing to build an official campaign report over an "
                    "incomplete schedule"
                )
            blocks_report[block_id] = {
                "status": BLOCK_STATUS_ADMISSION_DEFERRED,
                "committed_trial_count": len(committed_indices),
                "expected_trial_count": len(expected_indices),
            }
            continue

        try:
            block_report = benchmark_report.build_report(block_dir)
        except ReportError as exc:
            raise CampaignReportError(f"block {block_id!r} report failed: {exc}") from exc

        block_session = _read_json(block_dir / "session.json")
        if not isinstance(block_session, Mapping):
            raise CampaignReportError(f"{block_dir / 'session.json'} must be a JSON object")
        if block_session.get("campaign_fingerprint") != campaign_fingerprint:
            raise CampaignReportError(
                f"block {block_id!r} session.json campaign_fingerprint "
                f"{block_session.get('campaign_fingerprint')!r} does not match this "
                f"campaign's campaign_fingerprint {campaign_fingerprint!r}"
            )
        effective_position_id = position_id or block_session.get("position_id")
        if position_id is not None and block_session.get("position_id") != position_id:
            raise CampaignReportError(
                f"block {block_id!r} session.json position_id "
                f"{block_session.get('position_id')!r} does not match campaign.json's "
                f"declared position_id {position_id!r}"
            )
        block_scorer_fingerprint = block_report["scorer"]["fingerprint"]
        if campaign_scorer_fingerprint and block_scorer_fingerprint != campaign_scorer_fingerprint:
            raise CampaignReportError(
                f"block {block_id!r} scorer_fingerprint {block_scorer_fingerprint!r} does not "
                f"match campaign.json's contracts.scorer_fingerprint "
                f"{campaign_scorer_fingerprint!r}"
            )

        scored_by_index = _scored_by_index_from_block_report(block_report, str(effective_position_id))
        raw_by_index = {index: _read_json(trials_dir / trial_filename(index)) for index in expected_indices}

        calibration = _calibration_section(
            scored_by_index,
            expected_pair_count=int(rules["pairs_per_model"]),
            baseline_arm_id=baseline_arm_id,
            treatment_arm_id=treatment_arm_id,
            rules=rules,
        )
        metric_fidelity = _metric_fidelity_section(
            block_dir, block_session, audit_indices, raw_by_index, scored_by_index
        )
        tied_pairs = [pair for pair in calibration["pairs"] if not pair["decided"]]
        tie_attribution = _tie_attribution_section(block_dir, block_session, tied_pairs, raw_by_index)
        outcome = _block_outcome(calibration, metric_fidelity, tie_attribution)

        blocks_report[block_id] = {
            "status": BLOCK_STATUS_COMPLETE,
            "report": block_report,
            "calibration": calibration,
            "metric_fidelity": metric_fidelity,
            "tie_attribution": tie_attribution,
            "outcome": outcome,
        }

    verdict = _campaign_verdict(blocks_report, gemma_id, qwen_id)

    report: dict[str, object] = {
        "campaign": {
            "campaign_id": lock.get("campaign_id"),
            "campaign_fingerprint": campaign_fingerprint,
            "campaign_schema_version": lock.get("campaign_schema_version"),
            "position_id": position_id,
            "contracts": dict(contracts) if isinstance(contracts, Mapping) else contracts,
            "rules": rules,
            "audit_indices": audit_indices,
            "arms": {"baseline": baseline_arm_id, "treatment": treatment_arm_id},
            "models": [{"block_id": str(model["block_id"]), "model": model.get("model")} for model in models],
            "tool_surface_fingerprint": lock.get("tool_surface_fingerprint"),
            "digests": lock.get("digests"),
        },
        "blocks": blocks_report,
        "verdict": verdict,
    }
    return report


# ---------------------------------------------------------------------------
# render_campaign_markdown
# ---------------------------------------------------------------------------


def _render_calibration_table(calibration: Mapping[str, object]) -> list[str]:
    lines = [
        "| pair | baseline_normalized | treatment_normalized | signed_delta | decided |",
        "|---|---|---|---|---|",
    ]
    for pair in calibration["pairs"]:
        lines.append(
            "| {pair_id} | {baseline} | {treatment} | {delta} | {decided} |".format(
                pair_id=pair["pair_id"],
                baseline=_fmt(pair["baseline_normalized"]),
                treatment=_fmt(pair["treatment_normalized"]),
                delta=_fmt(pair["signed_delta"]),
                decided=pair["decided"],
            )
        )
    lines.append("")
    return lines


def render_campaign_markdown(report: Mapping[str, object]) -> str:
    """Render `report` (a `build_campaign_report` mapping) to Markdown.
    Blocks render in sorted order and every mapping is walked in sorted key
    order, so rendering is deterministic regardless of dict insertion order.
    Carries no generation-time timestamp."""
    lines: list[str] = ["# Arena calibration campaign report", ""]

    campaign = report["campaign"]
    lines.append(f"- Campaign id: {_fmt(campaign.get('campaign_id'))}")
    lines.append(f"- Campaign fingerprint: {_fmt(campaign.get('campaign_fingerprint'))}")
    lines.append(f"- Schema version: {_fmt(campaign.get('campaign_schema_version'))}")
    lines.append(f"- Position: {_fmt(campaign.get('position_id'))}")
    lines.append(f"- Baseline arm: {campaign['arms']['baseline']}")
    lines.append(f"- Treatment arm: {campaign['arms']['treatment']}")
    lines.append("")

    verdict = report["verdict"]
    lines.append(f"## Verdict: {verdict['outcome']}")
    lines.append("")
    lines.append(f"- Reason: {_fmt(verdict.get('reason'))}")
    lines.append("")

    for block_id in sorted(report["blocks"]):
        block = report["blocks"][block_id]
        lines.append(f"## Block: {block_id}")
        lines.append("")
        lines.append(f"- Status: {block['status']}")
        if block["status"] != BLOCK_STATUS_COMPLETE:
            lines.append(
                f"- Committed trials: {block.get('committed_trial_count')}/"
                f"{block.get('expected_trial_count')}"
            )
            lines.append("")
            continue

        lines.append(f"- Outcome: {block['outcome']}")
        lines.append("")

        calibration = block["calibration"]
        lines.append("### Calibration")
        lines.append("")
        lines.append(
            f"- Decided pairs: {calibration['decided_count']}/{calibration['pair_count']} "
            f"(threshold {calibration['thresholds']['minimum_decided_pairs']})"
        )
        lines.append(
            f"- Standard wins: {calibration['standard_wins']} "
            f"(threshold {calibration['thresholds']['minimum_standard_wins']})"
        )
        lines.append(
            f"- Median signed normalized delta: {_fmt(calibration['median_signed_delta'])} "
            f"(threshold {_fmt(calibration['thresholds']['minimum_median_normalized_delta'])})"
        )
        lines.append(
            f"- Sensitivity ok: {calibration['sensitivity_ok']}, "
            f"direction ok: {calibration['direction_ok']}, "
            f"effect ok: {calibration['effect_ok']}"
        )
        lines.append("")
        lines.extend(_render_calibration_table(calibration))

        metric_fidelity = block["metric_fidelity"]
        lines.append(f"### Metric fidelity: {'OK' if metric_fidelity['ok'] else 'FAILED'}")
        lines.append("")
        if not metric_fidelity["ok"]:
            lines.append(f"- Reason: {_fmt(metric_fidelity.get('reason'))}")
            for mismatch in metric_fidelity["mismatches"]:
                lines.append(f"  - index {mismatch['index']}: {mismatch['reason']}")
            lines.append("")

        tie_attribution = block["tie_attribution"]
        if tie_attribution["required"]:
            lines.append(f"### Tie attribution: {'RESOLVED' if tie_attribution['resolved'] else 'REQUIRED'}")
            lines.append("")
            if not tie_attribution["resolved"]:
                lines.append(f"- Reason: {_fmt(tie_attribution.get('reason'))}")
                lines.append("")
            else:
                for pair_id in sorted(tie_attribution["attributions"]):
                    entry = tie_attribution["attributions"][pair_id]
                    lines.append(
                        f"- {pair_id}: {entry['attribution']} "
                        f"(mechanical screen: {entry['mechanical_label']})"
                    )
                lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# write_campaign_reports + CLI
# ---------------------------------------------------------------------------


def write_campaign_reports(campaign_dir: str | Path) -> dict[str, object]:
    """Build the campaign report for `campaign_dir` and write
    `campaign_report.json` (canonical JSON) and `campaign_report.md`
    (rendered from that same mapping) into it. Returns the report mapping."""
    campaign_dir = Path(campaign_dir)
    report = build_campaign_report(campaign_dir)
    (campaign_dir / "campaign_report.json").write_bytes(_canonical_bytes(report))
    (campaign_dir / "campaign_report.md").write_text(render_campaign_markdown(report), encoding="utf-8")
    return report


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="civ-arena-benchmark-campaign-report")
    parser.add_argument("campaign_dir", help="path to a committed benchmark campaign directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    campaign_dir = Path(args.campaign_dir)
    try:
        write_campaign_reports(campaign_dir)
    except (CampaignReportError, ReportError, OSError, ValueError) as exc:
        print(f"civ-arena-benchmark-campaign-report: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {campaign_dir / 'campaign_report.json'} and {campaign_dir / 'campaign_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
