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
_SS_ESTABLISHED_ARGV = ("ss", "-H", "-tnp", "state established dport = :4318")


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
    run_local = _Recorder(
        {
            _SS_ARGV: _ok(_SS_ARGV, stdout=""),
            _SS_ESTABLISHED_ARGV: _ok(_SS_ESTABLISHED_ARGV, stdout=""),
        }
    )

    holder = classify_tuner_holder(run_local=run_local, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO)

    assert holder is None
    # Both the LISTEN and ESTABLISHED queries ran (neither found anything),
    # but no /proc read was attempted for a PID that was never identified.
    assert run_local.calls == [_SS_ARGV, _SS_ESTABLISHED_ARGV]


def test_established_client_connection_is_detected_as_the_tuner_holder():
    """The 2026-08-31 incident's actual shape: the game LISTENS on the
    Windows side, and a stale WSL civ-mcp process holds an ESTABLISHED
    connection with 4318 as its *destination* (not a local LISTEN
    socket). A LISTEN-only query finds nothing; the ESTABLISHED/dport
    query must find and classify it."""
    pid = 7777
    fixtures = {
        _SS_ARGV: _ok(_SS_ARGV, stdout=""),
        _SS_ESTABLISHED_ARGV: _ok(
            _SS_ESTABLISHED_ARGV,
            stdout=(
                f'ESTAB 0 0 127.0.0.1:54123 127.0.0.1:4318 users:(("civ-mcp",pid={pid},fd=12))\n'
            ),
        ),
        ("cat", f"/proc/{pid}/stat"): _ok(
            ("cat", f"/proc/{pid}/stat"),
            stdout=(
                f"{pid} (civ-mcp) S 1 {pid} {pid} 0 -1 4194304 0 0 0 0 0 0 0 0 20 0 1 0 55555 "
                "0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
            ),
        ),
        ("cat", f"/proc/{pid}/cmdline"): _ok(
            ("cat", f"/proc/{pid}/cmdline"), stdout="/usr/bin/civ-mcp\x00"
        ),
        ("readlink", "-f", f"/proc/{pid}/cwd"): _ok(
            ("readlink", "-f", f"/proc/{pid}/cwd"), stdout=WSL_REPO + "\n"
        ),
    }
    run_local = _Recorder(fixtures)

    holder = classify_tuner_holder(run_local=run_local, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO)

    assert holder == TunerHolder(
        pid=pid, start_ticks=55555, cmdline="/usr/bin/civ-mcp", cwd=WSL_REPO, known_repo_owned=True
    )
    # The LISTEN query ran first (came up empty), then the ESTABLISHED
    # query found the stale client.
    assert run_local.calls[:2] == [_SS_ARGV, _SS_ESTABLISHED_ARGV]


def test_failed_listen_query_raises_instead_of_reporting_no_holder():
    run_local = _Recorder({_SS_ARGV: _err(_SS_ARGV, returncode=1, stderr="ss: permission denied")})

    with pytest.raises(GateFailure) as exc_info:
        classify_tuner_holder(run_local=run_local, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO)
    assert exc_info.value.code == "tuner_holder_query_failed"
    assert exc_info.value.details["query"] == "listen"


def test_failed_established_query_raises_instead_of_reporting_no_holder():
    run_local = _Recorder(
        {
            _SS_ARGV: _ok(_SS_ARGV, stdout=""),
            _SS_ESTABLISHED_ARGV: _err(
                _SS_ESTABLISHED_ARGV, returncode=1, stderr="ss: permission denied"
            ),
        }
    )

    with pytest.raises(GateFailure) as exc_info:
        classify_tuner_holder(run_local=run_local, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO)
    assert exc_info.value.code == "tuner_holder_query_failed"
    assert exc_info.value.details["query"] == "established"


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
    run_local = _Recorder(
        {
            _SS_ARGV: _ok(_SS_ARGV, stdout=""),
            _SS_ESTABLISHED_ARGV: _ok(_SS_ESTABLISHED_ARGV, stdout=""),
        }
    )

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
            if ("kill", "-TERM", "4242") in self.calls:
                # After the kill has been sent, both port-holder queries
                # reflect a cleared port.
                return _ok(argv, stdout="")
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


