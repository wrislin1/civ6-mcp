"""Opt-in transport timing and retry policy for `GameConnection.execute_read`.

The tuner wire functions are replaced by fakes that advance a controlled
clock (`civ_mcp.connection._clock`), so every phase duration below is a
value the transport measured, not one it could have hard-coded.
"""
from __future__ import annotations

import asyncio

import pytest

from civ_mcp import connection as connection_module
from civ_mcp import tuner_client
from civ_mcp.connection import GameConnection
from civ_mcp.lua._helpers import SENTINEL

PRE_DRAIN = 0.013   # deliberately not 0.1 / 0.2: the drains are measured
POST_DRAIN = 0.021
SEND = 0.002
PER_MESSAGE = 0.05


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeWriter:
    def is_closing(self):
        return False


def out(text):
    return tuner_client.Message(tag=3, payload=f"O\x00GameCore_Tuner: {text}")


class Wire:
    """Fake tuner wire: scripted responses, recorded sends, clocked phases."""

    def __init__(self, clock, responses):
        self.clock = clock
        self.responses = list(responses)  # Message | BaseException per recv
        self.sends = []
        self.drains = []
        self.block_drain = None  # timeout value whose drain never returns

    async def drain_messages(self, reader, timeout=0.5):
        self.drains.append(timeout)
        if timeout == self.block_drain:
            await asyncio.Event().wait()
        self.clock.now += PRE_DRAIN if timeout == 0.1 else POST_DRAIN
        return []

    async def send_message(self, writer, tag, payload):
        self.sends.append(payload)
        self.clock.now += SEND

    async def recv_message_timeout(self, reader, timeout=2.0):
        self.clock.now += PER_MESSAGE
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(connection_module, "_clock", clock)
    return clock


def make_conn(monkeypatch, clock, responses):
    wire = Wire(clock, responses)
    for name in ("drain_messages", "send_message", "recv_message_timeout"):
        monkeypatch.setattr(tuner_client, name, getattr(wire, name))
    conn = GameConnection()
    conn._reader = object()
    conn._writer = FakeWriter()
    conn.gamecore_index = 1
    conn.ingame_index = 2
    return conn, wire


def ok_responses():
    return [out("BEGIN|x"), out("END|y"), out(SENTINEL)]


async def test_phases_are_measured_with_controlled_clock(monkeypatch, clock):
    conn, wire = make_conn(monkeypatch, clock, ok_responses())
    timing: dict = {}
    lines = await conn.execute_read("print(1)", timing=timing)
    assert lines == ["BEGIN|x", "END|y"]
    assert wire.drains == [0.1, 0.2]
    assert timing["pre_drain_s"] == pytest.approx(PRE_DRAIN)
    assert timing["post_drain_s"] == pytest.approx(POST_DRAIN)
    assert timing["response_wait_s"] == pytest.approx(SEND + 3 * PER_MESSAGE)
    assert timing["lock_wait_s"] == 0
    assert timing["connect_s"] == 0
    assert timing["lua_executions"] == 1
    assert set(timing) == {"connect_s", "lock_wait_s", "pre_drain_s",
                           "response_wait_s", "post_drain_s", "lua_executions"}


async def test_lines_identical_with_and_without_timing(monkeypatch, clock):
    conn, wire = make_conn(monkeypatch, clock, ok_responses() + ok_responses())
    plain = await conn.execute_read("print(1)")
    timed = await conn.execute_read("print(1)", timing={})
    assert plain == timed == ["BEGIN|x", "END|y"]
    assert wire.drains == [0.1, 0.2, 0.1, 0.2]
    assert len(wire.sends) == 2


async def test_connect_time_is_measured(monkeypatch, clock):
    conn, wire = make_conn(monkeypatch, clock, ok_responses())
    writer = conn._writer
    conn._writer = None

    async def fake_connect():
        clock.now += 1.5
        conn._writer = writer

    monkeypatch.setattr(conn, "connect", fake_connect)
    timing: dict = {}
    await conn.execute_read("print(1)", timing=timing)
    assert timing["connect_s"] == pytest.approx(1.5)


async def test_lock_wait_is_measured_when_lock_is_held(monkeypatch, clock):
    conn, wire = make_conn(monkeypatch, clock, ok_responses())
    timing: dict = {}
    await conn._lock.acquire()
    task = asyncio.create_task(conn.execute_read("print(1)", timing=timing))
    for _ in range(5):
        await asyncio.sleep(0)
    assert wire.sends == []
    clock.now += 3.0
    conn._lock.release()
    await task
    assert timing["lock_wait_s"] == pytest.approx(3.0)
    assert timing["lua_executions"] == 1


