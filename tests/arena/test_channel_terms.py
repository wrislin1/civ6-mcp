import dataclasses

import pytest

from civ_mcp.arena.channel_terms import (
    ChannelObservation,
    ObservationFamily,
    ObservedAction,
    ObservedCity,
    ObservedRoute,
    ObservedUnit,
    TERM_REGISTRY,
    TermMode,
    TermValidationContext,
    capture_baseline,
    compile_observation_request,
    unit_categories,
    validate_term,
    verify_term,
)
from civ_mcp.arena.channel_protocol import ChannelTurnContext
from civ_mcp.arena.config import ChannelRules


def obs(turn: int, **changes) -> ChannelObservation:
    base = ChannelObservation(
        player_id=2,
        turn=turn,
        families_present=frozenset(ObservationFamily),
        units=(),
        cities=(),
        camps=frozenset(),
        territory=frozenset(),
        wars=frozenset(),
        treasury_gold=500,
        trade_routes=(),
        action_audit=(),
        unit_distances={},
        zone_distances={},
        errors=(),
    )
    return dataclasses.replace(base, **changes)


@pytest.mark.parametrize(
    "term, before, after, expected",
    [
        (
            {"term_type": "destroy_camp", "params": {"x": 12, "y": 7}},
            obs(1, camps=frozenset({(12, 7)})),
            obs(2),
            "satisfied",
        ),
        (
            {
                "term_type": "found_city_within",
                "params": {"x": 12, "y": 7, "radius": 3},
            },
            obs(1),
            obs(
                2,
                cities=(ObservedCity(2, 99, 13, 7),),
                zone_distances={(99, 12, 7): 1},
            ),
            "satisfied",
        ),
        (
            {"term_type": "declare_war_on", "params": {"player_id": 3}},
            obs(1),
            obs(2, wars=frozenset({(2, 3)})),
            "satisfied",
        ),
        (
            {"term_type": "maintain_gold_reserve", "params": {"min_gold": 400}},
            obs(1),
            obs(2, treasury_gold=399),
            "failed",
        ),
    ],
)
def test_registered_term_outcomes(term, before, after, expected):
    baseline = capture_baseline(term, before)
    result = verify_term(term, baseline, {}, after, due_turn=3)
    assert result.status == expected


def test_missing_required_family_is_unverifiable_only_at_deadline():
    term = {"term_type": "keep_peace_with", "params": {"player_id": 3}}
    missing = dataclasses.replace(
        obs(3), families_present=frozenset(), errors=("diplomacy",)
    )
    assert verify_term(term, {}, {}, missing, due_turn=3).status == "unverifiable"


def test_camp_must_exist_when_proposed():
    with pytest.raises(ValueError, match="camp must exist at proposal"):
        capture_baseline(
            {"term_type": "destroy_camp", "params": {"x": 12, "y": 7}}, obs(1)
        )


def validation_context() -> TermValidationContext:
    return TermValidationContext(
        obligated_player=2,
        enabled_players=frozenset({1, 2, 3}),
        city_state_players=frozenset({55}),
    )


@pytest.mark.parametrize(
    "term",
    [
        {"term_type": "destroy_camp", "params": {"x": True, "y": 7}},
        {
            "term_type": "dont_settle_within",
            "params": {"x": 12, "y": 7, "radius": 0},
        },
        {"term_type": "declare_war_on", "params": {"player_id": 2}},
        {"term_type": "keep_peace_with", "params": {"player_id": 99}},
        {
            "term_type": "maintain_gold_reserve",
            "params": {"min_gold": 10_001},
        },
        {
            "term_type": "destroy_camp",
            "params": {"x": 12, "y": 7, "extra": 1},
        },
    ],
)
def test_term_parameter_validation_is_strict(term):
    with pytest.raises(ValueError):
        validate_term(term, validation_context())


def test_validation_returns_a_canonical_copy_and_rejects_narrative():
    term = {
        "term_type": "found_city_within",
        "params": {"x": 12, "y": 7, "radius": 3},
    }
    assert validate_term(term, validation_context()) == term
    with pytest.raises(
        ValueError, match="narrative terms require an active game master"
    ):
        validate_term(
            {"term_type": "narrative", "params": {"text": "hold"}},
            validation_context(),
        )


def test_registry_metadata_and_observation_request_are_closed():
    assert set(TERM_REGISTRY) == {
        "allow_only_unit_category",
        "destroy_camp",
        "dont_settle_within",
        "forbid_unit_acquisition",
        "found_city_within",
        "declare_war_on",
        "keep_peace_with",
        "keep_units_away",
        "maintain_gold_reserve",
        "military_unit_cap",
        "withdraw_units_from",
    }
    assert TERM_REGISTRY["destroy_camp"].baseline_phase == "proposal"
    assert TERM_REGISTRY["found_city_within"].baseline_phase == "favor_start"
    assert TERM_REGISTRY["keep_peace_with"].mode is TermMode.CONTINUOUS
    request = compile_observation_request(
        [
            {"term_type": "destroy_camp", "params": {"x": 1, "y": 2}},
            {
                "term_type": "dont_settle_within",
                "params": {"x": 4, "y": 5, "radius": 2},
            },
        ]
    )
    assert request.families == frozenset(
        {ObservationFamily.CAMPS, ObservationFamily.CITIES}
    )
    assert request.zone_centers == ((4, 5),)


def test_protocol_hook_rejects_invalid_term_before_staging():
    context = validation_context()
    turn_context = ChannelTurnContext(
        "run",
        1,
        7,
        frozenset({1, 2, 3}),
        ChannelRules(),
        term_validator=lambda term: validate_term(term, context),
    )
    with pytest.raises(ValueError):
        turn_context.dispatch(
            "propose_deal",
            {
                "to_player": 2,
                "text": "hold",
                "favor": {
                    "term_type": "maintain_gold_reserve",
                    "params": {"min_gold": -1},
                },
                "payment_gold": 10,
                "timing": "on_delivery",
                "within": 3,
            },
        )
    assert turn_context.staged_actions == []


def test_turn_context_uses_registry_validation_by_default():
    turn_context = ChannelTurnContext(
        "run", 1, 7, frozenset({1, 2, 3}), ChannelRules()
    )
    with pytest.raises(ValueError, match="radius must be 1..10"):
        turn_context.dispatch(
            "propose_deal",
            {
                "to_player": 2,
                "text": "settle nearby",
                "favor": {
                    "term_type": "found_city_within",
                    "params": {"x": 12, "y": 7, "radius": 11},
                },
                "payment_gold": 10,
                "timing": "on_delivery",
                "within": 3,
            },
        )
    assert turn_context.staged_actions == []


@pytest.mark.parametrize(
    "term, family",
    [
        (
            {"term_type": "destroy_camp", "params": {"x": 12, "y": 7}},
            ObservationFamily.CAMPS,
        ),
        (
            {"term_type": "declare_war_on", "params": {"player_id": 3}},
            ObservationFamily.DIPLOMACY,
        ),
    ],
)
def test_missing_family_is_pending_before_deadline_and_unverifiable_at_it(
    term, family
):
    before = obs(1, camps=frozenset({(12, 7)}))
    baseline = capture_baseline(term, before)
    missing = dataclasses.replace(
        obs(2), families_present=frozenset(ObservationFamily) - {family}
    )
    assert verify_term(term, baseline, {}, missing, due_turn=3).status == "pending"
    at_due = dataclasses.replace(missing, turn=3)
    assert verify_term(term, baseline, {}, at_due, due_turn=3).status == "unverifiable"


