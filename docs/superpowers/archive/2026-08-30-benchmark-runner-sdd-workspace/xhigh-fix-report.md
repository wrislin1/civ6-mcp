# xhigh review fix wave — fix report

Branch `worktree-benchmark-runner`, starting HEAD `f4061a2` (2460 tests green).
All work done via TDD: a failing test reproducing each finding, a minimal fix, GREEN.

Final state: 11 commits, full suite `2502 passed` (`uv run pytest -q -p no:cacheprovider`,
~133s). Byte-identical report regeneration re-verified (`test_write_reports_produces_canonical_json_and_markdown`,
`test_regenerating_the_same_run_is_byte_identical`, `test_regeneration_with_a_missing_position_is_still_byte_identical`
all still pass unmodified).

---

## F1 — Per-model/per-arm position aggregation

**Status: VERIFIED**

Repro: `test_build_report_aggregate_is_scoped_per_model_arm_group_not_pooled`
(new) — two models scoring perfectly/zero at two positions; against the
pre-fix pooled implementation the aggregate was one flat dict with keys
`equal_weight_mean`/`worst_position_id`/`worst_position_median` mixing both
models. RED confirmed via `git stash` of the source file (assertion failed
with `KeyError`-shaped diff showing the flat keys instead of per-group keys).

Fix (`src/civ_mcp/arena/benchmark_report.py`): `_position_summary` now
groups scored trials by `(model, arm_id)` into `positions[pid]["by_group"]
["<model>::<arm>"]`, each holding the previous full summary shape
(`_group_summary`, renamed from the old `_position_summary` body) scoped to
that one group. `report["aggregate"]` is now keyed by the same
`"<model>::<arm>"` label; `equal_weight_mean`/`worst_position_id`/
`worst_position_median` are computed only from that group's positions.
There is no pooled/mixed aggregate entry — F1 requires every equal-weight
statistic to be scoped to one (model, arm) group, so none is kept.
`render_markdown` renders one subsection per group per position and one
aggregate subsection per group.

Single-model/single-arm fixtures (no model/arm recorded on trials) collapse
to exactly one `"unknown::unknown"` group, so their numbers are numerically
unchanged from before the fix.

Tests updated to the new shape (pinned tests conflicting with the ruled
fix): `test_report_ignores_attempts_and_weights_positions_equally`,
`test_build_report_includes_per_position_output`,
`test_build_report_copies_retry_counts_from_raw_trials`,
`test_build_report_surfaces_seeds_and_endpoint_topology` (rewritten —
its old fixture mixed two different models/arms in one position and
asserted a POOLED seeds/endpoint_topology view, which is exactly the F1
bug; now asserts the two groups score separately),
`test_build_report_includes_terminal_conditions_latency_tokens_cost`,
`test_build_report_lists_a_zero_committed_position_in_completeness`,
`test_build_report_works_when_attempts_directory_is_absent`.

---

## F2 — Gates rubric shape incompatible with report

**Status: VERIFIED**

Repro:
`test_check_treatment_can_fire_does_not_fail_open_on_mapping_shaped_rubric_levels`
(new) — canonical `{score, predicate}`-shaped rubric with a level score of
1/2 that the minimal observation cannot discover; standard-arm capabilities
fully satisfy the (only) declared objective. Against the pre-fix code the
whole gate returned `ok=True` with **no** `GateFailure` at all (true
vacuous fail-open, matching the finding's description), instead of raising
`treatment_cannot_fire`. RED confirmed.

Ruling applied: the report's `{score, predicate}` mapping shape is
canonical. Fix (`src/civ_mcp/arena/benchmark_gates.py`):
`check_treatment_can_fire` now reads each level's `"score"` field via a new
`_reaches_level_1_or_2` inner helper, and raises
`GateFailure("malformed_rubric_level", ...)` for any level that isn't a
mapping (accepts ONLY the mapping shape).

Repro for the bare-int rejection:
`test_check_treatment_can_fire_rejects_bare_int_levels` (new) — asserts
`exc_info.value.code == "malformed_rubric_level"`.

Pinned tests updated to the canonical mapping shape (per the ruling, the
plan's old bare-int fixture was the defect): `_position()`'s default
`rubric` fixture and `test_calibration_gate_requires_minimal_progress_and_standard_completion`'s
inline rubric.

---

## F10 — Absent commit evidence passes the clean-checkout gate

**Status: VERIFIED**

Repro: `test_check_clean_checkout_rejects_absent_commits_on_both_sides` and
`test_check_clean_checkout_rejects_empty_string_commits_on_both_sides`
(new) — both `wsl.commit`/`windows.commit` `None`/`""`. Pre-fix:
`check_clean_checkout` returned evidence normally (`DID NOT RAISE`). RED
confirmed.

Fix: added `_present_commit(value)` (non-empty string) and a
`GateFailure("missing_commit", ...)` raised before the equality
comparison if either side is absent/empty. Deliberately NOT a strict
hex-format regex — the brief's "plausibly hex-ish" is a suggestion, not a
hard requirement, and a strict hex check would have broken the existing
pinned `test_build_session_lock_rejects_windows_commit_mismatch` fixture
(`"zzz999"`, non-hex but a legitimate non-empty commit string for that
test's purpose). Non-empty-string is the minimal fix that closes the
actual reported hole (both `None`) without over-constraining callers.

---

## F3 — Scheduled seed never bound to the backend

**Status: VERIFIED**

Repro: `test_make_agent_binds_the_trial_specs_seed_to_the_backend` (new) —
two `TrialSpec`s with different seeds against the same model; pre-fix,
`agent_a.backend.sampling.seed` and `agent_b.backend.sampling.seed` were
both `None` (the suite's static `sampling.seed`). RED confirmed
(`assert None == 101`).

Fix (`src/civ_mcp/arena/benchmark_runner.py`,
`_build_live_dependencies`): `backend_cache` is now keyed by `(model, seed)`
instead of `model` alone; `backend_for(model, seed)` constructs each
backend with `dataclasses.replace(suite.sampling, seed=seed)`. `make_agent`
and `probe_health_dep` updated to pass `spec.seed` / the last-used seed
through.

---

## F4 — Live wiring hands the agent a SimpleNamespace, not GameState

**Status: VERIFIED**

Repro: `test_run_trial_hands_the_agent_a_real_game_state` (new) — drives
`BenchmarkRunner.run_trial` with a recording fake agent and asserts the
`gs` object passed to `agent.run()` is `isinstance(gs, GameState)`. Pre-fix:
`gs` was `SimpleNamespace(conn=<object>)`. RED confirmed
(`AssertionError: expected a real GameState, got namespace(conn=...)`).

Fix (`src/civ_mcp/arena/benchmark_runner.py`, `run_trial`): replaced
`gs = SimpleNamespace(conn=self._deps.connection)` with
`gs = GameState(self._deps.connection)`, matching `arena/arena.py`'s live
wiring. Removed the now-unused `types.SimpleNamespace` import.

---

## F5 — Popup error statuses ignored

**Status: VERIFIED**

Repro:
`test_popup_failure_status_string_is_an_infrastructure_attempt` (new,
parametrized over `"err"` / `"?"` / `"SOMETHING_UNRECOGNIZED"`) and
`test_popup_success_status_proceeds_into_the_trial` (new, control). Pre-fix:
a failure status string proceeded straight into the trial (no infra attempt
recorded, `completed_indices() == {1}`). RED confirmed.

Fix (`src/civ_mcp/arena/benchmark_runner.py`, `run_trial`): the popup
step now inspects the returned status; anything that isn't a
`"POPUPS|..."` string is recorded as a `FailureClass.POPUP_HYGIENE`
infrastructure attempt instead of proceeding.

---

## F6 — Reload failures reported as strings are treated as success

**Status: VERIFIED**

Repro:
`test_live_reload_position_raises_on_failure_strings` (new, parametrized
over an `"Error: ..."` string and both `continue_after_lua_load` WARNING
strings) and `test_live_reload_position_passes_on_success_string` (new,
control, a `"Loaded ...: world ready, ..."` string). Pre-fix: none of the
failure strings raised — `deps.reload_position(...)` returned normally.
RED confirmed (`Failed: DID NOT RAISE`).

Fix (`src/civ_mcp/arena/benchmark_runner.py`, `_build_live_dependencies`):
added `_reload_result_is_success(result)` (`"Loaded "` prefix or
`"world ready"` substring); `reload_position` now raises `RuntimeError` on
a failure/warning result instead of discarding it — `RunnerDependencies.
reload_position`'s contract is `Awaitable[None]`, so this wrapper is the
only place that ever sees the raw string, and it now classifies it before
the runner's `except Exception` records `RELOAD_OR_RECONNECT`.

---

## F7 — Partial evidence lost on request timeouts

**Status: VERIFIED**

Repro: `test_request_timeout_commits_partial_progress_via_agent_accessor`
(new) — a fake agent mimicking `SingleTurnAgent`'s progress-tracking
contract (`run()` raises a bare `openai.APITimeoutError` with no
`partial_evidence` attribute; `partial_evidence()` exposes pre-failure
steps). Pre-fix: the committed trial's `steps` was `[]` even though
`partial_evidence()` had non-empty steps recorded. RED confirmed.

Fix:
- `src/civ_mcp/arena/benchmark_agent.py`: added
  `SingleTurnAgent.partial_evidence()`, a public accessor building an
  `EpisodeEvidence` from the same instance-level `_progress_*` state
  `EpisodeTimedOut.partial_evidence` was already built from (that
  construction now calls this new method, deduplicated). `_progress_*`
  attributes are also initialized in `__init__` (previously only inside
  `run()`) so the accessor is always safe to call.
- `src/civ_mcp/arena/benchmark_runner.py`: `_handle_timeout_like` gained an
  `agent` parameter; when the exception itself carries no
  `partial_evidence`, it falls back to `agent.partial_evidence()` (guarded
  via `getattr`/`callable` so fakes without the accessor are unaffected).
  All three call sites (`EpisodeTimedOut`, `openai.APITimeoutError`,
  `openai.APIConnectionError`) now pass `agent=agent`.

---

## F8 — Trials lack session provenance

**Status: VERIFIED**

Three repros (new):
- `test_finalize_trial_stamps_the_store_session_fingerprint` — committed
  trial payload had no `session_fingerprint` key at all pre-fix.
- `test_resume_fails_closed_on_a_session_fingerprint_mismatch` and
  `test_run_fails_closed_on_a_session_fingerprint_mismatch_during_resume_skip`
  — a committed trial stamped with a different `session_fingerprint`
  ("STALE_FINGERPRINT") was silently treated as done on resume pre-fix
  (`DID NOT RAISE`).
- `test_build_report_fails_closed_on_a_trial_session_fingerprint_mismatch`
  — `build_report` scored a trial whose `session_fingerprint` disagreed
  with `session.json`'s, pre-fix.

All four RED-confirmed.

Fix:
- `src/civ_mcp/arena/benchmark_runner.py`, `_finalize_trial`: stamps
  `"session_fingerprint": self.store.fingerprint` into every committed
  payload.
- `BenchmarkRunner._verify_resume_provenance(index)` (new): raises
  `SessionAborted("session_fingerprint_mismatch", ...)` if a
  already-completed trial's stamp disagrees with `self.store.fingerprint`.
  Called from both `run()`'s outer skip-loop and `run_trial`'s early-return
  branch (both are "resume-skip" entry points).
- `src/civ_mcp/arena/benchmark_report.py`, `build_report`: when both
  `session.json`'s `session_fingerprint` and a trial's own
  `session_fingerprint` are present and non-empty and disagree, raises
  `ReportError`. A trial with **no** stamp at all (predates this fix) is
  not treated as proof of a mismatch on its own — this keeps historical
  evidence readable while still catching a genuinely stale/copied file
  from a *different* current-lock session.

Test fixture `_committed_trial_payload` updated to carry a matching
`session_fingerprint` ("abc123", matching `_lock()`) by default so the two
existing resume-skip tests (`test_run_skips_already_committed_trials_on_resume`,
`test_scorer_absence_never_replays_a_committed_trial`) keep exercising the
"correct provenance" path rather than tripping the new mismatch guard.

---

## F9 — Malformed calls double-counted as domain rejections

**Status: VERIFIED**

Repro:
`test_malformed_tool_arguments_are_not_double_counted_as_domain_rejections`
(new) — runs a malformed-args episode through `SingleTurnAgent`, then
`action_metrics.classify_action_quality` on the resulting evidence. Pre-fix:
`domain_rejections == 1` while `invalid_calls == 1` (double count). RED
confirmed.

RULING applied: classifier logic in `action_metrics.py` unchanged. Fix
(`src/civ_mcp/arena/benchmark_agent.py`): the malformed-args step's
recorded result changed from `"ERROR: malformed arguments"` to
`"MALFORMED_ARGUMENTS: not dispatched"` — no longer starts with `"error"`
(case-insensitive) or contains `"|blocked"`, so `classify_result` reports
`"success"`; the step's `state_digest_before`/`state_digest_after` are both
`None`, so it's still never counted as a `successful_mutation` either.

Updated the two pinned assertions on the exact string in
`test_malformed_tool_arguments_are_not_dispatched_and_episode_continues`.

---

## F11 — Manhattan distance on a hex grid

**Status: VERIFIED**

Searched the repo for an existing Python-side hex-distance helper first
(per the brief) — grepped `src/civ_mcp` for `hex|cube|axial|distance`;
found only Lua-side `Map.GetPlotDistance` calls (the game engine's own
distance function, not callable from `action_metrics.py`) and unrelated
channel-term distance lookups backed by pre-computed observation dicts. No
reusable Python hex-distance helper exists.

Repro: `test_predicate_unit_distance_decreased_uses_hex_distance_not_manhattan`
(new) — the brief's exact counterexample, `(5,5)->(6,6)` toward `(5,8)`.
Pre-fix: `evaluate_predicate(...)` returned `False` (Manhattan distance
3->3, no apparent decrease). RED confirmed.

Fix (`src/civ_mcp/arena/action_metrics.py`): added `_offset_to_cube(x, y)`
(odd-r offset-to-cube conversion) and `_hex_distance(a, b)` (cube distance);
`_unit_distance` now calls `_hex_distance` instead of `abs(dx)+abs(dy)`.
Verified the odd-r formula against the brief's counterexample by hand
before implementing (cube distance 3->2, matching "real progress"); also
verified even-r gives the same result for this particular example, so the
choice between the two layouts is not discriminated by the given
counterexample alone — odd-r was picked as the implementation.

Added `test_predicate_unit_distance_decreased_true_on_straight_line_approach`
(new, a simple same-column approach) per the brief's "counterexample and
a straight-line case" requirement. The two pre-existing pinned tests
(`test_predicate_unit_distance_decreased_true_when_closer`/`_false_when_farther`)
both move along a constant-row axis, where hex distance and Manhattan
distance coincide exactly — verified by hand — so they needed no changes.

---

## F12 — state_digest numeric-type sensitivity + no diff on mismatch

**Status: VERIFIED**

Two repros (new):
- `test_state_digest_normalizes_integral_floats_to_ints` and
  `test_state_digest_normalizes_integral_floats_nested_in_rows` — `24` vs
  `24.0` (top-level and nested in a unit row) digested differently pre-fix.
  RED confirmed. Added a control,
  `test_state_digest_still_distinguishes_genuinely_different_non_integral_floats`
  (42.5 vs 42.6), confirming the fix doesn't collapse genuinely different
  values.
- `test_checksum_mismatch_journal_includes_a_field_level_diff` — the
  runner's `checksum_mismatch` journal event carried only the two opaque
  digests pre-fix (`KeyError: 'diff'`). RED confirmed.

Fix:
- `src/civ_mcp/arena/benchmark_state.py`: added `_canonicalize_numerics`
  (recursively coerces any numerically-integral float to the equivalent
  int, through dicts and lists); `normalize_state` now applies it to the
  whole normalized state, not just top-level fields.
- `src/civ_mcp/arena/benchmark_runner.py`, `run_trial`'s checksum-mismatch
  branch: added `"diff": diff_state(self._expected_state, observed_state)`
  to the journaled event details, importing `diff_state`.

Note on the journal test's assertion path: `BenchmarkStore.append_event`'s
signature is `append_event(event, *, trial_index=None, ..., **details)` —
calling it with a literal `details={...}` kwarg (the existing, unrelated
pattern every event in this module already uses) nests under
`record["details"]["details"]`, not `record["details"]` directly. The new
test asserts `events[0]["details"]["details"]["diff"]`, matching every
other event in the module; this pre-existing double-nesting quirk is out
of scope for this fix.

---

## F13 — Truncated FireTuner responses hashed as real state

**Status: VERIFIED**

Repro:
`test_capture_canonical_state_raises_on_truncated_response_missing_identity`
(a response with a `UNIT|...` line but no `IDENTITY` row) and
`test_capture_canonical_state_raises_on_completely_empty_response` (both
new). Pre-fix: `capture_canonical_state` returned the near-empty default
state (`turn: None, player_id: None, ...`) instead of raising. RED
confirmed (`Failed: DID NOT RAISE`).

Fix (`src/civ_mcp/arena/benchmark_state.py`, `capture_canonical_state`):
after parsing, raises `BenchmarkStateError` if `state["turn"] is None or
state["player_id"] is None` — treating a missing/None core identity field
as an incomplete/truncated response, never a "the state really looks like
this" answer. The runner's existing `except BenchmarkStateError` branch
already classifies this as `FailureClass.HARNESS_CRASH` (verified by the
existing `test_capture_state_failure_propagates_raw_out_of_run`-style
coverage in `test_benchmark_runner.py`, unchanged).

---

## F14 — Store filename regexes blind above index 999

**Status: VERIFIED**

Repro:
`test_completed_indices_sees_a_four_digit_trial_index`,
`test_attempt_count_sees_a_four_digit_trial_index` (both new). Pre-fix:
`completed_indices()` returned `set()` and `attempt_count(1000)` returned
`0` for a trial actually committed at index 1000. RED confirmed.

Fix (`src/civ_mcp/arena/benchmark_store.py`): `_TRIAL_NAME_RE` and
`_ATTEMPT_NAME_RE` changed from `\d{3}` to `\d{3,}` (three-or-more digits),
matching `{index:03d}`'s actual minimum-width semantics.

Added `test_commit_trial_at_index_1000_is_not_re_executed_or_overwritten`
as a control (this one passed even pre-fix, since `TrialExistsError`'s own
`dest.exists()` path-existence check is independent of the regex bug —
noted in-line in the test).

---

## F15 — Temp-0 greedy sampling structurally fails the seed gate

**Status: VERIFIED**

Repro (new, at both layers):
- `test_probe_backend_records_seed_verdict_not_applicable_at_temperature_zero`
  and `test_admit_model_block_accepts_not_applicable_greedy_seed_verdict` —
  both `AttributeError: 'BackendProbe' object has no attribute
  'seed_verdict'` pre-fix (the field didn't exist yet). RED confirmed.

RULING applied: at `temperature == 0`, seed honoring is unobservable and
irrelevant. Fix:
- `src/civ_mcp/arena/benchmark_backend.py`: `BackendProbe` gained a
  `seed_verdict: str | None = None` field
  (`"honored"`/`"not_honored"`/`"not_applicable_greedy"`/
  `"no_seed_configured"`/`"probe_error"`). `probe_backend` now skips the
  differing-seed call entirely when `sampling.temperature == 0`, recording
  `"not_applicable_greedy"` instead (repeated-consistency is still checked
  beforehand, unchanged).
- `src/civ_mcp/arena/benchmark_gates.py`, `admit_model_block`: the
  `seed_not_honored` check now also passes when
  `probe.seed_verdict == "not_applicable_greedy"`, even though
  `seed_honored` is necessarily `False` there (the check was never run).
  Any other configuration keeps the existing fail-closed behavior
  (`test_admit_model_block_rejects_seed_not_honored`, unchanged, still
  passes).

Updated two pinned `probe_backend` tests whose fakes used the default
`temperature=0.0` while artificially varying output by seed (unrealistic
under real greedy decoding, which is exactly why the original bug wasn't
caught): `test_probe_backend_confirms_model_and_honored_seed` and
`test_probe_backend_detects_unhonored_seed` now use `temperature=0.2` so
they genuinely exercise the differing-seed detection instead of
short-circuiting on the new greedy branch for the wrong reason.

---

## F16 — Wrong-save recovery stray click + false port-drop warning

**Status: VERIFIED** (both sub-parts)

### (a) server.py stray click

Repro: `tests/test_server_auto_boot.py` (new file) —
`test_wrong_save_recovery_skips_stray_click_when_reload_reports_world_ready`
drives `civ_mcp.server._auto_boot` end-to-end with fakes (no live game:
faked `GameConnection`, `heartbeat.write`, `game_launcher._launch_game_sync`
/`_click_text`/`_click_continue_positional`, `civ_mcp.game_lifecycle.
load_game_save`, and `asyncio.sleep` patched to instant) through the
wrong-save recovery branch with a reload result already containing
"world ready". Pre-fix: `click_positional` (the positional-click mock) was
called once. RED confirmed via `git stash` of `server.py` alone
(`AssertionError: Expected 'mock' to not have been called. Called 1
times.`).

Fix (`src/civ_mcp/server.py`): the wrong-save recovery block now checks
`"world ready" in result` after the Lua reload; if present, it just
`await conn.reconnect()`s (the frontend-Lua engaged path already carried
the load to a playable world, so there's no leader-screen intermission
left to click through). The old sleep+positional-click+reconnect-loop
sequence is kept only in the `else` branch, for the legacy quick-return
path.

Added `test_wrong_save_recovery_still_clicks_when_reload_does_not_report_world_ready`
as a control confirming the legacy path is unaffected.

### (b) game_launcher.py false port-drop warning

Repro: `test_continue_after_lua_load_treats_a_stable_open_port_as_world_ready`
(new, in `tests/test_game_launcher.py`) — a port that reports open for
every sample (simulating a drop-then-reopen cycle faster than the poll
interval). Pre-fix: returned the `"WARNING: FireTuner port never
dropped..."` string. RED confirmed.

Fix (`src/civ_mcp/game_launcher.py`, `continue_after_lua_load`): if
`engage_polls` elapses without observing a drop, the function now
re-checks the port once more after a short delay; a STABLE open port
(both checks `True`) is treated as world-ready success. The WARNING still
fires when the port is not actually settled open.

Updated the pinned test `test_continue_after_lua_load_warns_when_load_never_engages`
(renamed docstring, same test name) to a genuine non-readiness fixture (a
port sequence that flickers closed again on the stability recheck) — its
old "port always open from time zero" fixture is now exactly the
false-positive case F16(b) corrects, so a straight re-run of the same
fixture would no longer discriminate the WARNING path at all.

---

## F17 — Foreign TimeoutError misclassified as episode wall

**Status: VERIFIED**

Repro: `test_foreign_timeout_error_propagates_raw_not_as_episode_timeout`
(new) — an injected `capture_state` raises a bare `TimeoutError` well
within the 5s `episode_wall_s` budget. Pre-fix: `agent.run()` raised
`EpisodeTimedOut` instead of the raw `TimeoutError`. RED confirmed.

Fix (`src/civ_mcp/arena/benchmark_agent.py`, `SingleTurnAgent.run`): the
`asyncio.timeout(...)` context manager is now held via `as cm`; the
`except TimeoutError` handler checks `cm.expired()` and re-raises
unchanged when `False` (a foreign TimeoutError), converting to
`EpisodeTimedOut` only when `True` (the wall genuinely fired).

Added `test_genuine_episode_wall_expiry_still_converts_to_episode_timed_out`
as a counterfactual (a hanging backend against a 0.05s wall) confirming
the fix doesn't break genuine wall-clock expiry.

---

## Cheap fold-ins

Both addressed (not deferred to Plan 2):

1. **state_digest ensure_ascii drift vs fingerprint.** Repro:
   `test_state_digest_uses_ensure_ascii_false_matching_other_canonical_encoders`
   (new) — a state with a non-ASCII city name digested differently via
   `state_digest` than via an independently-derived
   `json.dumps(..., ensure_ascii=False)` hash. RED confirmed. Fix: added
   `ensure_ascii=False` to `state_digest`'s `json.dumps` call, matching
   `benchmark_manifest.fingerprint` / `benchmark_report._canonical_bytes` /
   `benchmark_store._canonical_bytes` (all three already use it). Did not
   extract a single shared cross-module helper — the four call sites live
   in three different modules with no existing shared utility module for
   this, and the fold-in explicitly allows a per-site fix ("one shared
   helper **if easy**"); unifying the literal `ensure_ascii=False` argument
   is the minimal fix that actually closes the drift.

2. **benchmark_runner resume: re-verify schedule.json against
   schedule_fingerprint on open.** Repro:
   `test_run_async_fails_closed_on_a_tampered_schedule_json_on_resume`
   (new) — runs `_run_async` once (writes `schedule.json`), overwrites
   `schedule.json` with mismatched content, runs `_run_async` again against
   the same run dir. Pre-fix: second run returned exit code `0` (silently
   trusted the tampered file). RED confirmed. Fix
   (`src/civ_mcp/arena/benchmark_runner.py`, `_run_async`): on resume (the
   `else` branch of "schedule.json already exists"), re-reads the file,
   recomputes its fingerprint, and refuses (`exit code 1`, message to
   stderr) if it disagrees with `lock["schedule_fingerprint"]`.

---

## Out of scope (recorded, not fixed, per the brief)

Lock fingerprints tool names not schemas; boot-health when Profile.csv
absent; YAML-null manifest TypeErrors; `baseline_offset` default 0 in
`benchmark_deploy`. None of these were touched.

## Not reproducible / blocked

None. All 17 findings plus both fold-ins reproduced as failing tests and
were fixed.

---

## Commits (in order)

1. `824762d` — F2 + F10 (gates: canonical rubric score field, real commits)
2. `63db607` — F1 (per-model/per-arm position + aggregate scoring)
3. `e5d05da` — F9 + F11 (malformed-call double-count, hex distance)
4. `2fa4ccb` — F3 + F4 + F5 + F6 + F7 (runner seed binding, real GameState,
   failure classification) — grouped together rather than split F3+F4 /
   F5+F6+F7 per the suggested grouping, because all five fixes landed as
   overlapping edits within the same `run_trial`/`_handle_timeout_like`
   state-machine functions in one file; splitting them after the fact would
   have meant re-editing already-committed lines rather than a clean
   line-range split. Noted here as a deliberate deviation from the
   suggested grouping.
5. `f481f23` — F8 (session provenance stamp + verification)
6. `4cd3f44` — F12 + F13 (numeric digest canonicalization, truncated-state
   fail-closed)
7. `a6cffc5` — F14 (store filename regex >= 1000)
8. `2744763` — F15 (temp-0 greedy seed gate)
9. `8a36873` — F16 (wrong-save recovery click + false port-drop warning)
10. `2c89d86` — F17 (foreign TimeoutError vs genuine episode wall)
11. `6185885` — cheap fold-ins (ensure_ascii unification, schedule.json
    resume verification)

## Full-suite verification

`uv run pytest -q -p no:cacheprovider` (full repo, not just `tests/arena/`):
**2502 passed** in 133.48s. (Starting point was 2460 green at HEAD
`f4061a2`; the increase reflects new regression tests added across the 17
findings + 2 fold-ins.)

Byte-identical report regeneration re-verified directly:
`tests/arena/test_benchmark_report.py::test_write_reports_produces_canonical_json_and_markdown`,
`::test_regenerating_the_same_run_is_byte_identical`, and
`::test_regeneration_with_a_missing_position_is_still_byte_identical` all
pass unmodified against the new grouped report shape.
