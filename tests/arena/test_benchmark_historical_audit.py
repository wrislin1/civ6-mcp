"""Offline regression: the historical uncredited-actions audit from tracked evidence."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from civ_mcp.arena.benchmark_audit import reproduce_audit

FIXTURE = Path("tests/arena/fixtures/builder_uncredited_audit_v1.json")


def test_historical_evaluators_match_reviewed_commit():
    expected = {
        "src/civ_mcp/arena/benchmark_report.py": "1a417f60a89effb9d58a1620dde4f875e226da6f3e33c2406d2f8903cee1ec5c",
        "src/civ_mcp/arena/action_metrics.py": "652923662e2a87f1437e93d2abc132996eadc5859bbab4c558f7cfd4e2c07709",
    }
    for path, digest in expected.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest


def test_all_raw_trials_reproduce_historical_membership():
    result = reproduce_audit(Path("tests/arena/fixtures/builder_uncredited_audit_v1.json"), root=Path("."))
    assert result["membership_matches"]
    assert result["trial_count"] == 96
    assert result["affected_trial_count"] == 64
    assert result["uncredited_count"] == 117
    assert result["tag_counts"] == {
        "farm_on_own_tile": 28, "closer_to_public_task": 13,
        "same_distance_to_public_task": 36, "farther_from_public_task": 9,
        "non_builder_move": 31,
    }
    assert result["declared_harm_count"] == 0
    assert result["undercredited_completion_count"] == 2


def _copy(tmp_path: Path, mutate) -> Path:
    fixture = json.loads(FIXTURE.read_text())
    mutate(fixture)
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture))
    return path


def test_altered_expectation_mismatches(tmp_path):
    def flip(fixture):
        entry = next(e for e in fixture["expected_membership"]
                     if e["tag"] == "same_distance_to_public_task")
        entry["tag"] = "farther_from_public_task"
    result = reproduce_audit(_copy(tmp_path, flip), root=Path("."))
    assert not result["membership_matches"]
    diff = result["membership_diff"]
    assert len(diff["missing"]) == 1 and len(diff["extra"]) == 1


def test_altered_input_hash_fails_before_scoring(tmp_path):
    def alter(fixture):
        entry = next(e for e in fixture["inputs"] if e["kind"] == "raw_trial")
        entry["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="historical input changed"):
        reproduce_audit(_copy(tmp_path, alter), root=Path("."))


def test_raw_path_subset_fails_before_scoring(tmp_path):
    def drop(fixture):
        raw = [i for i, e in enumerate(fixture["inputs"]) if e["kind"] == "raw_trial"]
        del fixture["inputs"][raw[-1]]
    with pytest.raises(ValueError, match="raw trial set"):
        reproduce_audit(_copy(tmp_path, drop), root=Path("."))


def test_fixture_inputs_are_git_tracked():
    fixture = json.loads(FIXTURE.read_text())
    tracked = set(subprocess.check_output(["git", "ls-files"], text=True).split("\n"))
    paths = [e["path"] for e in fixture["inputs"] + fixture["evaluator_files"]
             + fixture["supplements"]]
    assert sum(e["kind"] == "raw_trial" for e in fixture["inputs"]) == 96
    assert [p for p in paths if p not in tracked] == []
