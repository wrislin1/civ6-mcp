# Arena Analysis Report

## Experiment config

| player | model | tools | max_steps | n_ctx | avg briefing tok | avg steps | invalid rate | avg Δscore |
|--------|-------|-------|-----------|-------|------------------|-----------|--------------|------------|
| 2 | gpt-5.5 |  |  |  | 0.0 | 0.0 | 0.0% | 0.0 |
| 3 | scripted |  |  |  | 0.0 | 0.0 | 0.0% | 0.0 |

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
- **Driver mix**: cli=3, in_process=0, scripted=8
- **Puppeted players**: 2, 3

| player_id | driver | provider | model | turns failed | mem injected | mem captured | task attempts | task complete | task blocked | task lost | task failed | GP calls | trade calls | religion/WC calls |
|-----------|--------|----------|-------|--------------|---------------|--------------|---------------|----------------|--------------|-----------|-------------|----------|-------------|-------------------|
| 2 | cli | cli-codex | gpt-5.5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 3 | scripted | scripted |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Unofficial Channels

- **Current turn**: 164
- **Messages**: 3
- **Deals**: 2

### Outcomes

| outcome | count |
|---------|-------|
| honored | 1 |
| broken | 1 |
| declined | 0 |
| expired | 0 |
| unverifiable | 0 |

### Payments

| status | count |
|--------|-------|
| not_due | 0 |
| due | 0 |
| offered | 0 |
| settled | 1 |
| failed | 1 |
| waived | 0 |

### Grievances by adjudication source

| source | count | raw magnitude | effective magnitude |
|--------|-------|---------------|---------------------|
| deterministic | 1 | 0.250 | 0.250 |

### Ordered pairs

| pair | messages | deals |
|------|----------|-------|
| 1->2 | 2 | 1 |
| 2->1 | 1 | 1 |

## Player 2 — model `gpt-5.5`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.0000 (0.0%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 157 | 308 | 5 | 8 | 35.5 | 71.9 | 0 | 0 |  | 0 |
| 158 | 309 | 5 | 8 | 35.5 | 71.9 | 0 | 0 |  | 0 |
| 160 | 311 | 5 | 8 | 37.4 | 73.5 | 0 | 0 |  | 0 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: not observed
- **Set research / production (non-ERROR)**: not observed
- **Wasted / blind move (ERROR result)**: not observed
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed

## Player 3 — model `scripted`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.0000 (0.0%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 157 | 202 | 2 | 5 | 31.5 | 20.1 | 0 | 0 |  | 0 |
| 158 | 202 | 2 | 5 | 31.5 | 20.1 | 0 | 0 |  | 0 |
| 159 | 202 | 2 | 5 | 31.2 | 19.9 | 0 | 0 |  | 0 |
| 160 | 202 | 2 | 5 | 31.7 | 20.2 | 0 | 0 |  | 0 |
| 161 | 206 | 2 | 5 | 31.7 | 20.2 | 0 | 0 |  | 0 |
| 162 | 206 | 2 | 4 | 31.7 | 20.2 | 0 | 0 |  | 0 |
| 163 | 206 | 2 | 4 | 31.7 | 20.2 | 0 | 0 |  | 0 |
| 164 | 206 | 2 | 4 | 31.1 | 20.2 | 0 | 0 |  | 0 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: not observed
- **Set research / production (non-ERROR)**: not observed
- **Wasted / blind move (ERROR result)**: not observed
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed
