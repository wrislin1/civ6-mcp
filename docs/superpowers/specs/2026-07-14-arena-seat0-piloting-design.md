# Arena Autonomous Seat-0 Piloting — v1, Attended (Design)

**Date:** 2026-07-14
**Status:** Revised design approved section-by-section in riz's separate-session spec review (2026-07-14); written revision pending riz's final file review before `superpowers:writing-plans`.
**Predecessor:** Attention & turn-skipping slice merged and **live-probed** — P1–P4 all passed on a live game (turns 155→225), fixes merged at `480fc8d`, 1001 tests green. This design was re-verified against `480fc8d` after the probe fixes landed.
**Sequencing:** riz inserted this slice **before roadmap item A** (LLM↔LLM unofficial channels, spec `2026-07-09-arena-unofficial-channels-design.md`). That spec's Appendix A carried forward the autonomous-seat-0 findings and named this as its own future brainstorm — this document is that brainstorm's output. The **game-master idea is parked** (its purpose question unanswered); it comes after this slice and presumes all pilots are local LLMs.
**Slice split (riz):** "A then B as its own slice works. As usual we start small, test, then increase complexity."
- **Slice A (this spec): attended short runs** — an LLM (or script) pilots seat 0 for 10–50 turns with a human able to watch and take over.
- **Slice B (future): unattended hardening** — hang watchdog, automatic save-reload recovery, long-run resilience. Not designed here.

## Context & Motivation

The arena can puppet every AI civ seat, but seat 0 — the game's local/human player —
must be a human who clicks End Turn each round. That blocks two things:

1. **Automated testing.** Every live probe and every multi-round run needs a human in
   the loop pressing End Turn. Piloting seat 0 with an LLM (or a script) makes live
   gates and soak runs hands-free.
2. **Fully autonomous games.** The 8-civ smoke (2026-07-05) proved the current
   mechanics cannot run zero-human: configuring seat 0 as a puppet self-loops
   (seat 0 replayed 12/16 turns; seats 5–7 never moved).

### Verified current state (re-verified at `480fc8d`)

- **Puppet turns never end turns.** A puppet turn ends with `hook.finish_units(K)` +
  `hook.restore_local(0)` and **no** `ACTION_ENDTURN` (`coordinator.py:711-712`). The
  DESIGN NOTE at `coordinator.py:706-710` anticipates adding an InGame
  `UI.RequestAction(ActionTypes.ACTION_ENDTURN)` "while local == K, before
  restore_local — NEVER in the finally block".
- **Seat 0 as puppet self-loops.** `restore_local(0)` after playing seat 0 hands
  control back to the seat that just played; a local seat needs a real end-turn
  action to advance. Proven live (8-civ smoke).
- **`hook.py` has no end-turn builder.** Only inject/disable/poll/finish_units/
  restore_local; all are GameCore (`execute_read`, `hook.py:70-75`). The MCP-level
  builder exists at `lua/notifications.py:46-50` (`build_end_turn()` =
  `UI.RequestAction(ActionTypes.ACTION_ENDTURN)`).
- **`gs.end_turn()` cannot be reused for seat 0.** `execute_end_turn`
  (`end_turn.py:492`) fires ACTION_ENDTURN (`:1143`, dispatched InGame via
  `execute_write` at `:1154`) and then **blocks polling for the turn flip**
  (`:1158+`), deliberately GameCore-only during AI processing. A seat-0 pilot
  holding the single FireTuner slot inside that wait starves puppet servicing —
  deadlock by construction. Hence Approach 1 below.
- **Blocker machinery exists and is reusable.** `build_end_turn_blocking_query()`
  (`notifications.py:9`) enumerates all EndTurnBlocking notifications;
  `parse_end_turn_blocking` maps them to guidance. `autoresolve.sweep_promotions`
  is the precedent for a deterministic backstop resolver.
- **Exclusive tuner handoff exists.** `cli_agent.py:110` sets
  `needs_exclusive_tuner = True`; the coordinator honors it (`coordinator.py:193`):
  disconnect, let the CLI's own MCP server own the slot, reconnect after.
- **Post-probe changes (480fc8d) that matter here:**
  - Attention's `BLOCKER_IGNORE` now contains `ENDTURN_BLOCKING_UNITS` as well as
    `ENDTURN_BLOCKING_UNIT_PROMOTION` (`attention.py`). That list means "safe to
    sleep through" for *puppets*. It is **not** the seat-0 resolver table — see
    Blocker sweep below.
  - The attention scan moved GameCore→InGame (`execute_write`) because four scan
    families are nil in GameCore. Fresh evidence that **Lua execution context is a
    correctness issue**, codified in Execution-context discipline below.

