import pytest
import asyncio
from civ_mcp import lua as lq
from civ_mcp.arena import autoresolve
from civ_mcp.arena import coordinator as coordinator_mod
from civ_mcp.arena import hook as hook_mod
from civ_mcp.arena import seat0 as seat0_mod
from civ_mcp.arena.coordinator import run_arena, ScriptedPolicy, _reconnect_with_retry
from civ_mcp.arena.hook import PuppetState
from civ_mcp.arena.config import (
    ArenaConfig,
    BriefingOptions,
    CivOptions,
    MemoryOptions,
    PlayerSpec,
    TaskTrackerOptions,
)
from civ_mcp.arena.memory import memory_path, save_memory
from civ_mcp.arena.task_tracker import UnitTask, save_task_state, task_path

class FakeConn:
    """Serves canned GameCore reads by matching key substrings in the Lua.

    Models a REAL socket: when disconnected it raises on execute_* (a dead FireTuner
    socket cannot serve reads). This is what makes the human-safety tests honest — a
    permanently-dead connection genuinely cannot restore the human, and the tests must
    observe that rather than pass on canned reads served over a dead socket.
    """
    def __init__(self):
        self.restored = False
        self._connected = True
        self._dead_when_disconnected = True   # behave like a real socket
        self.read_calls = []                  # every lua passed to execute_read (even if it raises)
        self.write_calls = []                 # every lua passed to execute_write (even if it raises)
        self._polls = iter([
            ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],   # human turn
            ["LOCAL|1", "TURN|2", "ACTIVE|true", "LAST|1"],      # puppet held
        ])
    @property
    def is_connected(self): return self._connected
    async def connect(self): self._connected = True
    async def disconnect(self): self._connected = False
    def _maybe_die(self):
        if self._dead_when_disconnected and not self._connected:
            raise ConnectionError("FakeConn: socket dead while disconnected")
    async def execute_read(self, lua, timeout=5.0):
        self.read_calls.append(lua)
        self._maybe_die()
        if "GetCurrentGameTurn" in lua and "GetLocalPlayer" in lua and "ACTIVE" in lua:
            try: return next(self._polls)
            except StopIteration: return ["LOCAL|0", "TURN|2", "ACTIVE|false", "LAST|1"]
        if "SetLocalPlayerAndObserver(0)" in lua:
            self.restored = True; return ["LOCAL|0"]
        if "HOOK_OK" in lua or "__pt_registered" in lua: return ["HOOK_OK|true"]
        if "DISABLED" in lua: return ["DISABLED|true"]
        if "FINISHED" in lua: return ["FINISHED|1"]
        return []
    async def execute_write(self, lua, timeout=5.0):
        self.write_calls.append(lua)
        self._maybe_die()
        return []

class FakeGS:
    def __init__(self): self.ran = 0
    async def get_game_overview(self): return "OV"
    async def get_units(self): return []
    async def skip_unit(self, i): self.ran += 1; return "SKIP"


class _PromoUnit:
    def __init__(self, unit_id):
        self.unit_id = unit_id
        self.unit_type = "UNIT_WARRIOR"


class SweepGS(FakeGS):
    def __init__(self):
        super().__init__()
        self.promoted = []

    async def get_units(self):
        return [_PromoUnit(1)]

    async def get_unit_promotions(self, unit_id):
        return lq.UnitPromotionStatus(
            unit_id=unit_id,
            unit_index=1,
            unit_type="UNIT_WARRIOR",
            promotions=[
                lq.PromotionOption(
                    promotion_type="PROMOTION_BATTLECRY",
                    name="Battlecry",
                    description="d",
                )
            ],
        )

    async def promote_unit(self, unit_id, promotion_type):
        self.promoted.append((unit_id, promotion_type))
        return f"Promoted {unit_id}"


@pytest.mark.asyncio
async def test_coordinator_runs_one_puppet_turn_and_restores():
    conn, gs = FakeConn(), FakeGS()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])
    result = await run_arena(conn, gs, cfg, policy=ScriptedPolicy())
    assert result["puppet_turns_played"] == 1
    assert conn.restored is True
    assert gs.ran == 1


@pytest.mark.asyncio
async def test_coordinator_respects_idle_poll_limit(monkeypatch):
    async def noop(_delay): pass
    monkeypatch.setattr(asyncio, "sleep", noop)

    conn, gs = FakeConn(), FakeGS()
    conn._polls = iter([
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
    ])
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1], idle_poll_limit=2)
    result = await run_arena(conn, gs, cfg, policy=ScriptedPolicy())
    assert result["puppet_turns_played"] == 0
    poll_reads = [c for c in conn.read_calls if "GetCurrentGameTurn" in c]
    assert len(poll_reads) == 2


class FakeConnFlaky(FakeConn):
    """FakeConn where connect() raises on the first `fail_times` calls then succeeds."""
    def __init__(self, fail_times=1):
        super().__init__()
        self._fail_remaining = fail_times
        self.connect_attempts = 0

    async def connect(self):
        self.connect_attempts += 1
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise OSError("port 4318 still in use")
        await super().connect()


@pytest.mark.asyncio
async def test_reconnect_retry_succeeds_after_failures():
    """_reconnect_with_retry returns True when connect eventually succeeds."""
    conn = FakeConnFlaky(fail_times=2)
    conn._connected = False  # start disconnected
    result = await _reconnect_with_retry(conn, attempts=5, delay=0)
    assert result is True
    assert conn.is_connected is True
    assert conn.connect_attempts == 3  # 2 failures + 1 success


@pytest.mark.asyncio
async def test_reconnect_retry_all_fail():
    """_reconnect_with_retry returns False (no raise) when all attempts fail."""
    conn = FakeConnFlaky(fail_times=999)
    conn._connected = False
    result = await _reconnect_with_retry(conn, attempts=3, delay=0)
    assert result is False
    assert conn.connect_attempts == 3
    assert conn.is_connected is False


@pytest.mark.asyncio
async def test_coordinator_reclaim_retry_restores_human(monkeypatch):
    """Human is restored even when reclaim connect fails on the first attempt."""
    async def noop(_delay): pass
    monkeypatch.setattr(asyncio, "sleep", noop)

    class ExclusivePol:
        needs_exclusive_tuner = True
        async def __call__(self, gs, player_id, turn, **kwargs):
            return {"summary": "cli ran", "actions": []}

    conn = FakeConnFlaky(fail_times=1)
    gs = FakeGS()
    cfg = ArenaConfig(players=[PlayerSpec(1, "cli-claude", "")], max_puppet_turns=1,
                      puppet_ids=[1])
    result = await run_arena(conn, gs, cfg, policy=ExclusivePol())
    assert result["puppet_turns_played"] == 1
    assert conn.restored is True
    assert conn.is_connected is True


@pytest.mark.asyncio
async def test_coordinator_dead_socket_attempts_full_handback_then_surfaces(monkeypatch):
    """Permanently-dead tuner socket: the coordinator ATTEMPTS reclaim, restore, and disable
    (all three, best-effort), then surfaces the failure. It must NOT falsely report the human
    restored over a socket that genuinely cannot carry the restore command."""
    async def noop(_delay): pass
    monkeypatch.setattr(asyncio, "sleep", noop)

    class ExclusivePol:
        needs_exclusive_tuner = True
        async def __call__(self, gs, player_id, turn, **kwargs):
            return {"summary": "cli ran", "actions": []}

    class DeadSocketConn(FakeConn):
        """connect() always fails → after the exclusive disconnect the socket stays dead."""
        def __init__(self):
            super().__init__()
            self.connect_attempts = 0
        async def connect(self):
            self.connect_attempts += 1
            raise OSError("port 4318 still held")

    conn = DeadSocketConn()
    gs = FakeGS()
    cfg = ArenaConfig(players=[PlayerSpec(1, "cli-claude", "")], max_puppet_turns=1,
                      puppet_ids=[1])
    # Over a dead socket the handback cannot complete; run_arena surfaces the failure
    # rather than returning a fabricated success.
    with pytest.raises(ConnectionError):
        await run_arena(conn, gs, cfg, policy=ExclusivePol())
    # reclaim was attempted to exhaustion (in-loop + finally budgets)
    assert conn.connect_attempts >= 5
    # restore_local(0) AND disable were still ATTEMPTED in the finally despite the dead socket
    assert any("SetLocalPlayerAndObserver(0)" in c for c in conn.read_calls)
    assert any("DISABLED" in c for c in conn.read_calls)
    # ...but restore did NOT succeed — no fake handback over a dead socket
    assert conn.restored is False


@pytest.mark.asyncio
async def test_coordinator_body_cancellation_not_masked_by_cleanup_error(monkeypatch):
    """The realistic Ctrl-C path: cancellation originates in the policy BODY (during the long
    CLI turn), not in a finally step. The finally then runs over a dead socket, so reclaim/
    restore/disable each raise an ordinary ConnectionError. The propagated exception MUST stay
    CancelledError — a best-effort cleanup Exception must NOT replace the in-flight cancellation.
    Goes red under the pre-fix `raise first_exc` (which would surface ConnectionError instead)."""
    async def noop(_delay): pass
    monkeypatch.setattr(asyncio, "sleep", noop)

    class CancelInBodyPol:
        needs_exclusive_tuner = True
        async def __call__(self, gs, player_id, turn, **kwargs):
            raise asyncio.CancelledError()   # Ctrl-C lands mid-turn

    class DeadSocketConn(FakeConn):
        """connect() always fails → after the exclusive disconnect the socket stays dead, so
        every finally step raises an ordinary ConnectionError."""
        def __init__(self):
            super().__init__()
            self.connect_attempts = 0
        async def connect(self):
            self.connect_attempts += 1
            raise OSError("port 4318 still held")

    conn = DeadSocketConn()
    gs = FakeGS()
    cfg = ArenaConfig(players=[PlayerSpec(1, "cli-claude", "")], max_puppet_turns=1,
                      puppet_ids=[1])
    with pytest.raises(asyncio.CancelledError):
        await run_arena(conn, gs, cfg, policy=CancelInBodyPol())
    # cleanup was still attempted best-effort despite the in-flight cancellation
    assert any("SetLocalPlayerAndObserver(0)" in c for c in conn.read_calls)
    assert any("DISABLED" in c for c in conn.read_calls)


@pytest.mark.asyncio
async def test_coordinator_cancelled_in_finally_reraises_after_full_handback(monkeypatch):
    """A CancelledError from the FINALLY reclaim must (a) not skip restore/disable and (b) be
    the exception that propagates. The socket is dead so finish_units leaves a ConnectionError
    in flight; only the finally's `raise first_exc` turns the surfaced exception into
    CancelledError — so this test goes red if either the re-raise or the BaseException capture
    is removed."""
    async def noop(_delay): pass
    monkeypatch.setattr(asyncio, "sleep", noop)

    class ExclusivePol:
        needs_exclusive_tuner = True
        async def __call__(self, gs, player_id, turn, **kwargs):
            return {"summary": "cli ran", "actions": []}

    class CancelInFinallyConn(FakeConn):
        """In-loop reclaim fails with OSError (retry returns False, socket stays dead); the
        first dead-socket read marks that we've left the loop body, so the FINALLY reclaim's
        connect() is the one that raises CancelledError."""
        def __init__(self):
            super().__init__()
            self._headed_to_finally = False
        async def connect(self):
            if self._headed_to_finally:
                raise asyncio.CancelledError()
            raise OSError("port 4318 busy")
        async def execute_read(self, lua, timeout=5.0):
            if not self._connected:
                self._headed_to_finally = True
            return await super().execute_read(lua, timeout)

    conn = CancelInFinallyConn()
    gs = FakeGS()
    cfg = ArenaConfig(players=[PlayerSpec(1, "cli-claude", "")], max_puppet_turns=1,
                      puppet_ids=[1])
    with pytest.raises(asyncio.CancelledError):
        await run_arena(conn, gs, cfg, policy=ExclusivePol())
    # restore_local(0) was still attempted in the handback despite the CancelledError
    assert any("SetLocalPlayerAndObserver(0)" in c for c in conn.read_calls)


@pytest.mark.asyncio
async def test_sweep_runs_and_is_logged():
    conn, gs = FakeConn(), SweepGS()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])

    result = await run_arena(conn, gs, cfg, policy=ScriptedPolicy())

    assert conn.restored is True
    assert gs.promoted == [(1, "PROMOTION_BATTLECRY")]
    assert result["log"][0]["promotion_sweep"][0]["promotion_type"] == "PROMOTION_BATTLECRY"


@pytest.mark.asyncio
async def test_sweep_failure_does_not_block_handback(monkeypatch):
    async def boom(_gs):
        raise RuntimeError("sweep failed")

    class NoopPolicy:
        async def __call__(self, gs, player_id, turn, **kwargs):
            return {"summary": "noop", "actions": []}

    monkeypatch.setattr(autoresolve, "sweep_promotions", boom)
    conn, gs = FakeConn(), FakeGS()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])

    result = await run_arena(conn, gs, cfg, policy=NoopPolicy())

    assert conn.restored is True
    assert result["log"][0]["promotion_sweep"] == []


@pytest.mark.asyncio
async def test_policy_result_cannot_overwrite_promotion_sweep_log():
    class ConflictingPolicy:
        async def __call__(self, gs, player_id, turn, **kwargs):
            return {
                "summary": "conflict",
                "actions": [],
                "promotion_sweep": [{"promotion_type": "POLICY_VALUE"}],
            }

    conn, gs = FakeConn(), SweepGS()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])

    result = await run_arena(conn, gs, cfg, policy=ConflictingPolicy())

    assert result["log"][0]["promotion_sweep"][0]["promotion_type"] == "PROMOTION_BATTLECRY"


@pytest.mark.asyncio
async def test_exclusive_policy_reconnects_before_sweep(monkeypatch):
    sweep_connected = []

    async def recording_sweep(_gs):
        sweep_connected.append(conn.is_connected)
        return [{"promotion_type": "PROMOTION_BATTLECRY"}]

    class ExclusivePolicy:
        needs_exclusive_tuner = True

        async def __call__(self, gs, player_id, turn, **kwargs):
            assert conn.is_connected is False
            return {"summary": "exclusive", "actions": []}

    conn, gs = FakeConn(), FakeGS()
    monkeypatch.setattr(autoresolve, "sweep_promotions", recording_sweep)
    cfg = ArenaConfig(players=[PlayerSpec(1, "cli-claude", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])

    result = await run_arena(conn, gs, cfg, policy=ExclusivePolicy())

    assert result["puppet_turns_played"] == 1
    assert sweep_connected == [True]


# ---------------------------------------------------------------------------
# Task 4 — transcript instrumentation tests
# ---------------------------------------------------------------------------

_OV_BEFORE = "1|1|CivA|Leader|100.0|5.0|10.0|8.0|20.0|Mining|Drama|2|5|50"
_OV_AFTER  = "1|1|CivA|Leader|110.0|5.0|12.0|9.0|25.0|Mining|Drama|2|6|55"


class FakeConnWithOverview(FakeConn):
    """FakeConn that returns two distinct overview lines on sequential execute_write calls."""
    def __init__(self):
        super().__init__()
        self._overview_calls = 0

    async def execute_write(self, lua, timeout=5.0):
        self.write_calls.append(lua)
        self._maybe_die()
        if "Game.GetLocalPlayer" in lua:
            self._overview_calls += 1
            return [_OV_BEFORE] if self._overview_calls == 1 else [_OV_AFTER]
        return []


class FakeGSWithConn(FakeGS):
    """FakeGS with a .conn attribute for _overview_snapshot."""
    def __init__(self, conn):
        super().__init__()
        self.conn = conn


class FakeSink:
    """Recording transcript sink."""
    def __init__(self): self.records = []
    def write(self, record: dict): self.records.append(record)


class TranscriptPolicy:
    """Policy that returns a transcript payload."""
    provider = "local"
    model = "test-model"

    async def __call__(self, gs, player_id, turn, **kwargs):
        return {
            "summary": "done",
            "transcript": {
                "steps": [{"tool": "get_game_overview"}, {"tool": "end_turn"}],
                "final_answer": "ok",
            },
            "usage": {"usd": 0.05},
        }


@pytest.mark.asyncio
async def test_transcript_write_called_once_per_puppet_turn():
    """transcript.write is called exactly once per puppet turn, with correct payload."""
    conn = FakeConnWithOverview()
    gs = FakeGSWithConn(conn)
    sink = FakeSink()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])

    result = await run_arena(conn, gs, cfg, policy=TranscriptPolicy(), transcript=sink)

    assert result["puppet_turns_played"] == 1
    assert len(sink.records) == 1

    rec = sink.records[0]
    assert rec["schema_version"] == 1
    assert rec["player_id"] == 1
    assert rec["turn"] == 2          # from _polls: TURN|2
    assert rec["step_count"] == 2
    assert rec["usd"] == pytest.approx(0.05)
    assert rec["provider"] == "local"
    assert rec["model"] == "test-model"
    assert rec["driver"] == "in_process"
    # payload keys merged in
    assert rec["final_answer"] == "ok"


@pytest.mark.asyncio
async def test_transcript_record_includes_promotion_sweep():
    conn = FakeConnWithOverview()
    gs = SweepGS()
    gs.conn = conn
    sink = FakeSink()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])

    await run_arena(conn, gs, cfg, policy=TranscriptPolicy(), transcript=sink)

    assert sink.records[0]["promotion_sweep"][0]["promotion_type"] == "PROMOTION_BATTLECRY"


@pytest.mark.asyncio
async def test_transcript_state_before_after_delta():
    """state_before / state_after / state_delta are computed from the two overview snapshots."""
    conn = FakeConnWithOverview()
    gs = FakeGSWithConn(conn)
    sink = FakeSink()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])

    await run_arena(conn, gs, cfg, policy=TranscriptPolicy(), transcript=sink)

    rec = sink.records[0]
    before = rec["state_before"]
    after = rec["state_after"]
    delta = rec["state_delta"]

    assert before["gold"] == pytest.approx(100.0)
    assert after["gold"]  == pytest.approx(110.0)

    assert delta["gold"]    == pytest.approx(10.0)
    assert delta["science"] == pytest.approx(2.0)
    assert delta["culture"] == pytest.approx(1.0)
    assert delta["faith"]   == pytest.approx(5.0)
    assert delta["score"]   == 5
    assert delta["cities"]  == 0
    assert delta["units"]   == 1
    # string fields come from the after snapshot
    assert delta["research"] == "Mining"
    assert delta["civic"]    == "Drama"


@pytest.mark.asyncio
async def test_transcript_none_adds_no_snapshot_reads():
    """transcript=None (default) → ZERO overview queries issued to the game."""
    class CountingWriteConn(FakeConn):
        def __init__(self):
            super().__init__()
            self.overview_queries = 0
        async def execute_write(self, lua, timeout=5.0):
            self._maybe_die()
            if "Game.GetLocalPlayer" in lua:
                self.overview_queries += 1
            return []

    conn = CountingWriteConn()
    gs = FakeGSWithConn(conn)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])

    # transcript not passed → default None → behavior-neutral
    await run_arena(conn, gs, cfg, policy=TranscriptPolicy())
    assert conn.overview_queries == 0


@pytest.mark.asyncio
async def test_coordinator_run_id_propagates_to_record():
    """run_id set on ArenaConfig reaches the written transcript record."""
    conn = FakeConnWithOverview()
    gs = FakeGSWithConn(conn)
    sink = FakeSink()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1], run_id="arena-run-xyz-42")

    await run_arena(conn, gs, cfg, policy=TranscriptPolicy(), transcript=sink)

    assert len(sink.records) == 1
    assert sink.records[0]["run_id"] == "arena-run-xyz-42"


@pytest.mark.asyncio
async def test_log_entry_excludes_transcript_key():
    """run_arena log entries must NOT carry the 'transcript' key (stdout bloat).
    The sink record must still contain the steps (data not lost)."""
    conn = FakeConnWithOverview()
    gs = FakeGSWithConn(conn)
    sink = FakeSink()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])

    result = await run_arena(conn, gs, cfg, policy=TranscriptPolicy(), transcript=sink)

    assert result["puppet_turns_played"] == 1
    # Every log entry must be transcript-free
    for entry in result["log"]:
        assert "transcript" not in entry, "log entry must not carry the full transcript"
    # Sink must still have the full record with steps present
    assert len(sink.records) == 1
    assert sink.records[0]["step_count"] == 2


@pytest.mark.asyncio
async def test_null_sink_zero_snapshot_reads():
    """NullSink (enabled=False) → ZERO overview queries issued; write is a no-op.

    This is the H2 gate: NullSink overhead is eliminated. FAILS before H2 (was 2).
    """
    from civ_mcp.arena.transcript import NullSink

    ov_line = "1|1|CivA|Leader|100.0|5.0|10.0|8.0|20.0|Mining|Drama|2|5|50"

    class CountingWriteConn(FakeConn):
        def __init__(self):
            super().__init__()
            self.overview_queries = 0
        async def execute_write(self, lua, timeout=5.0):
            self._maybe_die()
            if "Game.GetLocalPlayer" in lua:
                self.overview_queries += 1
                return [ov_line]
            return []

    conn = CountingWriteConn()
    gs = FakeGSWithConn(conn)
    sink = NullSink()
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])

    result = await run_arena(conn, gs, cfg, policy=TranscriptPolicy(), transcript=sink)

    assert result["puppet_turns_played"] == 1
    # NullSink.enabled=False → coordinator must skip both snapshots entirely
    assert conn.overview_queries == 0, (
        f"NullSink must produce 0 overview queries, got {conn.overview_queries}"
    )


@pytest.mark.asyncio
async def test_transcript_sink_two_snapshot_reads():
    """TranscriptSink (enabled=True) → 2 overview queries per puppet turn (before + after)."""
    import tempfile, os
    from civ_mcp.arena.transcript import TranscriptSink

    ov_line = "1|1|CivA|Leader|100.0|5.0|10.0|8.0|20.0|Mining|Drama|2|5|50"

    class CountingWriteConn(FakeConn):
        def __init__(self):
            super().__init__()
            self.overview_queries = 0
        async def execute_write(self, lua, timeout=5.0):
            self._maybe_die()
            if "Game.GetLocalPlayer" in lua:
                self.overview_queries += 1
                return [ov_line]
            return []

    conn = CountingWriteConn()
    gs = FakeGSWithConn(conn)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1])

    with tempfile.TemporaryDirectory() as td:
        sink = TranscriptSink(os.path.join(td, "transcript.jsonl"))
        result = await run_arena(conn, gs, cfg, policy=TranscriptPolicy(), transcript=sink)

    assert result["puppet_turns_played"] == 1
    # TranscriptSink.enabled=True → both snapshots must fire
    assert conn.overview_queries == 2, (
        f"TranscriptSink must produce 2 overview queries, got {conn.overview_queries}"
    )


class _DiploConn:
    """Stub conn for _clear_blocking_diplomacy: serves the CLEAR result, records the Lua."""
    def __init__(self, clear_lines):
        self._lines = clear_lines
        self.writes = []
    async def execute_write(self, lua, timeout=5.0):
        self.writes.append(lua)
        return self._lines


def test_clear_blocking_diplomacy_reports_blocked():
    from civ_mcp.arena.coordinator import _clear_blocking_diplomacy
    conn = _DiploConn(["CLEAR|blocked|closed=1", "---END---"])
    assert asyncio.run(_clear_blocking_diplomacy(conn)) == "CLEAR|blocked|closed=1"
    # used the single visibility-gated clear builder (checks the views by name)
    assert any("DiplomacyActionView" in w and "LeaderScene" in w for w in conn.writes)


def test_clear_blocking_diplomacy_reports_none_when_nothing_visible():
    from civ_mcp.arena.coordinator import _clear_blocking_diplomacy
    conn = _DiploConn(["CLEAR|none", "---END---"])
    assert asyncio.run(_clear_blocking_diplomacy(conn)) == "CLEAR|none"


def test_clear_blocking_diplomacy_swallows_errors():
    from civ_mcp.arena.coordinator import _clear_blocking_diplomacy
    class _Boom:
        async def execute_write(self, lua, timeout=5.0):
            raise ConnectionError("dead socket")
    assert asyncio.run(_clear_blocking_diplomacy(_Boom())) == "err"


# ---------------------------------------------------------------------------
# Task 5 — standing memory / task tracker coordinator integration
# ---------------------------------------------------------------------------


class RecordingPolicy:
    """Fake policy that records the kwargs of every call and returns a canned result."""

    provider = "local"

    def __init__(self, result, options=None, needs_exclusive_tuner=False):
        self.result = result
        self.options = options or CivOptions()
        self.needs_exclusive_tuner = needs_exclusive_tuner
        self.calls = []

    async def __call__(self, gs, player_id, turn, **kwargs):
        self.calls.append(kwargs)
        return self.result


class FakeGSWithUnit(FakeGS):
    """FakeGS whose get_units() serves a single unit at a fixed position, and that
    supports found_city -- enough for a settle task to complete in run_pre_model_tasks."""

    def __init__(self, unit_id, unit_index, x, y, moves_remaining=2.0):
        super().__init__()
        self._unit = lq.UnitInfo(
            unit_id=unit_id, unit_index=unit_index, name="Settler",
            unit_type="UNIT_SETTLER", x=x, y=y, moves_remaining=moves_remaining,
            max_moves=2.0, health=100, max_health=100, valid_improvements=[],
        )
        self.found_city_calls = []
        self.move_unit_calls = []

    async def get_units(self):
        return [self._unit]

    async def get_diplomacy(self):
        return []

    async def get_threat_scan(self):
        return []

    async def get_map_area(self, x, y, radius=2):
        return []

    async def found_city(self, unit_index):
        self.found_city_calls.append(unit_index)
        return "FOUNDED|5,5"

    async def move_unit(self, unit_index, target_x, target_y):
        self.move_unit_calls.append((unit_index, target_x, target_y))
        return f"MOVING_TO|{target_x},{target_y}"


@pytest.mark.asyncio
async def test_memory_from_turn_n_injected_on_turn_n_plus_1(tmp_path):
    """Standing memory captured from one run_arena call's final summary is loaded and
    injected as memory_block on a LATER, independent run_arena call for the same
    run_id/player -- proving the persistence is run-local, not held in-process state."""
    opts = CivOptions(memory=MemoryOptions(enabled=True, max_chars=1200))
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1], run_id="memtest",
                      transcript_dir=str(tmp_path))

    pol1 = RecordingPolicy({
        "summary": "did stuff",
        "transcript": {"final_summary": (
            "TACTICAL: did stuff.\nSTANDING PLAN:\n- march settler to (18,24)\n"
        )},
    }, options=opts)
    await run_arena(FakeConn(), FakeGS(), cfg, policy=pol1)
    # Nothing was on disk yet when turn N's policy was invoked.
    assert pol1.calls[0]["memory_block"] == ""

    pol2 = RecordingPolicy({"summary": "no plan this time"}, options=opts)
    await run_arena(FakeConn(), FakeGS(), cfg, policy=pol2)
    assert pol2.calls[0]["memory_block"].startswith("== STANDING PLAN (captured turn 2")
    assert "march settler to (18,24)" in pol2.calls[0]["memory_block"]


@pytest.mark.asyncio
async def test_stale_memory_loaded_but_not_reported_as_injected(tmp_path):
    run_id, player_id = "stale-memtest", 9
    save_memory(
        str(tmp_path),
        run_id,
        player_id,
        turn=1,
        text="keep settling east",
        max_chars=1200,
    )
    opts = CivOptions(memory=MemoryOptions(enabled=True, max_chars=1200, max_age_turns=1))
    cfg = ArenaConfig(
        players=[PlayerSpec(player_id, "local", "m")],
        max_puppet_turns=1,
        dry_run=True,
        puppet_ids=[player_id],
        run_id=run_id,
        transcript_dir=str(tmp_path),
    )
    pol = RecordingPolicy(
        {
            "summary": "no plan this time",
            "transcript": {"final_summary": "TACTICAL: no standing plan"},
        },
        options=opts,
    )
    conn = FakeConnWithOverview()
    conn._polls = iter([
        [f"LOCAL|{player_id}", "TURN|3", "ACTIVE|true", "LAST|1"],
    ])
    gs = FakeGSWithConn(conn)
    sink = FakeSink()

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert pol.calls[0]["memory_block"] == ""
    log_memory = result["log"][0]["standing_memory"]
    assert log_memory["loaded"] is True
    assert log_memory["injected"] is False
    assert log_memory["injected_chars"] == 0

    transcript_memory = sink.records[0]["standing_memory"]
    assert transcript_memory["loaded"] is True
    assert transcript_memory["injected"] is False
    assert transcript_memory["injected_chars"] == 0


