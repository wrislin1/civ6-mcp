import json

import pytest

from civ_mcp.arena.backends import RetryPolicy, SamplingConfig
from civ_mcp.arena.benchmark_backend import BackendProbe
from civ_mcp.arena.benchmark_gates import (
    GateFailure,
    admit_model_block,
    build_session_lock,
    check_clean_checkout,
    check_gpu_conflicts,
    check_treatment_can_fire,
)
from civ_mcp.arena.benchmark_manifest import PositionManifest

FINISH_SCHEMA = {"type": "function", "function": {"name": "finish_trial", "parameters": {}}}
END_TURN_SCHEMA = {"type": "function", "function": {"name": "end_turn", "parameters": {}}}
GET_UNITS_SCHEMA = {"type": "function", "function": {"name": "get_units", "parameters": {}}}


def _position(**overrides):
    fields = dict(
        position_id="builder-cal-v1",
        version=1,
        archive="benchmarks/saves/BUILDER_ECONOMY_CAL_V1.Civ6Save",
        archive_sha256="a" * 64,
        game_save_name="BUILDER_ECONOMY_CAL_V1",
        player_id=0,
        expected_state={"turn": 157},
        expected_state_sha256="b" * 64,
        relevant_tiles=((9, 8), (10, 8), (11, 8)),
        objectives=({"task_id": "repair", "unit_index": 4, "target": [9, 8]},),
        rubric=(
            {
                "task_id": "repair",
                "levels": [
                    {"score": 0, "predicate": {"kind": "always"}},
                    {"score": 1, "predicate": {"kind": "always"}},
                    {"score": 2, "predicate": {"kind": "always"}},
                    {"score": 3, "predicate": {"kind": "always"}},
                    {"score": 4, "predicate": {"kind": "always"}},
                ],
            },
        ),
        split="calibration",
    )
    fields.update(overrides)
    return PositionManifest(**fields)


# ---------------------------------------------------------------------------
# GateFailure
# ---------------------------------------------------------------------------


def test_gate_failure_carries_code_and_details():
    exc = GateFailure("some_code", {"message": "boom", "extra": 1})
    assert exc.code == "some_code"
    assert exc.details == {"message": "boom", "extra": 1}
    assert str(exc) == "boom"


def test_gate_failure_falls_back_to_code_as_message():
    exc = GateFailure("some_code", {"extra": 1})
    assert str(exc) == "some_code"


# ---------------------------------------------------------------------------
# check_treatment_can_fire
# ---------------------------------------------------------------------------


def test_calibration_gate_requires_minimal_progress_and_standard_completion():
    position = PositionManifest(
        position_id="builder-cal-v1",
        version=1,
        archive="benchmarks/saves/BUILDER_ECONOMY_CAL_V1.Civ6Save",
        archive_sha256="a" * 64,
        game_save_name="BUILDER_ECONOMY_CAL_V1",
        player_id=0,
        expected_state={},
        expected_state_sha256="b" * 64,
        relevant_tiles=((9, 8), (10, 8), (11, 8)),
        objectives=({"task_id": "repair", "unit_index": 4, "target": [9, 8]},),
        rubric=(
            {
                "task_id": "repair",
                "levels": [
                    {"score": 0, "predicate": {"kind": "always"}},
                    {"score": 1, "predicate": {"kind": "always"}},
                    {"score": 2, "predicate": {"kind": "always"}},
                    {"score": 3, "predicate": {"kind": "always"}},
                    {"score": 4, "predicate": {"kind": "always"}},
                ],
            },
        ),
        split="calibration",
    )
    with pytest.raises(GateFailure, match="minimal.*levels 1-2"):
        check_treatment_can_fire(
            position=position,
            minimal_observation={"discoverable_task_ids": []},
            standard_capabilities={"improve_tile", "repair_improvement", "remove_feature"},
        )


def test_check_treatment_can_fire_passes_when_minimal_reaches_and_standard_completes():
    position = _position(
        objectives=(
            {
                "task_id": "repair",
                "unit_index": 4,
                "target": [9, 8],
                "requires": ["repair_improvement"],
            },
        ),
    )
    evidence = check_treatment_can_fire(
        position=position,
        minimal_observation={"discoverable_task_ids": ["repair"]},
        standard_capabilities={"improve_tile", "repair_improvement", "remove_feature"},
    )
    assert evidence["ok"] is True
    assert evidence["minimal_reachable_task_ids"] == ["repair"]
    json.dumps(evidence)


