"""Tests for civ_mcp.arena.benchmark_campaign_report -- TDD: RED first, then
GREEN.

`benchmark_campaign_report` derives campaign-wide sensitivity/separation
arithmetic and the final campaign verdict purely from committed campaign-
directory evidence: `campaign.json`, `schedule.json`, each block's
`blocks/<block-id>/{session.json,schedule.json,trials/}`,
`blocks/<block-id>/audit.json`, `blocks/<block-id>/tie_attribution.json`, and
`admissions/` (disposition scan only, never scored). It must never read any
`attempts/` directory, and it must reuse `benchmark_report.build_report`/
`score_trial` verbatim rather than re-implementing scoring.

Fixtures use a single-task rubric with integer levels 0..12 (mirroring
`test_benchmark_report.py`'s own `_calibration_run` convention) so a trial's
normalized score is `achieved / 12` -- the same "N / 12" convention the
design doc uses for its worked calibration threshold example (4/12).

Every fixture's `campaign.json` carries a REAL `campaign_fingerprint`
(`fingerprint()` over its own remaining fields, exactly like
`benchmark_campaign.build_campaign_lock` computes it) so that
`build_campaign_report`'s own campaign-fingerprint self-check
(round-1 review finding 1) passes for every well-formed fixture; negative
tests for that check, and for the other now-mandatory lock fields
(`contracts`, `digests.schedule`, `position_id`), build a deliberately
incomplete-but-self-consistent lock via `_write_campaign_lock`'s
`include_*` flags rather than tampering a valid lock after the fact (which
would trip the fingerprint check instead of the more specific one under
test).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from civ_mcp.arena.benchmark_campaign_report import (
    REPLICATION_DEFERRED_ADMISSION,
    CampaignReportError,
    _block_outcome,
    build_campaign_report,
    render_campaign_markdown,
    write_campaign_reports,
)
from civ_mcp.arena.benchmark_manifest import fingerprint

POSITION_ID = "pos-cal"
BASELINE_ARM = "minimal"
TREATMENT_ARM = "standard"
MAX_SCORE = 12


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _rubric(max_score: int = MAX_SCORE) -> list[dict]:
    levels = [{"score": 0, "predicate": {"kind": "always"}}]
    for n in range(1, max_score + 1):
        levels.append(
            {
                "score": n,
                "predicate": {"kind": "final_state_equals", "path": ["achieved"], "value": n},
            }
        )
    return [{"task_id": "primary", "levels": levels}]


def _passing_pairs(count: int, *, max_score: int = MAX_SCORE) -> list[tuple[str, int, int]]:
    """`count` pairs where treatment always beats baseline by the full
    rubric maximum -- unconditionally decided, unconditionally a standard
    win, and the largest possible normalized delta. Used as filler evidence
    for a block whose own outcome the test in question does not care about
    (e.g. a validly-deferred Qwen block's declared schedule)."""
    return [(f"filler{i}", 0, max_score) for i in range(count)]


def _pairs_schedule(pairs: list[tuple[str, int, int]], *, start_index: int = 1) -> list[dict]:
    """Schedule-only shape (no campaign_fingerprint, no trial payload) --
    pure function of `pairs` alone, so it can be computed BEFORE the
    campaign_fingerprint that trial payloads need is known."""
    schedule_trials: list[dict] = []
    index = start_index
    for pair_id, _, _ in pairs:
        for arm_id in (BASELINE_ARM, TREATMENT_ARM):
            schedule_trials.append(
                {"index": index, "position_id": POSITION_ID, "pair_id": pair_id, "arm_id": arm_id}
            )
            index += 1
    return schedule_trials


def _pairs_trial_payloads(
    pairs: list[tuple[str, int, int]],
    *,
    block_id: str,
    session_fingerprint: str,
    campaign_fingerprint: str,
    start_index: int = 1,
) -> dict[int, dict]:
    trial_payloads: dict[int, dict] = {}
    index = start_index
    for pair_id, baseline_score, treatment_score in pairs:
        for arm_id, score in ((BASELINE_ARM, baseline_score), (TREATMENT_ARM, treatment_score)):
            trial_payloads[index] = {
                "index": index,
                "position_id": POSITION_ID,
                "pair_id": pair_id,
                "arm_id": arm_id,
                "model": block_id,
                "attempt_count": 1,
                "terminal": "finish_trial",
                "session_fingerprint": session_fingerprint,
                "campaign_fingerprint": campaign_fingerprint,
                "steps": [],
                "initial_state": {"achieved": 0},
                "final_state": {"achieved": score},
            }
            index += 1
    return trial_payloads


def _pair_indices(pairs: list[tuple[str, int, int]], *, start_index: int = 1) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    index = start_index
    for pair_id, _, _ in pairs:
        result[pair_id] = (index, index + 1)
        index += 2
    return result


def _self_fingerprinted(session_payload: dict) -> dict:
    """Stamp `session_payload` with a REAL `session_fingerprint` --
    `fingerprint()` over its own remaining fields, exactly the computation
    `benchmark_gates.build_session_lock` mints with (G1, external review
    wave G: every consumer now re-derives and verifies it, so a fixture
    session must be genuinely self-consistent)."""
    body = {k: v for k, v in session_payload.items() if k != "session_fingerprint"}
    return {**body, "session_fingerprint": fingerprint(body)}


def _write_counted_admission_record(
    campaign_dir: Path,
    block_id: str,
    *,
    session_fingerprint: str,
    campaign_fingerprint: str,
) -> None:
    """The counted admission SUCCESS record the real writer produces
    (I1, external review wave I): `AdmissionPipeline.admit(mode="counted")`
    records `{block_id, mode, gates, ok, session_fingerprint}` via
    `CampaignStore.record_admission` (which stamps `campaign_fingerprint`)
    immediately after `open_block` mints session.json and BEFORE any trial
    runs. Ordinal allocation mirrors `record_admission`'s append-only
    numbered sequence."""
    admissions_dir = campaign_dir / "admissions"
    admissions_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{block_id}-attempt-"
    ordinal = (
        sum(
            1
            for path in admissions_dir.iterdir()
            if path.is_file() and path.name.startswith(prefix) and path.name.endswith(".json")
        )
        + 1
    )
    _write_json(
        admissions_dir / f"{prefix}{ordinal:03d}.json",
        {
            "block_id": block_id,
            "mode": "counted",
            "gates": {},
            "ok": True,
            "session_fingerprint": session_fingerprint,
            "campaign_fingerprint": campaign_fingerprint,
        },
    )


def _write_block(
    campaign_dir: Path,
    block_id: str,
    *,
    pairs: list[tuple[str, int, int]],
    campaign_fingerprint: str,
    scorer_fingerprint: str = "score-v1",
    commit_indices: set[int] | None = None,
    max_score: int = MAX_SCORE,
    position_id: str = POSITION_ID,
    endpoint_id: str = "ep-1",
    gpu_ids: list[int] | None = None,
    write_session: bool = True,
) -> tuple[list[dict], dict[int, dict], str]:
    """Write one block's schedule/session/trials. The session lock's
    `session_fingerprint` is computed from the session payload itself (G1)
    and returned as the third element so callers can stamp audits/tie
    reviews with the real value."""
    block_dir = campaign_dir / "blocks" / block_id
    schedule_trials = _pairs_schedule(pairs)
    # position_id override applies only to the trial/session payloads (used
    # by the position-mismatch negative tests) -- the schedule shape itself
    # always uses the module-wide POSITION_ID for simplicity.
    if position_id != POSITION_ID:
        for entry in schedule_trials:
            entry["position_id"] = position_id

    _write_json(block_dir / "schedule.json", {"trials": schedule_trials})
    sampling = {"temperature": 0.0, "top_p": 1.0}
    # D4 (external review wave D): the reporter cross-checks each block
    # session's model_config against campaign.json's declared
    # ModelBlockConfig for that block (canonical comparison) -- so a
    # well-formed fixture session must carry the SAME model_config the
    # lock declares (see _default_models), exactly as a real
    # build_session_lock output does (model_config=asdict(block)).
    model_config = next((dict(m) for m in _default_models() if m["block_id"] == block_id), None)
    if model_config is None:
        model_config = {
            "block_id": block_id,
            "model": f"{block_id}-model",
            "endpoint_id": endpoint_id,
            "sampling": sampling,
            "chat_template_kwargs": {"enable_thinking": False},
            "briefing_required": False,
        }
    admission_endpoint = model_config["endpoint_id"] if endpoint_id == "ep-1" else endpoint_id
    session_payload = _self_fingerprinted(
        {
            "position_id": position_id,
            "block_id": block_id,
            "campaign_fingerprint": campaign_fingerprint,
            "scorer_fingerprint": scorer_fingerprint,
            "positions": {position_id: {"rubric": _rubric(max_score)}},
            "model_config": model_config,
            "model_admission": {
                "requested_model": model_config["model"],
                "resolved_model": model_config["model"],
                "requested_endpoint": admission_endpoint,
                "resolved_endpoint": f"http://{admission_endpoint}.local:8000",
                "registry_fingerprint": "registry-fp-1",
                "gpu_topology": {"gpu_ids": gpu_ids if gpu_ids is not None else [0]},
                "sampling": sampling,
            },
        }
    )
    session_fingerprint = session_payload["session_fingerprint"]
    trial_payloads = _pairs_trial_payloads(
        pairs,
        block_id=block_id,
        session_fingerprint=session_fingerprint,
        campaign_fingerprint=campaign_fingerprint,
    )
    if position_id != POSITION_ID:
        for payload in trial_payloads.values():
            payload["position_id"] = position_id
    # write_session=False simulates a block whose admission never actually
    # minted a counted session lock -- the genuine shape of a validly
    # deferred block (A2, external review): a real REPLICATION_DEFERRED_
    # ADMISSION only ever applies to an admission failure that produced NO
    # evidence at all, session.json included.
    if write_session:
        _write_json(block_dir / "session.json", session_payload)
        # I1 (external review wave I): a minted session always has its
        # counted admission SUCCESS record on disk -- the real writer
        # records it in the same admit() call that minted the session,
        # before any trial runs. (A deferred block -- write_session=False
        # -- never has one, exactly as a real zero-evidence admission
        # failure never does.)
        _write_counted_admission_record(
            campaign_dir,
            block_id,
            session_fingerprint=session_fingerprint,
            campaign_fingerprint=campaign_fingerprint,
        )
    commit = commit_indices if commit_indices is not None else set(trial_payloads)
    for index in commit:
        _write_json(block_dir / "trials" / f"trial-{index:03d}.json", trial_payloads[index])
    return schedule_trials, trial_payloads, session_fingerprint


def _live_automatic_for_score(achieved_score: int) -> dict:
    return {
        "task_scores": {"primary": achieved_score},
        "useful_actions": None,
        "domain_rejections": 0,
        "repetitions": 0,
    }


def _write_audit(
    block_dir: Path,
    *,
    session_fingerprint: str,
    audit_indices: list[int],
    trial_payloads: dict[int, dict],
    disagree_indices: frozenset[int] = frozenset(),
    hash_mismatch_indices: frozenset[int] = frozenset(),
    omit_indices: frozenset[int] = frozenset(),
) -> None:
    entries = []
    for index in audit_indices:
        if index in omit_indices:
            continue
        trial = trial_payloads[index]
        achieved = trial["final_state"]["achieved"]
        automatic = _live_automatic_for_score(achieved)
        manual = dict(automatic)
        if index in disagree_indices:
            manual = dict(manual, domain_rejections=999)
        trial_sha256 = "deadbeef" * 8 if index in hash_mismatch_indices else fingerprint(trial)
        entries.append(
            {
                "index": index,
                "trial_sha256": trial_sha256,
                "automatic": automatic,
                "manual": manual,
                "agrees": index not in disagree_indices,
                "notes": "fixture-generated audit entry",
            }
        )
    _write_json(
        block_dir / "audit.json",
        {"session_fingerprint": session_fingerprint, "audit_indices": list(audit_indices), "trials": entries},
    )


def _write_tie_attribution(
    block_dir: Path,
    *,
    session_fingerprint: str,
    pairs: list[tuple[str, int, int]],
    trial_payloads: dict[int, dict],
    attribution_by_pair: dict[str, str],
    invalid_hash_pairs: frozenset[str] = frozenset(),
    start_index: int = 1,
) -> None:
    pair_indices = _pair_indices(pairs, start_index=start_index)
    attributions = []
    for pair_id, attribution in attribution_by_pair.items():
        baseline_index, treatment_index = pair_indices[pair_id]
        trial_sha256 = {
            str(baseline_index): fingerprint(trial_payloads[baseline_index]),
            str(treatment_index): fingerprint(trial_payloads[treatment_index]),
        }
        if pair_id in invalid_hash_pairs:
            trial_sha256[str(baseline_index)] = "bad-hash" * 4
        attributions.append(
            {
                "pair_id": pair_id,
                "trial_indices": [baseline_index, treatment_index],
                "trial_sha256": trial_sha256,
                "transcript_finding": "no material behavioral difference observed",
                "final_state_finding": "final states are equivalent for this attribution",
                "counterfactual_fixture_result": "counterfactual behavior scores as expected",
                "attribution": attribution,
            }
        )
    _write_json(
        block_dir / "tie_attribution.json",
        {"session_fingerprint": session_fingerprint, "attributions": attributions},
    )


def _write_schedule(campaign_dir: Path, blocks_schedule: dict) -> None:
    _write_json(campaign_dir / "schedule.json", {"blocks": blocks_schedule})


def _default_models() -> list[dict]:
    return [
        {
            "block_id": "gemma",
            "model": "gemma4-27b",
            "endpoint_id": "ep-gemma",
            "sampling": {"temperature": 0.0, "top_p": 1.0},
            "chat_template_kwargs": {"enable_thinking": False},
            "briefing_required": False,
        },
        {
            "block_id": "qwen",
            "model": "qwen3.6-27b",
            "endpoint_id": "ep-qwen",
            "sampling": {"temperature": 0.0, "top_p": 1.0},
            "chat_template_kwargs": {"enable_thinking": False},
            "briefing_required": False,
        },
    ]


def _write_campaign_lock(
    campaign_dir: Path,
    *,
    rules: dict,
    audit_indices: list[int],
    schedule: dict,
    scorer_fingerprint: str = "score-v1",
    campaign_id: str = "camp-1",
    campaign_schema_version: str = "v1",
    position_id: str | None = POSITION_ID,
    models: list[dict] | None = None,
    include_contracts: bool = True,
    include_digests: bool = True,
) -> str:
    """Write a REAL, self-consistent `campaign.json` -- `campaign_fingerprint`
    is computed over the lock body exactly the way
    `benchmark_campaign.build_campaign_lock` computes it (fingerprint the
    dict, then add the campaign_fingerprint key), so
    `build_campaign_report`'s own fingerprint self-check passes. Returns the
    computed `campaign_fingerprint` so callers can stamp blocks/trials with
    the same value. `include_contracts`/`include_digests`/`position_id=None`
    build a deliberately incomplete-but-self-consistent lock, for the
    negative tests that must trip the specific missing-field check rather
    than the fingerprint check.
    """
    body: dict[str, object] = {
        "campaign_id": campaign_id,
        "campaign_schema_version": campaign_schema_version,
        "models": models if models is not None else _default_models(),
        "arms": [{"arm_id": BASELINE_ARM}, {"arm_id": TREATMENT_ARM}],
        "rules": rules,
        "audit_indices": audit_indices,
    }
    if position_id is not None:
        body["position_id"] = position_id
    if include_contracts:
        body["contracts"] = {"scorer_fingerprint": scorer_fingerprint}
    if include_digests:
        body["digests"] = {"schedule": fingerprint(schedule)}

    campaign_fingerprint = fingerprint(body)
    payload = dict(body)
    payload["campaign_fingerprint"] = campaign_fingerprint
    _write_json(campaign_dir / "campaign.json", payload)
    return campaign_fingerprint


def _rules12(*, minimum_decided=10, minimum_wins=10, minimum_delta=None, required_audits_per_arm=1) -> dict:
    return {
        "pairs_per_model": 12,
        "minimum_decided_pairs": minimum_decided,
        "minimum_standard_wins": minimum_wins,
        "minimum_median_normalized_delta": (4 / 12) if minimum_delta is None else minimum_delta,
        "required_audits_per_arm": required_audits_per_arm,
    }


def _write_qwen_deferral_record(
    campaign_dir: Path,
    *,
    campaign_fingerprint: str | None = None,
    corroboration: str = "two_attempts",
    code: str = "tool_canary_failed",
    stamp_campaign_fingerprint: bool = True,
) -> None:
    """Write a `REPLICATION_DEFERRED_ADMISSION` admission record for
    `qwen`, corroborated on disk per finding 4 (final review), A1
    (external review), D1 (external review wave D), G3 (wave G), and
    Ruling J (wave H): the predicate is now exactly ">= 2 same-code
    allowlisted failures at distinct ordinals on stamped records strictly
    preceding the disposition" -- so every caller of this helper that
    expects the deferral to be HONORED must produce at least TWO preceding
    same-code failed admission attempts (see A1: the disposition's own
    triggering failure is itself one of the on-disk attempt records, so
    genuine corroboration needs a SECOND, independent one before it).
    `corroboration="two_attempts"` (the default) is the minimal honored
    shape; `corroboration="remediation"` (F, R-ok, F) is honored because
    its two failures straddling the remediation ARE two same-code
    failures -- Ruling J deleted the dedicated remediation branch as dead
    code, since that shape was already subsumed.

    Negative shapes: `"single_failure"` (A1: exactly ONE preceding
    failure, the same failure `admit()` itself just hit);
    `"failed_remediation"` (D1: a no-op remediation -- `result.ok` false,
    e.g. the real `{"ok": False, "reason": "no_holder"}` record
    `_run_remediation_async` writes -- plus one failure);
    `"remediation_no_retry"` (D1: a successful remediation that
    POSTDATES the only failure, i.e. zero journaled retries after it);
    `"remediation_without_preceding_failure"` (G3: the weakest shape the
    old D1 predicate accepted -- an UNRELATED remediation with no
    preceding same-code failure at all, plus one failure first observed
    only after it); `None` (bare, uncorroborated disposition-only
    record). All of these must be REFUSED.

    `code` defaults to `tool_canary_failed` -- a Ruling-G-allowlisted
    model-capability gate code (D2): any operator-error code (e.g.
    `dirty_checkout`) is deferral-ineligible no matter the corroboration.

    G4 (external review wave G): every record is stamped with this
    campaign's `campaign_fingerprint` (read from campaign.json when not
    passed), exactly as `CampaignStore.record_admission` stamps every
    record it writes; `stamp_campaign_fingerprint=False` produces the
    unstamped negative shape, which must never corroborate.
    """
    admissions_dir = campaign_dir / "admissions"
    admissions_dir.mkdir(parents=True, exist_ok=True)
    if campaign_fingerprint is None:
        campaign_fingerprint = json.loads(
            (campaign_dir / "campaign.json").read_text(encoding="utf-8")
        )["campaign_fingerprint"]
    ordinal = 1

    def _stamped(record: dict) -> dict:
        if stamp_campaign_fingerprint:
            record["campaign_fingerprint"] = campaign_fingerprint
        return record

    def _write_failure() -> None:
        # Ruling K (wave I, I2): the real disposition-writing CLI path runs
        # admit() in mode="counted", so a genuine corroborating failure
        # record carries mode/gates exactly as AdmissionPipeline._fail
        # records them -- and only counted-mode failures corroborate.
        nonlocal ordinal
        _write_json(
            admissions_dir / f"qwen-attempt-{ordinal:03d}.json",
            _stamped(
                {
                    "block_id": "qwen",
                    "mode": "counted",
                    "gates": {},
                    "ok": False,
                    "failure": {"code": code, "details": {}},
                }
            ),
        )
        ordinal += 1

    def _write_remediation(*, ok: bool) -> None:
        nonlocal ordinal
        result: dict = {"ok": True, "terminated_pid": 4242, "port": 4318} if ok else {"ok": False, "reason": "no_holder"}
        _write_json(
            admissions_dir / f"qwen-attempt-{ordinal:03d}.json",
            _stamped({"block_id": "qwen", "remediation": "terminate_tuner_pid", "result": result}),
        )
        ordinal += 1

    if corroboration == "two_attempts":
        _write_failure()
        _write_failure()
    elif corroboration == "single_failure":
        _write_failure()
    elif corroboration == "remediation":
        _write_failure()
        _write_remediation(ok=True)
        _write_failure()
    elif corroboration == "remediation_without_preceding_failure":
        _write_remediation(ok=True)
        _write_failure()
    elif corroboration == "failed_remediation":
        _write_remediation(ok=False)
        _write_failure()
    elif corroboration == "remediation_no_retry":
        _write_failure()
        _write_remediation(ok=True)
    elif corroboration is not None:
        raise ValueError(f"unknown corroboration kind {corroboration!r}")
    _write_json(
        admissions_dir / f"qwen-attempt-{ordinal:03d}.json",
        _stamped(
            {
                "block_id": "qwen",
                "disposition": REPLICATION_DEFERRED_ADMISSION,
                "underlying_failure": {"code": code},
            }
        ),
    )


def _build_campaign_with_gemma(
    tmp_path: Path,
    *,
    gemma_pairs: list[tuple[str, int, int]],
    rules: dict,
    audit_indices: list[int],
    gemma_audit_disagree_indices: frozenset[int] = frozenset(),
    gemma_audit_hash_mismatch_indices: frozenset[int] = frozenset(),
    gemma_omit_audit: bool = False,
    gemma_tie_attribution: dict[str, str] | None = None,
    gemma_tie_attribution_hash_break: frozenset[str] = frozenset(),
    qwen_deferral_record: bool = True,
    name: str = "campaign",
    scorer_fingerprint: str = "score-v1",
    gemma_scorer_fingerprint_override: str | None = None,
    include_contracts: bool = True,
    include_digests: bool = True,
    position_id: str | None = POSITION_ID,
    gemma_position_id: str | None = None,
) -> tuple[Path, dict[int, dict]]:
    """A campaign whose mandatory primary (Gemma) block is exactly what the
    caller asks for, and whose Qwen (secondary) block always has an
    INCOMPLETE schedule (zero committed trials) -- toggled only by whether a
    valid `REPLICATION_DEFERRED_ADMISSION` admission record exists for it.
    This is the shared shape for every test that only cares about Gemma's
    own calibration/outcome arithmetic."""
    campaign_dir = tmp_path / name
    pair_count = len(gemma_pairs)

    gemma_schedule = _pairs_schedule(gemma_pairs)
    qwen_schedule = _pairs_schedule(_passing_pairs(pair_count))
    schedule = {"blocks": {"gemma": {"trials": gemma_schedule}, "qwen": {"trials": qwen_schedule}}}

    campaign_fingerprint = _write_campaign_lock(
        campaign_dir,
        rules=rules,
        audit_indices=audit_indices,
        schedule=schedule,
        scorer_fingerprint=scorer_fingerprint,
        position_id=position_id,
        include_contracts=include_contracts,
        include_digests=include_digests,
    )
    _write_schedule(campaign_dir, schedule["blocks"])

    _, gemma_trials, gemma_session_fingerprint = _write_block(
        campaign_dir,
        "gemma",
        pairs=gemma_pairs,
        campaign_fingerprint=campaign_fingerprint,
        scorer_fingerprint=gemma_scorer_fingerprint_override or scorer_fingerprint,
        position_id=gemma_position_id or (position_id or POSITION_ID),
    )
    if not gemma_omit_audit:
        _write_audit(
            campaign_dir / "blocks" / "gemma",
            session_fingerprint=gemma_session_fingerprint,
            audit_indices=audit_indices,
            trial_payloads=gemma_trials,
            disagree_indices=gemma_audit_disagree_indices,
            hash_mismatch_indices=gemma_audit_hash_mismatch_indices,
        )
    if gemma_tie_attribution is not None:
        _write_tie_attribution(
            campaign_dir / "blocks" / "gemma",
            session_fingerprint=gemma_session_fingerprint,
            pairs=gemma_pairs,
            trial_payloads=gemma_trials,
            attribution_by_pair=gemma_tie_attribution,
            invalid_hash_pairs=gemma_tie_attribution_hash_break,
        )

    _write_block(
        campaign_dir,
        "qwen",
        pairs=_passing_pairs(pair_count),
        campaign_fingerprint=campaign_fingerprint,
        scorer_fingerprint=scorer_fingerprint,
        commit_indices=set(),
        # This helper's Qwen block always has zero committed trials -- the
        # true shape of an admission that never got as far as minting a
        # session lock, which is what a REPLICATION_DEFERRED_ADMISSION
        # disposition actually represents (see A2, external review).
        write_session=False,
    )

    if qwen_deferral_record:
        _write_qwen_deferral_record(campaign_dir, campaign_fingerprint=campaign_fingerprint)

    return campaign_dir, gemma_trials


def _build_full_two_block_campaign(
    campaign_dir: Path,
    *,
    gemma_pairs: list[tuple[str, int, int]],
    qwen_pairs: list[tuple[str, int, int]],
    rules: dict,
    audit_indices: list[int],
    gemma_tie_attribution: dict[str, str] | None = None,
    qwen_tie_attribution: dict[str, str] | None = None,
    scorer_fingerprint: str = "score-v1",
) -> tuple[str, dict[int, dict], dict[int, dict]]:
    """A campaign where BOTH blocks genuinely completed their schedule --
    for tests that need to observe how one block's outcome interacts with
    the other's. Returns (campaign_fingerprint, gemma_trials, qwen_trials)."""
    gemma_schedule = _pairs_schedule(gemma_pairs)
    qwen_schedule = _pairs_schedule(qwen_pairs)
    schedule = {"blocks": {"gemma": {"trials": gemma_schedule}, "qwen": {"trials": qwen_schedule}}}

    campaign_fingerprint = _write_campaign_lock(
        campaign_dir, rules=rules, audit_indices=audit_indices, schedule=schedule, scorer_fingerprint=scorer_fingerprint
    )
    _write_schedule(campaign_dir, schedule["blocks"])

    _, gemma_trials, gemma_session_fingerprint = _write_block(
        campaign_dir, "gemma", pairs=gemma_pairs,
        campaign_fingerprint=campaign_fingerprint, scorer_fingerprint=scorer_fingerprint,
    )
    _, qwen_trials, qwen_session_fingerprint = _write_block(
        campaign_dir, "qwen", pairs=qwen_pairs,
        campaign_fingerprint=campaign_fingerprint, scorer_fingerprint=scorer_fingerprint,
    )
    _write_audit(
        campaign_dir / "blocks" / "gemma", session_fingerprint=gemma_session_fingerprint,
        audit_indices=audit_indices, trial_payloads=gemma_trials,
    )
    _write_audit(
        campaign_dir / "blocks" / "qwen", session_fingerprint=qwen_session_fingerprint,
        audit_indices=audit_indices, trial_payloads=qwen_trials,
    )
    if gemma_tie_attribution is not None:
        _write_tie_attribution(
            campaign_dir / "blocks" / "gemma", session_fingerprint=gemma_session_fingerprint,
            pairs=gemma_pairs, trial_payloads=gemma_trials, attribution_by_pair=gemma_tie_attribution,
        )
    if qwen_tie_attribution is not None:
        _write_tie_attribution(
            campaign_dir / "blocks" / "qwen", session_fingerprint=qwen_session_fingerprint,
            pairs=qwen_pairs, trial_payloads=qwen_trials, attribution_by_pair=qwen_tie_attribution,
        )
    return campaign_fingerprint, gemma_trials, qwen_trials


def _passing_gemma_pairs() -> list[tuple[str, int, int]]:
    """12 decisively-passing pairs: decided=12, wins=12, median delta=0.5
    (well above the 4/12 threshold)."""
    return [(f"p{i}", 0, 6) for i in range(12)]


# ---------------------------------------------------------------------------
# Structural separation
# ---------------------------------------------------------------------------


def test_report_keeps_every_model_arm_and_position_separate(tmp_path):
    campaign_dir = tmp_path / "campaign"
    gemma_pairs = [(f"p{i}", 0, 12) for i in range(12)]  # median delta = 1.0
    qwen_pairs = [(f"p{i}", 0, 3) for i in range(12)]  # median delta = 0.25 (below 4/12 threshold)
    rules = _rules12()
    audit_indices = [1, 2]

    _build_full_two_block_campaign(
        campaign_dir, gemma_pairs=gemma_pairs, qwen_pairs=qwen_pairs, rules=rules, audit_indices=audit_indices
    )

    report = build_campaign_report(campaign_dir)

    gemma_by_group = report["blocks"]["gemma"]["report"]["positions"][POSITION_ID]["by_group"]
    qwen_by_group = report["blocks"]["qwen"]["report"]["positions"][POSITION_ID]["by_group"]
    assert set(gemma_by_group) == {"gemma::minimal", "gemma::standard"}
    assert set(qwen_by_group) == {"qwen::minimal", "qwen::standard"}

    gemma_aggregate = report["blocks"]["gemma"]["report"]["aggregate"]
    qwen_aggregate = report["blocks"]["qwen"]["report"]["aggregate"]
    assert gemma_aggregate["gemma::standard"]["equal_weight_mean"] == 1.0
    assert qwen_aggregate["qwen::standard"]["equal_weight_mean"] == 0.25

    assert report["blocks"]["gemma"]["outcome"] == "PASS"
    assert report["blocks"]["qwen"]["outcome"] == "MODEL_NULL"


# ---------------------------------------------------------------------------
# Fingerprint fail-closed (delegates to benchmark_report.build_report)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_field", ["session_fingerprint", "campaign_fingerprint"])
def test_report_refuses_trials_missing_either_fingerprint(tmp_path, missing_field):
    campaign_dir, gemma_trials = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    trial_path = campaign_dir / "blocks" / "gemma" / "trials" / "trial-001.json"
    payload = json.loads(trial_path.read_text(encoding="utf-8"))
    del payload[missing_field]
    _write_json(trial_path, payload)

    with pytest.raises(CampaignReportError, match=missing_field):
        build_campaign_report(campaign_dir)


def test_campaign_report_refuses_a_validation_shaped_session_promoted_into_blocks(tmp_path):
    """B2 (external review wave B): benchmark_report.build_report now
    accepts a `validation: true` lock standalone (no campaign_fingerprint
    required -- see test_benchmark_report.py). That exemption must NOT let
    a validation-shaped session.json/trials (single-stamped, no
    campaign_fingerprint at all) copied or promoted into `blocks/<id>/`
    satisfy the COUNTED campaign report -- the campaign_fingerprint
    cross-check at the campaign-report level must still fire."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    session_path = campaign_dir / "blocks" / "gemma" / "session.json"
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    del payload["campaign_fingerprint"]
    payload["validation"] = True
    _write_json(session_path, payload)
    # Strip the second (campaign) stamp from every committed trial to match
    # a genuinely validation-shaped, single-stamped lock -- exactly what a
    # real non-counting validation episode would have produced.
    trials_dir = campaign_dir / "blocks" / "gemma" / "trials"
    for trial_path in trials_dir.iterdir():
        trial_payload = json.loads(trial_path.read_text(encoding="utf-8"))
        trial_payload.pop("campaign_fingerprint", None)
        _write_json(trial_path, trial_payload)

    with pytest.raises(CampaignReportError):
        build_campaign_report(campaign_dir)


def test_campaign_report_refuses_gemma_evidence_copied_under_qwen_block(tmp_path):
    """D4 (external review wave D): cross-block evidence substitution --
    copying blocks/gemma wholesale to blocks/qwen used to pass because
    only campaign/position/scorer fingerprints were cross-checked, and
    every block of one campaign shares all three. The session's own
    declared block identity must match the block directory it is scored
    under, via a typed error -- never a silent verdict."""
    import shutil

    campaign_dir = tmp_path / "campaign"
    pairs = _passing_gemma_pairs()
    _build_full_two_block_campaign(
        campaign_dir, gemma_pairs=pairs, qwen_pairs=pairs, rules=_rules12(), audit_indices=[1, 2]
    )
    # Replace qwen's evidence with a byte-identical copy of gemma's --
    # internally fully self-consistent (session/trials/audit all agree
    # with each other and with the shared campaign fingerprint).
    shutil.rmtree(campaign_dir / "blocks" / "qwen")
    shutil.copytree(campaign_dir / "blocks" / "gemma", campaign_dir / "blocks" / "qwen")

    with pytest.raises(CampaignReportError, match="block_id"):
        build_campaign_report(campaign_dir)


def test_campaign_report_refuses_copied_evidence_with_forged_block_id(tmp_path):
    """D4 adversarial counterpart (standing rule): the weakest input
    satisfying ONLY the block_id check -- gemma's evidence copied under
    blocks/qwen with the session's block_id rewritten to "qwen" but its
    model_config still gemma's -- must still be rejected, by the
    model_config-vs-campaign-lock cross-check."""
    import shutil

    campaign_dir = tmp_path / "campaign"
    pairs = _passing_gemma_pairs()
    _build_full_two_block_campaign(
        campaign_dir, gemma_pairs=pairs, qwen_pairs=pairs, rules=_rules12(), audit_indices=[1, 2]
    )
    shutil.rmtree(campaign_dir / "blocks" / "qwen")
    shutil.copytree(campaign_dir / "blocks" / "gemma", campaign_dir / "blocks" / "qwen")
    session_path = campaign_dir / "blocks" / "qwen" / "session.json"
    session_payload = json.loads(session_path.read_text(encoding="utf-8"))
    session_payload["block_id"] = "qwen"
    # G1 (external review wave G): the weakest forged input now also
    # re-mints the session_fingerprint over the edited session AND restamps
    # every copied trial with it -- otherwise the (new) session-fingerprint
    # recomputation catches the edit before the model_config cross-check
    # this test pins ever runs.
    session_payload = _self_fingerprinted(session_payload)
    _write_json(session_path, session_payload)
    for trial_path in sorted((campaign_dir / "blocks" / "qwen" / "trials").glob("trial-*.json")):
        trial_payload = json.loads(trial_path.read_text(encoding="utf-8"))
        trial_payload["session_fingerprint"] = session_payload["session_fingerprint"]
        _write_json(trial_path, trial_payload)

    with pytest.raises(CampaignReportError, match="model_config"):
        build_campaign_report(campaign_dir)


# ---------------------------------------------------------------------------
# H1 (external review wave H, CRITICAL): the full re-mint exploit. Copy
# blocks/gemma to blocks/qwen, edit BOTH bound fields (block_id +
# model_config) to qwen's declared values, re-mint session_fingerprint via
# the PUBLIC compute_session_fingerprint, restamp every trial, and recompute
# the audit hashes -- every pre-H1 check passes, yet every scored trial is
# gemma's evidence laundered under the qwen block.
# ---------------------------------------------------------------------------


def _remint_gemma_evidence_as_qwen(campaign_dir: Path, *, fix_block_schedule: bool) -> None:
    """Perform the full H1 exploit against `campaign_dir`. With
    `fix_block_schedule=True` the attacker additionally swaps in the TRUE
    campaign qwen block schedule (defeating the H1(b) block-schedule
    binding), leaving only the per-trial schedule binding (H1(a)) to catch
    the substitution."""
    import shutil

    shutil.rmtree(campaign_dir / "blocks" / "qwen")
    shutil.copytree(campaign_dir / "blocks" / "gemma", campaign_dir / "blocks" / "qwen")

    campaign = json.loads((campaign_dir / "campaign.json").read_text(encoding="utf-8"))
    declared_qwen_config = next(m for m in campaign["models"] if m["block_id"] == "qwen")

    session_path = campaign_dir / "blocks" / "qwen" / "session.json"
    session_payload = json.loads(session_path.read_text(encoding="utf-8"))
    session_payload["block_id"] = "qwen"
    session_payload["model_config"] = declared_qwen_config
    session_payload = _self_fingerprinted(session_payload)
    _write_json(session_path, session_payload)
    new_session_fingerprint = session_payload["session_fingerprint"]

    trial_payloads: dict[int, dict] = {}
    for trial_path in sorted((campaign_dir / "blocks" / "qwen" / "trials").glob("trial-*.json")):
        payload = json.loads(trial_path.read_text(encoding="utf-8"))
        payload["session_fingerprint"] = new_session_fingerprint
        _write_json(trial_path, payload)
        trial_payloads[payload["index"]] = payload

    audit_path = campaign_dir / "blocks" / "qwen" / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["session_fingerprint"] = new_session_fingerprint
    for entry in audit["trials"]:
        entry["trial_sha256"] = fingerprint(trial_payloads[entry["index"]])
    _write_json(audit_path, audit)

    if fix_block_schedule:
        campaign_schedule = json.loads((campaign_dir / "schedule.json").read_text(encoding="utf-8"))
        _write_json(
            campaign_dir / "blocks" / "qwen" / "schedule.json",
            campaign_schedule["blocks"]["qwen"],
        )


def _distinct_qwen_pairs() -> list[tuple[str, int, int]]:
    """Qwen pairs with pair_ids DISTINCT from gemma's (`q{i}` vs `p{i}`) --
    the realistic campaign shape the exploit launders across."""
    return [(f"q{i}", 6, 0) for i in range(12)]


def test_campaign_report_refuses_the_full_reminted_cross_block_substitution(tmp_path):
    """H1: both bound fields edited, fingerprint re-minted, trials
    restamped, audit hashes recomputed -- the complete exploit must be a
    typed refusal (here via the H1(b) block-schedule binding: the copied
    blocks/qwen/schedule.json is gemma's, not the campaign schedule's
    declared qwen entry)."""
    campaign_dir = tmp_path / "campaign"
    _build_full_two_block_campaign(
        campaign_dir,
        gemma_pairs=_passing_gemma_pairs(),
        qwen_pairs=_distinct_qwen_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
    )
    _remint_gemma_evidence_as_qwen(campaign_dir, fix_block_schedule=False)

    with pytest.raises(CampaignReportError):
        build_campaign_report(campaign_dir)


def test_campaign_report_refuses_the_reminted_exploit_even_with_a_fixed_block_schedule(tmp_path):
    """H1(a) adversarial counterpart: the attacker additionally swaps in
    the TRUE campaign qwen block schedule, defeating the block-schedule
    binding -- the per-trial schedule binding (trial pair_id/model/arm/seed
    must equal the scheduled entry at that index) must still refuse, since
    the restamped trials carry gemma's scheduled identities."""
    campaign_dir = tmp_path / "campaign"
    _build_full_two_block_campaign(
        campaign_dir,
        gemma_pairs=_passing_gemma_pairs(),
        qwen_pairs=_distinct_qwen_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
    )
    _remint_gemma_evidence_as_qwen(campaign_dir, fix_block_schedule=True)

    with pytest.raises(CampaignReportError, match="schedule"):
        build_campaign_report(campaign_dir)


# ---------------------------------------------------------------------------
# I1 (external review wave I): the counted admission SUCCESS anchor. The
# writer cannot produce trials without first writing an
# admissions/<block>-attempt-NNN.json success record (ok=True,
# mode="counted", campaign_fingerprint stamp, session_fingerprint equal to
# the minted session lock's) -- AdmissionPipeline.admit writes it right
# after open_block and BEFORE returning the ResolvedBlock the caller runs
# trials with. The reporter must therefore require one for every COMPLETE
# block; a complete block with no matching counted admission record is
# substituted/forged evidence.
# ---------------------------------------------------------------------------


def test_complete_block_without_any_admission_records_is_refused(tmp_path):
    """I1, the original finding: build_campaign_report used to emit
    CALIBRATED over a campaign with NO admissions/ directory at all --
    an evidence tree the real writer can never produce."""
    import shutil

    campaign_dir = tmp_path / "campaign"
    pairs = _passing_gemma_pairs()
    _build_full_two_block_campaign(
        campaign_dir, gemma_pairs=pairs, qwen_pairs=pairs, rules=_rules12(), audit_indices=[1, 2]
    )
    shutil.rmtree(campaign_dir / "admissions", ignore_errors=True)

    with pytest.raises(CampaignReportError, match="counted admission"):
        build_campaign_report(campaign_dir)


def _rewrite_gemma_success_records(campaign_dir: Path, mutate) -> int:
    changed = 0
    for path in sorted((campaign_dir / "admissions").glob("gemma-attempt-*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("ok") is True:
            mutate(record)
            _write_json(path, record)
            changed += 1
    return changed


def test_admission_success_record_with_mismatched_session_fingerprint_is_refused(tmp_path):
    """I1: a success record whose session_fingerprint does not equal the
    block's own (re-derived) session fingerprint anchors nothing -- it
    belongs to some other minted session."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    assert _rewrite_gemma_success_records(
        campaign_dir, lambda r: r.__setitem__("session_fingerprint", "0" * 64)
    ), "fixture must have written a counted admission success record for gemma"

    with pytest.raises(CampaignReportError, match="counted admission"):
        build_campaign_report(campaign_dir)


def test_admit_only_success_record_never_anchors_a_complete_block(tmp_path):
    """I1, weakest form: an ok=True record from a NON-counting admit_only
    diagnostic invocation (identical in every other field, session
    fingerprint included -- admit_only proves a session COULD be minted
    without minting one) must never anchor a complete block."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    assert _rewrite_gemma_success_records(
        campaign_dir, lambda r: r.__setitem__("mode", "admit_only")
    ), "fixture must have written a counted admission success record for gemma"

    with pytest.raises(CampaignReportError, match="counted admission"):
        build_campaign_report(campaign_dir)


def _relabel_reminted_qwen_trials_to_schedule(campaign_dir: Path) -> None:
    """The wave-H ESCALATION on top of `_remint_gemma_evidence_as_qwen(
    fix_block_schedule=True)`: additionally relabel every copied trial's
    schedule-bound identity fields to the campaign qwen schedule's declared
    values at its index (and its `model` label to qwen's), then recompute
    the audit hashes over the relabelled payloads -- pure metadata
    relabeling with consistent re-minting of every self-fingerprinted
    stamp, which defeats H1(a)'s per-trial schedule binding."""
    campaign_schedule = json.loads((campaign_dir / "schedule.json").read_text(encoding="utf-8"))
    entries = {e["index"]: e for e in campaign_schedule["blocks"]["qwen"]["trials"]}

    trial_payloads: dict[int, dict] = {}
    for trial_path in sorted((campaign_dir / "blocks" / "qwen" / "trials").glob("trial-*.json")):
        payload = json.loads(trial_path.read_text(encoding="utf-8"))
        entry = entries[payload["index"]]
        for field in ("pair_id", "arm_id", "position_id"):
            payload[field] = entry[field]
        payload["model"] = "qwen"
        _write_json(trial_path, payload)
        trial_payloads[payload["index"]] = payload

    audit_path = campaign_dir / "blocks" / "qwen" / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    for entry in audit["trials"]:
        entry["trial_sha256"] = fingerprint(trial_payloads[entry["index"]])
    _write_json(audit_path, audit)


def test_wave_h_relabelled_forgery_is_refused_by_the_admission_anchor(tmp_path):
    """The wave-H relabeling exploit tree (fifth-round review): remint the
    session (both bound fields + fresh self-fingerprint), swap in the TRUE
    campaign qwen block schedule, relabel every trial's schedule-bound
    identity fields to qwen's declared values, restamp trials and audit
    hashes. Every fingerprint/schedule/identity check passes -- gemma's
    evidence launders cleanly under the qwen block -- EXCEPT the I1
    anchor: no counted admission success record exists whose
    session_fingerprint matches the re-minted session, because the real
    writer only ever records one for a session it actually minted."""
    campaign_dir = tmp_path / "campaign"
    _build_full_two_block_campaign(
        campaign_dir,
        gemma_pairs=_passing_gemma_pairs(),
        qwen_pairs=_distinct_qwen_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
    )
    _remint_gemma_evidence_as_qwen(campaign_dir, fix_block_schedule=True)
    _relabel_reminted_qwen_trials_to_schedule(campaign_dir)

    with pytest.raises(CampaignReportError, match="counted admission"):
        build_campaign_report(campaign_dir)


# ---------------------------------------------------------------------------
# H1(b): reporter-writer parity on the block-schedule <-> campaign-schedule
# binding (`CampaignStore.open_block` enforces it at write time; the
# reporter re-verifies it at read time).
# ---------------------------------------------------------------------------


def test_campaign_report_refuses_a_tampered_block_schedule(tmp_path):
    """H1(b), weakest form: a single edited field in one block's local
    schedule.json (here one entry's arm_id) must be a typed refusal."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    block_schedule_path = campaign_dir / "blocks" / "gemma" / "schedule.json"
    payload = json.loads(block_schedule_path.read_text(encoding="utf-8"))
    payload["trials"][0]["arm_id"] = TREATMENT_ARM
    _write_json(block_schedule_path, payload)

    with pytest.raises(CampaignReportError, match="schedule"):
        build_campaign_report(campaign_dir)


def test_campaign_report_refuses_a_complete_block_missing_its_schedule(tmp_path):
    """H1(b): a complete block with NO blocks/<id>/schedule.json at all is
    itself substituted evidence -- open_block always writes the schedule
    before any trial is committed."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    (campaign_dir / "blocks" / "gemma" / "schedule.json").unlink()

    with pytest.raises(CampaignReportError, match="schedule"):
        build_campaign_report(campaign_dir)


def test_campaign_report_refuses_a_tampered_block_schedule_on_a_deferred_block(tmp_path):
    """H1(b) parity for the deferral path: a deferred block may lack a
    local schedule.json entirely, but one that EXISTS and diverges from
    the campaign schedule's declared entry is still a refusal."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    block_schedule_path = campaign_dir / "blocks" / "qwen" / "schedule.json"
    payload = json.loads(block_schedule_path.read_text(encoding="utf-8"))
    payload["trials"][0]["pair_id"] = "forged-pair"
    _write_json(block_schedule_path, payload)

    with pytest.raises(CampaignReportError, match="schedule"):
        build_campaign_report(campaign_dir)


def test_valid_deferral_still_honored_when_deferred_block_has_no_schedule_file(tmp_path):
    """H1(b) non-regression: a genuinely deferred block (zero evidence)
    may legitimately have no blocks/<id>/schedule.json -- absence there
    must not refuse the otherwise-valid deferral."""
    import shutil

    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    shutil.rmtree(campaign_dir / "blocks" / "qwen")

    report = build_campaign_report(campaign_dir)
    assert report["verdict"]["outcome"] == "CALIBRATED_REPLICATION_DEFERRED"


# ---------------------------------------------------------------------------
# Incomplete schedule: refused unless a valid Qwen deferral is on record
# ---------------------------------------------------------------------------


def test_report_refuses_incomplete_schedule_without_valid_qwen_deferral(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


def test_gemma_pass_with_valid_qwen_deferral_is_calibrated_deferred(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=True,
    )

    report = build_campaign_report(campaign_dir)

    assert report["blocks"]["gemma"]["outcome"] == "PASS"
    assert report["blocks"]["qwen"]["status"] == "ADMISSION_DEFERRED"
    assert report["verdict"]["outcome"] == "CALIBRATED_REPLICATION_DEFERRED"


def test_gemma_pass_with_qwen_deferral_via_two_failures_straddling_a_remediation(tmp_path):
    """Ruling J (external review wave H): the F, R-ok, F sequence
    (`corroboration="remediation"`) is honored because it contains TWO
    same-code failures at distinct preceding ordinals -- the ONLY
    predicate that remains after the dead remediation branch was deleted.
    The spec's "journaled retry after a concrete remediation" path is
    SUBSUMED by this, not dropped: the post-remediation retry that fails
    IS the second same-code observation. This fixture keeps the F, R, F
    shape (rather than F, F) precisely to pin that subsumption."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )
    _write_qwen_deferral_record(campaign_dir, corroboration="remediation")

    report = build_campaign_report(campaign_dir)

    assert report["verdict"]["outcome"] == "CALIBRATED_REPLICATION_DEFERRED"


def test_report_refuses_qwen_deferral_via_failed_remediation(tmp_path):
    """D1 (external review wave D), adversarial branch test: the weakest
    input satisfying ONLY the remediation OR-branch of the old predicate
    -- a record that merely CONTAINS a `remediation` key but whose result
    reports failure (`{"ok": False, "reason": "no_holder"}`, the exact
    shape `_run_remediation_async` records for a refused/no-op
    remediation) plus one same-code failure -- must be REJECTED. A
    remediation that never actually did anything corroborates nothing."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )
    _write_qwen_deferral_record(campaign_dir, corroboration="failed_remediation")

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


def test_report_refuses_qwen_deferral_when_remediation_postdates_the_only_failure(tmp_path):
    """D1 (external review wave D): a SUCCESSFUL remediation with zero
    journaled failed retries AFTER it (the only failure on record precedes
    the remediation) must be rejected -- "at least one journaled retry
    after a concrete remediation" requires the same-code failure to have
    been observed again once the fix was in place."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )
    _write_qwen_deferral_record(campaign_dir, corroboration="remediation_no_retry")

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


def test_report_refuses_qwen_deferral_for_operator_error_code_even_fully_corroborated(tmp_path):
    """D2 (external review wave D, Ruling G): deferral eligibility is an
    explicit ALLOWLIST of model-capability gate failure codes. An
    operator-error code (`dirty_checkout`) must never be honored as a
    REPLICATION_DEFERRED_ADMISSION -- even with perfect two-failure
    corroboration AND a successful remediation followed by a retry."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )
    admissions_dir = campaign_dir / "admissions"
    admissions_dir.mkdir(parents=True, exist_ok=True)
    records = [
        {"block_id": "qwen", "ok": False, "failure": {"code": "dirty_checkout", "details": {}}},
        {"block_id": "qwen", "ok": False, "failure": {"code": "dirty_checkout", "details": {}}},
        {"block_id": "qwen", "remediation": "terminate_tuner_pid", "result": {"ok": True}},
        {"block_id": "qwen", "ok": False, "failure": {"code": "dirty_checkout", "details": {}}},
        {
            "block_id": "qwen",
            "disposition": REPLICATION_DEFERRED_ADMISSION,
            "underlying_failure": {"code": "dirty_checkout"},
        },
    ]
    for ordinal, record in enumerate(records, start=1):
        _write_json(admissions_dir / f"qwen-attempt-{ordinal:03d}.json", record)

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


def test_report_refuses_qwen_deferral_from_single_uncorroborated_failure(tmp_path):
    """Finding 4 (final review): a single admission-attempt record is not
    enough to mint CALIBRATED_REPLICATION_DEFERRED, even when its
    underlying failure carries a real, classified code. Deferral requires
    corroboration on disk -- at least one journaled retry after a concrete
    remediation, or two confirming attempts with the same code. This is
    the exact incident this finding closes: a bare single-attempt
    deferral record must be refused, falling back to the ordinary
    incomplete-schedule error."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )
    _write_qwen_deferral_record(campaign_dir, corroboration=None)

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


def test_report_refuses_qwen_deferral_from_one_preceding_failure_plus_disposition(tmp_path):
    """A1 (external review): the exact self-defeating shape the real CLI
    produces on a genuine, un-retried admission failure -- `admit()` writes
    ONE failed-gate attempt record, then (in the SAME invocation)
    `record_admission_disposition` writes a disposition record referencing
    that same failure as `underlying_failure`. `records[:index]` (every
    record strictly preceding the disposition) then contains exactly that
    ONE failure -- which the old logic accepted as "corroboration" for
    itself. This must be refused: one real failure, however it's framed on
    disk, is never two confirming attempts."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )
    _write_qwen_deferral_record(campaign_dir, corroboration="single_failure")

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


@pytest.mark.parametrize("failure_mode", ["admit_only", None])
def test_admit_only_diagnostic_failures_never_corroborate_a_deferral(tmp_path, failure_mode):
    """Ruling K (external review wave I, finding I2): the corroboration
    filter was mode-blind -- two `admit_only` DIAGNOSTIC failure records
    (written through the same `admissions/<block>-attempt-NNN.json` path
    as counted ones) corroborated a deferral, letting non-counting
    diagnostics stand in for the two independent COUNTED observations the
    spec demands. Corroborating failure records must carry
    `mode: "counted"` -- the mode the real disposition-writing CLI
    campaign loop actually runs in. `failure_mode=None` is the weakest
    form: a record with NO mode field at all must also never corroborate
    (the default points in the non-deferral-eligible direction)."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    for path in sorted((campaign_dir / "admissions").glob("qwen-attempt-*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("ok") is False:
            if failure_mode is None:
                record.pop("mode", None)
            else:
                record["mode"] = failure_mode
                record["gates"] = {}
            _write_json(path, record)

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


def test_report_refuses_qwen_deferral_when_committed_trials_exist_despite_disposition(tmp_path):
    """A2 (external review): "Replication deferral is only for failures
    that create no trials." A well-corroborated disposition record must
    still be refused once the block actually has ANY committed trial
    evidence on disk -- that is not the zero-evidence admission failure a
    deferral exists to excuse."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=True,
    )
    campaign_fingerprint = json.loads((campaign_dir / "campaign.json").read_text())["campaign_fingerprint"]
    qwen_trial_payloads = _pairs_trial_payloads(
        _passing_pairs(12),
        block_id="qwen",
        session_fingerprint="qwen-session",
        campaign_fingerprint=campaign_fingerprint,
    )
    _write_json(campaign_dir / "blocks" / "qwen" / "trials" / "trial-001.json", qwen_trial_payloads[1])

    with pytest.raises(CampaignReportError, match="committed trial"):
        build_campaign_report(campaign_dir)


def test_report_refuses_qwen_deferral_when_a_session_lock_was_minted_despite_disposition(tmp_path):
    """A2 (external review), second half: a block that actually minted a
    counted session.json means admission SUCCEEDED at some point -- not the
    failure-before-any-session-lock scenario deferral exists for. A
    well-corroborated disposition record must never override that."""
    campaign_dir, gemma_trials = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=True,
    )
    campaign_fingerprint = json.loads((campaign_dir / "campaign.json").read_text())["campaign_fingerprint"]
    # Qwen: a session.json WAS minted (admission actually succeeded and
    # created a counted session lock) but zero trials were ever committed.
    _write_block(
        campaign_dir,
        "qwen",
        pairs=_passing_pairs(12),
        campaign_fingerprint=campaign_fingerprint,
        commit_indices=set(),
        write_session=True,
    )

    with pytest.raises(CampaignReportError, match="session.json"):
        build_campaign_report(campaign_dir)


def test_report_refuses_qwen_deferral_from_unclassified_failure_even_if_corroborated(tmp_path):
    """Finding 4 (final review), part (a): `unexpected_admission_error` can
    never become a deferral, no matter how many times it repeats --
    "Unknown failures cannot be converted into a deferral." Two
    unclassified failures plus a disposition record must still be
    refused."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )
    admissions_dir = campaign_dir / "admissions"
    admissions_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        admissions_dir / "qwen-attempt-001.json",
        {"block_id": "qwen", "ok": False, "failure": {"code": "unexpected_admission_error", "details": {}}},
    )
    _write_json(
        admissions_dir / "qwen-attempt-002.json",
        {
            "block_id": "qwen",
            "disposition": REPLICATION_DEFERRED_ADMISSION,
            "underlying_failure": {"code": "unexpected_admission_error"},
        },
    )

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


# ---------------------------------------------------------------------------
# External review wave G
# ---------------------------------------------------------------------------


def test_campaign_report_refuses_copied_evidence_with_forged_identity_and_stale_fingerprint(tmp_path):
    """G1 (external review wave G), the PROVEN exploit: copy blocks/gemma
    wholesale to blocks/qwen, edit session.json's block_id to "qwen" AND
    its model_config to the campaign's declared qwen config, and leave
    session_fingerprint untouched -- every trial still references that
    stale fingerprint, so the D4 field cross-checks and every per-trial
    stamp comparison pass, and the reporter used to emit CALIBRATED with
    qwen's model_config over gemma's trials. The session_fingerprint must
    now be re-derived from the session document itself and refused on
    mismatch (typed error, never a verdict)."""
    import shutil

    campaign_dir = tmp_path / "campaign"
    pairs = _passing_gemma_pairs()
    _build_full_two_block_campaign(
        campaign_dir, gemma_pairs=pairs, qwen_pairs=pairs, rules=_rules12(), audit_indices=[1, 2]
    )
    shutil.rmtree(campaign_dir / "blocks" / "qwen")
    shutil.copytree(campaign_dir / "blocks" / "gemma", campaign_dir / "blocks" / "qwen")
    campaign_lock = json.loads((campaign_dir / "campaign.json").read_text(encoding="utf-8"))
    qwen_declared = next(m for m in campaign_lock["models"] if m["block_id"] == "qwen")
    session_path = campaign_dir / "blocks" / "qwen" / "session.json"
    session_payload = json.loads(session_path.read_text(encoding="utf-8"))
    session_payload["block_id"] = "qwen"
    session_payload["model_config"] = dict(qwen_declared)
    # Deliberately NOT re-fingerprinted: the stale stamp is the exploit.
    _write_json(session_path, session_payload)

    with pytest.raises(CampaignReportError, match="session_fingerprint"):
        build_campaign_report(campaign_dir)


@pytest.mark.parametrize("code", ["backend_auth_error", "backend_transport_error"])
def test_report_refuses_qwen_deferral_for_auth_or_transport_code_even_fully_corroborated(
    tmp_path, code
):
    """G2 (external review wave G, Ruling H): auth/transport failure codes
    are operator/environment codes, never deferral-eligible -- a forged
    REPLICATION_DEFERRED_ADMISSION built on them must be refused even with
    perfect two-failure corroboration."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )
    _write_qwen_deferral_record(campaign_dir, corroboration="two_attempts", code=code)

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


def test_report_refuses_qwen_deferral_when_remediation_lacks_preceding_failure(tmp_path):
    """G3 (external review wave G), the weakest shape the old D1 remediation
    OR-branch accepted: an UNRELATED successful remediation with NO
    preceding same-code failure at all (nothing for the "fix" to have been
    a fix for), followed by a single failure first observed only after it,
    then the disposition. Must be REJECTED -- the remediation branch
    requires the same-code failure both before AND after the remediation."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )
    _write_qwen_deferral_record(campaign_dir, corroboration="remediation_without_preceding_failure")

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


def test_report_refuses_qwen_deferral_from_unstamped_admission_records(tmp_path):
    """G4 (external review wave G): admission/remediation/disposition
    records with no campaign_fingerprint stamp at all (forged into the run
    dir without going through CampaignStore.record_admission) must never
    corroborate a deferral."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )
    _write_qwen_deferral_record(
        campaign_dir, corroboration="two_attempts", stamp_campaign_fingerprint=False
    )

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


def test_report_refuses_qwen_deferral_stamped_for_a_different_campaign(tmp_path):
    """G4 (external review wave G): fully corroborated records stamped with
    a DIFFERENT campaign's fingerprint (copied in from another run dir)
    must never corroborate a deferral for this campaign."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        qwen_deferral_record=False,
    )
    _write_qwen_deferral_record(
        campaign_dir,
        corroboration="two_attempts",
        campaign_fingerprint="another-campaigns-fingerprint",
    )

    with pytest.raises(CampaignReportError, match="incomplete schedule"):
        build_campaign_report(campaign_dir)


