"""Tests for the Monte Carlo simulator."""

from __future__ import annotations

import pytest

from bot.models.point import match_shape
from bot.models.simulate import (
    FITTED_RHO,
    FITTED_SIGMA,
    SKILL_SCALE,
    SimulationConfig,
    matchup_probabilities,
    monte_carlo_error,
    simulate_match,
)

FAST = SimulationConfig(n_sims=1500, seed=3)
CONSTANT_P = SimulationConfig(sigma=0.0, rho=0.0, n_sims=1500, seed=3)


def test_probabilities_are_valid():
    result = simulate_match(0.645, 0.630, best_of=5, config=FAST).as_dict()
    for key, value in result.items():
        if key.startswith("p_"):
            assert 0.0 <= value <= 1.0, key


def test_simulation_agrees_with_the_analytic_model_when_shocks_are_off():
    """With sigma = 0 the simulator must reproduce the exact enumeration.

    This is the check that the sampling machinery itself is unbiased; any
    disagreement here is a bug rather than a modelling choice.
    """
    analytic = match_shape(0.645, 0.645, best_of=5)
    simulated = simulate_match(0.645, 0.645, best_of=5, config=CONSTANT_P)
    tolerance = 4 * monte_carlo_error(0.5, CONSTANT_P.n_sims) + 0.02
    assert simulated.p_deciding_set == pytest.approx(analytic.p_deciding_set, abs=tolerance)
    assert simulated.p_any_tiebreak == pytest.approx(analytic.p_any_tiebreak, abs=tolerance)


def test_form_shocks_increase_lopsided_sets():
    """The reason the shock model exists.

    Constant per-point probabilities cannot generate the runs that produce 6-1
    sets, so the analytic model under-produces them: 0.488 against 0.627
    measured. Backtested over 150 real matches, the fitted shocks take the bias
    from -0.092 to -0.000.
    """
    flat = simulate_match(0.645, 0.645, 5, config=CONSTANT_P)
    shocked = simulate_match(0.645, 0.645, 5, config=FAST)
    assert shocked.p_any_lopsided_set > flat.p_any_lopsided_set


def test_fitted_defaults_are_the_backtested_values():
    assert SimulationConfig().sigma == FITTED_SIGMA
    assert SimulationConfig().rho == FITTED_RHO
    assert FITTED_RHO < 0, "one player's good set should tend to be the other's bad one"


def test_stronger_player_wins_more_often():
    weak = simulate_match(0.645, 0.645, 5, config=FAST)
    strong = simulate_match(0.700, 0.590, 5, config=FAST)
    assert strong.p_a_wins_match > weak.p_a_wins_match > 0.4


def test_mismatch_shortens_the_match():
    even = simulate_match(0.645, 0.645, 5, config=FAST)
    mismatch = simulate_match(0.710, 0.580, 5, config=FAST)
    assert mismatch.p_deciding_set < even.p_deciding_set
    assert mismatch.p_straight_sets > even.p_straight_sets
    assert mismatch.mean_total_games < even.mean_total_games


def test_best_of_five_produces_more_tiebreaks_than_best_of_three():
    bo3 = simulate_match(0.645, 0.645, 3, config=FAST)
    bo5 = simulate_match(0.645, 0.645, 5, config=FAST)
    assert bo5.p_any_tiebreak > bo3.p_any_tiebreak


def test_every_question_comes_from_the_same_runs():
    """Coherence by construction: a match cannot be straight sets and also
    reach a deciding set."""
    result = simulate_match(0.66, 0.62, 5, config=FAST)
    assert result.p_straight_sets + result.p_deciding_set <= 1.0 + 1e-9
    assert result.p_first_set_tiebreak <= result.p_any_tiebreak + 1e-9


def test_total_games_distribution_is_returned_for_derived_questions():
    result = simulate_match(0.645, 0.645, 5, config=FAST)
    assert len(result.total_games) == FAST.n_sims
    high = result.probability_total_games_over(60)
    low = result.probability_total_games_over(20)
    assert 0.0 <= high < low <= 1.0


def test_monte_carlo_error_shrinks_with_sample_size():
    assert monte_carlo_error(0.5, 1_000) > monte_carlo_error(0.5, 100_000)
    assert monte_carlo_error(0.5, 20_000) < 0.004


def test_skill_scale_widens_the_matchup():
    """Ridge shrinkage leaves the spread between players too narrow, so matches
    are predicted too long; scaling the differences back out corrects it."""
    narrow = matchup_probabilities(0.645, 0.04, 0.02, -0.01, -0.02, skill_scale=1.0)
    wide = matchup_probabilities(0.645, 0.04, 0.02, -0.01, -0.02, skill_scale=2.0)
    assert wide[0] - wide[1] > narrow[0] - narrow[1]
    assert SKILL_SCALE > 1.0


def test_matchup_probabilities_stay_in_range():
    pa, pb = matchup_probabilities(0.645, 5.0, 5.0, -5.0, -5.0)
    assert 0.05 <= pa <= 0.95
    assert 0.05 <= pb <= 0.95


def test_simulation_is_reproducible():
    a = simulate_match(0.65, 0.62, 3, config=SimulationConfig(n_sims=800, seed=42))
    b = simulate_match(0.65, 0.62, 3, config=SimulationConfig(n_sims=800, seed=42))
    assert a.as_dict() == b.as_dict()
