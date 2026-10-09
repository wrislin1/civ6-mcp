"""Expected-versus-actual validation cases and the validation CLI.

The scorer derives every actual value first (Task 12 reports over committed
raw trials); `check_case` only compares. Expected cases are strictly
validated, never reach the scripted actor, and never override derived output.
The end-to-end fake world reuses the transport-level fake from the scripted
runner tests: registry dispatch, `GameState`, the agent, the v2 capture and
the store are all real.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from civ_mcp.arena import benchmark_scripted_runner as scripted_runner
from civ_mcp.arena import benchmark_validation as validation
from civ_mcp.arena.benchmark_manifest_v2 import validate_v2_document
from civ_mcp.arena.benchmark_state import BenchmarkStateError
from civ_mcp.arena.benchmark_store import SessionLockMismatchError
from civ_mcp.arena.benchmark_validation import (
    build_reports,
    check_case,
    main,
    reconcile_mechanics,
    run_validation,
)

from .benchmark_v2_fixtures import state_v2
from .test_benchmark_scripted_runner import (
    FINISH,
    FORTIFY,
    PLAYER,
    FakeConnection,
    FakeWorld,
    _dump,
    _sha,
    write_fixture,
)


def test_expectation_cannot_override_score_or_missing_ledger():
    report = {"score": {"primary_score": -1 / 3}, "ledger": {"net": [], "steps": []}}
    expected = {"score": {"primary_score": 1.0}, "endpoints": [], "ledger": [{
        "id": "gold", "scope": "net", "step": None, "path": ["gold"],
        "coverage": "measured", "delta": 0}]}
    result = check_case(report, expected, trial={})
    assert not result["passed"]
    assert set(result["mismatches"]) == {"score:primary_score", "gold"}
    assert report["score"]["primary_score"] == -1 / 3


# ---------------------------------------------------------------------------
# Strict assertion validation
# ---------------------------------------------------------------------------

REF = {"path": "x.yaml", "sha256": "0" * 64}
UNIT_IN_AREA = {"kind": "unit_in_area", "unit_types": ["UNIT_WARRIOR"], "tiles": [[10, 10]]}


def _case(**expected):
    base = {"score": {"primary_score": 0.5, "harm_total": 0},
            "endpoints": [{"id": "warrior-home", "predicate": UNIT_IN_AREA, "value": True}],
            "ledger": [{"id": "net-gold", "scope": "net", "step": None, "path": ["gold"],
                        "coverage": "measured", "delta": 5},
                       {"id": "step-gold", "scope": "step", "step": 0, "path": ["gold"],
                        "coverage": "unavailable", "delta": None}]}
    base.update(expected)
    return {"schema_version": "2.0.0", "case_id": "c", "position": dict(REF),
            "script": dict(REF), "tags": [], "expected": base}


def test_valid_assertions_and_declared_rejections_pass():
    validate_v2_document(_case(), kind="case")
    raw = _case()
    raw["declared_rejections"] = [{"step": 0, "tool_name": "fortify_unit"}]
    validate_v2_document(raw, kind="case")


def _ledger(**overrides):
    row = {"id": "row", "scope": "net", "step": None, "path": ["gold"],
           "coverage": "measured", "delta": 1}
    row.update(overrides)
    return row


@pytest.mark.parametrize("expected, match", [
    ({"score": {"bonus": 1}}, "bonus"),
    ({"score": {"primary_score": True}}, "primary_score"),
    ({"score": {"primary_score": float("nan")}}, "primary_score"),
    ({"score": {"primary_score": ">= 0.5"}}, "primary_score"),
    ({"score": {"harms": ["x"]}}, "harms"),
    ({"endpoints": [{"id": "e", "predicate": {"kind": "unit_lost", "unit": [0, 1]},
                     "value": True}]}, "event-only"),
    ({"endpoints": [{"id": "e", "predicate": {"kind": "all", "predicates": [
        UNIT_IN_AREA, {"kind": "asset_displaced", "tiles": [[1, 1]],
                       "asset_fields": {"improvement": "IMPROVEMENT_FARM"}}]},
        "value": True}]}, "event-only"),
    ({"endpoints": [{"id": "e", "predicate": UNIT_IN_AREA, "value": 1}]}, "value"),
    ({"endpoints": [{"id": "e", "predicate": {"kind": "expression",
                                              "source": "final.gold > 3"},
                     "value": True}]}, "expression"),
    ({"endpoints": [{"id": "e", "predicate": UNIT_IN_AREA, "value": True,
                     "comparator": "lambda a, b: True"}]}, "comparator"),
    ({"ledger": [_ledger(step=0)]}, "step"),
    ({"ledger": [_ledger(scope="step", step=None)]}, "step"),
    ({"ledger": [_ledger(scope="step", step=True)]}, "step"),
    ({"ledger": [_ledger(scope="total")]}, "scope"),
    ({"ledger": [_ledger(coverage="unavailable", delta=0)]}, "delta"),
    ({"ledger": [_ledger(coverage="measured", delta=None)]}, "delta"),
    ({"ledger": [_ledger(delta="> 0")]}, "delta"),
    ({"ledger": [_ledger(delta=False)]}, "delta"),
    ({"ledger": [_ledger(path=[])]}, "path"),
    ({"ledger": [_ledger(path=["tiles", ""])]}, "path"),
    ({"ledger": [_ledger(path="gold")]}, "path"),
    ({"ledger": [_ledger(id="a"), _ledger(id="b")]}, "duplicate path"),
    ({"ledger": [_ledger(id="warrior-home")]}, "duplicate id"),
    ({"ledger": [_ledger(id="mechanics")]}, "reserved"),
])
def test_invalid_assertions_are_rejected(expected, match):
    with pytest.raises(ValueError, match=match):
        validate_v2_document(_case(**expected), kind="case")


def test_same_path_in_different_scopes_is_allowed():
    validate_v2_document(_case(ledger=[
        _ledger(id="a"), _ledger(id="b", scope="step", step=0),
        _ledger(id="c", scope="step", step=1)]), kind="case")


@pytest.mark.parametrize("rejections, match", [
    ([{"step": 0}], "tool_name"),
    ([{"step": "0", "tool_name": "fortify_unit"}], "step"),
    ([{"step": 0, "tool_name": "fortify_unit"}, {"step": 0, "tool_name": "move_unit"}],
     "duplicate"),
    ({"step": 0}, "list"),
])
def test_invalid_declared_rejections_are_rejected(rejections, match):
    raw = _case()
    raw["declared_rejections"] = rejections
    with pytest.raises(ValueError, match=match):
        validate_v2_document(raw, kind="case")


# ---------------------------------------------------------------------------
# Endpoint and ledger comparisons over actual evidence
# ---------------------------------------------------------------------------

def builder(x, y):
    return dict(owner=0, id=1, unit_index=1, type="UNIT_BUILDER", role="civilian",
                x=x, y=y, hp=100, max_hp=100, moves=2, charges=2)


def warrior(x, y):
    return dict(owner=0, id=9, unit_index=9, type="UNIT_WARRIOR", role="combat",
                x=x, y=y, hp=100, max_hp=100, moves=2, charges=0)


COVERED = {"kind": "civilian_covered", "unit": [0, 1], "tiles": [[11, 10]]}
EMPTY_REPORT = {"score": {}, "ledger": {"net": [], "steps": []}}


def _endpoint_expected(value=True):
    return {"score": {}, "ledger": [],
            "endpoints": [{"id": "rescued", "predicate": COVERED, "value": value}]}


def test_civilian_covered_assertion_follows_the_fixture_endpoint():
    initial = state_v2(units=[builder(10, 10)])
    covered = {"initial_state": initial,
               "final_state": state_v2(units=[builder(11, 10), warrior(12, 10)])}
    exposed = {"initial_state": initial,
               "final_state": state_v2(units=[builder(11, 10), warrior(14, 10)])}

    assert check_case(EMPTY_REPORT, _endpoint_expected(), trial=covered) == {
        "passed": True, "mismatches": {}}
    result = check_case(EMPTY_REPORT, _endpoint_expected(), trial=exposed)
    assert result["passed"] is False
    assert result["mismatches"] == {"rescued": {
        "actual": False, "expected": True,
        "source": {"trial": ["initial_state", "final_state"]}}}
    assert check_case(EMPTY_REPORT, _endpoint_expected(False), trial=exposed)["passed"]


def test_missing_predicate_evidence_raises_instead_of_false():
    trial = {"initial_state": state_v2(units=[]),
             "final_state": state_v2(units=[builder(11, 10), warrior(12, 10)])}
    with pytest.raises(BenchmarkStateError):
        check_case(EMPTY_REPORT, _endpoint_expected(False), trial=trial)


def _gold_row(**overrides):
    row = {"path": ["gold"], "before": 100, "after": 105, "delta": 5, "units": "gold",
           "coverage": "measured", "refs": {}, "source_steps": [0]}
    row.update(overrides)
    return row


def _ledger_expected(**overrides):
    row = {"id": "gold", "scope": "net", "step": None, "path": ["gold"],
           "coverage": "measured", "delta": 5}
    row.update(overrides)
    return {"score": {}, "endpoints": [], "ledger": [row]}


def test_measured_delta_assertion_fails_when_actual_coverage_is_unavailable():
    measured = {"score": {}, "ledger": {"net": [_gold_row()], "steps": []}}
    assert check_case(measured, _ledger_expected(), trial={})["passed"]

    unavailable = {"score": {}, "ledger": {"net": [_gold_row(
        after=None, delta=None, coverage="unavailable")], "steps": []}}
    result = check_case(unavailable, _ledger_expected(), trial={})
    assert result["mismatches"] == {"gold": {
        "actual": {"coverage": "unavailable", "delta": None},
        "expected": {"coverage": "measured", "delta": 5},
        "source": {"report": ["ledger", "net"]}}}


def test_step_ledger_rows_and_duplicate_matches():
    report = {"score": {}, "ledger": {"net": [], "steps": [
        {"step": 0, "tool_name": "get_units", "entries": []},
        {"step": 1, "tool_name": "fortify_unit", "entries": [_gold_row()]}]}}
    result = check_case(report, _ledger_expected(scope="step", step=1), trial={})
    assert result == {"passed": True, "mismatches": {}}
    result = check_case(report, _ledger_expected(scope="step", step=0), trial={})
    assert result["mismatches"]["gold"]["source"] == {"report": ["ledger", "steps", 0]}
    assert result["mismatches"]["gold"]["actual"] is None

    duplicated = {"score": {}, "ledger": {"net": [_gold_row(), _gold_row()], "steps": []}}
    assert not check_case(duplicated, _ledger_expected(), trial={})["passed"]


def test_score_mismatch_names_the_report_source_and_is_exact():
    report = {"score": {"primary_score": 0.25 + 1e-12, "harm_total": 0},
              "ledger": {"net": [], "steps": []}}
    expected = {"score": {"primary_score": 0.25, "harm_total": 0}, "endpoints": [],
                "ledger": []}
    result = check_case(report, expected, trial={})
    assert result["mismatches"] == {"score:primary_score": {
        "actual": 0.25 + 1e-12, "expected": 0.25,
        "source": {"report": "score.primary_score"}}}


# ---------------------------------------------------------------------------
# Mechanics reconciliation
# ---------------------------------------------------------------------------

REJECTION = {"reason": "rejected_operation", "step": 0, "tool_name": "fortify_unit",
             "result": "Error: UNIT_NOT_FOUND"}


def _trial(status, failures):
    return {"validation_status": status, "validation_failures": failures}


def test_declared_rejection_reconciles_a_failed_trial():
    result = reconcile_mechanics(_trial("failed", [REJECTION]),
                                 [{"step": 0, "tool_name": "fortify_unit"}])
    assert result == {"passed": True, "accepted_rejections": [REJECTION],
                      "unexpected_failures": [], "unobserved_rejections": []}


def test_undeclared_or_mismatched_rejection_fails():
    assert not reconcile_mechanics(_trial("failed", [REJECTION]), [])["passed"]
    result = reconcile_mechanics(_trial("failed", [REJECTION]),
                                 [{"step": 0, "tool_name": "move_unit"}])
    assert result["passed"] is False
    assert result["unexpected_failures"] == [REJECTION]
    assert result["unobserved_rejections"] == [{"step": 0, "tool_name": "move_unit"}]


def test_declared_rejection_that_did_not_occur_fails():
    result = reconcile_mechanics(_trial("passed_mechanics", []),
                                 [{"step": 0, "tool_name": "fortify_unit"}])
    assert result["passed"] is False
    assert result["unobserved_rejections"] == [{"step": 0, "tool_name": "fortify_unit"}]


def test_non_rejection_failure_is_never_reconciled():
    drift = {"reason": "identity_drift", "phase": "final", "field": "turn",
             "expected": 100, "observed": 101}
    result = reconcile_mechanics(_trial("failed", [REJECTION, drift]),
                                 [{"step": 0, "tool_name": "fortify_unit"}])
    assert result["passed"] is False
    assert result["unexpected_failures"] == [drift]
    assert reconcile_mechanics(_trial("passed_mechanics", []), [])["passed"]


# ---------------------------------------------------------------------------
# End-to-end: real dispatch -> committed trials -> reports -> comparison
# ---------------------------------------------------------------------------

class GoldWorld(FakeWorld):
    """Fortifying also pays 5 gold, so the measured ledger has a real delta."""

    def reset(self) -> None:
        super().reset()
        self.gold = 100

    def wire(self) -> list[str]:
        lines = super().wire()
        if self.moves == 0:
            self.gold = 105
        lines[1] = lines[1].replace(f"|{PLAYER}|100|12", f"|{PLAYER}|{self.gold}|12")
        return lines


PASSING_EXPECTED = {
    "score": {"primary_score": 0.0, "gross_credit": 0, "harm_total": 0},
    "endpoints": [{"id": "warrior-home", "predicate": UNIT_IN_AREA, "value": True}],
    "ledger": [
        {"id": "net-gold", "scope": "net", "step": None, "path": ["gold"],
         "coverage": "measured", "delta": 5},
        {"id": "step-gold", "scope": "step", "step": 0, "path": ["gold"],
         "coverage": "measured", "delta": 5},
    ],
}


def write_case(docs: Path, expected: dict, *, batches=None,
               declared_rejections=None) -> Path:
    """Write the fixture, then replace the case's expectations (re-digesting)."""
    suite_path = write_fixture(docs, batches)
    case_path = docs / "case.yaml"
    case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    case["expected"] = copy.deepcopy(expected)
    if declared_rejections is not None:
        case["declared_rejections"] = declared_rejections
    _dump(case_path, case)
    suite = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
    suite["cases"] = [{"path": "case.yaml", "sha256": _sha(case_path)}]
    return _dump(suite_path, suite)


class World:
    def __init__(self) -> None:
        self.world = GoldWorld()
        self.conn = FakeConnection(self.world)

    async def _reload(self) -> bool:
        self.world.reset()
        return True

    async def _popups(self) -> str:
        return "POPUPS|none"

    def transport(self) -> scripted_runner.ScriptedTransport:
        return scripted_runner.ScriptedTransport(
            connection=self.conn, deploy=lambda: None, reload=self._reload,
            dismiss_popups=self._popups)


def _files(run_dir: Path) -> dict[str, bytes]:
    return {str(p.relative_to(run_dir)): p.read_bytes()
            for p in sorted((run_dir / "reports").rglob("*")) if p.is_file()}


async def test_run_validation_end_to_end_with_real_dispatch(tmp_path):
    suite = write_case(tmp_path / "docs", PASSING_EXPECTED)
    world = World()
    run_dir = tmp_path / "run"
    result = await run_validation(suite, run_dir, dependencies=world.transport())

    assert world.conn.writes  # a real registered fortify_unit reached the world
    assert result["passed"] is True
    (case,) = result["cases"]
    assert case["case_id"] == "fortify-case"
    assert case["trial_index"] == 1
    assert case["mismatches"] == {}
    assert case["mechanics"]["passed"] is True
    on_disk = json.loads((run_dir / "validation.json").read_text(encoding="utf-8"))
    assert on_disk == result
    assert result["suite_id"] == "scripted-test"
    assert result["lock_sha256"] == _sha(run_dir / "session.json")

    report_json = run_dir / "reports" / "fortify-case" / "report.json"
    report = json.loads(report_json.read_text(encoding="utf-8"))
    assert case["report_sha256"] == _sha(report_json)
    assert report["score"]["primary_score"] == 0.0
    assert (run_dir / "reports" / "fortify-case" / "report.md").read_text().startswith(
        "# Version-2 trial report")
    # The actor never saw the case: nothing expected leaks into raw evidence.
    trial_text = (run_dir / "trials" / "trial-001.json").read_text(encoding="utf-8")
    assert "warrior-home" not in trial_text and "net-gold" not in trial_text

    first = _files(run_dir)
    mapping = build_reports(run_dir)
    assert _files(run_dir) == first
    assert build_reports(run_dir) == mapping
    assert _files(run_dir) == first
    assert mapping == {"fortify-case": case["report_sha256"]}