def test_check_treatment_can_fire_rejects_undeclared_objective_requirements():
    """Fail-closed on silence: an objective with no `requires` list must not
    be treated as trivially satisfied -- a real position manifest must
    declare what the standard arm needs before a counted run admits it."""
    position = _position(
        objectives=({"task_id": "repair", "unit_index": 4, "target": [9, 8]},),
    )
    with pytest.raises(GateFailure, match="do not declare a 'requires' capability") as exc_info:
        check_treatment_can_fire(
            position=position,
            minimal_observation={"discoverable_task_ids": ["repair"]},
            standard_capabilities={"improve_tile", "repair_improvement", "remove_feature"},
        )
    assert exc_info.value.code == "undeclared_objective_requirements"
    assert exc_info.value.details["undeclared_task_ids"] == ["repair"]


def test_check_treatment_can_fire_rejects_undeclared_objective_requirements_when_empty():
    """An explicitly empty `requires: []` is just as undeclared as an
    absent key -- both fail closed rather than being read as "needs
    nothing"."""
    position = _position(
        objectives=({"task_id": "repair", "requires": []},),
    )
    with pytest.raises(GateFailure) as exc_info:
        check_treatment_can_fire(
            position=position,
            minimal_observation={"discoverable_task_ids": ["repair"]},
            standard_capabilities={"repair_improvement"},
        )
    assert exc_info.value.code == "undeclared_objective_requirements"


def test_check_treatment_can_fire_rejects_standard_arm_missing_capabilities():
    position = _position(
        objectives=({"task_id": "repair", "requires": ["repair_improvement"]},),
    )
    with pytest.raises(GateFailure, match="standard arm lacks capabilit") as exc_info:
        check_treatment_can_fire(
            position=position,
            minimal_observation={"discoverable_task_ids": ["repair"]},
            standard_capabilities=set(),
        )
    assert exc_info.value.code == "treatment_cannot_fire"


# ---------------------------------------------------------------------------
# check_clean_checkout
# ---------------------------------------------------------------------------


def test_check_clean_checkout_rejects_dirty_wsl():
    with pytest.raises(GateFailure, match="WSL checkout is dirty") as exc_info:
        check_clean_checkout(
            wsl={"commit": "abc123", "status": " M src/foo.py\n"},
            windows={"commit": "abc123", "status": ""},
        )
    assert exc_info.value.code == "dirty_checkout"


def test_check_clean_checkout_rejects_dirty_windows():
    with pytest.raises(GateFailure, match="Windows companion checkout is dirty"):
        check_clean_checkout(
            wsl={"commit": "abc123", "status": ""},
            windows={"commit": "abc123", "status": " M foo.py\n"},
        )


def test_check_clean_checkout_rejects_windows_commit_mismatch():
    with pytest.raises(GateFailure, match="does not match Windows companion commit") as exc_info:
        check_clean_checkout(
            wsl={"commit": "abc123", "status": ""},
            windows={"commit": "def456", "status": ""},
        )
    assert exc_info.value.code == "commit_mismatch"


def test_check_clean_checkout_passes_when_clean_and_matching():
    evidence = check_clean_checkout(
        wsl={"commit": "abc123", "status": ""},
        windows={"commit": "abc123", "status": ""},
    )
    assert evidence == {"commit": "abc123", "wsl_status": "", "windows_status": ""}


# ---------------------------------------------------------------------------
# check_gpu_conflicts
# ---------------------------------------------------------------------------


def test_check_gpu_conflicts_rejects_unidentified_process():
    with pytest.raises(GateFailure, match="unidentified process") as exc_info:
        check_gpu_conflicts(
            processes=[{"pid": 4242, "service": None, "gpu_index": 0}],
            approved_services=set(),
        )
    assert exc_info.value.code == "gpu_conflict_unidentified_process"


def test_check_gpu_conflicts_rejects_without_scoped_acknowledgment():
    with pytest.raises(GateFailure, match="GPU conflict") as exc_info:
        check_gpu_conflicts(
            processes=[{"pid": 111, "service": "ollama", "gpu_index": 0}],
            approved_services=set(),
        )
    assert exc_info.value.code == "gpu_conflict_not_acknowledged"


def test_check_gpu_conflicts_rejects_over_broad_acknowledgment():
    with pytest.raises(GateFailure, match="GPU conflict"):
        check_gpu_conflicts(
            processes=[{"pid": 111, "service": "ollama", "gpu_index": 0}],
            approved_services={"ollama", "llama-swap"},
        )


