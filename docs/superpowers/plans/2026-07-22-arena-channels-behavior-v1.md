# Arena Channels Behavior v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first guided, channels-enabled local LLM arena experiment config and the small prompt/backend code changes it requires.

**Architecture:** Keep the change narrow: the OpenAI-compatible backend always sends the llama.cpp thinking-off chat-template hint, while channel guidance remains prompt furniture carried by per-civ config into admission, projection, and rendering. Canonical channel ledger identity stays unchanged; transcript civ-option fingerprints record whether a seat was guided.

**Tech Stack:** Python 3.12, dataclasses, PyYAML experiment parsing, OpenAI Python SDK, pytest + pytest-asyncio via `uv run --extra test pytest`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-22-arena-channels-behavior-v1-design.md`.
- `OpenAICompatBackend.chat` must send `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` on every chat-completions request.
- `OpenAICompatBackend.chat` must preserve `max_tokens`, `timeout`, optional `tools`/`tool_choice`, bounded non-timeout retries, and immediate timeout re-raise.
- `ChannelOptions.guidance` defaults to `False` and is parsed from per-civ `channels.guidance`.
- `_parse_channels` accepts exactly `enabled` and `guidance`; both values must be exact booleans.
- `CivOptions.fingerprint()["channels"]` includes both `enabled` and `guidance`.
- `channel_config_fingerprint()` and `ChannelState.rules_fingerprint` do not include guidance.
- `ChannelProjection.guidance` controls prompt rendering; no guidance text is written to `channels/state.json` or `channels/events.jsonl`.
- `format_channel_block(projection)` keeps its existing signature.
- The guidance paragraph must be exactly:
  `These channels are private back-channel negotiations with rival leaders — invisible to everyone else. You can send private messages, propose deals that trade gold for in-game favors (for example destroying a barbarian camp or keeping units out of an area), accept or decline offers, fund payments, and acknowledge payments received. Deals are NOT enforced by the game: a promise can be honored or broken. Breaking a promise creates a lasting grievance the wronged player remembers. Used well, deals can earn you gold, remove threats, or buy cooperation you cannot get openly. Review the channel state below every turn and weigh whether a message or deal would advance your position.`
- `experiments/arena-channels-behavior-v1.yaml` has no `live_gate` block.
- Do not modify `experiments/arena-channels-core-smoke.yaml` for this work.
- Run targeted tests with `uv run --extra test pytest <paths> -v`.
- Run `git diff --check` before each commit.
- Commit after each task.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/arena/backends.py` | Add the thinking-off OpenAI-compatible request hint. |
| `src/civ_mcp/arena/config.py` | Add `ChannelOptions.guidance` and include it in per-civ fingerprints. |
| `src/civ_mcp/arena/experiment.py` | Parse and validate `channels.guidance`. |
| `src/civ_mcp/arena/channels.py` | Add `CHANNEL_GUIDANCE_TEXT`, `ChannelProjection.guidance`, and conditional guidance rendering. |
| `src/civ_mcp/arena/channel_runtime.py` | Accept an admission `guidance` keyword and apply it to the returned projection/block. |
| `src/civ_mcp/arena/coordinator.py` | Pass each configured player's guidance flag into channel admission. |
| `experiments/arena-channels-behavior-v1.yaml` | New ordinary-run experiment artifact for the first local LLM channel-uptake run. |
| `tests/arena/test_backends.py` | Request-contract tests for thinking-off extra body. |
| `tests/arena/test_config.py` | Channel default and fingerprint tests. |
| `tests/arena/test_experiment.py` | Per-civ guidance parser tests and experiment artifact loader test. |
| `tests/arena/test_channels.py` | Guidance rendering tests. |
| `tests/arena/test_channel_runtime.py` | Admission keyword projection/block test. |
| `tests/arena/test_coordinator.py` | Coordinator guidance wiring tests. |

---

### Task 1: Backend Thinking-Off Request Hint

**Files:**
- Modify: `tests/arena/test_backends.py`
- Modify: `src/civ_mcp/arena/backends.py`

**Interfaces:**
- Consumes: existing `OpenAICompatBackend.chat(messages: list[dict], tools: list[dict]) -> Reply`.
- Produces: every call to `self._client.chat.completions.create(**kw)` receives `kw["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}`.

- [ ] **Step 1: Write the failing backend request tests**

In `tests/arena/test_backends.py`, add this helper after `_backend_with_capture()`:

```python
def _thinking_off_extra_body():
    return {"chat_template_kwargs": {"enable_thinking": False}}
```

Replace `test_chat_sends_max_tokens_and_timeout` with:

```python
def test_chat_sends_max_tokens_timeout_and_thinking_off_without_tools():
    # Without these, a degenerate generation runs until it exhausts context and
    # stalls the whole game — the cap + timeout bound each turn-step.
    b, cap = _backend_with_capture()
    asyncio.run(b.chat([{"role": "user", "content": "hi"}], tools=[]))
    assert cap.kwargs["max_tokens"] == MAX_COMPLETION_TOKENS
    assert cap.kwargs["timeout"] == REQUEST_TIMEOUT_S
    assert cap.kwargs["extra_body"] == _thinking_off_extra_body()
    assert "tools" not in cap.kwargs
```

Replace `test_chat_passes_tools_with_cap` with:

```python
def test_chat_passes_tools_with_cap_and_thinking_off():
    b, cap = _backend_with_capture()
    asyncio.run(b.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}]))
    assert cap.kwargs["tool_choice"] == "auto"
    assert cap.kwargs["max_tokens"] == MAX_COMPLETION_TOKENS
    assert cap.kwargs["extra_body"] == _thinking_off_extra_body()
```

- [ ] **Step 2: Run the backend tests and verify the expected failure**

Run:

```bash
uv run --extra test pytest tests/arena/test_backends.py::test_chat_sends_max_tokens_timeout_and_thinking_off_without_tools tests/arena/test_backends.py::test_chat_passes_tools_with_cap_and_thinking_off -v
```

Expected: both tests fail with `KeyError: 'extra_body'`.

- [ ] **Step 3: Add the thinking-off request hint**

In `src/civ_mcp/arena/backends.py`, edit the request keyword dictionary inside `OpenAICompatBackend.chat` so it is exactly:

```python
        kw = dict(
            model=self.model,
            messages=messages,
            max_tokens=MAX_COMPLETION_TOKENS,
            timeout=REQUEST_TIMEOUT_S,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
```

- [ ] **Step 4: Run the backend tests and verify they pass**

Run:

```bash
uv run --extra test pytest tests/arena/test_backends.py -v
```

Expected: all tests in `tests/arena/test_backends.py` pass.

- [ ] **Step 5: Check the diff and commit**

Run:

```bash
git diff --check
git add tests/arena/test_backends.py src/civ_mcp/arena/backends.py
git commit -m "fix(arena): disable local model thinking in backend calls"
```

Expected: `git diff --check` prints no output, and the commit succeeds.

---

### Task 2: Channel Guidance Config Parsing And Fingerprints

**Files:**
- Modify: `tests/arena/test_config.py`
- Modify: `tests/arena/test_experiment.py`
- Modify: `src/civ_mcp/arena/config.py`
- Modify: `src/civ_mcp/arena/experiment.py`

**Interfaces:**
- Consumes: existing `ChannelOptions(enabled: bool = False)`, `_parse_channels(civ_label: str, raw: object) -> ChannelOptions`, and `CivOptions.fingerprint() -> dict`.
- Produces:
  - `ChannelOptions(enabled: bool = False, guidance: bool = False)`.
  - `_parse_channels` accepts exact keys `enabled` and `guidance`.
  - `CivOptions.fingerprint()["channels"] == {"enabled": bool, "guidance": bool}`.

- [ ] **Step 1: Write the failing config and parser tests**

In `tests/arena/test_config.py`, replace `test_channel_defaults_are_off_and_fingerprinted` with:

```python
def test_channel_defaults_are_off_and_fingerprinted():
    opts = CivOptions()
    assert opts.channels == ChannelOptions(enabled=False, guidance=False)
    assert opts.fingerprint()["channels"] == {"enabled": False, "guidance": False}

    guided = CivOptions(channels=ChannelOptions(enabled=True, guidance=True))
    assert guided.fingerprint()["channels"] == {"enabled": True, "guidance": True}
```

In `tests/arena/test_config.py`, replace the first player in `test_channel_rules_defaults_and_enabled_set_are_canonical` with:

```python
        PlayerSpec(2, "local", "m", options=CivOptions(channels=ChannelOptions(True, True))),
```

Leave the expected `channel_config_fingerprint` assertions unchanged so the test proves guidance does not affect channel ledger identity.

In `tests/arena/test_experiment.py`, replace `test_loads_per_civ_channels_and_run_wide_rules` with:

```python
def test_loads_per_civ_channels_and_run_wide_rules(tmp_path):
    path = tmp_path / "channels.yaml"
    path.write_text("""
channel_rules:
  acceptance_turns: 3
  grievance_half_life_turns: 30
civs:
  - player: 1
    provider: local
    model: m
    channels: {enabled: true, guidance: true}
  - player: 2
    provider: cli-codex
    model: gpt-5
    channels: {enabled: false}
""")
    cfg = load_experiment(path)
    assert cfg.players[0].options.channels.enabled is True
    assert cfg.players[0].options.channels.guidance is True
    assert cfg.players[1].options.channels.enabled is False
    assert cfg.players[1].options.channels.guidance is False
    assert cfg.channel_rules == ChannelRules()
```

In `tests/arena/test_experiment.py`, replace the `test_rejects_invalid_channel_config` parameter list with:

```python
@pytest.mark.parametrize("fragment, match", [
    ("channels: {enabled: yes}", "channels.enabled must be a boolean"),
    ("channels: {enabled: true, guidance: yes}", "channels.guidance must be a boolean"),
    ("channel_rules: {max_payment_gold: 10001}", "max_payment_gold must be 1..10000"),
    ("channel_rules: {max_completion_turns: 31}", "max_completion_turns must be 1..30"),
    ("channel_rules: {max_zone_distance: 0}", "max_zone_distance must be 1..10"),
])
```

- [ ] **Step 2: Run the config/parser tests and verify the expected failures**

Run:

```bash
uv run --extra test pytest tests/arena/test_config.py::test_channel_defaults_are_off_and_fingerprinted tests/arena/test_config.py::test_channel_rules_defaults_and_enabled_set_are_canonical tests/arena/test_experiment.py::test_loads_per_civ_channels_and_run_wide_rules tests/arena/test_experiment.py::test_rejects_invalid_channel_config -v
```

Expected: failures mention `ChannelOptions` has no `guidance` field or `unexpected key(s) ['guidance']`.

- [ ] **Step 3: Add `ChannelOptions.guidance` and fingerprint it**

In `src/civ_mcp/arena/config.py`, replace `ChannelOptions` with:

```python
@dataclass(frozen=True)
class ChannelOptions:
    enabled: bool = False
    guidance: bool = False
```

In `src/civ_mcp/arena/config.py`, replace the `channels` entry in `CivOptions.fingerprint()` with:

```python
            "channels": {
                "enabled": self.channels.enabled,
                "guidance": self.channels.guidance,
            },
```

- [ ] **Step 4: Parse and validate `channels.guidance`**

In `src/civ_mcp/arena/experiment.py`, replace `_parse_channels` with:

```python
def _parse_channels(civ_label: str, raw: object) -> ChannelOptions:
    if not isinstance(raw, dict):
        raise _err(civ_label, f"channels must be a mapping, got {raw!r}")
    _validate_mapping_keys(civ_label, raw, {"enabled", "guidance"}, "channels")
    enabled = raw.get("enabled", _CHANNEL_DEFAULTS.enabled)
    if type(enabled) is not bool:
        raise _err(civ_label, f"channels.enabled must be a boolean, got {enabled!r}")
    guidance = raw.get("guidance", _CHANNEL_DEFAULTS.guidance)
    if type(guidance) is not bool:
        raise _err(civ_label, f"channels.guidance must be a boolean, got {guidance!r}")
    return ChannelOptions(enabled=enabled, guidance=guidance)
```

- [ ] **Step 5: Run the config/parser tests and verify they pass**

Run:

```bash
uv run --extra test pytest tests/arena/test_config.py::test_channel_defaults_are_off_and_fingerprinted tests/arena/test_config.py::test_channel_rules_defaults_and_enabled_set_are_canonical tests/arena/test_experiment.py::test_loads_per_civ_channels_and_run_wide_rules tests/arena/test_experiment.py::test_rejects_invalid_channel_config -v
```

Expected: selected tests pass.

- [ ] **Step 6: Run broader config and experiment tests**

Run:

```bash
uv run --extra test pytest tests/arena/test_config.py tests/arena/test_experiment.py -v
```

Expected: all tests in both files pass.

- [ ] **Step 7: Check the diff and commit**

Run:

```bash
git diff --check
git add tests/arena/test_config.py tests/arena/test_experiment.py src/civ_mcp/arena/config.py src/civ_mcp/arena/experiment.py
git commit -m "feat(arena): parse per-player channel guidance"
```

Expected: `git diff --check` prints no output, and the commit succeeds.

---

### Task 3: Channel Guidance Rendering And Runtime Admission

**Files:**
- Modify: `tests/arena/test_channels.py`
- Modify: `tests/arena/test_channel_runtime.py`
- Modify: `src/civ_mcp/arena/channels.py`
- Modify: `src/civ_mcp/arena/channel_runtime.py`

**Interfaces:**
- Consumes: `ChannelProjection`, `format_channel_block(projection) -> str`, and `ChannelRuntime.admit_player(gs, player_id, turn) -> ChannelAdmission`.
- Produces:
  - `CHANNEL_GUIDANCE_TEXT: str` in `civ_mcp.arena.channels`.
  - `ChannelProjection.guidance: bool = False`.
  - `ChannelRuntime.admit_player(gs, player_id, turn, *, guidance: bool = False) -> ChannelAdmission`.
  - `ChannelAdmission.projection.guidance` and `ChannelAdmission.block` reflect the admission keyword.

- [ ] **Step 1: Write the failing renderer test**

In `tests/arena/test_channels.py`, add `CHANNEL_GUIDANCE_TEXT` to the import from `civ_mcp.arena.channels`.

Add this test near `test_disabled_projection_is_empty_and_cli_examples_are_exact`:

```python
def test_channel_guidance_renders_immediately_after_header_only_when_enabled():
    unguided = format_channel_block(ChannelProjection(player_id=1))
    guided = format_channel_block(ChannelProjection(player_id=1, guidance=True))

    assert unguided == "== PRIVATE UNOFFICIAL CHANNELS =="
    assert CHANNEL_GUIDANCE_TEXT not in unguided

    lines = guided.splitlines()
    assert lines[0] == "== PRIVATE UNOFFICIAL CHANNELS =="
    assert lines[1] == CHANNEL_GUIDANCE_TEXT
```

- [ ] **Step 2: Write the failing runtime admission test**

In `tests/arena/test_channel_runtime.py`, add `CHANNEL_GUIDANCE_TEXT` to the import from `civ_mcp.arena.channels`.

