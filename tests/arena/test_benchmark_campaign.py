import copy
import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from civ_mcp.arena.benchmark_agent import BENCHMARK_SYSTEM, resolved_benchmark_tools
from civ_mcp.arena.benchmark_campaign import (
    BenchmarkCampaignError,
    CampaignLockMismatchError,
    CampaignStore,
    build_campaign_lock,
    compile_campaign_schedule,
)
from civ_mcp.arena.benchmark_contract import (
    fingerprint_identity,
    load_campaign_manifest,
    tool_input_identity,
    tool_surface_identity,
)
from civ_mcp.arena.benchmark_gates import GateFailure, build_session_lock
from civ_mcp.arena.benchmark_manifest import PositionManifest, fingerprint
from civ_mcp.arena.benchmark_store import BenchmarkStore, TrialProvenanceError

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


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


def _write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data))
    return path


def _write_campaign(tmp_path: Path, mutate=None) -> Path:
    _write_json(tmp_path / "provenance.json", PROVENANCE)
    _write_yaml(tmp_path / "contract.yaml", CONTRACT)
    data = _campaign_data()
    if mutate is not None:
        mutate(data)
    return _write_yaml(tmp_path / "campaign.yaml", data)


def _position(**overrides) -> PositionManifest:
    fields = dict(
        position_id="builder-economy-cal-v1",
        version=1,
        archive="benchmarks/saves/BUILDER_ECONOMY_CAL_V1.Civ6Save",
        archive_sha256=PROVENANCE["archive_sha256"],
        game_save_name="BUILDER_ECONOMY_CAL_V1",
        player_id=0,
        expected_state={"turn": 157},
        expected_state_sha256="b" * 64,
        relevant_tiles=((9, 8), (10, 8), (11, 8)),
        objectives=({"task_id": "repair", "unit_index": 4, "target": [9, 8], "requires": ["builders"]},),
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
    fields.update(overrides)
    return PositionManifest(**fields)


def _tools_by_arm() -> dict:
    return {"minimal": resolved_benchmark_tools("minimal"), "standard": resolved_benchmark_tools("standard")}


def _tool_surface_fingerprint() -> str:
    return fingerprint_identity(tool_surface_identity(_tools_by_arm()))


def _campaign_lock_kwargs(campaign, position, **overrides) -> dict:
    kwargs = dict(
        campaign=campaign,
        position=position,
        position_provenance=dict(PROVENANCE),
        schedule=compile_campaign_schedule(campaign),
        expected_commit=EXPECTED_COMMIT,
        prompt_fingerprint=fingerprint({"system": BENCHMARK_SYSTEM, "user": campaign.prompt}),
        rubric_fingerprint=fingerprint(list(position.rubric)),
        tool_surface_fingerprint=_tool_surface_fingerprint(),
    )
    kwargs.update(overrides)
    return kwargs


def _build_campaign(tmp_path: Path, mutate=None):
    campaign = load_campaign_manifest(_write_campaign(tmp_path, mutate=mutate))
    position = _position()
    campaign_lock = build_campaign_lock(**_campaign_lock_kwargs(campaign, position))
    schedule = compile_campaign_schedule(campaign)
    return campaign, position, campaign_lock, schedule


def _good_boot_health():
    return {
        "ok": True,
        "baseline_offset": 1024,
        "last_frame": 250,
        "elapsed_s": 12.0,
        "file_identity": {"inode": 1, "size": 2048},
        "profile_path": "C:\\Users\\x\\Profile.csv",
        "reason": None,
    }


def _good_deployment():
    return {
        "ok": True,
        "save_name": "BUILDER_ECONOMY_CAL_V1",
        "dest_path": "C:\\deploy\\path.Civ6Save",
        "archive_sha256": PROVENANCE["archive_sha256"],
        "deployed_sha256": PROVENANCE["archive_sha256"],
        "expected_sha256": PROVENANCE["archive_sha256"],
    }


def _session_lock_for_block(campaign, position, campaign_lock, schedule, block, **overrides) -> dict:
    tier = "minimal" if block.block_id == campaign.models[0].block_id else "standard"
    arm_id = campaign.arms[0].arm_id if tier == "minimal" else campaign.arms[1].arm_id
    tools_schema = resolved_benchmark_tools(tier)
    block_schedule = schedule["blocks"][block.block_id]

    kwargs = dict(
        position=position,
        wsl={"commit": EXPECTED_COMMIT, "status": ""},
        windows={"commit": EXPECTED_COMMIT, "status": ""},
        boot_health=_good_boot_health(),
        campaign_fingerprint=campaign_lock["campaign_fingerprint"],
        block_id=block.block_id,
        model_config=dataclasses.asdict(block),
        schedule_fingerprint=fingerprint(block_schedule),
        admission_fingerprint=fingerprint({"probe": "ok", "block_id": block.block_id}),
        tool_surface_fingerprint=campaign_lock["tool_surface_fingerprint"],
        tool_input_fingerprint=fingerprint_identity(tool_input_identity({arm_id: tools_schema})),
        scorer_fingerprint=campaign.contracts.scorer_fingerprint,
        episode_wall_s=120,
        tools_schema=tools_schema,
        deployment=_good_deployment(),
        canonical_state=dict(position.expected_state),
        model_admission={"ok": True, "resolved_model": block.model},
    )
    kwargs.update(overrides)
    return build_session_lock(**kwargs)


# ---------------------------------------------------------------------------
# compile_campaign_schedule
# ---------------------------------------------------------------------------


def test_campaign_schedule_contains_two_local_24_trial_block_schedules(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))

    schedule = compile_campaign_schedule(campaign)

    block_ids = [block.block_id for block in campaign.models]
    assert sorted(schedule["blocks"]) == sorted(block_ids)
    for block_id in block_ids:
        trials = schedule["blocks"][block_id]["trials"]
        assert [t["index"] for t in trials] == list(range(1, 25))
        assert all(t["model"] == next(b.model for b in campaign.models if b.block_id == block_id) for t in trials)


# ---------------------------------------------------------------------------
# build_campaign_lock
# ---------------------------------------------------------------------------


def test_build_campaign_lock_passes_and_is_deterministic(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))
    position = _position()

    lock_a = build_campaign_lock(**_campaign_lock_kwargs(campaign, position))
    lock_b = build_campaign_lock(**_campaign_lock_kwargs(campaign, position))

    assert lock_a["ok"] is True
    assert lock_a["campaign_id"] == "builder-economy-cal-v1"
    assert lock_a["campaign_fingerprint"] == lock_b["campaign_fingerprint"]
    json.dumps(lock_a)


def test_build_campaign_lock_excludes_exact_tool_input_schemas(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))
    position = _position()

    lock = build_campaign_lock(**_campaign_lock_kwargs(campaign, position))

    assert "tool_input_fingerprint" not in lock
    assert lock["tool_surface_fingerprint"] == _tool_surface_fingerprint()


def test_build_campaign_lock_rejects_missing_expected_commit(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))
    position = _position()

    with pytest.raises(GateFailure) as exc_info:
        build_campaign_lock(**_campaign_lock_kwargs(campaign, position, expected_commit=""))
    assert exc_info.value.code == "missing_expected_commit"


def test_build_campaign_lock_rejects_position_id_mismatch(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))
    position = _position(position_id="some-other-position")

    with pytest.raises(GateFailure) as exc_info:
        build_campaign_lock(**_campaign_lock_kwargs(campaign, position))
    assert exc_info.value.code == "campaign_position_mismatch"


def test_build_campaign_lock_rejects_provenance_content_mismatch(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))
    position = _position()

    tampered_provenance = dict(PROVENANCE)
    tampered_provenance["base_save"] = "a-different-base-save"

    with pytest.raises(GateFailure) as exc_info:
        build_campaign_lock(
            **_campaign_lock_kwargs(campaign, position, position_provenance=tampered_provenance)
        )
    assert exc_info.value.code == "provenance_mismatch"


