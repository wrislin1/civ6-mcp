"""Tests for `civ_mcp.arena.benchmark_position` -- the
`civ-arena-benchmark-position capture|verify` CLI.

Every test here spies on the EXACT production callables this module
imports (`deploy_via_windows`, `reload_position`, `dismiss_blocking_popups`,
`capture_canonical_state`) by monkeypatching them as module-level names on
`benchmark_position` -- proving the CLI calls the same production path the
counted runner uses, never a sibling reload loop or a re-implemented state
query.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

import civ_mcp.arena.benchmark_position as benchmark_position
from civ_mcp.arena.benchmark_deploy import DeploymentEvidence
from civ_mcp.arena.benchmark_manifest import PositionManifest
from civ_mcp.arena.benchmark_state import state_digest


class _FakeConnection:
    def __init__(self):
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False


def _deployment_evidence(sha256: str = "a" * 64) -> DeploymentEvidence:
    return DeploymentEvidence(
        ok=True,
        save_name="SOME_SAVE",
        dest_path="C:/saves/SOME_SAVE.Civ6Save",
        archive_sha256=sha256,
        deployed_sha256=sha256,
        expected_sha256=sha256,
        raw={"ok": True},
    )


def _provenance(**overrides) -> dict[str, object]:
    data: dict[str, object] = dict(
        archive="benchmarks/saves/BUILDER_ECONOMY_CAL_V1.Civ6Save",
        archive_sha256="a" * 64,
        game_save_name="BUILDER_ECONOMY_CAL_V1",
        player_id=0,
        relevant_tiles=[[9, 8], [10, 8]],
        game_build="1.2.3.4",
        ruleset="RULESET_STANDARD",
        dlc=[],
        mods=[],
        base_save_identity={"sha256": "b" * 64, "name": "BASE_SAVE"},
        mutation_journal=[],
    )
    data.update(overrides)
    return data


def _position_manifest(**overrides) -> PositionManifest:
    fields = dict(
        position_id="entity-cal-v1",
        version=1,
        archive="benchmarks/saves/ENTITY_CAL_V1.Civ6Save",
        archive_sha256="c" * 64,
        game_save_name="ENTITY_CAL_V1",
        player_id=0,
        expected_state={"turn": 1, "player_id": 0, "units": [], "cities": [], "tiles": []},
        expected_state_sha256="",
        relevant_tiles=((9, 8),),
        objectives=(),
        rubric=(),
        split="calibration",
        persistent_unit_ids=(),
        consumable_unit_ids=(),
    )
    fields.update(overrides)
    return PositionManifest(**fields)


_GOOD_STATE = {"turn": 1, "player_id": 0, "units": [], "cities": [], "tiles": []}
_GOOD_DIGEST = state_digest(_GOOD_STATE)


def _patch_common(monkeypatch, *, deploy=None, reload_fn=None, popups=None, capture=None):
    monkeypatch.setattr(
        benchmark_position, "deploy_via_windows", deploy or (lambda *a, **k: _deployment_evidence())
    )
    monkeypatch.setattr(
        benchmark_position, "reload_position", reload_fn or (lambda conn, pos: _true())
    )
    monkeypatch.setattr(
        benchmark_position, "dismiss_blocking_popups", popups or (lambda conn: _popups_ok())
    )
    monkeypatch.setattr(
        benchmark_position,
        "capture_canonical_state",
        capture or (lambda conn, player_id, tiles: _good_state()),
    )
    monkeypatch.setattr(benchmark_position, "GameConnection", lambda: _FakeConnection())


async def _true():
    return True


async def _popups_ok():
    return "POPUPS|none"


async def _good_state():
    return dict(_GOOD_STATE)


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


async def test_capture_deploys_then_calls_runner_reload_then_capture_state(monkeypatch):
    order: list[str] = []

    def fake_deploy(archive, save_name, sha256):
        order.append("deploy")
        assert archive == "benchmarks/saves/BUILDER_ECONOMY_CAL_V1.Civ6Save"
        assert save_name == "BUILDER_ECONOMY_CAL_V1"
        return _deployment_evidence(sha256)

    async def fake_reload(connection, position):
        order.append("reload")
        assert position.game_save_name == "BUILDER_ECONOMY_CAL_V1"
        return True

    async def fake_popups(connection):
        order.append("popup")
        return "POPUPS|none"

    async def fake_capture(connection, player_id, tiles):
        order.append("capture")
        assert player_id == 0
        assert list(tiles) == [(9, 8), (10, 8)]
        return dict(_GOOD_STATE)

    _patch_common(
        monkeypatch, deploy=fake_deploy, reload_fn=fake_reload, popups=fake_popups, capture=fake_capture
    )

    result = await benchmark_position.capture_position(_provenance())

    assert order == ["deploy", "reload", "popup", "capture"]
    assert result["reload"] == {"verified": True}
    assert result["popup_hygiene"] == {"status": "POPUPS|none"}
    assert result["captured_state"] == _GOOD_STATE
    assert result["captured_state_sha256"] == _GOOD_DIGEST
    assert result["deployment"]["ok"] is True


def test_module_never_imports_end_turn():
    """Neither CLI command advances a turn -- the module must never import
    or reference an end-turn call at all."""
    assert not hasattr(benchmark_position, "end_turn")
    assert "end_turn(" not in Path(benchmark_position.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


async def test_verify_redeploys_on_all_twelve_cycles(monkeypatch):
    position = _position_manifest(expected_state_sha256=_GOOD_DIGEST)
    deploy_calls: list[object] = []
    reload_calls: list[object] = []
    popup_calls: list[object] = []
    capture_calls: list[object] = []

    def fake_deploy(archive, save_name, sha256):
        deploy_calls.append((archive, save_name, sha256))
        return _deployment_evidence(sha256)

    async def fake_reload(connection, pos):
        reload_calls.append(pos.game_save_name)
        return True

    async def fake_popups(connection):
        popup_calls.append(True)
        return "POPUPS|none"

    async def fake_capture(connection, player_id, tiles):
        capture_calls.append(True)
        return dict(_GOOD_STATE)

    _patch_common(
        monkeypatch, deploy=fake_deploy, reload_fn=fake_reload, popups=fake_popups, capture=fake_capture
    )

    result = await benchmark_position.verify_position(position, 12)

    assert result["ok"] is True
    assert result["cycles_completed"] == 12
    assert len(result["digests"]) == 12
    assert all(d == _GOOD_DIGEST for d in result["digests"])
    assert len(deploy_calls) == 12
    assert len(reload_calls) == 12
    assert len(popup_calls) == 12
    assert len(capture_calls) == 12


async def test_verify_aborts_on_first_digest_mismatch(monkeypatch):
    bad_state = {"turn": 2, "player_id": 0, "units": [], "cities": [], "tiles": []}
    position = _position_manifest(expected_state_sha256=_GOOD_DIGEST)
    deploy_calls: list[object] = []
    call_count = {"n": 0}

    def fake_deploy(archive, save_name, sha256):
        deploy_calls.append(True)
        return _deployment_evidence(sha256)

    async def fake_reload(connection, pos):
        return True

    async def fake_popups(connection):
        return "POPUPS|none"

    async def fake_capture(connection, player_id, tiles):
        call_count["n"] += 1
        # First two cycles match; the third diverges.
        return dict(_GOOD_STATE) if call_count["n"] < 3 else dict(bad_state)

    _patch_common(
        monkeypatch, deploy=fake_deploy, reload_fn=fake_reload, popups=fake_popups, capture=fake_capture
    )

    result = await benchmark_position.verify_position(position, 12)

    assert result["ok"] is False
    assert result["mismatch_at_cycle"] == 3
    assert result["cycles_completed"] == 3
    assert len(result["digests"]) == 2  # only the two matching cycles are kept
    # No further deploy/reload/capture cycles ran past the mismatch.
    assert len(deploy_calls) == 3


async def test_verify_calls_shared_popup_hygiene_before_each_capture(monkeypatch):
    position = _position_manifest(expected_state_sha256=_GOOD_DIGEST)
    order: list[str] = []

    def fake_deploy(archive, save_name, sha256):
        order.append("deploy")
        return _deployment_evidence(sha256)

    async def fake_reload(connection, pos):
        order.append("reload")
        return True

    async def fake_popups(connection):
        order.append("popup")
        return "POPUPS|none"

    async def fake_capture(connection, player_id, tiles):
        order.append("capture")
        return dict(_GOOD_STATE)

    _patch_common(
        monkeypatch, deploy=fake_deploy, reload_fn=fake_reload, popups=fake_popups, capture=fake_capture
    )

    result = await benchmark_position.verify_position(position, 12)

    assert result["ok"] is True
    assert order == ["deploy", "reload", "popup", "capture"] * 12


def test_position_cli_requires_exactly_twelve_cycles_for_freeze_mode(tmp_path, monkeypatch):
    position_path = tmp_path / "entity-cal-v1.yaml"
    position_path.write_text(
        yaml.safe_dump(
            {
                "position_id": "entity-cal-v1",
                "version": 1,
                "archive": "benchmarks/saves/ENTITY_CAL_V1.Civ6Save",
                "archive_sha256": "c" * 64,
                "game_save_name": "ENTITY_CAL_V1",
                "player_id": 0,
                "expected_state": _GOOD_STATE,
                "expected_state_sha256": _GOOD_DIGEST,
                "relevant_tiles": [[9, 8]],
                "objectives": [],
                "rubric": [],
                "split": "calibration",
                "persistent_unit_ids": [],
                "consumable_unit_ids": [],
            }
        )
    )
    output_path = tmp_path / "out.json"

    deploy_calls: list[object] = []
    monkeypatch.setattr(
        benchmark_position, "deploy_via_windows", lambda *a, **k: deploy_calls.append(True)
    )

    exit_code = benchmark_position.main(
        [
            "verify",
            "--position", str(position_path),
            "--cycles", "5",
            "--output", str(output_path),
        ]
    )

    assert exit_code != 0
    assert deploy_calls == []  # freeze-mode validation must run before any deploy
    assert not output_path.exists()


def test_position_cli_accepts_exactly_twelve_cycles(tmp_path, monkeypatch):
    position_path = tmp_path / "entity-cal-v1.yaml"
    position_path.write_text(
        yaml.safe_dump(
            {
                "position_id": "entity-cal-v1",
                "version": 1,
                "archive": "benchmarks/saves/ENTITY_CAL_V1.Civ6Save",
                "archive_sha256": "c" * 64,
                "game_save_name": "ENTITY_CAL_V1",
                "player_id": 0,
                "expected_state": _GOOD_STATE,
                "expected_state_sha256": _GOOD_DIGEST,
                "relevant_tiles": [[9, 8]],
                "objectives": [],
                "rubric": [],
                "split": "calibration",
                "persistent_unit_ids": [],
                "consumable_unit_ids": [],
            }
        )
    )
    output_path = tmp_path / "out.json"

    _patch_common(monkeypatch)

    exit_code = benchmark_position.main(
        [
            "verify",
            "--position", str(position_path),
            "--cycles", "12",
            "--output", str(output_path),
        ]
    )

    assert exit_code == 0
    written = json.loads(output_path.read_text())
    assert written["ok"] is True
    assert len(written["digests"]) == 12


# ---------------------------------------------------------------------------
# provenance validation
# ---------------------------------------------------------------------------


def test_capture_cli_rejects_provenance_missing_required_field(tmp_path, capsys):
    provenance = _provenance()
    del provenance["mutation_journal"]
    provenance_path = tmp_path / "authoring.json"
    provenance_path.write_text(json.dumps(provenance))
    output_path = tmp_path / "capture.json"

    exit_code = benchmark_position.main(
        [
            "capture",
            "--authoring-provenance", str(provenance_path),
            "--output", str(output_path),
        ]
    )

    assert exit_code == 1
    assert not output_path.exists()
    err = capsys.readouterr().err
    assert "mutation_journal" in err
