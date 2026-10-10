"""Durable authoring attempt journal: clock, restart, substitution, ownership.

Every clock is injected -- no test waits in real time.
"""

from __future__ import annotations

import hashlib
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
    path.parent.mkdir(parents=True, exist_ok=True)
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


def test_elapsed_helper_never_shortens_and_tolerates_backwards_time():
    assert elapsed_authoring_seconds(100.0, 160.0, 30.0) == 60.0
    assert elapsed_authoring_seconds(100.0, 140.0, 40.0) == 40.0
    # A backwards wall clock keeps the recorded elapsed; nothing raises.
    assert elapsed_authoring_seconds(100.0, 139.0, 40.0) == 40.0
    assert journal_mod.clock_regression_seconds(100.0, 139.0, 40.0) == 1.0
    assert journal_mod.clock_regression_seconds(100.0, 140.0, 40.0) == 0.0


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


def test_backward_wall_clock_is_journaled_not_shortened_and_never_strands(tmp_path):
    """A wall clock stepping back (NTP, WSL clock jump) must neither shorten the
    budget nor make the attempt impossible to checkpoint, finish or abandon."""
    clock = FakeClock()
    path = tmp_path / "attempts.json"
    with _open(path, clock) as journal:
        _begin_first(journal)
        clock.advance(1000)
        journal.checkpoint(scenario_id="builder-a1")
        clock.wall -= 500
        journal.record_stage(scenario_id="builder-a1", stage="survey", evidence={})
        record = journal.record("builder-a1")
        assert record["recorded_elapsed_s"] == 1000.0  # never shortened
        assert record["checkpoints"][-1]["clock_backwards_s"] == 500.0
        assert record["stages"][0]["elapsed_s"] == 1000.0
        clock.wall -= 100_000  # a large jump: still finishable
        finished = journal.finish(scenario_id="builder-a1", passed=True)
        assert finished["status"] == "passed"
        assert finished["checkpoints"][-1]["clock_backwards_s"] == 100_500.0

    stored = json.loads(path.read_text())["families"]["builder"]["scenarios"][0]
    assert stored["recorded_elapsed_s"] == 1000.0
    assert stored["status"] == "passed"
    assert "clock_backwards_s" not in stored["checkpoints"][0]


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


# --- substitution across attempt directories --------------------------------


def _failed_attempt_one(path: Path, clock: FakeClock, seconds: float = 4000) -> Path:
    with _open(path, clock) as journal:
        _begin_first(journal)
        clock.advance(seconds)
        journal.finish(scenario_id="builder-a1", passed=False)
    return path


def test_substitute_in_fresh_dir_with_predecessor_journal_passes(tmp_path):
    clock = FakeClock()
    first = _failed_attempt_one(tmp_path / "builder-a1" / "journal.json", clock)
    clock.advance(50)
    fresh = tmp_path / "builder-a2" / "journal.json"
    with _open(fresh, clock) as journal:
        record = _begin_substitute(journal, predecessor_journal=first,
                                   predecessor_journal_ref="runs/builder-a1/journal.json",
                                   sibling_journals=[first])
        assert record["attempt"] == 2
        imported = journal.record("builder-a1")
        assert imported["status"] == "failed"
        assert imported["imported_from"] == {
            "journal": "runs/builder-a1/journal.json",
            "sha256": __import__("hashlib").sha256(first.read_bytes()).hexdigest()}
        clock.advance(2500)
        journal.finish(scenario_id="builder-a2", passed=True)
        # Family total sums the imported predecessor and the substitute.
        assert journal.family_total_seconds("builder") == 6500.0


def test_substitute_in_fresh_dir_without_predecessor_journal_fails(tmp_path):
    clock = FakeClock()
    first = _failed_attempt_one(tmp_path / "builder-a1" / "journal.json", clock)
    with _open(tmp_path / "builder-a2" / "journal.json", clock) as journal:
        with pytest.raises(ValueError, match="failed predecessor"):
            _begin_substitute(journal, sibling_journals=[first])


def test_predecessor_journal_must_hold_a_terminal_failed_predecessor(tmp_path):
    clock = FakeClock()
    first = tmp_path / "builder-a1" / "journal.json"
    with _open(first, clock) as journal:
        _begin_first(journal)  # still open
    with _open(tmp_path / "builder-a2" / "journal.json", clock) as journal:
        with pytest.raises(ValueError, match="not terminal-failed"):
            _begin_substitute(journal, predecessor_journal=first)


def test_third_identity_across_attempt_dirs_is_blocked(tmp_path):
    clock = FakeClock()
    first = _failed_attempt_one(tmp_path / "builder-a1" / "journal.json", clock)
    second = tmp_path / "builder-a2" / "journal.json"
    with _open(second, clock) as journal:
        _begin_substitute(journal, predecessor_journal=first, sibling_journals=[first])
        journal.finish(scenario_id="builder-a2", passed=False)
    with _open(tmp_path / "builder-a3" / "journal.json", clock) as journal:
        with pytest.raises(ValueError, match="blocked"):
            _begin_substitute(journal, scenario_id="builder-a3", predecessor_journal=first,
                              sibling_journals=[first, second])
        # Nor can a fresh "attempt 1" identity sidestep the budget.
        with pytest.raises(ValueError, match="blocked"):
            journal.begin(family="builder", scenario_id="builder-b1", predecessor=None,
                          reason=None, material_change=None,
                          sibling_journals=[first, second])
    with _open(tmp_path / "builder-b1" / "journal.json", clock) as journal:
        with pytest.raises(ValueError, match="already has attempt 1"):
            journal.begin(family="builder", scenario_id="builder-b1", predecessor=None,
                          reason=None, material_change=None, sibling_journals=[first])


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