@pytest.mark.asyncio
async def test_final_summary_with_standing_plan_saves_memory_to_disk(tmp_path):
    """A final summary carrying a STANDING PLAN block is captured to the on-disk
    memory store for this run_id/player, verified via the real file path."""
    opts = CivOptions(memory=MemoryOptions(enabled=True, max_chars=1200))
    cfg = ArenaConfig(players=[PlayerSpec(4, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[4], run_id="memtest2",
                      transcript_dir=str(tmp_path))
    pol = RecordingPolicy({
        "summary": "ignored",
        "transcript": {"final_summary": "STANDING PLAN:\n- keep exploring\n"},
    }, options=opts)

    conn = FakeConn()
    conn._polls = iter([
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
        ["LOCAL|4", "TURN|2", "ACTIVE|true", "LAST|1"],
    ])
    await run_arena(conn, FakeGS(), cfg, policy=pol)

    path = memory_path(str(tmp_path), "memtest2", 4)
    assert path.exists()
    assert "keep exploring" in path.read_text()


@pytest.mark.asyncio
async def test_final_summary_with_task_line_creates_persisted_task(tmp_path):
    """A final summary carrying a TASK line results in a persisted UnitTask on disk,
    even with no pre-existing task state for this player."""
    opts = CivOptions(task_tracker=TaskTrackerOptions(enabled=True, max_tasks=8))
    cfg = ArenaConfig(players=[PlayerSpec(5, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[5], run_id="tasktest",
                      transcript_dir=str(tmp_path))
    pol = RecordingPolicy({
        "summary": "ignored",
        "transcript": {"final_summary": (
            "STANDING PLAN:\n- march settler\nTASK settle unit_id=42 target=10,12\n"
        )},
    }, options=opts)

    conn = FakeConn()
    conn._polls = iter([
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
        ["LOCAL|5", "TURN|2", "ACTIVE|true", "LAST|1"],
    ])
    await run_arena(conn, FakeGS(), cfg, policy=pol)

    path = task_path(str(tmp_path), "tasktest", 5)
    assert path.exists()
    assert '"unit_id": 42' in path.read_text()
    assert '"kind": "settle"' in path.read_text()


@pytest.mark.asyncio
async def test_task_line_beyond_capture_clamp_still_creates_task(tmp_path):
    """TASK lines are parsed from the raw final summary, so a long Planning
    section that pushes them past the standing-plan capture budget must not
    silently drop them."""
    opts = CivOptions(task_tracker=TaskTrackerOptions(enabled=True, max_tasks=8))
    filler = "\n".join(f"reflection detail line {i}" for i in range(400))
    cfg = ArenaConfig(players=[PlayerSpec(5, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[5], run_id="taskclamp",
                      transcript_dir=str(tmp_path))
    pol = RecordingPolicy({
        "summary": "ignored",
        "transcript": {"final_summary": (
            "STANDING PLAN:\n- march settler\nPLANNING:\n"
            f"{filler}\nTASK settle unit_id=42 target=10,12\n"
        )},
    }, options=opts)

    conn = FakeConn()
    conn._polls = iter([
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
        ["LOCAL|5", "TURN|2", "ACTIVE|true", "LAST|1"],
    ])
    await run_arena(conn, FakeGS(), cfg, policy=pol)

    path = task_path(str(tmp_path), "taskclamp", 5)
    assert path.exists()
    assert '"unit_id": 42' in path.read_text()


@pytest.mark.asyncio
async def test_failed_tombstone_blocks_restatement_across_turns(tmp_path):
    """A task that exhausted its failure budget on an earlier turn must stay
    blocked when the model restates it verbatim on a later turn: the tombstone
    is persisted, loaded into the capture base, and wins over the restatement."""
    opts = CivOptions(task_tracker=TaskTrackerOptions(enabled=True, max_tasks=8))
    cfg = ArenaConfig(players=[PlayerSpec(5, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[5], run_id="tombtest",
                      transcript_dir=str(tmp_path))
    tombstone = UnitTask(
        task_id="settle:42", kind="settle", unit_id=42, target_x=10, target_y=12,
        created_turn=1, updated_turn=1, status="failed",
        last_result="found_city_failed_retry_limit", failure_count=3,
    )
    save_task_state(str(tmp_path), "tombtest", 5, [tombstone])
    pol = RecordingPolicy({
        "summary": "ignored",
        "transcript": {"final_summary": (
            "STANDING PLAN:\n- keep trying\nTASK settle unit_id=42 target=10,12\n"
        )},
    }, options=opts)

    conn = FakeConn()
    conn._polls = iter([
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
        ["LOCAL|5", "TURN|2", "ACTIVE|true", "LAST|1"],
    ])
    await run_arena(conn, FakeGS(), cfg, policy=pol)

    from civ_mcp.arena.task_tracker import load_task_state
    state = load_task_state(str(tmp_path), "tombtest", 5)
    assert [t.status for t in state.tasks] == ["failed"]
    assert state.tasks[0].failure_count == 3


@pytest.mark.asyncio
async def test_pre_model_task_results_appear_in_log_and_transcript(tmp_path):
    """A pre-existing active task that completes during the deterministic pre-model
    phase shows up in both the coordinator log entry and the transcript record's
    task_tracker.pre_model_results field."""
    opts = CivOptions(task_tracker=TaskTrackerOptions(enabled=True))
    run_id, player_id = "tasktest2", 6
    existing_task = UnitTask(
        task_id="settle:42", kind="settle", unit_id=42, target_x=5, target_y=5,
        created_turn=1, updated_turn=1,
    )
    save_task_state(str(tmp_path), run_id, player_id, [existing_task])

    cfg = ArenaConfig(players=[PlayerSpec(player_id, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[player_id], run_id=run_id,
                      transcript_dir=str(tmp_path))
    pol = RecordingPolicy({"summary": "no plan"}, options=opts)

    conn = FakeConn()
    conn._polls = iter([
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
        [f"LOCAL|{player_id}", "TURN|2", "ACTIVE|true", "LAST|1"],
    ])
    gs = FakeGSWithUnit(unit_id=42, unit_index=7, x=5, y=5)

    from civ_mcp.arena.transcript import TranscriptSink
    import os
    sink = TranscriptSink(os.path.join(str(tmp_path), "transcript.jsonl"))
    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert gs.found_city_calls == [7]
    log_entry = result["log"][0]
    assert log_entry["task_tracker"]["active_before"] == 1
    assert log_entry["task_tracker"]["pre_model_results"][0]["action"] == "found_city"
    assert log_entry["task_tracker"]["pre_model_results"][0]["status"] == "complete"


@pytest.mark.asyncio
async def test_pre_model_task_execution_refreshes_updated_turn(tmp_path):
    opts = CivOptions(task_tracker=TaskTrackerOptions(enabled=True, max_tasks=8))
    run_id, player_id = "task-refresh", 6
    existing_task = UnitTask(
        task_id="settle:65537",
        kind="settle",
        unit_id=65537,
        target_x=10,
        target_y=10,
        created_turn=2,
        updated_turn=2,
    )
    save_task_state(str(tmp_path), run_id, player_id, [existing_task])
    cfg = ArenaConfig(
        players=[PlayerSpec(player_id, "local", "m")],
        max_puppet_turns=1,
        dry_run=True,
        puppet_ids=[player_id],
        run_id=run_id,
        transcript_dir=str(tmp_path),
    )
    pol = RecordingPolicy(
        {"summary": "no new task", "transcript": {"final_summary": "TACTICAL: none"}},
        options=opts,
    )
    conn = FakeConn()
    conn._polls = iter([
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
        [f"LOCAL|{player_id}", "TURN|9", "ACTIVE|true", "LAST|1"],
    ])
    gs = FakeGSWithUnit(unit_id=65537, unit_index=1, x=1, y=1)

    await run_arena(conn, gs, cfg, policy=pol)

    saved = task_path(str(tmp_path), run_id, player_id).read_text()
    assert '"updated_turn": 9' in saved


@pytest.mark.asyncio
async def test_exclusive_cli_policy_still_receives_memory_and_task_blocks(tmp_path):
    """A CLI-style policy (needs_exclusive_tuner=True) still gets memory_block and
    task_block populated from disk, even though the tuner connection is released
    before the policy call -- proving load happens before the exclusive disconnect."""
    run_id, player_id = "clitest", 7
    from civ_mcp.arena.memory import save_memory
    save_memory(str(tmp_path), run_id, player_id, turn=1, text="scout north next.",
                max_chars=1200)

    opts = CivOptions(memory=MemoryOptions(enabled=True, max_chars=1200))
    cfg = ArenaConfig(players=[PlayerSpec(player_id, "cli-claude", "")], max_puppet_turns=1,
                      puppet_ids=[player_id], run_id=run_id, transcript_dir=str(tmp_path))
    pol = RecordingPolicy({"summary": "cli ran"}, options=opts, needs_exclusive_tuner=True)

    conn = FakeConn()
    conn._polls = iter([
        [f"LOCAL|{player_id}", "TURN|2", "ACTIVE|true", "LAST|1"],
    ])
    result = await run_arena(conn, FakeGS(), cfg, policy=pol)

    assert result["puppet_turns_played"] == 1
    assert conn.restored is True  # reconnect + handback still happened
    assert "scout north next." in pol.calls[0]["memory_block"]
    assert pol.calls[0]["memory_block"].startswith("== STANDING PLAN (captured turn 1, 1 turn old) ==")


@pytest.mark.asyncio
async def test_exclusive_cli_briefing_built_before_disconnect(monkeypatch):
    from civ_mcp.arena.briefing import Briefing
    import civ_mcp.arena.coordinator as coord_mod

    built_connected = []

    async def fake_build_briefing(gs, opts, budget):
        built_connected.append(conn.is_connected)
        return Briefing(text="PREBUILT BRIEFING", tokens=4, sections=["overview"])

    class ExclusiveBriefingPolicy(RecordingPolicy):
        needs_exclusive_tuner = True

        async def __call__(self, gs, player_id, turn, **kwargs):
            assert conn.is_connected is False
            assert kwargs["briefing"].text == "PREBUILT BRIEFING"
            return await super().__call__(gs, player_id, turn, **kwargs)

    monkeypatch.setattr(
        "civ_mcp.arena.prompt_context.build_briefing",
        fake_build_briefing,
    )
    conn = FakeConn()
    gs = FakeGS()
    opts = CivOptions(briefing=BriefingOptions(enabled=True))
    cfg = ArenaConfig(
        players=[PlayerSpec(7, "cli-claude", "")],
        max_puppet_turns=1,
        puppet_ids=[7],
    )
    conn._polls = iter([[ "LOCAL|7", "TURN|2", "ACTIVE|true", "LAST|1" ]])
    pol = ExclusiveBriefingPolicy({"summary": "cli ran"}, options=opts, needs_exclusive_tuner=True)

    result = await run_arena(conn, gs, cfg, policy=pol)

    assert result["puppet_turns_played"] == 1
    assert built_connected == [True]


@pytest.mark.asyncio
async def test_briefing_build_failure_does_not_abort_arena_turn(monkeypatch):
    """A briefing-build raise (e.g. a missing playbook file) must degrade this
    civ to no briefing, not abort the whole multi-civ run -- mirroring the
    memory/task-tracker load guards."""
    from civ_mcp.arena import coordinator

    async def boom(*args, **kwargs):
        raise RuntimeError("playbook missing")

    monkeypatch.setattr(coordinator, "maybe_build_briefing", boom)

    seen = {}

    class ExclusiveBriefingPolicy:
        needs_exclusive_tuner = True
        options = CivOptions(briefing=BriefingOptions(enabled=True))
        provider = "cli-claude"

        async def __call__(self, gs, player_id, turn, *, briefing=None):
            seen["briefing"] = briefing
            return {"summary": "ran"}

    conn = FakeConn()
    conn._polls = iter([["LOCAL|7", "TURN|2", "ACTIVE|true", "LAST|1"]])
    cfg = ArenaConfig(
        players=[PlayerSpec(7, "cli-claude", "")],
        max_puppet_turns=1,
        puppet_ids=[7],
    )
    pol = ExclusiveBriefingPolicy()

    result = await run_arena(conn, FakeGS(), cfg, policy=pol)

    assert result["puppet_turns_played"] == 1   # run survived the briefing failure
    assert seen["briefing"] is None              # degraded to no briefing


@pytest.mark.asyncio
async def test_exclusive_policy_without_briefing_kwarg_runs_with_briefing_enabled():
    class NarrowExclusivePolicy:
        needs_exclusive_tuner = True
        options = CivOptions(briefing=BriefingOptions(enabled=True))

        def __init__(self):
            self.calls = []

        async def __call__(
            self,
            gs,
            player_id,
            turn,
            *,
            memory_block="",
            task_block="",
        ):
            self.calls.append(
                {
                    "player_id": player_id,
                    "turn": turn,
                    "memory_block": memory_block,
                    "task_block": task_block,
                }
            )
            return {"summary": "narrow exclusive policy ran", "actions": []}

    conn = FakeConn()
    gs = FakeGS()
    cfg = ArenaConfig(
        players=[PlayerSpec(7, "cli-claude", "")],
        max_puppet_turns=1,
        puppet_ids=[7],
    )
    conn._polls = iter([[ "LOCAL|7", "TURN|2", "ACTIVE|true", "LAST|1" ]])
    pol = NarrowExclusivePolicy()

    result = await run_arena(conn, gs, cfg, policy=pol)

    assert result["puppet_turns_played"] == 1
    assert pol.calls == [
        {"player_id": 7, "turn": 2, "memory_block": "", "task_block": ""}
    ]


@pytest.mark.asyncio
async def test_nonexclusive_policy_without_briefing_kwarg_runs():
    class NarrowPolicy:
        options = CivOptions()

        def __init__(self):
            self.calls = []

        async def __call__(
            self,
            gs,
            player_id,
            turn,
            *,
            memory_block="",
            task_block="",
        ):
            self.calls.append(
                {
                    "player_id": player_id,
                    "turn": turn,
                    "memory_block": memory_block,
                    "task_block": task_block,
                }
            )
            return {"summary": "narrow policy ran", "actions": []}

    conn = FakeConn()
    gs = FakeGS()
    cfg = ArenaConfig(
        players=[PlayerSpec(7, "local", "")],
        max_puppet_turns=1,
        puppet_ids=[7],
    )
    conn._polls = iter([[ "LOCAL|7", "TURN|2", "ACTIVE|true", "LAST|1" ]])
    pol = NarrowPolicy()

    result = await run_arena(conn, gs, cfg, policy=pol)

    assert result["puppet_turns_played"] == 1
    assert pol.calls == [
        {"player_id": 7, "turn": 2, "memory_block": "", "task_block": ""}
    ]


@pytest.mark.asyncio
async def test_task_tracker_only_uses_task_capture_budget_not_memory_default(tmp_path):
    opts = CivOptions(task_tracker=TaskTrackerOptions(enabled=True, max_tasks=8))
    run_id, player_id = "task-capture-budget", 8
    long_plan = (
        "STANDING PLAN:\n"
        + ("- filler line to push task below memory default\n" * 80)
        + "TASK settle unit_id=42 target=10,12\n"
    )
    cfg = ArenaConfig(
        players=[PlayerSpec(player_id, "local", "m")],
        max_puppet_turns=1,
        dry_run=True,
        puppet_ids=[player_id],
        run_id=run_id,
        transcript_dir=str(tmp_path),
    )
    pol = RecordingPolicy(
        {"summary": "ignored", "transcript": {"final_summary": long_plan}},
        options=opts,
    )
    conn = FakeConn()
    conn._polls = iter([[f"LOCAL|{player_id}", "TURN|2", "ACTIVE|true", "LAST|1"]])

    await run_arena(conn, FakeGS(), cfg, policy=pol)

    path = task_path(str(tmp_path), run_id, player_id)
    assert '"unit_id": 42' in path.read_text()


@pytest.mark.asyncio
async def test_memory_save_failure_does_not_abort_arena_turn(monkeypatch, tmp_path):
    from civ_mcp.arena import coordinator

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(coordinator, "save_memory", boom)
    opts = CivOptions(memory=MemoryOptions(enabled=True, max_chars=1200))
    cfg = ArenaConfig(
        players=[PlayerSpec(4, "local", "m")],
        max_puppet_turns=1,
        dry_run=True,
        puppet_ids=[4],
        run_id="mem-save-failure",
        transcript_dir=str(tmp_path),
    )
    pol = RecordingPolicy(
        {
            "summary": "ignored",
            "transcript": {"final_summary": "STANDING PLAN:\n- keep exploring\n"},
        },
        options=opts,
    )
    conn = FakeConn()
    conn._polls = iter([
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
        ["LOCAL|4", "TURN|2", "ACTIVE|true", "LAST|1"],
    ])
    sink = FakeSink()

    result = await run_arena(conn, FakeGS(), cfg, policy=pol, transcript=sink)

    assert result["puppet_turns_played"] == 1
    assert result["log"][0]["standing_memory"]["error"] == "OSError('disk full')"
    assert sink.records[0]["standing_memory"]["error"] == "OSError('disk full')"


@pytest.mark.asyncio
async def test_task_state_save_failure_does_not_abort_arena_turn(monkeypatch, tmp_path):
    from civ_mcp.arena import coordinator

    def boom(*args, **kwargs):
        raise OSError("read only")

    monkeypatch.setattr(coordinator, "save_task_state", boom)
    opts = CivOptions(task_tracker=TaskTrackerOptions(enabled=True, max_tasks=8))
    cfg = ArenaConfig(
        players=[PlayerSpec(5, "local", "m")],
        max_puppet_turns=1,
        dry_run=True,
        puppet_ids=[5],
        run_id="task-save-failure",
        transcript_dir=str(tmp_path),
    )
    pol = RecordingPolicy(
        {
            "summary": "ignored",
            "transcript": {
                "final_summary": "STANDING PLAN:\nTASK settle unit_id=42 target=10,12\n"
            },
        },
        options=opts,
    )
    conn = FakeConn()
    conn._polls = iter([
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
        ["LOCAL|5", "TURN|2", "ACTIVE|true", "LAST|1"],
    ])
    sink = FakeSink()

    result = await run_arena(conn, FakeGS(), cfg, policy=pol, transcript=sink)

    assert result["puppet_turns_played"] == 1
    assert result["log"][0]["task_tracker"]["error"] == "OSError('read only')"
    assert sink.records[0]["task_tracker"]["error"] == "OSError('read only')"


@pytest.mark.asyncio
async def test_pre_model_save_failure_does_not_drop_turn_task_capture(monkeypatch, tmp_path):
    """A transient pre-model save failure must not discard the TASK lines the
    model emits this turn: post-turn capture still parses and persists them."""
    from civ_mcp.arena import coordinator

    real_save = coordinator.save_task_state
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(coordinator, "save_task_state", flaky)
    opts = CivOptions(task_tracker=TaskTrackerOptions(enabled=True, max_tasks=8))
    cfg = ArenaConfig(
        players=[PlayerSpec(5, "local", "m")],
        max_puppet_turns=1,
        dry_run=True,
        puppet_ids=[5],
        run_id="task-flaky-save",
        transcript_dir=str(tmp_path),
    )
    pol = RecordingPolicy(
        {
            "summary": "ignored",
            "transcript": {
                "final_summary": "STANDING PLAN:\nTASK settle unit_id=42 target=10,12\n"
            },
        },
        options=opts,
    )
    conn = FakeConn()
    conn._polls = iter([
        ["LOCAL|0", "TURN|1", "ACTIVE|false", "LAST|nil"],
        ["LOCAL|5", "TURN|2", "ACTIVE|true", "LAST|1"],
    ])

    result = await run_arena(conn, FakeGS(), cfg, policy=pol)

    path = task_path(str(tmp_path), "task-flaky-save", 5)
    assert path.exists()
    assert '"unit_id": 42' in path.read_text()
    # the transient pre-model error is still surfaced
    assert result["log"][0]["task_tracker"]["error"] == "OSError('disk full')"


@pytest.mark.asyncio
async def test_exclusive_cli_briefing_prebuild_uses_explicit_context_budget(monkeypatch):
    from civ_mcp.arena import coordinator
    from civ_mcp.arena.briefing import Briefing

    captured = {}

    async def fake_briefing(gs, options, *, n_ctx, playbook_chars, tool_schema_chars, supplied=None):
        captured["n_ctx"] = n_ctx
        return Briefing(text="PREBUILT", tokens=1, sections=["overview"])

    monkeypatch.setattr(coordinator, "maybe_build_briefing", fake_briefing)
    opts = CivOptions(context_budget=8192, briefing=BriefingOptions(enabled=True))
    cfg = ArenaConfig(
        players=[PlayerSpec(7, "cli-claude", "")],
        max_puppet_turns=1,
        puppet_ids=[7],
    )
    conn = FakeConn()
    conn._polls = iter([["LOCAL|7", "TURN|2", "ACTIVE|true", "LAST|1"]])
    pol = RecordingPolicy({"summary": "cli ran"}, options=opts, needs_exclusive_tuner=True)

    result = await run_arena(conn, FakeGS(), cfg, policy=pol)

    assert result["puppet_turns_played"] == 1
    assert captured["n_ctx"] == 8192


@pytest.mark.asyncio
async def test_old_signature_policy_without_kwargs_still_runs(tmp_path):
    """A pre-slice-3 policy whose __call__ is (gs, player_id, turn) must not be
    passed memory_block/task_block kwargs it cannot accept."""
    calls = []

    class OldStylePolicy:
        provider = "local"
        options = CivOptions()

        async def __call__(self, gs, player_id, turn):
            calls.append((player_id, turn))
            return {"summary": "old-style", "actions": []}

    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1], run_id="oldsig",
                      transcript_dir=str(tmp_path))

    result = await run_arena(FakeConn(), FakeGS(), cfg, policy=OldStylePolicy())

    assert result["puppet_turns_played"] == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_empty_run_id_does_not_share_memory_across_runs(tmp_path):
    """run_id='' must not collapse the memory dir onto transcript_dir, where a
    later unrelated run would inherit this run's standing plan."""
    opts = CivOptions(memory=MemoryOptions(enabled=True, max_chars=1200))
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      dry_run=True, puppet_ids=[1], run_id="",
                      transcript_dir=str(tmp_path))

    pol1 = RecordingPolicy({
        "summary": "did stuff",
        "transcript": {"final_summary": (
            "TACTICAL: did stuff.\nSTANDING PLAN:\n- march settler to (18,24)\n"
        )},
    }, options=opts)
    await run_arena(FakeConn(), FakeGS(), cfg, policy=pol1)

    assert not (tmp_path / "memory").exists()

    pol2 = RecordingPolicy({"summary": "no plan"}, options=opts)
    await run_arena(FakeConn(), FakeGS(), cfg, policy=pol2)

    assert pol2.calls[0]["memory_block"] == ""


def test_policy_accepts_kwarg_handles_bare_function_signature():
    """A plain-function policy's `.__call__` is a method-wrapper reporting
    (*args, **kwargs); introspecting it would spuriously accept every kwarg and
    then raise TypeError at the call site. Introspecting the callable itself
    must report the real signature."""
    from civ_mcp.arena.coordinator import _policy_accepts_kwarg

    async def bare(gs, player_id, turn):
        return {"summary": ""}

    async def flexible(gs, player_id, turn, **kwargs):
        return {"summary": ""}

    assert _policy_accepts_kwarg(bare, "memory_block") is False
    assert _policy_accepts_kwarg(bare, "briefing") is False
    assert _policy_accepts_kwarg(flexible, "memory_block") is True

    class Explicit:
        async def __call__(self, gs, player_id, turn, memory_block=""):
            return {"summary": ""}

    assert _policy_accepts_kwarg(Explicit(), "memory_block") is True
    assert _policy_accepts_kwarg(Explicit(), "task_block") is False


def test_repair_kwargs_rejects_positional_only_parameters():
    from civ_mcp.arena.coordinator import _policy_accepts_kwarg, _repair_kwargs

    class PositionalOnlyBlocker:
        async def __call__(self, gs, player_id, turn, blocker_block="", /):
            return {"summary": ""}

    class PositionalOnlyCaps:
        async def __call__(
            self, gs, player_id, turn, caps=None, /, *, blocker_block=""
        ):
            return {"summary": ""}

    assert _policy_accepts_kwarg(PositionalOnlyBlocker(), "blocker_block") is False
    assert _repair_kwargs(PositionalOnlyBlocker(), "repair", None) is None
    assert _policy_accepts_kwarg(PositionalOnlyCaps(), "blocker_block") is True
    assert _policy_accepts_kwarg(PositionalOnlyCaps(), "caps") is False
    assert _repair_kwargs(
        PositionalOnlyCaps(), "repair", {"government": True}
    ) == {"blocker_block": "repair"}


@pytest.mark.asyncio
async def test_tracker_only_capture_not_reported_as_memory_captured(tmp_path):
    """With memory disabled the extracted plan is never saved or injectable;
    captured_chars must read 0 or analyze counts the tracker-only civ as a
    standing-memory-captured turn."""
    opts = CivOptions(task_tracker=TaskTrackerOptions(enabled=True, max_tasks=8))
    run_id, player_id = "tracker-only-captured", 8
    cfg = ArenaConfig(
        players=[PlayerSpec(player_id, "local", "m")],
        max_puppet_turns=1,
        dry_run=True,
        puppet_ids=[player_id],
        run_id=run_id,
        transcript_dir=str(tmp_path),
    )
    pol = RecordingPolicy(
        {
            "summary": "ignored",
            "transcript": {
                "final_summary": (
                    "STANDING PLAN:\n- keep going\n"
                    "TASK settle unit_id=42 target=10,12\n"
                )
            },
        },
        options=opts,
    )
    conn = FakeConn()
    conn._polls = iter([[f"LOCAL|{player_id}", "TURN|2", "ACTIVE|true", "LAST|1"]])
    sink = FakeSink()

    result = await run_arena(conn, FakeGS(), cfg, policy=pol, transcript=sink)

    # Task capture itself still worked...
    assert '"unit_id": 42' in task_path(str(tmp_path), run_id, player_id).read_text()
    # ...but nothing reads as a standing-memory capture.
    assert result["log"][0]["standing_memory"]["captured_chars"] == 0
    assert sink.records[0]["standing_memory"]["captured_chars"] == 0


@pytest.mark.asyncio
async def test_policy_failure_is_skipped_not_crashed_and_restores_human():
    """A puppet LLM turn whose policy raises -- e.g. the llama.cpp gateway returns
    HTTP 500 on a malformed/truncated tool call (openai.InternalServerError) -- must
    NOT crash the whole run. The coordinator logs it, hands the seat back to the human
    (finish_units + restore_local(0)), consumes the puppet-turn budget, and continues.
    This mirrors the sweep/memory/task/briefing degrade-not-abort guards.

    Goes RED under the unguarded `result = await pol(...)`: the exception propagates
    out of run_arena and kills the watcher mid-round (leaving the human stuck on the
    puppet seat), which is exactly the live-run crash this guards against."""
    class BoomPolicy:
        provider = "local"
        options = CivOptions()

        def __init__(self):
            self.calls = 0

        async def __call__(self, gs, player_id, turn, **kwargs):
            self.calls += 1
            raise RuntimeError(
                "Error code: 500 - Failed to parse tool call arguments as JSON"
            )

    conn, gs = FakeConn(), FakeGS()
    # Two active puppet polls but budget of 1: a failed turn that correctly consumes
    # the budget yields exactly ONE attempt. A guard that forgot to decrement would
    # re-enter and call the policy a second time (or idle-loop) -- pol.calls pins it.
    conn._polls = iter([
        ["LOCAL|1", "TURN|2", "ACTIVE|true", "LAST|1"],
        ["LOCAL|1", "TURN|3", "ACTIVE|true", "LAST|1"],
    ])
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m")], max_puppet_turns=1,
                      puppet_ids=[1])
    pol = BoomPolicy()

    result = await run_arena(conn, gs, cfg, policy=pol)   # must NOT raise

    assert pol.calls == 1              # budget consumed: one attempt, no loop
    assert conn.restored is True       # human handed back despite the failure
    # The failure is surfaced in the log rather than silently swallowed.
    assert any(entry.get("skipped") for entry in result["log"])


# ---------------------------------------------------------------------------
# Task 10 — attention & turn-skipping coordinator integration
# ---------------------------------------------------------------------------

QUIET_SCAN_LINES = [
    "ATTN|THREAT|count=0|nearest=", "ATTN|CITYHP|damaged=", "ATTN|WAR|with=",
    "ATTN|LOYALTY|negative=", "ATTN|WC|turns=5", "ATTN|ERA|index=1",
    "ATTN|POP|total=12", "ATTN|GP|available=0", "ATTN|TRADE|idle=0",
    "ATTN|DIPLO|pending=0", "ATTN|BLOCKERS|types=",
]

# Overview snapshot matching FakeConnWithOverview's canned _OV_BEFORE line
# (score=50, gold=100.0, science=10.0, culture=8.0, faith=20.0, cities=2,
# units=5 -- see overview.py:584-598 field order). Seeding an attention
# baseline with THESE values (rather than zeros) means the freshly-read
# current-turn snapshot equals the seeded baseline, so no hard
# "CITY_COUNT_CHANGED"/"UNITS_LOST" trigger fires from a fixture mismatch
# that has nothing to do with the scenario under test.
_ATTN_BASELINE_SNAPSHOT = {
    "score": 50, "gold": 100.0, "science": 10.0, "culture": 8.0,
    "faith": 20.0, "research": "Mining", "civic": "Drama",
    "cities": 2, "units": 5,
}


class AttnConn(FakeConnWithOverview):
    # The attention scan is InGame Lua (execute_write): CITYHP/LOYALTY/WC/DIPLO
    # use APIs that are nil in GameCore -- live-probe P1 finding, turn 155.
    async def execute_write(self, lua, timeout=5.0):
        if "ATTN" in lua:
            self.write_calls.append(lua)
            return list(QUIET_SCAN_LINES)
        return await super().execute_write(lua, timeout)


class CountingPolicy:
    def __init__(self, options):
        self.options = options
        self.calls = 0
    async def __call__(self, gs, player_id, turn, *, digest_block="", **kw):
        self.calls += 1
        self.last_digest = digest_block
        return {"summary": "played", "actions": [], "transcript": {"steps": [], "final_summary": "played"}}


@pytest.mark.asyncio
async def test_auto_mode_sleeps_quiet_turn(tmp_path):
    from civ_mcp.arena.attention import AttentionState, save_attention_state
    from civ_mcp.arena.config import AttentionOptions
    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="auto"))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=3, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="r1", puppet_ids=[1])
    # seed a baseline so NO_BASELINE doesn't force a wake on the first capture
    # (matching FakeConnWithOverview's _OV_BEFORE fields -- see
    # _ATTN_BASELINE_SNAPSHOT above -- so the freshly-read current-turn
    # snapshot exactly matches it and no hard trigger fires from fixture
    # mismatch alone)
    save_attention_state(str(tmp_path), "r1", 1, AttentionState(
        run_id="r1", player_id=1,
        last_snapshot=dict(_ATTN_BASELINE_SNAPSHOT),
        last_scan={"at_war_with": [], "era_index": 1, "total_population": 12}))
    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)
    assert pol.calls == 0                       # no model invocation
    assert result["turns_slept"] == 1
    assert result["puppet_turns_played"] == 0   # max_puppet_turns NOT consumed
    rec = sink.records[-1]
    assert rec["slept"] is True and rec["turn_kind"] == "slept"
    assert rec["step_count"] == 0 and rec["usd"] == 0.0
    assert "skipped" not in rec                 # that key means FAILED
    assert conn.restored                        # handback still happened
    # Context regression (live-probe P1, turn 155): the scan MUST go through
    # execute_write (InGame). In GameCore, CITYHP/LOYALTY/WC/DIPLO hit nil
    # APIs -> ATTN_ERR every scan -> SCAN_PARTIAL wakes every turn and the
    # feature is silently inert.
    assert any("ATTN" in c for c in conn.write_calls)
    assert not any("ATTN" in c for c in conn.read_calls)


@pytest.mark.asyncio
async def test_scan_error_wake_carries_parse_detail(tmp_path):
    """Live-probe P3 finding (2026-07-14, turns 190/212): parse_attention_scan
    returning None produced a bare SCAN_ERROR wake with empty wake_detail and
    empty stderr -- undiagnosable post-run. The record must carry a preview of
    the raw lines (or name the missing snapshot) like SCAN_PARTIAL carries the
    Lua error (review-3 f3)."""
    class GarbageScanConn(FakeConnWithOverview):
        async def execute_write(self, lua, timeout=5.0):
            if "ATTN" in lua:
                self.write_calls.append(lua)
                return ["TUNER NOISE", "half a li"]
            return await super().execute_write(lua, timeout)

    from civ_mcp.arena.config import AttentionOptions
    conn = GarbageScanConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="auto"))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="rse", puppet_ids=[1])
    await run_arena(conn, gs, cfg, policy=pol, transcript=sink)
    rec = sink.records[-1]
    assert rec["attention"]["wake_cause"] == "SCAN_ERROR"
    detail = rec["attention"]["wake_detail"]
    assert "TUNER NOISE" in detail          # raw-line preview present


