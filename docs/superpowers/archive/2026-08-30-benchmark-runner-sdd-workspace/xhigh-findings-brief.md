# xhigh review fix wave — findings brief

Branch worktree-benchmark-runner @ f4061a2 (2460 tests green). An external xhigh multi-agent review confirmed the findings below. For EACH finding: first write a failing test that reproduces it (that test IS the verification); if you cannot reproduce a finding, do NOT fix it — record it as NOT-REPRODUCIBLE in your report with evidence and move on. Fix in focused commits, grouped sensibly.

## P1 findings

### F1 — Per-model/per-arm position aggregation (benchmark_report.py:262-265)
Position summaries pool every trial for a position into one median across models AND arms, so a model screen produces no per-model position score and A/B arms are mixed.
Fix: group scores by (model, arm) within each position; report per-position × per-model × per-arm medians; the equal-weight aggregate and worst-position stats must be computed per model/arm group (keep a combined view only if clearly labeled). Preserve byte-identical regeneration and existing single-model fixtures (update tests as needed).

### F2 — Gates rubric shape incompatible with report (benchmark_gates.py:152-157, also line ~156 check)
check_treatment_can_fire treats rubric "levels" as ints (`any(level in (1, 2))`), but the canonical rubric shape (benchmark_report._validate_rubric_shape, and what build_session_lock stamps into the lock) is a list of {"score": int, "predicate": {...}} mappings. Report-valid rubrics make the gate pass vacuously (fail-open); gates-valid int rubrics make every report abort.
RULING: the report's {score, predicate} mapping shape is canonical (this was a plan-internal defect — the plan's T10 fixture used ints). Fix the gate to read each level's "score" field; accept ONLY the mapping shape (fail closed on bare ints with a clear GateFailure); update the gates tests pinned to ints.

### F3 — Scheduled seed never bound to the backend (benchmark_runner.py:552-559)
backend_for() caches one backend per model built with static suite.sampling; TrialSpec.seed is never applied — trials record seeds that were never sent, invalidating paired-seed evidence.
Fix: bind spec.seed per trial — e.g. dataclasses.replace(suite.sampling, seed=spec.seed) on a per-trial backend or set per-request sampling. Must hold for the live wiring path; test asserts the backend used for a trial carries that trial's seed (and two trials with different seeds get different bound seeds).

### F4 — Live wiring hands the agent a SimpleNamespace, not GameState (benchmark_runner.py:295 / _build_live_dependencies)
Registry tools call GameState methods (gs.get_units() etc.); SimpleNamespace(conn=...) makes every dispatched game tool raise AttributeError in a live session (swallowed into ERROR steps — trials commit as evidence of a model that "couldn't act"). Arena builds GameState(conn) (see src/civ_mcp/arena/arena.py:~357).
Fix: construct a real GameState(conn) in the live wiring. Test: assert the object passed to SingleTurnAgent.run in live wiring is a GameState (or exposes the registry-required methods).

## P2 / confirmed findings

### F5 — Popup error statuses ignored (benchmark_runner.py:257)
dismiss_blocking_popups never raises; failures come back as "err"/"?" strings. The runner ignores the return value, so failed popup hygiene proceeds into the trial instead of an infra attempt (and FailureClass.POPUP_HYGIENE is currently unreachable).
Fix: inspect the returned status; "err"/"?"/unrecognized → record a POPUP_HYGIENE infrastructure attempt (typed branch). Test both success and err paths.

### F6 — reload failures reported as strings are treated as success (benchmark_runner.py:564)
load_game_save reports most failures as strings ("Error: ...", "WARNING: FireTuner port never dropped...", menu-fallback text) rather than raising; reload_position discards the return value, so a failed reload proceeds to checksum and kills the session as checksum_mismatch instead of a retryable RELOAD_OR_RECONNECT infra attempt.
Fix: inspect the returned string in the live reload wiring; strings indicating failure/warning → raise a typed error the runner classifies as RELOAD_OR_RECONNECT. Success strings ("world ready", "Loaded ...") pass. Journal the raw string either way. Test with representative strings.

### F7 — Partial evidence lost on request timeouts (benchmark_runner.py:403-410 + benchmark_agent.py)
Only EpisodeTimedOut carries partial_evidence; a mid-episode openai.APITimeoutError commits steps=[] even though earlier steps executed real mutations (ruling-13 contract violated on this path). Note the scaffold's episode_wall 300.0 == backends.REQUEST_TIMEOUT_S race.
Fix: expose the agent's accumulated progress (e.g. a `partial_evidence()` accessor over the same instance-level _progress_* state, reset per run()); the runner's timeout-like handler reads it for APITimeoutError/APIConnectionError paths too. Test: request-timeout mid-episode commits pre-timeout steps.

### F8 — Trials lack session provenance (benchmark_runner.py:452-459)
Trial payloads omit session_fingerprint; resume identifies completion by filename only, so a stale/copied trial-NNN.json is indistinguishable from current-lock evidence.
Fix: stamp store.fingerprint into every committed payload; on resume-skip and in build_report, validate the stamp matches (mismatch → fail closed with a clear error, not silent skip). Tests for stamp presence and mismatch refusal.

