# Arena Autonomous Seat-0 Piloting Implementation Plan

> **Status:** Tasks 1–9 ✓ DONE — executed 2026-07-14/15 on branch
> `arena-seat0-piloting` (tip `be30516`), then hardened by two review fix
> waves (`2026-07-15-arena-seat0-review-fixes.md`,
> `2026-07-15-arena-seat0-review2-fixes.md`); merged to main at `845ae09`,
> 1194 tests green. REMAINING: Task 10 attended live gates on the gaming PC —
> all gates BLOCKED/NOT EXERCISED as of 2026-07-15, see
> `2026-07-14-arena-seat0-live-gates.md`. Do not re-execute Tasks 1–9.
> Task-8 wording note: riz affirmed (2026-07-15) the implemented task-tally
> reading — count `turn_kind != "failed"` (slept turns carry real
> `run_pre_model_tasks` follow-through) — over this plan's literal
> played-only text; drivers/standing-memory stay played-only.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a configured arena policy play local seat 0, resolve every strategic choice itself, and advance the game without routine human input while retaining a bounded, visible hard-block escape hatch.

**Architecture:** Seat 0 stays local and reuses the coordinator's existing captured-turn preparation, memory, task-tracker, policy, exclusive-handoff, and transcript pipeline. A focused `arena/seat0.py` module owns the per-turn phase machine, blocker classification, mechanical cleanup, repair prompt, result merge, and best-effort recovery save. The coordinator owns admission budgets and non-blocking polling; `hook.end_turn()` is the only new InGame hook operation. A test-only `provider: scripted` PlayerSpec makes the mixed stage-1 gate reproducible without a seat-0 top-level flag.

**Tech Stack:** Python 3.12, asyncio, dataclasses + `StrEnum`, pytest/pytest-asyncio, FireTuner GameCore/InGame Lua, JSONL arena transcripts, existing `GameConnection`, `GameState`, and `game_lifecycle.save_game` APIs.

**Spec:** `docs/superpowers/specs/2026-07-14-arena-seat0-piloting-design.md` at reviewed commit `f21791c`. Read it before implementation; it is authoritative where this plan is silent.

## Global Constraints

- Start implementation with `superpowers:using-git-worktrees` on an isolated branch named `arena-seat0-piloting`. End with an unmerged local branch and summary. Never merge or push without riz's explicit direction.
- Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before any completion claim.
- For the live tasks, read and follow the `civ6-arena-live` skill before touching the gaming PC or FireTuner state.
- Test command: `uv run --extra test pytest tests/ -q`. Always scope collection to `tests/`; bare `pytest` can collect live scripts.
- Preserve unrelated workspace state. In particular, do not stage `.serena/memories/`.
- The policy has full strategic authority. Coordinator code must never choose promotions, research, civics, production, policies, governors, beliefs, envoys, dedications, World Congress votes, city-capture outcomes, stacked-unit moves, spy escape routes, or any other strategic option.
- The only coordinator-owned cleanup is: `finish_units(0)` after a successful policy return, dismissal of a stale notification only after Lua proves the underlying choice is already set, and acknowledgement of a purely informational prompt.
- `end_turn` remains unavailable to every arena policy. Do not add it to any in-process registry, CLI allowlist, or server arena tool set.
- Execution context is load-bearing:
  - GameCore / `execute_read`: hook inject, disable, poll, finish-units, restore-local, and every poll during AI processing.
  - InGame / `execute_write`: blocker query/cleanup, recovery save, and `hook.end_turn`, only while seat 0 is local and `seat0_active`.
- A normal call plus its optional repair call is one logical turn and one shared budget charge. Admission budgets never abort an in-flight seat-0 drain.
- A seat-0 transcript record is append-only and written exactly once at `advanced`, `human_pending`, or `interrupted`; never write at `ai_processing`.
- Commit messages use the existing `feat(arena):`, `test(arena):`, or `docs(arena):` style. Run the task's focused tests before each commit.

## Stable Interfaces and Record Schema

Implement these names unless an existing symbol makes a mechanically equivalent name clearly better:

`src/civ_mcp/arena/config.py` exports
`resolved_puppet_ids(config: ArenaConfig) -> list[int]` and
`validate_arena_config(config: ArenaConfig) -> None`.

`src/civ_mcp/arena/hook.py` extends its poll record and exports `end_turn(conn)`:

```python
@dataclass(frozen=True)
class PuppetState:
    local: int
    turn: int
    active: bool
    last: int | None
    seat0_active: bool = False
```

`src/civ_mcp/arena/seat0.py` exports `Seat0TurnState`, `BlockerGroups`,
`classify_blockers`, `build_blocker_block`, `query_blockers`,
`apply_mechanical_cleanup`, `save_recovery_anchor`, and
`merge_policy_attempts`, with the typed signatures specified in Tasks 3 and 6.
Its public phase vocabulary is:

```python
class Seat0Phase(StrEnum):
    READY = "ready"
    POLICY_PLAYED = "policy_played"
    END_FIRED = "end_fired"
    AI_PROCESSING = "ai_processing"
    ADVANCED = "advanced"
    HUMAN_PENDING = "human_pending"
    INTERRUPTED = "interrupted"
```

Seat-0 fields live under one namespaced transcript object so analysis and future schema migrations do not collide with generic policy payloads:

```json
{
  "player_id": 0,
  "turn_kind": "played",
  "seat0": {
    "normal": {"completed": true, "summary": "turn complete", "error": ""},
    "repair": {"attempted": false, "completed": false, "summary": "", "error": ""},
    "blocker_snapshots": [{"stage": "after_normal", "blockers": []}],
    "mechanical_cleanup": [],
    "autosave": {"name": "0_MCP_0007", "attempts": []},
    "end_turn_requests": 1,
    "terminal_state": "advanced"
  }
}
```

`turn_kind` is `"played"` when either policy call returns a result and `"failed"` only when neither call returns. Repair steps, invalid calls, token usage, USD, and wall-clock time are summed into the generic top-level policy payload; normal and repair summaries/errors remain separate under `seat0`.

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/civ_mcp/arena/config.py` | Modify | Optional puppet IDs, derivation/validation, scripted provider identity |
| `src/civ_mcp/arena/experiment.py` | Modify | Exclude seat 0 from derived puppets; validate seat-0 attention |
| `src/civ_mcp/arena/arena.py` | Modify | CLI derivation, config validation, scripted policy wiring/help |
| `src/civ_mcp/arena/hook.py` | Modify | Seat-0 poll bit and InGame end-turn wrapper |
| `src/civ_mcp/lua/notifications.py` | Modify | Closed-list stale/informational blocker cleanup builders |
| `src/civ_mcp/lua/__init__.py` | Modify | Export new notification builders |
| `src/civ_mcp/arena/seat0.py` | Create | Phase state, blocker authority boundary, cleanup, autosave, result merge |
| `src/civ_mcp/arena/prompting.py` | Modify | Focused `blocker_block` injection |
| `src/civ_mcp/arena/agent.py` | Modify | In-process repair mode and prompt metadata |
| `src/civ_mcp/arena/cli_agent.py` | Modify | CLI repair mode with same lockdown |
| `src/civ_mcp/arena/coordinator.py` | Modify | Shared seat admission, repair/end drain, transcript terminalization |
| `src/civ_mcp/arena/analyze.py` | Modify | Explicit failed turns and player-0 regression coverage |
| `experiments/arena-seat0-scripted-smoke.yaml` | Create | Stage-1 mixed scripted-seat0 + two local-LLM puppets |
| `experiments/arena-seat0-llm-smoke.yaml` | Create | Stage-2 CLI/local LLM seat-0 gate |
| `docs/superpowers/plans/2026-07-14-arena-seat0-live-gates.md` | Create | Stage 1-3 checklist and evidence log |
| `tests/arena/test_seat0.py` | Create | Pure state, blocker, cleanup, merge, autosave tests |
| `tests/arena/test_config.py`, `test_experiment.py`, `test_arena_wiring.py`, `test_hook.py`, `test_prompting.py`, `test_agent.py`, `test_cli_agent.py`, `test_coordinator.py`, `test_analyze.py`, `test_server_tool_gates.py` | Modify | Focused regressions |

Dependency order: Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 → Task 7. Task 8 depends on Task 3's schema. Task 9 depends on Tasks 1-8. Task 10 is live-only and last.

## Spec Coverage

| Approved design area | Implemented/tested in |
|---|---|
| Seat 0 as normal PlayerSpec; optional/explicit-empty puppets; attention off | Task 1 |
| GameCore seat-active poll; InGame non-blocking end request | Task 2 |
| Ready→played→end→AI→advanced state and duplicate prevention | Tasks 3, 5, 7 |
| Mechanical-only cleanup and hard/decision blocker classification | Tasks 2, 3, 6 |
| Same-policy one-shot focused repair with full strategic authority | Tasks 4, 6 |
| Best-effort `0_MCP_NNNN` save before end request | Tasks 3, 7 |
| Five-poll grace, three-request bound, no InGame during AI | Task 7 |
| Shared budgets, final-slot hook disable, in-flight drain | Tasks 5, 7 |
| One terminal transcript record with merged attempts | Tasks 3, 5-7 |
| Player-0 analysis; explicit failed turns isolated from success metrics | Task 8 |
| Scripted, LLM, and human-escape live gates | Tasks 9-10 |
| Reclaim→restore 0→disable and cancellation propagation | Tasks 5, 7, 9 |

---

### Task 1: Configuration derivation, validation, and seat-0 identity

**Files:**
- Modify: `src/civ_mcp/arena/config.py`
- Modify: `src/civ_mcp/arena/experiment.py`
- Modify: `src/civ_mcp/arena/arena.py`
- Test: `tests/arena/test_config.py`
- Test: `tests/arena/test_experiment.py`
- Test: `tests/arena/test_arena_wiring.py`

**Interfaces:** `ArenaConfig.puppet_ids: list[int] | None`; `resolved_puppet_ids`; `validate_arena_config`. `None` derives configured nonzero seats. An explicit list, including `[]`, is authoritative after validation.

- [ ] **Step 1: Add failing config unit tests**

Append tests equivalent to:

```python
def _seat(pid: int, *, attention: str = "off") -> PlayerSpec:
    return PlayerSpec(
        pid,
        "local",
        "m",
        options=CivOptions(attention=AttentionOptions(mode=attention)),
    )


def test_puppet_ids_none_derives_only_nonzero_configured_players():
    cfg = ArenaConfig(players=[_seat(0), _seat(2), _seat(4)], puppet_ids=None)
    assert resolved_puppet_ids(cfg) == [2, 4]


def test_explicit_empty_puppet_ids_stays_empty():
    cfg = ArenaConfig(players=[_seat(0), _seat(2)], puppet_ids=[])
    assert resolved_puppet_ids(cfg) == []


@pytest.mark.parametrize("ids", [[0], [2, 0]])
def test_explicit_puppet_ids_reject_seat_zero(ids):
    cfg = ArenaConfig(players=[_seat(0), _seat(2)], puppet_ids=ids)
    with pytest.raises(ValueError, match="seat 0"):
        validate_arena_config(cfg)


