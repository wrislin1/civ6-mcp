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

The exact standard ordering inserts `get_great_people` followed by
`activate_great_person` immediately after `repair_improvement`. The activation
tool is self-limiting: it carries `requires="gp_unit"` (`registry.py:1249`),
and `filter_tools` (`registry.py:1451`) drops it from the schema for any seat
that owns no Great Person. `get_great_people` is an ungated read, so a seat
without a Great Person still gains that one discovery tool but no unavailable
activation action.

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

The machine-readable field is
`tier_action_verbs_absent: {"minimal": [...], "standard": [...]}`. These are
the registry's raw verbs, without MCP alias normalization, so the list uses
`activate_great_person`, not `activate`. The human report and
`docs/research/arena-tool-coverage-audit.md` are refreshed to the new
`standard=28` count and exact tier membership.

### 3. Reachability-aware AVAILABLE NOW affordances

`UnitInfo` gains `can_activate_here: bool`, set by the units Lua scan: for a
unit whose `GameInfo.Units[…].GreatPersonClass` is non-empty, true when the
unit's own plot index appears in `gp:GetActivationHighlightPlots()`. The
lookup is wrapped in `pcall` and defaults false, matching how the units query
already guards optional engine calls.

The arena dispatch path passes the already-resolved `allowed` tool tuple to
the `get_units` renderer as internal context; this adds no user-visible tool
parameter. `narrate_units` accepts that context as
`available_tools: Collection[str] | None`. On the arena surface, an exact call
hint is emitted only when its tool name is in that tuple. This preserves the
frozen minimal world: minimal sees no calls to unavailable builder, GP, or
upgrade tools; standard sees improvement and activation calls; full may also
see upgrade calls. Capability-filtered tools are absent from the same tuple,
so reachability and narration cannot disagree. The dispatcher translates
`allowed=None` to the full registry before rendering; a direct
`narrate_units(surface="arena", available_tools=None)` call fails closed and
emits no exact action calls.

The opening briefing is a second units-rendering path and must preserve the
policy's actual tool surface. `LLMPolicy` (local OpenAI-compatible models)
passes `surface="arena"` and its filtered `visible_names` through
`maybe_build_briefing` / `build_briefing` to the units section renderer.
`CLIAgentPolicy` (`cli-claude` / `cli-codex`) passes `surface="mcp"` because
those subprocesses use the MCP server rather than the arena registry. A
supplied briefing is already rendered and is not rewritten. This prevents a
briefing from falling back to the wrong call syntax or advertising a tool
outside a local seat's tier.

The existing `>> CAN ATTACK:` and generic `>> Can build:` lines remain
unchanged. After them, `narrate_units` renders one line per reachable,
executable non-combat call, following the existing `>>` convention:

```
  Bhasa (UNIT_GREAT_WRITER) at (12,19) — moves 2/2 [id:65541, idx:5]
    >> AVAILABLE NOW: activate_great_person with {"unit_id": 65541}
```

Multiple valid improvements produce multiple exact call lines. The calls are
populated only from engine-safe signals already collected or newly added
here: great-person activation (`can_activate_here`), tile improvement
(`valid_improvements`), and upgrade (`can_upgrade`). Promotion is deliberately
not included: the live units query hard-codes `needs_promotion=false` because
only the end-turn GameCore blocker is authoritative. No new legality probe is
introduced. In particular nothing calls `CanStartOperation` on remote tiles,
which the units Lua documents as corrupting engine state and crashing
`end_turn` (`lua/units.py:1954-1956`).

### 4. Surface-appropriate call syntax

The affordance names a tool, and the two surfaces have different call syntax.
`narrate_units` and `narrate_builder_tasks` take
`surface: Literal["mcp", "arena"] = "mcp"`, replacing the interim
`tool_hints: bool` added when the builder board's arena syntax leaked into the
MCP board. Any other value raises `ValueError`.

| Affordance | `surface="arena"` | `surface="mcp"` |
|---|---|---|
| GP activation | `activate_great_person with {"unit_id": 65541}` | `unit_action(unit_id=65541, action="activate")` |
| Improvement | `improve_tile with {"unit_index": 5, "improvement_name": "IMPROVEMENT_MINE"}` | `unit_action(unit_id=65541, action="improve", improvement="IMPROVEMENT_MINE")` |
| Upgrade | `upgrade_unit with {"unit_id": 65541}` | `upgrade_unit(unit_id=65541)` |

`unit_index` appears only in arena syntax; MCP always uses the composite
`unit_id`. This supersedes `tool_hints`, which suppressed the builder hints
on MCP entirely — an MCP agent gets the affordance too, in its own syntax.
Callers: `server.py:864` and `server.py` `get_units` pass `surface="mcp"`;
`arena/registry.py:71`, its units renderer, and
`arena/briefing.py` pass an explicit surface. Local arena-registry
`get_units` and newly built `LLMPolicy` briefings additionally receive the
filtered tool tuple as `available_tools`; MCP server and `CLIAgentPolicy`
callers use `surface="mcp"` without arena-only reachability context.

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
- Reachability tests: minimal's `get_units` output contains no
  `improve_tile`, `activate_great_person`, or `upgrade_unit` call; standard
  contains improvement and activation calls when their signals are true but
  not upgrade; full contains all signaled calls.
- Briefing tests assert arena syntax and the same minimal/standard/full
  reachability behavior for `LLMPolicy`, including the fail-closed no-context
  case, while CLI policy briefings retain MCP syntax.
- Suppression tests: no affordance for a zero-move unit, none for
  `"UNKNOWN"`, and no promotion affordance from `needs_promotion`.
- Audit test: after the change, the `minimal` absent-verb list contains both
  `improve` and `activate_great_person`, and the `standard` list contains
  neither.
- A deterministic audit-doc test confirms the checked-in
  `docs/research/arena-tool-coverage-audit.md` count and the
  `get_great_people` / `activate_great_person` tier rows match script output.
- Full arena suite green before each commit.

## Out of scope

- Adding any verb the capability inventory ranks as missing; that spec
  produces the list, and closing it is separate work.
- `recruit_great_person` / `patronize_great_person` tier changes.
- Affordances requiring a new legality probe — settle-site validity, trade
  destinations, missionary spread targets. Each needs an engine call this
  codebase does not currently make safely.
- Changing `full`, which is the whole registry by construction.
