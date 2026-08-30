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

---

# v3: both levers land — deal machinery engages, payment step is the wall

**Date:** 2026-07-26 · **Run:** `arena_runs/arena-channels-behavior-v3` ·
**Config:** `experiments/arena-channels-behavior-v3.yaml` (implementation
`49f9903..02fbfa2`) · v2 baseline plus the two reserved levers applied
together: directive guidance naming the deal actions, and a scripted P3
opener proposing a formal deal to each LLM seat on turn 157.

## Result: all three criteria met

- 90/90 puppet turns over game turns 157–186, zero failed turns, $0.00
  (gemma 1.22M prompt / 9.6k completion; qwen 2.56M / 50.6k).
- **3 deal objects, 12 messages, 26 acknowledgements, 2 grievances.**
- **Primary** (≥1 `respond_to_deal` per LLM seat): met at T158 — both seats
  accepted the scripted opener one turn after it landed.
- **Secondary** (≥1 deal terminal honored/settled): met at T165 —
  `deal-000001` completed the full lifecycle.
- **Stretch** (LLM-initiated `propose_deal`): met at T166 — qwen proposed
  `deal-000003` to gemma unprompted.

v2 produced zero deal actions in 30 game turns, not even an invalid one.
v3 produced 14 applied deal actions and 7 rejected ones in the same span.

## The three deals

| Deal | Parties | Arc | Outcome |
|------|---------|-----|---------|
| `deal-000001` | P3 → gemma | proposed T157, accepted T158, favor satisfied T163, auto-funded T163, payment accepted T165 | **honored / settled** |
| `deal-000002` | P3 → qwen | proposed T157, accepted T158, favor satisfied T163, auto-funded T163, no payment response | **broken** (grv-000001) |
| `deal-000003` | qwen → gemma | proposed T166, accepted T167, funded T169, no payment response | **broken** (grv-000002) |

`deal-000003` is the notable one: qwen proposed, gemma accepted, qwen
funded — a complete LLM-to-LLM negotiation with no scripted involvement at
any step. It failed only at the counterparty's payment acknowledgement.

Both favors were verified deterministically from real unit observations
(`keep_units_away`, evidence `obs-000038`), not self-report. Both LLM civs
genuinely kept military units ≥3 tiles from P3 for the full window.

## The payment step is the single point of failure

Both LLM-side deals died on the same action, and it is the one action the
v3 directive never names:

| Grievance | Turn | Offender | Reason |
|-----------|------|----------|--------|
| `grv-000001` | T165 | qwen | exact linked payment not accepted by deadline |
| `grv-000002` | T171 | gemma | exact linked payment not accepted by deadline |

All 7 rejected channel actions cluster around this gap:

- **6 × wrong-role `fund_deal`** (gemma 4, qwen 1, plus qwen's inverse
  error) — every one from a player who was *not* the proposer. Not one
  came from a player who actually owed a payment.
- **1 × `respond_to_deal` by the proposer** (qwen, T167, on its own deal).
- **1 × hallucinated deal id** (gemma, T166, `deal-000010` — never existed).

The guidance sentence is the likely cause. It ends:

> "...fund deals you owe with fund_deal."

"Deals you owe" is ambiguous between owing a *favor* and owing a *payment*.
Both models resolved it toward the favor reading: gemma owed only favors
across all three deals and reached for `fund_deal` on four separate turns.
Meanwhile nothing in the directive tells a proposer with `up_front` timing
to pay before the favor begins, and nothing names `respond_to_payment` at
all — so the receiving side has no vocabulary for the step that terminates
the deal.

Two behaviors qualify the "models can't operate payments" reading:

- gemma **did** find `respond_to_payment` at T165 on `deal-000001`, after
  three rejections — then reverted to `fund_deal` on `deal-000003` four
  turns later and let it break. The success did not transfer to the next
  instance; it was situational search, not learning.
- qwen **did** use `fund_deal` correctly as proposer at T169, on the
  deadline turn, after having misfired it as counterparty at T164.

Both models act at the deadline rather than before it, and both burn
rejections finding the right action.

## Engagement shifted earlier and then collapsed

- First LLM channel action at **T157** (qwen → gemma), versus **T165** in
  both v1b and v2 with the identical roster, save, and step budget. The
  directive guidance appears to remove the warm-up delay.
- All channel activity finished by **T177**. The final 9 game turns
  (178–186) produced zero channel actions — the same burst-then-lull shape
  v2 showed, but with the burst carrying real mechanism instead of chat.
- qwen sent 10 of the 12 messages; 9 went to gemma, which never replied to
  any of them. qwen looped on an unanswered non-aggression thread from
  T157–T163 while handling its actual deals correctly, then at T165
  drafted a deal proposal *as prose* to gemma — and reissued the same
  intent as a real `propose_deal` action one turn later.

## Tooling observations

- The scripted-dispatch stderr instrumentation added before this run
  (`02fbfa2`) worked as designed: two `channel dispatch OK: player=3
  turn=157 action=propose_deal` lines confirmed the treatment fired within
  seconds of arming, rather than after the run.
- **`max_steps: 15` was not binding for qwen.** The analyzer reports an
  average of 20.4 steps and per-turn peaks of 29–32. gemma stayed under at
  8.3 average. Worth investigating before treating step budget as a
  controlled variable again — v1b's step-headroom conclusion may need
  re-examination.
- Truncation incident rate: gemma **15.6%**, qwen 6.0%. gemma truncated on
  roughly one turn in six; its higher error rate may be related.
- Invalid tool-call rate was 0.0% for both seats — channel-layer rejections
  are not counted as invalid tool calls, so the analyzer's invalid-rate
  metric does not surface this run's central failure mode.

## Recommendations for v4

Single-variable change, everything else held at the v3 baseline:

1. **Name every action in the directive**, including `respond_to_payment`,
   and disambiguate the payment sentence — "if you proposed a deal with
   up-front payment, fund it with fund_deal; when a payment is offered to
   you, accept it with respond_to_payment" — replacing "fund deals you owe
   with fund_deal", which misrouted six actions.
2. **Surface per-deal available actions in the channel block.** The
   projection already knows each deal's id, both party ids, and its state;
   rendering "your available actions" per open deal would address the
   wrong-role class and gemma's hallucinated deal id together. This is a
   projection change, not a guidance change, so it should be a separate
   variable from (1) if we want clean attribution.
3. Investigate the `max_steps` overrun before relying on step budget as a
   controlled variable.

---

# v4: the guidance fix corrected action choice and broke action timing

**Date:** 2026-07-26 · **Run:** `arena_runs/arena-channels-behavior-v4` ·
**Config:** `experiments/arena-channels-behavior-v4.yaml` (commit `d70b490`)
· byte-identical to v3 apart from `run_id` (pinned by
`test_arena_channels_behavior_v4_differs_from_v3_only_in_run_id`), so the
revised `CHANNEL_GUIDANCE_TEXT` is the single changed variable.

The v3 closing clause ("fund deals you owe with fund_deal") was replaced
with role-split payment instructions naming `respond_to_payment`:

> "Payment has two sides and only one of them is yours: if you proposed the
> deal, you are the payer — send the gold with fund_deal when payment is
> due. If you accepted someone else's deal, you are the payee — the gold is
> offered to you and you must take it with respond_to_payment before its
> deadline. A payment left unaccepted breaks the deal and earns you a
> grievance."

## Result: outcomes got worse

| metric | v3 | v4 |
|--------|----|----|
| deals | 3 | 3 |
| honored | **1** | **0** |
| broken | 2 | 2 |
| expired | 0 | 1 |
| rejected channel actions | 7 | 4 |
| messages | 12 | 8 |
| grievances | 2 | 2 |

