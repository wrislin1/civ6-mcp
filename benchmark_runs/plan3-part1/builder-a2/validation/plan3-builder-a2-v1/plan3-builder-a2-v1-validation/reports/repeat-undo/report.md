# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "repeat-undo"
- case_sha256: "d09617cf4d4eb40551e128740f441063c64bce27b5bb5cb3c80c6fecf7682572"
- contract_fingerprint: "af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697"
- contract_identity: "af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697"
- counting: false
- coverage: {"area": [[64, 20], [64, 21], [64, 22], [64, 23], [64, 24], [65, 20], [65, 21], [65, 22], [65, 23], [65, 24], [65, 25], [66, 20], [66, 21], [66, 22], [66, 23], [66, 24], [66, 25], [67, 20], [67, 21], [67, 22], [67, 23], [67, 24], [67, 25], [68, 20], [68, 21], [68, 22], [68, 23], [68, 24], [68, 25], [69, 20], [69, 21], [69, 22], [69, 23], [69, 24], [69, 25], [70, 20], [70, 21], [70, 22], [70, 23], [70, 24], [70, 25], [70, 29], [70, 30], [70, 31], [70, 32], [70, 33], [71, 20], [71, 21], [71, 22], [71, 23], [71, 24], [71, 25], [71, 27], [71, 28], [71, 29], [71, 30], [71, 31], [71, 32], [71, 33], [72, 20], [72, 21], [72, 22], [72, 23], [72, 24], [72, 27], [72, 28], [72, 29], [72, 30], [72, 31], [72, 32], [72, 33], [73, 27], [73, 28], [73, 29], [73, 30], [73, 31], [73, 32], [73, 33], [74, 27], [74, 28], [74, 29], [74, 30], [74, 31], [74, 32], [74, 33], [75, 27], [75, 28], [75, 29], [75, 30], [75, 31], [75, 32], [75, 33], [76, 28], [76, 29], [76, 30], [76, 31], [76, 32]], "include_owned_tiles": true, "tracked_targets": [[63, 16908390]]}
- evidence_version: "2.0.0"
- position_id: "plan3-builder-a2-v1"
- position_version: 1
- public_observation_sha256: "4852b35cb72943a81b3ffe570f18f53a83ff43415e501079e9edf56a2f8f1eaa"
- public_task_tiles: [[69, 23], [73, 29], [73, 31], [66, 22], [69, 22], [68, 23], [74, 30]]
- public_task_tiles_sha256: "08689d1cf7f8bd8b5caf896a73e931ab1cde2cbbffabc6f130bf2749321b9153"
- rubric_sha256: "42341f9ddb5bbb1b42fd8a96547bf5f2bb6a597d7180f8aedc4db2a76fb431a7"
- script_id: "repeat-undo"
- script_sha256: "33e6a28c60a0776ae8f258d844f495e5cdf290131a66436713636e1def438305"
- session_fingerprint: "409f9c8440a7836ccb52848fd4f2fb6820cd9ca60e0210b42c3d7f24500100f1"
- toolset_id: "plan3-part1-v1"
- toolset_identity: {"schemas_sha256": "79098f6c05ef8ac5b5a7c5317a8df9256313326a5108e0ca09de52edfa3f5de7", "source_sha256": "e09dca93f7afd7e57866009315226facaa00aa63e3c5aeff4d5da30ecdff2882"}

## Identity

### drift
```json
[]
```

### identity_ok
```json
true
```

## Mechanics

- invalid_tool_calls: []
- validation_failures: [{"reason": "rejected_operation", "result": "Error: NO_MOVES|Builder has no moves remaining this turn", "step": 2, "tool_name": "repair_improvement"}]
- validation_status: "failed"

## Terminal

- terminal: "finish_trial"
- truncated_steps: []
- truncation_unavailable_steps: []

## Counters

- attempts_per_round: 0.8333333333333334
- dispatched_calls: 5
- round_trips: 6
- round_trips_completed: 6
- tool_call_attempts: 5

## Score

- gross_credit: 4
- harm_total: 0
- maximum_credit: 12
- maximum_harm: 8
- net_credit: 4
- primary_score: 0.3333333333333333

### Objectives
| id | credit | maximum | rung_index |
|---|---|---|---|
| "restore-production" | 4 | 4 | 1 |
| "connect-resource" | 0 | 4 | null |
| "improve-food" | 0 | 4 | null |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "escort-loss" | "escort" | "not_fired" | "event" | 4 | 0 | [] | null |
| "new-exposure" | "civilian_exposure" | "not_fired" | "final" | 4 | 0 | [] | null |

