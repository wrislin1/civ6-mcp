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
3. **Stale skill doc.** `.claude/skills/civ6-arena-live/SKILL.md` still
   documents `origin` as the `.141` non-bare checkout with a
   push-branch-then-ff-merge dance; `origin` is now
   `git@github.com:wrislin1/civ6-mcp.git` and plain `git push origin main`
   is the landing path.

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
  except `run_id: arena-channels-behavior-v6`. Pinned by a loader test
  asserting `replace(v6, run_id=v5.run_id) == v5` (same pattern as the
  v4→v5 pin). P3 keeps `auto_accept: true`, so if initiative returns the
  previously unexercised auto-accept path gets live traffic.
- Prediction recorded in the findings doc when the run happens (the run
  itself is out of scope): messaging/initiative returns → cause (a); still
  quiet → cause (b), collapse is benign.

### 2. Tool-coverage audit and `standard` enrichment

- **Audit doc** `docs/research/arena-tool-coverage-audit.md` comparing
  three surfaces:
  1. `TOOL_REGISTRY` (89 tools) in `src/civ_mcp/arena/registry.py`;
  2. the public action methods of `GameState`
     (`src/civ_mcp/game_state.py`) that the MCP server exposes;
  3. the unit-action table in `CLAUDE.md`.
  The doc lists every game-reachable action absent from the registry, and
  every registry tool absent from each tier, with a disposition per gap:
  `add-to-standard-now` / `listed-for-later` / `intentionally-excluded`
  (ops tools like save/load/kill stay excluded).
  Known gaps going in (audit confirms and completes): `repair`, `sleep`,
  `delete` (disband), Military Engineer `build_route`.
- **`minimal` is frozen** exactly as v1–v5 ran it, pinned by a snapshot
  test asserting its full 15-tool contents, with a comment explaining the
  freeze (re-running historical artifacts must offer the same world).
- **`standard` gains `get_builder_tasks`** plus any gap the audit
  dispositions as `add-to-standard-now`. The decision rule for that
  disposition: read-only tools, and builder-class action verbs (repair
  included if `GameState` already supports it without new game-side code).
  Strategic new verbs (espionage, WC, religion, etc. already exist in
  `full`) and anything needing new `GameState`/Lua work are
  `listed-for-later`.
- A snapshot test pins `standard`'s new contents.
- No experiment artifact changes for this deliverable; future
  empire-behavior experiments opt in with `tools: standard`.

### 3. Skill-doc housekeeping

Rewrite the "Landing code on `.141`" section of
`.claude/skills/civ6-arena-live/SKILL.md`: `origin` is GitHub
(`git@github.com:wrislin1/civ6-mcp.git`); landing work is commit to `main`
and `git push origin main`; remove the push-branch/ff-merge instructions
and the "no GitHub remote on riz-llm" claim. The FireTuner/watcher
operational sections are accurate and untouched.

## Testing

- v6 loader test: equality-minus-`run_id` against v5, plus run_id value.
- Guidance/schema test: `propose_deal` description contains "YOU are the
  payer" and does not contain "do not use this".
- Tier pins: `minimal` full-contents snapshot (frozen); `standard`
  full-contents snapshot (enriched).
- Full arena suite green before each commit.

## Out of scope

- Running v6 (attended, scheduled separately).
- Adding action verbs beyond the audit's `add-to-standard-now` rule; no
  new `GameState`/Lua capabilities.
- Analyzer changes.
- Changes to `full` (it is the whole registry by construction).
