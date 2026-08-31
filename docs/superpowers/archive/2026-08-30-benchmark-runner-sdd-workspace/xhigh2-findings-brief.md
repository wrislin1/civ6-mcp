# xhigh re-review round 2 — fix brief (15 findings, all controller-verified)

Branch worktree-benchmark-runner @ 3a9ffcc (2502 tests green). Every finding below was verified against the code by the controller — none are speculative. For EACH: write a failing test first (RED), then fix (GREEN). Focused commits, grouped sensibly. Run tests in the foreground only — never via background monitors. Do not dispatch subagents.

## G1 — Scoring inversion: never-dispatched calls classify as "success" (action_metrics.py:36-55)
`classify_result` returns "success" for anything not starting "error"/containing "|blocked" — including the agent's "UNAVAILABLE: ..." and "MALFORMED_ARGUMENTS: not dispatched" strings. A `successful_tool_call` predicate (line ~157) then scores a never-dispatched call as a success — a minimal-arm model calling an out-of-tier tool satisfies a treatment-only rubric level. This flips the A/B comparison.
Fix: add a third class "not_dispatched" for prefixes "unavailable" / "malformed_arguments" (case-insensitive, matching the actual emitted strings). `successful_tool_call` requires "success". Domain-rejection counting unchanged (not_dispatched is neither). `classify_action_quality`'s success gate uses the same three-way result.
Tests: UNAVAILABLE and MALFORMED steps yield successful_tool_call False, domain_rejections 0, successful_mutations 0 even with a (spoofed) digest change.

## G2 — Tier-2 reload success strings classified as failure (benchmark_runner.py:610-617)
`_reload_result_is_success` accepts only "Loaded " prefix / "world ready". Real Tier-2 successes: `_navigate_to_save_sync` returns "Save loading (Ns). Steps: ...", and `restart_and_load` returns "Kill: ... | Launch: ... | Load: Save loading (...)". Both currently classify as failure → a working OCR/menu reload burns all 3 infra attempts and aborts the session.
Fix: success = (startswith "Loaded " OR contains "world ready" OR contains "Save loading (") AND contains none of "FAILED", "ABORTED", "WARNING:", "Error:", "not found". Keep fail-closed default for unrecognized text. Tests with every representative string from game_launcher/game_lifecycle (both success tiers, restart_and_load compound success, each failure string).

## G3 — Inert-load ambiguity: launcher claims "world ready" it cannot verify (game_launcher.py:2057-2076 + runner)
The F16(b) stable-open-port fallback returns "Loaded ...: world ready ... (drop was not observed ...)" — but a stable open port is exactly what an inert Network.LoadGame looks like. The launcher structurally cannot discriminate (no game connection); only the runner's checksum step can.
RULING (controller): keep the fallback returning success-shaped text but make it distinguishable — change that one return to start with "Loaded ... (UNVERIFIED: port drop not observed ...)" retaining neither plain parity with the verified path nor a WARNING. Then:
- `reload_position` in the live wiring detects the UNVERIFIED marker and reports it: change `RunnerDependencies.reload_position` contract from `Awaitable[None]` to `Awaitable[bool]` returning `verified` (True for observed-drop/Tier-2 paths, False for the UNVERIFIED marker). Update fakes.
- `run_trial`'s checksum-mismatch branch: if the immediately preceding reload was unverified, classify as a RELOAD_OR_RECONNECT infrastructure attempt (retryable, journal says "checksum mismatch after unverified reload — retrying reload") instead of SessionAborted("checksum_mismatch"). A verified reload followed by mismatch still aborts as today.
- server.py wrong-save recovery: its "world ready" check must also treat the UNVERIFIED string as reconnect-only (no positional click). Verify by reading the branch; add/adjust test.
Tests: unverified reload + mismatch → infra attempt; verified reload + mismatch → session abort; unverified reload + matching checksum → trial proceeds normally.

## G4 — Agent construction outside classification (benchmark_runner.py:342)
`agent = self._deps.make_agent(spec)` (and the GameState construction after it) sit outside every try — a construction failure escapes as a raw traceback, nothing journalled. Fix: bring construction inside the harness-failure classification (HARNESS_CRASH infra attempt, journalled). Test: make_agent raising → journalled infra attempt, not raw escape.

