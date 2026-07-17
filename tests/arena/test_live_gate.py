import json
import os
import stat
from collections import deque
from types import SimpleNamespace

import pytest

from civ_mcp.arena import live_gate
from civ_mcp.arena.live_gate import (
    GATE_ACTIVE,
    GATE_FAILED,
    GATE_PASSED,
    GATE_RESTART_REQUIRED,
    GATE_SCHEMA_VERSION,
    GateEvent,
    GateStateError,
    LiveGateJournal,
    ScenarioMeta,
    register_scenario,
    resolve_live_gate_driver,
    resolve_scenario,
)


FINGERPRINT = {"scenario": "demo_v1", "run_id": "run-g", "rules": {"x": 1}}


def open_journal(tmp_path, **overrides):
    kwargs = dict(
        run_id="run-g",
        scenario="demo_v1",
        scenario_revision=1,
        roles={"actor": 1, "observer": 2},
        config_fingerprint=FINGERPRINT,
        initial_phase="preflight",
    )
    kwargs.update(overrides)
    return LiveGateJournal.open(tmp_path, **kwargs)


def planned(source_id="api:run-g:1:5:0:abc", **overrides):
    payload = {
        "turn": 5,
        "player_id": 1,
        "phase": "preflight",
        "name": "send_message",
        "source_id": source_id,
        "payload_digest": "d" * 16,
    }
    payload.update(overrides)
    return payload


def test_open_initializes_identity_and_private_files(tmp_path):
    journal = open_journal(tmp_path)
    state = journal.state
    assert state.run_id == "run-g"
    assert state.scenario == "demo_v1"
    assert state.scenario_revision == 1
    assert state.roles == (("actor", 1), ("observer", 2))
    assert state.config_fingerprint == FINGERPRINT
    assert state.phase == "preflight"
    assert state.status == GATE_ACTIVE
    assert state.restart_count == 0
    assert state.last_event_sequence == 1  # gate_initialized
    gate_dir = tmp_path / "live_gate"
    assert stat.S_IMODE(os.stat(gate_dir).st_mode) == 0o700
    for path in (journal.events_path, journal.state_path):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not journal.result_path.exists()


