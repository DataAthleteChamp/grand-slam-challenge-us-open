"""Tests for the duration model and Match Charting ingestion."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.data.charting import _add_charting_rates, summarise_charting
from bot.models.duration import (
    DURATION_FIT,
    PACE_CAP_MINUTES,
    forecast_duration,
    minutes_from_games,
    player_pace,
)

# ------------------------------------------------------------------- duration


def test_minutes_scale_with_games():
    assert minutes_from_games(30, "ATP", 5) > minutes_from_games(20, "ATP", 5)


def test_fitted_minutes_per_game_is_plausible():
    """Roughly four to five and a half minutes per game across formats."""
    for (tour, best_of), (slope, _, _) in DURATION_FIT.items():
        assert 4.0 <= slope <= 6.0, f"{tour} Bo{best_of} slope looks wrong"


def test_womens_matches_run_longer_per_game():
    """More rallies and fewer aces mean each game takes longer on the WTA side."""
    assert DURATION_FIT[("WTA", 3)][0] > DURATION_FIT[("ATP", 3)][0]


def test_forecast_duration_splits_games_and_pace():
    fast = forecast_duration(30, "ATP", 5, pace_a=-15, pace_b=-15)
    neutral = forecast_duration(30, "ATP", 5)
    slow = forecast_duration(30, "ATP", 5, pace_a=15, pace_b=15)
    assert fast.expected_minutes < neutral.expected_minutes < slow.expected_minutes
    assert neutral.pace_adjustment == 0.0
    assert neutral.from_games == pytest.approx(minutes_from_games(30, "ATP", 5))


def test_pace_is_averaged_not_summed():
    """Both players are on court for every point, so a fast player offsets a slow one."""
    mixed = forecast_duration(30, "ATP", 5, pace_a=20, pace_b=-20)
    assert mixed.pace_adjustment == pytest.approx(0.0)


def test_pace_adjustment_is_capped():
    extreme = forecast_duration(30, "ATP", 5, pace_a=200, pace_b=200)
    assert extreme.pace_adjustment == pytest.approx(PACE_CAP_MINUTES)


def test_games_uncertainty_widens_the_forecast():
    tight = forecast_duration(30, "ATP", 5, games_sd=0.0)
    loose = forecast_duration(30, "ATP", 5, games_sd=6.0)
    assert loose.sd_minutes > tight.sd_minutes


def test_probability_over_is_monotonic_and_bounded():
    forecast = forecast_duration(30, "ATP", 5)
    high = forecast.probability_over(forecast.expected_minutes + 60)
    mid = forecast.probability_over(forecast.expected_minutes)
    low = forecast.probability_over(forecast.expected_minutes - 60)
    assert 0.0 <= high < mid < low <= 1.0
    assert mid == pytest.approx(0.5, abs=0.01)


def test_player_pace_recovers_a_planted_slow_player():
    """The measured spread between fastest and slowest is about 40 minutes, so
    the estimator must recover a deliberate offset of that order."""
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(400):
        games = int(rng.integers(18, 40))
        rows.append(
            {"player": "NORMAL", "total_games": games, "minutes": 4.9 * games + rng.normal(0, 8)}
        )
    for _ in range(120):
        games = int(rng.integers(18, 40))
        rows.append(
            {"player": "SLOW", "total_games": games, "minutes": 4.9 * games + 20 + rng.normal(0, 8)}
        )
    pace = player_pace(pd.DataFrame(rows), min_matches=15)
    assert pace["SLOW"] > 10
    assert pace["SLOW"] > pace["NORMAL"]


def test_player_pace_shrinks_thin_samples():
    """A player with fifteen matches must not be rated as confidently as a regular.

    Both planted players are equally slow; only the sample size differs, so any
    difference in the estimate is the shrinkage doing its job.
    """
    rng = np.random.default_rng(1)
    rows = [
        {"player": "BASELINE", "total_games": int(g), "minutes": 4.9 * g + rng.normal(0, 5)}
        for g in rng.integers(18, 40, size=400)
    ]
    rows += [
        {"player": "REGULAR", "total_games": int(g), "minutes": 4.9 * g + 20 + rng.normal(0, 5)}
        for g in rng.integers(18, 40, size=200)
    ]
    rows += [
        {"player": "THIN", "total_games": int(g), "minutes": 4.9 * g + 20 + rng.normal(0, 5)}
        for g in rng.integers(18, 40, size=15)
    ]
    pace = player_pace(pd.DataFrame(rows), min_matches=15)
    assert pace["THIN"] < pace["REGULAR"], "a thin sample must be shrunk harder"
    assert pace["THIN"] > 0, "but the direction must survive"


def test_player_pace_handles_insufficient_data():
    assert player_pace(pd.DataFrame(columns=["player", "minutes", "total_games"])) == {}


# ------------------------------------------------------------------- charting


@pytest.fixture
def charted() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "match_id": "m1",
                "player": "Alice",
                "set": "Total",
                "serve_pts": 60,
                "return_pts": 55,
                "winners": 30,
                "unforced": 20,
                "aces": 6,
                "dfs": 2,
                "Surface": "Hard",
                "tour": "ATP",
            },
            {
                "match_id": "m1",
                "player": "Bob",
                "set": "Total",
                "serve_pts": 55,
                "return_pts": 60,
                "winners": 18,
                "unforced": 28,
                "aces": 3,
                "dfs": 4,
                "Surface": "Hard",
                "tour": "ATP",
            },
            {
                "match_id": "m2",
                "player": "Alice",
                "set": "Total",
                "serve_pts": 70,
                "return_pts": 65,
                "winners": 40,
                "unforced": 25,
                "aces": 9,
                "dfs": 1,
                "Surface": "Hard",
                "tour": "ATP",
            },
        ]
    )


def test_rates_use_all_points_not_just_service_points(charted):
    """Winners and errors can occur on any point, so the denominator is total points."""
    frame = _add_charting_rates(charted)
    row = frame.iloc[0]
    assert row["total_points"] == 115
    assert row["winner_rate"] == pytest.approx(30 / 115)


def test_ace_rate_uses_service_points_only(charted):
    frame = _add_charting_rates(charted)
    assert frame.iloc[0]["ace_rate_mcp"] == pytest.approx(6 / 60)


def test_summarise_aggregates_across_matches(charted):
    summary = summarise_charting(_add_charting_rates(charted))
    alice = summary[summary.player == "Alice"].iloc[0]
    assert alice["charted_matches"] == 2
    assert alice["winners"] == 70
    assert alice["winner_rate"] == pytest.approx(70 / (115 + 135))


def test_aggressive_player_separates_from_error_prone(charted):
    summary = summarise_charting(_add_charting_rates(charted)).set_index("player")
    assert summary.loc["Alice", "winner_rate"] > summary.loc["Bob", "winner_rate"]
    assert summary.loc["Bob", "unforced_rate"] > summary.loc["Alice", "unforced_rate"]


def test_surface_filter_applies(charted):
    assert summarise_charting(_add_charting_rates(charted), surface="Clay").empty


def test_empty_input_returns_empty_frame():
    assert summarise_charting(pd.DataFrame()).empty


# --------------------------------------------- duration anchoring (live)


def test_duration_base_rate_differs_hugely_by_tour():
    """Regression: the pricer anchored every duration question at 0.5.

    'Longer than 150 minutes' is 0.514 for a men's best-of-five and 0.083 for a
    women's best-of-three. Anchoring the women's question at 0.5 drags an
    uncertain answer six times too high, and the anchor is what the number
    decays to exactly when the model is least trusted.
    """
    from bot.data.baserates import duration_base

    assert duration_base(150, "ATP") == pytest.approx(0.514, abs=0.01)
    assert duration_base(150, "WTA") == pytest.approx(0.083, abs=0.01)
    assert duration_base(70, "ATP") > 0.98
    assert duration_base(70, "WTA") < 0.81


def test_duration_base_rate_is_monotone_and_bounded():
    from bot.data.baserates import duration_base

    for tour in ("ATP", "WTA"):
        values = [duration_base(n, tour) for n in range(50, 265, 5)]
        assert values == sorted(values, reverse=True), tour
        assert all(0.0 <= v <= 1.0 for v in values)
        # Outside the grid it must clamp, not extrapolate to nonsense.
        assert duration_base(1, tour) == pytest.approx(values[0])
        assert duration_base(9999, tour) == pytest.approx(values[-1])


def test_duration_base_rate_interpolates_between_grid_points():
    from bot.data.baserates import duration_base

    low, high = duration_base(145, "ATP"), duration_base(150, "ATP")
    middle = duration_base(147.5, "ATP")
    assert high < middle < low
    assert middle == pytest.approx((low + high) / 2, abs=0.001)


def test_duration_fit_uses_the_current_rules_era():
    """Electronic line calling removed player challenges around 2023, which is a
    structural break in the calibration data. The eras genuinely differ: at 37
    games the men's fit moves 10.6 minutes between them, two whole steps on the
    five-minute grid the thresholds are set on.
    """
    from bot.models.duration import DURATION_FIT

    slope, intercept, _ = DURATION_FIT[("ATP", 5)]
    assert slope == pytest.approx(4.774, abs=0.01)
    assert slope * 37 + intercept == pytest.approx(170.3, abs=2.0)

    slope, intercept, _ = DURATION_FIT[("WTA", 3)]
    assert slope == pytest.approx(5.499, abs=0.01)
    assert slope * 21.6 + intercept == pytest.approx(102.9, abs=2.0)


def test_duration_spread_grows_with_match_length():
    """Residual sd runs about 1.4x higher on long matches than short ones, so a
    single constant is over-confident on exactly the long matches where the
    questions are hardest."""
    from bot.models.duration import MIN_DURATION_SD, residual_sd_for

    for tour, best_of in (("ATP", 5), ("WTA", 3)):
        short = residual_sd_for(18, tour, best_of)
        long_match = residual_sd_for(45, tour, best_of)
        assert long_match > short * 1.5
        assert short >= MIN_DURATION_SD


def test_duration_spread_never_collapses():
    """A tiny sd would push answers to 1 and 99 on the strength of a games
    estimate that is itself uncertain."""
    from bot.models.duration import MIN_DURATION_SD, residual_sd_for

    assert residual_sd_for(0, "ATP", 5) >= MIN_DURATION_SD
    assert residual_sd_for(-5, "WTA", 3) >= MIN_DURATION_SD
