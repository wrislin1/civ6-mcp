"""Pure-parser and safety tests for the live admission evidence collectors.

Every test injects fake `run_local`/`run_windows`/`run_ssh` callables that
return canned `CommandResult`s -- no real subprocess, socket, or SSH call
is ever made here. A `_Recorder` wraps each fake runner so tests can assert
exactly which commands (if any) were issued, which is how the "never a
broad pkill/wildcard/process-name termination" safety rule gets checked:
by inspecting the full list of argv actually sent.
"""
from __future__ import annotations

import pytest

from civ_mcp.arena.benchmark_gates import GateFailure
from civ_mcp.arena.benchmark_live_evidence import (
    CommandResult,
    GpuProcess,
    TunerHolder,
    classify_tuner_holder,
    collect_checkout_evidence,
    collect_gpu_evidence,
    collect_tuner_evidence,
    drain_gpu_service,
    gpu_processes_to_conflict_rows,
    terminate_tuner_pid,
)

WSL_REPO = "/home/riz/projects/civ6-mcp"
WINDOWS_REPO = "C:\\Users\\riz\\civ6-mcp-companion"


class _Recorder:
    """Wraps a dict of ``{argv-tuple: CommandResult}`` fixtures as an
    injectable runner, recording every argv it was actually called with."""

    def __init__(self, fixtures: dict[tuple, CommandResult]):
        self._fixtures = fixtures
        self.calls: list[tuple] = []

    def __call__(self, argv) -> CommandResult:
        argv = tuple(argv)
        self.calls.append(argv)
        try:
            return self._fixtures[argv]
        except KeyError:
            raise AssertionError(f"no fixture registered for argv={argv!r}") from None


class _SshRecorder:
    """Same as `_Recorder` but keyed on ``(host, argv-tuple)``."""

    def __init__(self, fixtures: dict[tuple, CommandResult]):
        self._fixtures = fixtures
        self.calls: list[tuple] = []

    def __call__(self, host: str, argv) -> CommandResult:
        argv = tuple(argv)
        self.calls.append((host, argv))
        try:
            return self._fixtures[(host, argv)]
        except KeyError:
            raise AssertionError(f"no fixture registered for host={host!r} argv={argv!r}") from None


def _ok(argv, stdout="", stderr="") -> CommandResult:
    return CommandResult(argv=tuple(argv), returncode=0, stdout=stdout, stderr=stderr)


