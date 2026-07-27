# Arena Post-V5 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the v6 ablation artifact (schema-clause revert), close the arena tool-coverage gap that left builders idle (audit + `standard` tier enrichment + repair verb), and fix the stale landing-code skill doc.

**Architecture:** Three independent deliverables against existing modules. The v6 change touches only the `propose_deal` schema description plus a byte-identical-minus-`run_id` experiment artifact so the description is the single run variable. The tier work freezes `minimal` as-is, adds a `repair_improvement` registry tool wrapping the existing `GameState.repair_improvement`, and enriches `standard` with exactly `get_builder_tasks` + `repair_improvement`. The audit doc records every remaining gap with a disposition instead of adding more tools.

**Tech Stack:** Python 3.12, pytest via `uv run --extra test pytest`, YAML experiment artifacts, markdown docs.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-26-arena-post-v5-fixes-design.md`.
- The `propose_deal` description loses exactly this text (including the leading space): ` If you want to be PAID for a favor you perform, do not use this — send a message asking the other player to propose the deal to you.` The retained description is exactly: `Propose an unofficial favor-for-gold deal. YOU are the payer: you pay payment_gold and to_player performs the favor.`
- `experiments/arena-channels-behavior-v6.yaml` is byte-identical to `experiments/arena-channels-behavior-v5.yaml` except `run_id: arena-channels-behavior-v6`.
- The `minimal` tier is frozen: `TIERS["minimal"]` in `src/civ_mcp/arena/registry.py` must not change.
- `standard` gains exactly two tools: `get_builder_tasks` and `repair_improvement`. No other tier additions in this cycle.
- The new `repair_improvement` registry tool wraps the existing `GameState.repair_improvement(unit_index)` — no new `GameState` or Lua code.
- Audit dispositions use exactly three labels: `add-to-standard-now`, `listed-for-later`, `intentionally-excluded`.
- Run targeted tests with `uv run --extra test pytest <paths> -v`; run `git diff --check` before each commit; commit after each task.
- Do not change the analyzer, `full` tier contents (beyond the automatic registry pickup), or any v1–v5 experiment artifact.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/arena/channel_protocol.py` | Drop the discouraging clause from the `propose_deal` schema description. |
| `experiments/arena-channels-behavior-v6.yaml` | v6 ablation artifact (v5 + run_id only). |
| `src/civ_mcp/arena/registry.py` | Add `repair_improvement` tool; extend `standard` tier tuple. |
| `docs/research/arena-tool-coverage-audit.md` | Full three-surface coverage audit with dispositions. |
| `.claude/skills/civ6-arena-live/SKILL.md` | Replace the stale "Landing code on `.141`" section. |
| `tests/arena/test_channel_protocol.py` | Pin the revised `propose_deal` description. |
| `tests/arena/test_experiment.py` | v6 loader test (equality-minus-run_id vs v5). |
| `tests/arena/test_registry.py` | Pin `standard` additions; freeze-comment on minimal; dispatch + invalid-arg tests for `repair_improvement`. |

---

### Task 1: V6 Schema Revert And Ablation Artifact

**Files:**
- Modify: `tests/arena/test_channel_protocol.py`
- Modify: `tests/arena/test_experiment.py`
- Modify: `src/civ_mcp/arena/channel_protocol.py`
- Create: `experiments/arena-channels-behavior-v6.yaml`

**Interfaces:**
- Consumes: `channel_tool_schemas()` (already imported in `tests/arena/test_channel_protocol.py:15`); `load_experiment(path)` and the `ARENA_CHANNELS_BEHAVIOR_V5` constant plus `replace` from `dataclasses` (already imported) in `tests/arena/test_experiment.py`.
- Produces: `experiments/arena-channels-behavior-v6.yaml` with `run_id: arena-channels-behavior-v6`; a `propose_deal` schema description without the discouraging clause.

- [ ] **Step 1: Write the failing schema-description test**

In `tests/arena/test_channel_protocol.py`, add at the end of the file:

```python
def test_propose_deal_description_states_payer_without_discouraging_use():
    # v5 ablation context: the clause "do not use this" on the only
    # deal-initiating action coincided with LLM channel initiative dropping
    # to zero (v3: 1 LLM-initiated deal, v4: 1, v5: 0). v6 keeps the
    # role-direction sentence (it fixed v4's inverted deal object) and drops
    # only the discouraging clause.
    schemas = {s["function"]["name"]: s for s in channel_tool_schemas()}
    description = schemas["propose_deal"]["function"]["description"]
    assert description == (
        "Propose an unofficial favor-for-gold deal. YOU are the payer: you "
        "pay payment_gold and to_player performs the favor."
    )
```

- [ ] **Step 2: Write the failing v6 loader test**

In `tests/arena/test_experiment.py`, add below the v5 constant (line ~31):

```python
ARENA_CHANNELS_BEHAVIOR_V6 = REPO_ROOT / "experiments" / "arena-channels-behavior-v6.yaml"
```

Add this test directly below `test_arena_channels_behavior_v5_differs_from_v4_only_in_run_id_and_auto_accept` (line ~349):

```python
def test_arena_channels_behavior_v6_differs_from_v5_only_in_run_id():
    # v6 isolates the propose_deal description revert: the config must be
    # equivalent to v5 apart from run_id, so the schema text is the single
    # changed variable between the two runs.
    v5 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V5)
    v6 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V6)

    assert v6.run_id == "arena-channels-behavior-v6"
    assert replace(v6, run_id=v5.run_id) == v5
```

- [ ] **Step 3: Run the new tests and verify the expected failures**

Run:

```bash
uv run --extra test pytest \
  tests/arena/test_channel_protocol.py::test_propose_deal_description_states_payer_without_discouraging_use \
  tests/arena/test_experiment.py::test_arena_channels_behavior_v6_differs_from_v5_only_in_run_id \
  -v
```

Expected: the description test fails on the "do not use this" clause still being present; the loader test fails because the v6 yaml does not exist.

- [ ] **Step 4: Revert the discouraging clause**

In `src/civ_mcp/arena/channel_protocol.py` (lines 444–447), replace:

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

- [ ] **Step 5: Create the v6 artifact**

Run:

```bash
sed 's/^run_id: arena-channels-behavior-v5$/run_id: arena-channels-behavior-v6/' \
  experiments/arena-channels-behavior-v5.yaml > experiments/arena-channels-behavior-v6.yaml
diff experiments/arena-channels-behavior-v5.yaml experiments/arena-channels-behavior-v6.yaml
```

Expected diff output — exactly one changed line:

```
1c1
< run_id: arena-channels-behavior-v5
---
> run_id: arena-channels-behavior-v6
```

- [ ] **Step 6: Run the tests and verify they pass**

Run:

```bash
uv run --extra test pytest tests/arena/test_channel_protocol.py tests/arena/test_experiment.py -v
```

Expected: all tests pass (the two new ones plus no regressions in either file).

- [ ] **Step 7: Check the diff and commit**

```bash
git diff --check
git add tests/arena/test_channel_protocol.py tests/arena/test_experiment.py \
  src/civ_mcp/arena/channel_protocol.py experiments/arena-channels-behavior-v6.yaml
git commit -m "feat(arena): cut channels behavior v6 ablation artifact"
```

Expected: `git diff --check` prints nothing; commit succeeds.

---

### Task 2: Repair Tool And Standard Tier Enrichment

**Files:**
- Modify: `tests/arena/test_registry.py`
- Modify: `src/civ_mcp/arena/registry.py`

**Interfaces:**
- Consumes: `GameState.repair_improvement(unit_index: int) -> str` (`src/civ_mcp/game_state.py:597`, existing); `_tool` and `_int_param` helpers in `registry.py`; `TIERS` dict (`registry.py:1347`).
- Produces: `TOOL_REGISTRY["repair_improvement"]`; `TIERS["standard"]` containing `get_builder_tasks` and `repair_improvement`. `full` picks up the new tool automatically (`TIERS["full"] = tuple(TOOL_REGISTRY)`).

- [ ] **Step 1: Write the failing tier and dispatch tests**

In `tests/arena/test_registry.py`, add below `test_standard_adds_map_and_combat` (line ~90):

```python
def test_standard_adds_builder_management():
    """Post-v5 fix (riz 2026-07-26): across v3-v5 the LLM seats' builders
    wandered without ever improving or repairing a tile. Root cause was the
    tool surface, not the models: minimal offers no improvement verbs at
    all, standard had improve_tile but no way to see what needs improving
    (get_builder_tasks) and no repair verb existed anywhere in the
    registry. minimal stays frozen for artifact comparability; standard is
    the empire-behavior tier."""
    extra = set(TIERS["standard"]) - set(TIERS["minimal"])
    assert {"get_builder_tasks", "repair_improvement"} <= extra


def test_minimal_tier_is_frozen_for_artifact_comparability():
    """Re-running a historical experiment artifact must offer the same
    world it originally ran with. Extend standard (or add a tier) instead
    of touching minimal."""
    assert set(TIERS["minimal"]) == MINIMAL_15
```

In the dispatch-test section (the fake `GameState` class at line ~1443 that
defines `async def improve_tile`), add a sibling method to the same fake:

```python
        async def repair_improvement(self, unit_index):
            self.calls.append(("repair_improvement", unit_index)); return "OK"
```

and, mirroring the existing `improve_tile` dispatch assertion at line ~1465,
add:

```python
    await _dispatch(gs, "repair_improvement", {"unit_index": "5"})
    assert ("repair_improvement", 5) in gs.calls
```

(Adapt the exact call pattern to the enclosing test's local helper names —
the `improve_tile` assertion three lines above is the template. If dispatch
goes through a differently-named local helper, use that helper.)

In the invalid-argument parametrize list at line ~1393 (the entries shaped
`("improve_tile", {"unit_index": "z", "improvement_name": "IMPROVEMENT_FARM"})`),
add:

```python
    ("repair_improvement", {"unit_index": "z"}),
```

- [ ] **Step 2: Run the new tests and verify the expected failures**

Run:

```bash
uv run --extra test pytest tests/arena/test_registry.py -v 2>&1 | tail -20
```

Expected: `test_standard_adds_builder_management` fails (tools absent);
the dispatch/invalid tests fail with `repair_improvement` unknown;
`test_minimal_tier_is_frozen_for_artifact_comparability` passes immediately
(minimal already matches `MINIMAL_15`).

- [ ] **Step 3: Add the registry tool**

In `src/civ_mcp/arena/registry.py`, directly below the `remove_feature`
entry (which starts at line ~614), add:

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

Match the surrounding entries' exact `_tool` argument order — `improve_tile`
at line ~603 is the template. If `_tool` in this file takes `verb` as a
keyword with a different name, mirror `improve_tile`'s usage exactly.

- [ ] **Step 4: Extend the standard tier**

In `TIERS["standard"]` (line ~1373), add two entries after
`"remove_feature",`:

```python
        "get_builder_tasks",
        "repair_improvement",
```

Do not touch `TIERS["minimal"]`.

- [ ] **Step 5: Run the registry tests and verify they pass**

Run:

```bash
uv run --extra test pytest tests/arena/test_registry.py -v 2>&1 | tail -10
```

Expected: all tests pass, including the pre-existing
`test_tiers_nest` (minimal ⊂ standard ⊂ full) and
`test_full_tier_initially_matches_registry_order` (full picks up the new
tool automatically).

- [ ] **Step 6: Check the diff and commit**

```bash
git diff --check
git add tests/arena/test_registry.py src/civ_mcp/arena/registry.py
git commit -m "feat(arena): add repair verb and builder tools to standard tier"
```

Expected: `git diff --check` prints nothing; commit succeeds.

---

### Task 3: Tool-Coverage Audit Doc

**Files:**
- Create: `docs/research/arena-tool-coverage-audit.md`

**Interfaces:**
- Consumes: `TOOL_REGISTRY` / `TIERS` from `civ_mcp.arena.registry`; `GameState` from `civ_mcp.game_state`; the unit-action table in `CLAUDE.md`.
- Produces: the audit document. No code changes — Task 2 already made the only tool additions this cycle.

- [ ] **Step 1: Generate the three-surface comparison**

Run and capture the output:

```bash
cd /home/riz/projects/civ6-mcp
uv run python - <<'PY'
import inspect
from civ_mcp.arena.registry import TOOL_REGISTRY, TIERS
from civ_mcp.game_state import GameState

methods = {
    n for n, m in inspect.getmembers(GameState, predicate=inspect.isfunction)
    if not n.startswith("_")
}
import re
covered = set(re.findall(r"gs\.([a-z_]+)\(", open("src/civ_mcp/arena/registry.py").read()))

print("== GameState methods with NO registry tool calling them ==")
for name in sorted(methods - covered):
    print(" ", name)
print()
print("== registry tools absent from standard ==")
for name in sorted(set(TOOL_REGISTRY) - set(TIERS["standard"])):
    print(" ", name)
PY
```

- [ ] **Step 2: Write the audit document**

Create `docs/research/arena-tool-coverage-audit.md` with this structure,
filling the tables from Step 1's output. Every row gets exactly one
disposition: `add-to-standard-now` / `listed-for-later` /
`intentionally-excluded`. The disposition rule (from the spec): read-only
tools and builder-class action verbs that need no new `GameState`/Lua code
may be `add-to-standard-now`; this cycle's additions are already fixed as
`get_builder_tasks` + `repair_improvement` (Task 2), so every other gap is
`listed-for-later` or `intentionally-excluded`.

