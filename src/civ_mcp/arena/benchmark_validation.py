"""Expected-versus-actual comparison of scripted validation cases, and its CLI.

`run_validation` runs a validation suite through Task 11's scripted runner
(which never reads expectations), derives each committed trial's Task 12
report, writes it, and only then compares the strictly validated case
assertions against that derived report and the trial's recorded endpoints.
A comparison never alters a report: nonpassing cases keep their actual
reports and fail the command. A case whose recorded evidence cannot answer a
predicate carries a `missing_evidence` error record (never a False actual);
the other cases are still evaluated and `validation.json` is still written.
`run` exits 0 when every case passed, 1 when a case failed, and 2 when any
case errored or the run was refused. `build_reports` re-derives every report from the
retained lock and raw trials, refusing changed code, scripts or cases.

    uv run python -m civ_mcp.arena.benchmark_validation run --suite PATH --run-dir PATH
    uv run python -m civ_mcp.arena.benchmark_validation report --run-dir PATH --output PATH
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from civ_mcp.arena.benchmark_contract_v2 import canonical_bytes, implementation_fingerprint
from civ_mcp.arena.benchmark_manifest_v2 import load_v2_document, validate_case_expected
from civ_mcp.arena.benchmark_report_v2 import build_trial_report, render_report
from civ_mcp.arena.benchmark_scripted_runner import ScriptedTransport, run_scripted_suite
from civ_mcp.arena.benchmark_state import BenchmarkStateError
from civ_mcp.arena.benchmark_store import BenchmarkStore, BenchmarkStoreError

__all__ = ["build_reports", "check_case", "main", "reconcile_mechanics", "run_validation"]

_REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATION_FILE = "validation.json"
REPORTS_DIR = "reports"


# ---------------------------------------------------------------------------
# Comparison (called only after `validate_case_expected`)
# ---------------------------------------------------------------------------

def check_case(report: dict[str, Any], expected: dict[str, Any], *,
               trial: dict[str, Any]) -> dict[str, Any]:
    """Compare validated assertions with the derived report and recorded endpoints.

    Exact equality, no tolerance. Missing predicate evidence raises
    `BenchmarkStateError`; a missing or duplicate ledger row is a mismatch even
    when the expected delta is zero. Neither input is modified.
    """
    from civ_mcp.arena.benchmark_predicates_v2 import evaluate_predicate
    mismatches = {}
    for key, value in expected["score"].items():
        actual = report["score"].get(key)
        if actual != value:
            mismatches[f"score:{key}"] = {"actual": actual, "expected": value,
                                          "source": {"report": f"score.{key}"}}
    for assertion in expected["endpoints"]:
        actual = evaluate_predicate(assertion["predicate"],
                                    initial=trial["initial_state"], final=trial["final_state"])
        if actual != assertion["value"]:
            mismatches[assertion["id"]] = {
                "actual": actual, "expected": assertion["value"],
                "source": {"trial": ["initial_state", "final_state"]}}
    for assertion in expected["ledger"]:
        rows = report["ledger"]["net"]
        source: list[Any] = ["ledger", "net"]
        if assertion["scope"] == "step":
            steps = report["ledger"]["steps"]
            rows = [row for step in steps
                    if step["step"] == assertion["step"] for row in step["entries"]]
            found = [i for i, step in enumerate(steps) if step["step"] == assertion["step"]]
            source = ["ledger", "steps", found[0] if len(found) == 1 else None]
        matches = [row for row in rows if row["path"] == assertion["path"]]
        target = {key: assertion[key] for key in ("coverage", "delta")}
        actual = ({key: matches[0][key] for key in target} if len(matches) == 1 else None)
        if actual != target:
            mismatches[assertion["id"]] = {"actual": actual, "expected": target,
                                           "source": {"report": source}}
    return {"passed": not mismatches, "mismatches": mismatches}


def reconcile_mechanics(trial: dict[str, Any],
                        declared_rejections: list[dict[str, Any]]) -> dict[str, Any]:
    """Accept only declared `rejected_operation` failures; every declaration must occur."""
    declared = {(d["step"], d["tool_name"]) for d in declared_rejections}
    accepted, unexpected, observed = [], [], set()
    for failure in trial["validation_failures"]:
        key = (failure.get("step"), failure.get("tool_name"))
        if failure.get("reason") == "rejected_operation" and key in declared:
            accepted.append(failure)
            observed.add(key)
        else:
            unexpected.append(failure)
    unobserved = [d for d in declared_rejections
                  if (d["step"], d["tool_name"]) not in observed]
    return {"passed": not unexpected and not unobserved,
            "accepted_rejections": accepted, "unexpected_failures": unexpected,
            "unobserved_rejections": unobserved}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _write_report(run_dir: Path, case_id: str, report: dict[str, Any]) -> str:
    directory = Path(run_dir) / REPORTS_DIR / case_id
    _write(directory / "report.md", render_report(report).encode("utf-8"))
    return _write(directory / "report.json", canonical_bytes(report))


def _trial_path(run_dir: Path, index: int) -> Path:
    return Path(run_dir) / BenchmarkStore.TRIALS_DIR / f"trial-{index:03d}.json"


def _load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _verified(path: Path, sha256: str, what: str) -> Path:
    actual = _file_sha256(path)
    if actual != sha256:
        raise ValueError(f"{what} {path} changed: recorded sha256 {sha256}, "
                         f"file bytes {actual}")
    return path


def _load_case(path: Path, sha256: str) -> dict[str, Any]:
    case = load_v2_document(_verified(path, sha256, "case"), kind="case")
    validate_case_expected(case["expected"])
    return case


def _check_trial_case(trial: dict[str, Any], case_id: str, case_sha256: str) -> None:
    if trial.get("case_id") != case_id or trial.get("case_sha256") != case_sha256:
        raise ValueError(f"trial {trial.get('index')} records case "
                         f"{trial.get('case_id')!r}/{trial.get('case_sha256')}, not "
                         f"{case_id!r}/{case_sha256}")


# ---------------------------------------------------------------------------
# Validation run
# ---------------------------------------------------------------------------

def _evaluate(trial: dict[str, Any], case: dict[str, Any], report: dict[str, Any]
              ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Mechanics, mismatches, and a missing-evidence error (never a False actual)."""
    mechanics = reconcile_mechanics(trial, case.get("declared_rejections", []))
    mismatches: dict[str, Any] = {}
    error = None
    if not mechanics["passed"]:
        mismatches["mechanics"] = {
            "actual": {"validation_status": trial["validation_status"],
                       "unexpected_failures": mechanics["unexpected_failures"],
                       "unobserved_rejections": mechanics["unobserved_rejections"]},
            "expected": {"declared_rejections": case.get("declared_rejections", [])},
            "source": {"trial": ["validation_failures"]}}
    try:
        comparison = check_case(report, case["expected"], trial=trial)
    except BenchmarkStateError as exc:
        error = {"kind": "missing_evidence", "message": str(exc)}
    else:
        mismatches.update(comparison["mismatches"])
    return mechanics, mismatches, error


