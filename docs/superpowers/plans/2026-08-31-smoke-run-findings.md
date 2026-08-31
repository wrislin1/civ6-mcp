# First live smoke run — findings for the Plan 2 planning session

Run: `benchmark_runs/smoke-live-002` (ungated smoke, 2026-08-31). Suite
`smoke-seondeok-v1` — position `smoke-seondeok-pyramid-v1` (SEONDEOK T48 Korea,
save `SEONDEOK Pyramid Start`), gemma4-26b, seed 11, arms minimal/standard,
max_steps 8. Both trials committed 1st attempt; report generated clean.

## What the smoke verified live

- Lua-tier reload + checksum position isolation: trial 1 moved the settler off
  (94,50); trial 2 opened with it back at (94,50) and a matching digest.
- Arm treatment reaches the model: prompt tokens 15,230 (minimal) vs 25,366
  (standard) on identical positions.
- Per-step `state_digest_before/after` recorded; mutations flip the digest,
  observations don't (`successful_mutations=1`, real `domain_rejections` from
  `ALREADY_COMPLETED` / `STACKING_CONFLICT`).
- Report stamps the UNGATED SMOKE warning, marks gemma4-26b UNPRICED (not $0),
  scores rubric via `evaluate_predicate`, and pairs the arms (tie, as expected
  with a saturated smoke rubric).

## Findings to fold into Plan 2

1. **Tool-call capability must be an admission gate.** gemma3-12b through
   llama.cpp silently ignores the OpenAI `tools` field (identical 106-token
   prompts across arms; prose ```tool_code``` reply; 0-step trials terminal
   `implicit_finish` in `smoke-live-001`). `probe_health` checks model identity
   only — Plan 2's admission should send a canary WITH a tool schema and
   require a `tool_calls` reply, or the A/B silently measures nothing.
2. **Rubric predicates can abort the report.** `unit_distance_decreased` raises
   `PredicateError` (aborting report generation) if the unit is absent from the
   final state — e.g. a settler consumed by `found_city`. Positions whose
   intended play consumes a unit must not reference that unit in a distance
   predicate; consider an `exists`-guarded variant.
3. **Menu-fallback save navigation can't reach old saves.** The Tier-2 OCR
   loader failed to find `SEONDEOK Pyramid Start` — buried under 12+ MCP
   autosaves, scroll limit exhausted. Benchmark saves should sort to the top
   (fresh install-save timestamps do this) or the loader needs deeper scroll.
4. **Load-screen ESC waiter toggled the pause menu** after the world was
   already ready (frontend-Lua load path, observed by Riz watching the screen).
   The runner's own InGame-context reloads showed no such issue. Worth a look
   at the frontend-path world-ready detection before Plan 2 relies on it.
5. **A stale `civ-mcp` process wedges everything silently.** FireTuner accepts
   one client; an orphaned server from a previous session held the port and
   every new connection timed out. Plan 2's gate pipeline should include a
   tuner-port-holder check (ss/pid scan) with a named remediation, per the
   arena recovery playbook.
6. **Thinking-budget interaction**: gemma4-26b at `max_tokens 1024` risks
   burning the budget on reasoning before emitting tool calls; 3072 worked
   (completion ~150 tokens/turn). Pin per-model `max_tokens` guidance in the
   suite authoring notes.