async def test_failing_expectations_keep_the_actual_report(tmp_path):
    expected = copy.deepcopy(PASSING_EXPECTED)
    expected["score"]["primary_score"] = 1.0
    expected["ledger"][0]["delta"] = 0
    suite = write_case(tmp_path / "docs", expected)
    run_dir = tmp_path / "run"
    result = await run_validation(suite, run_dir, dependencies=World().transport())
    (case,) = result["cases"]
    assert result["passed"] is False and case["passed"] is False
    assert set(case["mismatches"]) == {"score:primary_score", "net-gold"}
    assert case["mismatches"]["net-gold"]["actual"] == {"coverage": "measured", "delta": 5}
    report = json.loads((run_dir / "reports" / "fortify-case" / "report.json").read_text())
    assert report["score"]["primary_score"] == 0.0


async def test_turn_drift_fails_the_case(tmp_path):
    suite = write_case(tmp_path / "docs", PASSING_EXPECTED)
    world = World()
    world.world.drift_on_write = True
    result = await run_validation(suite, tmp_path / "run", dependencies=world.transport())
    (case,) = result["cases"]
    assert result["passed"] is False
    assert case["mechanics"]["passed"] is False
    assert {f["reason"] for f in case["mechanics"]["unexpected_failures"]} == {
        "identity_drift"}
    assert case["mismatches"]["mechanics"]["source"] == {"trial": ["validation_failures"]}


