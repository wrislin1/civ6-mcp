"""Tests for the strictly-serial controlled-position benchmark runner.

Most tests here use plain async fakes / `AsyncMock` -- no live game, no
network. `RunnerDependencies` is the seam: most production wiring (in
`benchmark_runner._build_live_dependencies` / `main`) is not exercised via a
live game or network, but `_build_live_dependencies.make_agent`'s arm-options
fail-closed check (below) is a pure function of manifest data and is tested
directly.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import openai
import pytest
from unittest.mock import AsyncMock

import civ_mcp.arena.benchmark_runner as benchmark_runner
from civ_mcp.arena.backends import SamplingConfig
from civ_mcp.arena.benchmark_agent import EpisodeEvidence, EpisodeTerminal, EpisodeTimedOut
from civ_mcp.arena.benchmark_backend import HealthProbe
from civ_mcp.arena.benchmark_manifest import PositionManifest, SuiteManifest, TreatmentArm
from civ_mcp.arena.benchmark_report import build_report
from civ_mcp.arena.benchmark_runner import (
    BenchmarkRunner,
    FailureClass,
    RunnerDependencies,
    SessionAborted,
)
from civ_mcp.arena.benchmark_schedule import TrialSpec
from civ_mcp.arena.benchmark_state import BenchmarkStateError
from civ_mcp.arena.benchmark_store import BenchmarkStore

CANONICAL = {"turn": 157, "player_id": 0, "units": [], "cities": [], "tiles": []}


def _lock() -> dict:
    return {"session_fingerprint": "abc123", "schedule_fingerprint": "def456"}


def _spec(
    index: int,
    arm_id: str,
    *,
    position_id: str = "builder-cal-v1",
    model: str = "qwen3.6-27b",
    seed: int = 101,
    pair_id: str = "pair-001",
) -> TrialSpec:
    return TrialSpec(
        index=index, pair_id=pair_id, position_id=position_id, model=model, arm_id=arm_id, seed=seed
    )


class _FinishingAgent:
    """A normal, complete finish_trial episode."""

    async def run(self, gs, player_id, turn):
        return EpisodeEvidence(
            terminal=EpisodeTerminal.FINISH_TRIAL,
            steps=[{"idx": 0, "tool_name": "get_units"}],
            invalid_tool_calls=[],
            final_summary="done",
            wall_clock_s=1.2,
            prompt_tokens=10,
            completion_tokens=5,
        )


class _RaisingAgent:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def run(self, gs, player_id, turn):
        raise self._exc


def _healthy_probe(model: str = "qwen3.6-27b") -> HealthProbe:
    return HealthProbe(healthy=True, model=model, latency_s=0.2, error=None)


def _unhealthy_probe() -> HealthProbe:
    return HealthProbe(healthy=False, model=None, latency_s=None, error="down")


def _deps(**overrides) -> RunnerDependencies:
    base = dict(
        reload_position=AsyncMock(return_value=None),
        dismiss_popups=AsyncMock(return_value="POPUPS|none"),
        capture_state=AsyncMock(return_value=dict(CANONICAL)),
        make_agent=lambda spec: _FinishingAgent(),
        probe_health=AsyncMock(return_value=_healthy_probe()),
    )
    base.update(overrides)
    return RunnerDependencies(**base)


def _runner(store: BenchmarkStore, deps: RunnerDependencies, *, expected_state=None, player_id: int = 0):
    return BenchmarkRunner(
        store=store,
        dependencies=deps,
        expected_state=expected_state if expected_state is not None else dict(CANONICAL),
        player_id=player_id,
    )


def _attempt_payload(run_dir, index: int, ordinal: int = 1) -> dict:
    path = run_dir / "attempts" / f"trial-{index:03d}-attempt-{ordinal:03d}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Step 1 (brief): timeout health discriminator. Adapted to the actual, pinned
# `HealthProbe` fields (healthy/model/latency_s/error) from Task 4's
# `benchmark_backend.py` -- the brief's example used identity_ok/detail
# kwargs that don't exist on that frozen dataclass; see task-11-report.md.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthy_timeout_is_raw_trial_but_dead_endpoint_is_retry(tmp_path):
    class TimeoutAgent:
        async def run(self, gs, player_id, turn):
            raise EpisodeTimedOut("episode wall reached")

    health_results = iter(
        [
            HealthProbe(healthy=True, model="qwen3.6-27b", latency_s=0.2, error=None),
            HealthProbe(healthy=False, model=None, latency_s=10.0, error="timeout"),
        ]
    )

    async def probe_after_timeout():
        return next(health_results)

    canonical = {"turn": 157, "player_id": 0, "units": [], "cities": [], "tiles": []}
    store = BenchmarkStore.create(
        tmp_path / "run",
        {"session_fingerprint": "abc123", "schedule_fingerprint": "def456"},
    )
    deps = RunnerDependencies(
        reload_position=AsyncMock(return_value=None),
        dismiss_popups=AsyncMock(return_value="POPUPS|none"),
        capture_state=AsyncMock(return_value=canonical),
        make_agent=lambda _trial: TimeoutAgent(),
        probe_health=probe_after_timeout,
    )
    runner = BenchmarkRunner(store=store, dependencies=deps, expected_state=canonical, player_id=0)

    await runner.run_trial(_spec(1, "minimal"))
    await runner.run_trial(_spec(2, "standard"))

    assert runner.store.completed_indices() == {1}
    assert runner.store.attempt_count(2) == 1
    assert runner.store.trial(1)["terminal"] == "runaway_timeout"


# ---------------------------------------------------------------------------
# Strict serial execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_processes_trials_strictly_serially(tmp_path):
    order: list[str] = []

    class OrderedAgent:
        def __init__(self, label):
            self.label = label

        async def run(self, gs, player_id, turn):
            order.append(f"{self.label}-start")
            await asyncio.sleep(0)
            order.append(f"{self.label}-end")
            return EpisodeEvidence(EpisodeTerminal.FINISH_TRIAL, [], [], "", 0.1, 1, 1)

    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps(make_agent=lambda spec: OrderedAgent(spec.index))
    runner = _runner(store, deps)

    await runner.run([_spec(1, "minimal"), _spec(2, "standard")])

    assert order == ["1-start", "1-end", "2-start", "2-end"]
    assert runner.store.completed_indices() == {1, 2}


# ---------------------------------------------------------------------------
# reload -> popup -> checksum order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_trial_calls_reload_popup_checksum_in_order(tmp_path):
    calls: list[str] = []

    async def reload_position(position_id):
        calls.append("reload")

    async def dismiss_popups():
        calls.append("popups")
        return "POPUPS|none"

    async def capture_state():
        calls.append("capture")
        return dict(CANONICAL)

    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps(reload_position=reload_position, dismiss_popups=dismiss_popups, capture_state=capture_state)
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    # capture_state is called twice (checksum verification, then final-state
    # capture) -- only the first three calls establish the mandated order.
    assert calls[:3] == ["reload", "popups", "capture"]
    assert runner.store.completed_indices() == {1}


# ---------------------------------------------------------------------------
# Checksum mismatch aborts the session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checksum_mismatch_aborts_session_with_no_attempt_recorded(tmp_path):
    store = BenchmarkStore.create(tmp_path / "run", _lock())
    wrong_state = {**CANONICAL, "turn": 999}
    make_agent_calls: list[int] = []
    deps = _deps(
        capture_state=AsyncMock(return_value=wrong_state),
        make_agent=lambda spec: make_agent_calls.append(spec.index) or _FinishingAgent(),
    )
    runner = _runner(store, deps)

    with pytest.raises(SessionAborted, match="checksum"):
        await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 0
    # A checksum mismatch aborts before a fresh agent is ever constructed --
    # no episode, no model observation of any kind.
    assert make_agent_calls == []


# ---------------------------------------------------------------------------
# Three-attempt cap, including across process resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_trial_stops_session_after_three_attempts_across_resume(tmp_path):
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    for _ in range(3):
        store.record_attempt(1, {"failure_class": FailureClass.RELOAD_OR_RECONNECT.value})

    # Simulate a resumed process: a brand-new BenchmarkStore instance reopens
    # the same run directory and sees the three attempts purely from disk.
    resumed_store = BenchmarkStore.open(run_dir, _lock())
    reload_position = AsyncMock(return_value=None)
    deps = _deps(reload_position=reload_position)
    runner = _runner(resumed_store, deps)

    with pytest.raises(SessionAborted, match="attempt"):
        await runner.run_trial(_spec(1, "minimal"))

    reload_position.assert_not_awaited()
    assert resumed_store.completed_indices() == set()


# ---------------------------------------------------------------------------
# Zero-action / step-limit / malformed-output episodes are admitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_limit_zero_action_and_malformed_output_are_admitted_as_scoreable(tmp_path):
    class NullAgent:
        async def run(self, gs, player_id, turn):
            return EpisodeEvidence(
                terminal=EpisodeTerminal.STEP_LIMIT,
                steps=[],
                invalid_tool_calls=[
                    {"tool_name": "frobnicate", "arguments": "{bad json", "reason": "bad_arguments"}
                ],
                final_summary="",
                wall_clock_s=5.0,
                prompt_tokens=3,
                completion_tokens=0,
            )

    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps(make_agent=lambda spec: NullAgent())
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == {1}
    trial = runner.store.trial(1)
    assert trial["terminal"] == "step_limit"
    assert trial["steps"] == []
    assert trial["invalid_tool_calls"][0]["reason"] == "bad_arguments"
    assert runner.store.attempt_count(1) == 0


# ---------------------------------------------------------------------------
# Reload / popup / harness-crash failures before a complete episode are
# infrastructure attempts, never a scoreable trial.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_failure_is_an_infrastructure_attempt(tmp_path):
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    deps = _deps(reload_position=AsyncMock(side_effect=RuntimeError("reload boom")))
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 1
    assert _attempt_payload(run_dir, 1)["failure_class"] == FailureClass.RELOAD_OR_RECONNECT.value


@pytest.mark.asyncio
async def test_popup_failure_is_an_infrastructure_attempt(tmp_path):
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    deps = _deps(dismiss_popups=AsyncMock(side_effect=RuntimeError("popup boom")))
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 1
    assert _attempt_payload(run_dir, 1)["failure_class"] == FailureClass.POPUP_HYGIENE.value


@pytest.mark.asyncio
async def test_pre_episode_capture_state_failure_is_a_harness_crash_attempt(tmp_path):
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    deps = _deps(capture_state=AsyncMock(side_effect=BenchmarkStateError("ERR:PLAYER_NOT_FOUND")))
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 1
    assert _attempt_payload(run_dir, 1)["failure_class"] == FailureClass.HARNESS_CRASH.value


@pytest.mark.asyncio
async def test_mid_episode_capture_state_failure_is_a_harness_crash_attempt(tmp_path):
    """`BenchmarkStateError` raised from inside `agent.run()` (e.g. the
    agent's own per-step capture hook hitting a stale connection) is a
    harness failure per `SingleTurnAgent.run()`'s pinned exception contract
    -- never scored as a model outcome."""
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    deps = _deps(make_agent=lambda spec: _RaisingAgent(BenchmarkStateError("ERR:STALE_CONN")))
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 1
    assert _attempt_payload(run_dir, 1)["failure_class"] == FailureClass.HARNESS_CRASH.value


@pytest.mark.asyncio
async def test_final_state_capture_failure_after_episode_is_a_harness_crash_attempt(tmp_path):
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    capture_state = AsyncMock(side_effect=[dict(CANONICAL), RuntimeError("final capture boom")])
    deps = _deps(capture_state=capture_state)
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 1
    assert _attempt_payload(run_dir, 1)["failure_class"] == FailureClass.HARNESS_CRASH.value


# ---------------------------------------------------------------------------
# Immediate health classification of timeouts and transport failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_episode_timeout_with_healthy_canary_admits_runaway_timeout(tmp_path):
    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps(
        make_agent=lambda spec: _RaisingAgent(EpisodeTimedOut("wall reached")),
        probe_health=AsyncMock(return_value=_healthy_probe()),
    )
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == {1}
    assert runner.store.trial(1)["terminal"] == "runaway_timeout"
    assert runner.store.attempt_count(1) == 0


@pytest.mark.asyncio
async def test_episode_timeout_commits_the_partial_transcript_not_an_empty_one(tmp_path):
    # A timed-out SingleTurnAgent attaches whatever it accumulated before
    # the wall to EpisodeTimedOut.partial_evidence. The runner must commit
    # that partial transcript for the runaway_timeout trial rather than
    # empty steps/invalid_tool_calls/tokens -- a scored final_state with
    # zero step evidence would misreport the episode.
    partial = EpisodeEvidence(
        terminal=EpisodeTerminal.STEP_LIMIT,
        steps=[
            {"idx": 0, "tool_name": "get_units", "tool_result_full": "UNITS"},
        ],
        invalid_tool_calls=[
            {"tool_name": "bogus_tool", "arguments": "{}", "reason": "unknown_tool"},
        ],
        final_summary="",
        wall_clock_s=0.3,
        prompt_tokens=7,
        completion_tokens=3,
    )
    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps(
        make_agent=lambda spec: _RaisingAgent(
            EpisodeTimedOut("wall reached", partial_evidence=partial)
        ),
        probe_health=AsyncMock(return_value=_healthy_probe()),
    )
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == {1}
    trial = runner.store.trial(1)
    assert trial["terminal"] == "runaway_timeout"
    assert trial["steps"] == partial.steps
    assert trial["invalid_tool_calls"] == partial.invalid_tool_calls
    assert trial["prompt_tokens"] == 7
    assert trial["completion_tokens"] == 3
    assert trial["wall_clock_s"] == 0.3


@pytest.mark.asyncio
async def test_episode_timeout_with_unhealthy_canary_is_an_infrastructure_attempt(tmp_path):
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    deps = _deps(
        make_agent=lambda spec: _RaisingAgent(EpisodeTimedOut("wall reached")),
        probe_health=AsyncMock(return_value=_unhealthy_probe()),
    )
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 1
    assert _attempt_payload(run_dir, 1)["failure_class"] == FailureClass.EPISODE_TIMEOUT_UNHEALTHY.value


@pytest.mark.asyncio
async def test_request_timeout_exception_with_healthy_canary_admits_runaway_timeout(tmp_path):
    req = httpx.Request("POST", "http://example.invalid/v1/chat/completions")
    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps(
        make_agent=lambda spec: _RaisingAgent(openai.APITimeoutError(request=req)),
        probe_health=AsyncMock(return_value=_healthy_probe()),
    )
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == {1}
    assert runner.store.trial(1)["terminal"] == "runaway_timeout"


@pytest.mark.asyncio
async def test_transport_failure_with_unhealthy_canary_is_an_infrastructure_attempt(tmp_path):
    req = httpx.Request("POST", "http://example.invalid/v1/chat/completions")
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    deps = _deps(
        make_agent=lambda spec: _RaisingAgent(openai.APIConnectionError(request=req)),
        probe_health=AsyncMock(return_value=_unhealthy_probe()),
    )
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 1
    assert _attempt_payload(run_dir, 1)["failure_class"] == FailureClass.TRANSPORT_FAILURE_UNHEALTHY.value


@pytest.mark.asyncio
async def test_transport_failure_with_healthy_canary_stops_session_not_scoreable(tmp_path):
    req = httpx.Request("POST", "http://example.invalid/v1/chat/completions")
    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps(
        make_agent=lambda spec: _RaisingAgent(openai.APIConnectionError(request=req)),
        probe_health=AsyncMock(return_value=_healthy_probe()),
    )
    runner = _runner(store, deps)

    with pytest.raises(SessionAborted) as exc_info:
        await runner.run_trial(_spec(1, "minimal"))
    assert exc_info.value.code == "healthy_transport_exception_not_scoreable"

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 0


# ---------------------------------------------------------------------------
# Unknown exceptions stop the session; never auto-retried
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_exception_stops_session_without_retry(tmp_path):
    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps(make_agent=lambda spec: _RaisingAgent(ValueError("totally unexpected")))
    runner = _runner(store, deps)

    with pytest.raises(SessionAborted) as exc_info:
        await runner.run_trial(_spec(1, "minimal"))
    assert exc_info.value.code == "unknown_failure"

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 0


# ---------------------------------------------------------------------------
# Resume skips committed trials; scorer absence never replays one
# ---------------------------------------------------------------------------


def _committed_trial_payload(index: int) -> dict:
    return {
        "index": index,
        "pair_id": "pair-001",
        "position_id": "builder-cal-v1",
        "model": "qwen3.6-27b",
        "arm_id": "minimal",
        "seed": 101,
        "attempt_count": 1,
        "terminal": "finish_trial",
        "steps": [],
        "invalid_tool_calls": [],
        "final_summary": "",
        "initial_state": dict(CANONICAL),
        "final_state": dict(CANONICAL),
        "wall_clock_s": 1.0,
        "prompt_tokens": 1,
        "completion_tokens": 1,
    }


@pytest.mark.asyncio
async def test_run_skips_already_committed_trials_on_resume(tmp_path):
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    store.commit_trial(1, _committed_trial_payload(1))

    reopened = BenchmarkStore.open(run_dir, _lock())
    make_agent_calls: list[int] = []
    deps = _deps(make_agent=lambda spec: make_agent_calls.append(spec.index) or _FinishingAgent())
    runner = _runner(reopened, deps)

    await runner.run([_spec(1, "minimal"), _spec(2, "standard")])

    assert make_agent_calls == [2]
    assert runner.store.completed_indices() == {1, 2}


@pytest.mark.asyncio
async def test_scorer_absence_never_replays_a_committed_trial(tmp_path):
    """No report.json/report.md/scorer artifacts exist anywhere in the run
    directory -- committed-trial resume-skip must not depend on their
    presence."""
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    store.commit_trial(1, _committed_trial_payload(1))
    assert not (run_dir / "report.json").exists()
    assert not (run_dir / "report.md").exists()

    make_agent_calls: list[int] = []
    deps = _deps(make_agent=lambda spec: make_agent_calls.append(spec.index) or _FinishingAgent())
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert make_agent_calls == []
    assert runner.store.completed_indices() == {1}


# ---------------------------------------------------------------------------
# CLI fail-closed guard: no admission gates are wired into `main`/`_run_async`
# yet, so a counted-looking session must be refused outright unless the
# operator deliberately opts into a marked, non-counted smoke run.
# ---------------------------------------------------------------------------


def _write_fixture_suite_and_position(tmp_path) -> "object":
    from pathlib import Path

    benchmarks_dir = tmp_path / "benchmarks"
    suites_dir = benchmarks_dir / "suites"
    positions_dir = benchmarks_dir / "positions"
    suites_dir.mkdir(parents=True)
    positions_dir.mkdir(parents=True)

    (positions_dir / "builder-cal-v1.yaml").write_text(
        """
position_id: builder-cal-v1
version: 1
archive: positions/builder-cal-v1.Civ6Save
archive_sha256: "abc123"
game_save_name: builder-cal-v1
player_id: 0
expected_state:
  turn: 42
expected_state_sha256: "def456"
relevant_tiles:
  - [9, 24]
objectives:
  - id: obj1
    description: improve the farm tile
rubric:
  - task_id: r1
    levels:
      - score: 0
        predicate:
          kind: always
split: calibration
""",
        encoding="utf-8",
    )

    suite_path = suites_dir / "builder-cal.yaml"
    suite_path.write_text(
        """
suite_id: builder-cal-v1
driver: single_turn
positions:
  - builder-cal-v1
models:
  - qwen3.6-27b
arms:
  - arm_id: minimal
    tools: minimal
    options: {}
seeds: [101]
order: abba
sampling:
  temperature: 0.2
  top_p: 0.95
  seed: null
  max_tokens: 6144
max_steps: 15
result_char_cap: 6000
audit_indices: []
""",
        encoding="utf-8",
    )
    return Path(suite_path)


def test_main_without_ungated_smoke_flag_refuses_before_touching_the_live_game(
    tmp_path, monkeypatch, capsys
):
    def _boom_create(*_args, **_kwargs):
        raise AssertionError("BenchmarkStore.create must not be called without --ungated-smoke")

    class _BoomConnection:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("GameConnection must not be constructed without --ungated-smoke")

    monkeypatch.setattr(benchmark_runner.BenchmarkStore, "create", staticmethod(_boom_create))
    monkeypatch.setattr(benchmark_runner, "GameConnection", _BoomConnection)

    # Deliberately points at a suite file that doesn't even exist: the guard
    # must fire before the suite/position manifests are ever loaded.
    exit_code = benchmark_runner.main(
        ["--suite", str(tmp_path / "missing-suite.yaml"), "--run-id", "smoke-run"]
    )

    assert exit_code == 1
    err = capsys.readouterr().err.lower()
    assert "admission gate" in err
    assert "ungated-smoke" in err


@pytest.mark.asyncio
async def test_ungated_smoke_flag_proceeds_and_stamps_the_session_lock(tmp_path, monkeypatch):
    suite_path = _write_fixture_suite_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    captured_locks: list[dict] = []
    real_create = benchmark_runner.BenchmarkStore.create.__func__

    def _capturing_create(cls, run_dir_arg, lock):
        captured_locks.append(dict(lock))
        return real_create(cls, run_dir_arg, lock)

    monkeypatch.setattr(benchmark_runner.BenchmarkStore, "create", classmethod(_capturing_create))

    class _FakeConnection:
        async def connect(self):
            return None

    monkeypatch.setattr(benchmark_runner, "GameConnection", _FakeConnection)

    class _FakeRunner:
        def __init__(self, **_kwargs):
            pass

        async def run(self, schedule):
            return None

    monkeypatch.setattr(benchmark_runner, "BenchmarkRunner", _FakeRunner)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--suite", str(suite_path),
            "--run-id", "smoke-run",
            "--run-dir", str(run_dir),
            "--gateway-url", "http://example.invalid/v1",
            "--ungated-smoke",
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 0
    assert len(captured_locks) == 1
    assert captured_locks[0]["ungated_smoke"] is True


@pytest.mark.asyncio
async def test_ungated_smoke_run_dir_is_report_ready(tmp_path, monkeypatch):
    """Integration (finding 1): `civ-arena-benchmark --ungated-smoke`
    followed by `civ-arena-benchmark-report <run_dir>` must not fail on a
    missing scorer_fingerprint. Runs the smoke CLI path end to end (with
    faked game/backend deps, one committed trial) and then calls
    `build_report` on the resulting run dir directly."""
    suite_path = _write_fixture_suite_and_position(tmp_path)
    run_dir_base = tmp_path / "runs"

    class _FakeConnection:
        async def connect(self):
            return None

    monkeypatch.setattr(benchmark_runner, "GameConnection", _FakeConnection)

    class _FinishingAgentForSmoke:
        async def run(self, gs, player_id, turn):
            return EpisodeEvidence(
                terminal=EpisodeTerminal.FINISH_TRIAL,
                steps=[],
                invalid_tool_calls=[],
                final_summary="done",
                wall_clock_s=0.1,
                prompt_tokens=1,
                completion_tokens=1,
            )

    def _fake_build_live_dependencies(**_kwargs) -> RunnerDependencies:
        return RunnerDependencies(
            reload_position=AsyncMock(return_value=None),
            dismiss_popups=AsyncMock(return_value="POPUPS|none"),
            # Matches the fixture position's expected_state ({"turn": 42})
            # so the pre-episode canonical checksum passes.
            capture_state=AsyncMock(return_value={"turn": 42}),
            make_agent=lambda spec: _FinishingAgentForSmoke(),
            probe_health=AsyncMock(
                return_value=HealthProbe(healthy=True, model="qwen3.6-27b", latency_s=0.1, error=None)
            ),
        )

    monkeypatch.setattr(benchmark_runner, "_build_live_dependencies", _fake_build_live_dependencies)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--suite", str(suite_path),
            "--run-id", "smoke-run",
            "--run-dir", str(run_dir_base),
            "--gateway-url", "http://example.invalid/v1",
            "--ungated-smoke",
        ]
    )

    exit_code = await benchmark_runner._run_async(args)
    assert exit_code == 0

    run_dir = run_dir_base / "smoke-run"
    report = build_report(run_dir)
    assert report["session"]["ungated_smoke"] is True
    assert report["completeness"]["by_position"]["builder-cal-v1"]["committed"] == 1


def _position_manifest(**overrides) -> PositionManifest:
    fields = dict(
        position_id="builder-cal-v1",
        version=1,
        archive="positions/builder-cal-v1.Civ6Save",
        archive_sha256="abc123",
        game_save_name="builder-cal-v1",
        player_id=0,
        expected_state={"turn": 42},
        expected_state_sha256="def456",
        relevant_tiles=((9, 24),),
        objectives=(),
        rubric=(),
        split="calibration",
    )
    fields.update(overrides)
    return PositionManifest(**fields)


def _suite_manifest(arms, **overrides) -> SuiteManifest:
    fields = dict(
        suite_id="builder-cal-v1",
        driver="single_turn",
        positions=("builder-cal-v1",),
        models=("qwen3.6-27b",),
        arms=arms,
        seeds=(101,),
        order="abba",
        sampling=SamplingConfig(temperature=0.2, top_p=0.95, seed=None, max_tokens=6144),
        max_steps=15,
        result_char_cap=6000,
        audit_indices=(),
    )
    fields.update(overrides)
    return SuiteManifest(**fields)


def test_make_agent_fails_closed_on_nonempty_arm_options():
    # TreatmentArm.options is validated by compile_schedule (a tools override
    # must expose finish_trial and never end_turn), but this scaffold's
    # make_agent only ever reads arm.tools -- it silently drops everything
    # else in arm.options. A declared treatment (e.g. a tools override or any
    # other option) must never silently run as the bare tier: fail closed
    # instead.
    position = _position_manifest()
    arm = TreatmentArm("standard", "standard", {"tools": ["get_units", "finish_trial"]})
    suite = _suite_manifest((arm,))
    deps = benchmark_runner._build_live_dependencies(
        connection=None,
        position=position,
        suite=suite,
        gateway_url="http://example.invalid/v1",
        api_key="x",
    )
    spec = TrialSpec(index=1, pair_id="p", position_id="builder-cal-v1", model="qwen3.6-27b", arm_id="standard", seed=101)

    with pytest.raises(ValueError, match="arm options"):
        deps.make_agent(spec)


def test_make_agent_allows_empty_arm_options():
    position = _position_manifest()
    arm = TreatmentArm("standard", "standard", {})
    suite = _suite_manifest((arm,))
    deps = benchmark_runner._build_live_dependencies(
        connection=None,
        position=position,
        suite=suite,
        gateway_url="http://example.invalid/v1",
        api_key="x",
    )
    spec = TrialSpec(index=1, pair_id="p", position_id="builder-cal-v1", model="qwen3.6-27b", arm_id="standard", seed=101)

    agent = deps.make_agent(spec)
    assert agent is not None
