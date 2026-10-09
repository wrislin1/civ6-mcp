# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "joint-full"
- case_sha256: "b6284565b8d387a878d753c2758242a1685220c1822652bbb823e51a858e4ea9"
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
- script_id: "joint-full"
- script_sha256: "d6242be99b989384b8e55a8bd85eb3c537ae22e3b5484cca67beb1dca3bd26ae"
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

- attempts_per_round: 1.5
- dispatched_calls: 3
- round_trips: 2
- round_trips_completed: 2
- tool_call_attempts: 3

## Score

- gross_credit: 12
- harm_total: 0
- maximum_credit: 12
- maximum_harm: 4
- net_credit: 12
- primary_score: 1.0

### Objectives
| id | credit | maximum | rung_index |
|---|---|---|---|
| "address-housing" | 4 | 4 | 1 |
| "commit-district" | 4 | 4 | 0 |
| "initiate-repair" | 4 | 4 | 0 |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "destructive-placement" | "productive_asset" | "not_fired" | "event" | 4 | 0 | [] | null |

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
      "status": "not_fired",
      "timing": "event",
      "weight": 4
    }
  ],
  "maximum_credit": 12,
  "maximum_harm": 4,
  "net_credit": 12,
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
      "credit": 4,
      "id": "commit-district",
      "maximum": 4,
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
      },
      "rung_index": 0,
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
      "credit": 4,
      "id": "initiate-repair",
      "maximum": 4,
      "predicate": {
        "cities": [
          196610
        ],
        "items": [
          "BUILDING_MONUMENT"
        ],
        "kind": "active_production",
        "repair": true
      },
      "rung_index": 0,
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
  "primary_score": 1.0,
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
| ["tiles", "68,24", "food"] | 2 | 0 | -2 | "measured" | [1] |
| ["tiles", "68,24", "production"] | 2 | 0 | -2 | "measured" | [1] |

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
        "before": 2,
        "coverage": "measured",
        "delta": -2,
        "path": [
          "tiles",
          "68,24",
          "food"
        ],
        "refs": {
          "tile": [
            68,
            24
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
          "68,24",
          "production"
        ],
        "refs": {
          "tile": [
            68,
            24
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
  },
  {
    "entries": [],
    "step": 2,
    "tool_name": "set_city_production"
  }
]
```

## Audits

### loss_coverage
```json
{
  "declared": 0,
  "observed": 0,
  "steps_examined": 3,
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
- in_episode_share: 0.0070501225034240635
- in_episode_total_s: 2.115036751027219
- non_drain_residual_s: 0.4160556950228056
- post_drain_total_s: 1.6023166249797214
- pre_drain_total_s: 0.8019851060234942
- total_s: 2.820357426026021

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 8,
  "in_episode_share": 0.0070501225034240635,
  "in_episode_total_s": 2.115036751027219,
  "lua_executions_total": 8,
  "max_s": 0.3572804089926649,
  "mean_s": 0.35254467825325264,
  "non_drain_residual_s": 0.4160556950228056,
  "p95_s": 0.3572804089926649,
  "post_drain_total_s": 1.6023166249797214,
  "pre_drain_total_s": 0.8019851060234942,
  "total_s": 2.820357426026021,
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
"238e27d51f9bbf7bc61462612c3a5373b9704a37decb458d2da72b392c35b821"
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
    "after": "321dbad555863f8d52f7b877d1862c2b0018bdae07d8d088a540af23a25cd350",
    "before": "55550e30f01833b59db4bf5b14406e5a18ae5dd9ebd19dcdd6bb5743e6629e88",
    "step": 1
  },
  {
    "after": "238e27d51f9bbf7bc61462612c3a5373b9704a37decb458d2da72b392c35b821",
    "before": "321dbad555863f8d52f7b877d1862c2b0018bdae07d8d088a540af23a25cd350",
    "step": 2
  }
]
```