def test_check_gpu_conflicts_passes_with_exact_acknowledgment():
    evidence = check_gpu_conflicts(
        processes=[{"pid": 111, "service": "ollama", "gpu_index": 0}],
        approved_services={"ollama"},
    )
    assert evidence["ok"] is True
    assert evidence["conflicting_services"] == ["ollama"]


def test_check_gpu_conflicts_never_touches_subprocess(monkeypatch):
    """check_gpu_conflicts must never terminate a process -- assert no
    subprocess/os.kill call happens even on the failing path."""
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("check_gpu_conflicts must never invoke subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    with pytest.raises(GateFailure):
        check_gpu_conflicts(
            processes=[{"pid": 111, "service": "ollama", "gpu_index": 0}],
            approved_services=set(),
        )


# ---------------------------------------------------------------------------
# admit_model_block
# ---------------------------------------------------------------------------


def _good_probe(*, seed_honored=True, model="qwen3.6-27b", model_confirmed=True, errors=None):
    return BackendProbe(
        samples=10,
        model=model,
        model_confirmed=model_confirmed,
        seed_honored=seed_honored,
        latencies_s=[1.0 + 0.01 * i for i in range(10)],
        errors=list(errors or []),
    )


def _admit_kwargs(**overrides):
    kwargs = dict(
        requested_model="qwen3.6-27b",
        resolved_model="qwen3.6-27b",
        requested_endpoint="http://192.168.20.196:11440/v1",
        resolved_endpoint="http://192.168.20.196:11440/v1",
        registry_fingerprint="reg-v1",
        gpu_topology={"gpu_indexes": [0], "mode": "single"},
        retry_policy=RetryPolicy(max_attempts=1),
        sampling=SamplingConfig(seed=101),
        probe=_good_probe(),
        briefing_budget_chars=4000,
        max_steps=6,
    )
    kwargs.update(overrides)
    return kwargs


def test_admit_model_block_rejects_endpoint_identity_mismatch():
    with pytest.raises(GateFailure, match="endpoint identity mismatch") as exc_info:
        admit_model_block(
            **_admit_kwargs(resolved_endpoint="http://192.168.20.196:9999/v1")
        )
    assert exc_info.value.code == "endpoint_identity_mismatch"


def test_admit_model_block_rejects_probe_reported_model_mismatch():
    with pytest.raises(GateFailure, match="endpoint identity mismatch"):
        admit_model_block(**_admit_kwargs(probe=_good_probe(model="some-other-model")))


def test_admit_model_block_rejects_counted_backend_with_hidden_retries():
    with pytest.raises(GateFailure, match="RetryPolicy") as exc_info:
        admit_model_block(**_admit_kwargs(retry_policy=RetryPolicy(max_attempts=3)))
    assert exc_info.value.code == "counted_backend_hidden_retries"


def test_admit_model_block_rejects_zero_briefing_budget():
    with pytest.raises(GateFailure, match="briefing budget") as exc_info:
        admit_model_block(**_admit_kwargs(briefing_budget_chars=0))
    assert exc_info.value.code == "zero_briefing_budget"


def test_admit_model_block_rejects_insufficient_warm_latency_samples():
    short_probe = BackendProbe(
        samples=10, model="qwen3.6-27b", model_confirmed=True,
        seed_honored=True, latencies_s=[1.0] * 5, errors=[],
    )
    with pytest.raises(GateFailure, match="warm latenc"):
        admit_model_block(**_admit_kwargs(probe=short_probe))


def test_admit_model_block_rejects_probe_errors():
    with pytest.raises(GateFailure, match="probe"):
        admit_model_block(**_admit_kwargs(probe=_good_probe(errors=["seed check failed"])))


def test_admit_model_block_rejects_seed_not_honored():
    with pytest.raises(GateFailure, match="seed"):
        admit_model_block(**_admit_kwargs(probe=_good_probe(seed_honored=False)))


def test_admit_model_block_passes_and_derives_wall():
    evidence = admit_model_block(**_admit_kwargs())
    assert evidence["ok"] is True
    assert len(evidence["warm_latencies_s"]) == 10
    assert evidence["episode_wall_s"] >= 300
    assert "p95_latency_s" in evidence
    json.dumps(evidence)


# ---------------------------------------------------------------------------
# build_session_lock
# ---------------------------------------------------------------------------


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
        "archive_sha256": "a" * 64,
        "deployed_sha256": "a" * 64,
        "expected_sha256": "a" * 64,
    }


def _lock_kwargs(**overrides):
    position = overrides.pop("position", _position())
    kwargs = dict(
        position=position,
        wsl={"commit": "abc123", "status": ""},
        windows={"commit": "abc123", "status": ""},
        boot_health=_good_boot_health(),
        manifest_fingerprint="mfp",
        schedule_fingerprint="sfp",
        prompt_fingerprint="pfp",
        rubric_fingerprint="rfp",
        tool_fingerprint="tfp",
        scorer_fingerprint="scfp",
        tools_schema=[GET_UNITS_SCHEMA, FINISH_SCHEMA],
        deployment=_good_deployment(),
        canonical_state=dict(position.expected_state),
        model_admission={"ok": True, "resolved_model": "qwen3.6-27b"},
    )
    kwargs.update(overrides)
    return kwargs


def test_build_session_lock_rejects_missing_boot_health_evidence():
    with pytest.raises(
        GateFailure, match="boot-health evidence is missing or reports failure"
    ) as exc_info:
        build_session_lock(**_lock_kwargs(boot_health=None))
    assert exc_info.value.code == "boot_health_missing_or_failed"


def test_build_session_lock_rejects_failed_boot_health_evidence():
    with pytest.raises(GateFailure, match="boot-health evidence is missing or reports failure"):
        build_session_lock(**_lock_kwargs(boot_health={"ok": False, "reason": "timeout"}))


def test_build_session_lock_rejects_boot_health_missing_baseline_offset():
    """The "fresh-offset" half of the boot-health gate: `ok: True` alone is
    not sufficient -- a poll that never recorded a baseline byte offset
    cannot prove the observed frame progress happened *after* this
    session's boot, so it must fail closed exactly like a missing/failed
    poll does."""
    with pytest.raises(
        GateFailure, match="boot-health evidence is missing or reports failure"
    ) as exc_info:
        build_session_lock(**_lock_kwargs(boot_health={"ok": True}))
    assert exc_info.value.code == "boot_health_missing_or_failed"


def test_build_session_lock_rejects_end_turn_exposure():
    with pytest.raises(GateFailure, match="end_turn") as exc_info:
        build_session_lock(
            **_lock_kwargs(tools_schema=[GET_UNITS_SCHEMA, END_TURN_SCHEMA, FINISH_SCHEMA])
        )
    assert exc_info.value.code == "end_turn_exposed"


def test_build_session_lock_rejects_missing_finish_control():
    with pytest.raises(GateFailure, match="finish_trial") as exc_info:
        build_session_lock(**_lock_kwargs(tools_schema=[GET_UNITS_SCHEMA]))
    assert exc_info.value.code == "missing_finish_control"


def test_build_session_lock_rejects_canonical_state_mismatch():
    position = _position(expected_state={"turn": 157})
    with pytest.raises(GateFailure, match="canonical-state mismatch") as exc_info:
        build_session_lock(
            **_lock_kwargs(position=position, canonical_state={"turn": 999})
        )
    assert exc_info.value.code == "canonical_state_mismatch"


def test_build_session_lock_rejects_dirty_wsl():
    with pytest.raises(GateFailure, match="WSL checkout is dirty"):
        build_session_lock(**_lock_kwargs(wsl={"commit": "abc123", "status": "M foo.py"}))


def test_build_session_lock_rejects_windows_commit_mismatch():
    with pytest.raises(GateFailure, match="does not match Windows companion commit"):
        build_session_lock(
            **_lock_kwargs(windows={"commit": "zzz999", "status": ""})
        )


def test_build_session_lock_rejects_missing_digest():
    with pytest.raises(GateFailure, match="missing required digest"):
        build_session_lock(**_lock_kwargs(scorer_fingerprint=""))


def test_build_session_lock_rejects_unverified_deployment():
    with pytest.raises(GateFailure, match="deployment evidence"):
        build_session_lock(**_lock_kwargs(deployment={"ok": False}))


def test_build_session_lock_rejects_unverified_model_admission():
    with pytest.raises(GateFailure, match="model admission"):
        build_session_lock(**_lock_kwargs(model_admission={"ok": False}))


