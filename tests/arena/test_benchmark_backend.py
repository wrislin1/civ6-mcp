import asyncio

import pytest

from civ_mcp.arena.backends import Reply, SamplingConfig
from civ_mcp.arena.benchmark_backend import (
    BackendProbe,
    HealthProbe,
    episode_wall_seconds,
    probe_backend,
    probe_health,
)


def test_episode_wall_uses_p95_and_five_minute_floor():
    assert episode_wall_seconds(max_steps=15, latencies_s=[20.0] * 9 + [30.0]) == 675
    assert episode_wall_seconds(max_steps=15, latencies_s=[2.0] * 10) == 300


def test_episode_wall_seconds_fails_closed_on_no_evidence():
    """An empty latency list (e.g. every probe_backend call errored) must not
    silently fall back to the 300s floor as if evidence existed -- that would
    let a fully-down backend sail through the admission gate. Raise instead."""
    with pytest.raises(ValueError):
        episode_wall_seconds(max_steps=15, latencies_s=[])


# ---------------------------------------------------------------------------
# probe_backend
# ---------------------------------------------------------------------------

class _FakeExactBackend:
    """Deterministic given (seed, messages) -- like a real exact-sampling
    backend that honors its locked seed."""

    def __init__(self, model="gemma4-26b", sampling=None):
        self.model = model
        self.sampling = sampling or SamplingConfig(temperature=0.0, top_p=1.0, seed=41, max_tokens=64)
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        seed = self.sampling.seed
        return Reply(text=f"seed={seed}", tool_calls=[], prompt_tokens=1, completion_tokens=1,
                     model=self.model)


class _FakeIgnoresSeedBackend(_FakeExactBackend):
    """Misbehaving backend: always returns the same text regardless of the
    configured seed -- i.e. it does NOT honor the seed."""

    async def chat(self, messages, tools):
        self.calls += 1
        return Reply(text="always-the-same", tool_calls=[], prompt_tokens=1, completion_tokens=1,
                     model=self.model)


class _FakeWrongModelBackend(_FakeExactBackend):
    async def chat(self, messages, tools):
        self.calls += 1
        return Reply(text=f"seed={self.sampling.seed}", tool_calls=[], prompt_tokens=1,
                     completion_tokens=1, model="some-other-model")


class _FakeNonDeterministicGreedyBackend(_FakeExactBackend):
    """G7 repro: claims temperature=0 (greedy) but its repeated calls at
    the SAME locked config disagree with each other -- a genuinely
    non-deterministic backend, not honoring anything, masquerading as
    "not applicable" under the old unconditional temp==0 verdict."""

    async def chat(self, messages, tools):
        self.calls += 1
        return Reply(text=f"call={self.calls}", tool_calls=[], prompt_tokens=1,
                     completion_tokens=1, model=self.model)


@pytest.mark.asyncio
async def test_probe_backend_confirms_model_and_honored_seed():
    backend = _FakeExactBackend(
        sampling=SamplingConfig(temperature=0.2, top_p=1.0, seed=41, max_tokens=64)
    )
    probe = await probe_backend(backend, [{"role": "user", "content": "act"}], [], samples=10)
    assert isinstance(probe, BackendProbe)
    assert probe.samples == 10
    assert probe.model_confirmed is True
    assert probe.seed_honored is True
    assert probe.seed_verdict == "honored"
    assert len(probe.latencies_s) == 10
    assert all(isinstance(x, float) for x in probe.latencies_s)
    assert probe.errors == []
    # the differing-seed verification call must not leak a mutated config
    assert backend.sampling.seed == 41


@pytest.mark.asyncio
async def test_probe_backend_records_seed_verdict_not_applicable_at_temperature_zero():
    """F15 ruling: at temperature == 0 (greedy decoding), seed honoring is
    unobservable and irrelevant -- varying the seed cannot change a greedy
    backend's real output, so the differing-seed probe would always
    (mis)report "not honored" for a perfectly healthy backend.
    probe_backend must record seed_verdict "not_applicable_greedy" instead
    of running that check at all. Repeated-consistency (still required) is
    unaffected -- this fake IS internally consistent across the `samples`
    identical calls."""
    backend = _FakeExactBackend()  # default sampling: temperature=0.0
    probe = await probe_backend(backend, [{"role": "user", "content": "act"}], [], samples=10)
    assert probe.seed_verdict == "not_applicable_greedy"
    # G7: repeated_consistent must be recorded on the probe, not silently
    # computed-and-discarded -- admission never sees it otherwise.
    assert probe.repeated_consistent is True
    # the verification call's config swap (if any) must never leak
    assert backend.sampling.seed == 41


