# Arena Channels Behavior V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the v3 channels experiment treatment: stronger deal-action guidance plus a scripted P3 opener that proposes formal channel deals and auto-funds accepted on-delivery deals.

**Architecture:** Keep channel script configuration as per-civ transcript metadata, not channel journal identity. The coordinator injects the existing per-turn private projection into policies that accept it, and `ScriptedPolicy` uses that projection for deterministic script dispatch and auto-funding while preserving its current normal/repair behavior. The v3 experiment artifact reuses the v2 model roster and run budget with only guidance text and P3 scripted channel actions changed.

**Tech Stack:** Python 3.12, frozen dataclasses, PyYAML experiment parsing, existing channel runtime/protocol types, pytest + pytest-asyncio via `uv run --extra test pytest`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-arena-channels-behavior-v3-design.md`.
- `CHANNEL_GUIDANCE_TEXT` gains exactly this appended directive: ` Important: messages alone are NOT binding — a deal exists only once it is created with the propose_deal action and answered with respond_to_deal. When you and a rival converge on terms, turn them into a propose_deal immediately; when a proposal is pending for you, answer it with respond_to_deal before it expires; fund deals you owe with fund_deal.`
- `ChannelScriptStep` is a frozen dataclass in `src/civ_mcp/arena/config.py` with fields `turn: int`, `action: str`, and `args: dict[str, object]`.
- `ChannelOptions.script` defaults to `()`.
- `_parse_channels` rejects `script` unless `channels.enabled` is exactly `true`.
- `_parse_channels` accepts script actions only from `CHANNEL_ACTION_NAMES`.
- `_parse_channels` deep-copies each script step's `args` mapping; runtime dispatch treats it as read-only.
- `CivOptions.fingerprint()["channels"]["script"]` is an order-preserving list of `{"turn": int, "action": str, "args": dict}` mappings.
- `channel_config_fingerprint()` and `ChannelState.rules_fingerprint` do not include `guidance` or `script`.
- The coordinator passes `channel_projection=ChannelAdmission.projection` wherever it already passes `channel_context` and `channel_block`.
- `_PRIVATE_CHANNEL_RESULT_FIELDS` includes `channel_projection`.
- `ScriptedPolicy` runs channel script logic only on normal calls with non-`None` `channel_context`; repair calls with non-empty `blocker_block` skip all channel logic.
- A `skip_unit(0)` failure is reported in `ScriptedPolicy`'s summary and does not prevent scripted channel dispatch.
- Auto-fund dispatches `fund_deal({"deal_id": deal.id})` only for deals whose proposer is the scripted player, state is `DealState.ACTIVE`, payment status is `PaymentStatus.DUE`, `fund_by_turn is not None`, and `turn <= fund_by_turn`.
- `experiments/arena-channels-behavior-v3.yaml` has no `live_gate` block and keeps the v2 baseline model roster, gateway URLs, `max_steps: 15`, `max_puppet_turns: 90`, and `max_game_turns: 108`.
- Do not change analyzer behavior.
- Run targeted tests with `uv run --extra test pytest <paths> -v`.
- Run `git diff --check` before each implementation commit.
- Commit after each task.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/arena/config.py` | Define `ChannelScriptStep`, add `ChannelOptions.script`, and include script metadata in per-civ fingerprints. |
| `src/civ_mcp/arena/experiment.py` | Parse and validate YAML `channels.script`, using `CHANNEL_ACTION_NAMES` and deep-copying `args`. |
| `src/civ_mcp/arena/coordinator.py` | Inject `channel_projection` into accepting policies and scrub it from public result logs. |
| `src/civ_mcp/arena/scripted_policy.py` | Dispatch configured script steps and auto-fund due accepted deals during normal scripted turns. |
| `src/civ_mcp/arena/channels.py` | Append the v3 deal-action directive to `CHANNEL_GUIDANCE_TEXT`. |
| `experiments/arena-channels-behavior-v3.yaml` | Add the attended v3 experiment artifact. |
| `tests/arena/test_config.py` | Pin script defaults, fingerprints, and channel-config fingerprint exclusion. |
| `tests/arena/test_experiment.py` | Pin script parsing, invalid YAML cases, and the v3 artifact loader. |
| `tests/arena/test_coordinator.py` | Pin projection injection, privacy scrubbing, and `ScriptedPolicy` channel behavior. |
| `tests/arena/test_channels.py` | Pin that guidance names binding deal actions and non-binding messages. |

---

### Task 1: Channel Script Config And Parser

**Files:**
- Modify: `tests/arena/test_config.py`
- Modify: `tests/arena/test_experiment.py`
- Modify: `src/civ_mcp/arena/config.py`
- Modify: `src/civ_mcp/arena/experiment.py`

**Interfaces:**
- Consumes:
  - Existing `ChannelOptions(enabled: bool = False, guidance: bool = False)`.
  - Existing `_parse_channels(civ_label: str, raw: object) -> ChannelOptions`.
  - Existing `CivOptions.fingerprint() -> dict`.
  - Existing `CHANNEL_ACTION_NAMES: tuple[str, ...]` in `civ_mcp.arena.channel_protocol`.
- Produces:
  - `ChannelScriptStep(turn: int, action: str, args: dict[str, object])`.
  - `ChannelOptions(enabled: bool = False, guidance: bool = False, script: tuple[ChannelScriptStep, ...] = ())`.
  - `_parse_channels` accepts `enabled`, `guidance`, and `script`.
  - `_parse_channels` rejects boolean/nonpositive `turn`, unknown `action`, non-mapping `args`, extra step keys, and script without `enabled: true`.
  - `CivOptions.fingerprint()["channels"]` includes `script` as a list of dicts.

- [x] **Step 1: Write the failing config tests**

In `tests/arena/test_config.py`, add `ChannelScriptStep` to the import from `civ_mcp.arena.config`:

```python
from civ_mcp.arena.config import (
    ArenaConfig,
    AttentionOptions,
    BriefingOptions,
    ChannelOptions,
    ChannelRules,
    ChannelScriptStep,
    CivOptions,
    LiveGateOptions,
    MemoryOptions,
    PlayerSpec,
    TaskTrackerOptions,
    channel_config_fingerprint,
    parse_player_spec,
    resolved_puppet_ids,
    validate_arena_config,
    DEFAULT_GATEWAY_ENDPOINT,
)
```

Replace `test_channel_defaults_are_off_and_fingerprinted` with:

```python
def test_channel_defaults_are_off_and_fingerprinted():
    opts = CivOptions()
    assert opts.channels == ChannelOptions(enabled=False, guidance=False, script=())
    assert opts.fingerprint()["channels"] == {
        "enabled": False,
        "guidance": False,
        "script": [],
    }

    step = ChannelScriptStep(
        turn=157,
        action="send_message",
        args={"to_player": 2, "text": "hello"},
    )
    guided = CivOptions(channels=ChannelOptions(enabled=True, guidance=True, script=(step,)))
    assert guided.fingerprint()["channels"] == {
        "enabled": True,
        "guidance": True,
        "script": [
            {
                "turn": 157,
                "action": "send_message",
                "args": {"to_player": 2, "text": "hello"},
            }
        ],
    }
```

Replace the first `PlayerSpec` in `test_channel_rules_defaults_and_enabled_set_are_canonical` with:

```python
        PlayerSpec(
            2,
            "local",
            "m",
            options=CivOptions(
                channels=ChannelOptions(
                    enabled=True,
                    guidance=True,
                    script=(
                        ChannelScriptStep(
                            turn=157,
                            action="send_message",
                            args={"to_player": 1, "text": "private"},
                        ),
                    ),
                )
            ),
        ),
```

Leave the existing `channel_config_fingerprint` expectations unchanged so the test proves script metadata does not change channel ledger identity.

- [x] **Step 2: Write the failing experiment parser tests**

In `tests/arena/test_experiment.py`, add `ChannelScriptStep` to the config import:

```python
from civ_mcp.arena.config import (
    ArenaConfig,
    AttentionOptions,
    BriefingOptions,
    ChannelOptions,
    ChannelRules,
    ChannelScriptStep,
    CivOptions,
    LiveGateOptions,
    MemoryOptions,
    PlayerSpec,
    TaskTrackerOptions,
)
```

Add this test near the existing channel parser tests:

```python
def test_loads_per_civ_channel_script(tmp_path):
    path = tmp_path / "channels-script.yaml"
    path.write_text("""
civs:
  - player: 3
    provider: scripted
    channels:
      enabled: true
      guidance: true
      script:
        - turn: 157
          action: send_message
          args:
            to_player: 1
            text: opener
        - turn: 158
          action: fund_deal
          args:
            deal_id: deal-000001
""")
    cfg = load_experiment(path)

    assert cfg.players[0].options.channels.enabled is True
    assert cfg.players[0].options.channels.guidance is True
    assert cfg.players[0].options.channels.script == (
        ChannelScriptStep(157, "send_message", {"to_player": 1, "text": "opener"}),
        ChannelScriptStep(158, "fund_deal", {"deal_id": "deal-000001"}),
    )
```

Add this parametrized rejection test near `test_rejects_invalid_channel_config`:

```python
@pytest.mark.parametrize(
    "fragment, match",
    [
        (
            "channels: {enabled: true, script: not-a-list}",
            "channels.script must be a list",
        ),
        (
            "channels: {enabled: false, script: []}",
            "channels.script requires channels.enabled true",
        ),
        (
            "channels: {script: []}",
            "channels.script requires channels.enabled true",
        ),
        (
            "channels: {enabled: true, script: [{turn: true, action: send_message, args: {}}]}",
            "channels.script\\[0\\].turn must be an integer",
        ),
        (
            "channels: {enabled: true, script: [{turn: 0, action: send_message, args: {}}]}",
            "channels.script\\[0\\].turn must be positive",
        ),
        (
            "channels: {enabled: true, script: [{turn: 1, action: trade_gold, args: {}}]}",
            "channels.script\\[0\\].action must be one of",
        ),
        (
            "channels: {enabled: true, script: [{turn: 1, action: send_message, args: []}]}",
            "channels.script\\[0\\].args must be a mapping",
        ),
        (
            "channels: {enabled: true, script: [{turn: 1, action: send_message, args: {}, note: bad}]}",
            "channels.script\\[0\\]: unknown key",
        ),
    ],
)
def test_rejects_invalid_channel_script_config(tmp_path, fragment, match):
    path = tmp_path / "bad-channel-script.yaml"
    path.write_text(
        "civs:\n"
        "  - player: 3\n"
        "    provider: scripted\n"
        f"    {fragment}\n"
    )
    with pytest.raises(ValueError, match=match):
        load_experiment(path)
```

- [x] **Step 3: Run the new tests and verify the expected failures**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_config.py::test_channel_defaults_are_off_and_fingerprinted \
  tests/arena/test_config.py::test_channel_rules_defaults_and_enabled_set_are_canonical \
  tests/arena/test_experiment.py::test_loads_per_civ_channel_script \
  tests/arena/test_experiment.py::test_rejects_invalid_channel_script_config \
  -v
```

Expected: import or assertion failures mentioning missing `ChannelScriptStep`, unexpected `ChannelOptions.script`, or rejected `channels.script` keys.

- [x] **Step 4: Add `ChannelScriptStep` and fingerprint support**

In `src/civ_mcp/arena/config.py`, add this dataclass immediately above `ChannelOptions`:

```python
@dataclass(frozen=True)
class ChannelScriptStep:
    turn: int
    action: str
    args: dict[str, object]
```

Replace `ChannelOptions` with:

```python
@dataclass(frozen=True)
class ChannelOptions:
    enabled: bool = False
    guidance: bool = False
    script: tuple[ChannelScriptStep, ...] = ()
```

Replace the `"channels"` block in `CivOptions.fingerprint()` with:

```python
            "channels": {
                "enabled": self.channels.enabled,
                "guidance": self.channels.guidance,
                "script": [
                    {
                        "turn": step.turn,
                        "action": step.action,
                        "args": copy.deepcopy(step.args),
                    }
                    for step in self.channels.script
                ],
            },
```

Add `import copy` at the top of `src/civ_mcp/arena/config.py`:

```python
from __future__ import annotations
import copy
from dataclasses import dataclass, field
```

- [x] **Step 5: Add parser support for script**

In `src/civ_mcp/arena/experiment.py`, update the imports:

```python
import copy
from dataclasses import dataclass, replace
```

Add `ChannelScriptStep` to the config import and import `CHANNEL_ACTION_NAMES`:

```python
    ChannelOptions,
    ChannelRules,
    ChannelScriptStep,
```

```python
from civ_mcp.arena.channel_protocol import CHANNEL_ACTION_NAMES
```

Add this helper above `_parse_channels`:

```python
def _parse_channel_script_step(
    civ_label: str,
    index: int,
    raw: object,
) -> ChannelScriptStep:
    label = f"channels.script[{index}]"
    if not isinstance(raw, dict):
        raise _err(civ_label, f"{label} must be a mapping, got {raw!r}")
    _validate_mapping_keys(civ_label, raw, {"turn", "action", "args"}, label)
    if "turn" not in raw:
        raise _err(civ_label, f"{label}.turn is required")
    if "action" not in raw:
        raise _err(civ_label, f"{label}.action is required")
    if "args" not in raw:
        raise _err(civ_label, f"{label}.args is required")

    turn = _positive_int(civ_label, f"{label}.turn", raw["turn"])
    action = raw["action"]
    if not isinstance(action, str) or action not in CHANNEL_ACTION_NAMES:
        raise _err(
            civ_label,
            f"{label}.action must be one of {CHANNEL_ACTION_NAMES}, got {action!r}",
        )
    args = raw["args"]
    if not isinstance(args, dict):
        raise _err(civ_label, f"{label}.args must be a mapping, got {args!r}")
    return ChannelScriptStep(
        turn=turn,
        action=action,
        args=copy.deepcopy(args),
    )
```

Replace `_parse_channels` with:

```python
def _parse_channels(civ_label: str, raw: object) -> ChannelOptions:
    if not isinstance(raw, dict):
        raise _err(civ_label, f"channels must be a mapping, got {raw!r}")
    _validate_mapping_keys(civ_label, raw, {"enabled", "guidance", "script"}, "channels")
    enabled = raw.get("enabled", _CHANNEL_DEFAULTS.enabled)
    if not isinstance(enabled, bool):
        raise _err(civ_label, f"channels.enabled must be a boolean, got {enabled!r}")
    guidance = raw.get("guidance", _CHANNEL_DEFAULTS.guidance)
    if not isinstance(guidance, bool):
        raise _err(civ_label, f"channels.guidance must be a boolean, got {guidance!r}")
    script_raw = raw.get("script", ())
    if "script" in raw and enabled is not True:
        raise _err(civ_label, "channels.script requires channels.enabled true")
    if script_raw == ():
        script = ()
    else:
        if not isinstance(script_raw, list):
            raise _err(civ_label, f"channels.script must be a list, got {script_raw!r}")
        script = tuple(
            _parse_channel_script_step(civ_label, index, step)
            for index, step in enumerate(script_raw)
        )
    return ChannelOptions(enabled=enabled, guidance=guidance, script=script)
```

- [x] **Step 6: Run the targeted config/parser tests**

Run:

```bash
uv run --extra test pytest tests/arena/test_config.py tests/arena/test_experiment.py -v
```

Expected: all tests in both files pass.

- [x] **Step 7: Check the diff and commit**

Run:

```bash
git diff --check
git add tests/arena/test_config.py tests/arena/test_experiment.py src/civ_mcp/arena/config.py src/civ_mcp/arena/experiment.py
git commit -m "feat(arena): parse scripted channel actions"
```

Expected: `git diff --check` prints no output, and the commit succeeds.

---

### Task 2: Coordinator Channel Projection Wiring And Privacy

**Files:**
- Modify: `tests/arena/test_coordinator.py`
- Modify: `src/civ_mcp/arena/coordinator.py`

**Interfaces:**
- Consumes:
  - Existing `ChannelAdmission.projection: ChannelProjection`.
  - Existing `_policy_accepts_kwarg(policy, name: str) -> bool`.
  - Existing `_PRIVATE_CHANNEL_RESULT_FIELDS`.
  - Existing `FakeChannelRuntime`, `ChannelRecordingPolicy`, and privacy tests in `tests/arena/test_coordinator.py`.
- Produces:
  - Policies that accept `channel_projection` receive the same object as `ChannelAdmission.projection`.
  - Policies that do not accept `channel_projection` keep running without the kwarg.
  - Public log/transcript sanitizing removes any `channel_projection` object echoed in policy results.

- [x] **Step 1: Extend the fake channel runtime and recording policy**

In `tests/arena/test_coordinator.py`, modify `FakeChannelRuntime.admit_player` to import and include `ChannelProjection`:

```python
    async def admit_player(self, gs, player_id, turn, *, guidance=False):
        from types import SimpleNamespace
        from civ_mcp.arena.channel_protocol import ChannelTurnContext
        from civ_mcp.arena.channels import ChannelProjection

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
        projection = ChannelProjection(
            player_id=player_id,
            guidance=guidance,
        )
        return SimpleNamespace(
            player_id=player_id,
            turn=turn,
            observation_id=None,
            projection=projection,
            block="CHANNEL BLOCK",
            context=context,
            wake_reasons=self.wake_reasons,
        )
```

Modify `ChannelRecordingPolicy.__call__` to accept and record `channel_projection`:

```python
    async def __call__(
        self,
        gs,
        player_id,
        turn,
        *,
        channel_context=None,
        channel_block="",
        channel_projection=None,
        master_block="",
    ):
        self.runtime._record(f"policy:{player_id}:{turn}")
        self.calls.append(
            {
                "channel_context": channel_context,
                "channel_block": channel_block,
                "channel_projection": channel_projection,
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
```

- [x] **Step 2: Write the failing normal puppet projection injection assertions**

In `test_channels_open_admit_policy_finish_with_run_identity`, add:

```python
    assert policy.calls[0]["channel_projection"].player_id == 1
    assert policy.calls[0]["channel_projection"].guidance is False
```

In `test_channel_guidance_option_is_passed_to_runtime`, add:

```python
    assert policy.calls[0]["channel_projection"].guidance is True
```

Add this test near those channel-wiring tests:

```python
@pytest.mark.asyncio
async def test_channel_projection_kwarg_is_signature_gated(monkeypatch, tmp_path):
    runtime = FakeChannelRuntime()
    _patch_channel_open(monkeypatch, runtime)

    class NoProjectionPolicy:
        provider = "local"
        model = "no-projection"
        options = _channel_options()

        def __init__(self):
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
            self.calls.append(
                {
                    "channel_context": channel_context,
                    "channel_block": channel_block,
                    "master_block": master_block,
                }
            )
            return {"summary": "ok", "actions": []}

    policy = NoProjectionPolicy()
    conn = FakeConn()
    conn._polls = iter([["LOCAL|1", "TURN|7", "ACTIVE|true", "LAST|0"]])

    await run_arena(
        conn,
        FakeGS(),
        _channel_config(tmp_path),
        policy=policy,
        transcript=FakeSink(),
    )

    assert policy.calls == [
        {
            "channel_context": runtime.finish_admissions[0].context,
            "channel_block": "CHANNEL BLOCK",
            "master_block": "",
        }
    ]
```

- [x] **Step 3: Write the failing privacy sanitizer test case**

Inside `_privacy_channel_result`, add `channel_projection` both at the top level and inside `transcript`:

```python
        "channel_projection": {
            "player_id": 1,
            "secret": f"{canary}-projection",
        },
```

and inside the returned `transcript` dict:

```python
            "channel_projection": {
                "player_id": 1,
                "secret": f"{canary}-transcript-projection",
            },
```

No assertion changes are needed because `_assert_public_channel_result_is_private_free` already fails when the canary leaks.

- [x] **Step 4: Run the coordinator channel tests and verify the expected failures**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_coordinator.py::test_channels_open_admit_policy_finish_with_run_identity \
  tests/arena/test_coordinator.py::test_channel_guidance_option_is_passed_to_runtime \
  tests/arena/test_coordinator.py::test_channel_projection_kwarg_is_signature_gated \
  tests/arena/test_coordinator.py::test_channel_enabled_puppet_public_view_removes_all_private_result_shapes \
  -v
```

Expected: projection assertions fail because `channel_projection` is not passed yet, and the privacy test leaks the projection canary.

- [x] **Step 5: Inject `channel_projection` for seat 0 capture**

In `src/civ_mcp/arena/coordinator.py`, add `"channel_projection"` to `_PRIVATE_CHANNEL_RESULT_FIELDS`:

```python
    "channel_projection",
```

In `_seat0_channel_policy_kwargs`, add `channel_projection` to the kwarg tuple:

```python
            for name, value in (
                ("channel_context", admission.context),
                ("channel_block", admission.block),
                ("channel_projection", admission.projection),
                ("master_block", ""),
            )
            if _policy_accepts_kwarg(pol, name)
```

- [x] **Step 6: Inject `channel_projection` for normal puppet turns**

In the normal puppet channel kwarg block, add `channel_projection` beside the existing context/block/master entries:

```python
                        (
                            "channel_projection",
                            channel_admission.projection
                            if channel_admission is not None else None,
                        ),
```

The complete tuple should now inject `channel_context`, `channel_block`, `channel_projection`, and `master_block`, each gated by `_policy_accepts_kwarg`.

- [x] **Step 7: Run the targeted coordinator tests**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_coordinator.py::test_channels_open_admit_policy_finish_with_run_identity \
  tests/arena/test_coordinator.py::test_channel_guidance_option_is_passed_to_runtime \
  tests/arena/test_coordinator.py::test_channel_projection_kwarg_is_signature_gated \
  tests/arena/test_coordinator.py::test_channel_enabled_puppet_public_view_removes_all_private_result_shapes \
  -v
```

Expected: all four tests pass.

- [x] **Step 8: Check the diff and commit**

Run:

```bash
git diff --check
git add tests/arena/test_coordinator.py src/civ_mcp/arena/coordinator.py
git commit -m "feat(arena): pass channel projections to policies"
```

Expected: `git diff --check` prints no output, and the commit succeeds.

---

### Task 3: ScriptedPolicy Script Dispatch And Auto-Fund

**Files:**
- Modify: `tests/arena/test_coordinator.py`
- Modify: `src/civ_mcp/arena/scripted_policy.py`

**Interfaces:**
- Consumes:
  - `CivOptions.channels.script: tuple[ChannelScriptStep, ...]`.
  - `ChannelTurnContext.dispatch(name: str, args: dict) -> str`.
  - `ChannelProjection.deals: tuple[Deal, ...]`.
  - `DealState.ACTIVE` and `PaymentStatus.DUE`.
- Produces:
  - `ScriptedPolicy.__call__(gs, player_id: int, turn: int, *, blocker_block: str = "", channel_context=None, channel_block: str = "", channel_projection=None, **_ignored) -> dict`.
  - Normal calls dispatch matching script steps in list order.
  - Normal calls append channel dispatch outcomes to `result["actions"]` as `{"tool": "channel:<action>", "result": <outcome>}`.
  - Dispatch exceptions append `{"tool": "channel:<action>", "error": repr(exc)}` and add an error fragment to the summary.
  - Auto-fund appends `{"tool": "channel:fund_deal", "deal_id": <deal_id>, "result": <outcome>}` on success.
  - Repair calls do not dispatch script steps or auto-fund deals.

- [x] **Step 1: Add scripted channel test helpers**

In `tests/arena/test_coordinator.py`, add these helpers below `_prod_block`:

```python
class _RecordingChannelContext:
    def __init__(self, *, fail_actions=()):
        self.dispatched = []
        self.fail_actions = set(fail_actions)

    def dispatch(self, action, args):
        self.dispatched.append((action, copy.deepcopy(args)))
        if action in self.fail_actions:
            raise RuntimeError(f"{action} boom")
        return f"QUEUED {action}"


def _script_step(turn, action, args):
    from civ_mcp.arena.config import ChannelScriptStep

    return ChannelScriptStep(turn=turn, action=action, args=args)


def _scripted_channel_options(*steps):
    from civ_mcp.arena.config import ChannelOptions

    return CivOptions(channels=ChannelOptions(enabled=True, script=tuple(steps)))


def _channel_projection_with_deals(player_id, *deals):
    from civ_mcp.arena.channels import ChannelProjection

    return ChannelProjection(player_id=player_id, deals=tuple(deals))


def _projection_deal(
    deal_id,
    *,
    proposer,
    counterparty=1,
    state=None,
    payment_status=None,
    fund_by_turn=9,
):
    from civ_mcp.arena.channels import (
        Deal,
        DealState,
        FavorStatus,
        FavorTerm,
        PaymentStatus,
    )

    return Deal(
        id=deal_id,
        proposer=proposer,
        counterparty=counterparty,
        created_turn=7,
        accepted_turn=7,
        accept_by_turn=10,
        completion_window_turns=5,
        favor=FavorTerm("keep_units_away", {"player_id": 3, "min_distance": 3, "unit_scope": "military"}),
        payment_gold=50,
        timing="on_delivery",
        state=state or DealState.ACTIVE,
        favor_status=FavorStatus.SATISFIED,
        payment_status=payment_status or PaymentStatus.DUE,
        fund_by_turn=fund_by_turn,
        payment_response_by_turn=None,
        favor_due_turn=12,
        terminal=None,
    )
```

- [x] **Step 2: Write failing script dispatch tests**

Add these tests in the existing `Task 9 - ScriptedPolicy normal/repair determinism` section:

```python
@pytest.mark.asyncio
async def test_scripted_normal_dispatches_matching_channel_script_steps_in_order():
    ctx = _RecordingChannelContext()
    policy = ScriptedPolicy(
        options=_scripted_channel_options(
            _script_step(7, "send_message", {"to_player": 1, "text": "first"}),
            _script_step(8, "send_message", {"to_player": 1, "text": "wrong turn"}),
            _script_step(7, "propose_deal", {
                "to_player": 1,
                "text": "second",
                "favor": {
                    "term_type": "keep_units_away",
                    "params": {"player_id": 3, "min_distance": 3, "unit_scope": "military"},
                },
                "payment_gold": 50,
                "timing": "on_delivery",
                "within": 5,
            }),
        )
    )

    result = await policy(
        _ScriptedGS(),
        3,
        7,
        channel_context=ctx,
        channel_projection=_channel_projection_with_deals(3),
    )

    assert ctx.dispatched == [
        ("send_message", {"to_player": 1, "text": "first"}),
        ("propose_deal", {
            "to_player": 1,
            "text": "second",
            "favor": {
                "term_type": "keep_units_away",
                "params": {"player_id": 3, "min_distance": 3, "unit_scope": "military"},
            },
            "payment_gold": 50,
            "timing": "on_delivery",
            "within": 5,
        }),
    ]
    assert result["actions"] == [
        {"tool": "skip_unit"},
        {"tool": "channel:send_message", "result": "QUEUED send_message"},
        {"tool": "channel:propose_deal", "result": "QUEUED propose_deal"},
    ]
    assert "channel send_message queued" in result["summary"]
    assert "channel propose_deal queued" in result["summary"]
```

```python
@pytest.mark.asyncio
async def test_scripted_normal_skip_failure_does_not_block_channel_script():
    class _NoUnitGS(_ScriptedGS):
        async def skip_unit(self, i):
            raise RuntimeError("no unit 0")

    ctx = _RecordingChannelContext()
    policy = ScriptedPolicy(
        options=_scripted_channel_options(
            _script_step(7, "send_message", {"to_player": 1, "text": "still runs"})
        )
    )

    result = await policy(
        _NoUnitGS(),
        3,
        7,
        channel_context=ctx,
        channel_projection=_channel_projection_with_deals(3),
    )

    assert ctx.dispatched == [("send_message", {"to_player": 1, "text": "still runs"})]
    assert result["actions"] == [
        {"tool": "channel:send_message", "result": "QUEUED send_message"}
    ]
    assert "skip failed RuntimeError('no unit 0')" in result["summary"]
```

```python
@pytest.mark.asyncio
async def test_scripted_normal_channel_dispatch_exception_is_summary_only():
    ctx = _RecordingChannelContext(fail_actions={"send_message"})
    policy = ScriptedPolicy(
        options=_scripted_channel_options(
            _script_step(7, "send_message", {"to_player": 1, "text": "bad"})
        )
    )

    result = await policy(
        _ScriptedGS(),
        3,
        7,
        channel_context=ctx,
        channel_projection=_channel_projection_with_deals(3),
    )

    assert ctx.dispatched == [("send_message", {"to_player": 1, "text": "bad"})]
    assert result["actions"] == [
        {"tool": "skip_unit"},
        {"tool": "channel:send_message", "error": "RuntimeError('send_message boom')"},
    ]
    assert "channel send_message failed RuntimeError('send_message boom')" in result["summary"]
```

- [x] **Step 3: Write failing auto-fund and repair-skip tests**

Add:

```python
@pytest.mark.asyncio
async def test_scripted_normal_auto_funds_only_own_active_due_deals():
    from civ_mcp.arena.channels import DealState, PaymentStatus

    ctx = _RecordingChannelContext()
    projection = _channel_projection_with_deals(
        3,
        _projection_deal("deal-000001", proposer=3, fund_by_turn=7),
        _projection_deal("deal-000002", proposer=2, fund_by_turn=7),
        _projection_deal("deal-000003", proposer=3, state=DealState.PROPOSED, fund_by_turn=7),
        _projection_deal("deal-000004", proposer=3, payment_status=PaymentStatus.OFFERED, fund_by_turn=7),
        _projection_deal("deal-000005", proposer=3, fund_by_turn=None),
        _projection_deal("deal-000006", proposer=3, fund_by_turn=6),
    )

    result = await ScriptedPolicy(options=_scripted_channel_options())(
        _ScriptedGS(),
        3,
        7,
        channel_context=ctx,
        channel_projection=projection,
    )

    assert ctx.dispatched == [("fund_deal", {"deal_id": "deal-000001"})]
    assert result["actions"] == [
        {"tool": "skip_unit"},
        {"tool": "channel:fund_deal", "deal_id": "deal-000001", "result": "QUEUED fund_deal"},
    ]
```

```python
@pytest.mark.asyncio
async def test_scripted_repair_skips_channel_script_and_auto_fund():
    ctx = _RecordingChannelContext()
    policy = ScriptedPolicy(
        options=_scripted_channel_options(
            _script_step(7, "send_message", {"to_player": 1, "text": "blocked"})
        )
    )

    await policy(
        _ScriptedGS(),
        3,
        7,
        blocker_block=_prod_block("ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT"),
        channel_context=ctx,
        channel_projection=_channel_projection_with_deals(
            3,
            _projection_deal("deal-000001", proposer=3, fund_by_turn=7),
        ),
    )

    assert ctx.dispatched == []
```

- [x] **Step 4: Run the ScriptedPolicy channel tests and verify the expected failures**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_coordinator.py::test_scripted_normal_dispatches_matching_channel_script_steps_in_order \
  tests/arena/test_coordinator.py::test_scripted_normal_skip_failure_does_not_block_channel_script \
  tests/arena/test_coordinator.py::test_scripted_normal_channel_dispatch_exception_is_summary_only \
  tests/arena/test_coordinator.py::test_scripted_normal_auto_funds_only_own_active_due_deals \
  tests/arena/test_coordinator.py::test_scripted_repair_skips_channel_script_and_auto_fund \
  -v
```

Expected: tests fail because `ScriptedPolicy` ignores `channel_context`, `channel_projection`, and `ChannelOptions.script`.

- [x] **Step 5: Add explicit channel parameters and helper imports**

In `src/civ_mcp/arena/scripted_policy.py`, replace the import block with:

```python
from __future__ import annotations

import copy

from civ_mcp.arena.channel_protocol import ChannelTurnContext
from civ_mcp.arena.channels import ChannelProjection, DealState, PaymentStatus
from civ_mcp.arena.config import CivOptions
```

Replace the `__call__` signature with:

```python
    async def __call__(
        self,
        gs,
        player_id: int,
        turn: int,
        *,
        blocker_block: str = "",
        channel_context: ChannelTurnContext | None = None,
        channel_block: str = "",
        channel_projection: ChannelProjection | None = None,
        **_ignored,
    ) -> dict:
```

- [x] **Step 6: Replace the normal path implementation**

Intentional format change: the no-context normal summary becomes
`"scripted: observed; skipped unit 0"` (was `"scripted: observed + skipped
unit 0"`). A repo-wide grep confirms no test or fake pins the old string;
the uniform `"; ".join(summary_parts)` shape is what lets skip failures
and channel outcomes compose into one summary.

Inside `ScriptedPolicy.__call__`, keep the repair branch first, then replace the normal body with:

```python
        # NORMAL / dry-run: observe, skip unit 0, choose nothing strategic.
        await gs.get_game_overview()
        await gs.get_units()
        actions: list[dict] = []
        summary_parts: list[str] = ["scripted: observed"]
        try:
            await gs.skip_unit(0)
            actions.append({"tool": "skip_unit"})
            summary_parts.append("skipped unit 0")
        except Exception as e:
            summary_parts.append(f"skip failed {e!r}")

        if channel_context is not None:
            channel_actions, channel_summaries = self._run_channel_actions(
                player_id=player_id,
                turn=turn,
                channel_context=channel_context,
                channel_projection=channel_projection,
            )
            actions.extend(channel_actions)
            summary_parts.extend(channel_summaries)

        return {"summary": "; ".join(summary_parts), "actions": actions}
```

- [x] **Step 7: Add channel dispatch helpers**

Add these methods inside `ScriptedPolicy`, below `__call__` and above `_repair`:

```python
    def _run_channel_actions(
        self,
        *,
        player_id: int,
        turn: int,
        channel_context: ChannelTurnContext,
        channel_projection: ChannelProjection | None,
    ) -> tuple[list[dict], list[str]]:
        actions: list[dict] = []
        summaries: list[str] = []

        for step in self.options.channels.script:
            if step.turn != turn:
                continue
            action, summary = self._dispatch_channel_action(
                channel_context,
                step.action,
                copy.deepcopy(step.args),
            )
            actions.append(action)
            summaries.append(summary)

        for deal_id in self._auto_fund_deal_ids(
            player_id=player_id,
            turn=turn,
            channel_projection=channel_projection,
        ):
            action, summary = self._dispatch_channel_action(
                channel_context,
                "fund_deal",
                {"deal_id": deal_id},
            )
            if "error" not in action:
                action["deal_id"] = deal_id
            actions.append(action)
            summaries.append(summary)

        return actions, summaries

    @staticmethod
    def _dispatch_channel_action(
        channel_context: ChannelTurnContext,
        action_name: str,
        args: dict,
    ) -> tuple[dict, str]:
        try:
            result = channel_context.dispatch(action_name, args)
        except Exception as exc:
            error = repr(exc)
            return (
                {"tool": f"channel:{action_name}", "error": error},
                f"channel {action_name} failed {error}",
            )
        return (
            {"tool": f"channel:{action_name}", "result": result},
            f"channel {action_name} queued",
        )

    @staticmethod
    def _auto_fund_deal_ids(
        *,
        player_id: int,
        turn: int,
        channel_projection: ChannelProjection | None,
    ) -> tuple[str, ...]:
        if channel_projection is None:
            return ()
        return tuple(
            deal.id
            for deal in channel_projection.deals
            if deal.proposer == player_id
            and deal.state is DealState.ACTIVE
            and deal.payment_status is PaymentStatus.DUE
            and deal.fund_by_turn is not None
            and turn <= deal.fund_by_turn
        )
```

- [x] **Step 8: Run the ScriptedPolicy tests**

Run:

```bash
uv run --extra test pytest tests/arena/test_coordinator.py -k 'scripted' -v
```

Expected: all selected scripted-policy tests pass.

- [x] **Step 9: Check the diff and commit**

Run:

```bash
git diff --check
git add tests/arena/test_coordinator.py src/civ_mcp/arena/scripted_policy.py
git commit -m "feat(arena): dispatch scripted channel actions"
```

Expected: `git diff --check` prints no output, and the commit succeeds.

---

### Task 4: V3 Guidance And Experiment Artifact

**Files:**
- Modify: `tests/arena/test_channels.py`
- Modify: `tests/arena/test_experiment.py`
- Modify: `src/civ_mcp/arena/channels.py`
- Create: `experiments/arena-channels-behavior-v3.yaml`

**Interfaces:**
- Consumes:
  - `CHANNEL_GUIDANCE_TEXT` and `format_channel_block` with a `ChannelProjection` whose `guidance` field is `True`.
  - `load_experiment(path: Path) -> ArenaConfig`.
  - `ChannelScriptStep` from Task 1.
- Produces:
  - Guidance text that explicitly says messages alone are not binding and names `propose_deal`, `respond_to_deal`, and `fund_deal`.
  - `experiments/arena-channels-behavior-v3.yaml` matching the approved spec.
  - Loader test pinning v3 run budgets, roster, guidance flags, and both P3 script steps.

- [x] **Step 1: Add the failing guidance assertion**

In `tests/arena/test_channels.py`, add this test near the existing guidance rendering tests:

```python
def test_channel_guidance_names_binding_deal_actions():
    assert "messages alone are NOT binding" in CHANNEL_GUIDANCE_TEXT
    assert "propose_deal action" in CHANNEL_GUIDANCE_TEXT
    assert "respond_to_deal before it expires" in CHANNEL_GUIDANCE_TEXT
    assert "fund deals you owe with fund_deal" in CHANNEL_GUIDANCE_TEXT
```

- [x] **Step 2: Add the v3 artifact loader test**

In `tests/arena/test_experiment.py`, add the v3 constant below the v2 constant:

```python
ARENA_CHANNELS_BEHAVIOR_V3 = REPO_ROOT / "experiments" / "arena-channels-behavior-v3.yaml"
```

Add this test below `test_loads_arena_channels_behavior_v2_artifact`:

```python
def test_loads_arena_channels_behavior_v3_artifact():
    cfg = load_experiment(ARENA_CHANNELS_BEHAVIOR_V3)

    assert cfg.run_id == "arena-channels-behavior-v3"
    assert cfg.max_puppet_turns == 90
    assert cfg.max_game_turns == 108
    assert cfg.channel_rules.acceptance_turns == 3
    assert cfg.channel_rules.funding_turns == 2
    assert cfg.channel_rules.payment_response_turns == 2
    assert cfg.live_gate == LiveGateOptions()

    by_player = {player.player_id: player for player in cfg.players}
    assert set(by_player) == {1, 2, 3}

    assert by_player[1].provider == "local"
    assert by_player[1].model == "gemma4-26b"
    assert by_player[1].gateway == "http://192.168.20.196:11440/v1"
    assert by_player[1].options.tools == "minimal"
    assert by_player[1].options.max_steps == 15
    assert by_player[1].options.channels.enabled is True
    assert by_player[1].options.channels.guidance is True
    assert by_player[1].options.channels.script == ()

    assert by_player[2].provider == "local"
    assert by_player[2].model == "qwen3.6-27b"
    assert by_player[2].gateway == "http://192.168.20.196:11441/v1"
    assert by_player[2].options.tools == "minimal"
    assert by_player[2].options.max_steps == 15
    assert by_player[2].options.channels.enabled is True
    assert by_player[2].options.channels.guidance is True
    assert by_player[2].options.channels.script == ()

    script = by_player[3].options.channels.script
    assert by_player[3].provider == "scripted"
    assert by_player[3].options.channels.enabled is True
    assert by_player[3].options.channels.guidance is False
    assert tuple(step.action for step in script) == ("propose_deal", "propose_deal")
    assert tuple(step.turn for step in script) == (157, 157)
    assert tuple(step.args["to_player"] for step in script) == (1, 2)
    assert all(step.args["payment_gold"] == 50 for step in script)
    assert all(step.args["timing"] == "on_delivery" for step in script)
    assert all(step.args["within"] == 5 for step in script)
    assert all(
        step.args["favor"] == {
            "term_type": "keep_units_away",
            "params": {"player_id": 3, "min_distance": 3, "unit_scope": "military"},
        }
        for step in script
    )
```

- [x] **Step 3: Run the new tests and verify the expected failures**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_channels.py::test_channel_guidance_names_binding_deal_actions \
  tests/arena/test_experiment.py::test_loads_arena_channels_behavior_v3_artifact \
  -v
```

Expected: guidance assertion fails and the v3 artifact path is missing.

- [x] **Step 4: Append the v3 directive to guidance text**

In `src/civ_mcp/arena/channels.py`, replace `CHANNEL_GUIDANCE_TEXT` with:

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
    "would advance your position. Important: messages alone are NOT binding — a "
    "deal exists only once it is created with the propose_deal action and "
    "answered with respond_to_deal. When you and a rival converge on terms, turn "
    "them into a propose_deal immediately; when a proposal is pending for you, "
    "answer it with respond_to_deal before it expires; fund deals you owe with "
    "fund_deal."
)
```

- [x] **Step 5: Create the v3 experiment artifact**

Create `experiments/arena-channels-behavior-v3.yaml` with exactly:

```yaml
run_id: arena-channels-behavior-v3
max_puppet_turns: 90
max_game_turns: 108
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
    max_steps: 15
    channels: {enabled: true, guidance: true}
  - player: 2
    provider: local
    model: qwen3.6-27b
    gateway: http://192.168.20.196:11441/v1
    tools: minimal
    max_steps: 15
    channels: {enabled: true, guidance: true}
  - player: 3
    provider: scripted
    channels:
      enabled: true
      script:
        - turn: 157
          action: propose_deal
          args:
            to_player: 1
            text: "I'll pay you 50 gold if you keep your military units at least 3 tiles away from my lands for the next few turns. A simple deal to build trust between us."
            favor:
              term_type: keep_units_away
              params: {player_id: 3, min_distance: 3, unit_scope: military}
            payment_gold: 50
            timing: on_delivery
            within: 5
        - turn: 157
          action: propose_deal
          args:
            to_player: 2
            text: "I'll pay you 50 gold if you keep your military units at least 3 tiles away from my lands for the next few turns. A simple deal to build trust between us."
            favor:
              term_type: keep_units_away
              params: {player_id: 3, min_distance: 3, unit_scope: military}
            payment_gold: 50
            timing: on_delivery
            within: 5
```

- [x] **Step 6: Run the channel guidance and artifact tests**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_channels.py::test_channel_guidance_names_binding_deal_actions \
  tests/arena/test_channels.py::test_disabled_projection_is_empty_and_cli_examples_are_exact \
  tests/arena/test_experiment.py::test_loads_arena_channels_behavior_v3_artifact \
  -v
```

Expected: all selected tests pass.

- [x] **Step 7: Run the focused regression set**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_config.py \
  tests/arena/test_experiment.py \
  tests/arena/test_channels.py \
  tests/arena/test_channel_runtime.py::test_admission_guidance_keyword_sets_projection_and_block \
  tests/arena/test_coordinator.py -k 'channel or scripted' \
  -v
```

Expected: all selected tests pass.

- [x] **Step 8: Check the diff and commit**

Run:

```bash
git diff --check
git add \
  tests/arena/test_channels.py \
  tests/arena/test_experiment.py \
  src/civ_mcp/arena/channels.py \
  experiments/arena-channels-behavior-v3.yaml
git commit -m "test(arena): add channels behavior v3 artifact"
```

Expected: `git diff --check` prints no output, and the commit succeeds.

---

## Final Verification

- [x] **Step 1: Run the complete targeted arena suite**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_config.py \
  tests/arena/test_experiment.py \
  tests/arena/test_channels.py \
  tests/arena/test_channel_protocol.py \
  tests/arena/test_channel_runtime.py \
  tests/arena/test_coordinator.py -k 'channel or scripted' \
  -v
```

Expected: all selected tests pass.

- [x] **Step 2: Run the full arena suite**

Run:

```bash
uv run --extra test pytest tests/arena -q
```

Expected: all tests pass (~1900+, roughly 2-3 minutes). This catches any regression outside the targeted channel/scripted selections — the coordinator and experiment parser are shared infrastructure.

- [x] **Step 3: Inspect git state**

Run:

```bash
git status --short
```

Expected: only pre-existing untracked arena run artifacts remain. The implementation files from this plan are clean after the task commits.
