"""Live evidence collectors and exact-scope remediation for the counted
benchmark admission pipeline.

Every collector here shells out through an *injected* runner
(`run_local`, `run_windows`, or `run_ssh`) so tests can fake the exact
command output byte-for-byte and never touch a real subprocess, socket, or
SSH connection. What each collector returns is JSON-safe evidence --
`CommandResult`/`TunerHolder`/`GpuProcess` are plain frozen dataclasses,
trivially converted with `dataclasses.asdict` -- consumed either directly
by a caller or by the existing pure fail-closed gates in `benchmark_gates`
(`check_tuner_holder`, `check_gpu_conflicts`).

Background: a stale `civ-mcp` process has previously silently wedged the
FireTuner port (4318, single-client) -- an admission run that connects
without checking first can spend its whole session talking to a dead
game. `classify_tuner_holder`/`check_tuner_holder` exist because of that
incident; `terminate_tuner_pid` is the *only* sanctioned way to clear it.

Two non-negotiable safety rules, both enforced here (never left to a
caller to remember):

- An unknown tuner-port holder or an unidentified GPU-resident process
  ALWAYS blocks. Neither has a remediation path -- `check_tuner_holder`
  and `check_gpu_conflicts` both raise `GateFailure` rather than letting a
  caller "acknowledge" its way past an unidentified process.
- Every termination/drain here is exact-target only. `terminate_tuner_pid`
  sends `SIGTERM` to a single PID after revalidating its full identity
  (pid, start ticks, cmdline, cwd) against the immediately preceding
  evidence, waits a bounded interval, then re-runs the port-holder check;
  survival or an identity change blocks. `drain_gpu_service` stops exactly
  one registry-managed systemd unit and re-snapshots; unknown processes or
  remaining conflicts block. Neither function ever constructs a `pkill`, a
  wildcard, or a bare process-name kill/stop command -- every remote
  command argv is fully enumerated ahead of time from evidence already
  gathered, never synthesized from a pattern.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Sequence

from civ_mcp.arena.benchmark_gates import GateFailure, check_gpu_conflicts, check_tuner_holder

__all__ = [
    "CommandResult",
    "TunerHolder",
    "GpuProcess",
    "collect_checkout_evidence",
    "classify_tuner_holder",
    "collect_tuner_evidence",
    "terminate_tuner_pid",
    "collect_gpu_evidence",
    "gpu_processes_to_conflict_rows",
    "drain_gpu_service",
]

# FireTuner's single-client listen port (see civ_mcp.game_launcher._TUNER_PORT).
_TUNER_PORT = 4318

# Markers expected in the cmdline of a genuine civ-mcp server process --
# see pyproject.toml's `[project.scripts]` (`civ-mcp = "civ_mcp.server:main"`)
# and the module actually invoked when run directly.
_EXPECTED_CMDLINE_MARKERS = ("civ-mcp", "civ_mcp.server")

_SERVICE_UNIT_RE = re.compile(r"([\w@.\-]+\.service)")


@dataclass(frozen=True)
class CommandResult:
    """The output of one injected command invocation.

    `argv` is recorded on the result (not just passed in separately) so a
    caller logging evidence can always tell exactly what was run without
    needing to also thread the original argv through by hand.
    """

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class TunerHolder:
    """Identity evidence for whoever currently holds the FireTuner port."""

    pid: int
    start_ticks: int
    cmdline: str
    cwd: str
    known_repo_owned: bool


@dataclass(frozen=True)
class GpuProcess:
    """One GPU-resident compute process, scoped to a single endpoint's GPU(s)."""

    host: str
    gpu_index: int
    gpu_uuid: str
    pid: int
    process_name: str
    service: str | None


# Injected runner signatures. `run_local`/`run_windows` take a single argv;
# `run_ssh` also takes the target host (there is exactly one registry, but
# many hosts).
RunLocal = Callable[[Sequence[str]], CommandResult]
RunWindows = Callable[[Sequence[str]], CommandResult]
RunSsh = Callable[[str, Sequence[str]], CommandResult]


# ---------------------------------------------------------------------------
# checkout evidence
# ---------------------------------------------------------------------------


def _git_side_evidence(run: RunLocal | RunWindows, repo: str) -> dict[str, object]:
    commit_result = run(("git", "-C", repo, "rev-parse", "HEAD"))
    status_result = run(("git", "-C", repo, "status", "--porcelain=v1"))

    # A nonzero rev-parse is recorded as an empty commit -- never stale or
    # garbage stdout -- so check_clean_checkout's missing_commit fail-closed
    # path catches it exactly like a genuinely absent commit would.
    commit = commit_result.stdout.strip() if commit_result.returncode == 0 else ""

    # A failed status query must never be read as "clean" -- fail closed by
    # recording a synthetic nonempty status so check_clean_checkout's
    # dirty_checkout path catches it instead of silently admitting a
    # checkout whose hygiene was never actually verified.
    if status_result.returncode == 0:
        status = status_result.stdout
    else:
        status = status_result.stderr or f"git status failed (exit {status_result.returncode})"

    return {"commit": commit, "status": status}


def collect_checkout_evidence(
    *,
    run_local: RunLocal,
    run_windows: RunWindows,
    wsl_repo: str,
    windows_repo: str,
) -> dict[str, dict[str, object]]:
    """Gather `check_clean_checkout`-shaped evidence from both checkouts.

    Returns ``{"wsl": {...}, "windows": {...}}`` -- pass straight through
    as ``check_clean_checkout(**collect_checkout_evidence(...))``.
    """
    return {
        "wsl": _git_side_evidence(run_local, wsl_repo),
        "windows": _git_side_evidence(run_windows, windows_repo),
    }


# ---------------------------------------------------------------------------
# FireTuner-holder classification and scoped remediation
# ---------------------------------------------------------------------------


def _find_port_holder_pid(run_local: RunLocal, port: int) -> int | None:
    """Parse `ss -H -tlnp sport = :<port>` for the PID of the process
    holding the FireTuner listen socket.

    Returns `None` when nothing is listening on the port -- never a guess,
    and never a fallback to some other identification method.
    """
    result = run_local(("ss", "-H", "-tlnp", f"sport = :{port}"))
    if result.returncode != 0:
        return None
    match = re.search(r"pid=(\d+)", result.stdout)
    return int(match.group(1)) if match else None


def _parse_start_ticks(stat_text: str) -> int | None:
    """Parse field 22 (`starttime`, clock ticks since boot) out of a raw
    `/proc/<pid>/stat` line.

    The `comm` field (field 2) can itself contain spaces and parentheses,
    so this splits on the LAST `)` in the line rather than on whitespace,
    matching the documented `proc(5)` parsing convention.
    """
    text = stat_text.strip()
    close = text.rfind(")")
    if close == -1:
        return None
    fields_after_comm = text[close + 1 :].split()
    # fields_after_comm[0] is field 3 (state); field 22 (starttime) is at
    # offset 22 - 3 = 19.
    if len(fields_after_comm) <= 19:
        return None
    try:
        return int(fields_after_comm[19])
    except ValueError:
        return None


def classify_tuner_holder(
    *,
    run_local: RunLocal,
    wsl_repo: str,
    windows_repo: str,
    port: int = _TUNER_PORT,
) -> TunerHolder | None:
    """Identify whoever currently holds the FireTuner listen port, if
    anyone.

    A holder is `known_repo_owned` only when its cmdline names the
    expected civ-mcp executable AND its cwd is exactly one of the two
    known repo checkouts (`wsl_repo`/`windows_repo`) -- anything else
    (including an unreadable `/proc` entry for a PID that vanished between
    the socket lookup and the read) is unknown.

    Returns `None` when nothing holds the port. Purely a read-only
    classification -- never issues a termination command of any kind.
    """
    pid = _find_port_holder_pid(run_local, port)
    if pid is None:
        return None

    stat_result = run_local(("cat", f"/proc/{pid}/stat"))
    cmdline_result = run_local(("cat", f"/proc/{pid}/cmdline"))
    cwd_result = run_local(("readlink", "-f", f"/proc/{pid}/cwd"))

    start_ticks = _parse_start_ticks(stat_result.stdout) if stat_result.returncode == 0 else None
    cmdline = (
        cmdline_result.stdout.replace("\x00", " ").strip()
        if cmdline_result.returncode == 0
        else ""
    )
    cwd = cwd_result.stdout.strip() if cwd_result.returncode == 0 else ""

    known_repo_owned = (
        start_ticks is not None
        and bool(cmdline)
        and any(marker in cmdline for marker in _EXPECTED_CMDLINE_MARKERS)
        and cwd in (wsl_repo, windows_repo)
    )
    return TunerHolder(
        pid=pid,
        # -1 is not a real starttime; it just keeps the field non-Optional
        # per the dataclass contract while still comparing unequal to any
        # later real reading, and known_repo_owned is already forced False
        # whenever start_ticks could not be read.
        start_ticks=start_ticks if start_ticks is not None else -1,
        cmdline=cmdline,
        cwd=cwd,
        known_repo_owned=bool(known_repo_owned),
    )


