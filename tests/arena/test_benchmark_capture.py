"""Tests for the bounded v2 capture wrapper and its telemetry.

Three deadline/cancel cases are kept distinct:
- external (caller) cancellation propagates as `CancelledError` and only
  latches `cancelled_capture`;
- the wrapper's own `limit_s` deadline is an infrastructure `CaptureFailure`;
- a CPU-bound overrun (returns after `limit_s` without awaiting) is also a
  `CaptureFailure`.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import civ_mcp.arena.benchmark_capture as benchmark_capture
from civ_mcp.arena import benchmark_contract_v2 as c2
from civ_mcp.arena.benchmark_capture import CaptureFailure, CaptureTelemetry, capture_bounded
from civ_mcp.arena.benchmark_state import BenchmarkStateError
from civ_mcp.arena.benchmark_state_v2 import digest_state_v2
from civ_mcp.connection import LuaError


def v2_state(turn: int = 1) -> dict:
    return {
        "wire_version": "2.0.0", "civ_type": "CIVILIZATION_X", "seed": 7, "turn": turn,
        "active_player": 0, "player_id": 0, "gold": 10, "faith": 0,
        "units": [], "targets": [], "cities": [], "tiles": [], "resources": [],
        "coverage": {"area": [], "tracked_targets": []}, "row_counts": {},
    }


@pytest.mark.asyncio
async def test_caller_cancel_is_not_capture_failure():
    entered = asyncio.Event()
    async def blocked_read():
        entered.set()
        await asyncio.Event().wait()
    telemetry = CaptureTelemetry()
    task = asyncio.create_task(capture_bounded(
        blocked_read, phase="tool_before", telemetry=telemetry))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert telemetry.cancelled_capture is not None
    assert telemetry.records[-1]["complete"] is False


async def test_external_cancel_record_names_the_active_phase():
    entered = asyncio.Event()

    async def blocked_read():
        entered.set()
        await asyncio.Event().wait()

    telemetry = CaptureTelemetry()
    task = asyncio.create_task(capture_bounded(blocked_read, phase="initial", telemetry=telemetry))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert telemetry.cancelled_capture is telemetry.records[-1]
    assert telemetry.cancelled_capture["phase"] == "initial"
    assert telemetry.cancelled_capture["cancelled"] is True


async def test_local_capture_timeout_is_capture_failure_not_cancellation():
    async def blocked_read():
        await asyncio.Event().wait()

    telemetry = CaptureTelemetry()
    with pytest.raises(CaptureFailure) as info:
        await capture_bounded(blocked_read, phase="tool_after", telemetry=telemetry, limit_s=0.005)
    assert isinstance(info.value, BenchmarkStateError)
    assert isinstance(info.value.__cause__, TimeoutError)
    record = telemetry.records[-1]
    assert record["complete"] is False
    assert record["cancelled"] is False
    assert record["phase"] == "tool_after"
    assert telemetry.cancelled_capture is None
    # The local deadline consumed its own cancellation: the task is clean.
    assert asyncio.current_task().cancelling() == 0


async def test_transport_timeout_error_from_read_is_capture_failure():
    async def timed_out_read():
        raise TimeoutError("transport deadline")

    telemetry = CaptureTelemetry()
    with pytest.raises(CaptureFailure):
        await capture_bounded(timed_out_read, phase="final", telemetry=telemetry)
    assert telemetry.records[-1]["cancelled"] is False
    assert telemetry.cancelled_capture is None


async def test_cpu_overrun_without_awaiting_is_capture_failure(monkeypatch):
    ticks = iter([100.0, 102.5, 102.5])
    monkeypatch.setattr(benchmark_capture, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    async def cpu_bound_read():
        return v2_state()  # never awaits, so asyncio.timeout cannot interrupt it

    telemetry = CaptureTelemetry()
    with pytest.raises(CaptureFailure, match="wall limit"):
        await capture_bounded(cpu_bound_read, phase="tool_before", telemetry=telemetry)
    record = telemetry.records[-1]
    assert record["complete"] is False
    assert record["cancelled"] is False
    assert record["duration_s"] == pytest.approx(2.5)
    assert telemetry.cancelled_capture is None


async def test_success_returns_state_v2_digest_and_records_io():
    telemetry = CaptureTelemetry()
    state = v2_state(turn=9)

    async def read():
        telemetry.current_io["pre_drain_s"] = 0.01
        telemetry.current_io["post_drain_s"] = 0.02
        telemetry.current_io["lua_executions"] = 1
        return state

    got_state, digest = await capture_bounded(read, phase="tool_before", telemetry=telemetry)
    assert got_state is state
    assert digest == digest_state_v2(state)
    record = telemetry.records[-1]
    assert record["complete"] is True
    assert record["cancelled"] is False
    assert record["io"] == {"pre_drain_s": 0.01, "post_drain_s": 0.02, "lua_executions": 1}
    # The record keeps a copy: later captures cannot rewrite it.
    telemetry.current_io["lua_executions"] = 99
    assert record["io"]["lua_executions"] == 1


async def test_each_capture_gets_a_fresh_io_dict():
    telemetry = CaptureTelemetry()
    seen = []

    async def read():
        seen.append(telemetry.current_io)
        telemetry.current_io["lua_executions"] = 1
        return v2_state()

    await capture_bounded(read, phase="tool_before", telemetry=telemetry)
    await capture_bounded(read, phase="tool_after", telemetry=telemetry)
    assert seen[0] is not seen[1]
    assert [r["phase"] for r in telemetry.records] == ["tool_before", "tool_after"]


async def test_lua_error_is_not_converted():
    async def lua_defect():
        raise LuaError("ERR: attempt to index nil")

    telemetry = CaptureTelemetry()
    with pytest.raises(LuaError):
        await capture_bounded(lua_defect, phase="tool_before", telemetry=telemetry)
    assert telemetry.records[-1]["complete"] is False
    assert telemetry.records[-1]["cancelled"] is False


def test_reset_clears_latch_records_and_io():
    telemetry = CaptureTelemetry()
    telemetry.records.append({"phase": "x"})
    telemetry.cancelled_capture = {"phase": "x"}
    telemetry.current_io = {"pre_drain_s": 1.0}
    telemetry.reset()
    assert telemetry.records == []
    assert telemetry.cancelled_capture is None
    assert telemetry.current_io == {}


def _rec(phase, duration, *, pre=0.0, post=0.0, lua=1, complete=True):
    io = {}
    if pre is not None:
        io["pre_drain_s"] = pre
    if post is not None:
        io["post_drain_s"] = post
    if lua is not None:
        io["lua_executions"] = lua
    return {"phase": phase, "duration_s": duration, "complete": complete,
            "cancelled": False, "io": io}


def test_summary_statistics_and_episode_share():
    telemetry = CaptureTelemetry()
    durations = [0.1 * n for n in range(1, 21)]  # 0.1 .. 2.0
    phases = ["initial"] + ["tool_before", "tool_after"] * 9 + ["final"]
    for phase, duration in zip(phases, durations):
        telemetry.records.append(_rec(phase, duration, pre=0.01, post=0.02))
    summary = telemetry.summary(episode_wall_s=20.0)
    assert summary["count"] == 20
    assert summary["total_s"] == pytest.approx(21.0)
    assert summary["mean_s"] == pytest.approx(1.05)
    assert summary["p95_s"] == pytest.approx(1.9)  # nearest rank ceil(0.95*20)=19
    assert summary["max_s"] == pytest.approx(2.0)
    assert summary["pre_drain_total_s"] == pytest.approx(0.2)
    assert summary["post_drain_total_s"] == pytest.approx(0.4)
    assert summary["non_drain_residual_s"] == pytest.approx(21.0 - 0.6)
    in_episode = sum(durations[1:19])
    assert summary["in_episode_total_s"] == pytest.approx(in_episode)
    assert summary["in_episode_share"] == pytest.approx(in_episode / 20.0)
    assert summary["lua_executions_total"] == 20
    assert summary["all_single_execution"] is True
    assert summary["unavailable"] == {}


def test_summary_reports_missing_io_as_unavailable_not_zero():
    telemetry = CaptureTelemetry()
    telemetry.records.append(_rec("tool_before", 0.5, pre=0.1, post=0.1))
    telemetry.records.append(_rec("tool_after", 0.5, pre=None, post=0.1, lua=None))
    summary = telemetry.summary(episode_wall_s=10.0)
    assert summary["pre_drain_total_s"] is None
    assert summary["post_drain_total_s"] == pytest.approx(0.2)
    assert summary["non_drain_residual_s"] is None
    assert summary["lua_executions_total"] is None
    assert summary["all_single_execution"] is False
    assert summary["unavailable"] == {
        "pre_drain_s": [{"index": 1, "phase": "tool_after"}],
        "lua_executions": [{"index": 1, "phase": "tool_after"}],
    }


def test_summary_flags_a_successful_capture_with_two_executions():
    telemetry = CaptureTelemetry()
    telemetry.records.append(_rec("tool_before", 0.5, lua=2))
    telemetry.records.append(_rec("tool_after", 0.5, lua=0, complete=False))
    summary = telemetry.summary(episode_wall_s=10.0)
    assert summary["lua_executions_total"] == 2
    assert summary["all_single_execution"] is False


def test_summary_of_no_records_is_empty_not_zero():
    summary = CaptureTelemetry().summary(episode_wall_s=10.0)
    assert summary["count"] == 0
    assert summary["mean_s"] is None
    assert summary["p95_s"] is None
    assert summary["max_s"] is None
    assert summary["total_s"] == 0.0


def test_capture_module_is_a_fingerprint_dependency():
    assert "src/civ_mcp/arena/benchmark_capture.py" in c2.FINGERPRINT_DEPENDENCIES
