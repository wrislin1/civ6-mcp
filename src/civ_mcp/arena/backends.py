from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Mapping
import openai
from openai import AsyncOpenAI

# A turn-step is "reason, then emit one tool call". Observed legit steps reach
# ~1,900 completion tokens; one live step hit the old 3072 cap (truncated tool
# JSON -> gateway 500 -> the 37a48ef crash). 6144 is ~3x observed max. At local
# speeds (~25-35 tok/s on a 3090) a full 6144-token generation runs 3-4 minutes,
# so the timeout rises with it: the token cap, not the clock, bounds a legit
# long step. A timeout at this cap means runaway generation - it is re-raised
# without retry (see chat()) so one seat stalls at most ~5 min before the
# coordinator's degrade guard skips the turn.
MAX_COMPLETION_TOKENS = 6144
REQUEST_TIMEOUT_S = 300.0

# A single chat step can fail transiently: the gateway 500s on a malformed/truncated
# tool call (which at temp>0 usually differs when resampled), llama-swap 503s while it
# loads the model, or a network blip drops the request. A bounded retry recovers these
# without falling through to the coordinator's skip-the-turn guard. A PERSISTENT failure
# exhausts the retries and re-raises, so the coordinator still degrades that one turn
# rather than the run. Kept small so a truly-wedged upstream is surfaced quickly.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = 1.0


@dataclass(frozen=True)
class SamplingConfig:
    """Locked sampling parameters for one backend. A field left `None` is
    omitted from the request entirely (provider default applies), which is
    what preserves legacy wire behavior when no sampling is specified.
    `max_tokens` always carries a value since a completion cap must always
    be sent (see MAX_COMPLETION_TOKENS's rationale above)."""
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    max_tokens: int = MAX_COMPLETION_TOKENS


@dataclass(frozen=True)
class RetryPolicy:
    """Backend-level transient-error retry. Counted benchmark trials MUST be
    constructed with RetryPolicy(max_attempts=1): a hidden backend/SDK retry
    would silently resample a model episode. Infrastructure-level retry (with
    attempts recorded under attempts/) lives solely in the benchmark runner,
    not here. Arena play keeps the legacy default (3 attempts)."""
    max_attempts: int = MAX_ATTEMPTS
    backoff_s: float = RETRY_BACKOFF_S


@dataclass
class Reply:
    text: str | None
    tool_calls: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str | None = None

class OpenAICompatBackend:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        sampling: SamplingConfig | None = None,
        retry_policy: RetryPolicy | None = None,
        chat_template_kwargs: Mapping[str, object] | None = None,
    ):
        self.model = model
        self.base_url = base_url
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        # Defaulting to the frozen configs' own defaults (rather than leaving
        # None) reproduces the pre-Task-4 wire behavior exactly: no sampling
        # keys sent, three-attempt retry.
        self.sampling = sampling or SamplingConfig()
        self.retry_policy = retry_policy or RetryPolicy()
        # `None` reproduces the pre-Task-5 hardcoded literal exactly (ordinary
        # arena callers never pass this). A counted block passes the exact
        # mapping from `ModelBlockConfig.chat_template_kwargs`. Stored as a
        # defensive copy so a caller mutating its own dict afterward can
        # never reach into a cached backend's locked config.
        self.chat_template_kwargs = dict(
            {"enable_thinking": False} if chat_template_kwargs is None else chat_template_kwargs
        )

    async def aclose(self) -> None:
        """Close the underlying AsyncOpenAI client's connection pool.

        G5: per-(model, seed) backends are cached for the life of a
        benchmark session with nothing closing them on any exit path --
        expose a close hook so a caller (the benchmark runner's cleanup)
        has something real to call.
        """
        await self._client.close()

    async def chat(self, messages: list[dict], tools: list[dict]) -> Reply:
        kw = dict(
            model=self.model,
            messages=messages,
            max_tokens=self.sampling.max_tokens,
            timeout=REQUEST_TIMEOUT_S,
            # Defensive copy: every request gets its own dict, never a shared
            # reference into `self.chat_template_kwargs`.
            extra_body={"chat_template_kwargs": dict(self.chat_template_kwargs)},
        )
        if self.sampling.temperature is not None:
            kw["temperature"] = self.sampling.temperature
        if self.sampling.top_p is not None:
            kw["top_p"] = self.sampling.top_p
        if self.sampling.seed is not None:
            kw["seed"] = self.sampling.seed
        if tools:
            kw["tools"] = tools
            kw["tool_choice"] = "auto"
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                resp = await self._client.chat.completions.create(**kw)
                break
            except openai.APITimeoutError:
                # Runaway generation, not a transient: resampling would repeat it.
                raise
            except Exception:
                if attempt >= self.retry_policy.max_attempts:
                    raise
                await asyncio.sleep(self.retry_policy.backoff_s * attempt)
        msg = resp.choices[0].message
        tcs = []
        for tc in (msg.tool_calls or []):
            tcs.append({"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments})
        u = resp.usage
        return Reply(text=msg.content, tool_calls=tcs,
                     prompt_tokens=getattr(u, "prompt_tokens", 0),
                     completion_tokens=getattr(u, "completion_tokens", 0),
                     model=getattr(resp, "model", None))

    async def reachable(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception:
            return False

    async def list_model_ids(self) -> tuple[str, ...]:
        """Served model ids from `/v1/models` -- the same cheap OpenAI-
        compat listing `reachable()` already calls, returning the actual
        ids instead of discarding them.

        B4 (external review wave B): supplementary endpoint-identity
        evidence, never a new admission gate of its own. G2 (external
        review wave G, Ruling H): a failed listing is no longer swallowed
        into an empty tuple here -- an empty tuple was indistinguishable
        from a genuinely-empty listing, so an auth/transport failure of
        this call could silently pass admission with `served_model_ids: []`
        recorded as if the endpoint had answered. The exception now
        propagates so the caller
        (`benchmark_runner._build_live_admission_dependencies`'s
        `probe_backend_dep`) can classify it
        (`benchmark_backend.classify_backend_exception`): auth/transport
        failures fail admission under their own non-deferrable codes, while
        any other failure (an endpoint that simply doesn't expose
        `/v1/models`) keeps the B4 best-effort empty-tuple behavior at that
        call site -- the success-path value (sorted ids) is byte-identical
        to before, so existing locks' `served_model_ids` are unaffected.
        """
        listing = await self._client.models.list()
        return tuple(sorted(model.id for model in listing.data))