def test_no_admission_sequence_with_fewer_than_two_same_code_failures_is_accepted(tmp_path):
    """Ruling J (external review wave H) enumeration guard: over every
    admissions/ sequence of length <= 3 drawn from {same-code failure,
    other-code failure, successful remediation, failed remediation}
    containing FEWER than two same-code failures, followed by a fully
    stamped, allowlisted-code disposition, the corroboration predicate
    must refuse -- no remediation combination can substitute for the
    second independent same-code observation. A minimal two-failure
    positive control proves the harness itself can accept."""
    from itertools import product

    from civ_mcp.arena.benchmark_campaign_report import (
        _has_valid_replication_deferred_admission,
    )

    fp = "fp-ruling-j-guard"
    code = "tool_canary_failed"

    # Ruling K (wave I, I2): failure records carry mode="counted" -- the
    # exhaustive guard must keep exercising the ORDINAL/CODE arithmetic,
    # not trip on the (separately tested) counted-mode filter.
    def _same_code_failure() -> dict:
        return {"block_id": "qwen", "mode": "counted", "ok": False, "failure": {"code": code, "details": {}}, "campaign_fingerprint": fp}

    def _other_code_failure() -> dict:
        return {"block_id": "qwen", "mode": "counted", "ok": False, "failure": {"code": "seed_not_honored", "details": {}}, "campaign_fingerprint": fp}

    def _successful_remediation() -> dict:
        return {"block_id": "qwen", "remediation": "terminate_tuner_pid", "result": {"ok": True, "terminated_pid": 4242}, "campaign_fingerprint": fp}

    def _failed_remediation() -> dict:
        return {"block_id": "qwen", "remediation": "terminate_tuner_pid", "result": {"ok": False, "reason": "no_holder"}, "campaign_fingerprint": fp}

    alphabet = {
        "F": _same_code_failure,
        "O": _other_code_failure,
        "R": _successful_remediation,
        "r": _failed_remediation,
    }
    disposition = {
        "block_id": "qwen",
        "disposition": REPLICATION_DEFERRED_ADMISSION,
        "underlying_failure": {"code": code},
        "campaign_fingerprint": fp,
    }

    def _write_sequence(seq_dir: Path, letters: tuple[str, ...]) -> None:
        admissions = seq_dir / "admissions"
        admissions.mkdir(parents=True)
        for ordinal, letter in enumerate(letters, start=1):
            _write_json(admissions / f"qwen-attempt-{ordinal:03d}.json", alphabet[letter]())
        _write_json(admissions / f"qwen-attempt-{len(letters) + 1:03d}.json", disposition)

    checked = 0
    for length in range(0, 4):
        for combo in product(alphabet, repeat=length):
            if sum(1 for letter in combo if letter == "F") >= 2:
                continue
            seq_dir = tmp_path / f"seq-{checked:03d}-{''.join(combo) or 'empty'}"
            _write_sequence(seq_dir, combo)
            assert _has_valid_replication_deferred_admission(seq_dir, "qwen", fp) is False, combo
            checked += 1
    assert checked > 0

    # Positive control: the minimal two-same-code-failure shape IS accepted
    # -- proves the fixtures above are capable of acceptance at all.
    positive_dir = tmp_path / "seq-positive-FF"
    _write_sequence(positive_dir, ("F", "F"))
    assert _has_valid_replication_deferred_admission(positive_dir, "qwen", fp) is True


# ---------------------------------------------------------------------------
# Sensitivity / direction / effect gates
# ---------------------------------------------------------------------------


def test_sensitivity_requires_ten_decided_pairs(tmp_path):
    # 9 decided pairs (standard wins each) + 3 zero-zero tied pairs = 12.
    gemma_pairs = [(f"p{i}", 0, 6) for i in range(9)] + [(f"z{i}", 0, 0) for i in range(3)]
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=gemma_pairs, rules=_rules12(), audit_indices=[1, 2]
    )

    report = build_campaign_report(campaign_dir)
    gemma = report["blocks"]["gemma"]

    assert gemma["calibration"]["decided_count"] == 9
    assert gemma["calibration"]["sensitivity_ok"] is False
    # Fewer than ten decided with no tie_attribution.json present: pending
    # review, not an automatic verdict of any kind.
    assert gemma["outcome"] == "TIE_ATTRIBUTION_REQUIRED"
    assert report["verdict"]["outcome"] == "BLOCKED"


def test_direction_requires_ten_standard_wins_out_of_original_twelve(tmp_path):
    # All 12 pairs decided (no ties at all): 9 standard wins, 3 baseline wins.
    gemma_pairs = [(f"p{i}", 0, 6) for i in range(9)] + [(f"b{i}", 6, 0) for i in range(3)]
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=gemma_pairs, rules=_rules12(), audit_indices=[1, 2]
    )

    report = build_campaign_report(campaign_dir)
    calibration = report["blocks"]["gemma"]["calibration"]

    assert calibration["decided_count"] == 12
    assert calibration["sensitivity_ok"] is True
    assert calibration["standard_wins"] == 9
    assert calibration["direction_ok"] is False
    assert report["blocks"]["gemma"]["outcome"] == "MODEL_NULL"


