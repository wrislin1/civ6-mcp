# src/civ_mcp/arena/benchmark_backend.py
"""Pure helpers for the controlled-position benchmark: episode wall-clock
sizing and pre-flight probes over an injected backend. No network of their
own -- everything here drives `backend.chat(...)`, so tests exercise these
with fake backends.
"""
from __future__ import annotations
import asyncio
import math
import time
from dataclasses import dataclass, field, replace


def nearest_rank_p95(values: list[float]) -> float:
    """Nearest-rank p95 of `values`. Shared by `episode_wall_seconds` and the
    benchmark admission gates (`benchmark_gates.admit_model_block`) so both
    agree on the exact same definition of p95 rather than maintaining two
    copies of the same one-line formula."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def episode_wall_seconds(*, max_steps: int, latencies_s: list[float]) -> int:
    """Nearest-rank p95 of observed per-step latencies, scaled by max_steps
    with 1.5x headroom, floored at 300s (5 minutes) so a fast-but-small probe
    sample never produces an unrealistically tight episode budget.

    An empty `latencies_s` means the warm-latency probe produced no evidence
    at all (e.g. every `probe_backend` call errored because the backend is
    down) -- this is fail-closed by design: we refuse to guess a 300s floor
    as if evidence existed, and raise instead so the admission gate refuses
    the model block rather than silently proceeding."""
    if not latencies_s:
        raise ValueError(
            "episode_wall_seconds: latencies_s is empty -- no probe evidence "
            "to size the episode wall from; refusing to guess a floor value"
        )
    p95 = nearest_rank_p95(latencies_s)
    return max(300, math.ceil(max_steps * p95 * 1.5))


@dataclass(frozen=True)
class BackendProbe:
    """Result of exercising a benchmark backend with `samples` exact-sampling,
    full-schema calls before counting any trial against it."""
    samples: int
    model: str | None
    model_confirmed: bool
    seed_honored: bool
    latencies_s: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HealthProbe:
    """Structured verdict from a single, independently-bounded identity
    canary -- used to fail fast on a wedged or misrouted backend without
    waiting out the backend's own (much larger) per-step request timeout."""
    healthy: bool
    model: str | None
    latency_s: float | None
    error: str | None = None


def _reply_signature(reply) -> str:
    """A comparable fingerprint for a Reply: same text + same tool calls."""
    tool_sig = tuple(
        (tc.get("name"), tc.get("arguments"))
        for tc in (getattr(reply, "tool_calls", None) or [])
    )
    return repr((getattr(reply, "text", None), tool_sig))


async def probe_backend(backend, messages, tools, samples: int = 10) -> BackendProbe:
    """Exercise `backend` with `samples` identical, exact-sampling, full
    tool-schema calls (the same `messages`/`tools` every time).

    Verifies:
    - reported model identity: every reply's `.model` matches `backend.model`
    - repeated-seed determinism: all `samples` outputs at the locked config
      agree with each other
    - differing-seed sensitivity: one extra call at `seed + 1` (via a
      temporary swap of `backend.sampling`, always restored) produces a
      DIFFERENT output -- proof the backend actually threads the seed through
      rather than silently ignoring it

    `seed_honored` is only True when both hold. A backend with no configured
    seed can't be verified this way and is reported as not honored.

    Returns the raw per-call latency list (the verification call's latency is
    not included) plus any errors encountered, without raising -- this is a
    pre-flight diagnostic, not a step in a scored episode.
    """
    errors: list[str] = []
    latencies: list[float] = []
    outputs: list[str] = []
    models: list[str | None] = []

    for _ in range(samples):
        start = time.monotonic()
        try:
            reply = await backend.chat(messages, tools)
        except Exception as exc:
            errors.append(str(exc))
            continue
        latencies.append(time.monotonic() - start)
        outputs.append(_reply_signature(reply))
        models.append(getattr(reply, "model", None))

    model_confirmed = bool(models) and all(m == backend.model for m in models)
    repeated_consistent = bool(outputs) and len(set(outputs)) == 1

    sampling = getattr(backend, "sampling", None)
    locked_seed = getattr(sampling, "seed", None)

    seed_honored = False
    if repeated_consistent and sampling is not None and locked_seed is not None:
        varied = replace(sampling, seed=locked_seed + 1)
        backend.sampling = varied
        try:
            varied_reply = await backend.chat(messages, tools)
            seed_honored = _reply_signature(varied_reply) != outputs[0]
        except Exception as exc:
            errors.append(str(exc))
            seed_honored = False
        finally:
            backend.sampling = sampling

    return BackendProbe(
        samples=samples,
        model=backend.model if models else None,
        model_confirmed=model_confirmed,
        seed_honored=seed_honored,
        latencies_s=latencies,
        errors=errors,
    )


async def probe_health(backend, expected_model: str, timeout_s: float) -> HealthProbe:
    """Send one identity canary, bounded by `timeout_s` independently of
    whatever request timeout the backend applies internally -- so a wedged
    benchmark backend is flagged unhealthy in seconds, not minutes."""
    start = time.monotonic()
    try:
        reply = await asyncio.wait_for(
            backend.chat([{"role": "user", "content": "Reply with only your model name."}], []),
            timeout=timeout_s,
        )
    except Exception as exc:
        return HealthProbe(healthy=False, model=None, latency_s=None, error=str(exc))
    latency = time.monotonic() - start
    reported = getattr(reply, "model", None)
    healthy = reported == expected_model
    error = None if healthy else f"model mismatch: expected {expected_model!r}, got {reported!r}"
    return HealthProbe(healthy=healthy, model=reported, latency_s=latency, error=error)
