# Tasks 2, 3, and 5 Combined Corrective Slice Report

## Status

Implemented the same-round payment settlement phase order, durable treasury
settlement evidence, and a restart checkpoint anchored on the settlement
digest. The five new regression tests pass.

## Implementation Summary

- Reordered the round-2 phase path to
  `fund_upfront -> accept_upfront_payment -> restart_required`.
- Updated started-capture reconciliation successors and the post-restart next
  phase to match the revision-2 order.
- Recorded payer/payee treasury baselines before API funding and results after
  the CLI settlement capture, with journal-data guards preventing game rereads
  during recovery replay.
- Verified the exact one-gold settlement delta from journaled evidence, then
  persisted a 16-hex `settlement_digest` publicly and in the private payment
  checkpoint sidecar.
- Made `_digest_mapping` normalize immutable journal mappings before JSON
  serialization; flat mapping digests remain unchanged.
- Reworked `_request_restart` to recompute and verify durable settlement
  evidence without querying game payment state.
- Re-armed an interrupted restart boundary only when `settlement_digest` is
  present.
- Added fail-closed structural type checks for treasury evidence before delta
  arithmetic.

## TDD Evidence

### RED

1. `uv run pytest tests/arena/test_live_gate_channels.py::test_round2_settles_payment_same_round_then_requests_restart -q`
   - Failed because the official offer remained in `gs.pending`; R2 had skipped
     `accept_upfront_payment`.
2. `uv run pytest tests/arena/test_live_gate_channels.py -q -k settlement_`
   - Failed because `settlement_baseline` and `settlement_result` were absent,
     the phantom transfer did not fail closed, and the initial immutable-state
     sabotage needed adjustment.
3. `uv run pytest tests/arena/test_live_gate_channels.py -q -k "restart_checkpoint or restart_without"`
   - Failed because the round-boundary checkpoint added a live payment query
     and missing settlement evidence was ignored.
4. The missing-digest test was rerun after changing sabotage from direct
   `mappingproxy` mutation to a journaled `data_recorded` overwrite. It then
   failed for the intended reason: status remained `restart_required` instead
   of becoming `failed`.

### GREEN

`uv run pytest tests/arena/test_live_gate_channels.py::test_round2_settles_payment_same_round_then_requests_restart tests/arena/test_live_gate_channels.py::test_settlement_records_baselines_deltas_and_digest tests/arena/test_live_gate_channels.py::test_settlement_delta_mismatch_fails_closed tests/arena/test_live_gate_channels.py::test_restart_checkpoint_uses_no_live_payment_query tests/arena/test_live_gate_channels.py::test_restart_without_settlement_digest_fails -q`

Result: `5 passed in 2.42s`.

## Test Commands and Results

- Task 2 focused command: `1 passed in 0.69s`.
- Task 3 keyword command: `3 passed, 96 deselected in 1.52s` (the keyword also
  selects the new missing-settlement-digest test).
- Task 5 literal keyword command: `2 passed, 1 failed, 96 deselected in 1.65s`.
  The sole failure is the existing revision-1
  `test_restart_checkpoint_persists_fingerprint_and_result`, which still
  requires `payment_checkpoint["before"]`; Task 7 owns that lifecycle sweep.
- Adjacent regression command for round-boundary deferral and half-ahead
  checkpoint reconciliation: `3 passed in 1.19s`.
- `uv run python -m compileall -q src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py`:
  passed.
- `git diff --check`: passed.
- `uv run ruff check ...` and `uv run ruff format --check ...`: not run because
  Ruff is not installed in this environment (`Failed to spawn: ruff`).

## Files Changed

- `src/civ_mcp/arena/live_gate_channels.py`
- `tests/arena/test_live_gate_channels.py`
- `.superpowers/sdd/task-2-3-5-report.md`

## Self-Review

- `_advance_after_capture` performs no new game reads. Both treasury reads are
  in the seat-turn or after-capture pre-journal paths and are guarded by
  journal data.
