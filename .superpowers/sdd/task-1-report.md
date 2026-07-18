# Task 1 Report: Scenario Revision Constant and Capture Math

## Implementation Summary

- Set `SCENARIO_REVISION` to `2`.
- Updated `minimum_captures` to use the revision-2 eight-round path:
  `2 + UPFRONT_WITHIN + 2 + ON_DELIVERY_WITHIN + funding_turns`, multiplied
  by the three role contracts.
- Updated the two capture-math tests and added the scenario revision pin.

## TDD RED/GREEN Evidence

### RED

Command:

```text
uv run pytest tests/arena/test_live_gate_channels.py -q -k "minimum_captures or scenario_revision_is_2"
```

Result: `3 failed, 91 deselected`.

The failures were the expected pre-implementation mismatches: `27 != 24`,
`33 != 30`, and `1 != 2`.

### GREEN

Command:

```text
uv run pytest tests/arena/test_live_gate_channels.py -q -k "minimum_captures or scenario_revision"
```

Result: `3 passed, 91 deselected`.

Final focused verification after commit:

```text
uv run pytest tests/arena/test_live_gate_channels.py -q -k "minimum_captures or scenario_revision_is_2"
```

Result: `3 passed, 91 deselected`.

## Test Commands and Results

- `uv run pytest tests/arena/test_live_gate_channels.py -q -k "minimum_captures or scenario_revision_is_2"`
  - PASS: 3 passed, 91 deselected.
- `uv run pytest tests/arena/test_live_gate_channels.py -q`
  - FAIL: 7 failed, 87 passed.
  - The failures are later lifecycle/end-to-end tests that still expect the
    pre-revision choreography; they are outside this task's permitted source
    and test scope.
- `git diff --check`
  - PASS.

## Files Changed

- `src/civ_mcp/arena/live_gate_channels.py`
- `tests/arena/test_live_gate_channels.py`
- `.superpowers/sdd/task-1-report.md`

## Self-Review

- The implementation matches the exact constants and formula in the brief.
- Only the requested production and test files were changed for the code task.
- No formatting errors were reported by `git diff --check`.
- The committed worktree is clean.

## Concerns

The complete `tests/arena/test_live_gate_channels.py` run currently has seven
failures in later lifecycle coverage: four crash-reconciliation cases, the
full-run pass case, the terminal privacy-coverage case, and the skipped
transcript-hook case. These appear to be expected follow-on revision-2
choreography updates, but are not addressed here because the brief limits this
task to the revision constant, capture math, and their tests.

## Commit

`9ebe043 feat(arena): live-gate revision 2 constants — 24-capture eight-round path`