async def run_validation(suite_path: Path, run_dir: Path, *,
                         dependencies: ScriptedTransport | None = None) -> dict[str, Any]:
    """Run the suite, derive and write reports, then compare each case."""
    run_dir = Path(run_dir)
    suite = load_v2_document(Path(suite_path), kind="validation_suite")
    position = load_v2_document(
        _verified(Path(suite["position"]["path"]), suite["position"]["sha256"], "position"),
        kind="position")
    # The scripted actor receives the suite only; expectations are loaded after.
    trials = await run_scripted_suite(suite, run_dir, dependencies=dependencies)

    cases = {}
    for ref in suite["cases"]:
        case = _load_case(Path(ref["path"]), ref["sha256"])
        cases[case["case_id"]] = (case, ref["sha256"])
    results = []
    for trial in trials:
        case, case_sha = cases[trial["case_id"]]
        _check_trial_case(trial, case["case_id"], case_sha)
        report = build_trial_report(trial, position)
        report_sha = _write_report(run_dir, case["case_id"], report)
        mechanics, mismatches, error = _evaluate(trial, case, report)
        results.append({"case_id": case["case_id"], "trial_index": trial["index"],
                        "passed": error is None and not mismatches, "mechanics": mechanics,
                        "mismatches": mismatches, "error": error,
                        "report_sha256": report_sha})
    result = {"suite_id": suite["suite_id"],
              "lock_sha256": _file_sha256(run_dir / BenchmarkStore.SESSION_FILE),
              "passed": all(r["passed"] for r in results),
              "errored": any(r["error"] is not None for r in results), "cases": results}
    _write(run_dir / VALIDATION_FILE, canonical_bytes(result))
    return result


# ---------------------------------------------------------------------------
# Report-only path over retained evidence
# ---------------------------------------------------------------------------

def _locked_path(portable: str) -> Path:
    path = Path(portable)
    return path if path.is_absolute() else _REPO_ROOT / path


def build_reports(run_dir: Path) -> dict[str, str]:
    """Re-derive every report from the retained lock and committed trials."""
    run_dir = Path(run_dir)
    lock = _load_json(run_dir / BenchmarkStore.SESSION_FILE)
    code_identity = implementation_fingerprint(_REPO_ROOT)
    if lock["code_identity"] != code_identity:
        raise ValueError(f"code identity changed since the run: lock records "
                         f"{lock['code_identity']}, current code is {code_identity}")
    for ref in lock["scripts"]:
        _verified(_locked_path(ref["path"]), ref["sha256"], "script")
    cases = {}
    for ref in lock["cases"]:
        case = _load_case(_locked_path(ref["path"]), ref["sha256"])
        cases[case["case_id"]] = (case, ref["sha256"])
    position_sha = lock["state_identity"]["position_sha256"]
    mapping = {}
    for spec in lock["schedule"]:
        case, case_sha = cases[spec["case_id"]]
        if case["position"]["sha256"] != position_sha:
            raise ValueError(f"case {case['case_id']!r} references a different position "
                             "than the lock")
        position = load_v2_document(
            _verified(Path(case["position"]["path"]), position_sha, "position"),
            kind="position")
        trial_path = _trial_path(run_dir, spec["index"])
        if not trial_path.is_file():
            raise ValueError(f"trial {spec['index']} has no committed evidence at {trial_path}")
        trial = _load_json(trial_path)
        _check_trial_case(trial, case["case_id"], case_sha)
        mapping[case["case_id"]] = _write_report(run_dir, case["case_id"],
                                                 build_trial_report(trial, position))
    return mapping


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m civ_mcp.arena.benchmark_validation",
        description="Run scripted validation suites and rebuild their reports.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a validation suite and compare its cases")
    run.add_argument("--suite", type=Path, required=True)
    run.add_argument("--run-dir", type=Path, required=True)
    report = sub.add_parser("report", help="rebuild reports from retained raw evidence")
    report.add_argument("--run-dir", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            result = asyncio.run(run_validation(args.suite, args.run_dir))
            print(args.run_dir / VALIDATION_FILE)
            if result["errored"]:
                return 2
            return 0 if result["passed"] else 1
        mapping = build_reports(args.run_dir)
        _write(args.output, canonical_bytes(mapping))
        print(args.output)
        return 0
    except (ValueError, BenchmarkStoreError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
