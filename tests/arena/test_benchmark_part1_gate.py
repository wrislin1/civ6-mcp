"""Part 1 acceptance gate: finish-packet resolution from raw files, table-driven
requirements, the family gate, the preflight probe binding and the
preregistered budget/contract records.

Each family's evidence is a realistic synthetic attempt (stage records,
journal, validation lock/results, raw trials, derived reports, case
documents, evidence index, finish packet) built from the real Part 1 recipes'
objectives, harms and case tags, inside a temporary Git repository so the
tracked-inventory requirement runs against real `git ls-files`. No game,
network or FireTuner.
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
from civ_mcp.arena import benchmark_part1_gate as gate
from civ_mcp.arena.benchmark_authoring import check_part1_gate, check_part1_packet
from civ_mcp.arena.benchmark_capture_probe import capture_implementation_digest
from civ_mcp.arena.benchmark_contract_v2 import (
    FINGERPRINT_DEPENDENCIES,
    implementation_fingerprint,
)
from civ_mcp.arena.benchmark_manifest_v2 import load_toolset
from civ_mcp.arena.benchmark_part1_evidence import load_gate_evidence

REPO = Path(__file__).resolve().parents[2]
CODE = implementation_fingerprint(REPO)
CAPTURE = capture_implementation_digest(REPO)
TOOLSET = load_toolset(REPO / "benchmarks/toolsets/plan3-part1-v1.yaml")["identity"]
STATE = "d" * 64
COVERAGE = {"include_owned_tiles": True, "area": [[1, 1], [1, 2]], "tracked_targets": []}
PROBE_PATH = "benchmarks/provenance/plan3-part1-capture-probe.json"
PREFLIGHT_PATH = "benchmarks/provenance/plan3-part1-offline-preflight.json"


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


def _sha(repo: Path, rel: str) -> str:
    return hashlib.sha256((repo / rel).read_bytes()).hexdigest()


POSITIVE_TAG_HARM = {"escort_loss": "escort-loss", "new_exposure": "new-exposure",
                     "destructive_placement": "destructive-placement",
                     "military_loss": "military-loss"}
PARTIAL_TAGS = {"partial_repair", "partial_resource", "partial_food", "housing_partial",
                "meaningful_damage", "reinforcement_partial"}
ZERO_TAGS = {"null_discovery", "closer_only", "uncredited_preparation", "harm_only"}


def _scores(recipe: dict) -> dict[str, dict]:
    """Per case: objective credits and charged harms consistent with its tags."""
    objectives = [(o["id"], [r["points"] for r in o["rungs"]]) for o in recipe["objectives"]]
    partial_targets = iter([oid for oid, rungs in objectives if 2 in rungs] * 3)
    harms = [h["id"] for h in recipe["harms"]]
    out = {}
    for case in recipe["cases"]:
        tags = set(case["tags"])
        charged = sorted({POSITIVE_TAG_HARM[t] for t in tags if t in POSITIVE_TAG_HARM})
        if "mixed_gain_loss" in tags and not charged:
            charged = [harms[0]]
        credits = {oid: 0 for oid, _ in objectives}
        if tags & {"joint_full", "alternative_full"}:
            credits = {oid: max(rungs) for oid, rungs in objectives}
        elif tags & PARTIAL_TAGS:
            credits[next(partial_targets)] = 2
        elif not tags & ZERO_TAGS:
            credits[objectives[0][0]] = max(objectives[0][1])
        gross, harm = sum(credits.values()), 4 * len(charged)
        out[case["case_id"]] = {"credits": credits, "charged": charged, "gross": gross,
                                "harm": harm, "primary": (gross - harm) / 12}
    return out


def _stage(repo, attempt, seq, stage, evidence, *, status="passed", scenario):
    return _write(repo, f"{attempt}/stages/{seq:03d}-{stage}.json", {
        "schema_version": "2.0.0", "sequence": seq, "stage": stage, "status": status,
        "scenario_id": scenario, "error": None, "evidence": evidence, "files": [],
        "recipe": {"path": f"benchmarks/recipes/{scenario}.yaml", "recipe_id": scenario,
                   "version": 1}})


def _journal(repo, attempt, family, scenarios):
    return _write(repo, f"{attempt}/authoring-journal.json", {
        "schema_version": "2.0.0", "families": {family: {
            "scenarios": scenarios,
            "total_elapsed_s": sum(s["recorded_elapsed_s"] for s in scenarios)}}})


def _scenario_record(scenario, family, *, attempt=1, status="passed", elapsed=7200.0,
                     predecessor=None):
    return {"scenario_id": scenario, "family": family, "attempt": attempt, "status": status,
            "recorded_elapsed_s": elapsed, "predecessor": predecessor,
            "reason": "setup unreachable" if predecessor else None,
            "material_change": "moved the escort" if predecessor else None}


def _index(repo, attempt, externals=()):
    files = sorted({p.relative_to(repo).as_posix() for p in (repo / attempt).rglob("*")
                    if p.is_file() and p.name != "evidence-index.json"} | set(externals))
    return _write(repo, f"{attempt}/evidence-index.json",
                  {"files": [{"path": f, "sha256": _sha(repo, f)} for f in files]})


def _shared_files(repo):
    _write(repo, PROBE_PATH, {
        "position_id": "builder-posctrl-v1", "samples": 20,
        "verdict": {"passed": True, "reasons": []},
        "capture_implementation_sha256": CAPTURE, "evidence_index_sha256": "e" * 64,
        "limitations": ["positive control only"]})
    _write(repo, PREFLIGHT_PATH, {"historical_audit": {
        "membership_matches": True, "trial_count": 96, "uncredited_count": 117}})


def _predecessor(repo, family, *, indexed=True, attempt_name="attempt-1"):
    scenario = f"{family}-a1"
    attempt = f"benchmark_runs/plan3-part1/{scenario}/{attempt_name}"
    _stage(repo, attempt, 1, "survey", {}, status="failed", scenario=scenario)
    _journal(repo, attempt, family, [_scenario_record(scenario, family, status="failed",
                                                      elapsed=10800.0)])
    if indexed:
        _stage(repo, attempt, 2, "abandon", {"reason": "clock expired"}, status="abandoned",
               scenario=scenario)
        _index(repo, attempt)
    _git(repo, "add", "-A")


def _build(repo: Path, family: str = "builder", *, substitute: bool = False,
           extra_packet: dict | None = None) -> Path:
    recipe = yaml.safe_load((REPO / f"benchmarks/recipes/plan3-{family}-a1.yaml").read_text())
    scenario = f"{family}-a2" if substitute else f"{family}-a1"
    pid = f"{scenario}-v1"
    attempt = f"benchmark_runs/plan3-part1/{scenario}/attempt-1"
    run_dir = f"{attempt}/validation/{pid}/{pid}-validation"
    _shared_files(repo)
    position = _write(repo, f"benchmarks/positions/{pid}.json", {
        "schema_version": "2.0.0", "position_id": pid, "family": family,
        "contract_identity": CODE, "toolset": {"identity": TOOLSET},
        "expected_state_sha256": STATE, "coverage": COVERAGE, "pilot_informed": False,
        "rubric": {"objectives": [{"id": o["id"], "rungs": [{"points": r["points"]}
                                                           for r in o["rungs"]]}
                                  for o in recipe["objectives"]],
                   "harms": [{"id": h["id"]} for h in recipe["harms"]]}})
    probe_file = _write(repo, f"{attempt}/probes/003-lethal.json", {"delta": 40})
    measured = [{"name": "minimum_damage", "value": 30, "evidence": [probe_file["path"]]}] \
        if family == "tactical" else []
    authoring_input = _write(repo, f"benchmarks/provenance/{pid}-authoring.json",
                             {"position_id": pid, "measured_parameters": measured})
    scores = _scores(recipe)
    case_paths, results = [], []
    for index, case in enumerate(recipe["cases"]):
        cid, s = case["case_id"], scores[case["case_id"]]
        case_paths.append(_write(repo, f"benchmarks/validation/{pid}/cases/{cid}.json", {
            "case_id": cid, "tags": case["tags"], "declared_rejections": []})["path"])
        null = "null_discovery" in case["tags"]
        steps = [{"step": i, "before": STATE, "after": STATE if null else f"{i:064d}"}
                 for i in range(3)]
        _write(repo, f"{run_dir}/trials/trial-{index:03d}.json", {
            "index": index, "case_id": cid, "script_id": cid,
            "script_sha256": hashlib.sha256(cid.encode()).hexdigest(),
            "actor_kind": "scripted", "counting": False, "coverage": COVERAGE,
            "validation_failures": [],
            "steps": [{"idx": i, "tool_name": "get_units"} for i in range(3)],
            "capture_summary": {"count": 8, "max_s": 0.9, "all_single_execution": True,
                                "lua_executions_total": 8, "unavailable": {}}})
        report = _write(repo, f"{run_dir}/reports/{cid}/report.json", {"score": {
            "objectives": [{"id": k, "credit": v} for k, v in s["credits"].items()],
            "harms": [{"id": h["id"], "status": "charged" if h["id"] in s["charged"]
                       else "not_fired"} for h in recipe["harms"]],
            "gross_credit": s["gross"], "harm_total": s["harm"],
            "primary_score": s["primary"]},
            "digests": {"initial": STATE, "final": STATE if null else "f" * 64,
                        "steps": steps}})
        results.append({"case_id": cid, "trial_index": index, "passed": True, "mechanics": {},
                        "mismatches": {}, "error": None, "report_sha256": report["sha256"]})
    _write(repo, f"{run_dir}/session.json", {"code_identity": CODE})
    validation = _write(repo, f"{run_dir}/validation.json", {
        "suite_id": f"{pid}-validation", "passed": True, "errored": False, "cases": results})
    stages = [("survey", {}), ("apply", {}), ("probe", {}),
              ("archive", {"required_facts": [{"id": "task_site", "discoverable": True}]}),
              ("capture", {"cases": case_paths}),
              ("verify", {"result": {"ok": True, "cycles_completed": 12,
                                     "digests": [STATE] * 12}}),
              ("menu-check", {"loader": "restart_and_load", "loader_result": "Loaded game",
                              "reconnect": {"reconnected": True}, "digest_matches": True,
                              "identity_matches": True}),
              ("validate", {"reports_identical": True, "validation_sha256": validation["sha256"],
                            "restore": {"reloaded": True, "reconnect": {"reconnected": True},
                                        "digest": STATE}}),
              ("finish", {})]
    for seq, (stage, evidence) in enumerate(stages, start=1):
        _stage(repo, attempt, seq, stage, evidence, scenario=scenario)
    records = [_scenario_record(scenario, family, attempt=2 if substitute else 1,
                                predecessor=f"{family}-a1" if substitute else None)]
    journal = _journal(repo, attempt, family, records)
    index = _index(repo, attempt, [position["path"], authoring_input["path"], *case_paths])
    packet = {"schema_version": "2.0.0", "position_id": pid, "scenario_id": scenario,
              "family": family, "authoring_input": authoring_input,
              "position": position, "journal": journal, "evidence_index": index,
              "validation_runs": [{"suite_id": f"{pid}-validation", "run_dir": run_dir,
                                   "validation_sha256": validation["sha256"]}],
              **(extra_packet or {})}
    packet_ref = _write(repo, f"benchmarks/provenance/{pid}.json", packet)
    _git(repo, "add", "-A")
    return repo / packet_ref["path"]


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    return tmp_path


@pytest.fixture
def packet_path(repo):
    return _build(repo)


@pytest.fixture
def view(packet_path, repo):
    return load_gate_evidence(packet_path, root=repo)


def _check(packet, repo):
    return check_part1_packet(packet, root=repo)


# ---------------------------------------------------------------------------
# Resolution from raw files
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("family", ["builder", "city", "tactical"])
def test_finish_packet_path_resolves_and_passes(repo, family):
    result = _check(_build(repo, family), repo)
    assert result["passed"] is True, {n: r["detail"] for n, r in result["requirements"].items()
                                      if not r["passed"]}
    assert result["failed_requirements"] == []
    for name, entry in result["requirements"].items():
        assert set(entry) == {"passed", "evidence", "detail"}, name


def test_view_is_derived_from_raw_files(view):
    assert view["code_identity"] == CODE
    assert view["contract_identity"] == CODE
    assert view["toolset_identity"] == TOOLSET
    assert view["verify"]["evidence"].endswith("stages/006-verify.json")
    assert view["null"]["capture_scope"] == "full"
    assert {c["case_id"] for c in view["cases"]} >= {"null-discovery", "joint-full"}
    assert view["scenarios"][0]["duration_s"] == 7200.0
    assert view["resolution"]["problems"] == []


def test_self_asserted_packet_fields_are_ignored(repo):
    path = _build(repo, extra_packet={
        "code_identity": "x" * 64, "cases": [], "verify": {"cycles_completed": 12,
                                                           "digests": ["bogus"] * 12},
        "null": {"gross_credit": 0}})
    view = load_gate_evidence(path, root=repo)
    assert view["code_identity"] == CODE
    assert view["verify"]["digests"] == [STATE] * 12
    assert len(view["cases"]) == 12
    assert _check(path, repo)["passed"] is True


def test_raw_stage_verdict_overrides_any_claim(repo):
    path = _build(repo, extra_packet={"verify": {"cycles_completed": 12,
                                                 "digests": [STATE] * 12}})
    attempt = path.parent.parent.parent / "benchmark_runs/plan3-part1/builder-a1/attempt-1"
    stage = attempt / "stages/006-verify.json"
    doc = json.loads(stage.read_text())
    doc["evidence"]["result"]["digests"] = [STATE] * 11
    stage.write_text(json.dumps(doc))
    assert "twelve_cycle_verify" in _check(path, repo)["failed_requirements"]


def test_corrupted_referenced_file_fails_resolution(packet_path, repo):
    position = next((repo / "benchmarks/positions").glob("*.json"))
    position.write_bytes(position.read_bytes() + b" ")
    result = _check(packet_path, repo)
    assert "finish_packet_resolved" in result["failed_requirements"]
    assert "sha256 differs" in result["requirements"]["finish_packet_resolved"]["detail"]


def test_altered_validation_json_fails_resolution(packet_path, repo):
    validation = next((repo / "benchmark_runs").rglob("validation.json"))
    validation.write_text("{}")
    assert "finish_packet_resolved" in _check(packet_path, repo)["failed_requirements"]


def test_unindexed_sibling_attempt_fails_history(packet_path, repo):
    _predecessor(repo, "builder", indexed=False, attempt_name="attempt-0")
    result = _check(packet_path, repo)
    assert "failed_attempt_history" in result["failed_requirements"]
    assert "not indexed" in result["requirements"]["failed_attempt_history"]["detail"]


def test_attempts_of_other_families_are_not_counted(packet_path, repo):
    _predecessor(repo, "city", indexed=False)
    assert _check(packet_path, repo)["passed"] is True


ATTEMPT = "benchmark_runs/plan3-part1/builder-a1/attempt-1"


def test_unindexed_post_finish_stage_record_is_not_used(packet_path, repo):
    _write(repo, f"{ATTEMPT}/stages/020-verify.json", {
        "schema_version": "2.0.0", "sequence": 20, "stage": "verify", "status": "passed",
        "scenario_id": "builder-a1", "error": None, "files": [],
        "evidence": {"result": {"ok": True, "cycles_completed": 12, "digests": ["z"] * 12}}})
    _git(repo, "add", "-A")
    view = load_gate_evidence(packet_path, root=repo)
    assert view["verify"]["evidence"] == f"{ATTEMPT}/stages/006-verify.json"
    assert view["verify"]["digests"] == [STATE] * 12
    assert view["resolution"]["unindexed"] == [
        f"unindexed_stage_record: {ATTEMPT}/stages/020-verify.json"]
    result = _check(packet_path, repo)
    assert result["failed_requirements"] == ["evidence_index_complete"]
    assert "020-verify.json" in result["requirements"]["evidence_index_complete"]["detail"]


def test_unindexed_attempt_file_fails_index_completeness(packet_path, repo):
    _write(repo, f"{ATTEMPT}/validation/extra.json", {"x": 1})
    _git(repo, "add", "-A")
    assert _check(packet_path, repo)["failed_requirements"] == ["evidence_index_complete"]


def test_altered_indexed_stage_record_is_not_used(packet_path, repo):
    stage = repo / ATTEMPT / "stages/007-menu-check.json"
    doc = json.loads(stage.read_text())
    doc["evidence"]["digest_matches"] = True
    doc["evidence"]["note"] = "edited after finish"
    stage.write_text(json.dumps(doc))
    view = load_gate_evidence(packet_path, root=repo)
    assert "menu_check" not in view
    assert any("007-menu-check.json: sha256 differs from the evidence index" in p
               for p in view["resolution"]["problems"])
    assert "menu_recovery_verified" in _check(packet_path, repo)["failed_requirements"]


def test_malformed_sibling_journal_never_raises(packet_path, repo):
    sibling = "benchmark_runs/plan3-part1/builder-a1/attempt-0"
    (repo / sibling / "stages").mkdir(parents=True)
    (repo / sibling / "stages/001-survey.json").write_text("{not json")
    (repo / sibling / "authoring-journal.json").write_text("{not json")
    result = _check(packet_path, repo)
    assert result["passed"] is False
    assert "finish_packet_resolved" in result["failed_requirements"]
    assert "authoring-journal.json: unreadable" in \
        result["requirements"]["finish_packet_resolved"]["detail"]


def test_packet_path_outside_root_is_a_named_failure(packet_path, repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "packet.json"
    outside.write_bytes(packet_path.read_bytes())
    result = _check(outside, repo)
    assert "finish_packet_resolved" in result["failed_requirements"]
    assert "outside the evidence root" in \
        result["requirements"]["finish_packet_resolved"]["detail"]


def test_malformed_validation_case_never_raises(packet_path, repo):
    packet = json.loads(packet_path.read_text())
    run = packet["validation_runs"][0]
    rel = f"{run['run_dir']}/validation.json"
    doc = json.loads((repo / rel).read_text())
    doc["cases"][1]["trial_index"] = None
    doc["cases"].append("not a case")
    run["validation_sha256"] = _write(repo, rel, doc)["sha256"]
    index_rel = packet["evidence_index"]["path"]
    index = json.loads((repo / index_rel).read_text())
    for entry in index["files"]:
        if entry["path"] == rel:
            entry["sha256"] = run["validation_sha256"]
    packet["evidence_index"] = _write(repo, index_rel, index)
    packet_path.write_text(json.dumps(packet))
    _git(repo, "add", "-A")
    result = _check(packet_path, repo)
    assert "finish_packet_resolved" in result["failed_requirements"]
    assert "trial_index None" in result["requirements"]["finish_packet_resolved"]["detail"]


def test_loader_exception_becomes_a_named_failure(packet_path, repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(gate, "load_gate_evidence", boom)
    result = _check(packet_path, repo)
    assert "finish_packet_resolved" in result["failed_requirements"]
    assert "RuntimeError: unexpected" in result["requirements"]["finish_packet_resolved"]["detail"]


# ---------------------------------------------------------------------------
# Table-driven removals on the derived view
# ---------------------------------------------------------------------------

def _case(view, case_id):
    return next(c for c in view["cases"] if c["case_id"] == case_id)


def _set_case(case_id, key, value):
    def mutate(v, repo):
        _case(v, case_id)[key] = value
    return mutate


def _set(path, value):
    def mutate(v, repo):
        node = v
        for key in path[:-1]:
            node = node[key]
        if value is _DELETE:
            del node[path[-1]]
        else:
            node[path[-1]] = value
    return mutate


_DELETE = object()


def _uncharge_everywhere(harm):
    def mutate(v, repo):
        for c in v["cases"]:
            for h in c["harms"]:
                if h["id"] == harm:
                    h["status"] = "not_fired"
    return mutate


def _charge(case_id, harm):
    def mutate(v, repo):
        for h in _case(v, case_id)["harms"]:
            if h["id"] == harm:
                h["status"] = "charged"
    return mutate


def _alt_same_script(v, repo):
    _case(v, "alternative-full")["script_sha256"] = _case(v, "joint-full")["script_sha256"]


def _drop_tag(case_id, tag):
    def mutate(v, repo):
        _case(v, case_id)["tags"].remove(tag)
    return mutate


def _credit(case_id, objective, points):
    def mutate(v, repo):
        _case(v, case_id)["objective_credits"][objective] = points
    return mutate


def _extra_copy(**changes):
    def mutate(v, repo):
        v["cases"].append({**copy.deepcopy(_case(v, "repeat-undo")), **changes})
    return mutate


def _undeclared_rejection(v, repo):
    _case(v, "joint-full")["validation_failures"] = [
        {"reason": "rejected_operation", "step": 2, "tool_name": "move_unit"}]


def _untrack_trial(v, repo):
    _git(repo, "rm", "-q", "--cached", _case(v, "partial-repair")["evidence"][0])


def _unindex_attempt(v, repo):
    v["attempts"][0]["evidence_index"] = None


REMOVALS = [
    ("finish_packet_resolved", _set(["resolution", "problems"], ["x.json: sha256 differs"])),
    ("evidence_index_complete", _set(["resolution", "unindexed"],
                                     ["unindexed_stage_record: a/stages/020-verify.json"])),
    ("twelve_cycle_verify", _set(["verify", "digests"], [STATE] * 11)),
    ("twelve_cycle_verify", _set(["verify", "digests"], [STATE] * 11 + ["x"])),
    ("menu_recovery_verified", _set(["menu_check", "digest_matches"], False)),
    ("menu_recovery_verified", _set(["menu_check", "loader_confirmed"], False)),
    ("joint_full_case", _set_case("joint-full", "primary_score", 11 / 12)),
    ("alternative_full_case", _set_case("alternative-full", "primary_score", 0.75)),
    ("alternative_full_case", _alt_same_script),
    ("required_live_tags", _drop_tag("repeat-undo", "repeat_undo")),
    ("intermediate_rungs_covered", _credit("partial-food", "improve-food", 4)),
    ("closer_only_zero", _set_case("closer-only", "gross_credit", 2)),
    ("harm_positive_cases", _uncharge_everywhere("escort-loss")),
    ("harm_negative_cases", _charge("joint-full", "escort-loss")),
    ("harm_only_negative", _set_case("escort-loss", "primary_score", 0.0)),
    ("null_digest_chain", _set(["null", "steps", 0, "after"], "x")),
    ("null_digest_chain", _set(["null", "steps"], [{"before": STATE, "after": STATE},
                                                   {"before": "y", "after": "y"},
                                                   {"before": STATE, "after": STATE}])),
    ("null_digest_chain", _set(["null", "final_digest"], "x")),
    ("null_observation_calls", _set(["null", "observation_calls"], 0)),
    ("null_observation_calls", _set(["null", "discoverability", 0, "discoverable"], False)),
    ("null_capture_timing", _set(["null", "capture", "max_s"], 2.5)),
    ("null_capture_timing", _set(["null", "capture_scope"], "partial")),
    ("capture_records_complete", _set_case("partial-repair", "capture",
                                           {"count": 8, "max_s": 0.9,
                                            "all_single_execution": False,
                                            "lua_executions_total": 9, "unavailable": {}})),
    ("final_restore_verified", _set(["restore", "digest"], "x")),
    ("report_regeneration_recorded", _set(["report_regeneration", "reports_identical"], False)),
    ("report_regeneration_recorded", _set(["report_regeneration", "validation_sha256_matches"],
                                          False)),
    ("failed_attempt_history", _unindex_attempt),
    ("offline_audit", _set(["offline_audit", "membership_matches"], False)),
    ("offline_audit", _set(["offline_audit", "uncredited_count"], 116)),
    ("positive_control_timing_probe", _set(["positive_control_probe"], _DELETE)),
    ("tracked_evidence_inventory", _untrack_trial),
    ("tracked_evidence_inventory", _set(["evidence_index", "sha256"], "0" * 64)),
    ("no_model_provenance", _set_case("partial-repair", "actor_kind", "model")),
    ("no_model_provenance", _set_case("partial-repair", "counting", True)),
    ("no_model_provenance", _set(["pilot_informed"], True)),
    ("measured_parameters_frozen", _set(["measured_parameters"],
                                        [{"name": "minimum_damage", "value": None,
                                          "evidence": []}])),
    ("rejections_declared", _undeclared_rejection),
    ("scenario_duration", _set(["scenarios", 0, "duration_s"], 10800.5)),
    ("scenario_duration", _set(["scenarios", 0, "duration_s"], None)),
    ("no_undefined_predicate_support", _set_case("partial-repair", "error",
                                                 {"kind": "missing_evidence"})),
    ("live_vs_offline_cases_distinguished", _extra_copy(live=False)),
    ("validation_cases_passed", _extra_copy(passed=False)),
]


@pytest.mark.parametrize("name,mutate", REMOVALS, ids=[f"{n}-{i}" for i, (n, _) in
                                                       enumerate(REMOVALS)])
def test_removing_one_piece_fails_for_exactly_that_reason(view, repo, name, mutate):
    broken = copy.deepcopy(view)
    mutate(broken, repo)
    result = _check(broken, repo)
    assert result["passed"] is False
    assert result["failed_requirements"] == [name]
    assert result["requirements"][name]["detail"]


def test_complete_view_passes(view, repo):
    assert _check(copy.deepcopy(view), repo)["passed"] is True


def test_missing_offline_fixture_fails_inventory(view, repo, monkeypatch):
    monkeypatch.setitem(gate.OFFLINE_FIXTURES, "capture_timeout",
                        "tests/arena/test_benchmark_capture.py::test_does_not_exist")
    assert _check(view, repo)["failed_requirements"] == ["offline_fixture_inventory"]


def test_offline_fixture_node_ids_exist_in_this_checkout():
    for node in gate.OFFLINE_FIXTURES.values():
        rel, name = node.split("::")
        assert f"def {name}(" in (REPO / rel).read_text(), node


def test_harm_without_preregistered_counterpart_fails(view, repo):
    broken = copy.deepcopy(view)
    broken["declared_harms"] = [*broken["declared_harms"], "unregistered-harm"]
    assert "harm_negative_cases" in _check(broken, repo)["failed_requirements"]


def test_altered_tracked_file_fails_inventory(view, repo):
    (repo / _case(view, "partial-repair")["evidence"][0]).write_text("{}")
    assert _check(view, repo)["failed_requirements"] == ["tracked_evidence_inventory"]


def test_probe_with_stale_capture_digest_fails(view, repo):
    doc = json.loads((repo / PROBE_PATH).read_text())
    doc["capture_implementation_sha256"] = "0" * 64
    broken = copy.deepcopy(view)
    broken["positive_control_probe"] = _write(repo, PROBE_PATH, doc)
    _git(repo, "add", "-A")
    assert _check(broken, repo)["failed_requirements"] == ["positive_control_timing_probe"]


def test_substitute_passes_only_with_both_scenario_durations(repo):
    _predecessor(repo, "builder")
    path = _build(repo, substitute=True)
    result = _check(path, repo)
    assert result["passed"] is True, result["failed_requirements"]
    assert "18000" in result["requirements"]["scenario_duration"]["detail"]
    view = load_gate_evidence(path, root=repo)
    for scenario in view["scenarios"]:
        if scenario["scenario_id"] == "builder-a1":
            scenario["duration_s"] = None
    assert _check(view, repo)["failed_requirements"] == ["scenario_duration"]


def test_substitute_without_declaration_fails(repo):
    _predecessor(repo, "builder")
    view = load_gate_evidence(_build(repo, substitute=True), root=repo)
    for scenario in view["scenarios"]:
        scenario["predecessor"] = None
    assert _check(view, repo)["failed_requirements"] == ["scenario_duration"]


def test_third_scenario_fails(repo):
    _predecessor(repo, "builder")
    view = load_gate_evidence(_build(repo, substitute=True), root=repo)
    view["scenarios"].append({**view["scenarios"][0], "scenario_id": "builder-a3"})
    assert _check(view, repo)["failed_requirements"] == ["scenario_duration"]


# ---------------------------------------------------------------------------
# Family gate
# ---------------------------------------------------------------------------

def _preflight(repo, **changes) -> dict:
    doc = {"passed": True, "failed_requirements": [], "code_identity": CODE,
           "recipes": [{"family": f, "toolset_identity": dict(TOOLSET)}
                       for f in ("builder", "city", "tactical")],
           "probe": {"present": True, "path": PROBE_PATH, "sha256": _sha(repo, PROBE_PATH),
                     "capture_implementation_sha256": CAPTURE}}
    doc.update(changes)
    return doc


@pytest.fixture
def family_paths(repo):
    return [_build(repo, family) for family in ("builder", "city", "tactical")]


def test_gate_passes_with_three_passing_families(family_paths, repo):
    result = check_part1_gate(family_paths, _preflight(repo), root=repo)
    assert result["passed"] is True, result["details"]
    assert set(result["families"]) == {"builder", "city", "tactical"}
    assert result["families"]["city"]["family_total_s"] == 7200.0


def _views(paths, repo):
    return [load_gate_evidence(p, root=repo) for p in paths]


@pytest.mark.parametrize("name,mutate", [
    ("family_coverage", lambda views, pre: views.pop()),
    ("identity_match", lambda views, pre: views[1].update(code_identity="x" * 64)),
    ("identity_match", lambda views, pre: views[2].update(contract_identity="x" * 64)),
    ("identity_match", lambda views, pre: views[0].update(toolset_identity={"x": 1})),
    ("identity_match", lambda views, pre: pre.update(code_identity="x" * 64)),
    ("code_identity_current", lambda views, pre: pre.update(code_identity="x" * 64)),
    ("capture_identity_current", lambda views, pre: pre["probe"].update(
        capture_implementation_sha256="0" * 64)),
    ("probe_binding", lambda views, pre: pre["probe"].update(sha256="0" * 64)),
    ("preflight_passed", lambda views, pre: pre.update(
        passed=False, failed_requirements=["positive_control_timing_probe"])),
    ("packets_passed", lambda views, pre: views[1]["verify"].update(cycles_completed=11)),
])
def test_gate_fails_for_named_reason(family_paths, repo, name, mutate):
    views, pre = _views(family_paths, repo), _preflight(repo)
    mutate(views, pre)
    result = check_part1_gate(views, pre, root=repo)
    assert result["passed"] is False
    assert name in result["failed_requirements"]
    assert result["details"][name]


def test_stale_preflight_and_packets_fail_together_against_current_code(family_paths, repo):
    views = _views(family_paths, repo)
    for v in views:
        v.update(code_identity="x" * 64, contract_identity="x" * 64)
    result = check_part1_gate(views, _preflight(repo, code_identity="x" * 64), root=repo)
    assert "identity_match" not in result["failed_requirements"]
    assert "code_identity_current" in result["failed_requirements"]


def test_gate_cli_resolves_finish_packet_paths(family_paths, repo, tmp_path, monkeypatch):
    pre = tmp_path / "pre.json"
    pre.write_text(json.dumps(_preflight(repo)))
    monkeypatch.setattr(authoring, "_REPO_ROOT", repo)
    out = tmp_path / "gate.json"
    paths = [str(p) for p in family_paths]
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
           "capture_implementation_sha256": CAPTURE,
           "evidence_index_sha256": "e" * 64, "limitations": ["positive control only"]}
    doc.update(changes)
    return doc


def _pytest_result(tmp_path: Path, **changes) -> Path:
    (tmp_path / "pytest.txt").write_text("1 passed\n")
    doc = {"command": "uv run pytest -q", "exit_code": 0, "passed": 1, "failed": 0,
           "errors": 0, "code_identity": CODE}
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
    for module in ("benchmark_part1_gate.py", "benchmark_part1_evidence.py"):
        assert f"src/civ_mcp/arena/{module}" in doc["fingerprint_dependencies"]
    assert doc["toolset"] == {"toolset_id": "plan3-part1-v1",
                              "path": "benchmarks/toolsets/plan3-part1-v1.yaml",
                              "identity": TOOLSET}
    for key in ("evidence_schema_version", "predicate_schema_version", "report_schema_version"):
        assert doc[key] == "2.0.0"


def test_candidate_contract_vocabulary_matches_the_code():
    from civ_mcp.arena.benchmark_predicates_v2 import _SPECS
    from civ_mcp.arena.benchmark_report_v2 import _SECTIONS
    doc = yaml.safe_load((REPO / "benchmarks/contracts/instrument-v2.yaml").read_text())
    assert doc["predicate_kinds"] == list(_SPECS)
    assert doc["report_sections"] == [key for key, _ in _SECTIONS]
    assert doc["scoring"]["maximum_credit"] == 12
    assert doc["scoring"]["harm_maxima"] == {"builder": 8, "city": 4, "tactical": 4}


def test_contract_record_lists_gate_tables():
    text = (REPO / "benchmarks/contracts/instrument-v2.md").read_text()
    for node in gate.OFFLINE_FIXTURES.values():
        assert node in text
    for tags in gate.REQUIRED_LIVE_TAGS.values():
        for tag in tags:
            assert f"`{tag}`" in text
