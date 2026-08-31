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

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

__all__ = [
    "BenchmarkStore",
    "BenchmarkStoreError",
    "SessionLockMismatchError",
    "TrialExistsError",
]


class BenchmarkStoreError(Exception):
    """Base class for BenchmarkStore errors."""


class SessionLockMismatchError(BenchmarkStoreError):
    """Raised when a provided lock does not match the run's session.json
    byte-for-byte (canonical JSON) on reopen."""


class TrialExistsError(BenchmarkStoreError):
    """Raised when `commit_trial` targets an index that already has
    committed raw evidence. A committed trial is never re-executed."""


# `{index:03d}` (used by _trial_path/_attempt_path below) is a MINIMUM
# width, not a fixed one -- index 1000 writes "trial-1000.json" (4 digits).
# `\d{3,}` (three-or-more) matches that; an exact `\d{3}` would silently
# ignore any index >= 1000, making completed_indices()/attempt_count() miss
# it (re-execution, then a TrialExistsError crash; the attempt cap disabled).
_TRIAL_NAME_RE = re.compile(r"^trial-(\d{3,})\.json$")
_ATTEMPT_NAME_RE = re.compile(r"^trial-(\d{3,})-attempt-(\d{3,})\.json$")


def _canonical_bytes(value: object) -> bytes:
    """Canonical-JSON encoding: sorted keys, no incidental whitespace. Two
    dicts that are equal encode to identical bytes regardless of the order
    their keys were supplied in."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return encoded.encode("utf-8")


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
        **details: object,
    ) -> dict:
        """Append one event to journal.jsonl. Sequence numbers are monotonic
        and continue across process resume (derived from the journal already
        on disk, never reset to zero on reopen)."""
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
            record["details"] = details

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
        return self.run_dir / self.TRIALS_DIR / f"trial-{index:03d}.json"

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
