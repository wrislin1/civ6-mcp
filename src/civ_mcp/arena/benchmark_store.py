"""Append-only session and raw-evidence store for the controlled-position
benchmark runner.

This module owns the on-disk evidence layer for one benchmark run:

    <run_dir>/
        session.json    immutable lock, canonical JSON, byte-for-byte on reopen
        journal.jsonl   append-only newline-delimited event log
        attempts/       non-scoreable infrastructure-attempt records
        trials/         immutable raw scoreable evidence (trial-NNN.json)

Design invariants (see docs/superpowers/specs/2026-08-30-arena-controlled-
position-benchmark-design.md):

- Raw scoreable evidence is committed before scoring ever runs.
- A committed trial is never re-executed, even if no report was ever
  generated for it -- `completed_indices()` is the sole source of truth.
- Attempts (`attempts/`) are infrastructure noise. They never count as
  trials and reports never scan them.
- `session.json` is canonical JSON; reopening a run refuses any lock that
  does not match byte-for-byte.
- `commit_trial` is crash-safe: it writes a temp file, flushes and fsyncs
  it, refuses an existing destination, then atomically `os.replace`s it
  into place. A crash before the replace leaves no completed trial behind.
- Trial indices are 1-based (`trial-001.json`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

__all__ = [
    "BenchmarkStore",
    "BenchmarkStoreError",
    "SessionLockMismatchError",
    "TrialExistsError",
    "TrialProvenanceError",
    "compute_session_fingerprint",
    "trial_filename",
]


class BenchmarkStoreError(Exception):
    """Base class for BenchmarkStore errors."""


class SessionLockMismatchError(BenchmarkStoreError):
    """Raised when a provided lock does not match the run's session.json
    byte-for-byte (canonical JSON) on reopen."""


class TrialExistsError(BenchmarkStoreError):
    """Raised when `commit_trial` targets an index that already has
    committed raw evidence. A committed trial is never re-executed."""


class TrialProvenanceError(BenchmarkStoreError):
    """Raised by `is_trial_complete` when a committed trial file exists on
    disk but its stamped provenance does not prove it belongs to this
    store's lock: a corrupt/unparseable file, a missing or mismatched
    `session_fingerprint`, or -- for a counted campaign block (a lock that
    carries a non-empty `campaign_fingerprint`) -- a missing or mismatched
    `campaign_fingerprint`. Never silently treated as incomplete or
    complete: a stale, copied, or single-stamped trial file must stop the
    campaign for operator review, not quietly re-run (double-counting an
    episode) or quietly pass (counting evidence that does not actually
    belong to this lock)."""


# `{index:03d}` (used by _trial_path/_attempt_path below) is a MINIMUM
# width, not a fixed one -- index 1000 writes "trial-1000.json" (4 digits).
# `\d{3,}` (three-or-more) matches that; an exact `\d{3}` would silently
# ignore any index >= 1000, making completed_indices()/attempt_count() miss
# it (re-execution, then a TrialExistsError crash; the attempt cap disabled).
_TRIAL_NAME_RE = re.compile(r"^trial-(\d{3,})\.json$")
_ATTEMPT_NAME_RE = re.compile(r"^trial-(\d{3,})-attempt-(\d{3,})\.json$")


def trial_filename(index: int) -> str:
    """Canonical committed-trial evidence filename for `index`.

    G12: the `{index:03d}` (MINIMUM width, not fixed -- see `_TRIAL_NAME_RE`'s
    own comment) convention used to live twice: once here and once
    duplicated in `benchmark_report.py`. A single shared function means the
    convention can only drift out of sync with itself if this one function
    changes, not if a second copy is edited and the first is forgotten.
    """
    return f"trial-{int(index):03d}.json"


def _canonical_bytes(value: object) -> bytes:
    """Canonical-JSON encoding: sorted keys, no incidental whitespace. Two
    dicts that are equal encode to identical bytes regardless of the order
    their keys were supplied in."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return encoded.encode("utf-8")


