# Handoff: Plan 3 Part 1, next session (written 2026-10-10, 08:45 UTC)

Continues [session 2](2026-10-09-plan-3-part-1-live-phase-session-2.md), which holds the full
state table, the two decisions for Riz, the code fixes and the hazard list. Everything there is
still true except where this file says otherwise. Binding authority stays with the spec, the plan
and `benchmarks/contracts/instrument-v2.md`.

## What changed since session 2

| Item | State |
|---|---|
| `main` | `432f14c`, pushed to GitHub (`9fb380f..432f14c`, the whole live phase including the three archives); WSL and Windows checkouts identical, both clean |
| `code_identity` | unchanged, `af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697`; `432f14c` touched only `tools/skills/`, confirmed with `identity-impact.py` |
| Repo skill | `tools/skills/civ6-arena-live` gained a "Plan 3 authoring stages" subsection, `references/gamecore-api-surface.md` and five scripts (below); `.claude/skills/civ6-arena-live` is now a symlink to it (it was a stale July copy, so sessions were loading the wrong text) |
| Shared skills | `wsl-windows-tools` gained the `grep -a` and `.ps1` rules (laptop-skills `97085ff`, pushed); the laptop's new `wslg-gui-app` is linked into `~/.claude/skills` and `~/.codex/skills` |
| Game | still running on the gaming PC at write time (tuner port 4318 listening, no client attached); state unverified since 20:05 UTC on 2026-10-09 |
| Packets | tactical done under the current identity; city done under the previous identity; builder not started; gate fails only `family_coverage` and `identity_match` |

## Recommended next steps, in order

1. **First fifteen minutes.** Kill the Claude session's own `civ-mcp` before any benchmark CLI
   (it reclaims the single FireTuner slot the moment the game answers). Run
   `tools/skills/civ6-arena-live/scripts/firetuner-owner-map.sh`, then
   `scripts/windows-civ6-launcher.sh boot-health --json` and `classify-frontend`. If the game is
   on the continue screen, `press-escape` through the bridge (never in-world). If the game is dead,
   `restart-and-load <save>` and expect the cold save-list stall of several minutes on the first
   frontend load; do not fire a second load while it is pending.
2. **Decision 1, builder family** (Riz). With the recommended option (b), in this order:
   record the prospective amendment under "Recorded amendments" in `instrument-v2.md`; remove
   `final_charge` from `REQUIRED_LIVE_TAGS["builder"]` in `benchmark_part1_gate.py` (a toolkit
   file, so no identity move; confirm with `identity-impact.py` before committing) and update the
   gate tests; revise `benchmarks/recipes/plan3-builder-a1.yaml` to the engine's default
   three-charge builders and drop its final-charge case; run `probe-gamecore-api.py` for every
   accessor the revised setup ops use; then run the family with
   `benchmark-family-chain.sh` from `survey` to `finish`. The scenario clock is 10,800 s from
   `survey`, one substitution per family.
3. **Decision 2, city revalidation** (Riz). Record a prospective amendment granting the city
   family one revalidation attempt (same recipe `plan3-city-a2.yaml`, new attempt directory, new
   clock, predecessor packet named). Change the journal rules in `benchmark_authoring.py` (toolkit)
   so a declared revalidation attempt is admitted despite the closed predecessor, the
   "already journaled" same-scenario refusal and the third-identity refusal. Then run the chain
   from `survey` in a new directory such as `benchmark_runs/plan3-part1/city-a2-reval`. No unit
   dies in that scenario, so it is expected to pass unchanged.
4. **Close out.** Runbook section 4 (`evidence-files`, force-add every attempt directory including
   the probe and the aborted validation directory), section 2 again (offline preflight with the
   final recipe set), section 5 (gate), then plan Task 22 (exit report, release).
5. **Only after the gate passes**, the fingerprint fixes recorded as hazards: registry rejections
   for a civilian's attack and fortify and for an archer's second shot. Any fingerprint change
   before the gate invalidates all three packets. The `game_lifecycle.py` two-phase
   find-then-load fix is toolkit and may land earlier, but not in the middle of a family.
6. **Hands off the gaming PC while a stage runs.** Operator mouse use minimizes the game and the
   Escape press is refused; the run aborts.

## Tools now in the repo

All under `tools/skills/civ6-arena-live/scripts/`, run from the WSL repo root:

```bash
# one stage, INFO logging, log under benchmark_runs/logs/ (never inside an attempt dir)
scripts/benchmark-stage.sh benchmarks/recipes/plan3-builder-a1.yaml benchmark_runs/plan3-part1/builder-a1 survey

# chained stages; after `archive` it commits the new save and syncs the Windows checkout
ARCHIVE_COMMIT_TRAILER='Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>' \
scripts/benchmark-family-chain.sh benchmarks/recipes/plan3-builder-a1.yaml \
  benchmark_runs/plan3-part1/builder-a1 survey apply probe archive capture verify menu-check validate finish

# Windows checkout fast-forward from the WSL repo (no GitHub push); run before every `capture`
scripts/sync-windows-checkout.sh

# fingerprint vs toolkit classification of a change, plus current identities vs the preflight record
uv run python scripts/identity-impact.py HEAD~1..HEAD

# live GameCore accessor check (free tuner slot, in-world game); compare with references/gamecore-api-surface.md
uv run python scripts/probe-gamecore-api.py
```

The stage CLI itself is unchanged: `uv run python -m civ_mcp.arena.benchmark_authoring <stage>
--recipe R --attempt-dir A`. A failed non-repeatable stage may be re-run; repeating `survey`
invalidates everything downstream.

## Hazards carried forward

The list in session 2 stands. Three to keep in front of you: GameCore is not InGame (probe
before a clock opens); the frontend save-list stall right after launch; and the stage machine's
refusal of every stage on a closed attempt, which is why decision 2 needs both an amendment and
a toolkit change.
