import pytest

from civ_mcp.arena.hook import (
    build_inject_lua,
    POLL_LUA,
    parse_poll,
    PuppetState,
    end_turn,
)


class RecordingConn:
    """Minimal fake: records the Lua sent to each execution context."""

    def __init__(self):
        self.reads: list[str] = []
        self.writes: list[str] = []

    async def execute_read(self, lua: str) -> list[str]:
        self.reads.append(lua)
        return []

    async def execute_write(self, lua: str) -> list[str]:
        self.writes.append(lua)
        return ["OK:TURN_ENDED"]


def test_poll_lua_guards_seat0_activity_probe():
    assert "local seat0OK, seat0Active = pcall(function()" in POLL_LUA
    assert "Players[0] ~= nil and Players[0]:IsTurnActive()" in POLL_LUA
    assert "tostring((seat0OK and seat0Active) or false)" in POLL_LUA
    assert POLL_LUA.index("pcall(function()") < POLL_LUA.index('print("LOCAL|"')


def test_inject_lua_contains_ids_and_switch():
    lua = build_inject_lua([1, 2])
    assert "SetLocalPlayerAndObserver" in lua
    assert "__pt_puppets" in lua and "[1]=true" in lua and "[2]=true" in lua
    assert "__pt_puppets = { [1]=true, [2]=true }" in lua

def test_parse_poll():
    lines = ["LOCAL|1", "TURN|2", "ACTIVE|true", "LAST|1"]
    st = parse_poll(lines)
    assert st == PuppetState(local=1, turn=2, active=True, last=1)


def test_parse_poll_includes_seat0_active():
    state = parse_poll([
        "LOCAL|0", "TURN|17", "ACTIVE|false", "LAST|2",
        "SEAT0_ACTIVE|true",
    ])
    assert state == PuppetState(0, 17, False, 2, seat0_active=True)


def test_parse_poll_old_payload_defaults_seat0_inactive():
    state = parse_poll(["LOCAL|2", "TURN|17", "ACTIVE|true", "LAST|0"])
    assert state.seat0_active is False


@pytest.mark.asyncio
async def test_end_turn_is_the_only_hook_write_operation():
    conn = RecordingConn()
    await end_turn(conn)
    assert len(conn.writes) == 1
    assert "ACTION_ENDTURN" in conn.writes[0]
    assert conn.reads == []
