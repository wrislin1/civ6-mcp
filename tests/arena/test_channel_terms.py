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
        "destroy_camp",
        "dont_settle_within",
        "found_city_within",
        "declare_war_on",
        "keep_peace_with",
        "maintain_gold_reserve",
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
