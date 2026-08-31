"""Tests for civ_mcp.arena.action_metrics — TDD: RED first, then GREEN."""
from __future__ import annotations

import pytest

from civ_mcp.arena.action_metrics import (
    PredicateError,
    classify_action_quality,
    classify_result,
    evaluate_predicate,
)


# ---------------------------------------------------------------------------
# classify_action_quality — brief's Step 1 fixture, verbatim
# ---------------------------------------------------------------------------

def test_classifier_separates_domain_rejection_from_invalid_call_and_loop():
    state_a = {
        "units": [{"id": 8, "x": 9, "y": 10}],
        "tiles": [{"x": 9, "y": 10, "improvement": None}],
    }
    state_b = {
        "units": [{"id": 8, "x": 9, "y": 10}],
        "tiles": [{"x": 9, "y": 10, "improvement": "IMPROVEMENT_MINE"}],
    }
    steps = [
        {"tool_name": "move_unit", "tool_args": {"unit_index": 7, "x": 4, "y": 5},
         "tool_result_full": "Error: BLOCKED", "state_before": state_a,
         "state_after": state_a, "state_digest_before": "a", "state_digest_after": "a"},
        {"tool_name": "move_unit", "tool_args": {"unit_index": 7, "x": 4, "y": 5},
         "tool_result_full": "Error: BLOCKED", "state_before": state_a,
         "state_after": state_a, "state_digest_before": "a", "state_digest_after": "a"},
        {"tool_name": "improve_tile", "tool_args": {"unit_index": 8,
                                                       "improvement_name": "IMPROVEMENT_MINE"},
         "tool_result_full": "IMPROVING|IMPROVEMENT_MINE|9,10",
         "state_before": state_a, "state_after": state_b,
         "state_digest_before": "a", "state_digest_after": "b"},
    ]
    got = classify_action_quality(
        steps=steps,
        invalid_tool_calls=[{"tool_name": "imaginary", "reason": "unknown_tool"}],
        objectives=[{"task_id": "mine", "unit_index": 8, "target": [9, 10],
                     "tools": ["improve_tile"], "progress_predicate": {
                         "kind": "state_changed_to",
                         "path": ["tiles", 0, "improvement"],
                         "value": "IMPROVEMENT_MINE",
                     }}],
    )
    assert got["invalid_calls"] == 1
    assert got["domain_rejections"] == 2
    assert got["successful_mutations"] == 1
    assert got["useful_actions"] == 1
    assert got["repetitions"] == 1
    assert got["loop_excess"] == 1


def test_classifier_reports_useful_actions_unavailable_without_objectives():
    """Historical records that carry no objective mapping must not fabricate a score."""
    steps = [
        {"tool_name": "set_research", "tool_args": {"tech": "TECH_POTTERY"},
         "tool_result_full": "Research set.", "state_before": {"a": 1},
         "state_after": {"a": 2}, "state_digest_before": "x", "state_digest_after": "y"},
    ]
    got = classify_action_quality(steps=steps, invalid_tool_calls=[])
    assert got["useful_actions"] is None
    assert got["useful_action_coverage"] == "unavailable"
    # Domain rejection / repetition stay available even without objectives.
    assert got["domain_rejections"] == 0
    assert got["successful_mutations"] == 1
    assert got["repetitions"] == 0


def test_classifier_does_not_double_count_gated_calls_as_domain_rejections():
    """Realistic shape from agent.py: a gated/out_of_tier/unknown_tool call is
    BOTH appended to invalid_tool_calls AND given a step whose
    tool_result_full is agent.py's canned "UNAVAILABLE: ..." string (the call
    never reached the game engine — it was intercepted before dispatch).
    That single event must be counted once, as an invalid call, never as a
    domain rejection (which means the call reached the engine and was
    refused)."""
    steps = [
        {"tool_name": "fake_tool", "tool_args": {},
         "tool_result_full": "UNAVAILABLE: fake_tool is not a real tool.",
         "state_before": {"a": 1}, "state_after": {"a": 1},
         "state_digest_before": "x", "state_digest_after": "x"},
    ]
    got = classify_action_quality(
        steps=steps,
        invalid_tool_calls=[{"tool_name": "fake_tool", "reason": "unknown_tool"}],
    )
    assert got["invalid_calls"] == 1
    assert got["domain_rejections"] == 0
    assert got["successful_mutations"] == 0


# ---------------------------------------------------------------------------
# classify_result
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["Error: BLOCKED", "OK|BLOCKED"])
def test_classify_result_domain_rejection(raw: str):
    assert classify_result(raw) == "domain_rejection"


def test_classify_result_success():
    assert classify_result("IMPROVING|IMPROVEMENT_MINE|9,10") == "success"


def test_classify_result_unavailable_is_not_a_domain_rejection():
    """agent.py's _unavailable_result() prefix means the call was intercepted
    before it ever reached the game engine (gated/out_of_tier/unknown_tool) —
    it must not read as the game having rejected a legal call."""
    assert classify_result("UNAVAILABLE: fake_tool is not a real tool.") != "domain_rejection"


def test_classify_result_unavailable_is_not_dispatched():
    """G1: a never-dispatched call must not classify as 'success' either --
    that would let a minimal-arm model's out-of-tier tool call satisfy a
    treatment-only rubric level, flipping the A/B comparison."""
    assert classify_result("UNAVAILABLE: fake_tool is not a real tool.") == "not_dispatched"


def test_classify_result_malformed_arguments_is_not_dispatched():
    assert classify_result("MALFORMED_ARGUMENTS: not dispatched") == "not_dispatched"


def test_classify_result_not_dispatched_is_case_insensitive():
    assert classify_result("unavailable: fake_tool") == "not_dispatched"
    assert classify_result("malformed_arguments: bad args") == "not_dispatched"


def test_classifier_unavailable_and_malformed_steps_never_count_as_success():
    """G1 repro: a never-dispatched call must not count as a domain rejection
    OR a successful mutation, even if its (spoofed) digest fields show a
    change -- a real agent never produces this combination, but the
    classifier must not trust the digest over the dispatch outcome."""
    steps = [
        {"tool_name": "fake_tool", "tool_args": {},
         "tool_result_full": "UNAVAILABLE: fake_tool is not a real tool.",
         "state_digest_before": "a", "state_digest_after": "b"},
        {"tool_name": "bad_args_tool", "tool_args": {},
         "tool_result_full": "MALFORMED_ARGUMENTS: not dispatched",
         "state_digest_before": "c", "state_digest_after": "d"},
    ]
    got = classify_action_quality(steps=steps, invalid_tool_calls=[])
    assert got["domain_rejections"] == 0
    assert got["successful_mutations"] == 0


def test_predicate_successful_tool_call_false_when_result_is_unavailable():
    """G1: evaluate_predicate's successful_tool_call must not treat a
    never-dispatched call (matching tool/args) as a success."""
    steps = [
        {"tool_name": "improve_tile", "tool_args": {"unit_index": 8},
         "tool_result_full": "UNAVAILABLE: improve_tile is not in this tier."},
    ]
    predicate = {"kind": "successful_tool_call", "tool": "improve_tile", "args": {"unit_index": 8}}
    assert evaluate_predicate(predicate, steps=steps) is False


# ---------------------------------------------------------------------------
# G14 — successful_mutations / repetitions must not fabricate 0 when no step
# carries digest fields at all (historical arena records never carried
# state_digest_before/after; None != None silently reported "no mutation").
# ---------------------------------------------------------------------------

def test_successful_mutations_none_when_no_step_carries_digest_fields():
    steps = [
        {"tool_name": "set_research", "tool_args": {"tech": "TECH_POTTERY"},
         "tool_result_full": "Research set."},
        {"tool_name": "set_research", "tool_args": {"tech": "TECH_POTTERY"},
         "tool_result_full": "Research set."},
    ]
    got = classify_action_quality(steps=steps, invalid_tool_calls=[])
    assert got["successful_mutations"] is None
    assert got["repetitions"] is None
    assert got["loop_excess"] is None
    # Digest-independent counts stay real.
    assert got["domain_rejections"] == 0


def test_successful_mutations_real_count_when_digest_fields_present():
    steps = [
        {"tool_name": "set_research", "tool_args": {"tech": "TECH_POTTERY"},
         "tool_result_full": "Research set.", "state_digest_before": "x",
         "state_digest_after": "y"},
    ]
    got = classify_action_quality(steps=steps, invalid_tool_calls=[])
    assert got["successful_mutations"] == 1
    assert got["repetitions"] == 0
    assert got["loop_excess"] == 0


def test_successful_mutations_real_zero_for_empty_steps():
    got = classify_action_quality(steps=[], invalid_tool_calls=[])
    assert got["successful_mutations"] == 0
    assert got["repetitions"] == 0


# ---------------------------------------------------------------------------
# evaluate_predicate — one positive + one counterfactual per kind
# ---------------------------------------------------------------------------

def test_predicate_always_is_true():
    assert evaluate_predicate({"kind": "always"}) is True


def test_predicate_always_ignores_missing_state():
    # Counterfactual: a naive implementation might dereference state and
    # break/return False when nothing is supplied. "always" must not care.
    assert evaluate_predicate(
        {"kind": "always"}, initial_state=None, final_state=None, steps=(),
    ) is True


def test_predicate_all_true_when_every_branch_true():
    predicate = {"kind": "all", "predicates": [{"kind": "always"}, {"kind": "always"}]}
    assert evaluate_predicate(predicate) is True


def test_predicate_all_false_when_one_branch_false():
    final_state = {"score": 5}
    predicate = {
        "kind": "all",
        "predicates": [
            {"kind": "always"},
            {"kind": "final_state_equals", "path": ["score"], "value": 999},
        ],
    }
    assert evaluate_predicate(predicate, final_state=final_state) is False


def test_predicate_any_true_when_one_branch_true():
    final_state = {"score": 5}
    predicate = {
        "kind": "any",
        "predicates": [
            {"kind": "final_state_equals", "path": ["score"], "value": 999},
            {"kind": "final_state_equals", "path": ["score"], "value": 5},
        ],
    }
    assert evaluate_predicate(predicate, final_state=final_state) is True


def test_predicate_any_false_when_every_branch_false():
    final_state = {"score": 5}
    predicate = {
        "kind": "any",
        "predicates": [
            {"kind": "final_state_equals", "path": ["score"], "value": 1},
            {"kind": "final_state_equals", "path": ["score"], "value": 2},
        ],
    }
    assert evaluate_predicate(predicate, final_state=final_state) is False


def test_predicate_successful_tool_call_true_on_matching_success():
    steps = [
        {"tool_name": "improve_tile", "tool_args": {"unit_index": 8, "improvement_name": "IMPROVEMENT_MINE"},
         "tool_result_full": "IMPROVING|IMPROVEMENT_MINE|9,10"},
    ]
    predicate = {
        "kind": "successful_tool_call",
        "tool": "improve_tile",
        "args": {"unit_index": 8},
    }
    assert evaluate_predicate(predicate, steps=steps) is True


def test_predicate_successful_tool_call_false_when_result_is_domain_rejection():
    # Counterfactual: same tool + matching args, but the game rejected the call.
    steps = [
        {"tool_name": "improve_tile", "tool_args": {"unit_index": 8, "improvement_name": "IMPROVEMENT_MINE"},
         "tool_result_full": "Error: BLOCKED"},
    ]
    predicate = {
        "kind": "successful_tool_call",
        "tool": "improve_tile",
        "args": {"unit_index": 8},
    }
    assert evaluate_predicate(predicate, steps=steps) is False


def test_predicate_final_state_equals_true_on_match():
    predicate = {"kind": "final_state_equals", "path": ["tiles", 0, "improvement"], "value": "IMPROVEMENT_MINE"}
    final_state = {"tiles": [{"improvement": "IMPROVEMENT_MINE"}]}
    assert evaluate_predicate(predicate, final_state=final_state) is True


def test_predicate_final_state_equals_false_on_mismatch():
    predicate = {"kind": "final_state_equals", "path": ["tiles", 0, "improvement"], "value": "IMPROVEMENT_MINE"}
    final_state = {"tiles": [{"improvement": "IMPROVEMENT_FARM"}]}
    assert evaluate_predicate(predicate, final_state=final_state) is False


