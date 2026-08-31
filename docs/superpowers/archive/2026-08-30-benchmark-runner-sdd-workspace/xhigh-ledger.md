# xhigh review wave — controller rulings (post-SDD, branch worktree-benchmark-runner)

Ruling: F2 rubric shape — the report's {score, predicate} mapping level shape is canonical; gates read level["score"] and fail closed on bare-int levels. The plan's own T10 fixture (levels as ints) was a plan defect. — Cost if wrong: gate rework; report consumers unaffected.
Ruling: F9 malformed-call metric — step result string changed to a non-"error"-prefixed marker (MALFORMED_ARGUMENTS) so the shared classifier counts it only in invalid_tool_calls, never as a domain rejection. — Cost if wrong: metric vocabulary tweak later.
Ruling: F15 temp-0 seed gate — at temperature 0 seed honoring is unobservable; probe records verdict "not_applicable_greedy" (repeated-consistency still required) and admit_model_block accepts it; all other configs keep fail-closed seed gating. Matches the spec's seed-probe degradation path. — Cost if wrong: an endpoint silently ignoring seeds at temp>0 is still caught; temp-0 runs rely on repetition-consistency alone.
Ruling: F13 truncated capture — capture_canonical_state parse is all-or-nothing; missing identity/core rows raise BenchmarkStateError (infra attempt), never hash a default state.
Ruling: F1 aggregation — per-position scores grouped by (model, arm); pooled view only if clearly labeled.
Ruling: out-of-scope items recorded for Plan 2, not fixed: lock fingerprints tool names not schemas; boot-health when Profile.csv absent; YAML-null manifest TypeErrors; baseline_offset default 0 in benchmark_deploy.
Re-review verdict: all 17 findings + 2 fold-ins ADDRESSED, no new Critical/Important breakage; suite independently re-run 2502 passed.
Ruling: two trivial out-of-scope residuals (stale F9 docstring; schedule.json ensure_ascii kwarg) dispatched to the wave agent as a one-commit chore; controller will verify the 2-line diff directly instead of a review seat (diff is trivially inspectable).

## xhigh round 2 (2026-08-31, 15 findings G1-G15, base 3a9ffcc)
All 15 controller-verified real before dispatch; fixed in 11 commits (93099bb..89e1d1c), suite 2502 -> 2552.
Ruling: G1 classify_result gains a third class "not_dispatched" (UNAVAILABLE/MALFORMED_ARGUMENTS prefixes) — neither success nor domain rejection; successful_tool_call requires "success". — Cost if wrong: metric vocabulary churn.
Ruling: G2 reload success grammar = {"Loaded " prefix, "world ready", "Save loading ("} minus failure markers {FAILED, ABORTED, WARNING:, Error:, not found}; unrecognized text stays failure (fail closed).
Ruling: G3 the launcher cannot discriminate an inert load from a fast auto-reconnect — only the checksum can. reload_position contract None -> bool(verified); a checksum mismatch after an UNVERIFIED reload reclassifies as retryable RELOAD_OR_RECONNECT infra attempt; after a verified reload it still aborts the session. server.py wrong-save recovery treats UNVERIFIED as reconnect-only. — Cost if wrong: up to 3 extra reload cycles on a genuinely drifted position behind an unverified reload.
Ruling: G6 (pushback on reviewer) api_key placeholder "x" stays when the env var is unset — warn loudly instead of refusing. probe_backend already fails closed on bad keys against real endpoints; refusal would break the local-gateway workflow (no keys exist).
Ruling: G8 session_fingerprint is REQUIRED in session.json — store refuses fingerprint-less locks; report hard-fails unstamped trials under a stamped lock (canonical schema comment updated from "optional").
Ruling: G7 temp-0 admission requires repeated_consistent; verdict "not_applicable_greedy" only when deterministic, else "not_honored".
Ruling: G10 expected_state_sha256 becomes a live integrity anchor — verified at startup against state_digest(normalize_state(expected_state)), fail closed.
Ruling: G13 unknown models price as None + listed in unpriced_models, never $0.
Ruling: G14 digest-dependent action metrics (successful_mutations, digest-keyed repetitions) report None when no step carries state digests (historical records), mirroring useful_actions.
Ruling: G15 a corrupt IDENTITY row raises a distinct parse error so BenchmarkStateError names corruption, not truncation; other row types keep skip-on-corrupt.
Re-review verdict (round 2): all 15 addressed per rulings, RED tests spot-verified genuine against 3a9ffcc; suite independently re-run 2552. ONE new Important: _group_summary (benchmark_report.py) unguarded against G14's None counts — digest-less trial file would crash build_report with TypeError.
Ruling: controller fixed the _group_summary gap directly with TDD (RED TypeError reproduced, then guarded summing mirroring useful_actions; None when no trial measured) — a one-function follow-up did not warrant another dispatch seat. Commit 3f220b2; suite 2554 green.
