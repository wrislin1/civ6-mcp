# Arena Analysis Report

## Experiment config

| player | model | tools | max_steps | n_ctx | avg briefing tok | avg steps | invalid rate | avg Δscore |
|--------|-------|-------|-----------|-------|------------------|-----------|--------------|------------|
| 1 | gemma4-26b | minimal | 15 |  | 0.0 | 7.8 | 0.0% | 0.0 |
| 2 | qwen3.6-27b | minimal | 15 |  | 0.0 | 18.5 | 0.0% | 0.0 |

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
- **Driver mix**: cli=0, in_process=20
- **Puppeted players**: 1, 2

| player_id | driver | provider | model | turns failed | mem injected | mem captured | task attempts | task complete | task blocked | task lost | task failed | GP calls | trade calls | religion/WC calls |
|-----------|--------|----------|-------|--------------|---------------|--------------|---------------|----------------|--------------|-----------|-------------|----------|-------------|-------------------|
| 1 | in_process | local | gemma4-26b | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2 | in_process | local | qwen3.6-27b | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Unofficial Channels

- **Current turn**: 166
- **Messages**: 3
- **Deals**: 0

### Outcomes

| outcome | count |
|---------|-------|
| honored | 0 |
| broken | 0 |
| declined | 0 |
| expired | 0 |
| unverifiable | 0 |

### Payments

| status | count |
|--------|-------|
| not_due | 0 |
| due | 0 |
| offered | 0 |
| settled | 0 |
| failed | 0 |
| waived | 0 |

### Grievances by adjudication source

| source | count | raw magnitude | effective magnitude |
|--------|-------|---------------|---------------------|
| none | 0 | 0.000 | 0.000 |

### Ordered pairs

| pair | messages | deals |
|------|----------|-------|
| 2->1 | 2 | 0 |
| 1->2 | 1 | 0 |

## Player 1 — model `gemma4-26b`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.1282 (12.8%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 157 | 320 | 7 | 13 | 49.7 | 43.1 | 16933 | 175 | 12.158493518829346 | 5 |
| 158 | 327 | 7 | 13 | 50.2 | 43.4 | 20434 | 235 | 7.085254669189453 | 6 |
| 159 | 328 | 7 | 13 | 50.2 | 43.4 | 16873 | 242 | 6.407951354980469 | 5 |
| 160 | 328 | 7 | 13 | 50.2 | 43.4 | 16874 | 238 | 6.365401744842529 | 5 |
| 161 | 328 | 7 | 14 | 50.2 | 43.4 | 46253 | 413 | 15.783205509185791 | 12 |
| 162 | 329 | 7 | 14 | 50.7 | 44.2 | 48135 | 743 | 15.912965297698975 | 13 |
| 163 | 330 | 7 | 14 | 51.9 | 46.1 | 45417 | 369 | 17.30033016204834 | 12 |
| 164 | 333 | 7 | 14 | 51.9 | 46.1 | 16994 | 249 | 6.698040962219238 | 5 |
| 165 | 337 | 7 | 15 | 51.9 | 46.1 | 37377 | 340 | 12.744643926620483 | 10 |
| 166 | 337 | 7 | 15 | 51.9 | 46.1 | 21584 | 255 | 7.729953050613403 | 5 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: not observed
- **Set research / production (non-ERROR)**: not observed
- **Wasted / blind move (ERROR result)**: not observed
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed

## Player 2 — model `qwen3.6-27b`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.0703 (7.0%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 157 | 308 | 5 | 8 | 35.5 | 71.9 | 58577 | 752 | 70.10058331489563 | 15 |
| 158 | 309 | 5 | 8 | 35.5 | 71.9 | 58417 | 734 | 42.516902446746826 | 15 |
| 159 | 311 | 5 | 8 | 35.5 | 71.9 | 59616 | 851 | 45.275249004364014 | 15 |
| 160 | 311 | 5 | 7 | 37.4 | 73.5 | 57943 | 800 | 45.3086793422699 | 15 |
| 161 | 316 | 5 | 7 | 37.4 | 73.5 | 87159 | 1390 | 71.9834578037262 | 26 |
| 162 | 316 | 5 | 7 | 37.9 | 73.9 | 59291 | 842 | 48.933480739593506 | 15 |
| 163 | 317 | 5 | 8 | 37.9 | 73.9 | 65842 | 1650 | 80.01185202598572 | 34 |
| 164 | 326 | 5 | 7 | 37.9 | 73.9 | 58298 | 794 | 47.30005645751953 | 15 |
| 165 | 326 | 5 | 7 | 37.9 | 73.9 | 50765 | 1139 | 53.29608106613159 | 18 |
| 166 | 326 | 5 | 8 | 37.9 | 73.9 | 70915 | 959 | 52.6714289188385 | 17 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: not observed
- **Set research / production (non-ERROR)**: not observed
- **Wasted / blind move (ERROR result)**: not observed
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed
