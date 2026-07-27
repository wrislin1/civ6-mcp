# Civ 6 capability inventory — Design

**Date:** 2026-07-27 · **Status:** approved (design presented and accepted in
session) · **Order:** first of three (see
`2026-07-27-arena-unit-action-visibility-design.md` and
`2026-07-27-arena-multi-turn-intent-design.md`)

## Context

Two capability gaps were found the expensive way, one live run each: arena
builders never improved a tile because the `minimal` tier had no
`improve_tile`, and a Great Writer never made a great work because
`activate_great_person` is `full`-only. Both were invisible to the existing
audit (`scripts/audit_arena_tool_coverage.py`), which diffs our own surfaces
against each other — an action missing from *every* surface has nothing to
diff against.

The game answers this directly. Probed against the live game on 2026-07-27:

| `GameInfo` table | Rows | Key field |
|---|---:|---|
| `UnitOperations` | 63 | `OperationType` |
| `UnitCommands` | 28 | `CommandType` |
| `DiplomaticActions` | 42 | `DiplomaticActionType` |

`CityCommands`, `CityOperations`, and `PlayerOperations` do not exist — the
engine enumerates unit and diplomatic verbs only.

A single read of those tables exposes actions no tool in this repository
implements on any surface, including `PILLAGE`, `PILLAGE_ROUTE`,
`PLUNDER_TRADE_ROUTE`, `DESIGNATE_PARK`, `PLANT_FOREST`, `HARVEST_RESOURCE`,
`EMBARK`, `DISEMBARK`, `COASTAL_RAID`, `TOURISM_BOMB`, `GIFT`, `AIRLIFT`,
`PARADROP`, `ENTER_FORMATION`, `EVANGELIZE_BELIEF`, `LAUNCH_INQUISITION`,
`REMOVE_HERESY`, `CONDEMN_HERETIC`, `SWAP_UNITS`, `ROUTE_TO`, `WMD_STRIKE`,
and `CLEAR_CONTAMINATION`.

This deliverable makes coverage a measured number and a test failure, not a
discovery. It implements no missing verb: the scope of that work should be
chosen from the ranked list this produces.

## Deliverables

### 1. Action-space snapshot

`scripts/audit_civ6_capabilities.py --capture` connects to FireTuner, reads
the three tables, and writes `docs/research/civ6-action-space.json`:

```json
{
  "schema_version": 1,
  "tables": {
    "UnitOperations": ["UNITOPERATION_AIR_ATTACK", "..."],
    "UnitCommands": ["UNITCOMMAND_ACTIVATE_GREAT_PERSON", "..."],
    "DiplomaticActions": ["DIPLOACTION_RESIDENT_EMBASSY", "..."]
  }
}
```

Each list is sorted. Capture requires a running game and refuses to write
unless all three tables return at least one row, so a failed read can never
silently shrink the snapshot into a green test run. The snapshot carries no
build stamp: neither `UI.GetAppVersion()` nor `Game.GetRuleSet()` is
available in this Lua context (both probed, both absent). A game update
surfaces as a diff when someone re-runs `--capture`.

The snapshot is committed. CI has no FireTuner, so every other component
reads the file, never the game.

### 2. Coverage map

`src/civ_mcp/capability_map.py` classifies every action in the snapshot,
one entry each:

```python
@dataclass(frozen=True)
class Coverage:
    status: str                  # "covered" | "missing" | "excluded"
    tool: str | None = None      # required when covered
    priority: str | None = None  # required when missing: "high"|"medium"|"low"
    note: str | None = None      # required when missing or excluded

ACTION_COVERAGE: dict[str, Coverage]
```

`tool` names either an arena registry tool (`"improve_tile"`) or an MCP-only
verb in the form `"unit_action:repair"`. Both forms are validated against
real surfaces, so a typo cannot mark an action covered.

Classification is a judgment call and stays hand-maintained. The rule:

- **covered** — a tool on either surface performs this action today.
- **missing** — a player action a competent human uses, with no tool. Carries
  a priority and a one-line reason it matters. `high` is reserved for actions
  that change a victory path or a war outcome (`PILLAGE`, `DESIGNATE_PARK`,
  `TOURISM_BOMB`); `medium` for routine play a seat visibly lacks
  (`HARVEST_RESOURCE`, `GIFT`); `low` for niche or late-game-only verbs
  (`WMD_STRIKE`, `AIRLIFT`).
- **excluded** — not a player capability. Engine-internal or debug hooks
  (`EXECUTE_SCRIPT`, `WAIT_FOR`, `MOVE_TO_UNIT`), cosmetic commands
  (`NAME_UNIT`, `PET_THE_DOG`), and actions the engine performs implicitly
  as part of a move the tools already issue (`EMBARK`, `DISEMBARK`) each
  record which of those three reasons applies.

`EMBARK`/`DISEMBARK` are called out because they are the ambiguous case: if
implementation finds that `move_unit` does not in fact auto-embark, they are
`missing` at `medium`, not `excluded`. The map records whichever the
implementer verifies, with the evidence in the note.

### 3. Enforcement test

`tests/test_capability_coverage.py` fails when the snapshot and the map
disagree:

- every action type in the snapshot has a map entry — the failure message
  lists the unclassified types and names `capability_map.py` as the file to
  edit;
- every map entry names an action present in the snapshot (no stale entries
  after a game update removes something);
- every `covered` entry names a real tool: an arena tool in `TOOL_REGISTRY`,
  or `unit_action:<verb>` where `<verb>` is one of the verbs
  `scripts/audit_arena_tool_coverage.py` extracts from the MCP `unit_action`
  match statement;
- every `missing` entry has a priority in `{"high","medium","low"}` and a
  non-empty note; every `excluded` entry has a non-empty note.

Classification logic is also tested against synthetic snapshot/map pairs, so
the rules are exercised independently of the real data.

### 4. Report

`scripts/audit_civ6_capabilities.py --report` reads only the committed
snapshot and the map — no game, no network — and prints the three counts
plus the `missing` entries ranked by priority. That ranked list is the input
to any future implementation spec. `--json` emits the same content
machine-readably, matching the existing audit script's interface.

## Testing

- Full arena suite green before each commit.
- The enforcement test runs offline against committed data.
- `--report` and `--json` are exercised by a subprocess test, as
  `tests/arena/test_tool_coverage_audit.py` already does for the tool audit.
- `--capture` is not tested against a live game; its parsing is tested
  against recorded FireTuner lines.

## Out of scope

- Implementing any missing verb. This spec produces the list; the scope of
  closing it gets its own spec once the numbers exist.
- `Projects` (38 rows) and `GovernorPromotions` (48 rows). These are content
  catalogs reached through parameterized tools (`set_city_production`,
  `promote_governor`), not verbs. The snapshot format holds additional
  tables, so adding them later is data, not redesign.
- Auto-generating the classification. A generated map would either mark
  everything uncovered (noise) or guess coverage (false confidence).
- Changing `scripts/audit_arena_tool_coverage.py`, whose surface-to-surface
  diff stays useful and is extended separately in the visibility spec.
