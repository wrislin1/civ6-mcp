"""Resolve a Part 1 finish packet into the gate's evidence view, from raw files only.

`load_gate_evidence` reads the immutable finish packet written by the
authoring `finish` stage and verifies the sha256 of every file it references
(position, authoring input, journal, evidence index). Every other file read
from the packet's attempt -- stage records, validation results, locks,
trials, reports and case documents -- is taken ONLY from that hash-verified
`evidence-index.json`: a file must be listed there and match its recorded
sha256, otherwise it is not used. A file present in the attempt directory but
absent from the index is recorded as `unindexed_stage_record` /
`unindexed_attempt_file` (it fails `evidence_index_complete`).

Nothing the packet says about itself beyond those references is used:
identities come from the position and the validation lock, stage verdicts
from the latest indexed record per stage, case scores/harms/digests from the
derived reports, capture costs and coverage from the raw trials, durations
from the authoring journals, and the attempt list from enumerating every
attempt directory of the family under `benchmark_runs/plan3-part1/`.

The loader never raises. Malformed JSON, missing keys, wrong types, a packet
outside `root` or any other error becomes a `resolution.problems` entry and
the affected section is omitted, so the matching requirement fails.

View keys: position_id, family, scenario_id, code_identity,
contract_identity, toolset_identity, expected_state_sha256, pilot_informed,
resolution{finish_packet, problems, unindexed}, verify, menu_check, restore,
report_regeneration, objectives, declared_harms, cases, null, attempts,
scenarios, offline_audit, positive_control_probe, evidence_index,
measured_parameters.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import yaml

__all__ = ["ATTEMPTS_ROOT", "PREFLIGHT_OUTPUT", "PROBE_PROVENANCE", "UNINDEXED_PREFIXES",
           "load_gate_evidence"]

ATTEMPTS_ROOT = "benchmark_runs/plan3-part1"
PREFLIGHT_OUTPUT = "benchmarks/provenance/plan3-part1-offline-preflight.json"
PROBE_PROVENANCE = "benchmarks/provenance/plan3-part1-capture-probe.json"
JOURNAL_FILE = "authoring-journal.json"
INDEX_FILE = "evidence-index.json"
NULL_TAG = "null_discovery"
UNINDEXED_PREFIXES = ("unindexed_stage_record", "unindexed_attempt_file")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _is_transient(rel: str) -> bool:
    from civ_mcp.arena.benchmark_authoring import _is_transient as transient
    return transient(rel)


class _Resolver:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.problems: list[str] = []
        self.unindexed: list[str] = []
        self.indexed: dict[str, str] = {}

    def problem(self, text: str) -> None:
        self.problems.append(text)

    def raw(self, rel: str) -> Any:
        """Guarded read of any JSON file; problems instead of exceptions."""
        try:
            path = self.root / rel
            if not path.is_file():
                self.problem(f"{rel}: missing")
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            self.problem(f"{rel}: unreadable ({type(exc).__name__}: {exc})")
            return None

    def verified(self, ref: Any, what: str) -> Any:
        """Load a finish-packet `{path, sha256}` reference."""
        if not (isinstance(ref, dict) and isinstance(ref.get("path"), str)
                and isinstance(ref.get("sha256"), str)):
            self.problem(f"{what}: no {{path, sha256}} reference in the finish packet")
            return None
        path = self.root / ref["path"]
        try:
            if path.is_file() and _sha(path) != ref["sha256"]:
                self.problem(f"{ref['path']}: sha256 differs from the finish packet")
                return None
        except OSError as exc:
            self.problem(f"{ref['path']}: unreadable ({exc})")
            return None
        return self.raw(ref["path"])

    def indexed_json(self, rel: Any) -> Any:
        """Read a file only if the verified evidence index lists it with this sha256."""
        if not isinstance(rel, str) or rel not in self.indexed:
            self.problem(f"{rel}: not listed in the evidence index; not used")
            return None
        path = self.root / rel
        try:
            if not path.is_file() or _sha(path) != self.indexed[rel]:
                self.problem(f"{rel}: sha256 differs from the evidence index; not used")
                return None
        except OSError as exc:
            self.problem(f"{rel}: unreadable ({exc})")
            return None
        return self.raw(rel)

    def ref(self, rel: str) -> dict[str, str] | None:
        try:
            path = self.root / rel
            return {"path": rel, "sha256": _sha(path)} if path.is_file() else None
        except OSError:
            return None

    def guard(self, section: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- the loader never raises
            self.problem(f"{section}: malformed evidence ({type(exc).__name__}: {exc})")


def _latest_passed(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in sorted(records, key=lambda r: r.get("sequence") if isinstance(
            r.get("sequence"), int) else -1):
        latest[record.get("stage")] = record
    return {stage: r for stage, r in latest.items() if r.get("status") == "passed"}


def _loader_confirmed(evidence: dict[str, Any]) -> bool:
    """The crash-recovery loader ran and its result is not a failure (the
    authoring stage's own `_load_failed` rule; imported late to avoid a cycle)."""
    from civ_mcp.arena.benchmark_authoring import _load_failed
    return evidence.get("loader") == "restart_and_load" and \
        not _load_failed(evidence.get("loader_result"))


def _indexed_stage_records(resolver: _Resolver, attempt_rel: str) -> list[dict[str, Any]]:
    prefix = f"{attempt_rel}/stages/"
    records = []
    for rel in sorted(resolver.indexed):
        if rel.startswith(prefix) and rel.endswith(".json") and "/" not in rel[len(prefix):]:
            doc = resolver.indexed_json(rel)
            if isinstance(doc, dict):
                records.append({**doc, "_path": rel})
    return records


def _record_unindexed(resolver: _Resolver, attempt_rel: str, index_rel: str) -> None:
    directory = resolver.root / attempt_rel
    for path in sorted(directory.rglob("*")) if directory.is_dir() else []:
        rel = _rel(path, resolver.root)
        if not path.is_file() or rel == index_rel or rel in resolver.indexed \
                or _is_transient(rel):
            continue
        kind = "unindexed_stage_record" if f"{attempt_rel}/stages/" in rel \
            else "unindexed_attempt_file"
        entry = f"{kind}: {rel}"
        resolver.unindexed.append(entry)
        resolver.problem(entry)


def _sibling_family(resolver: _Resolver, attempt_rel: str) -> tuple[str | None, Any]:
    journal_rel = f"{attempt_rel}/{JOURNAL_FILE}"
    journal = resolver.raw(journal_rel) if (resolver.root / journal_rel).is_file() else None
    families = list(_dict(_dict(journal).get("families")))
    if families:
        return str(families[0]), journal
    for path in sorted((resolver.root / attempt_rel / "stages").glob("*.json")):
        record = _dict(resolver.raw(_rel(path, resolver.root)))
        recipe = _dict(record.get("recipe")).get("path")
        if isinstance(recipe, str) and (resolver.root / recipe).is_file():
            try:
                doc = yaml.safe_load((resolver.root / recipe).read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                resolver.problem(f"{recipe}: unreadable ({exc})")
                continue
            if isinstance(doc, dict) and doc.get("family"):
                return str(doc["family"]), journal
    return None, journal


def _attempts(resolver: _Resolver, family: str) -> tuple[list[dict[str, Any]],
                                                         list[dict[str, Any]]]:
    """Every attempt directory of `family`, plus the union of its journal scenarios."""
    base = resolver.root / ATTEMPTS_ROOT
    dirs = sorted({p.parent for p in base.rglob("stages") if p.is_dir()}) if base.is_dir() else []
    attempts, scenarios = [], {}
    for directory in dirs:
        rel = _rel(directory, resolver.root)
        found, journal = _sibling_family(resolver, rel)
        if found is None:
            resolver.problem(f"{rel}: attempt family undeterminable")
            continue
        if found != family:
            continue
        journal_rel = f"{rel}/{JOURNAL_FILE}"
        index_ref = resolver.ref(f"{rel}/{INDEX_FILE}")
        if index_ref is not None:
            listed = {e.get("path"): e.get("sha256") for e in _items(
                _dict(resolver.raw(index_ref["path"])).get("files")) if isinstance(e, dict)}
            journal_ref = resolver.ref(journal_rel)
            if journal_ref and listed.get(journal_rel) != journal_ref["sha256"]:
                resolver.problem(f"{journal_rel}: not hash-bound by {index_ref['path']}")
                journal = None
        # A substitute's journal carries its failed predecessor by reference
        # (`imported_from`); the predecessor's own journal is its evidence.
        entries = [e for e in _items(_dict(_dict(_dict(journal).get("families")).get(family))
                                     .get("scenarios"))
                   if isinstance(e, dict) and not e.get("imported_from")]
        for entry in entries:
            sid = entry.get("scenario_id")
            known = scenarios.get(sid)
            elapsed = entry.get("recorded_elapsed_s")
            if known is None or (isinstance(elapsed, (int, float))
                                 and elapsed >= (known.get("duration_s") or 0)):
                scenarios[sid] = {
                    "scenario_id": sid, "status": entry.get("status"), "duration_s": elapsed,
                    "attempt": entry.get("attempt"), "predecessor": entry.get("predecessor"),
                    "reason": entry.get("reason"),
                    "material_change": entry.get("material_change"), "journal": journal_rel}
        attempts.append({
            "attempt_dir": rel, "journal": resolver.ref(journal_rel), "evidence_index": index_ref,
            "scenario_ids": sorted({str(e.get("scenario_id")) for e in entries}),
            "status": ("passed" if any(e.get("status") == "passed" for e in entries)
                       else "failed")})
    return attempts, sorted(scenarios.values(), key=lambda s: (
        s.get("attempt") if isinstance(s.get("attempt"), int) else 0, str(s["scenario_id"])))


def _case_view(resolver: _Resolver, run_dir: str, result: dict[str, Any],
               case_docs: dict[str, dict[str, Any]], position: dict[str, Any]) -> dict[str, Any]:
    case_id = result.get("case_id")
    index = result.get("trial_index")
    if not isinstance(index, int):
        raise ValueError(f"case {case_id!r} has trial_index {index!r}")
    trial_rel = f"{run_dir}/trials/trial-{index:03d}.json"
    report_rel = f"{run_dir}/reports/{case_id}/report.json"
    if resolver.indexed.get(report_rel) not in (None, result.get("report_sha256")):
        resolver.problem(f"{report_rel}: sha256 differs from validation.json")
    trial = _dict(resolver.indexed_json(trial_rel))
    report = _dict(resolver.indexed_json(report_rel))
    score = _dict(report.get("score"))
    case_doc = _dict(case_docs.get(case_id))
    if not case_doc:
        resolver.problem(f"case {case_id!r}: indexed case document not found")
    return {
        "case_id": case_id, "tags": list(_items(case_doc.get("tags"))), "live": True,
        "evidence": [trial_rel, report_rel], "passed": result.get("passed"),
        "error": result.get("error"), "script_sha256": trial.get("script_sha256"),
        "actor_kind": trial.get("actor_kind"), "counting": trial.get("counting"),
        "pilot_informed": position.get("pilot_informed"),
        "primary_score": score.get("primary_score"), "gross_credit": score.get("gross_credit"),
        "harm_total": score.get("harm_total"),
        "objective_credits": {o.get("id"): o.get("credit")
                              for o in _items(score.get("objectives")) if isinstance(o, dict)},
        "harms": [{"id": h.get("id"), "status": h.get("status")}
                  for h in _items(score.get("harms")) if isinstance(h, dict)],
        "capture": trial.get("capture_summary"),
        "coverage_matches": bool(trial) and trial.get("coverage") == position.get("coverage"),
        "validation_failures": list(_items(trial.get("validation_failures"))),
        "declared_rejections": list(_items(case_doc.get("declared_rejections"))),
        "observation_calls": len(_items(trial.get("steps"))),
        "digests": _dict(report.get("digests")),
    }


def load_gate_evidence(finish_packet_path: Path, *, root: Path,
                       probe_path: str | None = None) -> dict[str, Any]:
    """Derive the gate view of one finish packet from the raw files it references.

    Never raises: every problem is recorded in `resolution.problems`."""
    resolver = _Resolver(Path(root))
    view: dict[str, Any] = {"resolution": {"finish_packet": None, "problems": resolver.problems,
                                           "unindexed": resolver.unindexed}}
    resolver.guard("finish packet", lambda: _load(resolver, view, Path(finish_packet_path),
                                                  probe_path or PROBE_PROVENANCE))
    return view


def _load(resolver: _Resolver, view: dict[str, Any], packet_path: Path,
          probe_path: str = PROBE_PROVENANCE) -> None:
    absolute = packet_path if packet_path.is_absolute() else resolver.root / packet_path
    try:
        packet_rel = _rel(absolute, resolver.root)
    except ValueError:
        resolver.problem(f"finish packet {packet_path} is outside the evidence root "
                         f"{resolver.root}")
        return
    packet = resolver.raw(packet_rel)
    if not isinstance(packet, dict):
        resolver.problem(f"{packet_rel}: finish packet is not a JSON object")
        return
    view["resolution"]["finish_packet"] = resolver.ref(packet_rel)
    view.update(position_id=packet.get("position_id"), family=packet.get("family"),
                scenario_id=packet.get("scenario_id"))
    position = _dict(resolver.verified(packet.get("position"), "position"))
    authoring = _dict(resolver.verified(packet.get("authoring_input"), "authoring_input"))
    resolver.verified(packet.get("journal"), "journal")
    index = resolver.verified(packet.get("evidence_index"), "evidence_index")
    if isinstance(index, dict):
        view["evidence_index"] = packet["evidence_index"]
        resolver.indexed = {e["path"]: e["sha256"] for e in _items(index.get("files"))
                            if isinstance(e, dict) and isinstance(e.get("path"), str)
                            and isinstance(e.get("sha256"), str)}
        attempt_rel = str(PurePosixPath(packet["evidence_index"]["path"]).parent)
        _record_unindexed(resolver, attempt_rel, packet["evidence_index"]["path"])
    else:
        attempt_rel = None

    def identities() -> None:
        if not position:
            return
        rubric = _dict(position.get("rubric"))
        view.update(
            contract_identity=position.get("contract_identity"),
            toolset_identity=_dict(position.get("toolset")).get("identity"),
            expected_state_sha256=position.get("expected_state_sha256"),
            pilot_informed=position.get("pilot_informed"),
            objectives=[{"id": o.get("id"), "rungs": [_dict(r).get("points")
                                                      for r in _items(o.get("rungs"))]}
                        for o in _items(rubric.get("objectives")) if isinstance(o, dict)],
            declared_harms=[_dict(h).get("id") for h in _items(rubric.get("harms"))])
        if position.get("family") is not None and position.get("family") != view["family"]:
            resolver.problem("finish packet family differs from the position")

    def measured() -> None:
        if authoring:
            view["measured_parameters"] = [
                {"name": m.get("name"), "value": m.get("value"), "evidence": m.get("evidence")}
                for m in _items(authoring.get("measured_parameters")) if isinstance(m, dict)]

    resolver.guard("position", identities)
    resolver.guard("authoring input", measured)
    latest = _latest_passed(_indexed_stage_records(resolver, attempt_rel)) if attempt_rel else {}
    runs = [r for r in _items(packet.get("validation_runs")) if isinstance(r, dict)]

    def stages() -> None:
        verify = latest.get("verify")
        if verify:
            result = _dict(_dict(verify.get("evidence")).get("result"))
            view["verify"] = {"evidence": verify["_path"],
                              "cycles_completed": result.get("cycles_completed"),
                              "digests": result.get("digests")}
        menu = latest.get("menu-check")
        if menu:
            ev = _dict(menu.get("evidence"))
            view["menu_check"] = {"evidence": menu["_path"],
                                  "loader_confirmed": _loader_confirmed(ev),
                                  "reconnect": ev.get("reconnect"),
                                  "digest_matches": ev.get("digest_matches"),
                                  "identity_matches": ev.get("identity_matches")}
        validate = latest.get("validate")
        if validate:
            ev = _dict(validate.get("evidence"))
            restore = _dict(ev.get("restore"))
            view["restore"] = {"evidence": validate["_path"], "reloaded": restore.get("reloaded"),
                               "reconnect": restore.get("reconnect"),
                               "digest": restore.get("digest")}
            rels = [f"{r.get('run_dir')}/validation.json" for r in runs]
            view["report_regeneration"] = {
                "evidence": [validate["_path"], *rels],
                "reports_identical": ev.get("reports_identical"),
                "validation_sha256_matches": bool(runs) and all(
                    resolver.indexed.get(rel) == run.get("validation_sha256")
                    == ev.get("validation_sha256") for rel, run in zip(rels, runs))}

    resolver.guard("stage records", stages)

    def cases() -> None:
        case_docs: dict[str, dict[str, Any]] = {}
        capture = latest.get("capture") or {}
        for rel in _items(_dict(capture.get("evidence")).get("cases")):
            doc = resolver.indexed_json(rel)
            if isinstance(doc, dict):
                case_docs[doc.get("case_id")] = doc
        out, code_identities = [], set()
        for run in runs:
            run_dir = run.get("run_dir")
            validation_rel = f"{run_dir}/validation.json"
            if resolver.indexed.get(validation_rel) != run.get("validation_sha256"):
                resolver.problem(f"{validation_rel}: sha256 differs from the finish packet")
                continue
            validation = _dict(resolver.indexed_json(validation_rel))
            lock = _dict(resolver.indexed_json(f"{run_dir}/session.json"))
            code_identities.add(lock.get("code_identity"))
            for result in _items(validation.get("cases")):
                resolver.guard(f"{validation_rel} case", lambda r=result: out.append(
                    _case_view(resolver, run_dir, _dict(r), case_docs, position)))
        if len(code_identities) == 1:
            view["code_identity"] = code_identities.pop()
        elif code_identities:
            resolver.problem(f"validation runs disagree on code identity: {code_identities}")
        view["cases"] = out
        null = next((c for c in out if NULL_TAG in c["tags"]), None)
        if null is not None:
            null = copy.deepcopy(null)  # an independent section, never aliasing the case
            digests = null["digests"]
            archive = latest.get("archive") or {}
            view["null"] = {
                "evidence": null["evidence"], "gross_credit": null["gross_credit"],
                "harm_total": null["harm_total"], "primary_score": null["primary_score"],
                "initial_digest": digests.get("initial"), "final_digest": digests.get("final"),
                "steps": _items(digests.get("steps")),
                "observation_calls": null["observation_calls"],
                "discoverability": _dict(archive.get("evidence")).get("required_facts"),
                "capture_scope": "full" if null["coverage_matches"] else "partial",
                "capture": null["capture"], "actor_kind": null["actor_kind"],
                "counting": null["counting"], "pilot_informed": null["pilot_informed"]}

    resolver.guard("validation runs", cases)

    def history() -> None:
        family = view.get("family")
        if isinstance(family, str) and family:
            view["attempts"], view["scenarios"] = _attempts(resolver, family)

    resolver.guard("attempt history", history)

    def shared() -> None:
        if (resolver.root / probe_path).is_file():
            view["positive_control_probe"] = resolver.ref(probe_path)
        if (resolver.root / PREFLIGHT_OUTPUT).is_file():
            audit = _dict(_dict(resolver.raw(PREFLIGHT_OUTPUT)).get("historical_audit"))
            view["offline_audit"] = {"evidence": PREFLIGHT_OUTPUT,
                                     **{k: audit.get(k) for k in ("membership_matches",
                                                                  "trial_count",
                                                                  "uncredited_count")}}

    resolver.guard("shared evidence", shared)
