# Arena Multi-Turn Intent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Persist and deterministically execute Great Person activation intent across turns, then add a v7 artifact that isolates task-tracker follow-through from tool-tier changes.

**Architecture:** The existing `UnitTask` schema remains unchanged; a third kind reuses unit resolution, movement, hostile blocking, retry accounting, and terminal statuses. Only the at-target action is new. V7 keeps the v6 `minimal` seats and enables the harness-level tracker for the two LLM players, so the loaded-config delta is exactly attributable.

**Tech Stack:** Python 3.12, dataclasses, regex task grammar, YAML experiment artifacts, pytest via `uv run --extra test pytest`.

## Global Constraints

- New task kind is exactly `great_person_activate`.
- Task syntax is exactly `TASK great_person_activate unit_id=<id> target=<x>,<y>`.
- A GP activation task carrying `improvement=` is invalid and ignored.
- Prompt examples keep nonnumeric `<unit_id>`, `<x>`, and `<y>` placeholders.
- `GameState._action_result` supplies normalized success text; the tracker matches `GP_ACTIVATED|`, never `OK:GP_ACTIVATED|`.
- Success tuple is exactly `GP_ACTIVATE_SUCCESS_PREFIXES = ("GP_ACTIVATED|",)`.
- Retry limit remains `MAX_TASK_FAILURES = 3`.
- Existing status and blocked-reason vocabularies remain unchanged.
- Missing units become terminal `lost` with `unit_missing`.
- V7 is copied from v6; players 1 and 2 remain `tools: minimal`.
- V7 enables task tracking only for players 1 and 2; the scripted player remains tracker-disabled.
- Running v7 and creating a standard-tier integrated artifact are out of scope.
- Run the full arena suite before each task commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/arena/task_tracker.py` | Parse, execute, retry, and resolve `great_person_activate`. |
| `src/civ_mcp/arena/prompting.py` | Advertise the third task kind with inert placeholders. |
| `tests/arena/test_task_tracker.py` | Pin grammar, movement, activation success/failure, and lost-unit behavior. |
| `tests/arena/test_prompting.py` | Pin all three prompt examples and the placeholder safety property. |
| `experiments/arena-channels-behavior-v7.yaml` | Tracker-only ablation cloned from v6. |
| `tests/arena/test_experiment.py` | Enforce parsed equality with v6 after normalizing only the approved deltas. |

### Task 1: Great Person activation task kind and prompt contract

**Files:**
- Modify: `src/civ_mcp/arena/task_tracker.py`
- Modify: `src/civ_mcp/arena/prompting.py`
- Modify: `tests/arena/test_task_tracker.py`
- Modify: `tests/arena/test_prompting.py`

**Interfaces:**
- Produces: `TASK_KINDS` containing `great_person_activate`.
- Produces: `TASK_LINE_RE` recognition for the new syntax.
- Produces: GP activation result constants and at-target execution.
- Produces: a third inert example in `STANDING_PLAN_INSTRUCTION`.
- Consumed by Task 2: enabling the tracker in v7 exposes this prompt and execution path.

- [x] **Step 1: Write failing grammar tests**

Append to `tests/arena/test_task_tracker.py`:

```python
def test_parse_valid_great_person_activate_line():
    tasks = parse_task_lines(
        "TASK great_person_activate unit_id=65541 target=12,19",
        turn=7,
    )

    assert tasks == [
        UnitTask(
            task_id="great_person_activate:65541",
            kind="great_person_activate",
            unit_id=65541,
            target_x=12,
            target_y=19,
            created_turn=7,
            updated_turn=7,
            improvement="",
            status="active",
            last_result="",
        )
    ]


def test_great_person_activate_rejects_builder_improvement_argument():
    assert parse_task_lines(
        "TASK great_person_activate unit_id=65541 target=12,19 "
        "improvement=IMPROVEMENT_FARM",
        turn=7,
    ) == []


def test_great_person_placeholder_example_does_not_parse():
    assert parse_task_lines(
        "TASK great_person_activate unit_id=<unit_id> target=<x>,<y>",
        turn=7,
    ) == []
```

- [x] **Step 2: Extend the tracker fake and write failing execution tests**

Add `activate_result="GP_ACTIVATED|Bhasa at 12,19"` to `FakeGS.__init__`,
store `self.activate_result`, initialize `self.activate_calls = []`, and add:

```python
    async def activate_great_person(self, unit_index):
        self.activate_calls.append(unit_index)
        return self.activate_result
```

Append:

```python
@pytest.mark.asyncio
async def test_great_person_activate_moves_when_away_from_target():
    unit = _unit(65541, 5, 10, 18)
    task = _task(
        task_id="great_person_activate:65541",
        kind="great_person_activate",
        unit_id=65541,
        target_x=12,
        target_y=19,
    )
    gs = FakeGS([unit])

    updated, results = await run_pre_model_tasks(gs, [task], turn=2)

    assert updated[0].status == "active"
    assert gs.move_unit_calls == [(5, 12, 19)]
    assert gs.activate_calls == []
    assert results[0]["action"] == "move"