- The API write-ahead baseline is durable before the official funding side
  effect. The post-settlement result is durable before `seat_capture_started`.
- Recovery replays cannot dispatch a second payment from the new evidence
  logic; they only re-evaluate journaled evidence and append deterministic
  phase transitions.
- The phase sequence for the happy R2 path is exactly:
  `canary_and_upfront_proposal`, `accept_upfront`, `fund_upfront`,
  `accept_upfront_payment`, `restart_required`.
- Settlement mismatches use the allowlisted `payment_checkpoint_failed` reason
  with private `settlement_delta_mismatch` detail. Restart evidence failures
  use `restart_checkpoint_failed`.
- The settlement digest covers the exact payment fingerprint, baseline, and
  result and is recomputed before restart.

## Concerns and Deliberate Deviations

1. The Task 5 test snippet counted from attach and expected two payment-state
   calls, but the current production runtime performs three before this change:
   preflight, funding validation, and CLI response validation. The test now
   installs its counter after R1 and keeps the required `len(calls) == 2`, so
   it precisely proves that the two R2 runtime checks occur and the restart
   checkpoint adds none.
2. Task 5 says `_live_offer_fingerprint` is unused and should be deleted, but
   it remains referenced by the current revision-1 payment checkpoint
   reconciler and restart verifier. Deleting it produced real attribute errors.
   It is retained until the out-of-scope Task 6/7 replacement removes those
   references. `_request_restart` does not call it.
3. The full test file/suite was not run because Tasks 6 and 7 explicitly own
   the expected revision-1 resume and lifecycle failures. The literal Task 5
   keyword command's one stale assertion is documented above.

## Review Fix: Durable Revision-2 Resume Verification

This follow-up folds the controller-selected Task 6 resume behavior into the
same atomic slice. It resolves review finding locations that still depended on
a live exact offer after same-round settlement. This section supersedes
Concerns 2 and 3 above: `_live_offer_fingerprint` is now deleted, and the
controller's combined covering filter is green.

### Implementation

- Replaced half-ahead payment checkpoint repair's live-offer query with a
  fingerprint reconstructed from the canonical persisted `ChannelRuntime`
  deal. Both private-ahead and public-ahead repairs now work after the engine
  offer disappears.
- Replaced restart verification's OFFERED/live-exact checks with:
  - canonical deal status `SETTLED`;
  - recomputed `settlement_digest` equality;
  - live official payment state `absent`;
  - exactly one settlement acknowledgement at or before `restart_turn`, bound
    to the verified `respond_to_payment` plan's `source_id`;
  - no matching payment acknowledgement after `restart_turn`; and
  - unchanged channel event sequence.
- Added fail-closed validation for missing `upfront_deal_id` and invalid
  `restart_turn` durable fields.
- Deleted `_live_offer_fingerprint` and removed every reference to it.
- Updated only checkpoint/resume assertions directly superseded by Task 6;
  Tasks 4, 7, and 8 remain out of scope.

### TDD RED Evidence

Task 6 happy resume failed because the old verifier required the now-settled
deal to remain OFFERED:

```text
$ uv run pytest tests/arena/test_live_gate_channels.py -q -k "resume_verifies_settlement or resume_with_stray"
F.                                                                       [100%]
FAILED ...test_resume_verifies_settlement_and_continues
1 failed, 1 passed, 99 deselected in 1.45s
```

The stray-offer test was tightened to require the new forensic detail and then
failed against the old `payment_state_changed` reason:

```text
$ uv run pytest tests/arena/test_live_gate_channels.py::test_resume_with_stray_official_offer_fails -q
F                                                                        [100%]
E       assert 'stray_official_offer' in '{"detail":{"failure":"payment_state_changed",...}}'
1 failed in 0.93s
```

Both half-ahead variants failed once the transient engine offer was removed:

```text
$ uv run pytest tests/arena/test_live_gate_channels.py::test_payment_checkpoint_half_ahead_reconciles_on_attach -q
FF                                                                       [100%]
2 failed in 0.81s
```

