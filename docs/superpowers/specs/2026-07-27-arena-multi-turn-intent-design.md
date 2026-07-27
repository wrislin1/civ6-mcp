# Arena multi-turn intent — Design

**Date:** 2026-07-27 · **Status:** approved (design presented and accepted in
session) · **Order:** third of three (after
`2026-07-27-arena-unit-action-visibility-design.md`)

## Context

Reachability and visibility get a unit to act *this* turn. They do not carry
intent across turns: a Great Person four tiles from its district, or a builder
walking to a task, must be re-decided by the model every turn, and any turn
where the model's attention goes elsewhere resets the march.

`src/civ_mcp/arena/task_tracker.py` already solves this. A model ends its turn
with a `TASK` line; the tracker persists it per run and per player and
executes it deterministically at the start of the next turn, before the model
is invoked — moving the unit toward the target and performing the at-target
action on arrival. It never calls the model and deliberately refuses risky
actions (attack, purchase, diplomacy). It tracks retry limits, blocked
reasons (`blocked_visible_hostile`, `blocked_improvement_not_valid`), and
per-task results that `analyze.py` already reports.

Two gaps:

1. `TASK_KINDS` is `{"settle", "builder_improve"}`. Great-person activation —
   low-risk, deterministic, and exactly the "walk there, then act" shape — has
   no kind, so the Great Writer case cannot be handed off even once the tool
   is reachable and visible.
2. Every channels experiment ran with `task_tracker: {enabled: false}`, so
   nothing in the v1–v6 series exercised the mechanism at all.

This is a different mechanism from the visibility work — the harness acting
deterministically on the model's behalf, rather than the model seeing more —
and it carries its own experimental question: does deterministic
follow-through change measured behavior, or do the seats simply issue fewer
orders? That is why it is a separate spec and a separate live comparison.

## Deliverables

### 1. A third task kind

`TASK_KINDS` gains `great_person_activate`. `TASK_LINE_RE`
(`task_tracker.py:79`) extends its kind alternation; the grammar is otherwise
unchanged. A `great_person_activate` line carrying `improvement=` is invalid
and ignored rather than silently storing an irrelevant builder field:

```
TASK great_person_activate unit_id=65541 target=12,19
```

Execution reuses the existing move-then-act loop. The at-target action calls
`GameState.activate_great_person(unit_index)`, whose success shape is
`GP_ACTIVATED|…` after `GameState._action_result` strips the Lua `OK:` prefix
(`lua/great_people.py:258` and `:330`) — represented by
`GP_ACTIVATE_SUCCESS_PREFIXES = ("GP_ACTIVATED|",)` alongside `FOUNDED|` and
`IMPROVING|`/`REPAIRING|`, so
"not an error" is never read as success, matching the module's existing rule.

`_run_single_task` handles `great_person_activate` in an explicit branch
before the current builder fallback. Away from the target it reuses the
existing movement and visible-hostile checks. At the target it calls
`gs.activate_great_person(unit.unit_index)` and classifies the normalized
result through `_resolve_at_target_action`.

Failure handling reuses the established status and blocked vocabulary. It
adds only action-specific result diagnostics:
`GP_ACTIVATE_NO_RESPONSE = "gp_activate_no_response"`,
`GP_ACTIVATE_NO_RESPONSE_RETRY_LIMIT =
"gp_activate_no_response_retry_limit"`, and
`GP_ACTIVATE_ERROR_RETRY_LIMIT = "gp_activate_error_retry_limit"`. A
non-success response counts an attempt against the existing retry limit. A
Great Person that no longer exists — whether activated manually before the
tracker runs or lost — becomes terminal `lost` with `unit_missing`, exactly
as a vanished builder does today.

### 2. Prompt instruction

`STANDING_PLAN_INSTRUCTION` (`prompting.py:21`) gains one example line for
the new kind, keeping the non-numeric `<unit_id>` / `<x>` / `<y>`
placeholders. That convention is load-bearing and documented in the comment
above it: placeholders cannot match `TASK_LINE_RE`, so a model echoing the
example verbatim creates no task.

### 3. Experiment artifact

`experiments/arena-channels-behavior-v7.yaml` is copied from v6. It changes
`run_id` to `arena-channels-behavior-v7` and adds
`task_tracker: {enabled: true}` to players 1 and 2 only. Both remain
`tools: minimal`; the scripted player is byte-for-byte unchanged and its
tracker remains disabled. The loaded-config deltas are therefore exactly the
run ID and those two tracker flags.

Keeping `minimal` is intentional. Task lines are harness instructions, and
the tracker calls `GameState` directly rather than dispatching through the
seat's tool tier. V7 therefore isolates deterministic follow-through; it does
not validate the standard-tier visibility changes from the preceding spec.

This enables the tracker for the LLM seats, so the mechanism gets live
traffic and the analyzer's existing task counters (`task_completions`,
`task_blocked`, `task_lost`) have data. The loader test normalizes v7's run ID
and the two LLM tracker options back to v6 and asserts dataclass equality. It
also directly asserts that both tool selectors remain `minimal` and the
scripted seat's tracker is disabled.

The run itself is attended and scheduled separately; this spec delivers the
artifact, not the result.

## Testing

- Grammar: `TASK great_person_activate unit_id=65541 target=12,19` parses to
  a `UnitTask` with `kind="great_person_activate"` and no improvement; the
  same line with the `<unit_id>` placeholder does not parse, and a GP task
  carrying `improvement=` is ignored.
- Execution: with the unit away from target, the tracker moves and leaves the
  task active; on arrival it calls `activate_great_person` and an
  `GP_ACTIVATED|` response completes the task.
- A non-success response does not complete the task and counts toward the
  retry limit; exhausting the limit fails the task without changing status or
  blocked-reason vocabulary.
- A missing Great Person becomes `lost` with `unit_missing`.
- Prompt test: the instruction lists all three kinds, and every example line
  fails `TASK_LINE_RE` (the placeholder guarantee).
- Artifact loader test: parsed equality with v6 after normalizing only the run
  ID and players 1 and 2's tracker flags; both tools remain `minimal` and the
  scripted seat remains tracker-disabled.
- Full arena suite green before each commit.

## Out of scope

- Running the experiment.
- Additional task kinds. Anything that attacks, spends, or negotiates stays
  out — the tracker's value is that it is provably low-risk.
- Changing retry limits, blocked-reason vocabulary, or the analyzer's task
  metrics.
- Enabling the tracker in the existing v1–v6 artifacts, which are historical
  records and must keep re-running as they ran.
- Creating a standard-tier integrated artifact. That is a separate future
  experiment so the v7 result remains attributable to the tracker.
