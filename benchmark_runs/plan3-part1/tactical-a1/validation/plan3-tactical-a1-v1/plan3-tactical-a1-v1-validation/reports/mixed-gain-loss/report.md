# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "mixed-gain-loss"
- case_sha256: "af593dd1b3968fdaf92f1cbc94ad0b8d678612d7bc3292d41e5f6494264259b7"
- contract_fingerprint: "0858553801927366fda0e90f66dfec072f57de4511bbf45b1d0448ab91f7974a"
- contract_identity: "0858553801927366fda0e90f66dfec072f57de4511bbf45b1d0448ab91f7974a"
- counting: false
- coverage: {"area": [[69, 19], [69, 20], [69, 21], [69, 22], [69, 23], [70, 19], [70, 20], [70, 21], [70, 22], [70, 23], [71, 17], [71, 18], [71, 19], [71, 20], [71, 21], [71, 22], [71, 23], [71, 24], [72, 17], [72, 18], [72, 19], [72, 20], [72, 21], [72, 22], [72, 23], [72, 24], [73, 17], [73, 18], [73, 19], [73, 20], [73, 21], [73, 22], [73, 23], [73, 24], [74, 17], [74, 18], [74, 19], [74, 20], [74, 21], [74, 22], [74, 23], [74, 24], [75, 17], [75, 18], [75, 19], [75, 20], [75, 21], [75, 22], [75, 23], [75, 24], [76, 18], [76, 19], [76, 20], [76, 21], [76, 22]], "include_owned_tiles": true, "tracked_targets": [[63, 16908390]]}
- evidence_version: "2.0.0"
- position_id: "plan3-tactical-a1-v1"
- position_version: 1
- public_observation_sha256: "c2aae876e6a5681b198f4f91e634b978ea85fdba6b3cb067ee19a0d884e96848"
- public_task_tiles: []
- public_task_tiles_sha256: "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
- rubric_sha256: "9ff424e0245dc3fe0c4b51e09b707dbb0ca1d16579dbd25266dbe7ea5a27c705"
- script_id: "mixed-gain-loss"
- script_sha256: "e14d4afa43883416c9a45add344c27365f1993e030b827a5876c6f98383bdd25"
- session_fingerprint: "f9f5b739e0574c33be50cb57898656739b36bcdf132ddc57a95702815c2783db"
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
  },
  {
    "entries": [],
    "step": 1,
    "tool_name": "attack_unit"
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
          73,
          22
        ],
        "to": [
          73,
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
  },
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
          71,
          21
        ],
        "to": [
          -9999,
          -9999
        ],
        "unit": [
          0,
          1835021
        ]
      },
      "step": 1,
      "tool_name": "attack_unit"
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
- in_episode_share: 0.004890769086632645
- in_episode_total_s: 1.4672307259897934
- non_drain_residual_s: 0.4097929619747447
- post_drain_total_s: 1.2026907510007732
- pre_drain_total_s: 0.6022872510075103
- total_s: 2.214770963983028

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 6,
  "in_episode_share": 0.004890769086632645,
  "in_episode_total_s": 1.4672307259897934,
  "lua_executions_total": 6,
  "max_s": 0.374914751999313,
  "mean_s": 0.36912849399717135,
  "non_drain_residual_s": 0.4097929619747447,
  "p95_s": 0.374914751999313,
  "post_drain_total_s": 1.2026907510007732,
  "pre_drain_total_s": 0.6022872510075103,
  "total_s": 2.214770963983028,
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
"8c00843d23f022f952dec40c50237eb522d2597e692604cae5f5dbcf92090dd2"
```

### initial
```json
"8afb5df47442d7abfdaf245c07f2755fae10ee27c119a94181d820c83b840b6b"
```

### steps
```json
[
  {
    "after": "ac3b440ed8ecf6bd21b932175ad476dfed0e246bb081862506673b9d7a31f23a",
    "before": "8afb5df47442d7abfdaf245c07f2755fae10ee27c119a94181d820c83b840b6b",
    "step": 0
  },
  {
    "after": "8c00843d23f022f952dec40c50237eb522d2597e692604cae5f5dbcf92090dd2",
    "before": "ac3b440ed8ecf6bd21b932175ad476dfed0e246bb081862506673b9d7a31f23a",
    "step": 1
  }
]
```