def test_append_reduce_snapshot_reopen_equivalence(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("phase_advanced", {"phase": "act", "turn": 5})
    journal.append("data_recorded", {"data": {"deal_id": "deal-000007"}})
    journal.append(
        "observation_recorded",
        {"turn": 5, "player_id": 1, "families": ["treasury"]},
    )
    reopened = open_journal(tmp_path)
    assert reopened.state == journal.state
    assert reopened.state.phase == "act"
    assert reopened.state.data == {"deal_id": "deal-000007"}
    assert len(reopened.state.observations) == 1


def test_action_planned_then_verified_lifecycle(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("action_planned", planned())
    assert len(journal.state.pending_actions) == 1
    journal.append(
        "action_verified",
        {"source_id": "api:run-g:1:5:0:abc", "turn": 5},
    )
    assert journal.state.pending_actions == ()
    assert len(journal.state.verified_actions) == 1


def test_phase_advance_blocked_by_unverified_action(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("action_planned", planned())
    with pytest.raises(GateStateError):
        journal.append("phase_advanced", {"phase": "next", "turn": 5})


def test_action_verified_requires_matching_plan(tmp_path):
    journal = open_journal(tmp_path)
    with pytest.raises(GateStateError):
        journal.append(
            "action_verified",
            {"source_id": "api:run-g:1:5:0:zzz", "turn": 5},
        )


def test_duplicate_planned_source_rejected(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("action_planned", planned())
    with pytest.raises(GateStateError):
        journal.append("action_planned", planned())


def test_second_restart_required_rejected(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("restart_required", {"turn": 6})
    assert journal.state.status == GATE_RESTART_REQUIRED
    assert journal.state.restart_count == 1
    journal.append("restart_verified", {"turn": 7})
    assert journal.state.status == GATE_ACTIVE
    with pytest.raises(GateStateError):
        journal.append("restart_required", {"turn": 8})


def test_restart_verified_only_from_restart_required(tmp_path):
    journal = open_journal(tmp_path)
    with pytest.raises(GateStateError):
        journal.append("restart_verified", {"turn": 6})


def test_terminal_states_reject_further_events(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("gate_failed", {"reason": "boom"})
    assert journal.state.status == GATE_FAILED
    assert journal.state.reason == "boom"
    with pytest.raises(GateStateError):
        journal.append("phase_advanced", {"phase": "next", "turn": 6})

    passed = open_journal(tmp_path.parent / "p2")
    passed.append("gate_passed", {"evidence": {"honored": 1}})
    assert passed.state.status == GATE_PASSED
    with pytest.raises(GateStateError):
        passed.append("data_recorded", {"data": {}})


def test_privacy_fail_permits_only_gate_failed(tmp_path):
    journal = open_journal(tmp_path)
    journal.append(
        "privacy_asserted",
        {
            "turn": 5,
            "player_id": 2,
            "artifact_kind": "projection",
            "input_digest": "a" * 16,
            "forbidden_digests": [],
            "result": "FAIL",
        },
    )
    with pytest.raises(GateStateError):
        journal.append("phase_advanced", {"phase": "next", "turn": 5})
    journal.append("gate_failed", {"reason": "privacy"})
    assert journal.state.status == GATE_FAILED


def test_privacy_failure_allows_only_remaining_assertions_for_same_capture(tmp_path):
    journal = open_journal(tmp_path)
    common = {
        "turn": 5,
        "player_id": 2,
        "input_digest": "a" * 16,
        "forbidden_digests": [],
        "capture_artifact_kinds": ("projection", "channel_block"),
    }
    journal.append(
        "privacy_asserted",
        {**common, "artifact_kind": "projection", "result": "FAIL"},
    )

    journal.append(
        "privacy_asserted",
        {**common, "artifact_kind": "channel_block", "result": "PASS"},
    )
    with pytest.raises(GateStateError):
        journal.append(
            "privacy_asserted",
            {
                **common,
                "turn": 6,
                "artifact_kind": "opening_prompt",
                "result": "PASS",
            },
        )

    journal.append("gate_failed", {"reason": "privacy"})
    assert journal.state.status == GATE_FAILED


def declared_privacy_payload(
    artifact_kind="projection",
    *,
    declaration=("projection", "channel_block"),
    result="PASS",
    turn=5,
):
    return {
        "turn": turn,
        "player_id": 2,
        "artifact_kind": artifact_kind,
        "capture_artifact_kinds": declaration,
        "input_digest": "a" * 16,
        "forbidden_digests": [],
        "result": result,
    }


@pytest.mark.parametrize("artifact_kind", [None, "", [], {}])
def test_privacy_artifact_kind_must_be_nonempty_string(tmp_path, artifact_kind):
    journal = open_journal(tmp_path)

    with pytest.raises(GateStateError, match="artifact_kind"):
        journal.append(
            "privacy_asserted",
            declared_privacy_payload(artifact_kind),
        )


@pytest.mark.parametrize(
    "declaration",
    [
        None,
        {},
        "projection",
        (),
        ("projection", "projection"),
        ("projection", ""),
        ("projection", []),
        tuple(f"kind-{index}" for index in range(65)),
    ],
)
def test_privacy_declaration_must_be_bounded_unique_string_sequence(
    tmp_path, declaration
):
    journal = open_journal(tmp_path)

    with pytest.raises(GateStateError, match="capture_artifact_kinds"):
        journal.append(
            "privacy_asserted",
            declared_privacy_payload(declaration=declaration),
        )


def test_privacy_declaration_must_contain_current_kind(tmp_path):
    journal = open_journal(tmp_path)

    with pytest.raises(GateStateError, match="artifact_kind"):
        journal.append(
            "privacy_asserted",
            declared_privacy_payload(
                "eighth_unknown",
                declaration=("projection", "channel_block"),
            ),
        )


def test_privacy_capture_rejects_duplicate_kind_and_declaration_mismatch(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("privacy_asserted", declared_privacy_payload())

    with pytest.raises(GateStateError, match="duplicate privacy artifact"):
        journal.append("privacy_asserted", declared_privacy_payload())
    with pytest.raises(GateStateError, match="declaration"):
        journal.append(
            "privacy_asserted",
            declared_privacy_payload(
                "channel_block",
                declaration=("channel_block", "projection"),
            ),
        )


def test_failed_privacy_batch_is_finite_then_allows_gate_failed(tmp_path):
    journal = open_journal(tmp_path)
    declaration = ("projection", "channel_block")
    journal.append(
        "privacy_asserted",
        declared_privacy_payload(
            "projection", declaration=declaration, result="FAIL"
        ),
    )
    journal.append(
        "privacy_asserted",
        declared_privacy_payload("channel_block", declaration=declaration),
    )

    with pytest.raises(GateStateError):
        journal.append(
            "privacy_asserted",
            declared_privacy_payload("channel_block", declaration=declaration),
        )
    with pytest.raises(GateStateError):
        journal.append(
            "privacy_asserted",
            declared_privacy_payload(
                "eighth_unknown",
                declaration=("projection", "channel_block", "eighth_unknown"),
            ),
        )

    journal.append("gate_failed", {"reason": "privacy"})
    assert journal.state.status == GATE_FAILED


def test_legacy_privacy_fail_without_declaration_allows_only_gate_failed(tmp_path):
    journal = open_journal(tmp_path)
    legacy = declared_privacy_payload(result="FAIL")
    legacy.pop("capture_artifact_kinds")
    journal.append("privacy_asserted", legacy)

    with pytest.raises(GateStateError):
        journal.append(
            "privacy_asserted",
            declared_privacy_payload("channel_block"),
        )
    journal.append("gate_failed", {"reason": "privacy"})
    assert journal.state.status == GATE_FAILED


def test_gate_passed_requires_active_status(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("restart_required", {"turn": 6})
    with pytest.raises(GateStateError):
        journal.append("gate_passed", {"evidence": {}})


def test_reopen_identity_mismatch_fails(tmp_path):
    open_journal(tmp_path)
    with pytest.raises(GateStateError):
        open_journal(tmp_path, run_id="other-run")
    with pytest.raises(GateStateError):
        open_journal(
            tmp_path,
            config_fingerprint={"scenario": "demo_v1", "changed": True},
        )
    with pytest.raises(GateStateError):
        open_journal(tmp_path, roles={"actor": 1, "observer": 9})


def test_snapshot_newer_than_journal_rejected(tmp_path):
    journal = open_journal(tmp_path)
    snapshot = json.loads(journal.state_path.read_text())
    snapshot["last_event_sequence"] = 99
    journal.state_path.write_text(json.dumps(snapshot))
    with pytest.raises(GateStateError):
        open_journal(tmp_path)


def test_symlinked_journal_rejected(tmp_path):
    journal = open_journal(tmp_path)
    real = tmp_path / "elsewhere.jsonl"
    real.write_text(journal.events_path.read_text())
    journal.events_path.unlink()
    journal.events_path.symlink_to(real)
    with pytest.raises(GateStateError):
        open_journal(tmp_path)


def test_result_written_only_for_signal_states(tmp_path):
    journal = open_journal(tmp_path)
    with pytest.raises(GateStateError):
        journal.write_result()
    journal.append("restart_required", {"turn": 6})
    journal.write_result()
    payload = json.loads(journal.result_path.read_text())
    assert payload["status"] == GATE_RESTART_REQUIRED
    assert payload["run_id"] == "run-g"
    assert payload["restart_count"] == 1
    assert payload["schema_version"] == GATE_SCHEMA_VERSION
    assert stat.S_IMODE(os.stat(journal.result_path).st_mode) == 0o600


def test_restart_verified_removes_restart_result(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("restart_required", {"turn": 6})
    journal.write_result()
    assert journal.result_path.exists()

    journal.append("restart_verified", {"turn": 7})

    assert journal.state.status == GATE_ACTIVE
    assert not journal.result_path.exists()


def test_reopen_active_crash_boundary_removes_matching_restart_result(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("restart_required", {"turn": 6})
    journal.write_result()
    restart_verified = {
        "schema_version": GATE_SCHEMA_VERSION,
        "sequence": journal.state.last_event_sequence + 1,
        "kind": "restart_verified",
        "payload": {"turn": 7},
    }
    with journal.events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(restart_verified, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())

    reopened = open_journal(tmp_path)

    assert reopened.state.status == GATE_ACTIVE
    assert reopened.state.last_event_sequence == restart_verified["sequence"]
    assert not reopened.result_path.exists()


def test_fresh_active_journal_rejects_unrelated_stale_result(tmp_path):
    gate_dir = tmp_path / "live_gate"
    gate_dir.mkdir(mode=0o700)
    (gate_dir / "result.json").write_text(
        json.dumps({"status": GATE_RESTART_REQUIRED, "run_id": "unrelated"})
    )

    with pytest.raises(GateStateError, match="result.json"):
        open_journal(tmp_path)


def test_restart_result_cleanup_rejects_symlink_without_touching_target(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("restart_required", {"turn": 6})
    journal.write_result()
    target = tmp_path / "outside-result.json"
    target.write_text("outside")
    journal.result_path.unlink()
    journal.result_path.symlink_to(target)

    with pytest.raises(GateStateError):
        journal.append("restart_verified", {"turn": 7})

    assert journal.result_path.is_symlink()
    assert target.read_text() == "outside"


def test_restart_result_cleanup_rejects_non_regular_path(tmp_path):
    journal = open_journal(tmp_path)
    journal.append("restart_required", {"turn": 6})
    journal.write_result()
    journal.result_path.unlink()
    journal.result_path.mkdir()

    with pytest.raises(GateStateError):
        journal.append("restart_verified", {"turn": 7})

    assert journal.result_path.is_dir()


def test_unknown_event_kind_rejected(tmp_path):
    journal = open_journal(tmp_path)
    with pytest.raises(GateStateError):
        journal.append("mystery_event", {})


def test_event_and_state_are_deeply_immutable_and_detached_from_inputs(tmp_path):
    fingerprint = {
        "scenario": "demo_v1",
        "run_id": "run-g",
        "rules": {"limits": [1, {"window": 2}]},
    }
    expected_fingerprint = {
        "scenario": "demo_v1",
        "run_id": "run-g",
        "rules": {"limits": [1, {"window": 2}]},
    }
    journal = open_journal(tmp_path, config_fingerprint=fingerprint)
    payload = {"data": {"nested": {"items": [1, {"value": "kept"}]}}}
    expected_payload = {"data": {"nested": {"items": [1, {"value": "kept"}]}}}

    event = journal.append("data_recorded", payload)
    fingerprint["rules"]["limits"][1]["window"] = 99
    payload["data"]["nested"]["items"][1]["value"] = "changed"
    payload["data"]["nested"]["items"].append(2)

    assert event.payload == expected_payload
    assert journal.state.config_fingerprint == expected_fingerprint
    assert journal.state.data == expected_payload["data"]
    with pytest.raises(TypeError):
        event.payload["data"]["nested"]["new"] = True
    with pytest.raises(AttributeError):
        journal.state.data["nested"]["items"].append(3)

    reopened = open_journal(tmp_path, config_fingerprint=expected_fingerprint)
    assert reopened.state == journal.state


def test_gate_event_constructor_deeply_freezes_payload():
    payload = {"nested": {"items": ["kept"]}}
    event = GateEvent(GATE_SCHEMA_VERSION, 1, "data_recorded", payload)
    payload["nested"]["items"][0] = "changed"
    assert event.payload == {"nested": {"items": ["kept"]}}
    with pytest.raises(TypeError):
        event.payload["nested"]["extra"] = True


def test_unsupported_mutable_nested_value_is_rejected(tmp_path):
    journal = open_journal(tmp_path)
    mutable = deque(["caller-owned"])

    with pytest.raises(GateStateError, match="unsupported gate value type deque"):
        journal.append("data_recorded", {"data": {"mutable": mutable}})

    mutable.append("changed")
    assert journal.state.data == {}
    assert journal.state.last_event_sequence == 1


@pytest.mark.parametrize(
    "value",
    [{"alpha", "beta"}, frozenset({"alpha", "beta"})],
    ids=["set", "frozenset"],
)
def test_set_like_values_are_rejected_before_state_or_persistence_mutation(
    tmp_path, value
):
    journal = open_journal(tmp_path)
    journal_before = journal.events_path.read_bytes()

    with pytest.raises(GateStateError, match="unsupported gate value type"):
        journal.append("data_recorded", {"data": {"value": value}})

    assert journal.state.data == {}
    assert journal.state.last_event_sequence == 1
    assert journal.events_path.read_bytes() == journal_before


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_non_finite_floats_are_rejected_before_persistence(tmp_path, value):
    journal = open_journal(tmp_path)
    journal_before = journal.events_path.read_bytes()

    with pytest.raises(GateStateError, match="non-finite float"):
        journal.append("data_recorded", {"data": {"value": value}})

    assert journal.state.data == {}
    assert journal.state.last_event_sequence == 1
    assert journal.events_path.read_bytes() == journal_before


def test_public_tuples_normalize_to_reopen_safe_json_arrays(tmp_path):
    fingerprint = {"rules": {"windows": (1, {"turns": (2, 3)})}}
    expected_fingerprint = {"rules": {"windows": [1, {"turns": [2, 3]}]}}
    journal = open_journal(tmp_path, config_fingerprint=fingerprint)

    event = journal.append(
        "data_recorded",
        {"data": {"path": ("start", {"steps": ("middle", "end")})}},
    )
    expected_data = {"path": ["start", {"steps": ["middle", "end"]}]}

    assert event.payload == {"data": expected_data}
    assert journal.state.config_fingerprint == expected_fingerprint
    assert journal.state.data == expected_data
    assert type(journal.state.data["path"]) is type(event.payload["data"]["path"])

    reopened = open_journal(tmp_path, config_fingerprint=expected_fingerprint)
    assert reopened.state == journal.state
    assert type(reopened.state.data["path"]) is type(journal.state.data["path"])
    assert isinstance(reopened.state.pending_actions, tuple)
    assert isinstance(reopened.state.verified_actions, tuple)


def test_supported_nested_fingerprint_and_data_reopen_exactly(tmp_path):
    fingerprint = {
        "rules": {
            "thresholds": [1, 2.5, None],
            "flags": {"enabled": True, "strict": False},
        }
    }
    data = {
        "nested": [
            {"name": "alpha", "values": [0, 3.25, False]},
            {"name": "beta", "values": []},
        ]
    }
    journal = open_journal(tmp_path, config_fingerprint=fingerprint)
    journal.append("data_recorded", {"data": data})

    reopened = open_journal(tmp_path, config_fingerprint=fingerprint)

    assert reopened.state == journal.state
    assert reopened.state.config_fingerprint == fingerprint
    assert reopened.state.data == data


@pytest.mark.parametrize("artifact_name", ["events.jsonl", "state.json", "result.json"])
def test_symlinked_gate_artifacts_are_rejected(tmp_path, artifact_name):
    journal = open_journal(tmp_path)
    artifact = journal.gate_dir / artifact_name
    target = tmp_path / f"outside-{artifact_name}"
    target.write_text("outside")
    if artifact.exists():
        artifact.unlink()
    artifact.symlink_to(target)

    if artifact_name == "result.json":
        journal.append("restart_required", {"turn": 6})
        with pytest.raises(GateStateError):
            journal.write_result()
    else:
        with pytest.raises(GateStateError):
            open_journal(tmp_path)
    assert target.read_text() == "outside"


@pytest.mark.parametrize("artifact_name", ["events.jsonl", "state.json", "result.json"])
def test_non_regular_gate_artifacts_are_rejected(tmp_path, artifact_name):
    journal = open_journal(tmp_path)
    artifact = journal.gate_dir / artifact_name
    if artifact.exists():
        artifact.unlink()
    artifact.mkdir()

    if artifact_name == "result.json":
        journal.append("restart_required", {"turn": 6})
        with pytest.raises(GateStateError):
            journal.write_result()
    else:
        with pytest.raises(GateStateError):
            open_journal(tmp_path)


def test_private_json_helper_writes_mode_0600(tmp_path):
    journal = open_journal(tmp_path)

    path = journal.write_private_json(
        "privacy_fail_10_projection.json", {"input": "private", "turn": 10}
    )

    assert json.loads(path.read_text()) == {"input": "private", "turn": 10}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.parametrize(
    "basename",
    ["", ".", "..", "../escape.json", "nested/file.json", "nested\\file.json", "state.json"],
)
def test_private_json_helper_rejects_unsafe_basename(tmp_path, basename):
    journal = open_journal(tmp_path)

    with pytest.raises(GateStateError, match="basename"):
        journal.write_private_json(basename, {"private": True})


def test_private_json_helper_refuses_symlink_without_clobbering_target(tmp_path):
    journal = open_journal(tmp_path)
    target = tmp_path / "outside-private.json"
    target.write_text("outside")
    artifact = journal.gate_dir / "privacy_fail_10_projection.json"
    artifact.symlink_to(target)

    with pytest.raises(GateStateError):
        journal.write_private_json(artifact.name, {"input": "secret"})

    assert artifact.is_symlink()
    assert target.read_text() == "outside"


def test_private_json_helper_refuses_non_regular_destination(tmp_path):
    journal = open_journal(tmp_path)
    artifact = journal.gate_dir / "privacy_fail_10_projection.json"
    artifact.mkdir()

    with pytest.raises(GateStateError):
        journal.write_private_json(artifact.name, {"input": "secret"})

    assert artifact.is_dir()


def fake_meta(name="fake_gate_v1", **overrides):
    kwargs = dict(
        name=name,
        revision=1,
        role_contracts=(("actor", "in_process"), ("observer", "scripted")),
        minimum_captures=lambda config: 6,
        create_driver=lambda config: SimpleNamespace(config=config, kind="fake-driver"),
    )
    kwargs.update(overrides)
    return ScenarioMeta(**kwargs)


def test_register_and_resolve_scenario(monkeypatch):
    monkeypatch.setattr(live_gate, "_SCENARIOS", {})
    meta = fake_meta()
    register_scenario(meta)
    assert resolve_scenario("fake_gate_v1") is meta
    with pytest.raises(ValueError):
        register_scenario(fake_meta())  # duplicate name


def test_resolve_unknown_scenario_rejected(monkeypatch):
    monkeypatch.setattr(live_gate, "_SCENARIOS", {})
    with pytest.raises(ValueError, match="unknown live-gate scenario"):
        resolve_scenario("nope_v1")


def test_resolve_live_gate_driver_disabled_returns_none():
    config = SimpleNamespace(live_gate=SimpleNamespace(enabled=False, scenario="", roles=()))
    assert resolve_live_gate_driver(config) is None
    assert resolve_live_gate_driver(SimpleNamespace()) is None  # attribute missing


def test_resolve_live_gate_driver_enabled_creates_driver(monkeypatch):
    monkeypatch.setattr(live_gate, "_SCENARIOS", {})
    register_scenario(fake_meta())
    config = SimpleNamespace(
        live_gate=SimpleNamespace(enabled=True, scenario="fake_gate_v1", roles=(("actor", 1),))
    )
    driver = resolve_live_gate_driver(config)
    assert driver.kind == "fake-driver"
    assert driver.config is config
