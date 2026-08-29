"""Match duration model.

Duration is one of the ten published question families and, unlike the rest, it
is not a function of the scoreline alone.  It decomposes cleanly into two parts:

1. **How many games are played.**  This dominates.  Total games explains about
   84% of the variance in match minutes on hard court (r = 0.91 ATP, 0.92 WTA),
   and we already model the games distribution, so most of the answer comes free.

2. **How fast the players play them.**  The residual is a real, strongly
   repeatable player trait, not noise: between-player variance in the duration
   residual is roughly 14 times what label-shuffling produces (p = 0.0005 over
   14,325 ATP hard-court player-matches), with a 40-minute spread from fastest to
   slowest.  The extremes are exactly who you would expect — Murray and Nadal
   slowest, big servers such as Opelka and Isner fastest.

Fitted on ATP/WTA hard court 2021-2026, excluding retirements and matches under
20 or over 400 minutes::

    ATP Bo3   minutes = 4.93 * games -  8.8   residual sd 13.5
    ATP Bo5   minutes = 4.69 * games - 10.6   residual sd 19.4   (slams only)
    WTA Bo3   minutes = 5.16 * games - 12.2   residual sd 13.4   (slams only)

The best-of-five and women's best-of-three rows are fitted on hard-court *slam*
matches specifically (ATP n=2,777, WTA n=2,624, r=0.911 both), not on all hard
courts.  Slam matches run slightly faster per game than the tour average, and
the difference is not cosmetic: at a typical 37 games the all-surface slope
overstates the match by about five minutes, which is one whole step on the
five-minute grid the competition sets its thresholds on.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

#: ``(tour, best_of) -> (slope, intercept, residual_sd)`` for minutes on games.
DURATION_FIT = {
    ("ATP", 3): (4.93, -8.8, 13.5),
    ("ATP", 5): (4.69, -10.6, 19.4),
    ("WTA", 3): (5.16, -12.2, 13.4),
}

#: Cap on the per-player pace adjustment, in minutes per match.  The measured
#: spread is about +/-20 minutes per player; two slow players together should not
#: be allowed to compound without limit.
PACE_CAP_MINUTES = 25.0


@dataclass(frozen=True)
class DurationForecast:
    """Predicted match duration and the pieces it was built from."""

    expected_minutes: float
    sd_minutes: float
    from_games: float
    pace_adjustment: float

    def probability_over(self, threshold_minutes: float) -> float:
        """``P(duration > threshold)`` under a normal approximation.

        The residual around the games fit is close to symmetric and unimodal, so
        a normal is adequate; the dominant uncertainty is the games distribution
        itself, which the simulator handles exactly.
        """
        if self.sd_minutes <= 0:
            return float(self.expected_minutes > threshold_minutes)
        z = (float(threshold_minutes) - self.expected_minutes) / self.sd_minutes
        return float(stats.norm.sf(z))


def minutes_from_games(games: float, tour: str = "ATP", best_of: int = 3) -> float:
    """Expected minutes for a match of ``games`` games."""
    slope, intercept, _ = DURATION_FIT.get((tour, best_of), DURATION_FIT[("ATP", 3)])
    return slope * float(games) + intercept


def forecast_duration(
    expected_games: float,
    tour: str = "ATP",
    best_of: int = 3,
    *,
    games_sd: float = 0.0,
    pace_a: float = 0.0,
    pace_b: float = 0.0,
) -> DurationForecast:
    """Forecast match duration from the games distribution and player pace.

    Parameters
    ----------
    expected_games:
        Mean total games, taken from the match simulation.
    games_sd:
        Standard deviation of total games from the same simulation.  Folding it
        in matters: uncertainty about *how many* games are played usually exceeds
        uncertainty about how long each one takes.
    pace_a, pace_b:
        Per-player duration residuals in minutes, positive meaning slower than
        expected.  Averaged rather than summed — both players are on court for
        every point, so a fast player partly offsets a slow one.
    """
    slope, intercept, residual_sd = DURATION_FIT.get((tour, best_of), DURATION_FIT[("ATP", 3)])
    base = slope * float(expected_games) + intercept
    pace = float(np.clip((pace_a + pace_b) / 2.0, -PACE_CAP_MINUTES, PACE_CAP_MINUTES))

    # Total variance = pace-residual variance + variance propagated from games.
    games_variance = (slope * float(games_sd)) ** 2
    sd = float(np.sqrt(residual_sd**2 + games_variance))

    return DurationForecast(
        expected_minutes=base + pace,
        sd_minutes=sd,
        from_games=base,
        pace_adjustment=pace,
    )


def player_pace(long, *, min_matches: int = 15) -> dict[str, float]:
    """Estimate each player's duration residual, in minutes.

    Fits ``minutes ~ games`` across the whole sample, then averages each player's
    residual.  A positive value means the player's matches run longer than their
    scoreline implies.

    ``long`` must carry ``player``, ``minutes`` and ``total_games``.
    """
    frame = long.dropna(subset=["minutes", "total_games", "player"])
    frame = frame[(frame["minutes"] > 20) & (frame["minutes"] < 400)]
    if len(frame) < 50:
        return {}

    coefficients = np.polyfit(frame["total_games"], frame["minutes"], 1)
    residual = frame["minutes"] - np.polyval(coefficients, frame["total_games"])

    grouped = residual.groupby(frame["player"])
    means = grouped.mean()
    counts = grouped.size()
    keep = counts >= min_matches

    # Shrink toward zero so a player with fifteen matches is not treated as
    # confidently as one with two hundred.
    weight = counts[keep] / (counts[keep] + min_matches)
    return (means[keep] * weight).to_dict()
