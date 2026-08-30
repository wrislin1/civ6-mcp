# Arena Analysis Report

## Experiment config

| player | model | tools | max_steps | n_ctx | avg briefing tok | avg steps | invalid rate | avg Δscore |
|--------|-------|-------|-----------|-------|------------------|-----------|--------------|------------|
| 1 | gemma4-26b | minimal | 10 |  | 0.0 | 8.4 | 0.0% | 0.0 |
| 2 | qwen3.6-27b | minimal | 10 |  | 0.0 | 10.7 | 0.0% | 0.0 |

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
- **Messages**: 0
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

## Player 1 — model `gemma4-26b`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.1310 (13.1%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 157 | 320 | 7 | 13 | 49.7 | 43.1 | 31773 | 318 | 16.584039449691772 | 10 |
| 158 | 327 | 7 | 13 | 50.2 | 43.4 | 33672 | 197 | 10.849967956542969 | 10 |
| 159 | 328 | 7 | 13 | 50.2 | 43.4 | 32945 | 223 | 11.641696453094482 | 10 |
| 160 | 328 | 7 | 13 | 50.2 | 43.4 | 32607 | 214 | 12.391811847686768 | 10 |
| 161 | 328 | 7 | 14 | 50.2 | 43.4 | 31654 | 278 | 8.524923086166382 | 10 |
| 162 | 329 | 7 | 14 | 50.7 | 44.2 | 17010 | 240 | 5.985597372055054 | 5 |
| 163 | 330 | 7 | 14 | 51.9 | 46.1 | 32590 | 357 | 9.083457231521606 | 9 |
| 164 | 333 | 7 | 14 | 51.9 | 46.1 | 17008 | 172 | 5.558514833450317 | 5 |
| 165 | 339 | 7 | 14 | 51.9 | 46.1 | 31843 | 303 | 8.491678953170776 | 10 |
| 166 | 339 | 7 | 14 | 51.9 | 46.1 | 16946 | 455 | 6.9688427448272705 | 5 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: not observed
- **Set research / production (non-ERROR)**: not observed
- **Wasted / blind move (ERROR result)**: not observed
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed

## Player 2 — model `qwen3.6-27b`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.0935 (9.3%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 157 | 308 | 5 | 8 | 35.5 | 71.9 | 35506 | 435 | 52.175309896469116 | 10 |
| 158 | 309 | 5 | 8 | 35.5 | 71.9 | 35415 | 429 | 25.77542781829834 | 10 |
| 159 | 311 | 5 | 8 | 35.5 | 71.9 | 35121 | 338 | 23.283361673355103 | 10 |
| 160 | 311 | 5 | 8 | 37.4 | 73.5 | 35295 | 344 | 23.275119304656982 | 10 |
| 161 | 316 | 5 | 8 | 37.4 | 73.5 | 38282 | 1160 | 45.37499666213989 | 10 |
| 162 | 318 | 5 | 8 | 37.9 | 73.9 | 35766 | 498 | 28.46686029434204 | 10 |
| 163 | 319 | 5 | 9 | 37.9 | 73.9 | 35783 | 538 | 28.433244466781616 | 10 |
| 164 | 328 | 5 | 9 | 37.9 | 73.9 | 36520 | 613 | 30.12537121772766 | 10 |
| 165 | 328 | 5 | 9 | 37.9 | 73.9 | 40991 | 793 | 39.39161038398743 | 17 |
| 166 | 328 | 5 | 9 | 37.9 | 73.9 | 35569 | 500 | 26.633124113082886 | 10 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: not observed
- **Set research / production (non-ERROR)**: not observed
- **Wasted / blind move (ERROR result)**: not observed
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed
