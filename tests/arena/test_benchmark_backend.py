import asyncio
import json

import pytest

from civ_mcp.arena import registry as arena_registry
from civ_mcp.arena.backends import Reply, SamplingConfig
from civ_mcp.arena.benchmark_backend import (
    BackendProbe,
    HealthProbe,
    ToolCanaryEvidence,
    episode_wall_seconds,
    probe_backend,
    probe_health,
    probe_tool_capability,
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
    # G2: a generic exception is capability-side evidence, not operator-side.
    assert probe.error_kinds == ("model",)


# ---------------------------------------------------------------------------
# G2 (external review wave G, Ruling H): exception classification
# ---------------------------------------------------------------------------


def _openai_status_error(cls, status_code):
    import httpx
    import openai as _openai  # noqa: F401 - clarity: exceptions come from the real SDK

    request = httpx.Request("POST", "http://x/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return cls("probe error", response=response, body=None)


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: __import__("openai").AuthenticationError(
            "bad key",
            response=__import__("httpx").Response(
                401, request=__import__("httpx").Request("POST", "http://x/v1")
            ),
            body=None,
        ),
        lambda: _openai_status_error(__import__("openai").PermissionDeniedError, 403),
    ],
)
def test_classify_backend_exception_auth_shapes(make_exc):
    """G2: 401/403 shapes from the actual client library in use (openai
    SDK over httpx -- see backends.OpenAICompatBackend) classify as
    "auth" -- an operator credential problem, never capability evidence."""
    from civ_mcp.arena.benchmark_backend import classify_backend_exception

    assert classify_backend_exception(make_exc()) == "auth"


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: __import__("openai").APIConnectionError(
            request=__import__("httpx").Request("POST", "http://x/v1")
        ),
        lambda: __import__("openai").APITimeoutError(
            request=__import__("httpx").Request("POST", "http://x/v1")
        ),
        lambda: _openai_status_error(__import__("openai").RateLimitError, 429),
        lambda: __import__("httpx").ConnectError("connection refused"),
        lambda: __import__("httpx").ReadTimeout("read timed out"),
        # H2 (external review wave H, Ruling I): ALL HTTP 5xx are
        # environment evidence -- a llama-swap cold-start 503, a gateway
        # 500/502/504 -- never model capability. openai.InternalServerError
        # is the SDK's typed exception for every 5xx.
        lambda: _openai_status_error(__import__("openai").InternalServerError, 500),
        lambda: _openai_status_error(__import__("openai").InternalServerError, 503),
        lambda: _openai_status_error(__import__("openai").InternalServerError, 502),
    ],
)
def test_classify_backend_exception_transport_shapes(make_exc):
    """G2 + Ruling I (wave H): connection/timeout/rate-limit shapes AND
    every HTTP 5xx (openai SDK + raw httpx transport errors) classify as
    "transport" -- a down gateway, wrong endpoint URL, 429 storm, or
    cold-start 503, never capability evidence."""
    from civ_mcp.arena.benchmark_backend import classify_backend_exception

    assert classify_backend_exception(make_exc()) == "transport"


def test_classify_backend_exception_bare_5xx_status_is_transport():
    """Ruling I (wave H): the status-code fallback covers any
    status-carrying exception shape at 5xx, not just the openai SDK's
    typed InternalServerError."""
    from civ_mcp.arena.benchmark_backend import classify_backend_exception

    class _StatusCarrier(Exception):
        status_code = 502

    assert classify_backend_exception(_StatusCarrier("bad gateway")) == "transport"


@pytest.mark.parametrize(
    ("status", "expected_kind"),
    [(401, "auth"), (403, "auth"), (429, "transport"), (500, "transport"), (503, "transport")],
)
def test_classify_backend_exception_reads_httpx_response_status(status, expected_kind):
    """H5 (external review wave H): the status-code fallback must also read
    the httpx convention `exc.response.status_code` -- a raw
    httpx.HTTPStatusError carries no `status_code` attribute of its own."""
    import httpx

    from civ_mcp.arena.benchmark_backend import classify_backend_exception

    request = httpx.Request("POST", "http://x/v1/chat/completions")
    exc = httpx.HTTPStatusError(
        f"HTTP {status}", request=request, response=httpx.Response(status, request=request)
    )
    assert classify_backend_exception(exc) == expected_kind


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: RuntimeError("model emitted malformed tool JSON"),
        lambda: _openai_status_error(__import__("openai").BadRequestError, 400),
        lambda: _openai_status_error(__import__("openai").UnprocessableEntityError, 422),
    ],
)
def test_classify_backend_exception_everything_else_stays_model_evidence(make_exc):
    """G2 (updated by Ruling I, wave H): only auth, transport, and 5xx are
    carved out -- any other failure shape (a 4xx the server chose to emit,
    a bare runtime error from a reply that genuinely came back) remains
    capability-side evidence (backend_probe_errors et al.)."""
    from civ_mcp.arena.benchmark_backend import classify_backend_exception

    assert classify_backend_exception(make_exc()) == "model"


@pytest.mark.asyncio
async def test_probe_backend_classifies_auth_exceptions_in_the_funnel():
    """G2: the probe funnel records an index-aligned classification for
    every swallowed exception -- an all-401 probe carries error_kinds all
    "auth" so admit_model_block can refuse under backend_auth_error
    instead of endpoint_identity_mismatch."""
    import httpx
    import openai

    class _AuthFails(_FakeExactBackend):
        async def chat(self, messages, tools):
            self.calls += 1
            request = httpx.Request("POST", "http://x/v1/chat/completions")
            raise openai.AuthenticationError(
                "invalid api key", response=httpx.Response(401, request=request), body=None
            )

    probe = await probe_backend(_AuthFails(), [{"role": "user", "content": "act"}], [], samples=3)
    assert len(probe.errors) == 3
    assert probe.error_kinds == ("auth", "auth", "auth")
    assert probe.latencies_s == []


@pytest.mark.asyncio
async def test_probe_backend_classifies_transport_exceptions_in_the_funnel():
    import httpx

    class _Down(_FakeExactBackend):
        async def chat(self, messages, tools):
            self.calls += 1
            raise httpx.ConnectError("connection refused")

    probe = await probe_backend(_Down(), [{"role": "user", "content": "act"}], [], samples=3)
    assert len(probe.errors) == 3
    assert probe.error_kinds == ("transport", "transport", "transport")


def test_backend_probe_error_kinds_default_empty_for_old_constructors():
    """Pre-G2 callers/tests construct BackendProbe without error_kinds --
    it must default to () rather than raising a TypeError; the gate treats
    a missing kind as "model", preserving old backend_probe_errors
    behavior."""
    probe = BackendProbe(
        samples=10, model="qwen3.6-27b", model_confirmed=True,
        seed_honored=True, latencies_s=[1.0] * 10, errors=["boom"],
    )
    assert probe.error_kinds == ()


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


# ---------------------------------------------------------------------------
# probe_tool_capability
# ---------------------------------------------------------------------------

# The exact sentinel the required-argument canary asks for and must observe
# back verbatim -- x=11, never x=12 (see the counterfactual in Task 5's brief).
REQUIRED_ARGUMENT_SENTINEL = {"unit_index": 7, "x": 11, "y": 13}


def _tool_call(name: str, arguments) -> dict[str, object]:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": f"call-{name}", "name": name, "arguments": args}


