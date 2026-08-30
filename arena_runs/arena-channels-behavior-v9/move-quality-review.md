# v9 exploratory move-quality review

This is a post-hoc review of `arena-channels-behavior-v9`, requested after
the run was preregistered. It is **not** a v9 treatment outcome and should not
be used as a causal comparison with v7 or v8. The experiment used the
`minimal` tool tier, which does not expose several actions needed for competent
full-game play (notably builder improvement/repair and Great Person
activation). The scores below therefore assess execution within the available
surface, not general Civilization VI skill.

## Method

The review covers all recorded P1/P2 model turns from T157 through T186,
including both T163 attempts around the reload (31 rows per model, 30 distinct
game turns). It separates:

- **schema-invalid calls**: malformed tool calls rejected before execution;
- **domain-rejected actions**: well-formed calls the game rejected, such as
  producing a building already present or moving onto an occupied tile;
- **follow-through**: task-tracker completions/failures and repeated attempts;
- **empire direction**: state changes from the first T157 snapshot to the final
  T186 snapshot, treated as descriptive rather than attributable;
- **end-state sanity**: idle assets, economy, growth, and unresolved damage.

The generated `report.md` says both models had a 0% invalid-call rate. That is
correct for schema validity but does not measure whether the requested move was
legal or useful in the game.

## Results

| measure | gemma4-26b (P1/Khmer) | qwen3.6-27b (P2/Brazil) |
|---|---:|---:|
| distinct game turns | 30 | 30 |
| transcript rows (T163 replay included) | 31 | 31 |
| tool calls | 340 | 834 |
| action calls | 247 | 634 |
| domain-rejected actions | 114 (46.2%) | 181 (28.5%) |
| max-step turns | 12/31 (38.7%) | 14/31 (45.2%) |
| median wall time | 14.0 s | 78.6 s |
| task attempts / completions / failures | 6 / 2 / 0 | 69 / 3 / 9 |
| score, first snapshot to final | 320 -> 377 (+57) | 308 -> 359 (+51) |
| cities | 7 -> 8 | 5 -> 6 |
| units | 13 -> 20 | 8 -> 16 |
| science / culture | 49.7 / 43.1 -> 52.2 / 52.9 | 35.5 / 71.9 -> 40.6 / 82.0 |
| final gold income | -7/turn | -8/turn |

### Gemma: low execution quality

Gemma was fast, but nearly half of its action calls were rejected. The largest
problem areas were production (67/115 rejected), movement (16/33), research
(16/46), and founding (15/15). It repeatedly tried to build structures already
present or unsupported by the city's districts, repeatedly selected completed
technologies, and repeatedly called `found_city` on a non-settler. T171 is a
representative failure: it used all 15 steps, made ten rejected action attempts,
and did not react to the overture.

There was useful activity: 48 accepted production settings, 30 accepted
research selections, 17 accepted moves, and a substantial descriptive score
increase. The final empire nevertheless had an idle Settler, four builders
with charges, an idle Trader, two stagnant cities, many unimproved resources,
negative income, and a projected Dark Age. The city-count increase cannot be
credited to deliberate settlement because no recorded `found_city` call
succeeded.

**Verdict:** fast but brittle and highly repetitive; low move quality within
the available tool surface.

### Qwen: low-to-mixed execution quality

Qwen achieved a lower domain-rejection rate and more accepted actions, but it
used about 2.5 times as many tool calls and took roughly 5.6 times as long per
turn at the median. Its main failures were movement (116/318 rejected) and
production (54/96); all four promotion attempts and all four founding attempts
were rejected. The task tracker generated 69 attempts but only three
completions and nine terminal failures, with builders often oscillating around
occupied target tiles.

The model did show better local awareness than Gemma. It checked pending
diplomacy/trades, fortified military units, kept research calls mostly legal
(18/20 accepted), and explicitly noticed pillaged farms, negative income, and
Dark Age risk. However, the final state still had five builders holding charges
beside pillaged or unimproved tiles, three unused Great Writers, negative
income, and unresolved growth problems. Some of that non-completion is a
minimal-tier capability limitation rather than a reasoning failure.

**Verdict:** more observant and more often legal than Gemma, but loop-prone,
inefficient, and still below acceptable autonomous-play quality.

## Overall interpretation

The 8-15 second Gemma turns were not evidence of strong decisions. They were
often quick because the model committed immediately to a short, repetitive
sequence of guesses. Qwen produced more deliberate traces and a better
domain-success rate, but the additional latency bought only a modest execution
advantage.

For a real decision-quality benchmark, run a separately preregistered control
on the standard tier (with a briefing that has a positive token budget), score
legal action rate and plan completion automatically, and use recorded game
states so both models face the same position. v9 can support that design, but
it should not be retroactively treated as that benchmark.
