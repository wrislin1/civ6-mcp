"""Strictly-serial trial runner for the controlled-position benchmark.

This module is the plan's central integration point: it drives one
scheduled trial at a time through an explicit state machine, admits or
retries evidence according to a fixed, typed retry table, and persists
every outcome through `BenchmarkStore` so a crashed or killed process can
resume exactly where it left off without ever re-running a committed trial.

Per-trial state machine (`BenchmarkRunner.run_trial`, one attempt cycle):

    reload/reconnect -> popup hygiene -> canonical checksum
    -> fresh backend/agent -> episode -> final state -> atomic raw trial

("Deploy-confirmed" -- the save/build/registry admission gates in
`benchmark_gates` -- happens once, before a session starts, not per trial;
this module assumes it already holds.)

Retry and evidence-admission policy (see
`docs/superpowers/specs/2026-08-30-arena-controlled-position-benchmark-
design.md`, "Retry and evidence-admission policy"):

| Event                                                          | Outcome |
|-----------------------------------------------------------------|---------|
| Reload/reconnect failure                                        | infra attempt; retry |
| Popup hygiene failure                                            | infra attempt; retry |
| Harness crash before a complete episode (incl. `capture_state`  | infra attempt; retry |
|   failures, e.g. `BenchmarkStateError`)                          | |
| `EpisodeTimedOut` / request timeout, immediate canary healthy    | scoreable `runaway_timeout` trial |
| `EpisodeTimedOut` / request timeout, immediate canary unhealthy  | infra attempt; retry |
| Transport failure, immediate canary healthy                     | STOP session (not scoreable, not infra) |
| Transport failure, immediate canary unhealthy                   | infra attempt; retry |
| Canonical checksum mismatch                                      | STOP session; no model observation |
| Attempt cap (3, including across process resume) exhausted      | STOP session |
| Unknown/unrecognized exception                                  | STOP session; never auto-retried |
| Zero actions / step-limit / malformed model output               | admitted as a scoreable trial |

`run_trial` performs exactly ONE attempt cycle for one `TrialSpec` -- it
never loops internally to retry. `run` is the outer strictly-serial driver:
it re-invokes `run_trial` for the same spec until either a raw trial is
committed or `run_trial` raises `SessionAborted`, and it skips any index
`BenchmarkStore.completed_indices()` already reports done (resume).
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

import openai

from civ_mcp.arena.backends import OpenAICompatBackend, RetryPolicy
from civ_mcp.arena.benchmark_agent import EpisodeTimedOut, SingleTurnAgent
from civ_mcp.arena.benchmark_backend import HealthProbe
from civ_mcp.arena.benchmark_backend import probe_health as _probe_backend_health
from civ_mcp.arena.benchmark_manifest import (
    PositionManifest,
    SuiteManifest,
    fingerprint,
    load_position_manifest,
    load_suite_manifest,
)
from civ_mcp.arena.benchmark_schedule import TrialSpec, compile_schedule
from civ_mcp.arena.benchmark_state import (
    BenchmarkStateError,
    capture_canonical_state,
    state_digest,
)
from civ_mcp.arena.benchmark_store import BenchmarkStore, SessionLockMismatchError
from civ_mcp.arena.popups import dismiss_blocking_popups
from civ_mcp.connection import GameConnection
from civ_mcp.game_lifecycle import load_game_save
from civ_mcp.game_state import GameState
from civ_mcp.run_id import is_safe_run_id

log = logging.getLogger(__name__)

__all__ = [
    "FailureClass",
    "RunnerDependencies",
    "BenchmarkRunner",
    "SessionAborted",
    "RUNAWAY_TIMEOUT_TERMINAL",
    "MAX_INFRASTRUCTURE_ATTEMPTS",
    "main",
]

# The maximum is three total infrastructure attempts per scheduled trial,
# INCLUDING attempts recorded in an earlier, now-dead process -- always read
# from `store.attempt_count`, never from in-memory state, so this cap holds
# across process resume.
MAX_INFRASTRUCTURE_ATTEMPTS = 3

# Not a `FailureClass` member: this is a preregistered SCOREABLE terminal
# (a completed, model-attributable trial), never an infrastructure attempt.
RUNAWAY_TIMEOUT_TERMINAL = "runaway_timeout"


class SessionAborted(Exception):
    """Raised by `BenchmarkRunner` to stop the whole session immediately --
    never retried, never counted as an infrastructure attempt.

    Raised for: a canonical-checksum mismatch, an exhausted per-trial
    attempt cap, a healthy-but-non-scoreable transport exception, or any
    exception this module has no typed branch for ("unknown failure
    class"). `code` is a stable, machine-readable reason; `details` is
    JSON-safe evidence, mirroring `benchmark_gates.GateFailure`'s shape so
    both fail-closed exception types are easy to handle uniformly upstream
    (e.g. in `main`).
    """

    def __init__(self, code: str, details: Mapping[str, object] | None = None):
        self.code = code
        self.details = dict(details or {})
        super().__init__(str(self.details.get("message", code)))


class FailureClass(str, Enum):
    """Preregistered infrastructure-attempt failure classes.

    Every `BenchmarkStore.record_attempt` payload's `failure_class` is one
    of these -- never a free-text string derived from an exception message
    -- so a report can group retry pressure by cause without parsing text.
    Outcomes that are NOT infrastructure attempts (a scoreable
    `runaway_timeout` trial, a session-aborting checksum mismatch, an
    unknown exception) are deliberately not members here.
    """

    RELOAD_OR_RECONNECT = "reload_or_reconnect_failure"
    POPUP_HYGIENE = "popup_hygiene_failure"
    HARNESS_CRASH = "harness_crash"
    EPISODE_TIMEOUT_UNHEALTHY = "episode_timeout_unhealthy_endpoint"
    TRANSPORT_FAILURE_UNHEALTHY = "transport_failure_unhealthy_endpoint"


ReloadPositionFn = Callable[[str], Awaitable[None]]
DismissPopupsFn = Callable[[], Awaitable[str]]
CaptureStateFn = Callable[[], Awaitable[Mapping[str, object]]]
MakeAgentFn = Callable[[TrialSpec], Any]
ProbeHealthFn = Callable[[], Awaitable[HealthProbe]]


@dataclasses.dataclass
class RunnerDependencies:
    """Everything `BenchmarkRunner` needs injected, so its state machine is
    testable with plain async fakes and never touches a live game or
    network itself.

    - `reload_position(position_id)`: reload the named position's save,
      confirm the game continues, and reconnect -- one failure class
      (`FailureClass.RELOAD_OR_RECONNECT`) covers this whole sub-pipeline.
    - `dismiss_popups()`: best-effort popup hygiene before the episode.
    - `capture_state()`: query and return the canonical queried-state
      projection -- called once for the pre-episode checksum and once more
      for the post-episode final-state capture. Takes no arguments: the
      caller (production wiring or a test) already closes over whatever
      connection/player/tile_coords it needs.
    - `make_agent(spec)`: construct a fresh backend + agent conversation for
      one `TrialSpec` (arm-scoped tool tier, model, sampling). Production
      wiring MUST construct this agent with `capture_state` and the
      position manifest's `tile_coords` so counted trials record per-step
      state digests (see `SingleTurnAgent`).
    - `probe_health()`: an immediate, independently-bounded identity canary
      against the exact endpoint just used, run right after an
      `EpisodeTimedOut` / request-timeout / transport-failure exception.
    - `connection`: the live game connection threaded into `agent.run()` as
      `gs.conn` (only `SingleTurnAgent`'s own optional per-step capture
      uses it) -- `None` is fine for fakes that never touch it.
    """

    reload_position: ReloadPositionFn
    dismiss_popups: DismissPopupsFn
    capture_state: CaptureStateFn
    make_agent: MakeAgentFn
    probe_health: ProbeHealthFn
    connection: Any = None


class BenchmarkRunner:
    """Runs one benchmark session's schedule strictly serially against
    `store`, resuming cleanly from whatever `store` already has committed.
    """

    MAX_ATTEMPTS = MAX_INFRASTRUCTURE_ATTEMPTS

    def __init__(
        self,
        *,
        store: BenchmarkStore,
        dependencies: RunnerDependencies,
        expected_state: Mapping[str, object],
        player_id: int,
    ) -> None:
        self.store = store
        self._deps = dependencies
        self._expected_state = dict(expected_state)
        self._expected_digest = state_digest(self._expected_state)
        self.player_id = player_id

    async def run(self, schedule: Sequence[TrialSpec]) -> None:
        """Strictly serial: walk `schedule` in order, skip any index the
        store already has committed (resume), and keep re-attempting an
        incomplete trial until it commits or `run_trial` raises
        `SessionAborted` (which propagates and stops the whole run)."""
        for spec in schedule:
            while spec.index not in self.store.completed_indices():
                await self.run_trial(spec)

    async def run_trial(self, spec: TrialSpec) -> None:
        """Attempt `spec` exactly once.

        Returns normally either because the trial is already committed, a
        raw trial was just committed, or exactly one infrastructure attempt
        was just recorded (the caller decides whether to retry). Raises
        `SessionAborted` for anything that must stop the whole session.
        """
        if spec.index in self.store.completed_indices():
            return

        if self.store.attempt_count(spec.index) >= self.MAX_ATTEMPTS:
            self.store.append_event(
                "attempts_exhausted",
                trial_index=spec.index,
                failure_class="attempts_exhausted",
                details={"max_attempts": self.MAX_ATTEMPTS},
            )
            raise SessionAborted(
                "attempts_exhausted",
                {
                    "trial_index": spec.index,
                    "max_attempts": self.MAX_ATTEMPTS,
                    "message": (
                        f"trial {spec.index} exhausted {self.MAX_ATTEMPTS} infrastructure "
                        "attempts (including attempts from a prior process); stopping "
                        "the session rather than shrinking the arm"
                    ),
                },
            )

        self.store.append_event("trial_attempt_started", trial_index=spec.index)

        # -- reload / continue / reconnect ----------------------------------
        try:
            await self._deps.reload_position(spec.position_id)
        except Exception as exc:  # noqa: BLE001 - one scoped step, one failure class
            self._record_infra_attempt(spec.index, FailureClass.RELOAD_OR_RECONNECT, exc)
            return

        # -- popup hygiene ---------------------------------------------------
        try:
            popup_status = await self._deps.dismiss_popups()
        except Exception as exc:  # noqa: BLE001
            self._record_infra_attempt(spec.index, FailureClass.POPUP_HYGIENE, exc)
            return

        # dismiss_blocking_popups (see civ_mcp.arena.popups) never raises --
        # failures come back as "err"/"?" strings instead. The status must
        # be inspected here: an unrecognized/failure status must never
        # silently proceed into the trial with unhandled popups.
        if not isinstance(popup_status, str) or not popup_status.startswith("POPUPS|"):
            self._record_infra_attempt(
                spec.index,
                FailureClass.POPUP_HYGIENE,
                RuntimeError(f"popup hygiene reported failure status {popup_status!r}"),
            )
            return

        # -- canonical checksum -----------------------------------------------
        try:
            observed_state = await self._deps.capture_state()
        except Exception as exc:  # noqa: BLE001
            self._record_infra_attempt(spec.index, FailureClass.HARNESS_CRASH, exc)
            return

        observed_digest = state_digest(observed_state)
        if observed_digest != self._expected_digest:
            self.store.append_event(
                "checksum_mismatch",
                trial_index=spec.index,
                failure_class="checksum_mismatch",
                details={
                    "expected_digest": self._expected_digest,
                    "observed_digest": observed_digest,
                },
            )
            raise SessionAborted(
                "checksum_mismatch",
                {
                    "trial_index": spec.index,
                    "expected_digest": self._expected_digest,
                    "observed_digest": observed_digest,
                    "message": (
                        f"trial {spec.index}: canonical-state checksum mismatch after "
                        "reload; aborting the session without a model observation"
                    ),
                },
            )

        # -- fresh backend/agent, episode --------------------------------------
        agent = self._deps.make_agent(spec)
        # Registry tools call real GameState methods (gs.get_units() etc.);
        # a bare SimpleNamespace(conn=...) makes every dispatched game tool
        # raise AttributeError in a live session -- swallowed into ERROR
        # steps, so a trial would commit as evidence of a model that
        # "couldn't act" rather than surfacing this wiring bug.
        gs = GameState(self._deps.connection)
        turn = observed_state.get("turn")

        try:
            evidence = await agent.run(gs, self.player_id, turn)
        except EpisodeTimedOut as exc:
            await self._handle_timeout_like(spec, exc, observed_state, admits_runaway=True, agent=agent)
            return
        except openai.APITimeoutError as exc:
            # A single request's own timeout -- the agent's episode wall was
            # never reached, but this is the same "request/episode timeout"
            # preregistered-terminal bucket as EpisodeTimedOut.
            await self._handle_timeout_like(spec, exc, observed_state, admits_runaway=True, agent=agent)
            return
        except openai.APIConnectionError as exc:
            # Caught after APITimeoutError (a subclass) so a genuine timeout
            # never falls through to this broader "transport failure" branch.
            await self._handle_timeout_like(spec, exc, observed_state, admits_runaway=False, agent=agent)
            return
        except BenchmarkStateError as exc:
            # SingleTurnAgent.run()'s exception contract: any exception other
            # than EpisodeTimedOut (including a capture_state failure inside
            # the episode) is a harness failure, never a scoreable outcome.
            self._record_infra_attempt(spec.index, FailureClass.HARNESS_CRASH, exc)
            return
        except Exception as exc:  # noqa: BLE001 - deliberate terminal branch: stop, don't retry
            self.store.append_event(
                "unknown_failure",
                trial_index=spec.index,
                failure_class="unknown",
                details={"exception_type": type(exc).__qualname__, "error": repr(exc)},
            )
            raise SessionAborted(
                "unknown_failure",
                {
                    "trial_index": spec.index,
                    "exception_type": type(exc).__qualname__,
                    "message": (
                        f"trial {spec.index}: unrecognized exception {exc!r} out of "
                        "agent.run(); stopping the session for classification instead "
                        "of retrying"
                    ),
                },
            ) from exc

        await self._finalize_trial(
            spec,
            terminal=evidence.terminal.value,
            steps=evidence.steps,
            invalid_tool_calls=evidence.invalid_tool_calls,
            final_summary=evidence.final_summary,
            initial_state=observed_state,
            wall_clock_s=evidence.wall_clock_s,
            prompt_tokens=evidence.prompt_tokens,
            completion_tokens=evidence.completion_tokens,
        )

    async def _handle_timeout_like(
        self,
        spec: TrialSpec,
        exc: Exception,
        observed_state: Mapping[str, object],
        *,
        admits_runaway: bool,
        agent: Any = None,
    ) -> None:
        """Shared handling for `EpisodeTimedOut` / request-timeout /
        transport-failure exceptions: run the immediate health canary,
        journal both the original event and the canary, then branch.

        `admits_runaway` distinguishes the two exception buckets: timeouts
        are a preregistered scoreable terminal (`runaway_timeout`) when the
        canary is healthy; a transport failure is not preregistered as
        scoreable at all, so a healthy canary there means the failure
        wasn't demonstrably exogenous and the session stops for
        classification instead.
        """
        verdict = await self._deps.probe_health()
        healthy = bool(getattr(verdict, "healthy", False))

        self.store.append_event(
            "episode_exception",
            trial_index=spec.index,
            failure_class=None,
            details={"exception_type": type(exc).__qualname__, "error": str(exc)},
        )
        self.store.append_event(
            "health_canary",
            trial_index=spec.index,
            failure_class=None,
            details={
                "healthy": healthy,
                "model": getattr(verdict, "model", None),
                "latency_s": getattr(verdict, "latency_s", None),
                "error": getattr(verdict, "error", None),
            },
        )

        if healthy:
            if admits_runaway:
                # A timed-out SingleTurnAgent attaches whatever evidence it
                # accumulated before the wall-clock cutoff to
                # EpisodeTimedOut.partial_evidence (see benchmark_agent.py) --
                # a healthy-canary runaway_timeout terminal must commit that
                # partial transcript, not an empty one, since the episode
                # did take real, scoreable actions before running out of
                # budget. F7: EpisodeTimedOut is the only exception that
                # carries partial_evidence as an attribute -- a mid-episode
                # openai.APITimeoutError/APIConnectionError has none, so fall
                # back to the live agent's own partial_evidence() accessor
                # (the same instance-level progress state, per ruling-13's
                # "commit whatever real evidence already happened"
                # contract). `None` only when neither is available (e.g. a
                # bare exception raised before the agent ever ran, or a fake
                # agent in tests with no such accessor).
                partial = getattr(exc, "partial_evidence", None)
                if partial is None:
                    accessor = getattr(agent, "partial_evidence", None)
                    if callable(accessor):
                        partial = accessor()
                await self._finalize_trial(
                    spec,
                    terminal=RUNAWAY_TIMEOUT_TERMINAL,
                    steps=list(partial.steps) if partial is not None else [],
                    invalid_tool_calls=(
                        list(partial.invalid_tool_calls) if partial is not None else []
                    ),
                    final_summary=partial.final_summary if partial is not None else "",
                    initial_state=observed_state,
                    wall_clock_s=partial.wall_clock_s if partial is not None else None,
                    prompt_tokens=partial.prompt_tokens if partial is not None else 0,
                    completion_tokens=partial.completion_tokens if partial is not None else 0,
                )
                return
            raise SessionAborted(
                "healthy_transport_exception_not_scoreable",
                {
                    "trial_index": spec.index,
                    "exception_type": type(exc).__qualname__,
                    "message": (
                        f"trial {spec.index}: transport exception {exc!r} occurred but "
                        "the immediate health canary is healthy -- this is not a "
                        "preregistered scoreable terminal, so the session stops for "
                        "classification instead of being retried or admitted"
                    ),
                },
            )

        failure_class = (
            FailureClass.EPISODE_TIMEOUT_UNHEALTHY
            if admits_runaway
            else FailureClass.TRANSPORT_FAILURE_UNHEALTHY
        )
        self._record_infra_attempt(spec.index, failure_class, exc)

    async def _finalize_trial(self, spec: TrialSpec, **fields: object) -> None:
        """Capture final state and atomically commit the raw trial. A
        failure capturing final state after a completed/admitted episode is
        itself a harness crash (an infra attempt) -- the episode happened,
        but nothing is committed until this succeeds, so a fresh attempt
        reloads and reruns the whole episode from scratch."""
        try:
            final_state = await self._deps.capture_state()
        except Exception as exc:  # noqa: BLE001
            self._record_infra_attempt(spec.index, FailureClass.HARNESS_CRASH, exc)
            return

        attempt_count = self.store.attempt_count(spec.index) + 1
        payload: dict[str, object] = {
            "index": spec.index,
            "pair_id": spec.pair_id,
            "position_id": spec.position_id,
            "model": spec.model,
            "arm_id": spec.arm_id,
            "seed": spec.seed,
            "attempt_count": attempt_count,
            "final_state": final_state,
            **fields,
        }
        self.store.commit_trial(spec.index, payload)

    def _record_infra_attempt(
        self, index: int, failure_class: FailureClass, exc: Exception
    ) -> None:
        self.store.record_attempt(
            index,
            {
                "trial_index": index,
                "failure_class": failure_class.value,
                "exception_type": type(exc).__qualname__,
                "error": repr(exc),
            },
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
#
# session.json's canonical scorer_fingerprint (see the schema comment block
# above benchmark_report.build_report) is normally supplied by a real
# scorer-admission step; the ungated-smoke path has none, so it fingerprints
# a fixed evaluator identity instead -- deterministic, and truthy, which is
# all build_report requires.
_SMOKE_SCORER_EVALUATOR = "civ_mcp.arena.action_metrics.evaluate_predicate"
#
# Production wiring below assembles `RunnerDependencies` from a live game
# connection and OpenAI-compatible backends. It is deliberately NOT exercised
# by this task's test suite (no live game, no network, per the plan's
# constraints) and does not yet call the admission gates in
# `benchmark_gates` (`check_clean_checkout`, `check_gpu_conflicts`,
# `admit_model_block`, `build_session_lock`) -- assembling their live
# evidence (git status on both sides, a GPU process snapshot, native boot
# health) is out of this task's file scope and is intentionally left for
# Task 13's integration pass, which inspects the live game before writing
# the position-authoring plan. Treat this section as a working scaffold,
# not yet a fully gated admission path -- `_run_async` fails closed on this
# (refusing to run at all) unless `--ungated-smoke` explicitly opts into a
# marked, non-counted smoke session instead.


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="civ-arena-benchmark")
    parser.add_argument("--suite", required=True, help="path to a suite manifest YAML file")
    parser.add_argument(
        "--run-id", required=True, help="run id; the run directory is <run-dir>/<run-id>"
    )
    parser.add_argument(
        "--run-dir", default="benchmark_runs", help="base directory for benchmark run directories"
    )
    parser.add_argument(
        "--gateway-url",
        default=None,
        help="OpenAI-compatible gateway base URL for the counted backend (required to run)",
    )
    parser.add_argument("--api-key-env", default="LITELLM_OPENAI_API_KEY")
    parser.add_argument(
        "--ungated-smoke",
        action="store_true",
        help=(
            "Run without the live admission gate pipeline (check_clean_checkout / "
            "check_gpu_conflicts / admit_model_block / build_session_lock -- not yet "
            "wired into this CLI; see benchmark_gates.py). For NON-COUNTED SMOKE "
            "TESTING ONLY: evidence from this run is stamped ungated_smoke=true in "
            "the session lock so a scorer/report can refuse or flag it -- it must "
            "never be treated as a counted session."
        ),
    )
    return parser


def _resolve_position_path(suite_path: Path, position_id: str) -> Path:
    # Convention: a suite at <benchmarks>/suites/<name>.yaml pairs with
    # position manifests at <benchmarks>/positions/<position_id>.yaml.
    return suite_path.resolve().parent.parent / "positions" / f"{position_id}.yaml"


def _reload_result_is_success(result: object) -> bool:
    """Classify a raw `load_game_save` / `game_launcher.continue_after_lua_load`
    result string. Success strings start with "Loaded " or mention "world
    ready" (see `game_launcher.continue_after_lua_load`'s return values);
    everything else (an "Error: ..." string, a "WARNING: ..." string, or any
    other unrecognized text) is a failure."""
    text = str(result)
    return text.startswith("Loaded ") or "world ready" in text


def _build_live_dependencies(
    *,
    connection: GameConnection,
    position: PositionManifest,
    suite: SuiteManifest,
    gateway_url: str,
    api_key: str,
) -> RunnerDependencies:
    # Keyed by (model, seed): TrialSpec.seed must actually reach the backend
    # that executes the trial (F3) -- a backend cached by model alone would
    # always run with the suite's static `sampling.seed`, so a committed
    # trial's recorded seed was never the seed the endpoint actually saw,
    # invalidating paired-seed evidence.
    backend_cache: dict[tuple[str, int], OpenAICompatBackend] = {}
    last_model: list[str] = [suite.models[0]]
    last_seed: list[int] = [suite.seeds[0] if suite.seeds else 0]

    def backend_for(model: str, seed: int) -> OpenAICompatBackend:
        key = (model, seed)
        if key not in backend_cache:
            backend_cache[key] = OpenAICompatBackend(
                gateway_url,
                api_key,
                model,
                sampling=dataclasses.replace(suite.sampling, seed=seed),
                retry_policy=RetryPolicy(max_attempts=1),
            )
        return backend_cache[key]

    async def reload_position(_position_id: str) -> None:
        # load_game_save reports most failures as strings ("Error: ...",
        # "WARNING: FireTuner port never dropped...", menu-fallback text)
        # rather than raising -- RunnerDependencies.reload_position's
        # contract is Awaitable[None], so this is the only place that ever
        # sees the raw result. A failure/warning string must raise here so
        # run_trial classifies it as a retryable RELOAD_OR_RECONNECT infra
        # attempt instead of silently proceeding into the checksum check
        # (which would then abort the whole session as checksum_mismatch --
        # a harness failure misreported as a state defect).
        result = await load_game_save(connection, position.game_save_name)
        if _reload_result_is_success(result):
            log.info("reload_position(%s): %s", _position_id, result)
        else:
            log.warning("reload_position(%s) failed: %s", _position_id, result)
            raise RuntimeError(f"reload_position({_position_id!r}) failed: {result}")

    async def dismiss_popups() -> str:
        return await dismiss_blocking_popups(connection)

    async def capture_state() -> Mapping[str, object]:
        return await capture_canonical_state(connection, position.player_id, position.relevant_tiles)

    def make_agent(spec: TrialSpec) -> SingleTurnAgent:
        arm = next(a for a in suite.arms if a.arm_id == spec.arm_id)
        if arm.options:
            # Fail closed: compile_schedule validates a declared
            # TreatmentArm.options (e.g. a "tools" override) as a property of
            # the schedule config, but this scaffold's make_agent only ever
            # reads arm.tools -- it does not apply arm.options at all. A
            # declared treatment must never silently run as the bare tier.
            raise ValueError(
                f"arm {arm.arm_id!r} declares options {arm.options!r}, but arm "
                "options are not applied by this scaffold; Plan 2 wires treatments"
            )
        last_model[0] = spec.model
        last_seed[0] = spec.seed
        backend = backend_for(spec.model, spec.seed)
        # Ruling: production wiring must construct SingleTurnAgent with
        # capture_state + the position manifest's tile_coords so counted
        # trials always record per-step state digests.
        return SingleTurnAgent(
            backend,
            arm.tools,
            episode_wall_s=300.0,
            max_steps=suite.max_steps,
            char_cap=suite.result_char_cap,
            tile_coords=position.relevant_tiles,
            capture_state=capture_canonical_state,
        )

    async def probe_health_dep() -> HealthProbe:
        model = last_model[0]
        return await _probe_backend_health(
            backend_for(model, last_seed[0]), model, timeout_s=15.0
        )

    return RunnerDependencies(
        reload_position=reload_position,
        dismiss_popups=dismiss_popups,
        capture_state=capture_state,
        make_agent=make_agent,
        probe_health=probe_health_dep,
        connection=connection,
    )


async def _run_async(args: argparse.Namespace) -> int:
    # Fail-closed FIRST, before touching the suite/position manifests, the
    # live game connection, or the store: this CLI does not yet call the
    # admission gates in benchmark_gates.py (check_clean_checkout /
    # check_gpu_conflicts / admit_model_block / build_session_lock). Without
    # this guard, once suite/position fixtures exist, `civ-arena-benchmark`
    # would silently run a fully counted session with no admission evidence
    # at all -- a hard architecture violation. `--ungated-smoke` is the one
    # deliberate override, for non-counted smoke testing only; it stamps
    # `ungated_smoke: True` into the session lock so the artifact itself
    # carries the mark for a future scorer/report to refuse or flag.
    if not args.ungated_smoke:
        print(
            "civ-arena-benchmark: refusing to run -- the live admission gate "
            "pipeline (check_clean_checkout / check_gpu_conflicts / "
            "admit_model_block / build_session_lock in benchmark_gates.py) is not "
            "yet wired into this CLI, so a counted session cannot be admitted. "
            "This is tracked as a Task 13 follow-up. Pass --ungated-smoke to run "
            "an explicitly non-counted smoke session instead (its evidence is "
            "stamped ungated_smoke=true and must never be scored as a counted run).",
            file=sys.stderr,
        )
        return 1

    suite_path = Path(args.suite)
    try:
        suite = load_suite_manifest(suite_path)
    except (OSError, ValueError) as exc:
        print(f"civ-arena-benchmark: failed to load suite {suite_path}: {exc}", file=sys.stderr)
        return 1

    if len(suite.positions) != 1:
        print(
            f"civ-arena-benchmark: suite {suite_path} declares {len(suite.positions)} "
            "position(s); this runner currently supports exactly one position per suite",
            file=sys.stderr,
        )
        return 1

    position_id = suite.positions[0]
    position_path = _resolve_position_path(suite_path, position_id)
    try:
        position = load_position_manifest(position_path)
    except (OSError, ValueError) as exc:
        print(
            f"civ-arena-benchmark: failed to load position manifest {position_path}: {exc}",
            file=sys.stderr,
        )
        return 1

    if not args.gateway_url:
        print("civ-arena-benchmark: --gateway-url is required to run a session", file=sys.stderr)
        return 1

    try:
        schedule = compile_schedule(suite)
    except ValueError as exc:
        print(f"civ-arena-benchmark: invalid schedule: {exc}", file=sys.stderr)
        return 1

    run_dir = Path(args.run_dir) / args.run_id
    schedule_payload = {"trials": [dataclasses.asdict(t) for t in schedule]}
    lock = {
        "session_fingerprint": fingerprint(
            {"suite_id": suite.suite_id, "position_id": position_id}
        ),
        "schedule_fingerprint": fingerprint(schedule_payload),
        # Always True here (the function returns above when it's False) --
        # stamped explicitly, never omitted, so the artifact itself always
        # carries the mark for a future scorer/report to check.
        "ungated_smoke": True,
        # Canonical keys benchmark_report.build_report requires -- see the
        # schema comment block above that function. Without these,
        # `civ-arena-benchmark-report` fails on a missing scorer_fingerprint
        # for every run this CLI produces, gated or not.
        "scorer_fingerprint": fingerprint({"evaluator": _SMOKE_SCORER_EVALUATOR}),
        "positions": {
            position_id: {
                "rubric": list(position.rubric),
                "objectives": list(position.objectives),
            }
        },
    }
    try:
        store = BenchmarkStore.create(run_dir, lock)
    except SessionLockMismatchError as exc:
        print(f"civ-arena-benchmark: {exc}", file=sys.stderr)
        return 1

    schedule_path = run_dir / "schedule.json"
    if not schedule_path.exists():
        schedule_path.write_text(
            json.dumps(schedule_payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

    connection = GameConnection()
    await connection.connect()

    api_key = os.environ.get(args.api_key_env, "x")
    deps = _build_live_dependencies(
        connection=connection,
        position=position,
        suite=suite,
        gateway_url=args.gateway_url,
        api_key=api_key,
    )

    runner = BenchmarkRunner(
        store=store,
        dependencies=deps,
        expected_state=position.expected_state,
        player_id=position.player_id,
    )

    try:
        await runner.run(schedule)
    except SessionAborted as exc:
        print(f"civ-arena-benchmark: session aborted ({exc.code}): {exc}", file=sys.stderr)
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if not is_safe_run_id(args.run_id):
        print(f"civ-arena-benchmark: invalid --run-id {args.run_id!r}", file=sys.stderr)
        return 1
    try:
        return asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
