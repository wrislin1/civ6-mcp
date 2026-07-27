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
unchanged and takes no `improvement=` argument:

```
TASK great_person_activate unit_id=65541 target=12,19
```

Execution reuses the existing move-then-act loop. The at-target action calls
`GameState.activate_great_person(unit_index)`, whose success shape is
`OK:GP_ACTIVATED|…` (`lua/great_people.py:258` and `:330`) — added to the
success-prefix table alongside `FOUNDED|` and `IMPROVING|`/`REPAIRING|`, so
"not an error" is never read as success, matching the module's existing rule.

Failure handling reuses the established vocabulary rather than inventing
codes: a non-success response counts an attempt against the retry limit, and
a Great Person that no longer exists (activated manually, or lost) resolves
the task the same way a vanished builder does today.

### 2. Prompt instruction

`STANDING_PLAN_INSTRUCTION` (`prompting.py:21`) gains one example line for
the new kind, keeping the non-numeric `<unit_id>` / `<x>` / `<y>`
placeholders. That convention is load-bearing and documented in the comment
above it: placeholders cannot match `TASK_LINE_RE`, so a model echoing the
example verbatim creates no task.

### 3. Experiment artifact

An experiment artifact that enables the tracker for the LLM seats, so the
mechanism gets live traffic and the analyzer's existing task counters
(`task_completions`, `task_blocked`, `task_lost`) have data. It reuses the
channels-behavior seat configuration so the comparison is against a run
series whose behavior is already characterized, and it is pinned by a loader
test asserting equality-minus-delta against its parent artifact, as every
artifact in that series is.

The run itself is attended and scheduled separately; this spec delivers the
artifact, not the result.

## Testing

- Grammar: `TASK great_person_activate unit_id=65541 target=12,19` parses to
  a `UnitTask` with `kind="great_person_activate"` and no improvement; the
  same line with the `<unit_id>` placeholder does not parse.
- Execution: with the unit away from target, the tracker moves and leaves the
  task active; on arrival it calls `activate_great_person` and an
  `OK:GP_ACTIVATED|` response completes the task.
- A non-success response does not complete the task and counts toward the
  retry limit; exhausting the limit fails the task with the existing reason
  vocabulary.
- Prompt test: the instruction lists all three kinds, and every example line
  fails `TASK_LINE_RE` (the placeholder guarantee).
- Artifact loader test: parsed equality with its parent after normalizing the
  differing fields.
- Full arena suite green before each commit.

## Out of scope

- Running the experiment.
- Additional task kinds. Anything that attacks, spends, or negotiates stays
  out — the tracker's value is that it is provably low-risk.
- Changing retry limits, blocked-reason vocabulary, or the analyzer's task
  metrics.
- Enabling the tracker in the existing v1–v6 artifacts, which are historical
  records and must keep re-running as they ran.
