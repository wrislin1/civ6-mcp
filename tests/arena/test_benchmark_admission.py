import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from civ_mcp.arena.benchmark_admission import (
    GATE_ORDER,
    REPLICATION_DEFERRAL_ELIGIBLE_CODES,
    REPLICATION_DEFERRED_ADMISSION,
    AdmissionDependencies,
    AdmissionError,
    AdmissionPipeline,
    block_is_complete,
    build_campaign_bundle,
    classify_admission_disposition,
    record_admission_disposition,
    record_remediation_attempt,
    select_next_incomplete_block,
)
from civ_mcp.arena.benchmark_agent import BENCHMARK_SYSTEM, resolved_benchmark_tools
from civ_mcp.arena.benchmark_backend import BackendProbe, ToolCanaryEvidence
from civ_mcp.arena.benchmark_campaign import (
    BenchmarkCampaignError,
    CampaignStore,
    build_campaign_lock,
    compile_campaign_schedule,
)
from civ_mcp.arena.benchmark_contract import (
    fingerprint_identity,
    load_campaign_manifest,
    tool_surface_identity,
)
from civ_mcp.arena.benchmark_manifest import PositionManifest, fingerprint
from civ_mcp.arena.benchmark_runner import ResolvedBlock
from civ_mcp.arena.benchmark_store import compute_session_fingerprint, trial_filename

PROVENANCE = {"base_save": "organic-base", "archive_sha256": "deadbeef" * 8}
CONTRACT = {
    "evidence_schema_version": "1.0.0",
    "predicate_schema_version": "1.0.0",
    "report_schema_version": "1.0.0",
    "scorer_fingerprint": "scorerfp",
}
EXPECTED_COMMIT = "c" * 40


def _campaign_data() -> dict:
    return {
        "campaign_id": "builder-economy-cal-v1",
        "campaign_schema_version": "1.0.0",
        "position": "builder-economy-cal-v1",
        "position_provenance": "provenance.json",
        "contracts": "contract.yaml",
        "prompt": "Assess the current turn and call finish_trial when done.",
        "models": [
            {
                "block_id": "gemma4-26b",
                "model": "gemma4-26b",
                "endpoint_id": "home-gpu0-cpp",
                "sampling": {"temperature": 0.2, "top_p": 0.95, "seed": 101, "max_tokens": 3072},
                "chat_template_kwargs": {"enable_thinking": False},
                "briefing_required": False,
            },
            {
                "block_id": "qwen3.6-27b",
                "model": "qwen3.6-27b",
                "endpoint_id": "home-gpu0-cpp",
                "sampling": {"temperature": 0.2, "top_p": 0.95, "seed": 101, "max_tokens": 6144},
                "chat_template_kwargs": {"enable_thinking": False},
                "briefing_required": False,
            },
        ],
        "arms": [
            {"arm_id": "minimal", "tools": "minimal", "options": {}},
            {"arm_id": "standard", "tools": "standard", "options": {}},
        ],
        "seeds": [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201],
        "order": "abba",
        "driver": "single_turn",
        "fresh_conversation_per_trial": True,
        "retry_policy": {"max_attempts": 1, "backoff_s": 0.0},
        "max_steps": 8,
        "result_char_cap": 4000,
        "audit_indices": [1, 2, 11, 12, 23, 24],
        "rules": {
            "pairs_per_model": 12,
            "minimum_decided_pairs": 10,
            "minimum_standard_wins": 10,
            "minimum_median_normalized_delta": 0.3333333333333333,
            "required_audits_per_arm": 3,
        },
    }


def _write_campaign(tmp_path: Path) -> Path:
    (tmp_path / "provenance.json").write_text(json.dumps(PROVENANCE))
    (tmp_path / "contract.yaml").write_text(yaml.safe_dump(CONTRACT))
    return_path = tmp_path / "campaign.yaml"
    return_path.write_text(yaml.safe_dump(_campaign_data()))
    return return_path


def _position() -> PositionManifest:
    return PositionManifest(
        position_id="builder-economy-cal-v1",
        version=1,
        archive="benchmarks/saves/BUILDER_ECONOMY_CAL_V1.Civ6Save",
        archive_sha256=PROVENANCE["archive_sha256"],
        game_save_name="BUILDER_ECONOMY_CAL_V1",
        player_id=0,
        expected_state={"turn": 157},
        expected_state_sha256=fingerprint({"turn": 157}),
        relevant_tiles=((9, 8), (10, 8), (11, 8)),
        objectives=({"task_id": "repair", "unit_index": 4, "target": [9, 8], "requires": ["repair_improvement"]},),
        rubric=(
            {
                "task_id": "repair",
                "levels": [
                    {"score": 0, "predicate": {"kind": "always"}},
                    {"score": 1, "predicate": {"kind": "always"}},
                ],
            },
        ),
        split="calibration",
    )


def _build_campaign_and_store(tmp_path: Path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))
    position = _position()
    schedule = compile_campaign_schedule(campaign)

    tools_by_arm = {"minimal": resolved_benchmark_tools("minimal"), "standard": resolved_benchmark_tools("standard")}
    tool_surface_fp = fingerprint_identity(tool_surface_identity(tools_by_arm))
    campaign_lock = build_campaign_lock(
        campaign=campaign,
        position=position,
        position_provenance=dict(PROVENANCE),
        schedule=schedule,
        expected_commit=EXPECTED_COMMIT,
        prompt_fingerprint=fingerprint({"system": BENCHMARK_SYSTEM, "user": campaign.prompt}),
        rubric_fingerprint=fingerprint(list(position.rubric)),
        tool_surface_fingerprint=tool_surface_fp,
    )
    store = CampaignStore.create(tmp_path / "run", campaign_lock, schedule)
    bundle = build_campaign_bundle(campaign, position)
    return campaign, position, store, bundle


def _good_checkout() -> dict:
    return {
        "wsl": {"commit": EXPECTED_COMMIT, "status": ""},
        "windows": {"commit": EXPECTED_COMMIT, "status": ""},
    }


def _good_boot_health() -> dict:
    return {
        "ok": True,
        "baseline_offset": 1024,
        "last_frame": 250,
        "elapsed_s": 12.0,
        "file_identity": {"inode": 1, "size": 2048},
        "profile_path": "C:\\Users\\x\\Profile.csv",
        "reason": None,
    }


def _good_deployment() -> dict:
    return {
        "ok": True,
        "save_name": "BUILDER_ECONOMY_CAL_V1",
        "dest_path": "C:\\deploy\\path.Civ6Save",
        "archive_sha256": PROVENANCE["archive_sha256"],
        "deployed_sha256": PROVENANCE["archive_sha256"],
        "expected_sha256": PROVENANCE["archive_sha256"],
    }


def _good_reload(position: PositionManifest) -> dict:
    return {
        "reload": {"verified": True},
        "popup_hygiene": {"status": "POPUPS|none", "ok": True},
        "canonical_state": dict(position.expected_state),
    }


def _good_gpu() -> dict:
    return {"conflicting_services": [], "approved_services": [], "process_count": 0, "ok": True}


def _good_endpoint(endpoint_id: str, *, gpu_indexes=(0,)) -> dict:
    # requested_endpoint == resolved_endpoint is the "no drift" case
    # admit_model_block's identity check expects; a test that wants to
    # exercise a mismatch overrides this via endpoint_fn.
    url = f"http://{endpoint_id}.invalid/v1"
    return {
        "requested_endpoint": url,
        "resolved_endpoint": url,
        "registry_fingerprint": "registry-fp",
        "gpu_topology": {"gpu_indexes": list(gpu_indexes)},
    }


def _good_probe(model: str) -> BackendProbe:
    return BackendProbe(
        samples=10,
        model=model,
        model_confirmed=True,
        seed_honored=True,
        latencies_s=[0.1] * 10,
        errors=[],
        seed_verdict="honored",
        repeated_consistent=True,
    )


def _good_canary(arm_id: str) -> ToolCanaryEvidence:
    return ToolCanaryEvidence(
        arm_id=arm_id,
        finish_trial_ok=True,
        required_argument_ok=True,
        observed_calls=(),
        errors=(),
    )


def _tag(calls, tag, result):
    def fn(*_args, **_kwargs):
        calls.append(tag)
        return result

    return fn


def _async_tag(calls, tag, result):
    async def fn(*_args, **_kwargs):
        calls.append(tag)
        return result

    return fn


def _make_dependencies(
    calls: list,
    campaign,
    block,
    position,
    *,
    checkout=None,
    boot_health=None,
    tuner=None,
    deployment=None,
    reload_evidence=None,
    gpu=None,
    endpoint_fn=None,
    probe=None,
    canaries=None,
) -> AdmissionDependencies:
    canaries = canaries or {arm.arm_id: _good_canary(arm.arm_id) for arm in campaign.arms}

    def _resolve_endpoint(endpoint_id):
        calls.append("resolve_endpoint")
        if endpoint_fn is not None:
            return endpoint_fn(endpoint_id)
        return _good_endpoint(endpoint_id)

    def _gpu_evidence(endpoint_id):
        calls.append("gpu_evidence")
        return gpu if gpu is not None else _good_gpu()

    def _deploy_save(_position):
        calls.append("deploy_save")
        return deployment if deployment is not None else _good_deployment()

    async def _reload_and_capture(_position):
        calls.append("reload_and_capture")
        return reload_evidence if reload_evidence is not None else _good_reload(position)

    async def _probe_backend(**_kwargs):
        calls.append("probe_backend")
        return probe if probe is not None else _good_probe(block.model)

    async def _probe_tool_capability(*, arm_id, **_kwargs):
        calls.append(f"probe_tool_capability:{arm_id}")
        return canaries[arm_id]

    return AdmissionDependencies(
        checkout_evidence=_tag(calls, "checkout_evidence", checkout or _good_checkout()),
        boot_health=_tag(calls, "boot_health", boot_health or _good_boot_health()),
        tuner_evidence=_tag(calls, "tuner_evidence", tuner or {"holder": None, "ok": True, "port": 4318}),
        deploy_save=_deploy_save,
        reload_and_capture=_reload_and_capture,
        gpu_evidence=_gpu_evidence,
        resolve_endpoint=_resolve_endpoint,
        probe_backend=_probe_backend,
        probe_tool_capability=_probe_tool_capability,
    )


def _read_last_admission(store: CampaignStore, block_id: str) -> dict:
    admissions_dir = store.root / CampaignStore.ADMISSIONS_DIR
    matches = sorted(p for p in admissions_dir.iterdir() if p.name.startswith(f"{block_id}-attempt-"))
    return json.loads(matches[-1].read_text())


def _write_block_session(store: CampaignStore, block_id: str) -> dict:
    """Write a minimal, self-consistent `blocks/<block_id>/session.json`
    directly (bypassing `BenchmarkStore.create`) -- used by
    `block_is_complete` tests (A3, external review) that need a real,
    on-disk session lock for its `session_fingerprint` cross-check to
    verify against. Returns the payload; callers stamp trial files with
    its `session_fingerprint`.

    G1 (external review wave G): `session_fingerprint` is now computed
    over the session payload's own remaining fields (the exact
    `compute_session_fingerprint` computation `block_is_complete`
    re-derives and verifies), never an arbitrary label.

    H1(b) (external review wave H): `block_is_complete` now also requires
    `blocks/<id>/schedule.json` to exist and equal the campaign schedule's
    declared entry for the block (the invariant `CampaignStore.open_block`
    enforces at write time), so this fixture writes it too -- exactly as a
    genuinely admitted block always has it."""
    block_dir = store.root / "blocks" / block_id
    block_dir.mkdir(parents=True, exist_ok=True)
    (block_dir / "schedule.json").write_text(
        json.dumps(store.schedule["blocks"][block_id], sort_keys=True)
    )
    # D4 (external review wave D): block_is_complete now also binds the
    # session to its own block -- block_id plus the campaign lock's
    # declared ModelBlockConfig -- so a fixture session must carry both,
    # exactly as build_session_lock's real output does.
    declared_model_config = next(m for m in store.lock["models"] if m["block_id"] == block_id)
    session_payload = {
        "campaign_fingerprint": store.fingerprint,
        "block_id": block_id,
        "model_config": dict(declared_model_config),
    }
    session_payload["session_fingerprint"] = compute_session_fingerprint(session_payload)
    (block_dir / "session.json").write_text(json.dumps(session_payload))
    return session_payload


def _read_journal_gate_order(store: CampaignStore) -> list[str]:
    """Gate names in the order they were journaled to
    campaign-journal.jsonl. `record_admission`'s own on-disk JSON is
    canonicalized with sorted keys (so its `gates` mapping's on-disk key
    order is alphabetical, not insertion order) -- the journal's line
    order is the only on-disk evidence of the actual call sequence."""
    journal_path = store.root / CampaignStore.JOURNAL_FILE
    order: list[str] = []
    for line in journal_path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("event") == "admission_gate":
            order.append(record["gate"])
    return order


# ---------------------------------------------------------------------------
# Step 1 tests
# ---------------------------------------------------------------------------


async def test_admission_runs_all_gates_in_locked_order_then_mints_session(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    calls: list = []
    deps = _make_dependencies(calls, campaign, block, position)
    pipeline = AdmissionPipeline(deps)

    result = await pipeline.admit(bundle, block, store, mode="counted")

    assert isinstance(result, ResolvedBlock)
    assert calls == [
        "checkout_evidence",
        "boot_health",
        "tuner_evidence",
        "deploy_save",
        "reload_and_capture",
        "gpu_evidence",
        "resolve_endpoint",
        "probe_backend",
        "probe_tool_capability:minimal",
        "probe_tool_capability:standard",
    ]

    record = _read_last_admission(store, block.block_id)
    assert record["ok"] is True
    assert _read_journal_gate_order(store) == list(GATE_ORDER)
    assert (store.root / "blocks" / block.block_id / "session.json").exists()


async def test_admission_passes_locked_sampling_and_chat_template_kwargs_to_canaries(tmp_path):
    """Spec Sec 7: tool canaries (and the backend identity/seed/latency
    probe) must run with the block's EXACT locked sampling, token limit,
    and chat template -- "Qwen thinking/token misconfiguration fails
    admission, not calibration." A canary constructed with the block's
    model/endpoint but the wrong sampling or chat_template_kwargs would
    validate against a configuration the counted trials never actually
    use."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    calls: list = []
    recorded: dict[str, list] = {"probe_backend": [], "probe_tool_capability": []}

    async def _recording_probe_backend(**kwargs):
        calls.append("probe_backend")
        recorded["probe_backend"].append(kwargs)
        return _good_probe(block.model)

    async def _recording_probe_tool_capability(*, arm_id, **kwargs):
        calls.append(f"probe_tool_capability:{arm_id}")
        recorded["probe_tool_capability"].append({"arm_id": arm_id, **kwargs})
        return _good_canary(arm_id)

    deps = dataclasses.replace(
        _make_dependencies(calls, campaign, block, position),
        probe_backend=_recording_probe_backend,
        probe_tool_capability=_recording_probe_tool_capability,
    )
    pipeline = AdmissionPipeline(deps)

    result = await pipeline.admit(bundle, block, store, mode="counted")

    assert isinstance(result, ResolvedBlock)
    assert len(recorded["probe_backend"]) == 1
    backend_kwargs = recorded["probe_backend"][0]
    assert backend_kwargs["sampling"] == block.sampling
    assert backend_kwargs["chat_template_kwargs"] == block.chat_template_kwargs

    assert len(recorded["probe_tool_capability"]) == 2
    for canary_kwargs in recorded["probe_tool_capability"]:
        assert canary_kwargs["sampling"] == block.sampling
        assert canary_kwargs["chat_template_kwargs"] == block.chat_template_kwargs


async def test_admission_failure_never_creates_session_or_runs_trials(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    calls: list = []
    dirty_checkout = {
        "wsl": {"commit": EXPECTED_COMMIT, "status": " M dirty.py"},
        "windows": {"commit": EXPECTED_COMMIT, "status": ""},
    }
    deps = _make_dependencies(calls, campaign, block, position, checkout=dirty_checkout)
    pipeline = AdmissionPipeline(deps)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bundle, block, store, mode="counted")

    assert excinfo.value.code == "dirty_checkout"
    assert calls == ["checkout_evidence"]
    assert not (store.root / "blocks" / block.block_id).exists()

    record = _read_last_admission(store, block.block_id)
    assert record["ok"] is False
    assert record["failure"]["code"] == "dirty_checkout"


async def test_diagnostic_write_failure_never_masks_the_real_admission_error(tmp_path, monkeypatch):
    """A8 (external review): if the diagnostic write itself blows up (e.g.
    a full disk), the ORIGINAL gate failure (dirty_checkout here) must
    still be the exception that propagates -- never replaced by the
    write's own exception. The write failure is recorded on
    `diagnostic_write_error` instead."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    calls: list = []
    dirty_checkout = {
        "wsl": {"commit": EXPECTED_COMMIT, "status": " M dirty.py"},
        "windows": {"commit": EXPECTED_COMMIT, "status": ""},
    }
    deps = _make_dependencies(calls, campaign, block, position, checkout=dirty_checkout)
    pipeline = AdmissionPipeline(deps)

    def _boom_record_admission(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "record_admission", _boom_record_admission)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bundle, block, store, mode="counted")

    assert excinfo.value.code == "dirty_checkout"
    assert excinfo.value.diagnostic_write_error is not None
    assert "disk full" in excinfo.value.diagnostic_write_error


async def test_validation_record_never_overwrites_a_prior_attempt_on_ordinal_collision(tmp_path):
    """A9 (external review): `_write_validation_record` must match
    `record_admission`'s append-only, never-overwrite discipline. A gap in
    the numbered validation sequence (e.g. left by an earlier interrupted
    write) must never let a freshly-computed ordinal silently clobber a
    file that already exists at that ordinal."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    calls: list = []
    deps = _make_dependencies(calls, campaign, block, position)
    pipeline = AdmissionPipeline(deps)

    validation_dir = store.root / "validation"
    validation_dir.mkdir(parents=True)
    prefix = f"{block.block_id}-validation-"
    # Only ONE file exists on disk, but numbered "002" -- the naive
    # count-derived next ordinal (count=1 -> next=2) collides with it.
    (validation_dir / f"{prefix}002.json").write_text(json.dumps({"marker": "pre-existing"}))

    with pytest.raises(BenchmarkCampaignError, match="already recorded"):
        await pipeline.admit(bundle, block, store, mode="validation")

    assert json.loads((validation_dir / f"{prefix}002.json").read_text()) == {"marker": "pre-existing"}


async def test_real_admission_failure_plus_real_cli_disposition_write_is_refused_by_the_reporter(tmp_path):
    """A1 (external review), end-to-end: every existing CLI test stubbed
    `admit()` itself, so the interaction between the REAL
    `AdmissionPipeline.admit()` failure-write path and the REAL CLI
    disposition write (`benchmark_admission.classify_admission_disposition`
    + `record_admission_disposition`, exactly as `_run_campaign_async`
    calls them) was never actually exercised against
    `build_campaign_report`. This drives that real interaction end to end:
    Gemma is genuinely admitted and its full schedule genuinely committed
    (via the real `AdmissionPipeline`/`BenchmarkStore`), Qwen's admission
    genuinely fails once via the real pipeline, and the disposition is
    written via the real helper the CLI itself calls -- reproducing
    exactly the on-disk shape one real, un-retried admission failure
    produces. `build_campaign_report` must still refuse it (A1's fix)."""
    from civ_mcp.arena import benchmark_campaign_report

    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    gemma_block, qwen_block = campaign.models

    # 1. Genuinely admit Gemma (mode="counted") with entirely good evidence.
    gemma_calls: list = []
    gemma_deps = _make_dependencies(gemma_calls, campaign, gemma_block, position)
    gemma_pipeline = AdmissionPipeline(gemma_deps)
    resolved = await gemma_pipeline.admit(bundle, gemma_block, store, mode="counted")

    # 2. Genuinely commit Gemma's entire real, compiled schedule -- minimal
    # payloads (this position's rubric is `always`-satisfied at every
    # level, so the specific final_state content doesn't matter), stamped
    # exactly as a real trial would be.
    for trial in store.schedule["blocks"][gemma_block.block_id]["trials"]:
        resolved.store.commit_trial(
            trial["index"],
            {
                "index": trial["index"],
                "position_id": trial["position_id"],
                "pair_id": trial["pair_id"],
                "arm_id": trial["arm_id"],
                "model": trial["model"],
                # H1(a) (wave H): the reporter now binds every committed
                # trial's identity fields (seed included) to the scheduled
                # entry at its index -- exactly as the real runner stamps
                # them from TrialSpec.
                "seed": trial["seed"],
                "attempt_count": 1,
                "terminal": "finish_trial",
                "session_fingerprint": resolved.store.fingerprint,
                "campaign_fingerprint": store.fingerprint,
                "steps": [],
                "initial_state": {},
                "final_state": {},
            },
        )
    assert block_is_complete(store, gemma_block.block_id) is True

    # 3. Genuinely fail Qwen's admission once via the real pipeline.
    qwen_calls: list = []
    dirty_checkout = {
        "wsl": {"commit": EXPECTED_COMMIT, "status": " M dirty.py"},
        "windows": {"commit": EXPECTED_COMMIT, "status": ""},
    }
    qwen_deps = _make_dependencies(qwen_calls, campaign, qwen_block, position, checkout=dirty_checkout)
    qwen_pipeline = AdmissionPipeline(qwen_deps)
    with pytest.raises(AdmissionError) as excinfo:
        await qwen_pipeline.admit(bundle, qwen_block, store, mode="counted")

    # 4. Mirror the real CLI's exact call sequence (see
    # `benchmark_runner._run_campaign_async`) in the SAME "invocation".
    first_complete = block_is_complete(store, gemma_block.block_id)
    disposition = classify_admission_disposition(block_index=1, first_block_counted_complete=first_complete)
    assert disposition == REPLICATION_DEFERRED_ADMISSION
    record_admission_disposition(
        store, qwen_block.block_id, disposition, {"code": excinfo.value.code, "details": excinfo.value.details}
    )

    # 5. A1's fix: this ONE real failure plus its disposition record is not
    # corroboration -- the reporter must refuse it, not silently accept a
    # deferred verdict.
    with pytest.raises(benchmark_campaign_report.CampaignReportError, match="incomplete schedule"):
        benchmark_campaign_report.build_campaign_report(store.root)


async def test_unexpected_exception_still_produces_a_complete_diagnostic(tmp_path):
    """A non-GateFailure exception (a bug, a misbehaving dependency, a
    malformed evidence shape) must never bypass diagnostics -- it is
    wrapped in a typed code, journaled, and written to the numbered
    admission attempt file exactly like a recognized GateFailure, then
    re-raised as an AdmissionError rather than propagating as a bare,
    undiagnosed crash."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]

    def _boom_boot_health():
        raise ValueError("boot-health bridge returned garbage")

    deps = _make_dependencies([], campaign, block, position)
    deps = dataclasses.replace(deps, boot_health=_boom_boot_health)
    pipeline = AdmissionPipeline(deps)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bundle, block, store, mode="counted")

    assert excinfo.value.code == "unexpected_admission_error"
    assert isinstance(excinfo.value.__cause__, ValueError)
    assert not (store.root / "blocks" / block.block_id).exists()

    record = _read_last_admission(store, block.block_id)
    assert record["ok"] is False
    assert record["failure"]["code"] == "unexpected_admission_error"
    assert record["failure"]["details"]["exception_type"] == "ValueError"
    assert "boot-health bridge returned garbage" in record["failure"]["details"]["repr"]
    assert "Traceback" in record["failure"]["details"]["traceback"]

    order = _read_journal_gate_order(store)
    assert order == ["clean_checkout"]


async def test_admit_only_never_mints_reusable_session(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    calls: list = []
    deps = _make_dependencies(calls, campaign, block, position)
    pipeline = AdmissionPipeline(deps)

    result = await pipeline.admit(bundle, block, store, mode="admit_only")

    assert isinstance(result, dict)
    assert result["ok"] is True
    assert not (store.root / "blocks" / block.block_id / "session.json").exists()
    record = _read_last_admission(store, block.block_id)
    assert record["mode"] == "admit_only"


async def test_noncounting_validation_never_mints_counted_fingerprint_pair(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    calls: list = []
    deps = _make_dependencies(calls, campaign, block, position)
    pipeline = AdmissionPipeline(deps)

    result = await pipeline.admit(bundle, block, store, mode="validation")

    assert result["validation"] is True
    assert result["campaign_fingerprint"] is None
    assert result["admission_fingerprint"] is None
    assert not (store.root / "blocks" / block.block_id).exists()

    admissions_dir = store.root / CampaignStore.ADMISSIONS_DIR
    assert not any(admissions_dir.iterdir())

    validation_dir = store.root / "validation"
    validation_files = list(validation_dir.iterdir())
    assert len(validation_files) == 1
    on_disk = json.loads(validation_files[0].read_text())
    assert on_disk["validation_stamp"] is True
    assert on_disk["campaign_fingerprint"] is None


async def test_noncounting_validation_failure_writes_to_validation_not_admissions(tmp_path):
    """A failed validation attempt must go through the SAME validation-
    record path a successful one uses -- never admissions/, and never
    consuming a counted-attempt ordinal there."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    dirty_checkout = {
        "wsl": {"commit": EXPECTED_COMMIT, "status": " M dirty.py"},
        "windows": {"commit": EXPECTED_COMMIT, "status": ""},
    }
    deps = _make_dependencies([], campaign, block, position, checkout=dirty_checkout)
    pipeline = AdmissionPipeline(deps)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bundle, block, store, mode="validation")

    assert excinfo.value.code == "dirty_checkout"

    admissions_dir = store.root / CampaignStore.ADMISSIONS_DIR
    assert not any(admissions_dir.iterdir())

    validation_dir = store.root / "validation"
    validation_files = list(validation_dir.iterdir())
    assert len(validation_files) == 1
    on_disk = json.loads(validation_files[0].read_text())
    assert on_disk["validation_stamp"] is True
    assert on_disk["ok"] is False
    assert on_disk["failure"]["code"] == "dirty_checkout"
    assert not (store.root / "blocks" / block.block_id).exists()


async def test_second_model_gets_fresh_checkout_gpu_endpoint_and_canary_evidence(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block0, block1 = campaign.models

    calls0: list = []
    deps0 = _make_dependencies(calls0, campaign, block0, position)
    pipeline = AdmissionPipeline(deps0)
    result0 = await pipeline.admit(bundle, block0, store, mode="counted")
    assert isinstance(result0, ResolvedBlock)
    assert calls0.count("checkout_evidence") == 1
    assert calls0.count("gpu_evidence") == 1

    calls1: list = []
    deps1 = _make_dependencies(calls1, campaign, block1, position)
    pipeline1 = AdmissionPipeline(deps1)
    result1 = await pipeline1.admit(bundle, block1, store, mode="counted")

    assert isinstance(result1, ResolvedBlock)
    # Fully fresh evidence gathering for the second model -- nothing cached
    # or skipped from the first block's admission.
    assert calls1 == [
        "checkout_evidence",
        "boot_health",
        "tuner_evidence",
        "deploy_save",
        "reload_and_capture",
        "gpu_evidence",
        "resolve_endpoint",
        "probe_backend",
        "probe_tool_capability:minimal",
        "probe_tool_capability:standard",
    ]


async def test_resume_reuses_campaign_lock_but_reacquires_block_admission(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]

    calls_first: list = []
    deps_first = _make_dependencies(calls_first, campaign, block, position)
    await AdmissionPipeline(deps_first).admit(bundle, block, store, mode="counted")

    # Simulate a resumed process: reopen the SAME campaign (matching lock),
    # and admit the same block again.
    resumed_store = CampaignStore.open(store.root, store.lock, store.schedule)
    calls_second: list = []
    deps_second = _make_dependencies(calls_second, campaign, block, position)
    result = await AdmissionPipeline(deps_second).admit(bundle, block, resumed_store, mode="counted")

    assert isinstance(result, ResolvedBlock)
    # Every live gate ran again -- resume never skips block admission just
    # because the campaign-level lock was reused.
    assert calls_second == calls_first


def _jittered_boot_health(*, elapsed_s: float, last_frame: int) -> dict:
    """A boot-health reading with different volatile timings/frame counts
    from `_good_boot_health()` -- same shape, same `ok`/`baseline_offset`,
    but the per-boot measurements a real boot always produces differently
    each time."""
    return {
        "ok": True,
        "baseline_offset": 1024,
        "last_frame": last_frame,
        "elapsed_s": elapsed_s,
        "file_identity": {"inode": 7, "size": 4096},
        "profile_path": "C:\\Users\\x\\Profile.csv",
        "reason": None,
    }


def _jittered_probe(model: str, *, latencies_s: list) -> BackendProbe:
    """A backend probe with different warm-latency samples -- same
    identity/seed verdict, but the real per-call timings a live probe
    always measures differently each attempt."""
    return BackendProbe(
        samples=10,
        model=model,
        model_confirmed=True,
        seed_honored=True,
        latencies_s=latencies_s,
        errors=[],
        seed_verdict="honored",
        repeated_consistent=True,
    )


async def test_resume_reuses_existing_session_lock_when_locked_identity_is_unchanged(tmp_path):
    """A resumed admission attempt re-runs every live gate from scratch
    (see test_resume_reuses_campaign_lock_but_reacquires_block_admission)
    and a real boot-health poll / warm-latency probe never produces
    byte-identical timings twice -- this must still reuse the existing
    session.json unchanged, not raise locked_identity_changed on ordinary
    jitter. Both admit() calls here deliberately use DIFFERENT volatile
    evidence (boot timings, frame counts, warm latencies) to prove that."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]

    deps_first = _make_dependencies(
        [],
        campaign,
        block,
        position,
        boot_health=_jittered_boot_health(elapsed_s=9.5, last_frame=210),
        probe=_jittered_probe(block.model, latencies_s=[0.08, 0.09, 0.10, 0.11, 0.09, 0.10, 0.08, 0.12, 0.09, 0.10]),
    )
    await AdmissionPipeline(deps_first).admit(bundle, block, store, mode="counted")
    session_path = store.root / "blocks" / block.block_id / "session.json"
    first_bytes = session_path.read_bytes()

    deps_second = _make_dependencies(
        [],
        campaign,
        block,
        position,
        boot_health=_jittered_boot_health(elapsed_s=41.2, last_frame=980),
        probe=_jittered_probe(block.model, latencies_s=[0.31, 0.29, 0.33, 0.30, 0.28, 0.34, 0.30, 0.29, 0.32, 0.31]),
    )
    result = await AdmissionPipeline(deps_second).admit(bundle, block, store, mode="counted")

    assert isinstance(result, ResolvedBlock)
    assert session_path.read_bytes() == first_bytes
    # The locked episode_wall_s from the FIRST admission survives even
    # though the second attempt's own fresh p95 (from much slower
    # latencies) would derive a different value.
    assert result.episode_wall_s == json.loads(first_bytes)["episode_wall_s"]


async def test_resume_blocks_when_topology_model_or_config_differs_from_session_lock(tmp_path):
    """`gpu_indexes` is a genuinely LOCKED identity field (GPU topology --
    see benchmark_gates.locked_model_admission_evidence), unlike the
    volatile boot-health timings / warm latencies varied in
    test_resume_reuses_existing_session_lock_when_locked_identity_is_unchanged
    above -- this mutation must block resume, not be tolerated as jitter."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]

    deps_first = _make_dependencies([], campaign, block, position)
    await AdmissionPipeline(deps_first).admit(bundle, block, store, mode="counted")
    session_path = store.root / "blocks" / block.block_id / "session.json"
    first_bytes = session_path.read_bytes()

    changed_endpoint_fn = lambda endpoint_id: _good_endpoint(endpoint_id, gpu_indexes=(1,))
    deps_second = _make_dependencies(
        [],
        campaign,
        block,
        position,
        endpoint_fn=changed_endpoint_fn,
        # Also vary volatile evidence alongside the genuine identity change,
        # to prove the block is about the topology mutation, not incidental
        # jitter sensitivity.
        boot_health=_jittered_boot_health(elapsed_s=41.2, last_frame=980),
        probe=_jittered_probe(block.model, latencies_s=[0.31, 0.29, 0.33, 0.30, 0.28, 0.34, 0.30, 0.29, 0.32, 0.31]),
    )

    with pytest.raises(AdmissionError) as excinfo:
        await AdmissionPipeline(deps_second).admit(bundle, block, store, mode="counted")

    assert excinfo.value.code == "locked_identity_changed"
    # The existing lock (and any trials recorded under it) must never be
    # overwritten by a changed-identity admission attempt.
    assert session_path.read_bytes() == first_bytes


async def test_one_block_mode_stops_after_next_manifest_order_block(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block0, block1 = campaign.models

    assert select_next_incomplete_block(campaign, store) is block0

    block0_session = _write_block_session(store, block0.block_id)
    trials_dir = store.root / "blocks" / block0.block_id / "trials"
    trials_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block0.block_id]["trials"]:
        (trials_dir / trial_filename(trial["index"])).write_text(
            json.dumps(
                {
                    "campaign_fingerprint": store.fingerprint,
                    "session_fingerprint": block0_session["session_fingerprint"],
                }
            )
        )

    assert select_next_incomplete_block(campaign, store) is block1

    block1_session = _write_block_session(store, block1.block_id)
    trials_dir1 = store.root / "blocks" / block1.block_id / "trials"
    trials_dir1.mkdir(parents=True)
    for trial in store.schedule["blocks"][block1.block_id]["trials"]:
        (trials_dir1 / trial_filename(trial["index"])).write_text(
            json.dumps(
                {
                    "campaign_fingerprint": store.fingerprint,
                    "session_fingerprint": block1_session["session_fingerprint"],
                }
            )
        )

    assert select_next_incomplete_block(campaign, store) is None


# ---------------------------------------------------------------------------
# Smaller unit coverage
# ---------------------------------------------------------------------------


def test_block_is_complete_false_when_no_trials_dir(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    assert block_is_complete(store, campaign.models[0].block_id) is False


def test_block_is_complete_rejects_a_trial_copied_from_another_campaign(tmp_path):
    """Finding 5 (final review): block_is_complete/select_next_incomplete_
    block act BEFORE any report runs, so a copied/stale trial file (right
    filename, wrong or missing campaign_fingerprint) must never silently
    satisfy either consumer -- exactly the scenario a copied-from-another-
    campaign trial file reproduces."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block0 = campaign.models[0]

    block0_session = _write_block_session(store, block0.block_id)
    trials_dir = store.root / "blocks" / block0.block_id / "trials"
    trials_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block0.block_id]["trials"]:
        (trials_dir / trial_filename(trial["index"])).write_text(
            json.dumps(
                {
                    "campaign_fingerprint": "some-other-campaigns-fingerprint",
                    "session_fingerprint": block0_session["session_fingerprint"],
                }
            )
        )

    assert block_is_complete(store, block0.block_id) is False
    assert select_next_incomplete_block(campaign, store) is block0


def test_block_is_complete_false_on_unparseable_trial_file(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block0 = campaign.models[0]

    _write_block_session(store, block0.block_id)
    trials_dir = store.root / "blocks" / block0.block_id / "trials"
    trials_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block0.block_id]["trials"]:
        (trials_dir / trial_filename(trial["index"])).write_text("not json")

    assert block_is_complete(store, block0.block_id) is False


def test_block_is_complete_true_when_every_trial_carries_the_matching_fingerprint(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block0 = campaign.models[0]

    block0_session = _write_block_session(store, block0.block_id)
    trials_dir = store.root / "blocks" / block0.block_id / "trials"
    trials_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block0.block_id]["trials"]:
        (trials_dir / trial_filename(trial["index"])).write_text(
            json.dumps(
                {
                    "campaign_fingerprint": store.fingerprint,
                    "session_fingerprint": block0_session["session_fingerprint"],
                }
            )
        )

    assert block_is_complete(store, block0.block_id) is True


def test_block_is_complete_false_when_trials_exist_but_no_session_json(tmp_path):
    """A3 (external review): trial files without a recorded session.json
    are never complete -- a genuinely admitted block always mints
    session.json BEFORE any trial is committed under it, so trial files
    present without one are, at best, evidence copied in from elsewhere."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block0 = campaign.models[0]

    trials_dir = store.root / "blocks" / block0.block_id / "trials"
    trials_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block0.block_id]["trials"]:
        (trials_dir / trial_filename(trial["index"])).write_text(
            json.dumps({"campaign_fingerprint": store.fingerprint, "session_fingerprint": "whatever"})
        )

    assert not (store.root / "blocks" / block0.block_id / "session.json").exists()
    assert block_is_complete(store, block0.block_id) is False


def test_block_is_complete_rejects_gemma_trials_copied_into_qwen_directory(tmp_path):
    """A3 (external review): Gemma and Qwen share one campaign_fingerprint
    and the same trial_filename() convention -- copying Gemma's committed
    trial files straight into Qwen's block directory must never report
    Qwen complete just because every file carries the campaign's own
    campaign_fingerprint. Qwen's own recorded session.json declares a
    DIFFERENT session_fingerprint, which the copied files must not
    satisfy."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block0, block1 = campaign.models

    gemma_session = _write_block_session(store, block0.block_id)
    gemma_fp = gemma_session["session_fingerprint"]
    gemma_dir = store.root / "blocks" / block0.block_id / "trials"
    gemma_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block0.block_id]["trials"]:
        (gemma_dir / trial_filename(trial["index"])).write_text(
            json.dumps({"campaign_fingerprint": store.fingerprint, "session_fingerprint": gemma_fp})
        )
    assert block_is_complete(store, block0.block_id) is True

    # Copy Gemma's committed trial bytes verbatim into Qwen's own trials
    # directory. Qwen's own session.json declares a DIFFERENT
    # session_fingerprint -- these copied files must not satisfy it.
    _write_block_session(store, block1.block_id)
    qwen_dir = store.root / "blocks" / block1.block_id / "trials"
    qwen_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block1.block_id]["trials"]:
        (qwen_dir / trial_filename(trial["index"])).write_text(
            json.dumps({"campaign_fingerprint": store.fingerprint, "session_fingerprint": gemma_fp})
        )

    assert block_is_complete(store, block1.block_id) is False


def test_block_is_complete_rejects_gemma_session_and_trials_copied_wholesale_into_qwen(tmp_path):
    """D4 (external review wave D): copying gemma's session.json AND its
    trial files together into qwen's block directory defeats the A3
    session-fingerprint check (the copied trials agree with the copied
    session, and the copied session carries THIS campaign's fingerprint).
    The session's own declared block identity -- block_id plus the
    campaign lock's ModelBlockConfig for that block -- must also bind the
    evidence to the block directory it sits under."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block0, block1 = campaign.models

    gemma_session = _write_block_session(store, block0.block_id)
    gemma_fp = gemma_session["session_fingerprint"]
    gemma_dir = store.root / "blocks" / block0.block_id / "trials"
    gemma_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block0.block_id]["trials"]:
        (gemma_dir / trial_filename(trial["index"])).write_text(
            json.dumps({"campaign_fingerprint": store.fingerprint, "session_fingerprint": gemma_fp})
        )
    assert block_is_complete(store, block0.block_id) is True

    # Copy gemma's session.json verbatim (block_id/model_config and all)
    # into qwen's directory alongside gemma's trial bytes -- internally
    # fully self-consistent (its session_fingerprint still verifies over
    # the unedited copy), so only the block-identity binding can catch it.
    qwen_block_dir = store.root / "blocks" / block1.block_id
    qwen_block_dir.mkdir(parents=True, exist_ok=True)
    (qwen_block_dir / "session.json").write_text(json.dumps(gemma_session))
    qwen_dir = qwen_block_dir / "trials"
    qwen_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block1.block_id]["trials"]:
        (qwen_dir / trial_filename(trial["index"])).write_text(
            json.dumps({"campaign_fingerprint": store.fingerprint, "session_fingerprint": gemma_fp})
        )

    assert block_is_complete(store, block1.block_id) is False


def test_block_is_complete_rejects_forged_block_id_with_wrong_model_config(tmp_path):
    """D4 adversarial counterpart (standing rule): the weakest input
    satisfying ONLY the block_id check -- gemma's copied session with
    block_id rewritten to qwen's but model_config still gemma's -- must
    still be rejected by the ModelBlockConfig cross-check against the
    campaign lock."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block0, block1 = campaign.models

    gemma_declared = next(m for m in store.lock["models"] if m["block_id"] == block0.block_id)
    qwen_block_dir = store.root / "blocks" / block1.block_id
    qwen_block_dir.mkdir(parents=True, exist_ok=True)
    forged_session = {
        "campaign_fingerprint": store.fingerprint,
        "block_id": block1.block_id,
        "model_config": dict(gemma_declared),
    }
    # G1 (external review wave G): the weakest forged input now also
    # re-mints a VALID self-fingerprint over the forged session (and stamps
    # the trials with it) -- otherwise the session-fingerprint
    # recomputation rejects it before the model_config cross-check this
    # test pins ever runs.
    forged_session["session_fingerprint"] = compute_session_fingerprint(forged_session)
    (qwen_block_dir / "session.json").write_text(json.dumps(forged_session))
    qwen_dir = qwen_block_dir / "trials"
    qwen_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block1.block_id]["trials"]:
        (qwen_dir / trial_filename(trial["index"])).write_text(
            json.dumps(
                {
                    "campaign_fingerprint": store.fingerprint,
                    "session_fingerprint": forged_session["session_fingerprint"],
                }
            )
        )

    assert block_is_complete(store, block1.block_id) is False


def test_block_is_complete_rejects_edited_identity_with_stale_session_fingerprint(tmp_path):
    """G1 (external review wave G), the PROVEN exploit: copy gemma's
    session.json and trial files into qwen's directory, edit the session's
    block_id to qwen's AND its model_config to the campaign lock's declared
    qwen config, and leave session_fingerprint untouched -- the D4 field
    cross-checks now all pass, and every copied trial still references the
    stale fingerprint, so the pre-G1 predicate reported the block complete.
    block_is_complete must re-derive the session_fingerprint from the
    session document itself and reject the mismatch."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block0, block1 = campaign.models

    gemma_session = _write_block_session(store, block0.block_id)
    stale_fp = gemma_session["session_fingerprint"]

    forged_session = dict(gemma_session)
    forged_session["block_id"] = block1.block_id
    forged_session["model_config"] = dict(
        next(m for m in store.lock["models"] if m["block_id"] == block1.block_id)
    )
    # session_fingerprint left as gemma's stale value -- the exploit.
    assert forged_session["session_fingerprint"] == stale_fp
    qwen_block_dir = store.root / "blocks" / block1.block_id
    qwen_block_dir.mkdir(parents=True, exist_ok=True)
    (qwen_block_dir / "session.json").write_text(json.dumps(forged_session))
    qwen_dir = qwen_block_dir / "trials"
    qwen_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block1.block_id]["trials"]:
        (qwen_dir / trial_filename(trial["index"])).write_text(
            json.dumps({"campaign_fingerprint": store.fingerprint, "session_fingerprint": stale_fp})
        )

    assert block_is_complete(store, block1.block_id) is False