@pytest.mark.asyncio
async def test_great_person_activate_completes_on_normalized_success():
    unit = _unit(65541, 5, 12, 19)
    task = _task(
        task_id="great_person_activate:65541",
        kind="great_person_activate",
        unit_id=65541,
        target_x=12,
        target_y=19,
    )
    gs = FakeGS([unit], activate_result="GP_ACTIVATED|Bhasa at 12,19")

    updated, results = await run_pre_model_tasks(gs, [task], turn=2)

    assert updated[0].status == "complete"
    assert updated[0].last_result == "GP_ACTIVATED|Bhasa at 12,19"
    assert gs.activate_calls == [5]
    assert results[0]["action"] == "activate_great_person"
    assert results[0]["status"] == "complete"
```

Add a negative regression:

```python
@pytest.mark.asyncio
async def test_raw_ok_gp_prefix_is_not_tracker_success():
    unit = _unit(65541, 5, 12, 19)
    task = _task(
        task_id="great_person_activate:65541",
        kind="great_person_activate",
        unit_id=65541,
        target_x=12,
        target_y=19,
    )
    gs = FakeGS([unit], activate_result="OK:GP_ACTIVATED|raw-lua-shape")

    updated, _ = await run_pre_model_tasks(gs, [task], turn=2)

    assert updated[0].status == "active"
    assert updated[0].failure_count == 1
```

- [x] **Step 3: Write failing retry and missing-unit tests**

Parameterize activation failures over:

```python
[
    ("Error: cannot activate here", "gp_activate_error_retry_limit"),
    (ACTION_NO_RESPONSE, "gp_activate_no_response_retry_limit"),
    ("unexpected tuner line", "unrecognized_result_retry_limit"),
]
```

For each, run the same active task three times, carrying the returned task into
the next call. Assert attempts 1-2 remain active with failure counts 1-2;
attempt 3 becomes failed with the expected final diagnostic.

Add:

```python
@pytest.mark.asyncio
async def test_missing_great_person_is_lost():
    task = _task(
        task_id="great_person_activate:65541",
        kind="great_person_activate",
        unit_id=65541,
        target_x=12,
        target_y=19,
    )

    updated, results = await run_pre_model_tasks(FakeGS([]), [task], turn=2)

    assert updated[0].status == "lost"
    assert updated[0].last_result == "unit_missing"
    assert results[0]["result"] == "unit_missing"
```

- [x] **Step 4: Write the failing prompt test**

In `tests/arena/test_prompting.py`, strengthen the placeholder test:

```python
def test_every_standing_plan_task_example_is_inert():
    examples = [
        line.strip()
        for line in STANDING_PLAN_INSTRUCTION.splitlines()
        if line.strip().startswith("TASK ")
    ]

    assert [line.split()[1] for line in examples] == [
        "settle",
        "builder_improve",
        "great_person_activate",
    ]
    assert all(parse_task_lines(line, turn=1) == [] for line in examples)
```

- [x] **Step 5: Run focused tests and verify failures**

Run:

```bash
uv run --extra test pytest tests/arena/test_task_tracker.py tests/arena/test_prompting.py -q
```

Expected: the new kind does not parse or execute and the prompt lists only two
kinds.

- [x] **Step 6: Extend constants, grammar, and parser validation**

In `task_tracker.py`, change:

```python
TASK_KINDS = {"settle", "builder_improve", "great_person_activate"}
```

Add:

```python
GP_ACTIVATE_NO_RESPONSE = "gp_activate_no_response"
GP_ACTIVATE_NO_RESPONSE_RETRY_LIMIT = (
    "gp_activate_no_response_retry_limit"
)
GP_ACTIVATE_ERROR_RETRY_LIMIT = "gp_activate_error_retry_limit"
GP_ACTIVATE_SUCCESS_PREFIXES = ("GP_ACTIVATED|",)
```

Extend the regex kind alternation:

```python
r"^\s*(?:[-*•]+\s+)?TASK\s+"
r"(?P<kind>settle|builder_improve|great_person_activate)\s+"
```

After improvement normalization in `parse_task_lines`, enforce:

```python
            if kind == "builder_improve" and not improvement:
                continue
            if kind == "great_person_activate" and improvement:
                continue
```

Update the parser and `run_pre_model_tasks` docstrings to name all three
low-risk kinds.

- [x] **Step 7: Add the explicit execution branch**

In `_run_single_task`, insert this branch after `settle` and before the
builder fallback:

```python
    if task.kind == "great_person_activate":
        if at_target:
            result_str = await gs.activate_great_person(unit.unit_index)
            return _resolve_at_target_action(
                task,
                result_str,
                action="activate_great_person",
                success_prefixes=GP_ACTIVATE_SUCCESS_PREFIXES,
                no_response_result=GP_ACTIVATE_NO_RESPONSE,
                no_response_limit=GP_ACTIVATE_NO_RESPONSE_RETRY_LIMIT,
                error_limit=GP_ACTIVATE_ERROR_RETRY_LIMIT,
                turn=turn,
            )
        return await _advance_toward_target(
            gs,
            task,
            unit,
            owner_context,
            turn,
        )

    # task.kind == "builder_improve"
```

Do not add unit-type probing. As with existing settle/builder tasks, a
mismatched unit receives the GameState error and uses the normal retry budget.

- [x] **Step 8: Add the inert prompt example**

Append inside `STANDING_PLAN_INSTRUCTION`:

```text
  TASK great_person_activate unit_id=<unit_id> target=<x>,<y>
```

Keep the literal placeholders and existing comment unchanged.

- [x] **Step 9: Run focused and full tests**

Run:

```bash
uv run --extra test pytest tests/arena/test_task_tracker.py tests/arena/test_prompting.py -q
uv run --extra test pytest tests/arena -q
```

Expected: all tests pass.

- [x] **Step 10: Commit Task 1**

```bash
git add src/civ_mcp/arena/task_tracker.py src/civ_mcp/arena/prompting.py tests/arena/test_task_tracker.py tests/arena/test_prompting.py
git commit -m "feat(arena): track great-person activation intent"
```

### Task 2: Tracker-only channels behavior v7 artifact

**Files:**
- Create: `experiments/arena-channels-behavior-v7.yaml`
- Modify: `tests/arena/test_experiment.py`

**Interfaces:**
- Consumes: v6 as the experiment parent.
- Produces: run ID `arena-channels-behavior-v7`.
- Produces: tracker-enabled players 1 and 2 with unchanged minimal tools.

- [x] **Step 1: Write the failing loader equality test**

Add:

```python
ARENA_CHANNELS_BEHAVIOR_V7 = (
    REPO_ROOT / "experiments" / "arena-channels-behavior-v7.yaml"
)
```

Then:

```python
def test_arena_channels_behavior_v7_is_tracker_only_delta_from_v6():
    v6 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V6)
    v7 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V7)
    by_player = {player.player_id: player for player in v7.players}

    assert v7.run_id == "arena-channels-behavior-v7"
    assert by_player[1].options.tools == "minimal"
    assert by_player[2].options.tools == "minimal"
    assert by_player[1].options.task_tracker.enabled is True
    assert by_player[2].options.task_tracker.enabled is True
    assert by_player[3].options.task_tracker.enabled is False

    normalized_players = [
        replace(
            player,
            options=replace(
                player.options,
                task_tracker=replace(
                    player.options.task_tracker,
                    enabled=False,
                ),
            ),
        )
        if player.player_id in {1, 2}
        else player
        for player in v7.players
    ]
    assert replace(
        v7,
        run_id=v6.run_id,
        players=normalized_players,
    ) == v6
```

- [x] **Step 2: Run the focused test and verify the missing artifact failure**

Run:

```bash
uv run --extra test pytest tests/arena/test_experiment.py::test_arena_channels_behavior_v7_is_tracker_only_delta_from_v6 -q
```

Expected: fail because the v7 YAML file does not exist.

- [x] **Step 3: Create v7 from v6 with only the approved YAML edits**

Copy `experiments/arena-channels-behavior-v6.yaml` to
`experiments/arena-channels-behavior-v7.yaml`.

Change:

```yaml
run_id: arena-channels-behavior-v7
```

Under player 1, after `max_steps: 15`, add:

```yaml
    task_tracker: {enabled: true}
```

Add the identical line under player 2. Do not add it to player 3. Do not
change tools, model, gateway, channel settings, scripts, turn limits, or deal
text.

- [x] **Step 4: Run the focused test**

Run:

```bash
uv run --extra test pytest tests/arena/test_experiment.py::test_arena_channels_behavior_v7_is_tracker_only_delta_from_v6 -q
```

Expected: pass.

- [x] **Step 5: Inspect the raw YAML diff**

Run:

```bash
git diff --no-index experiments/arena-channels-behavior-v6.yaml experiments/arena-channels-behavior-v7.yaml
```

Expected: one run ID replacement and exactly two identical
`task_tracker: {enabled: true}` additions.

- [x] **Step 6: Run the full arena suite**

Run:

```bash
uv run --extra test pytest tests/arena -q
```

Expected: all tests pass.

- [x] **Step 7: Commit Task 2**

```bash
git add experiments/arena-channels-behavior-v7.yaml tests/arena/test_experiment.py
git commit -m "test(arena): add channels behavior v7 tracker ablation"
```

## Final Verification

- [x] Run:

```bash
uv run --extra test pytest tests/arena/test_task_tracker.py tests/arena/test_prompting.py tests/arena/test_experiment.py -q
uv run --extra test pytest tests/arena -q
git diff --check
git status --short
```

- [x] Confirm `GP_ACTIVATED|` is the only GP success prefix, v7's two LLM
seats remain minimal, the scripted seat remains tracker-disabled, no
analyzer/retry-limit changes were made, and the tracked worktree is clean.