@pytest.mark.asyncio
async def test_first_capture_wakes_no_baseline(tmp_path, monkeypatch):
    """No seeded attention state -> load_attention_state returns a fresh state
    whose last_snapshot is None -> evaluate() returns NO_BASELINE -> the model
    runs this turn (a played turn), and the transcript record is annotated
    with the wake cause."""
    from civ_mcp.arena.config import AttentionOptions

    async def noop(_delay): pass
    monkeypatch.setattr(asyncio, "sleep", noop)

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="auto"))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="r2", puppet_ids=[1])

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert pol.calls == 1
    assert result["puppet_turns_played"] == 1
    assert result["turns_slept"] == 0
    rec = sink.records[-1]
    assert rec["turn_kind"] == "played" and rec["attention"]["wake_cause"] == "NO_BASELINE"
    assert rec["attention"]["decision"] == "woke"


@pytest.mark.asyncio
async def test_off_mode_bit_for_bit_today(tmp_path, monkeypatch):
    """mode="off" (the default): no ATTN read is ever issued and no "attention"
    key appears on the record -- except "turn_kind" IS added to played records
    unconditionally, regardless of attention mode."""
    async def noop(_delay): pass
    monkeypatch.setattr(asyncio, "sleep", noop)

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions()  # attention.mode == "off" by default
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="r3", puppet_ids=[1])

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert pol.calls == 1
    assert result["puppet_turns_played"] == 1
    assert result["turns_slept"] == 0
    assert not any("ATTN" in c for c in conn.read_calls)   # scan never issued
    assert not any("ATTN" in c for c in conn.write_calls)  # ...on either context
    rec = sink.records[-1]
    assert rec["turn_kind"] == "played"     # added unconditionally
    assert "attention" not in rec           # off mode never produces this


@pytest.mark.asyncio
async def test_wake_digest_injected_after_sleep(tmp_path):
    """Two captured turns: the first sleeps (quiet scan); by the second, the
    streak (1, from the first sleep) has reached max_streak=1 -> STREAK_CAP
    wake. The wake digest rendered from the accumulated sleep record must
    reach the policy as digest_block, and the record must show a wake."""
    from civ_mcp.arena.attention import AttentionState, save_attention_state
    from civ_mcp.arena.config import AttentionOptions

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="auto", max_streak=1))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="r4", puppet_ids=[1])
    # Two consecutive active puppet polls, no idle detour needed: turn 2
    # sleeps (quiet scan matches the seeded baseline); turn 3's streak (1,
    # set by turn 2's sleep) meets max_streak (1) -> STREAK_CAP wake, and
    # remaining (1) is consumed so the loop stops there.
    conn._polls = iter([
        ["LOCAL|1", "TURN|2", "ACTIVE|true", "LAST|1"],
        ["LOCAL|1", "TURN|3", "ACTIVE|true", "LAST|1"],
    ])
    save_attention_state(str(tmp_path), "r4", 1, AttentionState(
        run_id="r4", player_id=1,
        last_snapshot=dict(_ATTN_BASELINE_SNAPSHOT),
        last_scan={"at_war_with": [], "era_index": 1, "total_population": 12}))

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert result["turns_slept"] == 1
    assert result["puppet_turns_played"] == 1
    assert pol.calls == 1
    assert "WHILE YOU SLEPT" in pol.last_digest
    rec = sink.records[-1]
    assert rec["turn_kind"] == "played"
    assert rec["attention"]["decision"] == "woke"
    assert rec["attention"]["wake_cause"] == "STREAK_CAP"


@pytest.mark.asyncio
async def test_directive_captured_without_memory(tmp_path, monkeypatch):
    """mode="model", memory+tracker disabled (CivOptions defaults): the
    model's final_summary carries a SKIP directive. With no seeded baseline
    the first capture wakes on NO_BASELINE regardless of mode, so the model
    runs; its directive is still parsed and persisted post-turn, and no
    standing-memory file is ever created since memory is disabled."""
    from civ_mcp.arena.attention import load_attention_state
    from civ_mcp.arena.config import AttentionOptions
    from civ_mcp.arena.memory import memory_path

    async def noop(_delay): pass
    monkeypatch.setattr(asyncio, "sleep", noop)

    opts = CivOptions(attention=AttentionOptions(mode="model"))

    class DirectivePolicy:
        provider = "local"
        options = opts
        async def __call__(self, gs, player_id, turn, **kwargs):
            return {
                "summary": "done",
                "transcript": {"steps": [], "final_summary": "done\nSKIP: 3"},
            }

    conn = AttnConn(); gs = FakeGSWithConn(conn)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="r5", puppet_ids=[1])

    result = await run_arena(conn, gs, cfg, policy=DirectivePolicy())

    assert result["puppet_turns_played"] == 1
    state = load_attention_state(str(tmp_path), "r5", 1)
    assert state.skips_remaining == 3
    assert not memory_path(str(tmp_path), "r5", 1).exists()


@pytest.mark.asyncio
async def test_max_game_turns_caps_run(tmp_path, monkeypatch):
    """max_game_turns=1 caps the run after exactly one slept turn even though
    max_puppet_turns=5 remains almost entirely unused -- game_turns counts
    ALL captured turns (played + slept + failed), not just played ones."""
    from civ_mcp.arena.attention import AttentionState, save_attention_state
    from civ_mcp.arena.config import AttentionOptions

    async def noop(_delay): pass
    monkeypatch.setattr(asyncio, "sleep", noop)

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="auto"))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=5, max_game_turns=1, idle_poll_limit=10,
                      transcript_dir=str(tmp_path), run_id="r6", puppet_ids=[1])
    save_attention_state(str(tmp_path), "r6", 1, AttentionState(
        run_id="r6", player_id=1,
        last_snapshot=dict(_ATTN_BASELINE_SNAPSHOT),
        last_scan={"at_war_with": [], "era_index": 1, "total_population": 12}))

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert pol.calls == 0
    assert result["turns_slept"] == 1
    assert result["puppet_turns_played"] == 0


@pytest.mark.asyncio
async def test_attention_load_crash_degrades_to_wake(tmp_path, monkeypatch):
    """load_attention_state raising must NOT abort the run: the coordinator
    degrades to a fresh in-memory state (== what load returns on any failure)
    with NO second disk read, evaluate() fails open to a wake, and the model
    runs this turn. Goes RED under the unguarded fallback reload, whose
    second load_attention_state call re-raises out of run_arena."""
    from civ_mcp.arena.config import AttentionOptions
    import civ_mcp.arena.coordinator as coord_mod

    async def noop(_delay): pass
    monkeypatch.setattr(asyncio, "sleep", noop)

    def boom(*args, **kwargs):
        raise RuntimeError("attention state dir unreadable")

    monkeypatch.setattr(coord_mod, "load_attention_state", boom)

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="auto"))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="r7", puppet_ids=[1])

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)  # must NOT raise

    assert pol.calls == 1                      # fail-open: woke, model turn
    assert result["turns_slept"] == 0
    assert result["puppet_turns_played"] == 1
    assert conn.restored


@pytest.mark.asyncio
async def test_partial_snapshot_state_delta_none(tmp_path):
    """A persisted last_snapshot missing one numeric key ("units") must not
    crash the slept-turn transcript write: the KeyError degrades to
    state_delta=None (the record's documented "unknown delta" value).

    The seeding still sleeps honestly: _hard_triggers uses .get() with
    defaults, so prev missing "units" reads as 0 and snapshot.units(5) < 0
    is False (no UNITS_LOST); cities match at 2 (no CITY_COUNT_CHANGED);
    gold equal (no GOLD_CRASH); the quiet scan matches the seeded scan
    scalars. Only the delta arithmetic touches the missing key."""
    from civ_mcp.arena.attention import AttentionState, save_attention_state
    from civ_mcp.arena.config import AttentionOptions

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="auto"))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=3, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="r8", puppet_ids=[1])
    partial = dict(_ATTN_BASELINE_SNAPSHOT)
    del partial["units"]                       # dict-shaped but key-incomplete
    save_attention_state(str(tmp_path), "r8", 1, AttentionState(
        run_id="r8", player_id=1,
        last_snapshot=partial,
        last_scan={"at_war_with": [], "era_index": 1, "total_population": 12}))

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)  # must NOT raise

    assert pol.calls == 0                      # the sleep genuinely happened
    assert result["turns_slept"] == 1
    rec = sink.records[-1]
    assert rec["turn_kind"] == "slept"
    assert rec["state_delta"] is None          # unknown delta, degrade not abort
    assert conn.restored


class RaisingPolicy:
    def __init__(self, options):
        self.options = options
    async def __call__(self, gs, player_id, turn, **kw):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_failed_wake_cancels_directive_remainder(tmp_path, monkeypatch):
    """Final-review Important 2: a wake decision whose policy call fails never
    reaches note_wake -- the failed-turn branch must still cancel the directive
    remainder (spec section 3: ANY wake cancels), or the seat resumes a stale
    sleep on the next captured turn. The slept accumulator survives so the
    digest reaches the eventual successful wake."""
    from civ_mcp.arena.attention import (
        AttentionState,
        load_attention_state,
        save_attention_state,
    )
    from civ_mcp.arena.config import AttentionOptions

    async def noop(_delay): pass
    monkeypatch.setattr(asyncio, "sleep", noop)

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="model"))
    pol = RaisingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="r7", puppet_ids=[1])
    slept_record = {"turn": 1, "snapshot": dict(_ATTN_BASELINE_SNAPSHOT),
                    "task_notes": [], "notifications": []}
    save_attention_state(str(tmp_path), "r7", 1, AttentionState(
        run_id="r7", player_id=1,
        directive={"skip": 3, "wake_if": []},
        skips_remaining=2, streak=1,
        last_snapshot=None,   # no baseline -> NO_BASELINE wake regardless of directive
        last_scan=None,
        slept=[slept_record]))

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert result["puppet_turns_played"] == 0
    assert any(entry.get("skipped") for entry in result["log"])   # the failed turn
    st = load_attention_state(str(tmp_path), "r7", 1)
    assert st.skips_remaining == 0            # stale sleep cancelled
    assert len(st.slept) == 1                 # digest accumulator survives
    assert st.streak == 1                     # streak keeps bounding model-free turns


@pytest.mark.asyncio
async def test_tampered_slept_record_costs_digest_not_run(tmp_path):
    """Final-review pinhole: a slept record missing "turn" (external tampering
    -- load validates list-of-dicts, not record internals) must degrade to a
    stub digest naming the failure on the wake turn, never abort run_arena."""
    from civ_mcp.arena.attention import AttentionState, save_attention_state
    from civ_mcp.arena.config import AttentionOptions

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="auto", max_streak=1))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="r8", puppet_ids=[1])
    save_attention_state(str(tmp_path), "r8", 1, AttentionState(
        run_id="r8", player_id=1,
        streak=1,  # meets max_streak=1 -> STREAK_CAP wake with slept populated
        last_snapshot=dict(_ATTN_BASELINE_SNAPSHOT),
        last_scan={"at_war_with": [], "era_index": 1, "total_population": 12},
        slept=[{"snapshot": dict(_ATTN_BASELINE_SNAPSHOT),
                "task_notes": [], "notifications": []}]))  # no "turn" key

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert pol.calls == 1                     # the wake still happened
    # Review-3 f6: the DETAIL is lost, but the FACT of the sleep survives --
    # an empty block silently erased the whole recap.
    assert "WHILE YOU SLEPT" in pol.last_digest
    assert "digest unavailable" in pol.last_digest
    assert "1 turns" in pol.last_digest       # len(slept) == 1 in this fixture
    assert result["puppet_turns_played"] == 1
    assert sink.records[-1]["turn_kind"] == "played"


@pytest.mark.asyncio
async def test_tampered_snapshot_value_degrades_slept_delta(tmp_path):
    """Final-review pinhole: a wrong-TYPED snapshot value in the state file
    ("score": "high") passes load's dict-shape check and the delta triggers
    (which only read units/cities/gold), then TypeErrors in the slept-record
    delta -- must record state_delta None, never abort."""
    from civ_mcp.arena.attention import AttentionState, save_attention_state
    from civ_mcp.arena.config import AttentionOptions

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="auto"))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=3, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="r9", puppet_ids=[1])
    tampered = dict(_ATTN_BASELINE_SNAPSHOT)
    tampered["score"] = "high"
    save_attention_state(str(tmp_path), "r9", 1, AttentionState(
        run_id="r9", player_id=1,
        last_snapshot=tampered,
        last_scan={"at_war_with": [], "era_index": 1, "total_population": 12}))

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert pol.calls == 0                     # still a quiet sleep
    assert result["turns_slept"] == 1
    rec = sink.records[-1]
    assert rec["turn_kind"] == "slept"
    assert rec["state_delta"] is None         # unknowable delta, not an abort


@pytest.mark.asyncio
async def test_puppet_partial_post_snapshot_state_delta_none(
    monkeypatch, tmp_path
):
    from civ_mcp.arena import coordinator as coord

    before = dict(_ATTN_BASELINE_SNAPSHOT)
    after = dict(_ATTN_BASELINE_SNAPSHOT)
    del after["units"]
    snapshots = iter([before, after])

    async def fake_snapshot(_gs):
        return next(snapshots)

    monkeypatch.setattr(coord, "_overview_snapshot", fake_snapshot)
    conn = AttnConn()
    sink = FakeSink()
    opts = CivOptions()
    cfg = ArenaConfig(
        players=[PlayerSpec(1, "local", "m", options=opts)],
        max_puppet_turns=1,
        idle_poll_limit=5,
        transcript_dir=str(tmp_path),
        run_id="puppet-partial-after",
        puppet_ids=[1],
    )

    result = await run_arena(
        conn, FakeGSWithConn(conn), cfg, policy=CountingPolicy(opts), transcript=sink
    )

    assert result["puppet_turns_played"] == 1
    assert sink.records[-1]["state_delta"] is None


@pytest.mark.asyncio
async def test_corrupt_snapshot_resets_and_wakes_not_aborts(tmp_path):
    """Review-2 finding 1: a dict-shaped but wrong-typed persisted snapshot
    passes load's shape validation and used to explode inside evaluate()'s
    comparisons, killing run_arena. Contract: reset + wake (STATE_CORRUPT)."""
    from civ_mcp.arena.attention import (
        AttentionState, load_attention_state, save_attention_state,
    )
    from civ_mcp.arena.config import AttentionOptions

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="auto"))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="rc1", puppet_ids=[1])
    corrupt = dict(_ATTN_BASELINE_SNAPSHOT)
    corrupt["units"] = "5"          # int < str -> TypeError in _hard_triggers
    save_attention_state(str(tmp_path), "rc1", 1, AttentionState(
        run_id="rc1", player_id=1, last_snapshot=corrupt,
        last_scan={"at_war_with": [], "era_index": 1, "total_population": 12}))

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert pol.calls == 1                       # woke; the run did not die
    assert result["puppet_turns_played"] == 1
    rec = sink.records[-1]
    assert rec["attention"]["wake_cause"] == "STATE_CORRUPT"
    assert rec["attention"]["wake_detail"]  # exception repr recorded (review-3 f5)
    assert "Error" in rec["attention"]["wake_detail"]  # repr(e) carries the class name
    healed = load_attention_state(str(tmp_path), "rc1", 1)
    assert healed.last_snapshot is not None
    # note_wake rewrote the baseline from the post-turn overview snapshot
    # (_OV_AFTER's units field -- see FakeConnWithOverview above), not the
    # corrupt persisted "5" string -- the key assertion is that it's a real
    # int again, healing the file.
    assert healed.last_snapshot["units"] == 6


@pytest.mark.asyncio
async def test_corrupt_directive_resets_and_wakes_not_aborts(tmp_path):
    """Same contract for a corrupt directive: wake_if=5 is now caught by
    load_attention_state's value-type validation (review-3 f1) before
    evaluate() ever runs -- fresh state, NO_BASELINE wake, not abort. (The
    evaluate()-level TypeError backstop this used to exercise now only fires
    for state constructed outside load; see
    test_evaluate_raises_on_non_list_wake_if in test_attention.py.)"""
    from civ_mcp.arena.attention import AttentionState, save_attention_state
    from civ_mcp.arena.config import AttentionOptions

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="hybrid"))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="rc2", puppet_ids=[1])
    save_attention_state(str(tmp_path), "rc2", 1, AttentionState(
        run_id="rc2", player_id=1,
        directive={"skip": 2, "wake_if": 5},    # dict-shaped, corrupt value
        skips_remaining=2,
        last_snapshot=dict(_ATTN_BASELINE_SNAPSHOT),
        last_scan={"at_war_with": [], "era_index": 1, "total_population": 12}))

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert pol.calls == 1
    assert result["puppet_turns_played"] == 1
    rec = sink.records[-1]
    assert rec["attention"]["wake_cause"] == "NO_BASELINE"
    assert rec["attention"]["wake_detail"] == ""


@pytest.mark.asyncio
async def test_wake_baseline_is_post_play_with_transcripts_off(tmp_path, monkeypatch):
    """Review-2 finding 2: state_after was gated on _tx_on only, so with
    transcripts off note_wake stored the PRE-play snapshot as the next wake
    baseline -- the following quiet turn's hard triggers would compare
    against a state that predates the puppet's own actions."""
    from civ_mcp.arena import coordinator as coord
    from civ_mcp.arena.attention import load_attention_state
    from civ_mcp.arena.config import AttentionOptions

    calls = []
    async def fake_snapshot(_gs):
        calls.append(1)
        return {**_ATTN_BASELINE_SNAPSHOT, "units": 5 + len(calls)}
    monkeypatch.setattr(coord, "_overview_snapshot", fake_snapshot)

    conn = AttnConn(); gs = FakeGSWithConn(conn)
    opts = CivOptions(attention=AttentionOptions(mode="auto"))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, idle_poll_limit=5,
                      transcript_dir=str(tmp_path), run_id="rt2", puppet_ids=[1])

    # transcripts OFF; no seeded baseline -> NO_BASELINE wake
    result = await run_arena(conn, gs, cfg, policy=pol, transcript=None)

    assert result["puppet_turns_played"] == 1
    assert len(calls) == 2                       # before AND after now taken
    st = load_attention_state(str(tmp_path), "rt2", 1)
    assert st.last_snapshot["units"] == 7        # the POST-play (2nd) snapshot


@pytest.mark.asyncio
async def test_slept_turns_refill_idle_budget(tmp_path):
    """Review-2 finding 8: slept turns burned deadline_polls (never refilled)
    while leaving `remaining` untouched, so a quiet game could end far short
    of its budget. A captured turn is activity: it must refill the budget."""
    from civ_mcp.arena.attention import AttentionState, save_attention_state
    from civ_mcp.arena.config import AttentionOptions

    conn = AttnConn(); gs = FakeGSWithConn(conn); sink = FakeSink()
    opts = CivOptions(attention=AttentionOptions(mode="auto", max_streak=10))
    pol = CountingPolicy(opts)
    cfg = ArenaConfig(players=[PlayerSpec(1, "local", "m", options=opts)],
                      max_puppet_turns=1, max_game_turns=5, idle_poll_limit=3,
                      transcript_dir=str(tmp_path), run_id="r8", puppet_ids=[1])
    conn._polls = iter([
        ["LOCAL|1", f"TURN|{t}", "ACTIVE|true", "LAST|1"] for t in range(2, 9)
    ])
    save_attention_state(str(tmp_path), "r8", 1, AttentionState(
        run_id="r8", player_id=1,
        last_snapshot=dict(_ATTN_BASELINE_SNAPSHOT),
        last_scan={"at_war_with": [], "era_index": 1, "total_population": 12}))

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    # idle_poll_limit=3 < 5 slept turns: pre-fix the run died after 3 polls.
    assert result["turns_slept"] == 5     # stopped by max_game_turns, not the idle budget
    assert pol.calls == 0


# ---------------------------------------------------------------------------
# Task 5 (seat-0 piloting) — coordinator happy path & duplicate-play prevention
# ---------------------------------------------------------------------------


def seat0_poll(turn: int, *, active: bool = True) -> PuppetState:
    return PuppetState(
        local=0,
        turn=turn,
        active=False,
        last=None,
        seat0_active=active,
    )


def puppet_poll(player_id: int, turn: int) -> PuppetState:
    return PuppetState(
        local=player_id,
        turn=turn,
        active=True,
        last=0,
        seat0_active=False,
    )


class Seat0Harness:
    """Monkeypatches every hook/seat0 side effect at the coordinator import
    sites (the hook and seat0 module attributes the coordinator resolves at
    call time) and records one ordered event stream, so seat-0 orchestration
    tests assert sequencing rather than Lua string matching."""

    def __init__(self, monkeypatch, polls):
        self.events: list[tuple] = []
        self._polls = iter(polls)
        self._last_poll = None
        self.blockers: list[dict] = []      # served by the patched query_blockers
        # Ordered per-query blocker snapshots (Task 6). Each query_blockers call
        # pops the next snapshot; once exhausted it falls back to self.blockers.
        self.blocker_queue: list[list[dict]] = []
        self.anchor: dict | None = None     # canned save_recovery_anchor result

        async def fake_poll(conn):
            try:
                self._last_poll = next(self._polls)
            except StopIteration:
                pass  # repeat the final poll; deadline_polls bounds the loop
            st = self._last_poll
            self.events.append(("poll", st.local, st.turn, st.seat0_active))
            return st

        async def fake_inject(conn, ids):
            self.events.append(("inject", tuple(ids)))
            return ["HOOK_OK|true"]

        async def fake_finish_units(conn, pid):
            self.events.append(("finish_units", pid))
            return ["FINISHED|0"]

        async def fake_end_turn(conn):
            self.events.append(("end_turn",))
            return ["OK:TURN_ENDED"]

        async def fake_restore_local(conn, pid=0):
            self.events.append(("restore_local", pid))
            return [f"LOCAL|{pid}"]

        async def fake_disable(conn):
            self.events.append(("disable",))
            return ["DISABLED|true"]

        async def fake_query_blockers(conn):
            self.events.append(("query_blockers", conn.is_connected))
            if self.blocker_queue:
                return [dict(b) for b in self.blocker_queue.pop(0)]
            return [dict(b) for b in self.blockers]

        async def fake_save_anchor(conn, turn):
            self.events.append(("save_anchor", turn))
            if self.anchor is not None:
                return dict(self.anchor)
            return {"name": f"0_MCP_{turn:04d}", "ok": True, "result": "Saved"}

        # Capture the real sleep before the monkeypatch below replaces it.
        # asyncio.sleep is patched GLOBALLY, so fake_sleep must not call
        # asyncio.sleep itself (that would recurse into fake_sleep); it awaits
        # this captured reference instead.
        real_sleep = asyncio.sleep

        async def fake_sleep(_delay):
            self.events.append(("sleep",))
            # A real, zero-delay yield keeps the event loop turning so any
            # asyncio.wait_for(...) deadline wrapping the caller can still
            # fire, without adding meaningful wall-clock time to the tests.
            await real_sleep(0)

        monkeypatch.setattr(hook_mod, "poll", fake_poll)
        monkeypatch.setattr(hook_mod, "inject", fake_inject)
        monkeypatch.setattr(hook_mod, "finish_units", fake_finish_units)
        monkeypatch.setattr(hook_mod, "end_turn", fake_end_turn)
        monkeypatch.setattr(hook_mod, "restore_local", fake_restore_local)
        monkeypatch.setattr(hook_mod, "disable", fake_disable)
        monkeypatch.setattr(seat0_mod, "query_blockers", fake_query_blockers)
        monkeypatch.setattr(seat0_mod, "save_recovery_anchor", fake_save_anchor)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    def names(self) -> list[str]:
        return [e[0] for e in self.events]


