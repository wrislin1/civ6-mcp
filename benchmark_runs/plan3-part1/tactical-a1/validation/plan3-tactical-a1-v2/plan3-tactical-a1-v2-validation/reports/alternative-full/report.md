# Version-2 trial report

## Provenance

- actor_kind: "scripted"
- case_id: "alternative-full"
- case_sha256: "9fb1990d25f93ab312b80a0f77ddcaa26c606428a7ed103f1667d3268410d09b"
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
- script_id: "alternative-full"
- script_sha256: "3c275b6f4419f08ccb994992367ed42dccac6f2af497ecbbb55d4f06f975b9bd"
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

- attempts_per_round: 1.0
- dispatched_calls: 4
- round_trips: 4
- round_trips_completed: 4
- tool_call_attempts: 4

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
| "neutralise-attacker" | 4 | 4 | 1 |
| "rescue-civilian" | 4 | 4 | 0 |
| "reinforce" | 4 | 4 | 1 |

### Harms
| id | loss_key | status | timing | weight | deduction | fired_steps | charged_harm |
|---|---|---|---|---|---|---|---|
| "military-loss" | "military_asset" | "compensated" | "event" | 4 | 0 | [1] | null |

### Scored evidence
```json
{
  "gross_credit": 12,
  "harm_total": 0,
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
          "satisfied": true,
          "step": null,
          "timing": "final"
        }
      ],
      "deduction": 0,
      "fired": true,
      "fired_steps": [
        1
      ],
      "first_step": 1,
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
      "status": "compensated",
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
      "id": "neutralise-attacker",
      "maximum": 4,
      "predicate": {
        "kind": "target_neutralised",
        "target": [
          63,
          16908390
        ]
      },
      "rung_index": 1,
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
      "credit": 4,
      "id": "rescue-civilian",
      "maximum": 4,
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
      },
      "rung_index": 0,
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
| ["gold"] | 500 | 240 | -260 | "measured" | [3] |
| ["units", "0:1835021", "charges"] | 0 | null | null | "unavailable" | [1] |

### Steps
```json
[
  {
    "entries": [],
    "step": 0,
    "tool_name": "move_unit"
  },
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
          1
        ],
        "tool_name": "attack_unit",
        "units": "charges"
      }
    ],
    "step": 1,
    "tool_name": "attack_unit"
  },
  {
    "entries": [],
    "step": 2,
    "tool_name": "attack_unit"
  },
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
          3
        ],
        "tool_name": "purchase_item",
        "units": "gold"
      }
    ],
    "step": 3,
    "tool_name": "purchase_item"
  }
]
```

## Audits

### loss_coverage
```json
{
  "declared": 1,
  "observed": 1,
  "steps_examined": 4,
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
    "step": 1,
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
      "step": 1,
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
- in_episode_share: 0.009991391596655982
- in_episode_total_s: 2.9974174789967947
- non_drain_residual_s: 0.7143670150107937
- post_drain_total_s: 2.022295613991446
- pre_drain_total_s: 1.0127986869920278
- total_s: 3.7494613159942674

### Unavailable
```json
{}
```

### Summary
```json
{
  "all_single_execution": true,
  "count": 10,
  "in_episode_share": 0.009991391596655982,
  "in_episode_total_s": 2.9974174789967947,
  "lua_executions_total": 10,
  "max_s": 0.38493937299062964,
  "mean_s": 0.37494613159942675,
  "non_drain_residual_s": 0.7143670150107937,
  "p95_s": 0.38493937299062964,
  "post_drain_total_s": 2.022295613991446,
  "pre_drain_total_s": 1.0127986869920278,
  "total_s": 3.7494613159942674,
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
"5c6b5579a5b5b179ea9e8dc151da2bc1467f611224aaea103134e4086a29150b"
```

### initial
```json
"12e8dd59233b638df36f5482c00d1ce21dfc4160772d75daeaba0ee359790f81"
```

### steps
```json
[
  {
    "after": "18dd5515e0b2b9863a8cbe35d3926119aaf48b9c220b273e636e69e2f1f34fd3",
    "before": "12e8dd59233b638df36f5482c00d1ce21dfc4160772d75daeaba0ee359790f81",
    "step": 0
  },
  {
    "after": "06945fa97623a36a5e481066b30c5b80078f11db6e0e63c85a692ab89a003124",
    "before": "18dd5515e0b2b9863a8cbe35d3926119aaf48b9c220b273e636e69e2f1f34fd3",
    "step": 1
  },
  {
    "after": "09bbc83431c505184b49e515fdc8fd60b0b94c1af5900004a211d16f9d6f6dbd",
    "before": "06945fa97623a36a5e481066b30c5b80078f11db6e0e63c85a692ab89a003124",
    "step": 2
  },
  {
    "after": "5c6b5579a5b5b179ea9e8dc151da2bc1467f611224aaea103134e4086a29150b",
    "before": "09bbc83431c505184b49e515fdc8fd60b0b94c1af5900004a211d16f9d6f6dbd",
    "step": 3
  }
]
```