def test_failed_gpu_index_query_raises_instead_of_reporting_no_gpus():
    endpoint = _FakeEndpoint(host_id=_HOST, gpu_indexes=(0,), units=("ollama@0.service",))
    registry = _FakeRegistry(endpoint)
    gpu_argv = ("nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader")
    run_ssh = _SshRecorder(
        {(_HOST, gpu_argv): _err(gpu_argv, returncode=9, stderr="Failed to query GPU")}
    )

    with pytest.raises(GateFailure) as exc_info:
        collect_gpu_evidence(run_ssh=run_ssh, registry=registry, endpoint_id="home-gpu0")
    assert exc_info.value.code == "gpu_snapshot_query_failed"
    assert exc_info.value.details["query"] == "index,uuid"
    # The compute-apps query must never even run once the prerequisite
    # index/uuid query has already failed.
    assert run_ssh.calls == [(_HOST, gpu_argv)]


def test_failed_compute_apps_query_raises_instead_of_reporting_no_processes():
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
            (_HOST, gpu_argv): _ok(gpu_argv, stdout=f"0, {_UUID_GPU0}\n"),
            (_HOST, proc_argv): _err(proc_argv, returncode=9, stderr="Failed to query processes"),
        }
    )

    with pytest.raises(GateFailure) as exc_info:
        collect_gpu_evidence(run_ssh=run_ssh, registry=registry, endpoint_id="home-gpu0")
    assert exc_info.value.code == "gpu_snapshot_query_failed"
    assert exc_info.value.details["query"] == "compute-apps"


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


# ---------------------------------------------------------------------------
# B6 (external review wave B): drain_gpu_service must apply the SAME own-
# endpoint-unit filtering semantics admission's own gpu_evidence collector
# already uses -- a DIFFERENT one of this endpoint's own declared units
# resting alongside the drain target must never block the sanctioned
# remediation admission itself would have tolerated. Unidentified always
# blocks regardless.
# ---------------------------------------------------------------------------


def test_drain_tolerates_a_different_own_declared_unit_resident_alongside_target():
    """The endpoint declares TWO units; only one (the drain target) is
    being remediated. The OTHER own-declared unit resting on the same GPU
    must not block the drain -- admission's own gpu_evidence collector
    already tolerates any of an endpoint's own declared units being
    resident, and drain_gpu_service must apply the identical semantics
    rather than requiring the operator to separately name every other own
    unit via approved_services."""
    endpoint = _FakeEndpoint(
        host_id=_HOST, gpu_indexes=(0,), units=("civ-arena-gemma4.service", "civ-arena-qwen.service")
    )
    registry = _FakeRegistry(endpoint)
    processes = [
        GpuProcess(
            host=_HOST, gpu_index=0, gpu_uuid=_UUID_GPU0, pid=555, process_name="gemma4",
            service="civ-arena-gemma4.service",
        ),
        GpuProcess(
            host=_HOST, gpu_index=0, gpu_uuid=_UUID_GPU0, pid=556, process_name="qwen",
            service="civ-arena-qwen.service",
        ),
    ]
    stop_argv = ("systemctl", "stop", "civ-arena-gemma4.service")
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
            # civ-arena-gemma4.service (the drained target) is gone; civ-arena-qwen.service
            # (the OTHER own-declared unit) is still resident, as expected.
            (_HOST, proc_argv): _ok(proc_argv, stdout=f"{_UUID_GPU0}, 556, qwen\n"),
            (_HOST, ("cat", "/proc/556/cgroup")): _ok(
                ("cat", "/proc/556/cgroup"), stdout="0::/system.slice/civ-arena-qwen.service\n"
            ),
        }
    )

    result = drain_gpu_service(
        run_ssh=run_ssh, registry=registry, endpoint_id="home-gpu0",
        processes=processes, unit="civ-arena-gemma4.service",
    )

    assert result["ok"] is True
    assert result["drained_unit"] == "civ-arena-gemma4.service"


