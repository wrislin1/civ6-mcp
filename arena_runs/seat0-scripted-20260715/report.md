# Arena Analysis Report

## Experiment config

| player | model | tools | max_steps | n_ctx | avg briefing tok | avg steps | invalid rate | avg Δscore |
|--------|-------|-------|-----------|-------|------------------|-----------|--------------|------------|
| 0 | seat0-smoke |  |  |  | 0.0 | 0.0 | 0.0% | 0.0 |
| 1 | gemma4-26b | full | 8 |  | 0.0 | 7.0 | 0.0% | 0.0 |
| 2 | gemma4-26b | full | 8 |  | 0.0 | 7.6 | 0.0% | 0.0 |

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
- **Driver mix**: cli=0, in_process=16, scripted=8
- **Puppeted players**: 0, 1, 2

| player_id | driver | provider | model | turns failed | mem injected | mem captured | task attempts | task complete | task blocked | task lost | task failed | GP calls | trade calls | religion/WC calls |
|-----------|--------|----------|-------|--------------|---------------|--------------|---------------|----------------|--------------|-----------|-------------|----------|-------------|-------------------|
| 0 | scripted | scripted | seat0-smoke | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 1 | in_process | local | gemma4-26b | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2 | in_process | local | gemma4-26b | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Player 1 — model `gemma4-26b`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.0714 (7.1%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 1 | 0 | 1 | 1 | 2.7 | 1.4 | 54573 | 1169 | 26.110527753829956 | 8 |
| 2 | 7 | 1 | 1 | 2.7 | 1.4 | 53585 | 919 | 17.026082754135132 | 8 |
| 3 | 7 | 1 | 1 | 2.7 | 1.4 | 54315 | 584 | 13.264022588729858 | 8 |
| 4 | 7 | 1 | 1 | 2.7 | 1.4 | 38579 | 834 | 13.089379072189331 | 5 |
| 5 | 7 | 1 | 1 | 2.7 | 1.4 | 38877 | 895 | 13.249549388885498 | 5 |
| 6 | 7 | 1 | 2 | 2.7 | 1.4 | 55408 | 768 | 14.496047735214233 | 8 |
| 7 | 7 | 1 | 2 | 2.7 | 1.4 | 53987 | 1371 | 20.142329454421997 | 7 |
| 8 | 7 | 1 | 2 | 2.7 | 1.4 | 54810 | 1021 | 17.30147695541382 | 7 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: YES — turn=1, note=cities delta=1
- **Explored vs idle loops**: YES — turn=1, note=explored (automate/move)
- **Set research / production (non-ERROR)**: YES — turn=1, tool=set_research
- **Wasted / blind move (ERROR result)**: YES — turn=2, note=move returned error/blocked
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed

## Player 2 — model `gemma4-26b`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.0820 (8.2%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 1 | 0 | 1 | 1 | 2.7 | 3.6 | 53639 | 347 | 26.458765268325806 | 7 |
| 2 | 9 | 1 | 1 | 2.7 | 3.6 | 54106 | 427 | 11.253376007080078 | 7 |
| 3 | 9 | 1 | 1 | 2.7 | 3.6 | 53238 | 655 | 12.713205575942993 | 7 |
| 4 | 9 | 1 | 1 | 2.7 | 3.6 | 55073 | 910 | 16.430399179458618 | 8 |
| 5 | 9 | 1 | 1 | 2.7 | 3.6 | 54252 | 1281 | 18.51456117630005 | 8 |
| 6 | 9 | 1 | 2 | 3.2 | 3.9 | 53921 | 449 | 12.960922241210938 | 8 |
| 7 | 10 | 1 | 2 | 3.2 | 3.9 | 55252 | 692 | 14.482831716537476 | 8 |
| 8 | 13 | 1 | 2 | 3.2 | 3.9 | 55735 | 484 | 11.636784076690674 | 8 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: YES — turn=1, note=cities delta=1
- **Explored vs idle loops**: YES — turn=1, note=explored (automate/move)
- **Set research / production (non-ERROR)**: YES — turn=1, tool=set_research
- **Wasted / blind move (ERROR result)**: YES — turn=2, note=move returned error/blocked
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: YES — turn=5, note=skip/fortify after truncation

## Player 0 — model `seat0-smoke`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.0000 (0.0%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 1 | 0 | 0 | 2 | 0.0 | 0.0 | 0 | 0 | 0 | 0 |
| 2 | 0 | 0 | 2 | 0.0 | 0.0 | 0 | 0 | 0 | 0 |
| 3 | 0 | 0 | 2 | 0.0 | 0.0 | 0 | 0 | 0 | 0 |
| 4 | 0 | 0 | 2 | 0.0 | 0.0 | 0 | 0 | 0 | 0 |
| 5 | 0 | 0 | 2 | 0.0 | 0.0 | 0 | 0 | 0 | 0 |
| 6 | 0 | 0 | 2 | 0.0 | 0.0 | 0 | 0 | 0 | 0 |
| 7 | 0 | 0 | 2 | 0.0 | 0.0 | 0 | 0 | 0 | 0 |
| 8 | 0 | 0 | 2 | 0.0 | 0.0 | 0 | 0 | 0 | 0 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: not observed
- **Set research / production (non-ERROR)**: not observed
- **Wasted / blind move (ERROR result)**: not observed
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed
