# Arena Tool Coverage Audit

## Scope and Method

This audit compares three surfaces: MCP tools exposed in `src/civ_mcp/server.py`, the arena registry in `src/civ_mcp/arena/registry.py`, and the unit-action reference in `CLAUDE.md`. The registry increased from 89 tools before this change to 90 after it. The `unit_action` cases define the direct gameplay-action set; those cases are compared with the action verbs exposed by the arena registry and documented in `CLAUDE.md`.

The deterministic registry snapshot after this change is: `registry=90`, `minimal=15`, `standard=26`, and `full=90`.

## Fixed This Cycle

`repair_improvement` is `add-to-standard-now`: it is a routine, non-destructive builder action that restores a pillaged improvement on the builder's current tile. `get_builder_tasks` is also `add-to-standard-now`, so a standard-tier pilot can identify routine builder work before acting.

The MCP unit-action reference now documents the three previously omitted actions: `repair`, `remove_improvement`, and `sacrifice_charges`.

## Unit-Action Matrix

The MCP `unit_action` declaration contains all 20 direct gameplay actions. Arena aliases are `start_trade_route` to `trade_route`, `teleport_trader` to `teleport`, and `activate_great_person` to `activate`.

| MCP unit action | Arena disposition |
|---|---|
| `move` | present |
| `attack` | present |
| `fortify` | present |
| `skip` | present |
| `found_city` | present |
| `improve` | present |
| `repair` | present |
| `remove_improvement` | listed-for-later |
| `remove_feature` | present |
| `build_route` | listed-for-later |
| `automate` | present |
| `heal` | present |
| `alert` | present |
| `sleep` | listed-for-later |
| `delete` | listed-for-later |
| `trade_route` | present via `start_trade_route` |
| `activate` | present via `activate_great_person` |
| `sacrifice_charges` | listed-for-later |
| `teleport` | present via `teleport_trader` |
| `spread_religion` | present |

The five post-change arena gaps are `build_route`, `delete`, `remove_improvement`, `sacrifice_charges`, and `sleep`; each is `listed-for-later`. The complete MCP unit-action reference has no actions absent from `CLAUDE.md`.

## Non-Action Exposed Helpers

`check_game_over`, `get_diary_snapshot`, `get_game_identity`, `get_threat_scan`, and `submit_congress` are composed/internal helpers and are `intentionally-excluded` from the arena registry. `end_turn`, `execute_lua`, `load_game_save`, and `load_save` are lifecycle/ops helpers and are likewise `intentionally-excluded`.

Together with the five deferred direct actions, these nine composed/lifecycle exclusions account for every post-change `GameState` method reported outside the arena registry.

## Tier Membership

Every absence from `minimal` is intentional because the historical tier is frozen for artifact comparability. Every absence from `standard` is `listed-for-later`; `full` contains the complete registry.

| tool | minimal | minimal disposition | standard | standard disposition | full |
|---|---:|---|---:|---|---:|
| `get_overview` | yes | present | yes | present | yes |
| `get_units` | yes | present | yes | present | yes |
| `get_cities` | yes | present | yes | present | yes |
| `move_unit` | yes | present | yes | present | yes |
| `found_city` | yes | present | yes | present | yes |
| `set_city_production` | yes | present | yes | present | yes |
| `set_research` | yes | present | yes | present | yes |
| `fortify_unit` | yes | present | yes | present | yes |
| `skip_unit` | yes | present | yes | present | yes |
| `get_map_area` | no | intentionally-excluded | yes | present | yes |
| `get_tech_civics` | no | intentionally-excluded | yes | present | yes |
| `attack_unit` | no | intentionally-excluded | yes | present | yes |
| `improve_tile` | no | intentionally-excluded | yes | present | yes |
| `remove_feature` | no | intentionally-excluded | yes | present | yes |
| `repair_improvement` | no | intentionally-excluded | yes | present | yes |
| `purchase_item` | no | intentionally-excluded | yes | present | yes |
| `heal_unit` | no | intentionally-excluded | yes | present | yes |
| `alert_unit` | no | intentionally-excluded | yes | present | yes |
| `set_civic` | no | intentionally-excluded | yes | present | yes |
| `get_settle_advisor` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_district_advisor` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_wonder_advisor` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_builder_tasks` | no | intentionally-excluded | yes | present | yes |
| `get_diplomacy` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_pending_diplomacy` | yes | present | yes | present | yes |
| `get_pending_trades` | yes | present | yes | present | yes |
| `get_trade_options` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_city_states` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_great_people` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_empire_resources` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_victory_progress` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_pathing_estimate` | no | intentionally-excluded | no | listed-for-later | yes |
| `send_envoy` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_policies` | no | intentionally-excluded | no | listed-for-later | yes |
| `set_policies` | no | intentionally-excluded | no | listed-for-later | yes |
| `appoint_governor` | no | intentionally-excluded | no | listed-for-later | yes |
| `assign_governor` | no | intentionally-excluded | no | listed-for-later | yes |
| `choose_pantheon` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_pantheon_status` | no | intentionally-excluded | no | listed-for-later | yes |
| `upgrade_unit` | no | intentionally-excluded | no | listed-for-later | yes |
| `promote_unit` | yes | present | yes | present | yes |
| `get_unit_promotions` | yes | present | yes | present | yes |
| `automate_explore` | no | intentionally-excluded | no | listed-for-later | yes |
| `skip_remaining_units` | no | intentionally-excluded | no | listed-for-later | yes |
| `purchase_tile` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_purchasable_tiles` | no | intentionally-excluded | no | listed-for-later | yes |
| `set_city_focus` | no | intentionally-excluded | no | listed-for-later | yes |
| `respond_to_diplomacy` | yes | present | yes | present | yes |
| `respond_to_trade` | yes | present | yes | present | yes |
| `propose_trade` | no | intentionally-excluded | no | listed-for-later | yes |
| `propose_peace` | no | intentionally-excluded | no | listed-for-later | yes |
| `send_diplomatic_action` | no | intentionally-excluded | no | listed-for-later | yes |
| `form_alliance` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_city_production` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_global_settle_advisor` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_governors` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_dedications` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_religion_beliefs` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_religion_spread` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_trade_routes` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_trade_destinations` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_gp_advisor` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_world_congress` | no | intentionally-excluded | no | listed-for-later | yes |
| `promote_governor` | no | intentionally-excluded | no | listed-for-later | yes |
| `choose_dedication` | no | intentionally-excluded | no | listed-for-later | yes |
| `found_religion` | no | intentionally-excluded | no | listed-for-later | yes |
| `recruit_great_person` | no | intentionally-excluded | no | listed-for-later | yes |
| `patronize_great_person` | no | intentionally-excluded | no | listed-for-later | yes |
| `reject_great_person` | no | intentionally-excluded | no | listed-for-later | yes |
| `start_trade_route` | no | intentionally-excluded | no | listed-for-later | yes |
| `teleport_trader` | no | intentionally-excluded | no | listed-for-later | yes |
| `queue_wc_votes` | no | intentionally-excluded | no | listed-for-later | yes |
| `city_attack` | no | intentionally-excluded | no | listed-for-later | yes |
| `resolve_city_capture` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_spies` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_strategic_map` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_notifications` | no | intentionally-excluded | no | listed-for-later | yes |
| `spy_action` | no | intentionally-excluded | no | listed-for-later | yes |
| `change_government` | no | intentionally-excluded | no | listed-for-later | yes |
| `spread_religion` | no | intentionally-excluded | no | listed-for-later | yes |
| `activate_great_person` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_gossip` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_loyalty` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_climate` | no | intentionally-excluded | no | listed-for-later | yes |
| `get_great_works` | no | intentionally-excluded | no | listed-for-later | yes |
| `move_great_work` | no | intentionally-excluded | no | listed-for-later | yes |
| `form_corps` | no | intentionally-excluded | no | listed-for-later | yes |
| `form_army` | no | intentionally-excluded | no | listed-for-later | yes |
| `rebase_unit` | no | intentionally-excluded | no | listed-for-later | yes |
| `excavate_artifact` | no | intentionally-excluded | no | listed-for-later | yes |

## Decision Record

Routine repair belongs in `standard` because it restores existing empire infrastructure without destroying player state. `remove_improvement` and `delete` remain deferred because they are destructive. `sacrifice_charges` and `build_route` remain deferred because they are specialized actions with material or strategic costs. Passive `sleep` remains deferred because the standard tier already provides explicit active orders and does not need a second passive hold behavior.
