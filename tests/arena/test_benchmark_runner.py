"""Tests for the strictly-serial controlled-position benchmark runner.

Most tests here use plain async fakes / `AsyncMock` -- no live game, no
network. `RunnerDependencies` is the seam: most production wiring (in
`benchmark_runner._build_live_dependencies` / `main`) is not exercised via a
live game or network, but `_build_live_dependencies.make_agent`'s arm-options
fail-closed check (below) is a pure function of manifest data and is tested
directly.
"""
from __future__ import annotations

import argparse
import asyncio
import json

import httpx
import openai
import pytest
import yaml
from unittest.mock import AsyncMock

import civ_mcp.arena.benchmark_runner as benchmark_runner
import civ_mcp.arena.endpoint_registry as endpoint_registry_module
from civ_mcp._vendor.brothereye_registry import Endpoint, Registry
from civ_mcp.arena.backends import SamplingConfig
from civ_mcp.arena.benchmark_agent import BENCHMARK_SYSTEM, EpisodeEvidence, EpisodeTerminal, EpisodeTimedOut
import civ_mcp.arena.benchmark_backend as benchmark_backend_module
from civ_mcp.arena.benchmark_backend import HealthProbe
from civ_mcp.arena.benchmark_gates import GateFailure
from civ_mcp.arena.benchmark_live_evidence import GpuProcess
import civ_mcp.arena.benchmark_live_evidence as benchmark_live_evidence_module
from civ_mcp.arena.benchmark_manifest import PositionManifest, SuiteManifest, TreatmentArm
from civ_mcp.arena.benchmark_report import build_report
from civ_mcp.arena.benchmark_store import trial_filename
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
    # G3: RunnerDependencies.reload_position's contract is Awaitable[bool]
    # (verified) -- the default fake reports a verified reload so existing
    # tests exercise the same "verified reload, mismatch aborts" behavior
    # as before this contract change.
    base = dict(
        reload_position=AsyncMock(return_value=True),
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
        reload_position=AsyncMock(return_value=True),
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
        return True

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


def _journal_events(run_dir, event: str) -> list[dict]:
    lines = (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    return [r for r in records if r["event"] == event]


@pytest.mark.asyncio
async def test_checksum_mismatch_journal_includes_a_field_level_diff(tmp_path):
    """F12 repro: the checksum-mismatch journal entry previously carried
    only two opaque hashes (expected_digest/observed_digest) and never
    called diff_state -- useless for actually seeing what differed."""
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    wrong_state = {**CANONICAL, "turn": 999}
    deps = _deps(capture_state=AsyncMock(return_value=wrong_state))
    runner = _runner(store, deps)

    with pytest.raises(SessionAborted):
        await runner.run_trial(_spec(1, "minimal"))

    events = _journal_events(run_dir, "checksum_mismatch")
    assert len(events) == 1
    # G9: append_event's details kwarg lands flat -- no more
    # record["details"]["details"] double-nesting.
    diff = events[0]["details"]["diff"]
    assert diff == {"turn": [157, 999]}


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
    reload_position = AsyncMock(return_value=True)
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


# ---------------------------------------------------------------------------
# G2 -- _reload_result_is_success must recognize every real Tier-0/1/2
# success shape, not just the original "Loaded "/"world ready" strings.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        # Tier-0/1 frontend-Lua engaged path.
        "Loaded builder-cal-v1: world ready, FireTuner port is open. "
        "Reconnect and verify with get_game_overview.",
        # F16(b) stable-open-port fallback (G3): still success-shaped, just
        # carrying the UNVERIFIED marker.
        "Loaded builder-cal-v1 (UNVERIFIED: port drop not observed -- "
        "likely faster than the poll interval): world ready, FireTuner "
        "port is open. Reconnect and verify with get_game_overview.",
        # Tier-2 _navigate_to_save_sync.
        "Save loading (12s). Steps: click Continue, wait. Wait ~10s then "
        "use get_game_overview to verify.",
        # restart_and_load's compound Kill/Launch/Load string.
        "Kill: ok | Launch: ok | Load: Save loading (10s). Steps: click. "
        "Wait ~10s then use get_game_overview to verify.",
    ],
)
def test_reload_result_is_success_recognizes_every_real_success_shape(result):
    assert benchmark_runner._reload_result_is_success(result) is True


@pytest.mark.parametrize(
    "result",
    [
        "Error: Save 'foo' not found in Lua query or on filesystem. Check the name.",
        "WARNING: FireTuner port never dropped after the Lua load of 'foo' -- "
        "the load may not have engaged; no Escape was sent.",
        "WARNING: FireTuner port did not reopen within the wait window for 'foo'.",
        # A "Save loading (" prefix must not override an explicit failure
        # marker later in the same compound string.
        "Kill: ok | Launch: FAILED | Load: Save loading (10s).",
        "Kill: ok | Launch: ok | Load: ABORTED before Save loading (10s).",
    ],
)
def test_reload_result_is_success_stays_fail_closed_on_failure_strings(result):
    assert benchmark_runner._reload_result_is_success(result) is False


# ---------------------------------------------------------------------------
# G3 -- reload_position's contract is Awaitable[bool] (verified). A
# checksum mismatch immediately after an unverified reload is a retryable
# infrastructure attempt (the launcher structurally could not tell an inert
# Network.LoadGame apart from a genuine stable-open-port success); a
# checksum mismatch after a verified reload still aborts the session.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checksum_mismatch_after_unverified_reload_is_infra_attempt_not_abort(tmp_path):
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    wrong_state = {**CANONICAL, "turn": 999}
    deps = _deps(
        reload_position=AsyncMock(return_value=False),
        capture_state=AsyncMock(return_value=wrong_state),
    )
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))  # must NOT raise SessionAborted

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 1
    attempt = _attempt_payload(run_dir, 1)
    assert attempt["failure_class"] == FailureClass.RELOAD_OR_RECONNECT.value
    assert "unverified reload" in attempt["error"]


@pytest.mark.asyncio
async def test_checksum_mismatch_after_verified_reload_still_aborts(tmp_path):
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    wrong_state = {**CANONICAL, "turn": 999}
    deps = _deps(
        reload_position=AsyncMock(return_value=True),
        capture_state=AsyncMock(return_value=wrong_state),
    )
    runner = _runner(store, deps)

    with pytest.raises(SessionAborted, match="checksum"):
        await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 0


@pytest.mark.asyncio
async def test_unverified_reload_with_matching_checksum_proceeds_normally(tmp_path):
    """An unverified reload is only a problem paired with a checksum
    mismatch -- if the observed state matches, the trial runs normally."""
    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps(reload_position=AsyncMock(return_value=False))
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == {1}


@pytest.mark.asyncio
async def test_reload_position_non_bool_return_fails_closed_as_infra_attempt(tmp_path):
    """A legacy `None` return (or anything else non-bool) must never be
    silently treated as verified=True -- fail closed as a retryable infra
    attempt instead."""
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    deps = _deps(reload_position=AsyncMock(return_value=None))
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
@pytest.mark.parametrize("status", ["err", "?", "SOMETHING_UNRECOGNIZED"])
async def test_popup_failure_status_string_is_an_infrastructure_attempt(tmp_path, status):
    """F5 repro: dismiss_blocking_popups never raises -- failures come back
    as "err"/"?" strings (see civ_mcp.arena.popups.dismiss_blocking_popups).
    The runner previously ignored the return value entirely, so a failed
    popup-hygiene status proceeded straight into the trial instead of an
    infra attempt."""
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    deps = _deps(dismiss_popups=AsyncMock(return_value=status))
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == set()
    assert runner.store.attempt_count(1) == 1
    assert _attempt_payload(run_dir, 1)["failure_class"] == FailureClass.POPUP_HYGIENE.value


@pytest.mark.asyncio
async def test_popup_success_status_proceeds_into_the_trial(tmp_path):
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    deps = _deps(dismiss_popups=AsyncMock(return_value="POPUPS|none"))
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == {1}


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
# G4 -- agent/GameState construction must be classified like any other
# harness step, never escape run_trial as a raw, unjournalled traceback.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_make_agent_construction_failure_is_a_harness_crash_attempt(tmp_path):
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())

    def boom(spec):
        raise RuntimeError("backend construction boom")

    deps = _deps(make_agent=boom)
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))  # must NOT raise

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
async def test_request_timeout_commits_partial_progress_via_agent_accessor(tmp_path):
    """F7 repro: only EpisodeTimedOut carries partial_evidence as an
    exception attribute. A mid-episode openai.APITimeoutError (a plain
    request timeout, not the episode-wall EpisodeTimedOut) has no such
    attribute, so the runner previously committed steps=[] even though
    earlier steps in the same episode executed real mutations. The runner
    must fall back to the live agent's own partial_evidence() accessor
    (the same instance-level progress state EpisodeTimedOut's own
    partial_evidence is built from) for this exception path too."""

    class _AgentWithPartialProgress:
        """Mimics SingleTurnAgent's progress-tracking contract: run() raises
        a bare request-timeout exception (no partial_evidence attribute),
        but partial_evidence() exposes whatever was recorded before that."""

        def __init__(self, exc: Exception, partial: EpisodeEvidence):
            self._exc = exc
            self._partial = partial

        async def run(self, gs, player_id, turn):
            raise self._exc

        def partial_evidence(self) -> EpisodeEvidence:
            return self._partial

    req = httpx.Request("POST", "http://example.invalid/v1/chat/completions")
    partial = EpisodeEvidence(
        terminal=EpisodeTerminal.STEP_LIMIT,
        steps=[{"idx": 0, "tool_name": "get_units", "tool_result_full": "UNITS"}],
        invalid_tool_calls=[],
        final_summary="",
        wall_clock_s=0.4,
        prompt_tokens=9,
        completion_tokens=4,
    )
    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps(
        make_agent=lambda spec: _AgentWithPartialProgress(
            openai.APITimeoutError(request=req), partial
        ),
        probe_health=AsyncMock(return_value=_healthy_probe()),
    )
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert runner.store.completed_indices() == {1}
    trial = runner.store.trial(1)
    assert trial["terminal"] == "runaway_timeout"
    assert trial["steps"] == partial.steps
    assert trial["prompt_tokens"] == 9
    assert trial["completion_tokens"] == 4


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


def _committed_trial_payload(index: int, *, session_fingerprint: str = "abc123") -> dict:
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
        # Matches _lock()'s session_fingerprint ("abc123") by default -- a
        # committed trial's own provenance stamp (see F8).
        "session_fingerprint": session_fingerprint,
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


@pytest.mark.asyncio
async def test_finalize_trial_stamps_the_store_session_fingerprint(tmp_path):
    """F8 repro: committed trial payloads omitted session_fingerprint, so
    resume identified completion by filename only -- a stale/copied
    trial-NNN.json is indistinguishable from current-lock evidence."""
    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps()
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    assert store.fingerprint == "abc123"
    assert runner.store.trial(1)["session_fingerprint"] == "abc123"


@pytest.mark.asyncio
async def test_resume_fails_closed_on_a_session_fingerprint_mismatch(tmp_path):
    """F8 repro: a committed trial stamped with a DIFFERENT
    session_fingerprint than the current store's (e.g. a stale/copied
    trial-NNN.json left over from an unrelated session directory) must
    never be silently treated as current-lock completion just because the
    filename matches."""
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    store.commit_trial(1, _committed_trial_payload(1, session_fingerprint="STALE_FINGERPRINT"))

    deps = _deps(make_agent=lambda spec: _FinishingAgent())
    runner = _runner(store, deps)

    with pytest.raises(Exception):
        await runner.run_trial(_spec(1, "minimal"))


@pytest.mark.asyncio
async def test_run_fails_closed_on_a_session_fingerprint_mismatch_during_resume_skip(tmp_path):
    """Same repro as above, but through the outer run() skip-loop (the
    resume path that never calls run_trial() at all for an already-
    completed index)."""
    run_dir = tmp_path / "run"
    store = BenchmarkStore.create(run_dir, _lock())
    store.commit_trial(1, _committed_trial_payload(1, session_fingerprint="STALE_FINGERPRINT"))

    deps = _deps(make_agent=lambda spec: _FinishingAgent())
    runner = _runner(store, deps)

    with pytest.raises(Exception):
        await runner.run([_spec(1, "minimal"), _spec(2, "standard")])


# ---------------------------------------------------------------------------
# CLI fail-closed guard: no admission gates are wired into `main`/`_run_async`
# yet, so a counted-looking session must be refused outright unless the
# operator deliberately opts into a marked, non-counted smoke run.
# ---------------------------------------------------------------------------