REJECTED_BATCHES = [{"calls": [{"name": "fortify_unit", "arguments": {"unit_index": 99}}]},
                    {"calls": [FINISH]}]
REJECTED_EXPECTED = {"score": {"primary_score": 0.0}, "endpoints": [], "ledger": []}


async def test_declared_rejected_operation_passes_end_to_end(tmp_path):
    suite = write_case(tmp_path / "docs", REJECTED_EXPECTED, batches=REJECTED_BATCHES,
                       declared_rejections=[{"step": 0, "tool_name": "fortify_unit"}])
    result = await run_validation(suite, tmp_path / "run", dependencies=World().transport())
    (case,) = result["cases"]
    assert result["passed"] is True
    assert [f["reason"] for f in case["mechanics"]["accepted_rejections"]] == [
        "rejected_operation"]


async def test_undeclared_rejected_operation_fails_end_to_end(tmp_path):
    suite = write_case(tmp_path / "docs", REJECTED_EXPECTED, batches=REJECTED_BATCHES)
    result = await run_validation(suite, tmp_path / "run", dependencies=World().transport())
    assert result["passed"] is False
    assert "mechanics" in result["cases"][0]["mismatches"]


async def test_changed_script_is_refused_by_the_lock(tmp_path):
    docs, run_dir = tmp_path / "docs", tmp_path / "run"
    await run_validation(write_case(docs, PASSING_EXPECTED), run_dir,
                         dependencies=World().transport())
    changed = write_case(docs, PASSING_EXPECTED, batches=[{"calls": [FORTIFY, FINISH]}])
    with pytest.raises(SessionLockMismatchError):
        await run_validation(changed, run_dir, dependencies=World().transport())


