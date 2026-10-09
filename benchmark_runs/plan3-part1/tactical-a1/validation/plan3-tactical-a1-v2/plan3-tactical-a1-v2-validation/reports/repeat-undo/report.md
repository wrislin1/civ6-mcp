# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "repeat-undo"
- case_sha256: "36d7b682b6a2d271ad27d77b6194093b431ad5f9bc4de2ca20b8d5389402e54a"
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
- script_id: "repeat-undo"
- script_sha256: "84e99fa06ab0a2a532a32a37fb33e562db09f837a0fccfeed9539a0daa23d515"
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
- validation_failures: [{"reason": "rejected_operation", "result": "Error: STACKING_CONFLICT|Cannot purchase UNIT_SPEARMAN — UNIT_SPEARMAN (unit_id=1900558) is on the city tile. Move it with unit_action(unit_id=1900558, action='move', target_x, target_y) first, then retry the purchase.", "step": 1, "tool_name": "purchase_item"}]
- validation_status: "failed"

## Terminal

- terminal: "finish_trial"
- truncated_steps: []
- truncation_unavailable_steps: []

## Counters

- attempts_per_round: 0.8
- dispatched_calls: 4
- round_trips: 5
- round_trips_completed: 5
- tool_call_attempts: 4

## Score

- gross_credit: 4
- harm_total: 0
- maximum_credit: 12
- maximum_harm: 4
- net_credit: 4
- primary_score: 0.3333333333333333

### Objectives
| id | credit | maximum | rung_index |
|---|---|---|---|
| "neutralise-attacker" | 0 | 4 | null |
| "rescue-civilian" | 0 | 4 | null |
| "reinforce" | 4 | 4 | 1 |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "military-loss" | "military_asset" | "not_fired" | "event" | 4 | 0 | [] | null |

### Scored evidence
```json
{
  "gross_credit": 4,
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
  "net_credit": 4,
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
      "credit": 4,
      "id": "reinforce",
      "maximum": 4,
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
      },
      "rung_index": 1,
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
  "primary_score": 0.3333333333333333,
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
    "entries": [],
    "step": 1,
    "tool_name": "purchase_item"
  },
  {
    "entries": [],
    "step": 2,
    "tool_name": "set_city_production"
  },
  {
    "entries": [],
    "step": 3,
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
  "steps_examined": 4,
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
  },
  {
    "category": "insufficient_evidence",
    "evidence": {
      "economic_paths": [],
      "improvement": null,
      "losses": [],
      "movement": null,
      "step": 3,
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
- in_episode_share: 0.01000527731666807
- in_episode_total_s: 3.0015831950004213
- non_drain_residual_s: 0.702382869028952
- post_drain_total_s: 2.0207227199862245
- pre_drain_total_s: 1.0164085299911676
- total_s: 3.739514119006344

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 10,
  "in_episode_share": 0.01000527731666807,
  "in_episode_total_s": 3.0015831950004213,
  "lua_executions_total": 10,
  "max_s": 0.3811287179996725,
  "mean_s": 0.3739514119006344,
  "non_drain_residual_s": 0.702382869028952,
  "p95_s": 0.3811287179996725,
  "post_drain_total_s": 2.0207227199862245,
  "pre_drain_total_s": 1.0164085299911676,
  "total_s": 3.739514119006344,
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
"43c2b3e5c71c1abdfc550ab76e4076f832559b21ebd7c172b796ccee054883fb"
```

### initial
```json
"12e8dd59233b638df36f5482c00d1ce21dfc4160772d75daeaba0ee359790f81"
```

### steps
```json
[
  {
    "after": "bbd159fb591812f8d6137c896ea1b423490c796796c2600653d83702df444efe",
    "before": "12e8dd59233b638df36f5482c00d1ce21dfc4160772d75daeaba0ee359790f81",
    "step": 0
  },
  {
    "after": "bbd159fb591812f8d6137c896ea1b423490c796796c2600653d83702df444efe",
    "before": "bbd159fb591812f8d6137c896ea1b423490c796796c2600653d83702df444efe",
    "step": 1
  },
  {
    "after": "27a8d97776837bfd534ce8d2d91567b21f9eea714b3a04c6ff02ffbe3d28fd50",
    "before": "bbd159fb591812f8d6137c896ea1b423490c796796c2600653d83702df444efe",
    "step": 2
  },
  {
    "after": "43c2b3e5c71c1abdfc550ab76e4076f832559b21ebd7c172b796ccee054883fb",
    "before": "27a8d97776837bfd534ce8d2d91567b21f9eea714b3a04c6ff02ffbe3d28fd50",
    "step": 3
  }
]
```
