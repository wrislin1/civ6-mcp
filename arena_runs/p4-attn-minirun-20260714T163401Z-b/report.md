# Arena Analysis Report

## Experiment config

| player | model | tools | max_steps | n_ctx | avg briefing tok | avg steps | invalid rate | avg Δscore |
|--------|-------|-------|-----------|-------|------------------|-----------|--------------|------------|
| 3 | gemma4-26b | full | 8 | 131072 | 2345.5 | 11.5 | 0.0% | 0.0 |
| 5 | gemma4-26b | full | 8 | 131072 | 1579.0 | 12.0 | 0.0% | 0.0 |

## Behavior Metrics

- **Standing memory injected turns**: 0
- **Standing memory captured turns**: 0
- **Task tracker active turns**: 0
- **Task pre-model actions**: 0
- **Task completions**: 0
- **Task blocked (visible hostile)**: 0
- **Task lost**: 0
- **Task failed**: 0
- **Driver mix**: in_process=4, cli=0
- **Puppeted players**: 3, 5

| player_id | driver | provider | model | mem injected | mem captured | task attempts | task complete | task blocked | task lost | task failed | GP calls | trade calls | religion/WC calls |
|-----------|--------|----------|-------|---------------|--------------|---------------|----------------|--------------|-----------|-------------|----------|-------------|-------------------|
| 3 | in_process | local | gemma4-26b | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 5 | in_process | local | gemma4-26b | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Attention

| player | captured | slept | skip rate | top wake causes | est USD saved | false-quiet rate |
|--------|----------|-------|-----------|------------------|---------------|-------------------|
| 3 | 4 | 2 | 50.0% | STREAK_CAP=1 | $0.0000 | 0.0% |
| 5 | 4 | 2 | 50.0% | STREAK_CAP=1 | $0.0000 | 0.0% |

## Player 3 — model `gemma4-26b`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.0000 (0.0%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 222 | 434 | 9 | 7 | 55.5 | 127.0 | 102418 | 5548 | 71.43202137947083 | 15 |
| 223 | 435 | 9 | 7 | 55.5 | 127.0 | 0 | 0 |  | 0 |
| 224 | 437 | 9 | 7 | 62.3 | 127.0 | 0 | 0 |  | 0 |
| 225 | 440 | 9 | 7 | 62.3 | 127.0 | 112101 | 5029 | 62.677406787872314 | 8 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: not observed
- **Set research / production (non-ERROR)**: not observed
- **Wasted / blind move (ERROR result)**: not observed
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed

## Player 5 — model `gemma4-26b`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.0000 (0.0%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 222 | 403 | 6 | 10 | 117.7 | 68.1 | 99164 | 3147 | 42.9033043384552 | 8 |
| 223 | 403 | 6 | 10 | 117.7 | 68.1 | 0 | 0 |  | 0 |
| 224 | 403 | 6 | 10 | 117.7 | 68.1 | 0 | 0 |  | 0 |
| 225 | 403 | 6 | 10 | 117.7 | 68.1 | 87155 | 3169 | 51.457998752593994 | 16 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: not observed
- **Set research / production (non-ERROR)**: not observed
- **Wasted / blind move (ERROR result)**: not observed
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed
