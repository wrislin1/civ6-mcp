# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "partial-resource"
- case_sha256: "67a85c16c0863ec6198698cb851721797011193231760a6ab152b0d6700b8bf4"
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
- script_id: "partial-resource"
- script_sha256: "1961b337fcec031dfc7a49ce28d973dc6d8192b9ad7d12ced70d58060fafae75"
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
- validation_failures: []
- validation_status: "passed_mechanics"

## Terminal

- terminal: "finish_trial"
- truncated_steps: []
- truncation_unavailable_steps: []

## Counters

- attempts_per_round: 1.0
- dispatched_calls: 2
- round_trips: 2
- round_trips_completed: 2
- tool_call_attempts: 2

## Score

- gross_credit: 2
- harm_total: 0
- maximum_credit: 12
- maximum_harm: 8
- net_credit: 2
- primary_score: 0.16666666666666666

### Objectives
| id | credit | maximum | rung_index |
|---|---|---|---|
| "restore-production" | 0 | 4 | null |
| "connect-resource" | 2 | 4 | 0 |
| "improve-food" | 0 | 4 | null |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "escort-loss" | "escort" | "not_fired" | "event" | 4 | 0 | [] | null |
| "new-exposure" | "civilian_exposure" | "not_fired" | "final" | 4 | 0 | [] | null |

### Scored evidence
```json
{
  "gross_credit": 2,
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
  "net_credit": 2,
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
  "primary_score": 0.16666666666666666,
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
    "tool_name": "skip_unit"
  }
]
```

## Audits

### loss_coverage
```json
{
  "declared": 0,
  "observed": 0,
  "steps_examined": 2,
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
- in_episode_share: 0.004962975466720915
- in_episode_total_s: 1.4888926400162745
- non_drain_residual_s: 0.42818900899146684
- post_drain_total_s: 1.202533306000987
- pre_drain_total_s: 0.6020351659972221
- total_s: 2.232757480989676

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 6,
  "in_episode_share": 0.004962975466720915,
  "in_episode_total_s": 1.4888926400162745,
  "lua_executions_total": 6,
  "max_s": 0.3785308329970576,
  "mean_s": 0.37212624683161266,
  "non_drain_residual_s": 0.42818900899146684,
  "p95_s": 0.3785308329970576,
  "post_drain_total_s": 1.202533306000987,
  "pre_drain_total_s": 0.6020351659972221,
  "total_s": 2.232757480989676,
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
"289afedaf7c7f572a60293b9d373a1318c4b6548f2e4168420bf063f638307e4"
```

### initial
```json
"62e56d7ea11b5650504a7b3ed750cf13fc8fa5199d0f463af75f6a689950717c"
```

### steps
```json
[
  {
    "after": "289afedaf7c7f572a60293b9d373a1318c4b6548f2e4168420bf063f638307e4",
    "before": "62e56d7ea11b5650504a7b3ed750cf13fc8fa5199d0f463af75f6a689950717c",
    "step": 0
  },
  {
    "after": "289afedaf7c7f572a60293b9d373a1318c4b6548f2e4168420bf063f638307e4",
    "before": "289afedaf7c7f572a60293b9d373a1318c4b6548f2e4168420bf063f638307e4",
    "step": 1
  }
]
```
