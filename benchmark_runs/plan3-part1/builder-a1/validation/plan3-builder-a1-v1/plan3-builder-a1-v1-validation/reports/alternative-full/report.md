# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "alternative-full"
- case_sha256: "5de093c57e07bbf5cdc2dccf2eb6c940da22b02df00d8596e8662f599a09713c"
- contract_fingerprint: "af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697"
- contract_identity: "af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697"
- counting: false
- coverage: {"area": [[64, 20], [64, 21], [64, 22], [64, 23], [64, 24], [65, 20], [65, 21], [65, 22], [65, 23], [65, 24], [65, 25], [65, 26], [66, 20], [66, 21], [66, 22], [66, 23], [66, 24], [66, 25], [66, 26], [67, 20], [67, 21], [67, 22], [67, 23], [67, 24], [67, 25], [67, 26], [68, 20], [68, 21], [68, 22], [68, 23], [68, 24], [68, 25], [68, 26], [69, 20], [69, 21], [69, 22], [69, 23], [69, 24], [69, 25], [69, 26], [70, 20], [70, 21], [70, 22], [70, 23], [70, 24], [70, 25], [70, 29], [70, 30], [70, 31], [70, 32], [70, 33], [71, 20], [71, 21], [71, 22], [71, 23], [71, 24], [71, 25], [71, 28], [71, 29], [71, 30], [71, 31], [71, 32], [71, 33], [72, 20], [72, 21], [72, 22], [72, 23], [72, 24], [72, 28], [72, 29], [72, 30], [72, 31], [72, 32], [72, 33], [73, 28], [73, 29], [73, 30], [73, 31], [73, 32], [73, 33], [74, 28], [74, 29], [74, 30], [74, 31], [74, 32], [74, 33], [75, 28], [75, 29], [75, 30], [75, 31], [75, 32], [75, 33], [76, 28], [76, 29], [76, 30], [76, 31], [76, 32], [76, 33]], "include_owned_tiles": true, "tracked_targets": [[63, 16908390]]}
- evidence_version: "2.0.0"
- position_id: "plan3-builder-a1-v1"
- position_version: 1
- public_observation_sha256: "48f52dbc4064aad7cee67e24e1476a00ac1a2670470d3324fe1094405dc47c0b"
- public_task_tiles: [[69, 23], [72, 31], [66, 22], [69, 22], [68, 23], [74, 30]]
- public_task_tiles_sha256: "c658b0ede3660837a9a443a3877e2bfc4a3039a27b06a7c357924f2cbf8a7f38"
- rubric_sha256: "42341f9ddb5bbb1b42fd8a96547bf5f2bb6a597d7180f8aedc4db2a76fb431a7"
- script_id: "alternative-full"
- script_sha256: "ba1fb21d7286969fb6c420e117f5be16ad91790552869e96abd73a703ff2098b"
- session_fingerprint: "5e1b95c2c150b1577ba3a92f035b686940a9f57bad27bd2f5061f6b1998a7478"
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
- validation_failures: [{"reason": "rejected_operation", "result": "Error: NO_MOVES|Builder has no moves remaining this turn", "step": 1, "tool_name": "repair_improvement"}, {"reason": "rejected_operation", "result": "Error: CANNOT_IMPROVE|Builder has no moves remaining this turn", "step": 5, "tool_name": "improve_tile"}]
- validation_status: "failed"

## Terminal

- terminal: "finish_trial"
- truncated_steps: []
- truncation_unavailable_steps: []

## Counters

- attempts_per_round: 1.5
- dispatched_calls: 6
- round_trips: 4
- round_trips_completed: 4
- tool_call_attempts: 6

## Score

- gross_credit: 6
- harm_total: 0
- maximum_credit: 12
- maximum_harm: 8
- net_credit: 6
- primary_score: 0.5

### Objectives
| id | credit | maximum | rung_index |
|---|---|---|---|
| "restore-production" | 2 | 4 | 0 |
| "connect-resource" | 2 | 4 | 0 |
| "improve-food" | 2 | 4 | 0 |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "escort-loss" | "escort" | "not_fired" | "event" | 4 | 0 | [] | null |
| "new-exposure" | "civilian_exposure" | "not_fired" | "final" | 4 | 0 | [] | null |

