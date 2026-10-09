"""Offline tests for the positive-control v2 capture timing probe.

Every test runs against fake operations: no FireTuner, no deployment, no
game. The fakes count each operation so the tests can prove the probe's
shape (positive-control admission, one v2 capture per sample, zero
mutation, untimed scope survey) rather than only its verdict.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from civ_mcp.arena import benchmark_capture_probe as probe
from civ_mcp.arena import benchmark_contract_v2 as c2
from civ_mcp.arena.benchmark_capture_probe import timing_probe_passes
from civ_mcp.arena.benchmark_state import normalize_state, state_digest

REPO = Path(__file__).resolve().parents[2]
POSCTRL = REPO / "benchmarks" / "positions" / "builder-posctrl-v1.yaml"


# ---------------------------------------------------------------------------
# Gate rule
# ---------------------------------------------------------------------------

def _row(**overrides):
    row = {"complete": True, "duration_s": 0.4, "lua_executions": 1,
           "pre_drain_s": 0.1, "post_drain_s": 0.05, "digest": "same", "phase": "probe"}
    row.update(overrides)
    return row


def test_two_queries_cannot_pass_by_being_fast():
    row = {"complete": True, "duration_s": 0.9, "lua_executions": 2,
           "pre_drain_s": 0.1, "post_drain_s": 0.2, "digest": "same"}
    assert not timing_probe_passes([dict(row) for _ in range(20)])


def test_twenty_complete_identical_single_execution_rows_pass():
    rows = [_row(duration_s=2.0 if i == 0 else 0.5) for i in range(20)]
    assert timing_probe_passes(rows)
    assert probe.gate_reasons(rows) == []


def test_nineteen_rows_fail():
    rows = [_row() for _ in range(19)]
    assert not timing_probe_passes(rows)
    assert [r["code"] for r in probe.gate_reasons(rows)] == ["sample_count"]


def test_differing_digest_fails():
    rows = [_row() for _ in range(19)] + [_row(digest="other")]
    assert not timing_probe_passes(rows)
    assert [r["code"] for r in probe.gate_reasons(rows)] == ["digest_drift"]


def test_overrun_fails():
    rows = [_row() for _ in range(19)] + [_row(duration_s=2.1)]
    assert not timing_probe_passes(rows)
    assert [r["code"] for r in probe.gate_reasons(rows)] == ["overrun"]


@pytest.mark.parametrize("field", ["pre_drain_s", "post_drain_s"])
def test_missing_drain_fails(field):
    rows = [_row() for _ in range(19)] + [_row(**{field: None})]
    assert not timing_probe_passes(rows)
    assert [r["code"] for r in probe.gate_reasons(rows)] == ["missing_drain"]


def test_incomplete_sample_fails():
    rows = [_row() for _ in range(19)] + [_row(complete=False, digest=None)]
    assert not timing_probe_passes(rows)
    assert "incomplete" in [r["code"] for r in probe.gate_reasons(rows)]


def test_gate_requires_exactly_twenty_samples_constant():
    assert probe.REQUIRED_SAMPLES == 20
    assert probe.POSITIVE_CONTROL_ID == "builder-posctrl-v1"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _posctrl_raw() -> dict:
    return yaml.safe_load(POSCTRL.read_text())


def _write_position(tmp_path: Path, **overrides) -> Path:
    raw = _posctrl_raw()
    archive = tmp_path / "ARCHIVE.Civ6Save"
    archive.write_bytes(b"fake positive-control archive")
    raw["archive"] = str(archive)
    raw["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    raw.update(overrides)
    path = tmp_path / "position.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def _v2_state(coverage: dict, *, turn: int = 100, player_id: int = 0, gold: int = 95) -> dict:
    return {
        "wire_version": "2.0.0", "civ_type": "CIVILIZATION_KOREA", "seed": 1881300077,
        "turn": turn, "active_player": 0, "player_id": player_id, "gold": gold, "faith": 119,
        "units": [], "targets": [], "cities": [], "tiles": [], "resources": [],
        "coverage": coverage, "row_counts": {"identity": 1, "unit": 0, "tile": 0},
    }


class FakeConn:
    def __init__(self):
        self.writes = 0

    async def execute_write(self, *_args, **_kwargs):  # mutation must never occur
        self.writes += 1
        raise AssertionError("probe issued a mutation")


class FakeOps:
    """Counts every operation; each behaviour is overridable per test."""

    def __init__(self, *, verified=True, v1_states=None, v2=None, io=None):
        self.calls: list[str] = []
        self.conn = FakeConn()
        self.verified = verified
        expected = normalize_state(_posctrl_raw()["expected_state"])
        self.v1_states = list(v1_states) if v1_states is not None else [expected, expected]
        self.v2 = v2
        self.io = io if io is not None else {"pre_drain_s": 0.1, "post_drain_s": 0.05,
                                             "lua_executions": 1}
        self.v2_calls = 0
        self.coverages: list[dict] = []

    def bundle(self) -> probe.ProbeOps:
        return probe.ProbeOps(
            connect=self.connect, deploy=self.deploy, reload=self.reload,
            dismiss_popups=self.dismiss_popups, capture_v1=self.capture_v1,
            capture_v2=self.capture_v2, grid_size=self.grid_size,
            disconnect=self.disconnect,
        )

    async def connect(self):
        self.calls.append("connect")
        return self.conn

    async def deploy(self, position, archive):
        self.calls.append("deploy")
        return {"ok": True, "save_name": position.game_save_name}

    async def reload(self, conn, position):
        self.calls.append("reload")
        return self.verified

    async def dismiss_popups(self, conn):
        self.calls.append("popups")
        return "none"

    async def capture_v1(self, conn, player_id, tiles):
        self.calls.append("capture_v1")
        return self.v1_states.pop(0)

    async def grid_size(self, conn):
        self.calls.append("grid_size")
        return (128, 80)

    async def capture_v2(self, conn, player_id, coverage, *, io_timing=None):
        self.calls.append("capture_v2")
        index = self.v2_calls
        self.v2_calls += 1
        self.coverages.append(coverage)
        if io_timing is not None:
            io_timing.update(self.io)
        if self.v2 is not None:
            return self.v2(index, coverage)
        return _v2_state(coverage)

    async def disconnect(self, conn):
        self.calls.append("disconnect")


async def _run(tmp_path, ops: FakeOps, *, position=None, samples=20):
    position = position or _write_position(tmp_path)
    out = tmp_path / "out"
    summary = await probe.probe_capture(position, samples=samples, output_dir=out,
                                        ops=ops.bundle())
    return summary, out


def _codes(summary) -> list[str]:
    return [r["code"] for r in summary["verdict"]["reasons"]]


# ---------------------------------------------------------------------------
# Probe flow
# ---------------------------------------------------------------------------

async def test_probe_passes_with_one_v2_execution_per_sample_and_no_mutation(tmp_path):
    ops = FakeOps()
    summary, out = await _run(tmp_path, ops)
    assert summary["verdict"] == {"passed": True, "reasons": []}
    assert ops.v2_calls == 20 and ops.calls.count("capture_v2") == 20
    assert ops.conn.writes == 0
    assert ops.calls[:5] == ["deploy", "connect", "reload", "popups", "capture_v1"]
    assert ops.calls[5] == "grid_size"  # untimed survey precedes every timed capture
    assert ops.calls[-2:] == ["capture_v1", "disconnect"]
    assert all(c == summary["scope"] for c in ops.coverages)
    assert summary["samples"] == 20 and len(summary["rows"]) == 20
    assert all(r["phase"] == "probe" and r["lua_executions"] == 1 for r in summary["rows"])
    assert summary["telemetry_summary"]["count"] == 20
    assert summary["telemetry_summary"]["all_single_execution"] is True


async def test_library_or_other_position_is_refused_before_any_deploy(tmp_path):
    for overrides in ({"position_id": "builder-econ-lib-v1"}, {"split": "development"}):
        ops = FakeOps()
        position = _write_position(tmp_path, **overrides)
        with pytest.raises(ValueError):
            await probe.probe_capture(position, samples=20, output_dir=tmp_path / "out",
                                      ops=ops.bundle())
        assert ops.calls == []
        assert not (tmp_path / "out").exists()


async def test_archive_hash_mismatch_is_refused(tmp_path):
    ops = FakeOps()
    position = _write_position(tmp_path, archive_sha256="0" * 64)
    with pytest.raises(probe.ProbeRefused):
        await probe.probe_capture(position, samples=20, output_dir=tmp_path / "out",
                                  ops=ops.bundle())
    assert ops.calls == []


async def test_unverified_reload_fails_without_sampling(tmp_path):
    ops = FakeOps(verified=False)
    summary, out = await _run(tmp_path, ops)
    assert summary["verdict"]["passed"] is False
    assert "reload_unverified" in _codes(summary)
    assert ops.v2_calls == 0
    assert ops.calls[-1] == "disconnect"
    assert (out / "summary.json").is_file()


async def test_v1_digest_mismatch_fails_without_sampling(tmp_path):
    drifted = normalize_state(dict(_posctrl_raw()["expected_state"], turn=101))
    ops = FakeOps(v1_states=[drifted])
    summary, _ = await _run(tmp_path, ops)
    assert summary["verdict"]["passed"] is False
    assert "v1_digest_mismatch" in _codes(summary)
    assert ops.v2_calls == 0


async def test_final_v1_identity_drift_fails(tmp_path):
    expected = normalize_state(_posctrl_raw()["expected_state"])
    drifted = normalize_state(dict(_posctrl_raw()["expected_state"], gold=1.0))
    ops = FakeOps(v1_states=[expected, drifted])
    summary, _ = await _run(tmp_path, ops)
    assert summary["verdict"]["passed"] is False
    assert "identity_drift" in _codes(summary)


async def test_sample_turn_drift_fails(tmp_path):
    ops = FakeOps(v2=lambda i, cov: _v2_state(cov, turn=101 if i == 7 else 100))
    summary, _ = await _run(tmp_path, ops)
    assert summary["verdict"]["passed"] is False
    assert "identity_drift" in _codes(summary)
    assert summary["rows"][7]["complete"] is False


async def test_sample_digest_drift_fails(tmp_path):
    ops = FakeOps(v2=lambda i, cov: _v2_state(cov, gold=96 if i == 3 else 95))
    summary, _ = await _run(tmp_path, ops)
    assert "digest_drift" in _codes(summary)


async def test_two_executions_in_the_probe_fail(tmp_path):
    ops = FakeOps(io={"pre_drain_s": 0.1, "post_drain_s": 0.1, "lua_executions": 2})
    summary, _ = await _run(tmp_path, ops)
    assert summary["verdict"]["passed"] is False
    assert "two_queries" in _codes(summary)


async def test_missing_drain_measurement_in_the_probe_fails(tmp_path):
    ops = FakeOps(io={"pre_drain_s": 0.1, "lua_executions": 1})
    summary, _ = await _run(tmp_path, ops)
    assert "missing_drain" in _codes(summary)
    assert "post_drain_s" in summary["telemetry_summary"]["unavailable"]


async def test_non_finite_duration_is_rejected_before_rows(tmp_path):
    ops = FakeOps(io={"pre_drain_s": float("nan"), "post_drain_s": 0.1, "lua_executions": 1})
    summary, out = await _run(tmp_path, ops)
    assert summary["verdict"]["passed"] is False
    assert "invalid_duration" in _codes(summary)
    json.loads((out / "summary.json").read_text())  # still strict JSON


async def test_failed_samples_are_recorded_with_reasons(tmp_path):
    def flaky(i, cov):
        if i == 4:
            raise probe.BenchmarkStateError("v2 capture reported an error: ERR:boom")
        return _v2_state(cov)

    ops = FakeOps(v2=flaky)
    summary, out = await _run(tmp_path, ops)
    assert ops.v2_calls == 20
    assert summary["verdict"]["passed"] is False
    assert "incomplete" in _codes(summary)
    sample = json.loads((out / "samples" / "005.json").read_text())
    assert sample["row"]["complete"] is False and sample["error"]["type"] == "BenchmarkStateError"
    assert "ERR:boom" in sample["error"]["message"]
    assert len(list((out / "samples").glob("*.json"))) == 20


async def test_nineteen_samples_cannot_pass(tmp_path):
    summary, _ = await _run(tmp_path, FakeOps(), samples=19)
    assert summary["verdict"]["passed"] is False
    assert _codes(summary) == ["sample_count"]


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def test_scope_is_deterministic_and_keeps_every_relevant_tile():
    state = normalize_state(_posctrl_raw()["expected_state"])
    relevant = [(68, 23), (74, 30), (71, 21), (0, 0)]
    scope_a, info_a = probe.build_probe_scope(state, relevant, grid=(128, 80))
    reversed_state = dict(state, units=list(reversed(state["units"])))
    scope_b, info_b = probe.build_probe_scope(reversed_state, list(reversed(relevant)),
                                              grid=(128, 80))
    assert scope_a == scope_b and info_a == info_b
    area = scope_a["area"]
    assert area == sorted(area) and len({tuple(p) for p in area}) == len(area)
    for tile in relevant:
        assert list(tile) in area
    assert scope_a["include_owned_tiles"] is True
    assert scope_a["tracked_targets"] == []
    assert info_a["tracked_targets_source"]
    # Radius-3 neighbourhood of a city, clipped to the grid.
    assert [75, 29] in area and [72, 26] in area
    assert all(0 <= x < 128 and 0 <= y < 80 for x, y in area)
    assert info_a["area_count"] == len(area)


def test_scope_clips_the_neighbourhood_to_the_grid():
    state = {"cities": [{"id": 1, "x": 0, "y": 0}], "units": []}
    scope, _ = probe.build_probe_scope(state, [], grid=(10, 10))
    assert scope["area"] == [[x, y] for x in range(4) for y in range(4)]


async def test_summary_records_scope_digest_and_row_counts(tmp_path):
    summary, _ = await _run(tmp_path, FakeOps())
    assert summary["scope_sha256"] == c2.document_digest(summary["scope"])
    assert summary["row_counts"] == {"identity": 1, "unit": 0, "tile": 0}
    assert summary["position_id"] == "builder-posctrl-v1"
    assert summary["expected_state_sha256"] == _posctrl_raw()["expected_state_sha256"]


# ---------------------------------------------------------------------------
# Evidence files
# ---------------------------------------------------------------------------

async def test_evidence_files_and_index_hashes(tmp_path):
    summary, out = await _run(tmp_path, FakeOps())
    on_disk = json.loads((out / "summary.json").read_text())
    assert on_disk == summary
    assert summary["capture_implementation_sha256"] == probe.capture_implementation_digest(REPO)
    assert summary["code_identity"] == c2.implementation_fingerprint(REPO)
    assert summary["attempt_id"].startswith("capture-probe-")
    index = json.loads((out / "evidence-index.json").read_text())
    names = sorted(Path(e["path"]).name for e in index["files"])
    assert names == sorted(["summary.json"] + [f"{i:03d}.json" for i in range(1, 21)])
    for entry in index["files"]:
        path = Path(entry["path"])
        path = path if path.is_absolute() else REPO / path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    assert all(not Path(e["path"]).name == "evidence-index.json" for e in index["files"])


async def test_output_dir_with_existing_evidence_is_refused(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "summary.json").write_text("{}")
    ops = FakeOps()
    with pytest.raises(probe.ProbeRefused):
        await probe.probe_capture(_write_position(tmp_path), samples=20, output_dir=out,
                                  ops=ops.bundle())
    assert ops.calls == []


def _copy_capture_files(tmp_path: Path) -> Path:
    for rel in probe.CAPTURE_IMPLEMENTATION_FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, dest)
    return tmp_path


def test_capture_implementation_digest_is_stable_and_tracks_each_file(tmp_path):
    root = _copy_capture_files(tmp_path / "copy")
    baseline = probe.capture_implementation_digest(REPO)
    assert probe.capture_implementation_digest(root) == baseline
    assert set(probe.CAPTURE_IMPLEMENTATION_FILES) == {
        "src/civ_mcp/lua/benchmark_v2.py",
        "src/civ_mcp/arena/benchmark_state_v2.py",
        "src/civ_mcp/arena/benchmark_capture.py",
        "src/civ_mcp/connection.py",
        "src/civ_mcp/tuner_client.py",
        "src/civ_mcp/arena/benchmark_state.py",
        "src/civ_mcp/arena/benchmark_contract_v2.py",
    }
    for rel in probe.CAPTURE_IMPLEMENTATION_FILES:
        target = root / rel
        original = target.read_bytes()
        target.write_bytes(original + b"\n# edit\n")
        assert probe.capture_implementation_digest(root) != baseline, rel
        target.write_bytes(original)
    assert probe.capture_implementation_digest(root) == baseline


def test_probe_module_is_a_fingerprint_dependency():
    deps = list(c2.FINGERPRINT_DEPENDENCIES)
    assert "src/civ_mcp/arena/benchmark_capture_probe.py" in deps
    assert deps == sorted(set(deps))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli(tmp_path, monkeypatch, ops: FakeOps, *extra, position=None):
    monkeypatch.setattr(probe, "production_ops", lambda: ops.bundle())
    position = position or _write_position(tmp_path)
    return probe.main(["--position", str(position), "--samples", "20",
                       "--output-dir", str(tmp_path / "out"), *extra])


def test_cli_exit_zero_on_pass_and_writes_provenance(tmp_path, monkeypatch):
    prov = tmp_path / "prov" / "probe.json"
    assert _cli(tmp_path, monkeypatch, FakeOps(), "--write-provenance", str(prov)) == 0
    record = json.loads(prov.read_text())
    assert "rows" not in record
    assert record["verdict"]["passed"] is True
    index_bytes = (tmp_path / "out" / "evidence-index.json").read_bytes()
    assert record["evidence_index_sha256"] == hashlib.sha256(index_bytes).hexdigest()


def test_cli_exit_one_on_failed_gate(tmp_path, monkeypatch):
    ops = FakeOps(io={"pre_drain_s": 0.1, "post_drain_s": 0.1, "lua_executions": 2})
    assert _cli(tmp_path, monkeypatch, ops) == 1
    assert (tmp_path / "out" / "summary.json").is_file()


def test_cli_exit_two_when_not_the_positive_control(tmp_path, monkeypatch):
    ops = FakeOps()
    position = _write_position(tmp_path, split="development")
    assert _cli(tmp_path, monkeypatch, ops, position=position) == 2
    assert ops.calls == []
