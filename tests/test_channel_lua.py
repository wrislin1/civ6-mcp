from __future__ import annotations

import pytest

from civ_mcp.arena.channel_terms import ObservationFamily, ObservationRequest
from civ_mcp.game_state import GameState
from civ_mcp.lua.channel_observation import (
    build_channel_observation_query,
    parse_channel_observation_response,
)
from civ_mcp.lua.channel_payments import (
    ExactPaymentOffer,
    build_channel_payment_offer,
    build_channel_payment_query,
    build_channel_payment_response,
    parse_channel_payment_query,
)


def test_observation_parser_preserves_stable_ids_categories_and_route_owner():
    request = ObservationRequest(
        families=frozenset({ObservationFamily.UNITS, ObservationFamily.TRADE_ROUTES}),
        protected_players=(2,),
        zone_centers=((12, 7),),
    )
    obs = parse_channel_observation_response(
        1,
        44,
        request,
        [
            "UNIT|1|17|UNIT_MILITARY_ENGINEER|FORMATION_CLASS_SUPPORT|0|9|8",
            "DIST|1|17|2|3",
            "COMPLETE|units",
            "ROUTE|1|22|5|1",
            "COMPLETE|trade_routes",
            "---END---",
        ],
    )
    assert obs.units[0].unit_id == 17
    assert obs.units[0].formation_class == "FORMATION_CLASS_SUPPORT"
    assert obs.unit_distances[(1, 17, 2)] == 3
    assert obs.trade_routes[0].destination_player == 5
    assert obs.trade_routes[0].destination_is_city_state is True


def test_observation_builder_is_one_query_for_a_union_request():
    request = ObservationRequest(
        families=frozenset(ObservationFamily),
        protected_players=(2, 3),
        zone_centers=((12, 7), (20, 9)),
    )
    lua = build_channel_observation_query(1, request)
    assert "Players[1]:GetUnits():Members()" in lua
    assert "Map.GetPlotDistance" in lua
    assert "DestinationCityPlayer" in lua
    assert lua.count('print("---END---")') == 1
    completion_markers = [
        "units",
        "cities",
        "territory",
        "camps",
        "treasury",
        "diplomacy",
        "trade_routes",
    ]
    marker_positions = [
        lua.index(f'print("COMPLETE|{family}")') for family in completion_markers
    ]
    assert marker_positions == sorted(marker_positions)


def test_payment_builders_are_exact_gold_only_and_never_accept_counteroffers():
    offer = build_channel_payment_offer(2, 100)
    assert "DealItemTypes.GOLD" in offer
    assert "SetAmount(100)" in offer
    assert "SetDuration(0)" in offer
    assert "DealProposalAction.PROPOSED" in offer
    assert "DealProposalAction.ACCEPTED" not in offer
    response = build_channel_payment_response(1, 100, True)
    assert "GetItemCount() ~= 1" in response
    assert "DealProposalAction.ACCEPTED" in response


def test_observation_parser_covers_each_authoritative_family():
    request = ObservationRequest(families=frozenset(ObservationFamily))
    obs = parse_channel_observation_response(
        2,
        9,
        request,
        [
            "UNIT|2|7|UNIT_WARRIOR|FORMATION_CLASS_LAND_COMBAT|0|4|5",
            "COMPLETE|units",
            "CITY|2|99|6|7",
            "ZONE|99|12|7|4",
            "COMPLETE|cities",
            "TERRITORY|8|9",
            "COMPLETE|territory",
            "GOLD|501",
            "COMPLETE|treasury",
            "WAR|2|3",
            "COMPLETE|diplomacy",
            "ROUTE|2|11|55|1",
            "COMPLETE|trade_routes",
            "CAMP|10|12",
            "COMPLETE|camps",
            "---END---",
        ],
    )

    assert obs.player_id == 2
    assert obs.turn == 9
    assert obs.families_present == frozenset(
        {
            ObservationFamily.UNITS,
            ObservationFamily.CITIES,
            ObservationFamily.TERRITORY,
            ObservationFamily.TREASURY,
            ObservationFamily.DIPLOMACY,
            ObservationFamily.TRADE_ROUTES,
            ObservationFamily.CAMPS,
        }
    )
    assert obs.units[0].owner_id == 2
    assert obs.cities[0].city_id == 99
    assert obs.zone_distances[(99, 12, 7)] == 4
    assert obs.territory == frozenset({(8, 9)})
    assert obs.treasury_gold == 501
    assert obs.wars == frozenset({(2, 3)})
    assert obs.trade_routes[0].trader_unit_id == 11
    assert obs.camps == frozenset({(10, 12)})


def test_observation_parser_marks_completed_empty_families_present():
    request = ObservationRequest(
        families=frozenset({ObservationFamily.UNITS, ObservationFamily.CAMPS})
    )
    obs = parse_channel_observation_response(
        2,
        9,
        request,
        ["COMPLETE|units", "COMPLETE|camps"],
    )

    assert obs.families_present == request.families
    assert obs.units == ()
    assert obs.camps == frozenset()
    assert obs.errors == ()


def test_observation_parser_keeps_completed_earlier_family_not_truncated_later_one():
    request = ObservationRequest(
        families=frozenset(
            {ObservationFamily.UNITS, ObservationFamily.TRADE_ROUTES}
        )
    )
    obs = parse_channel_observation_response(
        2,
        9,
        request,
        [
            "UNIT|2|7|UNIT_WARRIOR|FORMATION_CLASS_LAND_COMBAT|0|4|5",
            "COMPLETE|units",
            "ROUTE|2|11|3|0",
        ],
    )

    assert obs.families_present == frozenset({ObservationFamily.UNITS})
    assert obs.units[0].unit_id == 7
    assert obs.trade_routes == ()
    assert obs.errors == ("trade_routes",)


def test_observation_parser_treats_fully_truncated_output_as_unavailable():
    request = ObservationRequest(
        families=frozenset(
            {ObservationFamily.CAMPS, ObservationFamily.TREASURY}
        )
    )
    obs = parse_channel_observation_response(2, 9, request, [])

    assert obs.families_present == frozenset()
    assert obs.camps == frozenset()
    assert obs.treasury_gold == 0
    assert obs.errors == ("treasury", "camps")


def test_observation_builder_prints_only_requested_families():
    lua = build_channel_observation_query(
        1,
        ObservationRequest(families=frozenset({ObservationFamily.TREASURY})),
    )

    assert "GetTreasury():GetGoldBalance()" in lua
    assert "GetUnits():Members()" not in lua
    assert "GetCities():Members()" not in lua
    assert "GetOutgoingRoutes()" not in lua
    assert "Map.GetPlotByIndex" not in lua


@pytest.mark.parametrize(
    "builder,args",
    [
        (build_channel_observation_query, ('1); os.exit(); --', ObservationRequest())),
        (
            build_channel_observation_query,
            (1, ObservationRequest(protected_players=('2); os.exit(); --',))),
        ),
        (
            build_channel_observation_query,
            (1, ObservationRequest(zone_centers=((12, '7); os.exit(); --'),))),
        ),
        (build_channel_payment_offer, ('2); os.exit(); --', 100)),
        (build_channel_payment_offer, (2, '100); os.exit(); --')),
        (build_channel_payment_query, ('2); os.exit(); --', 100)),
        (build_channel_payment_query, (2, '100); os.exit(); --')),
        (build_channel_payment_response, ('2); os.exit(); --', 100, True)),
        (build_channel_payment_response, (2, '100); os.exit(); --', True)),
    ],
)
def test_channel_builders_reject_integer_injection(builder, args):
    with pytest.raises((TypeError, ValueError)):
        builder(*args)


def test_payment_query_parser_requires_exact_five_integer_fingerprint():
    offer = parse_channel_payment_query(["PAYMENT|1|2|100|0|1", "---END---"])

    assert offer == ExactPaymentOffer(payer=1, payee=2, gold=100)
    assert offer.fingerprint() == {
        "payer": 1,
        "payee": 2,
        "gold": 100,
        "duration": 0,
        "item_count": 1,
    }
    assert parse_channel_payment_query(["PAYMENT|1|2|100|30|1"]) is None
    assert parse_channel_payment_query(["PAYMENT|1|2|100|0|2"]) is None
    assert parse_channel_payment_query(["ERR:NO_EXACT_PAYMENT"]) is None


class _CannedWriteConnection:
    def __init__(self, responses: list[list[str]]):
        self.responses = list(responses)
        self.writes: list[str] = []

    async def execute_write(self, lua: str, timeout: float = 5.0) -> list[str]:
        self.writes.append(lua)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_game_state_channel_wrappers_use_ingame_write_context():
    request = ObservationRequest(families=frozenset({ObservationFamily.TREASURY}))
    conn = _CannedWriteConnection(
        [
            ["GOLD|250", "COMPLETE|treasury", "---END---"],
            ["OK:PAYMENT_PROPOSED", "---END---"],
            ["PAYMENT|1|2|100|0|1", "---END---"],
            ["OK:PAYMENT_ACCEPTED", "---END---"],
        ]
    )
    gs = GameState(conn)  # type: ignore[arg-type]

    obs = await gs.get_channel_observation(2, 10, request)
    proposed = await gs.offer_channel_payment(2, 100)
    pending = await gs.get_channel_payment_offer(1, 100)
    accepted = await gs.respond_to_channel_payment(1, 100, True)

    assert obs.treasury_gold == 250
    assert proposed == "PAYMENT_PROPOSED"
    assert pending == ExactPaymentOffer(1, 2, 100)
    assert accepted == "PAYMENT_ACCEPTED"
    assert len(conn.writes) == 4