Add this test after `test_admission_and_finish_make_two_union_observations_and_apply_context`:

```python
@pytest.mark.asyncio
async def test_admission_guidance_keyword_sets_projection_and_block(tmp_path):
    rt = runtime(tmp_path)

    guided = await rt.admit_player(CountingObservationGS(), 2, 3, guidance=True)
    assert guided.projection.guidance is True
    assert guided.block.splitlines()[1] == CHANNEL_GUIDANCE_TEXT

    unguided = await rt.admit_player(CountingObservationGS(), 1, 3)
    assert unguided.projection.guidance is False
    assert CHANNEL_GUIDANCE_TEXT not in unguided.block
```

- [ ] **Step 3: Run the renderer/runtime tests and verify the expected failures**

Run:

```bash
uv run --extra test pytest tests/arena/test_channels.py::test_channel_guidance_renders_immediately_after_header_only_when_enabled tests/arena/test_channel_runtime.py::test_admission_guidance_keyword_sets_projection_and_block -v
```

Expected: failures mention `CHANNEL_GUIDANCE_TEXT` import failure, `ChannelProjection` has no `guidance`, or `admit_player()` got an unexpected keyword argument.

- [ ] **Step 4: Add guidance text, projection field, and renderer behavior**

In `src/civ_mcp/arena/channels.py`, add this constant near `SCHEMA_VERSION`:

```python
CHANNEL_GUIDANCE_TEXT = (
    "These channels are private back-channel negotiations with rival leaders — "
    "invisible to everyone else. You can send private messages, propose deals "
    "that trade gold for in-game favors (for example destroying a barbarian "
    "camp or keeping units out of an area), accept or decline offers, fund "
    "payments, and acknowledge payments received. Deals are NOT enforced by the "
    "game: a promise can be honored or broken. Breaking a promise creates a "
    "lasting grievance the wronged player remembers. Used well, deals can earn "
    "you gold, remove threats, or buy cooperation you cannot get openly. Review "
    "the channel state below every turn and weigh whether a message or deal "
    "would advance your position."
)
```

In `src/civ_mcp/arena/channels.py`, replace `ChannelProjection` with:

```python
@dataclass(frozen=True)
class ChannelProjection:
    player_id: int
    messages: tuple[Message, ...] = ()
    deals: tuple[Deal, ...] = ()
    grievances: tuple[Grievance, ...] = ()
    acknowledgements: tuple[ChannelAcknowledgement, ...] = ()
    guidance: bool = False
    cli_instructions: bool = False
```

In `src/civ_mcp/arena/channels.py`, edit the first lines of `format_channel_block` so they are exactly:

```python
def format_channel_block(projection: ChannelProjection) -> str:
    lines = ["== PRIVATE UNOFFICIAL CHANNELS =="]
    if projection.guidance:
        lines.append(CHANNEL_GUIDANCE_TEXT)
```

Leave the existing `messages`, `deals`, `grievances`, `acknowledgements`, and `cli_instructions` blocks below this unchanged.

- [ ] **Step 5: Apply the admission keyword in runtime**

In `src/civ_mcp/arena/channel_runtime.py`, replace the `admit_player` signature with:

```python
    async def admit_player(
        self,
        gs: Any,
        player_id: int,
        turn: int,
        *,
        guidance: bool = False,
    ) -> ChannelAdmission:
```

In the same method, replace:

```python
        projection = self.project_for_player(player_id, turn)
```

with:

```python
        projection = self.project_for_player(player_id, turn)
        if guidance:
            projection = replace(projection, guidance=True)
```

`channel_runtime.py` already imports `replace` from `dataclasses`; no new import is needed.

- [ ] **Step 6: Run the renderer/runtime tests and verify they pass**

Run:

```bash
uv run --extra test pytest tests/arena/test_channels.py::test_channel_guidance_renders_immediately_after_header_only_when_enabled tests/arena/test_channel_runtime.py::test_admission_guidance_keyword_sets_projection_and_block -v
```