def _write_fixture_suite_and_position(tmp_path, *, expected_state_sha256: str | None = None) -> "object":
    from pathlib import Path

    from civ_mcp.arena.benchmark_state import state_digest

    benchmarks_dir = tmp_path / "benchmarks"
    suites_dir = benchmarks_dir / "suites"
    positions_dir = benchmarks_dir / "positions"
    suites_dir.mkdir(parents=True)
    positions_dir.mkdir(parents=True)

    # G10: expected_state_sha256 must actually match
    # state_digest({"turn": 42}) now that _run_async verifies it at
    # startup -- a test wanting a tampered/stale digest passes one
    # explicitly via the `expected_state_sha256` override.
    digest = expected_state_sha256 if expected_state_sha256 is not None else state_digest({"turn": 42})

    (positions_dir / "builder-cal-v1.yaml").write_text(
        f"""
position_id: builder-cal-v1
version: 1
archive: positions/builder-cal-v1.Civ6Save
archive_sha256: "abc123"
game_save_name: builder-cal-v1
player_id: 0
expected_state:
  turn: 42
expected_state_sha256: "{digest}"
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
persistent_unit_ids: []
consumable_unit_ids: []
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


def test_suite_without_ungated_smoke_flag_refuses_before_touching_the_live_game(
    tmp_path, monkeypatch, capsys
):
    """--suite with neither --campaign nor --ungated-smoke has no admission
    gate pipeline wired at all -- it must refuse outright, before ever
    touching the suite/position manifests or the live game. This is
    distinct from a --campaign run refusing on a specific failed live gate
    (see test_campaign_run_refuses_on_first_failed_live_gate below) -- that
    scenario replaces what used to be this same test's job before the
    admission gates were wired for --campaign."""

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


def test_campaign_and_suite_are_mutually_exclusive(tmp_path, capsys):
    exit_code = benchmark_runner.main(
        [
            "--campaign", str(tmp_path / "campaign.yaml"),
            "--suite", str(tmp_path / "suite.yaml"),
            "--run-id", "run1",
            "--ungated-smoke",
        ]
    )

    assert exit_code == 1
    err = capsys.readouterr().err.lower()
    assert "mutually exclusive" in err


def test_neither_campaign_nor_suite_refuses(tmp_path, capsys):
    exit_code = benchmark_runner.main(["--run-id", "run1"])

    assert exit_code == 1
    err = capsys.readouterr().err.lower()
    assert "--campaign" in err and "--suite" in err


def _write_fixture_campaign_and_position(tmp_path) -> "Path":
    from pathlib import Path

    from civ_mcp.arena.benchmark_state import state_digest

    benchmarks_dir = tmp_path / "benchmarks"
    campaigns_dir = benchmarks_dir / "campaigns"
    positions_dir = benchmarks_dir / "positions"
    campaigns_dir.mkdir(parents=True)
    positions_dir.mkdir(parents=True)

    provenance = {"base_save": "organic-base", "archive_sha256": "deadbeef" * 8}
    (campaigns_dir / "provenance.json").write_text(json.dumps(provenance))
    contract = {
        "evidence_schema_version": "1.0.0",
        "predicate_schema_version": "1.0.0",
        "report_schema_version": "1.0.0",
        "scorer_fingerprint": "scorerfp",
    }
    (campaigns_dir / "contract.yaml").write_text(yaml.safe_dump(contract))

    digest = state_digest({"turn": 157})
    (positions_dir / "builder-economy-cal-v1.yaml").write_text(
        f"""
position_id: builder-economy-cal-v1
version: 1
archive: benchmarks/saves/BUILDER_ECONOMY_CAL_V1.Civ6Save
archive_sha256: "{provenance['archive_sha256']}"
game_save_name: BUILDER_ECONOMY_CAL_V1
player_id: 0
expected_state:
  turn: 157
expected_state_sha256: "{digest}"
relevant_tiles:
  - [9, 8]
objectives:
  - task_id: repair
    unit_index: 4
    target: [9, 8]
    requires: [repair_improvement]
rubric:
  - task_id: repair
    levels:
      - score: 0
        predicate:
          kind: always
      - score: 1
        predicate:
          kind: always
split: calibration
persistent_unit_ids: []
consumable_unit_ids: []
""",
        encoding="utf-8",
    )

    campaign_data = {
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
    campaign_path = campaigns_dir / "campaign.yaml"
    campaign_path.write_text(yaml.safe_dump(campaign_data))
    return campaign_path


async def _campaign_store_fingerprint(campaign_path: Path, run_dir: Path, run_id: str = "campaign-run") -> str:
    """Build the SAME campaign lock `_load_campaign_context` would (deterministic
    for fixed inputs -- including the live `git rev-parse HEAD` clean-checkout
    commit it embeds) and return its `campaign_fingerprint`, so a test can
    pre-seed a block's trial fixtures with the fingerprint a real admission run
    against this exact campaign/run-dir would actually stamp them with (finding
    5, final review: block_is_complete now requires that stamp to match, not
    just filename presence). Safe to call before the test's own `_run_async`
    invocation re-derives the identical lock: `CampaignStore.create` reattaches
    to an existing campaign.json/schedule.json that matches byte-for-byte."""
    args = benchmark_runner._build_arg_parser().parse_args(
        ["--campaign", str(campaign_path), "--run-id", run_id, "--run-dir", str(run_dir)]
    )
    context = await benchmark_runner._load_campaign_context(args)
    assert context is not None, "fixture campaign/position failed to load while deriving its fingerprint"
    return context.store.fingerprint


def _preseed_gemma_block_complete(
    run_dir: Path, gemma_block_id: str, schedule: dict, campaign_fingerprint: str
) -> None:
    """Pre-seed gemma's block as already fully committed. A3 (external
    review): `block_is_complete` now also requires each committed trial's
    `session_fingerprint` to match a recorded `blocks/<block_id>/
    session.json` -- filename presence plus a matching campaign_fingerprint
    alone (the old fixture shape) is no longer enough, so this writes both
    a real session.json and dual-stamped trial files."""
    gemma_block_dir = run_dir / "campaign-run" / "blocks" / gemma_block_id
    gemma_trials_dir = gemma_block_dir / "trials"
    gemma_trials_dir.mkdir(parents=True)
    # H1(b) (external review wave H): block_is_complete now also requires
    # blocks/<id>/schedule.json to exist and equal the campaign schedule's
    # declared entry for the block (open_block's write-time invariant).
    (gemma_block_dir / "schedule.json").write_text(
        json.dumps(schedule["blocks"][gemma_block_id], sort_keys=True)
    )
    # D4 (external review wave D): block_is_complete now also requires the
    # session to declare its own block identity -- block_id plus the
    # campaign lock's ModelBlockConfig for that block. The campaign lock
    # was already written to disk by _campaign_store_fingerprint's
    # CampaignStore.create call, so read the declared config from there.
    campaign_lock = json.loads((run_dir / "campaign-run" / "campaign.json").read_text())
    declared_model_config = next(m for m in campaign_lock["models"] if m["block_id"] == gemma_block_id)
    gemma_block_dir_session = {
        "campaign_fingerprint": campaign_fingerprint,
        "block_id": gemma_block_id,
        "model_config": declared_model_config,
    }
    # G1 (external review wave G): block_is_complete re-derives the
    # session_fingerprint from the session document itself, so the fixture
    # must carry a genuinely self-consistent one, not a placeholder label.
    from civ_mcp.arena.benchmark_store import compute_session_fingerprint

    gemma_block_dir_session["session_fingerprint"] = compute_session_fingerprint(
        gemma_block_dir_session
    )
    (gemma_block_dir / "session.json").write_text(json.dumps(gemma_block_dir_session))
    # I1 (external review wave I): block_is_complete now also requires the
    # counted admission SUCCESS record the real admit() writes right after
    # minting the session and before any trial runs -- ok=true,
    # mode="counted", stamped with the campaign fingerprint and the minted
    # session's own fingerprint (CampaignStore.record_admission's shape).
    admissions_dir = run_dir / "campaign-run" / "admissions"
    admissions_dir.mkdir(parents=True, exist_ok=True)
    (admissions_dir / f"{gemma_block_id}-attempt-001.json").write_text(
        json.dumps(
            {
                "block_id": gemma_block_id,
                "mode": "counted",
                "gates": {},
                "ok": True,
                "session_fingerprint": gemma_block_dir_session["session_fingerprint"],
                "campaign_fingerprint": campaign_fingerprint,
            }
        )
    )
    for trial in schedule["blocks"][gemma_block_id]["trials"]:
        (gemma_trials_dir / trial_filename(trial["index"])).write_text(
            json.dumps(
                {
                    "campaign_fingerprint": campaign_fingerprint,
                    "session_fingerprint": gemma_block_dir_session["session_fingerprint"],
                }
            )
        )


@pytest.mark.asyncio
async def test_campaign_run_refuses_on_first_failed_live_gate(tmp_path, monkeypatch, capsys):
    """Replaces the old blanket "gates not wired" refusal: with --campaign,
    the admission gates ARE wired, so a real (fake, injected) failure on
    the very first live gate must refuse the run -- and must never create
    a block session or run a trial."""
    from civ_mcp.arena.benchmark_admission import AdmissionError

    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    monkeypatch.setenv("LITELLM_OPENAI_API_KEY", "test-key")

    class _FailFirstGatePipeline:
        def __init__(self, _deps):
            pass

        async def admit(self, *_args, **_kwargs):
            raise AdmissionError("dirty_checkout", {"message": "WSL checkout is dirty"})

    def _boom_run_resolved_block(*_args, **_kwargs):
        raise AssertionError("run_resolved_block must never be called on an admission failure")

    monkeypatch.setattr(benchmark_runner, "_build_admission_pipeline", lambda args, api_key, **_kwargs: _FailFirstGatePipeline(None))
    monkeypatch.setattr(benchmark_runner, "run_resolved_block", _boom_run_resolved_block)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--campaign", str(campaign_path),
            "--run-id", "campaign-run",
            "--run-dir", str(run_dir),
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    err = capsys.readouterr().err.lower()
    assert "dirty_checkout" in err
    assert not (run_dir / "campaign-run" / "blocks").exists() or not list(
        (run_dir / "campaign-run" / "blocks").iterdir()
    )


@pytest.mark.asyncio
async def test_campaign_qwen_failure_after_gemma_complete_records_disposition(tmp_path, monkeypatch, capsys):
    """Gemma must complete a counted block; Qwen is mandatory-to-attempt.
    Once Gemma has already completed a full counted session, a Qwen
    admission failure is recorded as REPLICATION_DEFERRED_ADMISSION -- and
    that typed disposition must reach an on-disk artifact (not just a
    stderr message), reconstructible by scanning
    admissions/<block-id>-attempt-*.json."""
    from civ_mcp.arena.benchmark_admission import (
        REPLICATION_DEFERRED_ADMISSION,
        AdmissionError,
    )
    from civ_mcp.arena.benchmark_campaign import compile_campaign_schedule
    from civ_mcp.arena.benchmark_contract import load_campaign_manifest

    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    campaign = load_campaign_manifest(campaign_path)
    schedule = compile_campaign_schedule(campaign)
    gemma_block_id = campaign.models[0].block_id

    # Pre-seed gemma's block as already fully committed -- block_is_complete
    # requires each expected trial filename to be present AND stamped with
    # this exact campaign's fingerprint AND a matching, recorded
    # session.json's session_fingerprint (finding 5, final review; A3,
    # external review), so the fixture must carry the real fingerprint this
    # run-dir/campaign would actually stamp, not an arbitrary placeholder.
    campaign_fingerprint = await _campaign_store_fingerprint(campaign_path, run_dir)
    _preseed_gemma_block_complete(run_dir, gemma_block_id, schedule, campaign_fingerprint)

    monkeypatch.setenv("LITELLM_OPENAI_API_KEY", "test-key")

    class _QwenFailsPipeline:
        def __init__(self, _deps):
            pass

        async def admit(self, bundle, block, store, *, mode, **_kwargs):
            raise AdmissionError("tool_canary_failed", {"message": "qwen never emits tool calls"})

    monkeypatch.setattr(benchmark_runner, "_build_admission_pipeline", lambda args, api_key, **_kwargs: _QwenFailsPipeline(None))

    def _boom_run_resolved_block(*_args, **_kwargs):
        raise AssertionError("run_resolved_block must never run when admission fails")

    monkeypatch.setattr(benchmark_runner, "run_resolved_block", _boom_run_resolved_block)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--campaign", str(campaign_path),
            "--run-id", "campaign-run",
            "--run-dir", str(run_dir),
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 2
    err = capsys.readouterr().err.lower()
    assert "replication_deferred_admission" in err

    qwen_block_id = campaign.models[1].block_id
    admissions_dir = run_dir / "campaign-run" / "admissions"
    matches = [
        json.loads(p.read_text())
        for p in admissions_dir.iterdir()
        if p.name.startswith(f"{qwen_block_id}-attempt-")
    ]
    dispositions = [m for m in matches if m.get("disposition") == REPLICATION_DEFERRED_ADMISSION]
    assert len(dispositions) == 1
    assert dispositions[0]["underlying_failure"]["code"] == "tool_canary_failed"


@pytest.mark.asyncio
async def test_campaign_qwen_unclassified_failure_never_records_disposition(tmp_path, monkeypatch, capsys):
    """Finding 4 (final review), part (b): an `unexpected_admission_error`
    -- the catch-all for an unrecognized exception, never a real diagnosed
    admission-gate code -- must never be converted into a
    REPLICATION_DEFERRED_ADMISSION disposition by the CLI, even once Gemma
    has completed, and even on the very first Qwen attempt. "Unknown
    failures cannot be converted into a deferral.\""""
    from civ_mcp.arena.benchmark_admission import (
        REPLICATION_DEFERRED_ADMISSION,
        AdmissionError,
    )
    from civ_mcp.arena.benchmark_campaign import compile_campaign_schedule
    from civ_mcp.arena.benchmark_contract import load_campaign_manifest

    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    campaign = load_campaign_manifest(campaign_path)
    schedule = compile_campaign_schedule(campaign)
    gemma_block_id = campaign.models[0].block_id

    campaign_fingerprint = await _campaign_store_fingerprint(campaign_path, run_dir)
    _preseed_gemma_block_complete(run_dir, gemma_block_id, schedule, campaign_fingerprint)

    monkeypatch.setenv("LITELLM_OPENAI_API_KEY", "test-key")

    class _QwenFailsUnclassifiedPipeline:
        def __init__(self, _deps):
            pass

        async def admit(self, bundle, block, store, *, mode, **_kwargs):
            raise AdmissionError("unexpected_admission_error", {"message": "boom"})

    monkeypatch.setattr(
        benchmark_runner, "_build_admission_pipeline", lambda args, api_key, **_kwargs: _QwenFailsUnclassifiedPipeline(None)
    )

    def _boom_run_resolved_block(*_args, **_kwargs):
        raise AssertionError("run_resolved_block must never run when admission fails")

    monkeypatch.setattr(benchmark_runner, "run_resolved_block", _boom_run_resolved_block)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--campaign", str(campaign_path),
            "--run-id", "campaign-run",
            "--run-dir", str(run_dir),
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    err = capsys.readouterr().err.lower()
    assert "unexpected_admission_error" in err
    assert "refus" in err  # "refusing to record ... disposition for an unclassified failure"

    qwen_block_id = campaign.models[1].block_id
    admissions_dir = run_dir / "campaign-run" / "admissions"
    matches = (
        [
            json.loads(p.read_text())
            for p in admissions_dir.iterdir()
            if p.name.startswith(f"{qwen_block_id}-attempt-")
        ]
        if admissions_dir.is_dir()
        else []
    )
    dispositions = [m for m in matches if m.get("disposition") == REPLICATION_DEFERRED_ADMISSION]
    assert dispositions == []


@pytest.mark.asyncio
async def test_campaign_qwen_operator_error_code_never_records_disposition(tmp_path, monkeypatch, capsys):
    """D2 (external review wave D, Ruling G): a classified OPERATOR-ERROR
    code (dirty_checkout -- and by the same allowlist every tuner/GPU/
    boot/deploy/reload/popup/canonical-state code) must never be written
    as a REPLICATION_DEFERRED_ADMISSION disposition, even once Gemma has
    completed a full counted block. Only model-capability gate failure
    codes (REPLICATION_DEFERRAL_ELIGIBLE_CODES) are deferral-eligible."""
    from civ_mcp.arena.benchmark_admission import (
        REPLICATION_DEFERRED_ADMISSION,
        AdmissionError,
    )
    from civ_mcp.arena.benchmark_campaign import compile_campaign_schedule
    from civ_mcp.arena.benchmark_contract import load_campaign_manifest

    monkeypatch.setenv("LITELLM_OPENAI_API_KEY", "test-key")
    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    campaign = load_campaign_manifest(campaign_path)
    schedule = compile_campaign_schedule(campaign)
    gemma_block_id = campaign.models[0].block_id

    campaign_fingerprint = await _campaign_store_fingerprint(campaign_path, run_dir)
    _preseed_gemma_block_complete(run_dir, gemma_block_id, schedule, campaign_fingerprint)

    class _QwenFailsOperatorErrorPipeline:
        def __init__(self, _deps):
            pass

        async def admit(self, bundle, block, store, *, mode, **_kwargs):
            raise AdmissionError("dirty_checkout", {"message": "WSL checkout is dirty"})

    monkeypatch.setattr(
        benchmark_runner, "_build_admission_pipeline", lambda args, api_key, **_kwargs: _QwenFailsOperatorErrorPipeline(None)
    )

    def _boom_run_resolved_block(*_args, **_kwargs):
        raise AssertionError("run_resolved_block must never run when admission fails")

    monkeypatch.setattr(benchmark_runner, "run_resolved_block", _boom_run_resolved_block)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--campaign", str(campaign_path),
            "--run-id", "campaign-run",
            "--run-dir", str(run_dir),
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    err = capsys.readouterr().err.lower()
    assert "dirty_checkout" in err
    assert "refus" in err

    qwen_block_id = campaign.models[1].block_id
    admissions_dir = run_dir / "campaign-run" / "admissions"
    matches = (
        [
            json.loads(p.read_text())
            for p in admissions_dir.iterdir()
            if p.name.startswith(f"{qwen_block_id}-attempt-")
        ]
        if admissions_dir.is_dir()
        else []
    )
    dispositions = [m for m in matches if m.get("disposition") == REPLICATION_DEFERRED_ADMISSION]
    assert dispositions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["backend_auth_error", "backend_transport_error", "treatment_cannot_fire"]
)
async def test_campaign_qwen_auth_transport_or_authoring_code_never_records_disposition(
    tmp_path, monkeypatch, capsys, code
):
    """G2 (Ruling H) / G5 (external review wave G): backend_auth_error and
    backend_transport_error are operator/environment codes, and
    treatment_cannot_fire is a model-independent authoring/config property
    -- none may ever be written as a REPLICATION_DEFERRED_ADMISSION
    disposition, even once Gemma has completed a full counted block."""
    from civ_mcp.arena.benchmark_admission import (
        REPLICATION_DEFERRED_ADMISSION,
        AdmissionError,
    )
    from civ_mcp.arena.benchmark_campaign import compile_campaign_schedule
    from civ_mcp.arena.benchmark_contract import load_campaign_manifest

    monkeypatch.setenv("LITELLM_OPENAI_API_KEY", "test-key")
    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    campaign = load_campaign_manifest(campaign_path)
    schedule = compile_campaign_schedule(campaign)
    gemma_block_id = campaign.models[0].block_id

    campaign_fingerprint = await _campaign_store_fingerprint(campaign_path, run_dir)
    _preseed_gemma_block_complete(run_dir, gemma_block_id, schedule, campaign_fingerprint)

    class _QwenFailsPipeline:
        def __init__(self, _deps):
            pass

        async def admit(self, bundle, block, store, *, mode, **_kwargs):
            raise AdmissionError(code, {"message": f"injected {code}"})

    monkeypatch.setattr(
        benchmark_runner,
        "_build_admission_pipeline",
        lambda args, api_key, **_kwargs: _QwenFailsPipeline(None),
    )

    def _boom_run_resolved_block(*_args, **_kwargs):
        raise AssertionError("run_resolved_block must never run when admission fails")

    monkeypatch.setattr(benchmark_runner, "run_resolved_block", _boom_run_resolved_block)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--campaign", str(campaign_path),
            "--run-id", "campaign-run",
            "--run-dir", str(run_dir),
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    err = capsys.readouterr().err.lower()
    assert code in err
    assert "refus" in err

    qwen_block_id = campaign.models[1].block_id
    admissions_dir = run_dir / "campaign-run" / "admissions"
    matches = (
        [
            json.loads(p.read_text())
            for p in admissions_dir.iterdir()
            if p.name.startswith(f"{qwen_block_id}-attempt-")
        ]
        if admissions_dir.is_dir()
        else []
    )
    dispositions = [m for m in matches if m.get("disposition") == REPLICATION_DEFERRED_ADMISSION]
    assert dispositions == []


@pytest.mark.asyncio
async def test_campaign_counted_run_refuses_when_api_key_env_is_unset(tmp_path, monkeypatch, capsys):
    """D3 (external review wave D): a counted --campaign run whose api-key
    env var is unset/empty must refuse up front, naming the variable --
    never fall back to the placeholder "x", whose 401s would surface as
    backend_probe_errors (a deferral-ELIGIBLE model-capability code under
    Ruling G) instead of the credentials problem they actually are."""
    monkeypatch.delenv("LITELLM_OPENAI_API_KEY", raising=False)
    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    def _boom_build_pipeline(*_args, **_kwargs):
        raise AssertionError("no admission pipeline may be constructed without credentials")

    monkeypatch.setattr(benchmark_runner, "_build_admission_pipeline", _boom_build_pipeline)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--campaign", str(campaign_path),
            "--run-id", "campaign-run",
            "--run-dir", str(run_dir),
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "LITELLM_OPENAI_API_KEY" in err


@pytest.mark.asyncio
async def test_campaign_admit_only_exits_without_running_trials(tmp_path, monkeypatch):
    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    monkeypatch.setenv("LITELLM_OPENAI_API_KEY", "test-key")

    class _AdmitOnlyPipeline:
        def __init__(self, _deps):
            self.calls = []

        async def admit(self, bundle, block, store, *, mode, **_kwargs):
            self.calls.append((block.block_id, mode))
            return {"ok": True, "mode": mode}

    monkeypatch.setattr(benchmark_runner, "_build_admission_pipeline", lambda args, api_key, **_kwargs: _AdmitOnlyPipeline(None))

    def _boom_run_resolved_block(*_args, **_kwargs):
        raise AssertionError("run_resolved_block must never run for --admit-only")

    monkeypatch.setattr(benchmark_runner, "run_resolved_block", _boom_run_resolved_block)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--campaign", str(campaign_path),
            "--run-id", "campaign-run",
            "--run-dir", str(run_dir),
            "--admit-only", "gemma4-26b",
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 0
    assert not (run_dir / "campaign-run" / "blocks" / "gemma4-26b").exists()


@pytest.mark.asyncio
async def test_campaign_non_counting_validation_runs_one_pair_and_writes_report_under_validation_dir(
    tmp_path, monkeypatch
):
    """B2 (external review wave B): --non-counting-validation must actually
    run one minimal and one standard episode through the trusted
    run_resolved_block (spec Task-12: "one complete non-counting episode
    per arm"), and write a per-block report under validation/ -- never
    under blocks/, and never stamped with a counted campaign/session
    fingerprint pair."""
    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    monkeypatch.setenv("LITELLM_OPENAI_API_KEY", "test-key")

    class _ValidationPipeline:
        def __init__(self, _deps):
            pass

        async def admit(self, bundle, block, store, *, mode, **_kwargs):
            assert mode == "validation"
            return {
                "ok": True,
                "validation": True,
                "campaign_fingerprint": None,
                "admission_fingerprint": None,
                "gates": {
                    "model_admission": {
                        "resolved_endpoint": "http://validation-endpoint.invalid/v1",
                        "episode_wall_s": 300,
                    }
                },
            }

    monkeypatch.setattr(benchmark_runner, "_build_admission_pipeline", lambda args, api_key, **_kwargs: _ValidationPipeline(None))

    ran_schedules: list = []

    async def _fake_run_resolved_block(resolved):
        # Stand-in for the trusted run_resolved_block: commits one trial per
        # scheduled TrialSpec directly into the validation store, mirroring
        # what BenchmarkRunner._finalize_trial actually stamps (single
        # session_fingerprint stamp only -- no campaign_fingerprint, since
        # this store's own lock never declares one).
        ran_schedules.append(resolved.schedule)
        for spec in resolved.schedule:
            resolved.store.commit_trial(
                spec.index,
                {
                    "index": spec.index,
                    "position_id": spec.position_id,
                    # H1(a) (wave H): _finalize_trial stamps the full
                    # TrialSpec identity, and build_report now binds each
                    # committed trial to the scheduled entry at its index.
                    "pair_id": spec.pair_id,
                    "model": spec.model,
                    "arm_id": spec.arm_id,
                    "seed": spec.seed,
                    "attempt_count": 1,
                    "terminal": "finish_trial",
                    "session_fingerprint": resolved.store.fingerprint,
                    "steps": [],
                    "initial_state": {"turn": 157},
                    "final_state": {"turn": 157},
                },
            )
        return 0

    monkeypatch.setattr(benchmark_runner, "run_resolved_block", _fake_run_resolved_block)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--campaign", str(campaign_path),
            "--run-id", "campaign-run",
            "--run-dir", str(run_dir),
            "--non-counting-validation", "gemma4-26b",
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 0
    # Exactly one pair (one trial per arm): 2 trials, through the real
    # run_resolved_block seam.
    assert len(ran_schedules) == 1
    assert len(ran_schedules[0]) == 2
    assert {spec.arm_id for spec in ran_schedules[0]} == {"minimal", "standard"}

    validation_dir = run_dir / "campaign-run" / "validation" / "gemma4-26b"
    assert (validation_dir / "trials" / "trial-001.json").exists()
    assert (validation_dir / "trials" / "trial-002.json").exists()
    assert (validation_dir / "report.json").exists()
    assert (validation_dir / "report.md").exists()

    report = json.loads((validation_dir / "report.json").read_bytes())
    assert report["session"]["validation"] is True
    assert report["session"]["campaign_fingerprint"] is None

    # Structural requirement (B2): validation writes never touch blocks/,
    # and cannot satisfy block completeness for the real counted block.
    assert not (run_dir / "campaign-run" / "blocks" / "gemma4-26b").exists()
    from civ_mcp.arena import benchmark_admission

    context = await benchmark_runner._load_campaign_context(args)
    assert benchmark_admission.block_is_complete(context.store, "gemma4-26b") is False


async def test_campaign_non_counting_validation_admission_failure_never_runs_a_trial(tmp_path, monkeypatch):
    """A failed validation admission must behave exactly like the diagnostic
    modes: no trial ever runs, and nothing is written under validation/."""
    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    from civ_mcp.arena.benchmark_admission import AdmissionError

    monkeypatch.setenv("LITELLM_OPENAI_API_KEY", "test-key")

    class _FailingValidationPipeline:
        def __init__(self, _deps):
            pass

        async def admit(self, bundle, block, store, *, mode, **_kwargs):
            assert mode == "validation"
            raise AdmissionError("boot_health_missing_or_failed", {"message": "boom"})

    monkeypatch.setattr(
        benchmark_runner, "_build_admission_pipeline", lambda args, api_key, **_kwargs: _FailingValidationPipeline(None)
    )

    def _boom_run_resolved_block(*_args, **_kwargs):
        raise AssertionError("run_resolved_block must never run when validation admission fails")

    monkeypatch.setattr(benchmark_runner, "run_resolved_block", _boom_run_resolved_block)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--campaign", str(campaign_path),
            "--run-id", "campaign-run",
            "--run-dir", str(run_dir),
            "--non-counting-validation", "gemma4-26b",
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    assert not (run_dir / "campaign-run" / "validation").exists()
    assert not (run_dir / "campaign-run" / "blocks" / "gemma4-26b").exists()


@pytest.mark.asyncio
async def test_campaign_one_block_mode_stops_after_one_block(tmp_path, monkeypatch):
    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    admitted_blocks: list[str] = []

    monkeypatch.setenv("LITELLM_OPENAI_API_KEY", "test-key")

    class _OneBlockPipeline:
        def __init__(self, _deps):
            pass

        async def admit(self, bundle, block, store, *, mode, **_kwargs):
            admitted_blocks.append(block.block_id)
            from civ_mcp.arena.benchmark_runner import ResolvedBlock

            return ResolvedBlock(
                position=bundle.position,
                suite=None,
                schedule=(),
                store=None,
                gateway_url="http://example.invalid/v1",
                api_key="x",
                episode_wall_s=300,
                chat_template_kwargs={},
                user_prompt="",
            )

    run_resolved_block_calls: list = []

    async def _fake_run_resolved_block(resolved):
        run_resolved_block_calls.append(resolved)
        return 0

    monkeypatch.setattr(benchmark_runner, "_build_admission_pipeline", lambda args, api_key, **_kwargs: _OneBlockPipeline(None))
    monkeypatch.setattr(benchmark_runner, "run_resolved_block", _fake_run_resolved_block)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--campaign", str(campaign_path),
            "--run-id", "campaign-run",
            "--run-dir", str(run_dir),
            "--one-block",
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 0
    assert admitted_blocks == ["gemma4-26b"]
    assert len(run_resolved_block_calls) == 1


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

        async def disconnect(self):
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

        async def disconnect(self):
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
            reload_position=AsyncMock(return_value=True),
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


# ---------------------------------------------------------------------------
# G5 -- _run_async must close the live game connection and every cached
# backend client on every exit path, not just the happy path.
# ---------------------------------------------------------------------------


def _fake_build_live_dependencies_with_aclose(aclose_calls: list):
    def _build(**_kwargs) -> RunnerDependencies:
        async def aclose() -> None:
            aclose_calls.append(True)

        return RunnerDependencies(
            reload_position=AsyncMock(return_value=True),
            dismiss_popups=AsyncMock(return_value="POPUPS|none"),
            capture_state=AsyncMock(return_value={"turn": 42}),
            make_agent=lambda spec: _FinishingAgent(),
            probe_health=AsyncMock(return_value=_healthy_probe()),
            aclose=aclose,
        )

    return _build


@pytest.mark.asyncio
async def test_run_async_closes_connection_and_backends_on_normal_exit(tmp_path, monkeypatch):
    suite_path = _write_fixture_suite_and_position(tmp_path)
    run_dir_base = tmp_path / "runs"

    disconnect_calls: list = []

    class _FakeConnection:
        async def connect(self):
            return None

        async def disconnect(self):
            disconnect_calls.append(True)

    monkeypatch.setattr(benchmark_runner, "GameConnection", _FakeConnection)

    aclose_calls: list = []
    monkeypatch.setattr(
        benchmark_runner,
        "_build_live_dependencies",
        _fake_build_live_dependencies_with_aclose(aclose_calls),
    )

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
    assert disconnect_calls == [True]
    assert aclose_calls == [True]


@pytest.mark.asyncio
async def test_run_async_closes_connection_and_backends_on_exception_exit(tmp_path, monkeypatch):
    """Cleanup must run even when the session doesn't exit cleanly -- an
    unrecognized exception out of runner.run() still propagates (only
    SessionAborted is caught), but the connection and cached backend
    clients must not leak."""
    suite_path = _write_fixture_suite_and_position(tmp_path)
    run_dir_base = tmp_path / "runs"

    disconnect_calls: list = []

    class _FakeConnection:
        async def connect(self):
            return None

        async def disconnect(self):
            disconnect_calls.append(True)

    monkeypatch.setattr(benchmark_runner, "GameConnection", _FakeConnection)

    aclose_calls: list = []
    monkeypatch.setattr(
        benchmark_runner,
        "_build_live_dependencies",
        _fake_build_live_dependencies_with_aclose(aclose_calls),
    )

    class _BoomRunner:
        def __init__(self, **_kwargs):
            pass

        async def run(self, schedule):
            raise RuntimeError("kaboom")

    monkeypatch.setattr(benchmark_runner, "BenchmarkRunner", _BoomRunner)

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--suite", str(suite_path),
            "--run-id", "smoke-run",
            "--run-dir", str(run_dir_base),
            "--gateway-url", "http://example.invalid/v1",
            "--ungated-smoke",
        ]
    )

    with pytest.raises(RuntimeError, match="kaboom"):
        await benchmark_runner._run_async(args)

    assert disconnect_calls == [True]
    assert aclose_calls == [True]


@pytest.mark.asyncio
async def test_openai_compat_backend_aclose_closes_the_underlying_client():
    """Unit-level: OpenAICompatBackend must expose a close hook so
    _build_live_dependencies's aclose() has something real to call."""
    backend = benchmark_runner.OpenAICompatBackend(
        "http://example.invalid/v1", "x", "some-model"
    )
    close_calls: list = []

    async def fake_close():
        close_calls.append(True)

    backend._client.close = fake_close
    await backend.aclose()
    assert close_calls == [True]


# ---------------------------------------------------------------------------
# G6 -- an unset --api-key-env must not refuse to start (local gateways
# need no key), but it must print a clear one-line warning naming the env
# var, since a real remote endpoint will fail the admission probe with the
# "x" placeholder.
# ---------------------------------------------------------------------------


def _minimal_smoke_args(suite_path, run_dir) -> list:
    return [
        "--suite", str(suite_path),
        "--run-id", "smoke-run",
        "--run-dir", str(run_dir),
        "--gateway-url", "http://example.invalid/v1",
        "--ungated-smoke",
    ]


def _patch_fake_connection_and_noop_runner(monkeypatch) -> None:
    class _FakeConnection:
        async def connect(self):
            return None

        async def disconnect(self):
            return None

    monkeypatch.setattr(benchmark_runner, "GameConnection", _FakeConnection)

    class _FakeRunner:
        def __init__(self, **_kwargs):
            pass

        async def run(self, schedule):
            return None

    monkeypatch.setattr(benchmark_runner, "BenchmarkRunner", _FakeRunner)


@pytest.mark.asyncio
async def test_run_async_warns_when_api_key_env_var_is_unset(tmp_path, monkeypatch, capsys):
    suite_path = _write_fixture_suite_and_position(tmp_path)
    run_dir = tmp_path / "runs"
    monkeypatch.delenv("LITELLM_OPENAI_API_KEY", raising=False)
    _patch_fake_connection_and_noop_runner(monkeypatch)

    args = benchmark_runner._build_arg_parser().parse_args(
        _minimal_smoke_args(suite_path, run_dir)
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 0
    err = capsys.readouterr().err
    assert "LITELLM_OPENAI_API_KEY" in err
    assert "placeholder" in err.lower()


@pytest.mark.asyncio
async def test_run_async_no_warning_when_api_key_env_var_is_set(tmp_path, monkeypatch, capsys):
    suite_path = _write_fixture_suite_and_position(tmp_path)
    run_dir = tmp_path / "runs"
    monkeypatch.setenv("LITELLM_OPENAI_API_KEY", "sk-real-key")
    _patch_fake_connection_and_noop_runner(monkeypatch)

    args = benchmark_runner._build_arg_parser().parse_args(
        _minimal_smoke_args(suite_path, run_dir)
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 0
    err = capsys.readouterr().err
    assert "LITELLM_OPENAI_API_KEY" not in err


# ---------------------------------------------------------------------------
# G10 -- expected_state_sha256 is a declared manifest field that must
# actually be verified against state_digest(expected_state) before any
# trial runs, not just loaded and never read again.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_async_refuses_a_tampered_expected_state_sha256(tmp_path, monkeypatch, capsys):
    suite_path = _write_fixture_suite_and_position(tmp_path, expected_state_sha256="0" * 64)
    run_dir = tmp_path / "runs"
    _patch_fake_connection_and_noop_runner(monkeypatch)

    args = benchmark_runner._build_arg_parser().parse_args(
        _minimal_smoke_args(suite_path, run_dir)
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    err = capsys.readouterr().err
    # Both digests must be named so a human can actually diagnose which one
    # is stale/wrong.
    assert "0" * 64 in err
    from civ_mcp.arena.benchmark_state import state_digest
    assert state_digest({"turn": 42}) in err


@pytest.mark.asyncio
async def test_run_async_proceeds_when_expected_state_sha256_matches(tmp_path, monkeypatch):
    """The default fixture's expected_state_sha256 is now the real digest
    of {"turn": 42} -- a correct manifest must not be refused."""
    suite_path = _write_fixture_suite_and_position(tmp_path)
    run_dir = tmp_path / "runs"
    _patch_fake_connection_and_noop_runner(monkeypatch)

    args = benchmark_runner._build_arg_parser().parse_args(
        _minimal_smoke_args(suite_path, run_dir)
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_async_fails_closed_on_a_tampered_schedule_json_on_resume(tmp_path, monkeypatch):
    """Cheap fold-in: on resume, `_run_async` only checks whether
    schedule.json already exists -- it never re-verifies the FILE'S
    CONTENT against the session lock's schedule_fingerprint. A schedule.json
    corrupted or partially written by a prior crash (distinct from
    session.json, which BenchmarkStore.create already verifies byte-for-
    byte) would otherwise be silently trusted on resume."""
    suite_path = _write_fixture_suite_and_position(tmp_path)
    run_dir_base = tmp_path / "runs"

    class _FakeConnection:
        async def connect(self):
            return None

        async def disconnect(self):
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
            "--run-dir", str(run_dir_base),
            "--gateway-url", "http://example.invalid/v1",
            "--ungated-smoke",
        ]
    )

    first_exit_code = await benchmark_runner._run_async(args)
    assert first_exit_code == 0

    schedule_path = run_dir_base / "smoke-run" / "schedule.json"
    assert schedule_path.exists()
    schedule_path.write_text(json.dumps({"trials": []}), encoding="utf-8")

    second_exit_code = await benchmark_runner._run_async(args)
    assert second_exit_code == 1


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


def test_make_agent_binds_the_trial_specs_seed_to_the_backend():
    """F3 repro: backend_for() caches one backend per model built with the
    suite's static sampling config -- TrialSpec.seed is never applied to the
    backend that actually executes the trial, so a committed trial's
    recorded seed was never sent to the endpoint at all (invalidating
    paired-seed evidence)."""
    position = _position_manifest()
    arm = TreatmentArm("standard", "standard", {})
    suite = _suite_manifest((arm,), sampling=SamplingConfig(temperature=0.2, seed=None))
    deps = benchmark_runner._build_live_dependencies(
        connection=None,
        position=position,
        suite=suite,
        gateway_url="http://example.invalid/v1",
        api_key="x",
    )
    spec_a = TrialSpec(
        index=1, pair_id="p1", position_id="builder-cal-v1",
        model="qwen3.6-27b", arm_id="standard", seed=101,
    )
    spec_b = TrialSpec(
        index=2, pair_id="p2", position_id="builder-cal-v1",
        model="qwen3.6-27b", arm_id="standard", seed=202,
    )

    agent_a = deps.make_agent(spec_a)
    agent_b = deps.make_agent(spec_b)

    assert agent_a.backend.sampling.seed == 101
    assert agent_b.backend.sampling.seed == 202


@pytest.mark.asyncio
async def test_run_trial_hands_the_agent_a_real_game_state(tmp_path):
    """F4 repro: run_trial constructs `SimpleNamespace(conn=connection)` as
    the object passed to `agent.run()`. Registry tools call real GameState
    methods (gs.get_units() etc.), so every dispatched game tool would raise
    AttributeError in a live session -- swallowed into ERROR steps, so a
    trial commits as evidence of a model that "couldn't act" rather than
    surfacing the wiring bug."""
    from civ_mcp.game_state import GameState

    captured_gs: list[object] = []

    class _RecordingAgent:
        async def run(self, gs, player_id, turn):
            captured_gs.append(gs)
            return EpisodeEvidence(
                terminal=EpisodeTerminal.FINISH_TRIAL,
                steps=[],
                invalid_tool_calls=[],
                final_summary="done",
                wall_clock_s=1.0,
                prompt_tokens=1,
                completion_tokens=1,
            )

    fake_connection = object()
    deps = _deps(make_agent=lambda spec: _RecordingAgent(), connection=fake_connection)
    store = BenchmarkStore.create(tmp_path / "run", _lock())
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "standard"))

    assert len(captured_gs) == 1
    gs = captured_gs[0]
    assert isinstance(gs, GameState), f"expected a real GameState, got {gs!r}"
    assert hasattr(gs, "get_units")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failing_result",
    [
        "Error: Save 'foo' not found in Lua query or on filesystem. Check the name.",
        (
            "WARNING: FireTuner port never dropped after the Lua load of 'foo' -- "
            "the load may not have engaged; no Escape was sent."
        ),
        "WARNING: FireTuner port did not reopen within the wait window for 'foo'.",
    ],
)
async def test_live_reload_position_raises_on_failure_strings(monkeypatch, failing_result):
    """F6 repro: load_game_save reports most failures as strings ("Error: ...",
    "WARNING: FireTuner port never dropped...", menu-fallback text) rather
    than raising. reload_position discarded the return value entirely, so a
    failed reload proceeded to the checksum check and killed the whole
    session as checksum_mismatch instead of a retryable
    RELOAD_OR_RECONNECT infra attempt."""
    position = _position_manifest()
    arm = TreatmentArm("standard", "standard", {})
    suite = _suite_manifest((arm,))
    monkeypatch.setattr(
        benchmark_runner, "load_game_save", AsyncMock(return_value=failing_result)
    )
    deps = benchmark_runner._build_live_dependencies(
        connection=object(),
        position=position,
        suite=suite,
        gateway_url="http://example.invalid/v1",
        api_key="x",
    )

    with pytest.raises(Exception):
        await deps.reload_position(position.position_id)