def compute_session_fingerprint(session_lock: Mapping[str, object]) -> str:
    """Re-derive a session lock's `session_fingerprint` from the lock's own
    OTHER fields -- the exact canonical computation
    `benchmark_gates.build_session_lock` performs when minting the lock
    (canonical-JSON sha256 of the lock dict BEFORE the
    `session_fingerprint` key exists on it, i.e.
    `benchmark_manifest.fingerprint` over everything else).

    G1 (external review wave G): the minting side and every consumer that
    trusts a recorded session.json as evidence
    (`benchmark_report.build_report`,
    `benchmark_campaign_report.build_campaign_report`,
    `benchmark_admission.block_is_complete`) share THIS one function so the
    computation can never drift between them. A session.json whose recorded
    `session_fingerprint` does not match this recomputation was edited
    after minting (e.g. `block_id`/`model_config` rewritten with the stamp
    left untouched, re-homing another block's trials) and must be refused
    wherever it is consumed -- exactly as
    `benchmark_campaign_report._verify_campaign_fingerprint` already
    refuses an edited campaign.json. Lives here (the session.json storage
    layer, already imported by every consumer) rather than in
    `benchmark_gates` so the pure evidence-to-report modules never have to
    pull in the gates module's heavy backend import graph."""
    body = {key: value for key, value in session_lock.items() if key != "session_fingerprint"}
    return hashlib.sha256(_canonical_bytes(body)).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fsync_write_bytes(path: Path, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


class BenchmarkStore:
    """Append-only evidence store for one benchmark run directory."""

    SESSION_FILE = "session.json"
    JOURNAL_FILE = "journal.jsonl"
    ATTEMPTS_DIR = "attempts"
    TRIALS_DIR = "trials"

    def __init__(self, run_dir: Path, lock: dict, *, fingerprint: str | None) -> None:
        self.run_dir = Path(run_dir)
        self.lock = dict(lock)
        self.fingerprint = fingerprint
        # A counted campaign block's lock carries a non-empty
        # campaign_fingerprint (see benchmark_campaign.build_campaign_lock /
        # benchmark_gates.build_session_lock); an ungated/smoke lock has
        # none. is_trial_complete only demands the second stamp when this
        # store's own lock actually declares one.
        self.campaign_fingerprint = lock.get("campaign_fingerprint")
        self._seq_lock = threading.Lock()
        self._seq = self._load_next_sequence()

    # -- construction -----------------------------------------------------

    @classmethod
    def create(cls, run_dir: str | Path, lock: dict) -> "BenchmarkStore":
        """Create a new run directory (or reattach to one whose session.json
        already matches `lock` byte-for-byte). Raises SessionLockMismatchError
        if the run directory exists with a different lock."""
        return cls._open_or_create(run_dir, lock, creating=True)

    @classmethod
    def open(cls, run_dir: str | Path, lock: dict) -> "BenchmarkStore":
        """Reopen an existing run directory. Raises SessionLockMismatchError
        if `lock` does not match the stored session.json byte-for-byte, or
        FileNotFoundError if no session was ever created there."""
        return cls._open_or_create(run_dir, lock, creating=False)

    @classmethod
    def _open_or_create(cls, run_dir: str | Path, lock: dict, *, creating: bool) -> "BenchmarkStore":
        # G8: a lock with no (or an empty) session_fingerprint must be
        # refused outright rather than silently accepted with
        # fingerprint=None -- a None fingerprint lets an unstamped stale
        # trial pass the runner's resume check (`_verify_resume_provenance`
        # compares `stamped != self.store.fingerprint`; None == None
        # passes) and makes the report's lock/trial fingerprint cross-check
        # a no-op (`lock_fp and trial_fp and ...` short-circuits when
        # either is falsy).
        if not lock.get("session_fingerprint"):
            raise BenchmarkStoreError(
                "session lock is missing a non-empty 'session_fingerprint' -- "
                "refusing to create or open a run without one (a fingerprint-less "
                "lock would let a stale/copied trial's missing stamp silently pass "
                "resume verification)"
            )
        run_dir = Path(run_dir)
        session_path = run_dir / cls.SESSION_FILE
        provided = _canonical_bytes(lock)

        if session_path.exists():
            stored = session_path.read_bytes()
            if stored != provided:
                raise SessionLockMismatchError(
                    f"session lock mismatch at {session_path}: provided lock does not "
                    "match the recorded session byte-for-byte"
                )
        elif creating:
            run_dir.mkdir(parents=True, exist_ok=True)
            _fsync_write_bytes(session_path, provided)
        else:
            raise FileNotFoundError(f"no session found at {session_path}")

        (run_dir / cls.TRIALS_DIR).mkdir(parents=True, exist_ok=True)
        (run_dir / cls.ATTEMPTS_DIR).mkdir(parents=True, exist_ok=True)
        (run_dir / cls.JOURNAL_FILE).touch(exist_ok=True)

        return cls(run_dir, lock, fingerprint=lock.get("session_fingerprint"))

    # -- journal ------------------------------------------------------------

    def append_event(
        self,
        event: str,
        *,
        trial_index: int | None = None,
        attempt_ordinal: int | None = None,
        failure_class: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> dict:
        """Append one event to journal.jsonl. Sequence numbers are monotonic
        and continue across process resume (derived from the journal already
        on disk, never reset to zero on reopen).

        G9: `details` is an explicit parameter, not a `**kwargs` catch-all --
        a `**details` catch-all would capture the literal keyword name
        "details" as just another entry in itself, so a caller passing
        `details={...}` (every real caller in this codebase) landed
        double-nested as `record["details"]["details"]` instead of flat.
        """
        with self._seq_lock:
            seq = self._seq
            self._seq += 1

        record: dict[str, object] = {
            "seq": seq,
            "ts": _utc_now_iso(),
            "trial_index": trial_index,
            "attempt_ordinal": attempt_ordinal,
            "event": event,
            "failure_class": failure_class,
        }
        if details:
            record["details"] = dict(details)

        line = _canonical_bytes(record).decode("utf-8")
        journal_path = self.run_dir / self.JOURNAL_FILE
        with open(journal_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return record

    def _load_next_sequence(self) -> int:
        journal_path = self.run_dir / self.JOURNAL_FILE
        if not journal_path.exists():
            return 0
        last_seq = -1
        with open(journal_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seq = record.get("seq")
                if isinstance(seq, int) and seq > last_seq:
                    last_seq = seq
        return last_seq + 1

    # -- attempts (infrastructure failures; never scoreable trials) --------

    def _attempt_path(self, index: int, ordinal: int) -> Path:
        return self.run_dir / self.ATTEMPTS_DIR / f"trial-{index:03d}-attempt-{ordinal:03d}.json"

    def _attempt_tmp_path(self, index: int, ordinal: int) -> Path:
        return self.run_dir / self.ATTEMPTS_DIR / f".trial-{index:03d}-attempt-{ordinal:03d}.json.tmp"

    def attempt_count(self, index: int) -> int:
        """Number of infrastructure-attempt records recorded for `index`,
        surviving process resume (derived purely from files on disk)."""
        attempts_dir = self.run_dir / self.ATTEMPTS_DIR
        if not attempts_dir.is_dir():
            return 0
        count = 0
        for path in attempts_dir.iterdir():
            if not path.is_file():
                continue
            match = _ATTEMPT_NAME_RE.match(path.name)
            if match and int(match.group(1)) == index:
                count += 1
        return count

    def record_attempt(self, index: int, payload: dict) -> int:
        """Record one non-scoreable infrastructure attempt for trial `index`.
        Never counts toward completed_indices(). Returns the assigned
        (1-based) attempt ordinal."""
        ordinal = self.attempt_count(index) + 1
        dest = self._attempt_path(index, ordinal)
        tmp = self._attempt_tmp_path(index, ordinal)
        _fsync_write_bytes(tmp, _canonical_bytes(payload))
        os.replace(tmp, dest)

        failure_class = payload.get("failure_class") if isinstance(payload, dict) else None
        self.append_event(
            "attempt",
            trial_index=index,
            attempt_ordinal=ordinal,
            failure_class=failure_class,
        )
        return ordinal

    # -- trials (immutable raw scoreable evidence) --------------------------

    def _trial_path(self, index: int) -> Path:
        return self.run_dir / self.TRIALS_DIR / trial_filename(index)

    def _trial_tmp_path(self, index: int) -> Path:
        return self.run_dir / self.TRIALS_DIR / f".trial-{index:03d}.json.tmp"

    def commit_trial(self, index: int, payload: dict) -> None:
        """Atomically commit raw scoreable evidence for trial `index`.

        Writes to a hidden temp file, flushes and fsyncs it, refuses an
        already-committed destination, then `os.replace`s it into place. If
        `os.replace` raises (simulating a crash at the atomicity boundary),
        no completed trial is left behind and any previously recorded
        attempts for this index are untouched.
        """
        dest = self._trial_path(index)
        if dest.exists():
            raise TrialExistsError(f"trial {index} is already committed at {dest}")

        tmp = self._trial_tmp_path(index)
        _fsync_write_bytes(tmp, _canonical_bytes(payload))

        if dest.exists():
            # Lost a race with another writer between the existence check
            # above and finishing the temp write.
            tmp.unlink(missing_ok=True)
            raise TrialExistsError(f"trial {index} is already committed at {dest}")

        os.replace(tmp, dest)

        self.append_event(
            "trial_committed",
            trial_index=index,
            attempt_ordinal=self.attempt_count(index) + 1,
            failure_class=None,
        )

    def trial(self, index: int) -> dict:
        """Load a committed raw trial. Raises FileNotFoundError if `index`
        has not been committed."""
        path = self._trial_path(index)
        return json.loads(path.read_text(encoding="utf-8"))

    def is_trial_complete(self, index: int) -> bool:
        """True only when trial `index` is committed AND its stamped
        provenance proves it belongs to this store's lock.

        `False` means "never committed" -- an ordinary, expected state for
        an unscheduled or not-yet-run trial. Anything else (a committed
        file that is corrupt, unstamped, or stamped for a different lock)
        raises `TrialProvenanceError` instead of returning `False`: treating
        bad provenance the same as "never ran" would let a resuming runner
        silently re-execute (double-counting an episode) or -- if the
        stamps happen to collide -- silently accept evidence that never
        actually ran under this lock.

        For a counted campaign block (this store's lock carries a non-empty
        `campaign_fingerprint`), BOTH the `session_fingerprint` and the
        `campaign_fingerprint` stamps on the committed trial must be present
        and match; a single-stamped file (e.g. carrying only
        `session_fingerprint`) is exactly the corrupt/incomplete-provenance
        case this method must fail closed on, not silently skip.
        """
        if index not in self.completed_indices():
            return False

        try:
            trial = self.trial(index)
        except (OSError, ValueError) as exc:
            raise TrialProvenanceError(
                f"trial {index} at {self._trial_path(index)} could not be read/parsed "
                f"as JSON: {exc}"
            ) from exc

        if not isinstance(trial, dict):
            raise TrialProvenanceError(
                f"trial {index} at {self._trial_path(index)} is not a JSON object "
                f"(got {type(trial).__name__}); refusing to treat unparseable "
                "evidence as complete"
            )

        stamped_session = trial.get("session_fingerprint")
        if not stamped_session or stamped_session != self.fingerprint:
            raise TrialProvenanceError(
                f"trial {index} is stamped with session_fingerprint "
                f"{stamped_session!r}, but this store's session_fingerprint is "
                f"{self.fingerprint!r}; refusing to treat a stale/copied/unstamped "
                "trial as complete"
            )

        if self.campaign_fingerprint:
            stamped_campaign = trial.get("campaign_fingerprint")
            if not stamped_campaign or stamped_campaign != self.campaign_fingerprint:
                raise TrialProvenanceError(
                    f"trial {index} is stamped with campaign_fingerprint "
                    f"{stamped_campaign!r}, but this counted block's "
                    f"campaign_fingerprint is {self.campaign_fingerprint!r}; refusing "
                    "to treat a stale/copied/single-stamped trial as complete"
                )

        return True

    def completed_indices(self) -> set[int]:
        """The set of trial indices with committed raw evidence. Incomplete
        temp files (`.trial-NNN.json.tmp`) never appear here."""
        trials_dir = self.run_dir / self.TRIALS_DIR
        if not trials_dir.is_dir():
            return set()
        indices: set[int] = set()
        for path in trials_dir.iterdir():
            if not path.is_file():
                continue
            match = _TRIAL_NAME_RE.match(path.name)
            if match:
                indices.add(int(match.group(1)))
        return indices

    def next_incomplete(self, indices: Iterable[int]) -> int | None:
        """The first index in `indices` (in order) that has no committed
        raw trial, or None if all are complete."""
        completed = self.completed_indices()
        for index in indices:
            if index not in completed:
                return index
        return None
