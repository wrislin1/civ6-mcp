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
from civ_mcp.arena.benchmark_agent import EpisodeTimedOut, SingleTurnAgent, resolved_benchmark_tools
from civ_mcp.arena.benchmark_backend import HealthProbe
from civ_mcp.arena.benchmark_backend import probe_health as _probe_backend_health
from civ_mcp.arena.benchmark_gates import GateFailure
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
    diff_state,
    state_digest,
    verify_expected_state_digest,
)
from civ_mcp.arena.benchmark_store import (
    BenchmarkStore,
    BenchmarkStoreError,
    SessionLockMismatchError,
    TrialProvenanceError,
)
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
    "ResolvedBlock",
    "run_resolved_block",
    "reload_position",
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


ReloadPositionFn = Callable[[str], Awaitable[bool]]
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
      Returns `verified: bool` -- True when the reload observed positive
      evidence the world came back up (an observed port drop, or a Tier-2
      OCR/menu navigation success); False when it only has a
      success-shaped-but-unconfirmable result (G3: the F16(b) stable-
      open-port fallback is structurally indistinguishable from an inert
      Network.LoadGame from the launcher's side -- only the runner's own
      checksum step can tell them apart). A caller MUST return an actual
      bool; a legacy `None`/other non-bool return is a contract violation
      and is NOT treated as verified=True (fail closed) -- see
      `run_trial`.
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
    - `aclose`: optional async cleanup callable closing every resource this
      `RunnerDependencies` opened that isn't `connection` itself (G5: the
      per-(model, seed) cached backend clients in the live wiring) --
      `None` is fine for fakes with nothing to close. The live game
      `connection`'s own close/disconnect is the caller's (`_run_async`'s)
      responsibility, not this callable's, since the caller owns
      `connection`'s lifetime independently of `RunnerDependencies`.
    """

    reload_position: ReloadPositionFn
    dismiss_popups: DismissPopupsFn
    capture_state: CaptureStateFn
    make_agent: MakeAgentFn
    probe_health: ProbeHealthFn
    connection: Any = None
    aclose: Callable[[], Awaitable[None]] | None = None


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
            if spec.index in self.store.completed_indices():
                self._verify_resume_provenance(spec.index)
                continue
            while spec.index not in self.store.completed_indices():
                await self.run_trial(spec)

    def _verify_resume_provenance(self, index: int) -> None:
        """F8/campaign-provenance: resume identifies completion by filename
        only (`completed_indices()` just lists `trials/*.json`) -- a stale
        or copied `trial-NNN.json` (e.g. left over from an unrelated run
        directory, or copied from a DIFFERENT counted campaign block) is
        otherwise indistinguishable from real current-lock evidence.

        Delegates to `BenchmarkStore.is_trial_complete`, which fails closed
        on exactly this: a missing/mismatched `session_fingerprint`, and --
        for a counted block whose own lock carries a `campaign_fingerprint`
        -- a missing/mismatched `campaign_fingerprint` too. A trial copied
        from a different campaign's block that happens to carry a matching
        `session_fingerprint` but a different (or absent)
        `campaign_fingerprint` must never be treated as this block's own
        completed evidence."""
        try:
            complete = self.store.is_trial_complete(index)
        except TrialProvenanceError as exc:
            raise SessionAborted(
                "trial_provenance_invalid",
                {
                    "trial_index": index,
                    "message": str(exc),
                },
            ) from exc
        if not complete:
            # Every caller only reaches this method for an index it already
            # observed in `completed_indices()` -- this branch defends only
            # against a TOCTOU change to the store between that check and
            # this call, not an expected steady-state outcome.
            raise SessionAborted(
                "trial_missing_on_resume",
                {
                    "trial_index": index,
                    "message": (
                        f"trial {index}: expected committed evidence during resume "
                        "but none was found"
                    ),
                },
            )

    async def run_trial(self, spec: TrialSpec) -> None:
        """Attempt `spec` exactly once.

        Returns normally either because the trial is already committed, a
        raw trial was just committed, or exactly one infrastructure attempt
        was just recorded (the caller decides whether to retry). Raises
        `SessionAborted` for anything that must stop the whole session.
        """
        if spec.index in self.store.completed_indices():
            self._verify_resume_provenance(spec.index)
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
            reload_verified = await self._deps.reload_position(spec.position_id)
            if not isinstance(reload_verified, bool):
                # G3: fail closed on a contract violation rather than
                # silently treating a legacy `None` (or any other non-bool)
                # return as verified=True.
                raise TypeError(
                    "RunnerDependencies.reload_position must return a bool "
                    f"(verified); got {type(reload_verified).__name__}: "
                    f"{reload_verified!r}"
                )
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
            # F12: journaling two opaque hashes is useless for actually
            # seeing what differed -- include the field-level diff so a
            # human (or a later automated triage pass) can tell "trivial
            # numeric-type drift" from "the reload landed on the wrong
            # save" without re-deriving it by hand.
            diff = diff_state(self._expected_state, observed_state)
            if not reload_verified:
                # G3: the immediately preceding reload could not itself
                # confirm the world came back up (F16(b) stable-open-port
                # fallback) -- a mismatch here is exactly what an inert
                # Network.LoadGame would also look like, so retry the
                # reload instead of aborting a session over what may be a
                # harness artifact rather than a real state defect.
                self.store.append_event(
                    "checksum_mismatch_unverified_reload",
                    trial_index=spec.index,
                    failure_class=FailureClass.RELOAD_OR_RECONNECT.value,
                    details={
                        "expected_digest": self._expected_digest,
                        "observed_digest": observed_digest,
                        "diff": diff,
                    },
                )
                self._record_infra_attempt(
                    spec.index,
                    FailureClass.RELOAD_OR_RECONNECT,
                    RuntimeError(
                        "checksum mismatch after unverified reload -- retrying reload"
                    ),
                )
                return
            self.store.append_event(
                "checksum_mismatch",
                trial_index=spec.index,
                failure_class="checksum_mismatch",
                details={
                    "expected_digest": self._expected_digest,
                    "observed_digest": observed_digest,
                    "diff": diff,
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
        # G4: construction failures here (a bad model/backend config, a
        # GameState construction failure) must be classified like every
        # other harness step, not escape run_trial as a raw, unjournalled
        # traceback.
        try:
            agent = self._deps.make_agent(spec)
            # Registry tools call real GameState methods (gs.get_units()
            # etc.); a bare SimpleNamespace(conn=...) makes every dispatched
            # game tool raise AttributeError in a live session -- swallowed
            # into ERROR steps, so a trial would commit as evidence of a
            # model that "couldn't act" rather than surfacing this wiring
            # bug.
            gs = GameState(self._deps.connection)
        except Exception as exc:  # noqa: BLE001
            self._record_infra_attempt(spec.index, FailureClass.HARNESS_CRASH, exc)
            return
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
            # F8: stamp session provenance into every committed payload so
            # resume/report can tell current-lock evidence apart from a
            # stale/copied trial-NNN.json (which would otherwise be
            # indistinguishable by filename alone).
            "session_fingerprint": self.store.fingerprint,
            **fields,
        }
        if self.store.campaign_fingerprint:
            # Counted evidence requires BOTH stamps (see
            # BenchmarkStore.is_trial_complete / TrialProvenanceError). A
            # non-counted --ungated-smoke store's lock carries no
            # campaign_fingerprint at all, so this branch never fires for
            # smoke and a smoke trial payload stays single-stamped, exactly
            # as before this dual-stamp change.
            payload["campaign_fingerprint"] = self.store.campaign_fingerprint
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
# connection and OpenAI-compatible backends, for the `--suite`/
# `--ungated-smoke` path. It is deliberately NOT exercised by this module's
# own test suite (no live game, no network) and deliberately does NOT call
# the admission gates in `benchmark_gates` (`check_clean_checkout`,
# `check_gpu_conflicts`, `admit_model_block`, `build_session_lock`) --
# `--ungated-smoke` is an explicitly non-counted escape hatch, never gated,
# by design, not by omission.
#
# The counted `--campaign` path is a separate wiring, `_build_live_admission_
# dependencies` below: it assembles the SAME live evidence (git status on
# both sides, a GPU process snapshot, native boot health, ...) and runs it
# through `benchmark_admission.AdmissionPipeline` (imported locally inside
# the CLI functions below to avoid a circular top-level import --
# benchmark_admission imports ResolvedBlock from this module) before ever
# handing a resolved block to the runner.


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="civ-arena-benchmark")
    parser.add_argument(
        "--suite", default=None, help="path to a suite manifest YAML file (used with --ungated-smoke)"
    )
    parser.add_argument(
        "--campaign",
        default=None,
        help="path to a campaign manifest YAML file for a counted, admission-gated run",
    )
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
        "--wsl-repo",
        default=None,
        help="WSL-side repo checkout path for the clean-checkout gate (--campaign only)",
    )
    parser.add_argument(
        "--windows-repo",
        default=None,
        help="Windows-companion repo checkout path for the clean-checkout gate (--campaign only)",
    )
    parser.add_argument(
        "--admit-only",
        default=None,
        metavar="BLOCK_ID",
        help="run admission diagnostics for one campaign block and exit without minting a "
        "reusable session or running trials",
    )
    parser.add_argument(
        "--non-counting-validation",
        default=None,
        metavar="BLOCK_ID",
        help="run the admission gates as a non-counted validation pass for one campaign "
        "block; writes under benchmark_runs/<id>/validation/ and mints no counted "
        "fingerprint pair",
    )
    parser.add_argument(
        "--terminate-tuner-pid",
        type=int,
        default=None,
        metavar="PID",
        help="terminate exactly this PID if (and only if) it currently holds the FireTuner "
        "port -- see benchmark_live_evidence.terminate_tuner_pid",
    )
    parser.add_argument(
        "--drain-gpu-service",
        action="append",
        default=[],
        metavar="UNIT",
        help="stop exactly this registry-managed systemd unit if it is the sole GPU "
        "conflict for --endpoint-id (repeatable) -- see benchmark_live_evidence.drain_gpu_service",
    )
    parser.add_argument(
        "--endpoint-id",
        default=None,
        help="registry endpoint id for --drain-gpu-service (not needed for --campaign, "
        "which reads endpoint_id per block from the campaign manifest)",
    )
    parser.add_argument(
        "--one-block",
        action="store_true",
        help="with --campaign, run only the next incomplete block in manifest order, then exit",
    )
    parser.add_argument(
        "--ungated-smoke",
        action="store_true",
        help=(
            "Run without the live admission gate pipeline, over --suite. For NON-COUNTED "
            "SMOKE TESTING ONLY: evidence from this run is stamped ungated_smoke=true in "
            "the session lock so a scorer/report can refuse or flag it -- it must never be "
            "treated as a counted session."
        ),
    )
    return parser


def _resolve_position_path(suite_path: Path, position_id: str) -> Path:
    # Convention: a suite at <benchmarks>/suites/<name>.yaml pairs with
    # position manifests at <benchmarks>/positions/<position_id>.yaml.
    return suite_path.resolve().parent.parent / "positions" / f"{position_id}.yaml"


def _reload_result_is_success(result: object) -> bool:
    """Classify a raw `load_game_save` / `game_launcher.continue_after_lua_load`
    / `game_launcher.restart_and_load` result string.

    G2: the original check only recognized the Tier-0/1 frontend-Lua
    strings ("Loaded " prefix / "world ready"). Real Tier-2 successes look
    different: `_navigate_to_save_sync` returns "Save loading (Ns).
    Steps: ..." and `restart_and_load` returns a compound
    "Kill: ... | Launch: ... | Load: Save loading (...)" string -- both
    were misclassified as failures, burning all infrastructure attempts on
    a working reload.

    Success requires a recognized success marker AND the absence of any
    failure marker (a failure marker anywhere in the string -- e.g. inside
    a compound Kill/Launch/Load string -- always wins). Unrecognized text
    stays a failure (fail closed)."""
    text = str(result)
    has_success_marker = (
        text.startswith("Loaded ") or "world ready" in text or "Save loading (" in text
    )
    has_failure_marker = any(
        marker in text for marker in ("FAILED", "ABORTED", "WARNING:", "Error:", "not found")
    )
    return has_success_marker and not has_failure_marker


async def reload_position(connection: GameConnection, position: PositionManifest) -> bool:
    """Reload `position`'s save via `load_game_save` and classify the raw
    result with `_reload_result_is_success`.

    THE single production reload step: both the live `RunnerDependencies`
    wiring (`_build_live_dependencies`, below) and the
    `civ-arena-benchmark-position` CLI's `capture`/`verify` commands import
    and call this exact function -- neither re-implements the
    success/failure/verified classification.

    `load_game_save` reports most failures as strings ("Error: ...",
    "WARNING: FireTuner port never dropped...", menu-fallback text) rather
    than raising -- this is the only place that ever sees the raw result. A
    failure/warning string must raise here so a caller (e.g. `run_trial`)
    classifies it as a retryable infra attempt instead of silently
    proceeding into a checksum check (which would then abort the whole
    session as a state defect that was really a harness failure).

    Returns `verified: bool` -- True when the reload observed positive
    evidence the world came back up (an observed port drop, or a Tier-2
    OCR/menu navigation success); False when it only has a
    success-shaped-but-unconfirmable result (G3: the F16(b) stable-open-port
    fallback is structurally indistinguishable from an inert
    Network.LoadGame from the launcher's side -- only a caller's own
    checksum step can tell them apart).
    """
    result = await load_game_save(connection, position.game_save_name)
    text = str(result)
    if _reload_result_is_success(result):
        # G3: the F16(b) stable-open-port fallback returns a
        # success-shaped string carrying an UNVERIFIED marker -- the
        # launcher structurally cannot confirm that reload (no game
        # connection of its own), so surface verified=False for it; every
        # other success path (observed port drop, Tier-2 OCR/menu
        # navigation) reports verified=True.
        verified = "UNVERIFIED" not in text
        log.info(
            "reload_position(%s): %s (verified=%s)", position.game_save_name, result, verified
        )
        return verified
    log.warning("reload_position(%s) failed: %s", position.game_save_name, result)
    raise RuntimeError(f"reload_position({position.game_save_name!r}) failed: {result}")


def _build_live_dependencies(
    *,
    connection: GameConnection,
    position: PositionManifest,
    suite: SuiteManifest,
    gateway_url: str,
    api_key: str,
    episode_wall_s: int = 300,
    chat_template_kwargs: Mapping[str, object] | None = None,
    user_prompt: str = "",
) -> RunnerDependencies:
    # `chat_template_kwargs` is threaded straight into `OpenAICompatBackend`'s
    # constructor (see backends.py -- `chat()` sends it as `extra_body`).
    # `None` here preserves `OpenAICompatBackend`'s own default
    # (`{"enable_thinking": False}`) exactly.
    resolved_chat_template_kwargs: dict[str, object] | None = (
        dict(chat_template_kwargs) if chat_template_kwargs is not None else None
    )
    # `user_prompt=""` (the empty string, NOT None) is ResolvedBlock's
    # smoke-path default -- its field is a plain `str`, not `str | None`.
    # SingleTurnAgent's own contract treats `None` as "no frozen campaign
    # prompt; fall back to the legacy per-turn benchmark_prompt(turn,
    # player_id) template" (see benchmark_agent.SingleTurnAgent.run()) -- so
    # an empty string is folded to None here, and only a genuinely non-empty
    # frozen prompt (a counted campaign's CampaignManifest.prompt) is passed
    # through verbatim.
    resolved_user_prompt = user_prompt or None
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
            backend = OpenAICompatBackend(
                gateway_url,
                api_key,
                model,
                sampling=dataclasses.replace(suite.sampling, seed=seed),
                retry_policy=RetryPolicy(max_attempts=1),
                chat_template_kwargs=resolved_chat_template_kwargs,
            )
            backend_cache[key] = backend
        return backend_cache[key]

    async def _reload_position_dep(_position_id: str) -> bool:
        # Thin adapter to the `RunnerDependencies.reload_position` contract
        # (Callable[[str], Awaitable[bool]], keyed by position_id): the
        # single production reload step itself lives in the top-level
        # `reload_position(connection, position)` (defined above this
        # function) -- shared verbatim with the `civ-arena-benchmark-position`
        # CLI, never duplicated here.
        return await reload_position(connection, position)

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
                "options are not applied by this scaffold; applying arm options "
                "is deferred to Plan 3"
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
            episode_wall_s=float(episode_wall_s),
            max_steps=suite.max_steps,
            char_cap=suite.result_char_cap,
            tile_coords=position.relevant_tiles,
            capture_state=capture_canonical_state,
            user_prompt=resolved_user_prompt,
        )

    async def probe_health_dep() -> HealthProbe:
        model = last_model[0]
        return await _probe_backend_health(
            backend_for(model, last_seed[0]), model, timeout_s=15.0
        )

    async def aclose() -> None:
        # G5: close every cached per-(model, seed) backend client -- none
        # of them are otherwise closed on any exit path.
        for backend in backend_cache.values():
            await backend.aclose()

    return RunnerDependencies(
        reload_position=_reload_position_dep,
        dismiss_popups=dismiss_popups,
        capture_state=capture_state,
        make_agent=make_agent,
        probe_health=probe_health_dep,
        connection=connection,
        aclose=aclose,
    )


@dataclasses.dataclass(frozen=True)
class ResolvedBlock:
    """Every input `run_resolved_block` needs to run one already-admitted
    block's compiled schedule to completion -- the whole production
    assembly boundary around the trusted `BenchmarkRunner`, collapsed into
    one frozen value so a caller (the `--ungated-smoke` CLI path today; a
    real gated campaign block in a later task) only has to build this once.

    `store` is a fully constructed `BenchmarkStore` (already `.create`d or
    `.open`ed against this block's own run directory) -- assembling ITS
    lock (deciding whether `campaign_fingerprint` is present, i.e. whether
    this is counted or smoke evidence) is the caller's job, not
    `run_resolved_block`'s. `schedule` is the exact, already-compiled
    trial order (`benchmark_schedule.compile_schedule`'s output, or a
    single campaign block's slice of it) -- `run_resolved_block` hands it
    to `BenchmarkRunner.run()` unchanged and in order; it never recompiles,
    reorders, or filters it.

    `episode_wall_s` / `chat_template_kwargs` / `user_prompt` are resolved
    model-facing settings threaded into every `SingleTurnAgent` / cached
    backend this block constructs (see `_build_live_dependencies`) -- for a
    counted campaign block these come from that block's admitted evidence
    (the tool-canary-derived episode wall, the locked chat template
    kwargs, the frozen campaign prompt); for the non-counted
    `--ungated-smoke` path they are fixed literals matching this scaffold's
    pre-existing hardcoded smoke behavior exactly (see `_run_async`).
    `user_prompt=""` (the empty string, not `None`) is how the smoke path
    spells "no frozen prompt" through this non-Optional field --
    `_build_live_dependencies` folds an empty string back to `None`, which
    is `SingleTurnAgent`'s own "use the legacy per-turn benchmark_prompt"
    sentinel.
    """

    position: PositionManifest
    suite: SuiteManifest
    schedule: tuple[TrialSpec, ...]
    store: BenchmarkStore
    gateway_url: str
    api_key: str
    episode_wall_s: int
    chat_template_kwargs: dict[str, object]
    user_prompt: str


async def run_resolved_block(block: ResolvedBlock) -> int:
    """Run `block`'s already-compiled schedule to completion (or session
    abort) against the trusted, unmodified `BenchmarkRunner`.

    This function IS the production assembly boundary: it connects to the
    live game, calls `_build_live_dependencies`, constructs
    `BenchmarkRunner`, calls `runner.run(block.schedule)` -- passing that
    exact schedule object straight through, never replicating or modifying
    any part of the retry/attempt/commit trial loop that lives inside
    `BenchmarkRunner` -- and closes every resource this function itself
    opened (the game connection, every cached backend client) on every
    exit path, mirroring the cleanup contract `_run_async` upheld before
    this extraction (see G5).
    """
    connection = GameConnection()
    await connection.connect()

    deps: RunnerDependencies | None = None
    try:
        deps = _build_live_dependencies(
            connection=connection,
            position=block.position,
            suite=block.suite,
            gateway_url=block.gateway_url,
            api_key=block.api_key,
            episode_wall_s=block.episode_wall_s,
            chat_template_kwargs=block.chat_template_kwargs,
            user_prompt=block.user_prompt,
        )

        runner = BenchmarkRunner(
            store=block.store,
            dependencies=deps,
            expected_state=block.position.expected_state,
            player_id=block.position.player_id,
        )

        try:
            await runner.run(block.schedule)
        except SessionAborted as exc:
            print(f"civ-arena-benchmark: session aborted ({exc.code}): {exc}", file=sys.stderr)
            return 1

        return 0
    finally:
        if deps is not None and deps.aclose is not None:
            await deps.aclose()
        await connection.disconnect()


def _git_rev_parse_head(repo: str) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


# B7 (external review wave B): a lapsed key or hung command must never
# stall a campaign forever -- every injected command runner below has a
# bounded wall-clock timeout. SSH gets a longer budget (network round trip
# on top of the remote command itself) than a local/Windows-bridge
# subprocess.
_LOCAL_COMMAND_TIMEOUT_S = 60.0
_WINDOWS_COMMAND_TIMEOUT_S = 60.0
_SSH_COMMAND_TIMEOUT_S = 120.0
_SSH_CONNECT_TIMEOUT_S = 10


def _timeout_command_result(argv: Sequence[str], timeout_s: float):
    """A `CommandResult` for a `subprocess.TimeoutExpired` -- surfaces the
    timeout as an ordinary command failure (Task 7's fail-closed contract:
    every downstream consumer here already treats a nonzero `returncode` as
    a hard failure) rather than propagating an unhandled exception out of
    an injected command runner."""
    from civ_mcp.arena.benchmark_live_evidence import CommandResult

    return CommandResult(
        argv=tuple(argv),
        returncode=124,  # conventional shell timeout exit code
        stdout="",
        stderr=f"command timed out after {timeout_s}s: {' '.join(argv)}",
    )


def _run_local_command(argv: Sequence[str]):
    import subprocess

    from civ_mcp.arena.benchmark_live_evidence import CommandResult

    try:
        result = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=_LOCAL_COMMAND_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return _timeout_command_result(argv, _LOCAL_COMMAND_TIMEOUT_S)
    return CommandResult(argv=tuple(argv), returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def _run_windows_command(argv: Sequence[str]):
    # WSL/Windows interop (WSL2 default): invoking a Windows executable
    # (git.exe on the Windows companion checkout's PATH) directly from WSL.
    # Mirrors the git argv shape benchmark_live_evidence.collect_checkout_evidence
    # always passes ("git", "-C", <windows path>, ...) -- only the
    # executable itself needs the .exe suffix.
    import subprocess

    from civ_mcp.arena.benchmark_live_evidence import CommandResult

    win_argv = ["git.exe", *argv[1:]] if argv and argv[0] == "git" else list(argv)
    try:
        result = subprocess.run(
            win_argv, capture_output=True, text=True, timeout=_WINDOWS_COMMAND_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return _timeout_command_result(argv, _WINDOWS_COMMAND_TIMEOUT_S)
    return CommandResult(argv=tuple(argv), returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def _run_ssh_command(host: str, argv: Sequence[str]):
    import shlex
    import subprocess

    from civ_mcp.arena.benchmark_live_evidence import CommandResult

    remote_cmd = " ".join(shlex.quote(a) for a in argv)
    # B7: -o BatchMode=yes refuses any interactive prompt (password/host-key
    # confirmation) outright rather than hanging on stdin forever; -o
    # ConnectTimeout bounds the TCP/handshake phase specifically, ahead of
    # the overall subprocess timeout below (which also covers the remote
    # command's own execution time).
    ssh_argv = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={_SSH_CONNECT_TIMEOUT_S}",
        host,
        remote_cmd,
    ]
    try:
        result = subprocess.run(
            ssh_argv, capture_output=True, text=True, timeout=_SSH_COMMAND_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return _timeout_command_result(argv, _SSH_COMMAND_TIMEOUT_S)
    return CommandResult(argv=tuple(argv), returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def _build_live_admission_dependencies(
    *, args: argparse.Namespace, api_key: str, user_prompt: str | None = None
):
    """Assemble a real, live `AdmissionDependencies` for the `--campaign`
    path -- the production counterpart to `_build_live_dependencies` above,
    wiring Task 6/7's collectors (`benchmark_deploy`,
    `benchmark_live_evidence`, `endpoint_registry`) plus `benchmark_backend`'s
    probes through the injected `AdmissionDependencies` contract.

    B3 (external review wave B): `user_prompt` is the frozen campaign
    prompt (`CampaignManifest.prompt`) the counted episode itself will send
    -- both `probe_backend`/`probe_tool_capability` dependencies below
    thread it (alongside `benchmark_agent.BENCHMARK_SYSTEM`) into the exact
    messages sent to the probed backend, so admission-time evidence is
    gathered under the same system-prompt shape a counted trial actually
    uses (spec Sec 7). `None` falls back to a generic identity-only prompt
    for the backend probe and omits the system turn from the tool
    canaries -- only exercised by direct unit tests of this factory that
    don't care about probe message shape; every real `--campaign` call site
    always passes the loaded campaign's own prompt.

    Deliberately NOT exercised by this task's test suite (no live game, no
    subprocess, no network, no SSH -- per the plan's constraints, mirroring
    `_build_live_dependencies`'s own precedent): CLI-level tests monkeypatch
    this factory (or `_build_admission_pipeline`) with a fake instead.
    """
    from civ_mcp.arena.benchmark_agent import BENCHMARK_SYSTEM
    from civ_mcp.arena import benchmark_admission
    from civ_mcp.arena.backends import OpenAICompatBackend
    from civ_mcp.arena.benchmark_backend import probe_backend as _probe_backend_impl
    from civ_mcp.arena.benchmark_backend import probe_tool_capability as _probe_tool_capability_impl
    from civ_mcp.arena.benchmark_deploy import check_boot_health_via_windows, deploy_via_windows
    from civ_mcp.arena.benchmark_live_evidence import (
        collect_checkout_evidence,
        collect_gpu_evidence,
        collect_tuner_evidence,
        gpu_processes_to_conflict_rows,
        partition_own_endpoint_processes,
    )
    from civ_mcp.arena.benchmark_gates import check_gpu_conflicts
    from civ_mcp.arena.endpoint_registry import _registry, resolve_gateway

    wsl_repo = args.wsl_repo or str(Path(__file__).resolve().parents[3])
    windows_repo = args.windows_repo or ""

    def checkout_evidence() -> dict:
        return collect_checkout_evidence(
            run_local=_run_local_command,
            run_windows=_run_windows_command,
            wsl_repo=wsl_repo,
            windows_repo=windows_repo,
        )

    def boot_health() -> dict:
        return dataclasses.asdict(check_boot_health_via_windows())

    def tuner_evidence() -> dict:
        return collect_tuner_evidence(run_local=_run_local_command, wsl_repo=wsl_repo, windows_repo=windows_repo)

    def deploy_save(position: PositionManifest) -> dict:
        return dataclasses.asdict(
            deploy_via_windows(position.archive, position.game_save_name, position.archive_sha256)
        )

    async def reload_and_capture(position: PositionManifest) -> dict:
        # Finding 2 (final review): delegate to the shared production
        # reload_position(connection, position) -- defined above this
        # function, the SAME single reload step the counted trial path
        # (`_reload_position_dep` below) and the capture/verify CLI use --
        # rather than re-deriving success/verified classification from a
        # raw load_game_save result here. Two independent copies of that
        # classification could silently drift; one production path can't.
        # reload_position raises RuntimeError on a failure/warning string
        # (see its docstring); that always means an unverified reload here,
        # never an unhandled crash out of an admission dependency.
        connection = GameConnection()
        await connection.connect()
        try:
            try:
                verified = await reload_position(connection, position)
                reload_raw: str | None = None
            except RuntimeError as exc:
                verified = False
                reload_raw = str(exc)
            popup_status = await dismiss_blocking_popups(connection)
            popup_ok = isinstance(popup_status, str) and popup_status.startswith("POPUPS|")
            canonical_state = await capture_canonical_state(
                connection, position.player_id, position.relevant_tiles
            )
            return {
                "reload": {"verified": verified, "raw": reload_raw},
                "popup_hygiene": {"status": popup_status, "ok": popup_ok},
                "canonical_state": canonical_state,
            }
        finally:
            await connection.disconnect()

    def gpu_evidence(endpoint_id: str) -> dict:
        # RULING (finding 3, final review): the requested endpoint's own
        # registry-declared units are always legitimately present on its
        # own GPU -- but `check_gpu_conflicts`'s `approved_services` is an
        # EXACT set match (over_approved fails too), designed for an
        # operator explicitly acknowledging a *currently observed*
        # conflict, not for a static allowance. Feeding it a static
        # `set(endpoint.units)` made an idle GPU (observed set = {}) always
        # fail against approved={declared unit} -- GateFailure on a clean
        # host every single time. Instead: filter the endpoint's own
        # declared units OUT of the observed conflict rows first (shared
        # with `drain_gpu_service`'s own remediation checks -- see B6,
        # external review wave B, `partition_own_endpoint_processes`), then
        # let `check_gpu_conflicts` apply its exact-match logic to whatever
        # foreign/unidentified rows remain, with an EMPTY approval set.
        # Declared-unit-resident and declared-unit-absent (idle) both leave
        # an empty remainder and pass; any other named service or any
        # unidentified process is never filtered and still blocks exactly
        # as `check_gpu_conflicts` already guarantees.
        registry = _registry()
        endpoint = registry.endpoint(endpoint_id)
        processes = collect_gpu_evidence(run_ssh=_run_ssh_command, registry=registry, endpoint_id=endpoint_id)
        remainder, filtered_own = partition_own_endpoint_processes(
            gpu_processes_to_conflict_rows(processes), endpoint.units
        )
        result = dict(check_gpu_conflicts(processes=remainder, approved_services=set()))
        # B6(b), external review wave B: the dropped own-unit rows must not
        # simply vanish -- recording them here makes a busy GPU (this
        # endpoint's own unit actually resident) distinguishable post-mortem
        # from a genuinely idle one, both of which otherwise produce the
        # identical "ok": True, empty-remainder evidence above.
        result["filtered_own_unit_rows"] = filtered_own
        return result

    def resolve_endpoint(endpoint_id: str) -> dict:
        # RULING: requested_endpoint is the registry EXPECTATION for this
        # campaign-declared endpoint_id -- resolved from the vendored
        # snapshot (_vendor/endpoints.json, the registry state pinned at
        # this checkout's authoring time), never from the same live call
        # `resolved_endpoint` uses. resolved_endpoint is what the live
        # registry/probe actually yields RIGHT NOW. Computing both from one
        # shared call (the previous bug) made this gate a tautology -- it
        # could never fire even on a genuine registry drift (an endpoint
        # moved/rerouted since the snapshot was vendored). With two
        # independent resolutions, a real drift shows up as a real
        # mismatch; the common, no-drift case still has them agree.
        requested_url = resolve_gateway(endpoint_id, snapshot_only=True)
        registry = _registry()
        endpoint = registry.endpoint(endpoint_id)
        resolved_url = resolve_gateway(endpoint_id)
        registry_fingerprint = fingerprint(
            {
                "endpoint_id": endpoint_id,
                "host_id": endpoint.host_id,
                "gpu_indexes": list(endpoint.gpu_indexes),
                "port": endpoint.port,
            }
        )
        return {
            "requested_endpoint": requested_url,
            "resolved_endpoint": resolved_url,
            "registry_fingerprint": registry_fingerprint,
            "gpu_topology": {"host_id": endpoint.host_id, "gpu_indexes": list(endpoint.gpu_indexes)},
        }

    async def probe_backend_dep(
        *,
        model: str,
        endpoint: str,
        sampling,
        chat_template_kwargs: Mapping[str, object] | None = None,
        tools: list[dict],
    ):
        backend = OpenAICompatBackend(
            endpoint,
            api_key,
            model,
            sampling=sampling,
            retry_policy=RetryPolicy(max_attempts=1),
            chat_template_kwargs=chat_template_kwargs,
        )
        probe_messages = [
            {"role": "system", "content": BENCHMARK_SYSTEM},
            {
                "role": "user",
                "content": user_prompt if user_prompt is not None else "Reply with only your model name.",
            },
        ]
        try:
            probe = await _probe_backend_impl(backend, probe_messages, tools)
            # B4 (external review wave B): fold the served /v1/models
            # listing -- already reachable cheaply off this same live
            # backend the probe just used -- into the returned probe as
            # supplementary identity evidence. Best-effort: list_model_ids
            # never raises, so a server without /v1/models support simply
            # yields an empty tuple rather than failing the probe.
            served_model_ids = await backend.list_model_ids()
            return dataclasses.replace(probe, served_model_ids=served_model_ids)
        finally:
            await backend.aclose()

    async def probe_tool_capability_dep(
        *,
        model: str,
        endpoint: str,
        arm_id: str,
        tools: list[dict],
        sampling=None,
        chat_template_kwargs: Mapping[str, object] | None = None,
    ):
        backend = OpenAICompatBackend(
            endpoint,
            api_key,
            model,
            sampling=sampling,
            retry_policy=RetryPolicy(max_attempts=1),
            chat_template_kwargs=chat_template_kwargs,
        )
        try:
            return await _probe_tool_capability_impl(
                backend, arm_id=arm_id, tools=tools, system_prompt=BENCHMARK_SYSTEM
            )
        finally:
            await backend.aclose()

    return benchmark_admission.AdmissionDependencies(
        checkout_evidence=checkout_evidence,
        boot_health=boot_health,
        tuner_evidence=tuner_evidence,
        deploy_save=deploy_save,
        reload_and_capture=reload_and_capture,
        gpu_evidence=gpu_evidence,
        resolve_endpoint=resolve_endpoint,
        probe_backend=probe_backend_dep,
        probe_tool_capability=probe_tool_capability_dep,
    )


def _build_admission_pipeline(args: argparse.Namespace, api_key: str, *, user_prompt: str | None = None):
    """Factory seam for `_run_campaign_async` -- monkeypatched by tests to
    inject a fake `AdmissionPipeline` instead of the real live-dependency
    one `_build_live_admission_dependencies` assembles.

    `user_prompt` (B3, external review wave B) is threaded straight through
    to `_build_live_admission_dependencies` -- see its docstring."""
    from civ_mcp.arena import benchmark_admission

    return benchmark_admission.AdmissionPipeline(
        _build_live_admission_dependencies(args=args, api_key=api_key, user_prompt=user_prompt)
    )


def _resolve_campaign_position_path(campaign_path: Path, position_id: str) -> Path:
    # Convention mirrors _resolve_position_path: a campaign at
    # <benchmarks>/campaigns/<name>.yaml pairs with position manifests at
    # <benchmarks>/positions/<position_id>.yaml.
    return campaign_path.resolve().parent.parent / "positions" / f"{position_id}.yaml"


class _CampaignContext:
    """Everything `_run_campaign_async` and `_run_remediation_async` share:
    the loaded campaign/position, the resolved tool bundle, and the opened
    `CampaignStore`. Factored out so a remediation-only invocation
    (`--terminate-tuner-pid`/`--drain-gpu-service` alongside `--campaign`)
    can also record its attempts against the right block (see
    `benchmark_admission.record_remediation_attempt`)."""

    def __init__(self, campaign, position, bundle, store) -> None:
        self.campaign = campaign
        self.position = position
        self.bundle = bundle
        self.store = store


async def _load_campaign_context(args: argparse.Namespace) -> "_CampaignContext | None":
    """Load + validate the campaign/position manifests, build the campaign
    lock, and open/create the `CampaignStore`. Returns `None` (after
    printing the error) on any failure -- the caller just needs to know
    whether it got a usable context, not which of several distinct load
    steps failed."""
    from civ_mcp.arena import benchmark_admission
    from civ_mcp.arena.benchmark_agent import BENCHMARK_SYSTEM
    from civ_mcp.arena.benchmark_campaign import (
        CampaignLockMismatchError,
        CampaignStore,
        build_campaign_lock,
        compile_campaign_schedule,
    )
    from civ_mcp.arena.benchmark_contract import (
        fingerprint_identity,
        load_campaign_manifest,
        tool_surface_identity,
    )

    campaign_path = Path(args.campaign)
    try:
        campaign = load_campaign_manifest(campaign_path)
    except (OSError, ValueError) as exc:
        print(f"civ-arena-benchmark: failed to load campaign {campaign_path}: {exc}", file=sys.stderr)
        return None

    position_path = _resolve_campaign_position_path(campaign_path, campaign.position)
    try:
        position = load_position_manifest(position_path)
    except (OSError, ValueError) as exc:
        print(f"civ-arena-benchmark: failed to load position manifest {position_path}: {exc}", file=sys.stderr)
        return None

    try:
        verify_expected_state_digest(position.expected_state, position.expected_state_sha256)
    except BenchmarkStateError as exc:
        print(f"civ-arena-benchmark: {exc}", file=sys.stderr)
        return None

    provenance = json.loads(Path(campaign.position_provenance).read_text())
    tools_by_arm = {arm.arm_id: resolved_benchmark_tools(arm.tools) for arm in campaign.arms}
    schedule = compile_campaign_schedule(campaign)
    wsl_repo = args.wsl_repo or str(Path(__file__).resolve().parents[3])
    try:
        campaign_lock = build_campaign_lock(
            campaign=campaign,
            position=position,
            position_provenance=provenance,
            schedule=schedule,
            expected_commit=_git_rev_parse_head(wsl_repo),
            prompt_fingerprint=fingerprint({"system": BENCHMARK_SYSTEM, "user": campaign.prompt}),
            rubric_fingerprint=fingerprint(list(position.rubric)),
            tool_surface_fingerprint=fingerprint_identity(tool_surface_identity(tools_by_arm)),
        )
    except GateFailure as exc:
        print(f"civ-arena-benchmark: campaign lock rejected: {exc}", file=sys.stderr)
        return None

    run_dir = Path(args.run_dir) / args.run_id
    try:
        store = CampaignStore.create(run_dir, campaign_lock, schedule)
    except CampaignLockMismatchError as exc:
        print(f"civ-arena-benchmark: {exc}", file=sys.stderr)
        return None

    bundle = benchmark_admission.build_campaign_bundle(campaign, position)
    return _CampaignContext(campaign, position, bundle, store)


async def _run_campaign_async(args: argparse.Namespace) -> int:
    from civ_mcp.arena import benchmark_admission

    # D3 (external review wave D): a counted --campaign run (and the
    # admit_only/validation paths sharing this pipeline) must never fall
    # back to the placeholder api key "x" -- against a real endpoint the
    # placeholder's 401s surface as backend_probe_errors, which is a
    # deferral-ELIGIBLE model-capability code under Ruling G, so a missing
    # credential could masquerade as a corroborable model failure. Checked
    # before the campaign context is even loaded, so no store/pipeline is
    # ever constructed on a credentials error. (--ungated-smoke keeps its
    # loud placeholder-warning behavior -- a local gateway needs no key,
    # and smoke evidence is never counted.)
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        print(
            f"civ-arena-benchmark: {args.api_key_env} is unset or empty; a --campaign run "
            "requires real credentials and never falls back to a placeholder api key "
            "(placeholder-driven 401s would be misclassified as backend_probe_errors, a "
            f"deferral-eligible failure code). Export {args.api_key_env} and retry.",
            file=sys.stderr,
        )
        return 1

    context = await _load_campaign_context(args)
    if context is None:
        return 1
    campaign, store, bundle = context.campaign, context.store, context.bundle

    pipeline = _build_admission_pipeline(args, api_key, user_prompt=campaign.prompt)

    blocks_by_id = {block.block_id: block for block in campaign.models}

    if args.admit_only is not None:
        return await _run_single_diagnostic(pipeline, bundle, blocks_by_id, args.admit_only, store, mode="admit_only")

    if args.non_counting_validation is not None:
        return await _run_non_counting_validation(
            pipeline, bundle, blocks_by_id, args.non_counting_validation, store, campaign, api_key
        )

    first_block_id = campaign.models[0].block_id

    while True:
        next_block = benchmark_admission.select_next_incomplete_block(campaign, store)
        if next_block is None:
            print("civ-arena-benchmark: campaign already complete", file=sys.stderr)
            return 0

        block_index = campaign.models.index(next_block)
        try:
            resolved = await pipeline.admit(bundle, next_block, store, mode="counted", api_key=api_key)
        except benchmark_admission.AdmissionError as exc:
            first_complete = benchmark_admission.block_is_complete(store, first_block_id)
            disposition = benchmark_admission.classify_admission_disposition(
                block_index=block_index, first_block_counted_complete=first_complete
            )
            # D2 (external review wave D, Ruling G) -- supersedes finding 4
            # (final review), part (b): deferral eligibility is an explicit
            # ALLOWLIST of model-capability gate failure codes
            # (benchmark_admission.REPLICATION_DEFERRAL_ELIGIBLE_CODES:
            # endpoint/model-identity, tool-canary, treatment-can-fire, and
            # backend-probe codes). Everything else -- the catch-all
            # unexpected_admission_error ("Unknown failures cannot be
            # converted into a deferral.") AND every classified operator-
            # error code (dirty_checkout, tuner-holder codes, GPU
            # conflicts, boot/deploy/reload/popup/canonical-state codes)
            # -- must never be written as a REPLICATION_DEFERRED_ADMISSION
            # disposition: an operator/environment failure is fixed and
            # retried, never deferred around. This is a necessary condition
            # at write time; the reporter separately enforces the same
            # allowlist AND requires on-disk corroboration (a successful
            # remediation followed by a same-code retry, or a second
            # same-code failure) before honoring even an eligible
            # disposition record -- see
            # benchmark_campaign_report._has_valid_replication_deferred_admission.
            if (
                disposition is not None
                and exc.code not in benchmark_admission.REPLICATION_DEFERRAL_ELIGIBLE_CODES
            ):
                print(
                    f"civ-arena-benchmark: block {next_block.block_id} admission failed "
                    f"({exc.code}); refusing to record a {disposition} disposition -- "
                    f"{exc.code!r} is not a model-capability gate failure code eligible "
                    "for replication deferral (Ruling G); fix the underlying "
                    "operator/environment problem and retry admission",
                    file=sys.stderr,
                )
                return 1
            if disposition is not None:
                # The admission artifact itself must expose the typed
                # disposition -- admit() already wrote its own failed-
                # attempt record; this is a SEPARATE, subsequent numbered
                # attempt record for the same block carrying the
                # disposition and a reference to the failure that produced
                # it, so scanning admissions/<block-id>-attempt-*.json
                # reconstructs the full story without out-of-band state.
                benchmark_admission.record_admission_disposition(
                    store, next_block.block_id, disposition, {"code": exc.code, "details": exc.details}
                )
                print(
                    f"civ-arena-benchmark: block {next_block.block_id} admission failed "
                    f"({exc.code}); recording disposition={disposition} and stopping "
                    "(final campaign disposition is decided elsewhere)",
                    file=sys.stderr,
                )
                return 2
            print(
                f"civ-arena-benchmark: block {next_block.block_id} admission failed: {exc.code}: {exc}",
                file=sys.stderr,
            )
            return 1

        exit_code = await run_resolved_block(resolved)
        if exit_code != 0:
            return exit_code

        if args.one_block:
            return 0


async def _run_single_diagnostic(pipeline, bundle, blocks_by_id, block_id, store, *, mode: str) -> int:
    from civ_mcp.arena import benchmark_admission

    block = blocks_by_id.get(block_id)
    if block is None:
        print(
            f"civ-arena-benchmark: unknown block id {block_id!r}; known blocks: "
            f"{sorted(blocks_by_id)}",
            file=sys.stderr,
        )
        return 1
    try:
        await pipeline.admit(bundle, block, store, mode=mode)
    except benchmark_admission.AdmissionError as exc:
        print(f"civ-arena-benchmark: {mode} for block {block_id} failed: {exc.code}: {exc}", file=sys.stderr)
        return 1
    print(f"civ-arena-benchmark: {mode} for block {block_id} succeeded")
    return 0


def _write_validation_schedule(validation_dir: Path, schedule_payload: Mapping[str, object]) -> None:
    """Write `<validation_dir>/schedule.json` -- the sibling file
    `benchmark_report.build_report` cross-checks against a lock's own
    `schedule_fingerprint` (see G11). Mirrors
    `CampaignStore.open_block`'s own schedule.json convention (verify
    byte-for-byte if it already exists, otherwise fsync-write it) since
    `BenchmarkStore.create` itself never manages this file."""
    schedule_path = validation_dir / "schedule.json"
    provided = json.dumps(schedule_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    if schedule_path.exists():
        if schedule_path.read_bytes() != provided:
            raise BenchmarkStoreError(
                f"validation schedule mismatch at {schedule_path}: provided schedule does "
                "not match the recorded schedule byte-for-byte"
            )
        return
    validation_dir.mkdir(parents=True, exist_ok=True)
    with open(schedule_path, "wb") as fh:
        fh.write(provided)
        fh.flush()
        os.fsync(fh.fileno())


async def _run_non_counting_validation(pipeline, bundle, blocks_by_id, block_id, store, campaign, api_key) -> int:
    """B2 (external review wave B): `--non-counting-validation` must not
    stop at admission -- spec Task-12 requires "one complete non-counting
    episode per arm" actually running through the trusted
    `run_resolved_block`, written under `<campaign root>/validation/
    <block_id>/`, never under `blocks/`, and never carrying a counted
    campaign/session fingerprint pair.

    On a successful validation admission, this assembles a validation-
    scoped `ResolvedBlock` (its own `BenchmarkStore`, built directly rather
    than through `CampaignStore.open_block` -- that helper is BLOCKS_DIR-
    scoped and requires a real `campaign_fingerprint` match, neither of
    which a non-counting validation lock has) covering exactly the
    campaign's already-compiled first pair (one seed, one trial per arm),
    runs it, and generates a per-block report into the same validation
    directory via the existing report machinery -- marked non-counting via
    the lock's own `validation: true` stamp (see benchmark_report.py).

    Never touches `BenchmarkRunner.run`/`run_trial`/the retry taxonomy --
    the episode itself runs through the same `run_resolved_block` a counted
    block uses, unmodified.
    """
    from civ_mcp.arena import benchmark_admission
    from civ_mcp.arena.benchmark_contract import suite_for_block
    from civ_mcp.arena.benchmark_report import write_reports

    block = blocks_by_id.get(block_id)
    if block is None:
        print(
            f"civ-arena-benchmark: unknown block id {block_id!r}; known blocks: "
            f"{sorted(blocks_by_id)}",
            file=sys.stderr,
        )
        return 1

    try:
        evidence = await pipeline.admit(bundle, block, store, mode="validation")
    except benchmark_admission.AdmissionError as exc:
        print(f"civ-arena-benchmark: validation for block {block_id} failed: {exc.code}: {exc}", file=sys.stderr)
        return 1

    model_admission = dict((evidence.get("gates") or {}).get("model_admission") or {})
    gateway_url = model_admission.get("resolved_endpoint")
    episode_wall_s = model_admission.get("episode_wall_s")
    if not gateway_url or not episode_wall_s:
        print(
            f"civ-arena-benchmark: validation for block {block_id} admitted but its evidence "
            "is missing resolved_endpoint/episode_wall_s; refusing to run a validation "
            "episode without a resolved backend",
            file=sys.stderr,
        )
        return 1

    block_schedule = store.schedule["blocks"][block.block_id]["trials"]
    if not block_schedule:
        print(f"civ-arena-benchmark: block {block_id} has an empty schedule; nothing to validate", file=sys.stderr)
        return 1
    # "One trial per arm (first seed, one pair)" -- the campaign's own
    # already-compiled ABBA schedule's first pair_id, taken verbatim (never
    # re-derived or reordered) so the validation episode exercises the same
    # arm order the counted run will actually use.
    first_pair_id = block_schedule[0]["pair_id"]
    first_pair = [dict(trial) for trial in block_schedule if trial["pair_id"] == first_pair_id]
    schedule = tuple(TrialSpec(**trial) for trial in first_pair)

    validation_dir = store.root / "validation" / block.block_id
    schedule_payload = {"trials": [dataclasses.asdict(trial) for trial in schedule]}
    validation_lock: dict[str, object] = {
        # Deliberately NOT "ungated_smoke" (Ruling B2): a validation episode
        # DID go through the full live admission gate pipeline; smoke never
        # does. A distinct, never-reused stamp so this can never be
        # confused with -- or promoted into -- either shape of evidence.
        "validation": True,
        "block_id": block.block_id,
        "position_id": bundle.position.position_id,
        "scorer_fingerprint": campaign.contracts.scorer_fingerprint,
        "schedule_fingerprint": fingerprint(schedule_payload),
        "positions": {
            bundle.position.position_id: {
                "rubric": list(bundle.position.rubric),
                "objectives": list(bundle.position.objectives),
            }
        },
    }
    # No campaign_fingerprint field at all (never merely `None`): this lock
    # must never resemble a counted block's lock, and BenchmarkStore only
    # demands a trial's own campaign_fingerprint stamp when
    # `self.campaign_fingerprint` is truthy -- absent here by construction.
    validation_lock["session_fingerprint"] = fingerprint(validation_lock)

    validation_store = BenchmarkStore.create(validation_dir, validation_lock)
    _write_validation_schedule(validation_dir, schedule_payload)

    resolved = ResolvedBlock(
        position=bundle.position,
        suite=suite_for_block(campaign, block),
        schedule=schedule,
        store=validation_store,
        gateway_url=str(gateway_url),
        api_key=api_key,
        episode_wall_s=int(episode_wall_s),
        chat_template_kwargs=dict(block.chat_template_kwargs),
        user_prompt=campaign.prompt,
    )

    exit_code = await run_resolved_block(resolved)
    if exit_code != 0:
        print(
            f"civ-arena-benchmark: validation episode for block {block_id} failed "
            f"(exit {exit_code})",
            file=sys.stderr,
        )
        return exit_code

    write_reports(validation_dir)
    print(
        f"civ-arena-benchmark: validation for block {block_id} succeeded; "
        f"report written to {validation_dir}"
    )
    return 0


async def _run_remediation_async(args: argparse.Namespace) -> int:
    """Standalone, one-shot remediation: `--terminate-tuner-pid` and/or
    `--drain-gpu-service`. Maps to Task 7's scoped functions only -- never
    constructs a wildcard/pattern kill or stop command itself (see
    benchmark_live_evidence.terminate_tuner_pid / drain_gpu_service for the
    exact-identity revalidation / re-check-after contract).

    When `--campaign` is also given, each remediation invocation's result
    is additionally recorded as its own numbered admission-attempt record
    against the campaign's next incomplete block (see
    `benchmark_admission.record_remediation_attempt`), so "all remediation
    attempts" for that block is reconstructible from disk. Without
    `--campaign` there is no block to link the attempt to -- the action
    still runs (never silently skipped), but only its stdout/stderr record
    survives.
    """
    from civ_mcp.arena.benchmark_live_evidence import (
        classify_tuner_holder,
        collect_gpu_evidence,
        drain_gpu_service,
        terminate_tuner_pid,
    )
    from civ_mcp.arena.endpoint_registry import _registry

    wsl_repo = args.wsl_repo or str(Path(__file__).resolve().parents[3])
    windows_repo = args.windows_repo or ""
    exit_code = 0

    campaign_context = await _load_campaign_context(args) if args.campaign else None
    linked_block_id: str | None = None
    if campaign_context is not None:
        from civ_mcp.arena import benchmark_admission

        next_block = benchmark_admission.select_next_incomplete_block(
            campaign_context.campaign, campaign_context.store
        )
        linked_block_id = next_block.block_id if next_block is not None else None

    def _record(action: str, result: Mapping[str, object]) -> None:
        if campaign_context is not None and linked_block_id is not None:
            from civ_mcp.arena import benchmark_admission

            benchmark_admission.record_remediation_attempt(
                campaign_context.store, linked_block_id, action, result
            )

    if args.terminate_tuner_pid is not None:
        holder = classify_tuner_holder(run_local=_run_local_command, wsl_repo=wsl_repo, windows_repo=windows_repo)
        if holder is None:
            print("civ-arena-benchmark: FireTuner port has no holder; nothing to terminate", file=sys.stderr)
            _record("terminate_tuner_pid", {"ok": False, "reason": "no_holder"})
            exit_code = 1
        else:
            try:
                result = terminate_tuner_pid(
                    run_local=_run_local_command,
                    requested_pid=args.terminate_tuner_pid,
                    preceding_evidence=holder,
                    wsl_repo=wsl_repo,
                    windows_repo=windows_repo,
                )
                print(f"civ-arena-benchmark: {result}")
                _record("terminate_tuner_pid", result)
            except GateFailure as exc:
                print(f"civ-arena-benchmark: terminate-tuner-pid refused: {exc}", file=sys.stderr)
                _record("terminate_tuner_pid", {"ok": False, "code": exc.code, "details": dict(exc.details)})
                exit_code = 1

    for unit in args.drain_gpu_service:
        if not args.endpoint_id:
            print("civ-arena-benchmark: --drain-gpu-service requires --endpoint-id", file=sys.stderr)
            exit_code = 1
            continue
        registry = _registry()
        processes = collect_gpu_evidence(run_ssh=_run_ssh_command, registry=registry, endpoint_id=args.endpoint_id)
        try:
            result = drain_gpu_service(
                run_ssh=_run_ssh_command, registry=registry, endpoint_id=args.endpoint_id, processes=processes, unit=unit
            )
            print(f"civ-arena-benchmark: {result}")
            _record(f"drain_gpu_service:{unit}", result)
        except GateFailure as exc:
            print(f"civ-arena-benchmark: drain-gpu-service {unit!r} refused: {exc}", file=sys.stderr)
            _record(f"drain_gpu_service:{unit}", {"ok": False, "code": exc.code, "details": dict(exc.details)})
            exit_code = 1

    return exit_code


async def _run_async(args: argparse.Namespace) -> int:
    # Remediation flags are standalone, one-shot actions -- checked first
    # and exclusively: they never combine with a --campaign/--suite run in
    # the same invocation (run remediation, then a separate invocation to
    # admit/run once the port/GPU is clear).
    if args.terminate_tuner_pid is not None or args.drain_gpu_service:
        return await _run_remediation_async(args)

    if args.campaign and args.suite:
        print("civ-arena-benchmark: --campaign and --suite are mutually exclusive", file=sys.stderr)
        return 1

    if args.campaign:
        return await _run_campaign_async(args)

    if not args.suite:
        print(
            "civ-arena-benchmark: one of --campaign or --suite is required (--suite is "
            "only for --ungated-smoke)",
            file=sys.stderr,
        )
        return 1

    # Fail-closed: this --suite path never runs the live admission gate
    # pipeline (check_clean_checkout / check_gpu_conflicts / admit_model_block /
    # build_session_lock) -- a counted run goes through --campaign, which
    # wires those gates via benchmark_admission.AdmissionPipeline.
    # `--ungated-smoke` is the one deliberate override for --suite, for
    # non-counted smoke testing only; it stamps `ungated_smoke: True` into
    # the session lock so the artifact itself carries the mark for a future
    # scorer/report to refuse or flag.
    if not args.ungated_smoke:
        print(
            "civ-arena-benchmark: refusing to run -- --suite has no admission gate "
            "pipeline (check_clean_checkout / check_gpu_conflicts / admit_model_block / "
            "build_session_lock); use --campaign for a counted, admission-gated run. "
            "Pass --ungated-smoke to run an explicitly non-counted smoke session over "
            "--suite instead (its evidence is stamped ungated_smoke=true and must never "
            "be scored as a counted run).",
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

    # G10: verify the manifest's declared expected_state_sha256 integrity
    # anchor actually matches its expected_state, before any trial runs --
    # not just loaded and never read again.
    try:
        verify_expected_state_digest(position.expected_state, position.expected_state_sha256)
    except BenchmarkStateError as exc:
        print(f"civ-arena-benchmark: {exc}", file=sys.stderr)
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
            json.dumps(schedule_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        # Cheap fold-in: on resume, verify the file on disk still matches
        # the session lock's schedule_fingerprint. BenchmarkStore.create
        # already verifies session.json byte-for-byte, but schedule.json is
        # a separate file with no such check -- one corrupted or partially
        # written by a prior crash would otherwise be silently trusted.
        try:
            existing_schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
            existing_fingerprint = fingerprint(existing_schedule)
        except (OSError, ValueError) as exc:
            print(
                f"civ-arena-benchmark: failed to read/verify {schedule_path}: {exc}",
                file=sys.stderr,
            )
            return 1
        if existing_fingerprint != lock["schedule_fingerprint"]:
            print(
                f"civ-arena-benchmark: {schedule_path} does not match the session "
                f"lock's schedule_fingerprint (expected {lock['schedule_fingerprint']!r}, "
                f"found {existing_fingerprint!r}); refusing to resume against "
                "mismatched schedule evidence",
                file=sys.stderr,
            )
            return 1

    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        # G6: do NOT refuse to start -- local gateways need no key, and
        # probe_backend already fails closed on a bad key against a real
        # endpoint. Just make the placeholder visible so it's never a
        # silent surprise when a remote endpoint later fails the admission
        # probe.
        print(
            f"civ-arena-benchmark: warning: {args.api_key_env} is unset; using "
            'placeholder api key "x" (fine for a local gateway with no auth; '
            "a remote endpoint will fail the admission probe)",
            file=sys.stderr,
        )
        api_key = "x"

    # This is the whole non-counted smoke assembly: connect / build deps /
    # construct BenchmarkRunner / run / close every resource on every exit
    # path all live in run_resolved_block now (see G5's cleanup contract,
    # preserved there unchanged). episode_wall_s / chat_template_kwargs /
    # user_prompt are fixed literals matching this scaffold's pre-existing
    # hardcoded smoke behavior exactly -- no admission gate ever ran to
    # produce real evidence for them (see the fail-closed guard above).
    block = ResolvedBlock(
        position=position,
        suite=suite,
        schedule=schedule,
        store=store,
        gateway_url=args.gateway_url,
        api_key=api_key,
        episode_wall_s=300,
        chat_template_kwargs={"enable_thinking": False},
        user_prompt="",
    )
    return await run_resolved_block(block)


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
