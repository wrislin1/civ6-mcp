import dataclasses
import hashlib
import json
import pytest

import civ_mcp.arena.channel_protocol as channel_protocol
from civ_mcp.arena.channel_protocol import (
    CHANNEL_ACTION_NAMES,
    ChannelTurnContext,
    FundDeal,
    ProposeDeal,
    RespondToDeal,
    RespondToPayment,
    SendMessage,
    channel_tool_schemas,
    parse_channel_action,
    parse_cli_channel_lines,
)
from civ_mcp.arena.config import ChannelRules


def test_actor_is_bound_and_never_accepted_from_model_args():
    with pytest.raises(ValueError, match="unknown field.*actor"):
        parse_channel_action(
            "send_message", {"actor": 7, "to_player": 2, "text": "x"},
            actor=1, enabled_players=frozenset({1, 2}), rules=ChannelRules(),
        )


def test_message_and_deal_bounds_are_checked_before_staging():
    with pytest.raises(ValueError, match="message text must be 1..2000"):
        parse_channel_action(
            "send_message", {"to_player": 2, "text": "x" * 2001},
            actor=1, enabled_players=frozenset({1, 2}), rules=ChannelRules(),
        )
    with pytest.raises(ValueError, match="within must be 1..30"):
        parse_channel_action(
            "propose_deal", {"to_player": 2, "text": "camp", "favor": {
                "term_type": "destroy_camp", "params": {"x": 4, "y": 5}},
                "payment_gold": 100, "timing": "on_delivery", "within": 31},
            actor=1, enabled_players=frozenset({1, 2}), rules=ChannelRules(),
        )


def test_cli_parser_isolates_bad_lines_and_has_deterministic_source_ids():
    summary = "\n".join([
        'CHANNEL {"action":"send_message","to_player":2,"text":"hello"}',
        "CHANNEL not-json",
        'CHANNEL {"action":"respond_to_deal","deal_id":"deal-000001","accept":true}',
    ])
    first = parse_cli_channel_lines(
        summary, run_id="r", actor=1, turn=9,
        enabled_players=frozenset({1, 2}), rules=ChannelRules(),
    )
    second = parse_cli_channel_lines(
        summary, run_id="r", actor=1, turn=9,
        enabled_players=frozenset({1, 2}), rules=ChannelRules(),
    )
    assert [line.source_id for line in first] == [line.source_id for line in second]
    assert [line.error for line in first] == ["", "invalid CHANNEL JSON", ""]


def test_context_stages_in_source_order_without_mutating_runtime():
    ctx = ChannelTurnContext("r", 1, 7, frozenset({1, 2}), ChannelRules())
    result = ctx.dispatch("send_message", {"to_player": 2, "text": "hello"})
    assert result.startswith("QUEUED channel action")
    assert len(ctx.staged_actions) == 1
    assert ctx.staged_actions[0].source_id.startswith("api:")


def parse(name, args, **changes):
    options = {
        "actor": 1,
        "enabled_players": frozenset({1, 2, 3}),
        "rules": ChannelRules(),
    }
    options.update(changes)
    return parse_channel_action(name, args, **options)


def valid_deal(**changes):
    args = {
        "to_player": 2,
        "text": "Clear the camp",
        "favor": {"term_type": "destroy_camp", "params": {"x": 4, "y": 5}},
        "payment_gold": 100,
        "timing": "on_delivery",
        "within": 10,
    }
    args.update(changes)
    return args


def test_all_valid_actions_construct_the_exact_frozen_record_types():
    actions = [
        parse("send_message", {"to_player": 2, "text": "hello"}),
        parse("propose_deal", valid_deal()),
        parse("respond_to_deal", {"deal_id": "deal-000001", "accept": True}),
        parse("fund_deal", {"deal_id": "deal-000002"}),
        parse("respond_to_payment", {"deal_id": "deal-000003", "accept": False}),
    ]
    assert [type(action) for action in actions] == [
        SendMessage, ProposeDeal, RespondToDeal, FundDeal, RespondToPayment,
    ]
    assert all(dataclasses.is_dataclass(action) for action in actions)
    with pytest.raises(dataclasses.FrozenInstanceError):
        actions[0].text = "changed"


@pytest.mark.parametrize(
    ("name", "args", "match"),
    [
        ("bogus", {}, "unknown channel action"),
        ("send_message", {"to_player": 2, "text": "x", "extra": 1}, "unknown field.*extra"),
        ("send_message", {"to_player": True, "text": "x"}, "to_player must be an integer"),
        ("send_message", {"to_player": 1, "text": "x"}, "cannot target self"),
        ("send_message", {"to_player": 8, "text": "x"}, "not channel-enabled"),
        ("send_message", {"to_player": 2, "text": "   "}, "message text must be 1..2000"),
        ("propose_deal", valid_deal(payment_gold=True), "payment_gold must be an integer"),
        ("propose_deal", valid_deal(payment_gold=0), "payment_gold must be 1..10000"),
        ("propose_deal", valid_deal(payment_gold=10_001), "payment_gold must be 1..10000"),
        ("propose_deal", valid_deal(within=True), "within must be an integer"),
        ("propose_deal", valid_deal(timing="later"), "timing must be up_front or on_delivery"),
        ("propose_deal", valid_deal(favor=[]), "favor must be an object"),
        ("propose_deal", valid_deal(favor={"term_type": "destroy_camp", "params": {}, "verdict": "honored"}), "unknown field.*verdict"),
        ("propose_deal", valid_deal(favor={"term_type": "future_term", "params": {}}), "unknown favor term"),
        ("propose_deal", valid_deal(favor={"term_type": "narrative", "params": {"text": "do it"}}), "narrative terms require an active game master"),
        ("respond_to_deal", {"deal_id": "deal-1", "accept": True}, "malformed deal_id"),
        ("respond_to_deal", {"deal_id": "deal-000001", "accept": 1}, "accept must be a boolean"),
    ],
)
def test_malformed_or_unbound_action_arguments_are_rejected(name, args, match):
    with pytest.raises(ValueError, match=match):
        parse(name, args)


def test_default_term_validation_is_closed_but_leaves_core_params_to_registry():
    core_names = {
        "destroy_camp",
        "dont_settle_within",
        "found_city_within",
        "declare_war_on",
        "keep_peace_with",
        "maintain_gold_reserve",
    }
    for term_type in core_names:
        action = parse(
            "propose_deal",
            valid_deal(favor={"term_type": term_type, "params": {"future": "shape"}}),
        )
        assert action.favor["term_type"] == term_type


def test_term_validator_canonicalizes_core_terms_before_staging():
    seen = []

    def validator(favor):
        seen.append(favor)
        return {"term_type": favor["term_type"], "params": {"x": 9, "y": 8}}

    action = parse("propose_deal", valid_deal(), term_validator=validator)
    assert seen == [{"term_type": "destroy_camp", "params": {"x": 4, "y": 5}}]
    assert action.favor == {"term_type": "destroy_camp", "params": {"x": 9, "y": 8}}


def test_narrative_envelope_is_only_available_when_explicitly_enabled():
    narrative = {"term_type": "narrative", "params": {"text": "Hold the pass"}}
    action = parse(
        "propose_deal", valid_deal(favor=narrative), narrative_allowed=True,
    )
    assert action.favor == narrative
    with pytest.raises(ValueError, match="narrative text must be 1..1000"):
        parse(
            "propose_deal",
            valid_deal(favor={"term_type": "narrative", "params": {"text": "x" * 1001}}),
            narrative_allowed=True,
        )


def test_queued_action_byte_bound_is_checked_before_record_construction():
    rules = dataclasses.replace(ChannelRules(), max_queued_action_bytes=300)
    with pytest.raises(ValueError, match="channel action must be at most 300 bytes"):
        parse(
            "propose_deal",
            valid_deal(favor={"term_type": "destroy_camp", "params": {"blob": "x" * 400}}),
            rules=rules,
        )


def test_cli_source_id_uses_physical_line_index_and_exact_line_hash():
    line = 'CHANNEL {"action":"send_message","to_player":2,"text":"hello"}'
    parsed = parse_cli_channel_lines(
        f"prose\n{line}\nCHANNEL not-json",
        run_id="r", actor=1, turn=9,
        enabled_players=frozenset({1, 2}), rules=ChannelRules(),
    )
    digest = hashlib.sha256(line.encode()).hexdigest()[:16]
    assert parsed[0].source_id == f"cli:r:1:9:1:{digest}"
    assert parsed[0].staged_action.action == SendMessage(2, "hello")
    assert parsed[1].staged_action is None


