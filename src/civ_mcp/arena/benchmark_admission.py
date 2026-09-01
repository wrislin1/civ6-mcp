"""Integrated admission pipeline: wire Tasks 3-7 into one fail-closed
orchestrator that runs immediately before every counted model block.

Every other module in this package is a piece: `benchmark_campaign`'s
`CampaignStore`/`build_campaign_lock`/`compile_campaign_schedule` (the
immutable, shared campaign identity and its two-level artifact layout);
`benchmark_runner`'s `ResolvedBlock`/`run_resolved_block` (the trusted,
already-tested trial loop this module hands off to and never replicates);
`benchmark_backend`'s `probe_backend`/`probe_tool_capability` (model/tool
evidence); `benchmark_deploy`'s boot-health/deploy evidence;
`benchmark_live_evidence`'s checkout/tuner/GPU collectors; and
`benchmark_gates`'s pure fail-closed checks (`check_clean_checkout`,
`check_treatment_can_fire`, `admit_model_block`, `build_session_lock`).
`AdmissionPipeline` is the one place that calls all of them, in a fixed,
locked order, for one model block, and either mints a fresh/reused
`session.json` and hands back a `ResolvedBlock` ready for
`run_resolved_block`, or refuses -- never both.

Locked gate order (see `GATE_ORDER`, and
`AdmissionPipeline.admit`'s docstring for exactly what evidence flows into
each): clean checkout; boot health; treatment-can-fire (static, so it runs
before any expensive live gate -- E7, external review wave E); tuner
holder; save deploy; production reload; popup hygiene; canonical state;
GPU isolation; model admission (endpoint/model identity + both tool
canaries per arm + seed/latency, all inside one `admit_model_block` call);
session lock creation. `run_resolved_block` itself is deliberately NOT called by
`admit()` -- `admit()`'s job ends at minting (or reusing) the session lock;
the caller (the CLI, or a test) decides whether/when to actually run the
returned `ResolvedBlock`.

Every gate result is journaled to `campaign-journal.jsonl` as it happens,
and the complete diagnostic (every gate's evidence, in order, plus the
final outcome) is written to the next numbered
`admissions/<block-id>-attempt-NNN.json` via `CampaignStore.record_admission`
-- exactly once per `admit()` call, on every exit path (success or
failure), so a resume or a post-mortem always has the full picture of what
was checked and what was found.

Three modes, all sharing the identical gate sequence:

- `mode="counted"`: on all-green evidence, mints (or reuses -- see below)
  the per-block `session.json` via `CampaignStore.open_block` and returns a
  `ResolvedBlock`. This is the only mode that ever creates a reusable
  session or makes a `campaign_fingerprint`/`admission_fingerprint` pair
  that a trial could stamp itself with.
- `mode="admit_only"`: runs every gate through session-lock construction
  (proving a session COULD be minted) but never calls
  `CampaignStore.open_block` -- diagnostics only, never a reusable session.
- `mode="validation"`: the same gate sequence, but the diagnostic record is
  written under `<campaign root>/validation/` (never `admissions/`) with an
  explicit `validation_stamp`, and its `campaign_fingerprint`/
  `admission_fingerprint` fields are always recorded as `None` regardless
  of what the gates actually computed -- a non-counting validation pass
  must never mint (or even resemble) the counted fingerprint pair.

Resume semantics: every `admit()` call re-runs every gate from scratch --
there is no cross-attempt caching of live evidence (see
`test_second_model_gets_fresh_checkout_gpu_endpoint_and_canary_evidence`
and `test_resume_reuses_campaign_lock_but_reacquires_block_admission`).
What CAN be reused is the on-disk `session.json` itself:
`CampaignStore.open_block` (which wraps `BenchmarkStore.create`) reattaches
to an existing block run directory only when the freshly-built candidate
session lock matches the recorded one byte-for-byte; a changed locked
identity (model, endpoint, topology, sampling, schema, code/position, ...)
raises `CampaignLockMismatchError`, which `admit()` turns into an
`AdmissionError` -- resume blocks rather than reminting a lock over
existing trials.
"""
from __future__ import annotations

import dataclasses
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Sequence

from civ_mcp.arena.benchmark_agent import resolved_benchmark_tools
from civ_mcp.arena.benchmark_backend import BackendProbe, ToolCanaryEvidence
from civ_mcp.arena.benchmark_campaign import BenchmarkCampaignError, CampaignLockMismatchError, CampaignStore
from civ_mcp.arena.benchmark_contract import (
    CampaignManifest,
    ModelBlockConfig,
    fingerprint_identity,
    suite_for_block,
    tool_input_identity,
)
from civ_mcp.arena.benchmark_gates import (
    GateFailure,
    admit_model_block,
    build_session_lock,
    check_clean_checkout,
    check_treatment_can_fire,
    locked_model_admission_evidence,
)
from civ_mcp.arena.benchmark_manifest import PositionManifest, fingerprint
from civ_mcp.arena.benchmark_schedule import TrialSpec
from civ_mcp.arena.benchmark_state import state_digest
from civ_mcp.arena.benchmark_store import (
    BenchmarkStore,
    SessionLockMismatchError,
    TrialProvenanceError,
    canonical_json_bytes,
    compute_session_fingerprint,
)
from civ_mcp.arena.registry import TOOL_REGISTRY, resolve_tools

# benchmark_runner imports ResolvedBlock/run_resolved_block: importing it
# here at module scope would be circular the moment benchmark_runner's own
# CLI wants to import this module back (it does, for --campaign). Only the
# ResolvedBlock TYPE is needed here, purely for the return annotation and
# for constructing one -- a local import inside admit() would work too, but
# importing once at module scope from the *type-only* side is fine because
# benchmark_runner does NOT import benchmark_admission at its own module
# scope (only inside its CLI functions); see benchmark_runner.py's
# _run_campaign_async.
from civ_mcp.arena.benchmark_runner import ResolvedBlock

__all__ = [
    "GATE_ORDER",
    "ADMISSION_MODES",
    "REPLICATION_DEFERRED_ADMISSION",
    "REPLICATION_DEFERRAL_ELIGIBLE_CODES",
    "AdmissionError",
    "AdmissionDependencies",
    "CampaignBundle",
    "AdmissionPipeline",
    "build_campaign_bundle",
    "block_is_complete",
    "select_next_incomplete_block",
    "classify_admission_disposition",
    "record_admission_disposition",
    "record_remediation_attempt",
]

GATE_ORDER: tuple[str, ...] = (
    "clean_checkout",
    "boot_health",
    # E7 (external review wave E): treatment_can_fire is a purely static
    # manifest+registry check (its inputs are fixed at import/authoring
    # time -- see _minimal_observation/_standard_capabilities) and runs
    # right after the cheap checkout/boot-health evidence, BEFORE the
    # expensive live gates (save deploy, live reload, GPU snapshot over
    # SSH, and the billed backend/canary probes). It previously sat second-
    # to-last, spending all of that on a campaign whose position could
    # never fire its treatment.
    "treatment_can_fire",
    "tuner_holder",
    "save_deploy",
    "production_reload",
    "popup_hygiene",
    "canonical_state",
    "gpu_isolation",
    "model_admission",
    "session_lock",
)

ADMISSION_MODES = frozenset({"counted", "admit_only", "validation"})

# Task 10's job is the final campaign disposition; this module only exposes
# the typed failure so it's there to expose -- see classify_admission_disposition.
REPLICATION_DEFERRED_ADMISSION = "REPLICATION_DEFERRED_ADMISSION"

# Ruling G (external review wave D, finding D2): deferral eligibility is an
# explicit ALLOWLIST of model-capability gate failure codes -- exactly the
# codes proving the MODEL/BACKEND failed a capability gate: the endpoint/
# model-identity gate, the tool-canary gate, and the backend pre-flight
# probe (all raised by benchmark_gates.admit_model_block). Every other
# classified code -- dirty_checkout, stale/unknown tuner holders, GPU
# conflicts, boot/deploy/reload/popup/canonical-state failures, config
# errors (counted_backend_hidden_retries, zero_briefing_budget),
# position-authoring errors (malformed_rubric_level,
# undeclared_objective_requirements, minimal_observation_not_validated,
# and -- G5, external review wave G -- treatment_cannot_fire, which derives
# purely from the static position manifest vs the static tool registry and
# is model-independent), and -- G2 (Ruling H), external review wave G --
# auth/transport failures (backend_auth_error, backend_transport_error:
# a stale key, wrong endpoint, down gateway, or 429 storm is never model-
# capability evidence) -- is an operator/environment/authoring problem
# that must be FIXED, never converted into a
# REPLICATION_DEFERRED_ADMISSION. Enforced BOTH at write time
# (benchmark_runner._run_campaign_async refuses to record the disposition)
# and at report time
# (benchmark_campaign_report._has_valid_replication_deferred_admission
# never honors a disposition whose underlying code is outside this set --
# the two frozensets are asserted equal by
# tests/arena/test_benchmark_admission.py).
REPLICATION_DEFERRAL_ELIGIBLE_CODES = frozenset(
    {
        # endpoint/model-identity gate (admit_model_block)
        "endpoint_identity_mismatch",
        # tool-canary gate (admit_model_block)
        "missing_tool_canary",
        "tool_canary_failed",
        # backend pre-flight probe failures (admit_model_block over probe_backend)
        "insufficient_warm_latency_samples",
        "backend_probe_errors",
        "seed_not_honored",
    }
)


class AdmissionError(Exception):
    """Raised by `AdmissionPipeline.admit` on any fail-closed refusal --
    wraps the underlying `GateFailure` / `CampaignLockMismatchError`
    (`__cause__` is always set). `code`/`details` mirror `GateFailure`'s
    shape so callers can handle both uniformly. An attempt is always made
    to write the complete diagnostic for this attempt before this is
    raised; if that write itself fails (A8: e.g. a full disk), the write's
    exception is recorded on `diagnostic_write_error` rather than replacing
    this exception -- the real gate failure this exception carries must
    always be what a caller actually sees and handles."""

    def __init__(self, code: str, details: Mapping[str, object]):
        self.code = code
        self.details = dict(details)
        self.diagnostic_write_error: str | None = None
        super().__init__(str(self.details.get("message", code)))


@dataclasses.dataclass(frozen=True)
class AdmissionDependencies:
    """Everything `AdmissionPipeline` needs injected, so its orchestration
    is testable with plain async fakes and never itself shells out, opens a
    socket, or touches a live game/backend.

    Every callable here is expected to return (or raise) exactly what the
    matching Task 3-7 collector/gate already returns/raises for that piece
    of evidence -- this dataclass adds no new evidence shape of its own,
    only the calling convention `AdmissionPipeline` uses:

    - `checkout_evidence()` -> `{"wsl": {...}, "windows": {...}}` (see
      `benchmark_live_evidence.collect_checkout_evidence`).
    - `boot_health()` -> boot-health evidence (see
      `benchmark_deploy.check_boot_health_via_windows`, `asdict`-ed).
    - `tuner_evidence()` -> tuner-holder evidence, ALREADY run through
      `check_tuner_holder` by the dependency itself (see
      `benchmark_live_evidence.collect_tuner_evidence`) -- raises
      `GateFailure` on an unknown holder rather than returning it.
    - `deploy_save(position)` -> deployment evidence (see
      `benchmark_deploy.deploy_via_windows`, `asdict`-ed).
    - `reload_and_capture(position)` (async) -> one combined production
      readiness probe: ``{"reload": {"verified": bool, ...}, "popup_hygiene":
      {"ok": bool, ...}, "canonical_state": {...}}``. This folds the
      "production reload; popup hygiene; canonical state" trio of the
      locked gate order into one live round-trip (reload the position,
      dismiss popups, capture the canonical state) since all three are
      inseparable steps of the same live reconnect.
    - `gpu_evidence(endpoint_id)` -> GPU-conflict evidence for exactly this
      endpoint's GPU(s), ALREADY run through `check_gpu_conflicts` by the
      dependency itself (see `benchmark_live_evidence.collect_gpu_evidence`
      + `gpu_processes_to_conflict_rows`) -- raises `GateFailure` on any
      unapproved conflict.
    - `resolve_endpoint(endpoint_id)` -> ``{"requested_endpoint": str,
      "resolved_endpoint": str, "registry_fingerprint": str,
      "gpu_topology": {...}}``. `requested_endpoint` is always just
      `endpoint_id` echoed back (the dependency's own contract, not
      computed by `AdmissionPipeline`) -- keeping both on the return value
      lets a fake simulate a registry resolving one endpoint id to
      unexpectedly different topology/URL evidence without
      `AdmissionPipeline` having to know what "expected" means.
    - `probe_backend(...)` (async) -> `BackendProbe` (see
      `benchmark_backend.probe_backend`) -- called once per block, using
      the broadest (standard-tier) resolved tool schema. Called with
      `sampling=block.sampling` and `chat_template_kwargs=
      block.chat_template_kwargs` (in addition to `model`/`endpoint`/
      `tools`) so the identity/seed/latency probe runs under the block's
      exact locked inference configuration rather than some other default
      -- a Qwen thinking/token misconfiguration must fail admission, not
      calibration (spec Sec 7).
    - `probe_tool_capability(...)` (async) -> `ToolCanaryEvidence` (see
      `benchmark_backend.probe_tool_capability`) -- called once per arm,
      also with `sampling=block.sampling` and `chat_template_kwargs=
      block.chat_template_kwargs` for the same reason: both structured
      tool-call canaries must exercise the model under the exact sampling,
      token limit, and chat template the counted trials will actually use.
    """

    checkout_evidence: Callable[..., dict]
    boot_health: Callable[..., dict]
    tuner_evidence: Callable[..., dict]
    deploy_save: Callable[..., dict]
    reload_and_capture: Callable[..., Awaitable[dict]]
    gpu_evidence: Callable[..., dict]
    resolve_endpoint: Callable[..., dict]
    probe_backend: Callable[..., Awaitable[BackendProbe]]
    probe_tool_capability: Callable[..., Awaitable[ToolCanaryEvidence]]


@dataclasses.dataclass(frozen=True)
class CampaignBundle:
    """Everything about the frozen campaign `AdmissionPipeline.admit` needs
    besides the one block being admitted and the `CampaignStore` it is
    admitted against.

    `tools_by_arm` is `arm_id -> resolved tool schemas`, computed once (not
    per admission attempt -- resolved tool schemas are a pure function of
    `arm.tools` and never change between attempts) via
    `build_campaign_bundle`.
    """

    manifest: CampaignManifest
    position: PositionManifest
    tools_by_arm: Mapping[str, tuple[Mapping[str, object], ...]]


def build_campaign_bundle(manifest: CampaignManifest, position: PositionManifest) -> CampaignBundle:
    """Resolve every arm's tool schema once and bundle it with the campaign
    manifest and position manifest `AdmissionPipeline.admit` needs."""
    tools_by_arm = {
        arm.arm_id: tuple(resolved_benchmark_tools(arm.tools)) for arm in manifest.arms
    }
    return CampaignBundle(manifest=manifest, position=position, tools_by_arm=tools_by_arm)


def _standard_capabilities() -> set[str]:
    """Capability ids the standard tool tier actually grants: the tool
    NAMES resolved for the standard tier (e.g. `repair_improvement`,
    matching the `objective["requires"]` vocabulary used throughout
    `benchmark_gates`'s own tests), unioned with the narrower feature-gate
    ids a handful of those tools additionally declare via
    `ToolDef.requires` (e.g. `gp_unit`, `spies`) for objectives that name a
    feature-gate rather than a bare tool name. Both are derived statically
    from `civ_mcp.arena.registry` -- fixed at import time, not live
    evidence that could drift between admission attempts, so no dependency
    call gathers it."""
    names = resolve_tools("standard")
    capabilities = set(names)
    capabilities.update(TOOL_REGISTRY[name].requires for name in names if TOOL_REGISTRY[name].requires)
    return capabilities


def _minimal_observation(position: PositionManifest) -> dict[str, object]:
    """`check_treatment_can_fire`'s minimal-arm evidence.

    B5 (external review wave B): this used to return
    `{"discoverable_task_ids": sorted({entry["task_id"] for entry in
    position.rubric})}` -- derived FROM the rubric's own answer, so the
    "minimal arm can reach rubric levels 1-2" half of
    `check_treatment_can_fire` could never fail (it was always checking the
    rubric against itself). The minimal tool tier restricts ACTION
    capability, never read/observation capability -- every read/query tool
    ships on every tier (see `civ_mcp.arena.registry.resolve_tools`) -- so
    reachability genuinely IS an authoring-time property of the position
    manifest, not something a live probe can meaningfully re-derive on
    every attempt. That authoring-time proof now actually happens:
    `benchmark_manifest.validate_position_contract` (run by every
    `load_position_manifest` call) asserts that every rubric task's
    level-1/2 predicates reference only entities present in the manifest's
    own declared observable state (`relevant_tiles` / `expected_state`
    units) -- a rubric task referencing an undeclared entity fails to even
    load as a position manifest.

    This function's only remaining job is to record that this authoring-
    time check actually ran for `position` (a static fact, not a live
    probe) -- `check_treatment_can_fire` fails closed if it doesn't see
    this marker, so the lock never implies a live reachability probe
    happened when none did.
    """
    return {"source": "authoring_validation"}


def _union_tool_schemas(
    tools_by_arm: Mapping[str, Sequence[Mapping[str, object]]]
) -> list[dict[str, object]]:
    """Every arm's resolved schema, deduplicated by tool name, for
    `build_session_lock`'s end_turn/finish_trial exposure check -- checking
    the union catches a violation in EITHER arm's schema, not just one."""
    seen: dict[str, dict[str, object]] = {}
    for schemas in tools_by_arm.values():
        for schema in schemas:
            name = (schema.get("function") or {}).get("name")
            if name and name not in seen:
                seen[name] = dict(schema)
    return list(seen.values())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_campaign_journal(store: CampaignStore, event: str, **fields: object) -> None:
    """Append one line to this campaign's `campaign-journal.jsonl`.

    `CampaignStore` (benchmark_campaign.py, out of this task's file scope)
    exposes no journal-append method of its own -- it only ensures the file
    exists (see `CampaignStore._open_or_create`) -- so this module owns its
    own tiny, direct append, mirroring `BenchmarkStore.append_event`'s
    canonical-line-per-event convention.
    """
    record: dict[str, object] = {"ts": _utc_now_iso(), "event": event, **fields}
    line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    journal_path = store.root / CampaignStore.JOURNAL_FILE
    with open(journal_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _write_validation_record(store: CampaignStore, block_id: str, evidence: Mapping[str, object]) -> Path:
    """Write one non-counting validation diagnostic under
    `<campaign root>/validation/` -- never under `admissions/`, and never
    carrying a real campaign_fingerprint/admission_fingerprint pair (the
    caller is responsible for having already nulled those on `evidence`).

    A9: matches `CampaignStore.record_admission`'s append-only,
    never-overwrite discipline -- the ordinal is derived from a plain
    on-disk count (not a monotonic counter), so a gap in the numbered
    sequence (e.g. left by an earlier interrupted write, or a concurrent
    writer) can make a freshly-computed ordinal collide with a file that
    already exists. `record_admission` already refuses rather than
    silently overwriting in that case; this function did not, and could
    clobber an existing validation record. Raises `BenchmarkCampaignError`
    on that collision instead of ever calling `os.replace` over it.
    """
    validation_dir = store.root / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{block_id}-validation-"
    ordinal = (
        sum(
            1
            for path in validation_dir.iterdir()
            if path.is_file() and path.name.startswith(prefix) and path.name.endswith(".json")
        )
        + 1
    )
    dest = validation_dir / f"{prefix}{ordinal:03d}.json"
    tmp = validation_dir / f".{prefix}{ordinal:03d}.json.tmp"
    payload = dict(evidence)
    payload["validation_stamp"] = True
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    if dest.exists():
        tmp.unlink(missing_ok=True)
        raise BenchmarkCampaignError(
            f"validation attempt already recorded at {dest}; _write_validation_record never "
            "overwrites a prior attempt"
        )
    os.replace(tmp, dest)
    return dest


def _existing_locked_episode_wall_s(store: CampaignStore, block_id: str) -> int | None:
    """If `block_id` already has a recorded `session.json`, return its
    locked `episode_wall_s` -- `build_session_lock`'s contract (see its
    docstring) is that this value is derived once, at first admission, and
    every subsequent admission attempt (including every resume, which
    re-runs the full live probe and necessarily observes different
    latencies) must reuse that already-locked value rather than passing a
    freshly re-derived one. `None` only when no prior session exists yet
    (first admission for this block), the existing file cannot be read/
    parsed, or -- H8 (external review wave H) -- the recorded
    session.json fails G1's `compute_session_fingerprint` verification
    (its session_fingerprint is not the fingerprint of its own remaining
    contents): a value read out of a tampered lock must never be reused as
    the locked wall. Returning `None` (matching this function's existing
    missing/unreadable semantics) rather than raising is still fail-closed
    end to end: the caller then derives a FRESH wall and mints a fresh
    self-fingerprinted lock, and `BenchmarkStore.create`'s byte-for-byte
    session.json comparison refuses to reattach over the tampered file --
    the tamper surfaces loudly there, on the write path, instead of being
    silently laundered into a reused `episode_wall_s` here.

    I4 (external review wave I): what this catches is UN-re-minted tamper
    only. A RE-MINTED tamper -- `episode_wall_s` edited AND
    `session_fingerprint` recomputed over the edited document via the
    public `compute_session_fingerprint` -- verifies here and reattaches
    cleanly, and is out of read-time reach by construction: every stamp
    is computable from public pure functions. That residual is anchored
    outside the filesystem -- see the threat-model boundary note in
    `benchmark_campaign_report`'s module docstring (the published
    campaign contract and the hash-bound live human audits are the
    anchors for live provenance)."""
    session_path = store.root / CampaignStore.BLOCKS_DIR / block_id / "session.json"
    if not session_path.is_file():
        return None
    try:
        existing = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(existing, dict):
        return None
    # H8: same G1 verification block_is_complete applies before trusting
    # any recorded session field.
    if not existing.get("session_fingerprint") or (
        compute_session_fingerprint(existing) != existing.get("session_fingerprint")
    ):
        return None
    value = existing.get("episode_wall_s")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def record_admission_disposition(
    store: CampaignStore, block_id: str, disposition: str, underlying_failure: Mapping[str, object]
) -> Path:
    """Persist a typed admission disposition (e.g.
    `REPLICATION_DEFERRED_ADMISSION`) as its own numbered admission-attempt
    record for `block_id`, referencing the underlying failure that
    triggered it.

    This is what makes the disposition discoverable from disk: `admit()`
    already wrote its own failed-attempt record for the gate that actually
    failed; this is a SEPARATE, subsequent record in the same append-only,
    numbered `admissions/<block-id>-attempt-NNN.json` sequence, carrying
    the disposition and a reference back to that failure -- scanning the
    sequence for a `disposition` key reconstructs the classification
    without any out-of-band state. Never used for `REPLICATION_DEFERRED_ADMISSION`
    on the first model's own block -- see `classify_admission_disposition`.
    """
    record = {
        "block_id": block_id,
        "disposition": disposition,
        "underlying_failure": dict(underlying_failure),
    }
    return store.record_admission(block_id, record)


def record_remediation_attempt(
    store: CampaignStore, block_id: str, action: str, result: Mapping[str, object]
) -> Path:
    """Persist one remediation invocation (`terminate_tuner_pid`,
    `drain_gpu_service`, ...) as its own numbered admission-attempt record
    for `block_id` -- so "all remediation attempts" for a block is
    reconstructible from disk by scanning
    `admissions/<block-id>-attempt-*.json` for a `remediation` key, in the
    same append-only, numbered sequence as ordinary admission attempts and
    disposition records."""
    record = {"block_id": block_id, "remediation": action, "result": dict(result)}
    return store.record_admission(block_id, record)


class AdmissionPipeline:
    """Runs the locked gate sequence (`GATE_ORDER`) for one model block,
    immediately before it runs. See the module docstring for the three
    modes and the resume-reuse contract.
    """

    def __init__(self, dependencies: AdmissionDependencies) -> None:
        self._deps = dependencies

    async def admit(
        self,
        campaign: CampaignBundle,
        block: ModelBlockConfig,
        store: CampaignStore,
        *,
        mode: str,
        api_key: str = "x",
    ) -> ResolvedBlock | dict:
        """Admit `block` against `store` (an already created/opened
        `CampaignStore`) in `mode`.

        Returns a `ResolvedBlock` only for `mode="counted"` on success.
        Every other outcome (any mode's diagnostics, or a counted success
        short of session-lock reuse) returns/raises instead -- see the
        module docstring. Raises `AdmissionError` on the first failed gate,
        always after writing the complete diagnostic for this attempt.
        """
        if mode not in ADMISSION_MODES:
            raise ValueError(f"unknown admission mode {mode!r}; expected one of {sorted(ADMISSION_MODES)}")

        gates: dict[str, dict[str, object]] = {}
        evidence: dict[str, object] = {"block_id": block.block_id, "mode": mode, "gates": gates}

        def _record_gate(name: str, result: Mapping[str, object]) -> None:
            gates[name] = dict(result)
            _append_campaign_journal(
                store, "admission_gate", gate=name, block_id=block.block_id, mode=mode
            )

        def _fail(code: str, details: Mapping[str, object]) -> AdmissionError:
            """Record a complete diagnostic for this failed attempt and
            return (never raises itself -- callers `raise _fail(...) from
            exc`) an `AdmissionError`. Mode-aware: a `mode="validation"`
            failure is written through the SAME validation-record path a
            success would use (never `admissions/`, never consuming a
            counted-attempt ordinal) -- a validation failure must never
            look, on disk, like a counted admission attempt.

            A8: the diagnostic write itself is best-effort from the
            caller's point of view -- if it raises (a full disk, a
            filesystem error, an ordinal collision), that write failure
            must never REPLACE the `AdmissionError` this function returns
            for the gate that actually failed. The write's exception is
            recorded on `diagnostic_write_error` instead, and this always
            returns (never raises) the real `AdmissionError`.
            """
            evidence["ok"] = False
            evidence["failure"] = {"code": code, "details": dict(details)}
            admission_error = AdmissionError(code, evidence["failure"])
            write_errors: list[str] = []
            # E2 (external review wave E, A8 completion): the journal append
            # during failure-recording is itself a persistence write and
            # gets the same protection as the diagnostic record below -- a
            # full-disk OSError here must never replace the real
            # AdmissionError with a raw traceback. Guarded separately from
            # the record write so a failed journal line never prevents the
            # numbered diagnostic record from being attempted (and vice
            # versa).
            try:
                _append_campaign_journal(
                    store, "admission_failed", block_id=block.block_id, mode=mode, code=code
                )
            except Exception as journal_exc:  # noqa: BLE001 - deliberate: see docstring above
                write_errors.append(f"journal append failed: {journal_exc!r}")
            try:
                if mode == "validation":
                    _write_validation_record(store, block.block_id, evidence)
                else:
                    self._write_record(store, block.block_id, evidence)
            except Exception as write_exc:  # noqa: BLE001 - deliberate: see docstring above
                write_errors.append(f"diagnostic record write failed: {write_exc!r}")
            if write_errors:
                admission_error.diagnostic_write_error = "; ".join(write_errors)
            return admission_error

        try:
            # 1. clean checkout
            checkout = self._deps.checkout_evidence()
            _record_gate(
                "clean_checkout", check_clean_checkout(wsl=checkout["wsl"], windows=checkout["windows"])
            )

            # 2. boot health -- validated immediately (B1, external review):
            # a failed/missing poll must abort admission here, before any
            # later gate runs, rather than being recorded and only checked
            # late inside build_session_lock. Mirrors build_session_lock's
            # own boot_health check exactly (same code/message) so the two
            # checks stay in lockstep.
            boot_health = self._deps.boot_health()
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
            _record_gate("boot_health", boot_health)

            # 3. treatment can fire -- E7 (external review wave E): purely
            # static manifest+registry evidence, so it runs here, before
            # any expensive live gate (save deploy, live reload, GPU
            # snapshot, billed backend/canary probes) can be spent on a
            # position that could never fire its treatment. First-failure
            # short-circuit semantics are unchanged -- this is simply the
            # earliest point its (static) inputs allow.
            treatment_evidence = check_treatment_can_fire(
                position=campaign.position,
                minimal_observation=_minimal_observation(campaign.position),
                standard_capabilities=_standard_capabilities(),
            )
            _record_gate("treatment_can_fire", treatment_evidence)

            # 4. tuner holder (already gate-checked by the dependency itself)
            _record_gate("tuner_holder", self._deps.tuner_evidence())

            # 5. save deploy -- validated immediately (B1, external review):
            # an unverified deployment must abort admission here, before
            # `reload_and_capture` reconnects to the live game. Mirrors
            # build_session_lock's own deployment check exactly.
            deployment = self._deps.deploy_save(campaign.position)
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
            _record_gate("save_deploy", deployment)

            # 6-8. production reload / popup hygiene / canonical state
            reload_evidence = await self._deps.reload_and_capture(campaign.position)
            reload_result = dict(reload_evidence.get("reload") or {})
            if not reload_result.get("verified"):
                raise GateFailure(
                    "production_reload_not_verified",
                    {
                        "reload": reload_result,
                        "message": (
                            "production reload evidence is missing or unverified; "
                            "refusing to admit a model block without a confirmed reload"
                        ),
                    },
                )
            _record_gate("production_reload", reload_result)

            popup_result = dict(reload_evidence.get("popup_hygiene") or {})
            if not popup_result.get("ok"):
                raise GateFailure(
                    "popup_hygiene_failed",
                    {
                        "popup_hygiene": popup_result,
                        "message": "popup hygiene did not report success before admission",
                    },
                )
            _record_gate("popup_hygiene", popup_result)

            canonical_state = dict(reload_evidence.get("canonical_state") or {})
            # Validated immediately (B1, external review): wrong-save or
            # drift evidence must abort admission before a model sees an
            # observation (spec Sec 4) -- GPU isolation and the live
            # backend/canary probes below must never run against a
            # canonical state that doesn't match the frozen position.
            expected_digest = state_digest(campaign.position.expected_state)
            captured_digest = state_digest(canonical_state)
            if captured_digest != expected_digest:
                raise GateFailure(
                    "canonical_state_mismatch",
                    {
                        "position_id": campaign.position.position_id,
                        "expected_digest": expected_digest,
                        "captured_digest": captured_digest,
                        "message": (
                            f"canonical-state mismatch for position {campaign.position.position_id}: "
                            f"captured state digest {captured_digest} does not match expected "
                            f"state digest {expected_digest}"
                        ),
                    },
                )
            _record_gate("canonical_state", {"state": canonical_state})

            # 9. GPU isolation (already gate-checked by the dependency itself)
            gpu_evidence = self._deps.gpu_evidence(block.endpoint_id)
            _record_gate("gpu_isolation", gpu_evidence)

            endpoint = self._deps.resolve_endpoint(block.endpoint_id)

            # 10: endpoint/model identity, both tool canaries per arm,
            # seed/latency -- all folded into one admit_model_block call.
            # Plan 2 pins arms [minimal, standard] in that order (see
            # benchmark_contract.PLAN2_ARM_SPEC) -- the broadest (standard)
            # arm's schema is used to probe backend identity/latency/seed,
            # independent of any one arm's tool-canary result.
            probe_arm_id = campaign.manifest.arms[-1].arm_id
            probe = await self._deps.probe_backend(
                model=block.model,
                endpoint=endpoint.get("resolved_endpoint"),
                sampling=block.sampling,
                chat_template_kwargs=block.chat_template_kwargs,
                tools=list(campaign.tools_by_arm[probe_arm_id]),
            )

            tool_canaries: dict[str, ToolCanaryEvidence] = {}
            for arm in campaign.manifest.arms:
                tool_canaries[arm.arm_id] = await self._deps.probe_tool_capability(
                    model=block.model,
                    endpoint=endpoint.get("resolved_endpoint"),
                    arm_id=arm.arm_id,
                    tools=list(campaign.tools_by_arm[arm.arm_id]),
                    sampling=block.sampling,
                    chat_template_kwargs=block.chat_template_kwargs,
                )

            model_admission = admit_model_block(
                requested_model=block.model,
                resolved_model=probe.model,
                requested_endpoint=endpoint.get("requested_endpoint", block.endpoint_id),
                resolved_endpoint=endpoint.get("resolved_endpoint"),
                registry_fingerprint=endpoint.get("registry_fingerprint", ""),
                gpu_topology=endpoint.get("gpu_topology") or {},
                retry_policy=campaign.manifest.retry_policy,
                sampling=block.sampling,
                probe=probe,
                briefing_required=block.briefing_required,
                # A10 (external review, "Ruling F"): `ModelBlockConfig` has
                # no budget field by design -- Plan 2 mandates briefing off,
                # and `load_campaign_manifest` now rejects any loaded
                # campaign that declares `briefing_required: true` (briefing
                # arms are Plan-3 scope). `block.briefing_required` is
                # therefore always `False` for any campaign that reached
                # this call, which makes `admit_model_block`'s own
                # briefing_required/briefing_budget_chars pairing check
                # unreachable here -- `None` is kept rather than removed so
                # a future Plan-3 loader that DOES allow briefing arms is
                # forced to revisit this call site instead of silently
                # inheriting a landmine (a `briefing_required: true` block
                # with `briefing_budget_chars=None` would be permanently
                # unadmittable).
                briefing_budget_chars=None,
                tool_canaries=tool_canaries,
                expected_arm_ids=[arm.arm_id for arm in campaign.manifest.arms],
                max_steps=campaign.manifest.max_steps,
            )
            _record_gate("model_admission", model_admission)

            # 11. session lock creation
            block_schedule = store.schedule["blocks"][block.block_id]
            # episode_wall_s is derived once, at first admission, then
            # locked -- a resume's fresh probe recomputes it (needed for
            # the model_admission gate itself) but must not rewrite the
            # value the existing lock already carries (see
            # build_session_lock's docstring and
            # _existing_locked_episode_wall_s).
            locked_episode_wall_s = _existing_locked_episode_wall_s(store, block.block_id)
            episode_wall_s = (
                locked_episode_wall_s
                if locked_episode_wall_s is not None
                else model_admission["episode_wall_s"]
            )
            session_lock = build_session_lock(
                position=campaign.position,
                wsl=checkout["wsl"],
                windows=checkout["windows"],
                boot_health=boot_health,
                campaign_fingerprint=store.fingerprint,
                block_id=block.block_id,
                model_config=dataclasses.asdict(block),
                schedule_fingerprint=fingerprint(block_schedule),
                # Fingerprinted over the TRIMMED, locked-identity subset
                # of model_admission (see benchmark_gates.
                # locked_model_admission_evidence) -- never the raw
                # evidence, which carries volatile per-attempt warm
                # latencies/p95/seed-honoring verdict/raw tool-canary
                # transcripts that differ on every real resume. Fingerprinting
                # the raw dict here would defeat build_session_lock's own
                # trimming: this digest is unconditionally part of
                # lock["digests"], which IS byte-compared on resume.
                admission_fingerprint=fingerprint(locked_model_admission_evidence(model_admission)),
                tool_surface_fingerprint=store.lock["tool_surface_fingerprint"],
                tool_input_fingerprint=fingerprint_identity(
                    tool_input_identity(dict(campaign.tools_by_arm))
                ),
                scorer_fingerprint=campaign.manifest.contracts.scorer_fingerprint,
                episode_wall_s=episode_wall_s,
                tools_schema=_union_tool_schemas(campaign.tools_by_arm),
                deployment=deployment,
                canonical_state=canonical_state,
                model_admission=model_admission,
            )
            _record_gate("session_lock", {"session_fingerprint": session_lock.get("session_fingerprint")})
        except GateFailure as exc:
            raise _fail(exc.code, exc.details) from exc
        except Exception as exc:  # noqa: BLE001 - deliberate: every exit path gets a complete diagnostic
            raise _fail(
                "unexpected_admission_error",
                {
                    "exception_type": type(exc).__qualname__,
                    "repr": repr(exc),
                    "traceback": traceback.format_exc(),
                    "message": (
                        f"unexpected exception during admission gates: {exc!r}; this is not "
                        "a recognized GateFailure -- treated as a fail-closed admission "
                        "refusal rather than an unhandled crash"
                    ),
                },
            ) from exc

        evidence["ok"] = True
        evidence["session_fingerprint"] = session_lock.get("session_fingerprint")

        if mode == "validation":
            # Never resembles a counted admission: no campaign/admission
            # fingerprint pair is ever recorded here, regardless of what
            # the gates above actually computed.
            evidence["validation"] = True
            evidence["campaign_fingerprint"] = None
            evidence["admission_fingerprint"] = None
            _write_validation_record(store, block.block_id, evidence)
            return evidence

        if mode == "admit_only":
            self._write_record(store, block.block_id, evidence)
            return evidence

        # mode == "counted"
        try:
            benchmark_store = store.open_block(block.block_id, session_lock, block_schedule)
        except (CampaignLockMismatchError, SessionLockMismatchError) as exc:
            raise _fail("locked_identity_changed", {"message": str(exc)}) from exc

        self._write_record(store, block.block_id, evidence)

        schedule = tuple(TrialSpec(**trial) for trial in block_schedule["trials"])
        return ResolvedBlock(
            position=campaign.position,
            suite=suite_for_block(campaign.manifest, block),
            schedule=schedule,
            store=benchmark_store,
            gateway_url=str(endpoint.get("resolved_endpoint")),
            api_key=api_key,
            # The LOCKED episode_wall_s (see above) -- never a value freshly
            # re-derived on a resumed admission attempt.
            episode_wall_s=int(episode_wall_s),
            chat_template_kwargs=dict(block.chat_template_kwargs),
            user_prompt=campaign.manifest.prompt,
        )

    @staticmethod
    def _write_record(store: CampaignStore, block_id: str, evidence: Mapping[str, object]) -> Path:
        return store.record_admission(block_id, dict(evidence))


# ---------------------------------------------------------------------------
# Campaign-order helpers (used by the --campaign CLI loop)
# ---------------------------------------------------------------------------


def block_is_complete(store: CampaignStore, block_id: str) -> bool:
    """True when `block_id`'s own run directory has every one of its
    scheduled trial indices committed AND stamped with THIS campaign's
    fingerprint AND `block_id`'s own recorded `session.json`'s
    `session_fingerprint`. Pure filesystem inspection -- no session lock
    needs to be handed in to ask "is this block done yet".

    Finding 5 (final review): `select_next_incomplete_block` and
    `classify_admission_disposition`'s `first_block_counted_complete` both
    act on this BEFORE any report ever runs -- filename presence alone
    would let a copied/stale trial file (e.g. lifted from another
    campaign's run directory, same expected filename) silently satisfy
    both. Every expected trial filename is parsed and its stamped
    `campaign_fingerprint` compared against `store.fingerprint`; missing,
    unparseable, or mismatched is NOT complete -- never silently promoted
    to "counts as done" the way bare filename presence used to.

    A3 (external review): `campaign_fingerprint` alone is NOT enough --
    every model block in a Plan 2 campaign shares the same
    `campaign_fingerprint` and the same `trial_filename()` convention, so
    copying one block's committed trial files straight into a sibling
    block's own `trials/` directory (e.g. Gemma's 24 trials into Qwen's
    directory) would satisfy the check above by itself. This reuses
    `BenchmarkStore.is_trial_complete`'s own discipline -- which ALSO
    demands a matching `session_fingerprint` -- against `block_id`'s own
    recorded `blocks/<block_id>/session.json` (never some other block's).
    A block with committed trial files but no recorded `session.json` at
    all is never complete: a genuinely admitted block always mints
    `session.json` before any trial is committed under it, so trial files
    without one are, at best, evidence copied in from elsewhere. A
    provenance failure (`TrialProvenanceError` -- corrupt/unstamped/
    mismatched, exactly the case `is_trial_complete` fails closed on) is
    treated as "not complete" here, matching this function's own existing
    fail-closed contract of returning `False` rather than raising for a
    corrupt/mismatched trial.

    G1 (external review wave G): before ANY of the recorded session's
    fields are trusted, its `session_fingerprint` is re-derived from the
    session document itself (`benchmark_store.compute_session_fingerprint`,
    the exact computation `build_session_lock` minted it with) -- an edited
    session.json (block_id/model_config rewritten, stamp left untouched)
    is never complete.

    I1 (external review wave I): completeness additionally requires at
    least one counted admission SUCCESS record in `admissions/` -- `ok:
    true`, `mode: "counted"`, stamped with this campaign's fingerprint
    (`CampaignStore.record_admission` stamps every record it writes) and
    carrying a `session_fingerprint` equal to this block's own re-derived
    session fingerprint. Writer parity: `AdmissionPipeline.admit(
    mode="counted")` records exactly this via `record_admission`
    immediately after `open_block` mints/reattaches `session.json` and
    BEFORE any trial can run (trials only run through the `ResolvedBlock`
    that same call returns afterwards) -- so a complete block without one
    is substituted evidence, and a record-without-trials left by a crash
    between admission and completion is simply an incomplete block, never
    an error. Mirrors
    `benchmark_campaign_report._require_counted_admission_success`.
    """
    trials_dir = store.root / CampaignStore.BLOCKS_DIR / block_id / "trials"
    if not trials_dir.is_dir():
        return False
    scheduled = store.schedule["blocks"][block_id]["trials"]

    # H1(b) (external review wave H): the block's own recorded
    # schedule.json must equal the campaign schedule's entry for this
    # block -- the exact invariant `CampaignStore.open_block` enforces at
    # write time, re-verified here (same shared canonical encoding,
    # `benchmark_store.canonical_json_bytes`) because this function acts on
    # pure filesystem state BEFORE any report runs. `store.schedule` is
    # trusted: `CampaignStore._open_or_create` already verified it
    # byte-for-byte against the recorded campaign schedule, which is
    # digest-bound into campaign_fingerprint. A genuinely admitted block
    # always has this file (open_block writes it before session.json), so
    # missing/unreadable/mismatched is NOT complete.
    block_schedule_path = store.root / CampaignStore.BLOCKS_DIR / block_id / "schedule.json"
    if not block_schedule_path.is_file():
        return False
    try:
        recorded_block_schedule = json.loads(block_schedule_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if canonical_json_bytes(recorded_block_schedule) != canonical_json_bytes(
        dict(store.schedule["blocks"][block_id])
    ):
        return False

    session_path = store.root / CampaignStore.BLOCKS_DIR / block_id / "session.json"
    if not session_path.is_file():
        return False
    try:
        session_lock = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(session_lock, dict) or not session_lock.get("session_fingerprint"):
        return False
    # G1 (external review wave G): the recorded session_fingerprint must
    # actually be the fingerprint OF this session document. Without this
    # recomputation, editing session.json's own bound fields (block_id,
    # model_config -- the exact fields the D4 checks below compare) while
    # leaving session_fingerprint untouched keeps every trial's stamp
    # "matching" and re-homes another block's evidence wholesale. Same
    # discipline `_verify_campaign_fingerprint` applies to campaign.json,
    # via the same shared computation build_session_lock mints with.
    if compute_session_fingerprint(session_lock) != session_lock.get("session_fingerprint"):
        return False
    # The recorded session must actually belong to THIS campaign -- a
    # session.json copied in alongside copied trial files, declaring some
    # OTHER campaign's fingerprint, must not be trusted just because it
    # happens to exist. Mirrors `CampaignStore.open_block`'s own check.
    if session_lock.get("campaign_fingerprint") != store.fingerprint:
        return False

    # D4 (external review wave D): the session must also declare THIS
    # block's own identity. Copying gemma's session.json AND trial files
    # together into qwen's directory defeats the A3 session-fingerprint
    # check above (the copied trials agree with the copied session, and
    # both carry this campaign's fingerprint) -- so the session's own
    # `block_id` must equal the block directory it sits under, and its
    # `model_config` must canonically equal the campaign lock's declared
    # `ModelBlockConfig` for that block (never object identity -- both
    # sides are compared as canonical JSON).
    if session_lock.get("block_id") != block_id:
        return False
    lock_models = store.lock.get("models") if isinstance(store.lock, Mapping) else None
    declared_model_config = None
    if isinstance(lock_models, Sequence) and not isinstance(lock_models, (str, bytes)):
        for model in lock_models:
            if isinstance(model, Mapping) and model.get("block_id") == block_id:
                declared_model_config = model
                break
    if declared_model_config is None:
        return False
    session_model_config = session_lock.get("model_config")
    if not isinstance(session_model_config, Mapping):
        return False

    def _canonical(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=list)

    if _canonical(dict(session_model_config)) != _canonical(dict(declared_model_config)):
        return False

    # I1 (external review wave I): the counted admission SUCCESS anchor --
    # see the docstring. The session_fingerprint compared against is the
    # verified (G1-re-derived-equal) recorded one. A missing admissions
    # directory, an unparseable record, or a record that is admit_only /
    # unstamped / stamped for another campaign or session all fail in the
    # NOT-complete direction.
    admissions_dir = store.root / CampaignStore.ADMISSIONS_DIR
    admission_anchor_found = False
    if admissions_dir.is_dir():
        prefix = f"{block_id}-attempt-"
        for path in sorted(admissions_dir.iterdir()):
            if not (path.is_file() and path.name.startswith(prefix) and path.name.endswith(".json")):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            if (
                record.get("ok") is True
                and record.get("mode") == "counted"
                # block_id bound by field, not just filename prefix.
                and record.get("block_id") == block_id
                and record.get("campaign_fingerprint") == store.fingerprint
                and record.get("session_fingerprint") == session_lock.get("session_fingerprint")
            ):
                admission_anchor_found = True
                break
    if not admission_anchor_found:
        return False

    block_store = BenchmarkStore(trials_dir.parent, session_lock, fingerprint=session_lock.get("session_fingerprint"))
    for trial in scheduled:
        try:
            if not block_store.is_trial_complete(trial["index"]):
                return False
        except TrialProvenanceError:
            return False
    return True


def select_next_incomplete_block(
    campaign: CampaignManifest, store: CampaignStore
) -> ModelBlockConfig | None:
    """The next model block in manifest order (Gemma before Qwen for Plan
    2) whose scheduled trials are not all committed yet, or `None` once
    every block is complete. Never reorders or skips ahead -- `--one-block`
    relies on this always meaning "the next one in line"."""
    for block in campaign.models:
        if not block_is_complete(store, block.block_id):
            return block
    return None


def classify_admission_disposition(
    *, block_index: int, first_block_counted_complete: bool
) -> str | None:
    """Whether a failed admission for `campaign.models[block_index]` may be
    recorded as `REPLICATION_DEFERRED_ADMISSION` rather than a fatal
    campaign failure.

    Only ever applies to a non-first model's block (Plan 2: Qwen, index 1),
    and only once the first model's block (Gemma, index 0) has already
    completed a full counted session -- Gemma itself must complete a
    counted block, full stop, never deferred. The final campaign
    disposition this typed failure feeds into is Task 10's job; this
    function only decides whether the label applies.
    """
    if block_index == 0:
        return None
    if not first_block_counted_complete:
        return None
    return REPLICATION_DEFERRED_ADMISSION
