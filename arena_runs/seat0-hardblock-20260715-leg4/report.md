# Arena Analysis Report

## Experiment config

| player | model | tools | max_steps | n_ctx | avg briefing tok | avg steps | invalid rate | avg Δscore |
|--------|-------|-------|-----------|-------|------------------|-----------|--------------|------------|
| 0 | cli-claude |  |  |  | 0.0 | 54.0 | 0.5% | 0.0 |

## Behavior Metrics

- **Failed turns**: 0
- **Standing memory injected turns**: 0
- **Standing memory captured turns**: 0
- **Task tracker active turns**: 0
- **Task pre-model actions**: 0
- **Task completions**: 0
- **Task blocked (visible hostile)**: 0
- **Task lost**: 0
- **Task failed**: 0
- **Driver mix**: cli=8, in_process=0
- **Puppeted players**: 0

| player_id | driver | provider | model | turns failed | mem injected | mem captured | task attempts | task complete | task blocked | task lost | task failed | GP calls | trade calls | religion/WC calls |
|-----------|--------|----------|-------|--------------|---------------|--------------|---------------|----------------|--------------|-----------|-------------|----------|-------------|-------------------|
| 0 | cli | cli-claude |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 5 | 10 |

## Player 0 — model `cli-claude`

- **Invalid call rate**: 0.0046 (0.5%)
- **Truncation incident rate**: 0.0000 (0.0%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 306 | 1406 | 8 | 51 | 573.7 | 312.4 | 48 | 23207 | 361.41552374599996 | 47 |
| 307 | 1407 | 8 | 51 | 569.4 | 281.8 | 45 | 23185 | 369.3337952610018 | 59 |
| 308 | 1414 | 8 | 51 | 559.9 | 270.4 | 44 | 22263 | 341.2924203729999 | 55 |
| 309 | 1421 | 9 | 52 | 562.3 | 272.3 | 120 | 20988 | 354.2026848109963 | 61 |
| 310 | 1426 | 9 | 52 | 577.9 | 272.3 | 27 | 12826 | 222.3138787889984 | 37 |
| 311 | 1428 | 9 | 52 | 546.2 | 272.3 | 36 | 15944 | 251.09408151699608 | 48 |
| 312 | 1432 | 9 | 53 | 519.5 | 284.4 | 78 | 23352 | 386.89246748299774 | 74 |
| 313 | 1437 | 9 | 53 | 515.5 | 284.4 | 47 | 17912 | 294.8470354070014 | 51 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: not observed
- **Set research / production (non-ERROR)**: not observed
- **Wasted / blind move (ERROR result)**: not observed
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed
