import asyncio
import types

import httpx
import openai
import pytest

from civ_mcp.arena.backends import (
    MAX_ATTEMPTS,
    MAX_COMPLETION_TOKENS,
    REQUEST_TIMEOUT_S,
    RETRY_BACKOFF_S,
    OpenAICompatBackend,
    Reply,
    RetryPolicy,
    SamplingConfig,
)


class _FakeUsage:  prompt_tokens = 11; completion_tokens = 3
class _FakeMsg:    content = "ok"; tool_calls = None
class _FakeChoice: message = _FakeMsg()
class _FakeResp:   choices = [_FakeChoice()]; usage = _FakeUsage()


@pytest.mark.asyncio
async def test_chat_parses_reply(monkeypatch):
    b = OpenAICompatBackend("http://x/v1", "k", "m")
    async def fake_create(**kw): return _FakeResp()
    monkeypatch.setattr(b._client.chat.completions, "create", fake_create)
    r = await b.chat([{"role": "user", "content": "hi"}], tools=[])
    assert isinstance(r, Reply)
    assert r.text == "ok" and r.prompt_tokens == 11 and r.completion_tokens == 3


class _CapturingCompletions:
    def __init__(self): self.kwargs = None
    async def create(self, **kw):
        self.kwargs = kw
        return _FakeResp()


def _backend_with_capture(*, sampling=None, retry_policy=None, chat_template_kwargs=None):
    b = OpenAICompatBackend.__new__(OpenAICompatBackend)
    b.model = "gemma4-26b"
    b.base_url = "http://x/v1"
    b.sampling = sampling or SamplingConfig()
    b.retry_policy = retry_policy or RetryPolicy()
    b.chat_template_kwargs = dict(
        {"enable_thinking": False} if chat_template_kwargs is None else chat_template_kwargs
    )
    cap = _CapturingCompletions()
    b._client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=cap))
    return b, cap


def _thinking_off_extra_body():
    return {"chat_template_kwargs": {"enable_thinking": False}}


def test_chat_sends_max_tokens_timeout_and_thinking_off_without_tools():
    # Without these, a degenerate generation runs until it exhausts context and
    # stalls the whole game — the cap + timeout bound each turn-step.
    b, cap = _backend_with_capture()
    asyncio.run(b.chat([{"role": "user", "content": "hi"}], tools=[]))
    assert cap.kwargs["max_tokens"] == MAX_COMPLETION_TOKENS
    assert cap.kwargs["timeout"] == REQUEST_TIMEOUT_S
    assert cap.kwargs["extra_body"] == _thinking_off_extra_body()
    assert "tools" not in cap.kwargs


def test_benchmark_backend_sends_exact_sampling_and_disables_resampling():
    sampling = SamplingConfig(temperature=0.2, top_p=0.95, seed=41, max_tokens=2048)
    backend, capture = _backend_with_capture(
        sampling=sampling,
        retry_policy=RetryPolicy(max_attempts=1, backoff_s=0.0),
    )
    asyncio.run(backend.chat([{"role": "user", "content": "act"}], []))
    assert capture.kwargs["temperature"] == 0.2
    assert capture.kwargs["top_p"] == 0.95
    assert capture.kwargs["seed"] == 41
    assert capture.kwargs["max_tokens"] == 2048


def test_chat_passes_tools_with_cap_and_thinking_off():
    b, cap = _backend_with_capture()
    asyncio.run(b.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}]))
    assert cap.kwargs["tool_choice"] == "auto"
    assert cap.kwargs["max_tokens"] == MAX_COMPLETION_TOKENS
    assert cap.kwargs["extra_body"] == _thinking_off_extra_body()


def test_default_construction_omits_sampling_keys_and_keeps_three_attempts():
    """Hard backward-compat gate: an arena caller that passes no `sampling` or
    `retry_policy` must see identical wire behavior to before this task --
    no temperature/top_p/seed on the request, and the legacy 3-attempt retry."""
    b = OpenAICompatBackend("http://x/v1", "k", "m")
    assert b.sampling == SamplingConfig()
    assert b.retry_policy == RetryPolicy(max_attempts=MAX_ATTEMPTS, backoff_s=RETRY_BACKOFF_S)
    cap = _CapturingCompletions()
    b._client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=cap))
    asyncio.run(b.chat([{"role": "user", "content": "hi"}], tools=[]))
    assert "temperature" not in cap.kwargs
    assert "top_p" not in cap.kwargs
    assert "seed" not in cap.kwargs
    assert cap.kwargs["max_tokens"] == MAX_COMPLETION_TOKENS


def test_backend_sends_locked_chat_template_kwargs_and_sampling():
    """Task 5: `chat_template_kwargs` becomes a constructor input -- a counted
    block passes the exact mapping from `ModelBlockConfig`, and it must be
    forwarded verbatim (as a defensive copy, both at construction and at
    send-time) rather than the hardcoded `{"enable_thinking": False}`."""
    sampling = SamplingConfig(temperature=0.2, top_p=0.9, seed=7, max_tokens=2048)
    locked_kwargs = {"enable_thinking": True, "custom_flag": "x"}
    backend = OpenAICompatBackend(
        "http://x/v1", "k", "m", sampling=sampling, chat_template_kwargs=locked_kwargs,
    )
    cap = _CapturingCompletions()
    backend._client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=cap))

    asyncio.run(backend.chat([{"role": "user", "content": "act"}], []))
    assert cap.kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True, "custom_flag": "x"}}
    assert cap.kwargs["extra_body"]["chat_template_kwargs"] is not locked_kwargs
    assert cap.kwargs["temperature"] == 0.2
    assert cap.kwargs["top_p"] == 0.9
    assert cap.kwargs["seed"] == 7
    assert cap.kwargs["max_tokens"] == 2048

    # Mutating the caller's dict after construction must never leak into the
    # backend's stored config or a subsequent request.
    locked_kwargs["custom_flag"] = "mutated"
    asyncio.run(backend.chat([{"role": "user", "content": "act"}], []))
    assert cap.kwargs["extra_body"]["chat_template_kwargs"]["custom_flag"] == "x"


def test_caps_are_bounded():
    # guard against someone loosening the cap back into runaway territory
    assert 256 <= MAX_COMPLETION_TOKENS <= 8192
    assert 30 <= REQUEST_TIMEOUT_S <= 600
    assert MAX_COMPLETION_TOKENS == 6144   # slice-4 decision (spec §4)
    assert REQUEST_TIMEOUT_S == 300.0      # token cap, not the clock, bounds a step


def _backend_with_create(create_fn):
    b = OpenAICompatBackend.__new__(OpenAICompatBackend)
    b.model = "gemma4-26b"
    b.base_url = "http://x/v1"
    b.sampling = SamplingConfig()
    b.retry_policy = RetryPolicy()
    b.chat_template_kwargs = {"enable_thinking": False}
    b._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create_fn))
    )
    return b


async def _noop(*_a, **_k):
    return None


@pytest.mark.asyncio
async def test_chat_retries_transient_error_then_succeeds(monkeypatch):
    """A transient gateway failure (e.g. a 500 on a malformed tool call -- which at
    temp>0 usually differs when resampled, or a model-swap 503) is retried, and a
    later success returns normally instead of bubbling up and costing the whole turn."""
    monkeypatch.setattr(asyncio, "sleep", _noop)
    calls = {"n": 0}

    async def flaky(**kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Error code: 500 - Failed to parse tool call arguments as JSON")
        return _FakeResp()

    b = _backend_with_create(flaky)
    r = await b.chat([{"role": "user", "content": "hi"}], tools=[])
    assert r.text == "ok"
    assert calls["n"] == 3   # two failures retried, third succeeded


@pytest.mark.asyncio
async def test_chat_raises_after_bounded_retries(monkeypatch):
    """A persistently-failing call is retried a bounded number of times, then the
    error is re-raised so the coordinator's degrade-not-abort guard skips the turn."""
    monkeypatch.setattr(asyncio, "sleep", _noop)
    calls = {"n": 0}

    async def always_fail(**kw):
        calls["n"] += 1
        raise RuntimeError("Error code: 500 - persistent")

    b = _backend_with_create(always_fail)
    with pytest.raises(RuntimeError):
        await b.chat([{"role": "user", "content": "hi"}], tools=[])
    assert calls["n"] == 3   # bounded: exactly three attempts, no unbounded loop


@pytest.mark.asyncio
async def test_timeout_errors_are_not_retried(monkeypatch):
    """A 300 s timeout at a 6144 cap means runaway generation; resampling it
    3x would stall one seat ~15 minutes. Timeouts re-raise immediately so the
    coordinator's degrade guard skips the turn (spec §4)."""
    monkeypatch.setattr(asyncio, "sleep", _noop)
    calls = {"n": 0}

    async def timing_out(**kw):
        calls["n"] += 1
        raise openai.APITimeoutError(request=httpx.Request("POST", "http://x/v1"))

    b = _backend_with_create(timing_out)
    with pytest.raises(openai.APITimeoutError):
        await b.chat([{"role": "user", "content": "hi"}], tools=[])
    assert calls["n"] == 1   # no retry


@pytest.mark.asyncio
async def test_non_timeout_errors_still_retry(monkeypatch):
    """The existing 3-attempt retry stays for gateway 500s / llama-swap 503s."""
    monkeypatch.setattr(asyncio, "sleep", _noop)
    calls = {"n": 0}

    async def flaky_then_ok(**kw):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("HTTP 500")
        import types as _t
        msg = _t.SimpleNamespace(content="ok", tool_calls=None)
        return _t.SimpleNamespace(
            choices=[_t.SimpleNamespace(message=msg)],
            usage=_t.SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    b = _backend_with_create(flaky_then_ok)
    r = await b.chat([{"role": "user", "content": "hi"}], tools=[])
    assert r.text == "ok"
    assert calls["n"] == 2