Expected: selected tests pass.

- [ ] **Step 7: Run broader channel tests**

Run:

```bash
uv run --extra test pytest tests/arena/test_channels.py tests/arena/test_channel_runtime.py -v
```

Expected: all tests in both files pass.

- [ ] **Step 8: Check the diff and commit**

Run:

```bash
git diff --check
git add tests/arena/test_channels.py tests/arena/test_channel_runtime.py src/civ_mcp/arena/channels.py src/civ_mcp/arena/channel_runtime.py
git commit -m "feat(arena): render guided channel prompt preamble"
```

Expected: `git diff --check` prints no output, and the commit succeeds.

---

### Task 4: Coordinator Guidance Wiring

**Files:**
- Modify: `tests/arena/test_coordinator.py`
- Modify: `src/civ_mcp/arena/coordinator.py`

**Interfaces:**
- Consumes:
  - `ChannelOptions.guidance`.
  - `ChannelRuntime.admit_player(gs, player_id, turn, *, guidance=False)`.
- Produces: every coordinator channel admission passes the configured player's guidance flag.

- [ ] **Step 1: Extend the coordinator fake runtime without changing existing call strings**

In `tests/arena/test_coordinator.py`, inside `FakeChannelRuntime.__init__`, add this line after `self.calls = []`:

```python
        self.admissions = []
```

Replace `FakeChannelRuntime.admit_player` with:

```python
    async def admit_player(self, gs, player_id, turn, *, guidance=False):
        from types import SimpleNamespace
        from civ_mcp.arena.channel_protocol import ChannelTurnContext

        self._record(f"admit:{player_id}:{turn}")
        self.admissions.append(
            {"player_id": player_id, "turn": turn, "guidance": guidance}
        )
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
```

Replace `_channel_options` with:

```python
def _channel_options(*, attention_mode="off", guidance=False):
    from civ_mcp.arena.config import AttentionOptions, ChannelOptions

    return CivOptions(
        attention=AttentionOptions(mode=attention_mode),
        channels=ChannelOptions(enabled=True, guidance=guidance),
    )
```

- [ ] **Step 2: Add the failing coordinator guidance test**

In `tests/arena/test_coordinator.py`, add this test after `test_channels_open_admit_policy_finish_with_run_identity`:

```python
@pytest.mark.asyncio
async def test_channel_guidance_option_is_passed_to_runtime(monkeypatch, tmp_path):
    runtime = FakeChannelRuntime(acknowledgements=1)
    _patch_channel_open(monkeypatch, runtime)
    policy = ChannelRecordingPolicy(runtime, final_summary="")
    conn = FakeConn()
    conn._polls = iter([["LOCAL|1", "TURN|7", "ACTIVE|true", "LAST|0"]])
    sink = FakeSink()
    config = _channel_config(
        tmp_path,
        options=_channel_options(guidance=True),
    )

    await run_arena(conn, FakeGS(), config, policy=policy, transcript=sink)

    assert runtime.admissions == [
        {"player_id": 1, "turn": 7, "guidance": True}
    ]
```

In `test_channels_open_admit_policy_finish_with_run_identity`, add this assertion after the `runtime.calls[:4]` assertion:

```python
    assert runtime.admissions[0] == {
        "player_id": 1,
        "turn": 7,
        "guidance": False,
    }
```

- [ ] **Step 3: Run the coordinator guidance tests and verify the expected failure**

Run:

```bash
uv run --extra test pytest tests/arena/test_coordinator.py::test_channels_open_admit_policy_finish_with_run_identity tests/arena/test_coordinator.py::test_channel_guidance_option_is_passed_to_runtime -v
```

Expected: the new guided test fails because the coordinator still calls `admit_player` without `guidance=True`.

- [ ] **Step 4: Add the per-player guidance map**