class _ScriptedCanaryBackend:
    """Returns one scripted Reply per `chat()` call, in order -- mirrors
    `probe_tool_capability`'s exactly-two-calls-in-order contract (one
    finish_trial-eliciting prompt, one move_unit-eliciting prompt)."""

    def __init__(self, replies: list[Reply]):
        self._replies = list(replies)
        self.calls: list[dict[str, object]] = []

    async def chat(self, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        return self._replies.pop(0)


@pytest.mark.asyncio
async def test_tool_canary_requires_finish_trial_call():
    backend = _ScriptedCanaryBackend([
        Reply(text=None, tool_calls=[_tool_call("finish_trial", {})]),
        Reply(text=None, tool_calls=[_tool_call("move_unit", REQUIRED_ARGUMENT_SENTINEL)]),
    ])
    evidence = await probe_tool_capability(backend, arm_id="standard", tools=[])
    assert isinstance(evidence, ToolCanaryEvidence)
    assert evidence.arm_id == "standard"
    assert evidence.finish_trial_ok is True
    assert evidence.required_argument_ok is True
    assert evidence.errors == ()
    assert len(backend.calls) == 2
    # Two distinct, generic prompts -- not the same message twice.
    assert backend.calls[0]["messages"] != backend.calls[1]["messages"]


@pytest.mark.asyncio
async def test_tool_canary_requires_exact_move_unit_arguments_without_dispatching(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("probe_tool_capability must never dispatch a tool call")

    monkeypatch.setattr(arena_registry, "dispatch", _boom)

    backend = _ScriptedCanaryBackend([
        Reply(text=None, tool_calls=[_tool_call("finish_trial", {})]),
        Reply(text=None, tool_calls=[_tool_call("move_unit", {"unit_index": 7, "x": 12, "y": 13})]),
    ])
    evidence = await probe_tool_capability(backend, arm_id="standard", tools=[])
    assert evidence.required_argument_ok is False
    assert any("move_unit" in e or "argument" in e for e in evidence.errors)

    # Restoring the exact sentinel (x=11, not x=12) must flip the verdict.
    backend_ok = _ScriptedCanaryBackend([
        Reply(text=None, tool_calls=[_tool_call("finish_trial", {})]),
        Reply(text=None, tool_calls=[_tool_call("move_unit", REQUIRED_ARGUMENT_SENTINEL)]),
    ])
    evidence_ok = await probe_tool_capability(backend_ok, arm_id="standard", tools=[])
    assert evidence_ok.required_argument_ok is True


@pytest.mark.asyncio
async def test_tool_canary_rejects_text_only_and_malformed_json_replies():
    backend = _ScriptedCanaryBackend([
        Reply(text="Sure, I am done for this turn.", tool_calls=[]),
        Reply(text=None, tool_calls=[_tool_call("move_unit", "{not valid json")]),
    ])
    evidence = await probe_tool_capability(backend, arm_id="standard", tools=[])
    assert evidence.finish_trial_ok is False
    assert evidence.required_argument_ok is False
    assert len(evidence.errors) == 2


@pytest.mark.asyncio
async def test_tool_canary_fails_when_a_correct_call_is_followed_by_a_wrong_one():
    """E8 (external review wave E): `required_argument_ok` must not be
    sticky/first-wins -- EVERY observed move_unit call is evaluated, and a
    single wrong-argument call fails the canary even when another call in
    the same reply matched the sentinel exactly."""
    backend = _ScriptedCanaryBackend([
        Reply(text=None, tool_calls=[_tool_call("finish_trial", {})]),
        Reply(
            text=None,
            tool_calls=[
                _tool_call("move_unit", REQUIRED_ARGUMENT_SENTINEL),
                _tool_call("move_unit", {"unit_index": 7, "x": 12, "y": 13}),
            ],
        ),
    ])
    evidence = await probe_tool_capability(backend, arm_id="standard", tools=[])
    assert evidence.required_argument_ok is False
    assert any("x" in e or "argument" in e for e in evidence.errors)


@pytest.mark.asyncio
async def test_tool_canary_fails_when_a_correct_call_is_followed_by_malformed_json():
    """Same order-independence, weakest form for the parse branch: the
    matching call first, then an unparseable one."""
    backend = _ScriptedCanaryBackend([
        Reply(text=None, tool_calls=[_tool_call("finish_trial", {})]),
        Reply(
            text=None,
            tool_calls=[
                _tool_call("move_unit", REQUIRED_ARGUMENT_SENTINEL),
                _tool_call("move_unit", "{not valid json"),
            ],
        ),
    ])
    evidence = await probe_tool_capability(backend, arm_id="standard", tools=[])
    assert evidence.required_argument_ok is False
    assert evidence.errors


# ---------------------------------------------------------------------------
# H4 (external review wave H): canary errors are classified at capture time,
# mirroring the probe funnel -- a 429 storm or token expiry between
# probe_backend and the canary loop must be distinguishable from genuine
# model-side canary failures.
# ---------------------------------------------------------------------------


class _RaisingCanaryBackend:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def chat(self, messages, tools):
        raise self._exc


@pytest.mark.asyncio
async def test_tool_canary_classifies_auth_exception():
    import httpx
    import openai

    request = httpx.Request("POST", "http://x/v1/chat/completions")
    exc = openai.AuthenticationError(
        "invalid api key", response=httpx.Response(401, request=request), body=None
    )
    evidence = await probe_tool_capability(_RaisingCanaryBackend(exc), arm_id="standard", tools=[])
    assert len(evidence.errors) == 2  # both canaries raised
    assert evidence.error_kinds == ("auth", "auth")


@pytest.mark.asyncio
async def test_tool_canary_classifies_transport_exception():
    import httpx
    import openai

    request = httpx.Request("POST", "http://x/v1/chat/completions")
    exc = openai.RateLimitError(
        "429 too many requests", response=httpx.Response(429, request=request), body=None
    )
    evidence = await probe_tool_capability(_RaisingCanaryBackend(exc), arm_id="standard", tools=[])
    assert evidence.error_kinds == ("transport", "transport")


@pytest.mark.asyncio
async def test_tool_canary_cold_start_503_classifies_transport():
    """Ruling I (wave H): a llama-swap cold-start 503 during the canary
    loop is environment evidence, kind "transport" -- never a model-side
    canary failure."""
    import httpx
    import openai

    request = httpx.Request("POST", "http://x/v1/chat/completions")
    exc = openai.InternalServerError(
        "503 loading model", response=httpx.Response(503, request=request), body=None
    )
    evidence = await probe_tool_capability(_RaisingCanaryBackend(exc), arm_id="standard", tools=[])
    assert evidence.error_kinds == ("transport", "transport")


@pytest.mark.asyncio
async def test_tool_canary_model_side_failures_classify_as_model_kind():
    """A genuine model-side canary failure (prose instead of a tool call,
    wrong arguments) stays kind "model" -- exactly the evidence
    tool_canary_failed exists for."""
    backend = _ScriptedCanaryBackend([
        Reply(text="Sure, I am done for this turn.", tool_calls=[]),
        Reply(text=None, tool_calls=[_tool_call("move_unit", {"unit_index": 7, "x": 12, "y": 13})]),
    ])
    evidence = await probe_tool_capability(backend, arm_id="standard", tools=[])
    assert len(evidence.errors) == 2
    assert evidence.error_kinds == ("model", "model")


@pytest.mark.asyncio
async def test_tool_canary_error_kinds_stay_aligned_with_errors():
    """H6 alignment invariant at the source: every error appended by
    probe_tool_capability carries exactly one index-aligned kind."""
    backend = _ScriptedCanaryBackend([
        Reply(text=None, tool_calls=[_tool_call("finish_trial", {})]),
        Reply(
            text=None,
            tool_calls=[
                _tool_call("move_unit", REQUIRED_ARGUMENT_SENTINEL),
                _tool_call("move_unit", "{not valid json"),
            ],
        ),
    ])
    evidence = await probe_tool_capability(backend, arm_id="standard", tools=[])
    assert len(evidence.error_kinds) == len(evidence.errors)
    assert evidence.error_kinds == ("model",)


# ---------------------------------------------------------------------------
# B3 (external review wave B): both tool canaries must run under the exact
# production system-prompt shape when a caller supplies one (spec Sec 7:
# "exact system prompt shape") -- omitting the system turn entirely
# exercises the model under a different prompt shape than a counted episode.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_canary_prepends_system_prompt_when_given():
    backend = _ScriptedCanaryBackend([
        Reply(text=None, tool_calls=[_tool_call("finish_trial", {})]),
        Reply(text=None, tool_calls=[_tool_call("move_unit", REQUIRED_ARGUMENT_SENTINEL)]),
    ])
    await probe_tool_capability(backend, arm_id="standard", tools=[], system_prompt="SYSTEM PROMPT TEXT")

    assert len(backend.calls) == 2
    for call in backend.calls:
        assert call["messages"][0] == {"role": "system", "content": "SYSTEM PROMPT TEXT"}
        assert call["messages"][1]["role"] == "user"


@pytest.mark.asyncio
async def test_tool_canary_omits_system_prompt_by_default():
    backend = _ScriptedCanaryBackend([
        Reply(text=None, tool_calls=[_tool_call("finish_trial", {})]),
        Reply(text=None, tool_calls=[_tool_call("move_unit", REQUIRED_ARGUMENT_SENTINEL)]),
    ])
    await probe_tool_capability(backend, arm_id="standard", tools=[])

    for call in backend.calls:
        assert call["messages"][0]["role"] == "user"