```markdown
# Arena Tool-Coverage Audit

**Date:** 2026-07-26 · **Trigger:** operator observation during the
channels-behavior v3–v5 runs — LLM builders wandered without improving,
building, or repairing a tile. Root cause: the `minimal` tier offered no
improvement verbs, and no repair verb existed in the registry at all.
Zero `improve_tile` calls and zero out-of-tier rejections across three
runs: a tool absent from the schema does not exist in the model's world.

## Method

Three surfaces compared:
1. `TOOL_REGISTRY` (`src/civ_mcp/arena/registry.py`) — N tools.
2. `GameState` public methods (`src/civ_mcp/game_state.py`) — the actions
   the MCP server can execute against the live game.
3. The unit-action table in `CLAUDE.md`.

Dispositions: `add-to-standard-now` (read-only or builder-class, no new
game-side code) / `listed-for-later` / `intentionally-excluded`
(ops/save/load/debug surfaces the arena must not expose).

## Fixed this cycle

| gap | disposition | change |
|---|---|---|
| no repair verb in registry | add-to-standard-now | `repair_improvement` tool added, wraps existing `GameState.repair_improvement` |
| `get_builder_tasks` absent from standard | add-to-standard-now | added to `TIERS["standard"]` |

## GameState actions with no registry tool

| GameState method | game action | disposition | notes |
|---|---|---|---|
| `sleep_unit` | unit sleep (manual wake) | listed-for-later | ... |
| `delete_unit` | disband unit | listed-for-later | risky verb for LLM seats; maintenance relief niche |
| `build_route` | Military Engineer railroad | listed-for-later | era-gated, needs Encampment+Armory |
| `remove_improvement` | ... | listed-for-later | ... |
| (every remaining row from Step 1 output) | ... | ... | ... |

## Registry tools absent from `standard`

| tool | disposition | notes |
|---|---|---|
| (every row from Step 1 output, post-Task-2) | listed-for-later / intentionally-excluded | one line each on why it stays out of standard for now |

## Tier philosophy (recorded for future cycles)

- `minimal` is frozen (pinned by
  `test_minimal_tier_is_frozen_for_artifact_comparability`): re-running a
  historical artifact must offer the same world it originally ran with.
- `standard` is the empire-behavior tier: it should contain everything a
  seat needs to run an empire without strategic-layer tools.
- `full` is the whole registry by construction.
```

Replace every `...` and the placeholder rows with real content from the
Step 1 output — the committed document must contain no ellipses. Keep
per-row notes to one line.

- [ ] **Step 3: Verify the document is complete**

Run:

```bash
grep -n '\.\.\.' docs/research/arena-tool-coverage-audit.md && echo "PLACEHOLDERS REMAIN" || echo "clean"
grep -c 'add-to-standard-now\|listed-for-later\|intentionally-excluded' docs/research/arena-tool-coverage-audit.md
```

Expected: `clean`, and the disposition count is at least the number of gap
rows (every row dispositioned).

- [ ] **Step 4: Commit**

```bash
git add docs/research/arena-tool-coverage-audit.md
git commit -m "docs(arena): audit tool coverage across registry, tiers, and game actions"
```

Expected: commit succeeds.

---

### Task 4: Skill-Doc Landing Section Fix

**Files:**
- Modify: `.claude/skills/civ6-arena-live/SKILL.md`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: an accurate landing-code section; no code or test surface.

- [ ] **Step 1: Replace the stale section**

In `.claude/skills/civ6-arena-live/SKILL.md`, replace the entire
`## Landing code on \`.141\`` section (heading plus its numbered list and
trailing paragraph, ending just before `## Scripts`) with:

```markdown
## Landing code

`origin` is GitHub (`git@github.com:wrislin1/civ6-mcp.git`). Land work by
committing to `main` and running `git push origin main`. There is no `.141`
checkout in the loop anymore; if another machine (e.g. riz-llm) needs the
code, it pulls from GitHub.
```

- [ ] **Step 2: Verify no stale references remain**

Run:

```bash
grep -n 'denyCurrentBranch\|ff-only\|non-bare' .claude/skills/civ6-arena-live/SKILL.md || echo "clean"
grep -n 'no GitHub remote' .claude/skills/civ6-arena-live/SKILL.md || echo "clean"
```

Expected: both print `clean`. (The `Environment` section's host lines and
the FireTuner/watcher sections are accurate and must remain untouched.)

- [ ] **Step 3: Commit**

```bash
git diff --check
git add .claude/skills/civ6-arena-live/SKILL.md
git commit -m "docs(skill): landing path is GitHub origin, not the .141 checkout"
```

Expected: `git diff --check` prints nothing; commit succeeds.

---

## Final Verification

- [ ] **Step 1: Run the targeted suites**

```bash
uv run --extra test pytest \
  tests/arena/test_channel_protocol.py \
  tests/arena/test_experiment.py \
  tests/arena/test_registry.py \
  -v 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 2: Run the full arena suite**

```bash
uv run --extra test pytest tests/arena -q
```

Expected: all tests pass (~1790+, roughly 2–3 minutes).

- [ ] **Step 3: Inspect git state and push**

```bash
git status --short --untracked-files=no
git push origin main
```

Expected: tracked tree clean; push succeeds.
