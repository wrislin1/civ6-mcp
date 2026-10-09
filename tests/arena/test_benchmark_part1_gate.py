"""Part 1 acceptance gate: table-driven packet requirements, the family gate,
the preflight probe binding and the preregistered budget/contract records.

Evidence files live in a temporary Git repository so the tracked-inventory
requirement runs against real `git ls-files`; no game, network or FireTuner.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from civ_mcp.arena import benchmark_authoring as authoring
from civ_mcp.arena.benchmark_authoring import check_part1_gate, check_part1_packet
from civ_mcp.arena.benchmark_capture_probe import capture_implementation_digest
from civ_mcp.arena.benchmark_contract_v2 import (
    FINGERPRINT_DEPENDENCIES,
    implementation_fingerprint,
)
from civ_mcp.arena.benchmark_manifest_v2 import load_toolset

REPO = Path(__file__).resolve().parents[2]
CODE = "c" * 64
TOOLSET = {"source_sha256": "a" * 64, "schemas_sha256": "b" * 64}
STATE = "d" * 64
PROBE_PATH = "benchmarks/provenance/plan3-part1-capture-probe.json"


def test_null_script_without_capture_timings_cannot_pass():
    result = check_part1_packet({"position_id": "incomplete", "null": {
        "gross_credit": 0, "harm_total": 0, "primary_score": 0,
        "initial_digest": "same", "final_digest": "same", "capture_records": []}})
    assert not result["passed"]
    assert "null_capture_timing" in result["failed_requirements"]


# ---------------------------------------------------------------------------
# Synthetic evidence repository
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _write(repo: Path, rel: str, doc) -> dict[str, str]:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(doc, sort_keys=True).encode()
    path.write_bytes(data)
    return {"path": rel, "sha256": hashlib.sha256(data).hexdigest()}


def _capture(duration=0.4):
    return {"phase": "step", "complete": True, "cancelled": False, "duration_s": duration,
            "io": {"lua_executions": 1}}


def _case(attempt: str, case_id: str, tags, *, script: str, score=1.0, credits=None,
          harms=(), negative_for=()):
    gross = sum((credits or {}).values())
    harm_total = sum(4 for h in harms if h["fired"] and not h["compensated"])
    return {"case_id": case_id, "tags": list(tags), "live": True, "passed": True, "error": None,
            "evidence": f"{attempt}/validation/trials/{case_id}.json",
            "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
            "actor_kind": "scripted", "counting": False, "pilot_informed": False,
            "primary_score": score, "gross_credit": gross, "harm_total": harm_total,
            "objective_credits": dict(credits or {}), "harms": [dict(h) for h in harms],
            "negative_for": list(negative_for), "capture_records": [_capture(), _capture(0.9)],
            "validation_failures": [], "declared_rejections": [], "undefined_support": []}


def _fired(hid):
    return {"id": hid, "fired": True, "compensated": False}


def _avoided(hid):
    return {"id": hid, "fired": False, "compensated": False}


def _cases(attempt: str) -> list[dict]:
    full = {"o1": 4, "o2": 4, "o3": 4}
    return [
        _case(attempt, "joint-full", ["joint_full"], script="joint", credits=full,
              harms=[_avoided("h1")], negative_for=["h1"]),
        _case(attempt, "alternative-full", ["alternative_full"], script="alt", credits=full),
        _case(attempt, "partial", ["partial"], script="partial", score=6 / 12,
              credits={"o1": 2, "o2": 2, "o3": 2}),
        _case(attempt, "closer-only", ["closer_only"], script="closer", score=0.0,
              credits={"o1": 0, "o2": 0, "o3": 0}),
        _case(attempt, "harm", ["harm_only"], script="harm", score=-4 / 12,
              credits={"o1": 0, "o2": 0, "o3": 0}, harms=[_fired("h1")]),
        _case(attempt, "repeat-undo", ["repeat_undo"], script="repeat", score=0.0,
              credits={"o1": 0, "o2": 0, "o3": 0}),
        {**_case(attempt, "malformed-predicate", ["malformed"], script="offline"),
         "live": False, "evidence": "tests/arena/test_benchmark_predicates_v2.py",
         "capture_records": []},
    ]


def _packet(repo: Path, family: str = "builder", *, scenario: str | None = None,
            predecessor: dict | None = None) -> dict:
    scenario = scenario or f"{family}-a1"
    attempt = f"benchmark_runs/plan3-part1/{scenario}/attempt-1"
    probe = _write(repo, PROBE_PATH, {
        "position_id": "builder-posctrl-v1", "samples": 20,
        "verdict": {"passed": True, "reasons": []},
        "capture_implementation_sha256": capture_implementation_digest(REPO),
        "evidence_index_sha256": "e" * 64, "limitations": ["positive control only"]})
    cases = _cases(attempt)
    for case in cases:
        if case["live"]:
            _write(repo, case["evidence"], {"case_id": case["case_id"]})
    (repo / "tests/arena").mkdir(parents=True, exist_ok=True)
    (repo / "tests/arena/test_benchmark_predicates_v2.py").write_text("# offline\n")
    refs = {name: _write(repo, f"{attempt}/stages/{name}.json", {"stage": name})
            for name in ("verify", "menu-check", "validate", "archive", "probe")}
    null_trial = _write(repo, f"{attempt}/validation/trials/null-discovery.json", {"null": 1})
    journal = _write(repo, f"{attempt}/authoring-journal.json", {"clock": "closed"})
    offline = _write(repo, "benchmarks/provenance/plan3-part1-offline-preflight.json",
                     {"historical_audit": {"membership_matches": True}})
    attempt_files = sorted(p.relative_to(repo).as_posix()
                           for p in (repo / attempt).rglob("*") if p.is_file())
    index = _write(repo, f"{attempt}/evidence-index.json", {"files": [
        {"path": rel, "sha256": hashlib.sha256((repo / rel).read_bytes()).hexdigest()}
        for rel in attempt_files]})
    attempts = [{"scenario_id": scenario, "attempt_dir": attempt, "status": "passed",
                 "duration_s": 7200.0, "journal": journal, "evidence_index": index,
                 "substitution": None}]
    if predecessor is not None:
        attempts[0]["substitution"] = {"predecessor": predecessor["scenario_id"],
                                       "reason": "setup unreachable",
                                       "material_change": "moved the escort"}
        attempts.insert(0, predecessor)
    packet = {
        "position_id": f"{scenario}-v1", "family": family, "scenario_id": scenario,
        "code_identity": CODE, "contract_identity": CODE, "toolset_identity": dict(TOOLSET),
        "expected_state_sha256": STATE, "pilot_informed": False,
        "verify": {"evidence": refs["verify"]["path"], "cycles_completed": 12,
                   "digests": [STATE] * 12},
        "menu_check": {"evidence": refs["menu-check"]["path"], "loader_confirmed": True,
                       "reconnect": {"reconnected": True}, "digest_matches": True,
                       "identity_matches": True},
        "restore": {"evidence": refs["validate"]["path"], "reloaded": True,
                    "reconnect": {"reconnected": True}, "digest": STATE},
        "objectives": [{"id": oid, "rungs": [2, 4]} for oid in ("o1", "o2", "o3")],
        "declared_harms": ["h1"],
        "cases": cases,
        "null": {"evidence": null_trial["path"], "live": True, "gross_credit": 0,
                 "harm_total": 0, "primary_score": 0, "initial_digest": STATE,
                 "final_digest": STATE,
                 "steps": [{"before": STATE, "after": STATE}, {"before": STATE, "after": STATE}],
                 "observation_calls": 3,
                 "discoverability": [{"id": "repair_site", "discoverable": True}],
                 "capture_scope": "full", "capture_records": [_capture(), _capture(1.2)],
                 "actor_kind": "scripted", "counting": False, "pilot_informed": False},
        "attempts": attempts,
        "offline_audit": {"evidence": offline["path"], "membership_matches": True,
                          "trial_count": 96, "uncredited_count": 117},
        "positive_control_probe": probe,
        "evidence_index": index,
        "measured_parameters": [{"name": "minimum_damage", "value": 30,
                                 "evidence": [refs["probe"]["path"]]}],
    }
    _git(repo, "add", "-A")
    return packet


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    return tmp_path


@pytest.fixture
def packet(repo):
    return _packet(repo)


def _check(packet, repo):
    return check_part1_packet(packet, root=repo)


def test_complete_packet_passes_with_evidence_for_every_requirement(packet, repo):
    result = _check(packet, repo)
    assert result["passed"] is True, result["failed_requirements"]
    assert result["failed_requirements"] == []
    assert result["position_id"] == "builder-a1-v1"
    for name, entry in result["requirements"].items():
        assert entry["passed"] is True, name
        assert set(entry) == {"passed", "evidence", "detail"}
    assert result["requirements"]["twelve_cycle_verify"]["evidence"] == [packet["verify"]["evidence"]]


def _set(path, value):
    def mutate(p, repo):
        node = p
        for key in path[:-1]:
            node = node[key]
        if value is _DELETE:
            del node[path[-1]]
        else:
            node[path[-1]] = value
    return mutate


_DELETE = object()


def _untrack_case_trial(p, repo):
    _git(repo, "rm", "-q", "--cached", p["cases"][0]["evidence"])


def _add_failed_attempt_without_index(p, repo):
    p["attempts"].insert(0, {"scenario_id": p["scenario_id"], "attempt_dir": "x",
                             "status": "failed", "duration_s": 100.0,
                             "journal": p["attempts"][0]["journal"], "substitution": None})


def _alt_same_script(p, repo):
    p["cases"][1]["script_sha256"] = p["cases"][0]["script_sha256"]


def _undeclared_rejection(p, repo):
    p["cases"][0]["validation_failures"] = [
        {"reason": "rejected_operation", "step": 2, "tool_name": "move_unit"}]


REMOVALS = [
    ("twelve_cycle_verify", _set(["verify", "digests"], [STATE] * 11)),
    ("twelve_cycle_verify", _set(["verify", "digests"], [STATE] * 11 + ["x"])),
    ("menu_recovery_verified", _set(["menu_check", "digest_matches"], False)),
    ("menu_recovery_verified", _set(["menu_check", "loader_confirmed"], _DELETE)),
    ("joint_full_case", _set(["cases", 0, "primary_score"], 11 / 12)),
    ("alternative_full_case", _set(["cases", 1, "primary_score"], 0.75)),
    ("alternative_full_case", _alt_same_script),
    ("intermediate_rungs_covered", _set(["cases", 2, "objective_credits"],
                                        {"o1": 2, "o2": 2, "o3": 4})),
    ("closer_only_zero", _set(["cases", 3, "gross_credit"], 2)),
    ("harm_positive_cases", _set(["cases", 4, "harms"], [_avoided("h1")])),
    ("harm_negative_cases", _set(["cases", 0, "negative_for"], [])),
    ("null_digest_chain", _set(["null", "steps", 0, "after"], "x")),
    ("null_digest_chain", _set(["null", "steps"], [{"before": STATE, "after": "y"},
                                                   {"before": "z", "after": STATE}])),
    ("null_digest_chain", _set(["null", "final_digest"], "x")),
    ("null_digest_chain", _set(["null", "steps"], [{"before": STATE, "after": STATE},
                                                   {"before": "y", "after": "y"},
                                                   {"before": STATE, "after": STATE}])),
    ("null_observation_calls", _set(["null", "observation_calls"], 0)),
    ("null_observation_calls", _set(["null", "discoverability", 0, "discoverable"], False)),
    ("null_capture_timing", _set(["null", "capture_records", 1, "duration_s"], 2.5)),
    ("null_capture_timing", _set(["null", "capture_scope"], "partial")),
    ("capture_records_complete", _set(["cases", 2, "capture_records", 0, "complete"], False)),
    ("capture_records_complete", _set(["cases", 2, "capture_records", 0, "io"],
                                      {"lua_executions": 2})),
    ("final_restore_verified", _set(["restore", "digest"], "x")),
    ("failed_attempt_history", _add_failed_attempt_without_index),
    ("failed_attempt_history", _set(["attempts", 0, "journal"], _DELETE)),
    ("offline_audit", _set(["offline_audit", "membership_matches"], False)),
    ("offline_audit", _set(["offline_audit", "uncredited_count"], 116)),
    ("positive_control_timing_probe", _set(["positive_control_probe"], _DELETE)),
    ("tracked_evidence_inventory", _untrack_case_trial),
    ("tracked_evidence_inventory", _set(["evidence_index", "sha256"], "0" * 64)),
    ("no_model_provenance", _set(["cases", 2, "actor_kind"], "model")),
    ("no_model_provenance", _set(["cases", 2, "counting"], True)),
    ("no_model_provenance", _set(["pilot_informed"], True)),
    ("measured_parameters_frozen", _set(["measured_parameters", 0, "evidence"], [])),
    ("rejections_declared", _undeclared_rejection),
    ("scenario_duration", _set(["attempts", 0, "duration_s"], 10800.5)),
    ("scenario_duration", _set(["attempts", 0, "duration_s"], _DELETE)),
    ("no_undefined_predicate_support", _set(["cases", 2, "undefined_support"],
                                            [{"predicate": "tile_matches", "field": "yields"}])),
    ("live_vs_offline_cases_distinguished", _set(["cases", 6, "live"], _DELETE)),
    ("validation_cases_passed", _set(["cases", 5, "passed"], False)),
]


@pytest.mark.parametrize("name,mutate", REMOVALS, ids=[f"{n}-{i}" for i, (n, _) in
                                                       enumerate(REMOVALS)])
def test_removing_one_piece_fails_for_exactly_that_reason(packet, repo, name, mutate):
    broken = copy.deepcopy(packet)
    mutate(broken, repo)
    result = _check(broken, repo)
    assert result["passed"] is False
    assert result["failed_requirements"] == [name]
    assert result["requirements"][name]["detail"]


def test_offline_fixture_is_not_counted_as_a_live_witness(packet, repo):
    broken = copy.deepcopy(packet)
    broken["cases"][1]["live"] = False
    result = _check(broken, repo)
    assert "alternative_full_case" in result["failed_requirements"]


def test_altered_tracked_file_fails_inventory(packet, repo):
    (repo / packet["cases"][0]["evidence"]).write_text("{}")
    result = _check(packet, repo)
    assert result["failed_requirements"] == ["tracked_evidence_inventory"]


def test_probe_with_stale_capture_digest_fails(packet, repo):
    doc = json.loads((repo / PROBE_PATH).read_text())
    doc["capture_implementation_sha256"] = "0" * 64
    broken = copy.deepcopy(packet)
    broken["positive_control_probe"] = _write(repo, PROBE_PATH, doc)
    _git(repo, "add", "-A")
    result = _check(broken, repo)
    assert result["failed_requirements"] == ["positive_control_timing_probe"]


def _predecessor(repo: Path, **overrides) -> dict:
    attempt = "benchmark_runs/plan3-part1/builder-a1/attempt-1"
    journal = _write(repo, f"{attempt}/authoring-journal.json", {"clock": "expired"})
    index = _write(repo, f"{attempt}/evidence-index.json", {"files": [journal]})
    _git(repo, "add", "-A")
    return {"scenario_id": "builder-a1", "attempt_dir": attempt, "status": "failed",
            "duration_s": 10800.0, "journal": journal, "evidence_index": index,
            "substitution": None, **overrides}


def test_substitute_passes_only_with_both_scenario_durations(repo):
    good = _packet(repo, scenario="builder-a2", predecessor=_predecessor(repo))
    result = _check(good, repo)
    assert result["passed"] is True, result["failed_requirements"]
    detail = result["requirements"]["scenario_duration"]["detail"]
    assert "18000" in detail
    missing = copy.deepcopy(good)
    del missing["attempts"][0]["duration_s"]
    result = _check(missing, repo)
    assert result["failed_requirements"] == ["scenario_duration"]


def test_substitute_without_declaration_fails(repo):
    packet = _packet(repo, scenario="builder-a2", predecessor=_predecessor(repo))
    packet["attempts"][1]["substitution"] = None
    assert _check(packet, repo)["failed_requirements"] == ["scenario_duration"]


def test_third_scenario_fails(repo):
    packet = _packet(repo, scenario="builder-a3", predecessor=_predecessor(repo))
    packet["attempts"].insert(0, _predecessor(repo, scenario_id="builder-a0"))
    assert _check(packet, repo)["failed_requirements"] == ["scenario_duration"]


# ---------------------------------------------------------------------------
# Family gate
# ---------------------------------------------------------------------------

def _preflight(probe: dict, **changes) -> dict:
    doc = {"passed": True, "failed_requirements": [], "code_identity": CODE,
           "recipes": [{"family": f, "toolset_identity": dict(TOOLSET)}
                       for f in ("builder", "city", "tactical")],
           "probe": {"present": True, "path": probe["path"], "sha256": probe["sha256"]}}
    doc.update(changes)
    return doc


@pytest.fixture
def family_packets(repo):
    return [_packet(repo, family) for family in ("builder", "city", "tactical")]


def test_gate_passes_with_three_passing_families(family_packets, repo):
    result = check_part1_gate(family_packets, _preflight(family_packets[0]["positive_control_probe"]),
                              root=repo)
    assert result["passed"] is True, result["failed_requirements"]
    assert set(result["families"]) == {"builder", "city", "tactical"}
    assert result["families"]["city"]["family_total_s"] == 7200.0


@pytest.mark.parametrize("name,mutate", [
    ("family_coverage", lambda packets, pre: packets.pop()),
    ("identity_match", lambda packets, pre: packets[1].update(code_identity="x" * 64)),
    ("identity_match", lambda packets, pre: packets[2].update(contract_identity="x" * 64)),
    ("identity_match", lambda packets, pre: packets[0].update(toolset_identity={"x": 1})),
    ("identity_match", lambda packets, pre: pre.update(code_identity="x" * 64)),
    ("probe_binding", lambda packets, pre: pre["probe"].update(sha256="0" * 64)),
    ("preflight_passed", lambda packets, pre: pre.update(
        passed=False, failed_requirements=["positive_control_timing_probe"])),
    ("packets_passed", lambda packets, pre: packets[1]["verify"].update(cycles_completed=11)),
])
def test_gate_fails_for_named_reason(family_packets, repo, name, mutate):
    pre = _preflight(family_packets[0]["positive_control_probe"])
    mutate(family_packets, pre)
    result = check_part1_gate(family_packets, pre, root=repo)
    assert result["passed"] is False
    assert name in result["failed_requirements"]


def test_gate_cli_writes_result_and_exits_nonzero_on_failure(family_packets, repo, tmp_path,
                                                             monkeypatch):
    paths = []
    for p in family_packets:
        path = tmp_path / f"{p['family']}-packet.json"
        path.write_text(json.dumps(p))
        paths.append(str(path))
    pre = tmp_path / "pre.json"
    pre.write_text(json.dumps(_preflight(family_packets[0]["positive_control_probe"])))
    monkeypatch.setattr(authoring, "_REPO_ROOT", repo)
    out = tmp_path / "gate.json"
    assert authoring.main(["gate", "--packets", *paths, "--preflight", str(pre),
                           "--output", str(out)]) == 0
    assert json.loads(out.read_text())["passed"] is True
    assert authoring.main(["gate", "--packets", *paths[:2], "--preflight", str(pre),
                           "--output", str(out)]) == 1
    assert "family_coverage" in json.loads(out.read_text())["failed_requirements"]


# ---------------------------------------------------------------------------
# Preflight probe binding (offline)
# ---------------------------------------------------------------------------

RECIPES = [REPO / "benchmarks/recipes" / f"plan3-{f}-a1.yaml" for f in ("builder", "city",
                                                                         "tactical")]


def _probe_doc(**changes) -> dict:
    doc = {"position_id": "builder-posctrl-v1", "samples": 20,
           "verdict": {"passed": True, "reasons": []},
           "capture_implementation_sha256": capture_implementation_digest(REPO),
           "evidence_index_sha256": "e" * 64, "limitations": ["positive control only"]}
    doc.update(changes)
    return doc


def _pytest_result(tmp_path: Path, **changes) -> Path:
    (tmp_path / "pytest.txt").write_text("1 passed\n")
    doc = {"command": "uv run pytest -q", "exit_code": 0, "passed": 1, "failed": 0,
           "errors": 0, "code_identity": implementation_fingerprint(REPO)}
    doc.update(changes)
    path = tmp_path / "pytest-result.json"
    path.write_text(json.dumps(doc))
    return path


@pytest.fixture
def offline_audit(monkeypatch):
    monkeypatch.setattr(authoring, "reproduce_audit", lambda fixture_path, *, root: {
        "membership_matches": True, "trial_count": 96, "uncredited_count": 117,
        "affected_trial_count": 64})
    monkeypatch.setattr(authoring, "production_ops",
                        lambda: pytest.fail("preflight must never build live operations"))


def test_preflight_with_missing_probe_reports_failed_requirement(tmp_path, offline_audit):
    result = authoring.preflight(RECIPES, root=REPO, probe_path=tmp_path / "absent.json",
                                 pytest_result_path=_pytest_result(tmp_path))
    assert result["passed"] is False
    assert result["probe"] == {"present": False}
    assert result["failed_requirements"] == ["positive_control_timing_probe"]


def test_preflight_rejects_probe_with_mismatched_capture_digest(tmp_path, offline_audit):
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps(_probe_doc(capture_implementation_sha256="0" * 64)))
    result = authoring.preflight(RECIPES, root=REPO, probe_path=probe,
                                 pytest_result_path=_pytest_result(tmp_path))
    assert result["failed_requirements"] == ["positive_control_timing_probe"]


def test_preflight_rejects_probe_with_wrong_sample_count(tmp_path, offline_audit):
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps(_probe_doc(samples=19)))
    result = authoring.preflight(RECIPES, root=REPO, probe_path=probe,
                                 pytest_result_path=_pytest_result(tmp_path))
    assert result["failed_requirements"] == ["positive_control_timing_probe"]


def test_preflight_binds_passing_probe_suite_and_recipe_versions(tmp_path, offline_audit):
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps(_probe_doc()))
    result = authoring.preflight(RECIPES, root=REPO, probe_path=probe,
                                 pytest_result_path=_pytest_result(tmp_path))
    assert result["passed"] is True, result["failed_requirements"]
    assert result["probe"]["present"] is True
    assert result["probe"]["evidence_index_sha256"] == "e" * 64
    assert result["probe"]["limitations"] == ["positive control only"]
    assert result["probe"]["sha256"] == hashlib.sha256(probe.read_bytes()).hexdigest()
    assert [(r["recipe_id"], r["version"]) for r in result["recipes"]] == [
        ("plan3-builder-a1", 1), ("plan3-city-a1", 1), ("plan3-tactical-a1", 1)]
    assert result["full_suite_result"]["passed"] is True
    assert len(result["historical_audit"]["fixture_sha256"]) == 64
    assert len(result["historical_audit"]["inputs_sha256"]) == 64


def test_preflight_fails_on_failed_or_stale_full_suite(tmp_path, offline_audit):
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps(_probe_doc()))
    for changes in ({"exit_code": 1, "failed": 2}, {"code_identity": "0" * 64}):
        result = authoring.preflight(RECIPES, root=REPO, probe_path=probe,
                                     pytest_result_path=_pytest_result(tmp_path, **changes))
        assert result["failed_requirements"] == ["full_suite_result"]


# ---------------------------------------------------------------------------
# Preregistered records
# ---------------------------------------------------------------------------

def test_budget_record_equals_the_preregistered_values():
    doc = yaml.safe_load((REPO / "benchmarks/contracts/plan3-part1-budget.yaml").read_text())
    assert doc == {
        "schema_version": "2.0.0", "budget_id": "plan3-part1-v1",
        "unit": "backend_round_trip", "max_steps": 15, "script_episode_wall_s": 300,
        "capture_wall_s": 2.0, "capture_lua_executions": 1,
        "positive_control_timing_samples": 20,
        "model_episode_wall_formula": "max(300, ceil(15 * p95_roundtrip_s * 1.5))",
        "primary_comparable_to_plan2": False, "benefit_ledger_gates_advancement": False,
        "authoring_seconds_per_scenario": 10800, "scenario_substitutions_per_family": 1,
    }


def test_candidate_contract_lists_dependencies_and_toolset_identity():
    doc = yaml.safe_load((REPO / "benchmarks/contracts/instrument-v2.yaml").read_text())
    assert doc["status"] == "candidate"
    assert doc["released"] is False
    assert doc["primary_comparable_to_plan2"] is False
    assert doc["fingerprint_dependencies"] == list(FINGERPRINT_DEPENDENCIES)
    assert "src/civ_mcp/arena/benchmark_part1_gate.py" in doc["fingerprint_dependencies"]
    toolset = load_toolset(REPO / "benchmarks/toolsets/plan3-part1-v1.yaml")
    assert doc["toolset"] == {"toolset_id": "plan3-part1-v1",
                              "path": "benchmarks/toolsets/plan3-part1-v1.yaml",
                              "identity": toolset["identity"]}
    for key in ("evidence_schema_version", "predicate_schema_version", "report_schema_version"):
        assert doc[key] == "2.0.0"