@pytest.mark.asyncio
async def test_live_reload_position_passes_on_success_string(monkeypatch):
    position = _position_manifest()
    arm = TreatmentArm("standard", "standard", {})
    suite = _suite_manifest((arm,))
    success_result = (
        "Loaded builder-cal-v1: world ready, FireTuner port is open. "
        "Reconnect and verify with get_game_overview."
    )
    monkeypatch.setattr(
        benchmark_runner, "load_game_save", AsyncMock(return_value=success_result)
    )
    deps = benchmark_runner._build_live_dependencies(
        connection=object(),
        position=position,
        suite=suite,
        gateway_url="http://example.invalid/v1",
        api_key="x",
    )

    verified = await deps.reload_position(position.position_id)  # must not raise
    assert verified is True


@pytest.mark.asyncio
async def test_live_reload_position_reports_unverified_for_stable_open_port_fallback(monkeypatch):
    """G3: the F16(b) stable-open-port fallback is success-shaped text the
    launcher cannot itself verify (no game connection) -- reload_position
    must surface that as verified=False, not silently claim verified=True
    like the observed-drop path."""
    position = _position_manifest()
    arm = TreatmentArm("standard", "standard", {})
    suite = _suite_manifest((arm,))
    unverified_result = (
        "Loaded builder-cal-v1 (UNVERIFIED: port drop not observed -- "
        "likely faster than the poll interval): world ready, FireTuner "
        "port is open. Reconnect and verify with get_game_overview."
    )
    monkeypatch.setattr(
        benchmark_runner, "load_game_save", AsyncMock(return_value=unverified_result)
    )
    deps = benchmark_runner._build_live_dependencies(
        connection=object(),
        position=position,
        suite=suite,
        gateway_url="http://example.invalid/v1",
        api_key="x",
    )

    verified = await deps.reload_position(position.position_id)
    assert verified is False


class _FakeAdmissionConnection:
    async def connect(self):
        return None

    async def disconnect(self):
        return None


def _admission_args(**overrides) -> argparse.Namespace:
    fields = dict(wsl_repo="/wsl/repo", windows_repo="/windows/repo")
    fields.update(overrides)
    return argparse.Namespace(**fields)


@pytest.mark.asyncio
async def test_live_admission_reload_and_capture_delegates_to_reload_position(monkeypatch):
    """Finding 2 (final review): the admission dependency's inline
    reload_and_capture must call the shared production `reload_position`
    rather than re-deriving its own success/verified classification --
    otherwise reload semantics can drift between the counted trial path and
    admission. Prove delegation directly: stub reload_position itself and
    assert reload_and_capture returns exactly its result, never
    re-computing verified from a raw load_game_save string."""
    position = _position_manifest()
    monkeypatch.setattr(benchmark_runner, "GameConnection", _FakeAdmissionConnection)
    monkeypatch.setattr(
        benchmark_runner, "dismiss_blocking_popups", AsyncMock(return_value="POPUPS|none")
    )
    monkeypatch.setattr(
        benchmark_runner, "capture_canonical_state", AsyncMock(return_value={"turn": 42})
    )

    calls: list = []

    async def _fake_reload_position(connection, pos):
        calls.append(pos.position_id)
        return True

    monkeypatch.setattr(benchmark_runner, "reload_position", _fake_reload_position)
    # If reload_and_capture still called load_game_save directly (the old
    # re-derived-classification path) instead of delegating, this would be
    # exercised -- assert it never is.
    monkeypatch.setattr(
        benchmark_runner,
        "load_game_save",
        AsyncMock(side_effect=AssertionError("load_game_save must not be called directly")),
    )

    deps = benchmark_runner._build_live_admission_dependencies(
        args=_admission_args(), api_key="x"
    )

    result = await deps.reload_and_capture(position)

    assert calls == [position.position_id]
    assert result["reload"]["verified"] is True
    assert result["canonical_state"] == {"turn": 42}


@pytest.mark.asyncio
async def test_live_admission_reload_and_capture_reports_unverified_on_reload_failure(monkeypatch):
    """A production reload_position failure (raised RuntimeError) must
    surface as verified=False evidence, not propagate as an unhandled
    exception out of the admission dependency -- admission's own
    production_reload_not_verified gate is what turns that into a fail-
    closed refusal."""
    position = _position_manifest()
    monkeypatch.setattr(benchmark_runner, "GameConnection", _FakeAdmissionConnection)
    monkeypatch.setattr(
        benchmark_runner, "dismiss_blocking_popups", AsyncMock(return_value="POPUPS|none")
    )
    monkeypatch.setattr(
        benchmark_runner, "capture_canonical_state", AsyncMock(return_value={"turn": 42})
    )

    async def _failing_reload_position(connection, pos):
        raise RuntimeError(f"reload_position({pos.game_save_name!r}) failed: Error: not found")

    monkeypatch.setattr(benchmark_runner, "reload_position", _failing_reload_position)
    # Same delegation proof as the success test: if this path still called
    # load_game_save directly instead of routing through (the stubbed,
    # raising) reload_position, this would fire instead of the RuntimeError
    # catch under test.
    monkeypatch.setattr(
        benchmark_runner,
        "load_game_save",
        AsyncMock(side_effect=AssertionError("load_game_save must not be called directly")),
    )

    deps = benchmark_runner._build_live_admission_dependencies(
        args=_admission_args(), api_key="x"
    )

    result = await deps.reload_and_capture(position)

    assert result["reload"]["verified"] is False


# ---------------------------------------------------------------------------
# B3 (external review wave B): the identity/seed probe and both tool
# canaries must run under the exact production system-prompt shape (spec
# Sec 7: "exact system prompt shape") -- BENCHMARK_SYSTEM plus the frozen
# campaign user prompt, not a bare ad hoc user message with no system turn.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_admission_probe_backend_uses_benchmark_system_and_frozen_prompt(monkeypatch):
    recorded: list = []

    async def _fake_probe_backend_impl(backend, messages, tools):
        recorded.append(messages)
        return benchmark_backend_module.BackendProbe(
            samples=1, model=backend.model, model_confirmed=True, seed_honored=True
        )

    monkeypatch.setattr(benchmark_backend_module, "probe_backend", _fake_probe_backend_impl)

    deps = benchmark_runner._build_live_admission_dependencies(
        args=_admission_args(), api_key="x", user_prompt="Assess the current turn and call finish_trial."
    )

    await deps.probe_backend(
        model="gemma4-26b",
        endpoint="http://example.invalid/v1",
        sampling=SamplingConfig(),
        chat_template_kwargs={"enable_thinking": False},
        tools=[],
    )

    assert len(recorded) == 1
    assert recorded[0] == [
        {"role": "system", "content": BENCHMARK_SYSTEM},
        {"role": "user", "content": "Assess the current turn and call finish_trial."},
    ]


@pytest.mark.asyncio
async def test_live_admission_probe_backend_folds_served_model_ids(monkeypatch):
    """B4 (external review wave B): probe_backend_dep must fold the live
    backend's served /v1/models listing into the returned BackendProbe --
    supplementary endpoint identity, reused from the same live backend the
    probe itself just used (no separate network call site)."""
    from civ_mcp.arena.backends import OpenAICompatBackend

    async def _fake_probe_backend_impl(backend, messages, tools):
        return benchmark_backend_module.BackendProbe(
            samples=1, model=backend.model, model_confirmed=True, seed_honored=True
        )

    async def _fake_list_model_ids(self):
        return ("gemma4-26b", "gemma4-26b-fp8")

    monkeypatch.setattr(benchmark_backend_module, "probe_backend", _fake_probe_backend_impl)
    monkeypatch.setattr(OpenAICompatBackend, "list_model_ids", _fake_list_model_ids)

    deps = benchmark_runner._build_live_admission_dependencies(
        args=_admission_args(), api_key="x", user_prompt="Assess the current turn and call finish_trial."
    )

    probe = await deps.probe_backend(
        model="gemma4-26b",
        endpoint="http://example.invalid/v1",
        sampling=SamplingConfig(),
        chat_template_kwargs={"enable_thinking": False},
        tools=[],
    )

    assert probe.served_model_ids == ("gemma4-26b", "gemma4-26b-fp8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("make_exc", "expected_kind"),
    [
        (
            lambda: __import__("openai").AuthenticationError(
                "invalid key",
                response=__import__("httpx").Response(
                    401, request=__import__("httpx").Request("GET", "http://x/v1/models")
                ),
                body=None,
            ),
            "auth",
        ),
        (lambda: __import__("httpx").ConnectError("connection refused"), "transport"),
    ],
)
async def test_live_admission_listing_auth_or_transport_failure_is_classified(
    monkeypatch, make_exc, expected_kind
):
    """G2 (external review wave G, Ruling H): a served-model listing that
    fails for auth/transport reasons must NOT silently fold an empty
    listing into the probe (indistinguishable from a genuinely-empty
    endpoint) -- it is recorded as a classified probe error, so
    admit_model_block refuses under backend_auth_error /
    backend_transport_error, never endpoint_identity_mismatch."""
    from civ_mcp.arena.backends import OpenAICompatBackend

    async def _fake_probe_backend_impl(backend, messages, tools):
        return benchmark_backend_module.BackendProbe(
            samples=1, model=backend.model, model_confirmed=True, seed_honored=True
        )

    async def _raising_list_model_ids(self):
        raise make_exc()

    monkeypatch.setattr(benchmark_backend_module, "probe_backend", _fake_probe_backend_impl)
    monkeypatch.setattr(OpenAICompatBackend, "list_model_ids", _raising_list_model_ids)

    deps = benchmark_runner._build_live_admission_dependencies(
        args=_admission_args(), api_key="x", user_prompt="Assess the current turn and call finish_trial."
    )

    probe = await deps.probe_backend(
        model="gemma4-26b",
        endpoint="http://example.invalid/v1",
        sampling=SamplingConfig(),
        chat_template_kwargs={"enable_thinking": False},
        tools=[],
    )

    assert probe.served_model_ids == ()
    assert len(probe.errors) == 1
    assert "listing failed" in probe.errors[0]
    assert probe.error_kinds == (expected_kind,)


@pytest.mark.asyncio
async def test_live_admission_listing_failure_fold_in_backfills_legacy_error_kinds(monkeypatch):
    """J7 (external review wave J): folding a classified listing failure
    into a probe carrying the LEGACY error_kinds=() shape (pre-G2 fakes/
    callers) used to append one kind against N+1 errors -- a length
    mismatch that surfaced a real 401 as
    backend_error_classification_misaligned instead of backend_auth_error.
    The fold-in must backfill the pre-existing errors' kinds ("transport",
    H6's fail-closed default for a genuinely absent classification) so it
    never manufactures misalignment."""
    from civ_mcp.arena.backends import OpenAICompatBackend, RetryPolicy
    from civ_mcp.arena.benchmark_gates import admit_model_block

    async def _fake_probe_backend_impl(backend, messages, tools):
        # Legacy shape: one recorded error, NO error_kinds at all.
        return benchmark_backend_module.BackendProbe(
            samples=1,
            model=backend.model,
            model_confirmed=True,
            seed_honored=True,
            errors=["legacy unclassified probe error"],
        )

    async def _raising_list_model_ids(self):
        import httpx
        import openai

        request = httpx.Request("GET", "http://x/v1/models")
        raise openai.AuthenticationError(
            "invalid key", response=httpx.Response(401, request=request), body=None
        )

    monkeypatch.setattr(benchmark_backend_module, "probe_backend", _fake_probe_backend_impl)
    monkeypatch.setattr(OpenAICompatBackend, "list_model_ids", _raising_list_model_ids)

    deps = benchmark_runner._build_live_admission_dependencies(
        args=_admission_args(), api_key="x", user_prompt="Assess the current turn and call finish_trial."
    )

    probe = await deps.probe_backend(
        model="gemma4-26b",
        endpoint="http://example.invalid/v1",
        sampling=SamplingConfig(),
        chat_template_kwargs={"enable_thinking": False},
        tools=[],
    )

    assert len(probe.errors) == 2
    assert probe.error_kinds == ("transport", "auth")

    from civ_mcp.arena.benchmark_backend import ToolCanaryEvidence

    canary = ToolCanaryEvidence(
        arm_id="minimal", finish_trial_ok=True, required_argument_ok=True,
        observed_calls=(), errors=(),
    )
    with pytest.raises(GateFailure) as exc_info:
        admit_model_block(
            requested_model="gemma4-26b",
            resolved_model="gemma4-26b",
            requested_endpoint="http://example.invalid/v1",
            resolved_endpoint="http://example.invalid/v1",
            registry_fingerprint="fp",
            gpu_topology={"host_id": "h", "gpu_indexes": [0]},
            retry_policy=RetryPolicy(max_attempts=1),
            sampling=SamplingConfig(),
            probe=probe,
            briefing_required=False,
            briefing_budget_chars=None,
            tool_canaries={"minimal": canary},
            expected_arm_ids=["minimal"],
            max_steps=40,
        )
    assert exc_info.value.code == "backend_auth_error"