### Scored evidence
```json
{
  "gross_credit": 4,
  "harm_total": 0,
  "harms": [
    {
      "charged_harm": null,
      "compensation": [],
      "deduction": 0,
      "fired": false,
      "fired_steps": [],
      "first_step": null,
      "id": "escort-loss",
      "loss_key": "escort",
      "objective_id": "connect-resource",
      "priority": 0,
      "reference": [
        {
          "kind": "unit_lost",
          "unit": [
            0,
            1376259
          ]
        }
      ],
      "status": "not_fired",
      "timing": "event",
      "weight": 4
    },
    {
      "charged_harm": null,
      "compensation": [],
      "deduction": 0,
      "fired": false,
      "fired_steps": [],
      "first_step": null,
      "id": "new-exposure",
      "loss_key": "civilian_exposure",
      "objective_id": "connect-resource",
      "priority": 0,
      "reference": [
        {
          "kind": "new_civilian_exposure",
          "unit": [
            0,
            1835021
          ]
        }
      ],
      "status": "not_fired",
      "timing": "final",
      "weight": 4
    }
  ],
  "maximum_credit": 12,
  "maximum_harm": 8,
  "net_credit": 4,
  "objectives": [
    {
      "credit": 4,
      "id": "restore-production",
      "maximum": 4,
      "predicate": {
        "fields": {
          "improvement": "IMPROVEMENT_MINE",
          "pillaged": false
        },
        "kind": "tile_matches",
        "tiles": [
          [
            68,
            23
          ]
        ]
      },
      "rung_index": 1,
      "rungs": [
        {
          "points": 2,
          "predicate": {
            "kind": "all",
            "predicates": [
              {
                "kind": "charged_builder_at",
                "tiles": [
                  [
                    68,
                    23
                  ]
                ]
              },
              {
                "fields": {
                  "improvement": "IMPROVEMENT_MINE",
                  "pillaged": true
                },
                "kind": "tile_matches",
                "tiles": [
                  [
                    68,
                    23
                  ]
                ]
              }
            ]
          }
        },
        {
          "points": 4,
          "predicate": {
            "fields": {
              "improvement": "IMPROVEMENT_MINE",
              "pillaged": false
            },
            "kind": "tile_matches",
            "tiles": [
              [
                68,
                23
              ]
            ]
          }
        }
      ]
    },
    {
      "credit": 0,
      "id": "connect-resource",
      "maximum": 4,
      "predicate": null,
      "rung_index": null,
      "rungs": [
        {
          "points": 2,
          "predicate": {
            "kind": "charged_builder_at",
            "tiles": [
              [
                74,
                30
              ]
            ]
          }
        },
        {
          "points": 4,
          "predicate": {
            "fields": {
              "improvement": "IMPROVEMENT_PASTURE",
              "owner": 0,
              "pillaged": false
            },
            "kind": "tile_matches",
            "tiles": [
              [
                74,
                30
              ]
            ]
          }
        }
      ]
    },
    {
      "credit": 0,
      "id": "improve-food",
      "maximum": 4,
      "predicate": null,
      "rung_index": null,
      "rungs": [
        {
          "points": 2,
          "predicate": {
            "kind": "charged_builder_at",
            "tiles": [
              [
                66,
                22
              ],
              [
                69,
                22
              ]
            ]
          }
        },
        {
          "points": 4,
          "predicate": {
            "fields": {
              "food": {
                "min": 2
              },
              "improvement": "IMPROVEMENT_FARM",
              "pillaged": false
            },
            "kind": "tile_matches",
            "tiles": [
              [
                66,
                22
              ],
              [
                69,
                22
              ]
            ]
          }
        }
      ]
    }
  ],
  "primary_score": 0.3333333333333333,
  "scales": {
    "attainable_values": [
      -0.6666666666666666,
      -0.5,
      -0.3333333333333333,
      -0.16666666666666666,
      0.0,
      0.16666666666666666,
      0.3333333333333333,
      0.5,
      0.6666666666666666,
      0.8333333333333334,
      1.0
    ],
    "maximum_credit": 12,
    "maximum_harm": 8,
    "one_objective": 0.3333333333333333,
    "smallest_increment": 0.16666666666666666
  }
}
```

## Ledger

