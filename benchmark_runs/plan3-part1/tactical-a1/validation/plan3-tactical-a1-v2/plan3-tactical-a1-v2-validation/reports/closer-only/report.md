# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "closer-only"
- case_sha256: "00b3c78512f74a828e8819d88d16dad7c7fed789689b6482ff66c6c6fa7aa237"
- contract_fingerprint: "af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697"
- contract_identity: "af0de81714e8319d0983540c25ef8b08afe083ab30b81a62d4229a7921649697"
- counting: false
- coverage: {"area": [[70, 19], [70, 20], [70, 21], [70, 22], [70, 23], [71, 17], [71, 18], [71, 19], [71, 20], [71, 21], [71, 22], [71, 23], [72, 17], [72, 18], [72, 19], [72, 20], [72, 21], [72, 22], [72, 23], [72, 24], [73, 17], [73, 18], [73, 19], [73, 20], [73, 21], [73, 22], [73, 23], [73, 24], [74, 17], [74, 18], [74, 19], [74, 20], [74, 21], [74, 22], [74, 23], [74, 24], [75, 17], [75, 18], [75, 19], [75, 20], [75, 21], [75, 22], [75, 23], [75, 24], [76, 18], [76, 19], [76, 20], [76, 21], [76, 22], [76, 23], [76, 24]], "include_owned_tiles": true, "tracked_targets": [[63, 16908390]]}
- evidence_version: "2.0.0"
- position_id: "plan3-tactical-a1-v2"
- position_version: 2
- public_observation_sha256: "425cdd993975a2d96341c3006f4e53062ef2701c7809ae15bf24f0305459ce85"
- public_task_tiles: []
- public_task_tiles_sha256: "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
- rubric_sha256: "9ff424e0245dc3fe0c4b51e09b707dbb0ca1d16579dbd25266dbe7ea5a27c705"
- script_id: "closer-only"
- script_sha256: "fbab46b1a4f92768eb071758b602066731583a4aee72f30cbdfeda4a55d0ba0c"
- session_fingerprint: "5b518707973bad33af34917abba6776f85f1aa63d8d98ce74703d271b28788b7"
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

- attempts_per_round: 0.5
- dispatched_calls: 1
- round_trips: 2
- round_trips_completed: 2
- tool_call_attempts: 1

## Score

- gross_credit: 0
- harm_total: 0
- maximum_credit: 12
- maximum_harm: 4
- net_credit: 0
- primary_score: 0.0

### Objectives
| id | credit | maximum | rung_index |
|---|---|---|---|
| "neutralise-attacker" | 0 | 4 | null |
| "rescue-civilian" | 0 | 4 | null |
| "reinforce" | 0 | 4 | null |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "military-loss" | "military_asset" | "not_fired" | "event" | 4 | 0 | [] | null |

### Scored evidence
```json
{
  "gross_credit": 0,
  "harm_total": 0,
  "harms": [
    {
      "charged_harm": null,
      "compensation": [],
      "deduction": 0,
      "fired": false,
      "fired_steps": [],
      "first_step": null,
      "id": "military-loss",
      "loss_key": "military_asset",
      "objective_id": "neutralise-attacker",
      "priority": 0,
      "reference": [
        {
          "kind": "unit_lost",
          "unit": [
            0,
            1835021
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
  "net_credit": 0,
  "objectives": [
    {
      "credit": 0,
      "id": "neutralise-attacker",
      "maximum": 4,
      "predicate": null,
      "rung_index": null,
      "rungs": [
        {
          "points": 2,
          "predicate": {
            "kind": "target_damaged",
            "minimum_damage": 40,
            "target": [
              63,
              16908390
            ]
          }
        },
        {
          "points": 4,
          "predicate": {
            "kind": "target_neutralised",
            "target": [
              63,
              16908390
            ]
          }
        }
      ]
    },
    {
      "credit": 0,
      "id": "rescue-civilian",
      "maximum": 4,
      "predicate": null,
      "rung_index": null,
      "rungs": [
        {
          "points": 4,
          "predicate": {
            "kind": "civilian_covered",
            "tiles": [
              [
                73,
                19
              ]
            ],
            "unit": [
              0,
              1769484
            ]
          }
        }
      ]
    },
    {
      "credit": 0,
      "id": "reinforce",
      "maximum": 4,
      "predicate": null,
      "rung_index": null,
      "rungs": [
        {
          "points": 2,
          "predicate": {
            "cities": [
              262147
            ],
            "items": [
              "UNIT_SPEARMAN",
              "UNIT_SWORDSMAN"
            ],
            "kind": "active_production",
            "repair": false
          }
        },
        {
          "points": 4,
          "predicate": {
            "kind": "unit_in_area",
            "tiles": [
              [
                72,
                18
              ],
              [
                73,
                18
              ],
              [
                74,
                18
              ],
              [
                72,
                19
              ],
              [
                73,
                19
              ],
              [
                74,
                19
              ],
              [
                72,
                20
              ],
              [
                73,
                20
              ],
              [
                74,
                20
              ]
            ],
            "unit_types": [
              "UNIT_SPEARMAN",
              "UNIT_SWORDSMAN"
            ]
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

### Steps
```json
[
  {
    "entries": [],
    "step": 0,
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
  "steps_examined": 1,
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
    "category": "non_builder_movement",
    "evidence": {
      "economic_paths": [],
      "improvement": null,
      "losses": [],
      "movement": {
        "builder": false,
        "distance_after": null,
        "distance_before": null,
        "from": [
          74,
          20
        ],
        "to": [
          74,
          21
        ],
        "unit": [
          0,
          1769484
        ]
      },
      "step": 0,
      "tool_name": "move_unit"
    },
    "primary_deduction": 0,
    "tags": [
      "non_builder_move"
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
- in_episode_share: 0.0024552558466772703
- in_episode_total_s: 0.7365767540031811
- non_drain_residual_s: 0.27163367002503946
- post_drain_total_s: 0.8015511660050834
- pre_drain_total_s: 0.4012283199845115
- total_s: 1.4744131560146343

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 4,
  "in_episode_share": 0.0024552558466772703,
  "in_episode_total_s": 0.7365767540031811,
  "lua_executions_total": 4,
  "max_s": 0.37417143100174144,
  "mean_s": 0.3686032890036586,
  "non_drain_residual_s": 0.27163367002503946,
  "p95_s": 0.37417143100174144,
  "post_drain_total_s": 0.8015511660050834,
  "pre_drain_total_s": 0.4012283199845115,
  "total_s": 1.4744131560146343,
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
"a824714ec955228928d410d9173a7d1be8609a77fbbf57ffc9a9b46bbd7c9da3"
```

### initial
```json
"12e8dd59233b638df36f5482c00d1ce21dfc4160772d75daeaba0ee359790f81"
```

### steps
```json
[
  {
    "after": "a824714ec955228928d410d9173a7d1be8609a77fbbf57ffc9a9b46bbd7c9da3",
    "before": "12e8dd59233b638df36f5482c00d1ce21dfc4160772d75daeaba0ee359790f81",
    "step": 0
  }
]
```