## Decisions (riz, this session)

1. **Attended short runs first** (slice A/B split above). Start small, test, then
   increase complexity.
2. **Approach 1: coordinator-owned turn-end.** The coordinator — not the pilot —
   fires ACTION_ENDTURN and never blocks waiting for the flip (it keeps polling as
   it already does). The pilot only plays the turn's content. This dissolves both
   the deadlock and the restore-to-self loop.
3. **Seat 0 is played in place** — never grabbed via `SetLocalPlayerAndObserver`,
   never restored. It is already the local player; the coordinator just acts while
   `Players[0]:IsTurnActive()`.
4. **The pilot retains full decision authority.** The coordinator may clean up
   mechanical residue after the pilot declares the turn complete, but it never
   chooses promotions, research, civics, production, policies, governors, beliefs,
   envoys, dedications, votes, city-capture outcomes, or other strategic actions.
   A bounded focused policy repair pass handles a missed choice; the human is a
   passive observer unless automation reaches a genuine hard block.

## Design

### Configuration

- Seat 0 becomes a normal `players:` entry in the experiment YAML (same `PlayerSpec`
  as puppets: backend, model, max_steps, …). `seat0_piloted = 0 in configured
  players` — no new top-level flag.
- `ArenaConfig.puppet_ids` becomes optional so an intentionally empty puppet set is
  distinguishable from "derive it": `None` derives all configured nonzero player
  IDs; an explicit list is used as-is after validation. Experiment YAML and CLI
  resolution always derive `puppet_ids` by excluding 0.
- **Validation:** `0 in puppet_ids` is a config error (the self-loop config is
  rejected, not silently repaired). Seat 0 is **never** in the hook inject list.
  A seat-0 `PlayerSpec` must set `attention.mode: off`; other modes fail config
  validation instead of being silently ignored.
- Seat 0's spec participates in the config `fingerprint()` (`config.py:71`) so runs
  are distinguishable.
- Run length control stays `max_game_turns`; seat-0 turns consume the shared
  `max_puppet_turns` budget and count in `game_turns`. A repair call is part of the
  same logical turn and consumes no additional budget. Seat-0 activity refills
  `deadline_polls` exactly as captured puppet turns do. Budgets gate admission of
  new policy turns, not completion of one already admitted: after seat 0 consumes
  the last slot, the coordinator still drains that turn to `advanced`,
  `human_pending`, or clean interruption. Before the final seat-0 ACTION_ENDTURN,
  disable the puppet hook while seat 0 is still active; the ensuing AI phase then
  runs normally without capturing a puppet that the exhausted budget cannot admit.

### Hook additions (`hook.py`)

- **POLL_LUA reports seat 0:** add `Players[0]:IsTurnActive()` (and current turn) to
  the poll output so the coordinator can see "seat 0's turn has started" and "the
  turn flipped after end-turn" from the same GameCore poll it already runs.
- **`hook.end_turn(conn)`:** thin async wrapper around `lq.build_end_turn()`. This
  is the **one InGame op in hook.py** — it must go through `conn.execute_write`,
  deviating from the module's all-GameCore rule; the deviation is documented at the
  definition. It is only ever called while seat 0 is local and turn-active (which,
  for a never-grabbed seat 0, is simply "while `Players[0]:IsTurnActive()`").

### Coordinator integration and seat-0 state

Generalize the coordinator's existing captured-turn path to recognize either a
captured puppet or active local seat 0. Policy preparation, memory, task tracking,
snapshots, transcript capture, and exclusive CLI handoff remain shared. Puppet
turns retain priority whenever a poll reports a captured puppet.

A focused `arena/seat0.py` module owns only seat-0 completion mechanics: blocker
classification, repair state, autosave attempt, and non-blocking end-turn requests.
It tracks each game turn through:

`ready → policy_played → end_fired → ai_processing → advanced`

The turn may move directly from `end_fired` to `advanced` when no intermediate AI
phase is observed. Exceptional transitions go to `human_pending` or `interrupted`.
The state prevents the same seat-0 turn from being replayed on every poll:

- Start only when the poll reports seat 0 local and turn-active and the current
  turn is `ready`.
- Seat 0 still active inside the post-request grace window → keep GameCore polling.
- Seat 0 still active after the grace window → recheck blockers and use the bounded
  repair/re-fire flow below.
- Seat 0 inactive while the game turn is unchanged → `ai_processing`; GameCore
  polling only.
- Game turn changes → mark the pending record `advanced`; admit the next turn as
  `ready` only when budget remains.
- `human_pending` → never retry the same turn automatically; reset only after the
  human advances the game turn.

