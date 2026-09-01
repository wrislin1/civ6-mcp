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
module's docstring). `admissions/` IS read, for exactly two purposes and
never passed to scoring: (a) to detect a typed
`REPLICATION_DEFERRED_ADMISSION` disposition, deciding whether an incomplete
Qwen schedule is a legitimate deferral rather than a refused report; and (b)
-- I1, external review wave I -- to require, for every COMPLETE block, the
counted admission SUCCESS record the real writer necessarily produced before
any trial ran (see `_require_counted_admission_success`). Every file this
module actually reads is enumerated, campaign-relative, in
`report["report_inputs"]`.

## Threat-model boundary (what this reporter can and cannot guarantee)

The reporter guarantees that any evidence tree it ACCEPTS is internally
consistent with the declared campaign: the fingerprint-anchored schedule,
session lock, committed trials, human audits, and admission records all
verify against each other and against the frozen campaign lock. It CANNOT
guarantee live provenance -- that these bytes came from a real run --
because every stamp it checks (campaign/session/admission/trial
fingerprints alike) is computable from public pure functions, so a
forger who re-mints EVERY artifact consistently produces a tree that is
indistinguishable at read time from a genuine one. Full metadata
relabeling with consistent re-minting of every artifact is therefore
detectable only by the anchors that live OUTSIDE the filesystem: the
published campaign contract, and the human metric-fidelity audits --
hash-bound to specific trial bytes. The audit anchor binds ONLY IF the
audits are performed against the live run's bytes before anyone could
re-mint (an audit run post-hoc over whatever is on disk anchors
nothing); the spec freezes the audit indices but does not yet codify
this ordering requirement, so it is an OPERATIONAL obligation on the
campaign runner until the spec does. Those external anchors, not this
reader, carry the final authority on live provenance.

## campaign.json integrity (verified before anything else)

`campaign.json["campaign_fingerprint"]` is independently recomputed here --
`fingerprint({k: v for k, v in lock.items() if k != "campaign_fingerprint"})`
-- and compared byte-for-byte against the recorded value, exactly mirroring
how `benchmark_campaign.build_campaign_lock` computed it in the first place
(`lock["campaign_fingerprint"] = fingerprint(lock)`, called before that key
existed on the dict). Editing any OTHER field in `campaign.json` (a
threshold, an audit index, a model config) while leaving
`campaign_fingerprint` untouched is exactly the tamper this check exists to
catch -- report generation aborts before anything else is even read from the
lock. `contracts`, `digests.schedule`, and `position_id` are then all
REQUIRED, non-optional fields (a missing one aborts report generation; the
corresponding block-vs-lock cross-checks below are unconditional, never
presence-gated).

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
    median_signed_normalized_delta = statistics.median(deltas)   # over ALL declared pairs

A block passes when `decided >= rules["minimum_decided_pairs"]` AND
`standard_wins >= rules["minimum_standard_wins"]` AND
`median_signed_normalized_delta >= rules["minimum_median_normalized_delta"]`.
All three thresholds come from the frozen `campaign.json["rules"]` -- never
hardcoded here. `rules["minimum_decided_pairs"]` may never exceed
`rules["pairs_per_model"]` (a rule that no block could ever satisfy, and
whose failure mode would leave nothing for a human tie review to attribute)
-- `_require_calibration_rules` rejects such a campaign lock outright.

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
    TIE_ATTRIBUTION_REQUIRED  tie-heavy and not (yet) fully reviewed -- OR
                             sensitivity failed with no tied pairs to review
                             at all (only possible for a malformed rules
                             config, which `_require_calibration_rules`
                             already rejects) -- pending, not a final
                             verdict; forces the campaign outcome to BLOCKED
    METRIC_FIDELITY_FAILED  the block's audit.json is missing, disagrees, or
                             is hash-stale against the actually committed
                             trials -- forces the campaign outcome to BLOCKED

Mechanical zero/nonzero tie labels (`mechanical_label` on each resolved
attribution) are recorded purely for legibility; they never by themselves
decide `MODEL_FLOOR_NULL` vs `MODEL_TIE_NULL` vs `NONDISCRIMINATIVE` -- only
a reviewed `tie_attribution.json` entry does (see module docstring section
above and `_tie_attribution_section`). A tie-derived outcome additionally
requires a NON-EMPTY reviewed-attributions mapping -- an empty mapping
(reachable only if `tied_pairs` were empty while sensitivity still failed)
must never be read as "every (zero) tied pair was attributed model_floor."

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

## Report content

Beyond the verdict/calibration/audit sections above, each completed block's
section also carries `model_config` and `model_admission` verbatim from that
block's own `session.json` -- the locked model configuration (endpoint id,
sampling, chat template kwargs) and the locked, already-trimmed admission
evidence (resolved endpoint, GPU topology, registry fingerprint; see
`benchmark_gates.locked_model_admission_evidence`), never recomputed here.
`report["campaign"]["models"]` echoes `campaign.json["models"]` in full
(every `ModelBlockConfig` field, not a curated subset). Every audited
trial's hash is recorded in `metric_fidelity["entries"]` regardless of
whether it agreed (`metric_fidelity["mismatches"]` is the filtered subset
that didn't), and every pair's two trial hashes are recorded in
`calibration["pairs"][i]["trial_sha256"]`.

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
from civ_mcp.arena.benchmark_store import (
    canonical_json_bytes,
    compute_session_fingerprint,
    trial_filename,
)

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

# Ruling G (external review wave D, finding D2) -- mirrors
# benchmark_admission.REPLICATION_DEFERRAL_ELIGIBLE_CODES exactly (same
# no-heavy-import rationale as the constant above; the two frozensets are
# asserted equal by tests/arena/test_benchmark_admission.py). Deferral
# eligibility is an explicit ALLOWLIST of model-capability gate failure
# codes: the endpoint/model-identity gate, the tool-canary gate, and the
# backend pre-flight probe. A disposition whose underlying_failure.code is
# outside this set is NEVER a valid replication deferral, no matter how
# well corroborated -- an operator/environment/authoring failure (dirty
# checkout, tuner holder, GPU conflict, boot/deploy/reload/popup/
# canonical-state, config errors; G5, wave G: treatment_cannot_fire, a
# model-independent position-authoring property; G2/Ruling H, wave G:
# backend_auth_error / backend_transport_error, operator credential/
# environment failures) must be fixed, never deferred around. Defense in
# depth: the runner already refuses to WRITE such a disposition; this
# reader independently refuses to HONOR one.
REPLICATION_DEFERRAL_ELIGIBLE_CODES = frozenset(
    {
        "endpoint_identity_mismatch",
        "missing_tool_canary",
        "tool_canary_failed",
        "insufficient_warm_latency_samples",
        "backend_probe_errors",
        "seed_not_honored",
    }
)

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


# H1 (external review wave H): the shared canonical encoding
# (`benchmark_store.canonical_json_bytes`) -- the same helper
# `CampaignStore.open_block`'s write-time schedule comparison builds on, so
# this reader's re-verification of that invariant can never drift from the
# writer's encoding.
_canonical_bytes = canonical_json_bytes


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_evidence(path: Path, context: str) -> object:
    """Every evidence read in this module goes through here (D9, external
    review wave D): a truncated write mid-multibyte-sequence raises
    UnicodeDecodeError from `read_text` before `json.loads` ever runs, and
    an unreadable file raises OSError -- all three failure shapes (plus
    JSONDecodeError) must surface as the module's typed
    `CampaignReportError` naming the file and what it was being read as,
    never a raw decoding exception."""
    try:
        return _read_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CampaignReportError(
            f"failed to read {context} evidence at {path}: {exc} -- refusing to build a "
            "campaign report over unreadable or corrupt evidence"
        ) from exc


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


# ---------------------------------------------------------------------------
# campaign.json integrity
# ---------------------------------------------------------------------------


def _verify_campaign_fingerprint(lock: Mapping[str, object], campaign_fingerprint: str) -> None:
    """Recompute `campaign_fingerprint` over every OTHER field of `lock` and
    require it to match byte-for-byte -- mirrors exactly how
    `benchmark_campaign.build_campaign_lock` computed it in the first place
    (fingerprint the lock dict, THEN add the `campaign_fingerprint` key).
    An edited threshold/audit-index/model-config with the stamp left
    untouched is undetectable any other way, since every block merely
    echoes this same opaque string rather than re-deriving it."""
    body = {key: value for key, value in lock.items() if key != "campaign_fingerprint"}
    expected = fingerprint(body)
    if expected != campaign_fingerprint:
        raise CampaignReportError(
            "campaign.json's campaign_fingerprint does not match a fresh fingerprint of its "
            "own remaining contents -- refusing to trust a campaign lock that may have been "
            "edited after it was frozen"
        )


# ---------------------------------------------------------------------------
# admissions/ -- read-only, disposition-only (never passed to scoring)
# ---------------------------------------------------------------------------


def _admission_record_paths(campaign_dir: Path, block_id: str) -> list[Path]:
    admissions_dir = campaign_dir / "admissions"
    if not admissions_dir.is_dir():
        return []
    prefix = f"{block_id}-attempt-"
    return [
        path
        for path in sorted(admissions_dir.iterdir())
        if path.is_file() and path.name.startswith(prefix) and path.name.endswith(".json")
    ]


def _admission_records(campaign_dir: Path, block_id: str) -> list[dict]:
    """Every admission-attempt record for `block_id`, in on-disk (numbered,
    i.e. chronological) order -- ordinary failed-gate records, remediation
    records, and disposition records alike. Tolerates a missing
    `admissions/` directory and skips any file that fails to parse as JSON --
    admission evidence is diagnostic, not scoreable, so a corrupt record here
    must never abort report generation the way corrupt trial evidence does."""
    records: list[dict] = []
    for path in _admission_record_paths(campaign_dir, block_id):
        try:
            record = _read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(record, Mapping):
            records.append(dict(record))
    return records


def _admission_dispositions(campaign_dir: Path, block_id: str) -> list[dict]:
    """Every admission-attempt record for `block_id` that declares a typed
    `disposition` field, in on-disk (numbered) order."""
    return [record for record in _admission_records(campaign_dir, block_id) if "disposition" in record]


def _has_valid_replication_deferred_admission(
    campaign_dir: Path, block_id: str, campaign_fingerprint: str
) -> bool:
    """True only for a CORROBORATED `REPLICATION_DEFERRED_ADMISSION` record
    for `block_id` -- finding 4 (final review) plus external review finding
    A1: a single admission failure of ANY code must never mint this
    disposition by itself. Spec: "Deferral requires at least one journaled
    retry after a concrete remediation, or two confirming attempts for a
    demonstrated non-remediable capability failure. Unknown failures cannot
    be converted into a deferral."

    All of the following must hold for at least one on-disk disposition
    record:

    - the referenced `underlying_failure.code` is present and is in the
      Ruling G allowlist `REPLICATION_DEFERRAL_ELIGIBLE_CODES` (D2,
      external review wave D) -- a model-capability gate failure code.
      An unclassified (`unexpected_admission_error`) or operator-error
      code can never become a deferral no matter how many times it
      repeats or how it was remediated; and
    - corroboration precedes that disposition record in the same
      append-only admissions/ sequence, in exactly ONE shape: at least
      TWO failed admission attempts (at distinct, strictly-preceding
      attempt ordinals) carrying the SAME code AND `mode: "counted"`.

      Ruling K (external review wave I, finding I2): corroborating
      failure records must carry `mode: "counted"` -- the mode the
      disposition-writing CLI path (`benchmark_runner._run_campaign_async`)
      actually runs `admit()` in, so every genuine corroborating failure
      (the disposition's own underlying failure record included) is a
      counted-mode record. The old predicate was mode-blind: two
      `admit_only` DIAGNOSTIC invocations' failure records (written
      through the same `admissions/<block>-attempt-NNN.json` path --
      only `mode="validation"` is diverted to `validation/`) corroborated
      a deferral, letting non-counting diagnostics stand in for the two
      independent counted observations the spec demands. A record with
      no `mode` field at all likewise never corroborates (the default
      points in the non-deferral-eligible direction).

      Ruling J (external review wave H, finding I2): the former
      remediation OR-branch ("a same-code failure before AND after a
      successful remediation") is DELETED as dead code, not as a policy
      change -- exhaustive enumeration proved it added zero accepting
      power. Its own requirements imply at least two same-code failures
      strictly before the disposition (one before the remediation, one
      after), so every sequence it accepted was already accepted by the
      two-failures predicate above. The spec's "at least one journaled
      retry after a concrete remediation" path is therefore SUBSUMED, not
      dropped: a post-remediation retry that fails IS the second
      same-code observation.

      A1 (external review): the real CLI writes exactly ONE failed-gate
      attempt record (the failure `admit()` itself just hit) and THEN, in
      the very same invocation, writes the disposition record referencing
      that same failure as `underlying_failure` -- so `records[:index]`
      (everything strictly before the disposition) always contains that
      one un-retried failure. Requiring only ONE preceding same-code
      failure therefore let a single admission attempt mint a deferral by
      itself (the disposition's own triggering failure double-counted as
      its own corroboration). Genuine corroboration requires the failure to
      have been independently observed at least TWICE before the
      disposition is ever written -- i.e. at least two preceding attempt
      records, not one.

    G4 (external review wave G): only records stamped with THIS campaign's
    `campaign_fingerprint` participate at all -- an unstamped record, or
    one stamped for a different campaign, corroborates nothing and can
    never be the disposition record itself (`CampaignStore.record_admission`
    stamps every admission-attempt/remediation/disposition record it
    writes; no unstamped live evidence exists, so requiring the stamp is
    safe).
    """
    records = [
        record
        for record in _admission_records(campaign_dir, block_id)
        if record.get("campaign_fingerprint") == campaign_fingerprint
    ]
    for index, record in enumerate(records):
        if record.get("disposition") != REPLICATION_DEFERRED_ADMISSION:
            continue
        if record.get("block_id") != block_id:
            continue
        underlying = record.get("underlying_failure")
        code = underlying.get("code") if isinstance(underlying, Mapping) else None
        if not code or code not in REPLICATION_DEFERRAL_ELIGIBLE_CODES:
            continue
        preceding = records[:index]
        same_code_failure_ordinals = [
            i
            for i, r in enumerate(preceding)
            if r.get("ok") is False
            # Ruling K (wave I, I2): only COUNTED-mode failures corroborate
            # -- an admit_only diagnostic (or a mode-less record) never does.
            and r.get("mode") == "counted"
            and isinstance(r.get("failure"), Mapping)
            and r["failure"].get("code") == code
        ]
        # Ruling J (wave H): the whole predicate. No remediation records
        # are consulted -- see the docstring's subsumption argument.
        if len(same_code_failure_ordinals) >= 2:
            return True
    return False


def _require_counted_admission_success(
    campaign_dir: Path,
    block_id: str,
    campaign_fingerprint: str,
    session_fingerprint: str,
) -> None:
    """I1 (external review wave I): every COMPLETE block must be anchored
    by at least one counted admission SUCCESS record in `admissions/` --
    `ok: true`, `mode: "counted"`, stamped with THIS campaign's
    `campaign_fingerprint` (`CampaignStore.record_admission` stamps every
    record it writes), and carrying a `session_fingerprint` equal to the
    block's own re-derived session fingerprint.

    Writer parity: `AdmissionPipeline.admit(mode="counted")` writes exactly
    this record via `CampaignStore.record_admission` immediately after
    `open_block` mints (or reattaches to) `session.json` -- and BEFORE any
    trial can run, because trials only run through the `ResolvedBlock` that
    same `admit()` call returns afterwards. The writer therefore cannot
    produce a complete block without this record, so a complete block
    lacking one is substituted/forged evidence, refused with a typed error.
    (The converse -- a record without trials, left by a crash between
    admission and completion -- is fine: the block is simply incomplete and
    never reaches this check.) An `ok: true` record from a NON-counting
    `admit_only` diagnostic (which proves a session COULD be minted without
    minting one) never anchors a complete block; nor does a record whose
    stamp names another campaign or another session. Deferred/incomplete
    blocks are exempt -- their absence of a session is handled by the
    deferral path."""
    for record in _admission_records(campaign_dir, block_id):
        if (
            record.get("ok") is True
            and record.get("mode") == "counted"
            # block_id is bound by field, not just filename prefix -- same
            # discipline as _has_valid_replication_deferred_admission above.
            and record.get("block_id") == block_id
            and record.get("campaign_fingerprint") == campaign_fingerprint
            and record.get("session_fingerprint") == session_fingerprint
        ):
            return
    raise CampaignReportError(
        f"block {block_id!r} has a complete schedule but no counted admission SUCCESS "
        "record in admissions/ (ok=true, mode='counted', stamped with this campaign's "
        "campaign_fingerprint and this block's session_fingerprint) -- the real writer "
        "always records one before any trial runs, so a complete block without one is "
        "substituted or forged evidence; refusing to score it"
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
    rules = dict(rules)
    if rules["minimum_decided_pairs"] > rules["pairs_per_model"]:
        raise CampaignReportError(
            "campaign.json 'rules' declares minimum_decided_pairs "
            f"({rules['minimum_decided_pairs']!r}) greater than pairs_per_model "
            f"({rules['pairs_per_model']!r}) -- no block could ever satisfy sensitivity, and "
            "a resulting tie review would have zero tied pairs to attribute"
        )
    # D5 (external review wave D), defense in depth (mirrors
    # benchmark_contract._load_calibration_rules -- a hand-authored,
    # self-consistently-fingerprinted campaign.json never went through
    # that loader): with all minimums at -1, twelve 0-0 ties would satisfy
    # every gate and report PASS. Every threshold must be structurally
    # meaningful before any gate arithmetic runs.
    if rules["pairs_per_model"] < 1:
        raise CampaignReportError(
            f"campaign.json 'rules' pairs_per_model must be >= 1 (got {rules['pairs_per_model']!r})"
        )
    # G6 (external review wave G): >= 1, not >= 0 -- a ZERO threshold is
    # just as vacuous as a negative one (every block trivially satisfies
    # decided >= 0 / wins >= 0, switching the sensitivity/direction gate
    # off entirely). Mirrors benchmark_contract._load_calibration_rules.
    if rules["minimum_decided_pairs"] < 1:
        raise CampaignReportError(
            "campaign.json 'rules' minimum_decided_pairs must be >= 1 "
            f"(got {rules['minimum_decided_pairs']!r}) -- a zero or negative sensitivity "
            "threshold switches the sensitivity gate off entirely"
        )
    if rules["minimum_standard_wins"] < 1:
        raise CampaignReportError(
            "campaign.json 'rules' minimum_standard_wins must be >= 1 "
            f"(got {rules['minimum_standard_wins']!r}) -- a zero or negative direction "
            "threshold switches the direction gate off entirely"
        )
    if rules["minimum_standard_wins"] > rules["minimum_decided_pairs"]:
        raise CampaignReportError(
            f"campaign.json 'rules' minimum_standard_wins ({rules['minimum_standard_wins']!r}) "
            f"must not exceed minimum_decided_pairs ({rules['minimum_decided_pairs']!r}) -- a "
            "standard win is a decided pair, so requiring more wins than decided pairs is "
            "unsatisfiable arithmetic"
        )
    if rules["minimum_median_normalized_delta"] <= 0:
        raise CampaignReportError(
            "campaign.json 'rules' minimum_median_normalized_delta must be > 0 "
            f"(got {rules['minimum_median_normalized_delta']!r}) -- a zero or negative effect "
            "threshold makes the effect gate vacuous"
        )
    if rules["required_audits_per_arm"] < 1:
        raise CampaignReportError(
            "campaign.json 'rules' required_audits_per_arm must be >= 1 "
            f"(got {rules['required_audits_per_arm']!r})"
        )
    return rules


def _calibration_section(
    scored_by_index: Mapping[int, Mapping[str, object]],
    *,
    raw_by_index: Mapping[int, Mapping[str, object]],
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
                # G-round-1 (finding 5): the computed hash of both raw
                # committed trials backing this pair, always recorded here
                # (not just when a tie-attribution file happens to cite it).
                "trial_sha256": {
                    baseline_arm_id: fingerprint(raw_by_index[baseline_trial["index"]]),
                    treatment_arm_id: fingerprint(raw_by_index[treatment_trial["index"]]),
                },
            }
        )

    decided_count = sum(1 for pair in pairs if pair["decided"])
    standard_wins = sum(1 for delta in deltas if delta > 0)
    median_signed_normalized_delta = statistics.median(deltas) if deltas else 0.0

    return {
        "pairs": pairs,
        "pair_count": len(pairs),
        "decided_count": decided_count,
        "tie_count": len(pairs) - decided_count,
        "standard_wins": standard_wins,
        "median_signed_normalized_delta": median_signed_normalized_delta,
        "thresholds": {
            "minimum_decided_pairs": rules["minimum_decided_pairs"],
            "minimum_standard_wins": rules["minimum_standard_wins"],
            "minimum_median_normalized_delta": rules["minimum_median_normalized_delta"],
        },
        "sensitivity_ok": decided_count >= rules["minimum_decided_pairs"],
        "direction_ok": standard_wins >= rules["minimum_standard_wins"],
        "effect_ok": median_signed_normalized_delta >= rules["minimum_median_normalized_delta"],
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

    `entries` records every audited index's computed hash and agree/disagree
    verdict UNCONDITIONALLY (finding 5: "record the computed hashes, not
    only the mismatches"); `mismatches` is the filtered subset that actually
    disagreed, kept for convenient rendering/inspection.
    """
    audit_path = block_dir / "audit.json"
    if not audit_path.is_file():
        return {
            "ok": False,
            "reason": f"{audit_path} is missing",
            "audit_indices": list(audit_indices),
            "entries": [],
            "mismatches": [],
        }
    # A7 (external review) / D9 (wave D): a truncated/corrupt/unreadable
    # audit.json must abort report generation with a typed, file-naming
    # error -- never a raw JSONDecodeError/UnicodeDecodeError/OSError, and
    # never silently folded into the "missing file" (ok=False) shape
    # above, which a corrupt file is NOT: unlike an absent file, corrupt
    # evidence may have once held a real verdict that a silent "absent"
    # treatment would erase.
    audit = _read_json_evidence(audit_path, "metric-fidelity audit")
    if not isinstance(audit, Mapping):
        return {
            "ok": False,
            "reason": f"{audit_path} must be a JSON object",
            "audit_indices": list(audit_indices),
            "entries": [],
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
            "entries": [],
            "mismatches": [],
        }

    entries_raw = audit.get("trials")
    file_entries: dict[int, Mapping[str, object]] = {}
    if isinstance(entries_raw, Sequence) and not isinstance(entries_raw, (str, bytes)):
        for entry in entries_raw:
            if isinstance(entry, Mapping) and isinstance(entry.get("index"), int):
                file_entries[entry["index"]] = entry

    missing = sorted(index for index in audit_indices if index not in file_entries)
    if missing:
        return {
            "ok": False,
            "reason": f"{audit_path} is missing audit entries for index(es) {missing}",
            "audit_indices": list(audit_indices),
            "entries": [],
            "mismatches": [],
        }

    entries: list[dict[str, object]] = []
    mismatches: list[dict[str, object]] = []
    for index in audit_indices:
        entry = file_entries[index]
        raw_trial = raw_by_index.get(index)
        scored_trial = scored_by_index.get(index)
        if raw_trial is None or scored_trial is None:
            problem = {"index": index, "reason": "audited index has no committed trial evidence"}
            entries.append({**problem, "trial_sha256": None, "agrees": False})
            mismatches.append(problem)
            continue

        expected_sha256 = fingerprint(raw_trial)
        recorded_sha256 = entry.get("trial_sha256")
        if recorded_sha256 != expected_sha256:
            problem = {
                "index": index,
                "reason": "trial_sha256 mismatch -- audited evidence no longer matches "
                "the committed trial",
                "recorded": recorded_sha256,
                "expected": expected_sha256,
            }
            entries.append({**problem, "trial_sha256": expected_sha256, "agrees": False})
            mismatches.append(problem)
            continue

        live_automatic = {
            "task_scores": {task_id: task["score"] for task_id, task in scored_trial["rubric"]["tasks"].items()},
            "useful_actions": scored_trial["action_quality"]["useful_actions"],
            "domain_rejections": scored_trial["action_quality"]["domain_rejections"],
            "repetitions": scored_trial["action_quality"]["repetitions"],
        }
        manual = entry.get("manual")
        agrees = manual == live_automatic
        entries.append(
            {
                "index": index,
                "trial_sha256": expected_sha256,
                "manual": manual,
                "live_automatic": live_automatic,
                "agrees": agrees,
            }
        )
        if not agrees:
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
        "entries": entries,
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
    treated as any particular attribution. An entry whose three
    human-review fields (`transcript_finding`, `final_state_finding`,
    `counterfactual_fixture_result`) are not all non-empty strings is a
    typed hard error (D7, external review wave D) -- an attribution
    without recorded findings is not a review at all."""
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
    # A7 (external review) / D9 (wave D): same discipline as the
    # audit.json read above -- a truncated/corrupt/unreadable
    # tie_attribution.json must hard-abort with a typed error naming the
    # file, never silently read as "not yet reviewed" and never a raw
    # decoding exception.
    payload = _read_json_evidence(path, "tie-attribution review")
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

        # D7 (external review wave D): the three human-review fields are
        # the actual review evidence -- an entry carrying only pair_id/
        # attribution/hashes is a mechanical stamp, not a review, and
        # honoring it would mint MODEL_FLOOR_NULL/MODEL_TIE_NULL with no
        # review having happened. Each must be a non-empty (non-blank)
        # string; anything less is a typed, fail-closed refusal naming
        # the pair and the missing field(s).
        review_fields = ("transcript_finding", "final_state_finding", "counterfactual_fixture_result")
        missing_review_fields = [
            field
            for field in review_fields
            if not isinstance(entry.get(field), str) or not entry.get(field).strip()
        ]
        if missing_review_fields:
            raise CampaignReportError(
                f"{path} entry for pair {pair_id!r} is missing non-empty human-review "
                f"field(s) {missing_review_fields} -- an attribution without recorded "
                "transcript/final-state/counterfactual findings is not a completed tie "
                "review and must never resolve a tie-derived outcome"
            )

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

    # G-round-1 (finding 3): a tie-derived outcome requires a genuinely
    # non-empty, fully reviewed attributions mapping -- `set() <= {"model_floor"}`
    # is vacuously true, so an EMPTY mapping must never fall through to
    # MODEL_FLOOR_NULL. This is unreachable through a well-formed rules
    # config (`_require_calibration_rules` already rejects
    # minimum_decided_pairs > pairs_per_model, the only way sensitivity can
    # fail with zero tied pairs), but the check stands on its own regardless
    # of that upstream guard.
    if not tie_attribution["resolved"] or not tie_attribution["attributions"]:
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
    lock = _read_json_evidence(campaign_dir / "campaign.json", "campaign lock")
    if not isinstance(lock, Mapping):
        raise CampaignReportError(f"campaign.json must be a JSON object, got {type(lock).__name__}")

    campaign_fingerprint = lock.get("campaign_fingerprint")
    if not campaign_fingerprint:
        raise CampaignReportError("campaign.json is missing a non-empty 'campaign_fingerprint'")
    # G-round-1 (finding 1): this is the LAST-resort gate a tampered lock
    # cannot pass by leaving campaign_fingerprint untouched -- see module
    # docstring "campaign.json integrity" and `_verify_campaign_fingerprint`.
    _verify_campaign_fingerprint(lock, str(campaign_fingerprint))

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

    # G-round-1 (finding 2c): position_id is now a MANDATORY lock field --
    # falling back to each block's own session.json's position_id (the old
    # behavior) would let two blocks at genuinely different positions be
    # compared against each other without ever being caught.
    position_id = lock.get("position_id")
    if not position_id:
        raise CampaignReportError("campaign.json is missing a non-empty 'position_id'")

    schedule = _read_json_evidence(campaign_dir / "schedule.json", "campaign schedule")
    if not isinstance(schedule, Mapping) or not isinstance(schedule.get("blocks"), Mapping):
        raise CampaignReportError("schedule.json is missing a 'blocks' mapping")

    # G-round-1 (finding 2b): digests.schedule is now MANDATORY -- the old
    # `if lock_schedule_fingerprint:` guard skipped verification entirely
    # whenever campaign.json simply omitted the digest, which is exactly
    # the tampered/incomplete-lock case this check exists to catch.
    digests = lock.get("digests")
    if not isinstance(digests, Mapping):
        raise CampaignReportError("campaign.json is missing a 'digests' mapping")
    lock_schedule_fingerprint = digests.get("schedule")
    if not lock_schedule_fingerprint:
        raise CampaignReportError("campaign.json 'digests' is missing a non-empty 'schedule' fingerprint")
    actual_schedule_fingerprint = fingerprint(schedule)
    if actual_schedule_fingerprint != lock_schedule_fingerprint:
        raise CampaignReportError(
            "schedule.json does not match campaign.json's declared digests.schedule "
            f"(expected {lock_schedule_fingerprint!r}, found "
            f"{actual_schedule_fingerprint!r}) -- refusing to score a campaign whose "
            "schedule may have been tampered with"
        )

    # G-round-1 (finding 2a): contracts.scorer_fingerprint is now MANDATORY
    # -- the old `if isinstance(contracts, Mapping) else None` plus
    # `if campaign_scorer_fingerprint and ...` skipped the per-block scorer
    # cross-check entirely whenever campaign.json simply omitted contracts.
    contracts = lock.get("contracts")
    if not isinstance(contracts, Mapping):
        raise CampaignReportError("campaign.json is missing a 'contracts' mapping")
    campaign_scorer_fingerprint = contracts.get("scorer_fingerprint")
    if not campaign_scorer_fingerprint:
        raise CampaignReportError("campaign.json 'contracts' is missing a non-empty 'scorer_fingerprint'")

    # Enumerates every file this function actually reads, campaign-relative
    # -- finding 5's "an enumeration of report inputs."
    report_inputs: list[str] = ["campaign.json", "schedule.json"]

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

        # H1(b) (external review wave H): re-verify at read time the exact
        # invariant `CampaignStore.open_block` enforces at write time --
        # blocks/<id>/schedule.json must equal the campaign schedule's
        # entry for that block (same shared canonical encoding,
        # `benchmark_store.canonical_json_bytes`). The campaign schedule is
        # digest-bound into campaign_fingerprint (digests.schedule,
        # verified above), so this closes the chain campaign_fingerprint ->
        # campaign schedule -> block schedule -> (via the per-trial
        # schedule binding in benchmark_report.build_report) every
        # committed trial's model/arm/seed identity. Required for a
        # complete block (open_block always writes it before any trial is
        # committed -- a complete block with no block schedule at all is
        # itself substituted evidence); for an incomplete (deferral-shaped)
        # block the file may legitimately be absent, but if it EXISTS it
        # must still match.
        block_schedule_path = block_dir / "schedule.json"
        if complete or block_schedule_path.is_file():
            recorded_block_schedule = _read_json_evidence(
                block_schedule_path, f"block {block_id!r} schedule"
            )
            if canonical_json_bytes(recorded_block_schedule) != canonical_json_bytes(
                dict(block_schedule)
            ):
                raise CampaignReportError(
                    f"block {block_id!r} schedule.json does not match the campaign "
                    "schedule's declared entry for this block "
                    "(schedule.json['blocks'][block_id]) -- refusing to score a block "
                    "whose local schedule diverges from the digest-anchored campaign "
                    "schedule"
                )
            report_inputs.append(f"blocks/{block_id}/schedule.json")

        if not complete:
            if is_primary:
                raise CampaignReportError(
                    f"block {block_id!r} (the mandatory primary block) has an incomplete "
                    f"schedule ({len(committed_indices)}/{len(expected_indices)} trials "
                    "committed) -- refusing to build an official campaign report over an "
                    "incomplete primary schedule"
                )
            # A2 (external review): replication deferral exists ONLY for an
            # admission failure that never created any evidence at all --
            # "Replication deferral is only for failures that create no
            # trials." A block that has ANY committed trial, or that
            # actually minted a counted session.json (proving admission
            # itself succeeded at some point), was NOT the zero-evidence
            # admission failure a deferral is meant to excuse -- honoring a
            # disposition record here regardless would let a genuinely
            # started-but-abandoned block masquerade as a clean deferral.
            # These checks apply unconditionally, before even looking for a
            # disposition record.
            if committed_indices:
                raise CampaignReportError(
                    f"block {block_id!r} has {len(committed_indices)} committed trial(s) "
                    f"despite an incomplete schedule ({len(committed_indices)}/"
                    f"{len(expected_indices)}) -- replication deferral is only valid for a "
                    "block that produced ZERO committed trials; refusing to honor any "
                    f"{REPLICATION_DEFERRED_ADMISSION!r} disposition here"
                )
            block_session_path = block_dir / "session.json"
            if block_session_path.is_file():
                raise CampaignReportError(
                    f"block {block_id!r} has a committed session.json at "
                    f"{block_session_path} despite an incomplete schedule -- a minted, "
                    "counted session lock means admission actually succeeded; refusing to "
                    f"honor any {REPLICATION_DEFERRED_ADMISSION!r} disposition for a block "
                    "that was actually admitted"
                )
            if not _has_valid_replication_deferred_admission(
                campaign_dir, block_id, str(campaign_fingerprint)
            ):
                raise CampaignReportError(
                    f"block {block_id!r} has an incomplete schedule "
                    f"({len(committed_indices)}/{len(expected_indices)} trials committed) and "
                    f"no valid {REPLICATION_DEFERRED_ADMISSION!r} admission record is present "
                    "in admissions/ -- refusing to build an official campaign report over an "
                    "incomplete schedule"
                )
            report_inputs.extend(
                f"admissions/{path.name}" for path in _admission_record_paths(campaign_dir, block_id)
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

        block_session = _read_json_evidence(block_dir / "session.json", f"block {block_id!r} session lock")
        if not isinstance(block_session, Mapping):
            raise CampaignReportError(f"{block_dir / 'session.json'} must be a JSON object")
        report_inputs.append(f"blocks/{block_id}/session.json")
        # G1 (external review wave G): the session lock's own
        # session_fingerprint is re-derived from the session document
        # itself (benchmark_store.compute_session_fingerprint -- the exact
        # computation build_session_lock minted it with) BEFORE any of its
        # fields are trusted. The fingerprint covers block_id and
        # model_config, so the proven exploit -- copy blocks/gemma to
        # blocks/qwen, edit BOTH bound fields to qwen's declared values,
        # leave session_fingerprint untouched so every trial's stamp still
        # "matches" -- fails here even though the D4 field comparisons
        # below would all pass. Exactly _verify_campaign_fingerprint's
        # discipline, applied to the per-block lock. Defense in depth:
        # benchmark_report.build_report and
        # benchmark_admission.block_is_complete independently apply the
        # same recomputation.
        recorded_session_fingerprint = block_session.get("session_fingerprint")
        if not recorded_session_fingerprint or (
            compute_session_fingerprint(block_session) != recorded_session_fingerprint
        ):
            raise CampaignReportError(
                f"block {block_id!r} session.json's session_fingerprint does not match a "
                "fresh fingerprint of its own remaining contents -- refusing to trust a "
                "session lock that may have been edited after it was minted"
            )
        if block_session.get("campaign_fingerprint") != campaign_fingerprint:
            raise CampaignReportError(
                f"block {block_id!r} session.json campaign_fingerprint "
                f"{block_session.get('campaign_fingerprint')!r} does not match this "
                f"campaign's campaign_fingerprint {campaign_fingerprint!r}"
            )
        # D4 (external review wave D): every block of one campaign shares
        # the same campaign/position/scorer fingerprints, so those three
        # cross-checks alone cannot catch blocks/gemma copied wholesale to
        # blocks/qwen. The session's own declared block identity must match
        # the block directory it is being scored under...
        if block_session.get("block_id") != block_id:
            raise CampaignReportError(
                f"block {block_id!r} session.json declares block_id "
                f"{block_session.get('block_id')!r} -- refusing to score evidence recorded "
                "for a different block (cross-block evidence substitution)"
            )
        # ...and its model_config must canonically equal campaign.json's
        # declared ModelBlockConfig for that block (canonical-JSON
        # comparison, never object identity), so a forged top-level
        # block_id alone cannot re-home another block's evidence.
        session_model_config = block_session.get("model_config")
        declared_model_config = dict(models[block_index])
        if (
            not isinstance(session_model_config, Mapping)
            or _canonical_bytes(dict(session_model_config)) != _canonical_bytes(declared_model_config)
        ):
            raise CampaignReportError(
                f"block {block_id!r} session.json model_config does not match campaign.json's "
                f"declared ModelBlockConfig for block {block_id!r} -- refusing to score "
                "evidence recorded under a different locked model configuration"
            )
        # G-round-1 (finding 2c continued): unconditional now that
        # position_id is a required lock field -- no more "only checked
        # when the lock happens to declare one."
        if block_session.get("position_id") != position_id:
            raise CampaignReportError(
                f"block {block_id!r} session.json position_id "
                f"{block_session.get('position_id')!r} does not match campaign.json's "
                f"declared position_id {position_id!r}"
            )
        block_scorer_fingerprint = block_report["scorer"]["fingerprint"]
        # G-round-1 (finding 2a continued): unconditional now that
        # contracts.scorer_fingerprint is required.
        if block_scorer_fingerprint != campaign_scorer_fingerprint:
            raise CampaignReportError(
                f"block {block_id!r} scorer_fingerprint {block_scorer_fingerprint!r} does not "
                f"match campaign.json's contracts.scorer_fingerprint "
                f"{campaign_scorer_fingerprint!r}"
            )
        # I1 (external review wave I): reporter-writer parity on the counted
        # admission SUCCESS record -- compared against the RE-DERIVED
        # session fingerprint (compute_session_fingerprint over the verified
        # session document; equal to the recorded value per the G1 check
        # above), never a value merely read off disk.
        _require_counted_admission_success(
            campaign_dir,
            block_id,
            str(campaign_fingerprint),
            compute_session_fingerprint(block_session),
        )
        report_inputs.extend(
            f"admissions/{path.name}" for path in _admission_record_paths(campaign_dir, block_id)
        )

        scored_by_index = _scored_by_index_from_block_report(block_report, str(position_id))
        raw_by_index: dict[int, Mapping[str, object]] = {}
        for index in expected_indices:
            raw_by_index[index] = _read_json_evidence(
                trials_dir / trial_filename(index), f"block {block_id!r} committed trial"
            )
            report_inputs.append(f"blocks/{block_id}/trials/{trial_filename(index)}")

        calibration = _calibration_section(
            scored_by_index,
            raw_by_index=raw_by_index,
            expected_pair_count=int(rules["pairs_per_model"]),
            baseline_arm_id=baseline_arm_id,
            treatment_arm_id=treatment_arm_id,
            rules=rules,
        )
        if (block_dir / "audit.json").is_file():
            report_inputs.append(f"blocks/{block_id}/audit.json")
        metric_fidelity = _metric_fidelity_section(
            block_dir, block_session, audit_indices, raw_by_index, scored_by_index
        )
        tied_pairs = [pair for pair in calibration["pairs"] if not pair["decided"]]
        if tied_pairs and (block_dir / "tie_attribution.json").is_file():
            report_inputs.append(f"blocks/{block_id}/tie_attribution.json")
        tie_attribution = _tie_attribution_section(block_dir, block_session, tied_pairs, raw_by_index)
        outcome = _block_outcome(calibration, metric_fidelity, tie_attribution)

        blocks_report[block_id] = {
            "status": BLOCK_STATUS_COMPLETE,
            "report": block_report,
            # Finding 5: model configuration (endpoint id, sampling, chat
            # template kwargs) and endpoint/GPU topology, as locked on this
            # block's own session.json -- verbatim, never recomputed.
            "model_config": block_session.get("model_config"),
            "model_admission": block_session.get("model_admission"),
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
            "contracts": dict(contracts),
            "rules": rules,
            "audit_indices": audit_indices,
            "arms": {"baseline": baseline_arm_id, "treatment": treatment_arm_id},
            # Finding 5: the FULL model configuration per block (endpoint
            # id, sampling, chat_template_kwargs, ...) -- not a curated
            # block_id/model subset.
            "models": [dict(model) for model in models],
            "tool_surface_fingerprint": lock.get("tool_surface_fingerprint"),
            "digests": dict(digests),
        },
        "blocks": blocks_report,
        "verdict": verdict,
        "report_inputs": sorted(set(report_inputs)),
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
    lines.append(f"- Scorer fingerprint: {_fmt(campaign.get('contracts', {}).get('scorer_fingerprint'))}")
    lines.append("")
    lines.append("### Model configuration")
    lines.append("")
    for model in campaign.get("models", []):
        lines.append(
            f"- {model.get('block_id')}: model={_fmt(model.get('model'))}, "
            f"endpoint_id={_fmt(model.get('endpoint_id'))}, "
            f"sampling={_fmt(model.get('sampling'))}, "
            f"chat_template_kwargs={_fmt(model.get('chat_template_kwargs'))}"
        )
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

        model_admission = block.get("model_admission") or {}
        lines.append("### Endpoint / GPU topology")
        lines.append("")
        lines.append(f"- Resolved model: {_fmt(model_admission.get('resolved_model'))}")
        lines.append(f"- Resolved endpoint: {_fmt(model_admission.get('resolved_endpoint'))}")
        lines.append(f"- GPU topology: {_fmt(model_admission.get('gpu_topology'))}")
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
            f"- Median signed normalized delta: {_fmt(calibration['median_signed_normalized_delta'])} "
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
        lines.append(f"- Audited indices: {metric_fidelity['audit_indices']}")
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

    lines.append("## Report inputs")
    lines.append("")
    for path in report.get("report_inputs", []):
        lines.append(f"- {path}")
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