### Scored evidence
```json
{
  "gross_credit": 6,
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
  "net_credit": 6,
  "objectives": [
    {
      "credit": 2,
      "id": "restore-production",
      "maximum": 4,
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
      },
      "rung_index": 0,
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
      "credit": 2,
      "id": "connect-resource",
      "maximum": 4,
      "predicate": {
        "kind": "charged_builder_at",
        "tiles": [
          [
            74,
            30
          ]
        ]
      },
      "rung_index": 0,
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
      "credit": 2,
      "id": "improve-food",
      "maximum": 4,
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
      },
      "rung_index": 0,
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
  "primary_score": 0.5,
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
| ["units", "0:1769484", "charges"] | 3 | 2 | -1 | "measured" | [3] |

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
    "tool_name": "move_unit"
  },
  {
    "entries": [
      {
        "after": 2,
        "before": 3,
        "coverage": "measured",
        "delta": -1,
        "path": [
          "units",
          "0:1769484",
          "charges"
        ],
        "refs": {
          "unit": [
            0,
            1769484
          ]
        },
        "source_steps": [
          3
        ],
        "tool_name": "improve_tile",
        "units": "charges"
      }
    ],
    "step": 3,
    "tool_name": "improve_tile"
  },
  {
    "entries": [],
    "step": 4,
    "tool_name": "move_unit"
  },
  {
    "entries": [],
    "step": 5,
    "tool_name": "improve_tile"
  }
]
```

## Audits

### loss_coverage
```json
{
  "declared": 0,
  "observed": 0,
  "steps_examined": 6,
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
    "category": "economic_change",
    "evidence": {
      "economic_paths": [
        [
          "units",
          "0:1769484",
          "charges"
        ]
      ],
      "improvement": {
        "on_task_tile": true,
        "on_unit_tile": true,
        "tile": [
          69,
          22
        ],
        "type": "IMPROVEMENT_FARM"
      },
      "losses": [],
      "movement": null,
      "step": 3,
      "tool_name": "improve_tile"
    },
    "primary_deduction": 0,
    "tags": [
      "farm_on_own_tile"
    ]
  }
]
```

### undercredit
```json
[]
```

## Capture

- available: true
- in_episode_share: 0.014884044759868023
- in_episode_total_s: 4.465213427960407
- non_drain_residual_s: 1.027734068891732
- post_drain_total_s: 2.806229740002891
- pre_drain_total_s: 1.4041811680654064
- total_s: 5.2381449769600295

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 14,
  "in_episode_share": 0.014884044759868023,
  "in_episode_total_s": 4.465213427960407,
  "lua_executions_total": 14,
  "max_s": 0.3955996820004657,
  "mean_s": 0.3741532126400021,
  "non_drain_residual_s": 1.027734068891732,
  "p95_s": 0.3955996820004657,
  "post_drain_total_s": 2.806229740002891,
  "pre_drain_total_s": 1.4041811680654064,
  "total_s": 5.2381449769600295,
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
"2714168c973b41c17ed2f5082240e923381302b2eb544914aac000b0ab1b51a8"
```

### initial
```json
"62e56d7ea11b5650504a7b3ed750cf13fc8fa5199d0f463af75f6a689950717c"
```

### steps
```json
[
  {
    "after": "8fa29f01f1b193276feb673fbe95afea2883e8a7f4d2685aaeab8871e751dc38",
    "before": "62e56d7ea11b5650504a7b3ed750cf13fc8fa5199d0f463af75f6a689950717c",
    "step": 0
  },
  {
    "after": "8fa29f01f1b193276feb673fbe95afea2883e8a7f4d2685aaeab8871e751dc38",
    "before": "8fa29f01f1b193276feb673fbe95afea2883e8a7f4d2685aaeab8871e751dc38",
    "step": 1
  },
  {
    "after": "3203adb49372296288399d807c6f139adf39db6f59bd907c4edeaca24deed275",
    "before": "8fa29f01f1b193276feb673fbe95afea2883e8a7f4d2685aaeab8871e751dc38",
    "step": 2
  },
  {
    "after": "698141a8c3ea1dc4ffe31efef540df3e5f279e331db11b4fc0d389e63a52a6d9",
    "before": "3203adb49372296288399d807c6f139adf39db6f59bd907c4edeaca24deed275",
    "step": 3
  },
  {
    "after": "2714168c973b41c17ed2f5082240e923381302b2eb544914aac000b0ab1b51a8",
    "before": "698141a8c3ea1dc4ffe31efef540df3e5f279e331db11b4fc0d389e63a52a6d9",
    "step": 4
  },
  {
    "after": "2714168c973b41c17ed2f5082240e923381302b2eb544914aac000b0ab1b51a8",
    "before": "2714168c973b41c17ed2f5082240e923381302b2eb544914aac000b0ab1b51a8",
    "step": 5
  }
]
```
