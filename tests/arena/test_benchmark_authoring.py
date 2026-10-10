"""Replayable authoring stage machine: recipes, stage order, evidence closure.

Every live operation is a fake bundled in `LiveOps`; registry dispatch is
real (`registry.dispatch -> GameState -> tool.call`) with the tool callables
replaced by a recording fake world. No game, network or Windows call.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from civ_mcp.arena import benchmark_authoring as authoring
from civ_mcp.arena import registry
from civ_mcp.arena.benchmark_authoring import (
    LiveOps,
    evidence_files,
    load_recipe,
    preflight,
    run_authoring_stage,
    stage_status,
    validate_stage_transition,
)
from civ_mcp.arena.benchmark_state_v2 import digest_state_v2
from civ_mcp.game_launcher import WSL_WINDOWS_REPO

from .benchmark_v2_fixtures import state_v2

REPO = Path(__file__).resolve().parents[2]
BASE_SHA = "2cd485b005cb2afe2d58ceaac60be56dd80ea3d5ccc66f6796427d9057c2ab29"
BASE_NAME = "SEONDEOK 100 400 BC"


def test_archive_requires_legality_probe_evidence():
    with pytest.raises(ValueError, match="probe"):
        validate_stage_transition("archive", {"survey": "passed", "apply": "passed"})


def test_every_stage_requires_its_predecessor_passed():
    order = ["survey", "apply", "probe", "archive", "capture", "verify", "menu-check",
             "validate", "finish"]
    validate_stage_transition("survey", {})
    for previous, stage in zip(order, order[1:]):
        with pytest.raises(ValueError, match=previous):
            validate_stage_transition(stage, {previous: "failed"})
        validate_stage_transition(stage, {previous: "passed"})


# ---------------------------------------------------------------------------
# Recipe fixture
# ---------------------------------------------------------------------------

def _objective(oid: str) -> dict:
    return {"id": oid, "rungs": [{"points": 4, "predicate": {
        "kind": "unit_in_area", "unit_types": ["UNIT_BUILDER"], "tiles": ["${site.xy}"]}}]}


RECIPE = {
    "schema_version": "2.0.0",
    "recipe_id": "test-builder-a1",
    "family": "builder",
    "scenario_id": "builder-a1",
    "version": 1,
    "predecessor": None,
    "substitution_reason": None,
    "material_change": None,
    "toolset_id": "test-tools",
    "toolset_path": "benchmarks/toolsets/test-tools.yaml",
    "max_steps": 15,
    "positive_maximum": 12,
    "harm_maximum": 4,
    "base_save_identity": {"name": BASE_NAME, "sha256": BASE_SHA, "turn": 100, "seed": 7,
                           "civ_type": "CIVILIZATION_KOREA"},
    "player_id": 0,
    "result_char_cap": 40,
    "setup": {
        "operations": [{"kind": "lua", "lua": "PLACE_BUILDER", "readback": "READ_BUILDER"}],
        "assertions": [{"id": "builder-placed", "value": True, "predicate": {
            "kind": "unit_in_area", "unit_types": ["UNIT_BUILDER"], "tiles": ["${builder.xy}"]}}],
    },
    "survey": {
        "queries": [{"tool": "get_units", "arguments": {}}],
        "required_facts": [{"id": "builder-visible", "pattern": "UNIT_BUILDER",
                            "source": "get_units"}],
    },
    "bindings": [
        {"name": "builder", "selector": {"owner": 0, "type": "UNIT_BUILDER"}, "resolves": "unit"},
        {"name": "site", "selector": {"x": 12, "y": 10}, "resolves": "tile"},
    ],
    "coverage_rule": {"include_owned_tiles": False, "area_radius": 1,
                      "tracked_target_bindings": []},
    "objectives": [_objective("o1"), _objective("o2"), _objective("o3")],
    "harms": [{"id": "h1", "loss_key": "builder", "objective_id": "o1", "weight": 4,
               "weight_reason": "", "priority": 0, "timing": "event",
               "predicate": {"kind": "unit_lost", "unit": "${builder.pair}"},
               "compensation": []}],
    "probes": [
        {"id": "improve-site", "tool": "improve_tile",
         "arguments_from_bindings": {"unit_index": "${builder.unit_index}",
                                     "improvement_name": "IMPROVEMENT_FARM"},
         "expect": "ok", "restore": True},
        {"id": "fortify-civilian", "tool": "fortify_unit",
         "arguments_from_bindings": {"unit_index": "${builder.unit_index}"},
         "expect": "rejected", "restore": True},
    ],
    "archive": {"name": "TEST_BUILDER_A1_V{version}",
                "path": "benchmarks/saves/test-builder-a1-v{version}.Civ6Save"},
    "outputs": {
        "position": "benchmarks/positions/test-builder-a1-v{version}.yaml",
        "provenance_authoring": "benchmarks/provenance/test-builder-a1-v{version}-authoring.json",
        "provenance_packet": "benchmarks/provenance/test-builder-a1-v{version}.json",
        "scripts_dir": "benchmarks/scripts/test-builder-a1-v{version}",
        "validation_dir": "benchmarks/validation/test-builder-a1-v{version}",
    },
    "scripts": [{"script_id": "observe", "batches": [
        {"calls": [{"name": "get_units", "arguments": {}}]},
        {"calls": [{"name": "finish_trial", "arguments": {}}]}]}],
    "cases": [{"case_id": "null-observe", "script_id": "observe", "tags": ["null"],
               "expected": {"score": {"primary_score": 0}, "endpoints": [], "ledger": []}}],
}

TOOLS = ["get_units", "improve_tile", "fortify_unit"]


def write_recipe(root: Path, recipe: dict | None = None, *, name: str = "test-builder-a1") -> Path:
    toolset = root / "benchmarks" / "toolsets" / "test-tools.yaml"
    toolset.parent.mkdir(parents=True, exist_ok=True)
    toolset.write_text(yaml.safe_dump({"toolset_id": "test-tools", "game_tools": TOOLS}))
    path = root / "benchmarks" / "recipes" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(recipe if recipe is not None else RECIPE, sort_keys=False))
    return path


def _recipe(**changes) -> dict:
    doc = copy.deepcopy(RECIPE)
    doc.update(changes)
    return doc


def test_recipe_loads_and_validates(tmp_path):
    recipe = load_recipe(write_recipe(tmp_path), root=tmp_path)
    assert recipe["scenario_id"] == "builder-a1"
    assert recipe["predecessor"] is None


def test_recipe_rejects_unknown_key(tmp_path):
    with pytest.raises(ValueError, match="unexpected keys"):
        load_recipe(write_recipe(tmp_path, _recipe(surprise=1)), root=tmp_path)


@pytest.mark.parametrize("where", ["scripts", "probes", "survey"])
def test_recipe_rejects_raw_lua_outside_setup(tmp_path, where):
    doc = _recipe()
    if where == "scripts":
        doc["scripts"][0]["batches"][0]["calls"][0]["arguments"] = {"lua": "Players[0]:Kill()"}
    elif where == "probes":
        doc["probes"][0]["lua"] = "Players[0]:Kill()"
    else:
        doc["survey"]["queries"][0]["arguments"] = {"lua": "print(1)"}
    with pytest.raises(ValueError, match="raw Lua"):
        load_recipe(write_recipe(tmp_path, doc), root=tmp_path)


def test_recipe_substitute_fields_are_all_or_nothing(tmp_path):
    partial = _recipe(predecessor="builder-a1", substitution_reason="camp refused",
                      material_change=None, scenario_id="builder-a2")
    with pytest.raises(ValueError, match="material_change"):
        load_recipe(write_recipe(tmp_path, partial), root=tmp_path)
    complete = _recipe(predecessor="builder-a1", substitution_reason="camp refused",
                       material_change="quarry site", scenario_id="builder-a2")
    assert load_recipe(write_recipe(tmp_path, complete), root=tmp_path)["predecessor"] == "builder-a1"


def test_recipe_rejects_tools_outside_frozen_toolset(tmp_path):
    doc = _recipe()
    doc["probes"][0]["tool"] = "get_cities"
    with pytest.raises(ValueError, match="toolset"):
        load_recipe(write_recipe(tmp_path, doc), root=tmp_path)


def test_recipe_rejects_action_tool_as_survey_query(tmp_path):
    doc = _recipe()
    doc["survey"]["queries"][0]["tool"] = "fortify_unit"
    with pytest.raises(ValueError, match="read-only"):
        load_recipe(write_recipe(tmp_path, doc), root=tmp_path)


def test_recipe_requires_base_identity_fields(tmp_path):
    doc = _recipe()
    del doc["base_save_identity"]["seed"]
    with pytest.raises(ValueError, match="seed"):
        load_recipe(write_recipe(tmp_path, doc), root=tmp_path)


def test_recipe_rejects_unknown_binding_reference(tmp_path):
    doc = _recipe()
    doc["probes"][0]["arguments_from_bindings"]["unit_index"] = "${ghost.unit_index}"
    with pytest.raises(ValueError, match="ghost"):
        load_recipe(write_recipe(tmp_path, doc), root=tmp_path)


def test_recipe_rejects_expected_scores_inside_scripts(tmp_path):
    doc = _recipe()
    doc["scripts"][0]["expected"] = {"score": {"primary_score": 1}}
    with pytest.raises(ValueError, match="unexpected keys"):
        load_recipe(write_recipe(tmp_path, doc), root=tmp_path)


def test_recipe_rejects_wrong_positive_maximum(tmp_path):
    doc = _recipe()
    doc["objectives"] = doc["objectives"][:2]
    with pytest.raises(ValueError, match="positive_maximum"):
        load_recipe(write_recipe(tmp_path, doc), root=tmp_path)


# ---------------------------------------------------------------------------
# Fake live operations
# ---------------------------------------------------------------------------

def _builder(x=10, y=10):
    return dict(owner=0, id=65536, unit_index=1, type="UNIT_BUILDER", role="civilian",
                x=x, y=y, hp=100, max_hp=100, moves=2, charges=3)


def _tile(x, y):
    return dict(x=x, y=y, owner=0, terrain="TERRAIN_GRASS", feature="NONE",
                resource="NONE", improvement="NONE", pillaged=False, district="NONE",
                visible=True, food=2, production=0, gold=0, science=0, culture=0, faith=0)


class FakeConn:
    def __init__(self, ops: FakeOps) -> None:
        self.ops = ops

    async def execute_write(self, lua, timeout=5.0):
        # InGame UI context: never the right place for a setup mutation.
        self.ops.calls.append(("write", lua))
        return list(self.ops.write_lines)

    async def execute_mutation(self, lua, timeout=5.0):
        # GameCore context: the only context whose APIs can mutate the world.
        self.ops.calls.append(("mutation", lua))
        self.ops.setup_applied = True
        return list(self.ops.write_lines)

    async def execute_read(self, lua, timeout=5.0, **_kwargs):
        self.ops.calls.append(("read", lua))
        return list(self.ops.readback_lines)

    async def disconnect(self):
        self.ops.calls.append(("disconnect",))


class FakeOps:
    def __init__(self, root: Path, attempt_dir: Path) -> None:
        self.root = root
        self.attempt_dir = attempt_dir
        self.calls: list[tuple] = []
        self.now = 1_000_000.0
        self.write_lines = ["OK|placed"]
        self.readback_lines = ["OK|builder at 10,10"]
        self.builder_xy = (10, 10)
        self.restore_builder_xy: tuple[int, int] | None = None
        self.reload_verified = True
        self.saves = 0
        self.saved: dict[str, bytes] = {}
        # The base world has no builder; setup introduces it (and the task tile).
        self.setup_applied = False
        self.tool_results = {"get_units": "Units: UNIT_WARRIOR (2) at 9,9",
                             "improve_tile": "Started IMPROVEMENT_FARM",
                             "fortify_unit": "Error: civilians cannot fortify"}
        self.setup_tool_results = {"get_units": "UNIT_BUILDER at 10,10 task 12,10"}
        self.load_result = "Loaded"
        self.restart_result = "Kill: ok | Launch: ok | Load: loaded"
        self.identity: dict = {}
        self.after_restart_builder_xy: tuple[int, int] | None = None
        self.validation_inputs: list[tuple] = []
        self.journal_open_at_connect: list[bool] = []
        self.grid = (100, 60)

    def tool_result(self, name: str) -> str:
        if self.setup_applied and name in self.setup_tool_results:
            return self.setup_tool_results[name]
        return self.tool_results[name]

    # -- clocks
    def wall(self) -> float:
        return self.now

    def mono(self) -> float:
        return self.now

    # -- live ops
    async def connect(self):
        journal = self.attempt_dir / "authoring-journal.json"
        started = journal.is_file() and any(
            s["status"] == "open"
            for f in json.loads(journal.read_text())["families"].values() for s in f["scenarios"])
        self.journal_open_at_connect.append(started)
        self.calls.append(("connect",))
        return FakeConn(self)

    def deploy(self, archive, save_name, sha256):
        self.calls.append(("deploy", archive, save_name, sha256))
        return {"ok": True}

    async def reload(self, conn, save_name):
        self.calls.append(("reload", save_name))
        if self.restore_builder_xy is not None:
            self.builder_xy = self.restore_builder_xy
        return self.reload_verified

    async def dismiss_popups(self, conn):
        return "POPUPS|none"

    def stat_save(self, name):
        self.calls.append(("stat", name))
        data = self.saved.get(name)
        return {"exists": data is not None, "size": len(data) if data else None,
                "mtime_ns": self.saves if data else None}

    def export_save(self, name, destination, expected_sha256=None, *, previous_signature=None):
        self.calls.append(("export", name, destination, expected_sha256, previous_signature))
        # Exports land in the gitignored benchmark_runs tree of the Windows checkout.
        assert destination.startswith(WSL_WINDOWS_REPO + "/benchmark_runs/plan3-part1/")
        sha = BASE_SHA if name == BASE_NAME else hashlib.sha256(self.saved[name]).hexdigest()
        if expected_sha256 is not None:
            assert expected_sha256 == sha
        return {"ok": True, "sha256": sha, "dest_path": destination}

    def publish_archive(self, source_path, destination, *, expected_sha256):
        self.calls.append(("publish", source_path, destination, expected_sha256))
        name = next(n for n, b in self.saved.items()
                    if hashlib.sha256(b).hexdigest() == expected_sha256)
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(self.saved[name])
        return {"sha256": expected_sha256, "dest_path": destination, "existed": False}

    async def save_game(self, conn, name):
        self.calls.append(("save", name))
        self.saves += 1
        self.saved[name] = f"SAVE:{name}:{self.saves}".encode()
        return True, f"Saved: {name}"

    async def load_game_save(self, conn, name):
        self.calls.append(("load", name))
        self.setup_applied = False
        return f"{self.load_result} {name}"

    async def restart_and_load(self, name):
        self.calls.append(("restart_and_load", name))
        if self.after_restart_builder_xy is not None:
            self.builder_xy = self.after_restart_builder_xy
        return self.restart_result

    async def reconnect(self, conn):
        self.calls.append(("reconnect",))

    async def grid_size(self, conn):
        self.calls.append(("grid_size",))
        return self.grid

    async def run_validation(self, suite_path, run_dir):
        self.validation_inputs.append((Path(suite_path), Path(run_dir)))
        self.calls.append(("run_validation",))
        self._write_reports(Path(run_dir))
        (Path(run_dir) / "validation.json").write_text('{"passed": true}')
        return {"suite_id": "s", "passed": True, "errored": False, "cases": []}

    def _write_reports(self, run_dir: Path):
        report = run_dir / "reports" / "null-observe"
        report.mkdir(parents=True, exist_ok=True)
        (report / "report.json").write_text('{"score": 0}')
        (report / "report.md").write_text("# report\n")

    def build_reports(self, run_dir):
        self.calls.append(("build_reports",))
        self._write_reports(Path(run_dir))
        return {}

    def state(self, coverage) -> dict:
        tiles = [_tile(x, y) for x, y in sorted(tuple(p) for p in coverage["area"])]
        return {**state_v2(units=[_builder(*self.builder_xy)], tiles=tiles), **self.identity}

    async def capture_state_v2(self, conn, player_id, coverage, *, io_timing=None):
        self.calls.append(("capture",))
        if io_timing is not None:
            io_timing.update(pre_drain_s=0.0, post_drain_s=0.0, lua_executions=1)
        return self.state(coverage)

    async def capture_position(self, provenance, *, capture_state, digest):
        self.calls.append(("capture_position", provenance["archive"], provenance["game_save_name"]))
        state = await capture_state(FakeConn(self), provenance["player_id"],
                                    provenance["relevant_tiles"])
        return {"provenance": dict(provenance), "deployment": {}, "reload": {"verified": True},
                "popup_hygiene": {"status": "POPUPS|none"}, "captured_state": state,
                "captured_state_sha256": digest(state)}

    async def verify_position(self, position, cycles, *, capture_state, digest):
        self.calls.append(("verify_position", cycles))
        digests = []
        for _ in range(cycles):
            state = await capture_state(FakeConn(self), position.player_id, position.relevant_tiles)
            digests.append(digest(state))
        ok = all(d == position.expected_state_sha256 for d in digests)
        return {"ok": ok, "cycles_completed": cycles, "digests": digests}

    def live_ops(self) -> LiveOps:
        return LiveOps(
            connect=self.connect, deploy=self.deploy, reload=self.reload,
            dismiss_popups=self.dismiss_popups, export_save=self.export_save,
            publish_archive=self.publish_archive, save_game=self.save_game,
            load_game_save=self.load_game_save, run_validation=self.run_validation,
            build_reports=self.build_reports, capture_state_v2=self.capture_state_v2,
            capture_position=self.capture_position, verify_position=self.verify_position,
            restart_and_load=self.restart_and_load, reconnect=self.reconnect,
            stat_save=self.stat_save, clocks=(self.wall, self.mono),
            grid_size=self.grid_size)


@pytest.fixture
def tool_log(monkeypatch):
    """Replace each frozen tool's callable with a recording fake world."""
    log: list[tuple[str, dict, object]] = []
    results: dict[str, str] = {}

    def install(ops: FakeOps):
        results.update(ops.tool_results)
        for name in TOOLS:
            async def call(gs, args, _name=name, **_context):
                log.append((_name, dict(args), gs))
                return ops.tool_result(_name)
            monkeypatch.setitem(registry.TOOL_REGISTRY, name,
                                dataclasses.replace(registry.TOOL_REGISTRY[name], call=call))
        return log
    return install


