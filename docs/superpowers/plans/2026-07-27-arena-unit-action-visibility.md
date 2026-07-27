# Arena Unit-Action Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Great Person activation reachable in the standard tier and render exact, legal-now unit calls without leaking the wrong surface syntax or tools outside a seat's filtered tier.

**Architecture:** The units Lua query adds one safe activation bit to `UnitInfo`; narration formats existing signals differently for MCP and arena callers. Local arena dispatch and local-model briefings pass the same filtered tool tuple used for schema/dispatch, while CLI-agent briefings remain MCP-surface because their subprocesses use the MCP server.

**Tech Stack:** Python 3.12, embedded Civ 6 Lua, arena registry/tool tiers, pytest via `uv run --extra test pytest`.

## Global Constraints

- `TIERS["minimal"]` remains the exact ordered 15-tool tuple.
- `TIERS["standard"]` becomes an exact ordered 28-tool tuple.
- Insert `get_great_people`, then `activate_great_person`, immediately after `repair_improvement`.
- `activate_great_person` remains gated by `requires="gp_unit"`; `get_great_people` remains ungated.
- Tier audit JSON field is exactly `tier_action_verbs_absent`.
- Tier audit lists raw registry verbs and does not apply MCP aliases.
- `surface` is exactly `Literal["mcp", "arena"]`, defaults to `"mcp"`, and invalid values raise `ValueError`.
- MCP upgrade syntax is `upgrade_unit(unit_id=<composite_id>)`.
- Arena action hints use only tools in the already-filtered allowlist.
- Direct arena narration without an allowlist fails closed and emits no exact action calls.
- Keep existing `CAN ATTACK` and generic `Can build` lines unchanged.
- Do not add a promotion affordance; the live units query intentionally leaves `needs_promotion` false.
- Do not add any remote-tile `CanStartOperation` probe.
- Run the full arena suite before each task commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/arena/registry.py` | Extend standard tier, pass dispatch reachability to `get_units`, and select arena narration. |
| `scripts/audit_arena_tool_coverage.py` | Report raw action verbs absent from minimal/standard. |
| `docs/research/arena-tool-coverage-audit.md` | Keep checked-in counts and tier rows synchronized. |
| `src/civ_mcp/lua/models.py` | Add `UnitInfo.can_activate_here`. |
| `src/civ_mcp/lua/units.py` | Collect and parse the safe GP activation signal. |
| `src/civ_mcp/narrate.py` | Validate surfaces and render exact MCP/arena action calls. |
| `src/civ_mcp/server.py` | Select MCP narration for `get_units` and `get_builder_tasks`. |
| `src/civ_mcp/arena/briefing.py` | Render units using the policy-selected surface and allowed tools. |
| `src/civ_mcp/arena/prompt_context.py` | Carry surface/reachability into newly built briefings. |
| `src/civ_mcp/arena/agent.py` | Pass filtered local arena tools to briefing construction. |
| `src/civ_mcp/arena/cli_agent.py` | Request MCP-surface briefing narration. |
| `tests/arena/test_registry.py` | Pin tier order, gating, and dispatch-level hint reachability. |
| `tests/arena/test_tool_coverage_audit.py` | Pin tier verb evidence and checked-in audit rows. |
| `tests/test_parsers.py` | Pin backward-compatible parsing of `can_activate_here`. |
| `tests/test_narrate_units.py` | Exercise both surfaces, exact syntax, ordering, and suppression. |
| `tests/test_narrate_builder_tasks.py` | Replace `tool_hints` tests with explicit surface tests. |
| `tests/arena/test_briefing.py` | Exercise surface/reachability in the units briefing section. |
| `tests/arena/test_prompt_context.py` | Verify context forwarding and supplied-briefing behavior. |
| `tests/arena/test_agent.py` | Verify local policy passes filtered arena tools. |
| `tests/arena/test_cli_agent.py` | Verify CLI policies request MCP narration. |

### Task 1: Standard-tier reachability and tier-aware audit

**Files:**
- Modify: `src/civ_mcp/arena/registry.py`
- Modify: `scripts/audit_arena_tool_coverage.py`
- Modify: `docs/research/arena-tool-coverage-audit.md`
- Modify: `tests/arena/test_registry.py`
- Modify: `tests/arena/test_tool_coverage_audit.py`

**Interfaces:**
- Produces: exact `TIERS["standard"]` 28-tuple.
- Produces: `collect_evidence()["tier_action_verbs_absent"]`.
- Consumed by Tasks 2-3: filtered tier names define which arena hints may render.

- [ ] **Step 1: Update the exact standard-tier snapshot test first**

In `tests/arena/test_registry.py`, update `STANDARD_TIER_SNAPSHOT` so this segment is exact:

```python
        "get_builder_tasks",
        "improve_tile",
        "remove_feature",
        "repair_improvement",
        "get_great_people",
        "activate_great_person",
        "purchase_item",
