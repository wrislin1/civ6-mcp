"""Scripted validation trials through the production benchmark runner.

The fake world sits only at the connection transport: `GameState` methods,
the registry, `SingleTurnAgent`, `ScriptedBackend`, the bounded v2 capture
and `BenchmarkStore` are all real. A scripted `fortify_unit` reaches the
fake world through `registry.dispatch -> GameState.fortify_unit ->
conn.execute_write`, and the world change is observed by the real v2 wire
parser between `state_before` and `state_after` in the committed trial file.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from civ_mcp import lua as lq
from civ_mcp.arena import benchmark_scripted_runner as scripted_runner
from civ_mcp.arena.benchmark_agent import EpisodeEvidence, EpisodeTerminal, EpisodeTimedOut
from civ_mcp.arena.benchmark_capture import CaptureTelemetry
from civ_mcp.arena.benchmark_contract_v2 import document_digest
from civ_mcp.arena.benchmark_manifest_v2 import load_toolset, load_v2_document
from civ_mcp.arena.benchmark_runner import BenchmarkRunner, RunnerDependencies
from civ_mcp.arena.benchmark_schedule import ScriptedTrialSpec
from civ_mcp.arena.benchmark_state_v2 import digest_state_v2, normalize_state_v2, parse_state_v2
from civ_mcp.arena.benchmark_store import BenchmarkStore, SessionLockMismatchError
from civ_mcp.arena.benchmark_scripted_runner import ScriptedTransport, run_scripted_suite
from civ_mcp.lua.benchmark_v2 import build_benchmark_state_query_v2


def test_script_identity_does_not_impersonate_a_model():
    spec = ScriptedTrialSpec(index=1, position_id="test", arm_id="validation",
                             script_id="observe", case_id="null")
    assert spec.model is None
    assert spec.seed is None
    assert spec.pair_id is None


# ---------------------------------------------------------------------------
# Fake world at the connection transport
# ---------------------------------------------------------------------------

COVERAGE = {"include_owned_tiles": False, "area": [[10, 10]], "tracked_targets": []}
PLAYER = 0


class FakeWorld:
    def __init__(self) -> None:
        self.drift_on_write = False
        self.reset()

    def reset(self) -> None:
        self.turn = 100
        self.moves = 2

    def wire(self) -> list[str]:
        return [
            f"BEGIN|2.0.0|{document_digest(COVERAGE)}",
            f"IDENTITY|CIVILIZATION_KOREA|7|{self.turn}|0|{PLAYER}|100|12",
            f"UNIT|0|131073|1|UNIT_WARRIOR|combat|10|10|100|100|{self.moves}|0",
            "TILE|10|10|0|TERRAIN_GRASS|NONE|NONE|NONE|0|NONE|1|2|1|0|0|0|0",
            "END|2.0.0|1|1|0|0|0|0|0|1|0",
        ]


class FakeConnection:
    """Answers only the exact Lua the production code sends."""

    def __init__(self, world: FakeWorld) -> None:
        self.world = world
        self.capture_query = build_benchmark_state_query_v2(PLAYER, COVERAGE)
        self.writes: list[str] = []
        self.block_writes: asyncio.Event | None = None
        self.write_entered = asyncio.Event()
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def execute_read(self, lua, timeout=5.0, *, timing=None, retry_on_disconnect=True):
        assert lua == self.capture_query, "unexpected read query"
        assert retry_on_disconnect is False
        if timing is not None:
            timing.update(pre_drain_s=0.0, post_drain_s=0.0, lua_executions=1)
        return self.world.wire()

    async def execute_write(self, lua, timeout=5.0):
        self.writes.append(lua)
        self.write_entered.set()
        if self.block_writes is not None:
            await self.block_writes.wait()
        if lua == lq.build_fortify_unit(1):
            self.world.moves = 0
            if self.world.drift_on_write:
                self.world.turn += 1
            return ["OK:FORTIFIED"]
        if lua == lq.build_fortify_unit(99):
            return ["ERR:UNIT_NOT_FOUND|no unit 99"]
        raise AssertionError(f"unexpected write: {lua[:80]!r}")


def _expected_state(world: FakeWorld) -> dict:
    return normalize_state_v2(parse_state_v2("\n".join(world.wire()), coverage=COVERAGE))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, doc: dict) -> Path:
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


FORTIFY = {"name": "fortify_unit", "arguments": {"unit_index": 1}}
FINISH = {"name": "finish_trial", "arguments": {}}


def write_fixture(root: Path, batches=None, *, tools=("get_units", "fortify_unit")) -> Path:
    """Write toolset/position/script/case/suite documents; return the suite path."""
    root.mkdir(parents=True, exist_ok=True)
    batches = batches if batches is not None else [{"calls": [FORTIFY]}, {"calls": [FINISH]}]
    toolset = _dump(root / "tools.yaml", {"toolset_id": "test-tools", "game_tools": list(tools)})
    (root / "save.Civ6Save").write_bytes(b"SAVE")
    (root / "provenance.json").write_text("{}", encoding="utf-8")
    (root / "observation.json").write_text("{}", encoding="utf-8")
    expected = _expected_state(FakeWorld())
    position = _dump(root / "position.yaml", {
        "schema_version": "2.0.0", "position_id": "scripted-test-v1", "version": 1,
        "family": "test", "split": "development",
        "archive": {"path": "save.Civ6Save", "sha256": _sha(root / "save.Civ6Save")},
        "game_save_name": "SCRIPTED_TEST_V1", "player_id": PLAYER,
        "expected_state": expected, "expected_state_sha256": digest_state_v2(expected),
        "coverage": COVERAGE,
        "toolset": {"path": "tools.yaml", "identity": load_toolset(toolset)["identity"]},
        "contract_identity": "e" * 64,
        "rubric": {"objectives": [{"id": "o", "rungs": [{"points": 4, "predicate": {
            "kind": "unit_in_area", "unit_types": ["UNIT_BUILDER"], "tiles": [[1, 2]]}}]}],
            "harms": []},
        "provenance": {"path": "provenance.json", "sha256": _sha(root / "provenance.json")},
        "public_observation": {"path": "observation.json",
                               "sha256": _sha(root / "observation.json")},
        "public_task_tiles": [[10, 10]], "pilot_informed": False,
    })
    script = _dump(root / "script.yaml", {
        "schema_version": "2.0.0", "script_id": "fortify", "batches": batches})
    case = _dump(root / "case.yaml", {
        "schema_version": "2.0.0", "case_id": "fortify-case",
        "position": {"path": "position.yaml", "sha256": _sha(position)},
        "script": {"path": "script.yaml", "sha256": _sha(script)},
        "tags": ["mechanics"],
        "expected": {"score": {"primary_score": 0.25}, "endpoints": [], "ledger": []},
    })
    return _dump(root / "suite.yaml", {
        "schema_version": "2.0.0", "suite_id": "scripted-test",
        "cases": [{"path": "case.yaml", "sha256": _sha(case)}],
        "position": {"path": "position.yaml", "sha256": _sha(position)},
        "toolset_identity": load_toolset(toolset)["identity"],
        "contract_identity": "e" * 64,
        "max_steps": 15, "episode_wall_s": 300, "result_char_cap": 4000,
        "actor_kind": "scripted", "counting": False,
    })


class Harness:
    def __init__(self, tmp_path: Path, **fixture_kwargs) -> None:
        self.world = FakeWorld()
        self.conn = FakeConnection(self.world)
        self.suite_path = write_fixture(tmp_path / "docs", **fixture_kwargs)
        self.run_dir = tmp_path / "run"
        self.reload_calls = 0
        self.reload_failures = 0
        self.deploys = 0

    async def _reload(self) -> bool:
        self.reload_calls += 1
        if self.reload_failures:
            self.reload_failures -= 1
            raise RuntimeError("reload failed")
        self.world.reset()
        return True

    async def _popups(self) -> str:
        return "POPUPS|none"

    def _deploy(self):
        self.deploys += 1

    def transport(self) -> ScriptedTransport:
        return ScriptedTransport(connection=self.conn, deploy=self._deploy,
                                 reload=self._reload, dismiss_popups=self._popups)

    def suite(self) -> dict:
        return load_v2_document(self.suite_path, kind="validation_suite")

    async def run(self) -> list[dict]:
        return await run_scripted_suite(self.suite(), self.run_dir, dependencies=self.transport())

    def trial(self, index: int = 1) -> dict:
        return json.loads((self.run_dir / "trials" / f"trial-{index:03d}.json").read_text())

    def attempts(self) -> list[Path]:
        return sorted((self.run_dir / "attempts").glob("*.json"))

    def trials(self) -> list[Path]:
        return sorted((self.run_dir / "trials").glob("*.json"))


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

async def test_real_fortify_dispatch_alters_world_between_snapshots(tmp_path):
    h = Harness(tmp_path)
    records = await h.run()

    trial = h.trial()
    assert records == [trial]
    assert h.conn.writes == [lq.build_fortify_unit(1)]
    assert h.attempts() == []

    (step,) = trial["steps"]
    assert step["tool_name"] == "fortify_unit"
    assert step["tool_result_full"] == "FORTIFIED"
    assert step["state_before"]["units"][0]["moves"] == 2
    assert step["state_after"]["units"][0]["moves"] == 0
    assert step["state_digest_before"] != step["state_digest_after"]
    assert trial["initial_state"]["units"][0]["moves"] == 2
    assert trial["final_state"]["units"][0]["moves"] == 0

    assert trial["validation_status"] == "passed_mechanics"
    assert trial["validation_failures"] == []
    assert trial["terminal"] == "finish_trial"
    assert trial["evidence_version"] == "2.0.0"
    assert trial["actor_kind"] == "scripted"
    assert trial["counting"] is False
    assert trial["script_id"] == "fortify"
    assert trial["script_sha256"] == _sha(tmp_path / "docs" / "script.yaml")
    assert trial["case_id"] == "fortify-case"
    assert trial["case_sha256"] == _sha(tmp_path / "docs" / "case.yaml")
    assert trial["toolset_id"] == "test-tools"
    assert trial["toolset_identity"] == load_toolset(tmp_path / "docs" / "tools.yaml")["identity"]
    assert trial["coverage"] == COVERAGE
    assert isinstance(trial["contract_fingerprint"], str) and trial["contract_fingerprint"]
    assert (trial["round_trips"], trial["round_trips_completed"]) == (2, 2)
    assert (trial["tool_call_attempts"], trial["dispatched_calls"]) == (1, 1)
    assert trial["capture_summary"]["count"] == 4  # initial, before, after, final
    assert trial["capture_summary"]["all_single_execution"] is True
    assert isinstance(trial["wall_clock_s"], float)
    assert trial["attempt_count"] == 1
    for field in ("model", "seed", "pair_id", "prompt_tokens", "completion_tokens",
                  "cost_usd", "model_latency_s"):
        assert field in trial and trial[field] is None, field
    # The expected case never reaches the actor or the raw evidence.
    assert "expected" not in json.dumps(trial)
    assert "primary_score" not in json.dumps(trial)


async def test_lock_binds_script_case_schedule_and_null_model_fields(tmp_path):
    h = Harness(tmp_path)
    await h.run()
    lock = json.loads((h.run_dir / "session.json").read_text())
    assert lock["actor"]["actor_kind"] == "scripted"
    assert lock["actor"]["counting"] is False
    for field in ("model", "seed", "token_budget", "cost", "latency"):
        assert lock[field] is None
    assert [ref["sha256"] for ref in lock["scripts"]] == [_sha(tmp_path / "docs" / "script.yaml")]
    assert [ref["sha256"] for ref in lock["cases"]] == [_sha(tmp_path / "docs" / "case.yaml")]
    assert lock["schedule"] == [{
        "index": 1, "position_id": "scripted-test-v1", "arm_id": "validation",
        "script_id": "fortify", "case_id": "fortify-case",
        "model": None, "seed": None, "pair_id": None,
    }]
    assert lock["limits"] == {"max_steps": 15, "episode_wall_s": 300, "result_char_cap": 4000}
    assert lock["session_fingerprint"] == h.trial()["session_fingerprint"]
    # No suite file supplied: the lock records the suite id and a portable digest.
    assert lock["suite"]["bound"] == "portable"
    assert lock["suite"]["path"] == lock["lock_id"].removesuffix(":scripted")


def test_lock_suite_bound_must_be_bytes_or_portable():
    from civ_mcp.arena.benchmark_manifest_v2 import _validate_lock
    lock = {"lock_id": "x", "suite": {"path": "s", "sha256": "a", "bound": "nope"},
            "scripts": [{"path": "p", "sha256": "a"}], "cases": [{"path": "c", "sha256": "a"}]}
    with pytest.raises(ValueError, match="bound"):
        _validate_lock(lock)


async def test_changed_script_is_refused_by_the_existing_lock(tmp_path):
    h = Harness(tmp_path)
    await h.run()
    write_fixture(tmp_path / "docs", [{"calls": [FORTIFY, FINISH]}])
    with pytest.raises(SessionLockMismatchError):
        await h.run()
    assert len(h.trials()) == 1


async def test_recorded_digest_mismatch_fails_before_any_trial(tmp_path):
    h = Harness(tmp_path)
    (tmp_path / "docs" / "script.yaml").write_text(
        (tmp_path / "docs" / "script.yaml").read_text() + "\n# edited\n")
    with pytest.raises(ValueError, match="script"):
        await h.run()
    assert not (h.run_dir / "session.json").exists()
    assert h.reload_calls == 0


async def test_script_naming_a_tool_outside_the_toolset_is_refused_before_any_trial(tmp_path):
    h = Harness(tmp_path, batches=[{"calls": [{"name": "get_cities", "arguments": {}}]},
                                   {"calls": [FINISH]}])
    with pytest.raises(ValueError, match="get_cities"):
        await h.run()
    assert not (h.run_dir / "session.json").exists()


async def test_failed_attempt_resumes_to_one_commit(tmp_path):
    h = Harness(tmp_path)
    h.reload_failures = 1
    await h.run()
    (attempt,) = h.attempts()
    assert json.loads(attempt.read_text())["failure_class"] == "reload_or_reconnect_failure"
    assert len(h.trials()) == 1
    assert h.trial()["attempt_count"] == 2
    assert h.trial()["validation_status"] == "passed_mechanics"


async def test_rerun_does_not_duplicate_commit(tmp_path):
    h = Harness(tmp_path)
    first = await h.run()
    before = (h.run_dir / "trials" / "trial-001.json").read_bytes()
    calls = h.reload_calls
    second = await h.run()
    assert second == first
    assert h.reload_calls == calls
    assert (h.run_dir / "trials" / "trial-001.json").read_bytes() == before


async def test_external_cancellation_propagates_without_attempt_or_commit(tmp_path):
    h = Harness(tmp_path)
    h.conn.block_writes = asyncio.Event()
    task = asyncio.create_task(h.run())
    await asyncio.wait_for(h.conn.write_entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.attempts() == []
    assert h.trials() == []


async def test_rejected_required_operation_is_committed_as_failed(tmp_path):
    h = Harness(tmp_path, batches=[{"calls": [{"name": "fortify_unit",
                                               "arguments": {"unit_index": 99}}]},
                                   {"calls": [FINISH]}])
    await h.run()
    trial = h.trial()
    assert trial["validation_status"] == "failed"
    assert [f["reason"] for f in trial["validation_failures"]] == ["rejected_operation"]
    assert trial["validation_failures"][0]["step"] == 0
    assert trial["steps"][0]["tool_result_full"].startswith("Error: UNIT_NOT_FOUND")
    assert h.attempts() == []


async def test_unconsumed_script_and_step_cap_fail_validation(tmp_path):
    batches = [{"calls": [FORTIFY]} for _ in range(15)] + [{"calls": [FINISH]}]
    h = Harness(tmp_path, batches=batches)
    await h.run()
    trial = h.trial()
    assert trial["terminal"] == "step_limit"
    assert trial["round_trips"] == 15
    reasons = {f["reason"] for f in trial["validation_failures"]}
    assert reasons == {"script_unconsumed", "no_explicit_finish"}
    assert trial["validation_status"] == "failed"
    assert len(trial["steps"]) == 15


async def test_turn_drift_fails_validation_with_raw_evidence(tmp_path):
    h = Harness(tmp_path)
    h.world.drift_on_write = True
    await h.run()
    trial = h.trial()
    assert trial["validation_status"] == "failed"
    drift = [f for f in trial["validation_failures"] if f["reason"] == "identity_drift"]
    assert {(f["phase"], f["field"]) for f in drift} == {("step 0 after", "turn"), ("final", "turn")}
    assert trial["steps"][0]["state_after"]["turn"] == 101


async def test_production_wiring_deploys_and_requires_confirmed_reload(tmp_path, monkeypatch):
    world = FakeWorld()
    conn = FakeConnection(world)
    deploys: list[tuple] = []
    reloads: list[object] = []
    verified = iter([False, True])

    def fake_deploy(archive, save_name, sha256):
        deploys.append((archive, save_name, sha256))
        return SimpleNamespace(ok=True)

    async def fake_reload(connection, position):
        assert connection is conn
        reloads.append(position.game_save_name)
        world.reset()
        return next(verified)

    async def fake_popups(connection):
        assert connection is conn
        return "POPUPS|none"

    monkeypatch.setattr(scripted_runner, "deploy_via_windows", fake_deploy)
    monkeypatch.setattr(scripted_runner, "GameConnection", lambda: conn)
    monkeypatch.setattr(scripted_runner, "reload_position", fake_reload)
    monkeypatch.setattr(scripted_runner, "dismiss_blocking_popups", fake_popups)

    suite_path = write_fixture(tmp_path / "docs")
    suite = load_v2_document(suite_path, kind="validation_suite")
    records = await run_scripted_suite(suite, tmp_path / "run")

    archive = tmp_path / "docs" / "save.Civ6Save"
    assert deploys == [(str(archive), "SCRIPTED_TEST_V1", _sha(archive))]
    assert reloads == ["SCRIPTED_TEST_V1", "SCRIPTED_TEST_V1"]
    (attempt,) = sorted((tmp_path / "run" / "attempts").glob("*.json"))
    assert json.loads(attempt.read_text())["failure_class"] == "reload_or_reconnect_failure"
    assert records[0]["validation_status"] == "passed_mechanics"
    assert conn.connected is False


def test_production_transport_hands_the_bridge_a_repo_relative_archive(monkeypatch):
    """`load_v2_document` resolves the archive to an absolute local path; the
    Windows bridge (cwd = Windows checkout) must get the repo-relative one."""
    deploys: list[tuple] = []
    monkeypatch.setattr(scripted_runner, "deploy_via_windows",
                        lambda *args: deploys.append(args))
    monkeypatch.setattr(scripted_runner, "GameConnection", lambda: object())
    local = scripted_runner._REPO_ROOT / "benchmarks/saves/builder-a1-v1.Civ6Save"
    transport = scripted_runner._production_transport({
        "archive": {"path": str(local), "sha256": "ab"}, "game_save_name": "S"})
    transport.deploy()
    assert deploys == [("benchmarks/saves/builder-a1-v1.Civ6Save", "S", "ab")]


# ---------------------------------------------------------------------------
# Runner branch: scripted timeout has no model canary
# ---------------------------------------------------------------------------

class _TimingOutAgent:
    episode_wall_s = 300.0

    def __init__(self) -> None:
        self.backend = SimpleNamespace(assert_exhausted=lambda: None)

    async def run(self, gs, player_id, turn):
        partial = EpisodeEvidence(
            terminal=EpisodeTerminal.STEP_LIMIT, steps=[], invalid_tool_calls=[],
            final_summary="", wall_clock_s=300.0, prompt_tokens=0, completion_tokens=0,
            round_trips=3, round_trips_completed=2, evidence_version="2.0.0")
        raise EpisodeTimedOut("too slow", partial_evidence=partial)


async def test_scripted_timeout_commits_validation_failure_without_model_canary(tmp_path):
    world = FakeWorld()
    conn = FakeConnection(world)
    expected = _expected_state(world)
    probes: list[int] = []

    async def capture():
        return _expected_state(world)

    async def ok_reload(_position_id):
        return True

    async def popups():
        return "POPUPS|none"

    async def probe():
        probes.append(1)
        raise AssertionError("scripted trials have no model canary")

    store = BenchmarkStore.create(tmp_path / "run", {"session_fingerprint": "s"})
    deps = RunnerDependencies(
        reload_position=ok_reload, dismiss_popups=popups, capture_state=capture,
        make_agent=lambda spec: _TimingOutAgent(), probe_health=probe, connection=conn,
        capture_telemetry=CaptureTelemetry(),
        trial_identity=lambda spec: {"script_id": spec.script_id},
    )
    runner = BenchmarkRunner(store=store, dependencies=deps, expected_state=expected,
                             player_id=PLAYER)
    spec = ScriptedTrialSpec(index=1, position_id="p", arm_id="validation",
                             script_id="s", case_id="c")
    await runner.run([spec])

    assert probes == []
    trial = json.loads((tmp_path / "run" / "trials" / "trial-001.json").read_text())
    assert trial["terminal"] == "episode_timeout"
    assert trial["terminal"] != "runaway_timeout"
    assert trial["validation_status"] == "failed"
    (failure,) = trial["validation_failures"]
    assert failure["reason"] == "episode_timeout"
    assert failure["game_health"]["healthy"] is True
    assert failure["game_health"]["turn"] == 100
    assert (trial["round_trips"], trial["round_trips_completed"]) == (3, 2)
    assert list((tmp_path / "run" / "attempts").glob("*.json")) == []