def test_effect_requires_frozen_normalized_threshold(tmp_path):
    # All 12 pairs decided, standard wins all 12, but each delta is only
    # 2/12 -- below the frozen 4/12 minimum_median_normalized_delta.
    gemma_pairs = [(f"p{i}", 0, 2) for i in range(12)]
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=gemma_pairs, rules=_rules12(), audit_indices=[1, 2]
    )

    report = build_campaign_report(campaign_dir)
    calibration = report["blocks"]["gemma"]["calibration"]

    assert calibration["sensitivity_ok"] is True
    assert calibration["direction_ok"] is True
    assert calibration["median_signed_normalized_delta"] == pytest.approx(2 / 12)
    assert calibration["effect_ok"] is False
    assert report["blocks"]["gemma"]["outcome"] == "MODEL_NULL"


def test_sufficiently_decided_separation_failure_is_model_null(tmp_path):
    # decided == 10 exactly (right at the sensitivity threshold), with 2
    # genuine ties present -- but direction still fails (7 < 10 wins), so
    # this must resolve directly to MODEL_NULL without ever touching tie
    # attribution at all (sensitivity already passed).
    gemma_pairs = (
        [(f"w{i}", 0, 6) for i in range(7)]
        + [(f"l{i}", 6, 0) for i in range(3)]
        + [("tz", 0, 0), ("tn", 6, 6)]
    )
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=gemma_pairs, rules=_rules12(), audit_indices=[1, 2]
    )

    report = build_campaign_report(campaign_dir)
    calibration = report["blocks"]["gemma"]["calibration"]

    assert calibration["decided_count"] == 10
    assert calibration["sensitivity_ok"] is True
    assert calibration["standard_wins"] == 7
    assert calibration["direction_ok"] is False
    assert report["blocks"]["gemma"]["outcome"] == "MODEL_NULL"


# ---------------------------------------------------------------------------
# Tie attribution: mechanical labels are starting hypotheses only
# ---------------------------------------------------------------------------


def test_zero_ties_are_only_floor_candidates_until_reviewed(tmp_path):
    # 8 decided + 4 zero-zero (mechanically "model floor candidate") tied
    # pairs, but NO tie_attribution.json -- the mechanical label alone must
    # never be enough to assign MODEL_FLOOR_NULL.
    gemma_pairs = [(f"p{i}", 0, 6) for i in range(8)] + [(f"z{i}", 0, 0) for i in range(4)]
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=gemma_pairs, rules=_rules12(), audit_indices=[1, 2]
    )

    report = build_campaign_report(campaign_dir)
    gemma = report["blocks"]["gemma"]

    assert gemma["calibration"]["tie_count"] == 4
    assert gemma["tie_attribution"]["required"] is True
    assert gemma["tie_attribution"]["resolved"] is False
    assert gemma["outcome"] == "TIE_ATTRIBUTION_REQUIRED"
    assert gemma["outcome"] != "MODEL_FLOOR_NULL"


def test_nonzero_ties_are_only_rubric_candidates_until_reviewed(tmp_path):
    # 8 decided + 4 nonzero (both score 6; mechanically "rubric-insensitivity
    # candidate") tied pairs, but NO tie_attribution.json -- the mechanical
    # label alone must never be enough to assign NONDISCRIMINATIVE.
    gemma_pairs = [(f"p{i}", 0, 6) for i in range(8)] + [(f"n{i}", 6, 6) for i in range(4)]
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=gemma_pairs, rules=_rules12(), audit_indices=[1, 2]
    )

    report = build_campaign_report(campaign_dir)
    gemma = report["blocks"]["gemma"]

    assert gemma["calibration"]["tie_count"] == 4
    assert gemma["tie_attribution"]["resolved"] is False
    assert gemma["outcome"] == "TIE_ATTRIBUTION_REQUIRED"
    assert gemma["outcome"] != "NONDISCRIMINATIVE"