@pytest.mark.asyncio
async def test_live_admission_listing_404_is_best_effort_nongating_diagnostics(monkeypatch, capsys):
    """J7/P2a (external review wave J): a served-model listing 404
    (NotFoundError -- the /v1/models route simply isn't implemented) while
    the chat probe SUCCEEDED is not an admission failure. The documented B4
    best-effort contract applies to that one shape: served_model_ids stays
    (), the failure is recorded as non-gating diagnostics (stderr), and no
    classified error is folded into the probe. (A 404 alongside a FAILED
    chat probe never turns a broken backend admissible -- the probe's own
    recorded errors gate admission first.)"""
    from civ_mcp.arena.backends import OpenAICompatBackend

    async def _fake_probe_backend_impl(backend, messages, tools):
        return benchmark_backend_module.BackendProbe(
            samples=1, model=backend.model, model_confirmed=True, seed_honored=True
        )

    async def _not_found_list_model_ids(self):
        import httpx
        import openai

        request = httpx.Request("GET", "http://x/v1/models")
        raise openai.NotFoundError(
            "no such route", response=httpx.Response(404, request=request), body=None
        )

    monkeypatch.setattr(benchmark_backend_module, "probe_backend", _fake_probe_backend_impl)
    monkeypatch.setattr(OpenAICompatBackend, "list_model_ids", _not_found_list_model_ids)

    deps = benchmark_runner._build_live_admission_dependencies(
        args=_admission_args(), api_key="x", user_prompt="Assess the current turn and call finish_trial."
    )

    probe = await deps.probe_backend(
        model="gemma4-26b",
        endpoint="http://example.invalid/v1",
        sampling=SamplingConfig(),
        chat_template_kwargs={"enable_thinking": False},
        tools=[],
    )

    assert probe.served_model_ids == ()
    assert probe.errors == []
    assert probe.error_kinds == ()
    assert "served-model listing" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_live_admission_listing_unsupported_endpoint_stays_best_effort(monkeypatch):
    """G2: any OTHER listing failure (an endpoint that simply doesn't
    expose /v1/models) keeps the B4 best-effort behavior -- empty tuple,
    no probe error, admission unaffected."""
    from civ_mcp.arena.backends import OpenAICompatBackend

    async def _fake_probe_backend_impl(backend, messages, tools):
        return benchmark_backend_module.BackendProbe(
            samples=1, model=backend.model, model_confirmed=True, seed_honored=True
        )

    async def _unsupported_list_model_ids(self):
        raise RuntimeError("endpoint does not support /v1/models")

    monkeypatch.setattr(benchmark_backend_module, "probe_backend", _fake_probe_backend_impl)
    monkeypatch.setattr(OpenAICompatBackend, "list_model_ids", _unsupported_list_model_ids)

    deps = benchmark_runner._build_live_admission_dependencies(
        args=_admission_args(), api_key="x", user_prompt="Assess the current turn and call finish_trial."
    )

    probe = await deps.probe_backend(
        model="gemma4-26b",
        endpoint="http://example.invalid/v1",
        sampling=SamplingConfig(),
        chat_template_kwargs={"enable_thinking": False},
        tools=[],
    )

    assert probe.served_model_ids == ()
    assert probe.errors == []


@pytest.mark.asyncio
async def test_live_admission_probe_tool_capability_includes_benchmark_system(monkeypatch):
    recorded_kwargs: list = []

    async def _fake_probe_tool_capability_impl(backend, *, arm_id, tools, system_prompt=None):
        recorded_kwargs.append({"arm_id": arm_id, "system_prompt": system_prompt})
        return benchmark_backend_module.ToolCanaryEvidence(
            arm_id=arm_id, finish_trial_ok=True, required_argument_ok=True, observed_calls=(), errors=()
        )

    monkeypatch.setattr(benchmark_backend_module, "probe_tool_capability", _fake_probe_tool_capability_impl)

    deps = benchmark_runner._build_live_admission_dependencies(
        args=_admission_args(), api_key="x", user_prompt="Assess the current turn and call finish_trial."
    )

    await deps.probe_tool_capability(
        model="gemma4-26b",
        endpoint="http://example.invalid/v1",
        arm_id="standard",
        tools=[],
        sampling=SamplingConfig(),
        chat_template_kwargs={"enable_thinking": False},
    )

    assert recorded_kwargs == [{"arm_id": "standard", "system_prompt": BENCHMARK_SYSTEM}]


# ---------------------------------------------------------------------------
# B7 (external review wave B): injected command runners must never stall a
# campaign forever on a lapsed key or hung command -- sensible timeouts
# surfacing as CommandResult failures (never an unhandled exception), and
# ssh must never block on an interactive prompt (-o BatchMode=yes) or hang
# indefinitely trying to connect (-o ConnectTimeout).
# ---------------------------------------------------------------------------


def test_run_local_command_passes_a_timeout_to_subprocess(monkeypatch):
    recorded_kwargs = {}

    def _fake_run(argv, **kwargs):
        recorded_kwargs.update(kwargs)
        import subprocess as _subprocess

        return _subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    result = benchmark_runner._run_local_command(["git", "-C", "/repo", "rev-parse", "HEAD"])

    assert result.returncode == 0
    assert "timeout" in recorded_kwargs
    assert recorded_kwargs["timeout"] == pytest.approx(60.0)


def test_run_local_command_timeout_surfaces_as_command_result_failure(monkeypatch):
    import subprocess as _subprocess

    def _fake_run(argv, **kwargs):
        raise _subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    monkeypatch.setattr("subprocess.run", _fake_run)

    result = benchmark_runner._run_local_command(["git", "-C", "/repo", "rev-parse", "HEAD"])

    assert result.returncode != 0
    assert "timed out" in result.stderr.lower()


def test_run_windows_command_passes_a_timeout_to_subprocess(monkeypatch):
    recorded_kwargs = {}

    def _fake_run(argv, **kwargs):
        recorded_kwargs.update(kwargs)
        import subprocess as _subprocess

        return _subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    result = benchmark_runner._run_windows_command(["git", "-C", "C:\\repo", "rev-parse", "HEAD"])

    assert result.returncode == 0
    assert "timeout" in recorded_kwargs
    assert recorded_kwargs["timeout"] == pytest.approx(60.0)


def test_run_windows_command_timeout_surfaces_as_command_result_failure(monkeypatch):
    import subprocess as _subprocess

    def _fake_run(argv, **kwargs):
        raise _subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    monkeypatch.setattr("subprocess.run", _fake_run)

    result = benchmark_runner._run_windows_command(["git", "-C", "C:\\repo", "rev-parse", "HEAD"])

    assert result.returncode != 0
    assert "timed out" in result.stderr.lower()


def _recorded_windows_argv(monkeypatch, argv):
    seen = {}

    def _fake_run(run_argv, **kwargs):
        seen["argv"] = list(run_argv)
        import subprocess as _subprocess

        return _subprocess.CompletedProcess(run_argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)
    benchmark_runner._run_windows_command(argv)
    return seen["argv"]


def test_run_windows_command_rewrites_to_git_exe_for_a_windows_shaped_path(monkeypatch):
    """A `C:\\...` repo path is only reachable through the Windows git, so
    the .exe rewrite stays for that shape."""
    argv = _recorded_windows_argv(
        monkeypatch, ["git", "-C", "C:\\Users\\wrisl\\dev\\civ6-mcp", "rev-parse", "HEAD"]
    )

    assert argv[0] == "git.exe"


def test_run_windows_command_uses_the_linux_git_for_a_wsl_mounted_path(monkeypatch):
    """NEW-1 (wave-J verification): `_derive_windows_repo_default` yields the
    WSL-mounted view (`/mnt/c/...`) of the Windows companion checkout. The
    Linux git reads that working tree directly; rewriting to `git.exe`
    would impose a Git-for-Windows dependency that is absent on this
    machine, failing the clean-checkout gate with FileNotFoundError before
    any evidence is collected."""
    argv = _recorded_windows_argv(
        monkeypatch, ["git", "-C", "/mnt/c/Users/wrisl/dev/civ6-mcp", "rev-parse", "HEAD"]
    )

    assert argv[0] == "git"
    assert "git.exe" not in argv


def test_run_ssh_command_uses_batch_mode_connect_timeout_and_a_run_timeout(monkeypatch):
    recorded = {}

    def _fake_run(argv, **kwargs):
        recorded["argv"] = argv
        recorded["kwargs"] = kwargs
        import subprocess as _subprocess

        return _subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    result = benchmark_runner._run_ssh_command("riz-llm", ["nvidia-smi", "--query-gpu=index,uuid"])

    assert result.returncode == 0
    assert recorded["argv"][0] == "ssh"
    assert "-o" in recorded["argv"] and "BatchMode=yes" in recorded["argv"]
    assert any(a.startswith("ConnectTimeout=") for a in recorded["argv"])
    assert recorded["argv"][-2] == "riz-llm"
    assert "timeout" in recorded["kwargs"]
    assert recorded["kwargs"]["timeout"] == pytest.approx(120.0)


def test_run_ssh_command_timeout_surfaces_as_command_result_failure(monkeypatch):
    import subprocess as _subprocess

    def _fake_run(argv, **kwargs):
        raise _subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    monkeypatch.setattr("subprocess.run", _fake_run)

    result = benchmark_runner._run_ssh_command("riz-llm", ["nvidia-smi"])

    assert result.returncode != 0
    assert "timed out" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Task 4: extract a resolved-block handoff and structurally exclude smoke
# evidence from counted (campaign) provenance.
# ---------------------------------------------------------------------------


class _FakeGpuRegistry:
    """Minimal registry fake exposing exactly what gpu_evidence's closure
    calls: `.endpoint(endpoint_id) -> Endpoint`."""

    def __init__(self, endpoint: Endpoint):
        self._endpoint = endpoint

    def endpoint(self, endpoint_id: str) -> Endpoint:
        assert endpoint_id == self._endpoint.id
        return self._endpoint


def _gpu_endpoint(units=("civ-arena-gemma4",)) -> Endpoint:
    return Endpoint(
        id="home-gpu0-cpp",
        kind="llamacpp",
        host_id="riz-llm",
        gpu_indexes=(0,),
        port=8000,
        urls={},
        units=tuple(units),
        modes=(),
        drain_by_hosts=(),
        acquisition="static",
    )


def _gpu_proc(*, service, pid=111) -> GpuProcess:
    return GpuProcess(
        host="riz-llm", gpu_index=0, gpu_uuid="GPU-abc", pid=pid, process_name="proc", service=service
    )


def _gpu_evidence_fn(monkeypatch, endpoint: Endpoint, processes: list[GpuProcess]):
    monkeypatch.setattr(endpoint_registry_module, "_registry", lambda: _FakeGpuRegistry(endpoint))
    monkeypatch.setattr(
        benchmark_live_evidence_module, "collect_gpu_evidence", lambda **_kwargs: processes
    )
    deps = benchmark_runner._build_live_admission_dependencies(args=_admission_args(), api_key="x")
    return deps.gpu_evidence(endpoint.id)


def test_gpu_evidence_passes_when_declared_unit_is_resident(monkeypatch):
    """Finding 3 (final review): the endpoint's own declared unit being
    resident on its own GPU is the ordinary, expected case -- it must
    pass, not require an operator to separately acknowledge it."""
    endpoint = _gpu_endpoint(units=("civ-arena-gemma4",))
    result = _gpu_evidence_fn(monkeypatch, endpoint, [_gpu_proc(service="civ-arena-gemma4")])
    assert result["ok"] is True


def test_gpu_evidence_passes_when_declared_unit_is_absent_idle_gpu(monkeypatch):
    """The bug this finding fixes: static `set(endpoint.units)` fed as
    `approved_services` made check_gpu_conflicts's EXACT set-equality
    compare an idle GPU's empty observed set against {declared unit} and
    always fail. An idle GPU (no processes at all) must pass."""
    endpoint = _gpu_endpoint(units=("civ-arena-gemma4",))
    result = _gpu_evidence_fn(monkeypatch, endpoint, [])
    assert result["ok"] is True


def test_gpu_evidence_blocks_on_foreign_service(monkeypatch):
    endpoint = _gpu_endpoint(units=("civ-arena-gemma4",))
    with pytest.raises(GateFailure) as exc_info:
        _gpu_evidence_fn(
            monkeypatch,
            endpoint,
            [_gpu_proc(service="civ-arena-gemma4"), _gpu_proc(service="ollama", pid=222)],
        )
    assert exc_info.value.code == "gpu_conflict_not_acknowledged"


def test_gpu_evidence_blocks_on_unidentified_process(monkeypatch):
    """Unidentified process always blocks -- this must stay intact even
    though the endpoint's own declared unit is filtered out first."""
    endpoint = _gpu_endpoint(units=("civ-arena-gemma4",))
    with pytest.raises(GateFailure) as exc_info:
        _gpu_evidence_fn(
            monkeypatch,
            endpoint,
            [_gpu_proc(service="civ-arena-gemma4"), _gpu_proc(service=None, pid=333)],
        )
    assert exc_info.value.code == "gpu_conflict_unidentified_process"


def test_gpu_evidence_records_filtered_own_unit_rows_for_a_busy_gpu(monkeypatch):
    """B6 (external review wave B): the own-unit rows dropped before
    check_gpu_conflicts must not simply vanish -- they must appear in the
    returned gpu_isolation evidence, so a busy GPU (own unit resident) is
    distinguishable post-mortem from a genuinely idle one."""
    endpoint = _gpu_endpoint(units=("civ-arena-gemma4",))
    result = _gpu_evidence_fn(monkeypatch, endpoint, [_gpu_proc(service="civ-arena-gemma4")])
    assert result["ok"] is True
    assert len(result["filtered_own_unit_rows"]) == 1
    assert result["filtered_own_unit_rows"][0]["service"] == "civ-arena-gemma4"


def test_gpu_evidence_records_no_filtered_rows_for_an_idle_gpu(monkeypatch):
    """The idle case (no processes at all) must record an empty
    filtered_own_unit_rows list, not merely omit the key -- so "busy vs
    idle" is distinguishable by inspecting the same field either way."""
    endpoint = _gpu_endpoint(units=("civ-arena-gemma4",))
    result = _gpu_evidence_fn(monkeypatch, endpoint, [])
    assert result["ok"] is True
    assert result["filtered_own_unit_rows"] == []


