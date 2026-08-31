"""Tests for civ_mcp.arena.benchmark_report — TDD: RED first, then GREEN.

`benchmark_report` derives scores and deterministic reports purely from
committed run-directory evidence (`session.json`, `schedule.json`, and
`trials/*.json`). It must reuse `action_metrics.evaluate_predicate` /
`classify_action_quality` verbatim rather than re-implementing predicate
semantics, and it must never read `attempts/` at all.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from civ_mcp.arena import action_metrics
from civ_mcp.arena.action_metrics import PredicateError
from civ_mcp.arena.benchmark_report import (
    MalformedRubricError,
    build_report,
    render_markdown,
    score_rubric,
    score_trial,
    write_reports,
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _mine_rubric(task_id: str = "primary") -> dict:
    return {
        "task_id": task_id,
        "levels": [
            {"score": 0, "predicate": {"kind": "always"}},
            {
                "score": 1,
                "predicate": {
                    "kind": "final_state_equals",
                    "path": ["tiles", 0, "improvement"],
                    "value": "IMPROVEMENT_MINE",
                },
            },
        ],
    }


def _build_basic_run(run_dir: Path) -> None:
    (run_dir / "trials").mkdir(parents=True)
    (run_dir / "attempts").mkdir()
    _write_json(
        run_dir / "session.json",
        {
            "session_fingerprint": "abc123",
            "scorer_fingerprint": "score-v1",
            "positions": {
                "easy": {"rubric": [_mine_rubric()]},
                "hard": {"rubric": [_mine_rubric()]},
            },
        },
    )
    _write_json(
        run_dir / "schedule.json",
        {
            "trials": [
                {"index": 1, "position_id": "easy"},
                {"index": 2, "position_id": "easy"},
                {"index": 3, "position_id": "hard"},
            ],
        },
    )
    for index, position_id, satisfied in (
        (1, "easy", True),
        (2, "easy", True),
        (3, "hard", False),
    ):
        _write_json(
            run_dir / "trials" / f"trial-{index:03d}.json",
            {
                "index": index,
                "position_id": position_id,
                "attempt_count": 1,
                "terminal": "finish_trial",
                "steps": [],
                "initial_state": {"tiles": [{"improvement": None}]},
                "final_state": {
                    "tiles": [{"improvement": "IMPROVEMENT_MINE" if satisfied else None}]
                },
            },
        )


# ---------------------------------------------------------------------------
# Step 1 (brief, verbatim): report derivation ignores attempts/ entirely and
# weights positions equally regardless of trial count per position.
# ---------------------------------------------------------------------------


def test_report_ignores_attempts_and_weights_positions_equally(tmp_path):
    def write_json(path, payload):
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    run_dir = tmp_path / "run"
    (run_dir / "trials").mkdir(parents=True)
    (run_dir / "attempts").mkdir()
    write_json(
        run_dir / "session.json",
        {
            "session_fingerprint": "abc123",
            "scorer_fingerprint": "score-v1",
            "positions": {
                "easy": {
                    "rubric": [
                        {
                            "task_id": "primary",
                            "levels": [
                                {"score": 0, "predicate": {"kind": "always"}},
                                {
                                    "score": 1,
                                    "predicate": {
                                        "kind": "final_state_equals",
                                        "path": ["tiles", 0, "improvement"],
                                        "value": "IMPROVEMENT_MINE",
                                    },
                                },
                            ],
                        }
                    ]
                },
                "hard": {
                    "rubric": [
                        {
                            "task_id": "primary",
                            "levels": [
                                {"score": 0, "predicate": {"kind": "always"}},
                                {
                                    "score": 1,
                                    "predicate": {
                                        "kind": "final_state_equals",
                                        "path": ["tiles", 0, "improvement"],
                                        "value": "IMPROVEMENT_MINE",
                                    },
                                },
                            ],
                        }
                    ]
                },
            },
        },
    )
    write_json(
        run_dir / "schedule.json",
        {
            "trials": [
                {"index": 1, "position_id": "easy"},
                {"index": 2, "position_id": "easy"},
                {"index": 3, "position_id": "hard"},
            ],
        },
    )
    for index, position_id, satisfied in (
        (1, "easy", True),
        (2, "easy", True),
        (3, "hard", False),
    ):
        write_json(
            run_dir / "trials" / f"trial-{index:03d}.json",
            {
                "index": index,
                "position_id": position_id,
                "attempt_count": 1,
                "terminal": "finish_trial",
                "steps": [],
                "initial_state": {"tiles": [{"improvement": None}]},
                "final_state": {
                    "tiles": [
                        {"improvement": "IMPROVEMENT_MINE" if satisfied else None}
                    ]
                },
            },
        )
    write_json(
        run_dir / "attempts" / "attempt-002-001.json",
        {"trial_index": 2, "failure_class": "gateway_unavailable"},
    )

    report = build_report(run_dir)
    assert report["aggregate"]["equal_weight_mean"] == 0.5

    (run_dir / "attempts" / "noise.json").write_text('{"rubric_score": 1.0}')
    assert build_report(run_dir) == report


# ---------------------------------------------------------------------------
# score_rubric: shared-evaluator reuse and fail-closed aborts
# ---------------------------------------------------------------------------


def test_score_rubric_delegates_to_shared_evaluator(monkeypatch):
    calls = []
    real = action_metrics.evaluate_predicate

    def spy(predicate, **kwargs):
        calls.append(predicate)
        return real(predicate, **kwargs)

    monkeypatch.setattr("civ_mcp.arena.benchmark_report.evaluate_predicate", spy)

    rubric = [_mine_rubric()]
    result = score_rubric(
        rubric,
        initial_state={"tiles": [{"improvement": None}]},
        final_state={"tiles": [{"improvement": "IMPROVEMENT_MINE"}]},
        steps=(),
    )
    assert calls, "score_rubric must call the shared evaluate_predicate, not reimplement it"
    assert result["tasks"]["primary"]["score"] == 1
    assert result["raw_total"] == 1
    assert result["max_total"] == 1
    assert result["normalized"] == 1.0


def test_score_rubric_picks_highest_satisfied_level_regardless_of_order():
    # Deliberately NOT ordered by score, and the first level whose predicate
    # is satisfied ("always", score 0) is NOT the highest-scoring satisfied
    # level (score 2, further down the list). A naive "return the first
    # satisfied level" implementation would report 0 here; only "return the
    # MAX satisfied level" reports 2, so this fixture actually discriminates
    # between the two behaviors.
    rubric = [
        {
            "task_id": "primary",
            "levels": [
                {"score": 0, "predicate": {"kind": "always"}},
                {
                    "score": 2,
                    "predicate": {
                        "kind": "final_state_equals",
                        "path": ["x"],
                        "value": 5,
                    },
                },
                {
                    "score": 1,
                    "predicate": {
                        "kind": "final_state_equals",
                        "path": ["x"],
                        "value": 1,
                    },
                },
            ],
        }
    ]
    result = score_rubric(rubric, initial_state={}, final_state={"x": 5}, steps=())
    assert result["tasks"]["primary"]["score"] == 2
    assert result["max_total"] == 2


def test_score_rubric_aborts_on_unknown_predicate_kind():
    rubric = [
        {
            "task_id": "primary",
            "levels": [{"score": 1, "predicate": {"kind": "not_a_real_kind"}}],
        }
    ]
    with pytest.raises(PredicateError):
        score_rubric(rubric, initial_state={}, final_state={}, steps=())


def test_score_rubric_aborts_on_missing_path_rather_than_scoring_zero():
    rubric = [
        {
            "task_id": "primary",
            "levels": [
                {
                    "score": 1,
                    "predicate": {
                        "kind": "final_state_equals",
                        "path": ["nope"],
                        "value": 1,
                    },
                }
            ],
        }
    ]
    with pytest.raises(PredicateError):
        score_rubric(rubric, initial_state={}, final_state={}, steps=())


@pytest.mark.parametrize(
    "bad_rubric",
    [
        [{"task_id": "primary"}],  # missing "levels"
        [{"levels": []}],  # missing "task_id"
        [{"task_id": "primary", "levels": "not-a-list"}],
        [{"task_id": "primary", "levels": [{"predicate": {"kind": "always"}}]}],  # no score
        [{"task_id": "primary", "levels": [{"score": 1}]}],  # no predicate
        "not-a-list",
    ],
)
def test_score_rubric_aborts_on_malformed_rubric_shape(bad_rubric):
    with pytest.raises(MalformedRubricError):
        score_rubric(bad_rubric, initial_state={}, final_state={}, steps=())


def test_score_rubric_aborts_on_duplicate_task_id():
    rubric = [_mine_rubric("primary"), _mine_rubric("primary")]
    with pytest.raises(MalformedRubricError):
        score_rubric(rubric, initial_state={}, final_state={}, steps=())


# ---------------------------------------------------------------------------
# score_trial: raw trial fields are copied verbatim, never re-derived
# ---------------------------------------------------------------------------


def test_score_trial_copies_attempt_count_and_terminal_from_raw_trial():
    trial = {
        "index": 7,
        "position_id": "easy",
        "attempt_count": 3,
        "terminal": "runaway_timeout",
        "seed": 42,
        "model": "qwen3.6-27b",
        "arm_id": "standard",
        "pair_id": "easy:qwen3.6-27b:seed42:0",
        "wall_clock_s": 12.5,
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "steps": [],
        "invalid_tool_calls": [],
        "initial_state": {"tiles": [{"improvement": None}]},
        "final_state": {"tiles": [{"improvement": "IMPROVEMENT_MINE"}]},
    }
    rubric = [_mine_rubric()]
    scored = score_trial(trial, rubric)
    assert scored["attempt_count"] == 3
    assert scored["terminal"] == "runaway_timeout"
    assert scored["seed"] == 42
    assert scored["model"] == "qwen3.6-27b"
    assert scored["arm_id"] == "standard"
    assert scored["pair_id"] == "easy:qwen3.6-27b:seed42:0"
    assert scored["wall_clock_s"] == 12.5
    assert scored["prompt_tokens"] == 100
    assert scored["completion_tokens"] == 20
    assert scored["rubric"]["raw_total"] == 1
    assert "useful_action_coverage" in scored["action_quality"]


def test_score_trial_never_reads_scorer_fields_from_raw_trial():
    """Raw trial artifacts never carry scorer-produced points or pass/fail
    labels -- score_trial must derive everything from steps/initial_state/
    final_state, ignoring any (illegitimate) 'rubric_score' field a raw
    trial might carry."""
    trial = {
        "index": 1,
        "position_id": "easy",
        "attempt_count": 1,
        "terminal": "finish_trial",
        "rubric_score": 999.0,  # must be ignored entirely
        "passed": True,  # must be ignored entirely
        "steps": [],
        "invalid_tool_calls": [],
        "initial_state": {"tiles": [{"improvement": None}]},
        "final_state": {"tiles": [{"improvement": None}]},
    }
    rubric = [_mine_rubric()]
    scored = score_trial(trial, rubric)
    assert scored["rubric"]["raw_total"] == 0
    assert scored["rubric"]["normalized"] == 0.0


# ---------------------------------------------------------------------------
# build_report: per-position rendering, retries, seeds, endpoint topology
# ---------------------------------------------------------------------------


def test_build_report_includes_per_position_output(tmp_path):
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)
    report = build_report(run_dir)

    assert set(report["positions"]) == {"easy", "hard"}
    assert report["positions"]["easy"]["trial_count"] == 2
    assert report["positions"]["hard"]["trial_count"] == 1
    assert report["positions"]["easy"]["rubric"]["normalized_median"] == 1.0
    assert report["positions"]["hard"]["rubric"]["normalized_median"] == 0.0


def test_build_report_copies_retry_counts_from_raw_trials(tmp_path):
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)
    # Bump trial 2's attempt_count to simulate one retried infrastructure
    # attempt before the trial finally committed.
    trial_path = run_dir / "trials" / "trial-002.json"
    payload = json.loads(trial_path.read_text(encoding="utf-8"))
    payload["attempt_count"] = 2
    _write_json(trial_path, payload)

    report = build_report(run_dir)
    attempt_counts = {
        t["index"]: t["attempt_count"] for t in report["positions"]["easy"]["trials"]
    }
    assert attempt_counts == {1: 1, 2: 2}
    assert report["positions"]["easy"]["attempts"]["max_attempt_count"] == 2
    assert report["positions"]["easy"]["attempts"]["trials_with_retries"] == 1


def test_build_report_surfaces_seeds_and_endpoint_topology(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "trials").mkdir(parents=True)
    (run_dir / "attempts").mkdir()
    _write_json(
        run_dir / "session.json",
        {
            "session_fingerprint": "abc123",
            "scorer_fingerprint": "score-v1",
            "positions": {"easy": {"rubric": [_mine_rubric()]}},
        },
    )
    _write_json(
        run_dir / "schedule.json",
        {
            "trials": [
                {"index": 1, "position_id": "easy"},
                {"index": 2, "position_id": "easy"},
            ],
        },
    )
    _write_json(
        run_dir / "trials" / "trial-001.json",
        {
            "index": 1,
            "position_id": "easy",
            "attempt_count": 1,
            "terminal": "finish_trial",
            "seed": 11,
            "model": "qwen3.6-27b",
            "arm_id": "standard",
            "steps": [],
            "initial_state": {"tiles": [{"improvement": None}]},
            "final_state": {"tiles": [{"improvement": None}]},
        },
    )
    _write_json(
        run_dir / "trials" / "trial-002.json",
        {
            "index": 2,
            "position_id": "easy",
            "attempt_count": 1,
            "terminal": "finish_trial",
            "seed": 12,
            "model": "gemma4-27b",
            "arm_id": "minimal",
            "steps": [],
            "initial_state": {"tiles": [{"improvement": None}]},
            "final_state": {"tiles": [{"improvement": None}]},
        },
    )

    report = build_report(run_dir)
    easy = report["positions"]["easy"]
    assert easy["seeds"]["distinct"] == [11, 12]
    assert easy["endpoint_topology"]["models"] == ["gemma4-27b", "qwen3.6-27b"]
    assert easy["endpoint_topology"]["arms"] == ["minimal", "standard"]


def test_build_report_includes_terminal_conditions_latency_tokens_cost(tmp_path):
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)
    # Give trial 3 a distinct terminal + measured latency/tokens.
    trial_path = run_dir / "trials" / "trial-003.json"
    payload = json.loads(trial_path.read_text(encoding="utf-8"))
    payload["terminal"] = "runaway_timeout"
    payload["wall_clock_s"] = 300.0
    payload["prompt_tokens"] = 500
    payload["completion_tokens"] = 50
    _write_json(trial_path, payload)

    report = build_report(run_dir)
    hard = report["positions"]["hard"]
    assert hard["terminal_conditions"] == {"runaway_timeout": 1}
    assert hard["latency"]["mean_s"] == 300.0
    assert hard["tokens"]["prompt_total"] == 500
    assert hard["tokens"]["completion_total"] == 50
    assert "cost" in hard and "usd_total" in hard["cost"]


# ---------------------------------------------------------------------------
# Completeness: a scheduled position with zero committed trials must never
# silently disappear from the report -- its absence would be
# indistinguishable from "scored 0", hiding a position-level regression.
# ---------------------------------------------------------------------------


def _run_with_a_never_run_position(tmp_path: Path) -> Path:
    """`easy` and `hard` both have committed trials; `never_ran` is declared
    in session.json and scheduled in schedule.json but has zero committed
    trial files (as if the run stopped before it started)."""
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)

    session_path = run_dir / "session.json"
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["positions"]["never_ran"] = {"rubric": [_mine_rubric()]}
    _write_json(session_path, payload)

    schedule_path = run_dir / "schedule.json"
    schedule_payload = json.loads(schedule_path.read_text(encoding="utf-8"))
    schedule_payload["trials"].append({"index": 4, "position_id": "never_ran"})
    _write_json(schedule_path, schedule_payload)
    # Deliberately no trials/trial-004.json -- this position never committed
    # a single trial.
    return run_dir


def test_build_report_lists_a_zero_committed_position_in_completeness(tmp_path):
    run_dir = _run_with_a_never_run_position(tmp_path)
    report = build_report(run_dir)

    completeness = report["completeness"]
    assert completeness["by_position"]["never_ran"] == {"expected": 1, "committed": 0}
    assert completeness["by_position"]["easy"] == {"expected": 2, "committed": 2}
    assert completeness["by_position"]["hard"] == {"expected": 1, "committed": 1}
    assert completeness["positions_missing"] == ["never_ran"]
    # A position with zero committed trials must not appear as a position
    # section at all (that would fabricate stats for data that doesn't
    # exist) -- but it must never be silently absent from the report either.
    assert "never_ran" not in report["positions"]
    # The aggregate is still computed only from positions with real data --
    # the completeness block is what makes that fact visible, not a change
    # to what the aggregate itself covers.
    assert report["aggregate"]["equal_weight_mean"] == 0.5


def test_render_markdown_visibly_flags_a_missing_position(tmp_path):
    run_dir = _run_with_a_never_run_position(tmp_path)
    report = build_report(run_dir)
    markdown = render_markdown(report)

    assert "never_ran" in markdown
    assert "INCOMPLETE RUN" in markdown.upper()
    # The completeness warning must appear before the aggregate section, at
    # the same "surface it prominently" standard as the ungated-smoke banner.
    assert markdown.index("INCOMPLETE RUN") < markdown.index("## Aggregate")


def test_render_markdown_completeness_table_present_even_when_nothing_missing(tmp_path):
    """A fully-committed run still renders the completeness table (all
    committed == expected, no warning banner) -- completeness is always
    surfaced, not only when something is wrong."""
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)
    report = build_report(run_dir)
    markdown = render_markdown(report)

    assert "## Run completeness" in markdown
    assert "INCOMPLETE RUN" not in markdown.upper()
    assert report["completeness"]["positions_missing"] == []


def test_regeneration_with_a_missing_position_is_still_byte_identical(tmp_path):
    run_dir = _run_with_a_never_run_position(tmp_path)

    write_reports(run_dir)
    first_json = (run_dir / "report.json").read_bytes()
    first_md = (run_dir / "report.md").read_bytes()

    write_reports(run_dir)
    second_json = (run_dir / "report.json").read_bytes()
    second_md = (run_dir / "report.md").read_bytes()

    assert hashlib.sha256(second_json).hexdigest() == hashlib.sha256(first_json).hexdigest()
    assert hashlib.sha256(second_md).hexdigest() == hashlib.sha256(first_md).hexdigest()


# ---------------------------------------------------------------------------
# Calibration: 10/12 wins, median delta >= 4, ties as non-wins
# ---------------------------------------------------------------------------


def _calibration_run(tmp_path: Path, standard_scores, minimal_scores) -> Path:
    """Twelve paired trials (schedule pairs) at one position, one rubric
    task worth 0-12 raw points via twelve `always`-satisfied sub-levels so
    each pair's two trials can be assigned arbitrary raw scores 0..12
    independent of any game-state shape."""
    run_dir = tmp_path / "run"
    (run_dir / "trials").mkdir(parents=True)
    (run_dir / "attempts").mkdir()

    levels = [{"score": 0, "predicate": {"kind": "always"}}]
    for n in range(1, 13):
        levels.append(
            {
                "score": n,
                "predicate": {
                    "kind": "final_state_equals",
                    "path": ["achieved"],
                    "value": n,
                },
            }
        )
    rubric = [{"task_id": "primary", "levels": levels}]
    _write_json(
        run_dir / "session.json",
        {
            "session_fingerprint": "cal-1",
            "scorer_fingerprint": "score-v1",
            "positions": {"cal": {"rubric": rubric}},
        },
    )

    trials_meta = []
    index = 1
    schedule_trials = []
    for pair_index, (s_score, m_score) in enumerate(zip(standard_scores, minimal_scores)):
        pair_id = f"cal:pair{pair_index}"
        for arm_id, score in (("standard", s_score), ("minimal", m_score)):
            schedule_trials.append(
                {
                    "index": index,
                    "position_id": "cal",
                    "pair_id": pair_id,
                    "arm_id": arm_id,
                }
            )
            trials_meta.append((index, score))
            index += 1

    _write_json(run_dir / "schedule.json", {"trials": schedule_trials})

    for (idx, score), sched in zip(trials_meta, schedule_trials):
        _write_json(
            run_dir / "trials" / f"trial-{idx:03d}.json",
            {
                "index": idx,
                "position_id": "cal",
                "pair_id": sched["pair_id"],
                "arm_id": sched["arm_id"],
                "attempt_count": 1,
                "terminal": "finish_trial",
                "steps": [],
                "initial_state": {"achieved": 0},
                "final_state": {"achieved": score},
            },
        )
    return run_dir


def test_calibration_reports_ten_of_twelve_wins_and_median_delta_four(tmp_path):
    standard_scores = [10, 9, 8, 7, 12, 11, 6, 10, 9, 8, 7, 3]
    minimal_scores = [2, 3, 1, 0, 4, 5, 2, 1, 3, 2, 8, 9]  # last two: minimal wins
    run_dir = _calibration_run(tmp_path, standard_scores, minimal_scores)

    report = build_report(run_dir)
    calibration = report["calibration"]
    assert calibration["win_counts"]["standard"] == 10
    assert calibration["win_counts"].get("minimal", 0) == 2
    assert calibration["tie_count"] == 0
    assert calibration["median_abs_delta"] >= 4


def test_calibration_ties_do_not_count_as_wins_for_either_arm(tmp_path):
    standard_scores = [5, 5, 5]
    minimal_scores = [5, 5, 5]
    run_dir = _calibration_run(tmp_path, standard_scores, minimal_scores)

    report = build_report(run_dir)
    calibration = report["calibration"]
    assert calibration["win_counts"].get("standard", 0) == 0
    assert calibration["win_counts"].get("minimal", 0) == 0
    assert calibration["tie_count"] == 3
    assert calibration["median_abs_delta"] == 0
    assert calibration["median_signed_delta"] == 0


def test_calibration_median_signed_delta_is_oriented_second_arm_minus_first_arm(tmp_path):
    # _calibration_run's schedule.json always lists "standard" first and
    # "minimal" second within each pair (and across the whole schedule) --
    # so the declared arm order here is (standard=first/baseline,
    # minimal=second/treatment) and the signed delta is minimal - standard.
    standard_scores = [0, 0, 0, 0]
    minimal_scores = [10, 8, 6, 4]
    run_dir = _calibration_run(tmp_path, standard_scores, minimal_scores)

    report = build_report(run_dir)
    calibration = report["calibration"]
    assert calibration["median_signed_delta"] == 7
    assert calibration["median_abs_delta"] == 7


def test_calibration_median_signed_delta_moves_down_when_baseline_wins_a_pair_but_abs_does_not(
    tmp_path,
):
    # Counterfactual: flip the first pair so the *first-declared* (baseline)
    # arm wins it, keeping the absolute magnitude of every delta identical
    # to the all-second-arm-wins case above. median_abs_delta must be
    # unchanged (it does not care which arm won); median_signed_delta must
    # move down, since a baseline win now contributes a negative delta.
    standard_scores = [10, 0, 0, 0]
    minimal_scores = [0, 8, 6, 4]
    run_dir = _calibration_run(tmp_path, standard_scores, minimal_scores)

    report = build_report(run_dir)
    calibration = report["calibration"]
    assert calibration["median_abs_delta"] == 7
    assert calibration["median_signed_delta"] == 5


# ---------------------------------------------------------------------------
# scorer fingerprint provenance
# ---------------------------------------------------------------------------


def test_build_report_surfaces_scorer_fingerprint(tmp_path):
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)
    report = build_report(run_dir)
    assert report["session"]["scorer_fingerprint"] == "score-v1"
    assert report["session"]["session_fingerprint"] == "abc123"
    assert report["scorer"]["evaluator"] == "civ_mcp.arena.action_metrics.evaluate_predicate"


def test_build_report_requires_scorer_fingerprint(tmp_path):
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)
    session_path = run_dir / "session.json"
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    del payload["scorer_fingerprint"]
    _write_json(session_path, payload)

    with pytest.raises(Exception):
        build_report(run_dir)


def test_build_report_surfaces_ungated_smoke_flag(tmp_path):
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)
    session_path = run_dir / "session.json"
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["ungated_smoke"] = True
    _write_json(session_path, payload)

    report = build_report(run_dir)
    assert report["session"]["ungated_smoke"] is True
    markdown = render_markdown(report)
    assert "UNGATED SMOKE" in markdown.upper()


# ---------------------------------------------------------------------------
# Markdown rendering: every position before the aggregate section
# ---------------------------------------------------------------------------


def test_render_markdown_renders_every_position_before_aggregate(tmp_path):
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)
    report = build_report(run_dir)
    markdown = render_markdown(report)

    easy_pos = markdown.index("## Position: easy")
    hard_pos = markdown.index("## Position: hard")
    aggregate_pos = markdown.index("## Aggregate")
    assert easy_pos < aggregate_pos
    assert hard_pos < aggregate_pos


def test_render_markdown_positions_are_sorted_for_determinism(tmp_path):
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)
    report = build_report(run_dir)
    markdown = render_markdown(report)
    assert markdown.index("## Position: easy") < markdown.index("## Position: hard")


# ---------------------------------------------------------------------------
# write_reports + byte-identical regeneration
# ---------------------------------------------------------------------------


def test_write_reports_produces_canonical_json_and_markdown(tmp_path):
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)
    write_reports(run_dir)

    report_json_path = run_dir / "report.json"
    report_md_path = run_dir / "report.md"
    assert report_json_path.exists()
    assert report_md_path.exists()

    raw = report_json_path.read_text(encoding="utf-8")
    # Canonical JSON: sorted keys, no incidental whitespace.
    assert json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":")) == raw


def test_regenerating_the_same_run_is_byte_identical(tmp_path):
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)

    write_reports(run_dir)
    first_json = (run_dir / "report.json").read_bytes()
    first_md = (run_dir / "report.md").read_bytes()
    first_json_hash = hashlib.sha256(first_json).hexdigest()
    first_md_hash = hashlib.sha256(first_md).hexdigest()

    write_reports(run_dir)
    second_json = (run_dir / "report.json").read_bytes()
    second_md = (run_dir / "report.md").read_bytes()

    assert hashlib.sha256(second_json).hexdigest() == first_json_hash
    assert hashlib.sha256(second_md).hexdigest() == first_md_hash


# ---------------------------------------------------------------------------
# attempts/ is never required to exist -- build_report does not scan it
# ---------------------------------------------------------------------------


def test_build_report_works_when_attempts_directory_is_absent(tmp_path):
    """build_report never scans attempts/, so a run directory that never had
    one at all (as opposed to the Step 1 fixture, which has an empty one)
    must still produce the same report."""
    run_dir = tmp_path / "run"
    _build_basic_run(run_dir)
    import shutil

    shutil.rmtree(run_dir / "attempts")
    assert not (run_dir / "attempts").exists()

    report = build_report(run_dir)
    assert report["aggregate"]["equal_weight_mean"] == 0.5
