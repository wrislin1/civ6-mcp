# Handoff: Plan 3 Part 1, session 3 (2026-10-10, 11:35–20:25 UTC)

Continues [the next-session handoff](2026-10-10-plan-3-part-1-next-session.md) and
[session 2](2026-10-09-plan-3-part-1-live-phase-session-2.md). Binding authority stays with
the spec, the plan and `benchmarks/contracts/instrument-v2.md` (five amendments recorded today
under "Recorded amendments"). Riz took both open decisions at the start: option (b) for the
builder family and the city revalidation attempt.

## Where things stand

| Item | State |
|---|---|
| `main` | `288598a` gate commit, followed by the exit-suite evidence and this handoff; Windows checkout fast-forwarded at session end; **not pushed to GitHub** (no push authorisation this session) |
| `code_identity` | unchanged all day: `af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697`; every code change was toolkit (`benchmark_part1_gate.py`, `benchmark_authoring.py`, `benchmark_authoring_journal.py`), confirmed with `identity-impact.py` before each commit |
| Full suite | 4063 passed (`benchmark_runs/plan3-part1/preflight/pytest.txt`, result JSON and index regenerated) |
| Offline preflight | passed with the final recipe set (builder-a2, city-a2 v2, tactical-a1) |
| tactical | unchanged: packet `plan3-tactical-a1-v2.json` |
| builder | **done**: `builder-a1` abandoned after `validate` (5/13; see below), substitute `builder-a2` finished 19:39 UTC, validate 12/12, packet `benchmarks/provenance/plan3-builder-a2-v3.json` (archive `plan3-builder-a2-v3`; v1 archive/outputs retained from the first capture). Journal: a1 6229 s, a2 6392 s, family 12621 s |
| city | **done**: revalidation attempt `city-a2-reval` (declared via `--revalidates`), every stage passed, validate 11/11, 2109 s; packet `benchmarks/provenance/plan3-city-a2-v2.json` (archive `plan3-city-a2-v2`) under `af0de817…`; `city-a2` (v1, previous identity) retained with its attempt dir |
| Gate | **PASSED 20:21 UTC**: `benchmarks/provenance/plan3-part1-exit.json`, every named requirement for all three packets under one code/contract/toolset identity; family totals builder 12621 s, city 4247 s (gate merges the retained a2 and the revalidation by the longer clock), tactical 7291 s. Exit-gate suite retained under `benchmark_runs/plan3-part1/exit/` |
| Game | running in-world on the gaming PC (last reload: the city-a2-v2 archive), tuner port listening, no client, no `civ-mcp`/arena process; restart the Claude session to get the civ6 MCP tools back |

## What happened, in order

1. **Recovery (step 1).** The game was dead. Native `restart-and-load "SEONDEOK 100 400 BC"`
   relaunched it but the OCR loader cannot reach a July save (12-page scroll limit over ~300
   saves sorted by date) and left the Load Game screen; `press-escape`, then
   `load PLAN3_TACTICAL_A1_V2` (on-disk underscore name; the first try toggled the open Single
   Player submenu shut) got in-world; `game_lifecycle.load_game_save` then reloaded the base
   through the in-game tier in about a minute, verified by content (12 own units, 63
   barbarians, mine unpillaged). Turn/civ/seed cannot distinguish an archive from its base.