def test_one_rubric_caused_tie_makes_block_nondiscriminative(tmp_path):
    # 8 decided + 2 zero ties (reviewed: model_floor) + 2 nonzero ties
    # (reviewed: one same_progress, one rubric_nondiscriminative). Even one
    # consequential rubric-caused tie makes the WHOLE block nondiscriminative
    # -- never a selective discard of just that one pair.
    gemma_pairs = (
        [(f"p{i}", 0, 6) for i in range(8)]
        + [("z0", 0, 0), ("z1", 0, 0)]
        + [("n0", 6, 6), ("n1", 6, 6)]
    )
    tie_attribution = {
        "z0": "model_floor",
        "z1": "model_floor",
        "n0": "same_progress",
        "n1": "rubric_nondiscriminative",
    }
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=gemma_pairs,
        rules=_rules12(),
        audit_indices=[1, 2],
        gemma_tie_attribution=tie_attribution,
    )

    report = build_campaign_report(campaign_dir)

    assert report["blocks"]["gemma"]["tie_attribution"]["resolved"] is True
    assert report["blocks"]["gemma"]["outcome"] == "NONDISCRIMINATIVE"
    assert report["verdict"]["outcome"] == "RUBRIC_NONDISCRIMINATIVE"


def test_model_floor_and_same_progress_null_preserve_other_block(tmp_path):
    scenarios = [
        ("model_floor", "MODEL_FLOOR_NULL", [(f"z{i}", 0, 0) for i in range(4)]),
        ("same_progress", "MODEL_TIE_NULL", [(f"n{i}", 6, 6) for i in range(4)]),
    ]
    for attribution_value, expected_gemma_outcome, tied_pairs in scenarios:
        campaign_dir = tmp_path / f"campaign-{attribution_value}"
        campaign_dir.mkdir()
        gemma_pairs = [(f"p{i}", 0, 6) for i in range(8)] + tied_pairs
        tie_attribution = {pair_id: attribution_value for pair_id, _, _ in tied_pairs}
        qwen_pairs = _passing_gemma_pairs()

        _build_full_two_block_campaign(
            campaign_dir,
            gemma_pairs=gemma_pairs,
            qwen_pairs=qwen_pairs,
            rules=_rules12(),
            audit_indices=[1, 2],
            gemma_tie_attribution=tie_attribution,
        )

        report = build_campaign_report(campaign_dir)

        assert report["blocks"]["gemma"]["outcome"] == expected_gemma_outcome, attribution_value
        # The other (Qwen) block's own PASS must be preserved untouched --
        # a model-floor/same-progress null on the primary block must never
        # corrupt or hide the secondary block's independent evidence.
        assert report["blocks"]["qwen"]["outcome"] == "PASS", attribution_value
        assert report["verdict"]["outcome"] == "CALIBRATED", attribution_value


# ---------------------------------------------------------------------------
# Round-1 review finding 3: MODEL_FLOOR_NULL must never be reachable with
# zero reviewed attributions.
# ---------------------------------------------------------------------------


def test_calibration_rules_reject_minimum_decided_exceeding_pairs_per_model(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(minimum_decided=20),  # > pairs_per_model (12): impossible to satisfy
        audit_indices=[1, 2],
    )

    with pytest.raises(CampaignReportError, match="minimum_decided_pairs"):
        build_campaign_report(campaign_dir)


@pytest.mark.parametrize(
    ("rules_override", "match"),
    [
        ({"minimum_decided": -1, "minimum_wins": -1, "minimum_delta": -1.0}, "minimum_decided_pairs"),
        ({"minimum_wins": -1}, "minimum_standard_wins"),
        # G6 (external review wave G): ZERO thresholds switch the
        # sensitivity/direction gates off entirely -- rejected exactly like
        # negative ones. minimum_decided=0 forces minimum_wins=0 too (wins
        # may never exceed decided), so decided is what the error names.
        ({"minimum_decided": 0, "minimum_wins": 0}, "minimum_decided_pairs"),
        ({"minimum_wins": 0}, "minimum_standard_wins"),
        ({"minimum_delta": 0.0}, "minimum_median_normalized_delta"),
        ({"minimum_delta": -1.0}, "minimum_median_normalized_delta"),
        ({"minimum_wins": 11}, "minimum_standard_wins"),  # wins > decided (10): unsatisfiable arithmetic
    ],
)
def test_report_rejects_vacuous_or_negative_calibration_rule_thresholds(tmp_path, rules_override, match):
    """D5 (external review wave D), reporter side (defense in depth --
    load_campaign_manifest enforces the same bounds, but a hand-authored,
    self-consistently-fingerprinted campaign.json never went through that
    loader): with all minimums at -1 (or, G6, at 0), twelve 0-0 ties would
    satisfy every gate and report PASS. The reporter must refuse such a
    rules block outright, naming the offending field."""
    gemma_pairs = [(f"z{i}", 0, 0) for i in range(12)]  # twelve 0-0 ties
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=gemma_pairs,
        rules=_rules12(**rules_override),
        audit_indices=[1, 2],
    )

    with pytest.raises(CampaignReportError, match=match):
        build_campaign_report(campaign_dir)