class Seat0RecordingPolicy:
    """Records (player_id, turn, kwargs) per call into its own list AND the
    shared harness event stream (for cross-policy ordering assertions)."""

    provider = "local"
    model = "seat0-m"

    def __init__(self, harness, result=None, options=None, needs_exclusive_tuner=False):
        self._harness = harness
        self.result = result or {"summary": "seat0 turn complete", "actions": []}
        self.options = options or CivOptions()
        self.needs_exclusive_tuner = needs_exclusive_tuner
        self.calls = []

    async def __call__(self, gs, player_id, turn, **kwargs):
        self._harness.events.append(("policy", player_id, turn))
        self.calls.append((player_id, turn, kwargs))
        return self.result


class Seat0CapsConn(FakeConnWithOverview):
    """Overview-serving conn that also answers the capability snapshot; hook
    and seat0 traffic never reaches it (patched by Seat0Harness), so GameCore
    reads here are caps-only."""

    async def execute_read(self, lua, timeout=5.0):
        if "CAPS|" in lua:
            self.read_calls.append(lua)
            self._maybe_die()
            return [
                "CAPS|spies=0|government=1|religious_unit=0|gp_unit=0|corps=0"
                "|army=0|air=0|archaeology=0|great_works=1"
            ]
        return await super().execute_read(lua, timeout)


class EventSink(FakeSink):
    """FakeSink that also stamps writes into the harness event stream."""

    def __init__(self, harness):
        super().__init__()
        self._harness = harness

    def write(self, record):
        self._harness.events.append(
            ("record", record.get("player_id"), record.get("turn"))
        )
        super().write(record)


class SnapshotEventSink(EventSink):
    """Models the real JSONL sink by freezing a record at write time."""

    def write(self, record):
        import copy

        self._harness.events.append(
            ("record", record.get("player_id"), record.get("turn"))
        )
        self.records.append(copy.deepcopy(record))


def _seat0_cfg(tmp_path, *, players=None, run_id="seat0-run", **kwargs):
    kwargs.setdefault("max_puppet_turns", 1)
    kwargs.setdefault("idle_poll_limit", 8)
    kwargs.setdefault("puppet_ids", [])
    return ArenaConfig(
        players=players or [PlayerSpec(0, "local", "m")],
        run_id=run_id,
        transcript_dir=str(tmp_path),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_seat0_happy_path_single_play_then_terminal_advanced(monkeypatch, tmp_path):
    """Brief Step 2: active seat 0 on turn 7 → inactive on 7 → active on 8.
    One policy call, one end request, no restore in the seat-0 body, record
    written only after the observed turn change with terminal `advanced`."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),    # admission
        seat0_poll(7, active=False),   # AI processing after the end request
        seat0_poll(8, active=True),    # turn advanced -> terminalize
    ])
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(tmp_path, run_id="seat0-happy")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    # Policy 0 called exactly once, for turn 7 — no replay on either
    # post-request poll.
    assert [c[:2] for c in pol.calls] == [(0, 7)]
    # Normal memory/task/capability kwargs pass the existing signature gate.
    kwargs = pol.calls[0][2]
    assert "memory_block" in kwargs and "task_block" in kwargs
    assert kwargs["caps"]["government"] is True
    assert kwargs["caps"]["spies"] is False

    events, names = harness.events, harness.names()
    # Ordered seat-0 sequence: policy → blocker query → recovery anchor →
    # end_turn. With no units blocker, the mechanical pass does not finish units.
    i_policy = events.index(("policy", 0, 7))
    i_blockers = names.index("query_blockers")
    i_anchor = events.index(("save_anchor", 7))
    i_end = names.index("end_turn")
    assert i_policy < i_blockers < i_anchor < i_end
    assert "finish_units" not in names[i_policy:i_end]
    assert names.count("end_turn") == 1
    # No restore_local(0) in the seat-0 body: the only restore is the
    # human-safety handback in finally, after the final poll.
    restores = [i for i, e in enumerate(events) if e[0] == "restore_local"]
    poll_positions = [i for i, e in enumerate(events) if e[0] == "poll"]
    assert len(restores) == 1 and restores[0] > poll_positions[-1]

    # Pending record written exactly once, only AFTER the turn change (the
    # third poll), with terminal `advanced`.
    i_record = events.index(("record", 0, 7))
    assert i_record > poll_positions[2]
    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec["player_id"] == 0 and rec["turn"] == 7
    assert rec["turn_kind"] == "played"
    s0 = rec["seat0"]
    assert s0["terminal_state"] == "advanced"
    assert s0["normal"] == {
        "completed": True, "summary": "seat0 turn complete", "error": "",
    }
    assert s0["repair"] == {
        "attempted": False, "completed": False, "summary": "", "error": "",
    }
    assert s0["blocker_snapshots"] == [{"stage": "after_normal", "blockers": []}]
    assert s0["mechanical_cleanup"] == []
    assert s0["autosave"] == {"name": "0_MCP_0007", "attempts": []}
    assert s0["end_turn_requests"] == 1
    # Generic transcript payload synthesized even though the policy returned
    # no "transcript" key.
    assert rec["steps"] == [] and rec["step_count"] == 0
    assert rec["prompt_tokens"] == 0
    # state_after captured while seat 0 was still active (before end request).
    assert rec["state_before"]["gold"] == pytest.approx(100.0)
    assert rec["state_after"]["gold"] == pytest.approx(110.0)
    assert rec["state_delta"]["gold"] == pytest.approx(10.0)

    assert result["seat0_turns_played"] == 1
    assert result["puppet_turns_played"] == 0
    assert result["seat0_turns_failed"] == 0
    assert result["seat0_human_pending"] == 0


@pytest.mark.asyncio
async def test_seat0_scripted_provider_records_scripted_driver(monkeypatch, tmp_path):
    """Review fix: the record builders classified driver via
    provider.startswith('cli'), so a scripted seat was transcribed as
    'in_process' -- desyncing from PlayerSpec.driver_kind()'s 'scripted' and
    polluting analyze()'s local-driver metrics."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("scripted ok")])
    pol.provider = "scripted"
    cfg = _seat0_cfg(
        tmp_path,
        players=[PlayerSpec(0, "scripted", "seat0-smoke")],
        run_id="seat0-scripted-driver",
    )

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert result["seat0_turns_played"] == 1
    assert sink.records[0]["driver"] == "scripted"


@pytest.mark.asyncio
async def test_seat0_no_replay_while_same_turn_still_active(monkeypatch, tmp_path):
    """Duplicate-play prevention: post-request polls that still show seat 0
    ACTIVE on the same turn (engine lag) must not re-admit or replay the
    policy, and must not re-fire the end request (retry bounds are Task 7)."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=True),   # engine lag: still active after the request
        seat0_poll(7, active=True),
        seat0_poll(8, active=True),
    ])
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(tmp_path, run_id="seat0-lag")

    result = await run_arena(FakeConn(), FakeGS(), cfg, policy=pol)

    assert [c[:2] for c in pol.calls] == [(0, 7)]
    assert harness.names().count("end_turn") == 1
    assert result["seat0_turns_played"] == 1


@pytest.mark.asyncio
async def test_seat0_degraded_and_backward_polls_do_not_terminalize_or_replay(
    monkeypatch, tmp_path
):
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        PuppetState(local=-1, turn=-1, active=False, last=None, seat0_active=False),
        seat0_poll(6, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=False),
    ])
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(
        tmp_path, run_id="seat0-degraded-poll", max_puppet_turns=2
    )

    result = await run_arena(
        conn, FakeGSWithConn(conn), cfg, policy=pol, transcript=sink
    )

    assert [call[:2] for call in pol.calls] == [(0, 7)]
    assert result["seat0_turns_played"] == 1
    assert len(sink.records) == 1
    assert sink.records[0]["turn"] == 7
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"


@pytest.mark.asyncio
async def test_seat0_turn_regression_terminalizes_and_replays(monkeypatch, tmp_path):
    """A human loading an older save mid-drain: three consecutive backward
    polls terminalize the in-flight turn as `regressed` (turn_kind failed),
    and the rolled-back turn is re-admitted and replayed."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),    # admit turn 7
        seat0_poll(7, active=False),   # AI processing after the end request
        seat0_poll(5, active=False),   # regression sample 1 -> DEGRADED
        seat0_poll(5, active=False),   # regression sample 2 -> DEGRADED
        seat0_poll(5, active=False),   # regression sample 3 -> REGRESSED
        seat0_poll(5, active=True),    # re-admit the rolled-back turn 5
        seat0_poll(5, active=False),   # AI processing
        seat0_poll(6, active=True),    # turn 5 advanced
    ])
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("turn 7"), _returned("turn 5")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-regressed", max_puppet_turns=2)

    result = await run_arena(
        conn, FakeGSWithConn(conn), cfg, policy=pol, transcript=sink
    )

    assert [call[:2] for call in pol.calls] == [(0, 7), (0, 5)]
    assert result["seat0_turns_failed"] == 1
    assert result["seat0_turns_played"] == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "regressed"
    assert sink.records[0]["turn_kind"] == "failed"
    assert sink.records[1]["seat0"]["terminal_state"] == "advanced"
    events = [e for e in result["log"] if e.get("event") == "seat0_turn_regressed"]
    assert len(events) == 1
    assert events[0]["turn"] == 7
    assert events[0]["observed_turn"] == 5


@pytest.mark.asyncio
async def test_seat0_receives_memory_and_task_blocks_for_player_zero(monkeypatch, tmp_path):
    """Regression 1: seat 0 rides the existing standing-memory and task
    pipeline, keyed by player 0 — including pre-model task follow-through."""
    run_id = "seat0-mem"
    save_memory(str(tmp_path), run_id, 0, turn=6,
                text="finish the granary next.", max_chars=1200)
    task = UnitTask(
        task_id="settle:42", kind="settle", unit_id=42, target_x=5, target_y=5,
        created_turn=6, updated_turn=6,
    )
    save_task_state(str(tmp_path), run_id, 0, [task])
    harness = Seat0Harness(monkeypatch, [seat0_poll(7), seat0_poll(8)])
    gs = FakeGSWithUnit(unit_id=42, unit_index=7, x=5, y=5)
    opts = CivOptions(
        memory=MemoryOptions(enabled=True, max_chars=1200),
        task_tracker=TaskTrackerOptions(enabled=True, max_tasks=8),
    )
    pol = Seat0RecordingPolicy(harness, options=opts)
    cfg = _seat0_cfg(tmp_path, run_id=run_id)

    result = await run_arena(FakeConn(), gs, cfg, policy=pol)

    kwargs = pol.calls[0][2]
    assert kwargs["memory_block"].startswith("== STANDING PLAN (captured turn 6")
    assert "finish the granary next." in kwargs["memory_block"]
    assert kwargs["task_block"].startswith("== DETERMINISTIC TASK TRACKER ==")
    assert gs.found_city_calls == [7]   # pre-model follow-through ran for seat 0
    assert result["seat0_turns_played"] == 1


@pytest.mark.asyncio
async def test_seat0_exclusive_policy_reconnects_before_blocker_query(monkeypatch, tmp_path):
    """Regression 2: a needs_exclusive_tuner seat-0 policy gets the tuner
    released before the call and reclaimed before the blocker query."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7), seat0_poll(8)])
    conn = FakeConn()

    class ExclusiveSeat0Policy(Seat0RecordingPolicy):
        async def __call__(self, gs, player_id, turn, **kwargs):
            assert conn.is_connected is False   # tuner released for the CLI
            return await super().__call__(gs, player_id, turn, **kwargs)

    pol = ExclusiveSeat0Policy(harness, needs_exclusive_tuner=True)
    cfg = _seat0_cfg(
        tmp_path, players=[PlayerSpec(0, "cli-claude", "")], run_id="seat0-excl",
    )

    result = await run_arena(conn, FakeGS(), cfg, policy=pol)

    assert result["seat0_turns_played"] == 1
    assert [c[:2] for c in pol.calls] == [(0, 7)]
    # Blocker query ran only after the tuner was reclaimed.
    assert [e for e in harness.events if e[0] == "query_blockers"] == [
        ("query_blockers", True)
    ]


@pytest.mark.asyncio
async def test_seat0_skips_promotion_sweep(monkeypatch, tmp_path):
    """Regression 3: promotion choices belong to the seat-0 policy; the
    coordinator's autoresolve sweep must never run for seat 0."""
    sweep_calls = []

    async def recording_sweep(_gs):
        sweep_calls.append(1)
        return []

    monkeypatch.setattr(autoresolve, "sweep_promotions", recording_sweep)
    harness = Seat0Harness(monkeypatch, [seat0_poll(7), seat0_poll(8)])
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(tmp_path, run_id="seat0-sweep")

    result = await run_arena(FakeConn(), FakeGS(), cfg, policy=pol)

    assert result["seat0_turns_played"] == 1
    assert sweep_calls == []


@pytest.mark.asyncio
async def test_active_puppet_serviced_before_seat0_admission(monkeypatch, tmp_path):
    """Regression 4: a poll where a puppet actually holds the capture is
    serviced first, even when seat 0 also reads active on that same poll."""
    puppet_first = PuppetState(local=1, turn=7, active=True, last=1, seat0_active=True)
    harness = Seat0Harness(monkeypatch, [puppet_first, seat0_poll(7), seat0_poll(8)])
    seat0_pol = Seat0RecordingPolicy(harness)
    puppet_pol = Seat0RecordingPolicy(harness)
    policies = {0: seat0_pol, 1: puppet_pol}
    cfg = _seat0_cfg(
        tmp_path,
        players=[PlayerSpec(0, "local", "m"), PlayerSpec(1, "local", "m")],
        run_id="seat0-priority", max_puppet_turns=2, puppet_ids=[1],
    )

    result = await run_arena(
        FakeConn(), FakeGS(), cfg, policy_for=lambda pid: policies[pid]
    )

    assert [c[:2] for c in puppet_pol.calls] == [(1, 7)]
    assert [c[:2] for c in seat0_pol.calls] == [(0, 7)]
    policy_events = [e for e in harness.events if e[0] == "policy"]
    assert policy_events == [("policy", 1, 7), ("policy", 0, 7)]
    assert result["puppet_turns_played"] == 1
    assert result["seat0_turns_played"] == 1


@pytest.mark.asyncio
async def test_no_seat0_spec_keeps_puppet_only_behavior(monkeypatch, tmp_path):
    """Regression 5: without a seat-0 PlayerSpec, an active seat 0 is never
    admitted — no end request fires and puppet servicing is unchanged."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),   # active seat 0, nobody configured to pilot it
        puppet_poll(1, 7),
        seat0_poll(7, active=True),
    ])
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(
        tmp_path,
        players=[PlayerSpec(1, "local", "m")],
        run_id="seat0-none", puppet_ids=[1], idle_poll_limit=3,
    )

    result = await run_arena(FakeConn(), FakeGS(), cfg, policy=pol)

    assert [c[:2] for c in pol.calls] == [(1, 7)]
    assert result["puppet_turns_played"] == 1
    assert result.get("seat0_turns_played", 0) == 0
    assert "end_turn" not in harness.names()


# ---------------------------------------------------------------------------
# Task 6 (seat-0 piloting) — one-shot repair & bounded human escape
# ---------------------------------------------------------------------------


def _blocker(blocking_type: str, message: str = "") -> dict:
    return {"type": blocking_type, "message": message or f"msg-{blocking_type}"}


_RESEARCH = _blocker("ENDTURN_BLOCKING_RESEARCH", "Choose Research")
_GOVERNOR = _blocker("ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT", "Appoint a governor")


class Seat0ScriptPolicy:
    """Seat-0 policy whose per-call behaviour is scripted: each element of
    ``behaviors`` is either a dict (returned) or a BaseException instance
    (raised). Records (player_id, turn, kwargs) per call and stamps a
    ``policy`` event into the shared harness stream."""

    provider = "local"
    model = "seat0-m"

    def __init__(self, harness, behaviors, *, options=None,
                 needs_exclusive_tuner=False):
        self._harness = harness
        self._behaviors = list(behaviors)
        self.options = options or CivOptions()
        self.needs_exclusive_tuner = needs_exclusive_tuner
        self.calls = []

    async def __call__(self, gs, player_id, turn, **kwargs):
        idx = len(self.calls)
        self._harness.events.append(("policy", player_id, turn))
        self.calls.append((player_id, turn, kwargs))
        behavior = self._behaviors[idx] if idx < len(self._behaviors) else self._behaviors[-1]
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior


def _returned(summary: str) -> dict:
    return {"summary": summary, "actions": []}


def _critical_events(result) -> list[dict]:
    return [e for e in result["log"] if e.get("event") == "seat0_human_pending"]


@pytest.mark.asyncio
async def test_seat0_units_blocker_finishes_once_per_mechanical_pass(
    monkeypatch, tmp_path
):
    units = _blocker("ENDTURN_BLOCKING_UNITS", "Units need orders")
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    harness.blocker_queue = [[units], []]
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])

    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _seat0_cfg(tmp_path, run_id="seat0-one-finish"),
        policy=pol,
        transcript=sink,
    )

    assert result["seat0_turns_played"] == 1
    assert harness.names().count("finish_units") == 1
    assert sink.records[0]["seat0"]["mechanical_cleanup"] == [{
        "type": "ENDTURN_BLOCKING_UNITS",
        "action": "finish_units",
        "result": "requested",
    }]


# --- Step 1: decision blocker triggers exactly one focused repair -----------


@pytest.mark.asyncio
async def test_seat0_policy_without_blocker_kwarg_is_not_called_unfocused(
    monkeypatch, tmp_path
):
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    harness.blocker_queue = [[_RESEARCH], [_RESEARCH]]
    sink = EventSink(harness)

    class LegacyPolicy:
        provider = "local"
        model = "legacy"
        options = CivOptions()

        def __init__(self):
            self.calls = 0

        async def __call__(self, gs, player_id, turn):
            self.calls += 1
            return {"summary": "normal returned", "actions": []}

    pol = LegacyPolicy()
    result = await run_arena(
        FakeConn(),
        FakeGS(),
        _seat0_cfg(tmp_path, run_id="seat0-legacy-repair", idle_poll_limit=3),
        policy=pol,
        transcript=sink,
    )

    assert pol.calls == 1
    assert result["seat0_human_pending"] == 1
    assert sink.records[0]["seat0"]["repair"]["attempted"] is False
    assert "required blocker_block keyword" in sink.records[0]["seat0"]["repair"]["error"]


@pytest.mark.asyncio
async def test_seat0_incompatible_recheck_repair_is_not_called_unfocused(
    monkeypatch, tmp_path
):
    harness = Seat0Harness(
        monkeypatch,
        [seat0_poll(7, active=True)] * 7,
    )
    harness.blocker_queue = [[], [_RESEARCH], [_RESEARCH]]
    sink = EventSink(harness)

    class PositionalOnlyPolicy:
        provider = "local"
        model = "positional-only"
        options = CivOptions()

        def __init__(self):
            self.calls = 0

        async def __call__(self, gs, player_id, turn, blocker_block="", /):
            self.calls += 1
            return {"summary": "normal returned", "actions": []}

    pol = PositionalOnlyPolicy()
    result = await run_arena(
        FakeConn(),
        FakeGS(),
        _seat0_cfg(tmp_path, run_id="seat0-recheck-incompatible", idle_poll_limit=8),
        policy=pol,
        transcript=sink,
    )

    assert pol.calls == 1
    assert result["seat0_human_pending"] == 1
    assert harness.names().count("end_turn") == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "human_pending"
    assert sink.records[0]["seat0"]["repair"]["attempted"] is False
    assert "required blocker_block keyword" in sink.records[0]["seat0"]["repair"]["error"]


@pytest.mark.asyncio
async def test_seat0_decision_blocker_triggers_one_repair(monkeypatch, tmp_path):
    """RESEARCH -> RESEARCH -> []: normal returns, mechanical cleanup does not
    choose a tech, and the second query drives a single focused repair that
    receives only blocker_block + caps and clears the turn."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    harness.blocker_queue = [[_RESEARCH], [_RESEARCH], []]
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [
        _returned("normal done"),
        _returned("repair done"),
    ])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-decision")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    # Same policy object, called twice, both for the SAME game turn (one
    # logical turn, not a second admission).
    assert [c[:2] for c in pol.calls] == [(0, 7), (0, 7)]
    # Normal call rides the full injected-kwarg set.
    normal_kwargs = pol.calls[0][2]
    assert "memory_block" in normal_kwargs and "task_block" in normal_kwargs
    assert normal_kwargs["caps"]["government"] is True
    # Repair call gets ONLY blocker_block + caps -- no memory/task/digest/briefing.
    repair_kwargs = pol.calls[1][2]
    assert set(repair_kwargs) == {"blocker_block", "caps"}
    assert repair_kwargs["blocker_block"] == seat0_mod.build_blocker_block([_RESEARCH])
    assert "Prior policy error" not in repair_kwargs["blocker_block"]
    assert repair_kwargs["caps"] is normal_kwargs["caps"]

    # One end request; the turn plays.
    assert harness.names().count("end_turn") == 1
    assert result["seat0_turns_played"] == 1
    assert result["seat0_turns_failed"] == 0
    assert result["seat0_human_pending"] == 0

    rec = sink.records[0]
    assert rec["turn_kind"] == "played"
    s0 = rec["seat0"]
    assert s0["terminal_state"] == "advanced"
    assert s0["normal"]["completed"] is True and s0["normal"]["summary"] == "normal done"
    assert s0["repair"] == {
        "attempted": True, "completed": True, "summary": "repair done", "error": "",
    }
    assert [snap["stage"] for snap in s0["blocker_snapshots"]] == [
        "after_normal", "after_normal_cleanup", "after_repair",
    ]
    assert s0["blocker_snapshots"][-1]["blockers"] == []


@pytest.mark.asyncio
async def test_seat0_repair_does_not_rerun_pre_model_tasks(monkeypatch, tmp_path):
    """Pre-model task follow-through runs once per logical turn, not again for
    the repair pass."""
    run_id = "seat0-repair-tasks"
    task = UnitTask(
        task_id="settle:42", kind="settle", unit_id=42, target_x=5, target_y=5,
        created_turn=6, updated_turn=6,
    )
    save_task_state(str(tmp_path), run_id, 0, [task])
    harness = Seat0Harness(monkeypatch, [seat0_poll(7), seat0_poll(8)])
    # after_normal -> after_normal_cleanup (governor persists) -> repair -> clear
    harness.blocker_queue = [[_GOVERNOR], [_GOVERNOR], []]
    gs = FakeGSWithUnit(unit_id=42, unit_index=7, x=5, y=5)
    opts = CivOptions(task_tracker=TaskTrackerOptions(enabled=True, max_tasks=8))
    pol = Seat0ScriptPolicy(harness, [
        _returned("normal"), _returned("repair"),
    ], options=opts)
    cfg = _seat0_cfg(tmp_path, run_id=run_id)

    result = await run_arena(FakeConn(), gs, cfg, policy=pol)

    assert [c[:2] for c in pol.calls] == [(0, 7), (0, 7)]  # repair happened
    assert gs.found_city_calls == [7]                      # settle ran exactly once
    assert result["seat0_turns_played"] == 1


# --- Step 2: policy-error matrix -------------------------------------------


@pytest.mark.asyncio
async def test_seat0_matrix_raise_then_repair_returns_plays(monkeypatch, tmp_path):
    """raises -> returns -> played: the repair block carries the prior error
    and the pilot inspects/finishes the turn; blockers clear -> end request."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    harness.blocker_queue = [[]]  # post-repair query only (raise skips normal pass)
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [
        RuntimeError("gateway unavailable"),
        _returned("repair recovered"),
    ])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-raise-return")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert [c[:2] for c in pol.calls] == [(0, 7), (0, 7)]
    repair_block = pol.calls[1][2]["blocker_block"]
    assert repair_block.startswith(
        "== END-TURN REPAIR ==\nPrior policy error: gateway unavailable"
    )
    assert "inspect" in repair_block.lower() or "finish" in repair_block.lower()
    assert harness.names().count("end_turn") == 1
    assert result["seat0_turns_played"] == 1
    assert result["seat0_turns_failed"] == 0
    assert result["seat0_human_pending"] == 0
    rec = sink.records[0]
    assert rec["turn_kind"] == "played"
    assert rec["seat0"]["normal"]["completed"] is False
    assert "gateway unavailable" in rec["seat0"]["normal"]["error"]
    assert rec["seat0"]["repair"]["completed"] is True


@pytest.mark.asyncio
async def test_seat0_matrix_returns_then_repair_raises_human_pending(monkeypatch, tmp_path):
    """returns -> raises -> human_pending, turn_kind played (a call returned)."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=True),
        seat0_poll(7, active=True),
    ])
    harness.blocker_queue = [[_RESEARCH], [_RESEARCH]]
    conn = FakeConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [
        _returned("normal ok"),
        RuntimeError("repair blew up"),
    ])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-return-raise", idle_poll_limit=4)

    result = await run_arena(conn, FakeGS(), cfg, policy=pol, transcript=sink)

    assert [c[:2] for c in pol.calls] == [(0, 7), (0, 7)]
    assert harness.names().count("end_turn") == 0
    assert result["seat0_human_pending"] == 1
    assert result["seat0_turns_failed"] == 0
    assert result["seat0_turns_played"] == 0
    rec = sink.records[0]
    assert rec["turn_kind"] == "played"
    assert rec["seat0"]["terminal_state"] == "human_pending"
    assert rec["seat0"]["repair"]["attempted"] is True
    assert "repair blew up" in rec["seat0"]["repair"]["error"]
    crit = _critical_events(result)
    assert len(crit) == 1
    assert crit[0]["turn"] == 7


@pytest.mark.asyncio
async def test_seat0_matrix_raise_then_repair_raises_failed(monkeypatch, tmp_path):
    """raises -> raises -> failed + human_pending (neither call returned)."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=True),
    ])
    conn = FakeConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [
        RuntimeError("gateway unavailable"),
        RuntimeError("still down"),
    ])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-raise-raise", idle_poll_limit=3)

    result = await run_arena(conn, FakeGS(), cfg, policy=pol, transcript=sink)

    assert [c[:2] for c in pol.calls] == [(0, 7), (0, 7)]
    assert harness.names().count("end_turn") == 0
    assert result["seat0_turns_failed"] == 1
    assert result["seat0_human_pending"] == 1
    assert result["seat0_turns_played"] == 0
    rec = sink.records[0]
    assert rec["turn_kind"] == "failed"
    assert rec["seat0"]["terminal_state"] == "human_pending"
    assert rec["seat0"]["normal"]["completed"] is False
    assert rec["seat0"]["repair"]["completed"] is False
    crit = _critical_events(result)
    assert len(crit) == 1
    assert "gateway unavailable" in crit[0]["policy_errors"]["normal"]
    assert "still down" in crit[0]["policy_errors"]["repair"]


@pytest.mark.asyncio
async def test_seat0_timeout_shaped_attempts_count_failed_not_played(monkeypatch, tmp_path):
    """Review fix: a timed-out CLI attempt RETURNS a normal-shaped result
    (transcript.reason == 'timeout', zero steps) instead of raising. Both
    attempts timing out means zero usable work happened -- the turn must be
    accounted `failed` (like raised attempts), never transcribed as a played
    zero-step turn with an automatic end request."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    timeout_result = {
        "summary": "cli timeout after 900s", "actions": [], "usage": {},
        "transcript": {
            "steps": [], "reason": "timeout",
            "final_summary": "cli timeout after 900s",
        },
    }
    pol = Seat0ScriptPolicy(harness, [dict(timeout_result), dict(timeout_result)])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-timeout", idle_poll_limit=6)

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert harness.names().count("end_turn") == 0  # zero-work turn never ends itself
    assert result["seat0_human_pending"] == 1
    assert result["seat0_turns_failed"] == 1
    assert result["seat0_turns_played"] == 0
    rec = sink.records[0]
    assert rec["turn_kind"] == "failed"
    assert rec["seat0"]["normal"]["completed"] is False
    assert rec["seat0"]["repair"]["completed"] is False
    assert "timeout" in rec["seat0"]["normal"]["error"]
    assert "timeout" in rec["seat0"]["repair"]["error"]