## G5 — Resource cleanup in _run_async (benchmark_runner.py:836+)
GameConnection is connected and per-(model,seed) AsyncOpenAI clients are cached with no close on any exit path. Fix: try/finally in `_run_async` closing the connection (use its real close/disconnect method — check GameConnection's API) and closing every cached backend client (AsyncOpenAI has `.close()`; expose a close hook from `_build_live_dependencies`, e.g. return deps plus an async cleanup callable, or attach `aclose()` to RunnerDependencies). Test: cleanup runs on both normal exit and exception.

## G6 — api_key placeholder (benchmark_runner.py:839)
RULING (controller): do NOT refuse to start — local gateways need no key and `probe_backend` already fails closed on a bad key against real endpoints. Fix: when `os.environ.get(args.api_key_env)` is unset, log/print a clear one-line warning naming the env var and that placeholder "x" is in use (fine for local gateways; remote endpoints will fail the admission probe). Test: warning emitted when unset, absent when set.

## G7 — temp-0 verdict drops repeated-consistency (benchmark_backend.py:117-134)
At temperature==0 the verdict is "not_applicable_greedy" unconditionally; `repeated_consistent` is computed but BackendProbe has no field for it and admission never sees it — a non-deterministic "greedy" backend admits. Fix: add `repeated_consistent: bool` to BackendProbe (default False for old constructors); temp-0 verdict becomes "not_applicable_greedy" only when repeated_consistent, else "not_honored" (fails admission as today for inconsistent outputs). Record repeated_consistent in the probe result regardless of path. Tests: temp-0 consistent → admits; temp-0 inconsistent → refuses.

## G8 — Provenance fails open on absent fingerprint (benchmark_store.py:141 + report 617-631)
`from_dir` accepts a lock with no `session_fingerprint` (fingerprint=None): runner resume check passes None==None for unstamped stale trials, and the report cross-check (`lock_fp and trial_fp and ...`) is a no-op when either is missing. Fix: (a) `BenchmarkStore.from_dir` (and any open path) refuses a lock whose `session_fingerprint` is missing/empty — clear error; (b) report: when the lock carries a fingerprint, a trial with a missing stamp is a hard ReportError (not silently passed); update the canonical-schema comment (line ~544) — `session_fingerprint` is required in session.json. Update any fixtures that omitted it. Tests: fingerprint-less lock refused; unstamped trial under stamped lock → ReportError.

## G9 — append_event swallows a `details=` kwarg (benchmark_store.py:145-153)
`**details` catch-all means a caller passing `details={...}` nests as details.details. No current caller does, but the API invites it. Fix: accept an explicit `details: Mapping | None = None` parameter merged with (or replacing) the catch-all — pick one clean signature, adjust callers, test that `append_event("x", details={"a": 1})` lands flat.

## G10 — expected_state_sha256 declared, never verified (benchmark_manifest.py:35,112)
The required manifest field is loaded and never read again — the runner digests `position.expected_state` directly, so the advertised integrity anchor doesn't exist. Fix: verify at load/startup — `state_digest(normalize_state(position.expected_state))` must equal `expected_state_sha256`, else fail closed with a clear error naming both digests. Place the check where manifest and state modules meet cleanly (a small helper in benchmark_state consumed by the CLI/runner init is fine; avoid making benchmark_manifest import benchmark_state if that feels circular — controller has no preference beyond fail-closed before any trial runs). Update fixtures to carry correct hashes. Test: tampered expected_state (or stale hash) refuses to run.

## G11 — Report never verifies schedule.json against the lock (benchmark_report.py:580)
The runner re-verifies schedule_fingerprint on resume; build_report reads schedule.json unverified — a swapped arm order silently flips median_signed_delta. Fix: when session.json carries `schedule_fingerprint`, recompute the schedule's fingerprint exactly as the runner does (reuse the same helper — extract to a shared location if it's currently CLI-local) and hard-fail on mismatch. Test: tampered schedule.json → ReportError.

## G12 — Report duplicates the trial filename convention (benchmark_report.py:605)
Reuse the store's naming (expose a module-level `trial_filename(index)`/equivalent from benchmark_store and use it in the report) so the `\d{3,}`/`03d` convention lives in one place. Behavior-neutral refactor; existing tests must stay green.

## G13 — Unknown model priced $0 (benchmark_report.py:255-259)
`_PRICE_PER_1K_USD.get(str(model), (0.0, 0.0))` conflates "free" with "no price data". Fix: unknown model → cost None; aggregate USD totals over priced trials only and add an explicit `unpriced_models: [...]` list (empty when all priced) so a $0 total is never silently wrong. Preserve byte-identical regeneration property (regenerating the same run twice). Tests: unknown model appears in unpriced_models and contributes no $0 line.

## G14 — Historical records fabricate successful_mutations: 0 (analyze.py:1231 + action_metrics.py:238-240)
`successful_mutations` requires per-step state digests (`state_digest_before/after`) that historical arena records never carry — None != None → False → a reported 0 that is not a measurement. Fix: in `classify_action_quality`, when NO step carries the digest fields, report `successful_mutations: None` (and any other digest-dependent count, e.g. repetitions if it keys on digests — check `_call_key`) mirroring the existing useful_actions: None pattern; analyze.py aggregates them as unavailable (same as useful_actions handling). Benchmark-agent steps (which do carry digests) keep real counts. Tests: digest-less steps → None; digest-carrying steps → real count.

## G15 — Lua IDENTITY parse swallowed whole (lua/benchmark.py:~157)
The per-line `except (ValueError, IndexError): continue` covers the IDENTITY branch too: one unparseable field (e.g. corrupt turn int) silently discards turn/player_id/civ_type, and the resulting incomplete state is misreported by capture_canonical_state as a truncated response. Fix: the IDENTITY branch must not be silently swallowed — on a coercion failure there, raise a distinct error (or mark the state corrupt) so capture_canonical_state's BenchmarkStateError names a corrupt IDENTITY row rather than truncation. Other row types (CITY/TILE/...) may keep skip-on-corrupt behavior. Test: IDENTITY row with a non-numeric turn → BenchmarkStateError mentioning the corrupt row (still fail-closed/infra-classified).

## Invariants that must hold at the end
- Full suite green (`uv run pytest -q -p no:cacheprovider`), foreground.
- Byte-identical report regeneration preserved.
- All fail-closed contracts stay fail-closed; no new fail-open defaults.
- Out of scope (record only, do NOT fix): lock fingerprints tool names not schemas; boot-health when Profile.csv absent; YAML-null manifest TypeErrors; baseline_offset default 0 in benchmark_deploy.

Report file: write your full report to `.superpowers/sdd/2026-08-30-arena-controlled-position-benchmark-runner/xhigh2-fix-report.md` (per-finding RED evidence → fix → GREEN, commits, final suite count). Return only: status, commit list, one-line test summary, concerns.
