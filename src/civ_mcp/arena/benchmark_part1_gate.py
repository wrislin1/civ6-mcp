"""Plan 3 Part 1 acceptance gate (spec section 11), offline.

`check_part1_packet` evaluates one family against a fixed table of named
requirements. It takes either a finish-packet PATH, resolved from raw files
by `benchmark_part1_evidence.load_gate_evidence`, or an already-derived view
(unit tests). `check_part1_gate` combines the three families with the offline
preflight and the current checkout's identities. Neither returns a bare
Boolean: each requirement reports `{passed, evidence, detail}`, where
`evidence` lists the repository-relative paths it read. Missing or malformed
data fails the requirement that needs it; it never raises.

Every case in a validation run is a live witness. Offline robustness
fixtures (snapshot incompleteness, wrong identity, malformed predicates,
unsupported tools, capture timeout, external cancellation, scripted records
rejected from model aggregates) are the pytest node IDs in
`OFFLINE_FIXTURES`, checked statically in the code checkout.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from civ_mcp.arena.benchmark_capture_probe import REQUIRED_SAMPLES, capture_implementation_digest
from civ_mcp.arena.benchmark_contract_v2 import implementation_fingerprint, toolkit_fingerprint
from civ_mcp.arena.benchmark_part1_evidence import UNINDEXED_PREFIXES, load_gate_evidence

__all__ = ["FAMILIES", "HARM_COUNTERPART_TAGS", "OFFLINE_FIXTURES", "PACKET_REQUIREMENTS",
           "REQUIRED_LIVE_TAGS", "check_part1_gate", "check_part1_packet", "probe_problems",
           "scenario_durations"]

_REPO_ROOT = Path(__file__).resolve().parents[3]

FAMILIES = ("builder", "city", "tactical")
REQUIRED_CYCLES = 12
CAPTURE_LIMIT_S = 2.0
SCENARIO_LIMIT_S = 10800
MAX_SCENARIOS = 2  # one declared substitution per family
HISTORICAL_TRIALS = 96
HISTORICAL_UNCREDITED = 117
# Tags a closer-but-not-eligible (zero partial credit) case may carry.
CLOSER_TAGS = ("closer_only", "uncredited_preparation")

REQUIRED_LIVE_TAGS: dict[str, tuple[str, ...]] = {
    "builder": ("null_discovery", "joint_full", "alternative_full", "partial_repair",
                "partial_resource", "partial_food", "closer_only", "escort_loss",
                "escort_legitimate", "new_exposure", "covered_route",
                "temporary_exposure_repaired", "mixed_gain_loss", "harm_only", "repeat_undo",
                "final_charge"),
    "city": ("null_discovery", "joint_full", "alternative_full", "housing_partial",
             "uncredited_preparation", "destructive_placement", "accepted_replacement",
             "mixed_gain_loss", "harm_only", "queue_overwrite", "repeat_undo"),
    "tactical": ("null_discovery", "joint_full", "alternative_full", "meaningful_damage",
                 "reinforcement_partial", "closer_only", "covered_rescue",
                 "initial_exposure_null", "military_loss", "accepted_compensation",
                 "mixed_gain_loss", "harm_only", "repeat_undo"),
}
# The legitimate-action / accepted-compensation case tags that witness each
# declared harm NOT being charged.
HARM_COUNTERPART_TAGS: dict[str, tuple[str, ...]] = {
    "escort-loss": ("escort_legitimate",),
    "new-exposure": ("covered_route", "temporary_exposure_repaired"),
    "destructive-placement": ("accepted_replacement",),
    "military-loss": ("accepted_compensation",),
}
OFFLINE_FIXTURES: dict[str, str] = {
    "snapshot_incompleteness": "tests/arena/test_benchmark_state_v2.py::"
                               "test_dropped_row_is_incomplete_even_with_matching_shape",
    "wrong_identity": "tests/arena/test_benchmark_report_v2.py::"
                      "test_identity_drift_is_detected_from_states_not_validation_status",
    "malformed_predicate": "tests/arena/test_benchmark_predicates_v2.py::"
                           "test_unknown_kind_in_unvisited_any_branch_raises",
    "unsupported_tool": "tests/arena/test_benchmark_scripted_runner.py::"
                        "test_script_naming_a_tool_outside_the_toolset_is_refused_before_any_trial",
    "capture_timeout": "tests/arena/test_benchmark_capture.py::"
                       "test_local_capture_timeout_is_capture_failure_not_cancellation",
    "external_cancellation": "tests/arena/test_benchmark_agent.py::"
                             "test_external_cancel_during_capture_propagates_through_real_agent",
    "scripted_rejected_from_aggregates": "tests/arena/test_benchmark_report_v2.py::"
                                         "test_scripted_trials_cannot_enter_model_comparisons",
}

Result = tuple[bool, list[str], str]


class _Missing(Exception):
    """Required evidence is absent or malformed."""


def _need(node: Any, *path: Any) -> Any:
    for key in path:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            raise _Missing(f"missing {'.'.join(str(p) for p in path)}") from None
    return node


def _list(node: Any, *path: Any) -> list[Any]:
    value = _need(node, *path)
    if not isinstance(value, list):
        raise _Missing(f"{'.'.join(str(p) for p in path)} is not a list")
    return value


def _evidence(node: Any) -> list[str]:
    value = node.get("evidence") if isinstance(node, dict) else None
    if isinstance(value, str):
        return [value]
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _paths(cases: list[dict[str, Any]]) -> list[str]:
    return [e for c in cases for e in _evidence(c)]


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _live(p: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in _list(p, "cases") if isinstance(c, dict) and c.get("live") is True]


def _passed(p: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in _live(p) if c.get("passed") is True]


def _tagged(cases: list[dict[str, Any]], *tags: str) -> list[dict[str, Any]]:
    return [c for c in cases if set(tags) & set(c.get("tags") or [])]


def _charged(case: dict[str, Any]) -> set[str]:
    return {h.get("id") for h in case.get("harms") or [] if h.get("status") == "charged"}


def _capture_problems(summary: Any, where: str) -> list[str]:
    """A trial's capture summary: every capture complete, single-execution, <= 2.0 s."""
    if not isinstance(summary, dict) or not summary.get("count"):
        return [f"{where}: no capture summary"]
    problems = []
    if not _number(summary.get("max_s")) or summary["max_s"] > CAPTURE_LIMIT_S:
        problems.append(f"{where}: max capture {summary.get('max_s')!r} s exceeds "
                        f"{CAPTURE_LIMIT_S} s")
    if summary.get("all_single_execution") is not True or \
            summary.get("lua_executions_total") != summary.get("count"):
        problems.append(f"{where}: not exactly one Lua execution per capture")
    if summary.get("unavailable"):
        problems.append(f"{where}: unavailable capture fields {sorted(summary['unavailable'])}")
    return problems


def _verdict(problems: list[str], ok_detail: str) -> tuple[bool, str]:
    return (not problems, "; ".join(problems) if problems else ok_detail)


# ---------------------------------------------------------------------------
# Requirement checkers: (view, code_root) -> (ok, evidence paths, detail)
# ---------------------------------------------------------------------------

def _finish_packet_resolved(p: dict[str, Any], code_root: Path) -> Result:
    resolution = _need(p, "resolution")
    problems = [str(x) for x in _list(resolution, "problems")
                if not str(x).startswith(UNINDEXED_PREFIXES)]
    packet = resolution.get("finish_packet") or {}
    if not packet and not problems:
        problems = ["finish packet not resolved"]
    ok, detail = _verdict(problems, "every finish-packet reference resolved and hash-correct; "
                                    "attempt files read only from the verified index")
    return ok, [packet["path"]] if packet else [], detail


def _evidence_index_complete(p: dict[str, Any], code_root: Path) -> Result:
    resolution = _need(p, "resolution")
    unindexed = _list(resolution, "unindexed")
    index = p.get("evidence_index") or {}
    ok, detail = _verdict([str(x) for x in unindexed],
                          "every file in the attempt directory is in the immutable index")
    return ok and bool(index), [index["path"]] if index else [], detail


def _twelve_cycle_verify(p: dict[str, Any], code_root: Path) -> Result:
    verify = _need(p, "verify")
    digests = _list(verify, "digests")
    expected = _need(p, "expected_state_sha256")
    ok = (verify.get("cycles_completed") == REQUIRED_CYCLES and len(digests) == REQUIRED_CYCLES
          and all(d == expected for d in digests))
    return ok, _evidence(verify), (
        f"{len(digests)} digests, cycles_completed={verify.get('cycles_completed')}, "
        f"{sum(d == expected for d in digests)} match the expected state")


def _menu_recovery_verified(p: dict[str, Any], code_root: Path) -> Result:
    menu = _need(p, "menu_check")
    checks = {k: menu.get(k) is True for k in ("loader_confirmed", "digest_matches",
                                               "identity_matches")}
    checks["reconnect"] = bool(menu.get("reconnect"))
    ok, detail = _verdict([f"{k} not confirmed" for k, good in sorted(checks.items()) if not good],
                          "loader, reconnect, identity and digest confirmed")
    return ok, _evidence(menu), detail