# --- revalidation (amendment 2026-10-10, decision 2) -----------------------


def _passed_substitute(tmp_path, clock):
    """Attempt 1 failed in builder-a1; the substitute builder-a2 passed in builder-a2."""
    first = _failed_attempt_one(tmp_path / "builder-a1" / "journal.json", clock)
    second = tmp_path / "builder-a2" / "journal.json"
    with _open(second, clock) as journal:
        _begin_substitute(journal, predecessor_journal=first, sibling_journals=[first])
        clock.advance(3000)
        journal.finish(scenario_id="builder-a2", passed=True)
    return first, second


def test_revalidation_attempt_is_admitted_for_the_passed_sibling_scenario(tmp_path):
    clock = FakeClock()
    first, second = _passed_substitute(tmp_path, clock)
    reval = tmp_path / "builder-a2-reval" / "journal.json"
    with _open(reval, clock) as journal:
        record = _begin_substitute(journal, predecessor_journal=first,
                                   sibling_journals=[first, second],
                                   revalidates=second, revalidates_ref="builder-a2/journal.json")
    assert record["attempt"] == 2 and record["status"] == "open"
    assert record["revalidates"] == {
        "journal": "builder-a2/journal.json",
        "sha256": hashlib.sha256(second.read_bytes()).hexdigest()}
    doc = json.loads(reval.read_text())
    imported, own = doc["families"]["builder"]["scenarios"]
    assert imported["scenario_id"] == "builder-a1" and imported["imported_from"]
    assert own["scenario_id"] == "builder-a2" and own["revalidates"]
    # A new clock: nothing of the revalidated attempt's elapsed time carries over.
    assert own["recorded_elapsed_s"] == 0.0
    # The revalidated journal is untouched.
    assert json.loads(second.read_text())["families"]["builder"]["scenarios"][1]["status"] \
        == "passed"


def test_revalidation_requires_the_named_journal_to_hold_the_scenario_as_passed(tmp_path):
    clock = FakeClock()
    first = _failed_attempt_one(tmp_path / "builder-a1" / "journal.json", clock)
    second = tmp_path / "builder-a2" / "journal.json"
    with _open(second, clock) as journal:
        _begin_substitute(journal, predecessor_journal=first, sibling_journals=[first])
        journal.finish(scenario_id="builder-a2", passed=False)
    with _open(tmp_path / "builder-a2-reval" / "journal.json", clock) as journal:
        with pytest.raises(ValueError, match="revalidation of 'builder-a2'.*not passed"):
            _begin_substitute(journal, predecessor_journal=first,
                              sibling_journals=[first, second], revalidates=second)
        with pytest.raises(ValueError, match="revalidation of 'builder-a2'.*no such scenario"):
            _begin_substitute(journal, predecessor_journal=first,
                              sibling_journals=[first, second], revalidates=first)
        with pytest.raises(ValueError, match="does not exist"):
            _begin_substitute(journal, predecessor_journal=first,
                              sibling_journals=[first, second],
                              revalidates=tmp_path / "nowhere.json")


def test_revalidation_waives_only_the_named_sibling_and_only_once(tmp_path):
    clock = FakeClock()
    first, second = _passed_substitute(tmp_path, clock)
    # Undeclared, the same scenario is still refused.
    with _open(tmp_path / "builder-a2-again" / "journal.json", clock) as journal:
        with pytest.raises(ValueError, match="already journaled"):
            _begin_substitute(journal, predecessor_journal=first,
                              sibling_journals=[first, second])
    # The named journal must be the sibling that holds the scenario.
    stray = tmp_path / "elsewhere" / "journal.json"
    stray.parent.mkdir()
    stray.write_bytes(second.read_bytes())
    with _open(tmp_path / "builder-a2-stray" / "journal.json", clock) as journal:
        with pytest.raises(ValueError, match="not the sibling attempt journal"):
            _begin_substitute(journal, predecessor_journal=first,
                              sibling_journals=[first, second], revalidates=stray)
    # One revalidation attempt is admitted; a second finds the scenario journaled twice.
    reval = tmp_path / "builder-a2-reval" / "journal.json"
    with _open(reval, clock) as journal:
        _begin_substitute(journal, predecessor_journal=first, sibling_journals=[first, second],
                          revalidates=second)
        journal.finish(scenario_id="builder-a2", passed=True)
    with _open(tmp_path / "builder-a2-reval-2" / "journal.json", clock) as journal:
        with pytest.raises(ValueError, match="already journaled"):
            _begin_substitute(journal, predecessor_journal=first,
                              sibling_journals=[first, second, reval], revalidates=reval)
    # Nor does a declaration admit a different identity.
    with _open(tmp_path / "builder-a3" / "journal.json", clock) as journal:
        with pytest.raises(ValueError, match="revalidation of 'builder-a3'.*no such scenario"):
            _begin_substitute(journal, scenario_id="builder-a3", predecessor_journal=first,
                              sibling_journals=[first, second], revalidates=second)
