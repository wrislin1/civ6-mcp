"""Tests for deterministic version-2 trial reports and model-input separation."""
from __future__ import annotations

import copy
import random

import pytest

from civ_mcp.arena.benchmark_report_v2 import (
    aggregate_model_reports,
    build_trial_report,
    render_report,
    validate_model_inputs,
)
from civ_mcp.arena.benchmark_state_v2 import digest_state_v2

from .benchmark_v2_fixtures import state_v2


def test_scripted_trials_cannot_enter_model_comparisons():
    with pytest.raises(ValueError, match="scripted"):
        validate_model_inputs([{"schema_version": "2.0.0", "actor_kind": "scripted",
                                "counting": False}])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def builder(*, id=1, x=10, y=10, charges=2):
    return dict(owner=0, id=id, unit_index=id, type="UNIT_BUILDER", role="civilian",
                x=x, y=y, hp=100, max_hp=100, moves=2, charges=charges)


def warrior(*, id=9, x=12, y=10):
    return dict(owner=0, id=id, unit_index=id, type="UNIT_WARRIOR", role="combat",
                x=x, y=y, hp=100, max_hp=100, moves=2, charges=0)


def tile(x=10, y=10, *, improvement="NONE", food=2):
    return dict(x=x, y=y, owner=0, terrain="TERRAIN_GRASS", feature="NONE",
                resource="NONE", improvement=improvement, pillaged=False,
                district="NONE", visible=True,
                yields=dict(food=food, production=0, gold=0, science=0, culture=0, faith=0))


def state(*, units=(), tiles=None):
    return state_v2(units=list(units), tiles=[tile()] if tiles is None else list(tiles))


def step(idx, before, after, tool_name, tool_args, result="OK", *, truncated=False,
         round_index=0):
    return {"idx": idx, "role": "tool", "ts_start": 1000.0 + idx, "ts_end": 1000.5 + idx,
            "tool_name": tool_name, "tool_args": tool_args, "tool_result_full": result,
            "result_total_chars": len(result), "result_chars_fed_to_model": len(result),
            "truncated": truncated, "prompt_tokens": None, "completion_tokens": None,
            "state_before": before, "state_after": after,
            "state_digest_before": digest_state_v2(before),
            "state_digest_after": digest_state_v2(after), "round_index": round_index}


CAPTURE = {"count": 4, "mean_s": 0.25, "p95_s": 0.5, "max_s": 0.5, "total_s": 1.0,
           "pre_drain_total_s": 0.125, "post_drain_total_s": 0.25,
           "non_drain_residual_s": 0.625, "in_episode_total_s": 0.5,
           "in_episode_share": 0.1, "lua_executions_total": 4,
           "all_single_execution": True, "unavailable": {}}

TOOLSET_IDENTITY = {"source_sha256": "a" * 64, "schemas_sha256": "b" * 64}

COVERAGE = {"include_owned_tiles": False, "area": [[10, 10]], "tracked_targets": []}


