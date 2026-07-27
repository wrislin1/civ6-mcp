# Arena Post-V5 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the v6 channel-guidance ablation, close the routine builder-tool gap in the arena `standard` tier with a complete coverage audit, and correct the tracked live-arena skill's GitHub landing instructions.

**Architecture:** This is a small post-run maintenance batch with three independently reviewable commits. The v6 task changes one schema string and clones v5 with only its run id changed. The tier task adds one adapter over existing `GameState` behavior, synchronizes action vocabulary, pins both stable tiers, updates the MCP action reference, and records the complete cross-surface audit. The final task changes only the tracked operator skill and its ignored local mirror.

**Tech Stack:** Python 3.12, pytest via `uv run --extra test pytest`, YAML experiment artifacts, Markdown documentation.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-26-arena-post-v5-fixes-design.md`.
- Remove exactly this suffix from the `propose_deal` description, including its leading space: ` If you want to be PAID for a favor you perform, do not use this — send a message asking the other player to propose the deal to you.`
- Retain exactly: `Propose an unofficial favor-for-gold deal. YOU are the payer: you pay payment_gold and to_player performs the favor.`
- `experiments/arena-channels-behavior-v6.yaml` must equal v5 byte-for-byte after replacing the single v5 `run_id` line with the v6 `run_id` line.
- `TIERS["minimal"]` remains the exact ordered 15-tool tuple used by v1-v5.
- `TIERS["standard"]` gains exactly `get_builder_tasks` and `repair_improvement`, becoming an exact ordered 26-tool tuple.
- `repair_improvement` wraps existing `GameState.repair_improvement(unit_index: int) -> str`; do not add `GameState` or Lua behavior.
- Keep `LOCAL_TOOL_VERBS` exactly synchronized with non-empty `TOOL_REGISTRY[*].verb` values.
- Audit dispositions are exactly `add-to-standard-now`, `listed-for-later`, and `intentionally-excluded`.
- Do not modify v1-v5 experiment artifacts, analyzer logic or rubrics, or the explicit contents of `minimal`.
- Run targeted tests first, then `uv run --extra test pytest tests/arena -q`, then `git diff --check` before every commit.
- Running v6 is attended work for another session.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/arena/channel_protocol.py` | Remove only the discouraging `propose_deal` suffix. |
| `experiments/arena-channels-behavior-v6.yaml` | Preserve the v5 treatment with a new run id. |
| `tests/arena/test_channel_protocol.py` | Pin the exact retained schema description. |
| `tests/arena/test_experiment.py` | Prove raw-byte v5/v6 equality after the run-id substitution and load v6. |
| `src/civ_mcp/arena/registry.py` | Register `repair_improvement` and extend `standard`. |
| `src/civ_mcp/arena/vocab.py` | Mirror the new action verb for analyzer vocabulary parity. |
| `tests/arena/test_registry.py` | Pin exact tier tuples and test repair dispatch/validation. |
| `CLAUDE.md` | Complete the MCP unit-action reference table. |
| `docs/research/arena-tool-coverage-audit.md` | Record action coverage, tier membership, and dispositions. |
| `tools/skills/civ6-arena-live/SKILL.md` | Correct the tracked landing-code instructions. |
| `.claude/skills/civ6-arena-live/SKILL.md` | Ignored local mirror of the tracked skill source. |

---

### Task 1: V6 Schema Ablation

**Files:**
- Modify: `tests/arena/test_channel_protocol.py`
- Modify: `tests/arena/test_experiment.py`
- Modify: `src/civ_mcp/arena/channel_protocol.py`
- Create: `experiments/arena-channels-behavior-v6.yaml`

**Interfaces:**
- Consumes: `channel_tool_schemas()`, `load_experiment(path)`, `ARENA_CHANNELS_BEHAVIOR_V5`.
- Produces: an exact retained `propose_deal` description and a loadable v6 artifact whose sole file-level difference from v5 is `run_id`.

- [ ] **Step 1: Add the failing exact-description test**

Append to `tests/arena/test_channel_protocol.py`:

```python
def test_propose_deal_description_states_payer_without_discouraging_use():
    schemas = {schema["function"]["name"]: schema for schema in channel_tool_schemas()}

    assert schemas["propose_deal"]["function"]["description"] == (
        "Propose an unofficial favor-for-gold deal. YOU are the payer: you "
        "pay payment_gold and to_player performs the favor."
    )
```

- [ ] **Step 2: Add the failing raw-byte and loader test**

Add beside the existing v4/v5 constants in `tests/arena/test_experiment.py`:

```python
ARENA_CHANNELS_BEHAVIOR_V6 = (
    REPO_ROOT / "experiments" / "arena-channels-behavior-v6.yaml"
)
```

Add below `test_arena_channels_behavior_v5_differs_from_v4_only_in_run_id_and_auto_accept`:

```python
def test_arena_channels_behavior_v6_differs_from_v5_only_in_run_id():
    v5_bytes = ARENA_CHANNELS_BEHAVIOR_V5.read_bytes()
    old_run_id = b"run_id: arena-channels-behavior-v5\n"
    new_run_id = b"run_id: arena-channels-behavior-v6\n"

    assert v5_bytes.count(old_run_id) == 1
    assert ARENA_CHANNELS_BEHAVIOR_V6.read_bytes() == v5_bytes.replace(
        old_run_id,
        new_run_id,
        1,
    )
    v5 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V5)
    v6 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V6)

    assert v6.run_id == "arena-channels-behavior-v6"
    assert replace(v6, run_id=v5.run_id) == v5
```

- [ ] **Step 3: Verify both tests fail for the intended reasons**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_channel_protocol.py::test_propose_deal_description_states_payer_without_discouraging_use \
  tests/arena/test_experiment.py::test_arena_channels_behavior_v6_differs_from_v5_only_in_run_id \
  -v
```

Expected: the schema assertion shows the existing discouraging suffix, and the artifact test raises `FileNotFoundError` for v6.

- [ ] **Step 4: Remove only the discouraging schema suffix**

In `src/civ_mcp/arena/channel_protocol.py`, replace:

```python
            "Propose an unofficial favor-for-gold deal. YOU are the payer: you "
            "pay payment_gold and to_player performs the favor. If you want to "
            "be PAID for a favor you perform, do not use this — send a message "
            "asking the other player to propose the deal to you.",
```

with:

```python
            "Propose an unofficial favor-for-gold deal. YOU are the payer: you "
            "pay payment_gold and to_player performs the favor.",
```

- [ ] **Step 5: Clone v5 and change its one run-id line**

Run:

```bash
cp experiments/arena-channels-behavior-v5.yaml \
  experiments/arena-channels-behavior-v6.yaml
```

Then change line 1 of the new file from:

```yaml
run_id: arena-channels-behavior-v5
```

to:

```yaml
run_id: arena-channels-behavior-v6
```

- [ ] **Step 6: Run the focused tests**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_channel_protocol.py \
  tests/arena/test_experiment.py \
  -v
```

Expected: both files pass, including the new exact-description and raw-byte tests.

- [ ] **Step 7: Confirm the preregistered prediction remains explicit**

Run:

```bash
rg -n "If messaging and LLM-initiated deals return, cause 1; if the" \
  docs/research/arena-channels-behavior-v1-findings.md
```

Expected: one match in the v5 engagement-collapse section. Do not append a v6 result before the attended run.

- [ ] **Step 8: Run the required pre-commit gate and commit**

Run:

```bash
uv run --extra test pytest tests/arena -q
git diff --check
git add \
  src/civ_mcp/arena/channel_protocol.py \
  experiments/arena-channels-behavior-v6.yaml \
  tests/arena/test_channel_protocol.py \
  tests/arena/test_experiment.py
git commit -m "feat(arena): cut channels behavior v6 ablation"
```

Expected: the full arena suite passes, `git diff --check` prints nothing, and the commit succeeds.

---

### Task 2: Builder Coverage, Tier Pins, And Audit

**Files:**
- Modify: `tests/arena/test_registry.py`
- Modify: `src/civ_mcp/arena/registry.py`
- Modify: `src/civ_mcp/arena/vocab.py`
- Modify: `CLAUDE.md`
- Create: `docs/research/arena-tool-coverage-audit.md`

**Interfaces:**
- Consumes: `GameState.repair_improvement(unit_index: int) -> str`, `_tool`, `_int_param`, `TOOL_REGISTRY`, `TIERS`, and `LOCAL_TOOL_VERBS`.
- Produces: `TOOL_REGISTRY["repair_improvement"]`, a 26-tool `standard` tier, unchanged 15-tool `minimal`, complete MCP action documentation, and a post-change coverage audit.

- [ ] **Step 1: Strengthen the existing tier snapshots**

Replace the existing `MINIMAL_15` declaration and
`test_minimal_tier_is_todays_fifteen` in `tests/arena/test_registry.py`
with:

```python
MINIMAL_TIER_SNAPSHOT = (
    "get_overview",
    "get_units",
    "get_cities",
    "move_unit",
    "found_city",
    "set_city_production",
    "set_research",
    "fortify_unit",
    "skip_unit",
    "get_unit_promotions",
    "promote_unit",
    "get_pending_diplomacy",
    "respond_to_diplomacy",
    "get_pending_trades",
    "respond_to_trade",
)
MINIMAL_15 = set(MINIMAL_TIER_SNAPSHOT)

STANDARD_TIER_SNAPSHOT = (
    "get_overview",
    "get_units",
    "get_cities",
    "move_unit",
    "found_city",
    "set_city_production",
    "set_research",
    "fortify_unit",
    "skip_unit",
    "get_unit_promotions",
    "promote_unit",
    "get_map_area",
    "get_tech_civics",
    "attack_unit",
    "get_builder_tasks",
    "improve_tile",
    "remove_feature",
    "repair_improvement",
    "purchase_item",
    "heal_unit",
    "alert_unit",
    "set_civic",
    "get_pending_diplomacy",
    "respond_to_diplomacy",
    "get_pending_trades",
    "respond_to_trade",
)


def test_minimal_tier_is_frozen_for_historical_artifact_comparability():
    """Historical artifacts must retain the exact tool order they ran with."""
    assert TIERS["minimal"] == MINIMAL_TIER_SNAPSHOT
```

Replace `test_standard_adds_map_and_combat` with:

```python
def test_standard_tier_is_pinned_for_empire_behavior():
    assert TIERS["standard"] == STANDARD_TIER_SNAPSHOT
```

This preserves `MINIMAL_15` for existing set-based assertions while adding exact ordered snapshots.

- [ ] **Step 2: Add repair metadata, validation, and dispatch tests**

Add to `tests/arena/test_registry.py` near the other direct dispatch tests:

```python
def test_repair_improvement_tool_metadata():
    tool = TOOL_REGISTRY["repair_improvement"]

    assert tool.verb == "repair"
    assert tool.required == ("unit_index",)


@pytest.mark.asyncio
async def test_repair_improvement_rejects_non_numeric_unit_index():
    class FakeGS:
        async def repair_improvement(self, unit_index):
            raise AssertionError("must not reach GameState")

    with pytest.raises((TypeError, ValueError)):
        await dispatch(FakeGS(), "repair_improvement", {"unit_index": "z"})


@pytest.mark.asyncio
async def test_repair_improvement_dispatches_numeric_unit_index():
    class FakeGS:
        def __init__(self):
            self.calls = []

        async def repair_improvement(self, unit_index):
            self.calls.append(("repair_improvement", unit_index))
            return "OK"

    gs = FakeGS()

    assert await dispatch(
        gs,
        "repair_improvement",
        {"unit_index": "5"},
    ) == "OK"
    assert gs.calls == [("repair_improvement", 5)]
```

- [ ] **Step 3: Verify the new tests fail while the minimal pin passes**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_registry.py::test_minimal_tier_is_frozen_for_historical_artifact_comparability \
  tests/arena/test_registry.py::test_standard_tier_is_pinned_for_empire_behavior \
  tests/arena/test_registry.py::test_repair_improvement_tool_metadata \
  tests/arena/test_registry.py::test_repair_improvement_rejects_non_numeric_unit_index \
  tests/arena/test_registry.py::test_repair_improvement_dispatches_numeric_unit_index \
  -v