```

Keep every other entry and the minimal snapshot unchanged. Add:

```python
def test_standard_gp_activation_is_capability_gated():
    assert filter_tools(
        TIERS["standard"], {"gp_unit": False}
    ) == tuple(
        name
        for name in TIERS["standard"]
        if name != "activate_great_person"
    )
    visible = filter_tools(TIERS["standard"], {"gp_unit": True})
    assert "get_great_people" in visible
    assert "activate_great_person" in visible
```

- [ ] **Step 2: Write failing tier-audit tests**

In `tests/arena/test_tool_coverage_audit.py`, append:

```python
def test_tier_action_verbs_are_raw_and_reachability_aware():
    evidence = _audit_module().collect_evidence()
    absent = evidence["tier_action_verbs_absent"]

    assert "improve" in absent["minimal"]
    assert "activate_great_person" in absent["minimal"]
    assert "improve" not in absent["standard"]
    assert "activate_great_person" not in absent["standard"]
    assert "activate" not in absent["minimal"]


def test_checked_in_audit_tracks_gp_tier_rows():
    text = (
        REPO_ROOT / "docs" / "research" / "arena-tool-coverage-audit.md"
    ).read_text(encoding="utf-8")

    assert "standard=28" in text
    assert (
        "| `get_great_people` | no | intentionally-excluded | "
        "yes | present | yes |"
    ) in text
    assert (
        "| `activate_great_person` | no | intentionally-excluded | "
        "yes | present | yes |"
    ) in text
```

- [ ] **Step 3: Run focused tests and verify failures**

Run:

```bash
uv run --extra test pytest tests/arena/test_registry.py tests/arena/test_tool_coverage_audit.py -q
```

Expected: the standard snapshot remains 26 and the audit evidence lacks `tier_action_verbs_absent`.

- [ ] **Step 4: Extend the standard tier**

In `TIERS["standard"]`, insert:

```python
        "get_great_people",
        "activate_great_person",
```

immediately after `repair_improvement`. Do not change `minimal`, `full`, registry order, tool definitions, or capability flags.

- [ ] **Step 5: Add raw per-tier action-verb evidence**

In `scripts/audit_arena_tool_coverage.py`, add:

```python
def _tier_action_verbs_absent(tier: str) -> list[str]:
    names = set(TIERS[tier])
    return sorted(
        {
            tool.verb
            for name, tool in TOOL_REGISTRY.items()
            if tool.verb and name not in names
        }
    )
```

Add to `collect_evidence()`:

```python
        "tier_action_verbs_absent": {
            "minimal": _tier_action_verbs_absent("minimal"),
            "standard": _tier_action_verbs_absent("standard"),
        },
```

In `_print_human`, print both lists after the existing three delta lines:

```python
    tier_absent = evidence["tier_action_verbs_absent"]
    print("Minimal action verbs absent:", tier_absent["minimal"])
    print("Standard action verbs absent:", tier_absent["standard"])
```

Keep `ACTION_ALIASES` only for the existing MCP-vs-arena comparison.

- [ ] **Step 6: Refresh the checked-in audit**

Run:

```bash
uv run python scripts/audit_arena_tool_coverage.py
```

Update `docs/research/arena-tool-coverage-audit.md`:

- counts line: `registry=90`, `minimal=15`, `standard=28`, `full=90`;
- fixed-this-cycle text: standard now includes GP discovery and activation;
- `get_great_people` row: standard `yes | present`;
- `activate_great_person` row: standard `yes | present`;
- decision record: activation is gated and paired with its read-only discovery tool.

Do not rewrite unrelated dispositions.

- [ ] **Step 7: Run focused and full tests**

Run:

```bash
uv run --extra test pytest tests/arena/test_registry.py tests/arena/test_tool_coverage_audit.py -q
uv run --extra test pytest tests/arena -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 1**

