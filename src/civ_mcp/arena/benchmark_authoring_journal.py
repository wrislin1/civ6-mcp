"""Durable, append-only journal of benchmark scenario authoring attempts.

Each scenario family gets at most two authoring identities: attempt 1 and,
only after attempt 1 failed terminally, one declared material substitute.
Every scenario carries a three-elapsed-hour clock (``AUTHORING_LIMIT_S``)
that starts before the first live command and never pauses: it survives
process restarts (UTC start time is persisted), counts same-process
monotonic progress even if the wall clock lags, and refuses -- rather than
absorbs -- a wall clock that moves backwards.

Writes are atomic (temp file + ``os.replace``) and a ``<path>.lock`` file
created with ``O_EXCL`` gives one process ownership of the journal so two
writers cannot edit the same clock concurrently.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2.0.0"
AUTHORING_LIMIT_S = 10_800


def elapsed_authoring_seconds(started_unix_s, now_unix_s, recorded_elapsed_s):
    """Wall-clock elapsed seconds, never below what was already recorded.

    A wall clock that steps backwards (NTP correction, a WSL clock jump after
    host sleep) cannot shorten the budget -- the recorded value wins -- and it
    must not strand the attempt either: raising here would make every later
    checkpoint, `finish` and `abandon` fail until the clock catches up. The
    anomaly is journaled by `_advance` instead.
    """
    return max(recorded_elapsed_s, now_unix_s - started_unix_s)


def clock_regression_seconds(started_unix_s, now_unix_s, recorded_elapsed_s) -> float:
    """How far `now` sits behind the last recorded wall position (0 when it does not)."""
    return max(0.0, started_unix_s + recorded_elapsed_s - now_unix_s)


def _read_journal(path: Path) -> dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported authoring journal schema in {path}")
    return doc


def _family_scenarios(doc: dict[str, Any], family: str) -> list[dict[str, Any]]:
    entry = doc.get("families", {}).get(family) or {}
    return [s for s in entry.get("scenarios", []) if isinstance(s, dict)]


def _import_predecessor(path: Path, family: str, predecessor: str, ref: str) -> dict[str, Any]:
    """The terminal-failed predecessor record from another journal, by reference."""
    if not path.is_file():
        raise ValueError(f"predecessor journal {path} does not exist")
    data = path.read_bytes()
    doc = _read_journal(path)
    found = [s for s in _family_scenarios(doc, family)
             if s.get("scenario_id") == predecessor and not s.get("imported_from")]
    if not found:
        raise ValueError(f"no predecessor {predecessor!r} of family {family!r} in {path}")
    record = copy.deepcopy(found[0])
    if record.get("status") != "failed":
        raise ValueError(f"predecessor {predecessor!r} in {path} is {record.get('status')!r}, "
                         "not terminal-failed")
    record["imported_from"] = {"journal": ref, "sha256": hashlib.sha256(data).hexdigest()}
    return record


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class AuthoringJournal:
    """One-process owner of the authoring attempt journal at ``path``."""

    def __init__(
        self,
        path: Path,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = Path(path)
        self._lock_path = Path(f"{self._path}.lock")
        self._wall = wall_clock
        self._monotonic = monotonic
        # scenario_id -> monotonic reading at the last persisted checkpoint
        # in *this* process (monotonic values are meaningless across processes).
        self._mono_marks: dict[str, float] = {}
        self._locked = False
        self._acquire_lock()
        try:
            self._doc = self._load()
        except Exception:
            self.close()
            raise

    # -- ownership ---------------------------------------------------------

    def _acquire_lock(self) -> None:
        for _ in range(2):
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    owner = int(self._lock_path.read_text().strip())
                except (OSError, ValueError):
                    owner = None
                if owner is None or _pid_alive(owner):
                    raise RuntimeError(
                        f"authoring journal {self._path} is locked by "
                        f"{'pid ' + str(owner) if owner else 'an unknown owner'} "
                        f"({self._lock_path})"
                    ) from None
                # Stale lock left by a dead process: reclaim it once.
                self._lock_path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(f"{os.getpid()}\n")
            self._locked = True
            return
        raise RuntimeError(f"authoring journal {self._path} is locked ({self._lock_path})")

    def close(self) -> None:
        if self._locked:
            self._lock_path.unlink(missing_ok=True)
            self._locked = False

    def __enter__(self) -> AuthoringJournal:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- persistence -------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema_version": SCHEMA_VERSION, "families": {}}
        doc = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported authoring journal schema in {self._path}")
        return doc

    def _commit(self, doc: dict[str, Any]) -> None:
        for family in doc["families"].values():
            family["total_elapsed_s"] = float(
                sum(s["recorded_elapsed_s"] for s in family["scenarios"])
            )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self._doc = doc

    # -- lookup ------------------------------------------------------------

    @staticmethod
    def _find(doc: dict[str, Any], scenario_id: str) -> dict[str, Any] | None:
        for family in doc["families"].values():
            for record in family["scenarios"]:
                if record["scenario_id"] == scenario_id:
                    return record
        return None

    def _require(self, doc: dict[str, Any], scenario_id: str) -> dict[str, Any]:
        record = self._find(doc, scenario_id)
        if record is None:
            raise ValueError(f"unknown scenario {scenario_id!r}")
        return record

    def record(self, scenario_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._require(self._doc, scenario_id))

    def family_total_seconds(self, family: str) -> float:
        entry = self._doc["families"].get(family)
        return float(entry["total_elapsed_s"]) if entry else 0.0

    # -- clock -------------------------------------------------------------

    def _current_elapsed(self, record: dict[str, Any]) -> tuple[float, float, float, float]:
        """Return ``(elapsed, wall_elapsed, now_unix_s, mono_now)`` without persisting."""
        now = self._wall()
        mono_now = self._monotonic()
        wall_elapsed = elapsed_authoring_seconds(
            record["started_unix_s"], now, record["wall_elapsed_s"]
        )
        recorded = record["recorded_elapsed_s"]
        mark = self._mono_marks.get(record["scenario_id"])
        mono_elapsed = recorded + max(0.0, mono_now - mark) if mark is not None else recorded
        return max(wall_elapsed, mono_elapsed, recorded), wall_elapsed, now, mono_now

    def _advance(self, record: dict[str, Any], label: str) -> float:
        """Advance ``record``'s clock in place; returns new elapsed seconds."""
        elapsed, wall_elapsed, now, _ = self._current_elapsed(record)
        regression = clock_regression_seconds(record["started_unix_s"], now,
                                              record["wall_elapsed_s"])
        record["recorded_elapsed_s"] = elapsed
        record["wall_elapsed_s"] = wall_elapsed
        checkpoint = {"label": label, "unix_s": now, "elapsed_s": elapsed}
        if regression > 0:
            # Visible in evidence; the budget itself is unaffected (never shortened).
            checkpoint["clock_backwards_s"] = regression
        record["checkpoints"].append(checkpoint)
        if elapsed > AUTHORING_LIMIT_S:
            record["expired"] = True
        return now

    def _mark(self, scenario_id: str) -> None:
        self._mono_marks[scenario_id] = self._monotonic()

    def remaining_seconds(self, scenario_id: str) -> float:
        record = self._require(self._doc, scenario_id)
        if record["expired"]:
            return 0.0
        elapsed = (
            record["recorded_elapsed_s"]
            if record["status"] != "open"
            else self._current_elapsed(record)[0]
        )
        return max(0.0, AUTHORING_LIMIT_S - elapsed)

    def is_expired(self, scenario_id: str) -> bool:
        record = self._require(self._doc, scenario_id)
        if record["expired"]:
            return True
        if record["status"] != "open":
            return False
        return self._current_elapsed(record)[0] > AUTHORING_LIMIT_S

    # -- attempts ----------------------------------------------------------

    def begin(
        self,
        *,
        family: str,
        scenario_id: str,
        predecessor: str | None,
        reason: str | None,
        material_change: str | None,
        predecessor_journal: Path | None = None,
        predecessor_journal_ref: str | None = None,
        sibling_journals: Sequence[Path] = (),
    ) -> dict[str, Any]:
        """Open (or resume) `scenario_id`'s clock.

        A substitute begun in a fresh journal names the failed predecessor's
        journal (`predecessor_journal`); that terminal-failed record is copied
        by reference (journal path + sha256, ``imported_from``) so substitution
        validation and the family total see both attempts. `sibling_journals`
        are every other attempt journal of the run: their identities count
        toward the two-identity family budget."""
        doc = copy.deepcopy(self._doc)
        existing = self._find(doc, scenario_id)
        if existing is not None:
            if existing["family"] != family:
                raise ValueError(
                    f"scenario {scenario_id!r} already belongs to family {existing['family']!r}"
                )
            if existing["status"] == "open":
                # Resuming (e.g. after a restart) continues the same clock.
                self._advance(existing, "resume")
                self._commit(doc)
                self._mark(scenario_id)
                if existing["expired"]:
                    raise ValueError("authoring clock expired")
            return copy.deepcopy(existing)

        entry = doc["families"].setdefault(family, {"scenarios": [], "total_elapsed_s": 0.0})
        scenarios = entry["scenarios"]
        # Identities journaled elsewhere (sibling attempt directories) count
        # toward the family's two-identity budget.
        elsewhere: dict[str, str] = {}
        for path in sibling_journals:
            path = Path(path)
            if path.resolve() == self._path.resolve() or not path.is_file():
                continue
            for other in _family_scenarios(_read_journal(path), family):
                if not other.get("imported_from"):
                    elsewhere.setdefault(other["scenario_id"], str(path))
        if scenario_id in elsewhere:
            raise ValueError(
                f"scenario {scenario_id!r} is already journaled in {elsewhere[scenario_id]}"
            )
        identities = set(elsewhere) | {
            s["scenario_id"] for s in scenarios if not s.get("imported_from")}
        if len(scenarios) >= 2 or len(identities | {scenario_id}) > 2:
            raise ValueError(
                f"family {family!r} is blocked: attempt and substitute already used "
                f"({sorted(identities)})"
            )
        if not scenarios and predecessor is not None and predecessor_journal is not None:
            scenarios.append(_import_predecessor(
                Path(predecessor_journal), family, predecessor,
                predecessor_journal_ref or Path(predecessor_journal).as_posix()))
        if not scenarios:
            if predecessor is not None:
                raise ValueError(
                    f"no failed predecessor {predecessor!r} in family {family!r} "
                    "(a substitute in a fresh attempt directory needs the predecessor's "
                    "journal: --predecessor-journal)"
                )
            if elsewhere:
                raise ValueError(
                    f"family {family!r} already has attempt 1 {sorted(elsewhere)}; a new "
                    "identity must be a declared substitute of a failed predecessor"
                )
            attempt = 1
        else:
            first = scenarios[0]
            if predecessor != first["scenario_id"] or first["status"] != "failed":
                raise ValueError(
                    f"substitution of {scenario_id!r} requires a failed predecessor "
                    f"(attempt 1 {first['scenario_id']!r} is {first['status']!r})"
                )
            if not (reason and reason.strip()):
                raise ValueError("substitution requires a non-empty reason")
            if not (material_change and material_change.strip()):
                raise ValueError("substitution requires a non-empty material_change")
            attempt = 2

        now = self._wall()
        record: dict[str, Any] = {
            "scenario_id": scenario_id,
            "family": family,
            "attempt": attempt,
            "predecessor": predecessor,
            "reason": reason,
            "material_change": material_change,
            "started_unix_s": now,
            "recorded_elapsed_s": 0.0,
            "wall_elapsed_s": 0.0,
            "checkpoints": [{"label": "begin", "unix_s": now, "elapsed_s": 0.0}],
            "stages": [],
            "status": "open",
            "expired": False,
            "finished_unix_s": None,
        }
        scenarios.append(record)
        self._commit(doc)
        self._mark(scenario_id)
        return copy.deepcopy(record)

    def _open_record(self, doc: dict[str, Any], scenario_id: str) -> dict[str, Any]:
        record = self._require(doc, scenario_id)
        if record["status"] != "open":
            raise ValueError(f"scenario {scenario_id!r} already finished ({record['status']})")
        return record

    def checkpoint(self, *, scenario_id: str, label: str = "checkpoint") -> float:
        doc = copy.deepcopy(self._doc)
        record = self._open_record(doc, scenario_id)
        self._advance(record, label)
        self._commit(doc)
        self._mark(scenario_id)
        if record["expired"]:
            raise ValueError("authoring clock expired")
        return record["recorded_elapsed_s"]

    def record_stage(
        self,
        *,
        scenario_id: str,
        stage: str,
        evidence: dict[str, Any],
        passed: bool | None = None,
    ) -> None:
        doc = copy.deepcopy(self._doc)
        record = self._open_record(doc, scenario_id)
        stages = record["stages"]
        previous_end = stages[-1]["ended_unix_s"] if stages else record["started_unix_s"]
        elapsed_before = sum(s["elapsed_s"] for s in stages)
        now = self._advance(record, f"stage:{stage}")
        stages.append(
            {
                "stage": stage,
                "started_unix_s": previous_end,
                "ended_unix_s": now,
                "elapsed_s": record["recorded_elapsed_s"] - elapsed_before,
                "passed": passed,
                "evidence": copy.deepcopy(evidence),
            }
        )
        self._commit(doc)
        self._mark(scenario_id)
        if record["expired"]:
            raise ValueError("authoring clock expired")

    def finish(self, *, scenario_id: str, passed: bool) -> dict[str, Any]:
        doc = copy.deepcopy(self._doc)
        record = self._open_record(doc, scenario_id)
        now = self._advance(record, "finish")
        expired = record["expired"]
        record["status"] = "passed" if passed and not expired else "failed"
        record["finished_unix_s"] = now
        self._commit(doc)
        self._mono_marks.pop(scenario_id, None)
        if expired:
            raise ValueError("authoring clock expired")
        return copy.deepcopy(record)