2. **Decisions landed (`a8b8765`).** `final_charge` withdrawn from the builder tag set (gate +
   contract table); `plan3-builder-a1` on default three-charge builders; stage CLI
   `--revalidates JOURNAL` with the journal rule (one revalidation per scenario, only the
   named sibling's "already journaled" refusal waived, reference recorded); city recipe
   `version: 2`; both amendments recorded; skill and runbook updated.
3. **builder-a1 live (16:06–17:50 UTC).** Four recipe corrections from first live contact,
   each measured read-only before editing: neither city ring has farmable grassland (food
   sites moved to plains (66,22)/(69,22), `b70b9da`); closer-only candidates carry districts
   ((69,23) named); the v2 target scan lists a hostile only inside the capture area or next to
   an owned unit (selector `area`); the builder-attack legality probe dropped (civilian attack
   answers `STOPPED_SHORT`, `35801a1`); the builder-task list places plain farms past the
   1500-char cap (facts moved to per-site map queries, `d00191f`). Validate then failed 5/13.
4. **Root cause, measured live on the archived start.** Civ VI zone of control empties a
   civilian's movement the moment it enters a tile adjacent to an enemy military unit (the
   Jinju builder reached the horses with 0/4 moves, `HasMovedIntoZOC` true), so a pasture next
   to the threat is impossible in one turn; a melee unit that entered ZOC this turn cannot
   attack (`ERR:ZOC`), so the escort's attack from two tiles away never resolved; Jeonju to
   the mine is two hills steps, all four moves. `builder-a1` abandoned (terminal-failed,
   indexed, retained).
5. **builder-a2 (`19241b6`, 17:52–19:39 UTC).** Threat at (72,31), adjacent to Jinju and to the
   exposure tile (73,31) but two from the horses; escort in the city at 5 hp attacks
   directly; escort leaves to the road tile (73,29) and back for the temporary case; Jeonju
   builder starts on the quarry (67,23). First validate 10/12: the improve-food rung's
   `food: {min: 2}` never reads true because the GameCore yield cache does not refresh
   in-turn (a fresh plains farm still read 1 food). Field dropped (`ca6eb03`), repeated from
   `probe`; the archive stage numbers re-captures as recipe version + archives already
   published, hence v3. Second validate 12/12.
6. **city revalidation (opened 19:39 UTC).** `survey --revalidates
   benchmark_runs/plan3-part1/city-a2/authoring-journal.json` admitted the new attempt
   `city-a2-reval` (journal: a1 imported-failed, a2 attempt 2 with the `revalidates`
   reference), then the chain `apply..finish`. Every stage passed first time (validate 11/11, 20:14 UTC). At close-out `evidence-files` refused closure because the retained `city-a2` index binds the shared recipe path at yesterday's digest and the revalidation bumped the file; the closure check now reports that one recipe entry of a revalidated attempt as superseded (toolkit, `7b454a2`, with a test), everything else must still match.

## Hazards carried forward

- Fingerprint fixes still deferred until after the gate: registry rejections for a civilian's
  attack and fortify and for an archer's second shot; `game_lifecycle.py` two-phase
  find-then-load (toolkit, may land any time outside a family).
- Native menu loader: 12-page scroll limit; our `PLAN3_*`/`BUILDER_*` archives sit at the top.
- Engine rules now in the skill: civilian ZOC stop, ZOC-then-attack refusal, stale in-turn
  yield cache, two hills steps = four builder moves, archive numbering.
- `benchmark_runs/logs/` holds every stage log of the day (outside attempt dirs).

## Next steps

1. **Plan Task 22**: exit report, report regeneration from a temporary checkout of the tracked
   tree (compare bytes with the retained reports), release notes. Nothing live is needed.
2. **Push** `main` to GitHub when Riz authorises (eleven commits today, `a8b8765..` onward).
3. **Fingerprint hazards, only after the release is cut**: the registry answers a civilian's
   attack and fortify and an archer's second shot with success-shaped text; fixing them moves
   `code_identity`, and the gate compares packets to the *current checkout*, so land them on a
   branch or after tagging the gated tree. `game_lifecycle.py` two-phase find-then-load is
   toolkit and can land any time.
4. Optional toolkit follow-ups surfaced today: a `benchmark-stage.sh` note that `grep -a -v`
   buffers the tee'd log; a native loader scroll past 12 pages (or a name filter) so old base
   saves load from the menu; `get_pathing_estimate` could report moves remaining on arrival.
