"""Fail-closed admission gates for the controlled-position benchmark runner.

Every gate here is a pure function: it consumes evidence the caller already
gathered (git status/commit pairs, a boot-health dict, a resolved tool
schema, a `BackendProbe`, ...) and either returns JSON-safe evidence or
raises `GateFailure(code, details)`. Nothing in this module shells out to
git, touches the filesystem, calls a backend, or terminates a process --
that keeps every gate testable with plain fixtures and keeps the "fail
closed" contract simple to audit: a missing or ambiguous piece of evidence
is always a raise, never a silent default.

Five gates plus the shared exception type:

- `check_clean_checkout` -- WSL/Windows companion git hygiene.
- `check_treatment_can_fire` -- the minimal arm can reach rubric levels 1-2
  and the standard arm has the capabilities to complete every objective.
- `check_gpu_conflicts` -- conflicting GPU work is blocked unless an
  operator-approved acknowledgment names *exactly* the conflicting
  services. Never kills or drains anything itself.
- `admit_model_block` -- per-model-block admission: endpoint/model identity,
  a counted backend's non-hidden retry policy, a positive briefing budget,
  ten warm-latency samples with no probe errors, seed-honoring, and the
  derived episode wall.
- `build_session_lock` -- assembles the above plus boot-health, digests,
  deployment evidence, and a canonical-state checksum into one immutable,
  JSON-safe, deterministically-fingerprinted session lock.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable, Mapping, Sequence


def _present_commit(value: object) -> bool:
    """A commit identifier must be a non-empty string -- `None`/empty on
    both sides must never be treated as "matching" for the clean-checkout
    gate (that would admit a session with no code revision recorded)."""
    return isinstance(value, str) and bool(value.strip())

from civ_mcp.arena.backends import RetryPolicy, SamplingConfig
from civ_mcp.arena.benchmark_agent import FINISH_TRIAL_TOOL_NAME
from civ_mcp.arena.benchmark_backend import BackendProbe, episode_wall_seconds, nearest_rank_p95
from civ_mcp.arena.benchmark_manifest import PositionManifest, fingerprint
from civ_mcp.arena.benchmark_state import state_digest

__all__ = [
    "GateFailure",
    "check_clean_checkout",
    "check_treatment_can_fire",
    "check_gpu_conflicts",
    "admit_model_block",
    "build_session_lock",
]


class GateFailure(Exception):
    """Raised by every gate in this module on a fail-closed admission
    refusal.

    `code` is a stable machine-readable identifier for the failure kind;
    `details` is JSON-safe evidence describing exactly what was checked and
    why it failed. The exception message is `details["message"]` when
    present, else `code` -- `details` itself is left untouched so a caller
    logging `exc.details` gets the full evidence including the message.
    """

    def __init__(self, code: str, details: Mapping[str, object]):
        self.code = code
        self.details = dict(details)
        super().__init__(str(self.details.get("message", code)))


# ---------------------------------------------------------------------------
# check_clean_checkout
# ---------------------------------------------------------------------------


def check_clean_checkout(
    *, wsl: Mapping[str, object], windows: Mapping[str, object]
) -> dict[str, object]:
    """Verify the WSL and Windows-companion checkouts are both clean and at
    the same commit.

    `wsl`/`windows` are evidence dicts of the form
    ``{"commit": "<sha>", "status": "<git status --porcelain output>"}``.
    A non-empty `status` means a dirty tree. Fails closed on either side
    being dirty, or on the two commits disagreeing.
    """
    wsl_commit = wsl.get("commit")
    wsl_status = str(wsl.get("status") or "")
    windows_commit = windows.get("commit")
    windows_status = str(windows.get("status") or "")

    if wsl_status.strip():
        raise GateFailure(
            "dirty_checkout",
            {
                "side": "wsl",
                "status": wsl_status,
                "message": f"WSL checkout is dirty: {wsl_status.strip()!r}",
            },
        )
    if windows_status.strip():
        raise GateFailure(
            "dirty_checkout",
            {
                "side": "windows",
                "status": windows_status,
                "message": f"Windows companion checkout is dirty: {windows_status.strip()!r}",
            },
        )
    if not _present_commit(wsl_commit) or not _present_commit(windows_commit):
        raise GateFailure(
            "missing_commit",
            {
                "wsl_commit": wsl_commit,
                "windows_commit": windows_commit,
                "message": (
                    "clean-checkout gate requires a non-empty commit identifier on both "
                    f"sides; got wsl_commit={wsl_commit!r} windows_commit={windows_commit!r}"
                ),
            },
        )
    if wsl_commit != windows_commit:
        raise GateFailure(
            "commit_mismatch",
            {
                "wsl_commit": wsl_commit,
                "windows_commit": windows_commit,
                "message": (
                    f"WSL commit {wsl_commit!r} does not match Windows companion "
                    f"commit {windows_commit!r}"
                ),
            },
        )

    return {"commit": wsl_commit, "wsl_status": wsl_status, "windows_status": windows_status}


# ---------------------------------------------------------------------------
# check_treatment_can_fire
# ---------------------------------------------------------------------------


def check_treatment_can_fire(
    *,
    position: PositionManifest,
    minimal_observation: Mapping[str, object],
    standard_capabilities: Iterable[str],
) -> dict[str, object]:
    """Verify both halves of the treatment design can actually fire for
    `position`:

    1. The minimal arm can reach rubric levels 1-2: every rubric task whose
       declared `levels` include 1 or 2 must be discoverable from the
       minimal-tier observation (`minimal_observation["discoverable_task_ids"]`).
       A task nominally reachable only at level 0 (baseline/no-op) needs no
       discovery, so it is not required here.
    2. The standard arm has the capabilities to complete every objective.
       Every objective must declare its `"requires"` list of capability
       names -- fail closed by design: a missing or empty `"requires"` is
       an undeclared requirement, not proof the objective needs nothing,
       and raises `undeclared_objective_requirements` rather than being
       silently treated as satisfied. A declared-but-uncovered requirement
       raises the `treatment_cannot_fire` / `standard_arm_missing_capabilities`
       failure instead.
    """
    discoverable = set(minimal_observation.get("discoverable_task_ids") or ())
    standard_caps = set(standard_capabilities)

    def _reaches_level_1_or_2(task_id: object, levels: object) -> bool:
        reached = False
        for level in levels or ():
            if not isinstance(level, Mapping):
                raise GateFailure(
                    "malformed_rubric_level",
                    {
                        "position_id": position.position_id,
                        "task_id": task_id,
                        "level": level,
                        "message": (
                            f"position {position.position_id}: rubric task {task_id!r} has a "
                            f"level that is not a {{'score', 'predicate'}} mapping: {level!r}. "
                            "check_treatment_can_fire accepts only the canonical mapping "
                            "rubric shape."
                        ),
                    },
                )
            if level.get("score") in (1, 2):
                reached = True
        return reached

    nontrivial_task_ids = sorted(
        {
            rubric_entry["task_id"]
            for rubric_entry in position.rubric
            if _reaches_level_1_or_2(rubric_entry.get("task_id"), rubric_entry.get("levels", []))
        }
    )
    unreachable = sorted(set(nontrivial_task_ids) - discoverable)
    if unreachable:
        raise GateFailure(
            "treatment_cannot_fire",
            {
                "position_id": position.position_id,
                "reason": "minimal_arm_cannot_reach_levels",
                "unreachable_task_ids": unreachable,
                "message": (
                    f"position {position.position_id}: minimal-tier observation "
                    f"cannot reach rubric levels 1-2 for task(s) {unreachable}"
                ),
            },
        )

    missing_capabilities: dict[str, list[str]] = {}
    undeclared_task_ids: list[str] = []
    for objective in position.objectives:
        task_id = str(objective.get("task_id"))
        required = objective.get("requires")
        if not required:
            # Fail closed on silence: an objective with no declared
            # requirement is NOT trivially satisfied. A real position
            # manifest must say what the standard arm needs before a
            # counted run can admit it -- absence of a "requires" list is
            # a manifest-authoring gap, not proof the objective needs
            # nothing.
            undeclared_task_ids.append(task_id)
            continue
        missing = sorted(set(required) - standard_caps)
        if missing:
            missing_capabilities[task_id] = missing

    if undeclared_task_ids:
        raise GateFailure(
            "undeclared_objective_requirements",
            {
                "position_id": position.position_id,
                "undeclared_task_ids": sorted(undeclared_task_ids),
                "message": (
                    f"position {position.position_id}: objective(s) "
                    f"{sorted(undeclared_task_ids)} do not declare a 'requires' "
                    "capability list; a counted run must not admit an objective "
                    "whose standard-arm capability requirements are undeclared"
                ),
            },
        )

    if missing_capabilities:
        raise GateFailure(
            "treatment_cannot_fire",
            {
                "position_id": position.position_id,
                "reason": "standard_arm_missing_capabilities",
                "missing_capabilities": missing_capabilities,
                "message": (
                    f"position {position.position_id}: standard arm lacks capabilities "
                    f"required to complete objectives: {missing_capabilities}"
                ),
            },
        )

    return {
        "position_id": position.position_id,
        "minimal_reachable_task_ids": nontrivial_task_ids,
        "standard_capabilities": sorted(standard_caps),
        "ok": True,
    }


# ---------------------------------------------------------------------------
# check_gpu_conflicts
# ---------------------------------------------------------------------------


def check_gpu_conflicts(
    *,
    processes: Sequence[Mapping[str, object]],
    approved_services: Iterable[str] = (),
) -> dict[str, object]:
    """Detect conflicting GPU work. Never terminates, kills, or drains any
    process -- it only reports.

    `processes` is a snapshot of GPU-resident processes the caller already
    gathered, each ``{"pid": int, "service": str | None, "gpu_index": int, ...}``.
    A row with no identified `service` is always a conflict -- an
    unidentified/unmanaged process is never auto-drained regardless of any
    acknowledgment. Named services are conflicts unless `approved_services`
    is an *exact* set match against them: naming a service that is not
    actually present, or omitting one that is, both fail closed.
    """
    unidentified = [p for p in processes if not p.get("service")]
    if unidentified:
        raise GateFailure(
            "gpu_conflict_unidentified_process",
            {
                "unidentified_pids": [p.get("pid") for p in unidentified],
                "message": (
                    "GPU conflict: unidentified process(es) present "
                    f"{[p.get('pid') for p in unidentified]}; an unmanaged/unidentified "
                    "process is never auto-drained"
                ),
            },
        )

    conflicting_services = {p["service"] for p in processes}
    approved = set(approved_services)
    if conflicting_services != approved:
        raise GateFailure(
            "gpu_conflict_not_acknowledged",
            {
                "conflicting_services": sorted(conflicting_services),
                "approved_services": sorted(approved),
                "unapproved": sorted(conflicting_services - approved),
                "over_approved": sorted(approved - conflicting_services),
                "message": (
                    "GPU conflict: operator-approved services do not exactly cover "
                    f"the conflicting service(s): conflicting={sorted(conflicting_services)} "
                    f"approved={sorted(approved)}"
                ),
            },
        )

    return {
        "conflicting_services": sorted(conflicting_services),
        "approved_services": sorted(approved),
        "process_count": len(processes),
        "ok": True,
    }


# ---------------------------------------------------------------------------
# admit_model_block
# ---------------------------------------------------------------------------


def admit_model_block(
    *,
    requested_model: str,
    resolved_model: str,
    requested_endpoint: str,
    resolved_endpoint: str,
    registry_fingerprint: str,
    gpu_topology: Mapping[str, object],
    retry_policy: RetryPolicy,
    sampling: SamplingConfig,
    probe: BackendProbe,
    briefing_budget_chars: int,
    max_steps: int,
) -> dict[str, object]:
    """Admit one strictly-serial model block, immediately before it runs.

    Fails closed on:

    - endpoint/model identity mismatch (resolved endpoint disagrees with the
      requested one, or the backend's own probe-reported model identity is
      unconfirmed/disagrees with `resolved_model`) -- green health alone is
      never sufficient;
    - a counted backend whose `retry_policy` would hide a resampled request
      attempt (`max_attempts != 1`);
    - a zero (or negative) briefing budget;
    - fewer than ten warm-latency samples (the pre-flight probe didn't
      complete its full sample set);
    - any error recorded by the pre-flight probe (including the seed
      differing-sensitivity check, which does not otherwise show up as a
      short `latencies_s` list);
    - a locked seed that the probe proved is not actually honored.

    Returns JSON-safe evidence including the ten warm latencies, their p95,
    and the derived episode wall (`benchmark_backend.episode_wall_seconds`).
    """
    identity_mismatches: dict[str, object] = {}
    if resolved_endpoint != requested_endpoint:
        identity_mismatches["endpoint"] = {
            "requested": requested_endpoint,
            "resolved": resolved_endpoint,
        }
    if not probe.model_confirmed or probe.model != resolved_model:
        identity_mismatches["model"] = {
            "resolved_model": resolved_model,
            "probe_model": probe.model,
            "model_confirmed": probe.model_confirmed,
        }
    if identity_mismatches:
        raise GateFailure(
            "endpoint_identity_mismatch",
            {
                "mismatches": identity_mismatches,
                "message": (
                    f"endpoint identity mismatch admitting model block for "
                    f"{requested_model!r}: {sorted(identity_mismatches)}"
                ),
            },
        )

    if retry_policy.max_attempts != 1:
        raise GateFailure(
            "counted_backend_hidden_retries",
            {
                "max_attempts": retry_policy.max_attempts,
                "message": (
                    "a counted benchmark backend must be constructed with "
                    f"RetryPolicy(max_attempts=1); got max_attempts={retry_policy.max_attempts}, "
                    "which would hide a resampled request attempt from recorded evidence"
                ),
            },
        )

    if briefing_budget_chars <= 0:
        raise GateFailure(
            "zero_briefing_budget",
            {
                "briefing_budget_chars": briefing_budget_chars,
                "message": (
                    f"briefing budget is {briefing_budget_chars}; a counted model "
                    "block requires a positive briefing budget"
                ),
            },
        )

    if len(probe.latencies_s) < 10:
        raise GateFailure(
            "insufficient_warm_latency_samples",
            {
                "sample_count": len(probe.latencies_s),
                "message": (
                    f"pre-flight probe produced only {len(probe.latencies_s)} warm "
                    "latency sample(s); ten are required before a model block can be admitted"
                ),
            },
        )

    if probe.errors:
        raise GateFailure(
            "backend_probe_errors",
            {
                "errors": list(probe.errors),
                "message": (
                    f"pre-flight backend probe recorded {len(probe.errors)} error(s), "
                    "including possibly the seed differing-sensitivity check; refusing "
                    "to admit a model block on unverified probe evidence"
                ),
            },
        )

    if sampling.seed is not None and not probe.seed_honored:
        raise GateFailure(
            "seed_not_honored",
            {
                "seed": sampling.seed,
                "message": (
                    f"sampling locks seed={sampling.seed} but the pre-flight probe could "
                    "not confirm the backend actually honors it"
                ),
            },
        )

    warm_latencies_s = list(probe.latencies_s)
    p95_latency_s = nearest_rank_p95(warm_latencies_s)
    episode_wall_s = episode_wall_seconds(max_steps=max_steps, latencies_s=warm_latencies_s)

    return {
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "requested_endpoint": requested_endpoint,
        "resolved_endpoint": resolved_endpoint,
        "registry_fingerprint": registry_fingerprint,
        "gpu_topology": dict(gpu_topology),
        "sampling": asdict(sampling),
        "retry_policy": asdict(retry_policy),
        "seed_honored": probe.seed_honored,
        "warm_latencies_s": warm_latencies_s,
        "p95_latency_s": p95_latency_s,
        "episode_wall_s": episode_wall_s,
        "briefing_budget_chars": briefing_budget_chars,
        "ok": True,
    }


# ---------------------------------------------------------------------------
# build_session_lock
# ---------------------------------------------------------------------------


def build_session_lock(
    *,
    position: PositionManifest,
    wsl: Mapping[str, object],
    windows: Mapping[str, object],
    boot_health: Mapping[str, object] | None,
    manifest_fingerprint: str,
    schedule_fingerprint: str,
    prompt_fingerprint: str,
    rubric_fingerprint: str,
    tool_fingerprint: str,
    scorer_fingerprint: str,
    tools_schema: Sequence[Mapping[str, Any]],
    deployment: Mapping[str, object] | None,
    canonical_state: Mapping[str, object],
    model_admission: Mapping[str, object] | None,
) -> dict[str, object]:
    """Assemble one immutable, JSON-safe, deterministically-fingerprinted
    session lock, or raise `GateFailure` if any piece of admission evidence
    fails closed.

    Checks, in order:

    1. `check_clean_checkout(wsl, windows)` -- dirty tree / commit mismatch.
    2. `boot_health` is present, `ok`, and carries a fresh baseline offset
       (a missing/failed boot-health poll must never be silently skipped).
    3. `tools_schema` never exposes `end_turn` and always exposes
       `finish_trial` (the benchmark control tool).
    4. Every digest (manifest/schedule/prompt/rubric/tool/scorer) is present.
    5. `deployment` is present and reports `ok` (a verified save deployment).
    6. `canonical_state`'s digest matches `state_digest(position.expected_state)`
       -- the queried-state checksum gate.
    7. `model_admission` is present and reports `ok` (the caller already ran
       `admit_model_block` for the session's initial model block).

    The returned lock includes a `session_fingerprint` computed over every
    other field, so identical inputs always produce an identical lock and
    any change to code/position/rubric/prompt/tool-schema/sampling/model
    topology changes it.

    Additive: alongside this module's own evidence structure (`digests.scorer`,
    a singular `position_id`, ...), the returned lock also carries the
    canonical top-level `scorer_fingerprint` / `positions` keys
    `benchmark_report.build_report` requires -- see the schema comment block
    immediately above `benchmark_report.build_report`. This is the only
    schema `build_report` reads; every session.json writer must satisfy it.
    """
    checkout = check_clean_checkout(wsl=wsl, windows=windows)

    if not boot_health or not boot_health.get("ok") or boot_health.get("baseline_offset") is None:
        raise GateFailure(
            "boot_health_missing_or_failed",
            {
                "boot_health": dict(boot_health) if boot_health else None,
                "message": (
                    "fresh-offset boot-health evidence is missing or reports failure; "
                    "refusing to start a session without a verified clean boot"
                ),
            },
        )

    tool_names = {(tool.get("function") or {}).get("name") for tool in tools_schema}
    if "end_turn" in tool_names:
        raise GateFailure(
            "end_turn_exposed",
            {
                "message": (
                    "resolved benchmark tool schema exposes end_turn; a trial must "
                    "never be able to advance the game turn"
                ),
            },
        )
    if FINISH_TRIAL_TOOL_NAME not in tool_names:
        raise GateFailure(
            "missing_finish_control",
            {
                "message": (
                    "resolved benchmark tool schema is missing the "
                    f"{FINISH_TRIAL_TOOL_NAME!r} control tool"
                ),
            },
        )

    digests = {
        "manifest": manifest_fingerprint,
        "schedule": schedule_fingerprint,
        "prompt": prompt_fingerprint,
        "rubric": rubric_fingerprint,
        "tool": tool_fingerprint,
        "scorer": scorer_fingerprint,
    }
    missing_digests = sorted(key for key, value in digests.items() if not value)
    if missing_digests:
        raise GateFailure(
            "missing_digest",
            {
                "missing": missing_digests,
                "message": f"session lock is missing required digest(s): {missing_digests}",
            },
        )

    if not deployment or not deployment.get("ok"):
        raise GateFailure(
            "deployment_not_verified",
            {
                "deployment": dict(deployment) if deployment else None,
                "message": (
                    "deployment evidence is missing or unverified; refusing to lock "
                    "a session on an unverified save"
                ),
            },
        )

    expected_digest = state_digest(position.expected_state)
    captured_digest = state_digest(canonical_state)
    if captured_digest != expected_digest:
        raise GateFailure(
            "canonical_state_mismatch",
            {
                "position_id": position.position_id,
                "expected_digest": expected_digest,
                "captured_digest": captured_digest,
                "message": (
                    f"canonical-state mismatch for position {position.position_id}: "
                    f"captured state digest {captured_digest} does not match expected "
                    f"state digest {expected_digest}"
                ),
            },
        )

    if not model_admission or not model_admission.get("ok"):
        raise GateFailure(
            "model_admission_not_verified",
            {
                "model_admission": dict(model_admission) if model_admission else None,
                "message": (
                    "model admission evidence is missing or unverified; refusing to "
                    "lock a session without an admitted model block"
                ),
            },
        )

    lock: dict[str, object] = {
        "position_id": position.position_id,
        "git": checkout,
        "boot_health": dict(boot_health),
        "digests": digests,
        "deployment": dict(deployment),
        "canonical_state": dict(canonical_state),
        "canonical_state_digest": captured_digest,
        "model_admission": dict(model_admission),
        "tool_names": sorted(name for name in tool_names if name),
        # Canonical keys benchmark_report.build_report requires -- see the
        # schema comment block above that function. Additive: everything
        # else on this lock is this module's own evidence structure.
        "scorer_fingerprint": scorer_fingerprint,
        "positions": {
            position.position_id: {
                "rubric": list(position.rubric),
                "objectives": list(position.objectives),
            }
        },
        "ok": True,
    }
    lock["session_fingerprint"] = fingerprint(lock)
    return lock