# ---------------------------------------------------------------------------
# H1(b) (external review wave H): block_is_complete re-verifies open_block's
# write-time invariant -- blocks/<id>/schedule.json must equal the campaign
# schedule's declared entry for that block.
# ---------------------------------------------------------------------------


def _complete_block0(tmp_path):
    """A genuinely complete block 0 (session + schedule + stamped trials)."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block0 = campaign.models[0]
    session = _write_block_session(store, block0.block_id)
    trials_dir = store.root / "blocks" / block0.block_id / "trials"
    trials_dir.mkdir(parents=True, exist_ok=True)
    for trial in store.schedule["blocks"][block0.block_id]["trials"]:
        (trials_dir / trial_filename(trial["index"])).write_text(
            json.dumps(
                {
                    "campaign_fingerprint": store.fingerprint,
                    "session_fingerprint": session["session_fingerprint"],
                }
            )
        )
    return store, block0


def test_block_is_complete_false_when_block_schedule_is_missing(tmp_path):
    store, block0 = _complete_block0(tmp_path)
    assert block_is_complete(store, block0.block_id) is True  # sanity: fixture completes

    (store.root / "blocks" / block0.block_id / "schedule.json").unlink()
    assert block_is_complete(store, block0.block_id) is False


def test_block_is_complete_false_when_block_schedule_diverges_from_campaign_schedule(tmp_path):
    """H1(b), weakest form: a single edited field in the block's local
    schedule.json (one entry's seed) must make the block NOT complete --
    open_block would refuse to reattach over it, and the read side must be
    at least as strict as that writer."""
    store, block0 = _complete_block0(tmp_path)
    assert block_is_complete(store, block0.block_id) is True

    schedule_path = store.root / "blocks" / block0.block_id / "schedule.json"
    payload = json.loads(schedule_path.read_text())
    payload["trials"][0]["seed"] = 999999
    schedule_path.write_text(json.dumps(payload, sort_keys=True))
    assert block_is_complete(store, block0.block_id) is False


# ---------------------------------------------------------------------------
# H8 (external review wave H): _existing_locked_episode_wall_s applies G1's
# session-fingerprint verification before reusing any recorded value.
# ---------------------------------------------------------------------------


def test_existing_locked_episode_wall_verifies_session_fingerprint(tmp_path):
    from civ_mcp.arena.benchmark_admission import _existing_locked_episode_wall_s

    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    block_dir = store.root / "blocks" / block.block_id
    block_dir.mkdir(parents=True, exist_ok=True)
    session_path = block_dir / "session.json"

    genuine = {
        "campaign_fingerprint": store.fingerprint,
        "block_id": block.block_id,
        "episode_wall_s": 1234,
    }
    genuine["session_fingerprint"] = compute_session_fingerprint(genuine)
    session_path.write_text(json.dumps(genuine))
    assert _existing_locked_episode_wall_s(store, block.block_id) == 1234

    # Tamper the wall value, leaving the stale fingerprint in place -- the
    # forged value must never be reused as the locked wall. None here is
    # still fail-closed end to end: the caller derives fresh and
    # BenchmarkStore.create's byte-for-byte session comparison then refuses
    # to reattach over the tampered file.
    tampered = dict(genuine)
    tampered["episode_wall_s"] = 999999
    session_path.write_text(json.dumps(tampered))
    assert _existing_locked_episode_wall_s(store, block.block_id) is None


def test_existing_locked_episode_wall_none_when_fingerprint_missing(tmp_path):
    """H8 fail-closed default: a recorded session with NO
    session_fingerprint at all is never a source of a reusable wall."""
    from civ_mcp.arena.benchmark_admission import _existing_locked_episode_wall_s

    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    block_dir = store.root / "blocks" / block.block_id
    block_dir.mkdir(parents=True, exist_ok=True)
    (block_dir / "session.json").write_text(json.dumps({"episode_wall_s": 1234}))
    assert _existing_locked_episode_wall_s(store, block.block_id) is None


def test_record_admission_disposition_is_reconstructible_from_disk(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block_id = campaign.models[1].block_id

    dest = record_admission_disposition(
        store,
        block_id,
        REPLICATION_DEFERRED_ADMISSION,
        {"code": "tool_canary_failed", "details": {"message": "qwen never emits tool calls"}},
    )

    assert dest.exists()
    on_disk = json.loads(dest.read_text())
    assert on_disk["block_id"] == block_id
    assert on_disk["disposition"] == REPLICATION_DEFERRED_ADMISSION
    assert on_disk["underlying_failure"]["code"] == "tool_canary_failed"

    # Reconstructible by scanning admissions/<block-id>-attempt-*.json.
    admissions_dir = store.root / CampaignStore.ADMISSIONS_DIR
    matches = [
        json.loads(p.read_text())
        for p in admissions_dir.iterdir()
        if p.name.startswith(f"{block_id}-attempt-")
    ]
    assert any(m.get("disposition") == REPLICATION_DEFERRED_ADMISSION for m in matches)


def test_record_remediation_attempt_is_reconstructible_from_disk(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block_id = campaign.models[0].block_id

    record_remediation_attempt(store, block_id, "terminate_tuner_pid", {"ok": True, "terminated_pid": 4242})
    record_remediation_attempt(store, block_id, "drain_gpu_service:foo.service", {"ok": True})

    admissions_dir = store.root / CampaignStore.ADMISSIONS_DIR
    matches = [
        json.loads(p.read_text())
        for p in admissions_dir.iterdir()
        if p.name.startswith(f"{block_id}-attempt-")
    ]
    remediations = [m for m in matches if "remediation" in m]
    assert len(remediations) == 2
    assert {m["remediation"] for m in remediations} == {
        "terminate_tuner_pid",
        "drain_gpu_service:foo.service",
    }
    assert all(m["block_id"] == block_id for m in remediations)


def test_deferral_eligible_codes_mirror_is_in_lockstep_with_the_reporter():
    """D2 (external review wave D, Ruling G): the write-time allowlist
    (this module) and the report-time allowlist
    (benchmark_campaign_report, kept as an independent frozenset to avoid
    the heavy import graph) must be byte-identical -- a divergence would
    let one side accept a code the other refuses."""
    from civ_mcp.arena import benchmark_campaign_report

    assert (
        REPLICATION_DEFERRAL_ELIGIBLE_CODES
        == benchmark_campaign_report.REPLICATION_DEFERRAL_ELIGIBLE_CODES
    )


def test_deferral_eligible_codes_exclude_operator_error_and_unclassified_codes():
    """Ruling G: only model-capability gate failure codes are eligible --
    spot-check that the catch-all and representative operator-error codes
    from every non-capability gate are excluded, and that each named
    capability-gate code is included."""
    assert "tool_canary_failed" in REPLICATION_DEFERRAL_ELIGIBLE_CODES
    assert "endpoint_identity_mismatch" in REPLICATION_DEFERRAL_ELIGIBLE_CODES
    assert "backend_probe_errors" in REPLICATION_DEFERRAL_ELIGIBLE_CODES
    for operator_code in (
        "unexpected_admission_error",
        "dirty_checkout",
        "stale_repo_owned_tuner_holder",
        "unknown_tuner_holder",
        "gpu_conflict_not_acknowledged",
        "gpu_conflict_unidentified_process",
        "boot_health_missing_or_failed",
        "deployment_not_verified",
        "production_reload_not_verified",
        "popup_hygiene_failed",
        "canonical_state_mismatch",
        "locked_identity_changed",
        # G5 (external review wave G): treatment_cannot_fire derives purely
        # from the static position manifest vs the static tool registry --
        # a model-independent authoring/config property, exactly like its
        # excluded siblings (malformed_rubric_level,
        # undeclared_objective_requirements).
        "treatment_cannot_fire",
        # G2 (external review wave G, Ruling H): auth/transport failures
        # are operator/environment codes, never model-capability evidence.
        "backend_auth_error",
        "backend_transport_error",
    ):
        assert operator_code not in REPLICATION_DEFERRAL_ELIGIBLE_CODES, operator_code


def test_classify_admission_disposition_never_applies_to_first_block():
    assert classify_admission_disposition(block_index=0, first_block_counted_complete=True) is None
    assert classify_admission_disposition(block_index=0, first_block_counted_complete=False) is None


def test_classify_admission_disposition_requires_first_block_complete():
    assert classify_admission_disposition(block_index=1, first_block_counted_complete=False) is None
    assert (
        classify_admission_disposition(block_index=1, first_block_counted_complete=True)
        == REPLICATION_DEFERRED_ADMISSION
    )


async def test_treatment_cannot_fire_blocks_admission(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    calls: list = []
    # An empty canonical_state's rubric is unaffected here -- instead force
    # the failure through a position whose objective declares no capability
    # requirement at all (undeclared_objective_requirements), reachable
    # without needing a live game to simulate a missing capability.
    bad_position = dataclasses.replace(
        position, objectives=({"task_id": "repair", "unit_index": 4, "target": [9, 8]},)
    )
    bad_bundle = build_campaign_bundle(campaign, bad_position)
    deps = _make_dependencies(calls, campaign, block, bad_position)
    pipeline = AdmissionPipeline(deps)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bad_bundle, block, store, mode="counted")

    assert excinfo.value.code == "undeclared_objective_requirements"
    assert not (store.root / "blocks" / block.block_id).exists()


async def test_endpoint_identity_mismatch_blocks_admission(tmp_path):
    """The endpoint-identity gate must be able to actually fire: a
    resolve_endpoint whose requested/resolved URLs genuinely disagree
    (e.g. a live registry resolution that has drifted from what was
    expected) is a real admission-time drift, not jitter -- distinct from
    the "no drift" happy-path fixture (_good_endpoint) where both sides
    legitimately agree."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]

    def _drifted_endpoint(endpoint_id):
        return {
            "requested_endpoint": f"http://{endpoint_id}.invalid/v1",
            "resolved_endpoint": f"http://{endpoint_id}-drifted.invalid/v1",
            "registry_fingerprint": "registry-fp",
            "gpu_topology": {"gpu_indexes": [0]},
        }

    deps = _make_dependencies([], campaign, block, position, endpoint_fn=_drifted_endpoint)
    pipeline = AdmissionPipeline(deps)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bundle, block, store, mode="counted")

    assert excinfo.value.code == "endpoint_identity_mismatch"
    assert not (store.root / "blocks" / block.block_id).exists()


# ---------------------------------------------------------------------------
# B1 (external review wave B): admission must stop at the FIRST failed gate,
# not collect evidence through every later gate (including live side
# effects -- save deployment, GPU drain, real backend/canary calls) and only
# validate late inside build_session_lock.
# ---------------------------------------------------------------------------


async def test_failed_boot_health_stops_before_deploy_or_any_later_gate(tmp_path):
    """A failed native boot-health poll must abort admission immediately --
    `deploy_save` (a live Windows-bridge side effect) and every gate after
    it must never run."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    calls: list = []
    bad_boot_health = {
        "ok": False,
        "baseline_offset": None,
        "last_frame": None,
        "elapsed_s": 0.0,
        "file_identity": None,
        "profile_path": None,
        "reason": "profile_missing",
    }
    deps = _make_dependencies(calls, campaign, block, position, boot_health=bad_boot_health)
    pipeline = AdmissionPipeline(deps)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bundle, block, store, mode="counted")

    assert excinfo.value.code == "boot_health_missing_or_failed"
    assert calls == ["checkout_evidence", "boot_health"]
    assert not (store.root / "blocks" / block.block_id).exists()

    record = _read_last_admission(store, block.block_id)
    assert record["ok"] is False
    assert record["failure"]["code"] == "boot_health_missing_or_failed"


async def test_failed_deploy_stops_before_reload_or_any_later_gate(tmp_path):
    """An unverified save deployment must abort admission immediately --
    `reload_and_capture` (which reconnects to the live game and captures
    canonical state) and every gate after it must never run."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    calls: list = []
    bad_deployment = {
        "ok": False,
        "save_name": "BUILDER_ECONOMY_CAL_V1",
        "dest_path": None,
        "archive_sha256": PROVENANCE["archive_sha256"],
        "deployed_sha256": "mismatched",
        "expected_sha256": PROVENANCE["archive_sha256"],
    }
    deps = _make_dependencies(calls, campaign, block, position, deployment=bad_deployment)
    pipeline = AdmissionPipeline(deps)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bundle, block, store, mode="counted")

    assert excinfo.value.code == "deployment_not_verified"
    assert calls == ["checkout_evidence", "boot_health", "tuner_evidence", "deploy_save"]
    assert not (store.root / "blocks" / block.block_id).exists()

    record = _read_last_admission(store, block.block_id)
    assert record["ok"] is False
    assert record["failure"]["code"] == "deployment_not_verified"


