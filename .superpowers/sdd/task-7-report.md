# Task 7 Report: Eight-Round Migration Sweep

## Implementation Summary

- Migrated the live gate lifecycle tests from the former nine-round path to
  the authoritative eight-round schedule (turns 10 through 17).
- Updated R2 assertions for same-round CLI payment acceptance and settlement,
  including the `accept_upfront_payment` phase and the third payment-state
  read.
- Retargeted lifecycle, deadline, crash/reconciliation, stale-deal, and
  terminal privacy tests to R3--R8. The final pass now has 24 captures and
  `fund_by_turn == 17`.
- Removed resume tests that duplicated durable stray-offer/digest-mismatch
  coverage added in Task 6.
- Added direct coverage that the CLI pre-acceptance guard journals the
  production `pre_acceptance_payment_status` before same-round settlement.
- Removed the reviewed dead no-op `captured_players` branch from
  `_recover_pending_actions`; it had no behavior after acknowledgement
  validation.

## Initial Failures

The initial `uv run pytest tests/arena/test_live_gate_channels.py -q` output
showed 11 failures before the terminal stream was truncated. The first
confirmed failure was:

- `test_restart_checkpoint_uses_no_live_payment_query` (expected two R2
  payment-state reads; rev-2 performs three: funding, pre-acceptance, and
  settlement verification).

Failure-driven migration runs then confirmed and corrected these stale
expectations:

- `test_partial_restart_round_survives_process_crash_and_restarts_once`
- `test_dual_advance_transition_crash_reconciles_and_converges`
  (deadline-satisfaction parameterization)
- `test_restart_live_fingerprint_mismatch_fails` (renamed to assert the
  intentional `official_payment_auto_resolved` forensic reason)
- `test_restart_fingerprint_mismatch_is_forensic_only` (removed as duplicate
  Task 6 stray-offer coverage)
- `test_premature_terminal_state_fails_gate`
- `test_upfront_responsible_capture_requires_deadline_transition`
- `test_funding_responsible_capture_requires_deadline_transition`
- `test_upfront_completed_transition_after_deadline_fails`

The obsolete changed-offer and absent-offer resume tests were also removed as
duplicate durable-resume cases covered by Task 6.

## Verification

- `uv run pytest tests/arena/test_live_gate_channels.py -q`
  - `111 passed in 46.99s`
- `uv run pytest tests/arena/test_live_gate_channels.py -q --tb=short -k
  'round2_funding_offers_exact_payment or
  round2_settles_payment_same_round_then_requests_restart or
  pre_acceptance_recorded_status_is_reused_without_live_query'`
  - `3 passed, 108 deselected in 1.32s`
- `uv run pytest tests -q`
  - `1948 passed in 127.32s (0:02:07)`
- `git diff --check`
  - Passed with no output.

## Files Changed

- `tests/arena/test_live_gate_channels.py`
- `src/civ_mcp/arena/live_gate_channels.py`

## Self-Review

- Confirmed every schedule-dependent assertion uses R1 turn 10 through R8
  turn 17, with the inclusive funding breach on turn 17.
- Confirmed the successful lifecycle phase list includes
  `accept_upfront_payment` and omits the attach-only restart transition.
- Confirmed final privacy coverage spans exactly eight turns and all 24 seat
  captures.
- No production behavior changed apart from removing a dead no-op branch.

## Concerns

- `.serena/memories/` is an untracked tooling directory created during
  semantic-tool onboarding. It is not part of this task and is excluded from
  the commit.

## Task 7 Review Fix

- Added an explicit journal assertion in
  `test_terminal_pass_requires_full_privacy_coverage_for_every_turn` that
  requires exactly every `(turn, player_id)` pair for turns 10 through 17 and
  players 1 through 3 in `seat_captured` events.
- No production behavior was changed.

## Review Fix Verification

- `uv run pytest tests/arena/test_live_gate_channels.py::test_terminal_pass_requires_full_privacy_coverage_for_every_turn -q`
  - `1 passed in 1.66s`
- `uv run pytest tests/arena/test_live_gate_channels.py -q`
  - `111 passed in 45.22s`
- `uv run pytest tests -q`
  - `1948 passed in 129.67s (0:02:09)`
- `git diff --check`
  - Passed with no output.
