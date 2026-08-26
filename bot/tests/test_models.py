"""Tests for count comparisons, shrinkage, lanes and scoring."""

from __future__ import annotations

import numpy as np
import pytest

from bot.data.baserates import COUNT_STATS, classify_matchup, structural
from bot.markets.taxonomy import Family
from bot.models.counts import (
    binomial_comparison,
    skellam_comparison,
    threshold_probability,
)
from bot.models.lanes import apply_lane, effective_entries, sharpen
from bot.models.scoring import (
    brier_score,
    expected_rbp,
    murphy_decomposition,
    recalibration_stage,
    relative_brier_points,
    spiegelhalter_z,
)
from bot.models.shrink import credibility_weight, height_prior_ace_rate, shrink_stat

# --------------------------------------------------------------------- counts


def test_skellam_tie_probability_matches_measured_double_faults():
    """Closed form must reproduce the measured double-fault tie rate.

    Measured over 7,545 charted men's matches: P(tie) = 0.158 at ~2.9 per player.
    """
    comparison = skellam_comparison(2.9, 2.9)
    assert comparison.p_tie == pytest.approx(0.158, abs=0.02)


def test_even_matchup_is_not_fifty_fifty_under_strict_comparison():
    """The core mispricing: ties push an even comparison well below 50%."""
    comparison = skellam_comparison(2.9, 2.9)
    assert comparison.resolve("no") == pytest.approx(0.421, abs=0.02)
    assert comparison.resolve("no") < 0.45


def test_tie_settlement_rule_changes_the_price_materially():
    comparison = skellam_comparison(2.9, 2.9)
    assert comparison.resolve("yes") - comparison.resolve("no") == pytest.approx(0.158, abs=0.02)
    assert comparison.resolve("void") == pytest.approx(0.5, abs=0.01)


def test_comparison_probabilities_sum_to_one():
    comparison = skellam_comparison(7.0, 5.0)
    total = comparison.p_a_greater + comparison.p_tie + comparison.p_b_greater
    assert total == pytest.approx(1.0, abs=1e-6)


def test_bigger_server_favoured_in_ace_comparison():
    comparison = skellam_comparison(12.0, 4.0)
    assert comparison.p_a_greater > 0.85


def test_binomial_comparison_agrees_with_skellam_for_even_players():
    binom = binomial_comparison(0.08, 0.08, 90, 90)
    skellam = skellam_comparison(7.2, 7.2)
    assert binom.p_a_greater == pytest.approx(skellam.p_a_greater, abs=0.05)


def test_overdispersion_increases_tie_and_flattens_comparison():
    tight = binomial_comparison(0.08, 0.06, 90, 90, overdispersion=0.0)
    loose = binomial_comparison(0.08, 0.06, 90, 90, overdispersion=0.05)
    assert loose.p_a_greater < tight.p_a_greater


def test_threshold_probability_is_monotonic():
    low = threshold_probability(0.30, 100, 25)
    high = threshold_probability(0.30, 100, 40)
    assert low > high


def test_invalid_tie_rule_rejected():
    with pytest.raises(ValueError):
        skellam_comparison(3.0, 3.0).resolve("maybe")


# ------------------------------------------------------------------ shrinkage


def test_credibility_weight_bounds():
    assert credibility_weight(0, 45) == 0.0
    assert credibility_weight(45, 45) == pytest.approx(0.5)
    assert credibility_weight(10_000, 45) > 0.99


def test_qualifier_is_shrunk_hard_toward_the_prior():
    """A qualifier with 8 matches keeps only ~15% of their observed rate."""
    result = shrink_stat(observed=0.20, n=8, prior=0.08, stat="ace_rate")
    assert result.weight == pytest.approx(8 / (8 + 45), abs=1e-6)
    assert result.estimate < 0.11


def test_established_player_keeps_most_of_their_own_rate():
    result = shrink_stat(observed=0.20, n=200, prior=0.08, stat="ace_rate")
    assert result.weight > 0.8
    assert result.estimate > 0.17