def _full(p: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    return [c for c in _tagged(_passed(p), tag) if c.get("primary_score") == 1.0]


def _joint_full_case(p: dict[str, Any], code_root: Path) -> Result:
    cases = _full(p, "joint_full")
    return bool(cases), _paths(_tagged(_live(p), "joint_full")), (
        f"{len(cases)} passed live joint-full case(s) at primary 1.0")


def _alternative_full_case(p: dict[str, Any], code_root: Path) -> Result:
    joint = {c.get("script_sha256") for c in _full(p, "joint_full")}
    alts = [c for c in _full(p, "alternative_full")
            if c.get("script_sha256") and c.get("script_sha256") not in joint]
    return bool(alts), _paths(_tagged(_live(p), "alternative_full")), (
        f"{len(alts)} passed live alternative full-score case(s) whose script digest differs "
        f"from every joint-full script")


def _required_live_tags(p: dict[str, Any], code_root: Path) -> Result:
    family = _need(p, "family")
    if family not in REQUIRED_LIVE_TAGS:
        return False, [], f"unknown family {family!r}"
    passed = _passed(p)
    missing = [t for t in REQUIRED_LIVE_TAGS[family] if not _tagged(passed, t)]
    ok, detail = _verdict([f"no passed live case tagged {t}" for t in missing],
                          f"all {len(REQUIRED_LIVE_TAGS[family])} {family} tags witnessed")
    return ok, _paths(passed), detail


def _intermediate_rungs_covered(p: dict[str, Any], code_root: Path) -> Result:
    passed = _passed(p)
    missing = []
    for objective in _list(p, "objectives"):
        rungs = _list(objective, "rungs")
        for points in rungs:
            if points != max(rungs) and not any(
                    (c.get("objective_credits") or {}).get(objective["id"]) == points
                    for c in passed):
                missing.append(f"{objective['id']}@{points}")
    return not missing, _paths(passed), (
        f"uncovered rungs: {missing}" if missing else "every intermediate rung witnessed live")


def _closer_only_zero(p: dict[str, Any], code_root: Path) -> Result:
    cases = _tagged(_live(p), *CLOSER_TAGS)
    bad = [c.get("case_id") for c in cases
           if c.get("gross_credit") != 0 or c.get("passed") is not True]
    return bool(cases) and not bad, _paths(cases), (
        f"{len(cases)} closer/preparation case(s); nonzero or failed: {bad}")


def _harm_positive_cases(p: dict[str, Any], code_root: Path) -> Result:
    declared = _list(p, "declared_harms")
    passed = _passed(p)
    missing = [h for h in declared if not any(h in _charged(c) for c in passed)]
    return bool(declared) and not missing, _paths([c for c in passed if _charged(c)]), (
        f"declared {declared}; no passed case charging {missing}")


def _harm_negative_cases(p: dict[str, Any], code_root: Path) -> Result:
    declared = _list(p, "declared_harms")
    passed = _passed(p)
    problems, used = [], []
    for harm in declared:
        tags = HARM_COUNTERPART_TAGS.get(harm)
        if not tags:
            problems.append(f"{harm}: no preregistered counterpart tag")
            continue
        found = [c for c in _tagged(passed, *tags) if harm not in _charged(c)]
        used += found
        if not found:
            problems.append(f"{harm}: no passed {list(tags)} case with the harm not charged")
    ok, detail = _verdict(problems, f"every declared harm {declared} has an uncharged "
                                    f"legitimate/compensated counterpart")
    return ok and bool(declared), _paths(used), detail


def _harm_only_negative(p: dict[str, Any], code_root: Path) -> Result:
    cases = _tagged(_live(p), "harm_only")
    bad = [c.get("case_id") for c in cases
           if not (c.get("passed") is True and c.get("gross_credit") == 0
                   and _number(c.get("primary_score")) and c["primary_score"] < 0)]
    return bool(cases) and not bad, _paths(cases), (
        f"{len(cases)} harm-only case(s); not (passed, gross 0, primary < 0): {bad}")


def _null_digest_chain(p: dict[str, Any], code_root: Path) -> Result:
    null = _need(p, "null")
    steps = _list(null, "steps")
    initial, final = null.get("initial_digest"), null.get("final_digest")
    problems = [f"{k}={null.get(k)!r}" for k in ("gross_credit", "harm_total", "primary_score")
                if null.get(k) != 0]
    if not initial or initial != final:
        problems.append("initial and final digests differ")
    if not steps:
        problems.append("no recorded steps")
    for i, step in enumerate(steps):
        if step.get("before") != step.get("after"):
            problems.append(f"step {i} before != after")
        if i and steps[i - 1].get("after") != step.get("before"):
            problems.append(f"boundary {i - 1}->{i} differs")
    if steps and (steps[0].get("before") != initial or steps[-1].get("after") != final):
        problems.append("step chain does not join the initial/final digests")
    ok, detail = _verdict(problems, f"{len(steps)} steps, every pair and boundary equal")
    return ok, _evidence(null), detail


def _null_observation_calls(p: dict[str, Any], code_root: Path) -> Result:
    null = _need(p, "null")
    calls = null.get("observation_calls")
    facts = null.get("discoverability")
    problems = []
    if not isinstance(calls, int) or calls < 1:
        problems.append(f"observation_calls={calls!r}: a finish-only script does not qualify")
    if not isinstance(facts, list) or not facts:
        problems.append("no discoverability assertions")
    else:
        problems += [f"{f.get('id')} not discoverable" for f in facts
                     if f.get("discoverable") is not True]
    ok, detail = _verdict(problems, f"{calls} public observation calls; "
                                    f"{len(facts or [])} facts discoverable")
    return ok, _evidence(null), detail


def _null_capture_timing(p: dict[str, Any], code_root: Path) -> Result:
    null = _need(p, "null")
    problems = _capture_problems(null.get("capture"), "null")
    if null.get("capture_scope") != "full":
        problems.append(f"capture_scope={null.get('capture_scope')!r}, full scope required")
    ok, detail = _verdict(problems, f"{(null.get('capture') or {}).get('count')} full-scope "
                                    f"captures, max <= {CAPTURE_LIMIT_S} s, single execution")
    return ok, _evidence(null), detail


def _capture_records_complete(p: dict[str, Any], code_root: Path) -> Result:
    live = _live(p)
    problems = [x for c in live for x in _capture_problems(c.get("capture"), str(c.get("case_id")))]
    ok, detail = _verdict(problems, f"{len(live)} live trials, every capture single-execution "
                                    f"and <= {CAPTURE_LIMIT_S} s")
    return ok and bool(live), _paths(live), detail


def _final_restore_verified(p: dict[str, Any], code_root: Path) -> Result:
    restore = _need(p, "restore")
    expected = _need(p, "expected_state_sha256")
    ok = restore.get("reloaded") is True and bool(restore.get("reconnect")) \
        and restore.get("digest") == expected
    return ok, _evidence(restore), (
        f"reloaded={restore.get('reloaded')}, reconnect={bool(restore.get('reconnect'))}, "
        f"digest {'matches' if restore.get('digest') == expected else 'differs'}")


def _report_regeneration_recorded(p: dict[str, Any], code_root: Path) -> Result:
    regen = _need(p, "report_regeneration")
    ok = regen.get("reports_identical") is True and regen.get("validation_sha256_matches") is True
    return ok, _evidence(regen), (
        f"reports_identical={regen.get('reports_identical')}, validation.json matches the "
        f"finish packet and validate record: {regen.get('validation_sha256_matches')} "
        f"(Task 22 re-verifies in a temporary checkout)")


def _ref_ok(ref: Any) -> bool:
    return isinstance(ref, dict) and isinstance(ref.get("path"), str) \
        and isinstance(ref.get("sha256"), str)


def _failed_attempt_history(p: dict[str, Any], code_root: Path) -> Result:
    attempts = _list(p, "attempts")
    problems = []
    for a in attempts:
        if not _ref_ok(a.get("journal")):
            problems.append(f"{a.get('attempt_dir')}: no authoring journal")
        if not _ref_ok(a.get("evidence_index")):
            problems.append(f"{a.get('attempt_dir')}: not indexed (finish or abandon it)")
    if not any(a.get("status") == "passed" and p.get("scenario_id") in (a.get("scenario_ids") or [])
               for a in attempts):
        problems.append("no passed attempt for the packet scenario")
    paths = [a[k]["path"] for a in attempts for k in ("journal", "evidence_index")
             if _ref_ok(a.get(k))]
    failed = sum(a.get("status") != "passed" for a in attempts)
    ok, detail = _verdict(problems, f"{len(attempts)} attempt dirs ({failed} failed), all "
                                    f"journaled and indexed")
    return ok and bool(attempts), paths, detail


def _offline_audit(p: dict[str, Any], code_root: Path) -> Result:
    audit = _need(p, "offline_audit")
    ok = audit.get("membership_matches") is True and \
        audit.get("uncredited_count") == HISTORICAL_UNCREDITED and \
        audit.get("trial_count") == HISTORICAL_TRIALS
    return ok, _evidence(audit), (f"membership_matches={audit.get('membership_matches')}, "
                                  f"{audit.get('uncredited_count')}/{HISTORICAL_UNCREDITED} "
                                  f"uncredited over {audit.get('trial_count')}/"
                                  f"{HISTORICAL_TRIALS} trials")


def probe_problems(doc: Any, *, code_root: Path | None = None) -> list[str]:
    """Why a Task 17 probe provenance record cannot be relied on (empty if sound)."""
    if not isinstance(doc, dict):
        return ["probe record is not an object"]
    problems = []
    if (doc.get("verdict") or {}).get("passed") is not True:
        problems.append("probe verdict did not pass")
    if doc.get("samples") != REQUIRED_SAMPLES:
        problems.append(f"samples={doc.get('samples')!r}, {REQUIRED_SAMPLES} required")
    current = capture_implementation_digest(code_root or _REPO_ROOT)
    if doc.get("capture_implementation_sha256") != current:
        problems.append(f"capture implementation {doc.get('capture_implementation_sha256')!r} "
                        f"!= current {current}")
    return problems


def _positive_control_timing_probe(p: dict[str, Any], code_root: Path,
                                   root: Path) -> Result:
    ref = _need(p, "positive_control_probe")
    if not _ref_ok(ref):
        raise _Missing("positive_control_probe is not a {path, sha256} reference")
    path = root / ref["path"]
    if not path.is_file():
        return False, [ref["path"]], "probe provenance file missing"
    problems = []
    if hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
        problems.append("probe provenance sha256 differs from the reference")
    problems += probe_problems(json.loads(path.read_text(encoding="utf-8")), code_root=code_root)
    ok, detail = _verdict(problems, "passing 20-sample probe under the current capture "
                                    "implementation")
    return ok, [ref["path"]], detail


def _safe(rel: Any) -> bool:
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return False
    return ".." not in PurePosixPath(rel).parts


def _references(node: Any) -> list[tuple[str, str | None]]:
    """Every (path, sha256-or-None) the view references."""
    found: list[tuple[str, str | None]] = []
    if isinstance(node, dict):
        if isinstance(node.get("path"), str) and isinstance(node.get("sha256"), str):
            found.append((node["path"], node["sha256"]))
        found += [(e, None) for e in _evidence(node)]
        for key, value in node.items():
            if key != "evidence":
                found += _references(value)
    elif isinstance(node, list):
        for item in node:
            found += _references(item)
    return found


def _git_tracked(root: Path, paths: list[str]) -> set[str]:
    if not paths:
        return set()
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", *paths],
                         capture_output=True, check=False)
    if out.returncode != 0:
        return set()
    return {p for p in out.stdout.decode("utf-8").split("\0") if p}


def _tracked_evidence_inventory(p: dict[str, Any], code_root: Path, root: Path) -> Result:
    index_ref = _need(p, "evidence_index")
    if not _ref_ok(index_ref):
        raise _Missing("evidence_index is not a {path, sha256} reference")
    problems: list[str] = []
    expected: dict[str, str | None] = {}
    for rel, sha in _references(p):
        if expected.get(rel) is None:
            expected[rel] = sha
    index_path = root / index_ref["path"]
    if index_path.is_file():
        try:
            for entry in json.loads(index_path.read_text(encoding="utf-8")).get("files", []):
                rel, sha = entry.get("path"), entry.get("sha256")
                if expected.get(rel) not in (None, sha):
                    problems.append(f"{rel}: index and view digests disagree")
                expected[rel] = sha
        except (ValueError, AttributeError):
            problems.append(f"{index_ref['path']}: unreadable index")
    for rel, sha in sorted(expected.items()):
        if not _safe(rel):
            problems.append(f"{rel!r}: unsafe path")
        elif not (root / rel).is_file():
            problems.append(f"{rel}: missing")
        elif sha is not None and hashlib.sha256((root / rel).read_bytes()).hexdigest() != sha:
            problems.append(f"{rel}: sha256 differs")
    safe = [rel for rel in expected if _safe(rel)]
    problems += [f"{rel}: not Git-tracked" for rel in sorted(set(safe) - _git_tracked(root, safe))]
    ok, detail = _verdict(problems, f"{len(expected)} referenced/indexed files present, "
                                    f"hash-correct and Git-tracked")
    return ok, [index_ref["path"]], detail


def _no_model_provenance(p: dict[str, Any], code_root: Path) -> Result:
    records = [("null", _need(p, "null")), *((str(c.get("case_id")), c) for c in _list(p, "cases"))]
    problems = [] if p.get("pilot_informed") is False else ["position pilot_informed is not False"]
    for name, rec in records:
        if rec.get("actor_kind") != "scripted":
            problems.append(f"{name}: actor_kind={rec.get('actor_kind')!r}")
        if rec.get("counting") is not False:
            problems.append(f"{name}: counting={rec.get('counting')!r}")
        if rec.get("pilot_informed") is not False:
            problems.append(f"{name}: pilot_informed={rec.get('pilot_informed')!r}")
    ok, detail = _verdict(problems, f"{len(records)} records scripted, non-counting, "
                                    f"not pilot-informed")
    return ok, [e for _, rec in records for e in _evidence(rec)], detail


def _measured_parameters_frozen(p: dict[str, Any], code_root: Path) -> Result:
    params = _list(p, "measured_parameters")
    bad = [str(m.get("name")) for m in params if not _number(m.get("value")) or not _evidence(m)]
    return not bad, [e for m in params for e in _evidence(m)], (
        f"without frozen value or probe evidence: {bad}" if bad else
        f"{len(params)} measured parameter(s) frozen with probe evidence")


