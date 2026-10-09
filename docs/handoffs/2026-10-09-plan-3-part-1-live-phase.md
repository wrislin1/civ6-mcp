# Handoff: Plan 3 Part 1 live phase

Date: 2026-10-09. For the next civ6-mcp session on the gaming PC. The offline half of
[the Part 1 plan](../superpowers/plans/2026-10-08-arena-benchmark-plan-3-part-1.md) is
implemented, reviewed and merged to `main`; what remains is the live phase that needs the
game running. Binding authority stays with the
[spec](../superpowers/specs/2026-10-08-arena-benchmark-plan-3-part-1-design.md), the plan and
the candidate contract (`benchmarks/contracts/instrument-v2.md`). This page says where things
stand and what to do first; it repeats nothing the runbook already sequences.

## Status

| Item | State |
|---|---|
| Branch `plan3-part1` (fork `67e4d2c`, head `5e29463`, 43 commits) | merged into `main` as `71b3ec6` and pushed to `origin/main` together with this handoff |
| Full suite | 4051 passed, exit 0, on the merged `main` tree (`71b3ec6`) and on the branch head's code (`8f9b884`) |
| Offline Tasks 1–18 | done, task-reviewed, final-reviewed, fix wave applied |
| Pre-live review (two external reviews, 18 findings) | all verified and fixed on 2026-10-09; amendment recorded in `instrument-v2.md` ("Recorded amendments") |
| Committed preflight evidence | `benchmark_runs/plan3-part1/preflight/` regenerated under `code_identity e9255e84…` |
| Live steps | **none run**: Task 17 probe, Task 18 preflight record, Tasks 19–21 authoring (three families), Task 22 exit gate |
| Worktree | `.claude/worktrees/plan3-part1` left on disk (merged, clean); tear down with `/worktree-janitor` when convenient |

Memory file for this work: `~/.claude/projects/-home-riz-projects-civ6-mcp/memory/plan3-part1-sdd-run.md`.

## Do this first, in order

1. Read the runbook end to end:
   `docs/research/arena-benchmark-plan-3-part-1-live-runbook.md`. Every live command and its
   outputs are sequenced there. Nothing below replaces it.
2. Confirm Riz has authorised launching Civilization VI and holding the desktop. Each family's
   authoring session owns a 10,800 s clock that never pauses; the run owns keyboard, mouse and
   focus while it drives the game (see the `live-operator-handoff` rules: one action per
   handoff, visible stop signals, no human use of the machine during an unattended window).
3. Put the Windows companion checkout (`/mnt/c/Users/wrisl/dev/civ6-mcp`) on the same commit
   as this checkout. The bridge runs *that* checkout's code with it as working directory.
   Nothing is ever written to a tracked path there (exports stage under its gitignored
   `benchmark_runs/plan3-part1/`), so a plain fast-forward always works.
4. Free the FireTuner slot: kill the session's `civ-mcp` MCP server (python and its `uv run`
   parent) before any benchmark CLI; see the memory note on the tuner slot and launch.
5. Check the game state read-only (port 4318) before assuming anything about it.
6. Run runbook step 1 (timing probe), step 2 (offline preflight), then step 3 per family,
   step 4 (force-add, including the probe output directory), step 5 (gate), then plan Task 22.

## Facts the live operator must know

These are enforced by code now; do not relearn them the hard way.

- **Setup Lua runs in GameCore** (`GameConnection.execute_mutation`, never re-sent on a
  dead socket). Recipes may use the GameCore mutation APIs the verified v1 journals used:
  `UnitManager.InitUnit/PlaceUnit/RestoreMovement`, `ImprovementBuilder.*`, treasury and
  city mutators. Every operation is proved by its readback.
- **Attempt directories and `--predecessor-journal` must live under
  `benchmark_runs/plan3-part1/`.** The stage CLI refuses anything else before the clock
  opens; the gate scans only that root and refuses imported predecessor references to any
  other journal.
- **The archive stage takes the native save's signature (`stat-save`) before saving**, and the
  export waits out a same-named stale save instead of archiving it. Exports land at a
  stage-unique path under the Windows checkout's `benchmark_runs/plan3-part1/exports/`.
- **`finish` re-runs recover both an index write failure and a packet-only write failure**
  (the existing index is verified and reused). A re-run after everything exists reports the
  attempt as closed.
- **The timing probe's output directory must be force-added**; the gate verifies every file
  its `evidence-index.json` lists is present, hash-correct and Git-tracked.
- **Coverage squares are clipped to the map grid** on all four edges (shared helper with the
  probe); a binding near the east or south edge is fine.
- **A backwards wall clock is journaled, not fatal** (`clock_backwards_s` on the checkpoint);
  the budget is never shortened.
- **Recipe binding selectors accept a list value as "any of"** (used for hills terrains).

## Known risks going live (decided; do not relitigate without new evidence)

- **City recipe, Gongju granary removal** uses GameCore `CityBuildings:RemoveBuilding`, which no
  verified journal exercised. The op checks the method exists and the readback proves the
  housing arithmetic (housing − pop < 1, + granary ≥ 1). If it is unavailable, `apply` fails
  within minutes with the Lua error in `mutations/`; author a different shortfall in the `a2`
  substitute. Every base city already held a granary or had housing to spare, so an authored
  shortfall is unavoidable.
- **City recipe, Seowon sites.** Korea builds `DISTRICT_SEOWON` (hills only; the tool passes the
  district through without resolving civilization replacements). The clean site, the
  plains-hills mine (accepted replacement) and the grass-hills mine (protected) are selected
  over terrain unknown offline; zero or several matches fail `apply` with the candidate list.
  Same exposure class as the original farm selectors.
- **Tactical recipe, archer relocation.** Setup moves Gongju's garrisoned archer to an empty
  land tile beside the city within two tiles of the attacker (frees the purchase slot;
  `lua.cities` refuses same-class purchases onto an occupied centre). Line of sight for the
  measuring shot is still unverified until the probe stage.
- **Tactical damage threshold** (`minimum_damage` 25) is provisional and frozen only from the
  probe stage's measured deltas (`measured_parameters`); the archive stage refuses otherwise.
- **GameCore availability** of `GetHousing`, `GetDistricts`, `GetBuildings`, `GetResourceAmount`
  in the single v2 capture program is assumed from the v1 patterns and blocked (not papered
  over) if a fact cannot be read.
- **Selector uniqueness** is the general residual: offline recipes over a live map can need one
  substitution per family; the journal and gate allow exactly one.

## What is finished and must not be redone

Tasks 1–18 and the review fix wave. The git history is the record (`git log 67e4d2c..5e29463`);
the SDD workspace was deleted after the final review. The rulings made during execution are in
the merged branch's commit messages and the previous session's final report; the material
ones live on as code and tests (fingerprint scope, `declared_rejections`, `measured_parameters`,
latch-only telemetry reset, status-based `target_neutralised`, GameCore setup context,
export staging).

## Research context (not a dependency)

Riz's 2026-10-09 direction ([research direction and SystemOne hold](../research/2026-10-09-victory-strategy-research-direction.md))
puts the broad decision-model comparison on hold and points the next architectural design at a
persistent victory strategy with milestone tracking and evidence-based revision. That record
states explicitly that Plan 3 Part 1 continues under its existing contract: scoring, tool
surface, exit gates, model roster, library freeze and held-out restrictions are unchanged, and
no SystemOne backend is a prerequisite. Two qualifications it preserves: the Pokémon findings
concern the tested configurations only, and tactical-04's battle overlap means its unqueried
status alone does not make it an independent holdout. The
[server handoff](2026-10-08-systemone-decision-servers.md) keeps the endpoint reference for any
future scoped use.

## Pointers

| What | Where |
|---|---|
| Live runbook (command order, outputs, recovery) | `docs/research/arena-benchmark-plan-3-part-1-live-runbook.md` |
| Candidate contract, identities, gate, recorded amendments | `benchmarks/contracts/instrument-v2.md`, `instrument-v2.yaml`, `plan3-part1-budget.yaml` |
| Recipes | `benchmarks/recipes/plan3-{builder,city,tactical}-a1.yaml` |
| Toolset (35 tools) | `benchmarks/toolsets/plan3-part1-v1.yaml` |
| Authoring CLI (stages, abandon, preflight, evidence-files, gate) | `src/civ_mcp/arena/benchmark_authoring.py` (`--help` per subcommand) |
| Timing probe CLI | `src/civ_mcp/arena/benchmark_capture_probe.py` |
| Gate and evidence loader | `src/civ_mcp/arena/benchmark_part1_gate.py`, `benchmark_part1_evidence.py` |
| Windows bridge (`install-save`, `export-save`, `stat-save`) | `src/civ_mcp/launcher_cli.py`, `src/civ_mcp/arena/benchmark_deploy.py` |
| Committed preflight suite evidence | `benchmark_runs/plan3-part1/preflight/` |
| Previous handoffs | `docs/superpowers/plans/2026-10-08-plan-3-brainstorm-handoff.md`, `docs/handoffs/2026-10-08-systemone-decision-servers.md` |