# --- Step 3: hard blocks and unresolved-after-repair ------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("hard_type", [
    "UNKNOWN",
    "ENDTURN_BLOCKING_SOME_FUTURE_TYPE",
])
async def test_seat0_hard_block_goes_straight_to_human_pending(monkeypatch, tmp_path, hard_type):
    """A hard/inaccessible blocker enters human_pending WITHOUT a repair call."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=True),
    ])
    hard = _blocker(hard_type, "hard block")
    harness.blocker_queue = [[hard], [hard]]
    conn = FakeConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-hard", idle_poll_limit=3)

    result = await run_arena(conn, FakeGS(), cfg, policy=pol, transcript=sink)

    assert [c[:2] for c in pol.calls] == [(0, 7)]  # NO repair call
    assert harness.names().count("end_turn") == 0
    assert result["seat0_human_pending"] == 1
    assert result["seat0_turns_failed"] == 0
    rec = sink.records[0]
    assert rec["turn_kind"] == "played"
    assert rec["seat0"]["terminal_state"] == "human_pending"
    assert rec["seat0"]["repair"]["attempted"] is False
    crit = _critical_events(result)
    assert len(crit) == 1
    assert hard_type in crit[0]["blockers"]


@pytest.mark.asyncio
async def test_seat0_decision_persists_after_repair_human_pending(monkeypatch, tmp_path):
    """A supported decision blocker still present after the one repair enters
    human_pending -- no second repair call."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=True),
    ])
    harness.blocker_queue = [[_RESEARCH], [_RESEARCH], [_GOVERNOR], [_GOVERNOR]]
    conn = FakeConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [
        _returned("normal"), _returned("repair"),
    ])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-persist", idle_poll_limit=3)

    result = await run_arena(conn, FakeGS(), cfg, policy=pol, transcript=sink)

    assert [c[:2] for c in pol.calls] == [(0, 7), (0, 7)]  # exactly one repair
    assert result["seat0_human_pending"] == 1
    assert result["seat0_turns_played"] == 0
    rec = sink.records[0]
    assert rec["turn_kind"] == "played"
    assert rec["seat0"]["terminal_state"] == "human_pending"
    assert rec["seat0"]["repair"]["attempted"] is True
    crit = _critical_events(result)
    assert len(crit) == 1
    assert "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT" in crit[0]["blockers"]


@pytest.mark.asyncio
async def test_seat0_human_pending_repeated_polls_do_not_recall(monkeypatch, tmp_path):
    """Repeated polls of the same human-pending turn do not re-invoke the
    policy, cleanup, blocker queries, or end-turn, and log exactly one
    CRITICAL event."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(7, active=True),
        seat0_poll(7, active=True),
    ])
    hard = _blocker("UNKNOWN", "??")
    harness.blocker_queue = [[hard], [hard]]
    conn = FakeConn()
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-repoll", idle_poll_limit=6)

    result = await run_arena(conn, FakeGS(), cfg, policy=pol)

    names = harness.names()
    assert names.count("policy") == 1
    assert names.count("query_blockers") == 2       # both from the single attempt
    assert names.count("end_turn") == 0
    assert len(_critical_events(result)) == 1
    assert result["seat0_human_pending"] == 1


# --- Step 4: human-resume -------------------------------------------------


@pytest.mark.asyncio
async def test_seat0_human_pending_record_once_then_resets_after_advance(monkeypatch, tmp_path):
    """Pending record is written once at human_pending entry, is not
    duplicated when the human advances, and the next seat-0 turn is admitted
    only because both budgets remain."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),    # admit 7 -> hard block -> human_pending
        seat0_poll(7, active=True),    # human still on 7 (drain)
        seat0_poll(8, active=True),    # human advanced -> reset -> admit 8
        seat0_poll(8, active=False),   # AI processing for turn 8
        seat0_poll(9, active=True),    # turn 8 advanced
    ])
    hard = _blocker("UNKNOWN", "??")
    harness.blocker_queue = [[hard], [hard], []]  # t7: 2 queries, t8: 1 query
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [
        _returned("turn 7"), _returned("turn 8"),
    ])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-resume", max_puppet_turns=2)

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert [c[:2] for c in pol.calls] == [(0, 7), (0, 8)]
    assert result["seat0_human_pending"] == 1
    assert result["seat0_turns_played"] == 1
    # Two records: turn 7 human_pending (written once at entry), turn 8 advanced.
    t7 = [r for r in sink.records if r["turn"] == 7]
    t8 = [r for r in sink.records if r["turn"] == 8]
    assert len(t7) == 1 and t7[0]["seat0"]["terminal_state"] == "human_pending"
    assert len(t8) == 1 and t8[0]["seat0"]["terminal_state"] == "advanced"
    assert harness.names().count("end_turn") == 1   # only the turn-8 play


@pytest.mark.asyncio
async def test_seat0_ai_processing_outlives_idle_poll_limit(monkeypatch, tmp_path):
    harness = Seat0Harness(
        monkeypatch,
        [seat0_poll(7, active=True)]
        + [seat0_poll(7, active=False)] * 7
        + [seat0_poll(8, active=True)],
    )
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(
        tmp_path, run_id="seat0-long-ai", idle_poll_limit=3
    )

    result = await run_arena(
        conn, FakeGSWithConn(conn), cfg, policy=pol, transcript=sink
    )

    assert result["seat0_turns_played"] == 1
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"


@pytest.mark.asyncio
async def test_seat0_human_pending_exits_after_human_pending_poll_limit(
    monkeypatch, tmp_path
):
    """If the human never advances, the drain exits once the DEDICATED
    human-pending budget is spent -- idle_poll_limit no longer bounds a
    human's decision window -- and a CRITICAL names the deadline."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    hard = _blocker("UNKNOWN", "??")
    harness.blocker_queue = [[hard], [hard]]
    conn = FakeConn()
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(
        tmp_path, run_id="seat0-idle", idle_poll_limit=5,
        seat0_human_pending_poll_limit=3,
    )

    result = await asyncio.wait_for(
        run_arena(conn, FakeGS(), cfg, policy=pol), timeout=5.0
    )

    # Entered human_pending and then quietly drained without further work.
    assert result["seat0_human_pending"] == 1
    assert harness.names().count("policy") == 1
    assert harness.names().count("end_turn") == 0
    assert harness.names().count("sleep") == cfg.seat0_human_pending_poll_limit
    # Human never advanced -> the pending turn is still counted exactly once.
    assert result["seat0_turns_played"] == 0
    events = [
        e for e in result["log"]
        if e.get("event") == "seat0_human_pending_deadline"
    ]
    assert len(events) == 1
    assert events[0]["turn"] == 7


@pytest.mark.asyncio
async def test_seat0_hung_ai_drain_exits_after_drain_poll_limit(
    monkeypatch, tmp_path
):
    """A hung AI turn (the turn number never advances) must not spin the
    arena forever: the drain cap breaks the loop, the finally terminalizes
    the record as `interrupted`, and a CRITICAL names the deadline."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),   # harness repeats this forever
    ])
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(
        tmp_path, run_id="seat0-hung-ai", seat0_drain_poll_limit=4
    )

    result = await asyncio.wait_for(
        run_arena(conn, FakeGSWithConn(conn), cfg, policy=pol, transcript=sink),
        timeout=5.0,
    )

    assert result["seat0_turns_played"] == 0
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "interrupted"
    assert sink.records[0]["turn_kind"] == "failed"
    events = [e for e in result["log"] if e.get("event") == "seat0_drain_deadline"]
    assert len(events) == 1
    assert events[0]["turn"] == 7


# ---------------------------------------------------------------------------
# Task 7 (seat-0 piloting) — end-turn retry bounds, drain budgets, autosave
# ordering, and append-only interruption
# ---------------------------------------------------------------------------


class _StrictAIWriteConn(Seat0CapsConn):
    """Records any execute_write issued while the most recent poll observed
    seat 0 inactive -- pins the no-InGame-during-AI-processing constraint."""

    def __init__(self, harness):
        super().__init__()
        self._harness = harness
        self.ai_phase_writes = []

    async def execute_write(self, lua, timeout=5.0):
        polls = [e for e in self._harness.events if e[0] == "poll"]
        if polls and polls[-1][3] is False:
            self.ai_phase_writes.append(lua)
        return await super().execute_write(lua, timeout)


class _OverviewRecordingConn(Seat0CapsConn):
    """Stamps an ('overview', n) harness event on each overview snapshot so the
    state_after snapshot's place in the operation order is assertable."""

    def __init__(self, harness):
        super().__init__()
        self._harness = harness
        self._n = 0

    async def execute_write(self, lua, timeout=5.0):
        if "Game.GetLocalPlayer" in lua:
            self._n += 1
            self._harness.events.append(("overview", self._n))
        return await super().execute_write(lua, timeout)


def _cancel_after_sleeps(harness, n):
    """A fake asyncio.sleep that records a ('sleep',) event then raises
    CancelledError on its n-th call (1-indexed), to cancel mid-drain."""
    state = {"count": 0}

    async def sleeper(_delay):
        state["count"] += 1
        harness.events.append(("sleep",))
        if state["count"] >= n:
            raise asyncio.CancelledError()

    return sleeper


# --- Step 1: exact grace/retry bound ---------------------------------------


@pytest.mark.asyncio
async def test_seat0_quiet_rechecks_one_guarded_refire_then_human_pending(
    monkeypatch, tmp_path
):
    """Full-LLM-control update to the review-fix contract: after the first
    end request, quiet rechecks (no blocker, no bounce proof, no open
    session) wait out the bounded budget as before -- but exhausting it with
    seat 0 STILL active on the same turn is now the strongest available
    evidence the request was dropped, so the coordinator spends ONE guarded
    refire (with a fresh recovery anchor) before any human escalation. A
    second exhaustion escalates to human_pending with exactly two requests
    ever fired -- the multi-turn-skip hazard stays bounded by the flag and
    the request cap."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    harness.blocker_queue = []  # every query returns [] (clear)
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal done")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-grace", idle_poll_limit=60)

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    names = harness.names()
    # The original request plus exactly one guarded refire.
    assert names.count("end_turn") == 2
    # The guarded refire re-saves the anchor first, like the bounce refire.
    assert names.count("save_anchor") == 2
    # One query for the played mech pass + one per recheck: 4 before the
    # guarded refire (3 quiet + the exhausting 4th) and 4 after it.
    assert names.count("query_blockers") == 9
    # The policy is never re-invoked (clear blockers -> no repair; no
    # session -> no diplomacy pass).
    assert names.count("policy") == 1
    # No InGame query during the five grace waits after the first end request.
    ev = harness.events
    i_end1 = next(i for i, e in enumerate(ev) if e[0] == "end_turn")
    i_query2 = next(
        i for i, e in enumerate(ev) if e[0] == "query_blockers" and i > i_end1
    )
    between = [e[0] for e in ev[i_end1 + 1:i_query2]]
    assert between.count("query_blockers") == 0
    assert between.count("sleep") == 5  # exactly five quiet grace waits

    refires = [e for e in result["log"] if e.get("event") == "seat0_guarded_refire"]
    assert len(refires) == 1
    assert result["seat0_human_pending"] == 1
    assert result["seat0_turns_played"] == 0
    assert result["seat0_turns_failed"] == 0
    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec["seat0"]["terminal_state"] == "human_pending"
    assert rec["seat0"]["end_turn_requests"] == 2
    assert rec["turn_kind"] == "played"


# --- Step 2: AI-phase execution context ------------------------------------


@pytest.mark.asyncio
async def test_seat0_ai_phase_issues_no_execute_write(monkeypatch, tmp_path):
    """Brief Step 2: once seat 0 goes inactive on the same turn (AI phase), the
    drain issues only hook.poll/sleep -- no blocker query, cleanup, autosave,
    overview snapshot, or end action -- until the turn number changes."""
    polls = (
        [seat0_poll(7, active=True)]
        + [seat0_poll(7, active=False)] * 6
        + [seat0_poll(8, active=True)]
    )
    harness = Seat0Harness(monkeypatch, polls)
    conn = _StrictAIWriteConn(harness)
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-aiphase", idle_poll_limit=12)

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert result["seat0_turns_played"] == 1
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    assert harness.names().count("end_turn") == 1
    # No execute_write (overview or otherwise) issued during the AI phase.
    assert conn.ai_phase_writes == []
    # The drain window (first inactive poll -> advancing poll) is poll/sleep only.
    ev = harness.events
    first_inactive = next(i for i, e in enumerate(ev) if e[0] == "poll" and e[3] is False)
    advance = next(i for i, e in enumerate(ev) if e[0] == "poll" and e[2] == 8)
    drain = ev[first_inactive:advance]
    assert all(e[0] in ("poll", "sleep") for e in drain), [e[0] for e in drain]


# --- Step 3: autosave ordering, failure, and re-save-before-refire ----------


@pytest.mark.asyncio
async def test_seat0_autosave_operation_order(monkeypatch, tmp_path):
    """Brief Step 3: policy -> blocker query/cleanup -> state_after snapshot
    -> save_game -> hook.end_turn."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    conn = _OverviewRecordingConn(harness)
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-order")

    await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    ev = harness.events
    i_policy = ev.index(("policy", 0, 7))
    i_query = next(i for i, e in enumerate(ev) if e[0] == "query_blockers")
    overviews = [i for i, e in enumerate(ev) if e[0] == "overview"]
    i_save = next(i for i, e in enumerate(ev) if e[0] == "save_anchor")
    i_end = next(i for i, e in enumerate(ev) if e[0] == "end_turn")
    # state_before is the first overview (before the policy call).
    assert overviews[0] < i_policy
    # state_after is the second overview, between the blocker query and save.
    assert i_policy < i_query < overviews[1] < i_save < i_end
    assert "finish_units" not in [e[0] for e in ev[i_policy:i_end]]


@pytest.mark.asyncio
async def test_seat0_autosave_exception_recorded_but_end_fires(monkeypatch, tmp_path):
    """A save exception is recorded in autosave.attempts and does not prevent
    the end request."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    harness.anchor = {"name": "0_MCP_0007", "ok": False, "error": "OSError('disk full')"}
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-savefail")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert harness.names().count("end_turn") == 1
    assert result["seat0_turns_played"] == 1
    s0 = sink.records[0]["seat0"]
    assert s0["autosave"]["attempts"] == [
        {"name": "0_MCP_0007", "ok": False, "error": "OSError('disk full')"}
    ]
    # Review fix: a failed save is never adopted as the recovery point.
    assert s0["autosave"]["name"] == ""


@pytest.mark.asyncio
async def test_seat0_autosave_save_may_have_failed_message_recorded(monkeypatch, tmp_path):
    """A reported save failure (ok=False, per the review fix save_game's OK|
    sentinel is authoritative) is recorded in attempts, is NOT adopted as the
    recovery-point name, and does not prevent the end request."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    harness.anchor = {
        "name": "0_MCP_0007", "ok": False, "result": "Save may have failed: timeout",
    }
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-savemsg")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert harness.names().count("end_turn") == 1
    assert result["seat0_turns_played"] == 1
    s0 = sink.records[0]["seat0"]
    assert len(s0["autosave"]["attempts"]) == 1
    assert "Save may have failed" in s0["autosave"]["attempts"][0]["result"]
    assert s0["autosave"]["name"] == ""  # a failed save is not a recovery point


@pytest.mark.asyncio
async def test_seat0_resave_before_refire_uses_same_name(monkeypatch, tmp_path):
    """Brief Step 3: a RECHECK re-fire re-saves the recovery anchor under the
    SAME 0_MCP_NNNN name before re-firing, and the re-save is recorded in
    autosave.attempts even on success. The recheck must find an open blocker
    (proof the previous request bounced) for a re-fire to happen at all."""
    harness = Seat0Harness(
        monkeypatch,
        [seat0_poll(7, active=True)] * 10 + [seat0_poll(8, active=True)],
    )
    units = _blocker("ENDTURN_BLOCKING_UNITS", "unit needs orders")
    # Played pass clear; the recheck finds proof of the bounce; cleanup clears.
    harness.blocker_queue = [[], [units], []]
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-resave", idle_poll_limit=20)

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    # Played end#1 saved once; at least one RECHECK re-fire re-saved.
    assert harness.names().count("save_anchor") >= 2
    assert result["seat0_turns_played"] == 1
    s0 = sink.records[0]["seat0"]
    assert len(s0["autosave"]["attempts"]) >= 1  # the re-save(s), recorded on success
    assert all(a["name"] == "0_MCP_0007" for a in s0["autosave"]["attempts"])
    assert s0["autosave"]["name"] == "0_MCP_0007"


@pytest.mark.asyncio
async def test_seat0_recheck_refires_only_after_proven_bounce(monkeypatch, tmp_path):
    """Review fix: a RECHECK that finds an open blocker HAS proof the end
    request bounced (the engine refuses to end the turn while a blocker is
    open), so after mechanical cleanup clears it, re-save + re-fire proceed;
    the re-fired request then advances the turn normally."""
    harness = Seat0Harness(
        monkeypatch,
        [seat0_poll(7, active=True)] * 8 + [seat0_poll(8, active=True)],
    )
    units = _blocker("ENDTURN_BLOCKING_UNITS", "unit needs orders")
    harness.blocker_queue = [[], [units], []]
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal done")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-bounce-refire", idle_poll_limit=30)

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    names = harness.names()
    assert names.count("end_turn") == 2  # original + one proven-bounce re-fire
    assert names.count("save_anchor") == 2  # initial + re-save before the re-fire
    assert result["seat0_turns_played"] == 1
    assert result["seat0_human_pending"] == 0
    rec = sink.records[0]
    assert rec["seat0"]["terminal_state"] == "advanced"
    assert rec["seat0"]["end_turn_requests"] == 2


# --- Step 4: final-budget hook disable --------------------------------------


@pytest.mark.asyncio
async def test_seat0_final_admission_disables_hook_before_end_turn(monkeypatch, tmp_path):
    """Brief Step 4: when seat 0 consumes the final admission (remaining == 0),
    the hook is disabled while seat 0 is still active and BEFORE the automatic
    end request; the finally disables again (idempotent) in reclaim -> restore
    0 -> disable order."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    pol = Seat0ScriptPolicy(harness, [_returned("ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-final", max_puppet_turns=1)

    result = await run_arena(conn, gs, cfg, policy=pol)

    names = harness.names()
    assert result["seat0_turns_played"] == 1
    assert names.index("disable") < names.index("end_turn")  # in-loop disable first
    assert names.count("disable") == 2  # in-loop + finally (flag-independent safety)
    assert names[-2:] == ["restore_local", "disable"]  # finally order, no reclaim


@pytest.mark.asyncio
async def test_seat0_final_admission_by_game_turns_disables_hook(monkeypatch, tmp_path):
    """Step 4: the game-turn cap is the other exhaustion dimension -- the final
    admitted turn disables the hook before its automatic end request."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    pol = Seat0ScriptPolicy(harness, [_returned("ok")])
    cfg = _seat0_cfg(
        tmp_path, run_id="seat0-gtcap", max_puppet_turns=5, max_game_turns=1,
    )

    result = await run_arena(conn, gs, cfg, policy=pol)

    names = harness.names()
    assert result["seat0_turns_played"] == 1
    assert names.index("disable") < names.index("end_turn")


@pytest.mark.asyncio
async def test_seat0_final_human_pending_disables_before_waiting(monkeypatch, tmp_path):
    """Step 4: a final admitted seat-0 turn going human_pending disables the
    hook before the human-pending drain (the human may advance into AI)."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    hard = _blocker("UNKNOWN", "??")
    harness.blocker_queue = [[hard], [hard]]
    conn = FakeConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(
        tmp_path, run_id="seat0-hp-final", max_puppet_turns=1, idle_poll_limit=4,
    )

    result = await run_arena(conn, FakeGS(), cfg, policy=pol, transcript=sink)

    names = harness.names()
    assert result["seat0_human_pending"] == 1
    assert names.count("end_turn") == 0
    i_first_disable = names.index("disable")
    i_record = next(i for i, e in enumerate(harness.events) if e[0] == "record")
    assert i_first_disable < i_record  # disabled before the human waits
    assert names.count("disable") == 2  # in-loop + finally


@pytest.mark.asyncio
async def test_puppet_final_slot_disables_hook_while_seat0_draining(monkeypatch, tmp_path):
    """Step 4: a puppet that spends the final slot while an earlier seat-0 turn
    is still draining disables the hook after servicing the puppet and before
    restoring/releasing it."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),  # admit seat 0, play, end#1 (remaining 2 -> 1)
        PuppetState(local=1, turn=7, active=True, last=0, seat0_active=False),
        seat0_poll(8, active=True),  # seat 0 advances
    ])
    seat0_pol = Seat0ScriptPolicy(harness, [_returned("seat0 ok")])
    puppet_pol = Seat0RecordingPolicy(
        harness, result={"summary": "puppet ok", "actions": []}
    )
    policies = {0: seat0_pol, 1: puppet_pol}
    cfg = _seat0_cfg(
        tmp_path,
        players=[PlayerSpec(0, "local", "m"), PlayerSpec(1, "local", "m")],
        run_id="seat0-puppet-final", max_puppet_turns=2, puppet_ids=[1],
        idle_poll_limit=8,
    )

    result = await run_arena(
        FakeConn(), FakeGS(), cfg, policy_for=lambda pid: policies[pid]
    )

    assert result["seat0_turns_played"] == 1
    assert result["puppet_turns_played"] == 1
    ev = harness.events
    i_pfinish = ev.index(("finish_units", 1))
    i_disable = next(i for i, e in enumerate(ev) if e[0] == "disable" and i > i_pfinish)
    i_prestore = next(
        i for i, e in enumerate(ev) if e == ("restore_local", 0) and i > i_disable
    )
    assert i_pfinish < i_disable < i_prestore


class _CapsAttnConn(Seat0CapsConn):
    """Caps-serving conn that also answers the InGame attention scan, so a
    seat-0 turn and an attention-slept puppet can share one fake conn."""

    async def execute_write(self, lua, timeout=5.0):
        if "ATTN" in lua:
            self.write_calls.append(lua)
            return list(QUIET_SCAN_LINES)
        return await super().execute_write(lua, timeout)


@pytest.mark.asyncio
async def test_slept_final_slot_disables_hook_while_seat0_draining(monkeypatch, tmp_path):
    """Review fix: a SLEPT puppet turn also increments game_turns, so it can
    be the turn that closes admission while an earlier seat-0 turn is still
    draining -- it must disable the hook before releasing the puppet, exactly
    like a played final-slot turn, or the next captured puppet is never
    serviced and the game freezes until the drain deadline."""
    from civ_mcp.arena.attention import AttentionState, save_attention_state
    from civ_mcp.arena.config import AttentionOptions

    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),  # admit seat 0, play, end#1 (game_turns 1)
        PuppetState(local=1, turn=7, active=True, last=0, seat0_active=False),
        seat0_poll(8, active=True),  # seat 0 advances
    ])
    conn = _CapsAttnConn()
    gs = FakeGSWithConn(conn)
    opts = CivOptions(attention=AttentionOptions(mode="auto"))
    seat0_pol = Seat0ScriptPolicy(harness, [_returned("seat0 ok")])
    puppet_pol = CountingPolicy(opts)
    policies = {0: seat0_pol, 1: puppet_pol}
    cfg = _seat0_cfg(
        tmp_path,
        players=[
            PlayerSpec(0, "local", "m"),
            PlayerSpec(1, "local", "m", options=opts),
        ],
        run_id="seat0-slept-final", max_puppet_turns=5, max_game_turns=2,
        puppet_ids=[1], idle_poll_limit=8,
    )
    save_attention_state(str(tmp_path), "seat0-slept-final", 1, AttentionState(
        run_id="seat0-slept-final", player_id=1,
        last_snapshot=dict(_ATTN_BASELINE_SNAPSHOT),
        last_scan={"at_war_with": [], "era_index": 1, "total_population": 12}))

    result = await run_arena(
        conn, gs, cfg, policy_for=lambda pid: policies[pid]
    )

    assert result["turns_slept"] == 1
    assert puppet_pol.calls == 0
    assert result["seat0_turns_played"] == 1
    ev = harness.events
    i_pfinish = ev.index(("finish_units", 1))
    i_disable = next(
        i for i, e in enumerate(ev) if e[0] == "disable" and i > i_pfinish
    )
    i_prestore = next(
        i for i, e in enumerate(ev) if e == ("restore_local", 0) and i > i_pfinish
    )
    assert i_pfinish < i_disable < i_prestore


# --- Step 5: interruption / cancellation transcript ------------------------


@pytest.mark.asyncio
async def test_seat0_cancel_during_normal_policy_propagates_and_cleans_up(monkeypatch, tmp_path):
    """Brief Step 5: CancelledError in the normal policy propagates; tuner
    reclaim/restore/disable still run. Task 7's interruption-safe skeleton
    means a record now exists before this cancel and is written
    `interrupted` (see test_seat0_cancel_during_policy_call_writes_interrupted_record
    for the record-shape assertions)."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [asyncio.CancelledError()])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-cancel-normal")

    with pytest.raises(asyncio.CancelledError):
        await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    names = harness.names()
    assert "restore_local" in names and "disable" in names
    assert len(sink.records) == 1  # Task 7: skeleton record written interrupted
    assert sink.records[0]["seat0"]["terminal_state"] == "interrupted"


@pytest.mark.asyncio
async def test_seat0_cancel_during_exclusive_repair_reclaims_and_cleans_up(monkeypatch, tmp_path):
    """Step 5: CancelledError during the one repair (exclusive tuner released
    for it) propagates; the finally reclaims the released tuner and still
    restores/disables. Task 7's interruption-safe skeleton means a record
    now exists (assigned before the normal attempt, well before the repair)
    and is written `interrupted`."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    harness.blocker_queue = [[_RESEARCH], [_RESEARCH]]
    conn = FakeConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(
        harness, [_returned("normal ok"), asyncio.CancelledError()],
        needs_exclusive_tuner=True,
    )
    cfg = _seat0_cfg(
        tmp_path, players=[PlayerSpec(0, "cli-claude", "")], run_id="seat0-cancel-repair",
    )

    with pytest.raises(asyncio.CancelledError):
        await run_arena(conn, FakeGS(), cfg, policy=pol, transcript=sink)

    names = harness.names()
    assert "restore_local" in names and "disable" in names
    assert conn.is_connected is True  # finally reclaimed the released tuner
    assert len(sink.records) == 1  # Task 7: skeleton record written interrupted
    assert sink.records[0]["seat0"]["terminal_state"] == "interrupted"


@pytest.mark.asyncio
async def test_seat0_cancel_during_grace_writes_interrupted_once(monkeypatch, tmp_path):
    """Step 5: cancel during a post-end grace wait -> the in-flight played
    record is terminalized `interrupted` exactly once, CancelledError
    propagates, tuner cleanup runs."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True), seat0_poll(7, active=True),
    ])
    harness.blocker_queue = []
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-cancel-grace", idle_poll_limit=8)
    monkeypatch.setattr(asyncio, "sleep", _cancel_after_sleeps(harness, 1))

    with pytest.raises(asyncio.CancelledError):
        await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec["seat0"]["terminal_state"] == "interrupted"
    # Review fix: the in-flight played record must not land as a counted
    # "played" turn -- the turn's outcome never materialized.
    assert rec["turn_kind"] == "failed"
    assert rec["player_id"] == 0 and rec["turn"] == 7
    names = harness.names()
    assert "restore_local" in names and "disable" in names


@pytest.mark.asyncio
async def test_seat0_cancel_during_ai_processing_writes_interrupted_once(monkeypatch, tmp_path):
    """Step 5: cancel during the AI-processing drain -> the in-flight record is
    written interrupted exactly once."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True), seat0_poll(7, active=False),
    ])
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-cancel-ai", idle_poll_limit=8)
    monkeypatch.setattr(asyncio, "sleep", _cancel_after_sleeps(harness, 1))

    with pytest.raises(asyncio.CancelledError):
        await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "interrupted"
    assert sink.records[0]["turn_kind"] == "failed"


@pytest.mark.asyncio
async def test_seat0_human_pending_not_duplicated_as_interrupted_on_cancel(monkeypatch, tmp_path):
    """Step 5: a record already written as human_pending is NOT rewritten as
    interrupted when a later cancel unwinds the drain."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True), seat0_poll(7, active=True),
    ])
    hard = _blocker("UNKNOWN", "??")
    harness.blocker_queue = [[hard], [hard]]
    conn = FakeConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-hp-cancel", idle_poll_limit=8)
    monkeypatch.setattr(asyncio, "sleep", _cancel_after_sleeps(harness, 1))

    with pytest.raises(asyncio.CancelledError):
        await run_arena(conn, FakeGS(), cfg, policy=pol, transcript=sink)

    assert len(sink.records) == 1  # written once at human_pending, no duplicate
    assert sink.records[0]["seat0"]["terminal_state"] == "human_pending"


@pytest.mark.asyncio
async def test_seat0_interrupted_write_failure_does_not_mask_cancellation(monkeypatch, tmp_path):
    """Step 9: a transcript failure while writing the interrupted record must
    neither mask the in-flight CancelledError nor skip the tuner handback."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True), seat0_poll(7, active=True),
    ])
    harness.blocker_queue = []
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)

    class BoomSink(EventSink):
        def write(self, record):
            self._harness.events.append(("record_attempt",))
            raise RuntimeError("transcript disk error")

    sink = BoomSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-boomsink", idle_poll_limit=8)
    monkeypatch.setattr(asyncio, "sleep", _cancel_after_sleeps(harness, 1))

    with pytest.raises(asyncio.CancelledError):
        await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    names = harness.names()
    assert "record_attempt" in names  # the interrupted write was attempted
    assert "restore_local" in names and "disable" in names  # handback still ran


@pytest.mark.asyncio
async def test_seat0_interrupted_write_cancellation_runs_full_handback(
    monkeypatch, tmp_path
):
    """A transcript-origin CancelledError during interrupted-record writing is
    retained, but cannot skip restore_local or hook.disable."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True), seat0_poll(7, active=True),
    ])
    conn = Seat0CapsConn()

    class CancelSink(EventSink):
        def write(self, record):
            self._harness.events.append(("record_attempt",))
            conn._connected = False
            raise asyncio.CancelledError("interrupted transcript write")

    sink = CancelSink(harness)
    cfg = _seat0_cfg(tmp_path, run_id="seat0-cancel-write", idle_poll_limit=8)
    monkeypatch.setattr(asyncio, "sleep", _cancel_after_sleeps(harness, 1))

    with pytest.raises(asyncio.CancelledError, match="interrupted transcript write"):
        await run_arena(
            conn,
            FakeGSWithConn(conn),
            cfg,
            policy=Seat0ScriptPolicy(harness, [_returned("normal ok")]),
            transcript=sink,
        )

    names = harness.names()
    assert "record_attempt" in names
    assert conn.is_connected is True
    assert "restore_local" in names and names.count("disable") == 2


@pytest.mark.asyncio
async def test_cleanup_later_cancellation_survives_earlier_ordinary_failure(
    monkeypatch, tmp_path
):
    """An ordinary restore failure is best-effort, but a later disable
    cancellation must still propagate after every cleanup step is attempted."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=False)])

    async def broken_restore(_conn, _pid=0):
        harness.events.append(("restore_local", 0))
        raise ConnectionError("restore unavailable")

    async def cancelled_disable(_conn):
        harness.events.append(("disable",))
        raise asyncio.CancelledError("disable interrupted")

    monkeypatch.setattr(hook_mod, "restore_local", broken_restore)
    monkeypatch.setattr(hook_mod, "disable", cancelled_disable)

    with pytest.raises(asyncio.CancelledError, match="disable interrupted"):
        await run_arena(
            Seat0CapsConn(),
            FakeGS(),
            _seat0_cfg(
                tmp_path, run_id="cleanup-interrupt-order", idle_poll_limit=1
            ),
            policy=Seat0ScriptPolicy(harness, []),
        )

    names = harness.names()
    assert "restore_local" in names and "disable" in names


@pytest.mark.asyncio
async def test_seat0_cleanup_exception_does_not_mask_seat0_cancellation(monkeypatch, tmp_path):
    """Carry-forward: an ordinary cleanup exception (a failing restore) must
    not replace the in-flight CancelledError from a seat-0 drain."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True), seat0_poll(7, active=True),
    ])
    harness.blocker_queue = []
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)

    async def boom_restore(conn_, pid=0):
        harness.events.append(("restore_local", pid))
        raise ConnectionError("restore failed")

    monkeypatch.setattr(hook_mod, "restore_local", boom_restore)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(tmp_path, run_id="seat0-cleanup-mask", idle_poll_limit=8)
    monkeypatch.setattr(asyncio, "sleep", _cancel_after_sleeps(harness, 1))

    with pytest.raises(asyncio.CancelledError):
        await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    # Interrupted record still written despite the later cleanup failure.
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "interrupted"
    names = harness.names()
    assert "restore_local" in names and "disable" in names


# --- Carry-forward: exclusive repair tuner handoff (non-cancel) -------------


@pytest.mark.asyncio
async def test_seat0_exclusive_repair_brackets_disconnect_reconnect(monkeypatch, tmp_path):
    """Carry-forward: an exclusive-tuner repair releases the tuner before the
    repair policy call and reclaims it afterward (regardless of outcome), so
    the post-repair blocker query runs on a live connection."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    harness.blocker_queue = [[_RESEARCH], [_RESEARCH], []]
    conn = FakeConn()
    conn_states = []

    class ExclusiveRepairPolicy(Seat0ScriptPolicy):
        async def __call__(self, gs, player_id, turn, **kwargs):
            conn_states.append(conn.is_connected)
            return await super().__call__(gs, player_id, turn, **kwargs)

    pol = ExclusiveRepairPolicy(
        harness, [_returned("normal"), _returned("repair")],
        needs_exclusive_tuner=True,
    )
    cfg = _seat0_cfg(
        tmp_path, players=[PlayerSpec(0, "cli-claude", "")], run_id="seat0-excl-repair",
    )

    result = await run_arena(conn, FakeGS(), cfg, policy=pol)

    assert [c[:2] for c in pol.calls] == [(0, 7), (0, 7)]  # normal + repair
    assert conn_states == [False, False]  # tuner released for both calls
    # Every blocker query (after_normal, after_normal_cleanup, after_repair)
    # ran on a reclaimed (connected) tuner.
    assert [e for e in harness.events if e[0] == "query_blockers"] == [
        ("query_blockers", True),
        ("query_blockers", True),
        ("query_blockers", True),
    ]
    assert result["seat0_turns_played"] == 1
    assert harness.names().count("end_turn") == 1


# ---------------------------------------------------------------------------
# Task 9 — ScriptedPolicy normal/repair determinism (test-only provider)
# ---------------------------------------------------------------------------


class _ScriptedGS:
    """Records every GameState call ScriptedPolicy makes and serves canned
    tech/civic/city/production data. Any read/action can be made to raise by
    adding its key to `raise_on`, so the exception-into-summary contract is
    exercised without a live game."""

    def __init__(self, *, techs=None, civics=None, cities=None, production=None):
        self.overview_calls = 0
        self.units_calls = 0
        self.skipped: list[int] = []
        self.research_set: list[str] = []
        self.civic_set: list[str] = []
        self.production_set: list[tuple] = []
        self.listed: list[int] = []
        self._techs = list(techs or [])
        self._civics = list(civics or [])
        self._cities = list(cities or [])
        self._production = dict(production or {})   # city_id -> [ProductionOption]
        self.raise_on: set[str] = set()

    async def get_game_overview(self):
        self.overview_calls += 1
        if "overview" in self.raise_on:
            raise RuntimeError("overview boom")
        return "OV"

    async def get_units(self):
        self.units_calls += 1
        return []

    async def skip_unit(self, i):
        self.skipped.append(i)
        return "SKIP"

    async def get_tech_civics(self):
        if "tech_civics" in self.raise_on:
            raise RuntimeError("tech_civics boom")
        return lq.TechCivicStatus(
            current_research="", current_research_turns=0,
            current_civic="", current_civic_turns=0,
            available_techs=list(self._techs),
            available_civics=list(self._civics),
        )

    async def get_cities(self):
        if "cities" in self.raise_on:
            raise RuntimeError("cities boom")
        return list(self._cities), []

    async def list_city_production(self, city_id):
        self.listed.append(city_id)
        if "list" in self.raise_on:
            raise RuntimeError("list boom")
        return list(self._production.get(city_id, []))

    async def set_research(self, tech):
        if "set_research" in self.raise_on:
            raise RuntimeError("set_research boom")
        self.research_set.append(tech)
        return f"RESEARCHING|{tech}"

    async def set_civic(self, civic):
        if "set_civic" in self.raise_on:
            raise RuntimeError("set_civic boom")
        self.civic_set.append(civic)
        return f"PROGRESSING|{civic}"

    async def set_city_production(self, city_id, item_type, item_name,
                                  target_x=None, target_y=None):
        if "set_prod" in self.raise_on:
            raise RuntimeError("set_prod boom")
        self.production_set.append((city_id, item_type, item_name, target_x, target_y))
        return f"PRODUCING|{item_name}"


def _tech(tech_type, turns):
    return lq.TechOption(name=tech_type, tech_type=tech_type, cost=0, progress_pct=0,
                         turns=turns, boosted=False, boost_desc="", unlocks="")


def _civic_opt(civic_type, turns):
    return lq.CivicOption(name=civic_type, civic_type=civic_type, cost=0, progress_pct=0,
                          turns=turns, boosted=False, boost_desc="")


def _prod(category, item_name, turns=5, *, is_repair=False, repair_x=None, repair_y=None):
    return lq.ProductionOption(category=category, item_name=item_name, cost=0, turns=turns,
                               gold_cost=-1, is_repair=is_repair,
                               repair_x=repair_x, repair_y=repair_y)


def _prod_city(city_id, building="NONE"):
    return lq.CityInfo(
        city_id=city_id, name=f"C{city_id}", x=0, y=0, population=1,
        food=0.0, production=0.0, gold=0.0, science=0.0, culture=0.0, faith=0.0,
        housing=0.0, amenities=0, turns_to_grow=0, currently_building=building,
    )


def _prod_block(*types):
    return seat0_mod.build_blocker_block([_blocker(t, f"choose {t}") for t in types])


_ALLOWED_TOOLS = {"skip_unit", "set_research", "set_civic", "set_city_production"}


@pytest.mark.asyncio
async def test_scripted_normal_observes_and_skips_without_strategic_choice():
    """NORMAL call (no blocker_block): observe overview + units, skip unit 0,
    and choose NO research/production so the probe blocker survives."""
    gs = _ScriptedGS(
        techs=[_tech("TECHNOLOGY_MINING", 3)],
        cities=[_prod_city(1)],
        production={1: [_prod("BUILDING", "BUILDING_MONUMENT")]},
    )
    result = await ScriptedPolicy()(gs, 0, 7)
    assert gs.overview_calls == 1 and gs.units_calls == 1
    assert gs.skipped == [0]
    assert gs.research_set == [] and gs.civic_set == [] and gs.production_set == []
    assert result["actions"] == [{"tool": "skip_unit"}]


@pytest.mark.asyncio
async def test_scripted_normal_ignores_injected_kwargs():
    """The seat-0 NORMAL path passes memory/task/caps kwargs; ScriptedPolicy
    absorbs them (signature-gated at the coordinator) and still chooses
    nothing strategic."""
    gs = _ScriptedGS()
    result = await ScriptedPolicy()(
        gs, 0, 7, memory_block="m", task_block="t", digest_block="d",
        caps={"government": True},
    )
    assert result["actions"] == [{"tool": "skip_unit"}]
    assert gs.research_set == [] and gs.production_set == []


@pytest.mark.asyncio
async def test_scripted_normal_skip_failure_reported_not_raised():
    class _NoUnitGS(_ScriptedGS):
        async def skip_unit(self, i):
            raise RuntimeError("no unit 0")

    gs = _NoUnitGS()
    result = await ScriptedPolicy()(gs, 0, 7)
    assert "skip failed" in result["summary"]
    assert result["actions"] == []


@pytest.mark.asyncio
async def test_scripted_repair_research_min_turns_then_type_name():
    """RESEARCH blocker: pick the available tech with key (turns, tech_type)."""
    gs = _ScriptedGS(techs=[
        _tech("TECHNOLOGY_POTTERY", 4),
        _tech("TECHNOLOGY_MINING", 3),
        _tech("TECHNOLOGY_ANIMAL_HUSBANDRY", 3),  # tie on turns → min type name wins
    ])
    result = await ScriptedPolicy()(gs, 0, 7,
                                    blocker_block=_prod_block("ENDTURN_BLOCKING_RESEARCH"))
    assert gs.research_set == ["TECHNOLOGY_ANIMAL_HUSBANDRY"]
    assert gs.civic_set == [] and gs.production_set == []
    tools = {a["tool"] for a in result["actions"]}
    assert tools == {"set_research"} and tools <= _ALLOWED_TOOLS


@pytest.mark.asyncio
async def test_scripted_repair_civic_only_when_named():
    """CIVIC blocker resolves a civic via set_civic; research untouched."""
    gs = _ScriptedGS(civics=[
        _civic_opt("CIVIC_CODE_OF_LAWS", 5),
        _civic_opt("CIVIC_CRAFTSMANSHIP", 4),
    ])
    await ScriptedPolicy()(gs, 0, 7, blocker_block=_prod_block("ENDTURN_BLOCKING_CIVIC"))
    assert gs.civic_set == ["CIVIC_CRAFTSMANSHIP"]  # min turns
    assert gs.research_set == []


@pytest.mark.asyncio
async def test_scripted_repair_research_and_civic_share_one_fetch():
    gs = _ScriptedGS(
        techs=[_tech("TECHNOLOGY_MINING", 3)],
        civics=[_civic_opt("CIVIC_CRAFTSMANSHIP", 2)],
    )
    await ScriptedPolicy()(
        gs, 0, 7,
        blocker_block=_prod_block("ENDTURN_BLOCKING_RESEARCH", "ENDTURN_BLOCKING_CIVIC"),
    )
    assert gs.research_set == ["TECHNOLOGY_MINING"]
    assert gs.civic_set == ["CIVIC_CRAFTSMANSHIP"]


@pytest.mark.asyncio
async def test_scripted_repair_production_prefers_named_over_cheaper_fallback():
    gs = _ScriptedGS(
        cities=[_prod_city(1, "NONE")],
        production={1: [
            _prod("BUILDING", "BUILDING_LIBRARY", turns=2),   # cheaper by turns
            _prod("BUILDING", "BUILDING_MONUMENT", turns=6),  # named-preferred wins
            _prod("UNIT", "UNIT_WARRIOR", turns=3),
        ]},
    )
    await ScriptedPolicy()(gs, 0, 7, blocker_block=_prod_block("ENDTURN_BLOCKING_PRODUCTION"))
    assert gs.listed == [1]
    assert gs.production_set == [(1, "BUILDING", "BUILDING_MONUMENT", None, None)]


@pytest.mark.asyncio
async def test_scripted_repair_production_named_priority_order():
    """Monument > Granary > Scout > Warrior when several are available."""
    gs = _ScriptedGS(
        cities=[_prod_city(1, "NONE")],
        production={1: [
            _prod("UNIT", "UNIT_WARRIOR"),
            _prod("BUILDING", "BUILDING_GRANARY"),
            _prod("UNIT", "UNIT_SCOUT"),
        ]},
    )
    await ScriptedPolicy()(gs, 0, 7, blocker_block=_prod_block("ENDTURN_BLOCKING_PRODUCTION"))
    assert gs.production_set == [(1, "BUILDING", "BUILDING_GRANARY", None, None)]


@pytest.mark.asyncio
async def test_scripted_repair_production_prefers_repair_over_named():
    """A pillaged-district repair carries its own coords and outranks Monument."""
    gs = _ScriptedGS(
        cities=[_prod_city(1, "NONE")],
        production={1: [
            _prod("BUILDING", "BUILDING_MONUMENT", turns=6),
            _prod("DISTRICT", "DISTRICT_CAMPUS", turns=4,
                  is_repair=True, repair_x=5, repair_y=6),
        ]},
    )
    await ScriptedPolicy()(gs, 0, 7, blocker_block=_prod_block("ENDTURN_BLOCKING_PRODUCTION"))
    assert gs.production_set == [(1, "DISTRICT", "DISTRICT_CAMPUS", 5, 6)]


@pytest.mark.asyncio
async def test_scripted_repair_production_never_selects_tile_district_or_project():
    """A new district needs a policy-chosen tile and a project is not a build;
    both are skipped. Only the tile-free option is chosen, with NO target."""
    gs = _ScriptedGS(
        cities=[_prod_city(1, "NONE")],
        production={1: [
            _prod("DISTRICT", "DISTRICT_CAMPUS", turns=1),    # needs a tile target
            _prod("PROJECT", "PROJECT_MANHATTAN", turns=1),   # not a tile-free build
            _prod("UNIT", "UNIT_SCOUT", turns=9),             # tile-free
        ]},
    )
    await ScriptedPolicy()(gs, 0, 7, blocker_block=_prod_block("ENDTURN_BLOCKING_PRODUCTION"))
    assert gs.production_set == [(1, "UNIT", "UNIT_SCOUT", None, None)]


@pytest.mark.asyncio
async def test_scripted_repair_production_fallback_by_turns_then_name():
    """With no named item present, fall back to (turns, item_name) among
    UNIT/BUILDING options only."""
    gs = _ScriptedGS(
        cities=[_prod_city(1, "NONE")],
        production={1: [
            _prod("BUILDING", "BUILDING_WATER_MILL", turns=5),
            _prod("BUILDING", "BUILDING_LIBRARY", turns=5),   # tie → LIBRARY < WATER_MILL
            _prod("UNIT", "UNIT_SLINGER", turns=8),
            _prod("DISTRICT", "DISTRICT_CAMPUS", turns=1),    # excluded (needs tile)
        ]},
    )
    await ScriptedPolicy()(gs, 0, 7, blocker_block=_prod_block("ENDTURN_BLOCKING_PRODUCTION"))
    assert gs.production_set == [(1, "BUILDING", "BUILDING_LIBRARY", None, None)]


@pytest.mark.asyncio
async def test_scripted_repair_production_only_empty_queue_cities():
    gs = _ScriptedGS(
        cities=[_prod_city(1, "BUILDING_MONUMENT"), _prod_city(2, "NONE")],
        production={2: [_prod("BUILDING", "BUILDING_GRANARY", turns=4)]},
    )
    await ScriptedPolicy()(gs, 0, 7, blocker_block=_prod_block("ENDTURN_BLOCKING_PRODUCTION"))
    assert gs.listed == [2]  # the busy city is never queried
    assert gs.production_set == [(2, "BUILDING", "BUILDING_GRANARY", None, None)]


@pytest.mark.asyncio
async def test_scripted_repair_resolves_only_named_blockers():
    """Only production is named → tech/civic are left untouched even though
    available data exists (so an unnamed blocker is never silently resolved)."""
    gs = _ScriptedGS(
        techs=[_tech("TECHNOLOGY_MINING", 1)],
        civics=[_civic_opt("CIVIC_CODE_OF_LAWS", 1)],
        cities=[_prod_city(1, "NONE")],
        production={1: [_prod("BUILDING", "BUILDING_MONUMENT", turns=6)]},
    )
    await ScriptedPolicy()(gs, 0, 7, blocker_block=_prod_block("ENDTURN_BLOCKING_PRODUCTION"))
    assert gs.production_set == [(1, "BUILDING", "BUILDING_MONUMENT", None, None)]
    assert gs.research_set == [] and gs.civic_set == []


@pytest.mark.asyncio
async def test_scripted_repair_unimplemented_blocker_returns_without_clearing():
    """A strategic blocker with no scripted resolver leaves game state
    untouched, so the coordinator correctly reaches human_pending after the
    single repair."""
    gs = _ScriptedGS(
        techs=[_tech("TECHNOLOGY_MINING", 1)],
        cities=[_prod_city(1, "NONE")],
        production={1: [_prod("BUILDING", "BUILDING_MONUMENT")]},
    )
    result = await ScriptedPolicy()(
        gs, 0, 7, blocker_block=_prod_block("ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT"),
    )
    assert gs.research_set == [] and gs.civic_set == [] and gs.production_set == []
    assert gs.listed == []
    assert result["actions"] == []


@pytest.mark.asyncio
async def test_scripted_repair_catches_read_exception_into_summary():
    gs = _ScriptedGS()
    gs.raise_on.add("tech_civics")
    result = await ScriptedPolicy()(gs, 0, 7,
                                    blocker_block=_prod_block("ENDTURN_BLOCKING_RESEARCH"))
    assert "error" in result["summary"].lower()
    assert gs.research_set == []  # no partial write, no raise into the coordinator


@pytest.mark.asyncio
async def test_scripted_repair_catches_action_exception_into_summary():
    gs = _ScriptedGS(cities=[_prod_city(1, "NONE")],
                     production={1: [_prod("BUILDING", "BUILDING_MONUMENT")]})
    gs.raise_on.add("set_prod")
    result = await ScriptedPolicy()(gs, 0, 7,
                                    blocker_block=_prod_block("ENDTURN_BLOCKING_PRODUCTION"))
    assert "error" in result["summary"].lower()
    assert result["actions"] == []


@pytest.mark.asyncio
async def test_scripted_repair_actions_stay_within_allowed_tools():
    """The repair never emits an end-turn (or any other) tool — only the
    deterministic research/civic/production choices."""
    gs = _ScriptedGS(
        techs=[_tech("TECHNOLOGY_MINING", 1)],
        civics=[_civic_opt("CIVIC_CRAFTSMANSHIP", 1)],
        cities=[_prod_city(1, "NONE")],
        production={1: [_prod("BUILDING", "BUILDING_MONUMENT")]},
    )
    result = await ScriptedPolicy()(
        gs, 0, 7,
        blocker_block=_prod_block(
            "ENDTURN_BLOCKING_RESEARCH",
            "ENDTURN_BLOCKING_CIVIC",
            "ENDTURN_BLOCKING_PRODUCTION",
        ),
    )
    tools = {a["tool"] for a in result["actions"]}
    assert tools == {"set_research", "set_civic", "set_city_production"}
    assert tools <= _ALLOWED_TOOLS


def test_scripted_policy_identity_is_scripted_seat0_smoke():
    pol = ScriptedPolicy()
    assert pol.provider == "scripted"
    assert pol.model == "seat0-smoke"


@pytest.mark.asyncio
async def test_seat0_transient_mechanical_failure_reconnects_and_continues(
    monkeypatch, tmp_path
):
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    calls = 0

    async def flaky_query(_conn):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("transient blocker query")
        return []

    monkeypatch.setattr(seat0_mod, "query_blockers", flaky_query)
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])

    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _seat0_cfg(tmp_path, run_id="seat0-mech-retry"),
        policy=pol,
        transcript=sink,
    )

    assert calls == 2
    assert result["seat0_turns_played"] == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    assert "transient blocker query" in sink.records[0]["seat0"]["automation_errors"][0]["error"]


@pytest.mark.asyncio
async def test_seat0_permanent_mechanical_failure_records_human_pending(
    monkeypatch, tmp_path
):
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])

    async def broken_query(_conn):
        raise ConnectionError("blocker query unavailable")

    monkeypatch.setattr(seat0_mod, "query_blockers", broken_query)
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _seat0_cfg(tmp_path, run_id="seat0-mech-hard", idle_poll_limit=3),
        policy=Seat0ScriptPolicy(harness, [_returned("normal ok")]),
        transcript=sink,
    )

    assert result["seat0_human_pending"] == 1
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "human_pending"
    assert len(sink.records[0]["seat0"]["automation_errors"]) == 2
    assert "end_turn" not in harness.names()


@pytest.mark.asyncio
async def test_seat0_mech_pass_does_not_retry_on_dead_reconnect(
    monkeypatch, tmp_path
):
    """When the reconnect after a failed blocker query cannot restore the
    tuner, the second query attempt must not run -- it is guaranteed to fail
    and would bury the original error in the automation blocker."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    calls = 0

    async def broken_query(_conn):
        nonlocal calls
        calls += 1
        raise ConnectionError("blocker query unavailable")

    monkeypatch.setattr(seat0_mod, "query_blockers", broken_query)

    class DeadReconnectConn(Seat0CapsConn):
        def __init__(self):
            super().__init__()
            self.connect_attempts = 0

        async def connect(self):
            self.connect_attempts += 1
            raise ConnectionError("tuner gone")

    conn = DeadReconnectConn()
    sink = EventSink(harness)
    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _seat0_cfg(tmp_path, run_id="seat0-mech-dead", idle_poll_limit=3),
        policy=Seat0ScriptPolicy(harness, [_returned("normal ok")]),
        transcript=sink,
    )

    assert calls == 1                      # no second doomed attempt
    # >= 5: the mech-pass reconnect makes 5 attempts; the finally block's
    # reclaim-retry step adds 5 more on the same dead conn.
    assert conn.connect_attempts >= 5
    assert result["seat0_human_pending"] == 1
    errors = sink.records[0]["seat0"]["automation_errors"]
    assert len(errors) == 1
    assert "blocker query unavailable" in errors[0]["error"]


@pytest.mark.asyncio
async def test_seat0_end_request_exception_keeps_polling_to_advance(
    monkeypatch, tmp_path
):
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(8, active=True),
    ])

    async def uncertain_end(_conn):
        harness.events.append(("end_turn",))
        raise ConnectionError("response lost after dispatch")

    monkeypatch.setattr(hook_mod, "end_turn", uncertain_end)
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _seat0_cfg(tmp_path, run_id="seat0-end-uncertain"),
        policy=Seat0ScriptPolicy(harness, [_returned("normal ok")]),
        transcript=sink,
    )

    assert result["seat0_turns_played"] == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    assert "response lost after dispatch" in sink.records[0]["seat0"]["end_turn_errors"][0]["error"]


@pytest.mark.asyncio
async def test_seat0_human_pending_drain_runs_orphan_sweep(
    monkeypatch, tmp_path
):
    """HUMAN_PENDING is a human-idle window: the orphan diplomacy sweep must
    keep firing on its usual idle cadence while the arena waits (an orphan
    puppet-to-puppet session can never be clicked by the human)."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    hard = _blocker("UNKNOWN", "??")
    harness.blocker_queue = [[hard], [hard]]
    sweeps = []

    async def fake_sweep(_conn):
        sweeps.append(True)
        return "ORPHANS|none"

    monkeypatch.setattr(coordinator_mod, "_sweep_orphan_sessions", fake_sweep)
    monkeypatch.setattr(coordinator_mod, "ORPHAN_SWEEP_IDLE_POLLS", 2)
    conn = FakeConn()
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])
    cfg = _seat0_cfg(
        tmp_path, run_id="seat0-hp-sweep", idle_poll_limit=5,
        seat0_human_pending_poll_limit=6,
    )

    result = await asyncio.wait_for(
        run_arena(conn, FakeGS(), cfg, policy=pol), timeout=5.0
    )

    assert result["seat0_human_pending"] == 1
    assert len(sweeps) == 3   # human-pending polls 2, 4, and 6


@pytest.mark.asyncio
async def test_seat0_cancel_during_policy_call_writes_interrupted_record(
    monkeypatch, tmp_path
):
    """A cancellation during the (potentially very long) seat-0 policy call
    must still leave an `interrupted` transcript record -- the record
    skeleton exists before the first await of the logical turn."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await run_arena(
            conn, FakeGSWithConn(conn),
            _seat0_cfg(tmp_path, run_id="seat0-cancel-policy"),
            policy=pol, transcript=sink,
        )

    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec["player_id"] == 0
    assert rec["turn"] == 7
    assert rec["turn_kind"] == "failed"
    assert rec["seat0"]["terminal_state"] == "interrupted"


@pytest.mark.asyncio
async def test_seat0_cancel_during_mech_pass_writes_interrupted_record(
    monkeypatch, tmp_path
):
    """Same guarantee one await later: a cancellation inside the mechanical
    pass (blocker query) may not skip the interrupted record."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])

    async def cancelled_query(_conn):
        raise asyncio.CancelledError()

    monkeypatch.setattr(seat0_mod, "query_blockers", cancelled_query)
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(harness, [_returned("normal ok")])

    with pytest.raises(asyncio.CancelledError):
        await run_arena(
            conn, FakeGSWithConn(conn),
            _seat0_cfg(tmp_path, run_id="seat0-cancel-mech"),
            policy=pol, transcript=sink,
        )

    assert len(sink.records) == 1
    assert sink.records[0]["turn"] == 7
    assert sink.records[0]["seat0"]["terminal_state"] == "interrupted"


@pytest.mark.asyncio
async def test_seat0_cancel_during_repair_marks_attempted_in_interrupted_record(
    monkeypatch, tmp_path
):
    """The one-shot repair mutates the record's repair sub-dict in place, so
    a cancellation mid-repair still shows attempted=true in the interrupted
    record -- the single repair charge was genuinely spent."""
    research = _blocker("ENDTURN_BLOCKING_RESEARCH", "Choose research")
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    # Decision blocker survives the mechanical pass -> need_repair fires.
    harness.blocker_queue = [[research], [research]]
    conn = Seat0CapsConn()
    sink = EventSink(harness)
    pol = Seat0ScriptPolicy(
        harness, [_returned("normal ok"), asyncio.CancelledError()]
    )

    with pytest.raises(asyncio.CancelledError):
        await run_arena(
            conn, FakeGSWithConn(conn),
            _seat0_cfg(tmp_path, run_id="seat0-cancel-repair"),
            policy=pol, transcript=sink,
        )

    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec["seat0"]["terminal_state"] == "interrupted"
    assert rec["seat0"]["repair"]["attempted"] is True


# --- Full-LLM-control: diplomacy-wedged seat 0 -------------------------------
# An AI-initiated deal/session with the human seat halts the whole turn cycle
# until answered. The coordinator must hand it to the pilot's own diplomacy
# tools (get_pending_diplomacy/respond_to_trade/...), bounded, never
# auto-answering and never stalling to the idle deadline.


class _AnsweringSessions:
    """Mutable session source: reports `value` until cleared."""

    def __init__(self, value="1#1"):
        self.value = value
        self.probes = 0

    async def __call__(self, conn):
        self.probes += 1
        return self.value


@pytest.mark.asyncio
async def test_seat0_idle_deal_wedge_invokes_diplomacy_pass(monkeypatch, tmp_path):
    """Seat 0 inactive with an open human-seat session: after the idle probe
    cadence the policy is invoked once with the focused diplomacy prompt; no
    turn is admitted, no end request fires, no turn record is written."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(298, active=False)])
    sessions = _AnsweringSessions("1#1")
    monkeypatch.setattr(seat0_mod, "query_local_player_sessions", sessions)

    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)

    class AnsweringPolicy(Seat0RecordingPolicy):
        async def __call__(self, gs, player_id, turn, **kwargs):
            sessions.value = ""   # the pilot answers the deal
            return await super().__call__(gs, player_id, turn, **kwargs)

    pol = AnsweringPolicy(harness)
    cfg = _seat0_cfg(tmp_path, idle_poll_limit=30, run_id="seat0-diplo-wedge")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert [c[:2] for c in pol.calls] == [(0, 298)]
    block = pol.calls[0][2]["blocker_block"]
    assert "PENDING DIPLOMACY" in block
    assert "1#1" in block
    assert "respond_to_trade" in block
    # No turn machinery ran: no end request, no blocker query, no record.
    assert "end_turn" not in harness.names()
    assert sink.records == []
    assert result["seat0_turns_played"] == 0
    passes = [e for e in result["log"] if e.get("event") == "seat0_diplomacy_pass"]
    assert len(passes) == 1
    assert passes[0]["completed"] is True
    assert passes[0]["sessions"] == "1#1"


@pytest.mark.asyncio
async def test_seat0_deal_wedge_attempts_bounded_then_critical(monkeypatch, tmp_path):
    """A session the policy never manages to close: exactly
    SEAT0_DIPLO_ATTEMPT_LIMIT passes, then one CRITICAL log entry and no
    further policy calls -- a human is expected, matching the escape-hatch
    contract."""
    harness = Seat0Harness(monkeypatch, [seat0_poll(298, active=False)])
    sessions = _AnsweringSessions("1#1")
    monkeypatch.setattr(seat0_mod, "query_local_player_sessions", sessions)

    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(tmp_path, idle_poll_limit=90, run_id="seat0-diplo-bound")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert len(pol.calls) == coordinator_mod.SEAT0_DIPLO_ATTEMPT_LIMIT
    crit = [e for e in result["log"]
            if e.get("event") == "seat0_diplomacy_unresolved"]
    assert len(crit) == 1
    assert crit[0]["level"] == "CRITICAL"
    assert sink.records == []


@pytest.mark.asyncio
async def test_seat0_drain_deal_wedge_runs_diplomacy_pass(monkeypatch, tmp_path):
    """A deal arriving during the post-end-turn AI phase (drain) also reaches
    the pilot: probe on the drain cadence, one diplomacy pass, then the turn
    advances and terminalizes `advanced` as normal."""
    monkeypatch.setattr(coordinator_mod, "SEAT0_DIPLO_DRAIN_POLLS", 4)
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),                       # admission
        *[seat0_poll(7, active=False) for _ in range(6)],  # drain (wedged)
        seat0_poll(8, active=True),                       # answered -> advance
    ])
    sessions = _AnsweringSessions("2#5")
    monkeypatch.setattr(seat0_mod, "query_local_player_sessions", sessions)

    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)

    class AnsweringPolicy(Seat0RecordingPolicy):
        async def __call__(self, gs, player_id, turn, **kwargs):
            if "blocker_block" in kwargs and "PENDING DIPLOMACY" in kwargs["blocker_block"]:
                sessions.value = ""
            return await super().__call__(gs, player_id, turn, **kwargs)

    pol = AnsweringPolicy(harness)
    cfg = _seat0_cfg(tmp_path, idle_poll_limit=30, run_id="seat0-diplo-drain")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    # Two policy invocations: the normal turn, then the mid-drain diplomacy pass.
    assert [c[:2] for c in pol.calls] == [(0, 7), (0, 7)]
    assert "PENDING DIPLOMACY" in pol.calls[1][2]["blocker_block"]
    # The seat-0 turn still terminalizes normally.
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    assert result["seat0_turns_played"] == 1
    passes = [e for e in result["log"] if e.get("event") == "seat0_diplomacy_pass"]
    assert len(passes) == 1 and passes[0]["completed"] is True


@pytest.mark.asyncio
async def test_seat0_quiet_recheck_probes_sessions_before_burning_budget(
    monkeypatch, tmp_path
):
    """A deal absorbing turn processing never shows as a blocker: the quiet
    recheck must probe for an open human-seat session and hand it to the
    pilot BEFORE spending the idle-recheck budget, then keep draining. No
    refire, no human_pending -- the turn advances once the deal is answered."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),                        # admission
        *[seat0_poll(7, active=True) for _ in range(6)],   # grace + recheck
        seat0_poll(8, active=True),                        # answered -> advance
    ])
    sessions = _AnsweringSessions("1#1")
    monkeypatch.setattr(seat0_mod, "query_local_player_sessions", sessions)

    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)

    class AnsweringPolicy(Seat0RecordingPolicy):
        async def __call__(self, gs, player_id, turn, **kwargs):
            if "PENDING DIPLOMACY" in kwargs.get("blocker_block", ""):
                sessions.value = ""
            return await super().__call__(gs, player_id, turn, **kwargs)

    pol = AnsweringPolicy(harness)
    cfg = _seat0_cfg(tmp_path, idle_poll_limit=40, run_id="seat0-recheck-deal")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    names = harness.names()
    assert names.count("end_turn") == 1          # never refired
    assert [c[:2] for c in pol.calls] == [(0, 7), (0, 7)]
    assert "PENDING DIPLOMACY" in pol.calls[1][2]["blocker_block"]
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    assert result["seat0_turns_played"] == 1
    assert result["seat0_human_pending"] == 0
    passes = [e for e in result["log"] if e.get("event") == "seat0_diplomacy_pass"]
    assert len(passes) == 1 and passes[0]["completed"] is True


@pytest.mark.asyncio
async def test_seat0_human_pending_arm_probes_sessions(monkeypatch, tmp_path):
    """Safety net: a turn already terminalized human_pending still probes for
    an open human-seat session on the human-poll cadence and hands it to the
    pilot, bounded by the shared attempt limit, then goes CRITICAL."""
    monkeypatch.setattr(coordinator_mod, "SEAT0_DIPLO_IDLE_POLLS", 2)
    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    hard = _blocker("ENDTURN_BLOCKING_SOME_FUTURE_TYPE", "hard block")
    harness.blockers = [hard]
    sessions = _AnsweringSessions("3#4")
    monkeypatch.setattr(seat0_mod, "query_local_player_sessions", sessions)

    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(
        tmp_path,
        idle_poll_limit=40,
        run_id="seat0-pending-deal",
        seat0_human_pending_poll_limit=20,
    )

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    # One normal policy call, then the bounded diplomacy passes from the
    # human-pending arm.
    diplo_calls = [
        c for c in pol.calls
        if "PENDING DIPLOMACY" in c[2].get("blocker_block", "")
    ]
    assert len(diplo_calls) == coordinator_mod.SEAT0_DIPLO_ATTEMPT_LIMIT
    crit = [e for e in result["log"]
            if e.get("event") == "seat0_diplomacy_unresolved"]
    assert len(crit) == 1
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "human_pending"


# --- Full-LLM-control: World Congress gate -----------------------------------
# The WC opens and closes synchronously INSIDE ACTION_ENDTURN (observed live,
# T303): votes must be registered before the coordinator fires, exactly as
# the solo end_turn path enforces with its pre-end WC gate.


class _WCStatus:
    def __init__(self, *, fires=True, resolutions=2, in_session=False, favor=100):
        self.turns_until_next = 0 if fires else 15
        self.is_in_session = in_session
        self.resolutions = list(range(resolutions))
        self.favor = favor


def _wc_env(monkeypatch, harness, *, handler_results, status):
    """Wire the WC seams: canned handler-registered results (popped per call)
    and a recorded default-voter registration."""
    calls = {"handler": 0, "default": 0}
    results = list(handler_results)

    async def fake_handler_registered(conn):
        calls["handler"] += 1
        return results.pop(0) if results else False

    async def fake_register_default(conn):
        calls["default"] += 1
        return True

    monkeypatch.setattr(seat0_mod, "wc_handler_registered", fake_handler_registered)
    monkeypatch.setattr(seat0_mod, "register_default_wc_voter", fake_register_default)
    return calls


@pytest.mark.asyncio
async def test_seat0_wc_gate_pilot_votes_then_fire(monkeypatch, tmp_path):
    """WC fires this turn with resolutions and no handler: one focused voting
    pass; the pilot registers votes; no default; the end request fires."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    calls = _wc_env(monkeypatch, harness, handler_results=[False, True],
                    status=None)

    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)

    async def fake_wc():
        return _WCStatus(fires=True, resolutions=2)

    gs.get_world_congress = fake_wc
    sink = EventSink(harness)
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(tmp_path, idle_poll_limit=30, run_id="seat0-wc-vote")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert [c[:2] for c in pol.calls] == [(0, 7), (0, 7)]
    assert "World Congress" in pol.calls[1][2]["blocker_block"]
    assert calls["default"] == 0
    assert harness.names().count("end_turn") == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    passes = [e for e in result["log"] if e.get("event") == "seat0_wc_vote_pass"]
    assert len(passes) == 1
    assert passes[0]["completed"] is True and passes[0]["defaulted"] is False


@pytest.mark.asyncio
async def test_seat0_wc_gate_defaults_when_pilot_fails(monkeypatch, tmp_path):
    """The pilot never registers votes: the coordinator registers the default
    voter (stuck-free beats optimal) and still fires the end request."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    calls = _wc_env(monkeypatch, harness, handler_results=[False, False],
                    status=None)

    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)

    async def fake_wc():
        return _WCStatus(fires=True, resolutions=1)

    gs.get_world_congress = fake_wc
    sink = EventSink(harness)
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(tmp_path, idle_poll_limit=30, run_id="seat0-wc-default")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert calls["default"] == 1
    assert harness.names().count("end_turn") == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    passes = [e for e in result["log"] if e.get("event") == "seat0_wc_vote_pass"]
    assert len(passes) == 1 and passes[0]["defaulted"] is True


@pytest.mark.asyncio
async def test_seat0_wc_gate_zero_resolutions_skips(monkeypatch, tmp_path):
    """A 0-resolution congress (even stale in_session=true, observed live at
    T303 post-bounce) has nothing to vote on: no pass, no handler check,
    normal fire."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    calls = _wc_env(monkeypatch, harness, handler_results=[], status=None)

    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)

    async def fake_wc():
        return _WCStatus(fires=True, resolutions=0, in_session=True)

    gs.get_world_congress = fake_wc
    sink = EventSink(harness)
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(tmp_path, idle_poll_limit=30, run_id="seat0-wc-zero")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    assert len(pol.calls) == 1
    assert calls["handler"] == 0 and calls["default"] == 0
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    assert not [e for e in result["log"] if e.get("event") == "seat0_wc_vote_pass"]


@pytest.mark.asyncio
async def test_seat0_recheck_wc_only_blocker_defaults_and_refires(monkeypatch, tmp_path):
    """Post-fire WC bounce (the T303 shape): the WC_SESSION blocker persists
    after the repair pass. All-WC remaining blockers are clears-at-end: ensure
    a voter (default if the pilot didn't register one) and refire instead of
    human_pending."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),                        # admission
        *[seat0_poll(7, active=True) for _ in range(6)],   # grace + recheck
        seat0_poll(8, active=True),                        # WC done -> advance
    ])
    wc = _blocker("ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION", "WC in session")
    harness.blocker_queue = [
        [],            # after_normal: clear -> fire
        [wc], [wc],    # recheck: after_refire, after_refire_cleanup
        [wc], [wc],    # after_repair, after_repair_cleanup
    ]
    calls = _wc_env(monkeypatch, harness, handler_results=[False], status=None)

    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(tmp_path, idle_poll_limit=40, run_id="seat0-wc-refire")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    names = harness.names()
    assert names.count("end_turn") == 2          # original + WC refire
    assert calls["default"] == 1
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    assert result["seat0_human_pending"] == 0
    events = [e for e in result["log"] if e.get("event") == "seat0_wc_default_vote"]
    assert len(events) == 1


@pytest.mark.asyncio
async def test_seat0_played_path_wc_only_after_repair_fires_anyway(monkeypatch, tmp_path):
    """A live WC session (with resolutions) blocking BEFORE any end request:
    the repair pass is the pilot's voting chance; a persisting all-WC blocker
    set is clears-at-end, so the played path ensures a voter (default if the
    pilot registered none) and fires instead of human_pending."""
    harness = Seat0Harness(monkeypatch, [
        seat0_poll(7, active=True),
        seat0_poll(7, active=False),
        seat0_poll(8, active=True),
    ])
    wc = _blocker("ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION", "Resume Congress")
    harness.blockers = [wc]  # persists through every query
    calls = _wc_env(monkeypatch, harness, handler_results=[False], status=None)

    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)
    sink = EventSink(harness)
    pol = Seat0RecordingPolicy(harness)
    cfg = _seat0_cfg(tmp_path, idle_poll_limit=30, run_id="seat0-wc-played")

    result = await run_arena(conn, gs, cfg, policy=pol, transcript=sink)

    names = harness.names()
    assert names.count("end_turn") == 1
    assert calls["default"] == 1
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"
    assert result["seat0_human_pending"] == 0
    events = [e for e in result["log"] if e.get("event") == "seat0_wc_default_vote"]
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Task 11 — unofficial-channel coordinator integration
# ---------------------------------------------------------------------------


class FakeChannelRuntime:
    def __init__(
        self,
        *,
        run_id="channels-run",
        enabled_players=frozenset({1, 2}),
        rules=None,
        wake_reasons=(),
        acknowledgements=1,
        admit_error=None,
        finish_error=None,
        reconcile_error=None,
        poll_error=None,
        events=None,
    ):
        from types import SimpleNamespace
        from civ_mcp.arena.config import ChannelRules

        self.rules = rules or ChannelRules()
        self.state = SimpleNamespace(
            run_id=run_id,
            enabled_players=frozenset(enabled_players),
            rules_fingerprint=self.rules.fingerprint(),
        )
        self.wake_reasons = tuple(wake_reasons)
        self.acknowledgements = acknowledgements
        self.admit_error = admit_error
        self.finish_error = finish_error
        self.reconcile_error = reconcile_error
        self.poll_error = poll_error
        self.calls = []
        self.finish_results = []
        self.finish_staged_actions = []
        self.events = events

    def _record(self, event):
        self.calls.append(event)
        if self.events is not None:
            self.events.append((event,))

    async def reconcile_payment_intents(
        self, gs, *, current_turn, current_player_id
    ):
        self._record(f"reconcile:{current_player_id}:{current_turn}")
        if self.reconcile_error is not None:
            raise self.reconcile_error

    async def admit_player(self, gs, player_id, turn):
        from types import SimpleNamespace
        from civ_mcp.arena.channel_protocol import ChannelTurnContext

        self._record(f"admit:{player_id}:{turn}")
        if self.admit_error is not None:
            raise self.admit_error
        context = ChannelTurnContext(
            self.state.run_id,
            player_id,
            turn,
            self.state.enabled_players,
            self.rules,
        )
        return SimpleNamespace(
            player_id=player_id,
            turn=turn,
            block="CHANNEL BLOCK",
            context=context,
            wake_reasons=self.wake_reasons,
        )

    async def finish_player(self, gs, admission, policy_result):
        self._record(f"finish:{admission.player_id}:{admission.turn}")
        self.finish_results.append(policy_result)
        self.finish_staged_actions.append(tuple(admission.context.staged_actions))
        if self.finish_error is not None:
            raise self.finish_error
        return tuple(object() for _ in range(self.acknowledgements))

    async def poll_unseated(self, gs, turn, local_player_id):
        self._record(f"poll:{local_player_id}:{turn}")
        if self.poll_error is not None:
            raise self.poll_error


def _channel_options(*, attention_mode="off"):
    from civ_mcp.arena.config import AttentionOptions, ChannelOptions

    return CivOptions(
        attention=AttentionOptions(mode=attention_mode),
        channels=ChannelOptions(enabled=True),
    )


def _channel_config(tmp_path, *, options=None, run_id="channels-run", **kwargs):
    opts = options or _channel_options()
    kwargs.setdefault("max_puppet_turns", 1)
    kwargs.setdefault("idle_poll_limit", 2)
    kwargs.setdefault("puppet_ids", [1])
    return ArenaConfig(
        players=[
            PlayerSpec(1, "local", "m", options=opts),
            PlayerSpec(2, "local", "m", options=_channel_options()),
        ],
        run_id=run_id,
        transcript_dir=str(tmp_path),
        **kwargs,
    )


class ChannelRecordingPolicy:
    provider = "local"
    model = "channel-test"

    def __init__(self, runtime, *, options=None, final_summary=""):
        self.runtime = runtime
        self.options = options or _channel_options()
        self.final_summary = final_summary
        self.calls = []

    async def __call__(
        self,
        gs,
        player_id,
        turn,
        *,
        channel_context=None,
        channel_block="",
        master_block="",
    ):
        self.runtime._record(f"policy:{player_id}:{turn}")
        self.calls.append(
            {
                "channel_context": channel_context,
                "channel_block": channel_block,
                "master_block": master_block,
            }
        )
        return {
            "summary": "channel turn",
            "actions": [],
            "transcript": {
                "steps": [],
                "final_summary": self.final_summary,
            },
        }


def _patch_channel_open(monkeypatch, runtime, opened=None):
    from civ_mcp.arena.channel_runtime import ChannelRuntime

    opened = opened if opened is not None else {}

    def fake_open(cls, run_dir, run_id, enabled_players, rules):
        opened.update(
            run_dir=run_dir,
            run_id=run_id,
            enabled_players=enabled_players,
            rules=rules,
        )
        return runtime

    monkeypatch.setattr(ChannelRuntime, "open", classmethod(fake_open))
    return opened


@pytest.mark.asyncio
async def test_channels_open_admit_policy_finish_with_run_identity(
    monkeypatch, tmp_path
):
    from pathlib import Path

    runtime = FakeChannelRuntime(acknowledgements=2)
    opened = _patch_channel_open(monkeypatch, runtime)
    policy = ChannelRecordingPolicy(
        runtime,
        final_summary=(
            'CHANNEL {"action":"send_message","to_player":2,"text":"hi"}'
        ),
    )
    conn = FakeConn()
    conn._polls = iter([["LOCAL|1", "TURN|7", "ACTIVE|true", "LAST|0"]])
    sink = FakeSink()
    config = _channel_config(tmp_path)

    result = await run_arena(conn, FakeGS(), config, policy=policy, transcript=sink)

    assert opened == {
        "run_dir": Path(tmp_path) / "channels-run",
        "run_id": "channels-run",
        "enabled_players": frozenset({1, 2}),
        "rules": config.channel_rules,
    }
    assert runtime.calls[:4] == [
        "reconcile:1:7",
        "admit:1:7",
        "policy:1:7",
        "finish:1:7",
    ]
    assert len(policy.calls) == 1
    assert policy.calls[0]["channel_block"] == "CHANNEL BLOCK"
    assert policy.calls[0]["channel_context"].player_id == 1
    assert policy.calls[0]["master_block"] == ""
    assert runtime.finish_results[0]["transcript"]["final_summary"].startswith(
        "CHANNEL "
    )
    assert result["log"][0]["channels"] == {
        "enabled": True,
        "acknowledgements": 2,
        "error": "",
    }
    assert sink.records[0]["channels"] == result["log"][0]["channels"]


@pytest.mark.asyncio
async def test_injected_channel_runtime_is_validated_and_used_exactly(
    monkeypatch, tmp_path
):
    from civ_mcp.arena.channel_runtime import ChannelRuntime

    runtime = FakeChannelRuntime()

    def unexpected_open(*args, **kwargs):
        raise AssertionError("injected runtime must not be replaced")

    monkeypatch.setattr(ChannelRuntime, "open", classmethod(unexpected_open))
    policy = ChannelRecordingPolicy(runtime)
    conn = FakeConn()
    conn._polls = iter([["LOCAL|1", "TURN|7", "ACTIVE|true", "LAST|0"]])

    await run_arena(
        conn,
        FakeGS(),
        _channel_config(tmp_path),
        policy=policy,
        channel_runtime=runtime,
    )

    assert runtime.calls[:4] == [
        "reconcile:1:7",
        "admit:1:7",
        "policy:1:7",
        "finish:1:7",
    ]


@pytest.mark.asyncio
async def test_channel_wake_cancels_attention_sleep_and_finishes(
    monkeypatch, tmp_path
):
    from civ_mcp.arena.attention import AttentionState, save_attention_state

    options = _channel_options(attention_mode="auto")
    runtime = FakeChannelRuntime(wake_reasons=("payment response due",))
    _patch_channel_open(monkeypatch, runtime)
    policy = ChannelRecordingPolicy(runtime, options=options)
    conn = AttnConn()
    conn._polls = iter([["LOCAL|1", "TURN|7", "ACTIVE|true", "LAST|0"]])
    save_attention_state(
        str(tmp_path),
        "channels-run",
        1,
        AttentionState(
            run_id="channels-run",
            player_id=1,
            last_snapshot=dict(_ATTN_BASELINE_SNAPSHOT),
            last_scan={
                "at_war_with": [],
                "era_index": 1,
                "total_population": 12,
            },
        ),
    )
    sink = FakeSink()

    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _channel_config(tmp_path, options=options),
        policy=policy,
        transcript=sink,
    )

    assert len(policy.calls) == 1
    assert result["puppet_turns_played"] == 1
    assert result["turns_slept"] == 0
    assert runtime.calls.count("admit:1:7") == 1
    assert runtime.calls.count("finish:1:7") == 1
    assert sink.records[0]["attention"]["wake_cause"] == "CHANNEL_DUE"
    assert sink.records[0]["attention"]["wake_detail"] == "payment response due"


@pytest.mark.asyncio
async def test_quiet_channel_sleep_still_finishes_admission(monkeypatch, tmp_path):
    from civ_mcp.arena.attention import AttentionState, save_attention_state

    options = _channel_options(attention_mode="auto")
    runtime = FakeChannelRuntime(wake_reasons=())
    _patch_channel_open(monkeypatch, runtime)
    policy = ChannelRecordingPolicy(runtime, options=options)
    conn = AttnConn()
    conn._polls = iter([["LOCAL|1", "TURN|7", "ACTIVE|true", "LAST|0"]])
    save_attention_state(
        str(tmp_path),
        "channels-run",
        1,
        AttentionState(
            run_id="channels-run",
            player_id=1,
            last_snapshot=dict(_ATTN_BASELINE_SNAPSHOT),
            last_scan={
                "at_war_with": [],
                "era_index": 1,
                "total_population": 12,
            },
        ),
    )

    result = await run_arena(
        conn,
        FakeGSWithConn(conn),
        _channel_config(tmp_path, options=options),
        policy=policy,
    )

    assert policy.calls == []
    assert result["turns_slept"] == 1
    assert runtime.calls.count("admit:1:7") == 1
    assert runtime.calls.count("finish:1:7") == 1
    assert runtime.finish_results == [None]


@pytest.mark.asyncio
async def test_channel_admission_exception_fails_open_with_safe_telemetry(
    monkeypatch, tmp_path
):
    runtime = FakeChannelRuntime(admit_error=RuntimeError("projection failed"))
    _patch_channel_open(monkeypatch, runtime)
    policy = ChannelRecordingPolicy(runtime)
    conn = FakeConn()
    conn._polls = iter([["LOCAL|1", "TURN|7", "ACTIVE|true", "LAST|0"]])

    result = await run_arena(
        conn, FakeGS(), _channel_config(tmp_path), policy=policy
    )

    assert result["puppet_turns_played"] == 1
    assert policy.calls == [
        {
            "channel_context": None,
            "channel_block": "",
            "master_block": "",
        }
    ]
    assert "finish:1:7" not in runtime.calls
    assert result["log"][0]["channels"] == {
        "enabled": True,
        "acknowledgements": 0,
        "error": "RuntimeError('projection failed')",
    }


@pytest.mark.asyncio
async def test_channel_finish_exception_does_not_block_game_progress(
    monkeypatch, tmp_path
):
    runtime = FakeChannelRuntime(finish_error=RuntimeError("capture failed"))
    _patch_channel_open(monkeypatch, runtime)
    policy = ChannelRecordingPolicy(runtime)
    conn = FakeConn()
    conn._polls = iter([["LOCAL|1", "TURN|7", "ACTIVE|true", "LAST|0"]])

    result = await run_arena(
        conn, FakeGS(), _channel_config(tmp_path), policy=policy
    )

    assert result["puppet_turns_played"] == 1
    assert runtime.calls.count("finish:1:7") == 1
    assert result["log"][0]["channels"] == {
        "enabled": True,
        "acknowledgements": 0,
        "error": "RuntimeError('capture failed')",
    }