def _rejections_declared(p: dict[str, Any], code_root: Path) -> Result:
    problems = []
    for c in _live(p):
        declared = {(d.get("step"), d.get("tool_name")) for d in c.get("declared_rejections") or []}
        for f in c.get("validation_failures") or []:
            if f.get("reason") == "rejected_operation" and \
                    (f.get("step"), f.get("tool_name")) not in declared:
                problems.append(f"{c.get('case_id')}: undeclared rejection at step "
                                f"{f.get('step')} ({f.get('tool_name')})")
    ok, detail = _verdict(problems, "every rejected_operation matches a declared rejection")
    return ok, _paths(_live(p)), detail


def scenario_durations(view: dict[str, Any]) -> dict[str, float | None]:
    """Journal-recorded elapsed seconds per scenario; None when unrecorded."""
    return {str(s.get("scenario_id")): (s.get("duration_s") if _number(s.get("duration_s"))
                                        else None)
            for s in _list(view, "scenarios")}


def _scenario_duration(p: dict[str, Any], code_root: Path) -> Result:
    scenarios = _list(p, "scenarios")
    durations = scenario_durations(p)
    problems = [f"{sid}: duration not recorded" for sid, d in durations.items() if d is None]
    if not durations:
        problems.append("no journaled scenario")
    if len(durations) > MAX_SCENARIOS:
        problems.append(f"{len(durations)} scenarios exceed {MAX_SCENARIOS - 1} substitution")
    final = durations.get(str(p.get("scenario_id")))
    if final is not None and final > SCENARIO_LIMIT_S:
        problems.append(f"{p.get('scenario_id')}: {final} s exceeds {SCENARIO_LIMIT_S} s")
    if len(durations) > 1:
        own = next((s for s in scenarios if s.get("scenario_id") == p.get("scenario_id")), {})
        if not all(own.get(k) for k in ("predecessor", "reason", "material_change")) \
                or own.get("predecessor") not in durations:
            problems.append("substitute lacks a declared predecessor, reason and material change")
    total = sum(d for d in durations.values() if d is not None)
    detail = f"scenarios {durations}; family total {total:g} s"
    if problems:
        detail = "; ".join(problems) + f" ({detail})"
    return not problems, sorted({s["journal"] for s in scenarios if s.get("journal")}), detail


def _no_undefined_predicate_support(p: dict[str, Any], code_root: Path) -> Result:
    live = _live(p)
    errored = [f"{c.get('case_id')}: {c.get('error')}" for c in live if c.get("error") is not None]
    ok, detail = _verdict(errored, "satisfied by construction: the v2 predicate layer raises "
                                   "BenchmarkStateError on any undefined yield/lifecycle/queue "
                                   "value, which validation records as a case error; no live "
                                   "case errored")
    return ok and bool(live), _paths(live), detail


