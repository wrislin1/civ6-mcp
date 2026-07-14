# Arena Autonomous Seat-0 Piloting — v1, Attended (Design)

**Date:** 2026-07-14
**Status:** Design approved section-by-section in brainstorming session (riz, this date); document pending riz's separate-session spec review before `superpowers:writing-plans`.
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

## Design

### Configuration

- Seat 0 becomes a normal `players:` entry in the experiment YAML (same `PlayerSpec`
  as puppets: backend, model, max_steps, …). `seat0_piloted = 0 in configured
  players` — no new top-level flag.
- **Validation:** `0 in puppet_ids` is a config error (the self-loop config is
  rejected, not silently repaired). Seat 0 is **never** in the hook inject list.
- Seat 0's spec participates in the config `fingerprint()` (`config.py:71`) so runs
  are distinguishable.
- Run length control stays `max_game_turns`; seat-0 turns consume the shared
  `max_puppet_turns` budget and count in `game_turns`. Seat-0 activity refills
  `deadline_polls` exactly as captured puppet turns do.

### Hook additions (`hook.py`)

- **POLL_LUA reports seat 0:** add `Players[0]:IsTurnActive()` (and current turn) to
  the poll output so the coordinator can see "seat 0's turn has started" and "the
  turn flipped after end-turn" from the same GameCore poll it already runs.
- **`hook.end_turn(conn)`:** thin async wrapper around `lq.build_end_turn()`. This
  is the **one InGame op in hook.py** — it must go through `conn.execute_write`,
  deviating from the module's all-GameCore rule; the deviation is documented at the
  definition. It is only ever called while seat 0 is local and turn-active (which,
  for a never-grabbed seat 0, is simply "while `Players[0]:IsTurnActive()`").

### Coordinator seat-0 branch

Gating: fires only when (a) no puppet poll is pending service, (b) the poll shows
seat 0 turn-active, and (c) seat 0 has not already been played this game turn.

Flow per seat-0 turn:

1. **Snapshot** (same pre-turn overview the puppet path takes).
2. **Policy plays the turn** — same policy interface as puppets. For `cli-claude`,
   the existing exclusive-tuner handoff is reused, with **`end_turn` stripped from
   the pilot's MCP config** (same sandbox-layer pattern as the arena's `run_lua`
   removal) so the pilot cannot enter `execute_end_turn`'s blocking wait.
3. **Deterministic blocker sweep** (below) — runs regardless of policy outcome.
4. **Autosave** — reuse the autosave module; this is the attended-recovery anchor.
5. **`hook.end_turn(conn)`** — coordinator fires ACTION_ENDTURN.
6. **Back to polling.** No blocking wait; the flip shows up in the next polls.

### Blocker sweep (deterministic backstop)

After the policy returns (or fails), query `build_end_turn_blocking_query()` and
resolve every blocker with boring defaults so ACTION_ENDTURN can succeed:

- Units awaiting orders (`ENDTURN_BLOCKING_UNITS`) → `finish_units(0)`.
- Promotions → `autoresolve.sweep_promotions` (existing).
- Research/civic empty → cheapest available.
- Production empty → first item in the city's list.
- Policy slots → fill with first legal cards.
- Remaining families enumerated at **plan time** from what actually fires in turns
  1–50 (open item below).

This table is deliberately **distinct from attention's `BLOCKER_IGNORE`**: that list
means "a sleeping *puppet* may ignore this because the sleep path finish_units()es
the seat"; the seat-0 sweep must actively *resolve* — an ignored blocker here would
leave the turn un-endable. Do not unify the two lists.

### Pilot policies

- **ScriptedPolicy** (existing, `coordinator.py:125-134` observe+skip): live
  gate stage 1, pure turn-cycling.
- **cli-claude**: exclusive handoff + stripped `end_turn`, as above.
- **Local LLM** (llama.cpp gateway): same interface; the eventual all-local
  configuration for GM-era runs.

### State, attention, transcript

- **Memory + task tracker** work unchanged with `player_id 0` (both are keyed by
  player id; no special-casing found).
