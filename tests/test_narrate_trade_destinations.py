"""Trade-destination narration must name a tool that exists on its surface."""

import pytest

from civ_mcp import lua as lq
from civ_mcp.narrate import narrate_trade_destinations


def _dest(**overrides) -> lq.TradeDestination:
    fields = {
        "city_name": "Kumasi",
        "owner_name": "Domestic",
        "x": 12,
        "y": 19,
        "is_domestic": True,
    }
    fields.update(overrides)
    return lq.TradeDestination(**fields)


def test_mcp_surface_names_the_unit_action_form():
    text = narrate_trade_destinations([_dest()], surface="mcp")

    assert "unit_action with action='trade_route'" in text
    assert "start_trade_route" not in text


def test_arena_surface_names_the_registry_tool():
    """unit_action is not in TOOL_REGISTRY -- naming it burns a model step."""
    text = narrate_trade_destinations([_dest()], surface="arena")

    assert "start_trade_route" in text
    assert "unit_action" not in text


def test_default_surface_is_mcp():
    assert narrate_trade_destinations([_dest()]) == narrate_trade_destinations(
        [_dest()], surface="mcp"
    )


def test_unknown_surface_is_rejected():
    with pytest.raises(ValueError, match="unknown tool surface"):
        narrate_trade_destinations([_dest()], surface="cli")
