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


# ---------------------------------------------------------------------------
# Task 4: extract a resolved-block handoff and structurally exclude smoke
# evidence from counted (campaign) provenance.
# ---------------------------------------------------------------------------


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