def test_build_session_lock_passes_and_returns_full_evidence():
    lock = build_session_lock(**_lock_kwargs())
    assert lock["ok"] is True
    assert lock["position_id"] == "builder-cal-v1"
    assert lock["digests"]["manifest"] == "mfp"
    assert lock["digests"]["scorer"] == "scfp"
    assert lock["canonical_state_digest"]
    assert "session_fingerprint" in lock and lock["session_fingerprint"]
    json.dumps(lock)

    # Deterministic: identical inputs must produce an identical fingerprint.
    lock2 = build_session_lock(**_lock_kwargs())
    assert lock2["session_fingerprint"] == lock["session_fingerprint"]


def test_build_session_lock_fingerprint_changes_with_digest():
    lock_a = build_session_lock(**_lock_kwargs())
    lock_b = build_session_lock(**_lock_kwargs(manifest_fingerprint="different-mfp"))
    assert lock_a["session_fingerprint"] != lock_b["session_fingerprint"]


def test_build_session_lock_also_emits_the_canonical_report_schema_keys():
    # benchmark_report.build_report is the only consumer of session.json and
    # requires a top-level scorer_fingerprint plus a positions mapping with
    # embedded rubric/objectives (see the schema comment near
    # benchmark_report.build_report). build_session_lock's own evidence
    # structure (digests.scorer, singular position_id, ...) stays as-is;
    # these are additive.
    position = _position()
    lock = build_session_lock(**_lock_kwargs(position=position))

    assert lock["scorer_fingerprint"] == "scfp"
    assert lock["positions"] == {
        position.position_id: {
            "rubric": list(position.rubric),
            "objectives": list(position.objectives),
        }
    }
    json.dumps(lock)


def test_check_treatment_can_fire_does_not_fail_open_on_mapping_shaped_rubric_levels():
    """F2 repro: the canonical rubric shape is a list of {"score", "predicate"}
    mappings (per benchmark_report._validate_rubric_shape / build_session_lock).
    The old `level in (1, 2)` check treats a mapping level as never equal to 1
    or 2, so no task is ever considered "nontrivial" and the minimal-arm
    reachability check passes vacuously (fail-open) even when the minimal
    observation cannot discover the task at all."""
    position = _position(
        rubric=(
            {
                "task_id": "repair",
                "levels": [
                    {"score": 0, "predicate": {"kind": "always"}},
                    {"score": 1, "predicate": {"kind": "always"}},
                    {"score": 2, "predicate": {"kind": "always"}},
                ],
            },
        ),
        objectives=({"task_id": "repair", "requires": ["repair_improvement"]},),
    )
    # standard_capabilities fully satisfies the (only) objective, and
    # discoverable_task_ids is empty -- if the level-shape bug is present,
    # nontrivial_task_ids is wrongly computed as empty, "unreachable" is
    # empty, and the whole gate returns ok=True with NO GateFailure raised
    # at all (silent fail-open) instead of catching the undiscoverable task.
    with pytest.raises(GateFailure, match="minimal.*levels 1-2"):
        check_treatment_can_fire(
            position=position,
            minimal_observation={"discoverable_task_ids": []},
            standard_capabilities={"repair_improvement"},
        )


def test_check_treatment_can_fire_rejects_bare_int_levels():
    """Ruled fix: the gate accepts ONLY the {"score", "predicate"} mapping
    shape -- bare-int levels (the plan's old T10 fixture shape) must fail
    closed with a clear GateFailure rather than being silently tolerated."""
    position = _position(
        rubric=({"task_id": "repair", "levels": [0, 1, 2, 3, 4]},),
        objectives=({"task_id": "repair", "requires": ["repair_improvement"]},),
    )
    with pytest.raises(GateFailure) as exc_info:
        check_treatment_can_fire(
            position=position,
            minimal_observation={"discoverable_task_ids": ["repair"]},
            standard_capabilities={"repair_improvement"},
        )
    assert exc_info.value.code == "malformed_rubric_level"


def test_check_clean_checkout_rejects_absent_commits_on_both_sides():
    """F10 repro: wsl_commit == windows_commit passes when both are None --
    a session could admit with no code revision recorded at all."""
    with pytest.raises(GateFailure):
        check_clean_checkout(
            wsl={"commit": None, "status": ""},
            windows={"commit": None, "status": ""},
        )


def test_check_clean_checkout_rejects_empty_string_commits_on_both_sides():
    with pytest.raises(GateFailure):
        check_clean_checkout(
            wsl={"commit": "", "status": ""},
            windows={"commit": "", "status": ""},
        )