- **Attention is forced OFF for seat 0 in v1**, for two independent reasons: the
  sleep path ends with `restore_local(0)` (`coordinator.py:412-413`, `:530-531`) —
  wrong for a seat that *is* local and must instead end its turn; and
  `BLOCKER_IGNORE` semantics don't transfer (above). Attention-for-seat-0 is a
  future item, not slice B's.
- **Transcript** records seat-0 turns with `player_id: 0`, `turn_kind: "played"`.
  `analyze`'s treatment of player-0 records is a plan-time item.

## Error handling (degrade-not-abort)

- **Policy failure** → logged; flow proceeds to the blocker sweep and still ends the
  turn. A wedged pilot plays a null turn; it can never stall the game. Mirrors the
  existing failed-puppet-turn guard.
- **Blocker the resolver can't handle** → one retry, then a CRITICAL log naming the
  blocker and transition to **human-pending**: the coordinator stops trying to end
  seat 0's turn, leaves seat 0 local and active, and keeps servicing puppet polls.
  The game is always human-playable when automation gives up. No unbounded
  ACTION_ENDTURN spinning.
- **End-turn fired but the turn doesn't flip** within a grace window of polls:
  - If the poll shows seat 0 **still turn-active** → not mid-AI-phase; safe to
    re-query blockers InGame (a new one may have surfaced, e.g. a World Congress
    session), re-sweep, re-fire. Bounded at 3 total attempts, then human-pending.
  - If seat 0 is **no longer active** but the turn hasn't flipped → AI phase is
    processing; do nothing but GameCore polling. This gate is why POLL_LUA carries
    the seat-0 fields — the InGame re-query must never fire mid-AI-phase.
- **Shutdown invariant unchanged.** The `finally` block still does reclaim →
  `restore_local(0)` → disable. Stopping while seat 0 is mid-turn is now the
  *clean* case (a human seamlessly takes over). Stopping mid-AI-phase with a puppet
  grabbed remains the documented hazard exactly as today — v1 does not touch it;
  hardening is slice B.

### Human takeover

Because seat 0 is never grabbed, takeover requires nothing: pause/stop the watcher
during seat 0's turn and play. This is also slice B's seam — unattended hardening
only automates what the human would do in the human-pending state.

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
- Coordinator seat-0 branch against stub conn/policies (`test_coordinator.py`
  style): happy path, policy failure, blocker-retry exhaustion → human-pending,
  end-turn no-flip paths (both the still-active re-fire and the AI-phase hold-off).
- Blocker-resolver decision table over fixture blocker lists.
- Config validation (seat 0 in `players` OK; seat 0 in `puppet_ids` rejected) and
  fingerprint change.

### Live gate — stage 1 (scripted)

Small game; seat 0 on ScriptedPolicy + 2 local-LLM puppets; 5–10 turns fully
hands-free. Validates: turn flips reliably; the sweep covers what actually fires in
early turns; autosaves land; mid-run human takeover works; clean shutdown during
seat 0's turn.

### Live gate — stage 2 (LLM pilot)

cli-claude (and/or a local model) on seat 0; 10–20 turns. Validates the exclusive
handoff with `end_turn` stripped, real blocker coverage when a pilot plays
properly, and transcript/analyze treatment of player-0 records.

## Out of scope (recorded)

- **Slice B — unattended hardening:** hang watchdog, automatic save-reload
  (`game_launcher` cannot drive the Windows Civ6 from .141's WSL — known
  limitation), long-run resilience, shutdown-while-puppet-grabbed recovery.
- **Attention/sleep for seat 0.**
- **Game master** (parked brainstorm; purpose question A–D open; all pilots local).
- **Channels (roadmap A)** — does not depend on this slice, but benefits from it
  for automated testing.

## Open items for plan time

1. Exact blocker-default table, enumerated from what fires live in turns 1–50.
2. `analyze` treatment of player-0 transcript records.
3. Grace-window size (polls) before the no-flip re-check.

## Process note

Per brainstorming-locals rule 11, implementation lands on an **unmerged branch**
for riz's separate-session review; never merge/push without explicit direction.
