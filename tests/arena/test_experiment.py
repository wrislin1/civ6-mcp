from dataclasses import replace
from pathlib import Path

import pytest

from civ_mcp.arena import live_gate as live_gate_module
from civ_mcp.arena import endpoint_registry
from civ_mcp.arena.config import (
    ChannelOptions,
    ChannelRules,
    ChannelScriptStep,
    CivOptions,
    LiveGateOptions,
    MemoryOptions,
    TaskTrackerOptions,
)
from civ_mcp.arena.experiment import load_experiment
from civ_mcp.arena.live_gate import ScenarioMeta
from civ_mcp.arena.registry import resolve_tools


REPO_ROOT = Path(__file__).resolve().parents[2]
SLICE1_GEMMA_STRATEGY_AB = REPO_ROOT / "experiments" / "gemma-strategy-ab-slice1.yaml"
SLICE3_BEHAVIOR_3LLM = REPO_ROOT / "experiments" / "arena-behavior-3llm-slice3.yaml"
SEAT0_SCRIPTED_SMOKE = REPO_ROOT / "experiments" / "arena-seat0-scripted-smoke.yaml"
SEAT0_LLM_SMOKE = REPO_ROOT / "experiments" / "arena-seat0-llm-smoke.yaml"
CHANNELS_CORE_SMOKE = REPO_ROOT / "experiments" / "arena-channels-core-smoke.yaml"
ARENA_CHANNELS_BEHAVIOR_V1 = REPO_ROOT / "experiments" / "arena-channels-behavior-v1.yaml"
ARENA_CHANNELS_BEHAVIOR_V1B = REPO_ROOT / "experiments" / "arena-channels-behavior-v1b.yaml"
ARENA_CHANNELS_BEHAVIOR_V2 = REPO_ROOT / "experiments" / "arena-channels-behavior-v2.yaml"
ARENA_CHANNELS_BEHAVIOR_V3 = REPO_ROOT / "experiments" / "arena-channels-behavior-v3.yaml"
ARENA_CHANNELS_BEHAVIOR_V4 = REPO_ROOT / "experiments" / "arena-channels-behavior-v4.yaml"
ARENA_CHANNELS_BEHAVIOR_V5 = REPO_ROOT / "experiments" / "arena-channels-behavior-v5.yaml"
ARENA_CHANNELS_BEHAVIOR_V6 = REPO_ROOT / "experiments" / "arena-channels-behavior-v6.yaml"
ARENA_CHANNELS_BEHAVIOR_V7 = REPO_ROOT / "experiments" / "arena-channels-behavior-v7.yaml"

GOOD = """
run_id: exp-1
max_puppet_turns: 80
idle_poll_limit: 3600
gateway_url: http://gw:11444/v1
civs:
  - player: 3
    provider: local
    model: gemma4-26b
    gateway: http://gw:11440/v1
    tools: standard
    result_char_cap: 6000
    max_steps: 10
    playbook: condensed
    context_budget: auto
    briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}
  - player: 1
    provider: cli-claude
    model: ""
"""


def _write(tmp_path, text):
    p = tmp_path / "exp.yaml"
    p.write_text(text)
    return p


def test_explicit_yaml_gateway_does_not_resolve_default(tmp_path, monkeypatch):
    from civ_mcp.arena import experiment as experiment_module

    def unexpected(endpoint_id):
        raise AssertionError(f"resolved default despite explicit YAML: {endpoint_id}")

    monkeypatch.setattr(experiment_module, "resolve_gateway", unexpected)
    cfg = load_experiment(_write(tmp_path, """
gateway_url: http://yaml.example/v1
civs:
  - {player: 3, provider: local, model: m}
"""))
    assert cfg.gateway_url == "http://yaml.example/v1"


def test_yaml_registry_ids_resolve_snapshot_only(tmp_path, monkeypatch):
    monkeypatch.delenv("CIV6_REGISTRY_OFFLINE")
    endpoint_registry._registry.cache_clear()
    monkeypatch.setattr(
        endpoint_registry.brothereye_registry,
        "load",
        lambda **kwargs: pytest.fail("called live loader"),
    )
    path = tmp_path / "experiment.yaml"
    path.write_text(
        "gateway_url: riz-unified-cpp\n"
        "civs:\n"
        "  - player: 1\n"
        "    provider: local\n"
        "    model: gemma4-26b\n"
        "    gateway: riz-gpu0-cpp\n"
    )
    cfg = load_experiment(path)
    assert cfg.gateway_url.endswith(":11444/v1")
    assert cfg.players[0].gateway.endswith(":11440/v1")


def test_omitted_yaml_gateway_is_snapshot_only(tmp_path, monkeypatch):
    monkeypatch.delenv("CIV6_REGISTRY_OFFLINE")
    endpoint_registry._registry.cache_clear()
    monkeypatch.setattr(
        endpoint_registry.brothereye_registry,
        "load",
        lambda **kwargs: pytest.fail("called live loader"),
    )
    path = tmp_path / "experiment.yaml"
    path.write_text("civs:\n  - player: 1\n    provider: scripted\n")
    assert load_experiment(path).gateway_url.endswith(":11444/v1")


def test_loads_gemma_strategy_ab_slice1_artifact():
    cfg = load_experiment(SLICE1_GEMMA_STRATEGY_AB)

    assert cfg.run_id == ""
    assert cfg.max_puppet_turns == 140
    assert cfg.idle_poll_limit == 3600
    assert cfg.puppet_ids == [1, 2, 3, 4, 5, 6, 7]

    by_player = {player.player_id: player for player in cfg.players}
    assert set(by_player) == {1, 2, 3, 4, 5, 6, 7}

    gateway = "http://192.168.20.196:11440/v1"
    treatment_sections = (
        "promotions",
        "overview",
        "units",
        "cities",
        "map",
        "research",
        "production_options",
        "threats",
        "rivals",
        "empire_resources",
    )

    for player_id in (1, 3, 5, 7):
        player = by_player[player_id]
        assert player.provider == "local"
        assert player.model == "gemma4-26b"
        assert player.gateway == gateway
        assert player.options.tools == "full"
        assert player.options.result_char_cap == 6000
        assert player.options.max_steps == 10
        assert player.options.playbook == "condensed"
        assert player.options.context_budget == "auto"
        assert player.options.briefing.enabled is True
        assert player.options.briefing.map_radius == 3
        assert player.options.briefing.sections == treatment_sections
        assert "victory" not in player.options.briefing.sections

    for player_id in (2, 4, 6):
        player = by_player[player_id]
        assert player.provider == "local"
        assert player.model == "gemma4-26b"
        assert player.gateway == gateway
        assert player.options.tools == "minimal"
        assert player.options.result_char_cap == 1500
        assert player.options.max_steps == 6
        assert player.options.playbook == "none"
        assert player.options.context_budget == "auto"
        assert player.options.briefing.enabled is False


def test_loads_arena_channels_behavior_v1_artifact():
    cfg = load_experiment(ARENA_CHANNELS_BEHAVIOR_V1)

    assert cfg.run_id == "arena-channels-behavior-v1"
    assert cfg.max_puppet_turns == 30
    assert cfg.max_game_turns == 36
    assert cfg.live_gate == LiveGateOptions()
    assert cfg.channel_rules.acceptance_turns == 3
    assert cfg.channel_rules.funding_turns == 2
    assert cfg.channel_rules.payment_response_turns == 2

    by_player = {player.player_id: player for player in cfg.players}
    assert set(by_player) == {1, 2, 3}

    p1 = by_player[1]
    assert p1.provider == "local"
    assert p1.model == "gemma4-26b"
    assert p1.gateway == "http://192.168.20.196:11440/v1"
    assert p1.options.tools == "minimal"
    assert p1.options.max_steps == 10
    assert p1.options.channels.enabled is True
    assert p1.options.channels.guidance is True

    p2 = by_player[2]
    assert p2.provider == "local"
    assert p2.model == "qwen3.6-27b"
    assert p2.gateway == "http://192.168.20.196:11441/v1"
    assert p2.options.tools == "minimal"
    assert p2.options.max_steps == 10
    assert p2.options.channels.enabled is True
    assert p2.options.channels.guidance is True

    p3 = by_player[3]
    assert p3.provider == "scripted"
    assert p3.options.channels.enabled is True
    assert p3.options.channels.guidance is False


def test_loads_arena_channels_behavior_v1b_artifact():
    cfg = load_experiment(ARENA_CHANNELS_BEHAVIOR_V1B)

    assert cfg.run_id == "arena-channels-behavior-v1b"
    assert cfg.max_puppet_turns == 30
    assert cfg.max_game_turns == 36
    assert cfg.live_gate == LiveGateOptions()
    assert cfg.channel_rules.acceptance_turns == 3
    assert cfg.channel_rules.funding_turns == 2
    assert cfg.channel_rules.payment_response_turns == 2

    by_player = {player.player_id: player for player in cfg.players}
    assert set(by_player) == {1, 2, 3}

    p1 = by_player[1]
    assert p1.provider == "local"
    assert p1.model == "gemma4-26b"
    assert p1.gateway == "http://192.168.20.196:11440/v1"
    assert p1.options.tools == "minimal"
    assert p1.options.max_steps == 15
    assert p1.options.channels.enabled is True
    assert p1.options.channels.guidance is True

    p2 = by_player[2]
    assert p2.provider == "local"
    assert p2.model == "qwen3.6-27b"
    assert p2.gateway == "http://192.168.20.196:11441/v1"
    assert p2.options.tools == "minimal"
    assert p2.options.max_steps == 15
    assert p2.options.channels.enabled is True
    assert p2.options.channels.guidance is True

    p3 = by_player[3]
    assert p3.provider == "scripted"
    assert p3.options.channels.enabled is True
    assert p3.options.channels.guidance is False


def test_loads_arena_channels_behavior_v2_artifact():
    cfg = load_experiment(ARENA_CHANNELS_BEHAVIOR_V2)

    assert cfg.run_id == "arena-channels-behavior-v2"
    assert cfg.max_puppet_turns == 90
    assert cfg.max_game_turns == 108
    assert cfg.live_gate == LiveGateOptions()
    assert cfg.channel_rules.acceptance_turns == 3
    assert cfg.channel_rules.funding_turns == 2
    assert cfg.channel_rules.payment_response_turns == 2

    by_player = {player.player_id: player for player in cfg.players}
    assert set(by_player) == {1, 2, 3}

    p1 = by_player[1]
    assert p1.provider == "local"
    assert p1.model == "gemma4-26b"
    assert p1.gateway == "http://192.168.20.196:11440/v1"
    assert p1.options.tools == "minimal"
    assert p1.options.max_steps == 15
    assert p1.options.channels.enabled is True
    assert p1.options.channels.guidance is True

    p2 = by_player[2]
    assert p2.provider == "local"
    assert p2.model == "qwen3.6-27b"
    assert p2.gateway == "http://192.168.20.196:11441/v1"
    assert p2.options.tools == "minimal"
    assert p2.options.max_steps == 15
    assert p2.options.channels.enabled is True
    assert p2.options.channels.guidance is True

    p3 = by_player[3]
    assert p3.provider == "scripted"
    assert p3.options.channels.enabled is True
    assert p3.options.channels.guidance is False


def test_loads_arena_channels_behavior_v3_artifact():
    cfg = load_experiment(ARENA_CHANNELS_BEHAVIOR_V3)

    assert cfg.run_id == "arena-channels-behavior-v3"
    assert cfg.max_puppet_turns == 90
    assert cfg.max_game_turns == 108
    assert cfg.channel_rules.acceptance_turns == 3
    assert cfg.channel_rules.funding_turns == 2
    assert cfg.channel_rules.payment_response_turns == 2
    assert cfg.live_gate == LiveGateOptions()

    by_player = {player.player_id: player for player in cfg.players}
    assert set(by_player) == {1, 2, 3}

    assert by_player[1].provider == "local"
    assert by_player[1].model == "gemma4-26b"
    assert by_player[1].gateway == "http://192.168.20.196:11440/v1"
    assert by_player[1].options.tools == "minimal"
    assert by_player[1].options.max_steps == 15
    assert by_player[1].options.channels.enabled is True
    assert by_player[1].options.channels.guidance is True
    assert by_player[1].options.channels.script == ()

    assert by_player[2].provider == "local"
    assert by_player[2].model == "qwen3.6-27b"
    assert by_player[2].gateway == "http://192.168.20.196:11441/v1"
    assert by_player[2].options.tools == "minimal"
    assert by_player[2].options.max_steps == 15
    assert by_player[2].options.channels.enabled is True
    assert by_player[2].options.channels.guidance is True
    assert by_player[2].options.channels.script == ()

    script = by_player[3].options.channels.script
    assert by_player[3].provider == "scripted"
    assert by_player[3].options.channels.enabled is True
    assert by_player[3].options.channels.guidance is False
    assert tuple(step.action for step in script) == ("propose_deal", "propose_deal")
    assert tuple(step.turn for step in script) == (157, 157)
    assert tuple(step.args["to_player"] for step in script) == (1, 2)
    assert all(step.args["payment_gold"] == 50 for step in script)
    assert all(step.args["timing"] == "on_delivery" for step in script)
    assert all(step.args["within"] == 5 for step in script)
    assert all(
        step.args["favor"] == {
            "term_type": "keep_units_away",
            "params": {"player_id": 3, "min_distance": 3, "unit_scope": "military"},
        }
        for step in script
    )
    # Both propose_deal steps must show identical text to both LLM seats, or
    # the two seats receive different treatments and the experiment is
    # confounded. Pin sameness, not the literal string.
    texts = {step.args["text"] for step in script}
    assert len(texts) == 1
    assert next(iter(texts)) != ""


def test_arena_channels_behavior_v4_differs_from_v3_only_in_run_id():
    # v4 isolates the guidance-text fix: the config must be byte-equivalent to
    # v3 apart from run_id, so the revised CHANNEL_GUIDANCE_TEXT is the single
    # changed variable between the two runs.
    v3 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V3)
    v4 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V4)

    assert v4.run_id == "arena-channels-behavior-v4"
    assert v3.run_id == "arena-channels-behavior-v3"
    assert replace(v4, run_id=v3.run_id) == v3


def test_arena_channels_behavior_v5_differs_from_v4_only_in_run_id_and_auto_accept():
    # v5 isolates the projection/affordance change: the only config delta is
    # P3's auto_accept, which exists so LLM-initiated deals aimed at the
    # scripted seat can reach a terminal state instead of expiring (v4
    # deal-000003). Roster, budgets, and the P3 script stay at the baseline.
    v4 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V4)
    v5 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V5)

    assert v5.run_id == "arena-channels-behavior-v5"
    by_player = {player.player_id: player for player in v5.players}
    assert by_player[3].options.channels.auto_accept is True
    assert by_player[1].options.channels.auto_accept is False
    assert by_player[2].options.channels.auto_accept is False

    normalized = [
        replace(
            player,
            options=replace(
                player.options,
                channels=replace(player.options.channels, auto_accept=False),
            ),
        )
        for player in v5.players
    ]
    assert replace(v5, run_id=v4.run_id, players=normalized) == v4


SEAT0_SCRIPTED_BLOCK = (
    b"  - player: 0\n"
    b"    provider: scripted\n"
    b"    model: seat0-smoke\n"
    b'    attention: {mode: "off"}\n'
)


def test_arena_channels_behavior_v6_differs_from_v5_only_in_run_id_seat0_and_budget():
    # v6 isolates the schema-suffix fix, so the LLM seats and the P3 script
    # must be byte-identical to v5. Two operator deltas are allowed on top:
    # a scripted seat-0 pilot (so the run is hands-free; the human civ's play
    # is irrelevant to the channels question) and the turn budgets scaled by
    # 4/3, because seat 0 charges the shared puppet budget -- 120 slots over
    # four seats is the same 30-game-turn window v5 had with 90 over three.
    v5_bytes = ARENA_CHANNELS_BEHAVIOR_V5.read_bytes()
    old_run_id = b"run_id: arena-channels-behavior-v5\n"
    new_run_id = b"run_id: arena-channels-behavior-v6\n"

    assert v5_bytes.count(old_run_id) == 1
    assert v5_bytes.count(b"max_puppet_turns: 90\n") == 1
    assert v5_bytes.count(b"max_game_turns: 108\n") == 1
    assert v5_bytes.count(b"civs:\n") == 1
    expected = (
        v5_bytes.replace(old_run_id, new_run_id, 1)
        .replace(b"max_puppet_turns: 90\n", b"max_puppet_turns: 120\n", 1)
        .replace(b"max_game_turns: 108\n", b"max_game_turns: 144\n", 1)
        .replace(b"civs:\n", b"civs:\n" + SEAT0_SCRIPTED_BLOCK, 1)
    )
    assert ARENA_CHANNELS_BEHAVIOR_V6.read_bytes() == expected

    v5 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V5)
    v6 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V6)

    assert v6.run_id == "arena-channels-behavior-v6"
    assert v6.max_puppet_turns == 120
    assert v6.max_game_turns == 144
    seat0 = next(player for player in v6.players if player.player_id == 0)
    assert seat0.provider == "scripted"
    assert seat0.model == "seat0-smoke"
    assert seat0.options.attention.mode == "off"
    assert seat0.options.channels.enabled is False

    without_seat0 = [player for player in v6.players if player.player_id != 0]
    assert replace(
        v6,
        run_id=v5.run_id,
        max_puppet_turns=v5.max_puppet_turns,
        max_game_turns=v5.max_game_turns,
        players=without_seat0,
    ) == v5


def test_arena_channels_behavior_v7_is_tracker_only_delta_from_v6():
    v6 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V6)
    v7 = load_experiment(ARENA_CHANNELS_BEHAVIOR_V7)
    by_player = {player.player_id: player for player in v7.players}

    assert v7.run_id == "arena-channels-behavior-v7"
    assert by_player[1].options.tools == "minimal"
    assert by_player[2].options.tools == "minimal"
    assert by_player[1].options.task_tracker.enabled is True
    assert by_player[2].options.task_tracker.enabled is True
    assert by_player[3].options.task_tracker.enabled is False

    normalized_players = [
        replace(
            player,
            options=replace(
                player.options,
                task_tracker=replace(
                    player.options.task_tracker,
                    enabled=False,
                ),
            ),
        )
        if player.player_id in {1, 2}
        else player
        for player in v7.players
    ]
    assert replace(
        v7,
        run_id=v6.run_id,
        players=normalized_players,
    ) == v6


def test_rejects_auto_accept_without_channels_enabled(tmp_path):
    path = tmp_path / "bad-auto-accept.yaml"
    path.write_text(
        "civs:\n"
        "  - player: 3\n"
        "    provider: scripted\n"
        "    channels: {enabled: false, auto_accept: true}\n"
    )
    with pytest.raises(ValueError, match="channels.auto_accept requires channels.enabled true"):
        load_experiment(path)


def test_rejects_non_boolean_auto_accept(tmp_path):
    path = tmp_path / "bad-auto-accept-type.yaml"
    path.write_text(
        "civs:\n"
        "  - player: 3\n"
        "    provider: scripted\n"
        "    channels: {enabled: true, auto_accept: yes-please}\n"
    )
    with pytest.raises(ValueError, match="channels.auto_accept must be a boolean"):
        load_experiment(path)


def test_slice1_treatment_full_tier_has_diplomacy_tools_and_control_does_not():
    cfg = load_experiment(SLICE1_GEMMA_STRATEGY_AB)
    by_player = {player.player_id: player for player in cfg.players}
    diplomacy_tools = {
        "get_pending_diplomacy",
        "respond_to_diplomacy",
        "get_pending_trades",
        "respond_to_trade",
        "get_trade_options",
        "propose_trade",
        "propose_peace",
        "send_diplomatic_action",
        "form_alliance",
    }

    # Full-LLM-control split (riz 2026-07-15): the reactive inspect/answer
    # pair now lives in every tier (an unanswered incoming deal wedges the
    # whole game), so the A/B contrast is on PROACTIVE diplomacy only.
    proactive_diplomacy = diplomacy_tools - {
        "get_pending_diplomacy",
        "respond_to_diplomacy",
        "get_pending_trades",
        "respond_to_trade",
    }

    for player_id in (1, 3, 5, 7):
        assert diplomacy_tools <= set(resolve_tools(by_player[player_id].options.tools))

    for player_id in (2, 4, 6):
        assert proactive_diplomacy.isdisjoint(
            set(resolve_tools(by_player[player_id].options.tools))
        )


def test_loads_arena_behavior_3llm_slice3_artifact():
    assert SLICE3_BEHAVIOR_3LLM.exists(), f"missing fixture: {SLICE3_BEHAVIOR_3LLM}"

    cfg = load_experiment(SLICE3_BEHAVIOR_3LLM)

    assert len(cfg.players) == 3
    assert [player.player_id for player in cfg.players] == [1, 3, 5]

    for player in cfg.players:
        assert player.options.memory.enabled is True
        assert player.options.task_tracker.enabled is True
        assert player.options.briefing.enabled is True
        assert "great_people" in player.options.briefing.sections


def test_playbook_covers_promotions_and_expansion_doctrine():
    text = (REPO_ROOT / "src" / "civ_mcp" / "arena" / "playbook.md").read_text()

    for header in ("## Unit promotions", "## Unit upgrades", "## Signals to watch"):
        assert header in text
    assert "promotions briefing appears" in text
    assert "get_unit_promotions(unit_id).promotions" in text


def test_playbook_covers_diplomacy_trade_and_peace_doctrine():
    text = (REPO_ROOT / "src" / "civ_mcp" / "arena" / "playbook.md").read_text()

    assert "## Diplomacy, trades, and peace" in text
    assert "get_pending_diplomacy" in text
    assert "respond_to_diplomacy" in text
    assert "get_pending_trades" in text
    assert "respond_to_trade" in text
    assert "get_trade_options" in text
    assert "propose_trade" in text
    assert "propose_peace" in text
    assert "form_alliance" in text
    assert "send_diplomatic_action" in text
    assert "DIPLOMATIC_DELEGATION" in text
    assert "DECLARE_FRIENDSHIP" in text
    assert "RESIDENT_EMBASSY" in text
    assert "DECLARE_SURPRISE_WAR" in text


def test_load_good(tmp_path):
    cfg = load_experiment(_write(tmp_path, GOOD))
    assert cfg.run_id == "exp-1" and cfg.max_puppet_turns == 80
    assert cfg.gateway_url == "http://gw:11444/v1"
    assert cfg.puppet_ids == [3, 1]
    local = cfg.players[0]
    assert local.gateway == "http://gw:11440/v1"
    assert local.options.tools == "standard"
    assert local.options.max_steps == 10
    assert local.options.briefing.enabled and local.options.briefing.map_radius == 4
    assert local.options.briefing.sections == ("overview", "units", "map")
    cli = cfg.players[1]
    assert cli.provider == "cli-claude" and cli.options == CivOptions()


def test_briefing_accepts_great_people_section(tmp_path):
    text = GOOD.replace(
        "sections: [overview, units, map]",
        "sections: [overview, units, map, great_people]",
    )

    cfg = load_experiment(_write(tmp_path, text))

    assert cfg.players[0].options.briefing.sections == (
        "overview",
        "units",
        "map",
        "great_people",
    )


def test_non_empty_briefing_block_defaults_enabled_true(tmp_path):
    text = GOOD.replace(
        "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}",
        "briefing: {map_radius: 4, sections: [overview, map, rivals]}",
    )

    cfg = load_experiment(_write(tmp_path, text))
    briefing = cfg.players[0].options.briefing

    assert briefing.enabled is True
    assert briefing.map_radius == 4
    assert briefing.sections == ("overview", "map", "rivals")


def test_briefing_block_explicit_enabled_false_stays_disabled(tmp_path):
    text = GOOD.replace(
        "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}",
        "briefing: {enabled: false, map_radius: 4, sections: [overview, map]}",
    )

    cfg = load_experiment(_write(tmp_path, text))
    briefing = cfg.players[0].options.briefing

    assert briefing.enabled is False
    assert briefing.map_radius == 4
    assert briefing.sections == ("overview", "map")


def test_load_experiment_uses_supplied_defaults_for_omitted_run_controls(tmp_path):
    from civ_mcp.arena.config import ArenaConfig

    p = _write(
        tmp_path,
        """
civs:
  - player: 3
    provider: local
    model: gemma4-26b
""",
    )
    cfg = load_experiment(
        p,
        defaults=ArenaConfig(
            players=[],
            max_puppet_turns=8,
            idle_poll_limit=3600,
            gateway_url="http://launcher.example/v1",
        ),
    )

    assert cfg.max_puppet_turns == 8
    assert cfg.idle_poll_limit == 3600
    assert cfg.gateway_url == "http://launcher.example/v1"


