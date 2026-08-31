"""Fail-closed admission gates for the controlled-position benchmark runner.

Every gate here is a pure function: it consumes evidence the caller already
gathered (git status/commit pairs, a boot-health dict, a resolved tool
schema, a `BackendProbe`, ...) and either returns JSON-safe evidence or
raises `GateFailure(code, details)`. Nothing in this module shells out to
git, touches the filesystem, calls a backend, or terminates a process --
that keeps every gate testable with plain fixtures and keeps the "fail
closed" contract simple to audit: a missing or ambiguous piece of evidence
is always a raise, never a silent default.

Six gates plus the shared exception type:

- `check_clean_checkout` -- WSL/Windows companion git hygiene.
- `check_treatment_can_fire` -- the minimal arm can reach rubric levels 1-2
  and the standard arm has the capabilities to complete every objective.
- `check_gpu_conflicts` -- conflicting GPU work is blocked unless an
  operator-approved acknowledgment names *exactly* the conflicting
  services. Never kills or drains anything itself.
- `check_tuner_holder` -- an unidentified FireTuner-port holder is blocked
  unconditionally; there is no acknowledgment path for it (unlike
  `check_gpu_conflicts`'s named-service acknowledgment) because an unknown
  process holding the tuner port has no safe remediation. Never terminates
  anything itself -- see `benchmark_live_evidence.terminate_tuner_pid` for
  the actual (exact-PID, revalidated) termination.
- `admit_model_block` -- per-model-block admission: endpoint/model identity,
  a counted backend's non-hidden retry policy, a positive briefing budget
  (only when the briefing treatment is on), proven structured tool-calling
  capability for every expected arm (`ToolCanaryEvidence`), ten warm-latency
  samples with no probe errors, seed-honoring, and the derived episode wall.
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
from civ_mcp.arena.benchmark_backend import (
    BackendProbe,
    ToolCanaryEvidence,
    episode_wall_seconds,
    nearest_rank_p95,
)
from civ_mcp.arena.benchmark_manifest import PositionManifest, fingerprint
from civ_mcp.arena.benchmark_state import state_digest

__all__ = [
    "GateFailure",
    "check_clean_checkout",
    "check_treatment_can_fire",
    "check_gpu_conflicts",
    "check_tuner_holder",
    "admit_model_block",
    "build_session_lock",
    "locked_boot_health_evidence",
    "locked_deployment_evidence",
    "locked_model_admission_evidence",
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
# check_tuner_holder
# ---------------------------------------------------------------------------


def check_tuner_holder(*, holder: Mapping[str, object] | None) -> dict[str, object]:
    """Verify the FireTuner listen port's current holder (if any) is a
    known, repo-owned civ-mcp process.

    `holder` is `None` when nothing currently holds the port -- nothing to
    check, always ok. `holder` present is evidence gathered by
    `benchmark_live_evidence.classify_tuner_holder`: a mapping carrying at
    least `pid`, `start_ticks`, `cmdline`, `cwd`, and `known_repo_owned`.

    A present holder that is not `known_repo_owned` is UNKNOWN and always
    fails closed, with no acknowledgment path (unlike `check_gpu_conflicts`'s
    named-service approval) -- an unidentified process holding the tuner
    port has no safe remediation, so this gate is the only thing standing
    between an admission run and either proceeding against it or blindly
    terminating it. Never kills or terminates anything itself; see
    `benchmark_live_evidence.terminate_tuner_pid` for the actual
    exact-PID, identity-revalidated termination path.
    """
    if holder is None:
        return {"holder": None, "ok": True}
    if not holder.get("known_repo_owned"):
        raise GateFailure(
            "unknown_tuner_holder",
            {
                "pid": holder.get("pid"),
                "cmdline": holder.get("cmdline"),
                "cwd": holder.get("cwd"),
                "message": (
                    f"FireTuner port is held by pid {holder.get('pid')!r} whose identity "
                    "does not match a known civ-mcp checkout; an unknown tuner-port holder "
                    "always blocks -- no remediation path exists for it"
                ),
            },
        )
    return {"holder": dict(holder), "ok": True}


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
    briefing_required: bool,
    briefing_budget_chars: int | None,
    tool_canaries: Mapping[str, ToolCanaryEvidence],
    expected_arm_ids: Sequence[str],
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
    - a zero, negative, or missing briefing budget -- but ONLY when
      `briefing_required` is True. This calibration runs with
      `briefing_required=False`, so `briefing_budget_chars` is expected to be
      `None` and is recorded as such in the returned evidence rather than
      enforced;
    - `tool_canaries` missing evidence for any id in `expected_arm_ids`, or
      carrying evidence where either canary (`finish_trial_ok` /
      `required_argument_ok`) did not pass -- proof that a model can emit
      structured tool calls, and specifically the exact required-argument
      shape a real trial depends on, is required for every arm before any
      trial against it is counted (see `benchmark_backend.probe_tool_capability`);
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

    # Only enforced when the treatment is actually on: this calibration runs
    # with briefing_required=False, and briefing_budget_chars is expected to
    # be None in that case -- enforcing positivity on a budget that was never
    # asked for would refuse a legitimately-off treatment.
    if briefing_required and (briefing_budget_chars is None or briefing_budget_chars <= 0):
        raise GateFailure(
            "zero_briefing_budget",
            {
                "briefing_budget_chars": briefing_budget_chars,
                "message": (
                    f"briefing budget is {briefing_budget_chars}; a counted model "
                    "block with briefing_required=True requires a positive briefing budget"
                ),
            },
        )

    missing_arm_ids = sorted(
        arm_id for arm_id in expected_arm_ids if arm_id not in tool_canaries
    )
    if missing_arm_ids:
        raise GateFailure(
            "missing_tool_canary",
            {
                "missing_arm_ids": missing_arm_ids,
                "message": (
                    "tool-canary evidence is missing for arm(s) "
                    f"{missing_arm_ids}; every expected arm must be probed for "
                    "structured tool-calling capability "
                    "(benchmark_backend.probe_tool_capability) before a model "
                    "block can be admitted"
                ),
            },
        )

    failed_arms: dict[str, object] = {}
    for arm_id in expected_arm_ids:
        canary = tool_canaries[arm_id]
        if not (canary.finish_trial_ok and canary.required_argument_ok):
            failed_arms[arm_id] = {
                "finish_trial_ok": canary.finish_trial_ok,
                "required_argument_ok": canary.required_argument_ok,
                "errors": list(canary.errors),
            }
    if failed_arms:
        raise GateFailure(
            "tool_canary_failed",
            {
                "failed_arms": failed_arms,
                "message": (
                    f"tool-canary probe failed for arm(s) {sorted(failed_arms)}; a "
                    "model block must not be admitted without proven structured "
                    "tool-calling capability (including the exact required-argument "
                    "shape) for every arm"
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

    # F15 ruling: at temperature == 0, seed honoring is unobservable and
    # irrelevant (greedy decoding) -- probe_backend records seed_verdict
    # "not_applicable_greedy" for that config instead of running the
    # differing-seed check, so seed_honored is necessarily False there.
    # Accept that verdict rather than fail-closed; any other config keeps
    # the existing fail-closed behavior.
    if (
        sampling.seed is not None
        and not probe.seed_honored
        and probe.seed_verdict != "not_applicable_greedy"
    ):
        raise GateFailure(
            "seed_not_honored",
            {
                "seed": sampling.seed,
                "seed_verdict": probe.seed_verdict,
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
        "briefing_required": briefing_required,
        "briefing_budget_chars": briefing_budget_chars,
        "tool_canaries": {
            arm_id: {
                "arm_id": tool_canaries[arm_id].arm_id,
                "finish_trial_ok": tool_canaries[arm_id].finish_trial_ok,
                "required_argument_ok": tool_canaries[arm_id].required_argument_ok,
                "observed_calls": list(tool_canaries[arm_id].observed_calls),
                "errors": list(tool_canaries[arm_id].errors),
            }
            for arm_id in expected_arm_ids
        },
        "ok": True,
    }


# ---------------------------------------------------------------------------
# build_session_lock
# ---------------------------------------------------------------------------


def locked_boot_health_evidence(boot_health: Mapping[str, object]) -> dict[str, object]:
    """The session lock's own record of the boot-health gate: proof it
    passed, never the volatile per-boot timings/frame counts/file identity
    that differ on every real boot and would otherwise turn every real
    resume into a spurious `locked_identity_changed` refusal. The full,
    volatile evidence still lives in the numbered admission attempt file
    (see `benchmark_admission.py`) -- never in this byte-compared,
    resume-reused lock."""
    return {"verified": True}


def locked_deployment_evidence(deployment: Mapping[str, object]) -> dict[str, object]:
    """The session lock's own record of the deploy gate: which save was
    deployed and that its hash chain verified end-to-end -- these ARE
    stable code/position identity, unlike the volatile filesystem
    destination path a re-deploy could land on differently each time."""
    return {
        "ok": bool(deployment.get("ok")),
        "save_name": deployment.get("save_name"),
        "archive_sha256": deployment.get("archive_sha256"),
        "deployed_sha256": deployment.get("deployed_sha256"),
        "expected_sha256": deployment.get("expected_sha256"),
    }


def locked_model_admission_evidence(model_admission: Mapping[str, object]) -> dict[str, object]:
    """The session lock's own record of `admit_model_block`'s evidence:
    model/endpoint identity, GPU topology, and locked sampling/retry/
    briefing config -- never the volatile per-attempt warm-latency
    samples, their derived p95, the live seed-honoring verdict, or raw
    tool-canary transcripts (a fresh probe's exact timings/output text
    differ on every attempt even against an unchanged model/endpoint).
    Proven tool-calling capability per arm (pass/fail only, not the raw
    observed calls or probe errors) IS kept -- that boolean outcome is
    part of this locked session's model-capability identity, not a timing
    measurement."""
    tool_canaries = model_admission.get("tool_canaries") or {}
    locked_canaries = {
        arm_id: {
            "finish_trial_ok": evidence.get("finish_trial_ok"),
            "required_argument_ok": evidence.get("required_argument_ok"),
        }
        for arm_id, evidence in tool_canaries.items()
    }
    return {
        "requested_model": model_admission.get("requested_model"),
        "resolved_model": model_admission.get("resolved_model"),
        "requested_endpoint": model_admission.get("requested_endpoint"),
        "resolved_endpoint": model_admission.get("resolved_endpoint"),
        "registry_fingerprint": model_admission.get("registry_fingerprint"),
        "gpu_topology": model_admission.get("gpu_topology"),
        "sampling": model_admission.get("sampling"),
        "retry_policy": model_admission.get("retry_policy"),
        "briefing_required": model_admission.get("briefing_required"),
        "briefing_budget_chars": model_admission.get("briefing_budget_chars"),
        "tool_canaries": locked_canaries,
    }


def build_session_lock(
    *,
    position: PositionManifest,
    wsl: Mapping[str, object],
    windows: Mapping[str, object],
    boot_health: Mapping[str, object] | None,
    campaign_fingerprint: str,
    block_id: str,
    model_config: Mapping[str, object],
    schedule_fingerprint: str,
    admission_fingerprint: str,
    tool_surface_fingerprint: str,
    tool_input_fingerprint: str,
    scorer_fingerprint: str,
    episode_wall_s: int,
    tools_schema: Sequence[Mapping[str, Any]],
    deployment: Mapping[str, object] | None,
    canonical_state: Mapping[str, object],
    model_admission: Mapping[str, object] | None,
) -> dict[str, object]:
    """Assemble one immutable, JSON-safe, deterministically-fingerprinted
    per-model-block lock -- this IS `session.json` (evolved; no third lock
    artifact is ever introduced), or raise `GateFailure` if any piece of
    admission evidence fails closed.

    Checks, in order:

    1. `check_clean_checkout(wsl, windows)` -- dirty tree / commit mismatch.
    2. `boot_health` is present, `ok`, and carries a fresh baseline offset
       (a missing/failed boot-health poll must never be silently skipped).
    3. `tools_schema` never exposes `end_turn` and always exposes
       `finish_trial` (the benchmark control tool).
    4. `campaign_fingerprint` is present -- a counted block lock must always
       reference the immutable campaign it belongs to (see
       `benchmark_campaign.build_campaign_lock`); this is checked separately
       from the other digests below because a missing campaign reference is
       categorically worse than a missing schedule/tool/scorer digest: it
       means this lock cannot be told apart from an uncounted/smoke run at
       all.
    5. Every remaining digest (schedule/tool_surface/tool_input/scorer/
       admission) is present, and `block_id`/`model_config` are present.
    6. `deployment` is present and reports `ok` (a verified save deployment).
    7. `canonical_state`'s digest matches `state_digest(position.expected_state)`
       -- the queried-state checksum gate.
    8. `model_admission` is present and reports `ok` (the caller already ran
       `admit_model_block` for this session's model block).

    The returned lock includes a `session_fingerprint` computed over every
    other field, so identical inputs always produce an identical lock and
    any change to code/position/rubric/prompt/tool-surface/tool-input/
    sampling/model topology changes it. `campaign_fingerprint` itself is
    just one more field folded into that computation -- a description-only
    schema edit changes `tool_input_fingerprint` (hence `session_fingerprint`)
    while leaving `campaign_fingerprint` untouched (tool surface identity is
    campaign evidence; exact input schemas are block-admission evidence
    only -- see `benchmark_contract`'s module docstring).

    The lock's own `boot_health`/`deployment`/`model_admission` fields are
    deliberately TRIMMED to locked-identity evidence only (see
    `locked_boot_health_evidence` / `locked_deployment_evidence` /
    `locked_model_admission_evidence`) -- the gate CHECKS above still run
    against the full evidence the caller passes in, but volatile
    per-attempt measurements (boot timings/frame counts/file identity, a
    deploy's filesystem destination path, warm-latency samples and their
    p95, the live seed-honoring verdict, raw tool-canary transcripts) are
    never folded into `session_fingerprint`. Embedding them would mean a
    resumed admission attempt -- which re-runs every live gate from
    scratch and necessarily observes different timings each time -- could
    never byte-match the recorded lock, turning ordinary resume into a
    spurious `locked_identity_changed` refusal on every attempt. The full,
    volatile evidence is not lost -- it lives in the numbered admission
    attempt file (`benchmark_admission.py`), never in this lock.
    `episode_wall_s` is the one derived-from-volatile-evidence value that
    DOES stay in the lock: it is computed once, at first admission, and
    the caller (`benchmark_admission.AdmissionPipeline`) is responsible
    for reusing that already-locked value on every subsequent resume
    attempt rather than passing a freshly re-derived one.

    Deliberately does NOT carry `manifest_fingerprint`/`prompt_fingerprint`/
    `rubric_fingerprint` as separate top-level digests any more: that shared
    campaign-wide content is now referenced through `campaign_fingerprint`
    instead of duplicated here. `position` (and its embedded rubric/
    objectives, below) is still retained so this lock can be scored
    standalone, without needing `campaign.json` at hand.

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

    # A counted block lock must always reference the immutable campaign it
    # belongs to -- checked ahead of (and separately from) the other
    # digests below because a missing campaign_fingerprint is categorically
    # worse than a missing schedule/tool/scorer digest: without it this
    # lock is indistinguishable from an ungated/smoke run, and
    # BenchmarkStore.is_trial_complete would silently stop demanding the
    # second stamp on every committed trial.
    if not campaign_fingerprint:
        raise GateFailure(
            "missing_campaign_fingerprint",
            {
                "message": (
                    "session lock is missing a non-empty campaign_fingerprint; a "
                    "counted per-model-block lock must always reference the "
                    "immutable campaign it belongs to"
                ),
            },
        )

    digests = {
        "schedule": schedule_fingerprint,
        "tool_surface": tool_surface_fingerprint,
        "tool_input": tool_input_fingerprint,
        "scorer": scorer_fingerprint,
        "admission": admission_fingerprint,
    }
    missing_digests = sorted(key for key, value in digests.items() if not value)
    if not block_id:
        missing_digests.append("block_id")
    if not model_config:
        missing_digests.append("model_config")
    missing_digests.sort()
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
        "block_id": block_id,
        "campaign_fingerprint": campaign_fingerprint,
        "model_config": dict(model_config),
        "episode_wall_s": episode_wall_s,
        "git": checkout,
        "boot_health": locked_boot_health_evidence(boot_health),
        "digests": digests,
        "deployment": locked_deployment_evidence(deployment),
        "canonical_state": dict(canonical_state),
        "canonical_state_digest": captured_digest,
        "model_admission": locked_model_admission_evidence(model_admission),
        "tool_names": sorted(name for name in tool_names if name),
        # Canonical keys benchmark_report.build_report requires -- see the
        # schema comment block above that function. Additive: everything
        # else on this lock is this module's own evidence structure.
        "scorer_fingerprint": scorer_fingerprint,
        "schedule_fingerprint": schedule_fingerprint,
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