def test_block_outcome_never_derives_floor_null_from_empty_attributions():
    """Direct unit test of the defense-in-depth guard in `_block_outcome`:
    even if `tie_attribution` reports `resolved: True` with an EMPTY
    attributions mapping (only reachable via a malformed rules config that
    `_require_calibration_rules` now rejects upstream), the outcome must
    never be MODEL_FLOOR_NULL (the bug: `set() <= {"model_floor"}` is
    vacuously true)."""
    calibration = {"sensitivity_ok": False}
    metric_fidelity = {"ok": True}
    tie_attribution = {"resolved": True, "attributions": {}}

    outcome = _block_outcome(calibration, metric_fidelity, tie_attribution)

    assert outcome == "TIE_ATTRIBUTION_REQUIRED"
    assert outcome != "MODEL_FLOOR_NULL"


# ---------------------------------------------------------------------------
# Round-1 review finding 1: campaign.json self-integrity
# ---------------------------------------------------------------------------


def test_campaign_fingerprint_tamper_is_detected(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    campaign_path = campaign_dir / "campaign.json"
    lock = json.loads(campaign_path.read_text(encoding="utf-8"))
    # Lower the effect threshold without touching campaign_fingerprint.
    lock["rules"]["minimum_median_normalized_delta"] = 0.0
    _write_json(campaign_path, lock)

    with pytest.raises(CampaignReportError, match="campaign_fingerprint"):
        build_campaign_report(campaign_dir)


def test_campaign_fingerprint_tamper_via_trimmed_audit_indices_is_detected(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2, 3, 4]
    )
    campaign_path = campaign_dir / "campaign.json"
    lock = json.loads(campaign_path.read_text(encoding="utf-8"))
    lock["audit_indices"] = [1]  # trimmed without recomputing campaign_fingerprint
    _write_json(campaign_path, lock)

    with pytest.raises(CampaignReportError, match="campaign_fingerprint"):
        build_campaign_report(campaign_dir)


# ---------------------------------------------------------------------------
# Round-1 review finding 2a: contracts.scorer_fingerprint fail-closed
# ---------------------------------------------------------------------------


def test_campaign_missing_contracts_fails_closed(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        include_contracts=False,
    )

    with pytest.raises(CampaignReportError, match="contracts"):
        build_campaign_report(campaign_dir)


def test_campaign_scorer_fingerprint_mismatch_fails_closed(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        scorer_fingerprint="score-v1",
        gemma_scorer_fingerprint_override="score-DIFFERENT",
    )

    with pytest.raises(CampaignReportError, match="scorer_fingerprint"):
        build_campaign_report(campaign_dir)


# ---------------------------------------------------------------------------
# Round-1 review finding 2b: digests.schedule fail-closed
# ---------------------------------------------------------------------------


def test_campaign_missing_digests_fails_closed(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        include_digests=False,
    )

    with pytest.raises(CampaignReportError, match="digests"):
        build_campaign_report(campaign_dir)


def test_campaign_schedule_digest_mismatch_fails_closed(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    # Tamper schedule.json AFTER campaign.json's digest was computed --
    # campaign.json itself is untouched, so this exercises the schedule
    # digest check specifically (not the campaign_fingerprint check).
    schedule_path = campaign_dir / "schedule.json"
    schedule_payload = json.loads(schedule_path.read_text(encoding="utf-8"))
    schedule_payload["blocks"]["gemma"]["trials"][0]["arm_id"] = "standard"
    _write_json(schedule_path, schedule_payload)

    with pytest.raises(CampaignReportError, match="digests.schedule"):
        build_campaign_report(campaign_dir)


# ---------------------------------------------------------------------------
# Round-1 review finding 2c: position_id fail-closed
# ---------------------------------------------------------------------------


def test_campaign_missing_position_id_fails_closed(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        position_id=None,
        gemma_position_id=POSITION_ID,
    )

    with pytest.raises(CampaignReportError, match="position_id"):
        build_campaign_report(campaign_dir)


def test_campaign_position_id_mismatch_fails_closed(tmp_path):
    """The session lock's own declared position_id must equal
    campaign.json's declared position_id. H1 (wave H) note: the tamper is
    applied to session.json's position_id field ALONE (re-minted and
    restamped so every fingerprint check passes) -- a block whose
    schedule/trials also diverged would now be refused earlier, by the
    H1(b) block-schedule binding or the H1(a) per-trial schedule binding,
    before this session-level cross-check is ever reached."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
    )
    session_path = campaign_dir / "blocks" / "gemma" / "session.json"
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["position_id"] = "pos-OTHER"
    payload = _self_fingerprinted(payload)
    _write_json(session_path, payload)
    for trial_path in sorted((campaign_dir / "blocks" / "gemma" / "trials").glob("trial-*.json")):
        trial_payload = json.loads(trial_path.read_text(encoding="utf-8"))
        trial_payload["session_fingerprint"] = payload["session_fingerprint"]
        _write_json(trial_path, trial_payload)

    with pytest.raises(CampaignReportError, match="position_id"):
        build_campaign_report(campaign_dir)


# ---------------------------------------------------------------------------
# Metric fidelity
# ---------------------------------------------------------------------------


def test_metric_fidelity_disagreement_blocks_campaign_verdict(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        gemma_audit_disagree_indices=frozenset({1}),
    )

    report = build_campaign_report(campaign_dir)
    gemma = report["blocks"]["gemma"]

    assert gemma["metric_fidelity"]["ok"] is False
    assert gemma["outcome"] == "METRIC_FIDELITY_FAILED"
    assert report["verdict"]["outcome"] == "BLOCKED"


def test_metric_fidelity_hash_mismatch_blocks_campaign_verdict(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        gemma_audit_hash_mismatch_indices=frozenset({2}),
    )

    report = build_campaign_report(campaign_dir)
    gemma = report["blocks"]["gemma"]

    assert gemma["metric_fidelity"]["ok"] is False
    assert any(m["index"] == 2 for m in gemma["metric_fidelity"]["mismatches"])
    assert report["verdict"]["outcome"] == "BLOCKED"


def test_metric_fidelity_missing_audit_blocks_campaign_verdict(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        gemma_omit_audit=True,
    )

    report = build_campaign_report(campaign_dir)

    assert report["blocks"]["gemma"]["metric_fidelity"]["ok"] is False
    assert report["blocks"]["gemma"]["outcome"] == "METRIC_FIDELITY_FAILED"
    assert report["verdict"]["outcome"] == "BLOCKED"


def test_metric_fidelity_records_every_audited_hash_not_only_mismatches(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2, 3]
    )

    report = build_campaign_report(campaign_dir)
    entries = report["blocks"]["gemma"]["metric_fidelity"]["entries"]

    assert {entry["index"] for entry in entries} == {1, 2, 3}
    assert all(entry["agrees"] is True for entry in entries)
    assert all(isinstance(entry["trial_sha256"], str) and entry["trial_sha256"] for entry in entries)


def test_metric_fidelity_truncated_audit_json_raises_campaign_report_error(tmp_path):
    """A7 (external review): a truncated/corrupt audit.json must abort
    report generation with a typed, file-naming CampaignReportError --
    never a raw JSONDecodeError, and never silently treated the same as an
    absent audit.json (which merely produces a soft METRIC_FIDELITY_FAILED
    block outcome rather than aborting)."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    audit_path = campaign_dir / "blocks" / "gemma" / "audit.json"
    audit_path.write_text('{"session_fingerprint": "gemma-session", "trials": [', encoding="utf-8")

    with pytest.raises(CampaignReportError, match=re.escape(str(audit_path))):
        build_campaign_report(campaign_dir)


# ---------------------------------------------------------------------------
# Round-1 review finding 6: tie-attribution refusal paths
# ---------------------------------------------------------------------------


def test_tie_attribution_hash_mismatch_leaves_block_unresolved(tmp_path):
    gemma_pairs = [(f"p{i}", 0, 6) for i in range(8)] + [(f"z{i}", 0, 0) for i in range(4)]
    tie_attribution = {f"z{i}": "model_floor" for i in range(4)}
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=gemma_pairs,
        rules=_rules12(),
        audit_indices=[1, 2],
        gemma_tie_attribution=tie_attribution,
        gemma_tie_attribution_hash_break=frozenset({"z0"}),
    )

    report = build_campaign_report(campaign_dir)
    gemma = report["blocks"]["gemma"]

    assert gemma["tie_attribution"]["resolved"] is False
    assert "trial_sha256" in gemma["tie_attribution"]["reason"]
    assert gemma["outcome"] == "TIE_ATTRIBUTION_REQUIRED"


def test_tie_attribution_missing_per_pair_entry_leaves_block_unresolved(tmp_path):
    gemma_pairs = [(f"p{i}", 0, 6) for i in range(8)] + [(f"z{i}", 0, 0) for i in range(4)]
    # Only 3 of the 4 tied pairs get an attribution entry.
    tie_attribution = {f"z{i}": "model_floor" for i in range(3)}
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=gemma_pairs,
        rules=_rules12(),
        audit_indices=[1, 2],
        gemma_tie_attribution=tie_attribution,
    )

    report = build_campaign_report(campaign_dir)
    gemma = report["blocks"]["gemma"]

    assert gemma["tie_attribution"]["resolved"] is False
    assert "z3" in gemma["tie_attribution"]["reason"]
    assert gemma["outcome"] == "TIE_ATTRIBUTION_REQUIRED"


def test_tie_attribution_invalid_value_leaves_block_unresolved(tmp_path):
    gemma_pairs = [(f"p{i}", 0, 6) for i in range(8)] + [(f"z{i}", 0, 0) for i in range(4)]
    tie_attribution = {f"z{i}": "model_floor" for i in range(3)}
    tie_attribution["z3"] = "not_a_real_attribution"
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=gemma_pairs,
        rules=_rules12(),
        audit_indices=[1, 2],
        gemma_tie_attribution=tie_attribution,
    )

    report = build_campaign_report(campaign_dir)
    gemma = report["blocks"]["gemma"]

    assert gemma["tie_attribution"]["resolved"] is False
    assert "invalid attribution" in gemma["tie_attribution"]["reason"]
    assert gemma["outcome"] == "TIE_ATTRIBUTION_REQUIRED"


@pytest.mark.parametrize(
    "strip_fields",
    [
        # All three human-review fields absent: only pair_id/attribution/hashes.
        ("transcript_finding", "final_state_finding", "counterfactual_fixture_result"),
        ("transcript_finding",),
        ("final_state_finding",),
        ("counterfactual_fixture_result",),
    ],
)
def test_tie_attribution_entry_without_review_findings_is_refused(tmp_path, strip_fields):
    """D7 (external review wave D): an attribution entry carrying only
    pair_id/attribution/hashes -- no transcript finding, no final-state
    finding, no counterfactual fixture result -- is not a human review at
    all; honoring it would mint MODEL_FLOOR_NULL/MODEL_TIE_NULL without
    any review evidence. Every review field must be a non-empty string
    before the entry counts; anything less is refused with a typed
    CampaignReportError naming the pair and the missing field(s)."""
    gemma_pairs = [(f"p{i}", 0, 6) for i in range(8)] + [(f"z{i}", 0, 0) for i in range(4)]
    tie_attribution = {f"z{i}": "model_floor" for i in range(4)}
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=gemma_pairs,
        rules=_rules12(),
        audit_indices=[1, 2],
        gemma_tie_attribution=tie_attribution,
    )
    tie_path = campaign_dir / "blocks" / "gemma" / "tie_attribution.json"
    payload = json.loads(tie_path.read_text(encoding="utf-8"))
    for entry in payload["attributions"]:
        if entry["pair_id"] == "z2":
            for field in strip_fields:
                del entry[field]
    _write_json(tie_path, payload)

    with pytest.raises(CampaignReportError) as excinfo:
        build_campaign_report(campaign_dir)
    assert "z2" in str(excinfo.value)
    assert strip_fields[0] in str(excinfo.value)


