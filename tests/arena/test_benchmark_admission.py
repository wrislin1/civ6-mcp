import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from civ_mcp.arena.benchmark_admission import (
    GATE_ORDER,
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
from civ_mcp.arena.benchmark_campaign import CampaignStore, build_campaign_lock, compile_campaign_schedule
from civ_mcp.arena.benchmark_contract import (
    fingerprint_identity,
    load_campaign_manifest,
    tool_surface_identity,
)
from civ_mcp.arena.benchmark_manifest import PositionManifest, fingerprint
from civ_mcp.arena.benchmark_runner import ResolvedBlock
from civ_mcp.arena.benchmark_store import trial_filename

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

    trials_dir = store.root / "blocks" / block0.block_id / "trials"
    trials_dir.mkdir(parents=True)
    for trial in store.schedule["blocks"][block0.block_id]["trials"]:
        (trials_dir / trial_filename(trial["index"])).write_text("{}")

    assert select_next_incomplete_block(campaign, store) is block1

    trials_dir1 = store.root / "blocks" / block1.block_id / "trials"
    trials_dir1.mkdir(parents=True)
    for trial in store.schedule["blocks"][block1.block_id]["trials"]:
        (trials_dir1 / trial_filename(trial["index"])).write_text("{}")

    assert select_next_incomplete_block(campaign, store) is None


# ---------------------------------------------------------------------------
# Smaller unit coverage
# ---------------------------------------------------------------------------


def test_block_is_complete_false_when_no_trials_dir(tmp_path):
    campaign, position, store, bundle = _build_campaign_and_store(tmp_path)
    assert block_is_complete(store, campaign.models[0].block_id) is False


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