def test_drain_still_fails_if_the_target_unit_itself_remains_despite_other_own_unit_tolerance():
    """The own-unit tolerance must never extend to the unit ACTUALLY being
    drained -- if civ-arena-gemma4.service (the target) is still resident after the
    stop command, the drain must still fail, even though civ-arena-qwen.service (a
    different own-declared unit) being present is fine."""
    endpoint = _FakeEndpoint(
        host_id=_HOST, gpu_indexes=(0,), units=("civ-arena-gemma4.service", "civ-arena-qwen.service")
    )
    registry = _FakeRegistry(endpoint)
    processes = [
        GpuProcess(
            host=_HOST, gpu_index=0, gpu_uuid=_UUID_GPU0, pid=555, process_name="gemma4",
            service="civ-arena-gemma4.service",
        ),
        GpuProcess(
            host=_HOST, gpu_index=0, gpu_uuid=_UUID_GPU0, pid=556, process_name="qwen",
            service="civ-arena-qwen.service",
        ),
    ]
    stop_argv = ("systemctl", "stop", "civ-arena-gemma4.service")
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
            # civ-arena-gemma4.service (the drained target) is STILL resident.
            (_HOST, proc_argv): _ok(
                proc_argv, stdout=f"{_UUID_GPU0}, 555, gemma4\n{_UUID_GPU0}, 556, qwen\n"
            ),
            (_HOST, ("cat", "/proc/555/cgroup")): _ok(
                ("cat", "/proc/555/cgroup"), stdout="0::/system.slice/civ-arena-gemma4.service\n"
            ),
            (_HOST, ("cat", "/proc/556/cgroup")): _ok(
                ("cat", "/proc/556/cgroup"), stdout="0::/system.slice/civ-arena-qwen.service\n"
            ),
        }
    )

    with pytest.raises(GateFailure) as exc_info:
        drain_gpu_service(
            run_ssh=run_ssh, registry=registry, endpoint_id="home-gpu0",
            processes=processes, unit="civ-arena-gemma4.service",
        )
    assert exc_info.value.code == "gpu_conflict_not_acknowledged"
    assert exc_info.value.details["unapproved"] == ["civ-arena-gemma4.service"]


# ---------------------------------------------------------------------------
# E1 (external review wave E): an unidentifiable tuner-port holder must
# never classify as known_repo_owned. A failed `readlink -f /proc/<pid>/cwd`
# used to yield cwd == "", which compared EQUAL to a defaulted-empty
# windows_repo -- so the ONE class terminate_tuner_pid is willing to SIGTERM
# (stale repo-owned holder) could be minted from zero identity evidence.
# ---------------------------------------------------------------------------


def _proc_fixtures_with_cwd_result(pid: int, *, start_ticks: int, cmdline: str, cwd_result) -> dict:
    fixtures = _proc_fixtures(pid, start_ticks=start_ticks, cmdline=cmdline, cwd="/ignored")
    fixtures[("readlink", "-f", f"/proc/{pid}/cwd")] = cwd_result
    return fixtures


def test_failed_cwd_read_with_empty_windows_repo_is_never_known_repo_owned():
    """The exact E1 fail-open shape: cwd read fails (cwd falls back to ""),
    windows_repo was defaulted to "" at the CLI boundary -- "" == "" must
    NOT make an unidentifiable holder repo-owned."""
    pid = 4242
    cwd_argv = ("readlink", "-f", f"/proc/{pid}/cwd")
    fixtures = _proc_fixtures_with_cwd_result(
        pid,
        start_ticks=100000,
        cmdline="/usr/bin/civ-mcp",
        cwd_result=_err(cwd_argv, returncode=1, stderr="readlink: permission denied"),
    )
    run_local = _Recorder(fixtures)

    holder = classify_tuner_holder(run_local=run_local, wsl_repo=WSL_REPO, windows_repo="")

    assert holder is not None
    assert holder.known_repo_owned is False


def test_successful_but_empty_cwd_read_is_never_known_repo_owned():
    """Weakest form of the empty-cwd match: the readlink 'succeeded'
    (returncode 0) but produced no path at all. An empty cwd must never
    match an empty repo candidate."""
    pid = 4242
    cwd_argv = ("readlink", "-f", f"/proc/{pid}/cwd")
    fixtures = _proc_fixtures_with_cwd_result(
        pid,
        start_ticks=100000,
        cmdline="/usr/bin/civ-mcp",
        cwd_result=_ok(cwd_argv, stdout=""),
    )
    run_local = _Recorder(fixtures)

    holder = classify_tuner_holder(run_local=run_local, wsl_repo=WSL_REPO, windows_repo="")

    assert holder is not None
    assert holder.known_repo_owned is False