@pytest.mark.asyncio
async def test_failed_policy_still_finishes_channel_before_release(
    monkeypatch, tmp_path
):
    runtime = FakeChannelRuntime()
    _patch_channel_open(monkeypatch, runtime)

    class FailingPolicy(ChannelRecordingPolicy):
        async def __call__(self, gs, player_id, turn, **kwargs):
            self.runtime._record(f"policy:{player_id}:{turn}")
            raise RuntimeError("model unavailable")

    conn = FakeConn()
    conn._polls = iter([["LOCAL|1", "TURN|7", "ACTIVE|true", "LAST|0"]])

    result = await run_arena(
        conn,
        FakeGS(),
        _channel_config(tmp_path),
        policy=FailingPolicy(runtime),
    )

    assert runtime.calls[:4] == [
        "reconcile:1:7",
        "admit:1:7",
        "policy:1:7",
        "finish:1:7",
    ]
    assert runtime.finish_results == [None]
    assert result["puppet_turns_played"] == 0
    assert result["log"][0]["channels"]["error"] == ""


@pytest.mark.asyncio
async def test_channel_baseexception_is_not_swallowed(monkeypatch, tmp_path):
    runtime = FakeChannelRuntime(admit_error=asyncio.CancelledError())
    _patch_channel_open(monkeypatch, runtime)
    conn = FakeConn()
    conn._polls = iter([["LOCAL|1", "TURN|7", "ACTIVE|true", "LAST|0"]])

    with pytest.raises(asyncio.CancelledError):
        await run_arena(
            conn,
            FakeGS(),
            _channel_config(tmp_path),
            policy=ChannelRecordingPolicy(runtime),
        )

    assert conn.restored is True


@pytest.mark.asyncio
async def test_idle_unconfigured_seat_reconciles_and_polls_unseated(
    monkeypatch, tmp_path
):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    runtime = FakeChannelRuntime()
    _patch_channel_open(monkeypatch, runtime)
    conn = FakeConn()
    conn._polls = iter(
        [
            ["LOCAL|0", "TURN|7", "ACTIVE|false", "LAST|0"],
            ["LOCAL|0", "TURN|7", "ACTIVE|false", "LAST|0"],
        ]
    )

    result = await run_arena(
        conn,
        FakeGS(),
        _channel_config(tmp_path, idle_poll_limit=2),
        policy=ChannelRecordingPolicy(runtime),
    )

    assert result["puppet_turns_played"] == 0
    assert runtime.calls == [
        "reconcile:0:7",
        "poll:0:7",
        "reconcile:0:7",
        "poll:0:7",
    ]


@pytest.mark.asyncio
async def test_seat0_repair_reuses_admission_and_preserves_raw_channel_order(
    monkeypatch, tmp_path
):
    from civ_mcp.arena.config import ChannelOptions

    harness = Seat0Harness(
        monkeypatch,
        [seat0_poll(7, active=True), seat0_poll(7, active=False), seat0_poll(8)],
    )
    harness.blocker_queue = [[_RESEARCH], [_RESEARCH], []]
    options = CivOptions(channels=ChannelOptions(enabled=True))
    normal_summary = (
        'CHANNEL {"action":"send_message","to_player":2,"text":"normal"}'
    )
    repair_summary = (
        'CHANNEL {"action":"send_message","to_player":2,"text":"repair"}'
    )
    policy = Seat0ScriptPolicy(
        harness,
        [
            {
                "summary": "normal",
                "actions": [],
                "transcript": {"steps": [], "final_summary": normal_summary},
            },
            {
                "summary": "repair",
                "actions": [],
                "transcript": {"steps": [], "final_summary": repair_summary},
            },
        ],
        options=options,
    )
    runtime = FakeChannelRuntime(
        run_id="seat0-channels",
        enabled_players=frozenset({0, 2}),
        events=harness.events,
    )
    _patch_channel_open(monkeypatch, runtime)
    sink = EventSink(harness)
    config = _seat0_cfg(
        tmp_path,
        players=[
            PlayerSpec(0, "local", "m", options=options),
            PlayerSpec(2, "local", "m", options=_channel_options()),
        ],
        run_id="seat0-channels",
    )

    result = await run_arena(
        Seat0CapsConn(),
        FakeGS(),
        config,
        policy=policy,
        transcript=sink,
    )

    normal_context = policy.calls[0][2]["channel_context"]
    repair_context = policy.calls[1][2]["channel_context"]
    assert repair_context is normal_context
    assert policy.calls[1][2]["channel_block"] == "CHANNEL BLOCK"
    assert runtime.calls.count("admit:0:7") == 1
    assert runtime.calls.count("finish:0:7") == 1
    assert runtime.finish_results[0]["transcript"]["final_summary"] == (
        normal_summary + "\n" + repair_summary
    )
    names = harness.names()
    assert names.index("policy", names.index("policy") + 1) < names.index(
        "finish:0:7"
    )
    assert names.index("finish:0:7") < names.index("record")
    assert result["seat0_turns_played"] == 1
    assert sink.records[0]["channels"] == {
        "enabled": True,
        "acknowledgements": 1,
        "error": "",
    }


@pytest.mark.asyncio
async def test_seat0_wc_pass_reuses_admission_before_channel_finish(
    monkeypatch, tmp_path
):
    from civ_mcp.arena.config import ChannelOptions

    harness = Seat0Harness(
        monkeypatch,
        [seat0_poll(7, active=True), seat0_poll(7, active=False), seat0_poll(8)],
    )
    _wc_env(monkeypatch, harness, handler_results=[False, True], status=None)
    options = CivOptions(channels=ChannelOptions(enabled=True))
    normal_summary = (
        'CHANNEL {"action":"send_message","to_player":2,"text":"normal"}'
    )
    wc_summary = (
        'CHANNEL {"action":"send_message","to_player":2,"text":"wc"}'
    )
    policy = Seat0ScriptPolicy(
        harness,
        [
            {
                "summary": "normal",
                "actions": [],
                "transcript": {"steps": [], "final_summary": normal_summary},
            },
            {
                "summary": "wc",
                "actions": [],
                "transcript": {"steps": [], "final_summary": wc_summary},
            },
        ],
        options=options,
    )
    runtime = FakeChannelRuntime(
        run_id="seat0-channel-wc",
        enabled_players=frozenset({0, 2}),
        events=harness.events,
    )
    _patch_channel_open(monkeypatch, runtime)
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)

    async def fake_wc():
        return _WCStatus(fires=True, resolutions=1)

    gs.get_world_congress = fake_wc
    config = _seat0_cfg(
        tmp_path,
        players=[
            PlayerSpec(0, "local", "m", options=options),
            PlayerSpec(2, "local", "m", options=_channel_options()),
        ],
        run_id="seat0-channel-wc",
    )

    result = await run_arena(conn, gs, config, policy=policy)

    assert [call[:2] for call in policy.calls] == [(0, 7), (0, 7)]
    assert policy.calls[1][2]["channel_context"] is policy.calls[0][2][
        "channel_context"
    ]
    assert runtime.calls.count("admit:0:7") == 1
    assert runtime.calls.count("finish:0:7") == 1
    assert runtime.finish_results[0]["transcript"]["final_summary"] == (
        normal_summary + "\n" + wc_summary
    )
    names = harness.names()
    assert names.index("policy", names.index("policy") + 1) < names.index(
        "finish:0:7"
    )
    assert names.index("finish:0:7") < names.index("restore_local")
    assert result["seat0_turns_played"] == 1


@pytest.mark.asyncio
async def test_seat0_idle_diplomacy_admits_once_then_normal_reuses_private_capture(
    monkeypatch, tmp_path
):
    """A pre-admission idle diplomacy pass is the first coordinator-owned
    policy call for seat 0, so it must establish the one channel capture before
    the call.  A later normal turn reuses it and finish receives both raw
    outputs plus the committed trade audit without a poll_unseated observation
    on the admission poll."""
    from civ_mcp.arena.channel_terms import normalize_action_audit
    from civ_mcp.arena.config import ChannelOptions

    monkeypatch.setattr(coordinator_mod, "SEAT0_DIPLO_IDLE_POLLS", 1)
    harness = Seat0Harness(
        monkeypatch,
        [
            seat0_poll(7, active=False),
            seat0_poll(7, active=True),
            seat0_poll(7, active=False),
            seat0_poll(8, active=True),
        ],
    )
    sessions = _AnsweringSessions("2#5")
    monkeypatch.setattr(seat0_mod, "query_local_player_sessions", sessions)
    options = CivOptions(channels=ChannelOptions(enabled=True))
    canary = "PRIVATE-IDLE-DIPLOMACY-CANARY"
    diplomacy_raw = (
        "diplomacy completed\n"
        'CHANNEL {"action":"send_message","to_player":2,'
        f'"text":"{canary}"}}'
    )
    normal_raw = (
        "normal completed\n"
        'CHANNEL {"action":"send_message","to_player":2,'
        f'"text":"{canary}-normal"}}'
    )
    behaviors = [
        {
            "summary": diplomacy_raw,
            "actions": [],
            "transcript": {
                "steps": [
                    {
                        "tool_name": "respond_to_trade",
                        "tool_args": {"other_player_id": 2, "accept": True},
                        "tool_result_full": "OK:DEAL_ACCEPTED|Rome",
                    }
                ],
                "final_summary": diplomacy_raw,
            },
        },
        {
            "summary": normal_raw,
            "actions": [],
            "transcript": {"steps": [], "final_summary": normal_raw},
        },
    ]

    class IdleThenNormalPolicy(Seat0ScriptPolicy):
        async def __call__(self, gs, player_id, turn, **kwargs):
            result = await super().__call__(gs, player_id, turn, **kwargs)
            if "PENDING DIPLOMACY" in kwargs.get("blocker_block", ""):
                sessions.value = ""
            return result

    policy = IdleThenNormalPolicy(harness, behaviors, options=options)
    runtime = FakeChannelRuntime(
        run_id="seat0-channel-idle-first",
        enabled_players=frozenset({0, 2}),
        events=harness.events,
    )
    _patch_channel_open(monkeypatch, runtime)
    sink = SnapshotEventSink(harness)
    config = _seat0_cfg(
        tmp_path,
        players=[
            PlayerSpec(0, "local", "m", options=options),
            PlayerSpec(2, "local", "m", options=_channel_options()),
        ],
        run_id="seat0-channel-idle-first",
        idle_poll_limit=20,
    )

    result = await run_arena(
        Seat0CapsConn(), FakeGS(), config, policy=policy, transcript=sink
    )

    assert [call[:2] for call in policy.calls] == [(0, 7), (0, 7)]
    for name in ("channel_context", "channel_block", "master_block"):
        if name == "channel_context":
            assert policy.calls[0][2][name] is policy.calls[1][2][name]
        else:
            assert policy.calls[0][2][name] == policy.calls[1][2][name]
    assert runtime.calls.count("admit:0:7") == 1
    assert runtime.calls.count("finish:0:7") == 1
    assert "poll:0:7" not in runtime.calls
    private_result = runtime.finish_results[0]
    assert private_result["transcript"]["final_summary"] == (
        diplomacy_raw + "\n" + normal_raw
    )
    assert [a.tool_name for a in normalize_action_audit(
        private_result, actor=0, turn=7
    )] == ["respond_to_trade"]
    assert canary not in repr(result["log"])
    assert canary not in repr(sink.records)
    assert result["seat0_turns_played"] == 1


@pytest.mark.asyncio
async def test_seat0_delayed_recheck_repair_stays_in_one_private_channel_capture(
    monkeypatch, tmp_path
):
    """A blocker that surfaces only after the first end request is still part
    of the admitted seat-0 turn.  The delayed RECHECK repair must reuse the
    original private prompt/context, and finish must receive every staged API
    action, raw CHANNEL line, and action-audit step in policy-call order."""
    from civ_mcp.arena.channel_terms import normalize_action_audit
    from civ_mcp.arena.config import ChannelOptions

    harness = Seat0Harness(
        monkeypatch,
        [
            seat0_poll(7, active=True),
            *[seat0_poll(7, active=True) for _ in range(6)],
            seat0_poll(8, active=True),
        ],
    )
    # Initial normal pass is clear.  The first end request then bounces on a
    # newly surfaced decision blocker; cleanup cannot choose it, the delayed
    # repair clears it, and the coordinator re-fires.
    harness.blocker_queue = [[], [_RESEARCH], [_RESEARCH], []]
    _wc_env(monkeypatch, harness, handler_results=[False, True], status=None)
    options = CivOptions(channels=ChannelOptions(enabled=True))
    labels = ("normal", "wc", "recheck")
    canary = "PRIVATE-AUXILIARY-SUMMARY-CANARY"

    def policy_result(label, target):
        raw_summary = (
            f"{label} public note\n"
            "CHANNEL "
            f'{{"action":"send_message","to_player":2,'
            f'"text":"{canary}-{label}"}}\n'
            f"CHANNEL malformed-{canary}-{label}"
        )
        return {
            # Real adapters derive this top-level summary from the same final
            # text, so both copies intentionally carry the private protocol
            # lines.  Only the private finish input may retain them.
            "summary": raw_summary,
            "actions": [],
            "transcript": {
                "steps": [
                    {
                        "tool_name": "propose_trade",
                        "tool_args": {
                            "other_player_id": target,
                            "mode": "send",
                        },
                        "tool_result_full": f"OK:PROPOSED|{label}",
                    }
                ],
                "final_summary": raw_summary,
            },
        }

    class StagingPolicy(Seat0ScriptPolicy):
        async def __call__(self, gs, player_id, turn, **kwargs):
            index = len(self.calls)
            result = await super().__call__(gs, player_id, turn, **kwargs)
            kwargs["channel_context"].dispatch(
                "send_message",
                {"to_player": 2, "text": f"{labels[index]}-api"},
            )
            return result

    policy = StagingPolicy(
        harness,
        [
            policy_result("normal", 2),
            policy_result("wc", 3),
            policy_result("recheck", 4),
        ],
        options=options,
    )
    runtime = FakeChannelRuntime(
        run_id="seat0-channel-recheck",
        enabled_players=frozenset({0, 2}),
        events=harness.events,
    )
    _patch_channel_open(monkeypatch, runtime)
    conn = Seat0CapsConn()
    gs = FakeGSWithConn(conn)

    async def fake_wc():
        return _WCStatus(fires=True, resolutions=1)

    gs.get_world_congress = fake_wc
    sink = EventSink(harness)
    config = _seat0_cfg(
        tmp_path,
        players=[
            PlayerSpec(0, "local", "m", options=options),
            PlayerSpec(2, "local", "m", options=_channel_options()),
        ],
        run_id="seat0-channel-recheck",
        idle_poll_limit=30,
    )

    result = await run_arena(conn, gs, config, policy=policy, transcript=sink)

    assert [call[:2] for call in policy.calls] == [(0, 7), (0, 7), (0, 7)]
    normal_channel = {
        name: policy.calls[0][2][name]
        for name in ("channel_context", "channel_block", "master_block")
    }
    for _, _, kwargs in policy.calls[1:]:
        assert kwargs["channel_context"] is normal_channel["channel_context"]
        assert kwargs["channel_block"] == normal_channel["channel_block"]
        assert kwargs["master_block"] == normal_channel["master_block"]
    assert runtime.calls.count("admit:0:7") == 1
    assert runtime.calls.count("finish:0:7") == 1
    assert [action.action.text for action in runtime.finish_staged_actions[0]] == [
        "normal-api",
        "wc-api",
        "recheck-api",
    ]
    private_result = runtime.finish_results[0]
    assert private_result["transcript"]["final_summary"] == "\n".join(
        policy_result(label, target)["transcript"]["final_summary"]
        for label, target in zip(labels, (2, 3, 4), strict=True)
    )
    assert [action.tool_args["other_player_id"] for action in normalize_action_audit(
        private_result, actor=0, turn=7
    )] == [2, 3, 4]
    names = harness.names()
    assert names.index("policy", names.index("end_turn")) < names.index("finish:0:7")
    assert names.index("finish:0:7") < names.index("reconcile:0:8")
    assert names.index("finish:0:7") < names.index("record")
    assert names.count("end_turn") == 2
    assert result["seat0_turns_played"] == 1
    assert canary in private_result["transcript"]["final_summary"]
    # Parseable and malformed protocol lines from normal, WC, and delayed
    # repair stay private; public prose survives in record/log summaries.
    assert canary not in repr(sink.records)
    assert canary not in repr(result["log"])
    assert sink.records[0]["final_summary"] == "normal public note"
    assert sink.records[0]["seat0"]["normal"]["summary"] == "normal public note"
    assert sink.records[0]["seat0"]["repair"]["summary"] == "recheck public note"


@pytest.mark.asyncio
async def test_seat0_drain_diplomacy_pass_reuses_capture_and_supplies_trade_audit(
    monkeypatch, tmp_path
):
    """A standalone diplomacy policy pass during the captured turn can accept
    an official trade and thereby violate a private ``dont_trade_with`` term.
    Its committed action audit and channel outputs must reach the same runtime
    finish before adjudication, without another admission or public leakage."""
    from civ_mcp.arena.channel_terms import normalize_action_audit
    from civ_mcp.arena.config import ChannelOptions

    monkeypatch.setattr(coordinator_mod, "SEAT0_DIPLO_DRAIN_POLLS", 4)
    harness = Seat0Harness(
        monkeypatch,
        [
            seat0_poll(7, active=True),
            *[seat0_poll(7, active=False) for _ in range(6)],
            seat0_poll(8, active=True),
        ],
    )
    sessions = _AnsweringSessions("2#5")
    monkeypatch.setattr(seat0_mod, "query_local_player_sessions", sessions)
    options = CivOptions(channels=ChannelOptions(enabled=True))
    normal_summary = (
        'CHANNEL {"action":"send_message","to_player":2,"text":"normal-raw"}'
    )
    diplomacy_summary = (
        'CHANNEL {"action":"send_message","to_player":2,"text":"diplo-raw"}'
    )
    behaviors = [
        {
            "summary": "normal public summary",
            "actions": [],
            "transcript": {"steps": [], "final_summary": normal_summary},
        },
        {
            "summary": "diplomacy public summary",
            "actions": [],
            "transcript": {
                "steps": [
                    {
                        "tool_name": "respond_to_trade",
                        "tool_args": {"other_player_id": 2, "accept": True},
                        "tool_result_full": "OK:DEAL_ACCEPTED|Rome",
                    }
                ],
                "final_summary": diplomacy_summary,
            },
        },
    ]

    class AnsweringStagingPolicy(Seat0ScriptPolicy):
        async def __call__(self, gs, player_id, turn, **kwargs):
            index = len(self.calls)
            result = await super().__call__(gs, player_id, turn, **kwargs)
            kwargs["channel_context"].dispatch(
                "send_message",
                {"to_player": 2, "text": f"pass-{index}-api"},
            )
            if "PENDING DIPLOMACY" in kwargs.get("blocker_block", ""):
                sessions.value = ""
            return result

    policy = AnsweringStagingPolicy(harness, behaviors, options=options)
    runtime = FakeChannelRuntime(
        run_id="seat0-channel-diplomacy",
        enabled_players=frozenset({0, 2}),
        events=harness.events,
    )
    _patch_channel_open(monkeypatch, runtime)
    sink = EventSink(harness)
    config = _seat0_cfg(
        tmp_path,
        players=[
            PlayerSpec(0, "local", "m", options=options),
            PlayerSpec(2, "local", "m", options=_channel_options()),
        ],
        run_id="seat0-channel-diplomacy",
        idle_poll_limit=30,
    )

    result = await run_arena(
        Seat0CapsConn(), FakeGS(), config, policy=policy, transcript=sink
    )

    assert [call[:2] for call in policy.calls] == [(0, 7), (0, 7)]
    for name in ("channel_context", "channel_block", "master_block"):
        if name == "channel_context":
            assert policy.calls[1][2][name] is policy.calls[0][2][name]
        else:
            assert policy.calls[1][2][name] == policy.calls[0][2][name]
    assert runtime.calls.count("admit:0:7") == 1
    assert runtime.calls.count("finish:0:7") == 1
    assert [action.action.text for action in runtime.finish_staged_actions[0]] == [
        "pass-0-api",
        "pass-1-api",
    ]
    private_result = runtime.finish_results[0]
    assert private_result["transcript"]["final_summary"] == (
        normal_summary + "\n" + diplomacy_summary
    )
    audit = normalize_action_audit(private_result, actor=0, turn=7)
    assert [(a.tool_name, a.tool_args["other_player_id"]) for a in audit] == [
        ("respond_to_trade", 2)
    ]
    names = harness.names()
    assert names.index("policy", names.index("end_turn")) < names.index("finish:0:7")
    assert names.index("finish:0:7") < names.index("reconcile:0:8")
    assert names.index("finish:0:7") < names.index("record")
    assert result["seat0_turns_played"] == 1
    assert "diplo-raw" not in sink.records[0]["final_summary"]
    assert all("diplo-raw" not in repr(entry) for entry in result["log"])


@pytest.mark.asyncio
async def test_seat0_channel_capture_finishes_once_after_policy_exception_repair(
    monkeypatch, tmp_path
):
    """A caught normal-policy Exception keeps fail-open repair behavior, then
    closes the one admission once after the repaired turn advances."""
    from civ_mcp.arena.config import ChannelOptions

    harness = Seat0Harness(
        monkeypatch,
        [seat0_poll(7, active=True), seat0_poll(7, active=False), seat0_poll(8)],
    )
    options = CivOptions(channels=ChannelOptions(enabled=True))
    repair_summary = (
        'CHANNEL {"action":"send_message","to_player":2,"text":"repaired"}'
    )
    policy = Seat0ScriptPolicy(
        harness,
        [
            RuntimeError("normal policy failed"),
            {
                "summary": "repair complete",
                "actions": [],
                "transcript": {"steps": [], "final_summary": repair_summary},
            },
        ],
        options=options,
    )
    runtime = FakeChannelRuntime(
        run_id="seat0-channel-policy-exception",
        enabled_players=frozenset({0, 2}),
        events=harness.events,
    )
    _patch_channel_open(monkeypatch, runtime)
    sink = EventSink(harness)
    config = _seat0_cfg(
        tmp_path,
        players=[
            PlayerSpec(0, "local", "m", options=options),
            PlayerSpec(2, "local", "m", options=_channel_options()),
        ],
        run_id="seat0-channel-policy-exception",
    )

    result = await run_arena(
        Seat0CapsConn(), FakeGS(), config, policy=policy, transcript=sink
    )

    assert len(policy.calls) == 2
    assert policy.calls[1][2]["channel_context"] is policy.calls[0][2][
        "channel_context"
    ]
    assert runtime.calls.count("admit:0:7") == 1
    assert runtime.calls.count("finish:0:7") == 1
    assert runtime.finish_results[0]["transcript"]["final_summary"] == repair_summary
    assert result["seat0_turns_played"] == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "advanced"


@pytest.mark.asyncio
async def test_seat0_human_pending_deadline_finishes_active_channel_capture_once(
    monkeypatch, tmp_path
):
    """A channel-enabled hard blocker retains its admission during the bounded
    human window, then finalizes it exactly once before outer cleanup."""
    from civ_mcp.arena.config import ChannelOptions

    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    hard = _blocker("ENDTURN_BLOCKING_FUTURE_CHOICE", "human decision required")
    harness.blocker_queue = [[hard], [hard]]
    options = CivOptions(channels=ChannelOptions(enabled=True))
    policy = Seat0ScriptPolicy(harness, [_returned("normal")], options=options)
    runtime = FakeChannelRuntime(
        run_id="seat0-channel-human-pending",
        enabled_players=frozenset({0, 2}),
        acknowledgements=2,
        events=harness.events,
    )
    _patch_channel_open(monkeypatch, runtime)
    sink = SnapshotEventSink(harness)
    config = _seat0_cfg(
        tmp_path,
        players=[
            PlayerSpec(0, "local", "m", options=options),
            PlayerSpec(2, "local", "m", options=_channel_options()),
        ],
        run_id="seat0-channel-human-pending",
        seat0_human_pending_poll_limit=2,
    )

    result = await run_arena(
        Seat0CapsConn(), FakeGS(), config, policy=policy, transcript=sink
    )

    assert result["seat0_human_pending"] == 1
    assert runtime.calls.count("admit:0:7") == 1
    assert runtime.calls.count("finish:0:7") == 1
    names = harness.names()
    assert names.index("finish:0:7") < names.index("record")
    assert names.index("finish:0:7") < names.index("restore_local")
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "human_pending"
    assert sink.records[0]["channels"] == {
        "enabled": True,
        "acknowledgements": 2,
        "error": "",
    }


@pytest.mark.asyncio
async def test_seat0_policy_baseexception_finishes_capture_once_in_cleanup(
    monkeypatch, tmp_path
):
    """Policy cancellation still propagates, but the admitted private capture
    is closed and cleared before the existing best-effort human handback.  An
    ordinary finish Exception is fail-open and cannot replace that cancellation."""
    from civ_mcp.arena.config import ChannelOptions

    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    options = CivOptions(channels=ChannelOptions(enabled=True))
    policy = Seat0ScriptPolicy(
        harness, [asyncio.CancelledError()], options=options
    )
    runtime = FakeChannelRuntime(
        run_id="seat0-channel-cancel",
        enabled_players=frozenset({0, 2}),
        finish_error=RuntimeError("channel cleanup failed"),
        events=harness.events,
    )
    _patch_channel_open(monkeypatch, runtime)
    sink = SnapshotEventSink(harness)
    config = _seat0_cfg(
        tmp_path,
        players=[
            PlayerSpec(0, "local", "m", options=options),
            PlayerSpec(2, "local", "m", options=_channel_options()),
        ],
        run_id="seat0-channel-cancel",
    )

    with pytest.raises(asyncio.CancelledError):
        await run_arena(
            Seat0CapsConn(), FakeGS(), config, policy=policy, transcript=sink
        )

    assert runtime.calls.count("admit:0:7") == 1
    assert runtime.calls.count("finish:0:7") == 1
    names = harness.names()
    assert names.index("finish:0:7") < names.index("record")
    assert names.index("finish:0:7") < names.index("restore_local")
    assert len(sink.records) == 1
    assert sink.records[0]["seat0"]["terminal_state"] == "interrupted"
    assert sink.records[0]["channels"] == {
        "enabled": True,
        "acknowledgements": 0,
        "error": "RuntimeError('channel cleanup failed')",
    }


@pytest.mark.asyncio
async def test_seat0_outer_shutdown_finishes_capture_without_replay(
    monkeypatch, tmp_path
):
    """An unexpected outer-loop shutdown after the first end request uses the
    final cleanup seam and cannot replay finish during human handback."""
    from civ_mcp.arena.config import ChannelOptions

    harness = Seat0Harness(monkeypatch, [seat0_poll(7, active=True)])
    first_poll = hook_mod.poll
    poll_calls = 0

    async def shutdown_poll(conn):
        nonlocal poll_calls
        poll_calls += 1
        if poll_calls > 1:
            raise RuntimeError("coordinator shutdown")
        return await first_poll(conn)

    monkeypatch.setattr(hook_mod, "poll", shutdown_poll)
    options = CivOptions(channels=ChannelOptions(enabled=True))
    policy = Seat0ScriptPolicy(harness, [_returned("normal")], options=options)
    runtime = FakeChannelRuntime(
        run_id="seat0-channel-shutdown",
        enabled_players=frozenset({0, 2}),
        events=harness.events,
    )
    _patch_channel_open(monkeypatch, runtime)
    config = _seat0_cfg(
        tmp_path,
        players=[
            PlayerSpec(0, "local", "m", options=options),
            PlayerSpec(2, "local", "m", options=_channel_options()),
        ],
        run_id="seat0-channel-shutdown",
    )

    with pytest.raises(RuntimeError, match="coordinator shutdown"):
        await run_arena(Seat0CapsConn(), FakeGS(), config, policy=policy)

    assert runtime.calls.count("admit:0:7") == 1
    assert runtime.calls.count("finish:0:7") == 1
    names = harness.names()
    assert names.index("finish:0:7") < names.index("restore_local")