```

Expected: the minimal pin passes; the standard pin and three repair tests fail because the additions do not exist yet.

- [ ] **Step 4: Register repair and synchronize the vocabulary mirror**

Add below `remove_feature` in `TOOL_REGISTRY`:

```python
    "repair_improvement": _tool(
        "repair_improvement",
        "Repair the pillaged improvement on the current tile.",
        {"unit_index": _int_param("Builder unit index.")},
        ("unit_index",),
        lambda gs, args: gs.repair_improvement(int(args["unit_index"])),
        verb="repair",
    ),
```

Add below `"remove_feature": "remove_feature",` in
`src/civ_mcp/arena/vocab.py`:

```python
    "repair_improvement": "repair",
```

- [ ] **Step 5: Extend `standard` without changing `minimal`**

In `TIERS["standard"]`, insert `get_builder_tasks` before `improve_tile` and
`repair_improvement` after `remove_feature`:

```python
        "attack_unit",
        "get_builder_tasks",
        "improve_tile",
        "remove_feature",
        "repair_improvement",
        "purchase_item",
```

Do not edit the `minimal` tuple. `full` becomes 90 tools automatically because it is defined as `tuple(TOOL_REGISTRY)`.

- [ ] **Step 6: Complete the MCP unit-action reference**

In the `CLAUDE.md` unit-action table, make the builder-action portion read:

```markdown
| `improve` | Build improvement | Builders and Military Engineers; see improvements below |
| `repair` | Repair pillaged improvement | Builders only; no improvement name required |
| `remove_improvement` | Demolish intact improvement | Builders only; costs one charge |
| `remove_feature` | Chop/harvest feature | Builders only; removes forest, jungle, or marsh from tile |
| `build_route` | Build road/railroad | Military Engineers only; on current tile; no charges used |
| `trade_route` | Start route | Traders; target_x/y of destination city |
| `teleport` | Move idle trader | Traders only; target_x/y of city |
| `activate` | Use Great Person | Must be on completed matching district |
| `sacrifice_charges` | Boost district project | Royal Society builders; spends all charges |
| `spread_religion` | Spread religion | Missionaries/Apostles |
```

- [ ] **Step 7: Run the registry and vocabulary tests**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_registry.py \
  tests/arena/test_analyze.py::test_local_tool_verbs_mirror_registry_verbs_exactly \
  -v
```

Expected: all tests pass. In particular, the tier tuples match exactly, repair coerces `"5"` to `5`, and the registry/vocabulary maps remain equal.

- [ ] **Step 8: Generate deterministic audit evidence**

Run this read-only script from the repository root:

```bash
uv run python - <<'PY'
import ast
import re
from pathlib import Path

from civ_mcp.arena.registry import TIERS, TOOL_REGISTRY

server_text = Path("src/civ_mcp/server.py").read_text()
registry_text = Path("src/civ_mcp/arena/registry.py").read_text()
claude_text = Path("CLAUDE.md").read_text()

server_tree = ast.parse(server_text)


def is_mcp_tool(node):
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "mcp"
            and target.attr == "tool"
        ):
            return True
    return False


exposed_methods = set()
for node in server_tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    if not is_mcp_tool(node):
        continue
    exposed_methods.update(
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "gs"
    )

registry_methods = set(re.findall(r"gs\.([a-z_]+)\(", registry_text))
unit_action_node = next(
    node
    for node in server_tree.body
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "unit_action"
)
unit_action_doc = ast.get_docstring(unit_action_node)
assert unit_action_doc is not None
action_line = re.search(r"action: One of: ([^\n]+)", unit_action_doc)
assert action_line is not None
server_actions = {value.strip() for value in action_line.group(1).split(",")}

table = claude_text.split("## Unit Actions Reference", 1)[1].split(
    "Common improvements:",
    1,
)[0]
documented_actions = set(re.findall(r"^\| `([^`]+)` \|", table, re.MULTILINE))

aliases = {
    "activate_great_person": "activate",
    "start_trade_route": "trade_route",
    "teleport_trader": "teleport",
}
arena_actions = {
    aliases.get(tool.verb, tool.verb)
    for tool in TOOL_REGISTRY.values()
    if tool.verb
}

print(
    "counts:",
    f"registry={len(TOOL_REGISTRY)}",
    f"minimal={len(TIERS['minimal'])}",
    f"standard={len(TIERS['standard'])}",
    f"full={len(TIERS['full'])}",
)
print("MCP unit actions absent from CLAUDE.md:", sorted(server_actions - documented_actions))
print("MCP unit actions absent from arena:", sorted(server_actions - arena_actions))
print(
    "Exposed GameState calls absent from arena registry:",
    sorted(exposed_methods - registry_methods),
)
print()
print(
    "| tool | minimal | minimal disposition | standard | "
    "standard disposition | full |"
)
print("|---|---:|---|---:|---|---:|")
for name in TOOL_REGISTRY:
    in_minimal = name in TIERS["minimal"]
    in_standard = name in TIERS["standard"]
    minimal_disposition = (
        "present"
        if in_minimal
        else "intentionally-excluded"
    )
    standard_disposition = (
        "present"
        if in_standard
        else "listed-for-later"
    )
    print(
        f"| `{name}` | {'yes' if in_minimal else 'no'} | "
        f"{minimal_disposition} | "
        f"{'yes' if in_standard else 'no'} | "
        f"{standard_disposition} | yes |"
    )
PY
```

Expected evidence after the change:

```text
counts: registry=90 minimal=15 standard=26 full=90
MCP unit actions absent from CLAUDE.md: []
MCP unit actions absent from arena: ['build_route', 'delete', 'remove_improvement', 'sacrifice_charges', 'sleep']
Exposed GameState calls absent from arena registry: ['build_route', 'check_game_over', 'delete_unit', 'end_turn', 'execute_lua', 'get_diary_snapshot', 'get_game_identity', 'get_threat_scan', 'load_game_save', 'load_save', 'remove_improvement', 'sacrifice_builder_charges', 'sleep_unit', 'submit_congress']
```

The script also prints exactly 90 tier-membership rows. Preserve those rows in registry order in the audit document.

- [ ] **Step 9: Write the complete audit document**

Create `docs/research/arena-tool-coverage-audit.md` with these sections and
facts:

1. **Scope and method:** name the three compared surfaces, state the
   pre-change/post-change registry counts (89/90), and explain that the
   `unit_action` cases define the direct gameplay-action set.
2. **Fixed this cycle:** record `repair_improvement` and
   `get_builder_tasks` as `add-to-standard-now`. Separately record the
   three `CLAUDE.md` documentation-parity fixes.
3. **Unit-action matrix:** include all 20 MCP unit actions. Mark the five
   post-change arena gaps from Step 8 as `listed-for-later`; the other 15
   are present. Note the arena aliases `start_trade_route` → `trade_route`,
   `teleport_trader` → `teleport`, and `activate_great_person` →
   `activate`.
4. **Non-action exposed helpers:** classify `check_game_over`,
   `get_diary_snapshot`, `get_game_identity`, `get_threat_scan`, and
   `submit_congress` as composed/internal and `intentionally-excluded`.
   Classify `end_turn`, `execute_lua`, `load_game_save`, and `load_save` as
   lifecycle/ops and `intentionally-excluded`.
5. **Tier membership:** include all 90 generated rows. Explain that every
   absence from `minimal` is intentional because the historical tier is
   frozen; every absence from `standard` is `listed-for-later`; `full`
   contains the whole registry.
6. **Decision record:** explain why routine, non-destructive repair is in
   `standard`, while destructive `remove_improvement`/`delete`,
   specialized `sacrifice_charges`/`build_route`, and passive `sleep`
   remain deferred.

Do not use placeholder rows or ellipses. The five unit-action gaps and nine
composed/lifecycle exclusions above account for every post-change
`GameState` method reported by Step 8.

- [ ] **Step 10: Verify the audit and run the required pre-commit gate**

Run:

```bash
test "$(rg -c '^\| `[^`]+` \| (yes|no) \| (present|intentionally-excluded) \| (yes|no) \| (present|listed-for-later) \| yes \|' \
  docs/research/arena-tool-coverage-audit.md)" -eq 90
! rg -n 'TO''DO|TB''D|[.][.][.]' docs/research/arena-tool-coverage-audit.md
uv run --extra test pytest tests/arena -q
git diff --check
```

Expected: the tier table has exactly 90 rows, the placeholder scan is
silent, the full arena suite passes, and `git diff --check` is silent.

- [ ] **Step 11: Commit the tier and audit deliverable**

Run:

```bash
git add \
  src/civ_mcp/arena/registry.py \
  src/civ_mcp/arena/vocab.py \
  tests/arena/test_registry.py \
  CLAUDE.md \
  docs/research/arena-tool-coverage-audit.md
git commit -m "feat(arena): add builder repair coverage to standard tier"
```

Expected: the commit succeeds with the registry, tier pins, documentation parity, and audit together.

---

### Task 3: GitHub Landing Skill Documentation

**Files:**
- Modify: `tools/skills/civ6-arena-live/SKILL.md`
- Modify locally when present: `.claude/skills/civ6-arena-live/SKILL.md`

**Interfaces:**
- Consumes: the verified `origin` URL `git@github.com:wrislin1/civ6-mcp.git`.
- Produces: one tracked canonical skill with an identical ignored local mirror.

- [ ] **Step 1: Replace the stale tracked section**

In `tools/skills/civ6-arena-live/SKILL.md`, replace the entire
`## Landing code on \`.141\`` section, ending immediately before
`## Scripts`, with:

```markdown
## Landing code

`origin` is GitHub (`git@github.com:wrislin1/civ6-mcp.git`). Land work by
committing to `main` and running `git push origin main`. There is no `.141`
checkout in the loop anymore; if another machine such as riz-llm needs the
code, it pulls from GitHub.
```

- [ ] **Step 2: Refresh and verify the ignored local mirror**

If `.claude/skills/civ6-arena-live/SKILL.md` exists, replace the same
section there with the exact content from Step 1. Then run:

```bash
cmp tools/skills/civ6-arena-live/SKILL.md \
  .claude/skills/civ6-arena-live/SKILL.md
git check-ignore .claude/skills/civ6-arena-live/SKILL.md
git diff -- tools/skills/civ6-arena-live/SKILL.md
```

Expected: `cmp` is silent, `git check-ignore` prints the local mirror path,
and `git diff` shows only the tracked landing-section replacement.

- [ ] **Step 3: Verify stale claims are gone and the remote matches**

Run:

```bash
! rg -n 'denyCurrentBranch|ff-only|non-bare|no GitHub remote' \
  tools/skills/civ6-arena-live/SKILL.md
test "$(git remote get-url origin)" = "git@github.com:wrislin1/civ6-mcp.git"
```

Expected: both commands exit zero with no stale-reference output.

- [ ] **Step 4: Run the required pre-commit gate and commit**

Run:

```bash
uv run --extra test pytest tests/arena -q
git diff --check
git add tools/skills/civ6-arena-live/SKILL.md
git commit -m "docs(skill): update arena landing path for GitHub origin"
```

Expected: the full arena suite passes, the diff check is silent, and only the tracked canonical skill is committed.

---

## Final Verification

- [ ] **Step 1: Re-run the complete acceptance set**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_channel_protocol.py \
  tests/arena/test_experiment.py \
  tests/arena/test_registry.py \
  tests/arena/test_analyze.py::test_local_tool_verbs_mirror_registry_verbs_exactly \
  -v
uv run --extra test pytest tests/arena -q
git diff --check
```

Expected: all focused tests and the complete arena suite pass; the diff check is silent.

- [ ] **Step 2: Verify scope and publish**

Run:

```bash
git status --short --untracked-files=no
git log -3 --oneline
git push origin main
```

Expected: the tracked tree is clean, the three task commits are at the tip of `main`, and the push to GitHub succeeds.