In `src/civ_mcp/arena/coordinator.py`, immediately after the existing `enabled_channel_players` assignment, add:

```python
    channel_guidance_by_player = {
        spec.player_id: spec.options.channels.guidance
        for spec in config.players
        if spec.options.channels.enabled
    }
```

- [ ] **Step 5: Pass guidance to seat-0 admission**

In `src/civ_mcp/arena/coordinator.py`, inside `ensure_seat0_channel_capture`, replace:

```python
            admission = await channel_runtime.admit_player(gs, 0, turn)
```

with:

```python
            admission = await channel_runtime.admit_player(
                gs,
                0,
                turn,
                guidance=channel_guidance_by_player.get(0, False),
            )
```

- [ ] **Step 6: Pass guidance to normal puppet admission**

In `src/civ_mcp/arena/coordinator.py`, inside the non-seat-0 channel admission block, replace:

```python
                        channel_admission = await channel_runtime.admit_player(
                            gs, st.local, st.turn
                        )
```

with:

```python
                        channel_admission = await channel_runtime.admit_player(
                            gs,
                            st.local,
                            st.turn,
                            guidance=channel_guidance_by_player.get(st.local, False),
                        )
```

- [ ] **Step 7: Run the coordinator guidance tests and verify they pass**

Run:

```bash
uv run --extra test pytest tests/arena/test_coordinator.py::test_channels_open_admit_policy_finish_with_run_identity tests/arena/test_coordinator.py::test_channel_guidance_option_is_passed_to_runtime -v
```

Expected: selected tests pass.

- [ ] **Step 8: Run the broader coordinator channel slice**

Run:

```bash
uv run --extra test pytest tests/arena/test_coordinator.py -k "channel" -v
```

Expected: selected coordinator channel tests pass.

- [ ] **Step 9: Check the diff and commit**

Run:

```bash
git diff --check
git add tests/arena/test_coordinator.py src/civ_mcp/arena/coordinator.py
git commit -m "feat(arena): pass channel guidance into admissions"
```

Expected: `git diff --check` prints no output, and the commit succeeds.

---

### Task 5: Behavior v1 Experiment Artifact

**Files:**
- Create: `experiments/arena-channels-behavior-v1.yaml`
- Modify: `tests/arena/test_experiment.py`

**Interfaces:**
- Consumes: `load_experiment(path: Path) -> ArenaConfig` with `ChannelOptions.guidance`.
- Produces: a checked-in ordinary-run experiment config for `arena-channels-behavior-v1`.

- [ ] **Step 1: Add the failing artifact loader test**

In `tests/arena/test_experiment.py`, add this constant near the existing experiment path constants:

```python
ARENA_CHANNELS_BEHAVIOR_V1 = REPO_ROOT / "experiments" / "arena-channels-behavior-v1.yaml"
```

Add this test after `test_loads_gemma_strategy_ab_slice1_artifact`:

```python
def test_loads_arena_channels_behavior_v1_artifact():
    cfg = load_experiment(ARENA_CHANNELS_BEHAVIOR_V1)

    assert cfg.run_id == "arena-channels-behavior-v1"
    assert cfg.max_puppet_turns == 30
    assert cfg.max_game_turns == 12
    assert cfg.live_gate == LiveGateOptions()
    assert cfg.channel_rules.acceptance_turns == 3
    assert cfg.channel_rules.funding_turns == 2
    assert cfg.channel_rules.payment_response_turns == 2

    by_player = {player.player_id: player for player in cfg.players}
    assert set(by_player) == {1, 2, 3}

    p1 = by_player[1]
    assert p1.provider == "local"
    assert p1.model == "gemma4-26b"
    assert p1.gateway == "http://192.168.20.196:11440/v1"
    assert p1.options.tools == "minimal"
    assert p1.options.max_steps == 10
    assert p1.options.channels.enabled is True
    assert p1.options.channels.guidance is True

    p2 = by_player[2]
    assert p2.provider == "local"
    assert p2.model == "qwen3.6-27b"
    assert p2.gateway == "http://192.168.20.196:11441/v1"
    assert p2.options.tools == "minimal"
    assert p2.options.max_steps == 10
    assert p2.options.channels.enabled is True
    assert p2.options.channels.guidance is True

    p3 = by_player[3]
    assert p3.provider == "scripted"
    assert p3.options.channels.enabled is True
    assert p3.options.channels.guidance is False
```

