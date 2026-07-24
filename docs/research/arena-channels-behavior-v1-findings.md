# arena-channels-behavior-v1 — Findings

**Date:** 2026-07-24 · **Run:** `arena_runs/arena-channels-behavior-v1` ·
**Config:** `experiments/arena-channels-behavior-v1.yaml` ·
**Save:** `CHANNELS_GATE_V1_T157` (Korea/Seondeok seat 0, turn 157)

First non-deterministic channels-enabled arena experiment: two local LLM
seats (P1 gemma4-26b with guidance, P2 qwen3.6-27b with guidance) plus a
scripted control seat (P3, channels enabled, no guidance), all admitted to
unofficial channels. Hypothesis under test: with channels available and an
encouraging guidance paragraph in the prompt, LLM agents will use the
channel machinery — send messages, propose deals — during ordinary play.

## Result: clean negative

- **30/30 puppet turns** played across **10 game turns (157–166)**; zero
  failed turns, zero invalid tool calls, watcher exited normally.
- **Zero channel actions.** No messages, no deals, no acknowledgements from
  either LLM seat (`channels/state.json`: `messages: 0, deals: 0`).
- Neither model **mentioned** channels or deals in any assistant text or
  tool argument across all 600 steps — not a single reference.
- Primary success criterion (≥1 channel action per LLM seat): **not met.**
  Secondary (≥1 deal with lifecycle tracking): **not met.**

## The machinery worked

The negative is behavioral, not mechanical:

- `prompt_injections.channels: true` on every LLM turn — the channel block
  (including the guidance paragraph; `civ_options.channels =
  {enabled: true, guidance: true}` for both seats) was rendered into every
  prompt.
- Channel tool schemas were appended to both agents' toolsets
  (`agent.py:132`).
- The channel runtime admitted all three seats and journaled per-player
  observations every turn (`channels/events.jsonl`).
- Thinking-off held: completion tokens stayed in the 200–1200/turn range
  (totals: gemma 2.8k, qwen 5.6k over 10 turns) — no reasoning-budget burn.

## Why the models never engaged (hypotheses)

1. **Step-budget crowding.** qwen used all 10 steps every single turn on
   empire micro (units, cities, research); gemma averaged 8.4. The agent
   loop offers no slack step for channel talk, and nothing in the loop
   sequences channels before empire work.
2. **Attention dilution.** Prompts ran 16–35k tokens of game state; the
   channel block is a small slice with no immediate game-state payoff. 26/27B
   models anchor on the concrete unit/city tasks in front of them.
3. **No inbound stimulus.** The scripted seat never initiates, so the LLMs
   only ever saw an empty channel. Responding to a live message is a much
   weaker ask than initiating into a void.
4. **Guidance is descriptive, not directive.** The paragraph explains what
   channels are for and encourages negotiation but assigns no task.

## Operational notes

- Attempt 1 (archived at `arena_runs/arena-channels-behavior-v1-attempt1`)
  ended after 4 game turns because `max_game_turns: 12` caps *captured turn
  slots* (played+slept+failed), not game-turn boundaries. Fixed to 36 in
  `2e4c602`; attempt 1's 4 turns showed the same zero-engagement pattern.
- Both attempts ran entirely on local gateways (`:11440` GPU0 gemma,
  `:11441` GPU1 qwen), $0.00, no 502s, no GPU contention.

## Recommendations for behavior-v2

Ordered by expected information per unit of change:

1. **Scripted opener:** have the scripted seat send an opening channel
   message (e.g. a deal proposal) to each LLM seat — tests reaction, the
   much lower bar, and exercises deal lifecycle from the receiving side.
2. **Directive guidance:** upgrade the guidance text from "you may" to a
   concrete standing instruction ("each turn, check the channel and respond
   to messages; propose at least one deal when it benefits you").
3. **Step headroom:** raise `max_steps` (e.g. 14) or reserve a dedicated
   channels phase in the agent loop so empire micro can't consume the whole
   budget.
4. Only after 1–3: try larger local models (qwen3.6-35b, gemma4-31b) if
   26/27B still won't engage.
