# arena post-v5 fixes — Design

**Date:** 2026-07-26 · **Status:** approved (design presented and accepted in
session)

## Context

The arena-channels-behavior series closed v5 with the deal lifecycle solved
(2/2 honored, 0 grievances; `arena_runs/arena-channels-behavior-v5`,
findings in `docs/research/arena-channels-behavior-v1-findings.md`) but left
three open items, plus an out-of-band observation from the operator:

1. **v5 engagement collapse.** Both LLM seats sent zero messages and
   initiated zero deals (v3: 12 msgs / 1 deal; v4: 8 / 1). Two candidate
   causes, not separable from the v5 data: (a) the `propose_deal` schema
   description gained a discouraging clause in `588eb91`; (b) v5 had no
   failures to negotiate about, and prior runs' messaging was failure
   chasing.
2. **Idle builders (operator observation, root-caused).** The qwen seat's
   builders wandered without ever improving, building, or repairing a tile
   across v3–v5. Transcripts show zero `improve_tile` calls AND zero
   out-of-tier rejections: the `minimal` tier offers no `improve_tile`,
   `get_builder_tasks`, or `remove_feature`, so the actions did not exist in
   the models' world. Additionally the registry has **no repair verb at
   all** — pillaged-improvement repair is impossible at any tier.
3. **Stale skill doc.** The tracked skill source
   `tools/skills/civ6-arena-live/SKILL.md` (mirrored locally under the
   gitignored `.claude/skills/` directory) still documents `origin` as the
   `.141` non-bare checkout with a push-branch-then-ff-merge dance; `origin`
   is now `git@github.com:wrislin1/civ6-mcp.git` and plain
   `git push origin main` is the landing path.

The operator also asked for a **proactive sweep**: find any other game
actions the arena tool surface lacks, rather than discovering them one
attended run at a time.

## Deliverables

### 1. v6 ablation artifact

Isolate the schema-clause variable.

- `src/civ_mcp/arena/channel_protocol.py`: the `propose_deal` description
  loses exactly this text (including the leading space):
  ` If you want to be PAID for a favor you perform, do not use this — send
  a message asking the other player to propose the deal to you.`
  The retained description is: `Propose an unofficial favor-for-gold deal.
  YOU are the payer: you pay payment_gold and to_player performs the
  favor.` The first sentence stays because it fixes v4's inverted deal
  object and is informative, not discouraging.
- `experiments/arena-channels-behavior-v6.yaml`: byte-identical to v5
  except `run_id: arena-channels-behavior-v6`. A raw-byte test replaces
  exactly the v5 `run_id` line and asserts that the resulting bytes equal
  the v6 file; the loader test also checks the parsed v6 run id and dataclass
  equality after normalizing it to v5. P3 keeps `auto_accept: true`, so if
  initiative returns the previously unexercised auto-accept path gets live
  traffic.
- The prediction is already preregistered in
  `docs/research/arena-channels-behavior-v1-findings.md`: messaging or
  initiative returns → cause (a); the run stays quiet → cause (b), and the
  collapse is benign. Running v6 and appending its result remain out of
  scope.

### 2. Tool-coverage audit and `standard` enrichment

- **Audit doc** `docs/research/arena-tool-coverage-audit.md` compares three
  surfaces:
  1. `TOOL_REGISTRY` in `src/civ_mcp/arena/registry.py` (89 tools before
     this change, 90 after adding `repair_improvement`);
  2. the `GameState` methods reached by public MCP tools, with the
     `unit_action` cases treated as the authoritative game-action list;
  3. the unit-action table in `CLAUDE.md`.
  The audit separates direct unit-action gaps from composed/internal MCP
  helpers and lifecycle/ops exclusions. It also records tier membership for
  every registry tool, with a disposition per gap:
  `add-to-standard-now` / `listed-for-later` /
  `intentionally-excluded`.
  The complete pre-change unit-action gap set is `repair`, `sleep`, `delete`
  (disband), Military Engineer `build_route`, `remove_improvement`, and
  `sacrifice_charges`. The audit must not report ordinary helper methods
  such as `get_game_identity` as missing player actions.
- **`CLAUDE.md` unit-action table** gains the three MCP actions it currently
  omits: `repair`, `remove_improvement`, and `sacrifice_charges`. This is
  documentation parity only; it does not add arena tools.
- **`minimal` is frozen** exactly as v1–v5 ran it, pinned by a snapshot
  test asserting its exact ordered 15-tool tuple, with a comment explaining
  the freeze (re-running historical artifacts must offer the same world).
  Strengthen the existing snapshot test rather than adding a duplicate.
- **`standard` gains exactly `get_builder_tasks` and
  `repair_improvement`.** The disposition rule is deliberately narrower
  than "all read-only tools": add routine builder-workflow discovery and
  non-destructive builder actions that already have `GameState`/Lua
  support. Destructive (`remove_improvement`, `delete`), specialized
  (`sacrifice_charges`, `build_route`), passive (`sleep`), and strategic
  tools remain `listed-for-later`.
- A snapshot test pins `standard`'s exact ordered 26-tool tuple.
- `src/civ_mcp/arena/vocab.py` gains the required
  `repair_improvement: repair` mirror entry. This is registry metadata
  parity, not an analyzer-logic or rubric change.
- No experiment artifact changes for this deliverable; future
  empire-behavior experiments opt in with `tools: standard`.

### 3. Skill-doc housekeeping

Rewrite the "Landing code on `.141`" section of the tracked canonical file
`tools/skills/civ6-arena-live/SKILL.md`: `origin` is GitHub
(`git@github.com:wrislin1/civ6-mcp.git`); landing work is commit to `main`
and `git push origin main`; remove the push-branch/ff-merge instructions
and the "no GitHub remote on riz-llm" claim. Mirror the same content into
`.claude/skills/civ6-arena-live/SKILL.md` when that ignored local copy
exists, and verify the two files match. The FireTuner/watcher operational
sections are accurate and untouched.

## Testing

- v6 artifact test: raw-byte equality against v5 after one exact `run_id`
  substitution, plus successful loading, the v6 run-id value, and parsed
  equality after normalizing the run id to v5.
- Guidance/schema test: the `propose_deal` description equals the retained
  text exactly.
- Tier pins: `minimal` exact ordered tuple (frozen); `standard` exact
  ordered tuple (enriched).
- Registry dispatch and invalid-argument tests for `repair_improvement`,
  plus the existing registry↔vocabulary exact-mirror test.
- Full arena suite green before each commit.

## Out of scope

- Running v6 (attended, scheduled separately).
- Adding arena action verbs beyond `repair_improvement`; no new
  `GameState`/Lua capabilities.
- Analyzer logic or rubric changes.
- Manual curation of `full`; it remains the whole registry by construction
  and therefore picks up `repair_improvement` automatically.
