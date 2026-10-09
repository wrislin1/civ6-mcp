"""Bounded v2 state capture with cancellation-honest telemetry.

`capture_bounded` is the hard 2.0-second wall around one v2 capture (the
transport's own receive timeout only bounds its read loop and returns partial
lines). Three outcomes are kept distinct:

- the wrapper's own deadline (or a `TimeoutError` from the read, or a capture
  that returns after `limit_s` without awaiting) is `CaptureFailure`, an
  infrastructure failure;
- an enclosing cancellation (caller shutdown, or the agent's episode
  deadline) propagates unchanged as `CancelledError`; the wrapper only
  records which capture was active in `CaptureTelemetry.cancelled_capture`.
  Only the agent can tell whether that cancellation was its own deadline;
- anything else the read raises (including an untyped Lua defect) propagates
  unchanged.

Telemetry is recorded in `finally`, so every capture -- complete, failed or
cancelled -- leaves exactly one record.
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Awaitable, Callable

from civ_mcp.arena.benchmark_state import BenchmarkStateError
from civ_mcp.arena.benchmark_state_v2 import digest_state_v2

__all__ = ["CaptureFailure", "CaptureTelemetry", "capture_bounded", "IN_EPISODE_PHASES"]

# Captures inside the agent's episode wall; initial/final are outside it.
IN_EPISODE_PHASES = frozenset({"tool_before", "tool_after"})


class CaptureFailure(BenchmarkStateError):
    """A capture missed its deadline: an infrastructure failure, never a
    model outcome and never a caller cancellation."""


class CaptureTelemetry:
    """Per-attempt capture records plus the cancelled-capture latch.

    `current_io` is replaced with a fresh dict at the start of every capture;
    a v2 capture closure passes it as `capture_state_v2(..., io_timing=...)`.
    """

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.current_io: dict[str, Any] = {}
        self.cancelled_capture: dict[str, Any] | None = None

    def reset(self) -> None:
        """Forget every record and the latch (call before each episode/attempt)."""
        self.records = []
        self.current_io = {}
        self.cancelled_capture = None

    def summary(self, *, episode_wall_s: float) -> dict[str, Any]:
        """Capture-cost summary.

        `non_drain_residual_s` is total duration minus measured pre/post
        drains; it includes query, transport, parse and digest time. Drain
        and Lua-execution totals are `None` (never zero-filled) when any
        record lacks that field; `unavailable` names those records.
        `in_episode_*` counts only tool-before/after captures.
        `all_single_execution` requires every complete record to show
        exactly one Lua execution.
        """
        durations = [float(r["duration_s"]) for r in self.records]
        count = len(durations)
        total = sum(durations)
        ordered = sorted(durations)
        p95 = ordered[math.ceil(0.95 * count) - 1] if count else None

        unavailable: dict[str, list[dict[str, Any]]] = {}

        def field_total(key: str) -> float | None:
            missing = [
                {"index": i, "phase": r.get("phase")}
                for i, r in enumerate(self.records)
                if key not in r.get("io", {})
            ]
            if missing:
                unavailable[key] = missing
                return None
            return sum(r["io"][key] for r in self.records)

        pre = field_total("pre_drain_s")
        post = field_total("post_drain_s")
        lua_total = field_total("lua_executions")
        residual = None if pre is None or post is None else total - pre - post

        in_episode = sum(
            float(r["duration_s"]) for r in self.records if r.get("phase") in IN_EPISODE_PHASES
        )
        share = in_episode / episode_wall_s if episode_wall_s > 0 else None
        single = all(
            r.get("io", {}).get("lua_executions") == 1
            for r in self.records
            if r.get("complete")
        )
        return {
            "count": count,
            "mean_s": total / count if count else None,
            "p95_s": p95,
            "max_s": ordered[-1] if count else None,
            "total_s": total,
            "pre_drain_total_s": pre,
            "post_drain_total_s": post,
            "non_drain_residual_s": residual,
            "in_episode_total_s": in_episode,
            "in_episode_share": share,
            "lua_executions_total": lua_total,
            "all_single_execution": single,
            "unavailable": unavailable,
        }


async def capture_bounded(
    read: Callable[[], Awaitable[dict[str, Any]]],
    *,
    phase: str,
    telemetry: CaptureTelemetry,
    limit_s: float = 2.0,
) -> tuple[dict[str, Any], str]:
    """Run `read()` under a hard `limit_s` wall; return `(state, v2 digest)`."""
    started = time.monotonic()
    complete = False
    telemetry.current_io = {}
    try:
        async with asyncio.timeout(limit_s):
            state = await read()
            digest = digest_state_v2(state)
        if time.monotonic() - started > limit_s:
            raise CaptureFailure("capture exceeded wall limit")
        complete = True
        return state, digest
    except TimeoutError as exc:
        # A local capture/transport timeout is not external cancellation.
        raise CaptureFailure("capture or transport exceeded its deadline") from exc
    finally:
        task = asyncio.current_task()
        cancelled = not complete and task is not None and task.cancelling() > 0
        record = {"phase": phase, "duration_s": time.monotonic() - started,
                  "complete": complete, "cancelled": cancelled,
                  "io": dict(telemetry.current_io)}
        telemetry.records.append(record)
        if cancelled:
            telemetry.cancelled_capture = record