90/90 puppet turns over game turns 157–186, zero failed turns, $0.00.

## The fix worked on action choice and failed on timing

v3's dominant error was six wrong-role `fund_deal` attempts, every one from
a non-proposer. **v4 produced zero of those.** Both models selected
`respond_to_payment` — the correct action for their role — on the first
try. Naming the action and anchoring it to a role did exactly what it was
meant to do.

But all four v4 rejections are the same new error: `respond_to_payment`
fired *before the payment existed* ("deal has no linked payment awaiting
response"). gemma's full attempt history on `deal-000001`:

| turn | payment state | attempt |
|------|---------------|---------|
| T161 | not offered | rejected |
| T162 | not offered | rejected |
| T163 | not offered (P3 funded later in the turn) | rejected |
| T164 | **offered** | none |
| T165 | **offered** | none — deal broke at deadline |

qwen repeated the pattern with a single premature attempt at T163 and
silence through T164–165. Both models exhausted their persistence before
the window opened, and three consecutive rejections evidently taught them
the action was unavailable.

The revised text tells the payee what to do and that a deadline exists, but
never says the gold becomes claimable only *after* the proposer funds it.
Both models treated their own acceptance as the trigger. In v3, gemma's
wrong-action flailing at least kept it engaged into the valid window and it
settled `deal-000001` at T165; in v4 both models had given up by then.

**Prose cannot convey state transitions.** Two runs now show the models can
be told *which* action to use but not *when* it becomes available. That is
the case for rendering per-deal available actions in the projection
(v3 recommendation 2) — only the projection can say "now".

## A third role-confusion class: the inverted deal object

qwen met the stretch criterion again (`deal-000003`, T176), and preceded it
at T175 with an unprompted apology to the party it had wronged — "I
apologize for the broken deal earlier - it was an oversight on my part" —
with the grievance visible in its projection. Emergent reputational repair.

The deal object contradicted its own message:

- **Message:** "I'll keep my military units at least 3 tiles away from your
  lands for 10 turns in exchange for 100 gold, paid up front."
- **Object:** `proposer: 2`, `payment_gold: 100`, `timing: up_front`,
  favor `keep_units_away {player_id: 2}` — i.e. qwen *pays* 100 gold and
  P3 keeps units away from *qwen's* lands.

It meant to sell a service and instead offered to buy one, at double the
scripted rate. The series now has three distinct role-confusion classes:

1. **v3** — wrong role when *selecting* an action (`fund_deal` as payee).
2. **v4** — right action, wrong *moment*.
3. **v4** — correct action, but the *constructed object* inverts the roles
   its own message describes.

The first two are self-correcting in principle: a rejection carries
information. The third produces a valid deal meaning the opposite of the
intent, and nothing flags it.

## Design limitation: the scripted seat cannot accept

`deal-000003` targeted P3, whose `ScriptedPolicy` only dispatches its
turn-157 script and auto-funds deals it proposed — there is no
`respond_to_deal` path. The proposal expired unanswered at T179 (payment
waived, correctly no grievance). v3's LLM-initiated deal went qwen→gemma
and reached funding; v4's went to a structurally mute seat.

Any future artifact testing LLM-initiated deals should either script an
acceptance path for P3 or expect only LLM→LLM proposals to complete.

## Other observations

- Messaging collapsed toward the scripted seat: v3 had 9 qwen→gemma
  messages; v4 had **zero**. All 6 of qwen's messages went to P3. gemma
  sent none in either run.
- `max_steps: 15` "overrun" (qwen 19.7 steps, v3: 20.4) was investigated
  after the run and is **not** an overrun — see the v5 section. `max_steps`
  caps model round-trips; the transcript records one step per tool call.
  v1b's step-headroom conclusion stands.
- Truncation: gemma 12.2% (v3: 15.6%), qwen 6.3% (v3: 6.0%).
- Invalid tool-call rate again 0.0% for both seats despite 4 rejected
  channel actions — the analyzer still does not surface channel-layer
  rejections.

## Recommendations for v5

1. **Render per-deal available actions in the channel projection**, with
   explicit role labels and current state — "deal-000001: a payment of 50
   gold is waiting for you; accept it with respond_to_payment by T165" and,
   at proposal time, "you would PAY 50 gold". This addresses all three
   role-confusion classes and the timing failure in one change, and it is a
   projection change, not a guidance change.
2. Keep the v4 guidance text. It demonstrably fixed action selection; the
   remaining failures are state-visibility problems, not vocabulary ones.
3. Give P3 a scripted acceptance path so LLM-initiated deals can complete.
4. Investigate the `max_steps` overrun (now confirmed across two runs).

---

# v5: implementing the v4 recommendations

**Date:** 2026-07-26 · **Config:** `experiments/arena-channels-behavior-v5.yaml`
· implementation `588eb91`. Not yet run.

All four v4 recommendations are implemented. v5 differs from v4 only by
P3's `auto_accept` flag (pinned by
`test_arena_channels_behavior_v5_differs_from_v4_only_in_run_id_and_auto_accept`);
the behavioral change under test is the projection rendering, which applies
to every channels-enabled run.