async def test_canonical_state_mismatch_stops_before_gpu_or_model_probes(tmp_path):
    """Drifted canonical state must abort admission immediately -- GPU
    isolation, endpoint resolution, and the live backend/canary probes must
    never run against a save that isn't the expected one (spec: "Wrong-save
    or drift evidence aborts before a model sees an observation")."""
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    calls: list = []
    drifted_reload = {
        "reload": {"verified": True},
        "popup_hygiene": {"status": "POPUPS|none", "ok": True},
        "canonical_state": {"turn": 999},
    }
    deps = _make_dependencies(calls, campaign, block, position, reload_evidence=drifted_reload)
    pipeline = AdmissionPipeline(deps)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bundle, block, store, mode="counted")

    assert excinfo.value.code == "canonical_state_mismatch"
    assert calls == [
        "checkout_evidence",
        "boot_health",
        "tuner_evidence",
        "deploy_save",
        "reload_and_capture",
    ]
    assert not (store.root / "blocks" / block.block_id).exists()

    record = _read_last_admission(store, block.block_id)
    assert record["ok"] is False
    assert record["failure"]["code"] == "canonical_state_mismatch"


# ---------------------------------------------------------------------------
# E2 (external review wave E, A8 completion): a persistence failure while
# RECORDING a gate failure (the campaign-journal append included) must never
# replace the real AdmissionError with a raw OSError.
# ---------------------------------------------------------------------------


async def test_journal_append_failure_while_recording_failure_yields_classified_refusal(
    tmp_path, monkeypatch
):
    import civ_mcp.arena.benchmark_admission as benchmark_admission_module

    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    dirty_checkout = {
        "wsl": {"commit": EXPECTED_COMMIT, "status": " M src/foo.py\n"},
        "windows": {"commit": EXPECTED_COMMIT, "status": ""},
    }
    deps = _make_dependencies([], campaign, block, position, checkout=dirty_checkout)
    pipeline = AdmissionPipeline(deps)

    real_append = benchmark_admission_module._append_campaign_journal

    def _full_disk_append(store_arg, event, **fields):
        if event == "admission_failed":
            raise OSError(28, "No space left on device")
        return real_append(store_arg, event, **fields)

    monkeypatch.setattr(
        benchmark_admission_module, "_append_campaign_journal", _full_disk_append
    )

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bundle, block, store, mode="counted")

    assert excinfo.value.code == "dirty_checkout"
    assert excinfo.value.diagnostic_write_error is not None
    assert "No space left" in excinfo.value.diagnostic_write_error
    # The numbered diagnostic record itself must still have been attempted
    # and written -- only the journal line was lost.
    record = _read_last_admission(store, block.block_id)
    assert record["failure"]["code"] == "dirty_checkout"


# ---------------------------------------------------------------------------
# E7 (external review wave E): treatment_can_fire is a purely static
# manifest+registry check -- it must run before the expensive live gates
# (save deploy, production reload, GPU snapshot, billed backend/canary
# probes), preserving first-failure short-circuit semantics.
# ---------------------------------------------------------------------------


async def test_static_treatment_gate_runs_before_live_deploy_reload_gpu_and_probes(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    bad_position = dataclasses.replace(
        position,
        objectives=(
            {"task_id": "repair", "unit_index": 4, "target": [9, 8], "requires": ["warp_drive"]},
        ),
    )
    bad_bundle = build_campaign_bundle(campaign, bad_position)
    calls: list = []
    deps = _make_dependencies(calls, campaign, block, bad_position)
    pipeline = AdmissionPipeline(deps)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bad_bundle, block, store, mode="counted")

    assert excinfo.value.code == "treatment_cannot_fire"
    # Only the cheap checkout/boot-health evidence was ever gathered -- no
    # save deploy, no live reload, no GPU snapshot, no billed backend call.
    assert calls == ["checkout_evidence", "boot_health"]

    assert GATE_ORDER.index("treatment_can_fire") < GATE_ORDER.index("save_deploy")
    assert GATE_ORDER.index("treatment_can_fire") < GATE_ORDER.index("gpu_isolation")
    assert GATE_ORDER.index("treatment_can_fire") < GATE_ORDER.index("model_admission")


# ---------------------------------------------------------------------------
# E8 (external review wave E): a canary carrying recorded errors fails
# admission even when both ok flags read True -- the shape the old sticky
# required_argument_ok produced for a correct-then-wrong call sequence.
# ---------------------------------------------------------------------------


async def test_canary_errors_alone_fail_admission_with_tool_canary_failed(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]
    canaries = {arm.arm_id: _good_canary(arm.arm_id) for arm in campaign.arms}
    canaries["standard"] = ToolCanaryEvidence(
        arm_id="standard",
        finish_trial_ok=True,
        required_argument_ok=True,
        observed_calls=(),
        errors=(
            "required-argument canary: expected arguments "
            "{'unit_index': 7, 'x': 11, 'y': 13}, got {'unit_index': 7, 'x': 12, 'y': 13}",
        ),
        # H4 (wave H): probe_tool_capability stamps a "model" kind for
        # this genuinely model-side failure; an unclassified canary error
        # would now refuse as backend_transport_error instead.
        error_kinds=("model",),
    )
    deps = _make_dependencies([], campaign, block, position, canaries=canaries)
    pipeline = AdmissionPipeline(deps)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bundle, block, store, mode="counted")

    assert excinfo.value.code == "tool_canary_failed"
    # Ruling G: tool_canary_failed stays a deferral-eligible model-
    # capability code -- this fix must not move it out of the allowlist.
    assert "tool_canary_failed" in REPLICATION_DEFERRAL_ELIGIBLE_CODES


# ---------------------------------------------------------------------------
# E3 (external review wave E): a live reload_and_capture whose reload raises
# (OSError et al.) must classify as the production-reload gate failure --
# never as unexpected_admission_error -- and must never run popup hygiene or
# canonical capture after the failed reload.
# ---------------------------------------------------------------------------


async def test_live_reload_exception_classifies_as_production_reload_gate_failure(
    tmp_path, monkeypatch
):
    import argparse

    import civ_mcp.arena.benchmark_runner as benchmark_runner_module

    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    block = campaign.models[0]

    class _FakeConnection:
        async def connect(self):
            return None

        async def disconnect(self):
            return None

    async def _boom_reload(connection, pos):
        raise OSError("connection reset by peer")

    async def _never_popups(connection):
        raise AssertionError("popup hygiene must never run after a failed reload")

    async def _never_canonical(connection, player_id, tiles):
        raise AssertionError("canonical capture must never run after a failed reload")

    monkeypatch.setattr(benchmark_runner_module, "GameConnection", _FakeConnection)
    monkeypatch.setattr(benchmark_runner_module, "reload_position", _boom_reload)
    monkeypatch.setattr(benchmark_runner_module, "dismiss_blocking_popups", _never_popups)
    monkeypatch.setattr(benchmark_runner_module, "capture_canonical_state", _never_canonical)

    live_deps = benchmark_runner_module._build_live_admission_dependencies(
        args=argparse.Namespace(wsl_repo="/wsl/repo", windows_repo="C:\\repo"), api_key="x"
    )
    deps = dataclasses.replace(
        _make_dependencies([], campaign, block, position),
        reload_and_capture=live_deps.reload_and_capture,
    )
    pipeline = AdmissionPipeline(deps)

    with pytest.raises(AdmissionError) as excinfo:
        await pipeline.admit(bundle, block, store, mode="counted")

    assert excinfo.value.code == "production_reload_not_verified"
    assert "OSError" in json.dumps(excinfo.value.details)

