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
    "base_save_identity": {"name": BASE_NAME, "sha256": BASE_SHA},
    "player_id": 0,
    "result_char_cap": 40,
    "setup": {
        "operations": [{"kind": "lua", "lua": "PLACE_BUILDER", "readback": "READ_BUILDER"}],
        "assertions": [{"id": "builder-placed", "value": True, "predicate": {
            "kind": "unit_in_area", "unit_types": ["UNIT_BUILDER"], "tiles": ["${builder.xy}"]}}],
    },
    "survey": {
        "queries": [{"tool": "get_units", "arguments": {}}],
        "required_facts": [{"id": "builder-visible", "pattern": "UNIT_BUILDER"}],
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
        self.ops.calls.append(("write", lua))
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
        self.saves = 0
        self.saved: dict[str, bytes] = {}
        self.tool_results = {"get_units": "Units: UNIT_BUILDER (1) at 10,10",
                             "improve_tile": "Started IMPROVEMENT_FARM",
                             "fortify_unit": "Error: civilians cannot fortify"}
        self.validation_inputs: list[tuple] = []
        self.journal_open_at_connect: list[bool] = []

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
        return True

    async def dismiss_popups(self, conn):
        return "POPUPS|none"

    def export_save(self, name, destination, expected_sha256=None):
        self.calls.append(("export", name, destination, expected_sha256))
        assert destination.startswith(WSL_WINDOWS_REPO + "/")
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
        return f"Loaded {name}"

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
        return state_v2(units=[_builder(*self.builder_xy)], tiles=tiles)

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
            clocks=(self.wall, self.mono))


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
                return ops.tool_results[_name]
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

    # apply: always reloads the identified base before any setup write.
    mark = rig.marks()
    apply = await rig.run("apply")
    assert apply["status"] == "passed", apply.get("error")
    since = rig.calls_since(mark)
    assert since[1] == ("load", BASE_NAME)
    assert since.index(("load", BASE_NAME)) < since.index(("write", "PLACE_BUILDER"))
    assert ("export", BASE_NAME,
            f"{WSL_WINDOWS_REPO}/benchmark_runs/plan3-part1/bases/{BASE_SHA}.Civ6Save",
            BASE_SHA) in since
    resolved = apply["evidence"]["bindings"]
    assert resolved["builder"]["pair"] == [0, 65536]
    assert resolved["site"]["xy"] == [12, 10]

    # probe: frozen registry tools, each from a fresh base replay.
    mark = rig.marks()
    probe = await rig.run("probe")
    assert probe["status"] == "passed", probe.get("error")
    assert [t[0] for t in rig.tools[1:]] == ["improve_tile", "fortify_unit"]
    assert rig.tools[1][1] == {"unit_index": 1, "improvement_name": "IMPROVEMENT_FARM"}
    since = rig.calls_since(mark)
    assert since.count(("load", BASE_NAME)) == 2
    assert since.count(("write", "PLACE_BUILDER")) == 2

    # archive: replay, save, export to the Windows checkout, publish locally.
    mark = rig.marks()
    archive = await rig.run("archive")
    assert archive["status"] == "passed", archive.get("error")
    since = rig.calls_since(mark)
    kinds = [c[0] for c in since]
    archive_rel = "benchmarks/saves/test-builder-a1-v1.Civ6Save"
    export = ("export", "TEST_BUILDER_A1_V1", f"{WSL_WINDOWS_REPO}/{archive_rel}", None)
    assert kinds.index("load") < kinds.index("write") < kinds.index("save") \
        < since.index(export) < kinds.index("publish")
    local = tmp_path / archive_rel
    assert archive["evidence"]["export_sha256"] == archive["evidence"]["publish_sha256"] \
        == hashlib.sha256(local.read_bytes()).hexdigest()

    capture = await rig.run("capture")
    assert capture["status"] == "passed", capture.get("error")
    authoring_input = tmp_path / "benchmarks/provenance/test-builder-a1-v1-authoring.json"
    frozen = json.loads(authoring_input.read_text())
    assert frozen["archive_sha256"] == archive["evidence"]["export_sha256"]
    assert frozen["base_save_identity"] == {"name": BASE_NAME, "sha256": BASE_SHA}
    assert frozen["capture"]["digest"] == capture["evidence"]["captured_state_sha256"]
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
    assert ("load", "TEST_BUILDER_A1_V1") in since
    assert not any(c[0] == "reload" for c in since)
    assert menu["evidence"]["digest_matches"] is True

    mark = rig.marks()
    validate = await rig.run("validate")
    assert validate["status"] == "passed", validate.get("error")
    since = [c[0] for c in rig.calls_since(mark)]
    assert since.index("run_validation") < since.index("build_reports") < since.index("reload")
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


async def test_survey_fact_beyond_cap_fails_and_keeps_observation(tmp_path, tool_log):
    rig = Rig(tmp_path, tool_log)
    rig.ops.tool_results["get_units"] = "x" * 45 + " UNIT_BUILDER"
    record = await rig.run("survey")
    assert record["status"] == "failed"
    assert "builder-visible" in record["error"]
    (obs,) = sorted((rig.attempt / "observations").glob("*.json"))
    saved = json.loads(obs.read_text())
    assert saved["result_full"].endswith("UNIT_BUILDER")
    assert saved["result_capped"] == "x" * 40
    with pytest.raises(ValueError, match="survey"):
        await rig.run("apply")


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
    assert result["passed"] is True
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


def test_fingerprint_covers_authoring_module():
    from civ_mcp.arena.benchmark_contract_v2 import FINGERPRINT_DEPENDENCIES
    assert "src/civ_mcp/arena/benchmark_authoring.py" in FINGERPRINT_DEPENDENCIES
    assert digest_state_v2  # imported for fixture parity
