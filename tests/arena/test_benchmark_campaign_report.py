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
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from civ_mcp.arena.benchmark_campaign_report import (
    REPLICATION_DEFERRED_ADMISSION,
    build_campaign_report,
    render_campaign_markdown,
    write_campaign_reports,
)
from civ_mcp.arena.benchmark_manifest import fingerprint

CAMPAIGN_FINGERPRINT = "camp-fp-1"
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


def _pair_schedule_and_trials(
    pairs: list[tuple[str, int, int]],
    *,
    block_id: str,
    session_fingerprint: str,
    campaign_fingerprint: str = CAMPAIGN_FINGERPRINT,
    start_index: int = 1,
) -> tuple[list[dict], dict[int, dict]]:
    schedule_trials: list[dict] = []
    trial_payloads: dict[int, dict] = {}
    index = start_index
    for pair_id, baseline_score, treatment_score in pairs:
        for arm_id, score in ((BASELINE_ARM, baseline_score), (TREATMENT_ARM, treatment_score)):
            schedule_trials.append(
                {"index": index, "position_id": POSITION_ID, "pair_id": pair_id, "arm_id": arm_id}
            )
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
    return schedule_trials, trial_payloads


def _pair_indices(pairs: list[tuple[str, int, int]], *, start_index: int = 1) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    index = start_index
    for pair_id, _, _ in pairs:
        result[pair_id] = (index, index + 1)
        index += 2
    return result


def _write_block(
    campaign_dir: Path,
    block_id: str,
    *,
    pairs: list[tuple[str, int, int]],
    session_fingerprint: str,
    campaign_fingerprint: str = CAMPAIGN_FINGERPRINT,
    scorer_fingerprint: str = "score-v1",
    commit_indices: set[int] | None = None,
    max_score: int = MAX_SCORE,
) -> tuple[list[dict], dict[int, dict]]:
    block_dir = campaign_dir / "blocks" / block_id
    schedule_trials, trial_payloads = _pair_schedule_and_trials(
        pairs,
        block_id=block_id,
        session_fingerprint=session_fingerprint,
        campaign_fingerprint=campaign_fingerprint,
    )
    _write_json(block_dir / "schedule.json", {"trials": schedule_trials})
    _write_json(
        block_dir / "session.json",
        {
            "position_id": POSITION_ID,
            "block_id": block_id,
            "campaign_fingerprint": campaign_fingerprint,
            "scorer_fingerprint": scorer_fingerprint,
            "session_fingerprint": session_fingerprint,
            "positions": {POSITION_ID: {"rubric": _rubric(max_score)}},
        },
    )
    commit = commit_indices if commit_indices is not None else set(trial_payloads)
    for index in commit:
        _write_json(block_dir / "trials" / f"trial-{index:03d}.json", trial_payloads[index])
    return schedule_trials, trial_payloads


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


def _write_campaign_lock(
    campaign_dir: Path,
    *,
    rules: dict,
    audit_indices: list[int],
    campaign_fingerprint: str = CAMPAIGN_FINGERPRINT,
    scorer_fingerprint: str = "score-v1",
) -> None:
    _write_json(
        campaign_dir / "campaign.json",
        {
            "campaign_id": "camp-1",
            "campaign_schema_version": "v1",
            "campaign_fingerprint": campaign_fingerprint,
            "position_id": POSITION_ID,
            "models": [
                {"block_id": "gemma", "model": "gemma4-27b"},
                {"block_id": "qwen", "model": "qwen3.6-27b"},
            ],
            "arms": [{"arm_id": BASELINE_ARM}, {"arm_id": TREATMENT_ARM}],
            "rules": rules,
            "audit_indices": audit_indices,
            "contracts": {"scorer_fingerprint": scorer_fingerprint},
        },
    )


def _rules12(*, minimum_decided=10, minimum_wins=10, minimum_delta=None, required_audits_per_arm=1) -> dict:
    return {
        "pairs_per_model": 12,
        "minimum_decided_pairs": minimum_decided,
        "minimum_standard_wins": minimum_wins,
        "minimum_median_normalized_delta": (4 / 12) if minimum_delta is None else minimum_delta,
        "required_audits_per_arm": required_audits_per_arm,
    }


def _write_qwen_deferral_record(campaign_dir: Path) -> None:
    admissions_dir = campaign_dir / "admissions"
    admissions_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        admissions_dir / "qwen-attempt-001.json",
        {
            "block_id": "qwen",
            "disposition": REPLICATION_DEFERRED_ADMISSION,
            "underlying_failure": {"code": "endpoint_unreachable"},
        },
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
    qwen_deferral_record: bool = True,
    name: str = "campaign",
    scorer_fingerprint: str = "score-v1",
) -> tuple[Path, dict[int, dict]]:
    """A campaign whose mandatory primary (Gemma) block is exactly what the
    caller asks for, and whose Qwen (secondary) block always has an
    INCOMPLETE schedule (zero committed trials) -- toggled only by whether a
    valid `REPLICATION_DEFERRED_ADMISSION` admission record exists for it.
    This is the shared shape for every test that only cares about Gemma's
    own calibration/outcome arithmetic."""
    campaign_dir = tmp_path / name
    pair_count = len(gemma_pairs)

    gemma_schedule, gemma_trials = _write_block(
        campaign_dir,
        "gemma",
        pairs=gemma_pairs,
        session_fingerprint="gemma-session",
        scorer_fingerprint=scorer_fingerprint,
    )
    if not gemma_omit_audit:
        _write_audit(
            campaign_dir / "blocks" / "gemma",
            session_fingerprint="gemma-session",
            audit_indices=audit_indices,
            trial_payloads=gemma_trials,
            disagree_indices=gemma_audit_disagree_indices,
            hash_mismatch_indices=gemma_audit_hash_mismatch_indices,
        )
    if gemma_tie_attribution is not None:
        _write_tie_attribution(
            campaign_dir / "blocks" / "gemma",
            session_fingerprint="gemma-session",
            pairs=gemma_pairs,
            trial_payloads=gemma_trials,
            attribution_by_pair=gemma_tie_attribution,
        )

    qwen_schedule, _ = _write_block(
        campaign_dir,
        "qwen",
        pairs=_passing_pairs(pair_count),
        session_fingerprint="qwen-session",
        scorer_fingerprint=scorer_fingerprint,
        commit_indices=set(),
    )
    _write_schedule(campaign_dir, {"gemma": {"trials": gemma_schedule}, "qwen": {"trials": qwen_schedule}})
    _write_campaign_lock(campaign_dir, rules=rules, audit_indices=audit_indices, scorer_fingerprint=scorer_fingerprint)

    if qwen_deferral_record:
        _write_qwen_deferral_record(campaign_dir)

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
) -> tuple[dict[int, dict], dict[int, dict]]:
    """A campaign where BOTH blocks genuinely completed their schedule --
    for tests that need to observe how one block's outcome interacts with
    the other's."""
    gemma_schedule, gemma_trials = _write_block(
        campaign_dir, "gemma", pairs=gemma_pairs, session_fingerprint="gemma-session",
        scorer_fingerprint=scorer_fingerprint,
    )
    qwen_schedule, qwen_trials = _write_block(
        campaign_dir, "qwen", pairs=qwen_pairs, session_fingerprint="qwen-session",
        scorer_fingerprint=scorer_fingerprint,
    )
    _write_audit(
        campaign_dir / "blocks" / "gemma", session_fingerprint="gemma-session",
        audit_indices=audit_indices, trial_payloads=gemma_trials,
    )
    _write_audit(
        campaign_dir / "blocks" / "qwen", session_fingerprint="qwen-session",
        audit_indices=audit_indices, trial_payloads=qwen_trials,
    )
    if gemma_tie_attribution is not None:
        _write_tie_attribution(
            campaign_dir / "blocks" / "gemma", session_fingerprint="gemma-session",
            pairs=gemma_pairs, trial_payloads=gemma_trials, attribution_by_pair=gemma_tie_attribution,
        )
    if qwen_tie_attribution is not None:
        _write_tie_attribution(
            campaign_dir / "blocks" / "qwen", session_fingerprint="qwen-session",
            pairs=qwen_pairs, trial_payloads=qwen_trials, attribution_by_pair=qwen_tie_attribution,
        )
    _write_schedule(campaign_dir, {"gemma": {"trials": gemma_schedule}, "qwen": {"trials": qwen_schedule}})
    _write_campaign_lock(campaign_dir, rules=rules, audit_indices=audit_indices, scorer_fingerprint=scorer_fingerprint)
    return gemma_trials, qwen_trials


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

    with pytest.raises(Exception):
        build_campaign_report(campaign_dir)


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

    with pytest.raises(Exception):
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
    assert calibration["median_signed_delta"] == pytest.approx(2 / 12)
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

    # Bump the scorer_fingerprint label in BOTH campaign.json and the Gemma
    # block's session.json -- a metadata-only relabel, touching no raw trial
    # bytes and no session_fingerprint.
    campaign_path = campaign_dir / "campaign.json"
    campaign_lock = json.loads(campaign_path.read_text(encoding="utf-8"))
    campaign_lock["contracts"]["scorer_fingerprint"] = "score-v2"
    _write_json(campaign_path, campaign_lock)

    session_path = campaign_dir / "blocks" / "gemma" / "session.json"
    session_payload = json.loads(session_path.read_text(encoding="utf-8"))
    session_payload["scorer_fingerprint"] = "score-v2"
    _write_json(session_path, session_payload)

    report2 = build_campaign_report(campaign_dir)

    assert report2["blocks"]["gemma"]["report"]["scorer"]["fingerprint"] == "score-v2"
    assert report2["blocks"]["gemma"]["calibration"] == report1["blocks"]["gemma"]["calibration"]
    assert report2["blocks"]["gemma"]["outcome"] == report1["blocks"]["gemma"]["outcome"] == "PASS"


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
