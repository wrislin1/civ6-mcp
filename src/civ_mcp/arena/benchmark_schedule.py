"""Immutable trial schedules compiled from a `SuiteManifest`.

`compile_schedule` expands a suite's positions x models x seeds x arms into a
flat, ordered tuple of `TrialSpec`s. The schedule is the *only* thing that
decides which (position, model, arm, seed) combinations run and in what
order -- nothing downstream may reorder, skip, or regenerate it, which is
why every field on `TrialSpec` is a plain schedule input and nothing is
derived at run time (session fingerprints are stamped later by
`BenchmarkStore`, once startup evidence exists to fingerprint).

Ordering: the only implemented `order` is "abba" -- for each
(position, model) group, seeds are visited in the order given and each seed
produces one back-to-back pair of trials (one per arm). Successive pairs
alternate arm direction (A,B then B,A then A,B, ...) so that whichever arm
runs first is balanced across the run, cancelling first-mover / freshness
order effects between arms.

Two arm-tool-surface checks belong here rather than in the benchmark agent
(a later task) because they are properties of the *schedule config*, not of
the live tool registry: a tier name must be one this runner knows how to
resolve, and an arm's explicit tool override (if a `TreatmentArm.options`
carries one) must not defeat the runner's `finish_trial`-required /
`end_turn`-forbidden tool-surface contract. Resolving a raw tool tier into
its actual tool list (e.g. turning "standard" into the tuple of tool names
it names in `registry.TIERS`) is deliberately NOT done here -- that
resolution, plus the `end_turn`-strip / `finish_trial`-append transform that
turns a raw tier into a *benchmark-safe* live tool surface, is the benchmark
agent's job in a later task. Duplicating that transform here would only
create a second place for it to drift; this module validates the tier
*name* (via `ALLOWED_TIERS`, below) and an arm's own declared `options`
override, and nothing more.

Indices are 1-based (the first trial is index 1, not 0) to match every other
task in this plan: the store's `commit_trial(1, ...)` fixture, the runner's
`TrialSpec(index=1, ...)` / `TrialSpec(index=2, ...)` test pair, and the
report task's `schedule.json` fixture (indices 1..3) all assume the first
trial is 1. Under 1-based ABBA, `index % 4` is 1 or 0 for the first arm and
2 or 3 for the second.

`audit_indices` balance is checked by exact per-arm count equality: each
audit index is resolved to the arm it actually lands on in the compiled
schedule (not inferred from index arithmetic), and every arm named in
`suite.arms` must appear the same number of times among the audited trials
(including zero, if audited at all) -- a mere `len(audit_indices) % len(arms)
== 0` divisibility check is not sufficient, since a same-size set can still
land unevenly across arms depending on where it falls in the ABBA cycle.
"""
from __future__ import annotations

import dataclasses

from civ_mcp.arena.benchmark_manifest import SuiteManifest, TreatmentArm
from civ_mcp.arena.registry import TIERS

# ALLOWED_TIERS is derived from registry.TIERS (the single source of truth
# for tier -> tool-name membership) rather than hardcoded, so a tier rename
# or addition in registry.py can never silently drift out of sync with what
# compile_schedule accepts. "full" is deliberately excluded: it is
# `tuple(TOOL_REGISTRY)`, the entire uncurated tool surface (every tool ever
# registered, present or future) rather than a hand-picked, audited tier --
# nothing guarantees it stays free of a turn-ending or otherwise
# benchmark-unsafe tool (e.g. an `end_turn`-like tool) if one is ever added
# to TOOL_REGISTRY. Only the curated "minimal" and "standard" tiers, which
# are hand-maintained to exclude such tools, are benchmark-eligible.
# test_allowed_tiers_tracks_registry_tiers_minus_full pins this relationship
# so registry drift breaks loudly instead of silently.
ALLOWED_TIERS = frozenset(TIERS) - {"full"}
SUPPORTED_ORDERS = frozenset({"abba"})


@dataclasses.dataclass(frozen=True)
class TrialSpec:
    """One scheduled trial. Schedule inputs ONLY -- no session fingerprint:
    that is computed after the schedule and startup evidence are locked, then
    stamped onto raw artifacts by `BenchmarkStore`."""
    index: int
    pair_id: str
    position_id: str
    model: str
    arm_id: str
    seed: int


def _validate_order(order: str) -> None:
    if order not in SUPPORTED_ORDERS:
        raise ValueError(
            f"unsupported schedule order {order!r}; supported orders: {sorted(SUPPORTED_ORDERS)}"
        )


def _validate_arms(arms: tuple[TreatmentArm, ...]) -> None:
    if not arms:
        raise ValueError("suite must declare at least one treatment arm")
    seen_arm_ids: set[str] = set()
    for arm in arms:
        if arm.arm_id in seen_arm_ids:
            raise ValueError(
                f"suite declares duplicate arm_id {arm.arm_id!r}; duplicate arm_ids "
                "would silently corrupt ABBA pairing and the calibration section "
                "(same-arm pairs are skipped, so a whole run could produce zero "
                "pairs with no error)"
            )
        seen_arm_ids.add(arm.arm_id)
    for arm in arms:
        if arm.tools not in ALLOWED_TIERS:
            raise ValueError(
                f"arm {arm.arm_id!r} has unknown tool tier {arm.tools!r}; "
                f"allowed tiers: {sorted(ALLOWED_TIERS)}"
            )
        override = arm.options.get("tools") if isinstance(arm.options, dict) else None
        if override is None:
            continue
        override_tools = list(override)
        if "end_turn" in override_tools:
            raise ValueError(
                f"arm {arm.arm_id!r} tool override exposes end_turn, which benchmark "
                "trials must never expose"
            )
        if "finish_trial" not in override_tools:
            raise ValueError(
                f"arm {arm.arm_id!r} tool override is missing finish_trial, which every "
                "benchmark arm must expose"
            )


def _validate_audit_indices(
    audit_indices: tuple[int, ...], trials: tuple[TrialSpec, ...], arm_ids: list[str]
) -> None:
    total_trials = len(trials)
    if len(audit_indices) != len(set(audit_indices)):
        raise ValueError(f"audit_indices contains duplicate indices: {audit_indices}")
    for i in audit_indices:
        if not (1 <= i <= total_trials):
            raise ValueError(
                f"audit index {i} is out of range for a schedule of {total_trials} "
                f"1-based trials (valid range 1..{total_trials})"
            )
    if not audit_indices:
        return

    arm_by_index = {t.index: t.arm_id for t in trials}
    counts = {arm_id: 0 for arm_id in arm_ids}
    for i in audit_indices:
        counts[arm_by_index[i]] += 1
    if len(set(counts.values())) > 1:
        raise ValueError(
            f"audit_indices must be balanced across arms (equal count per arm); "
            f"got counts {counts}"
        )


def _generate_trials(suite: SuiteManifest) -> tuple[TrialSpec, ...]:
    trials: list[TrialSpec] = []
    index = 1
    for position_id in suite.positions:
        for model in suite.models:
            for block_index, seed in enumerate(suite.seeds):
                pair_id = f"{position_id}:{model}:seed{seed}:{block_index}"
                arm_order = suite.arms if block_index % 2 == 0 else tuple(reversed(suite.arms))
                for arm in arm_order:
                    trials.append(
                        TrialSpec(
                            index=index,
                            pair_id=pair_id,
                            position_id=position_id,
                            model=model,
                            arm_id=arm.arm_id,
                            seed=seed,
                        )
                    )
                    index += 1
    return tuple(trials)


def compile_schedule(suite: SuiteManifest) -> tuple[TrialSpec, ...]:
    _validate_order(suite.order)
    _validate_arms(suite.arms)

    trials = _generate_trials(suite)

    seen_indices = {t.index for t in trials}
    if len(seen_indices) != len(trials):
        raise ValueError("compiled schedule produced duplicate trial indices")

    _validate_audit_indices(suite.audit_indices, trials, [arm.arm_id for arm in suite.arms])

    return trials
