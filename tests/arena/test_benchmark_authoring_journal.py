"""Durable authoring attempt journal: clock, restart, substitution, ownership.

Every clock is injected -- no test waits in real time.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from civ_mcp.arena import benchmark_authoring_journal as journal_mod
from civ_mcp.arena.benchmark_authoring_journal import (
    AUTHORING_LIMIT_S,
    AuthoringJournal,
    elapsed_authoring_seconds,
)


class FakeClock:
    def __init__(self, wall: float = 1_000_000.0, mono: float = 50.0) -> None:
        self.wall = wall
        self.mono = mono

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds

    def wall_clock(self) -> float:
        return self.wall

    def monotonic(self) -> float:
        return self.mono


def _open(path: Path, clock: FakeClock) -> AuthoringJournal:
    return AuthoringJournal(path, wall_clock=clock.wall_clock, monotonic=clock.monotonic)


def _begin_first(journal: AuthoringJournal, family: str = "builder", sid: str = "builder-a1"):
    return journal.begin(
        family=family, scenario_id=sid, predecessor=None, reason=None, material_change=None
    )


def _begin_substitute(journal: AuthoringJournal, **overrides):
    kwargs = dict(
        family="builder",
        scenario_id="builder-a2",
        predecessor="builder-a1",
        reason="different geometry",
        material_change="move threat to an open route",
    )
    kwargs.update(overrides)
    return journal.begin(**kwargs)


# --- brief test, verbatim -------------------------------------------------


def test_substitution_requires_a_failed_predecessor(tmp_path):
    journal = AuthoringJournal(tmp_path / "attempts.json")
    journal.begin(family="builder", scenario_id="builder-a1", predecessor=None,
                  reason=None, material_change=None)
    with pytest.raises(ValueError, match="failed predecessor"):
        journal.begin(family="builder", scenario_id="builder-a2", predecessor="builder-a1",
                      reason="different geometry", material_change="move threat to an open route")


# --- clock helper ---------------------------------------------------------


def test_elapsed_helper_never_shortens_and_rejects_backwards_time():
    assert elapsed_authoring_seconds(100.0, 160.0, 30.0) == 60.0
    assert elapsed_authoring_seconds(100.0, 140.0, 40.0) == 40.0
    with pytest.raises(ValueError, match="moved backwards"):
        elapsed_authoring_seconds(100.0, 139.0, 40.0)


# --- clock persistence and restart ----------------------------------------


def test_begin_persists_utc_start_and_schema(tmp_path):
    clock = FakeClock()
    path = tmp_path / "attempts.json"
    with _open(path, clock) as journal:
        record = _begin_first(journal)

    assert record["attempt"] == 1
    assert record["status"] == "open"
    assert record["started_unix_s"] == clock.wall
    doc = json.loads(path.read_text())
    assert doc["schema_version"] == "2.0.0"
    stored = doc["families"]["builder"]["scenarios"][0]
    assert stored["scenario_id"] == "builder-a1"
    assert stored["family"] == "builder"
    assert stored["predecessor"] is None
    assert stored["recorded_elapsed_s"] == 0.0
    assert stored["finished_unix_s"] is None


def test_process_restart_does_not_reset_the_scenario_clock(tmp_path):
    clock = FakeClock()
    path = tmp_path / "attempts.json"
    with _open(path, clock) as journal:
        _begin_first(journal)
        clock.advance(600)
        journal.record_stage(scenario_id="builder-a1", stage="survey", evidence={"k": 1})

    # Process dies; ten minutes pass before the next process starts.
    clock.advance(600)
    clock.mono = 3.0  # a new process has an unrelated monotonic origin
    with _open(path, clock) as journal:
        record = _begin_first(journal)
        assert record["started_unix_s"] == 1_000_000.0
        assert record["recorded_elapsed_s"] == 1200.0
        assert journal.remaining_seconds("builder-a1") == AUTHORING_LIMIT_S - 1200.0
        clock.advance(100)
        journal.record_stage(scenario_id="builder-a1", stage="reload", evidence={})
        stages = journal.record("builder-a1")["stages"]

    assert [s["stage"] for s in stages] == ["survey", "reload"]
    assert stages[0]["elapsed_s"] == 600.0
    assert stages[1]["elapsed_s"] == 700.0
    assert stages[1]["started_unix_s"] == stages[0]["ended_unix_s"]


def test_same_process_monotonic_progress_counts_even_if_wall_clock_lags(tmp_path):
    clock = FakeClock()
    with _open(tmp_path / "attempts.json", clock) as journal:
        _begin_first(journal)
        clock.mono += 500  # wall clock frozen (e.g. NTP slew) -- monotonic still counts
        elapsed = journal.checkpoint(scenario_id="builder-a1", label="debugging")
        assert elapsed == 500.0
        clock.wall += 100  # wall now behind recorded elapsed, but not behind its own past
        assert journal.checkpoint(scenario_id="builder-a1") == 500.0
        assert len(journal.record("builder-a1")["checkpoints"]) == 3


def test_backward_wall_clock_is_rejected_not_shortened(tmp_path):
    clock = FakeClock()
    path = tmp_path / "attempts.json"
    with _open(path, clock) as journal:
        _begin_first(journal)
        clock.advance(1000)
        journal.checkpoint(scenario_id="builder-a1")
        clock.wall -= 500
        with pytest.raises(ValueError, match="moved backwards"):
            journal.record_stage(scenario_id="builder-a1", stage="survey", evidence={})
        assert journal.record("builder-a1")["recorded_elapsed_s"] == 1000.0

    stored = json.loads(path.read_text())["families"]["builder"]["scenarios"][0]
    assert stored["recorded_elapsed_s"] == 1000.0
    assert stored["stages"] == []


def test_clock_expires_at_limit_and_expiry_survives_reopen(tmp_path):
    clock = FakeClock()
    path = tmp_path / "attempts.json"
    with _open(path, clock) as journal:
        _begin_first(journal)
        clock.advance(AUTHORING_LIMIT_S)
        journal.checkpoint(scenario_id="builder-a1")  # exactly at the limit is allowed
        assert journal.remaining_seconds("builder-a1") == 0.0
        assert journal.is_expired("builder-a1") is False
        clock.advance(1)
        with pytest.raises(ValueError, match="authoring clock expired"):
            journal.record_stage(scenario_id="builder-a1", stage="late", evidence={"x": 1})
        assert journal.is_expired("builder-a1") is True

    stored = json.loads(path.read_text())["families"]["builder"]["scenarios"][0]
    assert stored["expired"] is True
    assert stored["status"] == "open"
    # Stage evidence from the expired call is retained, not discarded.
    assert stored["stages"][-1]["stage"] == "late"

    clock.mono = 0.0
    with _open(path, clock) as journal:
        assert journal.is_expired("builder-a1") is True
        assert journal.remaining_seconds("builder-a1") == 0.0
        with pytest.raises(ValueError, match="authoring clock expired"):
            journal.finish(scenario_id="builder-a1", passed=True)
        record = journal.record("builder-a1")
        assert record["status"] == "failed"
        assert record["finished_unix_s"] == clock.wall


def test_resuming_an_expired_clock_raises_and_persists_expiry(tmp_path):
    clock = FakeClock()
    path = tmp_path / "attempts.json"
    with _open(path, clock) as journal:
        _begin_first(journal)
        clock.advance(600)
        journal.checkpoint(scenario_id="builder-a1")

    clock.advance(AUTHORING_LIMIT_S)  # process was down past the limit
    clock.mono = 0.0
    with _open(path, clock) as journal:
        with pytest.raises(ValueError, match="authoring clock expired"):
            _begin_first(journal)
        assert journal.is_expired("builder-a1") is True

    stored = json.loads(path.read_text())["families"]["builder"]["scenarios"][0]
    assert stored["expired"] is True
    assert stored["status"] == "open"
    assert stored["recorded_elapsed_s"] == 600.0 + AUTHORING_LIMIT_S
    assert stored["checkpoints"][-1]["label"] == "resume"


def test_finish_stops_the_clock(tmp_path):
    clock = FakeClock()
    with _open(tmp_path / "attempts.json", clock) as journal:
        _begin_first(journal)
        clock.advance(300)
        record = journal.finish(scenario_id="builder-a1", passed=True)
        assert record["status"] == "passed"
        clock.advance(AUTHORING_LIMIT_S * 2)
        assert journal.is_expired("builder-a1") is False
        assert journal.record("builder-a1")["recorded_elapsed_s"] == 300.0
        with pytest.raises(ValueError, match="finished"):
            journal.record_stage(scenario_id="builder-a1", stage="extra", evidence={})


# --- substitution rules ---------------------------------------------------


def test_rename_without_predecessor_is_rejected_as_substitution(tmp_path):
    clock = FakeClock()
    with _open(tmp_path / "attempts.json", clock) as journal:
        _begin_first(journal)
        with pytest.raises(ValueError, match="failed predecessor"):
            _begin_first(journal, sid="builder-renamed")
        journal.finish(scenario_id="builder-a1", passed=True)
        with pytest.raises(ValueError, match="failed predecessor"):
            _begin_first(journal, sid="builder-renamed")
        journal_doc = journal.record("builder-a1")
        assert journal_doc["status"] == "passed"


def test_exactly_one_declared_material_substitute_is_accepted_after_failure(tmp_path):
    clock = FakeClock()
    with _open(tmp_path / "attempts.json", clock) as journal:
        _begin_first(journal)
        journal.finish(scenario_id="builder-a1", passed=False)

        with pytest.raises(ValueError, match="reason"):
            _begin_substitute(journal, reason="")
        with pytest.raises(ValueError, match="material_change"):
            _begin_substitute(journal, material_change=None)
        with pytest.raises(ValueError, match="failed predecessor"):
            _begin_substitute(journal, predecessor="nonexistent")
        with pytest.raises(ValueError, match="failed predecessor"):
            _begin_substitute(journal, predecessor=None)

        record = _begin_substitute(journal)
        assert record["attempt"] == 2
        assert record["predecessor"] == "builder-a1"
        assert record["reason"] == "different geometry"
        assert record["material_change"] == "move threat to an open route"
        # Repeating the identical identity returns the same record.
        assert _begin_substitute(journal)["started_unix_s"] == record["started_unix_s"]


def test_second_failure_blocks_the_family(tmp_path):
    clock = FakeClock()
    with _open(tmp_path / "attempts.json", clock) as journal:
        _begin_first(journal)
        journal.finish(scenario_id="builder-a1", passed=False)
        _begin_substitute(journal)
        journal.finish(scenario_id="builder-a2", passed=False)
        with pytest.raises(ValueError, match="blocked"):
            _begin_substitute(journal, scenario_id="builder-a3", predecessor="builder-a2")
        # Failed attempts are retained, not removed.
        assert journal.record("builder-a1")["status"] == "failed"
        assert journal.record("builder-a2")["status"] == "failed"


def test_substitute_after_open_attempt_one_with_other_family_unaffected(tmp_path):
    clock = FakeClock()
    with _open(tmp_path / "attempts.json", clock) as journal:
        _begin_first(journal)
        other = _begin_first(journal, family="combat", sid="combat-a1")
        assert other["attempt"] == 1
        with pytest.raises(ValueError):
            _begin_first(journal, family="combat", sid="builder-a1")


def test_family_total_includes_both_attempts(tmp_path):
    clock = FakeClock()
    path = tmp_path / "attempts.json"
    with _open(path, clock) as journal:
        _begin_first(journal)
        clock.advance(4000)
        journal.finish(scenario_id="builder-a1", passed=False)
        clock.advance(50)  # gap between attempts is not on either clock
        _begin_substitute(journal)
        clock.advance(2500)
        journal.finish(scenario_id="builder-a2", passed=True)
        assert journal.family_total_seconds("builder") == 6500.0

    doc = json.loads(path.read_text())
    assert doc["families"]["builder"]["total_elapsed_s"] == 6500.0


# --- durability and ownership ---------------------------------------------


def test_writes_are_atomic(tmp_path, monkeypatch):
    clock = FakeClock()
    path = tmp_path / "attempts.json"
    with _open(path, clock) as journal:
        _begin_first(journal)
        before = path.read_bytes()

        def failing_replace(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(journal_mod.os, "replace", failing_replace)
        clock.advance(10)
        with pytest.raises(OSError, match="disk full"):
            journal.record_stage(scenario_id="builder-a1", stage="survey", evidence={})
        assert path.read_bytes() == before
        assert journal.record("builder-a1")["stages"] == []
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


def test_live_lock_owner_blocks_a_second_journal(tmp_path):
    path = tmp_path / "attempts.json"
    journal = AuthoringJournal(path)
    lock = Path(f"{path}.lock")
    assert lock.read_text().strip() == str(os.getpid())
    with pytest.raises(RuntimeError, match="locked"):
        AuthoringJournal(path)
    journal.close()
    assert not lock.exists()
    AuthoringJournal(path).close()


def test_stale_lock_from_dead_pid_is_reclaimed(tmp_path, monkeypatch):
    path = tmp_path / "attempts.json"
    lock = Path(f"{path}.lock")
    lock.write_text("999999999\n")
    monkeypatch.setattr(journal_mod, "_pid_alive", lambda pid: False)
    with AuthoringJournal(path):
        assert lock.read_text().strip() == str(os.getpid())
    assert not lock.exists()


def test_unreadable_lock_is_not_reclaimed(tmp_path):
    path = tmp_path / "attempts.json"
    Path(f"{path}.lock").write_text("")
    with pytest.raises(RuntimeError, match="locked"):
        AuthoringJournal(path)