class Rig:
    def __init__(self, tmp_path: Path, tool_log, recipe: dict | None = None) -> None:
        self.root = tmp_path
        self.attempt = tmp_path / "benchmark_runs" / "plan3-part1" / "builder-a1"
        self.recipe_path = write_recipe(tmp_path, recipe)
        self.ops = FakeOps(tmp_path, self.attempt)
        self.tools = tool_log(self.ops)

    async def run(self, stage: str) -> dict:
        return await run_authoring_stage(self.recipe_path, stage=stage,
                                         attempt_dir=self.attempt, ops=self.ops.live_ops(),
                                         root=self.root)

    async def run_through(self, last: str) -> None:
        for stage in authoring.STAGES:
            record = await self.run(stage)
            assert record["status"] == "passed", (stage, record.get("error"))
            if stage == last:
                return

    def marks(self) -> int:
        return len(self.ops.calls)

    def calls_since(self, mark: int) -> list[tuple]:
        return self.ops.calls[mark:]


# ---------------------------------------------------------------------------
# Stage order with fake live operations
# ---------------------------------------------------------------------------

async def test_full_stage_order_with_fake_live_ops(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)

    # survey: the clock starts before the first connection.
    mark = rig.marks()
    survey = await rig.run("survey")
    assert survey["status"] == "passed", survey.get("error")
    assert rig.ops.journal_open_at_connect == [True]
    assert rig.calls_since(mark)[:2] == [("connect",), ("load", BASE_NAME)]
    assert [t[0] for t in rig.tools] == ["get_units"]
    assert type(rig.tools[0][2]).__name__ == "GameState"
    # The base has no builder: the fact is recorded for the base, not gated here.
    (base_fact,) = survey["evidence"]["base_required_facts"]
    assert base_fact["discoverable"] is False
    assert survey["evidence"]["base_identity"]["matches"] is True

    # apply: always reloads the identified base, confirms it, then reconnects.
    mark = rig.marks()
    apply = await rig.run("apply")
    assert apply["status"] == "passed", apply.get("error")
    since = rig.calls_since(mark)
    assert since[1:3] == [("load", BASE_NAME), ("reconnect",)]
    assert since.index(("load", BASE_NAME)) < since.index(("mutation", "PLACE_BUILDER"))
    assert ("export", BASE_NAME,
            f"{WSL_WINDOWS_REPO}/benchmark_runs/plan3-part1/bases/{BASE_SHA}.Civ6Save",
            BASE_SHA, None) in since
    resolved = apply["evidence"]["bindings"]
    assert resolved["builder"]["pair"] == [0, 65536]
    assert resolved["site"]["xy"] == [12, 10]
    # Setup Lua runs in GameCore (the only context exposing mutation APIs), never InGame.
    assert not any(c[0] == "write" for c in since)
    (mutations,) = (rig.attempt / "mutations").glob("*-apply.json")
    (operation,) = json.loads(mutations.read_text())["operations"]
    assert operation["context"] == "gamecore" and operation["lua"] == "PLACE_BUILDER"

    # probe: frozen registry tools, each from a fresh base replay.
    mark = rig.marks()
    probe = await rig.run("probe")
    assert probe["status"] == "passed", probe.get("error")
    assert [t[0] for t in rig.tools[1:]] == ["improve_tile", "fortify_unit"]
    assert rig.tools[1][1] == {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"}
    since = rig.calls_since(mark)
    assert since.count(("load", BASE_NAME)) == 2
    assert since.count(("mutation", "PLACE_BUILDER")) == 2

    # archive: replay, stat the native save, save, export into the Windows
    # checkout's gitignored staging tree, publish locally.
    mark = rig.marks()
    archive = await rig.run("archive")
    assert archive["status"] == "passed", archive.get("error")
    since = rig.calls_since(mark)
    kinds = [c[0] for c in since]
    archive_rel = "benchmarks/saves/test-builder-a1-v1.Civ6Save"
    staged = (f"{WSL_WINDOWS_REPO}/benchmark_runs/plan3-part1/exports/builder-a1/"
              f"{archive['sequence']:03d}-test-builder-a1-v1.Civ6Save")
    export = ("export", "TEST_BUILDER_A1_V1", staged, None, None)  # no stale save: nothing excluded
    assert kinds.index("load") < kinds.index("mutation") < since.index(("stat", "TEST_BUILDER_A1_V1")) \
        < kinds.index("save") < since.index(export) < kinds.index("publish")
    assert since[kinds.index("publish")][1] == staged
    assert archive["evidence"]["native_save_before"]["exists"] is False
    assert archive["evidence"]["export_staging_path"] == staged
    local = tmp_path / archive_rel
    assert archive["evidence"]["export_sha256"] == archive["evidence"]["publish_sha256"] \
        == hashlib.sha256(local.read_bytes()).hexdigest()
    # The archived start is observed (setup fact discoverable) before saving.
    assert [t[0] for t in rig.tools[3:]] == ["get_units"]
    (fact,) = archive["evidence"]["required_facts"]
    assert fact["discoverable"] is True
    assert all("/observations/archived-" in p for p in archive["evidence"]["archived_observations"])

    capture = await rig.run("capture")
    assert capture["status"] == "passed", capture.get("error")
    authoring_input = tmp_path / "benchmarks/provenance/test-builder-a1-v1-authoring.json"
    frozen = json.loads(authoring_input.read_text())
    assert frozen["archive_sha256"] == archive["evidence"]["export_sha256"]
    assert frozen["base_save_identity"] == RECIPE["base_save_identity"]
    assert frozen["capture"]["digest"] == capture["evidence"]["captured_state_sha256"]
    validation_dir = tmp_path / "benchmarks/validation/test-builder-a1-v1"
    public = json.loads((validation_dir / "public-observation.json").read_text())
    assert [d["result_capped"] for d in public] == ["UNIT_BUILDER at 10,10 task 12,10"]
    position = json.loads((tmp_path / "benchmarks/positions/test-builder-a1-v1.yaml").read_text())
    assert position["public_task_tiles"] == [[12, 10]]
    # Scripts carry no expectations; cases are separate documents.
    script = json.loads((tmp_path / "benchmarks/scripts/test-builder-a1-v1/observe.json").read_text())
    assert set(script) == {"schema_version", "script_id", "batches"}
    case_path = tmp_path / "benchmarks/validation/test-builder-a1-v1/cases/null-observe.json"
    assert json.loads(case_path.read_text())["expected"]["score"] == {"primary_score": 0}

    mark = rig.marks()
    verify = await rig.run("verify")
    assert verify["status"] == "passed", verify.get("error")
    assert ("verify_position", 12) in rig.calls_since(mark)

    mark = rig.marks()
    menu = await rig.run("menu-check")
    assert menu["status"] == "passed", menu.get("error")
    since = rig.calls_since(mark)
    kinds = [c[0] for c in since]
    assert ("restart_and_load", "TEST_BUILDER_A1_V1") in since
    assert kinds.index("restart_and_load") < kinds.index("connect") < kinds.index("reconnect")
    assert not any(k in ("reload", "load") for k in kinds)
    assert menu["evidence"]["loader_result"] == rig.ops.restart_result
    assert menu["evidence"]["digest_matches"] is True
    assert menu["evidence"]["identity_matches"] is True

    mark = rig.marks()
    validate = await rig.run("validate")
    assert validate["status"] == "passed", validate.get("error")
    since = [c[0] for c in rig.calls_since(mark)]
    assert since.index("run_validation") < since.index("build_reports") < since.index("reload")
    # The in-place restore reload is followed by a reconnect before the capture.
    reload_at = since.index("reload")
    assert since[reload_at + 1] == "reconnect"
    assert since.index("capture", reload_at) > reload_at + 1
    restore = validate["evidence"]["restore"]
    assert restore["reloaded"] is True and restore["reconnect"] == {"reconnected": True}
    assert restore["digest"] == validate["evidence"]["restored_digest"]
    suite_path, run_dir = rig.ops.validation_inputs[0]
    assert run_dir == rig.attempt / "validation" / "test-builder-a1-v1" / "test-builder-a1-v1-validation"
    assert validate["evidence"]["restored_digest_matches"] is True

    finish = await rig.run("finish")
    assert finish["status"] == "passed", finish.get("error")
    journal = json.loads((rig.attempt / "authoring-journal.json").read_text())
    (record,) = journal["families"]["builder"]["scenarios"]
    assert record["status"] == "passed"
    index = json.loads((rig.attempt / "evidence-index.json").read_text())
    paths = {e["path"] for e in index["files"]}
    assert "benchmark_runs/plan3-part1/builder-a1/authoring-journal.json" in paths
    assert "benchmarks/saves/test-builder-a1-v1.Civ6Save" in paths
    assert "benchmarks/recipes/test-builder-a1.yaml" in paths
    assert not any(p.endswith((".lock", ".tmp")) for p in paths)
    assert "benchmark_runs/plan3-part1/builder-a1/evidence-index.json" not in paths
    packet = json.loads((tmp_path / "benchmarks/provenance/test-builder-a1-v1.json").read_text())
    index_bytes = (rig.attempt / "evidence-index.json").read_bytes()
    assert packet["evidence_index"]["sha256"] == hashlib.sha256(index_bytes).hexdigest()
    assert "test-builder-a1-v1.json" not in index_bytes.decode()
    assert stage_status(rig.attempt) == {s: "passed" for s in authoring.STAGES}


async def test_setup_fact_beyond_cap_at_archived_start_blocks_archive(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    rig.ops.setup_tool_results["get_units"] = "x" * 45 + " UNIT_BUILDER"
    await rig.run_through("probe")
    mark = rig.marks()
    record = await rig.run("archive")
    assert record["status"] == "failed"
    assert "builder-visible" in record["error"]
    assert not any(c[0] in ("save", "publish") or (c[0] == "export" and c[1] != BASE_NAME)
                   for c in rig.calls_since(mark))
    (obs,) = sorted((rig.attempt / "observations").glob("archived-*.json"))
    saved = json.loads(obs.read_text())
    assert saved["result_full"].endswith("UNIT_BUILDER")
    assert saved["result_capped"] == "x" * 40
    with pytest.raises(ValueError, match="archive"):
        await rig.run("capture")


async def test_binding_centred_query_is_deferred_on_base_and_resolved_at_archive(
        tmp_path, tool_log):
    """A survey query may centre on a binding (e.g. a radius-1 map on a spawned
    threat): the base survey skips it, the archived start dispatches it with
    the resolved coordinates."""
    survey = copy.deepcopy(RECIPE["survey"])
    survey["queries"].append({"tool": "get_units",
                              "arguments": {"x": "${builder.x}", "y": "${builder.y}"}})
    rig = Rig(tmp_path, tool_log, _recipe(survey=survey))
    record = await rig.run("survey")
    assert record["status"] == "passed", record.get("error")
    assert [t[1] for t in rig.tools] == [{}]
    await rig.run_through("probe")
    mark = len(rig.tools)
    record = await rig.run("archive")
    assert record["status"] == "passed", record.get("error")
    assert [t[1] for t in rig.tools[mark:]] == [{}, {"x": 10, "y": 10}]
    archived = sorted((rig.attempt / "observations").glob("archived-*.json"))
    saved = json.loads(archived[-1].read_text())
    assert saved["arguments"] == {"x": "${builder.x}", "y": "${builder.y}"}
    assert saved["resolved_arguments"] == {"x": 10, "y": 10}


def test_recipe_rejects_unknown_binding_in_survey_query(tmp_path):
    survey = copy.deepcopy(RECIPE["survey"])
    survey["queries"].append({"tool": "get_units", "arguments": {"x": "${ghost.x}"}})
    with pytest.raises(ValueError, match="ghost"):
        load_recipe(write_recipe(tmp_path, _recipe(survey=survey)), root=tmp_path)


async def test_base_load_error_fails_before_any_setup_mutation(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("survey")
    rig.ops.load_result = "Error: save not found"
    mark = rig.marks()
    record = await rig.run("apply")
    assert record["status"] == "failed"
    assert "base load" in record["error"]
    assert not any(c[0] == "mutation" for c in rig.calls_since(mark))
    assert list((rig.attempt / "mutations").glob("*.json"))


async def test_base_identity_mismatch_fails_before_any_setup_mutation(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("survey")
    rig.ops.identity = {"turn": 101}
    mark = rig.marks()
    record = await rig.run("apply")
    assert record["status"] == "failed"
    assert "identity" in record["error"]
    assert not any(c[0] == "mutation" for c in rig.calls_since(mark))


async def test_menu_check_fails_on_recovery_loader_error(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("verify")
    rig.ops.restart_result = "Kill: ok | Launch: ok | Load: Error: save not found"
    record = await rig.run("menu-check")
    assert record["status"] == "failed"
    assert record["evidence"]["loader_result"] == rig.ops.restart_result
    with pytest.raises(ValueError, match="menu-check"):
        await rig.run("validate")


async def test_menu_check_fails_when_state_after_recovery_differs(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("verify")
    rig.ops.after_restart_builder_xy = (11, 11)
    record = await rig.run("menu-check")
    assert record["status"] == "failed"
    assert record["evidence"]["digest_matches"] is False


async def test_clock_expiring_during_stage_records_failed_with_evidence(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("capture")
    original = rig.ops.verify_position

    async def slow_verify(position, cycles, *, capture_state, digest):
        result = await original(position, cycles, capture_state=capture_state, digest=digest)
        rig.ops.now += 10_801
        return result

    rig.ops.verify_position = slow_verify
    record = await rig.run("verify")
    assert record["status"] == "failed"
    assert record["error"].startswith("clock_expired")
    assert record["evidence"]["result"]["ok"] is True
    on_disk = json.loads(next((rig.attempt / "stages").glob("*-verify.json")).read_text())
    assert on_disk["status"] == "failed"
    journal = json.loads((rig.attempt / "authoring-journal.json").read_text())
    (scenario,) = journal["families"]["builder"]["scenarios"]
    assert scenario["expired"] is True


async def test_abandon_indexes_failed_attempt_and_closes_journal(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("survey")
    rig.ops.readback_lines = ["ERR|no builder"]
    assert (await rig.run("apply"))["status"] == "failed"
    record = authoring.abandon_attempt(rig.recipe_path, attempt_dir=rig.attempt,
                                       reason="setup readback cannot pass",
                                       ops=rig.ops.live_ops(), root=tmp_path)
    assert record["status"] == "abandoned"
    assert (rig.attempt / "evidence-index.json").is_file()
    journal = json.loads((rig.attempt / "authoring-journal.json").read_text())
    (scenario,) = journal["families"]["builder"]["scenarios"]
    assert scenario["status"] == "failed"
    paths = evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)
    assert "benchmark_runs/plan3-part1/builder-a1/evidence-index.json" in paths
    assert any("/mutations/" in p for p in paths)
    with pytest.raises(ValueError, match="closed"):
        await rig.run("apply")


async def test_abandon_indexes_an_expired_attempt(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("survey")
    rig.ops.now += 10_801
    assert (await rig.run("apply"))["status"] == "failed"
    record = authoring.abandon_attempt(rig.recipe_path, attempt_dir=rig.attempt,
                                       reason="clock expired", ops=rig.ops.live_ops(),
                                       root=tmp_path)
    assert record["evidence"]["expired_before"] is True
    evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)


async def test_abandon_is_idempotent_when_the_index_write_failed(tmp_path, tool_log, monkeypatch):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("survey")
    real = authoring._write_index

    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(authoring, "_write_index", broken)
    with pytest.raises(OSError, match="disk full"):
        authoring.abandon_attempt(rig.recipe_path, attempt_dir=rig.attempt, reason="r",
                                  ops=rig.ops.live_ops(), root=tmp_path)
    assert not (rig.attempt / "evidence-index.json").exists()
    monkeypatch.setattr(authoring, "_write_index", real)
    record = authoring.abandon_attempt(rig.recipe_path, attempt_dir=rig.attempt, reason="r",
                                       ops=rig.ops.live_ops(), root=tmp_path)
    assert record["status"] == "abandoned" and record["recovered"] is True
    assert (rig.attempt / "evidence-index.json").is_file()
    assert len(list((rig.attempt / "stages").glob("*-abandon.json"))) == 1
    evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)
    with pytest.raises(ValueError, match="closed"):
        authoring.abandon_attempt(rig.recipe_path, attempt_dir=rig.attempt, reason="r",
                                  ops=rig.ops.live_ops(), root=tmp_path)


async def test_finish_is_idempotent_when_the_index_write_failed(tmp_path, tool_log, monkeypatch):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("validate")
    real = authoring._write_index

    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(authoring, "_write_index", broken)
    with pytest.raises(OSError, match="disk full"):
        await rig.run("finish")
    journal = json.loads((rig.attempt / "authoring-journal.json").read_text())
    assert journal["families"]["builder"]["scenarios"][0]["status"] == "passed"
    assert not (rig.attempt / "evidence-index.json").exists()
    monkeypatch.setattr(authoring, "_write_index", real)
    mark = rig.marks()
    record = await rig.run("finish")
    assert record["status"] == "passed" and record["recovered"] is True
    assert rig.calls_since(mark) == []  # no live contact on recovery
    assert (rig.attempt / "evidence-index.json").is_file()
    assert (tmp_path / record["completion"]["packet"]["path"]).is_file()
    assert len(list((rig.attempt / "stages").glob("*-finish.json"))) == 1
    evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)
    with pytest.raises(ValueError, match="closed"):
        await rig.run("finish")


async def test_finish_recovers_when_only_the_packet_write_failed(tmp_path, tool_log,
                                                                 monkeypatch):
    """Index written, packet write failed: re-running `finish` reuses the
    verified index and publishes the missing packet without live contact."""
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("validate")
    real = authoring._write_immutable

    def broken(path, data):
        raise OSError("disk full")
    monkeypatch.setattr(authoring, "_write_immutable", broken)
    with pytest.raises(OSError, match="disk full"):
        await rig.run("finish")
    index_path = rig.attempt / "evidence-index.json"
    index_bytes = index_path.read_bytes()
    packet_path = tmp_path / "benchmarks" / "provenance" / "test-builder-a1-v1.json"
    assert index_path.is_file() and not packet_path.exists()

    monkeypatch.setattr(authoring, "_write_immutable", real)
    mark = rig.marks()
    record = await rig.run("finish")
    assert record["status"] == "passed" and record["recovered"] is True
    assert rig.calls_since(mark) == []  # no live contact on recovery
    assert packet_path.is_file()
    assert index_path.read_bytes() == index_bytes  # reused, never rewritten
    packet = json.loads(packet_path.read_text())
    assert packet["evidence_index"]["sha256"] == hashlib.sha256(index_bytes).hexdigest()
    assert record["completion"]["packet"]["path"] == "benchmarks/provenance/test-builder-a1-v1.json"
    assert packet["evidence_index"]["path"] == "benchmark_runs/plan3-part1/builder-a1/evidence-index.json"
    assert len(list((rig.attempt / "stages").glob("*-finish.json"))) == 1
    evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)
    with pytest.raises(ValueError, match="closed"):
        await rig.run("finish")


async def test_finish_recovery_refuses_an_index_that_no_longer_matches(tmp_path, tool_log,
                                                                       monkeypatch):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("validate")
    monkeypatch.setattr(authoring, "_write_immutable",
                        lambda path, data: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        await rig.run("finish")
    monkeypatch.undo()
    (rig.attempt / "notes.json").write_text("{}")  # appeared after the index was written
    with pytest.raises(ValueError, match="does not inventory the attempt's current files"):
        await rig.run("finish")


def _substitute_recipe() -> dict:
    return _recipe(recipe_id="test-builder-a2", scenario_id="builder-a2",
                   predecessor="builder-a1", substitution_reason="setup cannot pass",
                   material_change="different builder tile")


async def _failed_attempt_one(tmp_path, tool_log) -> Rig:
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("survey")
    authoring.abandon_attempt(rig.recipe_path, attempt_dir=rig.attempt, reason="dead end",
                              ops=rig.ops.live_ops(), root=tmp_path)
    return rig


async def test_substitute_in_fresh_attempt_dir_binds_the_sibling_predecessor_journal(
        tmp_path, tool_log):
    first = await _failed_attempt_one(tmp_path, tool_log)
    path = write_recipe(tmp_path, _substitute_recipe(), name="test-builder-a2")
    attempt = first.attempt.parent / "builder-a2"
    record = await run_authoring_stage(path, stage="survey", attempt_dir=attempt,
                                       ops=first.ops.live_ops(), root=tmp_path)
    assert record["status"] == "passed", record.get("error")
    journal = json.loads((attempt / "authoring-journal.json").read_text())
    imported, substitute = journal["families"]["builder"]["scenarios"]
    assert imported["imported_from"]["journal"] == \
        "benchmark_runs/plan3-part1/builder-a1/authoring-journal.json"
    assert substitute["attempt"] == 2 and substitute["predecessor"] == "builder-a1"


async def test_substitute_with_explicit_predecessor_journal_elsewhere(tmp_path, tool_log):
    first = await _failed_attempt_one(tmp_path, tool_log)
    path = write_recipe(tmp_path, _substitute_recipe(), name="test-builder-a2")
    # Not a sibling of the predecessor's attempt dir, so no automatic discovery.
    attempt = tmp_path / "benchmark_runs" / "plan3-part1" / "retry" / "builder-a2"
    with pytest.raises(ValueError, match="failed predecessor"):
        await run_authoring_stage(path, stage="survey", attempt_dir=attempt,
                                  ops=first.ops.live_ops(), root=tmp_path)
    record = await run_authoring_stage(
        path, stage="survey", attempt_dir=attempt, ops=first.ops.live_ops(), root=tmp_path,
        predecessor_journal=first.attempt / "authoring-journal.json")
    assert record["status"] == "passed", record.get("error")


async def test_attempt_dir_and_predecessor_journal_must_live_under_the_gate_root(
        tmp_path, tool_log):
    """The gate scans only benchmark_runs/plan3-part1/: an attempt or a
    predecessor journal anywhere else would be silently absent from it, so
    authoring refuses both before the journal opens."""
    first = await _failed_attempt_one(tmp_path, tool_log)
    path = write_recipe(tmp_path, _substitute_recipe(), name="test-builder-a2")
    outside = tmp_path / "benchmark_runs" / "plan3-part1-retry" / "builder-a2"
    with pytest.raises(ValueError, match="attempt directory .* must live under "
                                         "benchmark_runs/plan3-part1/"):
        await run_authoring_stage(
            path, stage="survey", attempt_dir=outside, ops=first.ops.live_ops(), root=tmp_path,
            predecessor_journal=first.attempt / "authoring-journal.json")
    assert not outside.exists()

    elsewhere = tmp_path / "benchmark_runs" / "elsewhere" / "authoring-journal.json"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_bytes((first.attempt / "authoring-journal.json").read_bytes())
    attempt = first.attempt.parent / "builder-a2"
    with pytest.raises(ValueError, match="predecessor journal .* must live under "
                                         "benchmark_runs/plan3-part1/"):
        await run_authoring_stage(path, stage="survey", attempt_dir=attempt,
                                  ops=first.ops.live_ops(), root=tmp_path,
                                  predecessor_journal=elsewhere)
    assert not (attempt / "authoring-journal.json").exists()


async def test_evidence_files_rejects_unindexed_attempt(tmp_path, tool_log):
    await _finished(tmp_path, tool_log)
    # A sibling attempt (another scenario) that ran a stage but was never closed.
    sibling = Rig(tmp_path, tool_log)
    sibling.attempt = tmp_path / "benchmark_runs" / "plan3-part1" / "city-a1"
    sibling.ops.attempt_dir = sibling.attempt
    sibling.recipe_path = write_recipe(tmp_path, _recipe(family="city", scenario_id="city-a1"),
                                       name="test-city-a1")
    assert (await sibling.run("survey"))["status"] == "passed"
    with pytest.raises(ValueError, match="unindexed_attempt: benchmark_runs/plan3-part1/city-a1"):
        evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)


async def test_failed_readback_blocks_probe_and_archive(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("survey")
    rig.ops.readback_lines = ["ERR|no builder"]
    record = await rig.run("apply")
    assert record["status"] == "failed"
    assert "readback" in record["error"]
    assert list((rig.attempt / "mutations").glob("*.json"))
    with pytest.raises(ValueError, match="apply"):
        await rig.run("probe")
    with pytest.raises(ValueError, match="probe"):
        await rig.run("archive")


async def test_failed_probe_prevents_archive_admission(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("apply")
    rig.ops.tool_results["improve_tile"] = "Error: cannot improve"
    record = await rig.run("probe")
    assert record["status"] == "failed"
    assert "improve-site" in record["error"]
    assert len(list((rig.attempt / "probes").glob("*.json"))) == 2
    mark = rig.marks()
    with pytest.raises(ValueError, match="probe"):
        await rig.run("archive")
    assert rig.calls_since(mark) == []


async def test_expired_clock_refuses_stage_without_live_commands(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("survey")
    rig.ops.now += 10_801
    mark = rig.marks()
    record = await rig.run("apply")
    assert record["status"] == "failed"
    assert "expired" in record["error"]
    assert rig.calls_since(mark) == []
    files = sorted(p.name for p in (rig.attempt / "stages").glob("*.json"))
    assert files == ["001-survey.json", "002-apply.json"]


async def test_repeating_apply_invalidates_downstream_and_bumps_archive_version(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("archive")
    rig.ops.builder_xy = (11, 10)
    record = await rig.run("apply")
    assert record["status"] == "passed"
    status = stage_status(rig.attempt)
    assert status["probe"] == "invalidated" and status["archive"] == "invalidated"
    with pytest.raises(ValueError, match="probe"):
        await rig.run("archive")
    assert (await rig.run("probe"))["status"] == "passed"
    archive = await rig.run("archive")
    assert archive["status"] == "passed", archive.get("error")
    assert archive["evidence"]["version"] == 2
    assert archive["evidence"]["bindings_changed"] is True
    assert (tmp_path / "benchmarks/saves/test-builder-a1-v2.Civ6Save").is_file()
    assert (tmp_path / "benchmarks/saves/test-builder-a1-v1.Civ6Save").is_file()
    # Every stage record is retained; nothing was overwritten.
    names = sorted(p.name for p in (rig.attempt / "stages").glob("*.json"))
    assert len(names) == len(set(names)) == 9


async def test_finish_requires_final_restored_digest(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("menu-check")
    rig.ops.restore_builder_xy = (11, 11)
    record = await rig.run("validate")
    assert record["status"] == "failed"
    assert record["evidence"]["restored_digest_matches"] is False
    with pytest.raises(ValueError, match="validate"):
        await rig.run("finish")


async def test_unverified_restore_reload_fails_validate(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("menu-check")
    rig.ops.reload_verified = False
    mark = rig.marks()
    record = await rig.run("validate")
    assert record["status"] == "failed"
    assert "not verified" in record["error"]
    assert record["evidence"]["restore"]["reloaded"] is False
    since = [c[0] for c in rig.calls_since(mark)]
    assert "reconnect" not in since[since.index("reload"):]
    assert "capture" not in since[since.index("reload"):]
    with pytest.raises(ValueError, match="validate"):
        await rig.run("finish")


async def test_finish_refuses_when_any_stage_was_invalidated(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("validate")
    assert (await rig.run("survey"))["status"] == "passed"
    with pytest.raises(ValueError, match="validate"):
        await rig.run("finish")


async def test_verification_mismatch_fails_verify(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("capture")
    rig.ops.builder_xy = (11, 10)
    record = await rig.run("verify")
    assert record["status"] == "failed"
    with pytest.raises(ValueError, match="verify"):
        await rig.run("menu-check")


# ---------------------------------------------------------------------------
# evidence-files
# ---------------------------------------------------------------------------

async def _finished(tmp_path, tool_log) -> Rig:
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("finish")
    return rig


async def test_revalidation_attempt_reopens_a_passed_scenario_in_a_fresh_dir(
        tmp_path, tool_log):
    """Amendment 2026-10-10 (decision 2): a declared revalidation of a closed,
    passed attempt is admitted in a new attempt directory with a new clock; the
    closed attempt is never touched."""
    done = await _finished(tmp_path, tool_log)
    closed_journal = done.attempt / "authoring-journal.json"
    before = closed_journal.read_bytes()
    attempt = done.attempt.parent / "builder-a1-reval"
    with pytest.raises(ValueError, match="already journaled"):
        await run_authoring_stage(done.recipe_path, stage="survey", attempt_dir=attempt,
                                  ops=done.ops.live_ops(), root=tmp_path)
    assert not (attempt / "stages").exists()
    outside = tmp_path / "benchmark_runs" / "elsewhere" / "authoring-journal.json"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(before)
    with pytest.raises(ValueError, match="revalidated journal .* must live under "
                                         "benchmark_runs/plan3-part1/"):
        await run_authoring_stage(done.recipe_path, stage="survey", attempt_dir=attempt,
                                  ops=done.ops.live_ops(), root=tmp_path, revalidates=outside)
    record = await run_authoring_stage(
        done.recipe_path, stage="survey", attempt_dir=attempt, ops=done.ops.live_ops(),
        root=tmp_path, revalidates=closed_journal)
    assert record["status"] == "passed", record.get("error")
    journal = json.loads((attempt / "authoring-journal.json").read_text())
    (own,) = journal["families"]["builder"]["scenarios"]
    assert own["revalidates"] == {
        "journal": "benchmark_runs/plan3-part1/builder-a1/authoring-journal.json",
        "sha256": hashlib.sha256(before).hexdigest()}
    assert own["attempt"] == 1 and own["status"] == "open"
    assert closed_journal.read_bytes() == before
    assert (done.attempt / "evidence-index.json").exists()
    # Later stages resume the open revalidation clock without the declaration.
    record = await run_authoring_stage(done.recipe_path, stage="apply", attempt_dir=attempt,
                                       ops=done.ops.live_ops(), root=tmp_path)
    assert record["status"] == "passed", record.get("error")


async def test_evidence_files_lists_closed_inventory(tmp_path, tool_log):
    rig = await _finished(tmp_path, tool_log)
    (rig.attempt / "stray.lock").write_text("123")
    (rig.attempt / "__pycache__").mkdir()
    (rig.attempt / "__pycache__" / "x.pyc").write_bytes(b"\0")
    paths = evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)
    assert "benchmark_runs/plan3-part1/builder-a1/evidence-index.json" in paths
    assert "benchmark_runs/plan3-part1/builder-a1/authoring-journal.json" in paths
    assert "benchmarks/saves/test-builder-a1-v1.Civ6Save" in paths
    assert not any(p.endswith((".lock", ".tmp", ".pyc")) for p in paths)
    assert paths == sorted(set(paths))


async def test_evidence_files_rejects_altered_entry(tmp_path, tool_log):
    rig = await _finished(tmp_path, tool_log)
    (next((rig.attempt / "observations").glob("*.json"))).write_text("{}")
    with pytest.raises(ValueError, match="sha256"):
        evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)


async def test_evidence_files_rejects_missing_entry(tmp_path, tool_log):
    rig = await _finished(tmp_path, tool_log)
    (tmp_path / "benchmarks/saves/test-builder-a1-v1.Civ6Save").unlink()
    with pytest.raises(ValueError, match="missing"):
        evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)


async def test_evidence_files_rejects_untracked_attempt_file(tmp_path, tool_log):
    rig = await _finished(tmp_path, tool_log)
    (rig.attempt / "probes" / "late.json").write_text("{}")
    with pytest.raises(ValueError, match="untracked"):
        evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)


async def test_evidence_files_rejects_untracked_stage_reference(tmp_path, tool_log):
    rig = await _finished(tmp_path, tool_log)
    index_path = rig.attempt / "evidence-index.json"
    index = json.loads(index_path.read_text())
    index["files"] = [e for e in index["files"] if "/observations/" not in e["path"]]
    index_path.write_text(json.dumps(index))
    for obs in (rig.attempt / "observations").glob("*.json"):
        obs.unlink()
    with pytest.raises(ValueError, match="observations"):
        evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)


async def test_evidence_files_rejects_out_of_scope_reference(tmp_path, tool_log):
    rig = await _finished(tmp_path, tool_log)
    outside = tmp_path / "src" / "secret.txt"
    outside.parent.mkdir()
    outside.write_text("x")
    index_path = rig.attempt / "evidence-index.json"
    index = json.loads(index_path.read_text())
    index["files"].append({"path": "src/secret.txt",
                           "sha256": hashlib.sha256(b"x").hexdigest()})
    index_path.write_text(json.dumps(index))
    with pytest.raises(ValueError, match="scope"):
        evidence_files(tmp_path / "benchmark_runs" / "plan3-part1", repo_root=tmp_path)


def test_evidence_files_cli_writes_one_path_per_line(tmp_path, monkeypatch):
    monkeypatch.setattr(authoring, "evidence_files",
                        lambda root, *, repo_root: ["benchmarks/a.json", "benchmarks/b.json"])
    out = tmp_path / "paths.txt"
    assert authoring.main(["evidence-files", "--root", str(tmp_path), "--output", str(out)]) == 0
    assert out.read_text() == "benchmarks/a.json\nbenchmarks/b.json\n"


# ---------------------------------------------------------------------------
# preflight (offline)
# ---------------------------------------------------------------------------

def _preflight_recipe(tmp_path: Path) -> Path:
    doc = _recipe(toolset_id="plan3-part1-v1",
                  toolset_path="benchmarks/toolsets/plan3-part1-v1.yaml")
    path = tmp_path / "recipe.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def test_preflight_binds_identities_offline(tmp_path, monkeypatch):
    calls = []

    def fake_audit(fixture_path, *, root):
        calls.append(Path(fixture_path))
        return {"membership_matches": True, "trial_count": 96, "uncredited_count": 3}

    monkeypatch.setattr(authoring, "reproduce_audit", fake_audit)
    monkeypatch.setattr(authoring, "production_ops",
                        lambda: pytest.fail("preflight must never build live operations"))
    result = preflight([_preflight_recipe(tmp_path)], root=REPO)
    assert "historical_audit" not in result["failed_requirements"]
    assert calls and calls[0].name == "builder_uncredited_audit_v1.json"
    (entry,) = result["recipes"]
    assert entry["recipe_id"] == "test-builder-a1"
    assert entry["toolset_identity"]["source_sha256"]
    assert len(result["code_identity"]) == 64
    assert set(result["schema_identity"]) >= {"schema_version", "manifest_sha256", "scorer_sha256"}
    assert "full_suite_result" in result
    assert result["historical_audit"]["membership_matches"] is True


def test_preflight_fails_when_historical_audit_diverges(tmp_path, monkeypatch):
    monkeypatch.setattr(authoring, "reproduce_audit",
                        lambda fixture_path, *, root: {"membership_matches": False})
    out = tmp_path / "preflight.json"
    code = authoring.main(["preflight", "--recipes", str(_preflight_recipe(tmp_path)),
                           "--output", str(out)])
    assert code == 1
    assert json.loads(out.read_text())["passed"] is False


def test_toolkit_identity_covers_authoring_modules():
    from civ_mcp.arena.benchmark_contract_v2 import (
        FINGERPRINT_DEPENDENCIES,
        TOOLKIT_DEPENDENCIES,
    )
    for module in ("benchmark_authoring.py", "benchmark_authoring_journal.py"):
        assert f"src/civ_mcp/arena/{module}" in TOOLKIT_DEPENDENCIES
        assert f"src/civ_mcp/arena/{module}" not in FINGERPRINT_DEPENDENCIES
    assert digest_state_v2  # imported for fixture parity


# ---------------------------------------------------------------------------
# Probe expect_pattern and measured parameters
# ---------------------------------------------------------------------------

THRESHOLD_PATH = ["objectives", 0, "rungs", 0, "predicate", "minimum_damage"]


def _measured_recipe() -> dict:
    doc = _recipe()
    doc["bindings"].append({"name": "threat", "selector": {"owner": 1, "hostile": True},
                            "resolves": "target"})
    doc["coverage_rule"]["tracked_target_bindings"] = ["threat"]
    doc["objectives"][0]["rungs"].insert(0, {"points": 2, "predicate": {
        "kind": "target_damaged", "target": "${threat.pair}", "minimum_damage": 25}})
    for probe_id, name in (("weak", "WEAK"), ("strong", "STRONG")):
        doc["probes"].append({
            "id": probe_id, "tool": "improve_tile", "expect": "ok", "restore": True,
            "measure_target": "${threat.pair}",
            "arguments_from_bindings": {"unit_index": "${builder.unit_index}",
                                        "improvement_name": name}})
    doc["measured_parameters"] = [{
        "name": "damage_threshold", "provisional": True, "used_by": [THRESHOLD_PATH],
        "source_probes": ["weak", "strong"],
        "rule": {"strictly_between": ["weak", "strong"], "survives": ["strong"]}}]
    return doc


class MeasuredOps(FakeOps):
    """A tracked hostile whose hp the measuring probes change; reloads restore it."""

    def __init__(self, root: Path, attempt_dir: Path) -> None:
        super().__init__(root, attempt_dir)
        self.target_hp = 100
        self.target_visible = True
        self.damage = {"WEAK": 10, "STRONG": 40}
        self.hide_after: str | None = None

    async def load_game_save(self, conn, name):
        self.target_hp, self.target_visible = 100, True
        return await super().load_game_save(conn, name)

    def state(self, coverage) -> dict:
        state = super().state(coverage)
        if self.target_visible:
            target = dict(owner=1, id=70001, tracked=True, role="combat", hostile=True,
                          visible=True, status="alive_visible", x=11, y=10,
                          hp=self.target_hp, max_hp=100)
        else:
            target = dict(owner=1, id=70001, tracked=True, role=None, hostile=True,
                          visible=False, status="alive_not_visible", x=None, y=None,
                          hp=None, max_hp=None)
        state["targets"] = [target]
        state["row_counts"] = {**state["row_counts"], "target": 1}
        state["coverage"] = {**state["coverage"], "tracked_targets": [[1, 70001]]}
        return state


def _measured_rig(tmp_path, tool_log, monkeypatch) -> Rig:
    rig = Rig(tmp_path, tool_log, _measured_recipe())
    rig.ops = MeasuredOps(tmp_path, rig.attempt)
    rig.tools = tool_log(rig.ops)
    ops = rig.ops

    async def improve(gs, args, **_context):
        name = args.get("improvement_name")
        ops.target_hp -= ops.damage.get(name, 0)
        if name == ops.hide_after:
            ops.target_visible = False
        return ops.tool_results["improve_tile"]
    monkeypatch.setitem(registry.TOOL_REGISTRY, "improve_tile",
                        dataclasses.replace(registry.TOOL_REGISTRY["improve_tile"], call=improve))
    return rig


async def test_probe_expect_pattern_must_match_the_result(tmp_path, tool_log):
    doc = _recipe()
    doc["probes"][0]["expect_pattern"] = "Started IMPROVEMENT_PASTURE"
    rig = Rig(tmp_path, tool_log, doc)
    await rig.run_through("apply")
    record = await rig.run("probe")
    assert record["status"] == "failed" and "improve-site" in record["error"]

    doc["probes"][0]["expect_pattern"] = "Started IMPROVEMENT_FARM"
    other = Rig(tmp_path / "ok", tool_log, doc)
    await other.run_through("probe")


def test_recipe_rejects_invalid_expect_pattern(tmp_path):
    doc = _recipe()
    doc["probes"][0]["expect_pattern"] = "("
    with pytest.raises(ValueError, match="expect_pattern"):
        load_recipe(write_recipe(tmp_path, doc), root=tmp_path)


def test_damage_threshold_must_be_a_provisional_measured_parameter(tmp_path):
    assert load_recipe(write_recipe(tmp_path, _measured_recipe()), root=tmp_path)
    doc = _measured_recipe()
    del doc["measured_parameters"]
    with pytest.raises(ValueError, match="measured parameter"):
        load_recipe(write_recipe(tmp_path, doc), root=tmp_path)
    doc = _measured_recipe()
    doc["measured_parameters"][0]["provisional"] = False
    with pytest.raises(ValueError, match="provisional"):
        load_recipe(write_recipe(tmp_path, doc), root=tmp_path)
    doc = _measured_recipe()
    del doc["probes"][-1]["measure_target"]
    with pytest.raises(ValueError, match="measure_target"):
        load_recipe(write_recipe(tmp_path, doc), root=tmp_path)


async def test_archive_freezes_measured_parameter_bracketed_by_probe_deltas(
        tmp_path, tool_log, monkeypatch):
    rig = _measured_rig(tmp_path, tool_log, monkeypatch)
    await rig.run_through("capture")
    archive = authoring._latest(authoring._stage_records(rig.attempt))["archive"]["evidence"]
    (frozen,) = archive["measured_parameters"]
    assert frozen["value"] == 25
    assert frozen["measurements"]["weak"]["delta"] == 10
    assert frozen["measurements"]["strong"]["delta"] == 40
    assert frozen["measurements"]["strong"]["status_after"] == "alive_visible"
    assert all((tmp_path / p).is_file() for p in frozen["evidence"])

    position = json.loads((tmp_path / "benchmarks/positions/test-builder-a1-v1.yaml").read_text())
    assert position["rubric"]["objectives"][0]["rungs"][0]["predicate"]["minimum_damage"] == 25
    provenance = json.loads(
        (tmp_path / "benchmarks/provenance/test-builder-a1-v1-authoring.json").read_text())
    assert provenance["measured_parameters"] == archive["measured_parameters"]


async def test_archive_refuses_threshold_outside_measured_deltas(tmp_path, tool_log, monkeypatch):
    rig = _measured_rig(tmp_path, tool_log, monkeypatch)
    rig.ops.damage["WEAK"] = 30  # the futile probe already exceeds the threshold
    await rig.run_through("probe")
    mark = rig.marks()
    record = await rig.run("archive")
    assert record["status"] == "failed"
    assert "strictly between" in record["error"]
    assert ("connect",) not in rig.calls_since(mark)
    assert not list((tmp_path / "benchmarks" / "saves").glob("*.Civ6Save"))


async def test_archive_refuses_without_probe_measurement(tmp_path, tool_log, monkeypatch):
    rig = _measured_rig(tmp_path, tool_log, monkeypatch)
    rig.ops.hide_after = "STRONG"  # the target is not visible after the probe: no delta
    await rig.run_through("probe")
    record = await rig.run("archive")
    assert record["status"] == "failed"
    assert "no passing probe measurement" in record["error"]


async def test_coverage_is_clipped_to_the_map_grid_on_every_edge(tmp_path, tool_log):
    """A binding within `area_radius` of the east or south edge must not put
    off-map tiles into the coverage: the v2 query errors on any such tile and
    no capture of the scenario could ever succeed."""
    rig = Rig(tmp_path, tool_log)
    rig.ops.grid = (13, 11)  # builder (10,10) and site (12,10) sit by the east/south edges
    assert (await rig.run("survey"))["status"] == "passed"
    apply = await rig.run("apply")
    assert apply["status"] == "passed", apply.get("error")
    assert apply["evidence"]["grid"] == [13, 11]
    area = {tuple(p) for p in apply["evidence"]["coverage"]["area"]}
    assert all(0 <= x < 13 and 0 <= y < 11 for x, y in area)
    assert {(12, 10), (11, 9), (9, 9)} <= area
    assert not {(13, 10), (12, 11), (13, 11)} & area
    assert ("grid_size",) in rig.ops.calls


async def test_archive_export_excludes_a_stale_same_name_native_save(tmp_path, tool_log):
    """A failed earlier attempt leaves a same-named save in the game's save
    directory; its pre-request signature is passed to the export so the stale
    file is waited out rather than archived as the new save."""
    rig = Rig(tmp_path, tool_log)
    await rig.run_through("probe")
    rig.ops.saved["TEST_BUILDER_A1_V1"] = b"STALE"  # leftover of an earlier attempt
    rig.ops.saves = 7
    archive = await rig.run("archive")
    assert archive["status"] == "passed", archive.get("error")
    before = archive["evidence"]["native_save_before"]
    assert before == {"exists": True, "size": 5, "mtime_ns": 7}
    (export,) = [c for c in rig.ops.calls if c[0] == "export" and c[1] == "TEST_BUILDER_A1_V1"]
    assert export[4] == (5, 7)
    # The published archive is the new save, not the stale bytes.
    local = tmp_path / "benchmarks/saves/test-builder-a1-v1.Civ6Save"
    assert local.read_bytes() != b"STALE"
    assert archive["evidence"]["export_sha256"] == hashlib.sha256(local.read_bytes()).hexdigest()


def test_list_selector_values_mean_any_of():
    """A Seowon site is 'any hills terrain': a list selector value matches any
    listed value; the resolved row must still be unique."""
    rows = [dict(_tile(1, 1), terrain="TERRAIN_GRASS"),
            dict(_tile(2, 1), terrain="TERRAIN_PLAINS_HILLS"),
            dict(_tile(3, 1), terrain="TERRAIN_GRASS_HILLS", improvement="IMPROVEMENT_MINE")]
    hills = ["TERRAIN_GRASS_HILLS", "TERRAIN_PLAINS_HILLS"]
    site = {"name": "site", "resolves": "tile",
            "selector": {"terrain": hills, "improvement": "NONE"}}
    resolved = authoring.resolve_bindings([site], state_v2(tiles=rows))
    assert resolved["site"]["xy"] == [2, 1]
    both = dict(site, selector={"terrain": hills})
    with pytest.raises(authoring.StageFailure, match="resolved to 2 rows"):
        authoring.resolve_bindings([both], state_v2(tiles=rows))
    none = dict(site, selector={"terrain": ["TERRAIN_SNOW_HILLS"]})
    with pytest.raises(authoring.StageFailure, match="resolved to 0 rows"):
        authoring.resolve_bindings([none], state_v2(tiles=rows))


def test_recipe_selector_lists_must_be_non_empty_scalars(tmp_path):
    recipe = _recipe()
    recipe["bindings"][1]["selector"] = {"terrain": ["TERRAIN_GRASS_HILLS", "TERRAIN_PLAINS_HILLS"],
                                         "x": 12, "y": 10}
    authoring.load_recipe(write_recipe(tmp_path, recipe), root=tmp_path)
    for bad in ([], [["nested"]], [None]):
        recipe["bindings"][1]["selector"] = {"terrain": bad, "x": 12, "y": 10}
        with pytest.raises(ValueError, match="scalar or a non-empty list"):
            authoring.load_recipe(write_recipe(tmp_path, recipe), root=tmp_path)


async def test_toolset_is_resolved_once_per_stage_not_per_dispatch(tmp_path, tool_log,
                                                                   monkeypatch):
    """Survey queries and legality probes run while the authoring clock
    ticks; the toolset YAML is parsed once per stage, not once per dispatch."""
    recipe = _recipe()
    recipe["survey"]["queries"] = [{"tool": "get_units", "arguments": {}}] * 3
    rig = Rig(tmp_path, tool_log, recipe)
    real = authoring.load_toolset
    loads: list[Path] = []

    def counting(path):
        loads.append(Path(path))
        return real(path)

    monkeypatch.setattr(authoring, "load_toolset", counting)
    survey = await rig.run("survey")
    assert survey["status"] == "passed", survey.get("error")
    assert [t[0] for t in rig.tools] == ["get_units"] * 3
    # One load validates the recipe (load_recipe), one serves every dispatch.
    assert len(loads) == 2