@pytest.mark.parametrize("empty_value", ["", "   ", None, 42])
def test_tie_attribution_entry_with_blank_or_nonstring_review_finding_is_refused(tmp_path, empty_value):
    """D7 companion: a review field that is present but empty, whitespace,
    None, or a non-string carries no review evidence either."""
    gemma_pairs = [(f"p{i}", 0, 6) for i in range(8)] + [(f"z{i}", 0, 0) for i in range(4)]
    tie_attribution = {f"z{i}": "model_floor" for i in range(4)}
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=gemma_pairs,
        rules=_rules12(),
        audit_indices=[1, 2],
        gemma_tie_attribution=tie_attribution,
    )
    tie_path = campaign_dir / "blocks" / "gemma" / "tie_attribution.json"
    payload = json.loads(tie_path.read_text(encoding="utf-8"))
    for entry in payload["attributions"]:
        if entry["pair_id"] == "z0":
            entry["transcript_finding"] = empty_value
    _write_json(tie_path, payload)

    with pytest.raises(CampaignReportError, match="transcript_finding"):
        build_campaign_report(campaign_dir)


def test_tie_attribution_truncated_json_raises_campaign_report_error(tmp_path):
    """A7 (external review), tie_attribution.json side: same discipline as
    the audit.json truncation test above."""
    gemma_pairs = [(f"p{i}", 0, 6) for i in range(8)] + [(f"z{i}", 0, 0) for i in range(4)]
    tie_attribution = {f"z{i}": "model_floor" for i in range(4)}
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=gemma_pairs,
        rules=_rules12(),
        audit_indices=[1, 2],
        gemma_tie_attribution=tie_attribution,
    )
    tie_path = campaign_dir / "blocks" / "gemma" / "tie_attribution.json"
    tie_path.write_text('{"session_fingerprint": "gemma-session", "attributions": [', encoding="utf-8")

    with pytest.raises(CampaignReportError, match=re.escape(str(tie_path))):
        build_campaign_report(campaign_dir)


@pytest.mark.parametrize(
    "relative_path",
    [
        "campaign.json",
        "schedule.json",
        "blocks/gemma/session.json",
        "blocks/gemma/trials/trial-001.json",
        "blocks/gemma/audit.json",
    ],
)
def test_truncated_multibyte_evidence_raises_typed_error_not_unicode_decode_error(tmp_path, relative_path):
    """D9 (external review wave D): a write truncated mid-multibyte-
    sequence raises UnicodeDecodeError from read_text before json.loads
    ever runs -- only json.JSONDecodeError was wrapped, and campaign.json/
    schedule.json/session.json/trial reads were entirely unwrapped. Every
    evidence read must surface as a typed CampaignReportError naming the
    file, never a raw UnicodeDecodeError/OSError/JSONDecodeError."""
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    target = campaign_dir / relative_path
    # b'{"a": "\xe2\x82' -- a euro sign truncated after two of its three bytes.
    target.write_bytes(b'{"a": "\xe2\x82')

    with pytest.raises(CampaignReportError, match=re.escape(str(target))):
        build_campaign_report(campaign_dir)


# ---------------------------------------------------------------------------
# attempts/ is never read
# ---------------------------------------------------------------------------


def test_report_never_reads_attempts_directory(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    attempts_dir = campaign_dir / "blocks" / "gemma" / "attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    (attempts_dir / "broken.json").write_text("{not valid json at all", encoding="utf-8")

    report = build_campaign_report(campaign_dir)  # must not raise
    assert report["blocks"]["gemma"]["outcome"] == "PASS"
    assert not any("attempts" in p for p in report["report_inputs"])


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_report_regenerates_byte_identically_without_wall_clock_fields(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )

    write_campaign_reports(campaign_dir)
    first_json = (campaign_dir / "campaign_report.json").read_bytes()
    first_md = (campaign_dir / "campaign_report.md").read_bytes()

    write_campaign_reports(campaign_dir)
    second_json = (campaign_dir / "campaign_report.json").read_bytes()
    second_md = (campaign_dir / "campaign_report.md").read_bytes()

    assert hashlib.sha256(second_json).hexdigest() == hashlib.sha256(first_json).hexdigest()
    assert hashlib.sha256(second_md).hexdigest() == hashlib.sha256(first_md).hexdigest()

    raw = (campaign_dir / "campaign_report.json").read_text(encoding="utf-8")
    assert json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":")) == raw


def test_scorer_only_fingerprint_change_rescores_same_raw_trials(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path,
        gemma_pairs=_passing_gemma_pairs(),
        rules=_rules12(),
        audit_indices=[1, 2],
        scorer_fingerprint="score-v1",
    )

    report1 = build_campaign_report(campaign_dir)
    assert report1["blocks"]["gemma"]["report"]["scorer"]["fingerprint"] == "score-v1"

    # Simulate a legitimate re-freeze after a pure scorer-implementation fix
    # (design doc: "a pure implementation correction ... changes the scorer
    # fingerprint and may regenerate reports over unchanged raw evidence"):
    # campaign.json's contracts.scorer_fingerprint changes, so
    # campaign_fingerprint (which covers the whole lock) changes too, and
    # every affected block's session.json + committed trials are re-stamped
    # with the new campaign_fingerprint -- but NO raw trial content
    # (steps/initial_state/final_state) changes at all.
    campaign_path = campaign_dir / "campaign.json"
    campaign_lock = json.loads(campaign_path.read_text(encoding="utf-8"))
    campaign_lock["contracts"]["scorer_fingerprint"] = "score-v2"
    body = {k: v for k, v in campaign_lock.items() if k != "campaign_fingerprint"}
    new_campaign_fingerprint = fingerprint(body)
    campaign_lock["campaign_fingerprint"] = new_campaign_fingerprint
    _write_json(campaign_path, campaign_lock)

    session_path = campaign_dir / "blocks" / "gemma" / "session.json"
    session_payload = json.loads(session_path.read_text(encoding="utf-8"))
    session_payload["scorer_fingerprint"] = "score-v2"
    session_payload["campaign_fingerprint"] = new_campaign_fingerprint
    # G1 (external review wave G): a legitimate re-freeze re-MINTS the
    # session lock, so its session_fingerprint is recomputed over the
    # edited contents (exactly what build_session_lock would produce) and
    # every trial is re-stamped with both new fingerprints.
    session_payload = _self_fingerprinted(session_payload)
    new_session_fingerprint = session_payload["session_fingerprint"]
    _write_json(session_path, session_payload)

    trials_dir = campaign_dir / "blocks" / "gemma" / "trials"
    restamped_trials: dict[int, dict] = {}
    for trial_path in sorted(trials_dir.glob("trial-*.json")):
        trial_payload = json.loads(trial_path.read_text(encoding="utf-8"))
        trial_payload["campaign_fingerprint"] = new_campaign_fingerprint
        trial_payload["session_fingerprint"] = new_session_fingerprint
        _write_json(trial_path, trial_payload)
        restamped_trials[trial_payload["index"]] = trial_payload

    # audit.json's trial_sha256 hash-binds to the exact raw trial bytes --
    # since the re-freeze legitimately changed those bytes (new
    # campaign_fingerprint/session_fingerprint stamps), the human review
    # record is re-issued against the SAME manual findings, re-hashed to
    # the restamped trials.
    _write_audit(
        campaign_dir / "blocks" / "gemma",
        session_fingerprint=new_session_fingerprint,
        audit_indices=[1, 2],
        trial_payloads=restamped_trials,
    )
    # I1 (external review wave I): the re-freeze re-mints the session lock,
    # so the counted admission SUCCESS record anchoring it is re-issued with
    # the new fingerprints too (the real re-freeze runs admission again --
    # a session lock is only ever minted by admit(), which always records
    # its success before any trial runs).
    _write_counted_admission_record(
        campaign_dir,
        "gemma",
        session_fingerprint=new_session_fingerprint,
        campaign_fingerprint=new_campaign_fingerprint,
    )

    # G4 (external review wave G): the deferral-corroboration scan only
    # honors records stamped with the campaign under report -- a re-freeze
    # re-issues the qwen admission records bound to the new fingerprint.
    for record_path in sorted((campaign_dir / "admissions").glob("qwen-attempt-*.json")):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["campaign_fingerprint"] = new_campaign_fingerprint
        _write_json(record_path, record)

    report2 = build_campaign_report(campaign_dir)

    calibration1 = report1["blocks"]["gemma"]["calibration"]
    calibration2 = report2["blocks"]["gemma"]["calibration"]
    assert report2["blocks"]["gemma"]["report"]["scorer"]["fingerprint"] == "score-v2"
    # Every scoring-relevant number is unchanged -- the raw trial evidence
    # (steps/initial_state/final_state) never changed, so the arithmetic
    # this module derives from it must be identical. `trial_sha256` on each
    # pair IS expected to differ: it hashes the whole raw trial file, which
    # legitimately now carries the new campaign_fingerprint stamp from this
    # re-freeze -- that is not a scoring input.
    for field in ("decided_count", "tie_count", "standard_wins", "median_signed_normalized_delta", "sensitivity_ok", "direction_ok", "effect_ok"):
        assert calibration2[field] == calibration1[field], field
    assert [
        {k: v for k, v in pair.items() if k != "trial_sha256"} for pair in calibration2["pairs"]
    ] == [{k: v for k, v in pair.items() if k != "trial_sha256"} for pair in calibration1["pairs"]]
    assert report2["blocks"]["gemma"]["outcome"] == report1["blocks"]["gemma"]["outcome"] == "PASS"


# ---------------------------------------------------------------------------
# Round-1 review finding 5: model configuration, topology, hashes, inputs
# ---------------------------------------------------------------------------


def test_report_surfaces_model_configuration_and_topology(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )

    report = build_campaign_report(campaign_dir)

    campaign_models = {m["block_id"]: m for m in report["campaign"]["models"]}
    assert campaign_models["gemma"]["endpoint_id"] == "ep-gemma"
    assert "temperature" in campaign_models["gemma"]["sampling"]
    assert "chat_template_kwargs" in campaign_models["gemma"]

    gemma_block = report["blocks"]["gemma"]
    # D4: the surfaced model_config is the session's own, which must (and
    # here does) match campaign.json's declared config for the block.
    assert gemma_block["model_config"]["endpoint_id"] == "ep-gemma"
    assert gemma_block["model_admission"]["gpu_topology"] == {"gpu_ids": [0]}
    assert gemma_block["model_admission"]["resolved_endpoint"] == "http://ep-gemma.local:8000"


def test_report_surfaces_pair_trial_hashes(tmp_path):
    campaign_dir, gemma_trials = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )

    report = build_campaign_report(campaign_dir)
    pair0 = report["blocks"]["gemma"]["calibration"]["pairs"][0]

    assert pair0["trial_sha256"][BASELINE_ARM] == fingerprint(gemma_trials[pair0["trial_indices"][BASELINE_ARM]])
    assert pair0["trial_sha256"][TREATMENT_ARM] == fingerprint(gemma_trials[pair0["trial_indices"][TREATMENT_ARM]])


def test_report_enumerates_report_inputs(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )

    report = build_campaign_report(campaign_dir)
    inputs = report["report_inputs"]

    assert "campaign.json" in inputs
    assert "schedule.json" in inputs
    assert "blocks/gemma/session.json" in inputs
    assert "blocks/gemma/trials/trial-001.json" in inputs
    assert "blocks/gemma/audit.json" in inputs
    assert any(p.startswith("admissions/qwen-attempt-") for p in inputs)
    assert inputs == sorted(inputs)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_campaign_markdown_surfaces_verdict_and_blocks(tmp_path):
    campaign_dir, _ = _build_campaign_with_gemma(
        tmp_path, gemma_pairs=_passing_gemma_pairs(), rules=_rules12(), audit_indices=[1, 2]
    )
    report = build_campaign_report(campaign_dir)
    markdown = render_campaign_markdown(report)

    assert "CALIBRATED_REPLICATION_DEFERRED" in markdown
    assert "## Block: gemma" in markdown
    assert "## Block: qwen" in markdown
    assert markdown.index("## Block: gemma") < markdown.index("## Block: qwen")
    assert "## Report inputs" in markdown
    assert "campaign.json" in markdown
