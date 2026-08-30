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
`end_turn`-forbidden tool-surface contract. Resolving a raw tool tier (e.g.
turning "standard" into an actual tool list) is deliberately NOT done here --
the raw arena "standard" tier includes `end_turn` (it is stripped and
`finish_trial` appended only when the benchmark agent builds the live tool
surface), so resolving it at schedule time would wrongly reject a perfectly
valid arm.
"""
from __future__ import annotations

import dataclasses

from civ_mcp.arena.benchmark_manifest import SuiteManifest, TreatmentArm

ALLOWED_TIERS = frozenset({"minimal", "standard"})
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
    audit_indices: tuple[int, ...], total_trials: int, num_arms: int
) -> None:
    if len(audit_indices) != len(set(audit_indices)):
        raise ValueError(f"audit_indices contains duplicate indices: {audit_indices}")
    for i in audit_indices:
        if not (0 <= i < total_trials):
            raise ValueError(
                f"audit index {i} is out of range for a schedule of {total_trials} trials"
            )
    if audit_indices and num_arms and len(audit_indices) % num_arms != 0:
        raise ValueError(
            f"audit_indices ({len(audit_indices)}) must be balanced across arms "
            f"({num_arms}); {len(audit_indices)} does not divide evenly"
        )


def compile_schedule(suite: SuiteManifest) -> tuple[TrialSpec, ...]:
    _validate_order(suite.order)
    _validate_arms(suite.arms)

    total_trials = len(suite.positions) * len(suite.models) * len(suite.seeds) * len(suite.arms)
    _validate_audit_indices(suite.audit_indices, total_trials, len(suite.arms))

    trials: list[TrialSpec] = []
    index = 0
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

    seen_indices = {t.index for t in trials}
    if len(seen_indices) != len(trials):
        raise ValueError("compiled schedule produced duplicate trial indices")

    return tuple(trials)
