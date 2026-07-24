# arena-channels-behavior-v1 / v1b / v2 — Findings

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

## Recommendations after v1

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

---

# v1b: step headroom alone flips the result

**Date:** 2026-07-24 · **Run:** `arena_runs/arena-channels-behavior-v1b` ·
**Config:** `experiments/arena-channels-behavior-v1b.yaml` (commit
`8f98479`) · same save, same roster; the single changed variable is
`max_steps` 10 → 15 on both LLM seats (recommendation 3 above).

## Result: primary criterion met

- 30/30 puppet turns over game turns 157–166, zero failures, $0.00.
- **3 channel messages, both LLM seats engaged:**
  - T165, qwen → gemma: "Greetings! I'm interested in peaceful relations
    and potential trade opportunities. What are your thoughts?"
  - T166, gemma → qwen: "I am also open to peaceful relations and trade.
    I'll keep an eye out for opportunities to work together."
  - T166, qwen → gemma: proposes exploring "gold-for-favor deals that
    benefit us both."
- 3 acknowledgements delivered; gemma's turn summary explicitly reasons
  about "Player 2's private message," so the reply was deliberate, not
  noise.
- Secondary criterion (deal with lifecycle tracking): **not met** — the
  run's turn budget expired at T166, right as qwen pivoted from greeting to
  concrete deal terms. No `deal` object was created.

## Interpretation

- **Step-budget crowding was the binding constraint** (v1 hypothesis 1
  confirmed). At 10 steps qwen saturated every turn and never touched
  channels; at 15 it satisfied empire micro first, then initiated channel
  contact unprompted. No guidance change, no scripted opener, no bigger
  model was needed.
- Engagement arrived **late** (turn 9 of 10): agents only reach for
  channels after their per-turn task list is comfortably covered. Deal
  formation needs more wall-clock turns, not more prodding.
- Channel sends ride the dedicated channel dispatch path (`agent.py:250`),
  not the game-tool step list — the channel ledger, not the step log, is
  the source of truth for engagement.

## Recommendations for v2

1. **Longer run:** raise `max_puppet_turns` to 60–90 (20–30 game turns,
   `max_game_turns` scaled to match) so a negotiation that starts around
   turn 9 has room to reach a proposed, accepted, and funded deal.
2. Keep `max_steps: 15` and the current guidance — they are now a validated
   baseline; change nothing else so turn count stays the single variable.
3. Hold the scripted-opener and directive-guidance levers in reserve for a
   v3 if a long v2 stalls at chat without deal objects.

---

# v2: models negotiate in text but never touch the deal machinery

**Date:** 2026-07-24 · **Run:** `arena_runs/arena-channels-behavior-v2` ·
**Config:** `experiments/arena-channels-behavior-v2.yaml` (commit
`c422a33`) · v1b baseline with `max_puppet_turns` 90 / `max_game_turns`
108; turn count the single changed variable.

## Result

- 90/90 puppet turns over game turns 157–186 (30 game turns), zero
  failures, $0.00 (gemma 10.9k / qwen 36.8k completion tokens).
- **5 messages, all applied, zero rejected or invalid channel actions.**
- **0 deals.** The exact v1-recommendation-3 outcome materialized: the run
  "stalls at chat without deal objects."

## The negotiation that happened

A genuinely grounded exchange, not small talk:

- T165 qwen → gemma: offers **100 gold** for removing a skirmisher near
  its units — a payment-for-military-action proposal referencing a real
  board state.
- T166 gemma → qwen: **declines accurately** — "it is not my unit" (true;
  the skirmisher was a barbarian). Honest, factually grounded refusal.
- T179–T180 (13 turns later): the thread resumes around clearing a
  barbarian camp for "a gold payment or other assistance," then fizzles in
  mutual uncertainty — neither model knows the camp's coordinates, and
  qwen backs out ("not in a position to offer military…").

## Interpretation

- **Turn count is no longer the constraint.** With 3× the runway,
  engagement still started at T165 (identical to v1b) and produced only 2
  more messages than v1b. The models treat channels as an occasional
  side-channel, not a standing workstream.
- **The deal machinery is invisible to them in practice.** Both models
  negotiated terms (gold amounts, services) purely in message text and
  never attempted a deal-proposal action — not even an invalid one (zero
  rejections in the ledger). Free-text chat is the path of least
  resistance; nothing pushes them from "discussing a deal" to "creating a
  deal object."
- Negotiation quality is bounded by game-state grounding: the camp thread
  died specifically because neither side could name coordinates —
  information a `get_map_area`-style query could have supplied, but neither
  model thought to fetch in service of the negotiation.

## Recommendations for v3

Turn count and step headroom are now both validated as non-binding. The
remaining levers target deal-object creation directly:

1. **Directive guidance about the deal action:** extend the guidance text
   to name the concrete mechanism — "to make a binding agreement, propose
   a deal with the deal action; message text alone is not binding." The
   models demonstrably want to trade gold for services; they lack the
   bridge from intent to mechanism.
2. **Scripted opener proposing a formal deal:** the scripted seat opens
   with an actual deal object, so both LLMs see the machinery in use and
   must respond to it through the deal lifecycle (accept/decline), not
   chat.
3. Keep roster, steps (15), and 90-turn budget fixed so guidance is the
   isolated variable (or guidance + opener as a combined treatment if we
   accept two levers for a faster answer).
