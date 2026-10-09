# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "mixed-gain-loss"
- case_sha256: "8292748325402c3ea46b96d4ebd163886a4e83ad770858d2be249cd10e1d9888"
- contract_fingerprint: "0858553801927366fda0e90f66dfec072f57de4511bbf45b1d0448ab91f7974a"
- contract_identity: "0858553801927366fda0e90f66dfec072f57de4511bbf45b1d0448ab91f7974a"
- counting: false
- coverage: {"area": [[66, 21], [66, 22], [66, 23], [66, 24], [66, 25], [67, 21], [67, 22], [67, 23], [67, 24], [67, 25], [68, 21], [68, 22], [68, 23], [68, 24], [68, 25], [69, 21], [69, 22], [69, 23], [69, 24], [69, 25], [70, 21], [70, 22], [70, 23], [71, 21], [71, 22], [71, 23]], "include_owned_tiles": true, "tracked_targets": []}
- evidence_version: "2.0.0"
- position_id: "plan3-city-a2-v1"
- position_version: 1
- public_observation_sha256: "f0f457730006fea96fb0a4b931733752cf333b622558c7fb172969401c1941e1"
- public_task_tiles: [[68, 24], [67, 23], [67, 22]]
- public_task_tiles_sha256: "218b9007caee5fa3ca5efa73cf8c81d2a01a66c2d027c256bb4e07f468533b25"
- rubric_sha256: "a5a47c3c56eb17de8bddfd798e6ba8c657ab993e6561d6cb5f849875e14f28e1"
- script_id: "mixed-gain-loss"
- script_sha256: "a6b43e4aaebb92f7f8f7c5caefa6ca3ae440041f56d94aad1681eb1647755c75"
- session_fingerprint: "f607d92b35e405c8d4d14764cd6ffb4e137aeb81c6e119a975134dc36577ad66"
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

- gross_credit: 4
- harm_total: 4
- maximum_credit: 12
- maximum_harm: 4
- net_credit: 0
- primary_score: 0.0

### Objectives
| id | credit | maximum | rung_index |
|---|---|---|---|
| "address-housing" | 4 | 4 | 1 |
| "commit-district" | 0 | 4 | null |
| "initiate-repair" | 0 | 4 | null |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "destructive-placement" | "productive_asset" | "charged" | "event" | 4 | 4 | [1] | null |

### Scored evidence
```json
{
  "gross_credit": 4,
  "harm_total": 4,
  "harms": [
    {
      "charged_harm": null,
      "compensation": [
        {
          "predicate": {
            "cities": [
              131073
            ],
            "district_types": [
              "DISTRICT_SEOWON"
            ],
            "kind": "district_committed",
            "tiles": [
              [
                67,
                22
              ]
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
      "id": "destructive-placement",
      "loss_key": "productive_asset",
      "objective_id": "commit-district",
      "priority": 0,
      "reference": [
        {
          "asset_fields": {
            "improvement": "IMPROVEMENT_QUARRY"
          },
          "kind": "asset_displaced",
          "tiles": [
            [
              67,
              23
            ]
          ]
        },
        {
          "asset_fields": {
            "improvement": "IMPROVEMENT_MINE"
          },
          "kind": "asset_displaced",
          "tiles": [
            [
              67,
              22
            ]
          ]
        }
      ],
      "status": "charged",
      "timing": "event",
      "weight": 4
    }
  ],
  "maximum_credit": 12,
  "maximum_harm": 4,
  "net_credit": 0,
  "objectives": [
    {
      "credit": 4,
      "id": "address-housing",
      "maximum": 4,
      "predicate": {
        "cities": [
          131073
        ],
        "kind": "housing_resolved",
        "minimum_surplus": 1,
        "remedy_buildings": [
          "BUILDING_GRANARY"
        ]
      },
      "rung_index": 1,
      "rungs": [
        {
          "points": 2,
          "predicate": {
            "cities": [
              131073
            ],
            "items": [
              "BUILDING_GRANARY"
            ],
            "kind": "active_production",
            "repair": false
          }
        },
        {
          "points": 4,
          "predicate": {
            "cities": [
              131073
            ],
            "kind": "housing_resolved",
            "minimum_surplus": 1,
            "remedy_buildings": [
              "BUILDING_GRANARY"
            ]
          }
        }
      ]
    },
    {
      "credit": 0,
      "id": "commit-district",
      "maximum": 4,
      "predicate": null,
      "rung_index": null,
      "rungs": [
        {
          "points": 4,
          "predicate": {
            "cities": [
              131073
            ],
            "district_types": [
              "DISTRICT_SEOWON"
            ],
            "kind": "district_committed",
            "tiles": [
              [
                68,
                24
              ],
              [
                67,
                22
              ]
            ]
          }
        }
      ]
    },
    {
      "credit": 0,
      "id": "initiate-repair",
      "maximum": 4,
      "predicate": null,
      "rung_index": null,
      "rungs": [
        {
          "points": 4,
          "predicate": {
            "cities": [
              196610
            ],
            "items": [
              "BUILDING_MONUMENT"
            ],
            "kind": "active_production",
            "repair": true
          }
        }
      ]
    }
  ],
  "primary_score": 0.0,
  "scales": {
    "attainable_values": [
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
    "maximum_harm": 4,
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
| ["gold"] | 500 | 240 | -260 | "measured" | [0] |
| ["resources", "RESOURCE_STONE", "access"] | 1 | 0 | -1 | "measured" | [1] |
| ["tiles", "67,23", "food"] | 2 | 0 | -2 | "measured" | [1] |
| ["tiles", "67,23", "production"] | 2 | 0 | -2 | "measured" | [1] |

### Steps
```json
[
  {
    "entries": [
      {
        "after": 240,
        "before": 500,
        "coverage": "measured",
        "delta": -260,
        "path": [
          "gold"
        ],
        "refs": {},
        "source_steps": [
          0
        ],
        "tool_name": "purchase_item",
        "units": "gold"
      }
    ],
    "step": 0,
    "tool_name": "purchase_item"
  },
  {
    "entries": [
      {
        "after": 0,
        "before": 1,
        "coverage": "measured",
        "delta": -1,
        "path": [
          "resources",
          "RESOURCE_STONE",
          "access"
        ],
        "refs": {
          "resource": "RESOURCE_STONE"
        },
        "source_steps": [
          1
        ],
        "tool_name": "set_city_production",
        "units": "access"
      },
      {
        "after": 0,
        "before": 2,
        "coverage": "measured",
        "delta": -2,
        "path": [
          "tiles",
          "67,23",
          "food"
        ],
        "refs": {
          "tile": [
            67,
            23
          ]
        },
        "source_steps": [
          1
        ],
        "tool_name": "set_city_production",
        "units": "yield"
      },
      {
        "after": 0,
        "before": 2,
        "coverage": "measured",
        "delta": -2,
        "path": [
          "tiles",
          "67,23",
          "production"
        ],
        "refs": {
          "tile": [
            67,
            23
          ]
        },
        "source_steps": [
          1
        ],
        "tool_name": "set_city_production",
        "units": "yield"
      }
    ],
    "step": 1,
    "tool_name": "set_city_production"
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
      "destructive-placement"
    ],
    "entity": [
      67,
      23
    ],
    "kind": "improvement",
    "lifecycle": "lost",
    "result_shape": "ok",
    "step": 1,
    "tool_name": "set_city_production"
  }
]
```

### uncredited
```json
[
  {
    "category": "declared_harm",
    "evidence": {
      "economic_paths": [
        [
          "resources",
          "RESOURCE_STONE",
          "access"
        ],
        [
          "tiles",
          "67,23",
          "food"
        ],
        [
          "tiles",
          "67,23",
          "production"
        ]
      ],
      "improvement": null,
      "losses": [
        {
          "declared": true,
          "entity": [
            67,
            23
          ],
          "kind": "improvement",
          "lifecycle": "lost"
        }
      ],
      "movement": null,
      "step": 1,
      "tool_name": "set_city_production"
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
- in_episode_share: 0.004692582379987774
- in_episode_total_s: 1.4077747139963321
- non_drain_residual_s: 0.3128408420161577
- post_drain_total_s: 1.201977831995464
- pre_drain_total_s: 0.6015561999956844
- total_s: 2.116374874007306

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 6,
  "in_episode_share": 0.004692582379987774,
  "in_episode_total_s": 1.4077747139963321,
  "lua_executions_total": 6,
  "max_s": 0.36004605500784237,
  "mean_s": 0.3527291456678843,
  "non_drain_residual_s": 0.3128408420161577,
  "p95_s": 0.36004605500784237,
  "post_drain_total_s": 1.201977831995464,
  "pre_drain_total_s": 0.6015561999956844,
  "total_s": 2.116374874007306,
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
"c26cbcba638f18c793ebaffe2e21c84d04872b1b94421673d79e71d5054c0fde"
```

### initial
```json
"9164c5fa6de5940b7a02a908175837264b522e01369e7b6939ce7aa3fd706731"
```

### steps
```json
[
  {
    "after": "55550e30f01833b59db4bf5b14406e5a18ae5dd9ebd19dcdd6bb5743e6629e88",
    "before": "9164c5fa6de5940b7a02a908175837264b522e01369e7b6939ce7aa3fd706731",
    "step": 0
  },
  {
    "after": "c26cbcba638f18c793ebaffe2e21c84d04872b1b94421673d79e71d5054c0fde",
    "before": "55550e30f01833b59db4bf5b14406e5a18ae5dd9ebd19dcdd6bb5743e6629e88",
    "step": 1
  }
]
```
