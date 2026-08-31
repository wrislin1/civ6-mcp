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
each): clean checkout; boot health; tuner holder; save deploy; production
reload; popup hygiene; canonical state; GPU isolation; model admission
(endpoint/model identity + both tool canaries per arm + seed/latency, all
inside one `admit_model_block` call); treatment-can-fire; session lock
creation. `run_resolved_block` itself is deliberately NOT called by
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
from civ_mcp.arena.benchmark_campaign import CampaignLockMismatchError, CampaignStore
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
from civ_mcp.arena.benchmark_store import SessionLockMismatchError, trial_filename
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
    "tuner_holder",
    "save_deploy",
    "production_reload",
    "popup_hygiene",
    "canonical_state",
    "gpu_isolation",
    "model_admission",
    "treatment_can_fire",
    "session_lock",
)

ADMISSION_MODES = frozenset({"counted", "admit_only", "validation"})

# Task 10's job is the final campaign disposition; this module only exposes
# the typed failure so it's there to expose -- see classify_admission_disposition.
REPLICATION_DEFERRED_ADMISSION = "REPLICATION_DEFERRED_ADMISSION"


class AdmissionError(Exception):
    """Raised by `AdmissionPipeline.admit` on any fail-closed refusal --
    wraps the underlying `GateFailure` / `CampaignLockMismatchError`
    (`__cause__` is always set). `code`/`details` mirror `GateFailure`'s
    shape so callers can handle both uniformly. Never raised before the
    complete diagnostic for this attempt has already been written."""

    def __init__(self, code: str, details: Mapping[str, object]):
        self.code = code
        self.details = dict(details)
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

    The minimal tool tier restricts ACTION capability, never read/
    observation capability -- every read/query tool ships on every tier
    (see `civ_mcp.arena.registry.resolve_tools`) -- so by construction
    every rubric task a properly authored position manifest declares is
    discoverable from the minimal tier. A position whose rubric references
    a task the minimal tier genuinely cannot observe is a position-
    authoring defect to catch when the position manifest is authored, not
    something an admission-time live probe re-derives on every attempt.
    """
    return {"discoverable_task_ids": sorted({entry["task_id"] for entry in position.rubric})}


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
    caller is responsible for having already nulled those on `evidence`)."""
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
    (first admission for this block) or the existing file cannot be read/
    parsed -- either way the caller falls back to deriving fresh."""
    session_path = store.root / CampaignStore.BLOCKS_DIR / block_id / "session.json"
    if not session_path.is_file():
        return None
    try:
        existing = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = existing.get("episode_wall_s") if isinstance(existing, dict) else None
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
            look, on disk, like a counted admission attempt."""
            evidence["ok"] = False
            evidence["failure"] = {"code": code, "details": dict(details)}
            _append_campaign_journal(
                store, "admission_failed", block_id=block.block_id, mode=mode, code=code
            )
            if mode == "validation":
                _write_validation_record(store, block.block_id, evidence)
            else:
                self._write_record(store, block.block_id, evidence)
            return AdmissionError(code, evidence["failure"])

        try:
            # 1. clean checkout
            checkout = self._deps.checkout_evidence()
            _record_gate(
                "clean_checkout", check_clean_checkout(wsl=checkout["wsl"], windows=checkout["windows"])
            )

            # 2. boot health
            boot_health = self._deps.boot_health()
            _record_gate("boot_health", boot_health)

            # 3. tuner holder (already gate-checked by the dependency itself)
            _record_gate("tuner_holder", self._deps.tuner_evidence())

            # 4. save deploy
            deployment = self._deps.deploy_save(campaign.position)
            _record_gate("save_deploy", deployment)

            # 5-7. production reload / popup hygiene / canonical state
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
            _record_gate("canonical_state", {"state": canonical_state})

            # 8. GPU isolation (already gate-checked by the dependency itself)
            gpu_evidence = self._deps.gpu_evidence(block.endpoint_id)
            _record_gate("gpu_isolation", gpu_evidence)

            endpoint = self._deps.resolve_endpoint(block.endpoint_id)

            # 9-11: endpoint/model identity, both tool canaries per arm,
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
                briefing_budget_chars=None,
                tool_canaries=tool_canaries,
                expected_arm_ids=[arm.arm_id for arm in campaign.manifest.arms],
                max_steps=campaign.manifest.max_steps,
            )
            _record_gate("model_admission", model_admission)

            # 12. treatment can fire
            treatment_evidence = check_treatment_can_fire(
                position=campaign.position,
                minimal_observation=_minimal_observation(campaign.position),
                standard_capabilities=_standard_capabilities(),
            )
            _record_gate("treatment_can_fire", treatment_evidence)

            # 13. session lock creation
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
    fingerprint. Pure filesystem inspection -- no session lock needs to be
    in hand to ask "is this block done yet".

    Finding 5 (final review): `select_next_incomplete_block` and
    `classify_admission_disposition`'s `first_block_counted_complete` both
    act on this BEFORE any report ever runs -- filename presence alone
    would let a copied/stale trial file (e.g. lifted from another
    campaign's run directory, same expected filename) silently satisfy
    both. Every expected trial filename is parsed and its stamped
    `campaign_fingerprint` compared against `store.fingerprint`; missing,
    unparseable, or mismatched is NOT complete -- never silently promoted
    to "counts as done" the way bare filename presence used to.
    """
    trials_dir = store.root / CampaignStore.BLOCKS_DIR / block_id / "trials"
    if not trials_dir.is_dir():
        return False
    scheduled = store.schedule["blocks"][block_id]["trials"]
    for trial in scheduled:
        path = trials_dir / trial_filename(trial["index"])
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        stamped = payload.get("campaign_fingerprint")
        if not stamped or stamped != store.fingerprint:
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