def test_cli_parser_isolates_all_ordinary_line_failures_and_continues():
    oversized_integer = (
        'CHANNEL {"action":"send_message","to_player":'
        + "9" * 5_000
        + ',"text":"bad"}'
    )
    surrogate = "CHANNEL \ud800"
    deeply_nested = "CHANNEL " + "[" * 2_000 + "]" * 2_000
    valid = 'CHANNEL {"action":"send_message","to_player":2,"text":"later"}'
    summary = "\n".join([oversized_integer, surrogate, deeply_nested, valid])

    first = parse_cli_channel_lines(
        summary, run_id="r", actor=1, turn=9,
        enabled_players=frozenset({1, 2}), rules=ChannelRules(),
    )
    second = parse_cli_channel_lines(
        summary, run_id="r", actor=1, turn=9,
        enabled_players=frozenset({1, 2}), rules=ChannelRules(),
    )

    assert [line.error for line in first] == [
        "invalid CHANNEL JSON",
        "invalid CHANNEL JSON",
        "invalid CHANNEL JSON",
        "",
    ]
    assert [line.source_id for line in first] == [line.source_id for line in second]
    surrogate_digest = hashlib.sha256(
        surrogate.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:16]
    assert first[1].source_id == f"cli:r:1:9:1:{surrogate_digest}"
    assert first[3].action == SendMessage(2, "later")


def test_cli_parser_does_not_contain_base_exceptions(monkeypatch):
    def interrupt(_text):
        raise KeyboardInterrupt

    monkeypatch.setattr(channel_protocol.json, "loads", interrupt)
    with pytest.raises(KeyboardInterrupt):
        parse_cli_channel_lines(
            "CHANNEL {}", run_id="r", actor=1, turn=9,
            enabled_players=frozenset({1, 2}), rules=ChannelRules(),
        )


def test_cli_parser_isolates_unexpected_validation_exceptions(monkeypatch):
    original = channel_protocol.parse_channel_action

    def fail_one_line(name, args, **kwargs):
        if args.get("text") == "fail":
            raise RuntimeError
        return original(name, args, **kwargs)

    monkeypatch.setattr(channel_protocol, "parse_channel_action", fail_one_line)
    parsed = parse_cli_channel_lines(
        "\n".join([
            'CHANNEL {"action":"send_message","to_player":2,"text":"fail"}',
            'CHANNEL {"action":"send_message","to_player":2,"text":"later"}',
        ]),
        run_id="r", actor=1, turn=9,
        enabled_players=frozenset({1, 2}), rules=ChannelRules(),
    )
    assert [line.error for line in parsed] == ["invalid CHANNEL action", ""]
    assert parsed[1].action == SendMessage(2, "later")


def test_api_source_id_uses_canonical_args_and_successful_stage_index():
    ctx = ChannelTurnContext("r", 1, 7, frozenset({1, 2}), ChannelRules())
    args = {"text": "hello", "to_player": 2}
    with pytest.raises(ValueError):
        ctx.dispatch("send_message", {"to_player": 1, "text": "bad"})
    ctx.dispatch("send_message", args)
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    assert ctx.staged_actions[0].source_id == f"api:r:1:7:0:{digest}"


def test_tool_schemas_expose_only_bound_strict_action_arguments():
    schemas = channel_tool_schemas()
    assert [schema["function"]["name"] for schema in schemas] == list(CHANNEL_ACTION_NAMES)
    for schema in schemas:
        parameters = schema["function"]["parameters"]
        assert parameters["additionalProperties"] is False
        assert "actor" not in parameters["properties"]
    favor = schemas[1]["function"]["parameters"]["properties"]["favor"]
    assert "narrative" not in favor["properties"]["term_type"]["enum"]
    narrative_favor = channel_tool_schemas(narrative_allowed=True)[1]["function"]["parameters"]["properties"]["favor"]
    assert "narrative" in narrative_favor["properties"]["term_type"]["enum"]


def test_propose_deal_description_states_payer_without_discouraging_use():
    schemas = {schema["function"]["name"]: schema for schema in channel_tool_schemas()}

    assert schemas["propose_deal"]["function"]["description"] == (
        "Propose an unofficial favor-for-gold deal. YOU are the payer: you "
        "pay payment_gold and to_player performs the favor."
    )