```bash
git add src/civ_mcp/arena/registry.py scripts/audit_arena_tool_coverage.py docs/research/arena-tool-coverage-audit.md tests/arena/test_registry.py tests/arena/test_tool_coverage_audit.py
git commit -m "feat(arena): expose great-person activation in standard tier"
```

### Task 2: Safe activation signal and surface-correct direct tool narration

**Files:**
- Modify: `src/civ_mcp/lua/models.py`
- Modify: `src/civ_mcp/lua/units.py`
- Modify: `src/civ_mcp/narrate.py`
- Modify: `src/civ_mcp/server.py`
- Modify: `src/civ_mcp/arena/registry.py`
- Modify: `tests/test_parsers.py`
- Create: `tests/test_narrate_units.py`
- Modify: `tests/test_narrate_builder_tasks.py`
- Modify: `tests/arena/test_registry.py`

**Interfaces:**
- Produces: `UnitInfo.can_activate_here: bool = False`.
- Produces: `narrate_units(units, threats=None, trade_status=None, *, surface="mcp", available_tools=None) -> str`.
- Produces: `narrate_builder_tasks(tasks, builders, *, surface="mcp") -> str`.
- Produces: arena dispatch behavior that passes `allowed` to direct `get_units` rendering.
- Consumed by Task 3: briefing renderers call the same `narrate_units` interface.

- [ ] **Step 1: Write failing parser tests**

In `tests/test_parsers.py`, append to `TestParseUnits`:

```python
    def test_can_activate_here_appended_field(self):
        line = (
            "65541|5|Bhasa|UNIT_GREAT_WRITER|12,19|2/2|100/100|"
            "0|0|1||0|0||||RELIGION_CATHOLICISM|1"
        )
        unit = parse_units_response([line])[0]
        assert unit.can_activate_here is True

    @pytest.mark.parametrize("suffix", ["", "|", "|bad", "|0"])
    def test_can_activate_here_absent_or_malformed_is_false(self, suffix):
        line = (
            "65541|5|Bhasa|UNIT_GREAT_WRITER|12,19|2/2|100/100|"
            f"0|0|1||0|0||||RELIGION_CATHOLICISM{suffix}"
        )
        unit = parse_units_response([line])[0]
        assert unit.can_activate_here is False
```

Add a query-string test asserting `build_units_query()` contains
`GetActivationHighlightPlots`, compares against the current plot index, and does not add any new remote-tile `CanStartOperation` loop.

- [ ] **Step 2: Write failing narration tests**

Create `tests/test_narrate_units.py` with a helper returning a `UnitInfo` whose moves are positive, `valid_improvements=["IMPROVEMENT_MINE"]`, `can_activate_here=True`, and `can_upgrade=True`.

Add:

```python
def test_mcp_surface_renders_callable_mcp_forms():
    text = narrate_units([_unit()], surface="mcp")

    assert 'unit_action(unit_id=65541, action="activate")' in text
    assert (
        'unit_action(unit_id=65541, action="improve", '
        'improvement="IMPROVEMENT_MINE")'
    ) in text
    assert "upgrade_unit(unit_id=65541)" in text
    assert "activate_great_person with" not in text


def test_arena_surface_renders_only_allowed_calls():
    text = narrate_units(
        [_unit()],
        surface="arena",
        available_tools={"improve_tile", "activate_great_person"},
    )

    assert 'activate_great_person with {"unit_id": 65541}' in text
    assert (
        'improve_tile with {"unit_index": 5, '
        '"improvement_name": "IMPROVEMENT_MINE"}'
    ) in text
    assert "upgrade_unit with" not in text
    assert "unit_action(" not in text


def test_arena_surface_without_context_fails_closed():
    text = narrate_units([_unit()], surface="arena")
    assert "AVAILABLE NOW" not in text
    assert ">> Can build: IMPROVEMENT_MINE" in text


def test_invalid_surface_is_rejected():
    with pytest.raises(ValueError, match="surface"):
        narrate_units([_unit()], surface="browser")
```

Add tests for multiple improvements (one line each, input order), zero moves, `"UNKNOWN"`, and `needs_promotion=True` producing no promotion call.

- [ ] **Step 3: Replace builder `tool_hints` tests with surface tests**

In `tests/test_narrate_builder_tasks.py`:

- replace `tool_hints=True` with `surface="arena"`;
- assert the default and explicit `surface="mcp"` use
  `unit_action(unit_id=65541, action="improve")` and
  `unit_action(unit_id=65541, action="repair")`;
- assert arena output uses registry JSON with `unit_index`;
- add invalid-surface and zero-move suppression cases.

The MCP repair call is:

```text
unit_action(unit_id=65541, action="repair")
```

- [ ] **Step 4: Run focused tests and verify failures**

Run:

```bash
uv run --extra test pytest tests/test_parsers.py tests/test_narrate_units.py tests/test_narrate_builder_tasks.py -q
```

Expected: missing model/parser field and unsupported narration arguments.

- [ ] **Step 5: Add and parse the safe Lua activation bit**

In `UnitInfo`, append:

```python
    can_activate_here: bool = False
```

In `build_units_query`, after `gp` is resolved and before the final print, add:

```lua
local canActivateHere = "0"
if gp and entry and entry.GreatPersonClass then
    pcall(function()
        local currentPlot = Map.GetPlot(x, y)
        local plots = gp:GetActivationHighlightPlots()
        if currentPlot and plots then
            local currentIndex = currentPlot:GetIndex()
            for _, plotIndex in ipairs(plots) do
                if plotIndex == currentIndex then
                    canActivateHere = "1"
                    break
                end
            end
        end
    end)
end
```

Append `|` plus `canActivateHere` after `relName` in the print line. Do not call `CanStartOperation` or `CanStartCommand`.

In `parse_units_response`:

```python
        can_activate_here = (
            len(parts) > 17 and parts[17] == "1"
        )
```

Pass it to `UnitInfo`.

- [ ] **Step 6: Implement shared surface validation and exact call formatting**

In `narrate.py`, add:

```python
from collections.abc import Collection
from typing import Literal

ToolSurface = Literal["mcp", "arena"]


def validate_surface(surface: str) -> ToolSurface:
    if surface not in {"mcp", "arena"}:
        raise ValueError(f"unknown tool surface: {surface!r}")
    return surface
```

Extend `narrate_units` with keyword-only `surface: ToolSurface = "mcp"` and
`available_tools: Collection[str] | None = None`. Validate before the empty-unit return. For each positive-move unit:

1. activation call if `can_activate_here`;
2. one improvement call for each non-`UNKNOWN` valid improvement;
3. upgrade call if `can_upgrade`.

On MCP, emit all signaled calls in MCP syntax. On arena, treat
`set(available_tools or ())` as the reachability set and emit only calls whose
registry name is present.

Extend `narrate_builder_tasks` and `_append_builder_list` with
`surface: ToolSurface = "mcp"`. Arena uses existing JSON call hints and shows
`unit_index`; MCP uses `unit_action` calls and shows only the composite ID.
Remove `tool_hints` completely.

- [ ] **Step 7: Select explicit surfaces at server and registry callers**

In `server.py`:

```python
return nr.narrate_units(
    units,
    threats,
    trade_status,
    surface="mcp",
)
```

and:

```python
return nr.narrate_builder_tasks(tasks, builders, surface="mcp")
```

In arena builder narration:

```python
return nr.narrate_builder_tasks(
    tasks,
    builders,
    surface="arena",
)
```

Change arena `_narrate_units` to:

```python
async def _narrate_units(
    gs: Any,
    args: dict[str, Any],
    *,
    available_tools: Collection[str] | None = None,
) -> str:
    del args
    return nr.narrate_units(
        await gs.get_units(),
        surface="arena",
        available_tools=available_tools,
    )
```

In `dispatch`, preserve the allowlist check, then special-case only `get_units`:

```python
    if name == "get_units":
        visible = tuple(TOOL_REGISTRY) if allowed is None else tuple(allowed)
        return await _narrate_units(
            gs,
            args,
            available_tools=visible,
        )
    return await tool.call(gs, args)
```

This bypass is intentionally narrow; do not change all 90 `ToolDef.call` signatures.

- [ ] **Step 8: Add dispatch-level reachability tests**

In `tests/arena/test_registry.py`, use a fake `get_units` result with all three signals true and assert:

```python
minimal = await dispatch(gs, "get_units", {}, allowed=TIERS["minimal"])
standard = await dispatch(gs, "get_units", {}, allowed=TIERS["standard"])
full = await dispatch(gs, "get_units", {}, allowed=None)

assert "improve_tile with" not in minimal
assert "activate_great_person with" not in minimal
assert "upgrade_unit with" not in minimal
assert "improve_tile with" in standard
assert "activate_great_person with" in standard
assert "upgrade_unit with" not in standard
assert "upgrade_unit with" in full
```

Also assert `filter_tools(TIERS["standard"], {"gp_unit": False})` passed as
`allowed` suppresses only the activation hint while retaining improvement.

- [ ] **Step 9: Run focused and full tests**

Run:

```bash
uv run --extra test pytest tests/test_parsers.py tests/test_narrate_units.py tests/test_narrate_builder_tasks.py tests/arena/test_registry.py -q
uv run --extra test pytest tests/arena -q
```

Expected: all tests pass.

- [ ] **Step 10: Commit Task 2**

```bash
git add src/civ_mcp/lua/models.py src/civ_mcp/lua/units.py src/civ_mcp/narrate.py src/civ_mcp/server.py src/civ_mcp/arena/registry.py tests/test_parsers.py tests/test_narrate_units.py tests/test_narrate_builder_tasks.py tests/arena/test_registry.py
git commit -m "feat(arena): narrate reachable unit actions by surface"
```

### Task 3: Preserve surface and reachability in opening briefings

**Files:**
- Modify: `src/civ_mcp/arena/briefing.py`
- Modify: `src/civ_mcp/arena/prompt_context.py`
- Modify: `src/civ_mcp/arena/agent.py`
- Modify: `src/civ_mcp/arena/cli_agent.py`
- Modify: `tests/arena/test_briefing.py`
- Modify: `tests/arena/test_prompt_context.py`
- Modify: `tests/arena/test_agent.py`
- Modify: `tests/arena/test_cli_agent.py`

**Interfaces:**
- Consumes: Task 2's `ToolSurface` and `narrate_units` arguments.
- Produces: `build_briefing(gs, opts, budget_tokens, *, surface, available_tools) -> Briefing`.
- Produces: `maybe_build_briefing(gs, options, *, n_ctx, playbook_chars, tool_schema_chars, supplied, surface, available_tools) -> Briefing`.
- Local `LLMPolicy` supplies arena surface plus `visible_names`.
- `CLIAgentPolicy` supplies MCP surface and no arena allowlist.

- [ ] **Step 1: Write failing briefing surface tests**

In `tests/arena/test_briefing.py`, construct a units-only briefing fake whose
unit has all Task 2 signals true. Add:

```python
@pytest.mark.asyncio
async def test_units_briefing_uses_arena_reachability():
    standard = await build_briefing(
        UnitsGS([_actionable_unit()]),
        BriefingOptions(enabled=True, sections=("units",)),
        10_000,
        surface="arena",
        available_tools=TIERS["standard"],
    )
    assert "activate_great_person with" in standard.text
    assert "improve_tile with" in standard.text
    assert "upgrade_unit with" not in standard.text
    assert "unit_action(" not in standard.text


@pytest.mark.asyncio
async def test_units_briefing_uses_mcp_surface_for_cli_policy():
    briefing = await build_briefing(
        UnitsGS([_actionable_unit()]),
        BriefingOptions(enabled=True, sections=("units",)),
        10_000,
        surface="mcp",
    )
    assert 'unit_action(unit_id=65541, action="activate")' in briefing.text
    assert "upgrade_unit(unit_id=65541)" in briefing.text
    assert "activate_great_person with" not in briefing.text
```

Add invalid-surface and arena-without-allowlist fail-closed tests.

- [ ] **Step 2: Write failing prompt-context forwarding tests**

Update the fake in
`test_maybe_build_briefing_builds_with_shared_budget` to accept keyword-only
`surface` and `available_tools`, capture them, then call:

```python
result = await maybe_build_briefing(
    gs,
    options,
    n_ctx=8192,
    playbook_chars=400,
    tool_schema_chars=800,
    surface="arena",
    available_tools=("get_units", "activate_great_person"),
)
```

Assert both values reached `build_briefing`. Keep the supplied-briefing test:
when `supplied` is non-`None`, the builder is not called regardless of the
surface arguments.

- [ ] **Step 3: Write policy-level forwarding tests**

In `tests/arena/test_agent.py`, monkeypatch `maybe_build_briefing`, call a
local `LLMPolicy` with standard tools and `caps={"gp_unit": False}`, and assert:

```python
assert captured["surface"] == "arena"
assert "get_great_people" in captured["available_tools"]
assert "activate_great_person" not in captured["available_tools"]
```

In `tests/arena/test_cli_agent.py`, extend the existing fake briefing capture
to assert:

```python
assert captured["surface"] == "mcp"
assert captured["available_tools"] is None
```

- [ ] **Step 4: Run focused tests and verify signature failures**

Run:

```bash
uv run --extra test pytest tests/arena/test_briefing.py tests/arena/test_prompt_context.py tests/arena/test_agent.py tests/arena/test_cli_agent.py -q
```

Expected: `build_briefing` / `maybe_build_briefing` do not yet accept the new
surface context.

- [ ] **Step 5: Thread the rendering context through briefing construction**

In `briefing.py`:

```python
async def build_briefing(
    gs: Any,
    opts: BriefingOptions,
    budget_tokens: int,
    *,
    surface: nr.ToolSurface = "mcp",
    available_tools: Collection[str] | None = None,
) -> Briefing:
```

Validate `surface` once using `nr.validate_surface`, and initialize:

```python
ctx: dict[str, Any] = {
    "surface": surface,
    "available_tools": available_tools,
}
```

Change `_units`:

```python
async def _units(gs: Any, ctx: dict[str, Any]) -> str:
    result = await _fetch_units_result(gs, ctx)
    if isinstance(result, str):
        return result
    return nr.narrate_units(
        result,
        surface=ctx["surface"],
        available_tools=ctx["available_tools"],
    )
```

Preserve `_render` error/string behavior if `_fetch_units_result` can return
an error string; do not call `narrate_units` on a string.

In `prompt_context.py`, add keyword-only defaults:

```python
    surface: ToolSurface = "mcp",
    available_tools: Collection[str] | None = None,
```

and pass them to `build_briefing`. Keep the early `supplied` return unchanged.

- [ ] **Step 6: Select the correct policy surface**

In local `LLMPolicy.__call__`, where `visible_names` already feeds schema and
dispatch, pass:

```python
surface="arena",
available_tools=visible_names,
```

to `maybe_build_briefing`.

In `CLIAgentPolicy.__call__`, pass:

```python
surface="mcp",
available_tools=None,
```

The CLI subprocess uses the MCP server, so do not resolve arena tiers in
`cli_agent.py`.

- [ ] **Step 7: Update existing test fakes for the new keyword arguments**

Search:

```bash
rg -n "fake_build_briefing|fake_briefing|maybe_build_briefing" tests/arena
```

For fakes that stand in for `build_briefing`, accept keyword-only `surface`
and `available_tools`. For fakes replacing `maybe_build_briefing`, accept
`**kwargs` unless the test explicitly asserts the new contract. Do not weaken
production signatures to accommodate stale fakes.

- [ ] **Step 8: Run focused and full tests**

Run:

```bash
uv run --extra test pytest tests/arena/test_briefing.py tests/arena/test_prompt_context.py tests/arena/test_agent.py tests/arena/test_cli_agent.py -q
uv run --extra test pytest tests/arena -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit Task 3**

```bash
git add src/civ_mcp/arena/briefing.py src/civ_mcp/arena/prompt_context.py src/civ_mcp/arena/agent.py src/civ_mcp/arena/cli_agent.py tests/arena/test_briefing.py tests/arena/test_prompt_context.py tests/arena/test_agent.py tests/arena/test_cli_agent.py
git commit -m "fix(arena): preserve tool surface in unit briefings"
```

## Final Verification

- [ ] Run:

```bash
uv run --extra test pytest tests/test_parsers.py tests/test_narrate_units.py tests/test_narrate_builder_tasks.py tests/arena/test_registry.py tests/arena/test_tool_coverage_audit.py tests/arena/test_briefing.py tests/arena/test_prompt_context.py tests/arena/test_agent.py tests/arena/test_cli_agent.py -q
uv run --extra test pytest tests/arena -q
uv run python scripts/audit_arena_tool_coverage.py --json
git diff --check
git status --short
```

- [ ] Confirm minimal remains 15, standard is 28, local arena hints never name
out-of-tier/gated tools, CLI briefings use MCP syntax, and the tracked
worktree is clean.
