# src/civ_mcp/arena/benchmark_backend.py
"""Pure helpers for the controlled-position benchmark: episode wall-clock
sizing and pre-flight probes over an injected backend. No network of their
own -- everything here drives `backend.chat(...)`, so tests exercise these
with fake backends.
"""
from __future__ import annotations
import asyncio
import json
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
    # One of: "honored", "not_honored", "not_applicable_greedy" (locked seed
    # but sampling.temperature == 0 -- seed honoring is unobservable and
    # irrelevant under greedy decoding), "no_seed_configured", or
    # "probe_error". `None` only for a BackendProbe constructed without this
    # field (pre-F15 callers/tests).
    seed_verdict: str | None = None
    # G7: whether the `samples` identical, exact-sampling calls all agreed
    # with each other. Always computed by probe_backend regardless of the
    # seed-verdict path taken; `False` by default for a BackendProbe
    # constructed without this field (pre-G7 callers/tests) so the field's
    # mere absence never silently reads as "consistent".
    repeated_consistent: bool = False
    # B4 (external review wave B): the endpoint's served /v1/models listing
    # at probe time (sorted ids) -- supplementary identity evidence folded
    # into the locked session identity (see benchmark_gates.
    # locked_model_admission_evidence), never a new admission gate. Empty
    # for any BackendProbe constructed without this field (pre-B4 callers/
    # tests) or when the live probe's best-effort listing call failed/was
    # unsupported.
    served_model_ids: tuple[str, ...] = ()


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
    temperature = getattr(sampling, "temperature", None)

    seed_honored = False
    seed_verdict: str | None = None
    if sampling is None or locked_seed is None:
        seed_verdict = "no_seed_configured"
    elif temperature == 0:
        # F15 ruling: at temperature == 0 (greedy/argmax decoding), seed
        # honoring is unobservable and irrelevant -- varying the seed
        # cannot change a genuinely greedy backend's output, so the
        # differing-seed call below would always (mis)report "not
        # honored" for a perfectly healthy backend. Skip it entirely --
        # but only when repeated-consistency actually held (G7): a
        # backend that disagrees with itself across the `samples` calls
        # at the SAME locked config is not honoring anything, greedy or
        # not, and must still fail admission via "not_honored" rather
        # than slip through as "not applicable".
        seed_verdict = "not_applicable_greedy" if repeated_consistent else "not_honored"
    elif repeated_consistent:
        varied = replace(sampling, seed=locked_seed + 1)
        backend.sampling = varied
        try:
            varied_reply = await backend.chat(messages, tools)
            seed_honored = _reply_signature(varied_reply) != outputs[0]
            seed_verdict = "honored" if seed_honored else "not_honored"
        except Exception as exc:
            errors.append(str(exc))
            seed_honored = False
            seed_verdict = "probe_error"
        finally:
            backend.sampling = sampling
    else:
        seed_verdict = "not_honored"

    return BackendProbe(
        samples=samples,
        model=backend.model if models else None,
        model_confirmed=model_confirmed,
        seed_honored=seed_honored,
        latencies_s=latencies,
        errors=errors,
        seed_verdict=seed_verdict,
        repeated_consistent=repeated_consistent,
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


# ---------------------------------------------------------------------------
# probe_tool_capability
# ---------------------------------------------------------------------------

# A smoke run proved gemma3-12b via llama.cpp silently ignores the OpenAI
# `tools` field and replies in prose -- an A/B on tool surfaces then measures
# nothing. These two canaries prove structured tool calling per arm before
# any counted trial: one requires calling `finish_trial` (the benchmark
# control tool, present on every arm's schema), the other requires calling
# `move_unit` with an exact required-argument sentinel. Both are deliberately
# generic, position-blind prompts -- this is a pre-flight capability check,
# not a scored trial -- and neither is ever routed to a real tool handler:
# this module has no dispatcher/game-connection dependency at all, so there
# is nothing here that COULD dispatch even by accident.
FINISH_TRIAL_CANARY_TOOL_NAME = "finish_trial"
REQUIRED_ARGUMENT_CANARY_TOOL_NAME = "move_unit"
REQUIRED_ARGUMENT_SENTINEL: dict[str, object] = {"unit_index": 7, "x": 11, "y": 13}

FINISH_TRIAL_CANARY_PROMPT = (
    "This is a pre-flight tool-capability check, not a real turn -- no game "
    "state exists yet. Call the finish_trial tool now, with no other tool "
    "call and no other text."
)

REQUIRED_ARGUMENT_CANARY_PROMPT = (
    "This is a pre-flight tool-capability check, not a real turn -- no game "
    "state exists yet. Call the move_unit tool now with exactly these "
    "arguments: unit_index=7, x=11, y=13. Make no other tool call."
)


@dataclass(frozen=True)
class ToolCanaryEvidence:
    """JSON-safe verdict from probing one arm's structured tool-calling
    capability. Both `finish_trial_ok` and `required_argument_ok` must be
    True for `benchmark_gates.admit_model_block` to admit this arm --
    neither canary is ever routed to a real tool handler."""

    arm_id: str
    finish_trial_ok: bool
    required_argument_ok: bool
    observed_calls: tuple[dict[str, object], ...]
    errors: tuple[str, ...]


def _parse_tool_call_arguments(raw: object) -> tuple[object, str | None]:
    """Best-effort JSON parse of a tool call's `arguments` string. Returns
    `(parsed, None)` on success or `(None, error)` on any failure -- never
    raises, since a malformed reply from the model under test is exactly the
    condition this probe exists to detect, not an infrastructure error."""
    if not isinstance(raw, str):
        return None, f"arguments is not a string: {raw!r}"
    try:
        return json.loads(raw), None
    except (TypeError, ValueError) as exc:
        return None, f"malformed JSON arguments {raw!r}: {exc}"


async def probe_tool_capability(
    backend, *, arm_id: str, tools: list[dict], system_prompt: str | None = None
) -> ToolCanaryEvidence:
    """Exercise `backend` with two fresh, generic, non-dispatching prompts to
    prove it can actually emit structured tool calls through `tools` --
    admission evidence, never a step in a scored episode.

    Canary 1 asks for a bare `finish_trial` call. Canary 2 asks for
    `move_unit` with the exact required-argument sentinel
    `{"unit_index": 7, "x": 11, "y": 13}` -- the returned JSON arguments are
    parsed and compared for an EXACT match (a backend that emits x=12, or
    prose instead of a tool call, or unparseable JSON, fails this canary).
    Both prompts are sent through the same `backend.chat(...)` used for a
    real trial step, but neither reply's tool calls are ever passed to
    `registry.dispatch` or any other tool handler -- this function only
    reads and compares `reply.tool_calls`.

    B3 (external review wave B): `system_prompt`, when given, is prepended
    as a `{"role": "system", ...}` turn ahead of each canary's user prompt
    -- spec Sec 7 requires probing under the "exact system prompt shape" a
    counted episode actually uses (`benchmark_agent.BENCHMARK_SYSTEM`), not
    a bare user-only message the model never sees during a real trial.
    `None` (the default) omits the system turn entirely, preserving this
    function's own pre-existing behavior for any caller that does not pass
    one.
    """
    observed_calls: list[dict[str, object]] = []
    errors: list[str] = []

    def _messages(user_content: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_prompt is not None:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})
        return messages

    finish_trial_ok = False
    try:
        reply = await backend.chat(_messages(FINISH_TRIAL_CANARY_PROMPT), tools)
        calls = list(reply.tool_calls or [])
        observed_calls.extend(dict(tc) for tc in calls)
        finish_trial_ok = any(
            tc.get("name") == FINISH_TRIAL_CANARY_TOOL_NAME for tc in calls
        )
        if not finish_trial_ok:
            errors.append(
                "finish_trial canary: expected a finish_trial tool call, got "
                f"tool_calls={[tc.get('name') for tc in calls]!r} text={reply.text!r}"
            )
    except Exception as exc:
        errors.append(f"finish_trial canary raised: {exc}")

    required_argument_ok = False
    try:
        reply = await backend.chat(_messages(REQUIRED_ARGUMENT_CANARY_PROMPT), tools)
        calls = list(reply.tool_calls or [])
        observed_calls.extend(dict(tc) for tc in calls)
        move_calls = [
            tc for tc in calls if tc.get("name") == REQUIRED_ARGUMENT_CANARY_TOOL_NAME
        ]
        if not move_calls:
            errors.append(
                "required-argument canary: expected a move_unit tool call, got "
                f"tool_calls={[tc.get('name') for tc in calls]!r} text={reply.text!r}"
            )
        else:
            # E8 (external review wave E): EVERY observed move_unit call is
            # evaluated -- required_argument_ok used to be sticky (first
            # matching call set it and a later wrong-argument call was
            # recorded but ignored). One wrong or unparseable call now
            # fails the canary regardless of order, even when another call
            # in the same reply matched the sentinel exactly.
            all_calls_ok = True
            for tc in move_calls:
                parsed, parse_error = _parse_tool_call_arguments(tc.get("arguments"))
                if parse_error is not None:
                    errors.append(f"required-argument canary: {parse_error}")
                    all_calls_ok = False
                elif parsed != REQUIRED_ARGUMENT_SENTINEL:
                    errors.append(
                        "required-argument canary: expected arguments "
                        f"{REQUIRED_ARGUMENT_SENTINEL!r}, got {parsed!r}"
                    )
                    all_calls_ok = False
            required_argument_ok = all_calls_ok
    except Exception as exc:
        errors.append(f"required-argument canary raised: {exc}")

    return ToolCanaryEvidence(
        arm_id=arm_id,
        finish_trial_ok=finish_trial_ok,
        required_argument_ok=required_argument_ok,
        observed_calls=tuple(observed_calls),
        errors=tuple(errors),
    )