def test_load_experiment_preserves_all_supplied_defaults_for_omitted_arena_fields(tmp_path):
    from civ_mcp.arena.config import ArenaConfig

    p = _write(
        tmp_path,
        """
civs:
  - player: 3
    provider: local
    model: gemma4-26b
""",
    )

    cfg = load_experiment(
        p,
        defaults=ArenaConfig(
            players=[],
            max_puppet_turns=8,
            gateway_url="http://launcher.example/v1",
            api_key_env="LOCAL_ARENA_KEY",
            dry_run=True,
            max_agent_steps=3,
            idle_poll_limit=3600,
            cost_path="custom-cost.jsonl",
            run_id="default-run",
            transcript_dir="custom-runs",
        ),
    )

    assert [p.player_id for p in cfg.players] == [3]
    assert cfg.puppet_ids == [3]
    assert cfg.max_puppet_turns == 8
    assert cfg.gateway_url == "http://launcher.example/v1"
    assert cfg.api_key_env == "LOCAL_ARENA_KEY"
    assert cfg.dry_run is True
    assert cfg.max_agent_steps == 3
    assert cfg.idle_poll_limit == 3600
    assert cfg.cost_path == "custom-cost.jsonl"
    assert cfg.run_id == "default-run"
    assert cfg.transcript_dir == "custom-runs"


def test_load_experiment_yaml_values_override_supplied_defaults(tmp_path):
    from civ_mcp.arena.config import ArenaConfig

    p = _write(
        tmp_path,
        """
max_puppet_turns: 12
idle_poll_limit: 7200
gateway_url: http://yaml.example/v1
civs:
  - player: 3
    provider: local
    model: gemma4-26b
""",
    )
    cfg = load_experiment(
        p,
        defaults=ArenaConfig(
            players=[],
            max_puppet_turns=8,
            idle_poll_limit=3600,
            gateway_url="http://launcher.example/v1",
        ),
    )

    assert cfg.max_puppet_turns == 12
    assert cfg.idle_poll_limit == 7200
    assert cfg.gateway_url == "http://yaml.example/v1"


def test_rejects_duplicate_players(tmp_path):
    bad = GOOD.replace("player: 1", "player: 3")
    with pytest.raises(ValueError, match="duplicate"):
        load_experiment(_write(tmp_path, bad))


def test_rejects_empty_civ_list(tmp_path):
    with pytest.raises(ValueError, match=r"civs.*at least one"):
        load_experiment(_write(tmp_path, "civs: []\n"))