class _FakeBlockConnection:
    async def connect(self):
        return None

    async def disconnect(self):
        return None


def _resolved_block(schedule, store, **overrides) -> "benchmark_runner.ResolvedBlock":
    fields = dict(
        position=_position_manifest(),
        suite=_suite_manifest((TreatmentArm("minimal", "minimal", {}),)),
        schedule=schedule,
        store=store,
        gateway_url="http://example.invalid/v1",
        api_key="x",
        episode_wall_s=300,
        chat_template_kwargs={"enable_thinking": False},
        user_prompt="",
    )
    fields.update(overrides)
    return benchmark_runner.ResolvedBlock(**fields)


@pytest.mark.asyncio
async def test_run_resolved_block_delegates_to_existing_benchmark_runner(tmp_path, monkeypatch):
    """run_resolved_block must not replicate or modify the trial loop: it
    hands the exact compiled schedule to the existing, unmodified
    BenchmarkRunner.run() -- unchanged (the very same tuple object) and in
    order -- and does nothing else with it."""
    monkeypatch.setattr(benchmark_runner, "GameConnection", _FakeBlockConnection)

    build_calls: list[dict] = []

    def _fake_build_live_dependencies(**kwargs):
        build_calls.append(kwargs)
        return _deps()

    monkeypatch.setattr(benchmark_runner, "_build_live_dependencies", _fake_build_live_dependencies)

    run_calls: list[object] = []
    constructor_calls: list[dict] = []

    class _SpyRunner:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)

        async def run(self, schedule_arg):
            run_calls.append(schedule_arg)

    monkeypatch.setattr(benchmark_runner, "BenchmarkRunner", _SpyRunner)

    store = BenchmarkStore.create(tmp_path / "run", _lock())
    schedule = (_spec(1, "minimal"), _spec(2, "minimal"))
    block = _resolved_block(schedule, store)

    exit_code = await benchmark_runner.run_resolved_block(block)

    assert exit_code == 0
    # The exact same tuple object, unchanged and in order -- not a copy,
    # not reordered, not filtered.
    assert run_calls == [schedule]
    assert run_calls[0] is schedule
    assert constructor_calls[0]["store"] is store
    assert build_calls[0]["position"] is block.position
    assert build_calls[0]["suite"] is block.suite
    assert build_calls[0]["episode_wall_s"] == 300
    assert build_calls[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert build_calls[0]["user_prompt"] == ""


@pytest.mark.asyncio
async def test_run_resolved_block_closes_connection_and_backends(tmp_path, monkeypatch):
    disconnect_calls: list = []

    class _FakeConnection:
        async def connect(self):
            return None

        async def disconnect(self):
            disconnect_calls.append(True)

    monkeypatch.setattr(benchmark_runner, "GameConnection", _FakeConnection)

    aclose_calls: list = []

    def _fake_build_live_dependencies(**_kwargs):
        async def aclose():
            aclose_calls.append(True)

        return _deps(aclose=aclose)

    monkeypatch.setattr(benchmark_runner, "_build_live_dependencies", _fake_build_live_dependencies)

    class _NoopRunner:
        def __init__(self, **_kwargs):
            pass

        async def run(self, schedule):
            return None

    monkeypatch.setattr(benchmark_runner, "BenchmarkRunner", _NoopRunner)

    store = BenchmarkStore.create(tmp_path / "run", _lock())
    block = _resolved_block((_spec(1, "minimal"),), store)

    exit_code = await benchmark_runner.run_resolved_block(block)

    assert exit_code == 0
    assert disconnect_calls == [True]
    assert aclose_calls == [True]


@pytest.mark.asyncio
async def test_finalize_trial_stamps_campaign_and_session_fingerprints(tmp_path):
    """A counted campaign block's lock carries a non-empty
    campaign_fingerprint alongside session_fingerprint (see
    benchmark_campaign.build_campaign_lock) -- _finalize_trial must copy
    BOTH stamps from the store's own lock into every committed trial
    payload, not just session_fingerprint."""
    lock = {**_lock(), "campaign_fingerprint": "camp789"}
    store = BenchmarkStore.create(tmp_path / "run", lock)
    deps = _deps()
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    trial = runner.store.trial(1)
    assert trial["session_fingerprint"] == "abc123"
    assert trial["campaign_fingerprint"] == "camp789"


@pytest.mark.asyncio
async def test_finalize_trial_omits_campaign_fingerprint_for_a_smoke_lock(tmp_path):
    """A non-counted (--ungated-smoke) store's lock carries no
    campaign_fingerprint at all -- a committed trial under it must stay
    single-stamped (session_fingerprint only), exactly as before Task 4's
    dual-stamp change, so it can never be mistaken for counted-campaign
    evidence."""
    store = BenchmarkStore.create(tmp_path / "run", _lock())
    deps = _deps()
    runner = _runner(store, deps)

    await runner.run_trial(_spec(1, "minimal"))

    trial = runner.store.trial(1)
    assert trial["session_fingerprint"] == "abc123"
    assert "campaign_fingerprint" not in trial


@pytest.mark.asyncio
async def test_resume_rejects_copied_trial_from_another_campaign(tmp_path):
    """A trial file copied from a DIFFERENT counted campaign block --
    stamped with a session_fingerprint that matches this store's, but a
    DIFFERENT campaign_fingerprint -- must never be treated as this
    block's own completed evidence on resume."""
    lock = {**_lock(), "campaign_fingerprint": "camp-A"}
    store = BenchmarkStore.create(tmp_path / "run", lock)
    payload = _committed_trial_payload(1)
    payload["campaign_fingerprint"] = "camp-B"  # a different campaign block
    store.commit_trial(1, payload)

    deps = _deps(make_agent=lambda spec: _FinishingAgent())
    runner = _runner(store, deps)

    with pytest.raises(SessionAborted):
        await runner.run([_spec(1, "minimal"), _spec(2, "standard")])


@pytest.mark.asyncio
async def test_resume_accepts_a_trial_stamped_with_the_matching_campaign_fingerprint(tmp_path):
    lock = {**_lock(), "campaign_fingerprint": "camp-A"}
    store = BenchmarkStore.create(tmp_path / "run", lock)
    payload = _committed_trial_payload(1)
    payload["campaign_fingerprint"] = "camp-A"
    store.commit_trial(1, payload)

    make_agent_calls: list[int] = []
    deps = _deps(make_agent=lambda spec: make_agent_calls.append(spec.index) or _FinishingAgent())
    runner = _runner(store, deps)

    await runner.run([_spec(1, "minimal"), _spec(2, "standard")])  # must not raise

    assert make_agent_calls == [2]
    assert runner.store.completed_indices() == {1, 2}


@pytest.mark.asyncio
async def test_smoke_lock_never_contains_campaign_fingerprint(tmp_path, monkeypatch):
    """The --ungated-smoke CLI path must never mint a campaign_fingerprint
    at all -- counted evidence requires both fingerprints, and
    --ungated-smoke never produces the pair."""
    suite_path = _write_fixture_suite_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    captured_locks: list[dict] = []
    real_create = benchmark_runner.BenchmarkStore.create.__func__

    def _capturing_create(cls, run_dir_arg, lock):
        captured_locks.append(dict(lock))
        return real_create(cls, run_dir_arg, lock)

    monkeypatch.setattr(benchmark_runner.BenchmarkStore, "create", classmethod(_capturing_create))
    _patch_fake_connection_and_noop_runner(monkeypatch)

    args = benchmark_runner._build_arg_parser().parse_args(
        _minimal_smoke_args(suite_path, run_dir)
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 0
    assert len(captured_locks) == 1
    assert "campaign_fingerprint" not in captured_locks[0]
    assert captured_locks[0]["ungated_smoke"] is True


# ---------------------------------------------------------------------------
# E3 (external review wave E): a failed production reload must short-circuit
# reload_and_capture -- popup hygiene and canonical capture never run after
# it (their own exceptions used to mask the reload failure as
# unexpected_admission_error) -- and the except must cover more than bare
# RuntimeError so BenchmarkStateError/OSError/TimeoutError classify as the
# production-reload gate failure instead of an unclassified crash.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reload_exc",
    [
        RuntimeError("reload_position('SAVE') failed: Error: not found"),
        OSError("connection reset by peer"),
        BenchmarkStateError("port dropped mid-reload"),
        TimeoutError("reload timed out"),
        asyncio.TimeoutError(),
    ],
)
async def test_live_admission_reload_failure_short_circuits_popup_and_canonical(
    monkeypatch, reload_exc
):
    position = _position_manifest()
    monkeypatch.setattr(benchmark_runner, "GameConnection", _FakeAdmissionConnection)
    popup_spy = AsyncMock(
        side_effect=AssertionError("popup hygiene must never run after a failed reload")
    )
    canonical_spy = AsyncMock(
        side_effect=AssertionError("canonical capture must never run after a failed reload")
    )
    monkeypatch.setattr(benchmark_runner, "dismiss_blocking_popups", popup_spy)
    monkeypatch.setattr(benchmark_runner, "capture_canonical_state", canonical_spy)

    async def _failing_reload_position(connection, pos):
        raise reload_exc

    monkeypatch.setattr(benchmark_runner, "reload_position", _failing_reload_position)

    deps = benchmark_runner._build_live_admission_dependencies(
        args=_admission_args(), api_key="x"
    )

    result = await deps.reload_and_capture(position)

    assert result["reload"]["verified"] is False
    assert type(reload_exc).__name__ in result["reload"]["raw"]
    popup_spy.assert_not_called()
    canonical_spy.assert_not_called()


@pytest.mark.asyncio
async def test_live_admission_unverified_reload_short_circuits_popup_and_canonical(monkeypatch):
    """reload_position returning verified=False (no exception -- e.g. the
    F16(b) stable-open-port fallback) must equally return the failed reload
    evidence immediately, never proceeding into popup/canonical capture."""
    position = _position_manifest()
    monkeypatch.setattr(benchmark_runner, "GameConnection", _FakeAdmissionConnection)
    popup_spy = AsyncMock(
        side_effect=AssertionError("popup hygiene must never run after an unverified reload")
    )
    canonical_spy = AsyncMock(
        side_effect=AssertionError("canonical capture must never run after an unverified reload")
    )
    monkeypatch.setattr(benchmark_runner, "dismiss_blocking_popups", popup_spy)
    monkeypatch.setattr(benchmark_runner, "capture_canonical_state", canonical_spy)

    async def _unverified_reload_position(connection, pos):
        return False

    monkeypatch.setattr(benchmark_runner, "reload_position", _unverified_reload_position)

    deps = benchmark_runner._build_live_admission_dependencies(
        args=_admission_args(), api_key="x"
    )

    result = await deps.reload_and_capture(position)

    assert result["reload"]["verified"] is False
    popup_spy.assert_not_called()
    canonical_spy.assert_not_called()


# ---------------------------------------------------------------------------
# E1(b) (external review wave E): the repo-path parameters are effectively
# mandatory at the CLI boundary -- no code path may reach tuner-holder
# classification (or termination) with an empty wsl_repo/windows_repo, where
# an unidentifiable holder's empty cwd could match an empty repo path.
# ---------------------------------------------------------------------------


def test_resolve_admission_repos_derives_both_defaults_without_flags(tmp_path, monkeypatch):
    """J1 (external review wave J), standing rule (b): the operating path
    that satisfies the repo-path precondition is a --campaign invocation
    launched from a git checkout of this repo on the gaming PC, where the
    Windows companion checkout exists at game_launcher.WSL_WINDOWS_REPO --
    a no-flag invocation must then resolve BOTH repos successfully instead
    of failing at gate zero (repo_paths_not_configured). The wsl default is
    the git root of the running package (for a worktree, the worktree dir
    -- correct: the tuner-holder check compares against the checkout
    actually running); the windows default is the companion checkout root
    the codebase already knows from the launcher bootstrap path."""
    import subprocess
    from pathlib import Path

    from civ_mcp import game_launcher

    companion = tmp_path / "companion-checkout"
    companion.mkdir()
    monkeypatch.setattr(game_launcher, "WSL_WINDOWS_REPO", str(companion))

    wsl_repo, windows_repo = benchmark_runner._resolve_admission_repos(
        _admission_args(wsl_repo=None, windows_repo=None)
    )

    expected_wsl = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=str(Path(benchmark_runner.__file__).parent),
    ).stdout.strip()
    assert wsl_repo == expected_wsl
    assert windows_repo == str(companion)


def test_live_admission_dependencies_refuse_windows_repo_when_derivation_fails(
    tmp_path, monkeypatch
):
    """J1: the refusal survives for the pathological case -- no
    --windows-repo flag AND the derived companion checkout does not exist
    on this machine. The default must never point classification at a
    nonexistent repo path."""
    from civ_mcp import game_launcher

    monkeypatch.setattr(
        game_launcher, "WSL_WINDOWS_REPO", str(tmp_path / "no-such-checkout")
    )
    with pytest.raises(GateFailure) as exc_info:
        benchmark_runner._build_live_admission_dependencies(
            args=_admission_args(windows_repo=None), api_key="x"
        )
    assert exc_info.value.code == "repo_paths_not_configured"


@pytest.mark.asyncio
async def test_remediation_terminate_refuses_empty_windows_repo_before_classifying(
    tmp_path, monkeypatch, capsys
):
    def _boom_classify(**_kwargs):
        raise AssertionError("classification must never run with an empty windows_repo")

    monkeypatch.setattr(benchmark_live_evidence_module, "classify_tuner_holder", _boom_classify)
    # J1: with derived defaults, a missing --windows-repo flag only refuses
    # when derivation ALSO fails -- simulate the companion checkout being
    # absent on this machine.
    from civ_mcp import game_launcher

    monkeypatch.setattr(
        game_launcher, "WSL_WINDOWS_REPO", str(tmp_path / "no-such-checkout")
    )

    args = benchmark_runner._build_arg_parser().parse_args(
        [
            "--terminate-tuner-pid", "4242",
            "--run-id", "remediation-run",
            "--run-dir", str(tmp_path / "runs"),
            "--wsl-repo", "/wsl/repo",
        ]
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "windows-repo" in err


# ---------------------------------------------------------------------------
# E4 + E6 (external review wave E): remediation-only invocations. Evidence
# collection failures (GateFailure from classify_tuner_holder /
# collect_gpu_evidence) must produce a journaled {"ok": false} remediation
# record and a classified nonzero exit -- never a raw traceback. And a
# remediation-only invocation must never create the counted-run scaffold
# (campaign.json/schedule.json/blocks/campaign-journal.jsonl) nor be blocked
# by campaign-lock reconstruction -- while still journaling into
# admissions/ where the deferral-corroboration logic looks.
# ---------------------------------------------------------------------------


def _remediation_argv(campaign_path, run_dir, *extra):
    return [
        "--campaign", str(campaign_path),
        "--run-id", "remediation-run",
        "--run-dir", str(run_dir),
        "--wsl-repo", "/wsl/repo",
        "--windows-repo", "C:\\Users\\riz\\civ6-mcp-companion",
        *extra,
    ]


def _assert_no_counted_scaffold(root):
    assert not (root / "campaign.json").exists()
    assert not (root / "schedule.json").exists()
    assert not (root / "blocks").exists()
    assert not (root / "campaign-journal.jsonl").exists()


@pytest.mark.asyncio
async def test_remediation_classification_gate_failure_is_journaled_and_classified(
    tmp_path, monkeypatch, capsys
):
    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    def _boom_classify(**_kwargs):
        raise GateFailure(
            "tuner_holder_query_failed",
            {"query": "listen", "message": "'ss' exited 127; no such binary"},
        )

    monkeypatch.setattr(benchmark_live_evidence_module, "classify_tuner_holder", _boom_classify)

    args = benchmark_runner._build_arg_parser().parse_args(
        _remediation_argv(campaign_path, run_dir, "--terminate-tuner-pid", "4242")
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "tuner_holder_query_failed" in err or "terminate-tuner-pid refused" in err

    root = run_dir / "remediation-run"
    records = sorted((root / "admissions").glob("gemma4-26b-attempt-*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["remediation"] == "terminate_tuner_pid"
    assert record["result"]["ok"] is False
    assert record["result"]["code"] == "tuner_holder_query_failed"
    _assert_no_counted_scaffold(root)


@pytest.mark.asyncio
async def test_remediation_drain_evidence_gate_failure_is_journaled_and_classified(
    tmp_path, monkeypatch, capsys
):
    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    monkeypatch.setattr(endpoint_registry_module, "_registry", lambda: object())

    def _boom_collect(**_kwargs):
        raise GateFailure(
            "gpu_snapshot_query_failed",
            {"host": "home-llm", "query": "index,uuid", "message": "ssh dead"},
        )

    monkeypatch.setattr(benchmark_live_evidence_module, "collect_gpu_evidence", _boom_collect)

    args = benchmark_runner._build_arg_parser().parse_args(
        _remediation_argv(
            campaign_path, run_dir,
            "--drain-gpu-service", "ollama@0.service",
            "--endpoint-id", "home-gpu0",
        )
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    root = run_dir / "remediation-run"
    records = sorted((root / "admissions").glob("gemma4-26b-attempt-*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["remediation"] == "drain_gpu_service:ollama@0.service"
    assert record["result"]["ok"] is False
    assert record["result"]["code"] == "gpu_snapshot_query_failed"
    _assert_no_counted_scaffold(root)


@pytest.mark.asyncio
async def test_remediation_only_success_is_journaled_without_counted_scaffold(
    tmp_path, monkeypatch
):
    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"

    holder = benchmark_live_evidence_module.TunerHolder(
        pid=4242, start_ticks=100, cmdline="/usr/bin/civ-mcp",
        cwd="/wsl/repo", known_repo_owned=True,
    )
    monkeypatch.setattr(
        benchmark_live_evidence_module, "classify_tuner_holder", lambda **_kwargs: holder
    )
    monkeypatch.setattr(
        benchmark_live_evidence_module,
        "terminate_tuner_pid",
        lambda **_kwargs: {"ok": True, "terminated_pid": 4242, "port": 4318},
    )

    args = benchmark_runner._build_arg_parser().parse_args(
        _remediation_argv(campaign_path, run_dir, "--terminate-tuner-pid", "4242")
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 0
    root = run_dir / "remediation-run"
    records = sorted((root / "admissions").glob("gemma4-26b-attempt-*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["remediation"] == "terminate_tuner_pid"
    assert record["result"] == {"ok": True, "terminated_pid": 4242, "port": 4318}
    _assert_no_counted_scaffold(root)


@pytest.mark.asyncio
async def test_remediation_journals_into_existing_campaign_dir_without_lock_rebuild(
    tmp_path, monkeypatch
):
    """The recorded campaign dir is the journaling authority: even a lock
    that could NEVER be rebuilt byte-for-byte from the current checkout
    (e.g. the checkout moved since the campaign was created -- here, an
    expected_commit no current checkout would produce) must accept the
    remediation record -- and remain byte-identical afterwards. G4
    (external review wave G): the recorded lock must still pass its own
    campaign_fingerprint self-check AND match the supplied manifest
    (campaign_id + digests.schedule), so this fixture lock is genuinely
    self-consistent and manifest-bound rather than an arbitrary stub."""
    import dataclasses

    from civ_mcp.arena.benchmark_campaign import compile_campaign_schedule
    from civ_mcp.arena.benchmark_contract import load_campaign_manifest
    from civ_mcp.arena.benchmark_manifest import fingerprint as _fingerprint

    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"
    root = run_dir / "remediation-run"
    root.mkdir(parents=True)
    manifest = load_campaign_manifest(campaign_path)
    compiled_schedule = compile_campaign_schedule(manifest)
    lock_body = {
        "campaign_id": manifest.campaign_id,
        # A commit no current checkout would rebuild -- this lock can never
        # byte-match a freshly built one (E6's whole point).
        "expected_commit": "0000000000000000000000000000000000000000",
        "digests": {"schedule": _fingerprint(compiled_schedule)},
        "models": [dataclasses.asdict(model) for model in manifest.models],
    }
    lock_payload = dict(lock_body)
    lock_payload["campaign_fingerprint"] = _fingerprint(lock_body)
    recorded_lock = json.dumps(lock_payload)
    recorded_schedule = json.dumps(compiled_schedule)
    (root / "campaign.json").write_text(recorded_lock)
    (root / "schedule.json").write_text(recorded_schedule)

    holder = benchmark_live_evidence_module.TunerHolder(
        pid=4242, start_ticks=100, cmdline="/usr/bin/civ-mcp",
        cwd="/wsl/repo", known_repo_owned=True,
    )
    monkeypatch.setattr(
        benchmark_live_evidence_module, "classify_tuner_holder", lambda **_kwargs: holder
    )
    monkeypatch.setattr(
        benchmark_live_evidence_module,
        "terminate_tuner_pid",
        lambda **_kwargs: {"ok": True, "terminated_pid": 4242, "port": 4318},
    )

    args = benchmark_runner._build_arg_parser().parse_args(
        _remediation_argv(campaign_path, run_dir, "--terminate-tuner-pid", "4242")
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 0
    records = sorted((root / "admissions").glob("gemma4-26b-attempt-*.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text())["result"]["ok"] is True
    # G4: the record carries the recorded campaign's own fingerprint stamp.
    assert (
        json.loads(records[0].read_text())["campaign_fingerprint"]
        == lock_payload["campaign_fingerprint"]
    )
    # The recorded lock/schedule were never rebuilt or rewritten.
    assert (root / "campaign.json").read_text() == recorded_lock
    assert (root / "schedule.json").read_text() == recorded_schedule


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["self_fingerprint", "campaign_id", "schedule_digest"])
async def test_remediation_refuses_tampered_or_foreign_recorded_campaign_lock(
    tmp_path, monkeypatch, capsys, tamper
):
    """G4 (external review wave G): _load_remediation_journal_target used
    to trust the recorded campaign.json verbatim -- a corroboration-grade
    remediation record could be written into ANY run dir against ANY
    manifest. It must verify the recorded lock's own campaign_fingerprint
    self-check AND that the lock matches the supplied manifest
    (campaign_id + digests.schedule), refusing on mismatch with NOTHING
    written and the remediation action never run."""
    import dataclasses

    from civ_mcp.arena.benchmark_campaign import compile_campaign_schedule
    from civ_mcp.arena.benchmark_contract import load_campaign_manifest
    from civ_mcp.arena.benchmark_manifest import fingerprint as _fingerprint

    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"
    root = run_dir / "remediation-run"
    root.mkdir(parents=True)
    manifest = load_campaign_manifest(campaign_path)
    compiled_schedule = compile_campaign_schedule(manifest)
    lock_body = {
        "campaign_id": manifest.campaign_id,
        "expected_commit": "0000000000000000000000000000000000000000",
        "digests": {"schedule": _fingerprint(compiled_schedule)},
        "models": [dataclasses.asdict(model) for model in manifest.models],
    }
    if tamper == "campaign_id":
        lock_body["campaign_id"] = "some-other-campaign"
    if tamper == "schedule_digest":
        lock_body["digests"] = {"schedule": "not-the-manifests-schedule"}
    lock_payload = dict(lock_body)
    lock_payload["campaign_fingerprint"] = _fingerprint(lock_body)
    if tamper == "self_fingerprint":
        # Edit a field AFTER fingerprinting -- the classic post-freeze edit.
        lock_payload["campaign_id"] = "edited-after-freeze"
    (root / "campaign.json").write_text(json.dumps(lock_payload))
    (root / "schedule.json").write_text(json.dumps(compiled_schedule))

    def _boom_classify(**_kwargs):
        raise AssertionError("remediation must not run when the journal target is refused")

    monkeypatch.setattr(benchmark_live_evidence_module, "classify_tuner_holder", _boom_classify)

    args = benchmark_runner._build_arg_parser().parse_args(
        _remediation_argv(campaign_path, run_dir, "--terminate-tuner-pid", "4242")
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "refus" in err.lower()
    assert not (root / "admissions").exists() or not list((root / "admissions").iterdir())


@pytest.mark.asyncio
async def test_remediation_refuses_edited_on_disk_schedule(tmp_path, monkeypatch, capsys):
    """J4 (external review wave J): the remediation-only path builds its
    CampaignStore via the bare constructor, which -- unlike
    CampaignStore._open_or_create -- verifies nothing against the on-disk
    schedule.json bytes. An edited schedule.json (lock left intact and
    manifest-consistent) must be refused with NOTHING written: the store's
    schedule feeds select_next_incomplete_block/block_is_complete, whose
    H1(b) trust argument assumes a digest-verified schedule."""
    import dataclasses

    from civ_mcp.arena.benchmark_campaign import compile_campaign_schedule
    from civ_mcp.arena.benchmark_contract import load_campaign_manifest
    from civ_mcp.arena.benchmark_manifest import fingerprint as _fingerprint

    campaign_path = _write_fixture_campaign_and_position(tmp_path)
    run_dir = tmp_path / "runs"
    root = run_dir / "remediation-run"
    root.mkdir(parents=True)
    manifest = load_campaign_manifest(campaign_path)
    compiled_schedule = compile_campaign_schedule(manifest)
    lock_body = {
        "campaign_id": manifest.campaign_id,
        "expected_commit": "0000000000000000000000000000000000000000",
        "digests": {"schedule": _fingerprint(compiled_schedule)},
        "models": [dataclasses.asdict(model) for model in manifest.models],
    }
    lock_payload = dict(lock_body)
    lock_payload["campaign_fingerprint"] = _fingerprint(lock_body)
    (root / "campaign.json").write_text(json.dumps(lock_payload))
    # The on-disk schedule is EDITED after the lock froze its digest.
    tampered_schedule = dict(compiled_schedule)
    tampered_schedule["blocks"] = dict(tampered_schedule["blocks"])
    tampered_schedule["blocks"]["forged-extra-block"] = {"trials": []}
    (root / "schedule.json").write_text(json.dumps(tampered_schedule))

    def _boom_classify(**_kwargs):
        raise AssertionError("remediation must not run against a tampered schedule")

    monkeypatch.setattr(benchmark_live_evidence_module, "classify_tuner_holder", _boom_classify)

    args = benchmark_runner._build_arg_parser().parse_args(
        _remediation_argv(campaign_path, run_dir, "--terminate-tuner-pid", "4242")
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "schedule" in err
    assert not (root / "admissions").exists() or not list((root / "admissions").iterdir())


@pytest.mark.asyncio
async def test_remediation_refuses_when_campaign_journal_target_unavailable(
    tmp_path, monkeypatch, capsys
):
    """--campaign names a manifest that cannot be loaded: a campaign-linked
    remediation must refuse up front (its journal record is what later
    corroborates a deferral) rather than run unjournaled."""

    def _boom_classify(**_kwargs):
        raise AssertionError("remediation must not run when the journal target is unavailable")

    monkeypatch.setattr(benchmark_live_evidence_module, "classify_tuner_holder", _boom_classify)

    args = benchmark_runner._build_arg_parser().parse_args(
        _remediation_argv(tmp_path / "missing.yaml", tmp_path / "runs", "--terminate-tuner-pid", "4242")
    )

    exit_code = await benchmark_runner._run_async(args)

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "journal" in err