### GREEN Command Output

```text
$ uv run pytest tests/arena/test_live_gate_channels.py::test_resume_verifies_settlement_and_continues tests/arena/test_live_gate_channels.py::test_resume_with_stray_official_offer_fails -q
..                                                                       [100%]
2 passed in 1.15s

$ uv run pytest tests/arena/test_live_gate_channels.py -q -k resume
........                                                                 [100%]
8 passed, 93 deselected in 4.27s

$ uv run pytest tests/arena/test_live_gate_channels.py::test_payment_checkpoint_half_ahead_reconciles_on_attach -q
..                                                                       [100%]
2 passed in 0.70s

$ uv run pytest tests/arena/test_live_gate_channels.py -q -k "same_round or settlement_ or restart_checkpoint or restart_without or resume"
...............                                                          [100%]
15 passed, 86 deselected in 7.02s

$ uv run python -m compileall -q src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
[exit 0]

$ git diff --check
[exit 0]
```

### Review-Fix Self-Review

- No official payment side effect was added. Resume performs one read-only
  payment-state query solely to require `absent`.
- Half-ahead reconciliation performs no game query and does not require a
  pending engine offer.
- The settlement acknowledgement is matched through the journaled verified
  action source, not inferred from message text or a nonexistent action field.
- `restart_verified.next_phase` remains
  `await_upfront_favor_deadline`, preserving deterministic revision-2 replay.
- Semantic diagnostics in the changed region show only the file's existing
  optional `_journal`/`_runtime`/`_gs` member warnings; the new journal field
  boundaries are explicitly validated.

### Remaining Concerns

- Ruff remains unavailable in this environment. The requested pytest,
  compilation, and diff checks pass.

## Re-Review Fix: Crash-Safe Settlement Recovery and Byte-Stable Data Replay

### Implementation Summary

- Pending canonical acknowledgements now queue their already-applied capture
  for the next async `seat_turn` boundary instead of synchronously advancing
  from `note_admission`.
- Normal and recovered CLI settlement captures share
  `_record_settlement_result()`. Recovery reads and journals the post-payment
  treasury result before `seat_capture_started`, then reconciles the capture
  without dispatching or finishing another channel action.
- Added `_record_data_once()` for atomic durable data writes. Absent values are
  appended, matching values are skipped on replay, and differing or partial
  values fail closed with the caller's allowlisted reason code.
- Applied the guard to settlement baseline/result, payment checkpoint digest,
  up-front deal metadata and due turn, settlement digest, on-delivery deal and
  due-turn metadata, half-ahead checkpoint repair, and restart metadata.
- Recovery reducer exceptions still persist the established sanitized
  `action_recovery_failed` result, now from the async recovery boundary.

### TDD RED Evidence

The four new recovery-boundary cases were added before production changes and
failed for the reported reasons:

```text
$ uv run pytest tests/arena/test_live_gate_channels.py -q -k "pending_payment_ack_recovery or settlement_data_append_crash or restart_metadata_append_crash"
FFFF                                                                     [100%]
FAILED ...test_pending_payment_ack_recovery_records_settlement_before_capture
E       AssertionError: assert 'failed' is None
FAILED ...test_settlement_data_append_crash_replays_without_duplicate[upfront_favor_due_turn]
E       AssertionError: assert 2 == 1
FAILED ...test_settlement_data_append_crash_replays_without_duplicate[settlement_digest]
E       AssertionError: assert 2 == 1
FAILED ...test_restart_metadata_append_crash_replays_without_duplicate
E       AssertionError: assert 2 == 1
4 failed, 101 deselected in 2.15s
```

This confirmed both root causes: recovered payment capture skipped durable
treasury evidence, and every post-append crash replayed the same data event.

### GREEN Command Output