def collect_tuner_evidence(
    *,
    run_local: RunLocal,
    wsl_repo: str,
    windows_repo: str,
    port: int = _TUNER_PORT,
) -> dict[str, object]:
    """Classify the tuner-port holder and immediately run it through
    `check_tuner_holder` -- the one call an admission pipeline needs to
    both gather and fail-closed-check this evidence.

    Raises `GateFailure` (via `check_tuner_holder`) on an unknown holder;
    otherwise returns JSON-safe evidence including the resolved `port`.
    """
    holder = classify_tuner_holder(
        run_local=run_local, wsl_repo=wsl_repo, windows_repo=windows_repo, port=port
    )
    evidence = check_tuner_holder(holder=asdict(holder) if holder is not None else None)
    evidence["port"] = port
    return evidence


def terminate_tuner_pid(
    *,
    run_local: RunLocal,
    requested_pid: int,
    preceding_evidence: TunerHolder,
    wsl_repo: str,
    windows_repo: str,
    port: int = _TUNER_PORT,
    wait_attempts: int = 5,
    poll_interval_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Send `SIGTERM` to exactly `requested_pid` -- the only sanctioned way
    to clear a wedged FireTuner port -- and nothing else.

    Fails closed (`GateFailure`, no signal ever sent) when:

    - `requested_pid` does not match `preceding_evidence.pid` (the caller
      is asking to terminate a PID it never actually observed holding the
      port);
    - a fresh re-classification, run immediately before the signal, does
      not exactly match `preceding_evidence` on all four identity fields
      (pid, start_ticks, cmdline, cwd) -- this is what catches PID reuse:
      the same numeric PID now naming an unrelated process;
    - the (matched, fresh) holder is not `known_repo_owned` -- an unknown
      owner is never terminated even if it happens to match a stale PID.

    After sending `SIGTERM`, waits up to `wait_attempts * poll_interval_s`
    seconds, re-running `classify_tuner_holder` each attempt. Returns
    success only once the port shows no holder at all; if the PID survives
    or a different identity reappears, raises `GateFailure` instead of
    reporting an unverified termination.
    """
    if preceding_evidence.pid != requested_pid:
        raise GateFailure(
            "tuner_pid_mismatch",
            {
                "requested_pid": requested_pid,
                "preceding_pid": preceding_evidence.pid,
                "message": (
                    f"requested termination of pid {requested_pid} but the immediately "
                    f"preceding evidence names pid {preceding_evidence.pid}; refusing to "
                    "terminate a pid that was never actually observed holding the port"
                ),
            },
        )

    fresh = classify_tuner_holder(
        run_local=run_local, wsl_repo=wsl_repo, windows_repo=windows_repo, port=port
    )
    preceding_identity = (
        preceding_evidence.pid,
        preceding_evidence.start_ticks,
        preceding_evidence.cmdline,
        preceding_evidence.cwd,
    )
    fresh_identity = (
        (fresh.pid, fresh.start_ticks, fresh.cmdline, fresh.cwd) if fresh is not None else None
    )
    if fresh_identity != preceding_identity:
        raise GateFailure(
            "tuner_identity_changed_before_termination",
            {
                "requested_pid": requested_pid,
                "preceding": asdict(preceding_evidence),
                "fresh": asdict(fresh) if fresh is not None else None,
                "message": (
                    "tuner-holder identity changed (or the holder vanished) between the "
                    f"preceding evidence and the termination request for pid {requested_pid}; "
                    "refusing to send SIGTERM without a positively revalidated identity"
                ),
            },
        )

    # Defense in depth: even if the caller never separately ran
    # check_tuner_holder on preceding_evidence, this is the line that
    # actually sends a real signal -- an unknown owner must never be
    # terminated here either, matched identity or not.
    check_tuner_holder(holder=asdict(fresh))

    run_local(("kill", "-TERM", str(requested_pid)))

    survivor: TunerHolder | None = fresh
    for _ in range(wait_attempts):
        sleep(poll_interval_s)
        survivor = classify_tuner_holder(
            run_local=run_local, wsl_repo=wsl_repo, windows_repo=windows_repo, port=port
        )
        if survivor is None:
            return {"ok": True, "terminated_pid": requested_pid, "port": port}

    raise GateFailure(
        "tuner_termination_did_not_clear_port",
        {
            "requested_pid": requested_pid,
            "survivor": asdict(survivor) if survivor is not None else None,
            "message": (
                f"pid {requested_pid} was signaled with SIGTERM but the tuner port still has "
                f"a holder after {wait_attempts} check(s); refusing to report a termination "
                "that was not actually verified"
            ),
        },
    )


# ---------------------------------------------------------------------------
# GPU snapshot and named-service drain
# ---------------------------------------------------------------------------


def _service_from_cgroup(cgroup_text: str) -> str | None:
    match = _SERVICE_UNIT_RE.search(cgroup_text)
    return match.group(1) if match else None


def collect_gpu_evidence(
    *,
    run_ssh: RunSsh,
    registry: Any,
    endpoint_id: str,
) -> list[GpuProcess]:
    """Snapshot GPU-resident compute processes for exactly the GPU
    index(es) `endpoint_id` resolves to through the vendored registry --
    never every GPU on both hosts.

    Maps each reported `gpu_uuid` back to its `gpu_index` via a single
    `nvidia-smi --query-gpu` call, and each PID to a `service` name (or
    `None` when unidentified) by reading that PID's cgroup on the same
    host.
    """
    endpoint = registry.endpoint(endpoint_id)
    host = endpoint.host_id

    gpu_result = run_ssh(host, ("nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"))
    uuid_to_index: dict[str, int] = {}
    for line in gpu_result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        index_text, uuid = (part.strip() for part in line.split(",", 1))
        uuid_to_index[uuid] = int(index_text)

    proc_result = run_ssh(
        host,
        ("nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name", "--format=csv,noheader"),
    )
    processes: list[GpuProcess] = []
    for line in proc_result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        gpu_uuid, pid_text, process_name = (part.strip() for part in line.split(",", 2))
        gpu_index = uuid_to_index.get(gpu_uuid)
        if gpu_index is None or gpu_index not in endpoint.gpu_indexes:
            # Not one of this endpoint's GPUs -- irrelevant to this
            # endpoint's admission, never even reported.
            continue
        pid = int(pid_text)
        cgroup_result = run_ssh(host, ("cat", f"/proc/{pid}/cgroup"))
        service = (
            _service_from_cgroup(cgroup_result.stdout) if cgroup_result.returncode == 0 else None
        )
        processes.append(
            GpuProcess(
                host=host,
                gpu_index=gpu_index,
                gpu_uuid=gpu_uuid,
                pid=pid,
                process_name=process_name,
                service=service,
            )
        )
    return processes


def gpu_processes_to_conflict_rows(processes: Sequence[GpuProcess]) -> list[dict[str, object]]:
    """Convert `GpuProcess` evidence into the row shape `check_gpu_conflicts` expects."""
    return [
        {
            "pid": p.pid,
            "service": p.service,
            "gpu_index": p.gpu_index,
            "gpu_uuid": p.gpu_uuid,
            "process_name": p.process_name,
            "host": p.host,
        }
        for p in processes
    ]


def drain_gpu_service(
    *,
    run_ssh: RunSsh,
    registry: Any,
    endpoint_id: str,
    processes: Sequence[GpuProcess],
    unit: str,
) -> dict[str, object]:
    """Stop exactly one registry-managed systemd unit and re-snapshot.

    Legal only when `unit` is both declared by the registry for this
    endpoint AND the exact (and only) conflicting service observed in
    `processes` -- reuses `check_gpu_conflicts` itself for that check, so
    an unidentified process anywhere in `processes` blocks up front exactly
    like an unattended conflict report would, and a named-but-different
    service is never silently drained alongside the requested one.

    Never constructs a wildcard/pattern stop command -- `unit` is passed
    to `systemctl stop` verbatim and alone. After stopping it, re-snapshots
    via `collect_gpu_evidence` and runs `check_gpu_conflicts` again with no
    approvals at all: an unidentified process, the drained unit somehow
    still running, or any other conflict remaining all block instead of
    reporting an unverified drain.
    """
    endpoint = registry.endpoint(endpoint_id)
    if unit not in endpoint.units:
        raise GateFailure(
            "gpu_drain_unit_not_registry_managed",
            {
                "unit": unit,
                "registry_units": list(endpoint.units),
                "message": (
                    f"{unit!r} is not one of endpoint {endpoint_id!r}'s registry-declared "
                    f"units {list(endpoint.units)}; refusing to drain a unit the registry "
                    "does not manage for this endpoint"
                ),
            },
        )

    # Legal to drain only when the ENTIRE current conflict is exactly this
    # one unit -- an unidentified process, or any other named service,
    # blocks here before any stop command is ever issued.
    check_gpu_conflicts(
        processes=gpu_processes_to_conflict_rows(processes), approved_services={unit}
    )

    stop_result = run_ssh(endpoint.host_id, ("systemctl", "stop", unit))
    if stop_result.returncode != 0:
        raise GateFailure(
            "gpu_drain_command_failed",
            {
                "unit": unit,
                "returncode": stop_result.returncode,
                "stderr": stop_result.stderr,
                "message": f"'systemctl stop {unit}' exited {stop_result.returncode}",
            },
        )

    after = collect_gpu_evidence(run_ssh=run_ssh, registry=registry, endpoint_id=endpoint_id)
    after_rows = gpu_processes_to_conflict_rows(after)
    # Post-drain, NOTHING should remain -- reusing check_gpu_conflicts with
    # no approvals means an unidentified process, the drained unit somehow
    # still running, or any other conflict all block identically.
    check_gpu_conflicts(processes=after_rows, approved_services=set())

    return {"ok": True, "drained_unit": unit, "remaining_processes": after_rows}
