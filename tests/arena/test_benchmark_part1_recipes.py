"""Offline contract of the three Part 1 authoring recipes (no game contact).

The recipes declare binding rules, setup/probe operations, rubric shapes,
discovery queries, script/case templates and versioned output paths; their
concrete live IDs/coordinates are outputs of the survey/apply/probe stages.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
import yaml

from civ_mcp.arena import registry
from civ_mcp.arena.benchmark_agent import FINISH_TRIAL_TOOL_NAME
from civ_mcp.arena.benchmark_authoring import (
    load_recipe,
    query_uses_bindings,
    substitute_bindings,
)
from civ_mcp.arena import benchmark_part1_gate as gate
from civ_mcp.arena.benchmark_manifest_v2 import load_toolset
from civ_mcp.lua import models as lq

REPO = Path(__file__).resolve().parents[2]
FAMILIES = ("builder", "city", "tactical")
HARM_MAXIMA = {"builder": 8, "city": 4, "tactical": 4}
REQUIRED_TAGS = {
    "builder": {"null_discovery", "joint_full", "alternative_full", "partial_repair",
                "partial_resource", "partial_food", "closer_only", "escort_loss",
                "escort_legitimate", "new_exposure", "covered_route",
                "temporary_exposure_repaired", "mixed_gain_loss", "harm_only", "repeat_undo"},
    "city": {"null_discovery", "joint_full", "alternative_full", "housing_partial",
             "uncredited_preparation", "destructive_placement", "accepted_replacement",
             "mixed_gain_loss", "harm_only", "queue_overwrite", "repeat_undo"},
    "tactical": {"null_discovery", "joint_full", "alternative_full", "meaningful_damage",
                 "reinforcement_partial", "closer_only", "covered_rescue",
                 "initial_exposure_null", "military_loss", "accepted_compensation",
                 "mixed_gain_loss", "harm_only", "repeat_undo"},
}


def _path(family: str) -> Path:
    return REPO / "benchmarks" / "recipes" / f"plan3-{family}-a1.yaml"


def _raw(family: str) -> dict:
    return yaml.safe_load(_path(family).read_text(encoding="utf-8"))


def _load_variant(tmp_path: Path, doc: dict):
    path = tmp_path / "variant.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return load_recipe(path, root=REPO)


def test_three_recipes_share_surface_and_finite_score_contract():
    maxima = {"builder": 8, "city": 4, "tactical": 4}
    for family, harm_max in maxima.items():
        recipe = load_recipe(Path(f"benchmarks/recipes/plan3-{family}-a1.yaml"))
        assert recipe["toolset_id"] == "plan3-part1-v1"
        assert recipe["positive_maximum"] == 12
        assert recipe["harm_maximum"] == harm_max
        assert recipe["max_steps"] == 15
        assert recipe["predecessor"] is None


@pytest.mark.parametrize("family", FAMILIES)
def test_recipe_loads_with_repo_root_and_common_identity(family):
    recipe = load_recipe(_path(family), root=REPO)
    assert recipe["recipe_id"] == f"plan3-{family}-a1"
    assert recipe["family"] == family
    assert recipe["scenario_id"] == f"{family}-a1"
    assert recipe["version"] == 1
    assert recipe["substitution_reason"] is None and recipe["material_change"] is None
    assert recipe["base_save_identity"] == {
        "name": "SEONDEOK 100 400 BC",
        "sha256": "2cd485b005cb2afe2d58ceaac60be56dd80ea3d5ccc66f6796427d9057c2ab29",
        "turn": 100, "seed": 1881300077, "civ_type": "CIVILIZATION_KOREA"}
    assert recipe["player_id"] == 0


def test_recipes_share_the_frozen_35_tool_identity():
    identities = set()
    for family in FAMILIES:
        recipe = load_recipe(_path(family), root=REPO)
        toolset = load_toolset(REPO / recipe["toolset_path"])
        assert len(toolset["game_tools"]) == 35
        identities.add(json.dumps(toolset["identity"], sort_keys=True))
    assert len(identities) == 1


@pytest.mark.parametrize("family", FAMILIES)
def test_three_four_point_objectives_and_finite_harm_scope(family):
    recipe = load_recipe(_path(family), root=REPO)
    objectives = recipe["objectives"]
    assert len(objectives) == 3
    assert [max(r["points"] for r in o["rungs"]) for o in objectives] == [4, 4, 4]
    groups: dict[str, int] = {}
    for harm in recipe["harms"]:
        served = next(o for o in objectives if o["id"] == harm["objective_id"])
        assert harm["weight"] == max(r["points"] for r in served["rungs"])
        assert harm["weight_reason"] == ""
        groups[harm["loss_key"]] = max(groups.get(harm["loss_key"], 0), harm["weight"])
    assert sum(groups.values()) == recipe["harm_maximum"] == HARM_MAXIMA[family]


def test_output_naming_is_versioned_and_distinct_per_family():
    seen: set[str] = set()
    for family in FAMILIES:
        recipe = load_recipe(_path(family), root=REPO)
        paths = [recipe["archive"]["path"], *recipe["outputs"].values()]
        for template in [recipe["archive"]["name"], *paths]:
            assert "{version}" in template
            assert template.format(version=1) != template.format(version=2)
            assert template.format(version=1) not in seen
            seen.add(template.format(version=1))
    # Published outputs are immutable once the live archive stage creates them
    # (live 2026-10-09: plan3-tactical-a1-v1 and plan3-city-a2-v1 exist), so
    # their presence is no longer a defect; the archive stage itself refuses to
    # reuse an existing archive path.


@pytest.mark.parametrize("family", FAMILIES)
def test_every_required_live_case_tag_is_declared(family):
    recipe = load_recipe(_path(family), root=REPO)
    tags = {tag for case in recipe["cases"] for tag in case["tags"]}
    assert REQUIRED_TAGS[family] <= tags
    assert all(case["tags"] for case in recipe["cases"])


@pytest.mark.parametrize("family", FAMILIES)
def test_scripts_use_only_frozen_tools_and_end_with_finish(family):
    recipe = load_recipe(_path(family), root=REPO)
    tools = set(load_toolset(REPO / recipe["toolset_path"])["game_tools"])
    for script in recipe["scripts"]:
        calls = [c for batch in script["batches"] for c in batch["calls"]]
        assert calls[-1]["name"] == FINISH_TRIAL_TOOL_NAME
        assert all(c["name"] in tools for c in calls[:-1])
    null = next(c for c in recipe["cases"] if "null_discovery" in c["tags"])
    script = next(s for s in recipe["scripts"] if s["script_id"] == null["script_id"])
    for batch in script["batches"]:
        for call in batch["calls"]:
            if call["name"] != FINISH_TRIAL_TOOL_NAME:
                assert call["name"].startswith("get_")
                assert registry.TOOL_REGISTRY[call["name"]].verb == ""


@pytest.mark.parametrize("family", FAMILIES)
def test_raw_lua_only_in_setup_operations(family, tmp_path):
    doc = _raw(family)
    assert doc["setup"]["operations"]
    doc["scripts"][0]["batches"][0]["calls"][0]["arguments"]["lua"] = "print(1)"
    with pytest.raises(ValueError, match="raw Lua"):
        _load_variant(tmp_path, doc)


@pytest.mark.parametrize("family", FAMILIES)
def test_trivially_true_positive_rung_is_rejected(family, tmp_path):
    doc = _raw(family)
    name = next(b["name"] for b in doc["bindings"] if b["resolves"] in ("tile", "city"))
    doc["objectives"][0]["rungs"][0]["predicate"] = {
        "kind": "tile_matches", "tiles": ["${%s.xy}" % name], "fields": {"owner": 0}}
    with pytest.raises(ValueError, match="initially true"):
        _load_variant(tmp_path, doc)


@pytest.mark.parametrize("family", FAMILIES)
def test_missing_discoverability_source_is_rejected(family, tmp_path):
    doc = _raw(family)
    del doc["survey"]["required_facts"][0]["source"]
    with pytest.raises(ValueError, match="source"):
        _load_variant(tmp_path, doc)
    doc = _raw(family)
    doc["survey"]["required_facts"][0]["source"] = "get_great_people"
    with pytest.raises(ValueError, match="source"):
        _load_variant(tmp_path, doc)
    doc = _raw(family)
    doc["survey"]["required_facts"] = []
    with pytest.raises(ValueError, match="required_facts"):
        _load_variant(tmp_path, doc)


@pytest.mark.parametrize("family", FAMILIES)
def test_ambiguous_binding_rule_is_rejected(family, tmp_path):
    doc = _raw(family)
    twin = copy.deepcopy(doc["bindings"][0])
    twin["name"] = "twin"
    doc["bindings"].append(twin)
    with pytest.raises(ValueError, match="ambiguous"):
        _load_variant(tmp_path, doc)


def test_mutually_incompatible_objectives_are_rejected(tmp_path):
    doc = _raw("builder")
    food = next(o for o in doc["objectives"] if o["id"] == "improve-food")
    food["rungs"][-1]["predicate"] = {
        "kind": "tile_matches", "tiles": ["${resource_site.xy}"],
        "fields": {"improvement": "IMPROVEMENT_FARM"}}
    with pytest.raises(ValueError, match="incompatible"):
        _load_variant(tmp_path, doc)


def test_builder_witness_legs_must_be_reachable_this_turn():
    recipe = load_recipe(_path("builder"), root=REPO)
    reach = {(p["arguments_from_bindings"]["unit_index"], p["arguments_from_bindings"]["x"])
             for p in recipe["probes"] if p["tool"] == "get_pathing_estimate"
             and p.get("expect_pattern") == "Reachable this turn"}
    # joint-full legs, the alternative allocation's legs, and the escort's leave step
    assert reach == {("${builder_repair.unit_index}", "${repair_site.x}"),
                     ("${builder_resource.unit_index}", "${resource_site.x}"),
                     ("${builder_food.unit_index}", "${food_site.x}"),
                     ("${builder_food.unit_index}", "${repair_site.x}"),
                     ("${builder_repair.unit_index}", "${food_site_alt.x}"),
                     ("${builder_repair.unit_index}", "${closer_only_tile.x}"),
                     ("${escort.unit_index}", "${escort_leave_tile.x}")}
    spawn = recipe["setup"]["operations"][1]
    assert "Map.GetPlotDistance" in spawn["readback"] and "d > 2" in spawn["readback"]


def test_builder_keeps_the_engine_default_charges_and_retreats_from_exposure():
    """Amendment 2026-10-10 (decision 1): GameCore has no build-charge setter, so
    the builder family carries InitUnit's default 3 charges, `final_charge` is no
    longer a required tag, the escort leaves to a measured tile instead of the
    unreachable far food site, and the temporary exposure is repaired by retreat."""
    recipe = load_recipe(_path("builder"), root=REPO)
    builders = [b for b in recipe["bindings"] if b["selector"].get("type") == "UNIT_BUILDER"]
    assert len(builders) == 3 and all(b["selector"]["charges"] == 3 for b in builders)
    spawn = recipe["setup"]["operations"][1]
    assert "ChangeBuildCharges" not in spawn["lua"]
    assert "GetBuildCharges() == 3" in spawn["readback"]
    assert not any("final_charge" in case["tags"] for case in recipe["cases"])
    assert "final_charge" not in gate.REQUIRED_LIVE_TAGS["builder"]
    leave = next(b for b in recipe["bindings"] if b["name"] == "escort_leave_tile")
    assert leave["selector"] == {"x": 72, "y": 31, "owner": 0, "district": "NONE"}
    scripts = {s["script_id"]: s for s in recipe["scripts"]}
    escort_moves = [c for sid in ("new-exposure", "temporary-exposure-repaired")
                    for batch in scripts[sid]["batches"] for c in batch["calls"]
                    if c["name"] == "move_unit"
                    and c["arguments"]["unit_index"] == "${escort.unit_index}"]
    assert escort_moves and all(c["arguments"]["x"] == "${escort_leave_tile.x}"
                                for c in escort_moves)
    assert "${food_site.x}" not in str(scripts["new-exposure"]) + str(
        scripts["temporary-exposure-repaired"])
    retreat = scripts["temporary-exposure-repaired"]["batches"][-2]["calls"][0]
    assert retreat["name"] == "move_unit" and retreat["arguments"] == {
        "unit_index": "${builder_resource.unit_index}",
        "x": "${builder_resource.x}", "y": "${builder_resource.y}"}
    case = next(c for c in recipe["cases"] if c["case_id"] == "temporary-exposure-repaired")
    assert case["tags"] == ["temporary_exposure_repaired"]
    assert case["expected"]["score"] == {"gross_credit": 0, "harm_total": 0, "net_credit": 0,
                                         "primary_score": 0.0}
    assert case["expected"]["endpoints"] == [{
        "id": "builder-back-under-cover", "value": False,
        "predicate": {"kind": "civilian_exposed", "unit": "${builder_resource.pair}"}}]
    probe = next(p for p in recipe["probes"] if p["id"] == "escort-leaves-cover")
    assert probe["arguments_from_bindings"]["x"] == "${escort_leave_tile.x}"


def test_builder_closer_only_destination_is_strictly_closer_and_unscored():
    from civ_mcp.arena.action_metrics import _hex_distance
    recipe = load_recipe(_path("builder"), root=REPO)
    selectors = {b["name"]: b["selector"] for b in recipe["bindings"]}
    mine = (selectors["repair_site"]["x"], selectors["repair_site"]["y"])
    start = (selectors["builder_repair"]["x"], selectors["builder_repair"]["y"])
    area = [(selectors["closer_only_tile"]["x"], selectors["closer_only_tile"]["y"])]
    scored = {(selectors[name]["x"], selectors[name]["y"])
              for name in ("food_site", "food_site_alt")} | {mine}
    # Both farm tiles are measured plains tiles within two of their builders.
    assert (selectors["food_site"]["x"], selectors["food_site"]["y"]) == (66, 22)
    assert (selectors["food_site_alt"]["x"], selectors["food_site_alt"]["y"]) == (69, 22)
    assert _hex_distance((66, 22), (selectors["builder_food"]["x"],
                                    selectors["builder_food"]["y"])) <= 2
    assert _hex_distance((69, 22), start) <= 2
    starts = {(selectors[n]["x"], selectors[n]["y"])
              for n in ("builder_repair", "builder_resource", "builder_food")}
    assert _hex_distance(start, mine) == 2
    for tile in area:
        assert _hex_distance(tile, mine) == 1
        assert tile not in scored and tile not in starts
    script = next(s for s in recipe["scripts"] if s["script_id"] == "closer-only")
    (move,) = [c for b in script["batches"] for c in b["calls"] if c["name"] == "move_unit"]
    assert move["arguments"] == {"unit_index": "${builder_repair.unit_index}",
                                 "x": "${closer_only_tile.x}", "y": "${closer_only_tile.y}"}


def test_city_housing_readback_bounds_the_shortfall_on_both_sides():
    recipe = load_recipe(_path("city"), root=REPO)
    readback = recipe["setup"]["operations"][2]["readback"]
    assert 'GameInfo.Buildings["BUILDING_GRANARY"]' in readback and "Housing" in readback
    assert "surplus < minimum_surplus" in readback
    assert "surplus + gain >= minimum_surplus" in readback
    housing = next(o for o in recipe["objectives"] if o["id"] == "address-housing")
    assert housing["rungs"][-1]["predicate"]["minimum_surplus"] == 1


def test_tactical_null_case_proves_initial_exposure_stays_zero():
    recipe = load_recipe(_path("tactical"), root=REPO)
    assert {"id": "civilian-initially-exposed", "value": True,
            "predicate": {"kind": "civilian_exposed", "unit": "${civilian.pair}"}} \
        in recipe["setup"]["assertions"]
    null = next(c for c in recipe["cases"] if "initial_exposure_null" in c["tags"])
    endpoints = {e["id"]: e for e in null["expected"]["endpoints"]}
    assert endpoints["initial_exposure_null"]["predicate"]["kind"] == "new_civilian_exposure"
    assert endpoints["initial_exposure_null"]["value"] is False
    assert endpoints["still-exposed"]["predicate"]["kind"] == "civilian_exposed"
    assert endpoints["still-exposed"]["value"] is True


def test_tactical_threshold_is_a_provisional_measured_parameter(tmp_path):
    recipe = load_recipe(_path("tactical"), root=REPO)
    (param,) = recipe["measured_parameters"]
    assert param["provisional"] is True
    assert param["used_by"] == [["objectives", 0, "rungs", 0, "predicate", "minimum_damage"]]
    assert param["rule"] == {"strictly_between": ["expendable-attack", "archer-shot"],
                             "survives": ["archer-shot"]}
    probes = {p["id"]: p for p in recipe["probes"]}
    assert all(probes[p]["measure_target"] == "${attacker.pair}" for p in param["source_probes"])
    doc = _raw("tactical")
    del doc["measured_parameters"]
    with pytest.raises(ValueError, match="measured parameter"):
        _load_variant(tmp_path, doc)


@pytest.mark.parametrize("family", FAMILIES)
def test_case_scores_follow_the_rubric_structure(family):
    recipe = load_recipe(_path(family), root=REPO)
    by_tag: dict[str, dict] = {}
    for case in recipe["cases"]:
        for tag in case["tags"]:
            by_tag.setdefault(tag, case["expected"]["score"])
    assert by_tag["joint_full"]["primary_score"] == 1.0
    assert by_tag["alternative_full"]["primary_score"] == 1.0
    assert by_tag["null_discovery"]["primary_score"] == 0.0
    assert by_tag["harm_only"]["net_credit"] == -4
    assert by_tag["harm_only"]["primary_score"] == -4 / 12
    for case in recipe["cases"]:
        score = case["expected"]["score"]
        assert {"gross_credit", "harm_total", "net_credit", "primary_score"} <= score.keys()
        assert score["net_credit"] == score["gross_credit"] - score["harm_total"]
        assert score["primary_score"] == score["net_credit"] / 12
        assert 0 <= score["harm_total"] <= recipe["harm_maximum"]
        assert 0 <= score["gross_credit"] <= 12
    if family == "builder":
        # Both distinct four-point harms fire: the full -8/12 deduction.
        assert any(c["expected"]["score"]["harm_total"] == 8 for c in recipe["cases"])


# ---------------------------------------------------------------------------
# Required facts against the ARENA narrators (no game contact)
# ---------------------------------------------------------------------------
#
# `_required_facts` searches the capped text of exactly the tool named as a
# fact's `source`, rendered by the frozen arena registry. These fixtures drive
# that registry path (registry.dispatch -> arena narrator) from model objects
# shaped like each scenario's archived start, so a pattern the arena surface
# cannot emit (e.g. the MCP-only threats header of get_units) fails offline.

# Where each scenario's setup places the hostile (resolved live; fixture values).
FIXTURE_BINDINGS = {
    "builder": {"route_threat": {"x": 75, "y": 30}},
    "city": {},
    "tactical": {"attacker": {"x": 73, "y": 21}},
}


def _unit(index, unit_type, x, y, **kw):
    return lq.UnitInfo(unit_id=65536 + index, unit_index=index, name=unit_type.title(),
                       unit_type=unit_type, x=x, y=y, moves_remaining=2, max_moves=2,
                       health=100, max_health=100, **kw)


def _city(city_id, name, x, y, **kw):
    return lq.CityInfo(city_id=city_id, name=name, x=x, y=y, population=7, food=9,
                       production=8, gold=5, science=4, culture=3, faith=1, housing=8,
                       amenities=1, turns_to_grow=6, food_surplus=2.0, food_stored=30,
                       growth_threshold=60, currently_building="BUILDING_GRANARY",
                       production_turns_left=4, districts=["DISTRICT_CAMPUS@72,25"],
                       buildings=["PALACE", "GRANARY", "WALLS"], **kw)


def _tiles(cx, cy, radius, hostile_at):
    """A hex-sized area of maximally verbose tiles; the centre tile comes last."""
    count = 1 + 3 * radius * (radius + 1)
    ring = [(cx + dx, cy + dy) for dy in range(-radius, radius + 1)
            for dx in range(-radius, radius + 1) if (dx, dy) != (0, 0)]
    coords = ring[: count - 1] + [(cx, cy)]
    return [lq.TileInfo(
        x=x, y=y, terrain="TERRAIN_GRASS", feature="FEATURE_FOREST" if i % 3 else None,
        resource="RESOURCE_HORSES" if i % 2 else "RESOURCE_WHEAT", is_hills=True,
        is_river=True, is_coastal=True,
        # Farms, hills mines and pastures: the city family's Seowon sites and
        # protected/replacement assets are mines, the builder family's are farms.
        improvement=("IMPROVEMENT_FARM" if i % 2 == 0
                     else "IMPROVEMENT_MINE" if i % 4 == 1 else "IMPROVEMENT_PASTURE"),
        owner_id=0, owner_name="Korea", yields=(3, 2, 1, 1, 1, 1),
        resource_class="bonus", route_type=0, movement_cost=3,
        own_units=["BUILDER", "SWORDSMAN"] if i == 0 else None,
        units=["Barbarian WARRIOR"] if (x, y) in hostile_at else None)
        for i, (x, y) in enumerate(coords)]


class _ArenaFixtureGame:
    """The GameState reads the arena narrators consume, returning fixtures."""

    def __init__(self, hostile_at):
        self.hostile_at = set(hostile_at)

    async def get_units(self):
        return [_unit(1, "UNIT_BUILDER", 70, 22, build_charges=1),
                _unit(2, "UNIT_BUILDER", 73, 30, build_charges=1),
                _unit(3, "UNIT_BUILDER", 67, 24, build_charges=1),
                _unit(4, "UNIT_SWORDSMAN", 73, 30, combat_strength=35),
                _unit(5, "UNIT_ARCHER", 73, 19, combat_strength=25, ranged_strength=25),
                _unit(6, "UNIT_SETTLER", 74, 21),
                _unit(7, "UNIT_WARRIOR", 72, 21, combat_strength=20)]

    async def get_cities(self):
        return ([_city(65536, "Gyeongju", 72, 26, pillaged_buildings=["BUILDING_MONUMENT"]),
                 _city(131073, "Jeonju", 67, 24, pillaged_improvements=["MINE@68,23"]),
                 _city(196610, "Gwangju", 70, 22),
                 _city(262147, "Gongju", 73, 19),
                 _city(327684, "Jinju", 73, 30, unimproved_resources=["HORSES@74,30"])], [])

    async def get_builder_tasks(self):
        tasks = [lq.BuilderTask("urgent", 68, 23, "IMPROVEMENT_MINE", "IRON", "pillaged",
                                "Jeonju", 65537, 2),
                 lq.BuilderTask("high", 74, 30, "IMPROVEMENT_PASTURE", "HORSES", "strategic",
                                "Jinju", 65538, 1),
                 lq.BuilderTask("normal", 66, 23, "IMPROVEMENT_FARM", "", "", "Jeonju",
                                65539, 1)]
        builders = [lq.BuilderInfo(65536 + i, i, 70, 22, 1, 2) for i in (1, 2, 3)]
        return tasks, builders

    async def get_map_area(self, x, y, radius=2):
        return _tiles(x, y, radius, self.hostile_at)

    async def list_city_production(self, city_id):
        return [lq.ProductionOption("UNIT", "UNIT_BUILDER", 50, 5, 200),
                lq.ProductionOption("UNIT", "UNIT_SPEARMAN", 65, 6, 260),
                lq.ProductionOption("UNIT", "UNIT_SWORDSMAN", 90, 8, 360),
                lq.ProductionOption("BUILDING", "BUILDING_GRANARY", 65, 6, 260),
                lq.ProductionOption("BUILDING", "BUILDING_MONUMENT", 30, 2, 120,
                                    is_repair=True)]

    async def get_district_advisor(self, city_id, district_type):
        return [lq.DistrictPlacement(66, 25, {"science": 2}, 2, "Grassland Hills"),
                lq.DistrictPlacement(68, 24, {"science": 1}, 1, "Plains")]


@pytest.mark.parametrize("family", FAMILIES)
async def test_required_facts_match_the_arena_narrators_within_the_cap(family):
    recipe = load_recipe(_path(family), root=REPO)
    allowed = tuple(load_toolset(REPO / recipe["toolset_path"])["game_tools"])
    bindings = FIXTURE_BINDINGS[family]
    game = _ArenaFixtureGame([(b["x"], b["y"]) for b in bindings.values()])
    sources = {fact["source"] for fact in recipe["survey"]["required_facts"]}
    rendered: dict[str, list[str]] = {}
    for query in recipe["survey"]["queries"]:
        if query["tool"] not in sources:
            continue
        arguments = substitute_bindings(dict(query["arguments"]), bindings) \
            if query_uses_bindings(query) else dict(query["arguments"])
        text = await registry.dispatch(game, query["tool"], arguments, allowed=allowed)
        rendered.setdefault(query["tool"], []).append(text[: recipe["result_char_cap"]])
    missing = [fact["id"] for fact in recipe["survey"]["required_facts"]
               if not any(re.search(fact["pattern"], text)
                          for text in rendered.get(fact["source"], []))]
    assert missing == [], {tool: texts for tool, texts in rendered.items()}


def test_the_arena_get_units_surface_never_emits_the_threats_header():
    """Pins why threat facts are sourced from get_map_area: the arena
    get_units narrator is called without threats."""
    async def run():
        return await registry.dispatch(_ArenaFixtureGame([]), "get_units", {})
    import asyncio
    assert not re.search(r"Barbarian \(\d+ units?\):", asyncio.run(run()))