def make_trial(**overrides):
    s0 = state(units=[builder(), warrior()])
    s1 = state(units=[builder(charges=1), warrior()],
               tiles=[tile(improvement="IMPROVEMENT_FARM", food=3)])
    s2 = state(units=[builder(charges=1)],
               tiles=[tile(improvement="IMPROVEMENT_FARM", food=3)])
    steps = [step(0, s0, s1, "improve_tile",
                  {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"}),
             step(1, s1, s2, "delete_unit", {"unit_index": 9}, truncated=True,
                  round_index=1)]
    trial = {
        "actor_kind": "scripted", "arm_id": "scripted", "attempt_count": 1,
        "capture_summary": copy.deepcopy(CAPTURE), "case_id": "farm-case",
        "case_sha256": "c" * 64, "completion_tokens": None,
        "contract_fingerprint": "f" * 64, "cost_usd": None, "counting": False,
        "coverage": copy.deepcopy(COVERAGE), "dispatched_calls": 2,
        "evidence_version": "2.0.0", "final_state": s2,
        "final_summary": "done", "index": 1, "initial_state": s0,
        "invalid_tool_calls": 0, "model": None, "model_latency_s": None, "pair_id": None,
        "position_id": "pos-farm", "prompt_tokens": None, "round_trips": 3,
        "round_trips_completed": 3, "script_id": "farm", "script_sha256": "s" * 64,
        "seed": None, "session_fingerprint": "e" * 64, "steps": steps,
        "terminal": "finish_trial", "tool_call_attempts": 2, "toolset_id": "tools",
        "toolset_identity": copy.deepcopy(TOOLSET_IDENTITY),
        "validation_failures": [], "validation_status": "passed_mechanics",
        "wall_clock_s": 5.0,
    }
    trial.update(overrides)
    return trial


FARM = {"kind": "tile_matches", "tiles": [[10, 10]],
        "fields": {"improvement": "IMPROVEMENT_FARM"}}


def make_position(**overrides):
    position = {
        "schema_version": "2.0.0", "position_id": "pos-farm", "version": 3,
        "contract_identity": "contract-v2",
        "public_observation": {"path": "/abs/obs.json", "sha256": "0" * 64},
        "public_task_tiles": [[11, 10]],
        "coverage": copy.deepcopy(COVERAGE),
        "toolset": {"path": "/abs/tools.yaml", "identity": copy.deepcopy(TOOLSET_IDENTITY)},
        "rubric": {"objectives": [{"id": "farm", "rungs": [{"points": 4,
                                                           "predicate": FARM}]}],
                   "harms": [{"id": "warrior-lost", "loss_key": "warrior",
                              "objective_id": "farm", "weight": 1,
                              "weight_reason": "escort", "timing": "event",
                              "predicate": {"kind": "unit_lost", "unit": [0, 9]},
                              "compensation": [], "priority": 1}]},
    }
    position.update(overrides)
    return position


def model_trial(model, *, farm_only=False, **overrides):
    trial = make_trial(actor_kind="model", counting=True, model=model, arm_id=model,
                       prompt_tokens=100, completion_tokens=20, cost_usd=0.01,
                       model_latency_s=1.5)
    if farm_only:
        trial["steps"] = trial["steps"][:1]
        trial["final_state"] = trial["steps"][0]["state_after"]
    trial.update(overrides)
    return trial


# ---------------------------------------------------------------------------
# Trial report contents
# ---------------------------------------------------------------------------

def test_report_carries_score_ledger_audits_and_provenance():
    report = build_trial_report(make_trial(), make_position())
    score = report["score"]
    assert [o["id"] for o in score["objectives"]] == ["farm"]
    assert score["gross_credit"] == 4 and score["harm_total"] == 1
    assert score["net_credit"] == 3 and score["maximum_credit"] == 4
    assert score["primary_score"] == 0.75
    (harm,) = score["harms"]
    assert harm["status"] == "charged" and harm["fired_steps"] == [1]
    assert [s["step"] for s in report["ledger"]["steps"]] == [0, 1]
    assert report["ledger"]["net"]
    audits = report["audits"]
    assert audits["losses"] == [{
        "step": 1, "kind": "unit", "entity": [0, 9], "lifecycle": "lost",
        "declared": True, "declared_harm_ids": ["warrior-lost"],
        "tool_name": "delete_unit", "result_shape": "ok"}]
    assert audits["loss_coverage"] == {"steps_examined": 2, "observed": 1,
                                       "declared": 1, "undeclared": 0}
    assert [u["category"] for u in audits["uncredited"]] == ["declared_harm"]
    assert audits["undercredit"] == []
    prov = report["provenance"]
    assert prov["position_id"] == "pos-farm" and prov["position_version"] == 3
    assert prov["contract_identity"] == "contract-v2"
    assert prov["public_observation_sha256"] == "0" * 64
    assert prov["public_task_tiles"] == [[11, 10]]
    assert prov["script_id"] == "farm" and prov["case_sha256"] == "c" * 64
    assert prov["actor_kind"] == "scripted" and prov["counting"] is False
    assert "/abs/" not in render_report(report)  # no machine-local paths
    assert report["mechanics"] == {"validation_status": "passed_mechanics",
                                   "validation_failures": [], "invalid_tool_calls": 0}
    assert report["model_usage"] == {"model": None, "prompt_tokens": None,
                                     "completion_tokens": None, "cost_usd": None,
                                     "model_latency_s": None}


def test_terminal_and_truncation_flags_come_from_steps():
    report = build_trial_report(make_trial(), make_position())
    assert report["terminal"] == {"terminal": "finish_trial", "truncated_steps": [1],
                                  "truncation_unavailable_steps": []}


def test_counters_are_never_inferred_from_step_rows():
    trial = make_trial(round_trips=1, round_trips_completed=1, tool_call_attempts=2)
    assert len(trial["steps"]) == 2
    report = build_trial_report(trial, make_position())
    assert report["counters"] == {"round_trips": 1, "round_trips_completed": 1,
                                  "tool_call_attempts": 2, "dispatched_calls": 2,
                                  "attempts_per_round": 2.0}
    zero = build_trial_report(make_trial(round_trips=0), make_position())
    assert zero["counters"]["attempts_per_round"] is None


def test_identity_drift_is_detected_from_states_not_validation_status():
    trial = make_trial()
    assert build_trial_report(trial, make_position())["identity"] == {
        "identity_ok": True, "drift": []}
    drifted = copy.deepcopy(trial)
    drifted["steps"][1]["state_after"]["turn"] = 101
    drifted["final_state"] = drifted["steps"][1]["state_after"]
    assert drifted["validation_status"] == "passed_mechanics"
    identity = build_trial_report(drifted, make_position())["identity"]
    assert identity["identity_ok"] is False
    assert {"where": "final", "field": "turn", "expected": 100, "actual": 101} \
        in identity["drift"]
    assert {"where": "step 1 after", "field": "turn", "expected": 100, "actual": 101} \
        in identity["drift"]


def test_capture_summary_is_surfaced_with_honest_unavailable_fields():
    report = build_trial_report(make_trial(), make_position())
    capture = report["capture"]
    assert capture["available"] is True
    assert capture["total_s"] == 1.0 and capture["in_episode_total_s"] == 0.5
    assert capture["in_episode_share"] == 0.1
    assert capture["pre_drain_total_s"] == 0.125 and capture["post_drain_total_s"] == 0.25
    assert capture["non_drain_residual_s"] == 0.625
    assert capture["summary"] == CAPTURE

    partial = dict(CAPTURE, pre_drain_total_s=None, non_drain_residual_s=None,
                   unavailable={"pre_drain_s": [{"index": 0, "phase": "initial"}]})
    partial_report = build_trial_report(make_trial(capture_summary=partial), make_position())
    capture = partial_report["capture"]
    assert capture["pre_drain_total_s"] is None and capture["non_drain_residual_s"] is None
    assert capture["unavailable"] == {"pre_drain_s": [{"index": 0, "phase": "initial"}]}
    assert "- pre_drain_total_s: unavailable" in render_report(partial_report)

    missing = build_trial_report(make_trial(capture_summary=None), make_position())["capture"]
    assert missing["available"] is False and missing["total_s"] is None


@pytest.mark.parametrize("field,trial_overrides,position_overrides", [
    ("position_id", {"position_id": "pos-other"}, {}),
    ("coverage", {"coverage": {"include_owned_tiles": True, "area": [[10, 10]],
                               "tracked_targets": []}}, {}),
    ("toolset_identity", {}, {"toolset": {"path": "/abs/tools.yaml",
                                          "identity": {"source_sha256": "x" * 64,
                                                       "schemas_sha256": "b" * 64}}}),
])
def test_trial_scored_against_a_mismatched_position_is_refused(field, trial_overrides,
                                                               position_overrides):
    with pytest.raises(ValueError, match=field):
        build_trial_report(make_trial(**trial_overrides), make_position(**position_overrides))


def test_matching_trial_and_position_still_build():
    report = build_trial_report(make_trial(), make_position())
    assert report["provenance"]["position_id"] == "pos-farm"


def test_evidence_version_one_is_rejected():
    with pytest.raises(ValueError, match="evidence_version"):
        build_trial_report(make_trial(evidence_version="1.0.0"), make_position())


# ---------------------------------------------------------------------------
# Determinism and independence
# ---------------------------------------------------------------------------

def test_two_builds_are_equal_and_two_renders_are_byte_identical():
    a = build_trial_report(make_trial(), make_position())
    b = build_trial_report(make_trial(), make_position())
    assert a == b
    assert render_report(a).encode() == render_report(b).encode()
    headings = [line for line in render_report(a).splitlines() if line.startswith("## ")]
    assert headings == ["## Provenance", "## Identity", "## Mechanics", "## Terminal",
                        "## Counters", "## Score", "## Ledger", "## Audits",
                        "## Capture", "## Model usage", "## Digests"]


def test_altered_expected_case_data_cannot_change_a_derived_report():
    trial, position = make_trial(), make_position()
    expected = {"score": {"primary_score": 0.75}, "endpoints": [], "ledger": []}
    before = build_trial_report(trial, position)
    expected["score"]["primary_score"] = -1.0
    expected["endpoints"].append({"bogus": True})
    after = build_trial_report(trial, position)
    assert before == after
    assert render_report(before) == render_report(after)


def test_timing_never_changes_canonical_state_digests():
    trial = make_trial()
    digests = build_trial_report(trial, make_position())["digests"]
    assert digests["initial"] == digest_state_v2(trial["initial_state"])
    assert digests["final"] == digest_state_v2(trial["final_state"])
    assert digests["steps"] == [
        {"step": s["idx"], "before": s["state_digest_before"],
         "after": s["state_digest_after"]} for s in trial["steps"]]

    retimed = copy.deepcopy(trial)
    retimed["wall_clock_s"] = 99.0
    retimed["capture_summary"]["total_s"] = 42.0
    for s in retimed["steps"]:
        s["ts_start"] += 500.0
        s["ts_end"] += 900.0
    assert build_trial_report(retimed, make_position())["digests"] == digests


# ---------------------------------------------------------------------------
# Model-comparison inputs and aggregation
# ---------------------------------------------------------------------------

def test_non_counting_or_old_trials_are_rejected():
    with pytest.raises(ValueError, match="scripted"):
        validate_model_inputs([model_trial("m", counting=False)])
    with pytest.raises(ValueError, match="evidence_version"):
        validate_model_inputs([model_trial("m", evidence_version="1.0.0")])
    assert validate_model_inputs([model_trial("m")]) is None


@pytest.mark.parametrize("field,value", [
    ("contract_fingerprint", "9" * 64),
    ("toolset_identity", {"source_sha256": "x", "schemas_sha256": "y"}),
    ("coverage", {"include_owned_tiles": True, "area": [], "tracked_targets": []}),
])
def test_mixed_identities_within_a_position_are_rejected(field, value):
    with pytest.raises(ValueError, match=field):
        validate_model_inputs([model_trial("m"), model_trial("m", **{field: value})])
    # Different positions may carry their own identities and scopes.
    validate_model_inputs([model_trial("m"),
                           model_trial("m", position_id="pos-other", **{field: value})])


def test_aggregation_reports_per_model_statistics():
    trials = [model_trial("alpha", index=i, tool_call_attempts=a, round_trips=r,
                          dispatched_calls=d)
              for i, (a, r, d) in enumerate([(2, 2, 2), (4, 2, 3), (6, 3, 5)])]
    trials.append(model_trial("beta", farm_only=True, index=9, tool_call_attempts=1,
                              round_trips=0, dispatched_calls=1))
    positions = {"pos-farm": make_position()}
    out = aggregate_model_reports(trials, positions)
    assert sorted(out["models"]) == ["alpha", "beta"]
    alpha = out["models"]["alpha"]
    assert alpha["n"] == 3
    assert alpha["primary_score"] == {"n": 3, "min": 0.75, "median": 0.75, "p95": 0.75,
                                      "max": 0.75}
    assert alpha["tool_call_attempts"] == {"n": 3, "min": 2, "median": 4, "p95": 6, "max": 6}
    assert alpha["round_trips"] == {"n": 3, "min": 2, "median": 2, "p95": 3, "max": 3}
    assert alpha["attempts_per_round"] == {"n": 3, "min": 1.0, "median": 2.0, "p95": 2.0,
                                           "max": 2.0}
    assert alpha["dispatched_calls"] == {"n": 3, "min": 2, "median": 3, "p95": 5, "max": 5}
    assert [t["model_usage"] for t in alpha["trials"]] == [
        {"model": "alpha", "prompt_tokens": 100, "completion_tokens": 20,
         "cost_usd": 0.01, "model_latency_s": 1.5}] * 3
    beta = out["models"]["beta"]
    assert beta["primary_score"]["max"] == 1.0
    assert beta["attempts_per_round"] == {"n": 0, "min": None, "median": None,
                                          "p95": None, "max": None}

    shuffled = trials[:]
    random.Random(4).shuffle(shuffled)
    assert aggregate_model_reports(shuffled, positions) == out


def test_aggregation_validates_before_scoring():
    with pytest.raises(ValueError, match="scripted"):
        aggregate_model_reports([make_trial()], {"pos-farm": make_position()})