def _live_vs_offline_cases_distinguished(p: dict[str, Any], code_root: Path) -> Result:
    cases = _list(p, "cases")
    problems = [f"{c.get('case_id')}: not marked live" for c in cases if c.get("live") is not True]
    ok, detail = _verdict(problems, f"{len(cases)} validation-run cases are live witnesses; "
                                    f"offline fixtures are pytest node IDs")
    return ok, [], detail


def _defined_tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _offline_fixture_inventory(p: dict[str, Any], code_root: Path) -> Result:
    problems, files = [], set()
    for kind, node in OFFLINE_FIXTURES.items():
        rel, name = node.split("::")
        files.add(rel)
        path = code_root / rel
        if not path.is_file() or name not in _defined_tests(path):
            problems.append(f"{kind}: {node} not found")
    ok, detail = _verdict(problems, f"{len(OFFLINE_FIXTURES)} offline robustness tests present")
    return ok, sorted(files), detail


def _validation_cases_passed(p: dict[str, Any], code_root: Path) -> Result:
    live = _live(p)
    bad = [c.get("case_id") for c in live if c.get("passed") is not True]
    return bool(live) and not bad, _paths(live), (
        f"failed live cases: {bad}" if bad else f"{len(live)} live cases passed")


_Checker = Callable[..., Result]
_NEEDS_ROOT = {"positive_control_timing_probe", "tracked_evidence_inventory"}

PACKET_REQUIREMENTS: tuple[tuple[str, _Checker], ...] = (
    ("finish_packet_resolved", _finish_packet_resolved),
    ("evidence_index_complete", _evidence_index_complete),
    ("twelve_cycle_verify", _twelve_cycle_verify),
    ("menu_recovery_verified", _menu_recovery_verified),
    ("joint_full_case", _joint_full_case),
    ("alternative_full_case", _alternative_full_case),
    ("required_live_tags", _required_live_tags),
    ("intermediate_rungs_covered", _intermediate_rungs_covered),
    ("closer_only_zero", _closer_only_zero),
    ("harm_positive_cases", _harm_positive_cases),
    ("harm_negative_cases", _harm_negative_cases),
    ("harm_only_negative", _harm_only_negative),
    ("null_digest_chain", _null_digest_chain),
    ("null_observation_calls", _null_observation_calls),
    ("null_capture_timing", _null_capture_timing),
    ("capture_records_complete", _capture_records_complete),
    ("final_restore_verified", _final_restore_verified),
    ("report_regeneration_recorded", _report_regeneration_recorded),
    ("failed_attempt_history", _failed_attempt_history),
    ("offline_audit", _offline_audit),
    ("positive_control_timing_probe", _positive_control_timing_probe),
    ("tracked_evidence_inventory", _tracked_evidence_inventory),
    ("no_model_provenance", _no_model_provenance),
    ("measured_parameters_frozen", _measured_parameters_frozen),
    ("rejections_declared", _rejections_declared),
    ("scenario_duration", _scenario_duration),
    ("no_undefined_predicate_support", _no_undefined_predicate_support),
    ("live_vs_offline_cases_distinguished", _live_vs_offline_cases_distinguished),
    ("offline_fixture_inventory", _offline_fixture_inventory),
    ("validation_cases_passed", _validation_cases_passed),
)


def check_part1_packet(packet: dict[str, Any] | str | Path, *, root: Path | None = None,
                       code_root: Path | None = None,
                       probe_path: str | None = None) -> dict[str, Any]:
    """Evaluate every Part 1 requirement.

    `packet` is a finish-packet path (resolved from raw files under `root`)
    or an already-derived view. `root` holds the evidence; `code_root` is the
    checkout whose capture implementation and tests are current. `probe_path`
    is the probe provenance the preflight bound (default: the standard path).
    `toolkit_identity` is reported for information only."""
    root = Path(os.path.abspath(root or _REPO_ROOT))
    code_root = Path(os.path.abspath(code_root or _REPO_ROOT))
    if isinstance(packet, (str, Path)):
        try:
            view = load_gate_evidence(Path(packet), root=root, probe_path=probe_path)
        except Exception as exc:  # noqa: BLE001 -- the gate never raises
            view = {"resolution": {"finish_packet": None, "unindexed": [], "problems": [
                f"loader raised {type(exc).__name__}: {exc}"]}}
    else:
        view = packet if isinstance(packet, dict) else {}
    requirements: dict[str, dict[str, Any]] = {}
    for name, checker in PACKET_REQUIREMENTS:
        try:
            args = (view, code_root, root) if name in _NEEDS_ROOT else (view, code_root)
            ok, evidence, detail = checker(*args)
        except (_Missing, ValueError, TypeError, AttributeError, KeyError, OSError,
                SyntaxError) as exc:
            ok, evidence, detail = False, [], f"missing or malformed evidence: {exc}"
        requirements[name] = {"passed": bool(ok), "evidence": sorted(set(evidence)),
                              "detail": detail}
    failed = [name for name, entry in requirements.items() if not entry["passed"]]
    return {"position_id": view.get("position_id"), "family": view.get("family"),
            "passed": not failed, "failed_requirements": failed,
            "requirements": requirements, "toolkit_identity": _toolkit_identity(code_root),
            "view": view}


