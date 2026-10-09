# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "reinforcement-partial"
- case_sha256: "0f12886069bc00c31648d6926f5e772806b7c4b49c016a20ad4e81d04cc8cb69"
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
- script_id: "reinforcement-partial"
- script_sha256: "6f66348c0c8c177e2201e560b6c31425617e9f2d51745256a7c606f4b7ecda09"
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

- attempts_per_round: 0.5
- dispatched_calls: 1
- round_trips: 2
- round_trips_completed: 2
- tool_call_attempts: 1

## Score

- gross_credit: 2
- harm_total: 0
- maximum_credit: 12
- maximum_harm: 4
- net_credit: 2
- primary_score: 0.16666666666666666

### Objectives
| id | credit | maximum | rung_index |
|---|---|---|---|
| "neutralise-attacker" | 0 | 4 | null |
| "rescue-civilian" | 0 | 4 | null |
| "reinforce" | 2 | 4 | 0 |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "military-loss" | "military_asset" | "not_fired" | "event" | 4 | 0 | [] | null |

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
  "net_credit": 2,
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
      "credit": 2,
      "id": "reinforce",
      "maximum": 4,
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
      },
      "rung_index": 0,
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
  "primary_score": 0.16666666666666666,
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
[]
```

### undercredit
```json
[]
```

## Capture

- available: true
- in_episode_share: 0.0024582564433512743
- in_episode_total_s: 0.7374769330053823
- non_drain_residual_s: 0.27453862100082915
- post_drain_total_s: 0.8012512510031229
- pre_drain_total_s: 0.40097890299512073
- total_s: 1.4767687749990728

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 4,
  "in_episode_share": 0.0024582564433512743,
  "in_episode_total_s": 0.7374769330053823,
  "lua_executions_total": 4,
  "max_s": 0.3743477609968977,
  "mean_s": 0.3691921937497682,
  "non_drain_residual_s": 0.27453862100082915,
  "p95_s": 0.3743477609968977,
  "post_drain_total_s": 0.8012512510031229,
  "pre_drain_total_s": 0.40097890299512073,
  "total_s": 1.4767687749990728,
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
"5ba947d8e8794c0fbd1d06f10fe0d424693eb056c2c0872c744615dd4297c638"
```

### initial
```json
"8afb5df47442d7abfdaf245c07f2755fae10ee27c119a94181d820c83b840b6b"
```

### steps
```json
[
  {
    "after": "5ba947d8e8794c0fbd1d06f10fe0d424693eb056c2c0872c744615dd4297c638",
    "before": "8afb5df47442d7abfdaf245c07f2755fae10ee27c119a94181d820c83b840b6b",
    "step": 0
  }
]
```
