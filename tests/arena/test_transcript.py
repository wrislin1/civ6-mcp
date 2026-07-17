import asyncio
import json
import os

import pytest

from civ_mcp.arena.transcript import (
    NullSink,
    TranscriptSink,
    serialize_transcript_record,
)

def test_write_appends_jsonl(tmp_path):
    """Test that write() appends valid JSONL records."""
    p = tmp_path / "transcript.jsonl"
    sink = TranscriptSink(str(p))

    record1 = {"turn": 1, "action": "move"}
    record2 = {"turn": 2, "action": "build"}

    sink.write(record1)
    sink.write(record2)

    lines = p.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == record1
    assert json.loads(lines[1]) == record2


def test_public_serializer_is_byte_for_byte_sink_compatible(tmp_path):
    path = tmp_path / "transcript.jsonl"
    record = {
        "turn": 7,
        "summary": "café",
        "nested": {"z": 2, "a": [True, None]},
    }
    expected = json.dumps(record)

    assert serialize_transcript_record(record) == expected
    TranscriptSink(str(path)).write(record)
    assert path.read_bytes() == (expected + "\n").encode("utf-8")

def test_for_run_makes_dir_and_returns_sink(tmp_path):
    """Test that for_run() creates the directory and returns a sink with correct path."""
    os.chdir(str(tmp_path))

    run_id = "test_run_123"
    sink = TranscriptSink.for_run(run_id)

    # Check that the directory was created
    assert os.path.isdir(os.path.join("arena_runs", run_id))

    # Check that the sink has the correct path
    expected_path = os.path.join("arena_runs", run_id, "transcript.jsonl")
    assert sink.path == expected_path

def test_null_sink_writes_nothing(tmp_path):
    """Test that NullSink.write() does nothing."""
    os.chdir(str(tmp_path))

    null_sink = NullSink()
    null_sink.write({"turn": 1, "action": "test"})

    # No files should be created
    assert not os.path.exists("arena_runs")


# ---------------------------------------------------------------------------
# Task 7: append-only seat-0 record at the physical-file level. Reuses the
# seat-0 coordinator harness (same cross-module pattern as test_orphan_sweep /
# test_capabilities importing FakeConn from test_coordinator).
# ---------------------------------------------------------------------------

from .test_coordinator import (  # noqa: E402
    FakeGSWithConn,
    Seat0CapsConn,
    Seat0Harness,
    Seat0ScriptPolicy,
    _blocker,
    _returned,
    _seat0_cfg,
    seat0_poll,
)


@pytest.mark.asyncio
async def test_seat0_interrupted_record_written_once_to_file(monkeypatch, tmp_path):
    """A seat-0 turn interrupted mid-drain appends exactly one physical JSONL
    record (terminal_state interrupted) through the real TranscriptSink."""
    from civ_mcp.arena.coordinator import run_arena

    harness = Seat0Harness(
        monkeypatch, [seat0_poll(7, active=True), seat0_poll(7, active=True)]
    )
    harness.blocker_queue = []
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    path = tmp_path / "transcript.jsonl"
    sink = TranscriptSink(str(path))
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-file-int", idle_poll_limit=6)

    async def cancel_sleep(_delay):
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", cancel_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["seat0"]["terminal_state"] == "interrupted"
    assert rec["player_id"] == 0 and rec["turn"] == 7


@pytest.mark.asyncio
async def test_seat0_human_pending_record_not_duplicated_in_file(monkeypatch, tmp_path):
    """A human_pending seat-0 turn writes exactly one physical record; a later
    cancel does not append a duplicate interrupted line."""
    from civ_mcp.arena.coordinator import run_arena

    harness = Seat0Harness(
        monkeypatch, [seat0_poll(7, active=True), seat0_poll(7, active=True)]
    )
    harness.blocker_queue = [[_blocker("UNKNOWN", "??")], [_blocker("UNKNOWN", "??")]]
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    path = tmp_path / "transcript.jsonl"
    sink = TranscriptSink(str(path))
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-file-hp", idle_poll_limit=6)

    async def cancel_sleep(_delay):
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", cancel_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["seat0"]["terminal_state"] == "human_pending"