def _err(argv, returncode=1, stdout="", stderr="error") -> CommandResult:
    return CommandResult(argv=tuple(argv), returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# checkout evidence
# ---------------------------------------------------------------------------


def test_checkout_evidence_records_nonempty_commit_and_porcelain_status():
    run_local = _Recorder(
        {
            ("git", "-C", WSL_REPO, "rev-parse", "HEAD"): _ok(
                ("git", "-C", WSL_REPO, "rev-parse", "HEAD"), stdout="deadbeef" * 5 + "\n"
            ),
            ("git", "-C", WSL_REPO, "status", "--porcelain=v1"): _ok(
                ("git", "-C", WSL_REPO, "status", "--porcelain=v1"), stdout=""
            ),
        }
    )
    run_windows = _Recorder(
        {
            ("git", "-C", WINDOWS_REPO, "rev-parse", "HEAD"): _ok(
                ("git", "-C", WINDOWS_REPO, "rev-parse", "HEAD"), stdout="deadbeef" * 5 + "\n"
            ),
            ("git", "-C", WINDOWS_REPO, "status", "--porcelain=v1"): _ok(
                ("git", "-C", WINDOWS_REPO, "status", "--porcelain=v1"), stdout=" M foo.py\n"
            ),
        }
    )

    evidence = collect_checkout_evidence(
        run_local=run_local, run_windows=run_windows, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO
    )

    assert evidence["wsl"]["commit"] == "deadbeef" * 5
    assert evidence["wsl"]["status"] == ""
    assert evidence["windows"]["commit"] == "deadbeef" * 5
    assert evidence["windows"]["status"] == " M foo.py\n"


def test_checkout_evidence_treats_failed_rev_parse_as_empty_commit():
    run_local = _Recorder(
        {
            ("git", "-C", WSL_REPO, "rev-parse", "HEAD"): _err(
                ("git", "-C", WSL_REPO, "rev-parse", "HEAD")
            ),
            ("git", "-C", WSL_REPO, "status", "--porcelain=v1"): _ok(
                ("git", "-C", WSL_REPO, "status", "--porcelain=v1")
            ),
        }
    )
    run_windows = _Recorder(
        {
            ("git", "-C", WINDOWS_REPO, "rev-parse", "HEAD"): _ok(
                ("git", "-C", WINDOWS_REPO, "rev-parse", "HEAD"), stdout="c" * 40 + "\n"
            ),
            ("git", "-C", WINDOWS_REPO, "status", "--porcelain=v1"): _ok(
                ("git", "-C", WINDOWS_REPO, "status", "--porcelain=v1")
            ),
        }
    )

    evidence = collect_checkout_evidence(
        run_local=run_local, run_windows=run_windows, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO
    )

    assert evidence["wsl"]["commit"] == ""


def test_checkout_evidence_treats_failed_status_query_as_dirty():
    run_local = _Recorder(
        {
            ("git", "-C", WSL_REPO, "rev-parse", "HEAD"): _ok(
                ("git", "-C", WSL_REPO, "rev-parse", "HEAD"), stdout="a" * 40 + "\n"
            ),
            ("git", "-C", WSL_REPO, "status", "--porcelain=v1"): _err(
                ("git", "-C", WSL_REPO, "status", "--porcelain=v1"), stderr="not a git repository"
            ),
        }
    )
    run_windows = _Recorder(
        {
            ("git", "-C", WINDOWS_REPO, "rev-parse", "HEAD"): _ok(
                ("git", "-C", WINDOWS_REPO, "rev-parse", "HEAD"), stdout="a" * 40 + "\n"
            ),
            ("git", "-C", WINDOWS_REPO, "status", "--porcelain=v1"): _ok(
                ("git", "-C", WINDOWS_REPO, "status", "--porcelain=v1")
            ),
        }
    )

    evidence = collect_checkout_evidence(
        run_local=run_local, run_windows=run_windows, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO
    )

    assert evidence["wsl"]["status"].strip()  # nonempty -> check_clean_checkout blocks


# ---------------------------------------------------------------------------
# FireTuner-holder classification
# ---------------------------------------------------------------------------

_SS_ARGV = ("ss", "-H", "-tlnp", "sport = :4318")


def _proc_fixtures(pid: int, *, start_ticks: int, cmdline: str, cwd: str) -> dict:
    stat_line = (
        f"{pid} (civ-mcp) S 1 {pid} {pid} 0 -1 4194304 0 0 0 0 0 0 0 0 20 0 1 0 {start_ticks} "
        "0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
    )
    return {
        (_SS_ARGV): _ok(_SS_ARGV, stdout=f'LISTEN 0 4096 127.0.0.1:4318 0.0.0.0:* users:(("civ-mcp",pid={pid},fd=10))\n'),
        ("cat", f"/proc/{pid}/stat"): _ok(("cat", f"/proc/{pid}/stat"), stdout=stat_line),
        ("cat", f"/proc/{pid}/cmdline"): _ok(
            ("cat", f"/proc/{pid}/cmdline"), stdout=cmdline.replace(" ", "\x00") + "\x00"
        ),
        ("readlink", "-f", f"/proc/{pid}/cwd"): _ok(
            ("readlink", "-f", f"/proc/{pid}/cwd"), stdout=cwd + "\n"
        ),
    }


def test_known_repo_owned_tuner_holder_classifies_as_known():
    fixtures = _proc_fixtures(4242, start_ticks=100000, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO)
    run_local = _Recorder(fixtures)

    holder = classify_tuner_holder(run_local=run_local, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO)

    assert holder == TunerHolder(
        pid=4242, start_ticks=100000, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO, known_repo_owned=True
    )


def test_no_holder_returns_none_and_reads_nothing_else():
    run_local = _Recorder({_SS_ARGV: _ok(_SS_ARGV, stdout="")})

    holder = classify_tuner_holder(run_local=run_local, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO)

    assert holder is None
    # No /proc read was attempted for a PID that was never identified.
    assert run_local.calls == [_SS_ARGV]


def test_unknown_tuner_holder_always_blocks():
    fixtures = _proc_fixtures(9999, start_ticks=1, cmdline="/usr/bin/some-other-thing", cwd="/tmp")
    run_local = _Recorder(fixtures)

    holder = classify_tuner_holder(run_local=run_local, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO)
    assert holder is not None
    assert holder.known_repo_owned is False

    with pytest.raises(GateFailure) as exc_info:
        collect_tuner_evidence(run_local=run_local, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO)
    assert exc_info.value.code == "unknown_tuner_holder"


def test_no_tuner_holder_does_not_issue_a_kill():
    run_local = _Recorder({_SS_ARGV: _ok(_SS_ARGV, stdout="")})

    evidence = collect_tuner_evidence(run_local=run_local, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO)

    assert evidence == {"holder": None, "ok": True, "port": 4318}
    assert not any("kill" in call for call in run_local.calls)


def test_targeted_tuner_termination_revalidates_pid_start_cmdline_and_cwd():
    """A fresh re-check right before the signal shows the SAME pid but a
    DIFFERENT start_ticks (classic PID reuse) -- termination must block and
    must never send SIGTERM."""
    preceding = TunerHolder(
        pid=4242, start_ticks=100000, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO, known_repo_owned=True
    )
    reused_fixtures = _proc_fixtures(
        4242, start_ticks=999999, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO
    )
    run_local = _Recorder(reused_fixtures)

    with pytest.raises(GateFailure) as exc_info:
        terminate_tuner_pid(
            run_local=run_local,
            requested_pid=4242,
            preceding_evidence=preceding,
            wsl_repo=WSL_REPO,
            windows_repo=WINDOWS_REPO,
            sleep=lambda _s: None,
        )
    assert exc_info.value.code == "tuner_identity_changed_before_termination"
    assert not any("kill" in call for call in run_local.calls)


def test_targeted_tuner_termination_succeeds_when_identity_matches_and_port_clears():
    preceding = TunerHolder(
        pid=4242, start_ticks=100000, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO, known_repo_owned=True
    )
    fixtures = dict(_proc_fixtures(4242, start_ticks=100000, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO))
    fixtures[("kill", "-TERM", "4242")] = _ok(("kill", "-TERM", "4242"))
    # Class order of calls: revalidation ss+proc reads, then kill, then one
    # post-signal ss check that shows the port cleared.
    calls_log = {"n": 0}
    fixtures_after_kill = {_SS_ARGV: _ok(_SS_ARGV, stdout="")}

    class _SequencedRecorder(_Recorder):
        def __call__(self, argv):
            argv = tuple(argv)
            self.calls.append(argv)
            if argv == ("kill", "-TERM", "4242"):
                calls_log["n"] += 1
                return fixtures[argv]
            if calls_log["n"] >= 1:
                # After the kill has been sent, the port-holder check
                # (and any /proc reads after it) reflect a cleared port.
                return fixtures_after_kill.get(argv, _ok(argv, stdout=""))
            return fixtures[argv]

    run_local = _SequencedRecorder(fixtures)

    result = terminate_tuner_pid(
        run_local=run_local,
        requested_pid=4242,
        preceding_evidence=preceding,
        wsl_repo=WSL_REPO,
        windows_repo=WINDOWS_REPO,
        sleep=lambda _s: None,
    )

    assert result == {"ok": True, "terminated_pid": 4242, "port": 4318}
    assert ("kill", "-TERM", "4242") in run_local.calls
    # Exactly one kill was sent -- never a repeated or broad signal.
    assert run_local.calls.count(("kill", "-TERM", "4242")) == 1


def test_targeted_tuner_termination_blocks_when_pid_survives():
    preceding = TunerHolder(
        pid=4242, start_ticks=100000, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO, known_repo_owned=True
    )
    fixtures = dict(_proc_fixtures(4242, start_ticks=100000, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO))
    fixtures[("kill", "-TERM", "4242")] = _ok(("kill", "-TERM", "4242"))
    run_local = _Recorder(fixtures)

    with pytest.raises(GateFailure) as exc_info:
        terminate_tuner_pid(
            run_local=run_local,
            requested_pid=4242,
            preceding_evidence=preceding,
            wsl_repo=WSL_REPO,
            windows_repo=WINDOWS_REPO,
            wait_attempts=2,
            sleep=lambda _s: None,
        )
    assert exc_info.value.code == "tuner_termination_did_not_clear_port"
    # Exactly one SIGTERM was sent, never repeated or escalated to a
    # stronger/broader signal.
    assert run_local.calls.count(("kill", "-TERM", "4242")) == 1


def test_terminate_tuner_pid_rejects_a_pid_it_never_observed():
    preceding = TunerHolder(
        pid=4242, start_ticks=100000, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO, known_repo_owned=True
    )
    run_local = _Recorder({})

    with pytest.raises(GateFailure) as exc_info:
        terminate_tuner_pid(
            run_local=run_local,
            requested_pid=9999,
            preceding_evidence=preceding,
            wsl_repo=WSL_REPO,
            windows_repo=WINDOWS_REPO,
            sleep=lambda _s: None,
        )
    assert exc_info.value.code == "tuner_pid_mismatch"
    assert run_local.calls == []  # not even a re-classification was attempted


def test_no_termination_command_is_ever_broad_or_wildcarded():
    """Across every remediation test above the only kill argv ever used is
    a single-PID SIGTERM. Assert that invariant directly against the
    argv shape this module is capable of constructing."""
    preceding = TunerHolder(
        pid=4242, start_ticks=100000, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO, known_repo_owned=True
    )
    fixtures = dict(_proc_fixtures(4242, start_ticks=100000, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO))
    fixtures[("kill", "-TERM", "4242")] = _ok(("kill", "-TERM", "4242"))

    class _SequencedRecorder(_Recorder):
        def __call__(self, argv):
            argv = tuple(argv)
            self.calls.append(argv)
            if argv == ("kill", "-TERM", "4242"):
                return fixtures[argv]
            if argv == _SS_ARGV and ("kill", "-TERM", "4242") in self.calls:
                return _ok(_SS_ARGV, stdout="")
            return fixtures[argv]

    run_local = _SequencedRecorder(fixtures)
    terminate_tuner_pid(
        run_local=run_local,
        requested_pid=4242,
        preceding_evidence=preceding,
        wsl_repo=WSL_REPO,
        windows_repo=WINDOWS_REPO,
        sleep=lambda _s: None,
    )

    kill_calls = [c for c in run_local.calls if c and c[0] == "kill"]
    assert kill_calls == [("kill", "-TERM", "4242")]
    for call in run_local.calls:
        joined = " ".join(call)
        assert "pkill" not in joined
        assert "*" not in joined
        # A kill command targets exactly one numeric PID -- never a
        # process-name pattern.
        if call and call[0] == "kill":
            assert call[-1].isdigit()


# ---------------------------------------------------------------------------
# GPU snapshot and drain
# ---------------------------------------------------------------------------


class _FakeEndpoint:
    def __init__(self, host_id, gpu_indexes, units):
        self.host_id = host_id
        self.gpu_indexes = gpu_indexes
        self.units = units


class _FakeRegistry:
    def __init__(self, endpoint: _FakeEndpoint):
        self._endpoint = endpoint

    def endpoint(self, endpoint_id: str):
        return self._endpoint


_HOST = "home-llm"
_UUID_GPU0 = "GPU-aaaaaaaa-0000-0000-0000-000000000000"
_UUID_GPU1 = "GPU-bbbbbbbb-0000-0000-0000-000000000000"


def test_gpu_snapshot_maps_uuid_to_index_and_pid_to_service():
    endpoint = _FakeEndpoint(host_id=_HOST, gpu_indexes=(0,), units=("ollama@0.service",))
    registry = _FakeRegistry(endpoint)
    gpu_argv = ("nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader")
    proc_argv = (
        "nvidia-smi",
        "--query-compute-apps=gpu_uuid,pid,process_name",
        "--format=csv,noheader",
    )
    run_ssh = _SshRecorder(
        {
            (_HOST, gpu_argv): _ok(gpu_argv, stdout=f"0, {_UUID_GPU0}\n1, {_UUID_GPU1}\n"),
            (_HOST, proc_argv): _ok(proc_argv, stdout=f"{_UUID_GPU0}, 555, ollama\n"),
            (_HOST, ("cat", "/proc/555/cgroup")): _ok(
                ("cat", "/proc/555/cgroup"),
                stdout="0::/system.slice/ollama@0.service\n",
            ),
        }
    )

    processes = collect_gpu_evidence(run_ssh=run_ssh, registry=registry, endpoint_id="home-gpu0")

    assert processes == [
        GpuProcess(
            host=_HOST,
            gpu_index=0,
            gpu_uuid=_UUID_GPU0,
            pid=555,
            process_name="ollama",
            service="ollama@0.service",
        )
    ]


def test_gpu_snapshot_scopes_to_endpoint_gpu_indexes_only():
    """A process on a GPU outside this endpoint's gpu_indexes must not be
    reported -- check_gpu_conflicts must see only relevant processes."""
    endpoint = _FakeEndpoint(host_id=_HOST, gpu_indexes=(0,), units=("ollama@0.service",))
    registry = _FakeRegistry(endpoint)
    gpu_argv = ("nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader")
    proc_argv = (
        "nvidia-smi",
        "--query-compute-apps=gpu_uuid,pid,process_name",
        "--format=csv,noheader",
    )
    run_ssh = _SshRecorder(
        {
            (_HOST, gpu_argv): _ok(gpu_argv, stdout=f"0, {_UUID_GPU0}\n1, {_UUID_GPU1}\n"),
            (_HOST, proc_argv): _ok(
                proc_argv, stdout=f"{_UUID_GPU1}, 777, some-other-workload\n"
            ),
        }
    )

    processes = collect_gpu_evidence(run_ssh=run_ssh, registry=registry, endpoint_id="home-gpu0")

    assert processes == []
    # No cgroup lookup was attempted for a PID on an irrelevant GPU.
    assert (_HOST, ("cat", "/proc/777/cgroup")) not in run_ssh.calls


def test_unknown_gpu_process_always_blocks_even_when_drain_requested():
    endpoint = _FakeEndpoint(host_id=_HOST, gpu_indexes=(0,), units=("ollama@0.service",))
    registry = _FakeRegistry(endpoint)
    processes = [
        GpuProcess(
            host=_HOST, gpu_index=0, gpu_uuid=_UUID_GPU0, pid=1, process_name="ollama",
            service="ollama@0.service",
        ),
        GpuProcess(
            host=_HOST, gpu_index=0, gpu_uuid=_UUID_GPU0, pid=2, process_name="mystery",
            service=None,
        ),
    ]
    run_ssh = _SshRecorder({})

    with pytest.raises(GateFailure) as exc_info:
        drain_gpu_service(
            run_ssh=run_ssh, registry=registry, endpoint_id="home-gpu0",
            processes=processes, unit="ollama@0.service",
        )
    assert exc_info.value.code == "gpu_conflict_unidentified_process"
    # No systemctl (or any other) command was ever issued -- blocked before
    # any remediation attempt.
    assert run_ssh.calls == []


def test_named_service_drain_rechecks_and_fails_if_process_remains():
    endpoint = _FakeEndpoint(host_id=_HOST, gpu_indexes=(0,), units=("ollama@0.service",))
    registry = _FakeRegistry(endpoint)
    processes = [
        GpuProcess(
            host=_HOST, gpu_index=0, gpu_uuid=_UUID_GPU0, pid=555, process_name="ollama",
            service="ollama@0.service",
        ),
    ]
    stop_argv = ("systemctl", "stop", "ollama@0.service")
    gpu_argv = ("nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader")
    proc_argv = (
        "nvidia-smi",
        "--query-compute-apps=gpu_uuid,pid,process_name",
        "--format=csv,noheader",
    )
    run_ssh = _SshRecorder(
        {
            (_HOST, stop_argv): _ok(stop_argv),
            (_HOST, gpu_argv): _ok(gpu_argv, stdout=f"0, {_UUID_GPU0}\n"),
            # The stop "succeeded" but the process is still resident --
            # e.g. a slow-to-exit worker -- so the re-snapshot still shows it.
            (_HOST, proc_argv): _ok(proc_argv, stdout=f"{_UUID_GPU0}, 555, ollama\n"),
            (_HOST, ("cat", "/proc/555/cgroup")): _ok(
                ("cat", "/proc/555/cgroup"), stdout="0::/system.slice/ollama@0.service\n"
            ),
        }
    )

    with pytest.raises(GateFailure) as exc_info:
        drain_gpu_service(
            run_ssh=run_ssh, registry=registry, endpoint_id="home-gpu0",
            processes=processes, unit="ollama@0.service",
        )
    assert exc_info.value.code == "gpu_conflict_not_acknowledged"
    assert (_HOST, stop_argv) in run_ssh.calls  # the stop WAS attempted...
    assert run_ssh.calls.count((_HOST, stop_argv)) == 1  # ...exactly once, never repeated


def test_named_service_drain_succeeds_when_process_actually_stops():
    endpoint = _FakeEndpoint(host_id=_HOST, gpu_indexes=(0,), units=("ollama@0.service",))
    registry = _FakeRegistry(endpoint)
    processes = [
        GpuProcess(
            host=_HOST, gpu_index=0, gpu_uuid=_UUID_GPU0, pid=555, process_name="ollama",
            service="ollama@0.service",
        ),
    ]
    stop_argv = ("systemctl", "stop", "ollama@0.service")
    gpu_argv = ("nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader")
    proc_argv = (
        "nvidia-smi",
        "--query-compute-apps=gpu_uuid,pid,process_name",
        "--format=csv,noheader",
    )
    run_ssh = _SshRecorder(
        {
            (_HOST, stop_argv): _ok(stop_argv),
            (_HOST, gpu_argv): _ok(gpu_argv, stdout=f"0, {_UUID_GPU0}\n"),
            (_HOST, proc_argv): _ok(proc_argv, stdout=""),
        }
    )

    result = drain_gpu_service(
        run_ssh=run_ssh, registry=registry, endpoint_id="home-gpu0",
        processes=processes, unit="ollama@0.service",
    )

    assert result == {"ok": True, "drained_unit": "ollama@0.service", "remaining_processes": []}


def test_drain_rejects_a_unit_the_registry_does_not_manage_for_this_endpoint():
    endpoint = _FakeEndpoint(host_id=_HOST, gpu_indexes=(0,), units=("ollama@0.service",))
    registry = _FakeRegistry(endpoint)
    processes = [
        GpuProcess(
            host=_HOST, gpu_index=0, gpu_uuid=_UUID_GPU0, pid=555, process_name="llama-swap",
            service="llama-swap@0.service",
        ),
    ]
    run_ssh = _SshRecorder({})

    with pytest.raises(GateFailure) as exc_info:
        drain_gpu_service(
            run_ssh=run_ssh, registry=registry, endpoint_id="home-gpu0",
            processes=processes, unit="llama-swap@0.service",
        )
    assert exc_info.value.code == "gpu_drain_unit_not_registry_managed"
    assert run_ssh.calls == []


def test_gpu_processes_to_conflict_rows_shape_matches_check_gpu_conflicts_input():
    processes = [
        GpuProcess(
            host=_HOST, gpu_index=0, gpu_uuid=_UUID_GPU0, pid=1, process_name="ollama",
            service="ollama@0.service",
        )
    ]
    rows = gpu_processes_to_conflict_rows(processes)
    assert rows == [
        {
            "pid": 1,
            "service": "ollama@0.service",
            "gpu_index": 0,
            "gpu_uuid": _UUID_GPU0,
            "process_name": "ollama",
            "host": _HOST,
        }
    ]
