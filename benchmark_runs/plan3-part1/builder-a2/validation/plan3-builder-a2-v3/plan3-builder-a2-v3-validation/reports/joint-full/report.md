# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "joint-full"
- case_sha256: "affca7d4c16b0e1417047c5aea614ac277f9bf134665361f6fc2e88f863e9e6a"
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
- script_id: "joint-full"
- script_sha256: "b45cd2a0dd8956cd8e69b889adf4848f0e1a9e96850a0b698f0f134b4a5727ed"
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

- attempts_per_round: 2.3333333333333335
- dispatched_calls: 7
- round_trips: 3
- round_trips_completed: 3
- tool_call_attempts: 7

## Score

- gross_credit: 12
- harm_total: 0
- maximum_credit: 12
- maximum_harm: 8
- net_credit: 12
- primary_score: 1.0

### Objectives
| id | credit | maximum | rung_index |
|---|---|---|---|
| "restore-production" | 4 | 4 | 1 |
| "connect-resource" | 4 | 4 | 1 |
| "improve-food" | 4 | 4 | 1 |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "escort-loss" | "escort" | "not_fired" | "event" | 4 | 0 | [] | null |
| "new-exposure" | "civilian_exposure" | "not_fired" | "final" | 4 | 0 | [] | null |

### Scored evidence
```json
{
  "gross_credit": 12,
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
  "net_credit": 12,
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
      "credit": 4,
      "id": "connect-resource",
      "maximum": 4,
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
      },
      "rung_index": 1,
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
      "credit": 4,
      "id": "improve-food",
      "maximum": 4,
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
      },
      "rung_index": 1,
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
  "primary_score": 1.0,
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
| ["units", "0:1835021", "charges"] | 3 | 2 | -1 | "measured" | [4] |
| ["units", "0:1900558", "charges"] | 3 | 2 | -1 | "measured" | [5] |

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
    "tool_name": "move_unit"
  },
  {
    "entries": [],
    "step": 2,
    "tool_name": "move_unit"
  },
  {
    "entries": [],
    "step": 3,
    "tool_name": "repair_improvement"
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
          "0:1835021",
          "charges"
        ],
        "refs": {
          "unit": [
            0,
            1835021
          ]
        },
        "source_steps": [
          4
        ],
        "tool_name": "improve_tile",
        "units": "charges"
      }
    ],
    "step": 4,
    "tool_name": "improve_tile"
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
          "0:1900558",
          "charges"
        ],
        "refs": {
          "unit": [
            0,
            1900558
          ]
        },
        "source_steps": [
          5
        ],
        "tool_name": "improve_tile",
        "units": "charges"
      }
    ],
    "step": 5,
    "tool_name": "improve_tile"
  },
  {
    "entries": [],
    "step": 6,
    "tool_name": "fortify_unit"
  }
]
```

## Audits

### loss_coverage
```json
{
  "declared": 0,
  "observed": 0,
  "steps_examined": 7,
  "undeclared": 0
}
```

### losses
```json
[]
```

### uncredited
```json
[]
```

### undercredit
```json
[]
```

## Capture

- available: true
- in_episode_share: 0.017418881243502257
- in_episode_total_s: 5.225664373050677
- non_drain_residual_s: 1.158415855083149
- post_drain_total_s: 3.2068658029893413
- pre_drain_total_s: 1.6051430600055028
- total_s: 5.970424718077993

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 16,
  "in_episode_share": 0.017418881243502257,
  "in_episode_total_s": 5.225664373050677,
  "lua_executions_total": 16,
  "max_s": 0.3834877690242138,
  "mean_s": 0.37315154487987456,
  "non_drain_residual_s": 1.158415855083149,
  "p95_s": 0.3834877690242138,
  "post_drain_total_s": 3.2068658029893413,
  "pre_drain_total_s": 1.6051430600055028,
  "total_s": 5.970424718077993,
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
"760c5afcac8a05a8496b953b08254c119e7a868f38c124b01283f567b85c5cd5"
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
    "after": "a4c514c1764f72d3cbedb09480d93dcf028d37b331649a4b78e0fcc14d4b6948",
    "before": "8cc4f38cb5057a748ed3d574bc772d8be506e2d186cd6e4eb6faeecd7a4e9c9a",
    "step": 1
  },
  {
    "after": "83edd0e0959195fe86e4b4adb67e8f315ae5647dc1a90040e9afe0886c846e00",
    "before": "a4c514c1764f72d3cbedb09480d93dcf028d37b331649a4b78e0fcc14d4b6948",
    "step": 2
  },
  {
    "after": "02c154349ae9ab15b4072cc4e4441c1a77dd0a60c35dc0a8fcd061f557857e6f",
    "before": "83edd0e0959195fe86e4b4adb67e8f315ae5647dc1a90040e9afe0886c846e00",
    "step": 3
  },
  {
    "after": "c0e8f4e7b215f115af07a028b5ed01e3406a4bbcb07df5f88b13f35f853f4cba",
    "before": "02c154349ae9ab15b4072cc4e4441c1a77dd0a60c35dc0a8fcd061f557857e6f",
    "step": 4
  },
  {
    "after": "760c5afcac8a05a8496b953b08254c119e7a868f38c124b01283f567b85c5cd5",
    "before": "c0e8f4e7b215f115af07a028b5ed01e3406a4bbcb07df5f88b13f35f853f4cba",
    "step": 5
  },
  {
    "after": "760c5afcac8a05a8496b953b08254c119e7a868f38c124b01283f567b85c5cd5",
    "before": "760c5afcac8a05a8496b953b08254c119e7a868f38c124b01283f567b85c5cd5",
    "step": 6
  }
]
```