### F9 — Malformed calls double-counted as domain rejections (action_metrics.py:52-54 + benchmark_agent.py)
The "ERROR: malformed arguments" step result classifies as domain_rejection while the same call is in invalid_tool_calls — inflating both, though the call never reached game rules.
RULING: change the step's tool_result_full to a string that does NOT classify as domain_rejection (e.g. "MALFORMED_ARGUMENTS: not dispatched") — the model-facing tool message can stay descriptive; classifier unchanged. Test: malformed-args step yields domain_rejections == 0 while invalid count is 1.

### F10 — Absent commit evidence passes the clean-checkout gate (benchmark_gates.py:105)
wsl_commit == windows_commit passes when both are None/empty — a session can admit with no code revision recorded.
Fix: each commit must be a non-empty string (plausibly hex-ish) before comparison; otherwise GateFailure. Test.

### F11 — Manhattan distance on a hex grid (action_metrics.py:100)
_unit_distance uses |dx|+|dy| on offset hex coordinates; unit_distance_decreased mis-scores real progress (counterexample: (5,5)→(6,6) toward (5,8): hex distance 3→2, Manhattan 3→3).
Fix: implement proper offset→cube conversion hex distance (Civ6 uses odd-r/even-r offset — check the repo's existing hex helpers first, e.g. any distance util in game_state/lua helpers, and reuse if one exists). Test with the counterexample and a straight-line case.

### F12 — state_digest numeric-type sensitivity + no diff on mismatch (benchmark_state.py:68 + runner journal)
json.dumps distinguishes 24 vs 24.0, so a hand-authored YAML int mismatches a captured float forever; the runner's mismatch branch journals two opaque hashes and never uses diff_state.
Fix: normalize_state coerces numerics canonically (e.g. floats that are integral → int, or format all numbers via one rule) so semantically equal states digest equal; runner's checksum-mismatch journal includes diff_state(expected, captured) output. Tests: int-vs-float equal digest; mismatch journal carries a field-level diff.

### F13 — Truncated FireTuner responses hashed as real state (benchmark_state.py:138 + lua/benchmark.py:~120)
execute_read swallows read timeouts and returns collected lines; a truncated/empty response without an ERR line parses to the near-empty default state and gets hashed → session-killing checksum abort for a retryable harness failure. Related: a truncated response can leave the parsed state half-mutated (identity set, turn/player/gold left None/0.0).
Fix: parse is all-or-nothing — capture_canonical_state raises BenchmarkStateError unless the response contains the complete required rows (identity row with turn/player populated; treat missing/None core fields as incomplete). The runner already classifies BenchmarkStateError as an infra attempt (HARNESS_CRASH) — assert that path in a test.

### F14 — Store filename regexes blind above index 999 (benchmark_store.py:59)
`trial-(\d{3})\.json` vs `{index:03d}` formatting: index 1000 writes 4 digits, completed_indices()/attempt_count() miss it → re-execution then TrialExistsError crash; attempt cap disabled.
Fix: `\d{3,}` in both regexes. Test at index 1000.

### F15 — temp-0 greedy sampling structurally fails the seed gate (benchmark_backend.py:117 + admit_model_block)
Under temperature=0 the differing-seed probe returns identical output, seed_honored is always False, and admit_model_block refuses every temp-0+seed config.
RULING: at temperature == 0, seed honoring is unobservable and irrelevant (greedy decoding). probe_backend records seed_verdict "not_applicable_greedy" when sampling.temperature == 0 (repeated-consistency must still hold); admit_model_block accepts that verdict. Any other config keeps the current fail-closed behavior. Tests for both.

### F16 — Wrong-save recovery stray click + false port-drop warning (game_lifecycle.py:608 + src/civ_mcp/server.py:~225-229 + game_launcher.continue_after_lua_load)
Since T2, the Tier-1 engaged path blocks until in-world; server.py's wrong-save recovery still sleeps then fires _click_continue_positional(), which now lands as a stray click in the loaded world. Separately, GameConnection auto-reconnect can make continue_after_lua_load wait for a port drop that already happened, returning a false "port never dropped" WARNING on a successful load.
Fix (a): remove/guard the recovery's positional click when load_game_save has already returned an in-world result (inspect server.py's recovery block; the click is only needed for the legacy quick-return path, which no longer exists).
Fix (b): in continue_after_lua_load, treat a verified-open port + world-ready check as success even if the drop wasn't observed (or verify world readiness instead of insisting on observing the drop). Keep the WARNING only when the world is genuinely not ready. Test with a fast-reload fake.

### F17 — Foreign TimeoutError misclassified as episode wall (benchmark_agent.py:244)
`except TimeoutError` after asyncio.timeout catches ANY builtin TimeoutError from inside (socket.timeout is TimeoutError), e.g. OS ETIMEDOUT from capture_state — misconverted to EpisodeTimedOut (scoreable-candidate) instead of propagating as a harness failure.
Fix: hold the asyncio.timeout context manager (`with asyncio.timeout(...) as cm`... async with) and re-raise when `not cm.expired()`. Test: a capture_state raising TimeoutError propagates raw (not EpisodeTimedOut); genuine wall expiry still converts.

## Cheap fold-ins (fix if trivial while in the file; otherwise note for Plan 2)
- state_digest ensure_ascii drift vs fingerprint (unify canonical JSON encoding — one shared helper if easy).
- benchmark_runner resume: re-verify schedule.json content against the lock's schedule_fingerprint on open.

## Out of scope (record only, do NOT fix): lock fingerprints tool names not schemas; boot-health when Profile.csv absent; YAML-null manifest TypeErrors; baseline_offset default 0 in benchmark_deploy.
