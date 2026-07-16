from dataclasses import replace

import pytest
from civ_mcp.arena.config import (
    ArenaConfig,
    AttentionOptions,
    BriefingOptions,
    ChannelOptions,
    ChannelRules,
    CivOptions,
    MemoryOptions,
    PlayerSpec,
    TaskTrackerOptions,
    channel_config_fingerprint,
    parse_player_spec,
    resolved_puppet_ids,
    validate_arena_config,
)

def test_parse_player_spec_local():
    assert parse_player_spec("1:local:qwen3-coder-30b") == PlayerSpec(1, "local", "qwen3-coder-30b")
    # no gateway override → empty string (falls back to the global --gateway-url)
    assert parse_player_spec("1:local:qwen3-coder-30b").gateway == ""


def test_parse_player_spec_per_civ_gateway():
    """A trailing '@<url>' pins a local civ to its own gateway (e.g. a per-GPU llama-swap)."""
    s = parse_player_spec("3:local:gemma4-26b@http://192.168.20.196:11440/v1")
    assert s == PlayerSpec(3, "local", "gemma4-26b", "http://192.168.20.196:11440/v1")
    assert s.model == "gemma4-26b"
    assert s.gateway == "http://192.168.20.196:11440/v1"


def test_parse_player_spec_gateway_with_colon_model():
    """Model names may contain ':'; the gateway split is on the last '@' only."""
    s = parse_player_spec("4:local:qwen3.6:27b@http://192.168.20.196:11441/v1")
    assert s.model == "qwen3.6:27b"
    assert s.gateway == "http://192.168.20.196:11441/v1"

def test_parse_player_spec_rejects_bad():
    with pytest.raises(ValueError):
        parse_player_spec("nope")

def test_local_model_with_colon():
    s = parse_player_spec("1:local:qwen3-coder:30b")
    assert s == PlayerSpec(1, "local", "qwen3-coder:30b")
    assert s.driver_kind() == "in_process"

def test_cli_claude_empty_model():
    s = parse_player_spec("2:cli-claude:")
    assert s == PlayerSpec(2, "cli-claude", "")
    assert s.driver_kind() == "cli"

def test_cli_codex_model_optional():
    s = parse_player_spec("2:cli-codex:gpt-5.5")
    assert s == PlayerSpec(2, "cli-codex", "gpt-5.5")
    assert s.driver_kind() == "cli"


def test_parse_player_spec_scripted():
    """Task 9: the test-only `scripted` provider parses and reports its own
    driver kind (neither cli nor in_process)."""
    s = parse_player_spec("0:scripted:seat0-smoke")
    assert s == PlayerSpec(0, "scripted", "seat0-smoke")
    assert s.driver_kind() == "scripted"


def test_parse_player_spec_scripted_empty_model():
    s = parse_player_spec("0:scripted:")
    assert s == PlayerSpec(0, "scripted", "")
    assert s.driver_kind() == "scripted"

def test_rejects_unknown_provider():
    with pytest.raises(ValueError):
        parse_player_spec("1:typo:model")

def test_arena_config_gateway_url_default():
    assert ArenaConfig(players=[]).gateway_url == "http://192.168.20.196:11444/v1"

def test_arena_config_idle_poll_limit_default():
    assert ArenaConfig(players=[]).idle_poll_limit == 600

def test_arena_config_run_id_default():
    assert ArenaConfig(players=[]).run_id == ""

def test_arena_config_transcript_dir_default():
    assert ArenaConfig(players=[]).transcript_dir == "arena_runs"


def test_civ_options_defaults_match_today():
    o = CivOptions()
    assert (o.tools, o.result_char_cap, o.max_steps, o.playbook) == ("minimal", 1500, 6, "none")
    assert o.context_budget == "auto"
    assert o.briefing.enabled is False


def test_player_spec_gets_default_options():
    s = parse_player_spec("1:local:qwen3-coder:30b")
    assert s.options == CivOptions()


def test_civ_options_fingerprint_is_json_safe():
    import json

    o = CivOptions(tools=("get_units", "move_unit"), max_steps=10,
                   briefing=BriefingOptions(enabled=True, map_radius=4))
    fp = o.fingerprint()
    assert json.dumps(fp)
    assert fp["tools"] == ["get_units", "move_unit"]
    assert fp["briefing"]["enabled"] is True


def test_civ_options_memory_fingerprint_includes_max_age_turns():
    opts = CivOptions(memory=MemoryOptions(enabled=True, max_chars=900, max_age_turns=6))

    assert opts.fingerprint()["memory"] == {
        "enabled": True,
        "max_chars": 900,
        "max_age_turns": 6,
    }


def test_civ_options_standing_plan_enabled_property():
    assert CivOptions().standing_plan_enabled is False
    assert CivOptions(memory=MemoryOptions(enabled=True)).standing_plan_enabled is True
    assert CivOptions(task_tracker=TaskTrackerOptions(enabled=True)).standing_plan_enabled is True
    assert CivOptions(
        memory=MemoryOptions(enabled=True),
        task_tracker=TaskTrackerOptions(enabled=True),
    ).standing_plan_enabled is True


def test_civ_options_standing_plan_capture_chars():
    assert CivOptions().standing_plan_capture_chars == 0
    assert CivOptions(memory=MemoryOptions(enabled=True, max_chars=900)).standing_plan_capture_chars == 900
    assert CivOptions(task_tracker=TaskTrackerOptions(enabled=True)).standing_plan_capture_chars == 4000
    assert CivOptions(
        memory=MemoryOptions(enabled=True, max_chars=1200),
        task_tracker=TaskTrackerOptions(enabled=True),
    ).standing_plan_capture_chars == 4000
    assert CivOptions(
        memory=MemoryOptions(enabled=True, max_chars=6000),
        task_tracker=TaskTrackerOptions(enabled=True),
    ).standing_plan_capture_chars == 6000
    assert CivOptions(
        memory=MemoryOptions(enabled=True, max_chars=6000),
        task_tracker=TaskTrackerOptions(enabled=True, max_tasks=12),
    ).standing_plan_capture_chars == 6000
    assert CivOptions(
        task_tracker=TaskTrackerOptions(enabled=True, max_tasks=12),
    ).standing_plan_capture_chars == 4480


def test_civ_options_standing_plan_summary_chars_matches_enabled_capture_budget():
    assert CivOptions().standing_plan_summary_chars == 500
    assert CivOptions(
        memory=MemoryOptions(enabled=True, max_chars=900),
    ).standing_plan_summary_chars == 1200
    assert CivOptions(
        memory=MemoryOptions(enabled=True, max_chars=6000),
    ).standing_plan_summary_chars == 6000
    assert CivOptions(task_tracker=TaskTrackerOptions(enabled=True)).standing_plan_summary_chars == 4000
    assert CivOptions(
        task_tracker=TaskTrackerOptions(enabled=True, max_tasks=12),
    ).standing_plan_summary_chars == 4480
    assert CivOptions(
        memory=MemoryOptions(enabled=True, max_chars=6000),
        task_tracker=TaskTrackerOptions(enabled=True, max_tasks=12),
    ).standing_plan_summary_chars == 6000


def test_attention_defaults_off():
    opts = CivOptions()
    assert opts.attention.mode == "off"
    assert opts.attention.max_skip == 5
    assert opts.attention.max_streak == 5
    assert opts.attention.threat_radius == 4


def test_attention_in_fingerprint():
    opts = CivOptions(attention=AttentionOptions(mode="hybrid", max_skip=3))
    fp = opts.fingerprint()
    assert fp["attention"] == {
        "mode": "hybrid", "max_skip": 3, "max_streak": 5, "threat_radius": 4,
    }


def test_attention_directives_enabled_property():
    assert not CivOptions().attention_directives_enabled
    assert not CivOptions(attention=AttentionOptions(mode="auto")).attention_directives_enabled
    assert CivOptions(attention=AttentionOptions(mode="model")).attention_directives_enabled
    assert CivOptions(attention=AttentionOptions(mode="hybrid")).attention_directives_enabled


def test_arena_config_max_game_turns_default_uncapped():
    assert ArenaConfig(players=[]).max_game_turns == 0


def test_summary_chars_widened_for_attention_directives():
    # Final-review Important 1: directives sit at the END of the final summary;
    # attention-directive civs must not get the plain 500-char front clamp on
    # the run-log summary / fallback path. auto mode issues no directives.
    assert CivOptions().standing_plan_summary_chars == 500
    assert CivOptions(attention=AttentionOptions(mode="auto")).standing_plan_summary_chars == 500
    assert CivOptions(attention=AttentionOptions(mode="model")).standing_plan_summary_chars == 1200
    assert CivOptions(attention=AttentionOptions(mode="hybrid")).standing_plan_summary_chars == 1200
    # memory/tracker civs keep their existing (>= 1200) widening untouched
    assert (
        CivOptions(memory=MemoryOptions(enabled=True)).standing_plan_summary_chars == 1200
    )


def _seat(pid: int, *, attention: str = "off") -> PlayerSpec:
    return PlayerSpec(
        pid,
        "local",
        "m",
        options=CivOptions(attention=AttentionOptions(mode=attention)),
    )


def test_puppet_ids_none_derives_only_nonzero_configured_players():
    cfg = ArenaConfig(players=[_seat(0), _seat(2), _seat(4)], puppet_ids=None)
    assert resolved_puppet_ids(cfg) == [2, 4]


def test_explicit_empty_puppet_ids_stays_empty():
    cfg = ArenaConfig(players=[_seat(0), _seat(2)], puppet_ids=[])
    assert resolved_puppet_ids(cfg) == []


@pytest.mark.parametrize("ids", [[0], [2, 0]])
def test_explicit_puppet_ids_reject_seat_zero(ids):
    cfg = ArenaConfig(players=[_seat(0), _seat(2)], puppet_ids=ids)
    with pytest.raises(ValueError, match="seat 0"):
        validate_arena_config(cfg)


def test_explicit_puppet_ids_reject_unknown_and_duplicate_seats():
    with pytest.raises(ValueError, match="not configured"):
        validate_arena_config(ArenaConfig(players=[_seat(2)], puppet_ids=[3]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_arena_config(ArenaConfig(players=[_seat(2)], puppet_ids=[2, 2]))


def test_seat0_requires_attention_off():
    cfg = ArenaConfig(players=[_seat(0, attention="auto")], puppet_ids=[])
    with pytest.raises(ValueError, match="seat 0.*attention.mode.*off"):
        validate_arena_config(cfg)


def test_channel_defaults_are_off_and_fingerprinted():
    opts = CivOptions()
    assert opts.channels == ChannelOptions(enabled=False)
    assert opts.fingerprint()["channels"] == {"enabled": False}


def test_channel_rules_defaults_and_enabled_set_are_canonical():
    cfg = ArenaConfig(players=[
        PlayerSpec(2, "local", "m", options=CivOptions(channels=ChannelOptions(True))),
        PlayerSpec(1, "local", "m"),
    ])
    fp = channel_config_fingerprint(cfg)
    assert fp["schema_version"] == 1
    assert fp["enabled_players"] == [2]
    assert fp["rules"] == ChannelRules().fingerprint()
    assert channel_config_fingerprint(replace(cfg, players=list(reversed(cfg.players)))) == fp
