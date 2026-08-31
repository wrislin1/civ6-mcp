# xhigh re-review round 2 — fix wave report

Branch `worktree-benchmark-runner`, base `3a9ffcc` (2502 tests green). Final: **2552 tests green** (50 net new tests across 15 findings). Strict TDD throughout: each finding's fix was preceded by a failing test observed to fail for the stated reason, then a minimal fix, then green.

---

## G1 — Scoring inversion: never-dispatched calls classify as "success"

**RED**: Added `test_classify_result_unavailable_is_not_dispatched`,
`test_classifier_unavailable_and_malformed_steps_never_count_as_success`,
`test_predicate_successful_tool_call_false_when_result_is_unavailable` to
`tests/arena/test_action_metrics.py`. Before the fix, `classify_result("UNAVAILABLE: ...")` returned `"success"`, and `evaluate_predicate`'s `successful_tool_call` returned `True` for a spoofed UNAVAILABLE result with matching tool/args (`assert True is False` failure).

**Fix**: `classify_result` (`src/civ_mcp/arena/action_metrics.py`) gained a third class `"not_dispatched"` for `unavailable`/`malformed_arguments` prefixes (case-insensitive), checked before the `error`/`|blocked` domain-rejection check. `successful_tool_call` and `classify_action_quality`'s success gate already compared for the literal `"success"` string, so they were automatically fixed by this one change.

**GREEN**: `tests/arena/test_action_metrics.py` — 35 passed.

---

## G2 — Tier-2 reload success strings classified as failure

**RED**: `test_reload_result_is_success_recognizes_every_real_success_shape` (parametrized over Tier-0/1, F16(b) UNVERIFIED fallback, Tier-2 `_navigate_to_save_sync`, and `restart_and_load`'s compound string) failed on the two Tier-2 shapes — `_reload_result_is_success` only recognized `"Loaded "` prefix / `"world ready"`.

**Fix**: `_reload_result_is_success` (`src/civ_mcp/arena/benchmark_runner.py`) now recognizes `"Save loading ("` as a third success marker, AND requires the absence of any of `"FAILED"`, `"ABORTED"`, `"WARNING:"`, `"Error:"`, `"not found"` anywhere in the string (so a failure marker buried in a compound Kill/Launch/Load string still wins).

**GREEN**: `tests/arena/test_benchmark_runner.py` — both parametrized groups pass; `test_reload_result_is_success_stays_fail_closed_on_failure_strings` (including compound-string failure-marker cases) passes.

---

## G3 — Inert-load ambiguity: launcher claims "world ready" it cannot verify

**RED**:
- `test_continue_after_lua_load_treats_a_stable_open_port_as_world_ready` extended with `assert "UNVERIFIED" in result` — failed (marker didn't exist yet).
- `test_checksum_mismatch_after_unverified_reload_is_infra_attempt_not_abort`, `test_checksum_mismatch_after_verified_reload_still_aborts`, `test_unverified_reload_with_matching_checksum_proceeds_normally`, `test_reload_position_non_bool_return_fails_closed_as_infra_attempt`, `test_live_reload_position_reports_unverified_for_stable_open_port_fallback` — all failed against the old `Awaitable[None]` contract (`SessionAborted` raised instead of a retryable infra attempt; `assert None is True` on the live-wiring return).

**Fix**:
- `game_launcher.continue_after_lua_load`'s F16(b) stable-open-port fallback now returns `"Loaded {name} (UNVERIFIED: port drop not observed ...): world ready, ..."` — still success-shaped (starts with `"Loaded "`, contains `"world ready"`, no `WARNING`) but distinguishable.
- `RunnerDependencies.reload_position` contract changed to `Callable[[str], Awaitable[bool]]` (returns `verified`). Live wiring's `reload_position` returns `verified = "UNVERIFIED" not in text` on success.
- `run_trial` validates the return is an actual `bool` (fails closed as a `RELOAD_OR_RECONNECT` infra attempt on `None`/non-bool — no silent verified=True).
- On checksum mismatch: if the preceding reload was unverified, records a `RELOAD_OR_RECONNECT` infra attempt (journal: "checksum mismatch after unverified reload -- retrying reload") and returns instead of aborting; a verified reload followed by a mismatch still raises `SessionAborted` as before.
- `server.py`'s wrong-save recovery `if "world ready" in result:` substring check already treats the UNVERIFIED string as reconnect-only (the marker text still contains `"world ready"`) — verified by a new test, no code change needed there.
- Every fake `reload_position` across `tests/arena/test_benchmark_runner.py` updated to return `True`/`False` per the new contract (the `_deps()` default now returns `True`).

**GREEN**: `tests/arena/test_benchmark_runner.py` (61 passed), `tests/test_game_launcher.py`, `tests/test_server_auto_boot.py`, `tests/test_game_lifecycle_load.py` — all green.

---

## G4 — Agent construction outside classification

**RED**: `test_make_agent_construction_failure_is_a_harness_crash_attempt` — a `RuntimeError` from `make_agent` propagated as a raw, unjournalled exception out of `run_trial` instead of being classified.

**Fix**: Wrapped `agent = self._deps.make_agent(spec)` and the following `GameState(...)` construction in a `try/except Exception` that records a `HARNESS_CRASH` infra attempt (`src/civ_mcp/arena/benchmark_runner.py`).

**GREEN**: `tests/arena/test_benchmark_runner.py` — 56 passed.

---

## G5 — Resource cleanup in `_run_async`

**RED**: `test_run_async_closes_connection_and_backends_on_normal_exit`, `test_run_async_closes_connection_and_backends_on_exception_exit`, `test_openai_compat_backend_aclose_closes_the_underlying_client` — the first two failed with `TypeError: RunnerDependencies.__init__() got an unexpected keyword argument 'aclose'`; the third failed with `AttributeError: 'OpenAICompatBackend' object has no attribute 'aclose'`.

**Fix**:
- `OpenAICompatBackend.aclose()` added (`src/civ_mcp/arena/backends.py`) — closes the underlying `AsyncOpenAI` client.
- `RunnerDependencies` gained an optional `aclose: Callable[[], Awaitable[None]] | None = None` field.
- `_build_live_dependencies` returns an `aclose` callable that closes every cached per-`(model, seed)` backend.
- `_run_async`'s body wrapped in `try/finally`: the `finally` block calls `deps.aclose()` (if set) then `connection.disconnect()`, unconditionally (normal return, caught `SessionAborted`, or any propagating exception).
- Every `_FakeConnection` fixture across `tests/arena/test_benchmark_runner.py` gained a `disconnect()` stub to satisfy the new unconditional call.

**GREEN**: `tests/arena/test_benchmark_runner.py` (59 passed), `tests/arena/test_backends.py` (10 passed).

---

## G6 — `api_key` placeholder

**RED**: `test_run_async_warns_when_api_key_env_var_is_unset` — `assert "LITELLM_OPENAI_API_KEY" in err` failed against empty stderr.

**Fix** (ruling: do NOT refuse to start): `_run_async` now checks `os.environ.get(args.api_key_env)`; if falsy, prints a one-line warning naming the env var and that placeholder `"x"` is in use, then proceeds with `api_key = "x"`.

**GREEN**: `test_run_async_warns_when_api_key_env_var_is_unset` and `test_run_async_no_warning_when_api_key_env_var_is_set` both pass; full `tests/arena/test_benchmark_runner.py` — 61 passed.

---

## G7 — temp-0 verdict drops repeated-consistency

**RED**: `test_probe_backend_temp_zero_inconsistent_output_is_not_honored` (a fake backend claiming `temperature=0.0` but returning a different reply on every call) and `test_backend_probe_repeated_consistent_defaults_false_for_old_constructors` both failed with `AttributeError: 'BackendProbe' object has no attribute 'repeated_consistent'`.

**Fix**: `BackendProbe` gained `repeated_consistent: bool = False` (`src/civ_mcp/arena/benchmark_backend.py`). `probe_backend` always records it. The temp-0 branch now reports `"not_applicable_greedy"` only when `repeated_consistent` is `True`; otherwise `"not_honored"` (fails admission via the existing `benchmark_gates` check, unchanged there).

**GREEN**: `tests/arena/test_benchmark_backend.py` (12 passed) and `tests/arena/test_benchmark_gates.py` (unaffected, still passing — its fixtures construct `BackendProbe` with an explicit `seed_verdict` and never exercise `probe_backend` directly).

---

## G8 — Provenance fails open on absent fingerprint (two parts)

**Part (a) — `BenchmarkStore`**: RED — `test_create_refuses_a_lock_with_no_session_fingerprint`, `test_create_refuses_a_lock_with_an_empty_session_fingerprint`, `test_open_refuses_a_lock_with_no_session_fingerprint` all failed (`create`/`open` succeeded, or raised the wrong error — `FileNotFoundError` instead of a clear refusal). Fix: `BenchmarkStore._open_or_create` now refuses (raises `BenchmarkStoreError`) any lock whose `session_fingerprint` is missing or empty, before any file I/O. GREEN: `tests/arena/test_benchmark_store.py`.

**Part (b) — `benchmark_report.build_report`**: RED — `test_build_report_fails_closed_on_an_unstamped_trial_under_a_stamped_lock` failed with `DID NOT RAISE`. The old check `lock_fp and trial_fp and trial_fp != lock_fp` was a no-op whenever either side was falsy. Fix: when the lock carries a `session_fingerprint`, a trial with **no** stamp at all is now a hard `ReportError`; a mismatched stamp remains a `ReportError` as before. Updated the canonical-schema comment (`session_fingerprint` documented as required) and stamped every previously-unstamped trial fixture in `tests/arena/test_benchmark_report.py` (`_build_basic_run`, the inline fixture in `test_report_ignores_attempts_and_weights_positions_equally`, `test_build_report_aggregate_is_scoped_per_model_arm_group_not_pooled`, `test_build_report_surfaces_seeds_and_endpoint_topology`, `_calibration_run`) to match their lock's fingerprint. GREEN: `tests/arena/test_benchmark_report.py` — 41 passed at that point (44 after G13's additions).

---

## G9 — `append_event` swallows a `details=` kwarg

**RED**: `test_append_event_details_kwarg_lands_flat_not_nested` failed: `{'details': {'a': 1}} == {'a': 1}` — the old `**details: object` catch-all captured the literal keyword name `"details"` as an entry in itself, so every real caller (all of which pass `details={...}` explicitly) landed double-nested as `record["details"]["details"]`.

**Fix**: `BenchmarkStore.append_event` (`src/civ_mcp/arena/benchmark_store.py`) now takes an explicit `details: Mapping[str, object] | None = None` parameter (no catch-all); `record["details"] = dict(details)` when truthy. Updated the one existing test (`test_checksum_mismatch_journal_includes_a_field_level_diff`) that had pinned the old double-nested shape to the new flat one.

**GREEN**: `tests/arena/test_benchmark_store.py` (25 passed), `tests/arena/test_benchmark_runner.py` (61 passed).

---

## G10 — `expected_state_sha256` declared, never verified

**RED**: `test_run_async_refuses_a_tampered_expected_state_sha256` failed (`assert 0 == 1` — the CLI proceeded despite a manifest whose `expected_state_sha256` was `"0"*64` instead of the real digest of `{"turn": 42}`).

**Fix**: Added `benchmark_state.verify_expected_state_digest(expected_state, expected_state_sha256)` (kept in `benchmark_state.py`, not `benchmark_manifest.py`, to avoid the manifest module importing the state module). `_run_async` calls it immediately after `load_position_manifest` succeeds, before the schedule compiles or any trial runs; a mismatch prints both digests and returns 1. The CLI-path test fixture's YAML now carries the real `state_digest({"turn": 42})`.

**GREEN**: `tests/arena/test_benchmark_runner.py` (`test_run_async_refuses_a_tampered_expected_state_sha256`, `test_run_async_proceeds_when_expected_state_sha256_matches`) and `tests/arena/test_benchmark_state.py` — all pass.

---

## G11 — Report never verifies `schedule.json` against the lock

**RED**: `test_build_report_fails_closed_on_a_tampered_schedule` failed with `DID NOT RAISE` — `build_report` read `schedule.json` with no fingerprint check at all.

**Fix**: `build_report` (`src/civ_mcp/arena/benchmark_report.py`) now imports `fingerprint` from `civ_mcp.arena.benchmark_manifest` (the same helper the runner uses on resume) and, when `session.json` carries a `schedule_fingerprint`, recomputes it over the whole parsed `schedule.json` mapping and hard-fails on a mismatch. Only activates when the lock declares one, so every existing fixture (none of which declare `schedule_fingerprint`) is unaffected.

**GREEN**: `test_build_report_accepts_a_schedule_matching_its_declared_fingerprint`, `test_build_report_fails_closed_on_a_tampered_schedule`, `test_build_report_skips_schedule_verification_when_lock_has_no_schedule_fingerprint` all pass.

---

## G12 — Report duplicates the trial filename convention

**RED**: `test_trial_filename_matches_the_minimum_width_convention` / `test_committed_trial_lands_at_the_path_trial_filename_predicts` failed with `ImportError: cannot import name 'trial_filename'`.

**Fix**: Added `benchmark_store.trial_filename(index) -> str` (the `{index:03d}` minimum-width convention, matching `_TRIAL_NAME_RE`); used it in `BenchmarkStore._trial_path` and in `benchmark_report.build_report` (replacing its private duplicate `f"trial-{int(index):03d}.json"`). Behavior-neutral.

**GREEN**: `tests/arena/test_benchmark_store.py` (27 passed), `tests/arena/test_benchmark_report.py` (unaffected, still green).

---

## G13 — Unknown model priced $0

**RED**: `test_unpriced_model_is_excluded_from_usd_total_and_listed`, `test_priced_model_reports_empty_unpriced_models_list`, `test_unpriced_and_priced_models_in_the_same_group_summary_do_not_mix_costs` all failed with `KeyError: 'unpriced_models'`.

**Fix**: `_usd_cost` (`src/civ_mcp/arena/benchmark_report.py`) now returns `float | None` — `None` when `model` has no entry in `_PRICE_PER_1K_USD` (rather than the old `.get(str(model), (0.0, 0.0))` conflating "free" with "no data"). `_group_summary` aggregates `usd_total`/`usd_mean` over priced trials only and adds an explicit `unpriced_models: [...]` list (empty when everything was priced), surfaced in both `report.json` and the rendered markdown (`- Cost (USD): total=... (UNPRICED, excluded from total: ...)`).

**GREEN**: `tests/arena/test_benchmark_report.py` — 44 passed, including `test_regenerating_the_same_run_is_byte_identical` (byte-identical regeneration preserved).

---

## G14 — Historical records fabricate `successful_mutations: 0`

**RED**: `test_successful_mutations_none_when_no_step_carries_digest_fields` failed: `assert 0 is None` — steps entirely lacking `state_digest_before`/`state_digest_after` keys (the historical-record shape) produced a fabricated `0` instead of `None`.

**Fix**: `classify_action_quality` (`src/civ_mcp/arena/action_metrics.py`) computes `digest_fields_present` (`True` when any step carries either digest key, or trivially `True` for an empty step list) and reports `successful_mutations`/`repetitions`/`loop_excess` as `None` when digest fields are entirely absent, mirroring the existing `useful_actions: None` pattern. `analyze.py`'s aggregation (`scripts/analyze.py` is unaffected; `src/civ_mcp/arena/analyze.py`) tracks availability the same way `useful_actions` already does, summing only non-`None` per-record values. Updated the one existing `test_analyze.py` assertion (`test_action_quality_attached_to_played_series_rows`) that had pinned the old fabricated-`0` behavior, plus its aggregate counterpart, and added a positive test confirming benchmark-agent-style digest-carrying steps keep real counts.

**GREEN**: `tests/arena/test_action_metrics.py` (35 passed), `tests/arena/test_analyze.py` (98 passed).

---

## G15 — Lua IDENTITY parse swallowed whole

**RED**: `test_parse_benchmark_state_raises_on_a_corrupt_identity_row` and `test_capture_canonical_state_names_a_corrupt_identity_row_not_truncation` both failed — the latter raised `BenchmarkStateError("incomplete or truncated benchmark-state response: missing identity row ...")` instead of naming the row as corrupt.

**Fix**: Added `CorruptIdentityRow` exception (`src/civ_mcp/lua/benchmark.py`), raised only from the IDENTITY branch on a coercion failure (restructured so the IDENTITY branch has its own `try/except` outside the generic per-line `try/except (ValueError, IndexError): continue` that still covers UNIT/CITY/TILE). Re-exported via `civ_mcp/lua/__init__.py`. `benchmark_state.capture_canonical_state` catches `lq.CorruptIdentityRow` and raises `BenchmarkStateError("corrupt IDENTITY row in benchmark-state response: ...")` instead of falling through to the generic truncation check.

**GREEN**: `tests/arena/test_benchmark_state.py` — 19 passed, including a regression pin (`test_parse_benchmark_state_still_skips_corrupt_unit_rows_after_a_good_identity_row`) confirming other row types keep their skip-on-corrupt behavior.

---

## Out of scope (recorded, not fixed)

Per the brief: lock fingerprints tool names not schemas; boot-health when `Profile.csv` absent; YAML-null manifest `TypeError`s; `baseline_offset` default `0` in `benchmark_deploy`.

---

## Commits

1. `93099bb` — fix(arena): stop scoring never-dispatched calls as successes, fabricating 0 mutations (G1, G14)
2. `a646ce2` — fix(arena): recognize real Tier-2 reload successes, mark unverifiable reloads distinctly (G2, G3)
3. `784fc1c` — fix(arena): classify agent/GameState construction failures as harness crashes (G4)
4. `3d2e39c` — fix(arena): close game connection and cached backend clients on every exit path (G5)
5. `4808a5f` — fix(arena): warn on an unset --api-key-env instead of running silently (G6)
6. `28519bb` — fix(arena): admit a temp-0 backend only when it's actually deterministic (G7)
7. `4eaee3f` — fix(arena): refuse a fingerprint-less session lock, flatten append_event details (G8, G9)
8. `35be6a3` — fix(arena): verify expected_state_sha256 at startup instead of never reading it again (G10)
9. `23bbdf7` — fix(arena): report hard-fails on an unstamped trial under a stamped lock (G8 continued)
10. `003ac95` — fix(arena): verify schedule.json, share the trial filename convention, stop pricing unknown models $0 (G11, G12, G13)
11. `89e1d1c` — fix(arena): a corrupt IDENTITY row must not be misreported as a truncated response (G15)

## Final full-suite result

`uv run pytest -q -p no:cacheprovider` (foreground): **2552 passed** (up from the 2502-test baseline at `3a9ffcc`; 50 net new tests across the 15 findings).