### Policy and blocker flow

The normal pilot call owns every strategic choice and receives the configured arena
toolset except `end_turn`. That restriction already exists in both CLI lockdown
layers (`cli_agent.py` denylist and `server.py`'s `_ARENA_PUPPET_TOOLS`); the
in-process arena registry does not expose `end_turn`.

After the pilot returns:

1. Query `build_end_turn_blocking_query()` while seat 0 is still active.
2. Apply only non-strategic cleanup: `finish_units(0)` after the pilot declares the
   turn complete, dismiss stale notifications whose underlying choice is already
   set, and mark purely informational prompts such as reviewed World Congress
   results as seen.
3. Query blockers again.
4. If a decision blocker remains **or the normal policy call failed**, invoke the
   same pilot once more with a focused `blocker_block` naming the blockers and
   available resolution tools, plus the prior call error when present.
5. Query again. If a decision blocker remains, or the repair call also fails, enter
   `human_pending`; never substitute a coordinator choice.

Decision blockers include promotions, research, civics, production, policies,
governors, pantheon/religion choices, envoys, dedications, World Congress votes,
city captures, stacked units, and any other choice that can affect strategy. A
blocker with no pilot-accessible resolution tool is a hard block and goes directly
to `human_pending`.

The repair pass uses the same model and permissions, cannot call `end_turn`, does
not rerun pre-model tasks, does not create a second logical turn, and runs at most
once per game turn. Its steps and usage merge into the original seat-0 transcript
record. The coordinator holds that record pending until the state reaches a real
terminal outcome. This completion table remains deliberately **distinct from
attention's `BLOCKER_IGNORE`**: that list describes what a sleeping puppet may
ignore, not what seat 0 may resolve without model judgment.

### Autosave and turn-end

After blockers clear and before ACTION_ENDTURN, attempt
`game_lifecycle.save_game(conn, "0_MCP_NNNN")`. This best-effort call is not gated
solely by the Python host OS because WSL may control a Windows game. Save failure is
logged but never blocks progression; the live gate verifies whether the save lands
in the target deployment.

Then call `hook.end_turn(conn)` and return immediately to polling. No code in this
path waits synchronously for the turn flip.

### Pilot policies

- **ScriptedPolicy** (existing, `coordinator.py:125-134` observe+skip): live
  gate stage 1. For seat-0 smoke runs the scripted pilot — not the coordinator —
  makes deterministic research/production decisions and responds to
  `blocker_block`, preserving the policy/host authority boundary.
- **cli-claude**: exclusive handoff + stripped `end_turn`, as above.
- **Local LLM** (llama.cpp gateway): same interface; the eventual all-local
  configuration for GM-era runs.

### State, attention, transcript

- **Memory + task tracker** work unchanged with `player_id 0` (both are keyed by
  player id; no special-casing found).
- **Attention is rejected unless OFF for seat 0 in v1**, for two independent reasons: the
  sleep path ends with `restore_local(0)` (`coordinator.py:412-413`, `:530-531`) —
  wrong for a seat that *is* local and must instead end its turn; and
  `BLOCKER_IGNORE` semantics don't transfer (above). Attention-for-seat-0 is a
  future item, not slice B's.
- **Transcript** emits one record for every attempted seat-0 turn, including
  failures. Normal and repair steps/usage are merged. Records carry `player_id: 0`,
  `turn_kind: "played"` when either policy call completes and `"failed"` when both
  fail, separate normal/repair summaries, blocker snapshots, mechanical cleanup,
  repair result, autosave result, end-turn request count, and terminal state.
  Because the sink is append-only, the coordinator writes the record only at
  `advanced`, `human_pending`, or `interrupted`; `ai_processing` is an intermediate
  state, never a terminal label.
- **Analyze** treats player 0 like every other seat. Current grouping already uses
  `pid is not None`, so `0` is preserved; this needs regression coverage rather
  than seat-specific production behavior. `_turn_kind` is extended to recognize
  explicit `"failed"` while preserving legacy absent-field behavior; config,
  rubric, memory, and other success metrics accept only `"played"`, while failed
  records remain in the series and a separate failed-turn count.

## Error handling (bounded degrade-to-human)

- **Policy failure** → logged; the same policy may receive the one focused repair
  call. A second model/backend failure is a genuine hard block and transitions to
  `human_pending` rather than letting the coordinator make strategic choices.
- **Unsupported or inaccessible blocker** → one CRITICAL structured event naming
  the blocker and immediate `human_pending`.
- **End-turn fired but the turn doesn't flip** within 5 GameCore polls, one second
  apart:
  - If the poll shows seat 0 **still turn-active** → not mid-AI-phase; safe to
    re-query blockers InGame (a new one may have surfaced, e.g. a World Congress
    session), apply mechanical cleanup or the still-unused repair pass, and re-fire.
    Bounded at 3 ACTION_ENDTURN requests total, then `human_pending`.
  - If seat 0 is **no longer active** but the turn hasn't flipped → AI phase is
    processing; do nothing but GameCore polling. This gate is why POLL_LUA carries
    the seat-0 fields — the InGame re-query must never fire mid-AI-phase.
- **Human pending** → emit the CRITICAL event once, leave seat 0 local, and poll
  GameCore once per second. If the observer advances the turn inside
  `idle_poll_limit`, reset and resume when admission budget remains (or finish the
  in-flight drain and exit when it does not); otherwise exit the watcher cleanly.
  No unbounded blocker or ACTION_ENDTURN loop.
- **Shutdown invariant unchanged.** The `finally` block still does reclaim →
  `restore_local(0)` → disable. Stopping while seat 0 is mid-turn is now the
  *clean* case (a human seamlessly takes over). Stopping mid-AI-phase with a puppet
  grabbed remains the documented hazard exactly as today — v1 does not touch it;
  hardening is slice B.

### Human takeover

The human is a passive observer during normal operation. Because seat 0 is never
grabbed, a hard-block takeover requires no control transfer: resolve the blocker
and end the turn. The watcher detects the new turn and resumes when budget remains.
This is also slice B's seam — unattended hardening only automates what the human
does in `human_pending`.

## Execution-context discipline

Codifying what the attention P1 fix proved the hard way:

| Op | Context | Why |
|----|---------|-----|
| hook inject/disable/poll/finish_units/restore_local | GameCore (`execute_read`) | existing rule (`hook.py:70`); safe during AI processing |
| `hook.end_turn` | **InGame** (`execute_write`) | `UI.RequestAction` is a UI-context action; fire only while seat 0 is local + turn-active |
| `build_end_turn_blocking_query` | **InGame** | NotificationManager; query only while seat 0 is turn-active |
| post-end-turn flip detection | GameCore poll only | InGame queries during AI processing stall the diplomacy subsystem (`end_turn.py:1158-1162`) |

## Testing

### Offline (TDD, existing harness patterns)

- Poll parsing with the new seat-0 fields.
- Config derivation and explicit-empty semantics; seat 0 in `players` accepted,
  seat 0 in `puppet_ids` rejected, non-off seat-0 attention rejected, and
  fingerprint coverage.
- Seat-0 state transitions and duplicate-play prevention against stub polls.
- Mechanical-vs-decision blocker classification; focused repair exactly once;
  unresolved repair, policy failure, and inaccessible blocker paths.
- Transcript merge and synthetic failure records; player-0 analysis grouping and
  failed-vs-played metrics.
- Autosave ordering and nonfatal failure; 3-request/5-poll bounds; budget
  admission vs in-flight drain, final-slot hook disable, and human-pending recovery.
- No InGame query/action while seat 0 is inactive.
- Existing puppet, attention, exclusive handoff, cancellation, and cleanup
  regressions remain green.

### Live gate — stage 1 (scripted)

Small game; seat 0 on the scripted smoke policy + 2 local-LLM puppets; 5–10 turns
fully hands-free. The scripted policy itself makes deterministic research and
production choices. Validate: turn flips reliably; focused blocker repair works;
autosave evidence lands (or records the deployment limitation); and shutdown is
clean during seat 0's turn.

### Live gate — stage 2 (LLM pilot)

cli-claude (and/or a local model) on seat 0; 10–20 turns. Validate the exclusive
handoff with `end_turn` stripped, full pilot-owned strategic choices, real blocker
repair, and transcript/analyze treatment of player-0 records.

### Live gate — stage 3 (human escape)

Induce or wait for an unsupported hard blocker. Verify exactly one
`human_pending` event, manually resolve and advance, then confirm the watcher
resumes autonomous play on the next seat-0 turn.

## Out of scope (recorded)

- **Slice B — unattended hardening:** hang watchdog, automatic save-reload
  (`game_launcher` cannot drive the Windows Civ6 from .141's WSL — known
  limitation), long-run resilience, shutdown-while-puppet-grabbed recovery.
- **Attention/sleep for seat 0.**
- **Game master** (parked brainstorm; purpose question A–D open; all pilots local).
- **Channels (roadmap A)** — does not depend on this slice, but benefits from it
  for automated testing.

## Process note

Per brainstorming-locals rule 11, implementation lands on an **unmerged branch**
for riz's separate-session review; never merge/push without explicit direction.