def test_build_campaign_lock_rejects_provenance_archive_digest_mismatch(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))
    position = _position(archive_sha256="f" * 64)

    with pytest.raises(GateFailure) as exc_info:
        build_campaign_lock(**_campaign_lock_kwargs(campaign, position))
    assert exc_info.value.code == "provenance_archive_digest_mismatch"


def test_build_campaign_lock_rejects_stale_schedule(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))
    position = _position()
    stale_schedule = {"blocks": {}}

    with pytest.raises(GateFailure) as exc_info:
        build_campaign_lock(**_campaign_lock_kwargs(campaign, position, schedule=stale_schedule))
    assert exc_info.value.code == "schedule_mismatch"


def test_build_campaign_lock_rejects_prompt_fingerprint_mismatch(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))
    position = _position()

    with pytest.raises(GateFailure) as exc_info:
        build_campaign_lock(**_campaign_lock_kwargs(campaign, position, prompt_fingerprint="wrong"))
    assert exc_info.value.code == "prompt_fingerprint_mismatch"


def test_build_campaign_lock_rejects_rubric_fingerprint_mismatch(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))
    position = _position()

    with pytest.raises(GateFailure) as exc_info:
        build_campaign_lock(**_campaign_lock_kwargs(campaign, position, rubric_fingerprint="wrong"))
    assert exc_info.value.code == "rubric_fingerprint_mismatch"


def test_build_campaign_lock_fingerprint_changes_with_position(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))
    position_a = _position()
    position_b = _position(archive_sha256=PROVENANCE["archive_sha256"], expected_state_sha256="c" * 64)

    lock_a = build_campaign_lock(**_campaign_lock_kwargs(campaign, position_a))
    lock_b = build_campaign_lock(**_campaign_lock_kwargs(campaign, position_b))

    assert lock_a["campaign_fingerprint"] != lock_b["campaign_fingerprint"]


# ---------------------------------------------------------------------------
# CampaignStore -- two-level artifact layout
# ---------------------------------------------------------------------------