def test_predicate_state_changed_to_true_when_value_actually_changes():
    predicate = {"kind": "state_changed_to", "path": ["tiles", 0, "improvement"], "value": "IMPROVEMENT_MINE"}
    initial_state = {"tiles": [{"improvement": None}]}
    final_state = {"tiles": [{"improvement": "IMPROVEMENT_MINE"}]}
    assert evaluate_predicate(predicate, initial_state=initial_state, final_state=final_state) is True


def test_predicate_state_changed_to_false_when_already_at_target():
    # Counterfactual: value matches target but nothing actually changed this step.
    predicate = {"kind": "state_changed_to", "path": ["tiles", 0, "improvement"], "value": "IMPROVEMENT_MINE"}
    state = {"tiles": [{"improvement": "IMPROVEMENT_MINE"}]}
    assert evaluate_predicate(predicate, initial_state=state, final_state=state) is False


def test_predicate_unit_distance_decreased_true_when_closer():
    predicate = {"kind": "unit_distance_decreased", "unit_index": 8, "target": [9, 10]}
    initial_state = {"units": [{"id": 8, "x": 4, "y": 10}]}
    final_state = {"units": [{"id": 8, "x": 7, "y": 10}]}
    assert evaluate_predicate(predicate, initial_state=initial_state, final_state=final_state) is True


def test_predicate_unit_distance_decreased_false_when_farther():
    # Counterfactual: unit moved away from the target instead of toward it.
    predicate = {"kind": "unit_distance_decreased", "unit_index": 8, "target": [9, 10]}
    initial_state = {"units": [{"id": 8, "x": 8, "y": 10}]}
    final_state = {"units": [{"id": 8, "x": 4, "y": 10}]}
    assert evaluate_predicate(predicate, initial_state=initial_state, final_state=final_state) is False


def test_predicate_unit_distance_decreased_uses_hex_distance_not_manhattan():
    """F11 repro: the brief's counterexample. (5,5)->(6,6) approaching
    target (5,8) is real progress on a hex grid (hex distance 3 -> 2), but
    Manhattan distance (|dx|+|dy|) misses it entirely (3 -> 3, i.e. no
    apparent decrease), so the old implementation reports False here."""
    predicate = {"kind": "unit_distance_decreased", "unit_index": 8, "target": [5, 8]}
    initial_state = {"units": [{"id": 8, "x": 5, "y": 5}]}
    final_state = {"units": [{"id": 8, "x": 6, "y": 6}]}
    assert evaluate_predicate(predicate, initial_state=initial_state, final_state=final_state) is True


def test_predicate_unit_distance_decreased_true_on_straight_line_approach():
    # A simple straight-line approach (same column, moving south toward
    # the target) must still register as a decrease under the hex metric.
    predicate = {"kind": "unit_distance_decreased", "unit_index": 8, "target": [5, 20]}
    initial_state = {"units": [{"id": 8, "x": 5, "y": 5}]}
    final_state = {"units": [{"id": 8, "x": 5, "y": 15}]}
    assert evaluate_predicate(predicate, initial_state=initial_state, final_state=final_state) is True


# ---------------------------------------------------------------------------
# Fail-closed behavior: never a silent False/0
# ---------------------------------------------------------------------------

def test_evaluate_predicate_unknown_kind_raises():
    with pytest.raises(PredicateError):
        evaluate_predicate({"kind": "teleport_unit"})


def test_evaluate_predicate_missing_typed_path_raises():
    predicate = {"kind": "final_state_equals", "path": ["tiles", 0, "improvement"], "value": "IMPROVEMENT_MINE"}
    with pytest.raises(PredicateError):
        evaluate_predicate(predicate, final_state={"tiles": []})


def test_evaluate_predicate_missing_final_state_raises():
    predicate = {"kind": "final_state_equals", "path": ["score"], "value": 5}
    with pytest.raises(PredicateError):
        evaluate_predicate(predicate, final_state=None)


def test_evaluate_predicate_unit_not_found_raises():
    predicate = {"kind": "unit_distance_decreased", "unit_index": 99, "target": [0, 0]}
    with pytest.raises(PredicateError):
        evaluate_predicate(
            predicate,
            initial_state={"units": [{"id": 8, "x": 0, "y": 0}]},
            final_state={"units": [{"id": 8, "x": 0, "y": 0}]},
        )