def test_found_city_requires_a_new_stable_identity_and_complete_distance():
    term = {
        "term_type": "found_city_within",
        "params": {"x": 12, "y": 7, "radius": 3},
    }
    existing = ObservedCity(2, 10, 12, 7)
    baseline = capture_baseline(term, obs(1, cities=(existing,)))
    assert (
        verify_term(
            term,
            baseline,
            {},
            obs(3, cities=(existing,), zone_distances={(10, 12, 7): 0}),
            due_turn=3,
        ).status
        == "failed"
    )
    new = ObservedCity(2, 11, 13, 7)
    assert (
        verify_term(
            term,
            baseline,
            {},
            obs(3, cities=(existing, new)),
            due_turn=3,
        ).status
        == "unverifiable"
    )


def test_dont_settle_and_keep_peace_persist_first_violation():
    settle_term = {
        "term_type": "dont_settle_within",
        "params": {"x": 12, "y": 7, "radius": 3},
    }
    settle_baseline = capture_baseline(settle_term, obs(1))
    city = ObservedCity(2, 11, 13, 7)
    breached = verify_term(
        settle_term,
        settle_baseline,
        {},
        obs(2, cities=(city,), zone_distances={(11, 12, 7): 1}),
        due_turn=4,
    )
    repaired = verify_term(
        settle_term, settle_baseline, breached.monitor, obs(3), due_turn=4
    )
    assert breached.status == repaired.status == "failed"
    assert set(breached.monitor) == {"violation_observation_id"}
    assert repaired.monitor == breached.monitor

    peace_term = {"term_type": "keep_peace_with", "params": {"player_id": 3}}
    peace_baseline = capture_baseline(peace_term, obs(1))
    at_war = verify_term(
        peace_term,
        peace_baseline,
        {},
        obs(2, wars=frozenset({(2, 3)})),
        due_turn=4,
    )
    at_peace = verify_term(
        peace_term, peace_baseline, at_war.monitor, obs(3), due_turn=4
    )
    assert at_war.status == at_peace.status == "failed"
    assert at_peace.monitor == at_war.monitor


def test_gold_reserve_transient_violation_remains_decisive():
    term = {"term_type": "maintain_gold_reserve", "params": {"min_gold": 400}}
    baseline = capture_baseline(term, obs(1))
    breached = verify_term(
        term, baseline, {}, obs(2, treasury_gold=399), due_turn=4
    )
    restored = verify_term(
        term, baseline, breached.monitor, obs(3, treasury_gold=500), due_turn=4
    )
    assert breached.status == restored.status == "failed"
    assert restored.monitor == breached.monitor


@pytest.mark.parametrize("term", [
    {
        "term_type": "found_city_within",
        "params": {"x": 12, "y": 7, "radius": 3},
    },
    {
        "term_type": "dont_settle_within",
        "params": {"x": 12, "y": 7, "radius": 3},
    },
])
def test_incomplete_city_baseline_cannot_support_a_verdict(term):
    incomplete = dataclasses.replace(
        obs(1), families_present=frozenset(ObservationFamily) - {ObservationFamily.CITIES}
    )
    baseline = capture_baseline(term, incomplete)
    city = ObservedCity(2, 11, 13, 7)
    at_due = obs(
        3, cities=(city,), zone_distances={(11, 12, 7): 1}
    )
    assert verify_term(term, baseline, {}, at_due, due_turn=3).status == "unverifiable"


@pytest.mark.parametrize(
    "term, before",
    [
        (
            {"term_type": "keep_peace_with", "params": {"player_id": 3}},
            obs(1, wars=frozenset({(2, 3)})),
        ),
        (
            {"term_type": "maintain_gold_reserve", "params": {"min_gold": 400}},
            obs(1, treasury_gold=399),
        ),
    ],
)
def test_baseline_condition_violation_keeps_first_observation_reference(term, before):
    result = verify_term(term, capture_baseline(term, before), {}, obs(2), due_turn=3)
    assert result.status == "failed"
    assert result.monitor == {"violation_observation_id": "turn:1"}


def test_destroy_camp_requires_a_later_complete_absence_observation():
    term = {"term_type": "destroy_camp", "params": {"x": 12, "y": 7}}
    baseline = capture_baseline(term, obs(1, camps=frozenset({(12, 7)})))
    same_turn = verify_term(term, baseline, {}, obs(1), due_turn=3)
    later = verify_term(term, baseline, same_turn.monitor, obs(2), due_turn=3)
    assert (same_turn.status, later.status) == ("pending", "satisfied")


@pytest.mark.parametrize(
    "term, before, missing_family",
    [
        (
            {"term_type": "keep_peace_with", "params": {"player_id": 3}},
            obs(1, wars=frozenset({(2, 3)})),
            ObservationFamily.DIPLOMACY,
        ),
        (
            {"term_type": "maintain_gold_reserve", "params": {"min_gold": 400}},
            obs(1, treasury_gold=399),
            ObservationFamily.TREASURY,
        ),
    ],
)
def test_initial_condition_violation_survives_a_missing_family(
    term, before, missing_family
):
    baseline = capture_baseline(term, before)
    missing = dataclasses.replace(
        obs(3),
        families_present=frozenset(ObservationFamily) - {missing_family},
        errors=(missing_family.value,),
    )
    result = verify_term(term, baseline, {}, missing, due_turn=3)
    assert result.status == "failed"
    assert result.monitor == {"violation_observation_id": "turn:1"}


@pytest.mark.parametrize(
    "term, terminal_status",
    [
        (
            {
                "term_type": "found_city_within",
                "params": {"x": 12, "y": 7, "radius": 3},
            },
            "failed",
        ),
        (
            {
                "term_type": "dont_settle_within",
                "params": {"x": 12, "y": 7, "radius": 3},
            },
            "satisfied",
        ),
    ],
)
@pytest.mark.parametrize(
    "baseline_city, captured_city",
    [
        (
            ObservedCity(
                3, 10, 13, 7, component_id=700, original_owner=3
            ),
            ObservedCity(
                2, 90, 13, 7, component_id=700, original_owner=3
            ),
        ),
        (
            ObservedCity(
                3, 11, 13, 7, component_id=701, original_owner=2
            ),
            ObservedCity(
                2, 91, 13, 7, component_id=701, original_owner=2
            ),
        ),
        (
            None,
            ObservedCity(
                2, 92, 13, 7, component_id=702, original_owner=3
            ),
        ),
    ],
)
def test_city_terms_ignore_foreign_capture_recapture_and_foreign_founding(
    term, terminal_status, baseline_city, captured_city
):
    baseline_cities = () if baseline_city is None else (baseline_city,)
    baseline = capture_baseline(term, obs(1, cities=baseline_cities))
    result = verify_term(
        term,
        baseline,
        {},
        obs(
            3,
            cities=(captured_city,),
            zone_distances={(captured_city.city_id, 12, 7): 1},
        ),
        due_turn=3,
    )
    assert result.status == terminal_status


@pytest.mark.parametrize(
    "term, expected",
    [
        (
            {
                "term_type": "found_city_within",
                "params": {"x": 12, "y": 7, "radius": 3},
            },
            "satisfied",
        ),
        (
            {
                "term_type": "dont_settle_within",
                "params": {"x": 12, "y": 7, "radius": 3},
            },
            "failed",
        ),
    ],
)
def test_city_terms_count_new_obligated_founded_component(term, expected):
    baseline = capture_baseline(
        term,
        obs(
            1,
            cities=(
                ObservedCity(
                    3, 10, 20, 20, component_id=700, original_owner=3
                ),
            ),
        ),
    )
    founded = ObservedCity(
        2, 99, 13, 7, component_id=800, original_owner=2
    )
    result = verify_term(
        term,
        baseline,
        {},
        obs(2, cities=(founded,), zone_distances={(99, 12, 7): 1}),
        due_turn=3,
    )
    assert result.status == expected


@pytest.mark.parametrize("formation,religious,categories", [
    ("FORMATION_CLASS_LAND_COMBAT", 0, {"military"}),
    ("FORMATION_CLASS_SUPPORT", 0, {"military"}),
    ("FORMATION_CLASS_CIVILIAN", 0, {"civilian"}),
    ("FORMATION_CLASS_CIVILIAN", 110, {"civilian", "religious"}),
])
def test_unit_category_is_definition_driven(formation, religious, categories):
    unit = ObservedUnit(2, 7, "UNIT_X", formation, religious, 1, 1)
    assert unit_categories(unit) == categories


def test_upgrade_same_identity_is_not_acquisition_but_new_identity_is():
    term = {"term_type": "forbid_unit_acquisition", "params": {"category": "military"}}
    old = ObservedUnit(2, 7, "UNIT_WARRIOR", "FORMATION_CLASS_LAND_COMBAT", 0, 1, 1)
    upgraded = ObservedUnit(2, 7, "UNIT_SWORDSMAN", "FORMATION_CLASS_LAND_COMBAT", 0, 1, 1)
    new = ObservedUnit(2, 8, "UNIT_ARCHER", "FORMATION_CLASS_LAND_COMBAT", 0, 2, 1)
    baseline = capture_baseline(term, obs(1, units=(old,)))
    assert verify_term(term, baseline, {}, obs(2, units=(upgraded,)), due_turn=4).status == "pending"
    assert verify_term(term, baseline, {}, obs(2, units=(upgraded, new)), due_turn=4).status == "failed"


def test_keep_units_away_has_one_turn_grace_and_persists_transient_violation():
    term = {"term_type": "keep_units_away", "params": {
        "player_id": 1, "min_distance": 3, "unit_scope": "military"}}
    unit = ObservedUnit(2, 7, "UNIT_WARRIOR", "FORMATION_CLASS_LAND_COMBAT", 0, 5, 5)
    baseline = capture_baseline(term, obs(10, units=(unit,), unit_distances={(2, 7, 1): 1}))
    grace = verify_term(term, baseline, {}, obs(11, units=(unit,), unit_distances={(2, 7, 1): 1}), 15)
    breach = verify_term(term, baseline, grace.monitor, obs(12, units=(unit,), unit_distances={(2, 7, 1): 1}), 15)
    left = verify_term(term, baseline, breach.monitor, obs(13, units=(unit,), unit_distances={(2, 7, 1): 5}), 15)
    assert (grace.status, breach.status, left.status) == ("pending", "failed", "failed")


def test_allow_only_category_accepts_overlap_and_rejects_another_new_category():
    term = {
        "term_type": "allow_only_unit_category",
        "params": {"category": "civilian"},
    }
    religious = ObservedUnit(
        2, 8, "UNIT_MISSIONARY", "FORMATION_CLASS_CIVILIAN", 100, 2, 1
    )
    military = ObservedUnit(
        2, 9, "UNIT_WARRIOR", "FORMATION_CLASS_LAND_COMBAT", 0, 3, 1
    )
    baseline = capture_baseline(term, obs(1))
    allowed = verify_term(term, baseline, {}, obs(2, units=(religious,)), 4)
    rejected = verify_term(
        term, baseline, allowed.monitor, obs(3, units=(religious, military)), 4
    )
    assert (allowed.status, rejected.status) == ("pending", "failed")


def test_post_start_upgrade_of_seen_identity_is_not_another_acquisition():
    term = {
        "term_type": "allow_only_unit_category",
        "params": {"category": "civilian"},
    }
    civilian = ObservedUnit(
        2, 7, "UNIT_BUILDER", "FORMATION_CLASS_CIVILIAN", 0, 2, 1
    )
    changed = ObservedUnit(
        2, 7, "UNIT_WARRIOR", "FORMATION_CLASS_LAND_COMBAT", 0, 2, 1
    )
    baseline = capture_baseline(term, obs(10))
    acquired = verify_term(term, baseline, {}, obs(11, units=(civilian,)), 13)
    changed_type = verify_term(
        term, baseline, acquired.monitor, obs(12, units=(changed,)), 13
    )
    assert (acquired.status, changed_type.status) == ("pending", "pending")


def test_military_cap_applies_at_favor_start_and_persists_first_excess():
    term = {"term_type": "military_unit_cap", "params": {"max_units": 1}}
    units = (
        ObservedUnit(2, 7, "UNIT_WARRIOR", "FORMATION_CLASS_LAND_COMBAT", 0, 1, 1),
        ObservedUnit(2, 8, "UNIT_BATTERING_RAM", "FORMATION_CLASS_SUPPORT", 0, 2, 1),
    )
    baseline = capture_baseline(term, obs(10, units=units))
    result = verify_term(term, baseline, {}, obs(11), due_turn=15)
    assert result.status == "failed"
    assert result.monitor == {
        "violation_observation_id": "turn:10",
        "violation_count": 2,
    }


def test_withdrawal_requires_complete_current_distances():
    term = {"term_type": "withdraw_units_from", "params": {
        "player_id": 1, "min_distance": 3, "unit_scope": "military"}}
    military = ObservedUnit(
        2, 7, "UNIT_WARRIOR", "FORMATION_CLASS_LAND_COMBAT", 0, 5, 5
    )
    civilian = ObservedUnit(
        2, 8, "UNIT_BUILDER", "FORMATION_CLASS_CIVILIAN", 0, 6, 5
    )
    baseline = capture_baseline(term, obs(10, units=(military, civilian)))

    pending = verify_term(
        term,
        baseline,
        {},
        obs(11, units=(military, civilian), unit_distances={(2, 7, 1): 2}),
        12,
    )
    incomplete = verify_term(
        term, baseline, pending.monitor, obs(12, units=(military,)), 12
    )
    complete = verify_term(
        term,
        baseline,
        pending.monitor,
        obs(12, units=(military,), unit_distances={(2, 7, 1): 3}),
        12,
    )
    assert (pending.status, incomplete.status, complete.status) == (
        "pending", "unverifiable", "satisfied"
    )


def test_withdrawal_distance_records_are_complete_unit_family_evidence():
    term = {"term_type": "withdraw_units_from", "params": {
        "player_id": 1, "min_distance": 3, "unit_scope": "all"}}
    unit = ObservedUnit(
        2, 7, "UNIT_BUILDER", "FORMATION_CLASS_CIVILIAN", 0, 5, 5
    )
    observation = obs(
        12,
        families_present=frozenset({ObservationFamily.UNITS}),
        units=(unit,),
        unit_distances={(2, 7, 1): 3},
    )
    result = verify_term(
        term, capture_baseline(term, obs(10)), {}, observation, due_turn=12
    )
    assert result.status == "satisfied"


def test_withdrawal_known_deadline_breach_is_decisive_despite_another_gap():
    term = {"term_type": "withdraw_units_from", "params": {
        "player_id": 1, "min_distance": 3, "unit_scope": "all"}}
    units = (
        ObservedUnit(2, 7, "UNIT_BUILDER", "FORMATION_CLASS_CIVILIAN", 0, 5, 5),
        ObservedUnit(2, 8, "UNIT_SETTLER", "FORMATION_CLASS_CIVILIAN", 0, 6, 5),
    )
    result = verify_term(
        term,
        capture_baseline(term, obs(10)),
        {},
        obs(12, units=units, unit_distances={(2, 7, 1): 2}),
        due_turn=12,
    )
    assert result.status == "failed"


def test_keep_units_away_partial_distance_gap_is_terminally_unverifiable():
    term = {"term_type": "keep_units_away", "params": {
        "player_id": 1, "min_distance": 3, "unit_scope": "all"}}
    unit = ObservedUnit(
        2, 7, "UNIT_BUILDER", "FORMATION_CLASS_CIVILIAN", 0, 5, 5
    )
    baseline = capture_baseline(term, obs(10, units=(unit,)))
    missing = verify_term(term, baseline, {}, obs(12, units=(unit,)), 13)
    at_due = verify_term(
        term,
        baseline,
        missing.monitor,
        obs(13, units=(unit,), unit_distances={(2, 7, 1): 5}),
        13,
    )
    assert (missing.status, at_due.status) == ("pending", "unverifiable")


def test_keep_units_away_known_breach_is_decisive_despite_another_gap():
    term = {"term_type": "keep_units_away", "params": {
        "player_id": 1, "min_distance": 3, "unit_scope": "all"}}
    units = (
        ObservedUnit(2, 7, "UNIT_BUILDER", "FORMATION_CLASS_CIVILIAN", 0, 5, 5),
        ObservedUnit(2, 8, "UNIT_SETTLER", "FORMATION_CLASS_CIVILIAN", 0, 6, 5),
    )
    result = verify_term(
        term,
        capture_baseline(term, obs(10)),
        {},
        obs(12, units=units, unit_distances={(2, 7, 1): 2}),
        due_turn=13,
    )
    assert result.status == "failed"


@pytest.mark.parametrize(
    "term",
    [
        {"term_type": "forbid_unit_acquisition", "params": {"category": "siege"}},
        {"term_type": "forbid_unit_acquisition", "params": {"category": []}},
        {"term_type": "allow_only_unit_category", "params": {"category": 3}},
        {"term_type": "military_unit_cap", "params": {"max_units": -1}},
        {"term_type": "military_unit_cap", "params": {"max_units": True}},
        {"term_type": "withdraw_units_from", "params": {
            "player_id": 2, "min_distance": 3, "unit_scope": "military"}},
        {"term_type": "keep_units_away", "params": {
            "player_id": 1, "min_distance": -1, "unit_scope": "all"}},
        {"term_type": "keep_units_away", "params": {
            "player_id": 1, "min_distance": 3, "unit_scope": "civilian"}},
        {"term_type": "keep_units_away", "params": {
            "player_id": 1, "min_distance": 3, "unit_scope": []}},
    ],
)
def test_unit_term_parameter_validation_is_strict(term):
    with pytest.raises(ValueError):
        validate_term(term, validation_context())


@pytest.mark.parametrize(
    "term_type", ["withdraw_units_from", "keep_units_away"]
)
@pytest.mark.parametrize("min_distance", [0, 11])
def test_unit_distance_terms_reject_values_outside_exact_bounds(
    term_type, min_distance
):
    term = {
        "term_type": term_type,
        "params": {
            "player_id": 1,
            "min_distance": min_distance,
            "unit_scope": "military",
        },
    }
    with pytest.raises(ValueError, match="min_distance must be 1..10"):
        validate_term(term, validation_context())


@pytest.mark.parametrize(
    "term_type", ["withdraw_units_from", "keep_units_away"]
)
@pytest.mark.parametrize("min_distance", [1, 10])
def test_unit_distance_terms_accept_boundary_values(term_type, min_distance):
    term = {
        "term_type": term_type,
        "params": {
            "player_id": 1,
            "min_distance": min_distance,
            "unit_scope": "all",
        },
    }
    assert validate_term(term, validation_context()) == term


@pytest.mark.parametrize(
    "term_type", ["withdraw_units_from", "keep_units_away"]
)
def test_unit_distance_terms_reject_bool_as_min_distance(term_type):
    term = {
        "term_type": term_type,
        "params": {
            "player_id": 1,
            "min_distance": True,
            "unit_scope": "all",
        },
    }
    with pytest.raises(ValueError, match="min_distance must be an integer"):
        validate_term(term, validation_context())


def test_distance_terms_compile_protected_players_and_observation_families():
    request = compile_observation_request([
        {"term_type": "withdraw_units_from", "params": {
            "player_id": 3, "min_distance": 2, "unit_scope": "all"}},
        {"term_type": "keep_units_away", "params": {
            "player_id": 1, "min_distance": 3, "unit_scope": "military"}},
    ])
    assert request.families == frozenset(
        {ObservationFamily.UNITS}
    )
    assert request.protected_players == (1, 3)