def test_campaign_store_creates_campaign_schedule_admissions_and_blocks(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    root = tmp_path / "runs" / campaign.campaign_id

    store = CampaignStore.create(root, campaign_lock, schedule)

    assert (root / "campaign.json").is_file()
    assert (root / "schedule.json").is_file()
    assert (root / "campaign-journal.jsonl").is_file()
    assert (root / "admissions").is_dir()
    assert (root / "blocks").is_dir()
    assert store.fingerprint == campaign_lock["campaign_fingerprint"]

    on_disk_lock = json.loads((root / "campaign.json").read_text())
    assert on_disk_lock == campaign_lock


def test_campaign_store_record_admission_is_append_only_and_never_overwrites(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    root = tmp_path / "runs" / campaign.campaign_id
    store = CampaignStore.create(root, campaign_lock, schedule)

    block_id = campaign.models[0].block_id
    first = store.record_admission(block_id, {"ok": True, "attempt": 1})
    second = store.record_admission(block_id, {"ok": True, "attempt": 2})

    assert first != second
    assert first.name == f"{block_id}-attempt-001.json"
    assert second.name == f"{block_id}-attempt-002.json"
    # G4 (external review wave G): every record is stamped with this
    # campaign's fingerprint at write time.
    assert json.loads(first.read_text()) == {
        "ok": True,
        "attempt": 1,
        "campaign_fingerprint": store.fingerprint,
    }
    assert json.loads(second.read_text()) == {
        "ok": True,
        "attempt": 2,
        "campaign_fingerprint": store.fingerprint,
    }


def test_campaign_store_record_admission_stamps_campaign_fingerprint(tmp_path):
    """G4 (external review wave G): admission-attempt, remediation, and
    disposition records are all written through record_admission, which
    stamps each with the campaign_fingerprint of the campaign it belongs
    to -- the reporter's deferral-corroboration scan ignores unstamped or
    foreign-stamped records, so a record forged into another run dir can
    never corroborate a deferral. A store with no recorded campaign yet
    (fingerprint=None -- the pre-campaign remediation journal target)
    writes its record unstamped: journaled diagnostics, deliberately never
    corroboration-grade."""
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    root = tmp_path / "runs" / campaign.campaign_id
    store = CampaignStore.create(root, campaign_lock, schedule)
    block_id = campaign.models[0].block_id

    remediation = store.record_admission(
        block_id, {"block_id": block_id, "remediation": "terminate_tuner_pid", "result": {"ok": True}}
    )
    assert json.loads(remediation.read_text())["campaign_fingerprint"] == store.fingerprint

    unattached = CampaignStore(tmp_path / "runs" / "pre-campaign", {}, dict(schedule), fingerprint=None)
    record = unattached.record_admission(block_id, {"block_id": block_id, "remediation": "x", "result": {"ok": True}})
    assert "campaign_fingerprint" not in json.loads(record.read_text())


def test_campaign_store_reopen_requires_byte_identical_campaign_lock(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    root = tmp_path / "runs" / campaign.campaign_id
    CampaignStore.create(root, campaign_lock, schedule)

    mutated_lock = dict(campaign_lock)
    mutated_lock["campaign_fingerprint"] = "mutated-campaign-fingerprint"

    with pytest.raises(CampaignLockMismatchError):
        CampaignStore.open(root, mutated_lock, schedule)

    # Restoring the original, unmutated lock reopens cleanly.
    reopened = CampaignStore.open(root, campaign_lock, schedule)
    assert reopened.fingerprint == campaign_lock["campaign_fingerprint"]


def test_campaign_store_reopen_requires_byte_identical_schedule(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    root = tmp_path / "runs" / campaign.campaign_id
    CampaignStore.create(root, campaign_lock, schedule)

    mutated_schedule = copy.deepcopy(schedule)
    first_block_id = next(iter(mutated_schedule["blocks"]))
    mutated_schedule["blocks"][first_block_id]["trials"] = []

    with pytest.raises(CampaignLockMismatchError):
        CampaignStore.open(root, campaign_lock, mutated_schedule)


def test_campaign_store_create_refuses_a_lock_with_no_campaign_fingerprint(tmp_path):
    lock_without_fingerprint = {"campaign_id": "x"}
    with pytest.raises(BenchmarkCampaignError):
        CampaignStore.create(tmp_path / "run", lock_without_fingerprint, {"blocks": {}})


def test_campaign_store_open_refuses_a_never_created_campaign(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    with pytest.raises(FileNotFoundError):
        CampaignStore.open(tmp_path / "runs" / "never-created", campaign_lock, schedule)


# ---------------------------------------------------------------------------
# open_block -- the per-model block lock IS the evolved session.json; no
# third lock artifact is ever introduced.
# ---------------------------------------------------------------------------


def test_session_lock_evolves_to_block_lock_without_new_lock_artifact(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    root = tmp_path / "runs" / campaign.campaign_id
    campaign_store = CampaignStore.create(root, campaign_lock, schedule)

    block = campaign.models[0]
    session_lock = _session_lock_for_block(campaign, position, campaign_lock, schedule, block)

    block_store = campaign_store.open_block(block.block_id, session_lock, schedule["blocks"][block.block_id])

    assert isinstance(block_store, BenchmarkStore)
    block_dir = root / "blocks" / block.block_id
    assert sorted(p.name for p in block_dir.iterdir()) == [
        "attempts",
        "journal.jsonl",
        "schedule.json",
        "session.json",
        "trials",
    ]
    on_disk_session = json.loads((block_dir / "session.json").read_text())
    assert on_disk_session == session_lock
    assert on_disk_session["session_fingerprint"]
    assert block_store.fingerprint == session_lock["session_fingerprint"]
    assert block_store.campaign_fingerprint == campaign_lock["campaign_fingerprint"]


def test_open_block_reopen_requires_byte_identical_block_schedule(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    root = tmp_path / "runs" / campaign.campaign_id
    campaign_store = CampaignStore.create(root, campaign_lock, schedule)

    block = campaign.models[0]
    session_lock = _session_lock_for_block(campaign, position, campaign_lock, schedule, block)
    campaign_store.open_block(block.block_id, session_lock, schedule["blocks"][block.block_id])

    mutated_block_schedule = {"trials": []}
    with pytest.raises(CampaignLockMismatchError):
        campaign_store.open_block(block.block_id, session_lock, mutated_block_schedule)


def test_open_block_rejects_a_session_lock_declaring_a_different_campaigns_fingerprint(tmp_path):
    """The reviewer's reproduction: `open_block` is the one join point where
    both the campaign store's own identity and a caller-supplied
    `session_lock` are in hand at the same time. It must fail closed on a
    session lock that declares some OTHER campaign's fingerprint, the same
    way every other artifact identity check in this module does -- never
    silently write a block under campaign A whose session.json references
    campaign B."""
    campaign_a_dir = tmp_path / "campaign_a"
    campaign_a_dir.mkdir()
    campaign_b_dir = tmp_path / "campaign_b"
    campaign_b_dir.mkdir()

    campaign_a, position, campaign_lock_a, schedule_a = _build_campaign(campaign_a_dir)
    campaign_b, _, campaign_lock_b, schedule_b = _build_campaign(
        campaign_b_dir, mutate=lambda d: d.__setitem__("campaign_id", "builder-economy-cal-v2")
    )
    assert campaign_lock_a["campaign_fingerprint"] != campaign_lock_b["campaign_fingerprint"]

    root_a = tmp_path / "runs" / campaign_a.campaign_id
    store_a = CampaignStore.create(root_a, campaign_lock_a, schedule_a)

    block = campaign_a.models[0]
    # Built entirely against campaign B: its campaign_fingerprint,
    # tool_surface_fingerprint, and schedule all belong to campaign B, not
    # the campaign A store we are about to call open_block on.
    session_lock_for_other_campaign = _session_lock_for_block(
        campaign_b, position, campaign_lock_b, schedule_b, campaign_b.models[0]
    )

    with pytest.raises(CampaignLockMismatchError):
        store_a.open_block(
            block.block_id,
            session_lock_for_other_campaign,
            schedule_a["blocks"][block.block_id],
        )

    # No block directory (and certainly no session.json) must be left
    # behind by the rejected attempt.
    assert not (root_a / "blocks" / block.block_id / "session.json").exists()


# ---------------------------------------------------------------------------
# Both campaign_fingerprint and session_fingerprint stamps are required for
# a counted block trial to be treated as complete.
# ---------------------------------------------------------------------------


def test_block_store_requires_campaign_and_session_fingerprints(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    root = tmp_path / "runs" / campaign.campaign_id
    campaign_store = CampaignStore.create(root, campaign_lock, schedule)

    block = campaign.models[0]
    session_lock = _session_lock_for_block(campaign, position, campaign_lock, schedule, block)
    block_store = campaign_store.open_block(block.block_id, session_lock, schedule["blocks"][block.block_id])

    # Single-stamped: carries session_fingerprint but no campaign_fingerprint.
    block_store.commit_trial(1, {"session_fingerprint": block_store.fingerprint})

    with pytest.raises(TrialProvenanceError):
        block_store.is_trial_complete(1)


def test_existing_block_trial_with_wrong_campaign_fingerprint_is_not_complete(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    root = tmp_path / "runs" / campaign.campaign_id
    campaign_store = CampaignStore.create(root, campaign_lock, schedule)

    block = campaign.models[0]
    session_lock = _session_lock_for_block(campaign, position, campaign_lock, schedule, block)
    block_store = campaign_store.open_block(block.block_id, session_lock, schedule["blocks"][block.block_id])

    block_store.commit_trial(
        1,
        {
            "session_fingerprint": block_store.fingerprint,
            "campaign_fingerprint": "a-different-campaign-fingerprint",
        },
    )

    with pytest.raises(TrialProvenanceError):
        block_store.is_trial_complete(1)


def test_correctly_double_stamped_trial_is_complete(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    root = tmp_path / "runs" / campaign.campaign_id
    campaign_store = CampaignStore.create(root, campaign_lock, schedule)

    block = campaign.models[0]
    session_lock = _session_lock_for_block(campaign, position, campaign_lock, schedule, block)
    block_store = campaign_store.open_block(block.block_id, session_lock, schedule["blocks"][block.block_id])

    block_store.commit_trial(
        1,
        {
            "session_fingerprint": block_store.fingerprint,
            "campaign_fingerprint": block_store.campaign_fingerprint,
        },
    )

    assert block_store.is_trial_complete(1) is True


# ---------------------------------------------------------------------------
# Step 7 counterfactual: a description-only schema edit must preserve
# campaign_fingerprint but change session_fingerprint through
# tool_input_fingerprint (tool_surface_fingerprint -- campaign evidence --
# stays put; tool_input_fingerprint -- block admission evidence -- moves).
# ---------------------------------------------------------------------------


def test_description_only_schema_edit_preserves_campaign_but_moves_session_fingerprint(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)

    block = campaign.models[1]
    tools_schema = resolved_benchmark_tools("standard")
    arm_id = campaign.arms[1].arm_id

    session_lock_before = _session_lock_for_block(
        campaign,
        position,
        campaign_lock,
        schedule,
        block,
        tool_input_fingerprint=fingerprint_identity(tool_input_identity({arm_id: tools_schema})),
        tools_schema=tools_schema,
    )

    mutated_tools_schema = copy.deepcopy(tools_schema)
    for schema in mutated_tools_schema:
        schema["function"]["description"] = schema["function"]["description"] + " MUTATED DESCRIPTION"

    # The campaign-wide tool_surface_fingerprint (computed over BOTH arms,
    # same as campaign_lock["tool_surface_fingerprint"]) must NOT move: it
    # only hashes ordered tool names + capability ids, not description text.
    tools_by_arm_before = _tools_by_arm()
    tools_by_arm_after = dict(tools_by_arm_before)
    tools_by_arm_after["standard"] = mutated_tools_schema
    surface_before = fingerprint_identity(tool_surface_identity(tools_by_arm_before))
    surface_after = fingerprint_identity(tool_surface_identity(tools_by_arm_after))
    assert surface_before == surface_after
    assert surface_before == campaign_lock["tool_surface_fingerprint"]

    session_lock_after = _session_lock_for_block(
        campaign,
        position,
        campaign_lock,
        schedule,
        block,
        tool_input_fingerprint=fingerprint_identity(tool_input_identity({arm_id: mutated_tools_schema})),
        tools_schema=mutated_tools_schema,
    )

    # campaign_fingerprint (referenced, not recomputed, by the block lock)
    # is untouched by the schema-text edit ...
    assert session_lock_before["campaign_fingerprint"] == campaign_lock["campaign_fingerprint"]
    assert session_lock_after["campaign_fingerprint"] == campaign_lock["campaign_fingerprint"]
    # ... but session_fingerprint moves, driven by tool_input_fingerprint.
    assert session_lock_before["digests"]["tool_input"] != session_lock_after["digests"]["tool_input"]
    assert session_lock_before["session_fingerprint"] != session_lock_after["session_fingerprint"]


# ---------------------------------------------------------------------------
# Fingerprint-mutation resume test (Step 7): mutating a fixture's campaign
# fingerprint after creation must fail reopen/resume; restoring it must let
# it succeed again.
# ---------------------------------------------------------------------------


def test_mutating_campaign_fingerprint_after_creation_breaks_resume_then_restoring_fixes_it(tmp_path):
    campaign, position, campaign_lock, schedule = _build_campaign(tmp_path)
    root = tmp_path / "runs" / campaign.campaign_id
    CampaignStore.create(root, campaign_lock, schedule)

    tampered_lock = dict(campaign_lock)
    tampered_lock["campaign_fingerprint"] = "tampered"

    with pytest.raises(CampaignLockMismatchError):
        CampaignStore.open(root, tampered_lock, schedule)

    restored = CampaignStore.open(root, campaign_lock, schedule)
    assert restored.fingerprint == campaign_lock["campaign_fingerprint"]
