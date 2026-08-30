import pytest

from civ_mcp.arena import registry
from civ_mcp.arena.backends import SamplingConfig
from civ_mcp.arena.benchmark_manifest import SuiteManifest, TreatmentArm
from civ_mcp.arena.benchmark_schedule import ALLOWED_TIERS, TrialSpec, compile_schedule


def test_allowed_tiers_tracks_registry_tiers_minus_full():
    # ALLOWED_TIERS must be derived from registry.TIERS, not a second,
    # independent literal -- if registry.TIERS ever gains/renames a tier,
    # this must break loudly instead of silently drifting out of sync.
    # "full" (the uncurated tuple(TOOL_REGISTRY)) is deliberately excluded:
    # only hand-curated tiers are benchmark-eligible.
    assert ALLOWED_TIERS == frozenset(registry.TIERS) - {"full"}
    assert "full" not in ALLOWED_TIERS
    assert {"minimal", "standard"} <= ALLOWED_TIERS


def _base_suite(**overrides) -> SuiteManifest:
    fields = dict(
        suite_id="builder-cal-v1",
        driver="single_turn",
        positions=("builder-cal-v1",),
        models=("qwen3.6-27b",),
        arms=(
            TreatmentArm("minimal", "minimal", {}),
            TreatmentArm("standard", "standard", {}),
        ),
        seeds=tuple(range(101, 113)),
        order="abba",
        sampling=SamplingConfig(temperature=0.2, top_p=0.95, seed=None, max_tokens=6144),
        max_steps=15,
        result_char_cap=6000,
        audit_indices=(1, 4, 9, 14, 19, 22),
    )
    fields.update(overrides)
    return SuiteManifest(**fields)


def test_calibration_schedule_is_twelve_abba_pairs_with_shared_pair_seed():
    suite = _base_suite()
    trials = compile_schedule(suite)
    assert len(trials) == 24
    assert [t.arm_id for t in trials[:8]] == ["minimal", "standard", "standard", "minimal",
                                                    "minimal", "standard", "standard", "minimal"]
    for left, right in zip(trials[::2], trials[1::2], strict=True):
        assert left.pair_id == right.pair_id
        assert left.seed == right.seed


def test_compile_schedule_assigns_sequential_unique_indices_starting_at_one():
    trials = compile_schedule(_base_suite())
    assert [t.index for t in trials] == list(range(1, 25))
    assert trials[0].index == 1


def test_compile_schedule_trial_spec_has_only_schedule_fields():
    trials = compile_schedule(_base_suite())
    fields = {f.name for f in __import__("dataclasses").fields(TrialSpec)}
    assert fields == {"index", "pair_id", "position_id", "model", "arm_id", "seed"}


def test_compile_schedule_rejects_duplicate_audit_indices():
    suite = _base_suite(audit_indices=(1, 1, 4, 9, 14, 19))
    with pytest.raises(ValueError, match="duplicate"):
        compile_schedule(suite)


def test_compile_schedule_rejects_out_of_range_audit_index():
    suite = _base_suite(audit_indices=(1, 4, 9, 14, 19, 999))
    with pytest.raises(ValueError, match="out of range"):
        compile_schedule(suite)


def test_compile_schedule_rejects_zero_audit_index_since_indices_are_one_based():
    suite = _base_suite(audit_indices=(0, 4, 9, 14, 19, 22))
    with pytest.raises(ValueError, match="out of range"):
        compile_schedule(suite)


def test_compile_schedule_brief_audit_indices_land_exactly_three_per_arm():
    # 1-based ABBA: index % 4 in {1, 0} -> minimal, {2, 3} -> standard.
    # (1, 4, 9, 14, 19, 22) resolves to minimal={1,4,9}, standard={14,19,22}.
    suite = _base_suite()
    trials = compile_schedule(suite)
    by_index = {t.index: t.arm_id for t in trials}
    audited_arms = [by_index[i] for i in suite.audit_indices]
    assert audited_arms == ["minimal", "minimal", "minimal", "standard", "standard", "standard"]


def test_compile_schedule_rejects_unbalanced_audit_indices():
    # Same size as the brief's balanced set (6), but resolves to 4 minimal / 2
    # standard trials under 1-based ABBA -- exact per-arm counts must match,
    # not merely the divisibility of len(audit_indices) by len(arms).
    suite = _base_suite(audit_indices=(1, 4, 9, 13, 14, 22))
    with pytest.raises(ValueError, match="balanced"):
        compile_schedule(suite)


def test_compile_schedule_rejects_unknown_tier():
    suite = _base_suite(
        arms=(
            TreatmentArm("minimal", "minimal", {}),
            TreatmentArm("weird", "weird-tier", {}),
        ),
        audit_indices=(),
    )
    with pytest.raises(ValueError, match="tier"):
        compile_schedule(suite)


def test_compile_schedule_rejects_arm_tool_override_exposing_end_turn():
    suite = _base_suite(
        arms=(
            TreatmentArm("minimal", "minimal", {}),
            TreatmentArm("standard", "standard", {"tools": ["get_units", "end_turn", "finish_trial"]}),
        ),
        audit_indices=(),
    )
    with pytest.raises(ValueError, match="end_turn"):
        compile_schedule(suite)


def test_compile_schedule_rejects_arm_tool_override_missing_finish_trial():
    suite = _base_suite(
        arms=(
            TreatmentArm("minimal", "minimal", {}),
            TreatmentArm("standard", "standard", {"tools": ["get_units", "move_unit"]}),
        ),
        audit_indices=(),
    )
    with pytest.raises(ValueError, match="finish_trial"):
        compile_schedule(suite)


def test_compile_schedule_allows_arm_tool_override_with_finish_trial_and_no_end_turn():
    suite = _base_suite(
        arms=(
            TreatmentArm("minimal", "minimal", {}),
            TreatmentArm("standard", "standard", {"tools": ["get_units", "finish_trial"]}),
        ),
        audit_indices=(),
    )
    trials = compile_schedule(suite)
    assert len(trials) == 24


def test_compile_schedule_rejects_unsupported_order():
    suite = _base_suite(order="random", audit_indices=())
    with pytest.raises(ValueError, match="order"):
        compile_schedule(suite)