### Net
| path | before | after | delta | coverage | source_steps |
|---|---|---|---|---|---|
| ["faith"] | 119.7890625 | 119.7890625 | 0.0 | "measured" | [] |
| ["gold"] | 95.61328125 | 95.61328125 | 0.0 | "measured" | [] |

### Steps
```json
[
  {
    "entries": [],
    "step": 0,
    "tool_name": "move_unit"
  },
  {
    "entries": [],
    "step": 1,
    "tool_name": "repair_improvement"
  },
  {
    "entries": [],
    "step": 2,
    "tool_name": "repair_improvement"
  },
  {
    "entries": [],
    "step": 3,
    "tool_name": "move_unit"
  },
  {
    "entries": [],
    "step": 4,
    "tool_name": "move_unit"
  }
]
```

## Audits

### loss_coverage
```json
{
  "declared": 0,
  "observed": 0,
  "steps_examined": 5,
  "undeclared": 0
}
```

### losses
```json
[]
```

### uncredited
```json
[
  {
    "category": "builder_positioning",
    "evidence": {
      "economic_paths": [],
      "improvement": null,
      "losses": [],
      "movement": {
        "builder": true,
        "distance_after": 1,
        "distance_before": 0,
        "from": [
          66,
          22
        ],
        "to": [
          66,
          23
        ],
        "unit": [
          0,
          1900558
        ]
      },
      "step": 4,
      "tool_name": "move_unit"
    },
    "primary_deduction": 0,
    "tags": [
      "farther_from_public_task"
    ]
  }
]
```

### undercredit
```json
[
  {
    "final_credit": 0,
    "max_attained": 2,
    "objective_id": "improve-food",
    "steps": [
      3
    ]
  }
]
```

## Capture

- available: true
- in_episode_share: 0.012407045913375138
- in_episode_total_s: 3.7221137740125414
- non_drain_residual_s: 0.8561037510226015
- post_drain_total_s: 2.405525526002748
- pre_drain_total_s: 1.2039368219557218
- total_s: 4.465566098981071

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 12,
  "in_episode_share": 0.012407045913375138,
  "in_episode_total_s": 3.7221137740125414,
  "lua_executions_total": 12,
  "max_s": 0.3782610689813737,
  "mean_s": 0.3721305082484226,
  "non_drain_residual_s": 0.8561037510226015,
  "p95_s": 0.3782610689813737,
  "post_drain_total_s": 2.405525526002748,
  "pre_drain_total_s": 1.2039368219557218,
  "total_s": 4.465566098981071,
  "unavailable": {}
}
```

## Model usage

- completion_tokens: null
- cost_usd: null
- model: null
- model_latency_s: null
- prompt_tokens: null

## Digests

### final
```json
"024addd182d8f515e1d1103b01a671d7e83d64cd9962115eca0e73f85c8a4035"
```

### initial
```json
"a29b6d01b3b3e61f63c59dc0c585aa12c238a9a8247414564dec09c7fa0f1bb5"
```

### steps
```json
[
  {
    "after": "8cc4f38cb5057a748ed3d574bc772d8be506e2d186cd6e4eb6faeecd7a4e9c9a",
    "before": "a29b6d01b3b3e61f63c59dc0c585aa12c238a9a8247414564dec09c7fa0f1bb5",
    "step": 0
  },
  {
    "after": "dfb1ad54faec5f4586b728dfbb4211b8f97cd8468f8b04d3feb1839f4c5eb163",
    "before": "8cc4f38cb5057a748ed3d574bc772d8be506e2d186cd6e4eb6faeecd7a4e9c9a",
    "step": 1
  },
  {
    "after": "dfb1ad54faec5f4586b728dfbb4211b8f97cd8468f8b04d3feb1839f4c5eb163",
    "before": "dfb1ad54faec5f4586b728dfbb4211b8f97cd8468f8b04d3feb1839f4c5eb163",
    "step": 2
  },
  {
    "after": "4d59b2bb57ad1d6dc7518a1aac4c6d85c7dd981c09a34f196b27124638318df9",
    "before": "dfb1ad54faec5f4586b728dfbb4211b8f97cd8468f8b04d3feb1839f4c5eb163",
    "step": 3
  },
  {
    "after": "024addd182d8f515e1d1103b01a671d7e83d64cd9962115eca0e73f85c8a4035",
    "before": "4d59b2bb57ad1d6dc7518a1aac4c6d85c7dd981c09a34f196b27124638318df9",
    "step": 4
  }
]
```
