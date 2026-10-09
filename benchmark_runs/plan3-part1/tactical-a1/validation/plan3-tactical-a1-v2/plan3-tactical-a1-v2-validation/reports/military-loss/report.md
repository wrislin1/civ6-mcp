# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "military-loss"
- case_sha256: "12a62a9c9f38eca16e17fbb21133bc04e7658d8f39e1397602b4183d5327e2e3"
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
- script_id: "military-loss"
- script_sha256: "72f2b5d6b1ec8434b73b86b4fea3bb91cac8376d57547c5e9c28c4118e0353b4"
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
- harm_total: 4
- maximum_credit: 12
- maximum_harm: 4
- net_credit: -4
- primary_score: -0.3333333333333333

### Objectives
| id | credit | maximum | rung_index |
|---|---|---|---|
| "neutralise-attacker" | 0 | 4 | null |
| "rescue-civilian" | 0 | 4 | null |
| "reinforce" | 0 | 4 | null |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "military-loss" | "military_asset" | "charged" | "event" | 4 | 4 | [0] | null |

### Scored evidence
```json
{
  "gross_credit": 0,
  "harm_total": 4,
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
        0
      ],
      "first_step": 0,
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
      "status": "charged",
      "timing": "event",
      "weight": 4
    }
  ],
  "maximum_credit": 12,
  "maximum_harm": 4,
  "net_credit": -4,
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
  "primary_score": -0.3333333333333333,
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
| ["units", "0:1835021", "charges"] | 0 | null | null | "unavailable" | [0] |

### Steps
```json
[
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
          0
        ],
        "tool_name": "attack_unit",
        "units": "charges"
      }
    ],
    "step": 0,
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
  "steps_examined": 1,
  "undeclared": 0
}
```

### losses
```json
[
  {
    "declared": true,
    "declared_harm_ids": [
      "military-loss"
    ],
    "entity": [
      0,
      1835021
    ],
    "kind": "unit",
    "lifecycle": "lost",
    "result_shape": "ok",
    "step": 0,
    "tool_name": "attack_unit"
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
          "units",
          "0:1835021",
          "charges"
        ]
      ],
      "improvement": null,
      "losses": [
        {
          "declared": true,
          "entity": [
            0,
            1835021
          ],
          "kind": "unit",
          "lifecycle": "lost"
        }
      ],
      "movement": null,
      "step": 0,
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
- in_episode_share: 0.002482761046655166
- in_episode_total_s: 0.7448283139965497
- non_drain_residual_s: 0.2925058819964761
- post_drain_total_s: 0.8026767749979626
- pre_drain_total_s: 0.4022478059923742
- total_s: 1.497430462986813

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 4,
  "in_episode_share": 0.002482761046655166,
  "in_episode_total_s": 0.7448283139965497,
  "lua_executions_total": 4,
  "max_s": 0.3780954599933466,
  "mean_s": 0.37435761574670323,
  "non_drain_residual_s": 0.2925058819964761,
  "p95_s": 0.3780954599933466,
  "post_drain_total_s": 0.8026767749979626,
  "pre_drain_total_s": 0.4022478059923742,
  "total_s": 1.497430462986813,
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
"5ac622d9c39c2ded9af91d69be5e9072b0dcbf1474ff79b7151164600a65049f"
```

### initial
```json
"12e8dd59233b638df36f5482c00d1ce21dfc4160772d75daeaba0ee359790f81"
```

### steps
```json
[
  {
    "after": "5ac622d9c39c2ded9af91d69be5e9072b0dcbf1474ff79b7151164600a65049f",
    "before": "12e8dd59233b638df36f5482c00d1ce21dfc4160772d75daeaba0ee359790f81",
    "step": 0
  }
]
```