def test_root_owned_pid_with_unreadable_cwd_is_never_known_repo_owned():
    """A root-owned holder: /proc/<pid>/cwd is unreadable (permission
    denied) even though the cmdline happens to match the civ-mcp markers,
    and both repo paths are genuinely configured. Identity is incomplete ->
    never repo-owned."""
    pid = 4242
    cwd_argv = ("readlink", "-f", f"/proc/{pid}/cwd")
    fixtures = _proc_fixtures_with_cwd_result(
        pid,
        start_ticks=100000,
        cmdline="/usr/bin/civ-mcp",
        cwd_result=_err(cwd_argv, returncode=1, stderr="readlink: Permission denied"),
    )
    run_local = _Recorder(fixtures)

    holder = classify_tuner_holder(
        run_local=run_local, wsl_repo=WSL_REPO, windows_repo=WINDOWS_REPO
    )

    assert holder is not None
    assert holder.known_repo_owned is False


def test_terminate_refuses_unidentifiable_holder_under_empty_windows_repo_and_sends_no_kill():
    """End-to-end E1 destructive-path proof: with an unreadable cwd and an
    empty windows_repo, termination must refuse (the holder is NOT
    stale_repo_owned) and never issue any kill command."""
    pid = 4242
    cwd_argv = ("readlink", "-f", f"/proc/{pid}/cwd")
    fixtures = _proc_fixtures_with_cwd_result(
        pid,
        start_ticks=100000,
        cmdline="/usr/bin/civ-mcp",
        cwd_result=_err(cwd_argv, returncode=1, stderr="readlink: permission denied"),
    )
    run_local = _Recorder(fixtures)

    preceding = classify_tuner_holder(run_local=run_local, wsl_repo=WSL_REPO, windows_repo="")
    assert preceding is not None

    with pytest.raises(GateFailure) as exc_info:
        terminate_tuner_pid(
            run_local=run_local,
            requested_pid=pid,
            preceding_evidence=preceding,
            wsl_repo=WSL_REPO,
            windows_repo="",
            sleep=lambda _s: None,
        )
    assert exc_info.value.code == "unknown_tuner_holder"
    assert not any(call and call[0] == "kill" for call in run_local.calls)


# ---------------------------------------------------------------------------
# E5 (external review wave E): nvidia-smi CSV parsing must fail closed on
# any malformed/unparseable line -- a stdout warning line, a truncated row,
# or an "[N/A]" field must raise GateFailure (quoting the offending line),
# never ValueError, and never be silently skipped (skipping could hide a
# real process).
# ---------------------------------------------------------------------------

_GPU_ARGV = ("nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader")
_PROC_ARGV = (
    "nvidia-smi",
    "--query-compute-apps=gpu_uuid,pid,process_name",
    "--format=csv,noheader",
)


def _gpu_endpoint_registry():
    endpoint = _FakeEndpoint(host_id=_HOST, gpu_indexes=(0,), units=("ollama@0.service",))
    return _FakeRegistry(endpoint)


def test_gpu_index_query_warning_line_raises_gate_failure_quoting_the_line():
    warning_line = "Warning: infoROM is corrupted on GPU 0000:01:00.0"
    run_ssh = _SshRecorder(
        {(_HOST, _GPU_ARGV): _ok(_GPU_ARGV, stdout=f"{warning_line}\n0, {_UUID_GPU0}\n")}
    )

    with pytest.raises(GateFailure) as exc_info:
        collect_gpu_evidence(run_ssh=run_ssh, registry=_gpu_endpoint_registry(), endpoint_id="home-gpu0")
    assert exc_info.value.code == "gpu_snapshot_parse_error"
    assert exc_info.value.details["line"] == warning_line


