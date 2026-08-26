"""Tests for the analytic point -> game -> set -> match model."""

from __future__ import annotations

import pytest

from bot.models.point import (
    game_prob,
    match_shape,
    match_win_prob,
    serve_point_prob,
    set_distribution,
    set_win_prob,
    tiebreak_prob,
)


def test_game_prob_is_monotonic_in_point_prob():
    probs = [game_prob(p) for p in (0.4, 0.5, 0.6, 0.7, 0.8)]
    assert probs == sorted(probs)


def test_game_prob_at_even_points_is_even():
    assert game_prob(0.5) == pytest.approx(0.5, abs=1e-9)


def test_tour_average_serve_reproduces_known_hold_rates():
    """Tour-average inputs must reproduce the observed hold rates on hard court."""
    atp = game_prob(serve_point_prob(0.645, 0.355, "ATP"))
    wta = game_prob(serve_point_prob(0.585, 0.415, "WTA"))
    assert atp == pytest.approx(0.80, abs=0.03), "ATP hard-court hold is ~80%"
    assert wta == pytest.approx(0.68, abs=0.04), "WTA hard-court hold is ~67%"


def test_serve_point_prob_is_symmetric_for_average_players():
    assert serve_point_prob(0.645, 0.355, "ATP") == pytest.approx(0.645)


def test_set_distribution_sums_to_one():
    dist = set_distribution(0.65, 0.62)
    assert sum(dist.values()) == pytest.approx(1.0, abs=1e-9)


def test_set_distribution_only_contains_legal_scores():
    for (a, b, tb), _ in set_distribution(0.65, 0.60).items():
        assert max(a, b) in (6, 7)
        if tb:
            assert {a, b} == {6, 7}
        else:
            assert abs(a - b) >= 2 or (max(a, b) == 7 and min(a, b) == 5)


def test_stronger_server_wins_more_sets():
    strong = set_win_prob(set_distribution(0.72, 0.60))
    even = set_win_prob(set_distribution(0.65, 0.65))
    assert strong > even > 0.45


def test_tiebreak_is_even_for_identical_players():
    assert tiebreak_prob(0.6, 0.6) == pytest.approx(0.5, abs=0.02)


def test_longer_tiebreak_favours_the_better_player():
    """A 10-point tiebreak gives the stronger player more chances to convert."""
    short = tiebreak_prob(0.70, 0.60, target=7)
    long_tb = tiebreak_prob(0.70, 0.60, target=10)
    assert long_tb > short


def test_match_win_prob_formulas():
    assert match_win_prob(0.5, 3) == pytest.approx(0.5)
    assert match_win_prob(0.5, 5) == pytest.approx(0.5)
    # Best-of-five amplifies an edge relative to best-of-three.
    assert match_win_prob(0.6, 5) > match_win_prob(0.6, 3)


def test_match_win_prob_rejects_other_formats():
    with pytest.raises(ValueError):
        match_win_prob(0.5, 4)


def test_match_shape_probabilities_are_valid():
    shape = match_shape(0.66, 0.63, best_of=5)
    values = shape.as_dict()
    for key, value in values.items():
        if key.startswith("p_"):
            assert 0.0 <= value <= 1.0, f"{key} out of range"
    assert 0 < shape.expected_total_games < 100
    assert 3.0 <= shape.expected_sets <= 5.0


def test_best_of_five_has_more_tiebreaks_but_fewer_deciders():
    """The central structural asymmetry between the two draws.

    Men play best-of-five and reach a tiebreak far more often, but reaching a
    *deciding* set requires 2-2 rather than 1-1, so it is rarer than in the
    women's draw.  Forecasters who apply one mental model to both draws get one
    of these two backwards.
    """
    atp = match_shape(0.645, 0.645, best_of=5)
    wta = match_shape(0.585, 0.585, best_of=3)
    assert atp.p_any_tiebreak > wta.p_any_tiebreak
    assert atp.p_deciding_set < wta.p_deciding_set


def test_mismatch_produces_more_lopsided_sets_and_fewer_deciders():
    even = match_shape(0.645, 0.645, best_of=5)
    mismatch = match_shape(0.72, 0.58, best_of=5)
    assert mismatch.p_any_lopsided_set > even.p_any_lopsided_set
    assert mismatch.p_deciding_set < even.p_deciding_set
    assert mismatch.p_straight_sets > even.p_straight_sets


def test_identical_players_split_the_match():
    shape = match_shape(0.64, 0.64, best_of=5)
    assert shape.p_a_wins_match == pytest.approx(0.5, abs=0.02)