- [ ] **Step 2: Run the artifact test and verify the expected failure**

Run:

```bash
uv run --extra test pytest tests/arena/test_experiment.py::test_loads_arena_channels_behavior_v1_artifact -v
```

Expected: test fails with `FileNotFoundError` for `experiments/arena-channels-behavior-v1.yaml`.

- [ ] **Step 3: Create the experiment YAML**

Create `experiments/arena-channels-behavior-v1.yaml` with exactly:

```yaml
run_id: arena-channels-behavior-v1
max_puppet_turns: 30
max_game_turns: 12
channel_rules:
  acceptance_turns: 3
  funding_turns: 2
  payment_response_turns: 2
civs:
  - player: 1
    provider: local
    model: gemma4-26b
    gateway: http://192.168.20.196:11440/v1
    tools: minimal
    max_steps: 10
    channels: {enabled: true, guidance: true}
  - player: 2
    provider: local
    model: qwen3.6-27b
    gateway: http://192.168.20.196:11441/v1
    tools: minimal
    max_steps: 10
    channels: {enabled: true, guidance: true}
  - player: 3
    provider: scripted
    channels: {enabled: true}
```

- [ ] **Step 4: Run the artifact test and verify it passes**

Run:

```bash
uv run --extra test pytest tests/arena/test_experiment.py::test_loads_arena_channels_behavior_v1_artifact -v
```

Expected: selected test passes.

- [ ] **Step 5: Run the full affected test slice**

Run:

```bash
uv run --extra test pytest tests/arena/test_backends.py tests/arena/test_config.py tests/arena/test_experiment.py tests/arena/test_channels.py tests/arena/test_channel_runtime.py tests/arena/test_coordinator.py -v
```

Expected: all tests in the affected slice pass.

- [ ] **Step 6: Check the diff and commit**

Run:

```bash
git diff --check
git add experiments/arena-channels-behavior-v1.yaml tests/arena/test_experiment.py
git commit -m "test(arena): add channels behavior v1 experiment artifact"
```

Expected: `git diff --check` prints no output, and the commit succeeds.

---

## Final Verification

- [ ] **Step 1: Run the affected test slice**

Run:

```bash
uv run --extra test pytest tests/arena/test_backends.py tests/arena/test_config.py tests/arena/test_experiment.py tests/arena/test_channels.py tests/arena/test_channel_runtime.py tests/arena/test_coordinator.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run the full test suite**

Run:

```bash
uv run --extra test pytest -v
```

Expected: full suite passes.

- [ ] **Step 3: Check formatting-sensitive whitespace**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 4: Inspect final history**

Run:

```bash
git log --oneline -6
git status --short
```

Expected: the five task commits appear above the spec commit. `git status --short` may still show unrelated untracked arena run artifacts that existed before this plan; no tracked implementation files should be modified.

## Self-Review Notes

- Spec coverage: Task 1 covers backend thinking-off; Tasks 2-4 cover per-player guidance config, fingerprinting, projection rendering, and admission/coordinator wiring; Task 5 covers the experiment artifact and ordinary-run/no-live-gate contract.
- Canonical-state safety: no task adds guidance to `ChannelState`, `state_to_dict`, `state_from_dict`, `channel_config_fingerprint`, or `ChannelRules`.
- Scope: no analyzer changes, no live-gate scenario changes, no CLI-provider seats, no thinking-mode config knob.
