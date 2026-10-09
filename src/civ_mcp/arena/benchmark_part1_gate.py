"""Plan 3 Part 1 acceptance gate (spec section 11), offline.

`check_part1_packet` evaluates one family's assembled gate packet against a
fixed table of named requirements; `check_part1_gate` combines the three
family packets with the offline preflight. Neither returns a bare Boolean:
each requirement reports `{passed, evidence, detail}`, where `evidence` lists
the repository-relative paths it read. Missing or malformed data fails the
requirement that needs it; it never raises.

Gate packet shape (assembled from an attempt's stage records, validation
reports, journals and indices; every `evidence` value is a repository path):

    position_id, family, scenario_id, code_identity, contract_identity,
    toolset_identity, expected_state_sha256, pilot_informed
    verify        {evidence, cycles_completed, digests[12]}
    menu_check    {evidence, loader_confirmed, reconnect, digest_matches,
                   identity_matches}
    restore       {evidence, reloaded, reconnect, digest}
    objectives    [{id, rungs[points...]}]          declared_harms [harm ids]
    cases         [{case_id, tags, live, evidence, passed, error, script_sha256,
                    actor_kind, counting, pilot_informed, primary_score,
                    gross_credit, harm_total, objective_credits{id: points},
                    harms[{id, fired, compensated}], negative_for[harm ids],
                    capture_records[{complete, duration_s, io{lua_executions}}],
                    validation_failures[], declared_rejections[],
                    undefined_support[]}]
    null          {evidence, gross_credit, harm_total, primary_score,
                   initial_digest, final_digest, steps[{before, after}],
                   observation_calls, discoverability[{id, discoverable}],
                   capture_scope, capture_records[], actor_kind, counting,
                   pilot_informed}
    attempts      [{scenario_id, attempt_dir, status, duration_s,
                    journal{path, sha256}, evidence_index{path, sha256},
                    substitution{predecessor, reason, material_change}|null}]
    offline_audit {evidence, membership_matches, trial_count, uncredited_count}
    positive_control_probe {path, sha256}   (Task 17 probe provenance)
    evidence_index {path, sha256}
    measured_parameters [{name, value, evidence[paths]}]

Cases with `live: false` are offline robustness fixtures (schema errors,
timeouts, actor separation); they are reported but never count as live
witnesses and need no live reload or capture records.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from civ_mcp.arena.benchmark_capture_probe import REQUIRED_SAMPLES, capture_implementation_digest

__all__ = ["FAMILIES", "PACKET_REQUIREMENTS", "check_part1_gate", "check_part1_packet"]

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

Result = tuple[bool, list[str], str]


class _Missing(Exception):
    """Required packet data is absent or malformed."""


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


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _live(packet: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in _list(packet, "cases") if isinstance(c, dict) and c.get("live") is True]


def _tagged(packet: dict[str, Any], *tags: str) -> list[dict[str, Any]]:
    return [c for c in _live(packet) if set(tags) & set(c.get("tags") or [])]


def _deducted(case: dict[str, Any]) -> set[str]:
    return {h.get("id") for h in case.get("harms") or []
            if h.get("fired") is True and h.get("compensated") is not True}


def _capture_problems(records: Any, where: str) -> list[str]:
    if not isinstance(records, list) or not records:
        return [f"{where}: no capture records"]
    problems = []
    for i, r in enumerate(records):
        io = r.get("io") if isinstance(r, dict) else None
        duration = r.get("duration_s") if isinstance(r, dict) else None
        if not isinstance(r, dict) or r.get("complete") is not True:
            problems.append(f"{where}[{i}] incomplete")
        elif not _number(duration) or duration > CAPTURE_LIMIT_S:
            problems.append(f"{where}[{i}] duration {duration!r} exceeds {CAPTURE_LIMIT_S} s")
        elif not isinstance(io, dict) or io.get("lua_executions") != 1:
            problems.append(f"{where}[{i}] not a single Lua execution")
    return problems


def _verdict(problems: list[str], ok_detail: str) -> tuple[bool, str]:
    return (not problems, "; ".join(problems) if problems else ok_detail)


# ---------------------------------------------------------------------------
# Requirement checkers: (packet, root) -> (ok, evidence paths, detail)
# ---------------------------------------------------------------------------

def _twelve_cycle_verify(p: dict[str, Any], root: Path) -> Result:
    verify = _need(p, "verify")
    digests = _list(verify, "digests")
    expected = _need(p, "expected_state_sha256")
    ok = (verify.get("cycles_completed") == REQUIRED_CYCLES and len(digests) == REQUIRED_CYCLES
          and all(d == expected for d in digests))
    return ok, _evidence(verify), (
        f"{len(digests)} digests, cycles_completed={verify.get('cycles_completed')}, "
        f"{sum(d == expected for d in digests)} match the expected state")


def _menu_recovery_verified(p: dict[str, Any], root: Path) -> Result:
    menu = _need(p, "menu_check")
    checks = {k: menu.get(k) is True for k in ("loader_confirmed", "digest_matches",
                                               "identity_matches")}
    checks["reconnect"] = bool(menu.get("reconnect"))
    failed = sorted(k for k, ok in checks.items() if not ok)
    ok, detail = _verdict([f"{k} not confirmed" for k in failed],
                          "loader, reconnect, identity and digest confirmed")
    return ok, _evidence(menu), detail


def _full(p: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    return [c for c in _tagged(p, tag) if c.get("primary_score") == 1.0 and c.get("passed") is True]


def _joint_full_case(p: dict[str, Any], root: Path) -> Result:
    cases = _full(p, "joint_full")
    return bool(cases), [e for c in _tagged(p, "joint_full") for e in _evidence(c)], (
        f"{len(cases)} live joint-full case(s) at primary 1.0")


def _alternative_full_case(p: dict[str, Any], root: Path) -> Result:
    joint = {c.get("script_sha256") for c in _full(p, "joint_full")}
    alts = [c for c in _full(p, "alternative_full")
            if c.get("script_sha256") and c.get("script_sha256") not in joint]
    return bool(alts), [e for c in _tagged(p, "alternative_full") for e in _evidence(c)], (
        f"{len(alts)} live alternative full-score case(s) with a script digest different "
        f"from every joint-full script")


def _intermediate_rungs_covered(p: dict[str, Any], root: Path) -> Result:
    live = [c for c in _live(p) if c.get("passed") is True]
    missing = []
    for objective in _list(p, "objectives"):
        rungs = _list(objective, "rungs")
        for points in rungs:
            if points == max(rungs):
                continue
            if not any((c.get("objective_credits") or {}).get(objective["id"]) == points
                       for c in live):
                missing.append(f"{objective['id']}@{points}")
    return not missing, [e for c in live for e in _evidence(c)], (
        f"uncovered rungs: {missing}" if missing else "every intermediate rung witnessed live")


def _closer_only_zero(p: dict[str, Any], root: Path) -> Result:
    cases = _tagged(p, *CLOSER_TAGS)
    bad = [c.get("case_id") for c in cases if c.get("gross_credit") != 0 or c.get("passed") is not True]
    ok = bool(cases) and not bad
    return ok, [e for c in cases for e in _evidence(c)], (
        f"{len(cases)} closer/preparation case(s); nonzero or failed: {bad}")


def _harm_positive_cases(p: dict[str, Any], root: Path) -> Result:
    declared = _list(p, "declared_harms")
    live = [c for c in _live(p) if c.get("passed") is True]
    missing = [h for h in declared if not any(h in _deducted(c) for c in live)]
    ok = bool(declared) and not missing
    return ok, [e for c in live if _deducted(c) for e in _evidence(c)], (
        f"declared {declared}; no firing case for {missing}")


def _harm_negative_cases(p: dict[str, Any], root: Path) -> Result:
    declared = _list(p, "declared_harms")
    live = [c for c in _live(p) if c.get("passed") is True]
    found = {h: [c for c in live if h in (c.get("negative_for") or []) and h not in _deducted(c)]
             for h in declared}
    missing = [h for h, cases in found.items() if not cases]
    ok = bool(declared) and not missing
    return ok, [e for cases in found.values() for c in cases for e in _evidence(c)], (
        f"declared {declared}; no legitimate/compensated case for {missing}")


def _null_digest_chain(p: dict[str, Any], root: Path) -> Result:
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


def _null_observation_calls(p: dict[str, Any], root: Path) -> Result:
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


def _null_capture_timing(p: dict[str, Any], root: Path) -> Result:
    null = _need(p, "null")
    problems = _capture_problems(null.get("capture_records"), "null")
    if null.get("capture_scope") != "full":
        problems.append(f"capture_scope={null.get('capture_scope')!r}, full scope required")
    ok, detail = _verdict(problems, f"{len(null.get('capture_records') or [])} complete full-scope "
                                    f"records <= {CAPTURE_LIMIT_S} s")
    return ok, _evidence(null), detail


def _capture_records_complete(p: dict[str, Any], root: Path) -> Result:
    live = _live(p)
    problems = [x for c in live for x in _capture_problems(c.get("capture_records"),
                                                          str(c.get("case_id")))]
    ok, detail = _verdict(problems, f"{len(live)} live cases, every capture complete, single "
                                    f"execution, <= {CAPTURE_LIMIT_S} s")
    return ok and bool(live), [e for c in live for e in _evidence(c)], detail


def _final_restore_verified(p: dict[str, Any], root: Path) -> Result:
    restore = _need(p, "restore")
    expected = _need(p, "expected_state_sha256")
    ok = restore.get("reloaded") is True and bool(restore.get("reconnect")) \
        and restore.get("digest") == expected
    return ok, _evidence(restore), (f"reloaded={restore.get('reloaded')}, reconnect="
                                    f"{bool(restore.get('reconnect'))}, digest "
                                    f"{'matches' if restore.get('digest') == expected else 'differs'}")


def _ref_ok(ref: Any) -> bool:
    return isinstance(ref, dict) and isinstance(ref.get("path"), str) \
        and isinstance(ref.get("sha256"), str)


def _failed_attempt_history(p: dict[str, Any], root: Path) -> Result:
    attempts = _list(p, "attempts")
    problems = []
    for i, a in enumerate(attempts):
        if not _ref_ok(a.get("journal")):
            problems.append(f"attempt {i} ({a.get('status')}) has no journal")
        if not _ref_ok(a.get("evidence_index")):
            problems.append(f"attempt {i} ({a.get('status')}) is not indexed")
    if not any(a.get("status") == "passed" and a.get("scenario_id") == p.get("scenario_id")
               for a in attempts):
        problems.append("no passed attempt for the packet scenario")
    paths = [a[k]["path"] for a in attempts for k in ("journal", "evidence_index")
             if _ref_ok(a.get(k))]
    failed = sum(a.get("status") != "passed" for a in attempts)
    ok, detail = _verdict(problems, f"{len(attempts)} attempts ({failed} failed), all "
                                    f"journaled and indexed")
    return ok and bool(attempts), paths, detail


def _offline_audit(p: dict[str, Any], root: Path) -> Result:
    audit = _need(p, "offline_audit")
    ok = audit.get("membership_matches") is True and \
        audit.get("uncredited_count") == HISTORICAL_UNCREDITED and \
        audit.get("trial_count") == HISTORICAL_TRIALS
    return ok, _evidence(audit), (f"membership_matches={audit.get('membership_matches')}, "
                                  f"{audit.get('uncredited_count')}/{HISTORICAL_UNCREDITED} "
                                  f"uncredited over {audit.get('trial_count')}/"
                                  f"{HISTORICAL_TRIALS} trials")


def _read_json(root: Path, rel: str) -> Any:
    return json.loads((root / rel).read_text(encoding="utf-8"))


def probe_problems(doc: Any) -> list[str]:
    """Why a Task 17 probe provenance record cannot be relied on (empty if sound)."""
    if not isinstance(doc, dict):
        return ["probe record is not an object"]
    problems = []
    if (doc.get("verdict") or {}).get("passed") is not True:
        problems.append("probe verdict did not pass")
    if doc.get("samples") != REQUIRED_SAMPLES:
        problems.append(f"samples={doc.get('samples')!r}, {REQUIRED_SAMPLES} required")
    current = capture_implementation_digest(_REPO_ROOT)
    if doc.get("capture_implementation_sha256") != current:
        problems.append(f"capture implementation {doc.get('capture_implementation_sha256')!r} "
                        f"!= current {current}")
    return problems


def _positive_control_timing_probe(p: dict[str, Any], root: Path) -> Result:
    ref = _need(p, "positive_control_probe")
    if not _ref_ok(ref):
        raise _Missing("positive_control_probe is not a {path, sha256} reference")
    path = root / ref["path"]
    if not path.is_file():
        return False, [ref["path"]], "probe provenance file missing"
    problems = []
    if hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
        problems.append("probe provenance sha256 differs from the packet reference")
    problems += probe_problems(_read_json(root, ref["path"]))
    ok, detail = _verdict(problems, "passing 20-sample probe under the current capture "
                                    "implementation")
    return ok, [ref["path"]], detail


def _safe(rel: Any) -> bool:
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return False
    return ".." not in PurePosixPath(rel).parts


def _references(node: Any) -> list[tuple[str, str | None]]:
    """Every (path, sha256-or-None) the packet references."""
    found: list[tuple[str, str | None]] = []
    if isinstance(node, dict):
        if isinstance(node.get("path"), str) and isinstance(node.get("sha256"), str):
            found.append((node["path"], node["sha256"]))
        found += [(e, None) for e in _evidence(node)]
        for key, value in node.items():
            if key not in ("evidence",):
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


def _tracked_evidence_inventory(p: dict[str, Any], root: Path) -> Result:
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
            index = json.loads(index_path.read_text(encoding="utf-8"))
            for entry in index.get("files", []):
                rel, sha = entry.get("path"), entry.get("sha256")
                if expected.get(rel) not in (None, sha):
                    problems.append(f"{rel}: index and packet digests disagree")
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
    tracked = _git_tracked(root, safe)
    problems += [f"{rel}: not Git-tracked" for rel in sorted(set(safe) - tracked)]
    ok, detail = _verdict(problems, f"{len(expected)} referenced/indexed files present, "
                                    f"hash-correct and Git-tracked")
    return ok, [index_ref["path"]], detail


def _no_model_provenance(p: dict[str, Any], root: Path) -> Result:
    records = [("null", _need(p, "null")), *((str(c.get("case_id")), c) for c in _list(p, "cases"))]
    problems = [] if p.get("pilot_informed") is False else ["packet pilot_informed is not False"]
    for name, rec in records:
        if rec.get("live") is False:
            continue
        if rec.get("actor_kind") != "scripted":
            problems.append(f"{name}: actor_kind={rec.get('actor_kind')!r}")
        if rec.get("counting") is not False:
            problems.append(f"{name}: counting={rec.get('counting')!r}")
        if rec.get("pilot_informed") is not False:
            problems.append(f"{name}: pilot_informed={rec.get('pilot_informed')!r}")
    ok, detail = _verdict(problems, f"{len(records)} records scripted, non-counting, "
                                    f"not pilot-informed")
    return ok, [e for _, rec in records for e in _evidence(rec)], detail


def _measured_parameters_frozen(p: dict[str, Any], root: Path) -> Result:
    params = _list(p, "measured_parameters")
    bad = [str(m.get("name")) for m in params
           if not _number(m.get("value")) or not _evidence(m)]
    return not bad, [e for m in params for e in _evidence(m)], (
        f"without frozen value or probe evidence: {bad}" if bad else
        f"{len(params)} measured parameter(s) frozen with probe evidence")


def _rejections_declared(p: dict[str, Any], root: Path) -> Result:
    problems = []
    for c in _live(p):
        declared = {(d.get("step"), d.get("tool_name")) for d in c.get("declared_rejections") or []}
        for f in c.get("validation_failures") or []:
            if f.get("reason") == "rejected_operation" and \
                    (f.get("step"), f.get("tool_name")) not in declared:
                problems.append(f"{c.get('case_id')}: undeclared rejection at step "
                                f"{f.get('step')} ({f.get('tool_name')})")
    ok, detail = _verdict(problems, "every rejected_operation matches a declared rejection")
    return ok, [e for c in _live(p) for e in _evidence(c)], detail


def scenario_durations(packet: dict[str, Any]) -> dict[str, float | None]:
    """Per-scenario elapsed seconds (max over its attempts); None when unrecorded."""
    out: dict[str, float | None] = {}
    for a in _list(packet, "attempts"):
        sid = str(a.get("scenario_id"))
        value = a.get("duration_s")
        if not _number(value) or sid in out and out[sid] is None:
            out[sid] = None
        else:
            out[sid] = max(value, out.get(sid) or 0.0)
    return out


def _scenario_duration(p: dict[str, Any], root: Path) -> Result:
    attempts = _list(p, "attempts")
    durations = scenario_durations(p)
    problems = [f"{sid}: duration not recorded" for sid, d in durations.items() if d is None]
    if len(durations) > MAX_SCENARIOS:
        problems.append(f"{len(durations)} scenarios exceed {MAX_SCENARIOS - 1} substitution")
    final = durations.get(str(p.get("scenario_id")))
    if final is not None and final > SCENARIO_LIMIT_S:
        problems.append(f"{p.get('scenario_id')}: {final} s exceeds {SCENARIO_LIMIT_S} s")
    if len(durations) > 1:
        sub = next((a.get("substitution") for a in attempts
                    if a.get("scenario_id") == p.get("scenario_id")
                    and isinstance(a.get("substitution"), dict)), None)
        if not sub or not all(sub.get(k) for k in ("predecessor", "reason", "material_change")) \
                or sub.get("predecessor") not in durations:
            problems.append("substitute lacks a declared predecessor, reason and material change")
    total = sum(d for d in durations.values() if d is not None)
    detail = f"scenarios {durations}; family total {total:g} s"
    if problems:
        detail = "; ".join(problems) + f" ({detail})"
    return not problems, [a["journal"]["path"] for a in attempts if _ref_ok(a.get("journal"))], \
        detail


def _no_undefined_predicate_support(p: dict[str, Any], root: Path) -> Result:
    problems = []
    for c in _live(p):
        support = c.get("undefined_support")
        if not isinstance(support, list):
            problems.append(f"{c.get('case_id')}: undefined_support not recorded")
        elif support:
            problems.append(f"{c.get('case_id')}: {support}")
        if c.get("error"):
            problems.append(f"{c.get('case_id')}: errored ({c.get('error')})")
    ok, detail = _verdict(problems, "no primary or compensation predicate relied on an "
                                    "undefined yield/lifecycle/queue value")
    return ok, [e for c in _live(p) for e in _evidence(c)], detail


def _live_vs_offline_cases_distinguished(p: dict[str, Any], root: Path) -> Result:
    cases = _list(p, "cases")
    problems = [f"{c.get('case_id')}: live flag missing" for c in cases
                if not isinstance(c.get("live"), bool)]
    problems += [f"{c.get('case_id')}: offline case has no evidence" for c in cases
                 if c.get("live") is False and not _evidence(c)]
    offline = [c.get("case_id") for c in cases if c.get("live") is False]
    ok, detail = _verdict(problems, f"{len(cases) - len(offline)} live, offline fixtures "
                                    f"{offline}")
    return ok, [e for c in cases if c.get("live") is False for e in _evidence(c)], detail


def _validation_cases_passed(p: dict[str, Any], root: Path) -> Result:
    live = _live(p)
    bad = [c.get("case_id") for c in live if c.get("passed") is not True]
    return bool(live) and not bad, [e for c in live for e in _evidence(c)], (
        f"failed live cases: {bad}" if bad else f"{len(live)} live cases passed")


PACKET_REQUIREMENTS: tuple[tuple[str, Callable[[dict[str, Any], Path], Result]], ...] = (
    ("twelve_cycle_verify", _twelve_cycle_verify),
    ("menu_recovery_verified", _menu_recovery_verified),
    ("joint_full_case", _joint_full_case),
    ("alternative_full_case", _alternative_full_case),
    ("intermediate_rungs_covered", _intermediate_rungs_covered),
    ("closer_only_zero", _closer_only_zero),
    ("harm_positive_cases", _harm_positive_cases),
    ("harm_negative_cases", _harm_negative_cases),
    ("null_digest_chain", _null_digest_chain),
    ("null_observation_calls", _null_observation_calls),
    ("null_capture_timing", _null_capture_timing),
    ("capture_records_complete", _capture_records_complete),
    ("final_restore_verified", _final_restore_verified),
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
    ("validation_cases_passed", _validation_cases_passed),
)


def check_part1_packet(packet: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    """Evaluate every Part 1 packet requirement; `root` holds the evidence files."""
    root = Path(os.path.abspath(root or _REPO_ROOT))
    requirements: dict[str, dict[str, Any]] = {}
    for name, checker in PACKET_REQUIREMENTS:
        try:
            ok, evidence, detail = checker(packet if isinstance(packet, dict) else {}, root)
        except (_Missing, ValueError, TypeError, AttributeError, KeyError, OSError) as exc:
            ok, evidence, detail = False, [], f"missing or malformed evidence: {exc}"
        requirements[name] = {"passed": bool(ok), "evidence": sorted(set(evidence)),
                              "detail": detail}
    failed = [name for name, entry in requirements.items() if not entry["passed"]]
    return {"position_id": packet.get("position_id") if isinstance(packet, dict) else None,
            "passed": not failed, "failed_requirements": failed, "requirements": requirements}


def check_part1_gate(packets: list[dict[str, Any]], preflight: dict[str, Any], *,
                     root: Path | None = None) -> dict[str, Any]:
    """All three families passed under one code/toolset/contract identity and
    a passing preflight bound to the same positive-control probe."""
    failed: list[str] = []
    details: dict[str, str] = {}

    def fail(name: str, why: str) -> None:
        if name not in failed:
            failed.append(name)
        details[name] = "; ".join(filter(None, (details.get(name), why)))

    families: dict[str, Any] = {}
    by_family: dict[str, list[dict[str, Any]]] = {}
    for packet in packets:
        by_family.setdefault(str(packet.get("family")), []).append(packet)
    for family in FAMILIES:
        if len(by_family.get(family, [])) != 1:
            fail("family_coverage", f"{family}: {len(by_family.get(family, []))} packets")
    for extra in sorted(set(by_family) - set(FAMILIES)):
        fail("family_coverage", f"unknown family {extra!r}")

    pre_failed = preflight.get("failed_requirements")
    if preflight.get("passed") is not True or pre_failed:
        fail("preflight_passed", f"preflight failed: {pre_failed}")
    code = preflight.get("code_identity")
    toolsets = {r.get("family"): r.get("toolset_identity") for r in preflight.get("recipes", [])}
    probe = preflight.get("probe") or {}
    if probe.get("present") is not True:
        fail("probe_binding", "preflight has no positive-control probe")

    for family, group in sorted(by_family.items()):
        for packet in group:
            result = check_part1_packet(packet, root=root)
            durations = {}
            try:
                durations = scenario_durations(packet)
            except _Missing:
                pass
            families[family] = {
                "position_id": packet.get("position_id"), "passed": result["passed"],
                "failed_requirements": result["failed_requirements"],
                "requirements": result["requirements"], "scenario_durations": durations,
                "family_total_s": sum(d for d in durations.values() if d is not None)}
            if not result["passed"]:
                fail("packets_passed", f"{family}: {result['failed_requirements']}")
            for key in ("code_identity", "contract_identity"):
                if not code or packet.get(key) != code:
                    fail("identity_match", f"{family}: {key} {packet.get(key)!r} != preflight "
                                           f"code identity {code!r}")
            if packet.get("toolset_identity") != toolsets.get(family) or not toolsets.get(family):
                fail("identity_match", f"{family}: toolset identity differs from preflight")
            ref = packet.get("positive_control_probe") or {}
            if (ref.get("path"), ref.get("sha256")) != (probe.get("path"), probe.get("sha256")):
                fail("probe_binding", f"{family}: probe reference differs from preflight")
    return {"passed": not failed, "failed_requirements": failed, "details": details,
            "families": families,
            "preflight": {k: preflight.get(k) for k in ("passed", "failed_requirements",
                                                        "code_identity", "probe")}}
