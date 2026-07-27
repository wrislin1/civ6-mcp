import pytest

from civ_mcp import lua as lq
from civ_mcp.narrate import narrate_units


def _unit(**overrides) -> lq.UnitInfo:
    fields = {
        "unit_id": 65541,
        "unit_index": 5,
        "name": "Bhasa",
        "unit_type": "UNIT_GREAT_WRITER",
        "x": 12,
        "y": 19,
        "moves_remaining": 2,
        "max_moves": 2,
        "health": 100,
        "max_health": 100,
        "valid_improvements": ["IMPROVEMENT_MINE"],
        "can_activate_here": True,
        "can_upgrade": True,
        "upgrade_target": "UNIT_TEST_UPGRADE",
        "upgrade_cost": 100,
    }
    fields.update(overrides)
    return lq.UnitInfo(**fields)


def test_mcp_surface_renders_callable_mcp_forms():
    text = narrate_units([_unit()], surface="mcp")

    assert 'unit_action(unit_id=65541, action="activate")' in text
    assert (
        'unit_action(unit_id=65541, action="improve", '
        'improvement="IMPROVEMENT_MINE")'
    ) in text
    assert "upgrade_unit(unit_id=65541)" in text
    assert "activate_great_person with" not in text


def test_arena_surface_renders_only_allowed_calls():
    text = narrate_units(
        [_unit()],
        surface="arena",
        available_tools={"improve_tile", "activate_great_person"},
    )

    assert 'activate_great_person with {"unit_id": 65541}' in text
    assert (
        'improve_tile with {"unit_index": 5, '
        '"improvement_name": "IMPROVEMENT_MINE"}'
    ) in text
    assert "upgrade_unit with" not in text
    assert "unit_action(" not in text


def test_arena_surface_without_context_fails_closed():
    text = narrate_units([_unit()], surface="arena")
    assert "AVAILABLE NOW" not in text
    assert ">> Can build: IMPROVEMENT_MINE" in text


def test_invalid_surface_is_rejected():
    with pytest.raises(ValueError, match="surface"):
        narrate_units([_unit()], surface="browser")


def test_multiple_improvements_render_one_call_each_in_input_order():
    text = narrate_units(
        [
            _unit(
                valid_improvements=[
                    "IMPROVEMENT_MINE",
                    "IMPROVEMENT_FARM",
                ]
            )
        ],
        surface="arena",
        available_tools={"improve_tile"},
    )
    calls = [line for line in text.splitlines() if "improve_tile with" in line]

    assert calls == [
        '    >> AVAILABLE NOW: improve_tile with {"unit_index": 5, '
        '"improvement_name": "IMPROVEMENT_MINE"}',
        '    >> AVAILABLE NOW: improve_tile with {"unit_index": 5, '
        '"improvement_name": "IMPROVEMENT_FARM"}',
    ]


def test_zero_moves_suppresses_all_exact_calls():
    text = narrate_units(
        [_unit(moves_remaining=0)],
        surface="arena",
        available_tools={
            "activate_great_person",
            "improve_tile",
            "upgrade_unit",
        },
    )

    assert "AVAILABLE NOW" not in text


def test_unknown_improvement_has_no_exact_call():
    text = narrate_units(
        [_unit(valid_improvements=["UNKNOWN"])],
        surface="arena",
        available_tools={"improve_tile"},
    )

    assert "improve_tile with" not in text


def test_needs_promotion_has_no_promotion_call():
    text = narrate_units(
        [
            _unit(
                can_activate_here=False,
                can_upgrade=False,
                valid_improvements=[],
                needs_promotion=True,
            )
        ],
        surface="arena",
        available_tools={"promote_unit"},
    )

    assert "promote_unit with" not in text
    assert "AVAILABLE NOW" not in text
