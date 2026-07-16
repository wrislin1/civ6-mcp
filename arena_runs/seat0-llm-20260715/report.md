# Arena Analysis Report

## Experiment config

| player | model | tools | max_steps | n_ctx | avg briefing tok | avg steps | invalid rate | avg Δscore |
|--------|-------|-------|-----------|-------|------------------|-----------|--------------|------------|
| 0 | cli-claude |  |  |  | 0.0 | 25.2 | 5.6% | 0.0 |
| 1 | gemma4-26b | full | 8 |  | 0.0 | 7.9 | 0.0% | 0.0 |
| 2 | gemma4-26b | full | 8 |  | 0.0 | 7.9 | 0.0% | 0.0 |

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
- **Driver mix**: cli=12, in_process=24
- **Puppeted players**: 0, 1, 2

| player_id | driver | provider | model | turns failed | mem injected | mem captured | task attempts | task complete | task blocked | task lost | task failed | GP calls | trade calls | religion/WC calls |
|-----------|--------|----------|-------|--------------|---------------|--------------|---------------|----------------|--------------|-----------|-------------|----------|-------------|-------------------|
| 0 | cli | cli-claude |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 1 | in_process | local | gemma4-26b | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2 | in_process | local | gemma4-26b | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Player 1 — model `gemma4-26b`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.0947 (9.5%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 9 | 7 | 1 | 2 | 3.2 | 1.7 | 53617 | 902 | 25.503286361694336 | 8 |
| 10 | 8 | 1 | 2 | 3.2 | 1.7 | 54699 | 436 | 10.609436273574829 | 7 |
| 11 | 8 | 1 | 2 | 3.2 | 1.7 | 55570 | 548 | 12.860241413116455 | 8 |
| 12 | 8 | 1 | 2 | 3.2 | 1.7 | 55060 | 531 | 12.079469203948975 | 8 |
| 13 | 8 | 1 | 2 | 3.2 | 1.7 | 55032 | 1327 | 21.407154321670532 | 8 |
| 14 | 8 | 1 | 2 | 3.2 | 1.7 | 55834 | 467 | 12.0889732837677 | 8 |
| 15 | 10 | 1 | 2 | 3.2 | 3.9 | 54272 | 768 | 16.154696702957153 | 8 |
| 16 | 14 | 1 | 2 | 3.2 | 3.9 | 55798 | 617 | 14.102938175201416 | 8 |
| 17 | 14 | 1 | 2 | 3.2 | 3.9 | 55147 | 365 | 9.400004625320435 | 8 |
| 18 | 14 | 1 | 2 | 3.2 | 3.9 | 56363 | 858 | 15.039392709732056 | 8 |
| 19 | 14 | 1 | 2 | 3.2 | 3.9 | 55221 | 1077 | 18.717910289764404 | 8 |
| 20 | 14 | 1 | 2 | 3.2 | 3.9 | 55255 | 667 | 13.842382431030273 | 8 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: YES — turn=9, note=explored (automate/move)
- **Set research / production (non-ERROR)**: YES — turn=9, tool=set_research
- **Wasted / blind move (ERROR result)**: YES — turn=9, note=move returned error/blocked
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: YES — turn=12, note=skip/fortify after truncation

## Player 2 — model `gemma4-26b`

- **Invalid call rate**: 0.0000 (0.0%)
- **Truncation incident rate**: 0.0737 (7.4%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 9 | 13 | 1 | 2 | 3.2 | 3.9 | 55674 | 742 | 15.467260122299194 | 8 |
| 10 | 13 | 1 | 2 | 3.2 | 3.9 | 38531 | 565 | 13.759663581848145 | 10 |
| 11 | 12 | 1 | 2 | 3.2 | 3.9 | 54795 | 436 | 10.60016679763794 | 7 |
| 12 | 13 | 1 | 2 | 3.2 | 3.9 | 57023 | 853 | 15.983806848526001 | 8 |
| 13 | 13 | 1 | 2 | 3.2 | 3.9 | 56435 | 519 | 12.628307104110718 | 8 |
| 14 | 13 | 1 | 2 | 3.2 | 3.9 | 47591 | 423 | 9.730786085128784 | 6 |
| 15 | 15 | 1 | 3 | 3.2 | 3.9 | 56846 | 1429 | 21.131038188934326 | 8 |
| 16 | 15 | 1 | 3 | 3.2 | 3.9 | 54576 | 674 | 14.287562131881714 | 8 |
| 17 | 15 | 1 | 3 | 3.2 | 3.9 | 56058 | 628 | 13.945685625076294 | 8 |
| 18 | 18 | 1 | 3 | 3.2 | 3.9 | 54575 | 842 | 17.84807300567627 | 8 |
| 19 | 18 | 1 | 3 | 3.8 | 4.2 | 53814 | 1125 | 19.67656183242798 | 8 |
| 20 | 19 | 1 | 4 | 3.8 | 4.2 | 53689 | 1134 | 19.103266954421997 | 8 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: not observed
- **Explored vs idle loops**: YES — turn=9, note=explored (automate/move)
- **Set research / production (non-ERROR)**: YES — turn=16, tool=set_research
- **Wasted / blind move (ERROR result)**: YES — turn=9, note=move returned error/blocked
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: YES — turn=11, note=skip/fortify after truncation

## Player 0 — model `cli-claude`

- **Invalid call rate**: 0.0563 (5.6%)
- **Truncation incident rate**: 0.0000 (0.0%)

### Turn Series

| Turn | Score | Cities | Units | Science | Culture | Prompt Tok | Compl Tok | Wall (s) | Steps |
|------|-------|--------|-------|---------|---------|------------|-----------|----------|-------|
| 9 | 0 | 1 | 1 | 2.5 | 1.3 | 28 | 9442 | 158.22399029299777 | 27 |
| 10 | 7 | 1 | 1 | 2.5 | 1.3 | 19 | 10531 | 170.2244246850023 | 22 |
| 11 | 7 | 1 | 1 | 2.5 | 1.3 | 16 | 7621 | 131.62757333099944 | 24 |
| 12 | 7 | 1 | 1 | 2.5 | 1.3 | 22 | 10384 | 171.9937977720001 | 27 |
| 13 | 7 | 1 | 1 | 2.5 | 1.3 | 16 | 8498 | 145.1147174580001 | 27 |
| 14 | 7 | 1 | 2 | 2.5 | 1.3 | 15 | 5047 | 87.27590813499774 | 19 |
| 15 | 7 | 1 | 2 | 2.5 | 1.3 | 22 | 7041 | 123.70582832299988 | 30 |
| 16 | 7 | 1 | 2 | 2.5 | 1.3 | 154 | 6382 | 149.64185423099843 | 28 |
| 17 | 10 | 1 | 2 | 3.0 | 1.6 | 283 | 6801 | 119.19002510299833 | 24 |
| 18 | 12 | 1 | 2 | 3.0 | 1.6 | 123 | 7304 | 124.44896664000044 | 27 |
| 19 | 12 | 1 | 2 | 3.0 | 1.6 | 14 | 5289 | 93.28501605199926 | 21 |
| 20 | 14 | 1 | 2 | 3.0 | 1.6 | 287 | 7839 | 132.8230467479989 | 26 |

### Early-Game Rubric (Turns 1–20)

- **Founded extra city**: YES — turn=9, note=cities delta=1
- **Explored vs idle loops**: YES — turn=9, note=explored (automate/move)
- **Set research / production (non-ERROR)**: YES — turn=9, tool=set_city_production
- **Wasted / blind move (ERROR result)**: YES — turn=13, note=move returned error/blocked
- **Hallucinated / unknown tool names**: not observed
- **Truncation → bad move correlation**: not observed