## 1. Per-deal affordances in the projection

`deal_action_hint(deal, viewer)` (`channels.py`) renders, under every deal
in the channel block, the viewer's role and the single currently-legal
action. The v4 state that both models failed to act on now reads:

```
- [deal-000001] Player 3 -> Player 1: active; ... payment=50 gold/on_delivery
  (offered); accept by 160, fund by 165, payment response by 165, favor due 163
  YOU ACCEPTED THIS — you are the payee, you receive 50 gold and owe the
  favor; AVAILABLE NOW: respond_to_payment — the 50 gold is waiting for you,
  accept it by turn 165
```

Before the payment is funded the same deal reads `AVAILABLE NOW: nothing —
waiting for Player 3 to fund the payment`, which is the state v4's models
misread as "act now". The role line is conditional before acceptance
(`PROPOSED TO YOU — accept and you become the payee`) and past-tense after.

This targets all three failure classes at once: role (v3), timing (v4), and
the payer/payee direction that v4's `deal-000003` inverted.

## 2. Tool schemas state who may call them

`propose_deal` now says "YOU are the payer: you pay payment_gold and
to_player performs the favor. If you want to be PAID for a favor you
perform, do not use this." `fund_deal` and `respond_to_payment` name the
eligible role and the required payment status. v4's inverted deal object
was constructed against a schema whose description was "Propose an
unofficial favor-for-gold deal" — silent on direction.

## 3. Scripted seats can answer (`channels.auto_accept`)

Opt-in per civ. When set, `ScriptedPolicy` accepts deals proposed to it
(within `accept_by_turn`) and accepts payments offered to it (within
`payment_response_by_turn`), skipping terminal deals. Default `False`
leaves every existing artifact unchanged. This exists because v4's
`deal-000003` — the only LLM-initiated deal of the run — expired
unanswered: the scripted seat had no respond path at all.

## 4. `max_steps` resolved: a metric mismatch, not an overrun

`agent.py:198` loops `for _ in range(max_steps)` over **model replies**,
while `agent.py:285` appends one transcript step per **tool call**, inside
a loop over `reply.tool_calls`. One reply can carry several calls, so
qwen's ~15 round-trips were recorded as ~20 "steps".

The analyzer now reports both. On the real v4 run:

| player | model | max_steps | avg model turns | avg tool calls |
|--------|-------|-----------|-----------------|----------------|
| 1 | gemma4-26b | 15 | 10.7 | 10.7 |
| 2 | qwen3.6-27b | 15 | **12.4** | 19.7 |

qwen never exceeded the cap. **v1b's step-headroom conclusion stands** —
raising 10→15 gave real additional round-trips. The v4 findings entry above
has been corrected.

## Also: the analyzer now counts rejected channel actions

Both v3 and v4 reported `invalid call rate: 0.0%` while every LLM-side deal
died on a rejected channel action — channel dispatch never reaches the tool
layer, so the per-player metric could not see it. The channels section now
reports `Rejected channel actions` with a by-reason breakdown, which for v4
is the four premature `respond_to_payment` calls.

## What v5 tests

Whether state-explicit affordances succeed where vocabulary alone did not.
The specific prediction: the four premature `respond_to_payment` calls
should disappear, both scripted deals should reach honored/settled, and any
LLM-initiated deal aimed at P3 should now terminate rather than expire.

If the payment step still fails with the role, the action, the deadline,
and the current legality all stated explicitly on every turn, the
constraint is not prompt-side and the next lever is model capability
(the v1 recommendation 4 that has never been tried: larger local models).

---

# v5 result: both deals honored, and LLM channel initiative vanished

**Date:** 2026-07-26 · **Run:** `arena_runs/arena-channels-behavior-v5` ·
**Config:** `experiments/arena-channels-behavior-v5.yaml` (implementation
`588eb91`) · 90/90 puppet turns over game turns 157–186, zero failed turns,
$0.00.

## Result: the deal lifecycle is solved; engagement collapsed

| metric | v3 | v4 | v5 |
|--------|----|----|----|
| deals | 3 | 3 | **2** |
| honored | 1 | 0 | **2** |
| broken | 2 | 2 | **0** |
| expired | 0 | 1 | 0 |
| grievances | 2 | 2 | **0** |
| rejected channel actions | 7 | 4 | **2** |
| messages | 12 | 8 | **0** |
| LLM-initiated deals | 1 | 1 | **0** |

Both scripted deals completed the full lifecycle — proposed T157, accepted
T158, favor verified satisfied T163, auto-funded T163, payment accepted
T164. First clean sweep of the series, and the first run with no grievance.

**Both LLM seats sent zero messages.** The only two messages in the ledger
are P3's two deal texts. All channel activity ended at T164; the remaining
22 game turns produced nothing.

## What the affordance line did and did not do

It **redirected** action when a legal action existed. gemma settled
`deal-000001` on the turn its line flipped to `AVAILABLE NOW:
respond_to_payment — the 50 gold is waiting for you, accept it by turn
165`. In v4, the same model on the same deal with the same deadline made
three premature attempts and then went silent through both valid turns.

It **did not suppress** illegal attempts. Both rejections came from models
acting against an explicit line in their own context:

- T162 qwen → `fund_deal`, while its block read `YOU ACCEPTED THIS — you
  are the payee … AVAILABLE NOW: nothing — you owe the favor, due turn 163`.
  This is v3's error class, which v4's guidance had eliminated entirely.
- T163 gemma → `respond_to_payment`, while its block read `AVAILABLE NOW:
  nothing — waiting for Player 3 to fund the payment`.

So the mechanism is narrower than "the models now read state": **naming the
currently-legal action gets it taken; stating that nothing is available does
not stop an idle model from trying anyway.** Redirection works, suppression
does not.

This also revises the v4 conclusion. "Prose can name an action but cannot
convey when it becomes available" was right about the fix but wrong about
the constraint: vocabulary was never binding at the moment of action —
salience at the moment of legality was.

## The engagement collapse needs an ablation

Two candidate causes, not distinguished by this run:

1. **The `propose_deal` schema change.** Its description gained "If you want
   to be PAID for a favor you perform, do not use this — send a message
   asking the other player to propose the deal to you." That is a
   *discouraging* clause on the only deal-initiating action, added to fix
   v4's inverted deal object. It may have suppressed initiation outright.
2. **Messages in v3/v4 were symptoms of failure.** Most of qwen's traffic
   was chasing an unpaid deal, apologising for a breach, and renegotiating
   after one. v5 had no failures to chase: every deal settled by T164, so
   the thread that generated 8–12 messages never started.

Explanation 2 is consistent with the timing — v3/v4 messaging clustered
after their first breach — but explanation 1 is a change I introduced and
cannot be dismissed. **v6 should revert only the discouraging clause of the
`propose_deal` description** (keeping "YOU are the payer") and change
nothing else. If messaging and LLM-initiated deals return, cause 1; if the
run stays quiet, cause 2 and the collapse is a benign consequence of things
working.

> **Amendment 2026-08-28 (before v6 ran).** v6 and v7 add two operator deltas
> that are not treatments: a scripted seat-0 pilot (`seat0-smoke`, attention
> off) so the run is hands-free, and `max_puppet_turns`/`max_game_turns`
> scaled 90/108 → 120/144 because seat 0 charges the shared puppet budget —
> four seats at 120 slots is the same 30-game-turn window v5 had at 90 over
> three. The LLM seats, the P3 script, and the channel rules are byte-identical
> to v5 (pinned in `test_experiment.py`). v1–v5 had a human at seat 0; the
> human civ is not a channels participant, so this does not touch the
> prediction above. The v6 attempt paused at T161 (`arena_runs/
> arena-channels-behavior-v6-paused-t161`) before any deal window closed and
> is not a result.

`auto_accept` was never exercised: no LLM proposed a deal to P3, so the
path added for v4's expired `deal-000003` saw no traffic.

## max_steps confirmed again

gemma 11.5 model turns / 11.5 tool calls; qwen 14.0 / 18.5. Both under the
cap of 15. Third run consistent with the metric-mismatch explanation.

---

# v6 result: initiative returned minimally, not the deal-making thread

**Date:** 2026-08-29 · **Run:** `arena_runs/arena-channels-behavior-v6` ·
**Config:** `experiments/arena-channels-behavior-v6.yaml` · scripted seat 0,
gemma4-26b at P1, qwen3.6-27b at P2, $0.00.

## Result: the ablation prediction was only partially met

| metric | v3 | v4 | v5 | v6 |
|--------|----|----|----|----|
| deals | 3 | 3 | 2 | **2** |
| honored | 1 | 0 | 2 | **2** |
| broken | 2 | 2 | 0 | **0** |
| grievances | 2 | 2 | 0 | **0** |
| rejected channel actions | 7 | 4 | 2 | **2** |
| LLM messages | 12 | 8 | 0 | **1** |
| LLM-initiated deals | 1 | 1 | 0 | **0** |

Removing the discouraging `propose_deal` suffix restored one unprompted
channel action: qwen sent gemma `Checking for deals` at T157, before P3's
scripted proposals were applied. That is real initiative and rejects the
strict “v6 stays completely quiet” outcome. It did **not** restore an
LLM-initiated deal or the sustained 8–12-message thread from v3/v4.

The clean interpretation is that both v5 hypotheses contributed. The schema
clause probably suppressed some initiation, because the one-line ablation
immediately brought a message back. But most earlier traffic was still a
symptom of lifecycle failure: once both v6 deals completed, neither model
started a new conversation. The suffix was not the sole cause of the collapse.

## Deal result

Both scripted deals followed the same logical lifecycle:

- proposed T157 and accepted by both LLM seats T158;
- `keep_units_away` verified satisfied T163 (`obs-000038` and `obs-000040`);
- payments offered and accepted T163;
- terminal state `honored`, favor `satisfied`, payment `settled`, with zero
  grievances.

qwen made both rejected actions: a premature payment response at T162, then
an attempt at T175 to respond to a deal for which it was not the counterparty.
The latter happened long after both deals were terminal and did not change
state.

## Operational qualifications

This was an attended recovery run, not a clean uninterrupted artifact.
Scripted seat 0 exposed missing deterministic handlers for live empty
production queues, diplomacy, Great Person claims, envoys, dedications, and
governor titles; each was regression-tested and fixed before resuming. A T163
engine payment proposal also reproduced a Civ AI hard interturn. Recovery used
`0_MCP_0163` and preserved the channel ledger, but did not replay the two
engine gold-transfer proposals because replay reproduced the hang. Therefore
`settled` is proven in the channel state machine; the corresponding 50-gold
engine transfers are not claimed as final-timeline evidence.

The intended observation window was 30 game turns (T157–T186). Resume budgets
were reduced from transcript rows, but scripted P3 consumes the shared budget
without emitting a transcript row. That under-counted spent slots across
restarts and extended the final artifact through seat 0 at T193: 36 unique LLM
turns per seat instead of 30, plus a duplicate rolled-back T163 record for each
LLM. No channel messages, deals, or grievances were added after T175, so the
metrics and the ablation conclusion are unchanged at the intended T186 cutoff.
Future resume accounting must include every configured seat, not only
persisted transcript rows.

## Recommendation for v7

Run v7 as preregistered. It changes only `task_tracker.enabled: true` on the
two LLM seats and asks whether harness-level intent tracking produces more
deterministic follow-through. Treat v6's one-message/no-LLM-deal outcome as
the comparison point, and use game-turn/seat accounting rather than transcript
row counts if another resume is required.

---

# v7 result: the task tracker sustained engagement without restoring deal-making

**Date:** 2026-08-30 · **Run:** `arena_runs/arena-channels-behavior-v7` ·
**Config:** `experiments/arena-channels-behavior-v7.yaml` · scripted seat 0,
gemma4-26b at P1, qwen3.6-27b at P2, task_tracker enabled on both, $0.00.

## Result

| metric | v3 | v4 | v5 | v6 | v7 |
|--------|----|----|----|----|----|
| deals | 3 | 3 | 2 | 2 | **2** |
| honored | 1 | 0 | 2 | 2 | **2** |
| broken | 2 | 2 | 0 | 0 | **0** |
| grievances | 2 | 2 | 0 | 0 | **0** |
| rejected channel actions | 7 | 4 | 2 | 2 | **1** |
| LLM messages | 12 | 8 | 0 | 1 | **9** |
| LLM-initiated deals | 1 | 1 | 0 | 0 | **0** |

The single treatment delta from v6 (`task_tracker.enabled: true` on both LLM
seats) coincided with the largest engagement change of the series: qwen sent
nine messages across the window (v6: one; v5: zero). The pattern is one
acceptance note at T159, one unprompted thanks at T167 after payment, then
seven near-identical "our peaceful cooperation continues" status pings to P3
across the final twelve turns (T175, T176, T179, T180, T182, T184, T186).
All nine went to the scripted seat; gemma sent zero
and never replied. So the tracker produced *sustained* engagement — the first
run since v4 with channel traffic after the deals closed — but the content is
formulaic relationship maintenance, not negotiation, and no LLM proposed a
deal. The v3/v4-style LLM deal-making thread remains unrecovered.

Rejected actions dropped to one: gemma attempted to fund `deal-000001` at
T163 as the payee ("only the proposer may fund a deal") — the same inverted
role confusion class documented in v4 — and never repeated it. qwen, whose
premature payment attempts defined v4/v6's rejection rows, made none: it
acted exactly once per lifecycle transition, on the turn the affordance line
offered it. That timing discipline is consistent with the tracker's intent
state carrying "waiting for X" across turns.

## Deal result

Both scripted deals ran the full lifecycle on the final timeline: proposed
T157, accepted T158, `keep_units_away` verified satisfied from real unit
observations at T163, auto-funded T163, payment responses accepted T164–165,
terminal `honored` / payment `settled`, zero grievances. Unlike v6, the two
50-gold engine transfers are part of the surviving timeline: the T163 funding
hard-interturn was recovered live by closing the orphaned AI↔AI diplomacy
sessions over FireTuner (no reload, no rollback), so the engine-level payment
evidence stands for the first time in the series.

## Operational qualifications

Attended recovery run over exactly the preregistered T157–T186 window. Both
LLM seats have exactly one row per turn: 60 LLM rows, no duplicates, no
rollbacks. The transcript's 92 rows are those 60 plus one seat-0 record per
turn and two seat-0 re-admission records where a held turn was repaired and
re-run after a watcher restart (T166, T176); seat-0 terminal states are
`advanced` everywhere except `interrupted` at T163 and `human_pending` at
T165/T169 — turns that nonetheless ended on the final timeline. Three
operational resumes (`.arena-runs/arena-channels-behavior-v7.resume-*.yaml`)
used round-based budget accounting (remaining rounds × 4 seats). Five stalls
required operator input — T163, T165, T166, T169, T176 — in three classes,
none attributable to the treatment:

1. **T163 funding hard-interturn** — same wedge as v6: engine gold transfers
   open AI↔AI diplomacy sessions that the coordinator's drain arm never
   probes (it checks local-player sessions only). The watcher self-terminated
   at `seat0_drain_poll_limit`; `build_close_orphan_sessions()` run manually
   over the freed tuner closed session `2-3#131075` and the interturn
   completed in place.
2. **UI-modal stalls (T165, T169, plus mid-turn queues)** — Gathering
   Storm disaster cinematics and Historic Moment/Inspiration popups block the
   end turn while the blocker radar reads empty (`human_pending` with
   `blockers=[]`). Cleared with a single synthetic ESC per modal; from T170 an
   OCR-gated auto-dismisser handled them without operator input (a T175
   disaster cinematic was dismissed this way and never reached
   `human_pending`).
3. **Silent production repair failure (T166: three cities; T176: capital)** —
   the seat-0 repair logged `set_city_production=... ` as applied while
   tuner readback showed `GetCurrentProductionTypeHash()==0`. Queues were
   refilled manually with readback verification before each relaunch.

## Recommendations before v8

1. Run the orphan-session sweep in the seat-0 drain arm on the existing
   45-poll cadence — removes stall class 1 entirely.
2. Dismiss known engine modals (disaster views, moment timelines) from the
   seat-0 refire path via Lua `UIManager:DequeuePopup`, mirroring the sweep's
   view-hiding block — removes class 2.
3. Add readback verification (`GetCurrentProductionTypeHash() ~= 0`) to the
   scripted production repair and re-issue on mismatch — removes class 3 and
   the false "completed" repair records.
4. On the treatment question: the tracker earns its place in the standard
   tier for lifecycle discipline alone. If the goal is LLM-initiated deals,
   the next single-variable candidate is seeding a non-scripted counterparty
   overture (P3 messaging without a deal attached) rather than further
   schema/tracker changes — both models now handle every *offered*
   transition correctly and initiate none.

## Evidence integrity

The run directory `arena_runs/arena-channels-behavior-v7/` is committed
alongside this section. SHA-256 of the primary evidence files:

- `transcript.jsonl` — `b988205983f0820244abcaea6dadccf2371aea70b5feaca01d0bd23d010ecc74`
- `arena_cost.jsonl` — `b115da1bf2da2a3f70831848fcabccefa24f58eb870aa949ac00c580cdd70420`
- `channels/events.jsonl` — `a2fb93c78b8f43daaa5df4f9bcb8f02786eaf86b7b3cb1af04529caca3be2b62`
- `channels/state.json` — `0e02cf1a7f402d6164af3788d43c12bc10053b21ddbba2ce0fde03b54f10dbae`
- `report.md` — `241ce3dfaa3a56906f6d42907dc331ab470967b07962edeef9bf007ff7229845`
- `report.json` — `8a0582e636cae7c68de636aeffaa84e8165d093f4a87e4d6f1664dc6fda6b47b`