def test_explicit_puppet_ids_reject_unknown_and_duplicate_seats():
    with pytest.raises(ValueError, match="not configured"):
        validate_arena_config(ArenaConfig(players=[_seat(2)], puppet_ids=[3]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_arena_config(ArenaConfig(players=[_seat(2)], puppet_ids=[2, 2]))


def test_seat0_requires_attention_off():
    cfg = ArenaConfig(players=[_seat(0, attention="auto")], puppet_ids=[])
    with pytest.raises(ValueError, match="seat 0.*attention.mode.*off"):
        validate_arena_config(cfg)
```

- [ ] **Step 2: Add failing YAML and CLI derivation tests**

In `test_experiment.py`, load a YAML with seats `0, 2, 4` and assert `cfg.puppet_ids == [2, 4]`; assert the seat-0 `options.fingerprint()` is preserved exactly like a nonzero seat; add a YAML seat 0 with `attention: {mode: hybrid}` and assert a contextual `ValueError`. In `test_arena_wiring.py`, resolve `--player 0:local:m --player 2:local:m` and assert only `[2]` is a puppet.

- [ ] **Step 3: Run the focused tests and confirm RED**

Run:

```bash
uv run --extra test pytest tests/arena/test_config.py tests/arena/test_experiment.py tests/arena/test_arena_wiring.py -q
```

Expected: failures from the old `default_factory=list`, truthy fallback behavior, seat 0 appearing in derived puppets, and absent validation helpers.

- [ ] **Step 4: Implement the config helpers**

In `config.py`, change the field and add:

```python
@dataclass
class ArenaConfig:
    # existing fields unchanged
    puppet_ids: list[int] | None = None


def resolved_puppet_ids(config: ArenaConfig) -> list[int]:
    configured = {spec.player_id for spec in config.players}
    ids = (
        [spec.player_id for spec in config.players if spec.player_id != 0]
        if config.puppet_ids is None
        else list(config.puppet_ids)
    )
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate puppet ids {ids}")
    if 0 in ids:
        raise ValueError("seat 0 cannot appear in puppet_ids")
    unknown = sorted(set(ids) - configured)
    if unknown:
        raise ValueError(f"puppet ids are not configured players: {unknown}")
    return ids


def validate_arena_config(config: ArenaConfig) -> None:
    resolved_puppet_ids(config)
    seat0 = next((spec for spec in config.players if spec.player_id == 0), None)
    if seat0 is not None and seat0.options.attention.mode != "off":
        raise ValueError(
            "seat 0 requires attention.mode 'off' for autonomous piloting"
        )
```

Keep duplicate `players` validation at the YAML loader boundary. Call `validate_arena_config` before returning from both `load_experiment` and `resolve_config`, and once at the start of `run_arena` in Task 5 so programmatic callers cannot bypass it.

- [ ] **Step 5: Fix derivation sites and CLI help**

Use `[pid for pid in ids if pid != 0]` in `load_experiment`, and `[s.player_id for s in specs if s.player_id != 0]` in the non-config CLI path. Update the `--max-puppet-turns` and `--max-game-turns` help text to say they cover admitted arena policy turns, including seat 0.

- [ ] **Step 6: Run focused tests and commit**

Run the command from Step 3; expected PASS.

```bash
git add src/civ_mcp/arena/config.py src/civ_mcp/arena/experiment.py src/civ_mcp/arena/arena.py tests/arena/test_config.py tests/arena/test_experiment.py tests/arena/test_arena_wiring.py
git commit -m "feat(arena): validate seat-zero piloting config"
```

---

### Task 2: Hook polling, end-turn dispatch, and safe Lua cleanup primitives

**Files:**
- Modify: `src/civ_mcp/arena/hook.py`
- Modify: `src/civ_mcp/lua/notifications.py`
- Modify: `src/civ_mcp/lua/__init__.py`
- Test: `tests/arena/test_hook.py`
- Test: `tests/test_parsers.py`

**Interfaces:** `PuppetState.seat0_active`; `hook.end_turn(conn)`; closed-list Lua builders `build_clear_stale_end_turn_blocker(blocking_type)` and `build_mark_end_turn_prompt_seen(blocking_type)`.

- [ ] **Step 1: Write failing hook tests**

`tests/arena/test_hook.py` has no fake connection yet; define one at module level first:

```python
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
```

- [ ] **Step 2: Write failing Lua builder tests**

Test all accepted closed-list values and rejection of injected/unknown strings:

```python
@pytest.mark.parametrize("blocking_type", [
    "ENDTURN_BLOCKING_RESEARCH",
    "ENDTURN_BLOCKING_CIVIC",
    "ENDTURN_BLOCKING_PRODUCTION",
])
def test_stale_blocker_builder_checks_underlying_state(blocking_type):
    lua = build_clear_stale_end_turn_blocker(blocking_type)
    assert blocking_type in lua
    assert "NotificationManager.Dismiss" in lua
    assert "STALE_CLEARED" in lua


def test_cleanup_builders_reject_untrusted_blocker_names():
    with pytest.raises(ValueError):
        build_clear_stale_end_turn_blocker('ENDTURN_BLOCKING_RESEARCH; os.execute("x")')
```

Also assert the informational builder accepts only `ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE` and `ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK`.

- [ ] **Step 3: Run focused tests and confirm RED**

```bash
uv run --extra test pytest tests/arena/test_hook.py tests/test_parsers.py -q
```

- [ ] **Step 4: Implement the poll field and end-turn wrapper**

Add a robust GameCore line to `POLL_LUA`:

```lua
local seat0Active = Players[0] ~= nil and Players[0]:IsTurnActive()
print("SEAT0_ACTIVE|" .. tostring(seat0Active))
```

Parse it with a default of `False` so all existing fake payloads remain valid. Import `civ_mcp.lua as lq` and add:

```python
async def end_turn(conn):
    """Fire local seat 0's turn end in InGame context; never wait for a flip here."""
    return await conn.execute_write(lq.build_end_turn())
```

- [ ] **Step 5: Implement closed-list notification builders**

The stale builder must prove state before dismissal:

- Research: `Players[me]:GetTechs():GetResearchingTech() >= 0`.
- Civic: `Players[me]:GetCulture():GetProgressingCivic() >= 0`.
- Production: every owned city that can trigger the blocker has a nonzero current production hash; if any queue is empty/zero, return `NOT_SET` and dismiss nothing.

The informational builder may only:

- call `SetGovernmentChangeConsidered(true)` for `CONSIDER_GOVERNMENT_CHANGE`;
- issue `WORLD_CONGRESS_LOOKED_AT_AVAILABLE` and dismiss only the matching `WORLD_CONGRESS_LOOK` notification.

Do not port any strategic autoresolver from `end_turn.py`. In particular, do not port promotion, policy, envoy, city-action, dedication, vote, spy escape, research choice, or production choice logic.

- [ ] **Step 6: Export, verify, and commit**

Export both builders from `civ_mcp/lua/__init__.py`. Run Step 3; expected PASS.

```bash
git add src/civ_mcp/arena/hook.py src/civ_mcp/lua/notifications.py src/civ_mcp/lua/__init__.py tests/arena/test_hook.py tests/test_parsers.py
git commit -m "feat(arena): add seat-zero hook and cleanup primitives"
```

---

### Task 3: Pure seat-0 state machine, blocker boundary, autosave, and result merge

**Files:**
- Create: `src/civ_mcp/arena/seat0.py`
- Create: `tests/arena/test_seat0.py`

**Interfaces:** `Seat0Phase`, `Seat0TurnState`, `BlockerGroups`, blocker query/classification/formatting, mechanical cleanup, recovery save, policy-result merge.

- [ ] **Step 1: Write phase-machine tests**

Cover the exact lifecycle and bounds:

```python
def test_state_does_not_readmit_same_active_turn():
    state = Seat0TurnState()
    assert state.can_admit(turn=7, seat0_active=True)
    state.admit(7)
    state.mark_policy_played()
    state.mark_end_fired()
    for _ in range(5):
        assert state.observe(turn=7, seat0_active=True) == Seat0Poll.WAIT
    assert state.observe(turn=7, seat0_active=True) == Seat0Poll.RECHECK
    assert not state.can_admit(turn=7, seat0_active=True)


def state_after_one_end_request(turn: int) -> Seat0TurnState:
    state = Seat0TurnState()
    state.admit(turn)
    state.mark_policy_played()
    state.mark_end_fired()
    return state


def test_state_distinguishes_ai_processing_from_advance():
    state = state_after_one_end_request(turn=7)
    assert state.observe(turn=7, seat0_active=False) == Seat0Poll.WAIT
    assert state.phase is Seat0Phase.AI_PROCESSING
    assert state.observe(turn=8, seat0_active=True) == Seat0Poll.ADVANCED
    assert state.phase is Seat0Phase.ADVANCED


def test_end_turn_requests_are_bounded_at_three():
    state = Seat0TurnState()
    state.admit(7)
    for expected in (1, 2, 3):
        state.mark_end_fired()
        assert state.end_turn_requests == expected
    assert not state.may_fire_end_turn
```

Add tests for `human_pending`, `interrupted`, reset only after turn advance, and exactly one critical-event marker.

- [ ] **Step 2: Write blocker-authority tests**

Mechanical types are only:

```python
MECHANICAL_BLOCKERS = frozenset({
    "ENDTURN_BLOCKING_UNITS",
    "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK",
})
```

Research/civic/production start as decision blockers; `apply_mechanical_cleanup` may clear them only through Task 2's state-proving stale builder. Promotions, policies, governors, beliefs/religion, envoys, dedications, WC sessions/special sessions, city captures, and stacked units remain decision blockers. `ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE`, `UNKNOWN`, and any unmapped type are hard blockers because the arena registry exposes no pilot-accessible resolver.

Assert `build_blocker_block` contains each blocker message, `BLOCKING_TOOL_MAP` hint, the prior exception text when supplied, and an explicit instruction that this is the one repair pass and `end_turn` is unavailable.

- [ ] **Step 3: Write async cleanup/autosave tests**

Use a fake connection to prove:

- `query_blockers` uses only `execute_write`.
- `ENDTURN_BLOCKING_UNITS` calls `hook.finish_units(conn, 0)`.
- stale research returning `NOT_SET` is recorded but not claimed cleared.
- a WC-look acknowledgement is recorded as informational cleanup.
- `save_recovery_anchor(conn, 7)` calls `save_game(conn, "0_MCP_0007")` without an OS check.
- save exceptions return structured failure data instead of raising.

- [ ] **Step 4: Write merge tests**

Build small normal/repair payloads with steps, invalid calls, transcript tokens, usage USD, and wall time. Assert concatenation/sums and that the original dicts are not mutated. Include normal-missing/repair-present and both-missing synthetic cases.

- [ ] **Step 5: Run the new test module and confirm RED**

```bash
uv run --extra test pytest tests/arena/test_seat0.py -q
```

- [ ] **Step 6: Implement the state types**

Use `StrEnum` and a separate poll outcome enum so phase and action are not conflated:

```python
class Seat0Poll(StrEnum):
    WAIT = "wait"
    RECHECK = "recheck"
    ADVANCED = "advanced"


@dataclass
class Seat0TurnState:
    phase: Seat0Phase = Seat0Phase.READY
    turn: int | None = None
    repair_used: bool = False
    end_turn_requests: int = 0
    grace_polls: int = 0
    critical_emitted: bool = False
    record: dict | None = None
    record_written: bool = False

    def can_admit(self, *, turn: int, seat0_active: bool) -> bool:
        return self.phase is Seat0Phase.READY and seat0_active

    @property
    def needs_drain(self) -> bool:
        return self.phase in {
            Seat0Phase.POLICY_PLAYED,
            Seat0Phase.END_FIRED,
            Seat0Phase.AI_PROCESSING,
            Seat0Phase.HUMAN_PENDING,
        }
```

`observe` increments grace only in `END_FIRED` while the same turn is still active, returns `RECHECK` after five completed waits, enters `AI_PROCESSING` when inactive on the same turn, and enters `ADVANCED` whenever `turn != self.turn`. `mark_end_fired` increments before dispatch so an exception cannot create an unbounded retry.

- [ ] **Step 7: Implement blocker, cleanup, save, and merge helpers**

Use `lq.BLOCKING_TOOL_MAP`; never duplicate strategic tool hints. Preserve ordered blocker snapshots in the concrete shape `{"type": "ENDTURN_BLOCKING_RESEARCH", "message": "Choose Research"}`. `merge_policy_attempts` should use the first available transcript as metadata base, concatenate `steps` and `invalid_tool_calls`, sum numeric token/usage/wall fields, and keep the normal final summary when present (repair summary stays separately recorded).

- [ ] **Step 8: Verify and commit**

Run Step 5; expected PASS.

```bash
git add src/civ_mcp/arena/seat0.py tests/arena/test_seat0.py
git commit -m "feat(arena): add seat-zero completion state"
```

---

### Task 4: Focused repair prompt in both policy drivers

**Files:**
- Modify: `src/civ_mcp/arena/prompting.py`
- Modify: `src/civ_mcp/arena/agent.py`
- Modify: `src/civ_mcp/arena/cli_agent.py`
- Test: `tests/arena/test_prompting.py`
- Test: `tests/arena/test_agent.py`
- Test: `tests/arena/test_cli_agent.py`
- Test: `tests/arena/test_server_tool_gates.py`

**Interfaces:** keyword-only `blocker_block: str = ""` on `build_opening_prompt`, `LLMPolicy.__call__`, and `CLIAgentPolicy.__call__`; transcript injection flag `prompt_injections.blocker_repair`.

- [ ] **Step 1: Write failing prompt-order tests**

Assert this order when every block is present:

```text
briefing → standing memory → task tracker → wake digest → end-turn repair → turn announcement
```

Assert `blocker_block=""` leaves all existing snapshots byte-for-byte unchanged.

- [ ] **Step 2: Write failing in-process policy tests**

Invoke `LLMPolicy` with a fake backend and `blocker_block`. Assert:

- the repair text reaches the first model message;
- no standing-plan or attention instruction is appended in repair mode;
- no fresh full briefing is built in repair mode;
- the registry is otherwise unchanged and does not expose `end_turn`;
- transcript metadata has `blocker_repair: True`.

- [ ] **Step 3: Write failing CLI policy tests**

Capture the generated CLI prompt and assert the same focused behavior. Reassert both lockdown layers: `_DENIED_CIV6_TOOLS` contains `end_turn`, and the spawned environment still enables the server-side arena tool gate.

- [ ] **Step 4: Run focused tests and confirm RED**

```bash
uv run --extra test pytest tests/arena/test_prompting.py tests/arena/test_agent.py tests/arena/test_cli_agent.py tests/arena/test_server_tool_gates.py -q
```

- [ ] **Step 5: Implement repair mode**

In both policies, compute the shared flags first:

```python
repair_mode = bool(blocker_block)
include_standing_plan_instruction = (
    self.options.standing_plan_enabled and not repair_mode
)
include_attention_instruction = (
    self.options.attention_directives_enabled and not repair_mode
)
if repair_mode:
    briefing = Briefing()
```

In `LLMPolicy`, put its existing `resolve_n_ctx` and `maybe_build_briefing`
sequence in the `not repair_mode` branch; retain its existing `n_ctx`,
`playbook_chars`, and `tool_schema_chars` arguments exactly. In
`CLIAgentPolicy`, put its existing call in the `not repair_mode` branch:

```python
if not repair_mode:
    briefing = await maybe_build_briefing(
        gs,
        self.options,
        n_ctx=explicit_n_ctx(self.options.context_budget),
        playbook_chars=playbook_chars,
        tool_schema_chars=0,
        supplied=briefing,
    )
```

Pass `blocker_block` into `build_opening_prompt`. Do not pass memory/task/digest blocks from the coordinator's repair invocation in Task 6. Add the injection flag without removing existing flags:

```python
"blocker_repair": repair_mode,
```

The repair pass retains the same policy object, registry/tier, capability snapshot, model, context limit, timeout, and CLI environment. It is focused by prompt content, not by giving it a weaker strategic toolset.

- [ ] **Step 6: Verify and commit**

Run Step 4; expected PASS.

```bash
git add src/civ_mcp/arena/prompting.py src/civ_mcp/arena/agent.py src/civ_mcp/arena/cli_agent.py tests/arena/test_prompting.py tests/arena/test_agent.py tests/arena/test_cli_agent.py tests/arena/test_server_tool_gates.py
git commit -m "feat(arena): add focused blocker repair prompts"
```

---

### Task 5: Coordinator happy path and duplicate-play prevention

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py`
- Test: `tests/arena/test_coordinator.py`

**Interfaces:** seat 0 is a second captured-turn kind inside `run_arena`; puppet capture has priority; pending seat-0 state survives polls; result adds `seat0_turns_played`, `seat0_turns_failed`, and `seat0_human_pending` without changing `puppet_turns_played` semantics.

- [ ] **Step 1: Add a reusable seat-0 poll fixture**

In `test_coordinator.py`, add helpers that return real `PuppetState` objects instead of hand-building Lua lines:

```python
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
```

Monkeypatch `hook.poll`, `hook.inject`, `hook.finish_units`, `hook.end_turn`, `hook.restore_local`, `hook.disable`, `seat0.query_blockers`, `seat0.save_recovery_anchor`, and `asyncio.sleep` at their coordinator import sites. This keeps orchestration tests independent of Lua string matching.

- [ ] **Step 2: Write the failing happy-path test**

Poll sequence: active seat 0 on turn 7 → inactive seat 0 on turn 7 → active seat 0 on turn 8. Return no blockers and a successful autosave. Assert:

- policy `0` is called exactly once for turn 7;
- the normal memory/task/capability kwargs are accepted through the existing signature gate;
- `finish_units(conn, 0)` happens after the policy return;
- `hook.end_turn` happens once, with no `restore_local(0)` in the seat-0 body;
- no policy replay occurs on either post-request poll;
- the pending record is written only after the turn changes and has terminal `advanced`;
- `seat0_turns_played == 1`, `puppet_turns_played == 0`.

- [ ] **Step 3: Write shared-pipeline regressions**

Add tests that prove:

1. Seat 0 receives existing standing memory and task blocks keyed by player `0`.
2. A `needs_exclusive_tuner=True` seat-0 policy gets disconnect-before-call and reconnect-before-blocker-query.
3. `autoresolve.sweep_promotions` is not called for seat 0.
4. A poll representing an active puppet is serviced before any seat-0 work.
5. A config with no seat-0 PlayerSpec retains existing puppet-only behavior.

- [ ] **Step 4: Run the focused coordinator tests and confirm RED**

```bash
uv run --extra test pytest tests/arena/test_coordinator.py -q
```

- [ ] **Step 5: Add admission and in-flight loop guards**

At the top of `run_arena`:

```python
validate_arena_config(config)
puppet_ids = set(resolved_puppet_ids(config))
players_by_id = {spec.player_id: spec for spec in config.players}
seat0_spec = players_by_id.get(0)
seat0_state = Seat0TurnState()
```

Replace the loop's budget-only condition with explicit admission plus drain:

```python
def admission_open() -> bool:
    return (
        remaining > 0
        and (max_game_turns <= 0 or game_turns < max_game_turns)
    )


while deadline_polls > 0 and (admission_open() or seat0_state.needs_drain):
    st = await hook.poll(conn)
    # First observe/finalize an existing seat-0 turn, then give an actually
    # captured puppet priority, then consider a new local seat-0 admission.
```

An admitted seat-0 turn decrements `remaining` and increments `game_turns` once immediately after the normal policy attempt is accounted for. Repair never touches either counter. Refill `deadline_polls` and reset `idle_streak` on seat-0 admission exactly as for a puppet capture.

- [ ] **Step 6: Generalize the captured-turn predicate without duplicating preparation**

Use a single existing body:

```python
captured_puppet = st.active and st.local in puppet_ids and admission_open()
local_seat0 = (
    seat0_spec is not None
    and st.local == 0
    and st.seat0_active
    and seat0_state.can_admit(turn=st.turn, seat0_active=True)
    and admission_open()
)
if captured_puppet or local_seat0:
    is_seat0 = local_seat0
    # Existing policy preparation follows once.
```

For seat 0, force `attention_on = False` after Task 1 has already validated `mode == "off"`. Keep memory, task pre-model follow-through, overview snapshots, capability scan, briefing prebuild, signature-gated kwargs, and exclusive disconnect/reconnect shared.

Branch only where semantics differ:

- policy exceptions remain the old skip/restore behavior for puppets;
- seat-0 policy exceptions are retained for Task 6 repair;
- promotion sweep remains puppet-only;
- puppet transcript writes immediately and then finish/restores as today;
- seat-0 builds a pending record, performs Task 3 cleanup/save, calls `hook.end_turn`, and returns to polling without restore or a synchronous wait.

- [ ] **Step 7: Build and terminalize the initial seat-0 record**

Always synthesize a generic transcript payload for seat 0, even when `ScriptedPolicy` returns no `transcript`. Capture `state_after` before the first end-turn request while seat 0 is still active. Store the record in `seat0_state.record`; on the observed turn change, set `seat0.terminal_state = "advanced"`, write once, increment the result counters, and reset the state for the next turn only after terminalization.

- [ ] **Step 8: Verify puppet regressions and commit**

Run:

```bash
uv run --extra test pytest tests/arena/test_coordinator.py tests/arena/test_coordinator_router.py tests/arena/test_attention.py tests/arena/test_orphan_sweep.py -q
```

Expected: PASS, including all pre-seat0 puppet, attention, exclusive-handoff, and cleanup tests.

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py
git commit -m "feat(arena): pilot local seat zero through coordinator"
```

---

### Task 6: One-shot policy repair and bounded human escape

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py`
- Modify: `src/civ_mcp/arena/seat0.py`
- Test: `tests/arena/test_seat0.py`
- Test: `tests/arena/test_coordinator.py`

**Interfaces:** exactly one repair invocation per logical turn; direct `human_pending` for hard/inaccessible blockers; one structured CRITICAL event; GameCore-only polling after handoff.

- [ ] **Step 1: Write failing decision-repair tests**

Use blocker snapshots `RESEARCH → RESEARCH → []`: the first query follows normal return, mechanical cleanup does not choose a tech, and the second query triggers repair. The repair policy asserts its kwargs are exactly the signature-supported subset of:

```python
{
    "blocker_block": "== END-TURN REPAIR ==\nPrior policy error: gateway unavailable",
    "caps": original_caps,  # only when the policy accepts it
}
```

Assert no memory/task/digest/briefing kwarg is reinjected, pre-model tasks ran once, the same policy object was called twice, and the repair is not a second game turn or budget charge.

- [ ] **Step 2: Write failing policy-error matrix tests**

Cover all four outcomes:

| Normal call | Repair call | Expected `turn_kind` | Terminal path |
|---|---|---|---|
| returns | not needed | played | end-turn |
| raises | returns | played | end-turn if blockers clear |
| returns | raises | played | human_pending |
| raises | raises | failed | human_pending |

For a normal exception and empty blockers, the repair block must still include the prior exception and instruct the pilot to inspect/finish the turn.

- [ ] **Step 3: Write failing hard-block and unresolved tests**

Assert:

- `UNKNOWN` and `SPY_CHOOSE_ESCAPE_ROUTE` go directly to `human_pending` without a repair call;
- a supported decision blocker still present after repair enters `human_pending`;
- repeated polls of the same human-pending turn do not call policy, cleanup, blocker queries, or end-turn again;
- stderr/log receives exactly one structured event with `level=CRITICAL`, event name `seat0_human_pending`, turn, blockers, and policy errors.

- [ ] **Step 4: Write human-resume tests**

Poll the same human-pending turn twice, then a later game turn. Assert the coordinator:

- sleeps/polls GameCore once per second;
- writes the pending record once at entry to `human_pending`;
- resets only after the human advances;
- admits the next seat-0 turn only if both budgets remain;
- exits cleanly after `idle_poll_limit` if the human never advances.

- [ ] **Step 5: Run focused tests and confirm RED**

```bash
uv run --extra test pytest tests/arena/test_seat0.py tests/arena/test_coordinator.py -q
```

- [ ] **Step 6: Implement the post-policy authority flow**

For a successful policy result:

```python
await hook.finish_units(conn, 0)
cleanup.append({"action": "finish_units", "result": "requested"})
after_normal = await query_blockers(conn)
cleanup.extend(await apply_mechanical_cleanup(conn, after_normal))
after_cleanup = await query_blockers(conn)
groups = classify_blockers(after_cleanup)
```

If `groups.hard` is nonempty, enter human-pending immediately. If `groups.decision` is nonempty or the normal policy raised, invoke repair if unused. After a successful repair return, call `finish_units(0)` again, rerun mechanical cleanup, and query blockers one final time. Any remaining decision/hard blocker or repair exception enters human-pending.

Do not call `finish_units` solely because a failed policy raised: only a returned normal/repair result declares that attempt complete.

- [ ] **Step 7: Implement the repair invocation with exclusive handoff**

Reuse the exact normal policy object. If it needs exclusive tuner ownership, disconnect before repair and reconnect before any blocker query. Set `repair_used = True` before awaiting the policy so cancellation/exception cannot permit a second repair. Pass only `blocker_block` and the already-computed `caps` if accepted.

- [ ] **Step 8: Implement human-pending terminalization**

Create one helper in coordinator that:

1. transitions state;
2. fills the `seat0` record fields;
3. writes the record once;
4. prints/appends the structured CRITICAL event once;
5. leaves local seat 0 untouched.

No strategic default is permitted in this helper.

- [ ] **Step 9: Verify and commit**

Run Step 5; expected PASS.

```bash
git add src/civ_mcp/arena/coordinator.py src/civ_mcp/arena/seat0.py tests/arena/test_seat0.py tests/arena/test_coordinator.py
git commit -m "feat(arena): bound seat-zero repair and human escape"
```

---

### Task 7: End-turn retry bounds, drain budgets, autosave ordering, and append-only interruption

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py`
- Modify: `src/civ_mcp/arena/seat0.py`
- Test: `tests/arena/test_seat0.py`
- Test: `tests/arena/test_coordinator.py`
- Test: `tests/arena/test_transcript.py`

**Interfaces:** five one-second active grace polls; at most three ACTION_ENDTURN requests; disable hook before the exhausted-budget turn can release into AI; terminal record on cancellation.

- [ ] **Step 1: Write the exact grace/retry bound test**

After a clear blocker query and the first end request, return five same-turn/active polls. Assert no InGame query occurs during those five waits. On the sixth same-turn/active observation, allow blocker re-query and a second end request. Repeat until the third request, then keep seat 0 active and assert transition to `human_pending`, not a fourth request.

Count end requests before awaiting `hook.end_turn`; a dispatch exception therefore consumes one of the three attempts.

- [ ] **Step 2: Write the AI-phase execution-context test**

After the first end request, poll `seat0_active=False` with the same game turn for at least six polls, then advance. Instrument `conn.execute_write` to fail if called after the inactive observation. Expected: no blocker query, cleanup, autosave, overview snapshot, or end action during the AI phase; only `hook.poll`/sleep until the turn number changes.

- [ ] **Step 3: Write autosave ordering and failure tests**

Record operation order and assert:

```text
policy → finish_units → blocker query/cleanup → state_after snapshot → save_game → hook.end_turn
```

A save exception or a returned `Save may have failed:` message is recorded in `seat0.autosave.attempts` and does not prevent end-turn. If a newly surfaced blocker causes a repair after an unsuccessful end request, autosave again with the same `0_MCP_NNNN` name before re-fire so the anchor includes the repaired state.

- [ ] **Step 4: Write final-budget hook-disable tests**

Cover both exhaustion dimensions (`remaining == 0` and `game_turns == max_game_turns`):

- When seat 0 consumes the final admission, `hook.disable` occurs while `seat0_active` and before automatic `hook.end_turn`.
- If the final admitted seat-0 turn goes `human_pending`, disable before waiting for the human because the human may advance into AI immediately.
- If a puppet consumes the final slot while an earlier seat-0 turn is draining in `ai_processing`, disable after servicing that puppet and before restoring/releasing it.
- A repeated `hook.disable` in `finally` remains safe and cleanup order remains reclaim → restore 0 → disable.

- [ ] **Step 5: Write interruption/cancellation transcript tests**

Cancel during each of: normal policy, exclusive repair, active grace wait, and AI processing. Assert:

- `CancelledError` propagates;
- tuner reclaim/restore/disable still run in order;
- if a seat-0 record exists, it is marked `interrupted` and written once;
- a record already written as `human_pending` is not duplicated as interrupted;
- an ordinary cleanup exception does not replace the in-flight cancellation.

- [ ] **Step 6: Run focused tests and confirm RED**

```bash
uv run --extra test pytest tests/arena/test_seat0.py tests/arena/test_coordinator.py tests/arena/test_transcript.py -q
```

- [ ] **Step 7: Implement non-blocking retry handling**

At the start of each loop, observe the pending state before admission:

```python
poll_action = seat0_state.observe(turn=st.turn, seat0_active=st.seat0_active)
if poll_action is Seat0Poll.ADVANCED:
    terminalize_advanced()
elif poll_action is Seat0Poll.RECHECK:
    # Safe because observe only returns RECHECK while seat0_active is true.
    await recheck_cleanup_repair_or_refire()
elif seat0_state.phase in {Seat0Phase.END_FIRED, Seat0Phase.AI_PROCESSING}:
    await asyncio.sleep(1.0)
```

Do not hide this behind `gs.end_turn()` or `execute_end_turn`; both synchronously wait and violate puppet servicing.

- [ ] **Step 8: Implement drain-safe hook disable**

Track `hook_enabled = True` after successful injection. Use one idempotent coordinator helper:

```python
async def disable_hook_for_drain() -> None:
    nonlocal hook_enabled
    if hook_enabled:
        await hook.disable(conn)
        hook_enabled = False
```

Call it before a final automatic seat-0 end, before final-slot human waiting, and when another admission exhausts the budget while a seat-0 turn is still draining. The `finally` block still calls `hook.disable` best-effort even when the flag is false; safety cleanup must not depend on bookkeeping.

- [ ] **Step 9: Finalize interruption records inside `finally`**

Before connection cleanup, if `seat0_state.record` exists and was not written, set terminal `interrupted` and write it best-effort. Never let transcript failure mask `CancelledError` or skip tuner cleanup.

- [ ] **Step 10: Verify and commit**

Run Step 6; expected PASS.

```bash
git add src/civ_mcp/arena/coordinator.py src/civ_mcp/arena/seat0.py tests/arena/test_seat0.py tests/arena/test_coordinator.py tests/arena/test_transcript.py
git commit -m "feat(arena): drain seat-zero turns within bounded retries"
```

---

### Task 8: Failed-turn analysis without polluting success metrics

**Files:**
- Modify: `src/civ_mcp/arena/analyze.py`
- Test: `tests/arena/test_analyze.py`

**Interfaces:** `_turn_kind` recognizes `played`, `slept`, and `failed`; legacy records without `turn_kind` remain played; per-player output includes `failed_turns`.

- [ ] **Step 1: Write failing classification tests**

```python
@pytest.mark.parametrize(
    "record, expected",
    [
        ({"turn_kind": "failed"}, "failed"),
        ({"turn_kind": "played"}, "played"),
        ({"turn_kind": "slept", "slept": True}, "slept"),
        ({"slept": True}, "slept"),
        ({}, "played"),
    ],
)
def test_turn_kind_preserves_legacy_and_explicit_failure(record, expected):
    assert _turn_kind(record) == expected
```

- [ ] **Step 2: Write player-zero and failed-metric tests**

Analyze records for `player_id: 0` containing one played and one failed turn. Assert:

- group key `0` is preserved, not replaced by the model fallback;
- both records remain in chronological `series`;
- `failed_turns == 1`;
- config summary `turns == 1` and uses only played data;
- rubric, standing-memory, driver, invalid-call/truncation success rates, task-success, and behavior-critical tool counts do not credit the failed record;
- token and state fields on the failed series point remain visible for diagnostics.

Add an attention regression showing failed records are neither slept nor model-success turns and do not affect savings baselines.

- [ ] **Step 3: Run focused tests and confirm RED**

```bash
uv run --extra test pytest tests/arena/test_analyze.py -q
```

- [ ] **Step 4: Implement classification and filters**

```python
def _turn_kind(rec: dict) -> str:
    explicit = rec.get("turn_kind")
    if explicit == "failed":
        return "failed"
    if explicit == "slept" or rec.get("slept") is True:
        return "slept"
    return "played"
```

Use `kind == "played"` for config, rubric, memory, driver, attention model-turn baselines, and all success/quality accumulators. Keep every record in `series`, add its `turn_kind`, and count failures separately per player and at the neutral aggregate level. Do not special-case player ID 0; the existing `pid is not None` grouping is correct.

- [ ] **Step 5: Verify and commit**

Run Step 3; expected PASS.

```bash
git add src/civ_mcp/arena/analyze.py tests/arena/test_analyze.py
git commit -m "feat(arena): report failed seat-zero turns separately"
```

---

### Task 9: Reproducible scripted provider, smoke artifacts, and full offline regression

**Files:**
- Modify: `src/civ_mcp/arena/config.py`
- Modify: `src/civ_mcp/arena/experiment.py`
- Modify: `src/civ_mcp/arena/arena.py`
- Modify: `src/civ_mcp/arena/coordinator.py`
- Create: `experiments/arena-seat0-scripted-smoke.yaml`
- Create: `experiments/arena-seat0-llm-smoke.yaml`
- Test: `tests/arena/test_config.py`
- Test: `tests/arena/test_experiment.py`
- Test: `tests/arena/test_arena_wiring.py`
- Test: `tests/arena/test_coordinator.py`

**Interfaces:** `provider: scripted` selects `ScriptedPolicy` for that PlayerSpec only. Existing `--dry-run` remains a global all-scripted compatibility mode.

- [ ] **Step 1: Write failing provider-wiring tests**

Assert YAML and `--player 0:scripted:` parse; scripted rejects gateway/local-only model knobs that it does not use; `build_policies` returns `ScriptedPolicy` only for seat 0 while constructing real local policies/backends for nonzero seats. Existing local and CLI provider tests must remain unchanged.

- [ ] **Step 2: Write failing ScriptedPolicy repair tests**

Normal call: observe overview/units and return without choosing research/production. Repair call with a blocker block:

- fetches `get_tech_civics` and chooses the available tech/civic with key `(turns, type_name)`;
- fetches cities and `list_city_production` for empty queues;
- prefers repairs, then Monument, Granary, Scout, Warrior, then `(turns, item_name)` among UNIT/BUILDING options;
- never selects a district/wonder that requires a tile target;
- resolves only blocker types named in `blocker_block`;
- returns a structured action summary and never calls end-turn.

This deliberate normal/repair split makes stage 1 exercise the real focused repair path while keeping every choice inside the policy.

- [ ] **Step 3: Run focused tests and confirm RED**

```bash
uv run --extra test pytest tests/arena/test_config.py tests/arena/test_experiment.py tests/arena/test_arena_wiring.py tests/arena/test_coordinator.py -q
```

- [ ] **Step 4: Implement the test-only provider**

Add `scripted` to the valid provider set and return `driver_kind == "scripted"`. In `build_policies`:

```python
if spec.driver_kind() == "scripted":
    policies[spec.player_id] = ScriptedPolicy()
    continue
```

Leave the existing CLI and local-backend branches immediately after this new
guard; they retain their current implementations unchanged.

Do not create a backend, cost preflight, CLI executable check, or exclusive tuner handoff for scripted specs. Keep `provider`/`model` identity in transcripts/fingerprints as `scripted` / `seat0-smoke`.

- [ ] **Step 5: Implement deterministic repair choices**

Factor small private helpers on `ScriptedPolicy` (`_choose_research`, `_choose_production`) and use GameState methods only. Catch each read/action exception into the returned summary; do not make coordinator fallbacks. If an unimplemented strategic blocker appears, return normally without clearing it so the coordinator correctly reaches `human_pending` after the single repair.

- [ ] **Step 6: Create the two live configs**

Stage 1 config:

```yaml
max_puppet_turns: 24
max_game_turns: 24
idle_poll_limit: 600
gateway_url: http://192.168.20.196:11444/v1
civs:
  - player: 0
    provider: scripted
    model: seat0-smoke
    attention: {mode: "off"}
  - player: 1
    provider: local
    model: gemma4-26b
    gateway: http://192.168.20.196:11440/v1
    tools: full
    max_steps: 8
    attention: {mode: "off"}
  - player: 2
    provider: local
    model: gemma4-26b
    gateway: http://192.168.20.196:11440/v1
    tools: full
    max_steps: 8
    attention: {mode: "off"}
```

Stage 2 config uses the same two puppets, `max_puppet_turns/max_game_turns: 36`, and:

```yaml
  - player: 0
    provider: cli-claude
    model: ""
    attention: {mode: "off"}
```

Do not bake a `run_id` into either file; live commands supply unique IDs.

Two YAML traps, both load-time `ValueError`s if reintroduced: keep `mode: "off"`
quoted — PyYAML reads a bare `off` as boolean `False` and `_parse_attention`
rejects it — and never give `tools`/`max_steps`/`result_char_cap`/`gateway` to
a `scripted` or `cli-claude` entry; `experiment.py` restricts those knobs to
`local` civs. Budget rationale: 24 shared captures ≈ 8 rounds of the 3
configured seats, so gate 1's 5–10 seat-0-turn acceptance range has slack
(15 would land exactly on the minimum, where one failed capture fails the gate).

- [ ] **Step 7: Run the complete offline verification matrix**

```bash
uv run --extra test pytest tests/arena -q
uv run --extra test pytest tests/test_parsers.py tests/test_save_scumming.py -q
uv run --extra test pytest tests/ -q
git diff --check
```

Expected: every command PASS; full-suite count is at least the reviewed baseline of 1001 plus the new tests.

- [ ] **Step 8: Audit the authority and context boundaries**

Run:

```bash
rg -n "end_turn|ACTION_ENDTURN|execute_write|execute_read|sweep_promotions|choose_|set_research|set_city_production" src/civ_mcp/arena src/civ_mcp/lua/notifications.py
```

Manually verify:

- policies still cannot see `end_turn`;
- only `hook.end_turn` fires ACTION_ENDTURN in the new path;
- no InGame calls are reachable from `AI_PROCESSING`;
- only `ScriptedPolicy`, never coordinator/seat0 cleanup, chooses research/production;
- no attention sleep path is reachable for player 0.

- [ ] **Step 9: Commit the offline-complete slice**

```bash
git add src/civ_mcp/arena/config.py src/civ_mcp/arena/experiment.py src/civ_mcp/arena/arena.py src/civ_mcp/arena/coordinator.py experiments/arena-seat0-scripted-smoke.yaml experiments/arena-seat0-llm-smoke.yaml tests/arena/test_config.py tests/arena/test_experiment.py tests/arena/test_arena_wiring.py tests/arena/test_coordinator.py
git commit -m "test(arena): add reproducible seat-zero smoke policies"
```

---

### Task 10: Three attended live gates and evidence record

**Files:**
- Create: `docs/superpowers/plans/2026-07-14-arena-seat0-live-gates.md`
- Modify only if a live failure produces a separately reviewed TDD fix: relevant source/test files

**Precondition:** Offline Task 9 is fully green. Read `civ6-arena-live` before any operation. The human observes and intervenes only if the watcher reaches a declared hard block.

- [ ] **Step 1: Create the evidence checklist before the run**

The live-gate document must include tables for each seat-0 game turn with: game turn, policy kind, normal/repair calls, blockers before/after, save result, end requests, observed inactive-AI phase, terminal state, transcript line number, and human action. Include exact run IDs and commit SHA.

- [ ] **Step 2: Live gate 1 — scripted seat 0 + two LLM puppets**

Using the live skill, prepare a small game whose configured player IDs are 0, 1, and 2 and whose seat 0 has a missing research/civic or production decision early enough to exercise repair. Then run:

```bash
uv run civ-arena --config experiments/arena-seat0-scripted-smoke.yaml --run-id seat0-scripted-20260714
```

Acceptance:

- at least 5 and at most 10 distinct seat-0 game turns advance hands-free;
- both nonzero puppets are serviced and no seat self-loops;
- scripted normal call leaves the chosen probe blocker, focused repair makes the deterministic choice, and coordinator makes no choice;
- each seat-0 turn has one terminal transcript record;
- an `0_MCP_NNNN` save is visible through the game save interface, or the recorded save result precisely documents the deployment limitation;
- Ctrl-C during an active seat-0 turn restores seat 0 and disables the hook cleanly.

- [ ] **Step 3: Analyze gate 1 offline**

```bash
uv run civ-arena-analyze --run-id seat0-scripted-20260714
```

Verify `report.json` has player key `0`, the expected played/failed counts, and no double-counted repair turn.

- [ ] **Step 4: Live gate 2 — LLM seat-0 pilot**

Run:

```bash
uv run civ-arena --config experiments/arena-seat0-llm-smoke.yaml --run-id seat0-llm-20260714
```

Acceptance:

- 10-20 distinct seat-0 turns advance;
- CLI exclusive handoff disconnects/reconnects cleanly;
- `end_turn` is absent/denied inside the CLI pilot;
- research, production, promotion, policy, diplomacy, and other strategic choices seen in the interval are made by the pilot;
- at least one real blocker repair completes when one naturally appears; if none appears, stop at a safe seat-0 boundary, load a nearby autosave with a pending supported choice, and run a short focused continuation rather than altering production code;
- transcript normal/repair steps and usage merge into one record, and analysis treats player 0 normally.

- [ ] **Step 5: Analyze gate 2 offline**

```bash
uv run civ-arena-analyze --run-id seat0-llm-20260714
```

Inspect transcript/report evidence for player 0, repair metadata, terminal states, invalid calls, and cost/token totals.

- [ ] **Step 6: Live gate 3 — hard-block human escape**

Use the live skill to load a save that already presents an unsupported/inaccessible blocker (preferred: `ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE`; otherwise an unmapped blocker captured from a real save). Do not add a coordinator resolver to manufacture a pass. Run the matching seat-0 config and verify:

- exactly one `seat0_human_pending` CRITICAL event;
- no repeated model call, blocker query, cleanup, or end request on the same turn;
- seat 0 remains locally controllable;
- the human resolves the blocker and ends the turn;
- the watcher observes the new turn and resumes if budget remains, or drains/exits if exhausted.

If no real save can produce an inaccessible blocker in the attended window, record this gate as **not exercised**, with the saves/turns checked. Do not weaken the gate by fabricating Lua state or silently substituting a different success claim.

- [ ] **Step 7: Stop-on-failure rule**

If any gate fails, preserve the run directory and autosave, write the observed/expected delta into the evidence document, stop the live run safely, and return to a new TDD task/commit. Rerun Task 9's full suite before repeating the failed live gate. Do not patch while the game is mid-turn.

- [ ] **Step 8: Final verification and evidence commit**

After all exercised gates:

```bash
uv run --extra test pytest tests/ -q
git diff --check
git status --short
```

Update the evidence document with PASS/FAIL/NOT EXERCISED per gate, run IDs, report paths, save names, and final test count.

```bash
git add docs/superpowers/plans/2026-07-14-arena-seat0-live-gates.md
git commit -m "docs(arena): record seat-zero live gate evidence"
```

---

## Final Review Checklist

- [ ] Compare every spec bullet against Tasks 1-10 and add a test reference beside each in the live-gate document.
- [ ] Run `rg -n "T[B]D|T[O]DO|F[I]XME|place[h]older|implement la[t]er" docs/superpowers/plans/2026-07-14-arena-seat0-piloting.md src/civ_mcp/arena/seat0.py`; expected: no unresolved markers.
- [ ] Confirm `ArenaConfig.puppet_ids=None` and `[]` remain observably different and no truthiness fallback remains.
- [ ] Confirm seat 0 is never passed to `hook.inject` or `restore_local` during its turn body.
- [ ] Confirm every repair path uses the same policy permissions and no second task-prepass/budget charge.
- [ ] Confirm no ACTION_ENDTURN or InGame query occurs after `seat0_active` becomes false.
- [ ] Confirm three requests/five polls are exact bounds, not approximate timeout behavior.
- [ ] Confirm one transcript record per attempted seat-0 turn for played, failed, human-pending, and interrupted cases.
- [ ] Confirm player 0 appears in series/analysis and failed records do not influence success metrics.
- [ ] Confirm cancellation propagation and final reclaim → restore 0 → disable ordering remain covered.
- [ ] Leave the branch unmerged and unpushed for riz's separate-session review.