def test_rejects_unknown_tier(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*tools"):
        load_experiment(_write(tmp_path, GOOD.replace("tools: standard", "tools: mega")))


def test_rejects_unknown_tool_name_in_list(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*tools"):
        load_experiment(
            _write(
                tmp_path,
                GOOD.replace("tools: standard", "tools: [get_units, launch_nuke]"),
            )
        )


def test_rejects_unknown_section(tmp_path):
    with pytest.raises(ValueError, match="player 3"):
        load_experiment(
            _write(
                tmp_path,
                GOOD.replace("[overview, units, map]", "[overview, minimap]"),
            )
        )


def test_rejects_local_knobs_on_cli_civ(tmp_path):
    bad = GOOD + "    max_steps: 9\n"
    with pytest.raises(ValueError, match="cli-claude"):
        load_experiment(_write(tmp_path, bad))


def test_explicit_tool_list(tmp_path):
    cfg = load_experiment(
        _write(tmp_path, GOOD.replace("tools: standard", "tools: [get_units, move_unit]"))
    )
    assert cfg.players[0].options.tools == ("get_units", "move_unit")


def test_rejects_missing_player_key(tmp_path):
    with pytest.raises(ValueError, match="player"):
        load_experiment(_write(tmp_path, "civs:\n  - {provider: local, model: m}\n"))


def test_rejects_missing_local_model(tmp_path):
    text = """
civs:
  - player: 3
    provider: local
"""
    with pytest.raises(ValueError, match=r"player 3.*model"):
        load_experiment(_write(tmp_path, text))


def test_rejects_empty_local_model(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*model"):
        load_experiment(_write(tmp_path, GOOD.replace("model: gemma4-26b", 'model: ""')))


def test_rejects_whitespace_local_model(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*model"):
        load_experiment(_write(tmp_path, GOOD.replace("model: gemma4-26b", 'model: "   "')))


def test_rejects_surrounding_whitespace_local_model(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*model"):
        load_experiment(_write(tmp_path, GOOD.replace("model: gemma4-26b", 'model: " gemma4-26b "')))


@pytest.mark.parametrize(
    ("good", "bad", "field"),
    [
        ("max_steps: 10", "max_steps: nope", "max_steps"),
        ("context_budget: auto", "context_budget: nope", "context_budget"),
        ("map_radius: 4", "map_radius: nope", "briefing.map_radius"),
    ],
)
def test_rejects_malformed_ints_with_civ_named(tmp_path, good, bad, field):
    # bare int() would raise "invalid literal..." without naming the civ or field
    with pytest.raises(ValueError, match=f"player 3.*{field}"):
        load_experiment(_write(tmp_path, GOOD.replace(good, bad)))


def test_rejects_out_of_range_map_radius(tmp_path):
    with pytest.raises(ValueError, match="map_radius must be 0..5"):
        load_experiment(_write(tmp_path, GOOD.replace("map_radius: 4", "map_radius: 9")))


def test_rejects_non_positive_result_char_cap_with_civ_named(tmp_path):
    with pytest.raises(ValueError, match=r"player 3: result_char_cap must be positive$"):
        load_experiment(_write(tmp_path, GOOD.replace("result_char_cap: 6000", "result_char_cap: 0")))


def test_rejects_non_positive_max_steps_with_civ_named(tmp_path):
    with pytest.raises(ValueError, match=r"player 3: max_steps must be positive$"):
        load_experiment(_write(tmp_path, GOOD.replace("max_steps: 10", "max_steps: 0")))


def test_rejects_boolean_player_id(tmp_path):
    with pytest.raises(ValueError, match=r"player .*player"):
        load_experiment(_write(tmp_path, GOOD.replace("player: 3", "player: true", 1)))


def test_rejects_boolean_max_steps(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*max_steps"):
        load_experiment(_write(tmp_path, GOOD.replace("max_steps: 10", "max_steps: true")))


def test_rejects_boolean_context_budget(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*context_budget"):
        load_experiment(_write(tmp_path, GOOD.replace("context_budget: auto", "context_budget: true")))


@pytest.mark.parametrize("field", ["max_puppet_turns", "idle_poll_limit"])
def test_rejects_boolean_top_level_ints(tmp_path, field):
    with pytest.raises(ValueError, match=field):
        load_experiment(_write(tmp_path, GOOD.replace(f"{field}: ", f"{field}: true # ")))


@pytest.mark.parametrize("field", ["max_puppet_turns", "idle_poll_limit"])
def test_rejects_null_top_level_ints(tmp_path, field):
    with pytest.raises(ValueError, match=field):
        load_experiment(_write(tmp_path, GOOD.replace(f"{field}: ", f"{field}: null # ")))


@pytest.mark.parametrize("bad", ["briefing: []", "briefing: false"])
def test_rejects_non_mapping_briefing(tmp_path, bad):
    with pytest.raises(ValueError, match=r"player 3.*briefing"):
        load_experiment(
            _write(
                tmp_path,
                GOOD.replace(
                    "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}",
                    bad,
                ),
            )
        )


def test_rejects_null_briefing(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*briefing"):
        load_experiment(
            _write(
                tmp_path,
                GOOD.replace(
                    "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}",
                    "briefing: null",
                ),
            )
        )


def test_rejects_non_boolean_briefing_enabled(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*briefing.enabled"):
        load_experiment(_write(tmp_path, GOOD.replace("enabled: true", 'enabled: "false"')))


@pytest.mark.parametrize("bad", ["sections: overview", "sections: [overview, 2]"])
def test_rejects_bad_briefing_sections_shape(tmp_path, bad):
    with pytest.raises(ValueError, match=r"player 3.*briefing.sections"):
        load_experiment(
            _write(
                tmp_path,
                GOOD.replace("sections: [overview, units, map]", bad),
            )
        )


def test_rejects_non_string_or_sequence_tools(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*tools"):
        load_experiment(_write(tmp_path, GOOD.replace("tools: standard", "tools: 5")))


@pytest.mark.parametrize(
    ("good", "bad", "field"),
    [
        ("max_steps: 10", "max_steps: 1.5", "max_steps"),
        ("context_budget: auto", "context_budget: 1.5", "context_budget"),
        ("result_char_cap: 6000", "result_char_cap: 1.5", "result_char_cap"),
        ("map_radius: 4", "map_radius: 1.5", "briefing.map_radius"),
    ],
)
def test_rejects_floats_for_civ_int_fields(tmp_path, good, bad, field):
    with pytest.raises(ValueError, match=rf"player 3.*{field}"):
        load_experiment(_write(tmp_path, GOOD.replace(good, bad)))


@pytest.mark.parametrize(
    ("good", "bad", "field"),
    [
        ("max_steps: 10", "max_steps: null", "max_steps"),
        ("context_budget: auto", "context_budget: null", "context_budget"),
        ("result_char_cap: 6000", "result_char_cap: null", "result_char_cap"),
        ("map_radius: 4", "map_radius: null", "briefing.map_radius"),
    ],
)
def test_rejects_nulls_for_civ_int_fields(tmp_path, good, bad, field):
    with pytest.raises(ValueError, match=rf"player 3.*{field}"):
        load_experiment(_write(tmp_path, GOOD.replace(good, bad)))


@pytest.mark.parametrize("field,bad", [("max_puppet_turns", "2.7"), ("idle_poll_limit", "2.7")])
def test_rejects_floats_for_top_level_int_fields(tmp_path, field, bad):
    with pytest.raises(ValueError, match=field):
        load_experiment(_write(tmp_path, GOOD.replace(f"{field}: ", f"{field}: {bad} # ")))


@pytest.mark.parametrize(
    ("good", "bad", "field"),
    [
        ("max_steps: 10", 'max_steps: "10"', "max_steps"),
        ("context_budget: auto", 'context_budget: "10"', "context_budget"),
        ("result_char_cap: 6000", 'result_char_cap: "6000"', "result_char_cap"),
    ],
)
def test_rejects_quoted_numeric_strings_for_civ_int_fields(tmp_path, good, bad, field):
    with pytest.raises(ValueError, match=rf"player 3.*{field}"):
        load_experiment(_write(tmp_path, GOOD.replace(good, bad)))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("max_puppet_turns", '"80"'),
        ("idle_poll_limit", '"3600"'),
    ],
)
def test_rejects_quoted_numeric_strings_for_top_level_int_fields(tmp_path, field, bad):
    with pytest.raises(ValueError, match=field):
        load_experiment(_write(tmp_path, GOOD.replace(f"{field}: ", f"{field}: {bad} # ")))


def test_rejects_float_player_id(tmp_path):
    with pytest.raises(ValueError, match="player"):
        load_experiment(_write(tmp_path, GOOD.replace("player: 3", "player: 3.5", 1)))


def test_rejects_gateway_on_cli_civ(tmp_path):
    bad = GOOD + "    gateway: http://gw:11441/v1\n"
    with pytest.raises(ValueError, match=r"(cli-claude.*gateway|player 1.*gateway)"):
        load_experiment(_write(tmp_path, bad))


def test_rejects_non_string_model(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*model"):
        load_experiment(_write(tmp_path, GOOD.replace("model: gemma4-26b", "model: 123")))


def test_rejects_non_string_gateway(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*gateway"):
        load_experiment(_write(tmp_path, GOOD.replace("gateway: http://gw:11440/v1", "gateway: 123")))


def test_rejects_whitespace_local_gateway(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*gateway"):
        load_experiment(_write(tmp_path, GOOD.replace("gateway: http://gw:11440/v1", 'gateway: "   "')))


def test_rejects_surrounding_whitespace_local_gateway(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*gateway"):
        load_experiment(_write(tmp_path, GOOD.replace("gateway: http://gw:11440/v1", 'gateway: " http://gw:11440/v1 "')))


def test_rejects_non_string_gateway_url(tmp_path):
    with pytest.raises(ValueError, match="gateway_url"):
        load_experiment(_write(tmp_path, GOOD.replace("gateway_url: http://gw:11444/v1", "gateway_url: [a, b]")))


@pytest.mark.parametrize("bad", ['gateway_url: ""', 'gateway_url: "   "'])
def test_rejects_blank_gateway_url(tmp_path, bad):
    with pytest.raises(ValueError, match="gateway_url"):
        load_experiment(_write(tmp_path, GOOD.replace("gateway_url: http://gw:11444/v1", bad)))


def test_rejects_surrounding_whitespace_gateway_url(tmp_path):
    with pytest.raises(ValueError, match="gateway_url"):
        load_experiment(_write(tmp_path, GOOD.replace("gateway_url: http://gw:11444/v1", 'gateway_url: " http://gw:11444/v1 "')))


def test_rejects_non_string_run_id(tmp_path):
    with pytest.raises(ValueError, match="run_id"):
        load_experiment(_write(tmp_path, GOOD.replace("run_id: exp-1", "run_id: [a, b]")))


@pytest.mark.parametrize(
    "bad",
    [
        'run_id: ""',
        'run_id: "   "',
        "run_id: ../outside",
        "run_id: nested/path",
        r"run_id: nested\path",
        "run_id: bad id",
        "run_id: .",
        "run_id: ..",
    ],
)
def test_rejects_unsafe_run_id_values(tmp_path, bad):
    with pytest.raises(ValueError, match="run_id"):
        load_experiment(_write(tmp_path, GOOD.replace("run_id: exp-1", bad)))


@pytest.mark.parametrize("run_id", ["exp-1", "exp_1", "EXP.20260704T000000Z"])
def test_accepts_safe_run_id_values(tmp_path, run_id):
    cfg = load_experiment(_write(tmp_path, GOOD.replace("run_id: exp-1", f"run_id: {run_id}")))
    assert cfg.run_id == run_id


def test_rejects_non_string_unknown_top_level_key(tmp_path):
    with pytest.raises(ValueError, match=r"experiment config: .*top-level"):
        load_experiment(_write(tmp_path, "5: x\nfoo: y\ncivs: []\n"))


def test_rejects_non_string_unknown_civ_key(tmp_path):
    bad = """
civs:
  - player: 3
    provider: local
    model: gemma4-26b
    5: x
    foo: y
"""
    with pytest.raises(ValueError, match=r"player 3"):
        load_experiment(_write(tmp_path, bad))


def test_rejects_non_string_unknown_briefing_key(tmp_path):
    bad = GOOD.replace(
        "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}",
        "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map], 5: x, foo: y}",
    )
    with pytest.raises(ValueError, match=r"player 3.*briefing"):
        load_experiment(_write(tmp_path, bad))


def test_rejects_null_provider(tmp_path):
    with pytest.raises(ValueError, match=r"player .*provider"):
        load_experiment(_write(tmp_path, GOOD.replace("provider: local", "provider: null", 1)))


def test_rejects_null_model(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*model"):
        load_experiment(_write(tmp_path, GOOD.replace("model: gemma4-26b", "model: null")))


def test_rejects_null_gateway(tmp_path):
    with pytest.raises(ValueError, match=r"player 3.*gateway"):
        load_experiment(_write(tmp_path, GOOD.replace("gateway: http://gw:11440/v1", "gateway: null")))


def test_rejects_null_gateway_url(tmp_path):
    with pytest.raises(ValueError, match="gateway_url"):
        load_experiment(_write(tmp_path, GOOD.replace("gateway_url: http://gw:11444/v1", "gateway_url: null")))


def test_rejects_null_run_id(tmp_path):
    with pytest.raises(ValueError, match="run_id"):
        load_experiment(_write(tmp_path, GOOD.replace("run_id: exp-1", "run_id: null")))


def test_rejects_invalid_yaml_with_config_context(tmp_path):
    with pytest.raises(ValueError, match=r"experiment config .*invalid YAML"):
        load_experiment(_write(tmp_path, "civs:\n  - player: [\n"))


def test_rejects_duplicate_top_level_key(tmp_path):
    text = """
run_id: first
run_id: second
civs:
  - player: 3
    provider: local
    model: gemma4-26b
"""
    with pytest.raises(ValueError, match=r"experiment config .*duplicate"):
        load_experiment(_write(tmp_path, text))


def test_rejects_duplicate_civ_key(tmp_path):
    text = """
civs:
  - player: 3
    provider: local
    model: gemma4-26b
    model: qwen
"""
    with pytest.raises(ValueError, match=r"experiment config .*duplicate"):
        load_experiment(_write(tmp_path, text))


def test_rejects_duplicate_briefing_key(tmp_path):
    text = GOOD.replace(
        "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}",
        "briefing: {enabled: true, enabled: false, map_radius: 4, sections: [overview, units, map]}",
    )
    with pytest.raises(ValueError, match=r"experiment config .*duplicate"):
        load_experiment(_write(tmp_path, text))


def test_rejects_missing_file_with_config_context(tmp_path):
    with pytest.raises(ValueError, match=r"experiment config .*missing\.yaml"):
        load_experiment(tmp_path / "missing.yaml")


def test_omitted_string_defaults_still_apply(tmp_path):
    text = """
civs:
  - player: 3
    provider: cli-claude
"""
    cfg = load_experiment(_write(tmp_path, text))
    assert cfg.players[0].model == ""
    assert cfg.players[0].gateway == ""
    assert cfg.run_id == ""
    assert cfg.gateway_url == "http://192.168.20.196:11444/v1"


def _load(tmp_path, text):
    """Helper to write YAML text and load as experiment."""
    return load_experiment(_write(tmp_path, text))


def test_attention_yaml_parsed(tmp_path):
    cfg = _load(tmp_path, """
run_id: t1
civs:
  - player: 1
    provider: local
    model: m
    attention:
      mode: hybrid
      max_skip: 3
""")
    assert cfg.players[0].options.attention.mode == "hybrid"
    assert cfg.players[0].options.attention.max_skip == 3
    assert cfg.players[0].options.attention.max_streak == 5  # default preserved


def test_attention_bad_mode_rejected(tmp_path):
    with pytest.raises(ValueError, match="attention.mode"):
        _load(tmp_path, """
run_id: t1
civs:
  - {player: 1, provider: local, model: m, attention: {mode: sometimes}}
""")


def test_attention_unknown_subkey_rejected(tmp_path):
    with pytest.raises(ValueError, match="attention"):
        _load(tmp_path, """
run_id: t1
civs:
  - {player: 1, provider: local, model: m, attention: {mode: auto, nap_time: 9}}
""")


def test_max_game_turns_top_level(tmp_path):
    cfg = _load(tmp_path, """
run_id: t1
max_game_turns: 200
civs:
  - {player: 1, provider: local, model: m}
""")
    assert cfg.max_game_turns == 200


def test_max_game_turns_negative_rejected(tmp_path):
    with pytest.raises(ValueError, match="max_game_turns"):
        _load(tmp_path, """
run_id: t1
max_game_turns: -1
civs:
  - {player: 1, provider: local, model: m}
""")


def test_local_civ_parses_memory_and_task_tracker(tmp_path):
    text = GOOD.replace(
        "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}",
        "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}\n"
        "    memory: {enabled: true, max_chars: 800, max_age_turns: 6}\n"
        "    task_tracker: {enabled: true, max_tasks: 5}",
    )
    cfg = load_experiment(_write(tmp_path, text))
    local = cfg.players[0]
    assert local.options.memory == MemoryOptions(
        enabled=True,
        max_chars=800,
        max_age_turns=6,
    )
    assert local.options.task_tracker == TaskTrackerOptions(enabled=True, max_tasks=5)


def test_cli_civ_parses_shared_behavior_knobs(tmp_path):
    text = """
civs:
  - player: 1
    provider: cli-claude
    playbook: condensed
    briefing: {enabled: true, map_radius: 2, sections: [overview, units]}
    memory: {enabled: true, max_chars: 900}
    task_tracker: {enabled: true, max_tasks: 4}
"""
    cfg = load_experiment(_write(tmp_path, text))
    cli = cfg.players[0]
    assert cli.provider == "cli-claude"
    assert cli.options.playbook == "condensed"
    assert cli.options.briefing.enabled is True
    assert cli.options.briefing.map_radius == 2
    assert cli.options.briefing.sections == ("overview", "units")
    assert cli.options.memory == MemoryOptions(enabled=True, max_chars=900)
    assert cli.options.task_tracker == TaskTrackerOptions(enabled=True, max_tasks=4)
    # local-only knobs stay at defaults for CLI providers
    assert cli.options.tools == CivOptions().tools
    assert cli.options.result_char_cap == CivOptions().result_char_cap
    assert cli.options.max_steps == CivOptions().max_steps


@pytest.mark.parametrize(
    "knob_line",
    [
        "    tools: standard\n",
        "    result_char_cap: 6000\n",
        "    max_steps: 10\n",
        "    gateway: http://gw:11441/v1\n",
    ],
)
def test_cli_civ_still_rejects_local_only_knobs_and_gateway(tmp_path, knob_line):
    text = (
        "civs:\n"
        "  - player: 1\n"
        "    provider: cli-claude\n"
        + knob_line
    )
    with pytest.raises(ValueError, match=r"player 1.*cli-claude"):
        load_experiment(_write(tmp_path, text))


def test_rejects_non_boolean_memory_enabled(tmp_path):
    bad = GOOD.replace(
        "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}",
        "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}\n"
        '    memory: {enabled: "true"}',
    )
    with pytest.raises(ValueError, match=r"player 3.*memory\.enabled"):
        load_experiment(_write(tmp_path, bad))


def test_memory_max_age_turns_must_be_positive(tmp_path):
    text = """
civs:
  - player: 1
    provider: cli-claude
    memory: {enabled: true, max_age_turns: 0}
"""

    with pytest.raises(ValueError, match="memory.max_age_turns must be a positive integer"):
        load_experiment(_write(tmp_path, text))


def test_rejects_non_positive_task_tracker_max_tasks(tmp_path):
    bad = GOOD.replace(
        "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}",
        "briefing: {enabled: true, map_radius: 4, sections: [overview, units, map]}\n"
        "    task_tracker: {enabled: true, max_tasks: 0}",
    )
    with pytest.raises(ValueError, match=r"player 3.*task_tracker\.max_tasks must be positive"):
        load_experiment(_write(tmp_path, bad))


def test_civ_options_fingerprint_contains_memory_and_task_tracker():
    fp = CivOptions(
        memory=MemoryOptions(enabled=True, max_chars=900),
        task_tracker=TaskTrackerOptions(enabled=True, max_tasks=4),
    ).fingerprint()
    assert fp["memory"] == {"enabled": True, "max_chars": 900, "max_age_turns": 10}
    assert fp["task_tracker"] == {"enabled": True, "max_tasks": 4}


def test_attention_yaml_on_cli_civ(tmp_path):
    """Final-review triage (T2): CLI civs are the expensive seats -- pin that
    the attention knob reaches CivOptions through the cli-provider branch too,
    not just the local one."""
    cfg = _load(tmp_path, """
run_id: t1
civs:
  - player: 2
    provider: cli-claude
    attention:
      mode: hybrid
      threat_radius: 6
""")
    assert cfg.players[0].provider == "cli-claude"
    assert cfg.players[0].options.attention.mode == "hybrid"
    assert cfg.players[0].options.attention.threat_radius == 6


def test_puppet_ids_exclude_seat_zero(tmp_path):
    cfg = _load(tmp_path, """
run_id: t1
civs:
  - {player: 0, provider: local, model: m}
  - {player: 2, provider: local, model: m}
  - {player: 4, provider: local, model: m}
""")
    assert cfg.puppet_ids == [2, 4]


def test_seat_zero_options_fingerprint_preserved_like_nonzero_seat(tmp_path):
    cfg = _load(tmp_path, """
run_id: t1
civs:
  - player: 0
    provider: local
    model: m
    tools: standard
    result_char_cap: 6000
    max_steps: 10
    playbook: condensed
  - {player: 2, provider: local, model: m}
""")
    seat0 = next(p for p in cfg.players if p.player_id == 0)
    seat2 = next(p for p in cfg.players if p.player_id == 2)
    assert seat0.options.fingerprint() == CivOptions(
        tools="standard",
        result_char_cap=6000,
        max_steps=10,
        playbook="condensed",
    ).fingerprint()
    assert seat2.options.fingerprint() == CivOptions().fingerprint()


def test_seat_zero_non_off_attention_rejected(tmp_path):
    with pytest.raises(ValueError, match="seat 0"):
        _load(tmp_path, """
run_id: t1
civs:
  - player: 0
    provider: local
    model: m
    attention:
      mode: hybrid
""")


# ---------------------------------------------------------------------------
# Task 9 — scripted provider parsing, local-knob rejection, live artifacts
# ---------------------------------------------------------------------------


def test_scripted_provider_parses(tmp_path):
    """`provider: scripted` is a valid seat-0 spec; it carries only the shared
    behaviour knobs (no local-only knobs) and reports its own driver kind."""
    cfg = _load(tmp_path, """
run_id: t1
civs:
  - player: 0
    provider: scripted
    model: seat0-smoke
    attention: {mode: "off"}
  - {player: 1, provider: local, model: m}
""")
    seat0 = next(p for p in cfg.players if p.player_id == 0)
    assert seat0.provider == "scripted"
    assert seat0.model == "seat0-smoke"
    assert seat0.driver_kind() == "scripted"
    assert seat0.options == CivOptions()  # shared knobs defaulted, no local knobs


@pytest.mark.parametrize(
    "knob",
    [
        "tools: full",
        "max_steps: 8",
        "result_char_cap: 6000",
        "gateway: http://gw:11440/v1",
    ],
)
def test_scripted_rejects_local_only_knobs(tmp_path, knob):
    """A scripted civ never uses gateway/tools/max_steps/result_char_cap;
    experiment.py restricts those to local civs."""
    with pytest.raises(ValueError, match="scripted"):
        _load(tmp_path, f"""
run_id: t1
civs:
  - player: 0
    provider: scripted
    model: seat0-smoke
    {knob}
  - {{player: 1, provider: local, model: m}}
""")


def test_scripted_bare_off_attention_rejected(tmp_path):
    """YAML trap: bare `off` is PyYAML boolean False; _parse_attention rejects
    a non-string mode, so the quote is load-bearing."""
    with pytest.raises(ValueError, match="attention.mode"):
        _load(tmp_path, """
run_id: t1
civs:
  - player: 0
    provider: scripted
    model: seat0-smoke
    attention: {mode: off}
  - {player: 1, provider: local, model: m}
""")


def test_loads_seat0_scripted_smoke_artifact():
    """The checked-in stage-1 live config parses exactly as specified, with no
    baked run_id."""
    cfg = load_experiment(SEAT0_SCRIPTED_SMOKE)
    assert cfg.max_puppet_turns == 24
    assert cfg.max_game_turns == 24
    assert cfg.idle_poll_limit == 600
    assert cfg.gateway_url == "http://192.168.20.196:11444/v1"
    assert cfg.run_id == ""  # supplied per live command, never baked in

    seat0 = next(p for p in cfg.players if p.player_id == 0)
    assert seat0.provider == "scripted"
    assert seat0.model == "seat0-smoke"
    assert seat0.driver_kind() == "scripted"
    assert seat0.options.attention.mode == "off"

    puppets = [p for p in cfg.players if p.player_id != 0]
    assert {p.player_id for p in puppets} == {1, 2}
    for p in puppets:
        assert p.provider == "local"
        assert p.model == "gemma4-26b"
        assert p.gateway == "http://192.168.20.196:11440/v1"
        assert p.options.tools == "full"
        assert p.options.max_steps == 8
        assert p.options.attention.mode == "off"
    assert cfg.puppet_ids == [1, 2]


def test_loads_seat0_llm_smoke_artifact():
    """The checked-in stage-2 live config swaps seat 0 to cli-claude and widens
    the shared budgets to 36; the two local puppets are unchanged."""
    cfg = load_experiment(SEAT0_LLM_SMOKE)
    assert cfg.max_puppet_turns == 36
    assert cfg.max_game_turns == 36
    assert cfg.idle_poll_limit == 600
    assert cfg.gateway_url == "http://192.168.20.196:11444/v1"
    assert cfg.run_id == ""

    seat0 = next(p for p in cfg.players if p.player_id == 0)
    assert seat0.provider == "cli-claude"
    assert seat0.model == ""
    assert seat0.driver_kind() == "cli"
    assert seat0.options.attention.mode == "off"

    puppets = [p for p in cfg.players if p.player_id != 0]
    assert {p.player_id for p in puppets} == {1, 2}
    for p in puppets:
        assert p.provider == "local"
        assert p.model == "gemma4-26b"
        assert p.gateway == "http://192.168.20.196:11440/v1"
        assert p.options.max_steps == 8
    assert cfg.puppet_ids == [1, 2]


def test_checked_in_channels_core_gate_experiment_validates():
    cfg = load_experiment(CHANNELS_CORE_SMOKE)

    assert cfg.run_id == "arena-channels-core-gate-v4"
    assert cfg.live_gate.enabled is True
    assert cfg.live_gate.scenario == "unofficial_channels_core_v1"
    assert cfg.live_gate.roles == (
        ("api_actor", 1),
        ("cli_actor", 2),
        ("privacy_observer", 3),
    )
    assert cfg.max_puppet_turns == 36
    assert cfg.max_game_turns == 36
    assert (
        cfg.channel_rules.acceptance_turns,
        cfg.channel_rules.funding_turns,
        cfg.channel_rules.payment_response_turns,
    ) == (3, 2, 2)

    players = {spec.player_id: spec for spec in cfg.players}
    assert {
        player_id: (spec.provider, spec.model, spec.gateway)
        for player_id, spec in players.items()
    } == {
        1: ("local", "gemma4-26b", "http://192.168.20.196:11440/v1"),
        2: ("cli-codex", "gpt-5.5", ""),
        3: ("scripted", "", ""),
    }
    assert players[1].options.tools == "full"
    assert cfg.puppet_ids == [1, 2, 3]
    assert all(spec.options.channels.enabled for spec in cfg.players)
    assert all(spec.options.attention.mode == "off" for spec in cfg.players)


def test_seat0_poll_limit_knobs_default_and_parse(tmp_path):
    cfg = load_experiment(_write(tmp_path, GOOD))
    assert cfg.seat0_drain_poll_limit == 1800
    assert cfg.seat0_human_pending_poll_limit == 1800

    text = GOOD.replace(
        "idle_poll_limit: 3600",
        "idle_poll_limit: 3600\n"
        "seat0_drain_poll_limit: 900\n"
        "seat0_human_pending_poll_limit: 1200",
    )
    cfg = load_experiment(_write(tmp_path, text))
    assert cfg.seat0_drain_poll_limit == 900
    assert cfg.seat0_human_pending_poll_limit == 1200


@pytest.mark.parametrize("bad", ["0", "-5", "true", '"x"'])
@pytest.mark.parametrize(
    "field", ["seat0_drain_poll_limit", "seat0_human_pending_poll_limit"]
)
def test_seat0_poll_limit_knobs_reject_non_positive(tmp_path, field, bad):
    text = GOOD.replace(
        "idle_poll_limit: 3600",
        f"idle_poll_limit: 3600\n{field}: {bad}",
    )
    with pytest.raises(ValueError):
        load_experiment(_write(tmp_path, text))


def test_loads_per_civ_channels_and_run_wide_rules(tmp_path):
    path = tmp_path / "channels.yaml"
    path.write_text("""
channel_rules:
  acceptance_turns: 3
  grievance_half_life_turns: 30
civs:
  - player: 1
    provider: local
    model: m
    channels: {enabled: true, guidance: true}
  - player: 2
    provider: cli-codex
    model: gpt-5
    channels: {enabled: false}
""")
    cfg = load_experiment(path)
    assert cfg.players[0].options.channels.enabled is True
    assert cfg.players[0].options.channels.guidance is True
    assert cfg.players[1].options.channels.enabled is False
    assert cfg.players[1].options.channels.guidance is False
    assert cfg.channel_rules == ChannelRules()


def test_loads_per_civ_channel_script(tmp_path):
    path = tmp_path / "channels-script.yaml"
    path.write_text("""
civs:
  - player: 3
    provider: scripted
    channels:
      enabled: true
      guidance: true
      script:
        - turn: 157
          action: send_message
          args:
            to_player: 1
            text: opener
        - turn: 158
          action: fund_deal
          args:
            deal_id: deal-000001
""")
    cfg = load_experiment(path)

    assert cfg.players[0].options.channels.enabled is True
    assert cfg.players[0].options.channels.guidance is True
    assert cfg.players[0].options.channels.script == (
        ChannelScriptStep(157, "send_message", {"to_player": 1, "text": "opener"}),
        ChannelScriptStep(158, "fund_deal", {"deal_id": "deal-000001"}),
    )


@pytest.mark.parametrize("fragment, match", [
    ("channels: {enabled: yes}", "channels.enabled must be a boolean"),
    ("channels: {enabled: true, guidance: yes}", "channels.guidance must be a boolean"),
    ("channel_rules: {max_payment_gold: 10001}", "max_payment_gold must be 1..10000"),
    ("channel_rules: {max_completion_turns: 31}", "max_completion_turns must be 1..30"),
    ("channel_rules: {max_zone_distance: 0}", "max_zone_distance must be 1..10"),
])
def test_rejects_invalid_channel_config(tmp_path, fragment, match):
    path = tmp_path / "bad.yaml"
    if fragment.startswith("channels"):
        text = f"civs:\n  - {{player: 1, provider: local, model: m, {fragment}}}\n"
    else:
        text = f"{fragment}\ncivs:\n  - {{player: 1, provider: local, model: m}}\n"
    path.write_text(text)
    with pytest.raises(ValueError, match=match):
        load_experiment(path)


@pytest.mark.parametrize(
    "fragment, match",
    [
        (
            "channels: {enabled: true, script: not-a-list}",
            "channels.script must be a list",
        ),
        (
            "channels: {enabled: false, script: []}",
            "channels.script requires channels.enabled true",
        ),
        (
            "channels: {script: []}",
            "channels.script requires channels.enabled true",
        ),
        (
            "channels: {enabled: true, script: [{turn: true, action: send_message, args: {}}]}",
            "channels.script\\[0\\].turn must be an integer",
        ),
        (
            "channels: {enabled: true, script: [{turn: 0, action: send_message, args: {}}]}",
            "channels.script\\[0\\].turn must be positive",
        ),
        (
            "channels: {enabled: true, script: [{turn: 1, action: trade_gold, args: {}}]}",
            "channels.script\\[0\\].action must be one of",
        ),
        (
            "channels: {enabled: true, script: [{turn: 1, action: send_message, args: []}]}",
            "channels.script\\[0\\].args must be a mapping",
        ),
        (
            "channels: {enabled: true, script: [{turn: 1, action: send_message, args: {}, note: bad}]}",
            "channels.script\\[0\\]: unknown key",
        ),
    ],
)
def test_rejects_invalid_channel_script_config(tmp_path, fragment, match):
    path = tmp_path / "bad-channel-script.yaml"
    path.write_text(
        "civs:\n"
        "  - player: 3\n"
        "    provider: scripted\n"
        f"    {fragment}\n"
    )
    with pytest.raises(ValueError, match=match):
        load_experiment(path)


@pytest.mark.parametrize(("field", "upper"), [
    ("max_active_deals_per_pair", 3),
    ("max_message_chars", 2_000),
    ("max_narrative_chars", 1_000),
    ("max_messages_per_pair", 200),
    ("prompt_messages_per_counterpart", 10),
    ("recent_terminal_deals", 5),
    ("max_queued_action_bytes", 8_192),
])
def test_rejects_channel_rule_integer_above_exact_bound(tmp_path, field, upper):
    path = tmp_path / "bad-bound.yaml"
    path.write_text(
        f"channel_rules: {{{field}: {upper + 1}}}\n"
        "civs:\n  - {player: 1, provider: local, model: m}\n"
    )
    with pytest.raises(ValueError, match=rf"{field} must be 1\.\.{upper}"):
        load_experiment(path)


@pytest.mark.parametrize("value", [0, 0.051])
def test_rejects_channel_grievance_threshold_outside_supported_bound(tmp_path, value):
    path = tmp_path / "bad-threshold.yaml"
    path.write_text(
        f"channel_rules: {{prompt_grievance_threshold: {value}}}\n"
        "civs:\n  - {player: 1, provider: local, model: m}\n"
    )
    with pytest.raises(ValueError, match="prompt_grievance_threshold"):
        load_experiment(path)


GATE_CIVS = """
civs:
  - player: 1
    provider: local
    model: m
    channels: {enabled: true}
  - player: 2
    provider: cli-codex
    channels: {enabled: true}
  - player: 3
    provider: scripted
    channels: {enabled: true}
"""


def _write_gate_yaml(tmp_path, live_gate_block, *, run_id="run-gate"):
    text = (
        f"run_id: {run_id}\n"
        "max_puppet_turns: 36\n"
        "max_game_turns: 36\n"
        f"{live_gate_block}\n"
        f"{GATE_CIVS}"
    )
    path = tmp_path / "gate.yaml"
    path.write_text(text)
    return path


@pytest.fixture
def registered_gate(monkeypatch):
    meta = ScenarioMeta(
        name="fake_gate_v1",
        revision=1,
        role_contracts=(
            ("api_actor", "in_process"),
            ("cli_actor", "cli"),
            ("privacy_observer", "scripted"),
        ),
        minimum_captures=lambda config: 27,
        create_driver=lambda config: object(),
    )
    monkeypatch.setattr(live_gate_module, "_SCENARIOS", {meta.name: meta})
    return meta


def test_live_gate_block_parses_and_validates(tmp_path, registered_gate):
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: true\n"
        "  scenario: fake_gate_v1\n"
        "  roles:\n"
        "    api_actor: 1\n"
        "    cli_actor: 2\n"
        "    privacy_observer: 3\n"
    ))
    cfg = load_experiment(path)
    assert cfg.live_gate == LiveGateOptions(
        enabled=True,
        scenario="fake_gate_v1",
        roles=(("api_actor", 1), ("cli_actor", 2), ("privacy_observer", 3)),
    )


def test_live_gate_absent_defaults_disabled(tmp_path):
    path = _write_gate_yaml(tmp_path, "")
    cfg = load_experiment(path)
    assert cfg.live_gate == LiveGateOptions()


def test_live_gate_requires_exact_boolean_enabled(tmp_path):
    path = _write_gate_yaml(tmp_path, "live_gate:\n  enabled: yes\n")
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        load_experiment(path)
    path = _write_gate_yaml(tmp_path, "live_gate:\n  scenario: fake_gate_v1\n")
    with pytest.raises(ValueError, match="enabled is required"):
        load_experiment(path)


def test_live_gate_disabled_cannot_carry_scenario_or_roles(tmp_path):
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: false\n"
        "  scenario: fake_gate_v1\n"
    ))
    with pytest.raises(ValueError, match="disabled live_gate"):
        load_experiment(path)


def test_live_gate_unknown_key_rejected(tmp_path):
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: true\n"
        "  scenario: fake_gate_v1\n"
        "  roles: {api_actor: 1, cli_actor: 2, privacy_observer: 3}\n"
        "  surprise: 1\n"
    ))
    with pytest.raises(ValueError, match="unknown key"):
        load_experiment(path)


def test_live_gate_roles_must_be_string_to_int_mapping(tmp_path, registered_gate):
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: true\n"
        "  scenario: fake_gate_v1\n"
        "  roles: {api_actor: one, cli_actor: 2, privacy_observer: 3}\n"
    ))
    with pytest.raises(ValueError, match="must be an integer"):
        load_experiment(path)
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: true\n"
        "  scenario: fake_gate_v1\n"
        "  roles: {}\n"
    ))
    with pytest.raises(ValueError, match="non-empty mapping"):
        load_experiment(path)


def test_live_gate_enabled_requires_scenario(tmp_path):
    path = _write_gate_yaml(tmp_path, (
        "live_gate:\n"
        "  enabled: true\n"
        "  roles: {api_actor: 1, cli_actor: 2, privacy_observer: 3}\n"
    ))
    with pytest.raises(ValueError, match="requires a scenario"):
        load_experiment(path)