```text
$ uv run pytest tests/arena/test_live_gate_channels.py -q -k "pending_payment_ack_recovery or settlement_data_append_crash or restart_metadata_append_crash"
....                                                                     [100%]
4 passed, 101 deselected in 1.94s

$ uv run pytest tests/arena/test_live_gate_channels.py -q -k "same_round or settlement_ or restart_checkpoint or restart_without or resume or pending_actions"
.....................                                                    [100%]
21 passed, 84 deselected in 8.23s

$ uv run python -m compileall -q src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
[exit 0; no output]

$ git diff --check
[exit 0; no output]
```

An additional full-module probe reached a stale, out-of-filter assertion that
exists unchanged at `HEAD` and expects `restart_required` immediately after
the API funding seat, before the CLI same-round settlement seat:

```text
$ uv run pytest tests/arena/test_live_gate_channels.py -x --tb=short
FAILED tests/arena/test_live_gate_channels.py::test_partial_restart_round_survives_process_crash_and_restarts_once
E   AssertionError: assert 'accept_upfront_payment' == 'restart_required'
1 failed, 52 passed in 11.04s
```

Ruff was also probed but is not installed:

```text
$ uv run ruff check src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
error: Failed to spawn: `ruff`
  Caused by: No such file or directory (os error 2)
```

### Files Changed

- `src/civ_mcp/arena/live_gate_channels.py`
- `tests/arena/test_live_gate_channels.py`
- `.superpowers/sdd/task-2-3-5-report.md` (report append only)

### Self-Review

- `_advance_after_capture()` remains game-read-only. The only new treasury read
  runs in async `seat_turn` recovery before the capture journal marker.
- Recovery never calls `respond_to_payment`, dispatches a plan, or invokes
  `finish_player`; the crash test proves one CLI acknowledgement and one gold
  transfer after convergence.
- Matching durable values produce no event, preserving the uncrashed
  `data_recorded` and `phase_advanced` sequence. Conflicts fail closed rather
  than overwriting evidence.
- Restart metadata remains one atomic two-key event, so partial durable state
  is treated as corruption rather than repaired into a different journal
  shape.
- Tasks 4, 7, and 8 were not implemented.

### Concerns

- The requested focused suite is green. The broader module still contains the
  stale partial-restart assertion shown above; changing it is outside this
  review fix and outside the requested covering filter.
- Ruff is unavailable; compileall and `git diff --check` pass.

## Latest Re-Review Fix: Durable Verified-Capture Handoff

### Implementation Summary

- Removed the process-local `_recovered_captures` handoff.
- Added reconstruction of an unfinished capture from durable
  `verified_actions` whose phase still matches the journal's current phase.
  Reconstruction excludes identities that still have pending actions and
  fails closed if more than one verified identity is eligible.
- Both `attach()` and the async seat-turn pre-journal path now reconstruct the
  capture, record a missing settlement treasury result before
  `seat_capture_started`, and finish through the existing started-capture
  reconciler without dispatching or finishing another channel action.
- The same reconstruction covers the normal callback crash after the durable
  `settlement_result` append but before `seat_capture_started`; the existing
  result is reused without a second data event or game read.
- Wrapped settlement digest recomputation in `_verify_restart()` and
  `_request_restart()`. Invalid digest inputs now persist sanitized
  `restart_verification_failed` or `restart_checkpoint_failed` results with
  private `settlement_evidence_invalid` detail.

### TDD RED Evidence

After correcting the non-finite test fixture to use a JSON-parseable NaN in
the private fingerprint digest input (the public journal reducer already
rejects non-finite floats), the four new tests failed at the intended
boundaries:

```text
$ uv run pytest tests/arena/test_live_gate_channels.py -q -k "action_verified_crash or settlement_result_append_crash or non_finite_settlement_evidence"
FFFF                                                                     [100%]
FAILED ...test_action_verified_crash_reconstructs_recovered_settlement_capture
E       AssertionError: assert 'accept_upfront_payment' == 'restart_required'
FAILED ...test_settlement_result_append_crash_reconstructs_normal_capture
E       AssertionError: assert 'accept_upfront_payment' == 'restart_required'
FAILED ...test_restart_request_non_finite_settlement_evidence_fails_closed
E       AssertionError: assert 'gate_invariant_failed' == 'restart_checkpoint_failed'
FAILED ...test_restart_verify_non_finite_settlement_evidence_fails_closed
E       ValueError: Out of range float values are not JSON compliant: nan
4 failed, 105 deselected in 1.98s
```

### GREEN Command Output

```text
$ uv run pytest tests/arena/test_live_gate_channels.py::test_action_verified_crash_reconstructs_recovered_settlement_capture tests/arena/test_live_gate_channels.py::test_settlement_result_append_crash_reconstructs_normal_capture tests/arena/test_live_gate_channels.py::test_restart_request_non_finite_settlement_evidence_fails_closed tests/arena/test_live_gate_channels.py::test_restart_verify_non_finite_settlement_evidence_fails_closed -q
....                                                                     [100%]
4 passed in 1.80s

$ uv run pytest tests/arena/test_live_gate_channels.py -q -k "same_round or settlement_ or restart_checkpoint or restart_without or resume or pending_actions or action_verified"
.........................                                                [100%]
25 passed, 84 deselected in 8.94s

$ uv run python -m compileall -q src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
[exit 0; no output]

$ git diff --check
[exit 0; no output]
```

Additional recovery-contract probe:

```text
$ uv run pytest tests/arena/test_live_gate_channels.py -q -k "recovery_reducer or recovered_capture or pending_applied"
...                                                                      [100%]
3 passed, 106 deselected in 0.51s
```

### Files Changed

- `src/civ_mcp/arena/live_gate_channels.py`
- `tests/arena/test_live_gate_channels.py`
- `.superpowers/sdd/task-2-3-5-report.md` (required report append)

### Self-Review

- The capture-recovery source of truth is now entirely durable: verified
  action identity, current phase, pending actions, and captured players.
- A partial multi-action recovery cannot start a capture because any remaining
  pending action for the same identity suppresses reconstruction.
- Settlement treasury reads occur only in async attach/seat-turn recovery
  before `seat_capture_started`; `_advance_after_capture()` remains game-read
  only.
- Recovery does not invoke channel dispatch or `finish_player`; tests assert
  one payment acknowledgement, one treasury transfer, and one
  `settlement_result` event.
- Digest exceptions are converted at the two required ownership boundaries,
  with no non-finite value copied into the public failure journal.
- Tasks 4, 7, and 8 remain untouched.

### Concerns

- No new concerns in the requested scope. The previously documented stale
  full-module phase assertion and unavailable Ruff executable remain unchanged.

## Narrow Boundary Fix: Defer Missing Settlement Evidence from Attach

### Implementation Summary

- Added the keyword-only `allow_settlement_read` mode to
  `_reconcile_verified_capture()`.
- `attach()` passes `False`. A durable verified payment capture with no
  `settlement_result` remains reconstructible but attach appends no capture
  marker and performs no treasury observation.
- `_seat_turn_inner()` passes `True`, so the next seat-turn pre-journal path
  records the payer/payee treasury result once and completes the verified
  capture before normal seat work.
- If `settlement_result` is already durable, attach still completes recovery
  immediately because `_record_settlement_result()` returns without querying
  the game.

### TDD RED Evidence

The updated boundary tests were run before the production change. The
durable-result case already passed, while the missing-result case proved that
attach incorrectly advanced to restart instead of deferring:

```text
$ uv run pytest tests/arena/test_live_gate_channels.py::test_action_verified_crash_reconstructs_recovered_settlement_capture tests/arena/test_live_gate_channels.py::test_settlement_result_append_crash_reconstructs_normal_capture -q
F.                                                                       [100%]
FAILED ...test_action_verified_crash_reconstructs_recovered_settlement_capture
E       AssertionError: assert 'restart_required' == 'accept_upfront_payment'
1 failed, 1 passed in 1.24s
```

### GREEN Command Output

```text
$ uv run pytest tests/arena/test_live_gate_channels.py::test_action_verified_crash_reconstructs_recovered_settlement_capture tests/arena/test_live_gate_channels.py::test_settlement_result_append_crash_reconstructs_normal_capture -q
..                                                                       [100%]
2 passed in 0.99s

$ uv run pytest tests/arena/test_live_gate_channels.py -q -k "same_round or settlement_ or restart_checkpoint or restart_without or resume or pending_actions or action_verified"
.........................                                                [100%]
25 passed, 84 deselected in 9.16s

$ uv run python -m compileall -q src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
[exit 0; no output]

$ git diff --check
[exit 0; no output]
```

### Files Changed

- `src/civ_mcp/arena/live_gate_channels.py`
- `tests/arena/test_live_gate_channels.py`
- `.superpowers/sdd/task-2-3-5-report.md` (required report append)

### Self-Review

- Attach performs no settlement treasury query. The regression records all
  treasury-family observations and requires none during attach.
- The next seat turn performs exactly the two expected treasury observations,
  records one `settlement_result`, and does not create a second payment
  acknowledgement or treasury transfer.
- Existing durable-result recovery remains attach-completable with no duplicate
  data event and no treasury observation.
- `_advance_after_capture()` remains game-read-only; journal shape and event
  kinds are unchanged.
- Tasks 4, 7, and 8 remain untouched.

### Concerns

- No new concerns in this boundary fix. Previously documented unrelated
  concerns remain unchanged.

## Validation Fix: Unexpected Acknowledgement on Verified Recovery

### Implementation Summary

- `_reconcile_verified_capture()` now calls
  `_check_no_unexpected_acknowledgements(player_id, turn)` immediately after
  reconstructing the durable capture identity.
- A failed check returns before the attach settlement-read deferral decision,
  before any seat-turn treasury evidence read, and before
  `seat_capture_started`.
- Added a regression that combines a durable `action_verified` crash with an
  injected rogue same-player/same-turn acknowledgement.

### TDD RED Evidence

```text
$ uv run pytest tests/arena/test_live_gate_channels.py::test_action_verified_crash_rejects_rogue_same_turn_acknowledgement -q
F                                                                        [100%]
FAILED ...test_action_verified_crash_rejects_rogue_same_turn_acknowledgement
E       AssertionError: assert None == 'failed'
1 failed in 1.72s
```

The failure confirmed that attach deferred the verified capture without
checking the rogue acknowledgement.

### GREEN Command Output

```text
$ uv run pytest tests/arena/test_live_gate_channels.py::test_action_verified_crash_rejects_rogue_same_turn_acknowledgement -q
.                                                                        [100%]
1 passed in 0.50s

$ uv run pytest tests/arena/test_live_gate_channels.py -q -k "same_round or settlement_ or restart_checkpoint or restart_without or resume or pending_actions or action_verified or unexpected_ack"
...........................                                              [100%]
27 passed, 83 deselected in 9.72s

$ uv run python -m compileall -q src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
[exit 0; no output]

$ git diff --check
[exit 0; no output]
```

### Files Changed

- `src/civ_mcp/arena/live_gate_channels.py`
- `tests/arena/test_live_gate_channels.py`
- `.superpowers/sdd/task-2-3-5-report.md` (required report append)

### Self-Review

- The existing verifier is reused; no acknowledgement policy or journal shape
  was redesigned.
- The rogue acknowledgement fails with the allowlisted
  `unexpected_acknowledgement` reason and private source detail.
- Recovery writes neither `settlement_result` nor `seat_capture_started` after
  detecting the rogue acknowledgement.
- Valid attach deferral and seat-turn settlement recovery remain covered by the
  focused suite.
- Tasks 4, 7, and 8 remain untouched.

### Concerns

- No new concerns in this validation fix. Previously documented unrelated
  concerns remain unchanged.
