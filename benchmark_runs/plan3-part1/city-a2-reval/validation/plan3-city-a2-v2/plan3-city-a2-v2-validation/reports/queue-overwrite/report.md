# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "queue-overwrite"
- case_sha256: "ab2768108a1f15560ae14222e4b0f88d95c51f8d5b3832c882f4c5dc190ad1ac"
- contract_fingerprint: "af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697"
- contract_identity: "af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697"
- counting: false
- coverage: {"area": [[66, 21], [66, 22], [66, 23], [66, 24], [66, 25], [67, 21], [67, 22], [67, 23], [67, 24], [67, 25], [68, 21], [68, 22], [68, 23], [68, 24], [68, 25], [69, 21], [69, 22], [69, 23], [69, 24], [69, 25], [70, 21], [70, 22], [70, 23], [71, 21], [71, 22], [71, 23]], "include_owned_tiles": true, "tracked_targets": []}
- evidence_version: "2.0.0"
- position_id: "plan3-city-a2-v2"
- position_version: 2
- public_observation_sha256: "9a52f573280d0dda9d906db19e878c87d3be344e5636ee3045fb0f56049eb7d3"
- public_task_tiles: [[68, 24], [67, 23], [67, 22]]
- public_task_tiles_sha256: "218b9007caee5fa3ca5efa73cf8c81d2a01a66c2d027c256bb4e07f468533b25"
- rubric_sha256: "a5a47c3c56eb17de8bddfd798e6ba8c657ab993e6561d6cb5f849875e14f28e1"
- script_id: "queue-overwrite"
- script_sha256: "eaa50de6e56801f20aba1d5a341508c2173d2aa8889fe0b4ac956705662b98d5"
- session_fingerprint: "d94a820dabe6b892530cda4dc4c588bdd98b249cfdc8357f76f7017f7622bef9"
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

- attempts_per_round: 0.75
- dispatched_calls: 3
- round_trips: 4
- round_trips_completed: 4
- tool_call_attempts: 3

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
| "address-housing" | 0 | 4 | null |
| "commit-district" | 0 | 4 | null |
| "initiate-repair" | 4 | 4 | 0 |

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
      "credit": 0,
      "id": "address-housing",
      "maximum": 4,
      "predicate": null,
      "rung_index": null,
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
| ["gold"] | 500 | 500 | 0 | "measured" | [] |
| ["tiles", "67,22", "food"] | 2 | 0 | -2 | "measured" | [1] |
| ["tiles", "67,22", "production"] | 1 | 0 | -1 | "measured" | [1] |

### Steps
```json
[
  {
    "entries": [],
    "step": 0,
    "tool_name": "set_city_production"
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
          "67,22",
          "food"
        ],
        "refs": {
          "tile": [
            67,
            22
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
        "before": 1,
        "coverage": "measured",
        "delta": -1,
        "path": [
          "tiles",
          "67,22",
          "production"
        ],
        "refs": {
          "tile": [
            67,
            22
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
  "declared": 1,
  "observed": 1,
  "steps_examined": 3,
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
      22
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
    "category": "insufficient_evidence",
    "evidence": {
      "economic_paths": [],
      "improvement": null,
      "losses": [],
      "movement": null,
      "step": 2,
      "tool_name": "set_city_production"
    },
    "primary_deduction": 0,
    "tags": []
  }
]
```

### undercredit
```json
[
  {
    "final_credit": 0,
    "max_attained": 4,
    "objective_id": "commit-district",
    "steps": [
      1
    ]
  }
]
```

## Capture

- available: true
- in_episode_share: 0.007410317043443987
- in_episode_total_s: 2.223095113033196
- non_drain_residual_s: 0.5588353980274405
- post_drain_total_s: 1.603338209039066
- pre_drain_total_s: 0.8021429649961647
- total_s: 2.964316572062671

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 8,
  "in_episode_share": 0.007410317043443987,
  "in_episode_total_s": 2.223095113033196,
  "lua_executions_total": 8,
  "max_s": 0.375097596028354,
  "mean_s": 0.3705395715078339,
  "non_drain_residual_s": 0.5588353980274405,
  "p95_s": 0.375097596028354,
  "post_drain_total_s": 1.603338209039066,
  "pre_drain_total_s": 0.8021429649961647,
  "total_s": 2.964316572062671,
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
"8857fa790949afaf037fe5f8b09e8a4516cf211ddb1acf7dfd946154dfbe85d2"
```

### initial
```json
"9164c5fa6de5940b7a02a908175837264b522e01369e7b6939ce7aa3fd706731"
```

### steps
```json
[
  {
    "after": "ebe0d562f8904f5fb74e8f53fde7965fd9fdc6909839cd874698c3770f829ef7",
    "before": "9164c5fa6de5940b7a02a908175837264b522e01369e7b6939ce7aa3fd706731",
    "step": 0
  },
  {
    "after": "2c50c7f6528720960d829b450f51e8aa96119e270154aaf7a6d337566436e694",
    "before": "ebe0d562f8904f5fb74e8f53fde7965fd9fdc6909839cd874698c3770f829ef7",
    "step": 1
  },
  {
    "after": "8857fa790949afaf037fe5f8b09e8a4516cf211ddb1acf7dfd946154dfbe85d2",
    "before": "2c50c7f6528720960d829b450f51e8aa96119e270154aaf7a6d337566436e694",
    "step": 2
  }
]
```