@pytest.mark.asyncio
async def test_probe_backend_temp_zero_inconsistent_output_is_not_honored():
    """G7 repro: at temperature==0 the verdict was unconditionally
    "not_applicable_greedy", even when the backend's own repeated calls at
    the same config disagree with each other -- a non-deterministic
    "greedy" backend must fail admission (seed_verdict "not_honored"),
    not slip through as "not applicable"."""
    backend = _FakeNonDeterministicGreedyBackend(
        sampling=SamplingConfig(temperature=0.0, top_p=1.0, seed=41, max_tokens=64)
    )
    probe = await probe_backend(backend, [{"role": "user", "content": "act"}], [], samples=10)
    assert probe.repeated_consistent is False
    assert probe.seed_verdict == "not_honored"
    assert probe.seed_honored is False


def test_backend_probe_repeated_consistent_defaults_false_for_old_constructors():
    """Pre-G7 callers/tests construct BackendProbe without repeated_consistent
    -- it must default to False rather than raising a TypeError."""
    probe = BackendProbe(
        samples=10, model="qwen3.6-27b", model_confirmed=True,
        seed_honored=True, latencies_s=[1.0] * 10, errors=[],
    )
    assert probe.repeated_consistent is False


@pytest.mark.asyncio
async def test_probe_backend_detects_unhonored_seed():
    backend = _FakeIgnoresSeedBackend(
        sampling=SamplingConfig(temperature=0.2, top_p=1.0, seed=41, max_tokens=64)
    )
    probe = await probe_backend(backend, [{"role": "user", "content": "act"}], [], samples=5)
    assert probe.seed_honored is False
    assert probe.seed_verdict == "not_honored"


@pytest.mark.asyncio
async def test_probe_backend_detects_model_mismatch():
    backend = _FakeWrongModelBackend()
    probe = await probe_backend(backend, [{"role": "user", "content": "act"}], [], samples=3)
    assert probe.model_confirmed is False


@pytest.mark.asyncio
async def test_probe_backend_records_errors_without_raising():
    class _Flaky(_FakeExactBackend):
        async def chat(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return await super().chat(messages, tools)

    backend = _Flaky()
    probe = await probe_backend(backend, [{"role": "user", "content": "act"}], [], samples=3)
    assert probe.errors == ["boom"]
    assert len(probe.latencies_s) == 2


# ---------------------------------------------------------------------------
# probe_health
# ---------------------------------------------------------------------------

class _FakeHealthyBackend:
    model = "gemma4-26b"

    async def chat(self, messages, tools):
        return Reply(text="ok", tool_calls=[], model="gemma4-26b")


class _FakeSlowBackend:
    model = "gemma4-26b"

    async def chat(self, messages, tools):
        await asyncio.sleep(10)
        return Reply(text="too slow", tool_calls=[], model="gemma4-26b")


class _FakeMismatchedBackend:
    model = "gemma4-26b"

    async def chat(self, messages, tools):
        return Reply(text="ok", tool_calls=[], model="wrong-model")


@pytest.mark.asyncio
async def test_probe_health_reports_healthy_on_matching_identity():
    verdict = await probe_health(_FakeHealthyBackend(), expected_model="gemma4-26b", timeout_s=5.0)
    assert isinstance(verdict, HealthProbe)
    assert verdict.healthy is True
    assert verdict.model == "gemma4-26b"
    assert verdict.error is None
    assert verdict.latency_s is not None


@pytest.mark.asyncio
async def test_probe_health_times_out_independently_of_backend_timeout():
    verdict = await probe_health(_FakeSlowBackend(), expected_model="gemma4-26b", timeout_s=0.05)
    assert verdict.healthy is False
    assert verdict.error is not None


@pytest.mark.asyncio
async def test_probe_health_flags_model_mismatch():
    verdict = await probe_health(_FakeMismatchedBackend(), expected_model="gemma4-26b", timeout_s=5.0)
    assert verdict.healthy is False
    assert verdict.model == "wrong-model"
