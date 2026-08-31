import json
import os

import pytest

from civ_mcp.arena.benchmark_store import (
    BenchmarkStore,
    SessionLockMismatchError,
    TrialExistsError,
)


def _lock() -> dict:
    return {"session_fingerprint": "abc123", "schedule_fingerprint": "def456"}


def test_scoreable_raw_trial_prevents_reexecution_even_without_report(tmp_path):
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)
    store.commit_trial(1, {"session_fingerprint": store.fingerprint, "terminal": "step_limit"})

    reopened = BenchmarkStore.open(tmp_path / "run", lock)

    assert reopened.completed_indices() == {1}
    assert reopened.next_incomplete([1, 2, 3]) == 2
    with pytest.raises(TrialExistsError):
        reopened.commit_trial(1, {"session_fingerprint": store.fingerprint})


def test_create_exposes_session_fingerprint_from_lock(tmp_path):
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)
    assert store.fingerprint == "abc123"


def test_open_refuses_a_mismatched_lock(tmp_path):
    lock = _lock()
    BenchmarkStore.create(tmp_path / "run", lock)

    mismatched = dict(lock)
    mismatched["schedule_fingerprint"] = "different"

    with pytest.raises(SessionLockMismatchError):
        BenchmarkStore.open(tmp_path / "run", mismatched)


def test_session_lock_is_canonical_json_matched_byte_for_byte(tmp_path):
    lock = {"b": 2, "a": 1, "session_fingerprint": "abc123", "schedule_fingerprint": "def456"}
    BenchmarkStore.create(tmp_path / "run", lock)

    on_disk = (tmp_path / "run" / "session.json").read_bytes()
    assert on_disk == json.dumps(lock, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    # Key order in the caller-supplied lock must not matter on reopen.
    reordered = {"schedule_fingerprint": "def456", "session_fingerprint": "abc123", "a": 1, "b": 2}
    reopened = BenchmarkStore.open(tmp_path / "run", reordered)
    assert reopened.fingerprint == "abc123"


def test_incomplete_temp_files_are_never_counted_as_trials(tmp_path):
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)
    store.commit_trial(1, {"session_fingerprint": store.fingerprint})

    # Simulate a crash mid-write: a stray tmp file for an index that never
    # actually landed a committed trial.
    stray = tmp_path / "run" / "trials" / ".trial-002.json.tmp"
    stray.write_text('{"session_fingerprint": "abc123"}', encoding="utf-8")

    assert store.completed_indices() == {1}
    assert store.next_incomplete([1, 2, 3]) == 2


def test_attempts_never_count_as_trials_and_attempt_counts_survive_resume(tmp_path):
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)

    store.record_attempt(2, {"trial_index": 2, "failure_class": "reload_failed"})
    store.record_attempt(2, {"trial_index": 2, "failure_class": "popup_hygiene_failed"})
    store.commit_trial(1, {"session_fingerprint": store.fingerprint})

    assert store.completed_indices() == {1}
    assert store.next_incomplete([1, 2, 3]) == 2
    assert store.attempt_count(2) == 2

    reopened = BenchmarkStore.open(tmp_path / "run", lock)
    assert reopened.completed_indices() == {1}
    assert reopened.attempt_count(2) == 2
    assert reopened.attempt_count(1) == 0


def test_trial_accessor_loads_committed_raw_evidence(tmp_path):
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)
    payload = {"session_fingerprint": store.fingerprint, "terminal": "finish_trial", "steps": []}
    store.commit_trial(3, payload)

    loaded = store.trial(3)
    assert loaded == payload


def test_journal_events_carry_sequence_timestamp_and_failure_class(tmp_path):
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)

    store.record_attempt(5, {"trial_index": 5, "failure_class": "gateway_unavailable"})
    store.commit_trial(5, {"session_fingerprint": store.fingerprint})

    lines = (tmp_path / "run" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]

    attempt_record, commit_record = records
    assert attempt_record["seq"] == 0
    assert commit_record["seq"] == 1
    assert attempt_record["trial_index"] == 5
    assert attempt_record["attempt_ordinal"] == 1
    assert attempt_record["event"] == "attempt"
    assert attempt_record["failure_class"] == "gateway_unavailable"
    assert isinstance(attempt_record["ts"], str) and attempt_record["ts"]

    assert commit_record["trial_index"] == 5
    assert commit_record["event"] == "trial_committed"
    assert commit_record["failure_class"] is None


def test_journal_sequence_continues_monotonically_across_resume(tmp_path):
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)
    store.record_attempt(1, {"trial_index": 1, "failure_class": "reload_failed"})

    reopened = BenchmarkStore.open(tmp_path / "run", lock)
    reopened.record_attempt(1, {"trial_index": 1, "failure_class": "reload_failed"})

    lines = (tmp_path / "run" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == [0, 1]


def test_commit_trial_crash_before_replace_leaves_no_completed_trial_but_keeps_attempt(tmp_path, monkeypatch):
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)

    store.record_attempt(1, {"trial_index": 1, "failure_class": "reload_failed"})

    def _boom(_src, _dst):
        raise OSError("simulated crash before atomic replace")

    monkeypatch.setattr(os, "replace", _boom)

    with pytest.raises(OSError):
        store.commit_trial(1, {"session_fingerprint": store.fingerprint})

    monkeypatch.undo()

    reopened = BenchmarkStore.open(tmp_path / "run", lock)
    assert reopened.completed_indices() == set()
    assert reopened.attempt_count(1) == 1


def test_record_attempt_returns_the_assigned_ordinal(tmp_path):
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)

    first = store.record_attempt(4, {"trial_index": 4, "failure_class": "reload_failed"})
    second = store.record_attempt(4, {"trial_index": 4, "failure_class": "popup_hygiene_failed"})

    assert first == 1
    assert second == 2


def test_completed_indices_sees_a_four_digit_trial_index(tmp_path):
    r"""F14 repro: the trial filename regex was `trial-(\d{3})\.json` while
    `_trial_path` formats with `{index:03d}` (a MINIMUM width, not a fixed
    one) -- index 1000 writes "trial-1000.json" (4 digits), which the old
    regex's exact-3-digit match silently ignores. completed_indices() and
    attempt_count() must see index 1000, not just indices 1-999."""
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)

    store.commit_trial(1000, {"session_fingerprint": store.fingerprint})

    assert store.completed_indices() == {1000}
    assert store.trial(1000) == {"session_fingerprint": store.fingerprint}


def test_attempt_count_sees_a_four_digit_trial_index(tmp_path):
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)

    store.record_attempt(1000, {"trial_index": 1000, "failure_class": "reload_failed"})
    store.record_attempt(1000, {"trial_index": 1000, "failure_class": "reload_failed"})

    assert store.attempt_count(1000) == 2


def test_commit_trial_at_index_1000_is_not_re_executed_or_overwritten(tmp_path):
    """The attempt cap depends on attempt_count() actually counting attempts
    for high indices -- if the regex silently ignores them, TrialExistsError
    never fires either way (re-execution just always "succeeds" against a
    destination the code thinks doesn't exist -- until it collides)."""
    lock = _lock()
    store = BenchmarkStore.create(tmp_path / "run", lock)
    store.commit_trial(1000, {"session_fingerprint": store.fingerprint, "terminal": "a"})

    with pytest.raises(TrialExistsError):
        store.commit_trial(1000, {"session_fingerprint": store.fingerprint, "terminal": "b"})
