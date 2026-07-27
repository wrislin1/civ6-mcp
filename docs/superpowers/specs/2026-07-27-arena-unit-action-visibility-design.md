# Arena unit-action visibility — Design

**Date:** 2026-07-27 · **Status:** approved (design presented and accepted in
session) · **Order:** second of three (after
`2026-07-27-civ6-capability-inventory-design.md`)

## Context

A seat can only take an action that is both **reachable** (the tool is in its
tier) and **visible** (it knows the action is legal right now). Two live runs
established that each failure mode looks identical from the outside — the unit
just sits there:

- **Reachable but invisible** was never observed, because
- **Unreachable** produced zero attempts *and* zero rejections in v3–v5: the
  arena `minimal` tier had no builder verbs, so improvement never entered the
  models' world. The same is true today of Great People —
  `activate_great_person`, `get_great_people`, `get_great_works`, and
  `recruit_great_person` are all `full`-only, and the v6 seats run `minimal`.
  A Great Writer standing on a Theater Square could not have made a great
  work.
- **Visibility** is what the v5 channels result isolated: per-deal "AVAILABLE
  NOW" affordances moved payment honoring from 0/2 to 2/2, while prose that
  merely named the action did not. Prose can name an action; it cannot convey
  when the action becomes available.

`get_units` already renders two affordance lines — `>> CAN ATTACK:` and
`>> Can build:` (`narrate.py:206-208`) — but `UnitInfo` carries no
great-person signal, so a Great Person renders as an ordinary unit. The
information exists: `gp:GetActivationHighlightPlots()`
(`lua/great_people.py:421`) returns the exact set of legal activation plots,
and the units scan already visits every unit.

## Deliverables

### 1. Tier reachability

`TIERS["standard"]` gains exactly `get_great_people` and
`activate_great_person`, going from 26 to 28 tools. `minimal` stays frozen at
its 15, pinned by the existing snapshot test — historical artifacts must
re-run against the same world.

This addition is self-limiting: `activate_great_person` carries
`requires="gp_unit"` (`registry.py:1249`), and `filter_tools`
(`registry.py:1451`) drops it from the schema for any seat that owns no Great
Person. A seat without one sees no extra tool.

`recruit_great_person` and `patronize_great_person` stay `listed-for-later`:
recruiting claims a point pool and patronizing spends gold, neither of which
the audit's disposition rule ("routine workflow discovery and non-destructive
actions with existing `GameState` support") covers.

### 2. Tier-aware coverage audit

`scripts/audit_arena_tool_coverage.py` currently answers "does the arena
registry have a tool for this?" — which is why `activate` never appeared as a
gap. It gains a per-tier view answering "can the seat in this experiment
reach it?": for `minimal` and `standard`, the list of action verbs
(registry tools with a non-`None` `verb`) absent from that tier, in the JSON
evidence and in the human report. Both prior discoveries — idle builders and
the Great Writer — would have appeared in that list before either live run.

### 3. Per-unit AVAILABLE NOW affordance

`UnitInfo` gains `can_activate_here: bool`, set by the units Lua scan: for a
unit whose `GameInfo.Units[…].GreatPersonClass` is non-empty, true when the
unit's own plot index appears in `gp:GetActivationHighlightPlots()`. The
lookup is wrapped in `pcall` and defaults false, matching how the units query
already guards optional engine calls.

`narrate_units` renders one line per unit that can act now, following the
existing `>>` convention:

```
  Bhasa (UNIT_GREAT_WRITER) at (12,19) — moves 2/2 [id:65541, idx:5]
    >> AVAILABLE NOW: activate_great_person with {"unit_id": 65541}
```

The line is populated only from engine-safe signals already collected or
newly added here: great-person activation (`can_activate_here`), tile
improvement (`valid_improvements`), upgrade (`can_upgrade`), and promotion
(`needs_promotion`). No new legality probe is introduced. In particular
nothing calls `CanStartOperation` on remote tiles, which the units Lua
documents as corrupting engine state and crashing `end_turn`
(`lua/units.py:1954-1956`).

### 4. Surface-appropriate call syntax

The affordance names a tool, and the two surfaces have different call syntax.
`narrate_units` and `narrate_builder_tasks` take `surface: str` — `"mcp"` or
`"arena"` — replacing the interim `tool_hints: bool` added when the builder
board's arena syntax leaked into the MCP board:

| Affordance | `surface="arena"` | `surface="mcp"` |
|---|---|---|
| GP activation | `activate_great_person with {"unit_id": 65541}` | `unit_action(unit_id=65541, action="activate")` |
| Improvement | `improve_tile with {"unit_index": 5, "improvement_name": "IMPROVEMENT_MINE"}` | `unit_action(unit_id=65541, action="improve", improvement="IMPROVEMENT_MINE")` |
| Upgrade | `upgrade_unit with {"unit_id": 65541}` | `unit_action(unit_id=65541, action="upgrade")` |
| Promotion | `get_unit_promotions with {"unit_id": 65541}` | `get_unit_promotions(unit_id=65541)` |

`unit_index` appears only in arena syntax; MCP always uses the composite
`unit_id`. This supersedes `tool_hints`, which suppressed the builder hints
on MCP entirely — an MCP agent gets the affordance too, in its own syntax.
Callers: `server.py:864` and `server.py` `get_units` pass `surface="mcp"`;
`arena/registry.py:71` and its units renderer pass `surface="arena"`.

Suppression rules already established for the builder board carry over
unchanged and apply to every affordance: no hint for a unit with no moves
left, and no hint naming an improvement the Lua scan could not map
(`"UNKNOWN"`).

## Testing

- Tier pins: `minimal` exact ordered 15-tuple (frozen), `standard` exact
  ordered 28-tuple.
- `filter_tools` drops `activate_great_person` when `caps["gp_unit"]` is
  false and keeps it when true.
- Parser test: a units response line with the new field yields
  `can_activate_here=True`; a malformed or absent field yields `False`.
- Narration tests per surface: arena output contains the arena call and the
  MCP output contains the `unit_action` form, and neither contains the
  other's syntax — the regression that the builder board shipped with.
- Suppression tests: no affordance for a zero-move unit, none for
  `"UNKNOWN"`.
- Audit test: after the change, the `minimal` absent-verb list contains both
  `improve` and `activate_great_person`, and the `standard` list contains
  neither.
- Full arena suite green before each commit.

## Out of scope

- Adding any verb the capability inventory ranks as missing; that spec
  produces the list, and closing it is separate work.
- `recruit_great_person` / `patronize_great_person` tier changes.
- Affordances requiring a new legality probe — settle-site validity, trade
  destinations, missionary spread targets. Each needs an engine call this
  codebase does not currently make safely.
- Changing `full`, which is the whole registry by construction.