def test_compute_apps_warning_line_raises_gate_failure_quoting_the_line():
    warning_line = "Warning: persistence mode is disabled"
    run_ssh = _SshRecorder(
        {
            (_HOST, _GPU_ARGV): _ok(_GPU_ARGV, stdout=f"0, {_UUID_GPU0}\n"),
            (_HOST, _PROC_ARGV): _ok(
                _PROC_ARGV, stdout=f"{warning_line}\n{_UUID_GPU0}, 555, ollama\n"
            ),
        }
    )

    with pytest.raises(GateFailure) as exc_info:
        collect_gpu_evidence(run_ssh=run_ssh, registry=_gpu_endpoint_registry(), endpoint_id="home-gpu0")
    assert exc_info.value.code == "gpu_snapshot_parse_error"
    assert exc_info.value.details["line"] == warning_line


def test_truncated_compute_apps_row_raises_gate_failure_not_value_error():
    truncated = f"{_UUID_GPU0}, 55"
    run_ssh = _SshRecorder(
        {
            (_HOST, _GPU_ARGV): _ok(_GPU_ARGV, stdout=f"0, {_UUID_GPU0}\n"),
            (_HOST, _PROC_ARGV): _ok(_PROC_ARGV, stdout=f"{truncated}\n"),
        }
    )

    with pytest.raises(GateFailure) as exc_info:
        collect_gpu_evidence(run_ssh=run_ssh, registry=_gpu_endpoint_registry(), endpoint_id="home-gpu0")
    assert exc_info.value.code == "gpu_snapshot_parse_error"
    assert exc_info.value.details["line"] == truncated


def test_not_available_fields_raise_gate_failure_not_value_error():
    na_row = f"{_UUID_GPU0}, [N/A], [N/A]"
    run_ssh = _SshRecorder(
        {
            (_HOST, _GPU_ARGV): _ok(_GPU_ARGV, stdout=f"0, {_UUID_GPU0}\n"),
            (_HOST, _PROC_ARGV): _ok(_PROC_ARGV, stdout=f"{na_row}\n"),
        }
    )

    with pytest.raises(GateFailure) as exc_info:
        collect_gpu_evidence(run_ssh=run_ssh, registry=_gpu_endpoint_registry(), endpoint_id="home-gpu0")
    assert exc_info.value.code == "gpu_snapshot_parse_error"
    assert exc_info.value.details["line"] == na_row


def test_non_numeric_pid_raises_gate_failure_not_value_error():
    bad_row = f"{_UUID_GPU0}, notapid, ollama"
    run_ssh = _SshRecorder(
        {
            (_HOST, _GPU_ARGV): _ok(_GPU_ARGV, stdout=f"0, {_UUID_GPU0}\n"),
            (_HOST, _PROC_ARGV): _ok(_PROC_ARGV, stdout=f"{bad_row}\n"),
        }
    )

    with pytest.raises(GateFailure) as exc_info:
        collect_gpu_evidence(run_ssh=run_ssh, registry=_gpu_endpoint_registry(), endpoint_id="home-gpu0")
    assert exc_info.value.code == "gpu_snapshot_parse_error"
    assert exc_info.value.details["line"] == bad_row


def test_unmapped_gpu_uuid_row_raises_gate_failure_instead_of_silent_skip():
    """A well-formed row whose gpu_uuid maps to no GPU reported by the
    index/uuid query cannot be attributed -- skipping it could hide a real
    process on this endpoint's GPU; it must fail closed instead."""
    orphan_row = "GPU-cccccccc-0000-0000-0000-000000000000, 555, ollama"
    run_ssh = _SshRecorder(
        {
            (_HOST, _GPU_ARGV): _ok(_GPU_ARGV, stdout=f"0, {_UUID_GPU0}\n"),
            (_HOST, _PROC_ARGV): _ok(_PROC_ARGV, stdout=f"{orphan_row}\n"),
        }
    )

    with pytest.raises(GateFailure) as exc_info:
        collect_gpu_evidence(run_ssh=run_ssh, registry=_gpu_endpoint_registry(), endpoint_id="home-gpu0")
    assert exc_info.value.code == "gpu_snapshot_parse_error"
    assert exc_info.value.details["line"] == orphan_row
