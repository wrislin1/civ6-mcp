"""Resolve a Part 1 finish packet into the gate's evidence view, from raw files only.

`load_gate_evidence` reads the immutable finish packet written by the
authoring `finish` stage, verifies the sha256 of every file it references
(position, authoring input, journal, evidence index and each validation run's
`validation.json`), and derives the inline view the `benchmark_part1_gate`
checkers consume. Nothing the packet says about itself beyond those
references is used: identities come from the position document and the
validation lock, stage verdicts from the attempt's stage records, case
scores/harms/digests from the derived reports, capture costs and coverage
from the raw trials, durations from the authoring journals, and the attempt
list from enumerating every attempt directory of the family under
`benchmark_runs/plan3-part1/`. The loader never raises for missing or
altered evidence: it records each problem in `resolution.problems` and
leaves the affected section absent, so the matching requirement fails.

View keys: position_id, family, scenario_id, code_identity,
contract_identity, toolset_identity, expected_state_sha256, pilot_informed,
resolution{finish_packet, problems}, verify, menu_check, restore,
report_regeneration, objectives, declared_harms, cases, null, attempts,
scenarios, offline_audit, positive_control_probe, evidence_index,
measured_parameters.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

__all__ = ["ATTEMPTS_ROOT", "PREFLIGHT_OUTPUT", "PROBE_PROVENANCE", "load_gate_evidence"]

ATTEMPTS_ROOT = "benchmark_runs/plan3-part1"
PREFLIGHT_OUTPUT = "benchmarks/provenance/plan3-part1-offline-preflight.json"
PROBE_PROVENANCE = "benchmarks/provenance/plan3-part1-capture-probe.json"
JOURNAL_FILE = "authoring-journal.json"
INDEX_FILE = "evidence-index.json"
NULL_TAG = "null_discovery"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


class _Resolver:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.problems: list[str] = []

    def json(self, rel: str) -> Any:
        path = self.root / rel
        if not path.is_file():
            self.problems.append(f"{rel}: missing")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            self.problems.append(f"{rel}: unreadable ({exc})")
            return None

    def verified(self, ref: Any, what: str) -> Any:
        """Load a `{path, sha256}` reference, recording a mismatch as a problem."""
        if not (isinstance(ref, dict) and isinstance(ref.get("path"), str)):
            self.problems.append(f"{what}: no {{path, sha256}} reference in the finish packet")
            return None
        path = self.root / ref["path"]
        if path.is_file() and _sha(path) != ref.get("sha256"):
            self.problems.append(f"{ref['path']}: sha256 differs from the finish packet")
            return None
        return self.json(ref["path"])

    def ref(self, rel: str) -> dict[str, str] | None:
        path = self.root / rel
        return {"path": rel, "sha256": _sha(path)} if path.is_file() else None


def _stage_records(resolver: _Resolver, attempt_rel: str) -> list[dict[str, Any]]:
    directory = resolver.root / attempt_rel / "stages"
    records = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        doc = resolver.json(_rel(path, resolver.root))
        if isinstance(doc, dict):
            records.append({**doc, "_path": _rel(path, resolver.root)})
    return sorted(records, key=lambda r: r.get("sequence", 0))


def _latest_passed(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        latest[record.get("stage")] = record
    return {stage: r for stage, r in latest.items() if r.get("status") == "passed"}


def _loader_confirmed(evidence: dict[str, Any]) -> bool:
    """The crash-recovery loader ran and its result is not a failure (the
    authoring stage's own `_load_failed` rule; imported late to avoid a cycle)."""
    from civ_mcp.arena.benchmark_authoring import _load_failed
    return evidence.get("loader") == "restart_and_load" and \
        not _load_failed(evidence.get("loader_result"))


def _family_of(resolver: _Resolver, attempt_rel: str,
               records: list[dict[str, Any]]) -> str | None:
    journal = resolver.root / attempt_rel / JOURNAL_FILE
    if journal.is_file():
        families = list((json.loads(journal.read_text(encoding="utf-8")).get("families")
                         or {}))
        if families:
            return families[0]
    for record in records:
        recipe = (record.get("recipe") or {}).get("path")
        if recipe and (resolver.root / recipe).is_file():
            doc = yaml.safe_load((resolver.root / recipe).read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                return doc.get("family")
    return None


def _attempts(resolver: _Resolver, family: str) -> tuple[list[dict[str, Any]],
                                                         list[dict[str, Any]]]:
    """Every attempt directory of `family`, plus the union of its journal scenarios."""
    base = resolver.root / ATTEMPTS_ROOT
    dirs = sorted({p.parent for p in base.rglob("stages") if p.is_dir()}) if base.is_dir() else []
    attempts, scenarios = [], {}
    for directory in dirs:
        rel = _rel(directory, resolver.root)
        records = _stage_records(resolver, rel)
        if _family_of(resolver, rel, records) != family:
            continue
        journal_rel = f"{rel}/{JOURNAL_FILE}"
        journal = resolver.json(journal_rel) if (resolver.root / journal_rel).is_file() else None
        entries = (((journal or {}).get("families") or {}).get(family) or {}).get("scenarios", [])
        for entry in entries:
            sid = entry.get("scenario_id")
            known = scenarios.get(sid)
            if known is None or (entry.get("recorded_elapsed_s") or 0) >= \
                    (known.get("duration_s") or 0):
                scenarios[sid] = {
                    "scenario_id": sid, "status": entry.get("status"),
                    "duration_s": entry.get("recorded_elapsed_s"),
                    "attempt": entry.get("attempt"), "predecessor": entry.get("predecessor"),
                    "reason": entry.get("reason"),
                    "material_change": entry.get("material_change"), "journal": journal_rel}
        attempts.append({
            "attempt_dir": rel, "journal": resolver.ref(journal_rel),
            "evidence_index": resolver.ref(f"{rel}/{INDEX_FILE}"),
            "scenario_ids": sorted({str(e.get("scenario_id")) for e in entries}
                                   | {str(r.get("scenario_id")) for r in records}),
            "status": ("passed" if any(e.get("status") == "passed" for e in entries)
                       else "failed")})
    return attempts, sorted(scenarios.values(), key=lambda s: (s.get("attempt") or 0,
                                                               str(s["scenario_id"])))


def _case_view(resolver: _Resolver, run_dir: str, result: dict[str, Any],
               case_docs: dict[str, dict[str, Any]], position: dict[str, Any]) -> dict[str, Any]:
    case_id = result.get("case_id")
    trial_rel = f"{run_dir}/trials/trial-{int(result.get('trial_index', -1)):03d}.json"
    report_rel = f"{run_dir}/reports/{case_id}/report.json"
    report_path = resolver.root / report_rel
    if report_path.is_file() and _sha(report_path) != result.get("report_sha256"):
        resolver.problems.append(f"{report_rel}: sha256 differs from validation.json")
    trial = resolver.json(trial_rel) or {}
    report = resolver.json(report_rel) or {}
    score = report.get("score") or {}
    case_doc = case_docs.get(case_id) or {}
    if not case_doc:
        resolver.problems.append(f"case {case_id!r}: case document not found")
    return {
        "case_id": case_id, "tags": list(case_doc.get("tags") or []), "live": True,
        "evidence": [trial_rel, report_rel], "passed": result.get("passed"),
        "error": result.get("error"), "script_sha256": trial.get("script_sha256"),
        "actor_kind": trial.get("actor_kind"), "counting": trial.get("counting"),
        "pilot_informed": position.get("pilot_informed"),
        "primary_score": score.get("primary_score"), "gross_credit": score.get("gross_credit"),
        "harm_total": score.get("harm_total"),
        "objective_credits": {o.get("id"): o.get("credit") for o in score.get("objectives", [])},
        "harms": [{"id": h.get("id"), "status": h.get("status")} for h in score.get("harms", [])],
        "capture": trial.get("capture_summary"),
        "coverage_matches": trial.get("coverage") == position.get("coverage"),
        "validation_failures": list(trial.get("validation_failures") or []),
        "declared_rejections": list(case_doc.get("declared_rejections") or []),
        "observation_calls": len(trial.get("steps") or []),
        "digests": report.get("digests") or {},
    }


def load_gate_evidence(finish_packet_path: Path, *, root: Path) -> dict[str, Any]:
    """Derive the gate view of one finish packet from the raw files it references."""
    resolver = _Resolver(Path(root))
    packet_rel = _rel(Path(finish_packet_path) if Path(finish_packet_path).is_absolute()
                      else resolver.root / finish_packet_path, resolver.root)
    packet = resolver.json(packet_rel) or {}
    view: dict[str, Any] = {
        "position_id": packet.get("position_id"), "family": packet.get("family"),
        "scenario_id": packet.get("scenario_id"),
        "resolution": {"finish_packet": resolver.ref(packet_rel), "problems": resolver.problems},
    }
    position = resolver.verified(packet.get("position"), "position") or {}
    authoring = resolver.verified(packet.get("authoring_input"), "authoring_input") or {}
    resolver.verified(packet.get("journal"), "journal")
    index = resolver.verified(packet.get("evidence_index"), "evidence_index")
    if index is not None:
        view["evidence_index"] = packet["evidence_index"]
    if position:
        rubric = position.get("rubric") or {}
        view.update(
            contract_identity=position.get("contract_identity"),
            toolset_identity=(position.get("toolset") or {}).get("identity"),
            expected_state_sha256=position.get("expected_state_sha256"),
            pilot_informed=position.get("pilot_informed"),
            objectives=[{"id": o.get("id"), "rungs": [r.get("points") for r in o.get("rungs", [])]}
                        for o in rubric.get("objectives", [])],
            declared_harms=[h.get("id") for h in rubric.get("harms", [])])
        if position.get("family") is not None and position.get("family") != view["family"]:
            resolver.problems.append("finish packet family differs from the position")
    if authoring:
        view["measured_parameters"] = [
            {"name": m.get("name"), "value": m.get("value"), "evidence": m.get("evidence")}
            for m in authoring.get("measured_parameters", [])]

    attempt_rel = str(Path((packet.get("journal") or {}).get("path", "")).parent)
    latest = _latest_passed(_stage_records(resolver, attempt_rel)) if attempt_rel != "." else {}
    verify = latest.get("verify")
    if verify:
        result = (verify.get("evidence") or {}).get("result") or {}
        view["verify"] = {"evidence": verify["_path"],
                          "cycles_completed": result.get("cycles_completed"),
                          "digests": result.get("digests")}
    menu = latest.get("menu-check")
    if menu:
        ev = menu.get("evidence") or {}
        view["menu_check"] = {"evidence": menu["_path"], "loader_confirmed": _loader_confirmed(ev),
                              "reconnect": ev.get("reconnect"),
                              "digest_matches": ev.get("digest_matches"),
                              "identity_matches": ev.get("identity_matches")}
    validate = latest.get("validate")
    runs = packet.get("validation_runs") or []
    if validate:
        ev = validate.get("evidence") or {}
        restore = ev.get("restore") or {}
        view["restore"] = {"evidence": validate["_path"], "reloaded": restore.get("reloaded"),
                           "reconnect": restore.get("reconnect"), "digest": restore.get("digest")}
        on_disk = [resolver.ref(f"{r.get('run_dir')}/validation.json") for r in runs]
        view["report_regeneration"] = {
            "evidence": [validate["_path"], *(r["path"] for r in on_disk if r)],
            "reports_identical": ev.get("reports_identical"),
            "validation_sha256_matches": bool(runs) and all(
                ref is not None and ref["sha256"] == run.get("validation_sha256")
                == ev.get("validation_sha256") for ref, run in zip(on_disk, runs))}
    archive = latest.get("archive")
    capture = latest.get("capture")

    case_docs: dict[str, dict[str, Any]] = {}
    for rel in ((capture or {}).get("evidence") or {}).get("cases", []):
        doc = resolver.json(rel)
        if isinstance(doc, dict):
            case_docs[doc.get("case_id")] = doc
    cases, code_identities = [], set()
    for run in runs:
        run_dir = run.get("run_dir")
        validation_rel = f"{run_dir}/validation.json"
        path = resolver.root / validation_rel
        if path.is_file() and _sha(path) != run.get("validation_sha256"):
            resolver.problems.append(f"{validation_rel}: sha256 differs from the finish packet")
            continue
        validation = resolver.json(validation_rel) or {}
        lock = resolver.json(f"{run_dir}/session.json") or {}
        code_identities.add(lock.get("code_identity"))
        cases += [_case_view(resolver, run_dir, r, case_docs, position)
                  for r in validation.get("cases", [])]
    if len(code_identities) == 1:
        view["code_identity"] = code_identities.pop()
    elif code_identities:
        resolver.problems.append(f"validation runs disagree on code identity: {code_identities}")
    view["cases"] = cases

    null = next((c for c in cases if NULL_TAG in c["tags"]), None)
    if null is not None:
        null = copy.deepcopy(null)  # an independent section, never aliasing the case
        digests = null["digests"]
        view["null"] = {
            "evidence": null["evidence"], "gross_credit": null["gross_credit"],
            "harm_total": null["harm_total"], "primary_score": null["primary_score"],
            "initial_digest": digests.get("initial"), "final_digest": digests.get("final"),
            "steps": digests.get("steps") or [], "observation_calls": null["observation_calls"],
            "discoverability": ((archive or {}).get("evidence") or {}).get("required_facts"),
            "capture_scope": "full" if null["coverage_matches"] else "partial",
            "capture": null["capture"], "actor_kind": null["actor_kind"],
            "counting": null["counting"], "pilot_informed": null["pilot_informed"]}

    family = view.get("family")
    if family:
        view["attempts"], view["scenarios"] = _attempts(resolver, family)
    if (resolver.root / PROBE_PROVENANCE).is_file():
        view["positive_control_probe"] = resolver.ref(PROBE_PROVENANCE)
    preflight = resolver.json(PREFLIGHT_OUTPUT) if (resolver.root / PREFLIGHT_OUTPUT).is_file() \
        else None
    if isinstance(preflight, dict):
        audit = preflight.get("historical_audit") or {}
        view["offline_audit"] = {"evidence": PREFLIGHT_OUTPUT,
                                 **{k: audit.get(k) for k in ("membership_matches",
                                                              "trial_count",
                                                              "uncredited_count")}}
    return view