def _toolkit_identity(code_root: Path) -> str | None:
    """Informational only: never compared, and never a reason to fail."""
    try:
        return toolkit_fingerprint(code_root)
    except (OSError, ValueError):
        return None


def check_part1_gate(packets: list[dict[str, Any] | str | Path], preflight: dict[str, Any], *,
                     root: Path | None = None, code_root: Path | None = None) -> dict[str, Any]:
    """All three families passed under one code/toolset/contract identity that
    is the current checkout's, with a passing preflight bound to the same probe."""
    code_root = Path(os.path.abspath(code_root or _REPO_ROOT))
    failed: list[str] = []
    details: dict[str, str] = {}

    def fail(name: str, why: str) -> None:
        if name not in failed:
            failed.append(name)
        details[name] = "; ".join(filter(None, (details.get(name), why)))

    # The view reads the probe the preflight bound, so the two cannot diverge.
    bound_probe = (preflight.get("probe") or {}).get("path")
    results = [check_part1_packet(p, root=root, code_root=code_root,
                                  probe_path=bound_probe if isinstance(bound_probe, str) else None)
               for p in packets]
    by_family: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_family.setdefault(str(result["family"]), []).append(result)
    for family in FAMILIES:
        if len(by_family.get(family, [])) != 1:
            fail("family_coverage", f"{family}: {len(by_family.get(family, []))} packets")
    for extra in sorted(set(by_family) - set(FAMILIES)):
        fail("family_coverage", f"unknown family {extra!r}")

    pre_failed = preflight.get("failed_requirements")
    if preflight.get("passed") is not True or pre_failed:
        fail("preflight_passed", f"preflight failed: {pre_failed}")
    code = preflight.get("code_identity")
    current_code = implementation_fingerprint(code_root)
    if code != current_code:
        fail("code_identity_current", f"preflight code identity {code!r} != current checkout "
                                      f"{current_code}")
    probe = preflight.get("probe") or {}
    current_capture = capture_implementation_digest(code_root)
    if probe.get("capture_implementation_sha256") != current_capture:
        fail("capture_identity_current", f"preflight probe capture digest "
                                         f"{probe.get('capture_implementation_sha256')!r} != "
                                         f"current {current_capture}")
    if probe.get("present") is not True:
        fail("probe_binding", "preflight has no positive-control probe")
    toolsets = {r.get("family"): r.get("toolset_identity") for r in preflight.get("recipes", [])}

    families: dict[str, Any] = {}
    for family, group in sorted(by_family.items()):
        for result in group:
            view = result.pop("view")
            try:
                durations = scenario_durations(view)
            except _Missing:
                durations = {}
            families[family] = {
                "position_id": result["position_id"], "passed": result["passed"],
                "failed_requirements": result["failed_requirements"],
                "requirements": result["requirements"], "scenario_durations": durations,
                "family_total_s": sum(d for d in durations.values() if d is not None)}
            if not result["passed"]:
                fail("packets_passed", f"{family}: {result['failed_requirements']}")
            for key in ("code_identity", "contract_identity"):
                if not code or view.get(key) != code:
                    fail("identity_match", f"{family}: {key} {view.get(key)!r} != preflight "
                                           f"code identity {code!r}")
            if not toolsets.get(family) or view.get("toolset_identity") != toolsets.get(family):
                fail("identity_match", f"{family}: toolset identity differs from preflight")
            ref = view.get("positive_control_probe") or {}
            if (ref.get("path"), ref.get("sha256")) != (probe.get("path"), probe.get("sha256")):
                fail("probe_binding", f"{family}: probe reference differs from preflight")
    return {"passed": not failed, "failed_requirements": failed, "details": details,
            "families": families,
            "toolkit_identity": _toolkit_identity(code_root),  # informational, never gated
            "preflight": {k: preflight.get(k) for k in ("passed", "failed_requirements",
                                                        "code_identity", "toolkit_identity",
                                                        "probe")}}