def test_height_prior_orders_players_correctly():
    """Height explains ~83% of ace-rate variance — the fallback for unknown players."""
    tall = height_prior_ace_rate(208, "ATP")
    short = height_prior_ace_rate(175, "ATP")
    assert tall > height_prior_ace_rate(188, "ATP") > short
    assert short > 0


def test_height_prior_handles_missing_height():
    assert height_prior_ace_rate(None, "ATP") == pytest.approx(0.084)


# ---------------------------------------------------------------------- lanes


def test_sharpen_is_identity_at_temperature_one():
    assert sharpen(0.73, 1.0) == pytest.approx(0.73)


def test_sharpen_pushes_away_from_fifty():
    assert sharpen(0.70, 0.5) > 0.70
    assert sharpen(0.30, 0.5) < 0.30


def test_sharpen_leaves_fifty_alone():
    assert sharpen(0.5, 0.25) == pytest.approx(0.5)


def test_apply_lane_returns_valid_api_integer():
    for lane in ("honest", "slight", "scenario"):
        value = apply_lane(0.73, Family.TIEBREAK, lane)
        assert isinstance(value, int)
        assert 1 <= value <= 99


def test_apply_lane_preserves_non_round_numbers():
    """Crowds cluster on multiples of five; we must not."""
    assert apply_lane(0.73, Family.TIEBREAK, "honest") == 73


def test_unforced_errors_are_pinned_to_the_base_rate():
    """The noisiest family must ignore a confident model."""
    value = apply_lane(0.90, Family.UNFORCED_ERRORS, "scenario", base_rate=0.50)
    assert 50 <= value <= 60, "an over-confident UE call must be pulled back"


def test_effective_entries_rewards_decorrelation():
    assert effective_entries(1.0) == pytest.approx(1.0)
    assert effective_entries(0.0) == pytest.approx(2**0.5, abs=1e-6)


# -------------------------------------------------------------------- scoring


def test_brier_score_basics():
    assert brier_score(np.array([1.0, 0.0]), np.array([1.0, 0.0])) == 0.0
    assert brier_score(np.array([0.5]), np.array([1.0])) == pytest.approx(0.25)


def test_relative_brier_points_reward_beating_the_crowd():
    rbp = relative_brier_points(np.array([0.8]), np.array([0.5]), np.array([1.0]))
    assert rbp[0] > 0


def test_multiplier_scales_relative_points():
    base = relative_brier_points(np.array([0.8]), np.array([0.5]), np.array([1.0]), 1.0)
    tripled = relative_brier_points(np.array([0.8]), np.array([0.5]), np.array([1.0]), 3.0)
    assert tripled[0] == pytest.approx(3.0 * base[0])


def test_truth_telling_is_optimal_under_relative_scoring():
    """Submitting the true probability maximises expected RBP, whatever the crowd does.

    E[RBP] = E[(c - y)^2] - E[(p - y)^2]; the crowd term is independent of p, so
    the optimum is p = mu.  This is why no crowd-guessing game is worth playing.
    """
    truth, crowd = 0.70, 0.55
    candidates = np.linspace(0.01, 0.99, 99)
    expected = [
        truth * ((crowd - 1) ** 2 - (p - 1) ** 2) + (1 - truth) * (crowd**2 - p**2)
        for p in candidates
    ]
    assert candidates[int(np.argmax(expected))] == pytest.approx(truth, abs=0.01)


def test_expected_rbp_scales_with_squared_crowd_bias():
    small = expected_rbp(0.55, 0.50)
    large = expected_rbp(0.80, 0.50)
    assert large == pytest.approx(36 * small, rel=0.01)


def test_murphy_decomposition_identity_is_exact_for_integer_forecasts():
    """Submissions are integers 1-99, so grouping by value makes BS = REL - RES + UNC exact."""
    rng = np.random.default_rng(0)
    p = np.round(rng.uniform(0.05, 0.95, 2000), 2)
    y = (rng.uniform(size=2000) < p).astype(float)
    d = murphy_decomposition(p, y)
    assert d.brier == pytest.approx(d.reliability - d.resolution + d.uncertainty, abs=1e-9)


def test_murphy_decomposition_with_coarse_bins_is_approximate():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.05, 0.95, 500)
    y = (rng.uniform(size=500) < p).astype(float)
    d = murphy_decomposition(p, y, n_bins=10)
    assert d.brier == pytest.approx(d.reliability - d.resolution + d.uncertainty, abs=0.01)