async def test_lua_executions_counts_the_send(monkeypatch, clock):
    conn, wire = make_conn(monkeypatch, clock, [asyncio.IncompleteReadError(b"", 8)])
    timing: dict = {}
    with pytest.raises(asyncio.IncompleteReadError):
        await conn.execute_read("print(1)", timing=timing, retry_on_disconnect=False)
    assert len(wire.sends) == 1
    assert timing["lua_executions"] == 1
    assert "response_wait_s" in timing  # partial observation survives the error
    assert "post_drain_s" not in timing  # never ran: absent, not zero


async def test_no_hidden_retry_when_disabled(monkeypatch, clock):
    conn, wire = make_conn(monkeypatch, clock,
                           [asyncio.IncompleteReadError(b"", 8)] + ok_responses())
    reconnects = []

    async def fake_reconnect():
        reconnects.append(1)

    monkeypatch.setattr(conn, "reconnect", fake_reconnect)
    with pytest.raises(asyncio.IncompleteReadError):
        await conn.execute_read("print(1)", retry_on_disconnect=False)
    assert len(wire.sends) == 1
    assert reconnects == []


async def test_default_read_still_retries_once(monkeypatch, clock):
    conn, wire = make_conn(monkeypatch, clock,
                           [asyncio.IncompleteReadError(b"", 8)] + ok_responses())
    reconnects = []

    async def fake_reconnect():
        reconnects.append(1)

    monkeypatch.setattr(conn, "reconnect", fake_reconnect)
    timing: dict = {}
    lines = await conn.execute_read("print(1)", timing=timing)
    assert lines == ["BEGIN|x", "END|y"]
    assert len(wire.sends) == 2
    assert reconnects == [1]
    assert timing["lua_executions"] == 2


async def test_cancellation_during_post_drain_keeps_partial_timing(monkeypatch, clock):
    conn, wire = make_conn(monkeypatch, clock, ok_responses())
    wire.block_drain = 0.2
    timing: dict = {}
    task = asyncio.create_task(conn.execute_read("print(1)", timing=timing))
    for _ in range(20):
        await asyncio.sleep(0)
    assert wire.drains == [0.1, 0.2]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert timing["pre_drain_s"] == pytest.approx(PRE_DRAIN)
    assert timing["response_wait_s"] == pytest.approx(SEND + 3 * PER_MESSAGE)
    assert timing["lua_executions"] == 1
    assert timing["post_drain_s"] == 0  # interrupted phase: partial observation
    assert not conn._lock.locked()


async def test_cancellation_during_pre_drain_sends_nothing(monkeypatch, clock):
    conn, wire = make_conn(monkeypatch, clock, ok_responses())
    wire.block_drain = 0.1
    timing: dict = {}
    task = asyncio.create_task(conn.execute_read("print(1)", timing=timing))
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire.sends == []
    assert "pre_drain_s" in timing
    assert not {"lua_executions", "response_wait_s", "post_drain_s"} & set(timing)
    assert not conn._lock.locked()


async def test_write_path_unchanged(monkeypatch, clock):
    conn, wire = make_conn(monkeypatch, clock, ok_responses())
    assert await conn.execute_write("print(1)") == ["BEGIN|x", "END|y"]
    assert wire.sends == ["CMD:2:print(1)"]


async def test_execute_mutation_targets_gamecore_and_never_retries(monkeypatch, clock):
    conn, wire = make_conn(monkeypatch, clock, ok_responses())
    lines = await conn.execute_mutation("UnitManager.InitUnit(0, 'UNIT_WARRIOR', 1, 1)")
    assert lines == ["BEGIN|x", "END|y"]
    assert wire.sends == [f"CMD:{conn.gamecore_index}:UnitManager.InitUnit(0, 'UNIT_WARRIOR', 1, 1)"]

    conn, wire = make_conn(monkeypatch, clock,
                           [asyncio.IncompleteReadError(b"", 8)] + ok_responses())
    reconnects = []

    async def fake_reconnect():
        reconnects.append(1)

    monkeypatch.setattr(conn, "reconnect", fake_reconnect)
    with pytest.raises(asyncio.IncompleteReadError):
        await conn.execute_mutation("print(1)")
    assert len(wire.sends) == 1 and reconnects == []