async def test_report_only_path_refuses_changed_dependencies(tmp_path):
    docs, run_dir = tmp_path / "docs", tmp_path / "run"
    await run_validation(write_case(docs, PASSING_EXPECTED), run_dir,
                         dependencies=World().transport())
    build_reports(run_dir)

    case_path = docs / "case.yaml"
    original = case_path.read_bytes()
    case_path.write_bytes(original + b"\n# edited\n")
    with pytest.raises(ValueError, match="case"):
        build_reports(run_dir)
    case_path.write_bytes(original)

    script_path = docs / "script.yaml"
    script_path.write_bytes(script_path.read_bytes() + b"\n# edited\n")
    with pytest.raises(ValueError, match="script"):
        build_reports(run_dir)


async def test_report_only_path_refuses_changed_code_identity(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    await run_validation(write_case(tmp_path / "docs", PASSING_EXPECTED), run_dir,
                         dependencies=World().transport())
    monkeypatch.setattr(validation, "implementation_fingerprint", lambda root: "0" * 64)
    with pytest.raises(ValueError, match="code"):
        build_reports(run_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _production(monkeypatch, world: World) -> None:
    monkeypatch.setattr(scripted_runner, "_production_transport",
                        lambda position: world.transport())


def test_cli_run_and_report_exit_codes(tmp_path, monkeypatch, capsys):
    _production(monkeypatch, World())
    suite = write_case(tmp_path / "docs", PASSING_EXPECTED)
    run_dir = tmp_path / "run"
    assert main(["run", "--suite", str(suite), "--run-dir", str(run_dir)]) == 0
    assert capsys.readouterr().out.strip() == str(run_dir / "validation.json")

    output = tmp_path / "reports.json"
    assert main(["report", "--run-dir", str(run_dir), "--output", str(output)]) == 0
    validation_doc = json.loads((run_dir / "validation.json").read_text())
    assert json.loads(output.read_text()) == {
        "fortify-case": validation_doc["cases"][0]["report_sha256"]}

    (tmp_path / "docs" / "script.yaml").write_text("# edited\n", encoding="utf-8")
    assert main(["report", "--run-dir", str(run_dir), "--output", str(output)]) != 0
    assert "script" in capsys.readouterr().err


def test_cli_run_exits_nonzero_when_a_case_fails(tmp_path, monkeypatch, capsys):
    _production(monkeypatch, World())
    expected = copy.deepcopy(PASSING_EXPECTED)
    expected["score"]["primary_score"] = 1.0
    suite = write_case(tmp_path / "docs", expected)
    run_dir = tmp_path / "run"
    assert main(["run", "--suite", str(suite), "--run-dir", str(run_dir)]) == 1
    assert json.loads((run_dir / "validation.json").read_text())["passed"] is False
