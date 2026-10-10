# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "two-harm"
- case_sha256: "3bcf58d65f337ca082abe95c95d1a08b1dfac0bb2d596ff35f1d711200e20d84"
- contract_fingerprint: "af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697"
- contract_identity: "af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697"
- counting: false
- coverage: {"area": [[64, 20], [64, 21], [64, 22], [64, 23], [64, 24], [65, 20], [65, 21], [65, 22], [65, 23], [65, 24], [65, 25], [66, 20], [66, 21], [66, 22], [66, 23], [66, 24], [66, 25], [67, 20], [67, 21], [67, 22], [67, 23], [67, 24], [67, 25], [68, 20], [68, 21], [68, 22], [68, 23], [68, 24], [68, 25], [69, 20], [69, 21], [69, 22], [69, 23], [69, 24], [69, 25], [70, 20], [70, 21], [70, 22], [70, 23], [70, 24], [70, 25], [70, 29], [70, 30], [70, 31], [70, 32], [70, 33], [71, 20], [71, 21], [71, 22], [71, 23], [71, 24], [71, 25], [71, 27], [71, 28], [71, 29], [71, 30], [71, 31], [71, 32], [71, 33], [72, 20], [72, 21], [72, 22], [72, 23], [72, 24], [72, 27], [72, 28], [72, 29], [72, 30], [72, 31], [72, 32], [72, 33], [73, 27], [73, 28], [73, 29], [73, 30], [73, 31], [73, 32], [73, 33], [74, 27], [74, 28], [74, 29], [74, 30], [74, 31], [74, 32], [74, 33], [75, 27], [75, 28], [75, 29], [75, 30], [75, 31], [75, 32], [75, 33], [76, 28], [76, 29], [76, 30], [76, 31], [76, 32]], "include_owned_tiles": true, "tracked_targets": [[63, 16908390]]}
- evidence_version: "2.0.0"
- position_id: "plan3-builder-a2-v3"
- position_version: 3
- public_observation_sha256: "c12bfede31d5f81621e3096902bc4b6cc7279fa2ed577d58f21a8822f24404cb"
- public_task_tiles: [[69, 23], [73, 29], [73, 31], [66, 22], [69, 22], [68, 23], [74, 30]]
- public_task_tiles_sha256: "08689d1cf7f8bd8b5caf896a73e931ab1cde2cbbffabc6f130bf2749321b9153"
- rubric_sha256: "9f57785d59f6248f9c80f8ae9bf135cae3033cdd55080d71fdfadfb6b3f05595"
- script_id: "two-harm"
- script_sha256: "06129a7df8953e40430a075969d053fd5b74e35972e935fa6b02834614e1869c"
- session_fingerprint: "904c0bc39f671a08287b2fc2f4f4b34ff31284f58e9f97cbd06db580663fc046"
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
- validation_failures: []
- validation_status: "passed_mechanics"

## Terminal

- terminal: "finish_trial"
- truncated_steps: []
- truncation_unavailable_steps: []

## Counters

- attempts_per_round: 0.6666666666666666
- dispatched_calls: 2
- round_trips: 3
- round_trips_completed: 3
- tool_call_attempts: 2

## Score

- gross_credit: 0
- harm_total: 8
- maximum_credit: 12
- maximum_harm: 8
- net_credit: -8
- primary_score: -0.6666666666666666

### Objectives
| id | credit | maximum | rung_index |
|---|---|---|---|
| "restore-production" | 0 | 4 | null |
| "connect-resource" | 0 | 4 | null |
| "improve-food" | 0 | 4 | null |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "escort-loss" | "escort" | "charged" | "event" | 4 | 4 | [1] | null |
| "new-exposure" | "civilian_exposure" | "charged" | "final" | 4 | 4 | [] | null |

### Scored evidence
```json
{
  "gross_credit": 0,
  "harm_total": 8,
  "harms": [
    {
      "charged_harm": null,
      "compensation": [
        {
          "predicate": {
            "kind": "target_neutralised",
            "target": [
              63,
              16908390
            ]
          },
          "satisfied": false,
          "step": null,
          "timing": "final"
        }
      ],
      "deduction": 4,
      "fired": true,
      "fired_steps": [
        1
      ],
      "first_step": 1,
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
      "status": "charged",
      "timing": "event",
      "weight": 4
    },
    {
      "charged_harm": null,
      "compensation": [],
      "deduction": 4,
      "fired": true,
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
      "status": "charged",
      "timing": "final",
      "weight": 4
    }
  ],
  "maximum_credit": 12,
  "maximum_harm": 8,
  "net_credit": -8,
  "objectives": [
    {
      "credit": 0,
      "id": "restore-production",
      "maximum": 4,
      "predicate": null,
      "rung_index": null,
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
  "primary_score": -0.6666666666666666,
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
| ["units", "0:1376259", "charges"] | 0 | null | null | "unavailable" | [1] |

### Steps
```json
[
  {
    "entries": [],
    "step": 0,
    "tool_name": "move_unit"
  },
  {
    "entries": [
      {
        "after": null,
        "before": 0,
        "coverage": "unavailable",
        "delta": null,
        "lifecycle": "lost",
        "path": [
          "units",
          "0:1376259",
          "charges"
        ],
        "refs": {
          "unit": [
            0,
            1376259
          ]
        },
        "source_steps": [
          1
        ],
        "tool_name": "attack_unit",
        "units": "charges"
      }
    ],
    "step": 1,
    "tool_name": "attack_unit"
  }
]
```

## Audits

### loss_coverage
```json
{
  "declared": 1,
  "observed": 1,
  "steps_examined": 2,
  "undeclared": 0
}
```

### losses
```json
[
  {
    "declared": true,
    "declared_harm_ids": [
      "escort-loss"
    ],
    "entity": [
      0,
      1376259
    ],
    "kind": "unit",
    "lifecycle": "lost",
    "result_shape": "ok",
    "step": 1,
    "tool_name": "attack_unit"
  }
]
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
        "distance_after": 0,
        "distance_before": 1,
        "from": [
          73,
          30
        ],
        "to": [
          73,
          31
        ],
        "unit": [
          0,
          1835021
        ]
      },
      "step": 0,
      "tool_name": "move_unit"
    },
    "primary_deduction": 0,
    "tags": [
      "closer_to_public_task"
    ]
  },
  {
    "category": "declared_harm",
    "evidence": {
      "economic_paths": [
        [
          "units",
          "0:1376259",
          "charges"
        ]
      ],
      "improvement": null,
      "losses": [
        {
          "declared": true,
          "entity": [
            0,
            1376259
          ],
          "kind": "unit",
          "lifecycle": "lost"
        }
      ],
      "movement": null,
      "step": 1,
      "tool_name": "attack_unit"
    },
    "primary_deduction": 0,
    "tags": []
  }
]
```

### undercredit
```json
[]
```

## Capture

- available: true
- in_episode_share: 0.004961804333239949
- in_episode_total_s: 1.4885412999719847
- non_drain_residual_s: 0.42790890400647186
- post_drain_total_s: 1.2024400259833783
- pre_drain_total_s: 0.6020563689817209
- total_s: 2.232405298971571

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 6,
  "in_episode_share": 0.004961804333239949,
  "in_episode_total_s": 1.4885412999719847,
  "lua_executions_total": 6,
  "max_s": 0.3781977650069166,
  "mean_s": 0.3720675498285952,
  "non_drain_residual_s": 0.42790890400647186,
  "p95_s": 0.3781977650069166,
  "post_drain_total_s": 1.2024400259833783,
  "pre_drain_total_s": 0.6020563689817209,
  "total_s": 2.232405298971571,
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
"cd8a6f09839be3a95e46458f3f58dfe43df9d92bb0e96817862f21117c64839c"
```

### initial
```json
"a29b6d01b3b3e61f63c59dc0c585aa12c238a9a8247414564dec09c7fa0f1bb5"
```

### steps
```json
[
  {
    "after": "2c00a9fabcfb756442e3b4b1fee9b399d22e839d72a4bb2a9b60cf4228d28552",
    "before": "a29b6d01b3b3e61f63c59dc0c585aa12c238a9a8247414564dec09c7fa0f1bb5",
    "step": 0
  },
  {
    "after": "cd8a6f09839be3a95e46458f3f58dfe43df9d92bb0e96817862f21117c64839c",
    "before": "2c00a9fabcfb756442e3b4b1fee9b399d22e839d72a4bb2a9b60cf4228d28552",
    "step": 1
  }
]
```
