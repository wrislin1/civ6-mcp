"""Objective-blind single-turn agent for controlled-position benchmark trials.

This is a deliberately separate, simpler loop from `arena.agent.LLMPolicy`:
one trial is exactly one turn, the prompt carries zero position-specific
nouns/coordinates/rubric hints (so the model cannot infer what is being
measured), and the tool surface is a benchmark-safe projection of an arena
tool tier -- `end_turn` is never exposed (a benchmark trial must never let
the model advance the game past the single scored turn) and a `finish_trial`
control tool is always appended so the model has an explicit way to signal
"done" that isn't tied to any game action.

`SingleTurnAgent.run` dispatches only real game tools through
`arena.registry.dispatch` (never the arena's own `agent._dispatch`, which is
tied to `LLMPolicy`'s capability-filtered visible-tools bookkeeping) and
records the same complete per-step fields `LLMPolicy` does, plus a
manifest-scoped canonical-state projection and digest captured immediately
before and after every dispatched game tool call. Those captures are a
harness-only verification query (see `arena.benchmark_state`) run out of
band through the caller-supplied `capture_state`/`tile_coords` -- they are
never appended to the model-visible conversation. By default (no
`tile_coords`) no such capture happens, which keeps this agent usable with a
plain `GameState`-like object and no live FireTuner connection at all.

The whole episode is wrapped in `asyncio.timeout(episode_wall_s)`. A timeout
raises `EpisodeTimedOut` rather than propagating asyncio's own
`TimeoutError`, so the benchmark runner can catch a single, agent-owned
exception type and run its health discriminator (a slow-but-alive backend
scores a "runaway_timeout" terminal; a dead one is an infrastructure retry)
without needing to know anything about this module's internals.

A tool call with malformed arguments (invalid JSON, or valid JSON that
isn't an object) is never dispatched -- fabricating an empty `args={}` call
the model never specified would execute a real game mutation on the model's
behalf. Instead it is recorded in `invalid_tool_calls` (reason
`"bad_arguments"`, the same counted metric as an unknown-tool call), a step
is recorded with an `"ERROR: malformed arguments"` result and no state
capture, and an error tool-result message is fed back into the model
conversation so the episode continues -- a single garbled call among
otherwise-valid ones is an invalid call, not a new terminal condition.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Sequence

from civ_mcp.arena.agent import MODEL_FEED_CHAR_CAP
from civ_mcp.arena.benchmark_state import capture_canonical_state, state_digest
from civ_mcp.arena.registry import dispatch as _registry_dispatch
from civ_mcp.arena.registry import openai_tools, resolve_tools

FINISH_TRIAL_TOOL_NAME = "finish_trial"

FINISH_TRIAL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": FINISH_TRIAL_TOOL_NAME,
        "description": (
            "Call this when you are finished issuing orders for this turn. "
            "Takes no arguments."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

# Deliberately generic: no position-specific nouns, coordinates, unit
# references, or rubric hints belong here any more than in benchmark_prompt
# -- this is the system half of the same objective-blind conversation.
BENCHMARK_SYSTEM = (
    "You control one civilization's turn in a running game. You have no "
    "memory of prior turns or sessions beyond what the current message "
    "tells you. Use the available tools to observe the current situation, "
    "then issue whatever orders are appropriate. When you have nothing "
    "further to do this turn, call finish_trial."
)


def benchmark_prompt(turn: int, player_id: int) -> str:
    """The exact, objective-blind per-turn prompt for a benchmark trial.

    No position-specific nouns, coordinates, unit references, or rubric
    hints: the model must infer what to do entirely from live tool calls,
    never from the prompt itself.
    """
    return (
        f"It is turn {turn}. You control player {player_id}. Assess the "
        "current situation and issue the best orders available for this "
        "turn. When finished, call finish_trial."
    )


def _resolve_game_tool_names(tier: str | Sequence[str]) -> tuple[str, ...]:
    names = resolve_tools(tier)
    if "end_turn" in names:
        raise ValueError(
            f"benchmark tool tier {tier!r} resolves to a tool surface exposing "
            "end_turn, which benchmark trials must never expose"
        )
    return names


def resolved_benchmark_tools(tier: str | Sequence[str]) -> list[dict[str, Any]]:
    """Resolve an arena tool tier into a benchmark-safe OpenAI tool schema list.

    Rejects (raises `ValueError`) any tier whose resolved tool surface
    includes `end_turn`, then appends `FINISH_TRIAL_SCHEMA` last so
    `finish_trial` is present in every benchmark tier and always the final
    schema entry.
    """
    names = _resolve_game_tool_names(tier)
    tools = openai_tools(names)
    tools.append(FINISH_TRIAL_SCHEMA)
    return tools


class EpisodeTerminal(str, Enum):
    """Why a `SingleTurnAgent` episode ended."""

    # The model called finish_trial (alone or alongside other tool calls in
    # the same response); any game calls in that response were executed first.
    FINISH_TRIAL = "finish_trial"
    # The model returned a response with no tool calls at all.
    IMPLICIT_FINISH = "implicit_finish"
    # max_steps chat rounds elapsed without an explicit or implicit finish.
    STEP_LIMIT = "step_limit"


class EpisodeTimedOut(Exception):
    """Raised when a benchmark episode exceeds its `episode_wall_s` budget.

    The runner catches this and runs its health discriminator: a healthy,
    identity-correct backend admits the timeout as a scoreable
    "runaway_timeout" terminal, while an unhealthy/unreachable one is
    recorded as an infrastructure attempt and retried.

    `partial_evidence` carries whatever `EpisodeEvidence` fields
    (steps/invalid_tool_calls/tokens/final_summary) `SingleTurnAgent`
    accumulated before the wall-clock cutoff cancelled `_run_episode` --
    `None` only if the timeout landed before a single step was recorded.
    A healthy-canary "runaway_timeout" terminal must commit this partial
    transcript rather than an empty one: the episode *did* take real,
    scoreable actions before running out of budget.
    """

    def __init__(self, message: str, *, partial_evidence: "EpisodeEvidence | None" = None) -> None:
        super().__init__(message)
        self.partial_evidence = partial_evidence


@dataclass(frozen=True)
class EpisodeEvidence:
    """Everything a benchmark runner needs from one completed episode.

    `steps` carries the same complete per-step fields `LLMPolicy` records
    (idx, role, ts_start/ts_end, tool_name, tool_args, tool_result_full,
    result_total_chars, result_chars_fed_to_model, truncated, prompt_tokens,
    completion_tokens) plus the harness-only `state_before`/`state_after`/
    `state_digest_before`/`state_digest_after` canonical-state projection
    captured around every dispatched game tool call -- `None` when no
    `tile_coords`/`capture_state` was configured.
    """

    terminal: EpisodeTerminal
    steps: list[dict[str, Any]]
    invalid_tool_calls: list[dict[str, Any]]
    final_summary: str
    wall_clock_s: float
    prompt_tokens: int
    completion_tokens: int


CaptureStateFn = Callable[[Any, int, Sequence[tuple[int, int]]], Awaitable[Mapping[str, object]]]


class SingleTurnAgent:
    """One objective-blind turn: observe, act, `finish_trial`. No briefing,
    no standing plan, no channels -- those are `LLMPolicy` concerns that
    would leak position-specific context into a benchmark trial.
    """

    def __init__(
        self,
        backend: Any,
        tier: str | Sequence[str],
        *,
        episode_wall_s: float,
        max_steps: int = 6,
        char_cap: int = MODEL_FEED_CHAR_CAP,
        tile_coords: Sequence[tuple[int, int]] = (),
        capture_state: CaptureStateFn | None = capture_canonical_state,
    ) -> None:
        self.backend = backend
        self._game_tool_names = _resolve_game_tool_names(tier)
        self._tools_schema = resolved_benchmark_tools(tier)
        self.episode_wall_s = episode_wall_s
        self.max_steps = max_steps
        self._char_cap = char_cap
        self._tile_coords = tuple(tile_coords)
        self._capture_state = capture_state
        # Reset at the start of every run() (see run()'s docstring) --
        # initialized here too so partial_evidence() is always safe to call.
        self._progress_steps: list[dict[str, Any]] = []
        self._progress_invalid_tool_calls: list[dict[str, Any]] = []
        self._progress_final_summary: str = ""
        self._progress_prompt_tokens: int = 0
        self._progress_completion_tokens: int = 0
        self._progress_wall_clock_start: float = time.time()

    def partial_evidence(self) -> EpisodeEvidence:
        """Snapshot of progress accumulated so far in the current/most
        recent `run()` -- the same instance-level `_progress_*` state
        `EpisodeTimedOut.partial_evidence` is built from (see `run()`'s
        `except TimeoutError` branch below). Exposed so a caller can
        recover pre-failure evidence for exception paths that don't
        themselves carry a `partial_evidence` attribute (e.g. the runner's
        handling of a mid-episode `openai.APITimeoutError` /
        `APIConnectionError`, per ruling-13's "commit whatever real
        evidence already happened" contract)."""
        return EpisodeEvidence(
            # Placeholder: no caller of this accessor trusts this field for
            # its own terminal -- each stamps its own preregistered
            # terminal onto the trial itself.
            terminal=EpisodeTerminal.STEP_LIMIT,
            steps=list(self._progress_steps),
            invalid_tool_calls=list(self._progress_invalid_tool_calls),
            final_summary=self._progress_final_summary,
            wall_clock_s=time.time() - self._progress_wall_clock_start,
            prompt_tokens=self._progress_prompt_tokens,
            completion_tokens=self._progress_completion_tokens,
        )

    async def _capture(
        self, gs: Any, player_id: int
    ) -> tuple[Mapping[str, object] | None, str | None]:
        # No tile_coords/capture_state configured -> no harness query at all,
        # so this agent works against a bare GameState-like object with no
        # live connection. Never added to the model conversation either way.
        if not self._tile_coords or self._capture_state is None:
            return None, None
        state = await self._capture_state(gs.conn, player_id, self._tile_coords)
        return state, state_digest(state)

    async def run(self, gs: Any, player_id: int, turn: int) -> EpisodeEvidence:
        """Run one objective-blind turn and return its evidence.

        Exception contract for the runner: `EpisodeTimedOut` is the *only*
        exception this method raises deliberately -- it means the episode
        ran out of wall-clock budget and is a scoreable-candidate timeout for
        the runner's health discriminator (healthy/identity-correct backend
        -> "runaway_timeout" terminal; unhealthy/unreachable -> infrastructure
        retry). Any *other* exception (including one raised by an injected
        `capture_state`, e.g. `benchmark_state.BenchmarkStateError` on a
        stale connection or a wrong manifest `player_id`) propagates out of
        `run()` unchanged, with no `EpisodeEvidence` returned -- this is a
        harness failure, not a model outcome, and the runner must classify
        it as an infrastructure attempt rather than score it.
        """
        # Reset before every run(): these are mutated in place by
        # _run_episode as it goes (never reassigned via a local variable),
        # so they still hold everything recorded so far even if
        # asyncio.timeout cancels _run_episode mid-flight and tears down its
        # local frame. See EpisodeTimedOut.partial_evidence.
        self._progress_steps: list[dict[str, Any]] = []
        self._progress_invalid_tool_calls: list[dict[str, Any]] = []
        self._progress_final_summary: str = ""
        self._progress_prompt_tokens: int = 0
        self._progress_completion_tokens: int = 0
        self._progress_wall_clock_start: float = time.time()
        try:
            async with asyncio.timeout(self.episode_wall_s):
                return await self._run_episode(gs, player_id, turn)
        except TimeoutError as exc:
            raise EpisodeTimedOut(
                f"benchmark episode exceeded episode_wall_s={self.episode_wall_s}",
                partial_evidence=self.partial_evidence(),
            ) from exc

    async def _run_episode(self, gs: Any, player_id: int, turn: int) -> EpisodeEvidence:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": BENCHMARK_SYSTEM},
            {"role": "user", "content": benchmark_prompt(turn, player_id)},
        ]
        # Mutated in place (never reassigned) so partial progress survives a
        # mid-flight cancellation -- see run()'s except TimeoutError branch.
        steps = self._progress_steps
        invalid_tool_calls = self._progress_invalid_tool_calls
        terminal = EpisodeTerminal.STEP_LIMIT

        for _ in range(self.max_steps):
            reply = await self.backend.chat(messages, self._tools_schema)
            self._progress_prompt_tokens += reply.prompt_tokens
            self._progress_completion_tokens += reply.completion_tokens

            if not reply.tool_calls:
                self._progress_final_summary = reply.text or ""
                terminal = EpisodeTerminal.IMPLICIT_FINISH
                break

            if reply.text:
                self._progress_final_summary = reply.text
            messages.append({
                "role": "assistant",
                "content": reply.text or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in reply.tool_calls
                ],
            })

            saw_finish = any(
                tc["name"] == FINISH_TRIAL_TOOL_NAME for tc in reply.tool_calls
            )

            for tc in reply.tool_calls:
                if tc["name"] == FINISH_TRIAL_TOOL_NAME:
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"], "content": "TRIAL_FINISHED",
                    })
                    continue

                ts_start = time.time()
                malformed_args = False
                try:
                    args = json.loads(tc["arguments"] or "{}")
                    if not isinstance(args, dict):
                        args = {}
                        malformed_args = True
                except (json.JSONDecodeError, ValueError):
                    args = {}
                    malformed_args = True

                if malformed_args:
                    # Fail closed: a garbled/non-dict tool call is an invalid
                    # call (already a counted metric), not a license to
                    # dispatch the real game tool with arguments the model
                    # never actually specified. No dispatch, no state
                    # capture -- just record the invalid call and feed an
                    # error back so the episode continues.
                    invalid_tool_calls.append({
                        "tool_name": tc["name"], "arguments": tc["arguments"],
                        "reason": "bad_arguments",
                    })
                    # Deliberately NOT prefixed "ERROR: " -- action_metrics
                    # .classify_result treats any leading "Error: ..." string
                    # as a domain_rejection (a legal call the game engine
                    # rejected). This call never reached the game engine at
                    # all; it is already counted in invalid_tool_calls, and
                    # counting it as a domain_rejection too would
                    # double-count the same failure under two metrics.
                    result: Any = "MALFORMED_ARGUMENTS: not dispatched"
                    state_before = state_after = None
                    digest_before = digest_after = None
                elif tc["name"] not in self._game_tool_names:
                    invalid_tool_calls.append({
                        "tool_name": tc["name"], "arguments": tc["arguments"],
                        "reason": "unknown_tool",
                    })
                    result = f"UNAVAILABLE: {tc['name']} is not a real tool."
                    state_before = state_after = None
                    digest_before = digest_after = None
                else:
                    state_before, digest_before = await self._capture(gs, player_id)
                    try:
                        result = await _registry_dispatch(
                            gs, tc["name"], args, allowed=self._game_tool_names
                        )
                    except Exception as e:
                        result = f"ERROR: {e!r}"
                    state_after, digest_after = await self._capture(gs, player_id)

                ts_end = time.time()
                result_str = str(result)
                result_len = len(result_str)
                steps.append({
                    "idx": len(steps),
                    "role": "tool",
                    "ts_start": ts_start,
                    "ts_end": ts_end,
                    "tool_name": tc["name"],
                    "tool_args": args,
                    "tool_result_full": result_str,
                    "result_total_chars": result_len,
                    "result_chars_fed_to_model": min(result_len, self._char_cap),
                    "truncated": result_len > self._char_cap,
                    "prompt_tokens": reply.prompt_tokens,
                    "completion_tokens": reply.completion_tokens,
                    "state_before": state_before,
                    "state_after": state_after,
                    "state_digest_before": digest_before,
                    "state_digest_after": digest_after,
                })
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": result_str[: self._char_cap],
                })

            if saw_finish:
                terminal = EpisodeTerminal.FINISH_TRIAL
                break

        return EpisodeEvidence(
            terminal=terminal,
            steps=steps,
            invalid_tool_calls=invalid_tool_calls,
            final_summary=self._progress_final_summary,
            wall_clock_s=time.time() - self._progress_wall_clock_start,
            prompt_tokens=self._progress_prompt_tokens,
            completion_tokens=self._progress_completion_tokens,
        )