def test_perfect_calibration_has_near_zero_reliability():
    """Continuous forecasts must be binned; grouping by unique value degenerates."""
    rng = np.random.default_rng(1)
    p = rng.uniform(0.05, 0.95, 4000)
    y = (rng.uniform(size=4000) < p).astype(float)
    assert murphy_decomposition(p, y, n_bins=10).reliability < 0.01


def test_grouping_continuous_forecasts_by_value_degenerates():
    """Guard against silently mis-using the exact mode on continuous input.

    With unique float forecasts every group holds one observation, so the observed
    rate is 0 or 1 and reliability collapses to the Brier score. Real submissions
    are integers, so this is a documentation test, not a supported path.
    """
    rng = np.random.default_rng(1)
    p = rng.uniform(0.05, 0.95, 1000)
    y = (rng.uniform(size=1000) < p).astype(float)
    exact = murphy_decomposition(p, y)
    assert exact.reliability == pytest.approx(exact.brier, abs=1e-6)


def test_spiegelhalter_z_detects_overconfidence():
    p = np.full(300, 0.9)
    y = np.zeros(300)  # forecast 90%, nothing happened
    assert spiegelhalter_z(p, y) < -2


def test_recalibration_gates_block_early_overfitting():
    assert recalibration_stage(40) == "diagnose_only"
    assert recalibration_stage(200) == "intercept_shift"
    assert recalibration_stage(350) == "platt"
    assert recalibration_stage(600) == "isotonic"


# ----------------------------------------------------------------- base rates


def test_structural_rates_reflect_the_draw_asymmetry():
    atp, wta = structural("ATP", "all"), structural("WTA", "all")
    assert atp.tiebreak > wta.tiebreak
    assert atp.deciding_set < wta.deciding_set


def test_lopsided_sets_are_common_even_between_top_players():
    """The single largest measured crowd error: 63% of even men's matches
    contain a 6-0/6-1/6-2 set, but LLM forecasters price it near 32%."""
    assert structural("ATP", "even").lopsided_set > 0.6


def test_matchup_classifier():
    assert classify_matchup(3, 150) == "mismatch"
    assert classify_matchup(4, 9) == "even"
    assert classify_matchup(40, 60) == "all"
    assert classify_matchup(None, 12) == "all"


def test_count_stat_table_is_self_consistent():
    for stat, values in COUNT_STATS.items():
        expected = (1.0 - values["p_tie"]) / 2.0
        assert values["even_p_a_greater"] == pytest.approx(expected, abs=0.01), stat


# ------------------------------------------------- beta comparison (regression)


def test_beta_comparison_is_symmetric_under_identical_evidence():
    from bot.models.counts import beta_comparison

    assert beta_comparison(30, 100, 30, 100) == pytest.approx(0.5, abs=0.01)


def test_beta_comparison_favours_the_better_rate():
    from bot.models.counts import beta_comparison

    assert beta_comparison(45, 100, 30, 100) > 0.9


def test_beta_comparison_handles_asymmetric_priors():
    """Regression: both posteriors must take the prior's success parameter.

    Using the failure parameter for B's successes is invisible under the default
    symmetric prior (1, 1) and silently wrong for any other, so a symmetric-only
    test suite passes while the function is broken.
    """
    from bot.models.counts import beta_comparison

    # Identical evidence must stay a coin flip whatever the prior, as long as
    # the same prior is applied to both players.
    for prior in [(1.0, 1.0), (5.0, 1.0), (1.0, 8.0), (0.5, 2.0)]:
        assert beta_comparison(20, 60, 20, 60, prior=prior) == pytest.approx(0.5, abs=0.02), prior


def test_beta_comparison_prior_shifts_thin_samples_more_than_thick_ones():
    from bot.models.counts import beta_comparison

    thin = beta_comparison(3, 5, 2, 5, prior=(1.0, 1.0))
    thick = beta_comparison(300, 500, 200, 500, prior=(1.0, 1.0))
    assert thick > thin, "a large sample should express the difference more strongly"
