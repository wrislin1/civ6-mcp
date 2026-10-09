# Handoff: Plan 3 Part 1 live phase, session 2 (2026-10-09, 16:08–20:05 UTC)

Continues [the live-phase handoff](2026-10-09-plan-3-part-1-live-phase.md). Binding authority stays
with the spec, the plan and `benchmarks/contracts/instrument-v2.md` (three live-phase amendments
recorded today under "Recorded amendments").

## Where things stand

| Item | State |
|---|---|
| `main` | `6dd8ade` (WSL and Windows checkouts identical); working tree clean |
| `code_identity` | `af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697` (suite 4056 green, preflight evidence `benchmark_runs/plan3-part1/preflight/`) |
| Timing probe | passed: `benchmark_runs/plan3-part1/capture-probe-5` (20/20, mean 0.385 s, p95 0.397 s); four earlier probe dirs retained as failures |
| Offline preflight | passed under the identity above (`benchmarks/provenance/plan3-part1-offline-preflight.json`) |
| tactical | **done**: packet `benchmarks/provenance/plan3-tactical-a1-v2.json`, archive `plan3-tactical-a1-v2`; validate 10/10; family 7291 s |
| city | a1 abandoned (Seowon sites); a2 **done but stale**: packet `plan3-city-a2-v1.json` carries the previous identity `08585538…` (captured before the delayed-death fix) |
| builder | **not started**: a1 cannot be authored as written (see decision 1) |
| Gate on the two packets | fails `family_coverage` (no builder packet) and `identity_match` (city); each packet passes every requirement of its own |
| Game | left running in-world on the gaming PC; the Claude session's `civ-mcp` was killed at 16:08 |

## Two decisions for Riz

1. **Builder family design.** GameCore has no build-charge setter (`Unit:ChangeBuildCharges` and
   `UnitManager.ChangeBuildCharges` are nil; `Set/ChangeActionCharges` do not touch build charges;
   every DB builder-charge modifier is positive). `UnitManager.InitUnit` gives a builder 3 charges and
   4 moves. `plan3-builder-a1` requires exactly one charge per builder and the gate requires a passed
   `final_charge` case. Options: (a) default-charge redesign with a three-build final-charge chain
   (scarcity rule from the spec lost; feasibility depends on three eligible tiles within one turn);
   (b) relax `final_charge` for the builder family (gate code `REQUIRED_LIVE_TAGS` plus the contract's
   tag table) and keep the rest; (c) burn charges in setup with InGame build-then-remove operations
   (new authoring op kind, hidden side effects such as Eurekas). Recommendation: (b) with an explicit
   prospective amendment, because the one-charge builder is not expressible in the game's own API.
2. **City revalidation.** The contract says a scoring-chain change is cured by re-running the packet's
   `validate` stage, but the stage machine refuses every stage on a closed attempt, the packet's
   `contract_identity` is bound at capture (the position file), a fresh attempt for the same scenario
   is refused ("already journaled") and a third identity is refused. A prospective amendment is needed
   that grants the city family a revalidation attempt (same recipe `plan3-city-a2.yaml`, new attempt
   dir, new clock), with the matching journal-rule change (toolkit). The a2 clock itself expired at
   19:52 UTC. Nothing in the city scenario depends on the fixed code path (no unit dies), so a re-run
   is expected to pass unchanged.

## What was fixed in code today (all committed, all tested)

- `ecec720` v2 capture: GameCore `CityDistricts` has no `Members()` (index accessors), empty build
  queue reads `"NONE"`.
- `f9728c5` launcher: under WSL `restart_and_load` delegates to the native bridge `restart-and-load`
  (menu-check could never run from WSL before).
- `12050df` v2 capture: dead / delayed-death units skipped (losses were never charged, kills never
  counted).
- Recipes: `plan3-city-a2.yaml` (third Seowon site authored on (67,22); housing in Jeonju for the
  feed cap), `plan3-tactical-a1.yaml` (joint layout search; attacker 65 hp; threshold 40 frozen from
  measured 56/28; spearman; purchase repeat).

## Hazards recorded for a later identity (not fixed)

- `attack_unit` answers a settler's attack and an archer's second shot, and `fortify_unit` answers a
  settler, with success-shaped text (registry, fingerprint).
- Frontend save-list query right after launch can stall the game for minutes; the 30 s cancel lands
  after a late result has already fired `Network.LoadGame` (`game_lifecycle.py`, toolkit). Once during
  tactical validation an in-game reload of an archive that was on disk answered "not found" three
  times and left the game on the continue screen; the aborted run is retained as
  `…/plan3-tactical-a1-v2-validation.aborted-001` and the re-run passed.
- `benchmark_runs/plan3-part1` stage logs piped through `grep -v` must use `grep -a` (Lua readback
  bytes look binary and the log comes out empty).

## Commands that worked (WSL)

- Per stage: `uv run python -m civ_mcp.arena.benchmark_authoring <stage> --recipe R --attempt-dir A`
  (a failed non-repeatable stage may be re-run; repeating survey invalidates downstream).
- Windows checkout sync without pushing: `git -C /mnt/c/Users/wrisl/dev/civ6-mcp fetch /home/riz/projects/civ6-mcp main && git -C /mnt/c/Users/wrisl/dev/civ6-mcp merge --ff-only FETCH_HEAD`
  (required after every archive commit, before `capture`).
- Continue screen: bridge `press-escape` (never in-world).
