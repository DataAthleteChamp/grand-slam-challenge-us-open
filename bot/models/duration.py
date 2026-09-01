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

    ATP Bo3   minutes = 4.93 * games -  8.8
    ATP Bo5   minutes = 4.77 * games -  6.3   (hard-court slams, 2023-2026)
    WTA Bo3   minutes = 5.50 * games - 15.9   (hard-court slams, 2023-2026)

The slam rows are fitted on 2023-2026 only.  Electronic line calling removed
player challenges from the US Open around 2023, which is a structural break in
the calibration data, and the eras genuinely differ: at a typical 37 games the
men's fit moves by 10.6 minutes between them, which is two whole steps on the
five-minute grid the competition sets its thresholds on.

The spread is **not** constant.  Residual sd runs about 1.4x higher on long
matches than short ones (ATP 15.2 against 22.2, WTA 10.3 against 17.2), so it is
modelled as a linear function of games rather than a single number.  Treating it
as constant would be over-confident on exactly the long matches where the
questions are hardest.

The best-of-five and women's best-of-three rows are fitted on hard-court *slam*
matches specifically (ATP n=2,777, WTA n=2,624, r=0.911 both), not on all hard
courts.  Slam matches run slightly faster per game than the tour average, and
the difference is not cosmetic: at a typical 37 games the all-surface slope
overstates the match by about five minutes, which is one whole step on the
five-minute grid the competition sets its thresholds on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

#: ``(tour, best_of) -> (slope, intercept, residual_sd)`` for minutes on games.
#: ``residual_sd`` is retained for callers that want a single number; the
#: pricing path uses the heteroscedastic form below.
DURATION_FIT = {
    ("ATP", 3): (4.93, -8.8, 13.5),
    ("ATP", 5): (4.774, -6.3, 19.1),
    ("WTA", 3): (5.499, -15.9, 14.4),
}

#: ``(tour, best_of) -> (sd_slope, sd_intercept)``: residual sd as a linear
#: function of total games, fitted on absolute residuals and rescaled by
#: sqrt(pi/2) to convert mean absolute deviation to a standard deviation.
DURATION_SD_FIT = {
    ("ATP", 3): (0.4359, 2.70),
    ("ATP", 5): (0.4359, 2.70),
    ("WTA", 3): (0.4687, 2.71),
}

#: Never let the modelled spread collapse: a duration forecast with a tiny sd
#: would push answers to 1 and 99 on the strength of a games estimate that is
#: itself uncertain.
MIN_DURATION_SD = 8.0


def residual_sd_for(games: float, tour: str, best_of: int) -> float:
    """Residual sd in minutes at a given number of games."""
    slope, intercept = DURATION_SD_FIT.get((tour, best_of), DURATION_SD_FIT[("ATP", 3)])
    return max(slope * float(games) + intercept, MIN_DURATION_SD)


#: Correction applied to the simulator's expected total games, in games.
#:
#: Measured against real hard-court slam matches, 2025-2026: the simulator
#: predicts 39.18 games where 36.94 are played on the men's draw, and 22.97
#: against 22.04 on the women's.  Left uncorrected this pushed every duration
#: forecast about 11 minutes long on the men's side, which is two steps on the
#: five-minute grid the competition sets its thresholds on, and put us above the
#: unconditional base rate on 60 of 64 Round 1 duration questions - a systematic
#: lean in a first round full of mismatches, where the opposite was expected.
#:
#: A flat offset rather than a fitted regression on purpose.  ``mean_total_games``
#: is an *expectation* over thousands of simulations, so it legitimately varies
#: less across matchups than single realised matches do; regressing one on the
#: other would rescale a quantity that is not mis-scaled, only mis-centred.
#:
#: The women's offset was retired once the between-set sigma was fitted per tour.
#: Both corrections were removing the *same* over-prediction, so applying them
#: together double-counted.  Checked against Pinnacle's total-games lines on 45
#: live US Open matches (they post per-player game totals, and Pinnacle is the
#: sharpest book on tennis totals), comparing the raw simulator before any
#: offset:
#:
#:   ATP  simulator - Pinnacle  +2.76 games  sd 3.17  n=24  t=+4.27  systematic
#:   WTA  simulator - Pinnacle  -0.48 games  sd 1.98  n=21  t=-1.12  no bias left
#:
#: So the men's offset is still earning its keep (residual after -2.24 is +0.52,
#: SE 0.65, indistinguishable from zero) while the women's had become harmful:
#: -0.48 - 0.93 = -1.41 games, SE 0.43, t = -3.3 against the sharpest available
#: benchmark.  The two independent estimates of the remaining women's raw bias
#: (+0.57 from the historical sigma sweep, -0.48 from Pinnacle) average to about
#: zero, so the offset goes to zero rather than being flipped positive on the
#: strength of one sample.
#:
#: The men's residual is consistent with Klaassen & Magnus (2001), who show that
#: points in tennis are *not* iid: servers win fewer points than iid predicts on
#: the most important points, so real matches contain more breaks - and therefore
#: fewer games - than an iid simulator produces.  That is a point-level effect
#: and cannot be fixed by the between-set sigma, which is why the men's draw
#: still needs an explicit offset while the women's no longer does.
GAMES_BIAS = {("ATP", 5): -2.24, ("ATP", 3): -1.20, ("WTA", 3): 0.0, ("WTA", 5): 0.0}


def correct_games(games: float, tour: str, best_of: int) -> float:
    """Remove the simulator's measured over-prediction of total games."""
    offset = GAMES_BIAS.get((tour, best_of), 0.0)
    return max(float(games) + offset, 6.0)


#: Weight given to a bookmaker's total-games line over the simulator's own.
#:
#: Measured against Pinnacle on 45 live US Open matches, the two disagree far
#: more than a shared offset would explain: per-match correlation is only +0.52
#: on the men's draw and +0.63 on the women's, with sd(simulator - Pinnacle) of
#: 3.17 and 1.98 games.  They disagree about *which* matches run long, not just
#: about the average, so this is a genuine second opinion rather than a
#: recalibration of the first.
#:
#: 0.60 rather than the variance-optimal weight.  If both were unbiased with
#: independent errors, sigma_sim^2 + sigma_pin^2 = 3.17^2, and a sharp closing
#: total on a Grand Slam match is one of the better-priced numbers in tennis, so
#: the optimal weight on Pinnacle would be well above 0.8.  It is held down to
#: 0.6 for two reasons: the market number is *inferred* from per-player game
#: totals rather than read directly, and unlike every other constant here it has
#: not yet been validated against settled results.  Re-fit it once enough
#: duration questions have settled with a line recorded against them.
GAMES_MARKET_WEIGHT = 0.60


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
        """``P(duration > threshold)`` under a lognormal.

        Match duration is right-skewed: a five-setter can run two hours past the
        median but nothing finishes in under about forty minutes.  The men's
        hard-court slam mean is 167.8 minutes against a median near 153, and a
        normal centred on the mean therefore puts too much weight above every
        threshold below it.  Measured on real 2025-2026 matches the normal
        predicted 0.66 at the 150-minute line where 0.58 was realised, and it
        over-predicted at all five thresholds tested on both draws.

        The lognormal is parameterised to carry the same mean and standard
        deviation, so this changes the shape of the tail without re-centring the
        forecast or discarding the fitted spread.
        """
        threshold = float(threshold_minutes)
        if threshold <= 0.0:
            return 1.0
        if self.sd_minutes <= 0:
            return float(self.expected_minutes > threshold)
        mean = max(float(self.expected_minutes), 1.0)
        variance = float(self.sd_minutes) ** 2
        sigma_sq = math.log1p(variance / (mean * mean))
        if sigma_sq <= 0:
            return float(mean > threshold)
        sigma = math.sqrt(sigma_sq)
        mu = math.log(mean) - 0.5 * sigma_sq
        z = (math.log(threshold) - mu) / sigma
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
    already_corrected: bool = False,
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
    already_corrected:
        Set when the caller has already applied ``correct_games`` — for example
        after blending the simulator against a bookmaker's total-games line,
        where the market number needs no correction of its own and applying the
        offset a second time would double-count it.
    """
    slope, intercept, _ = DURATION_FIT.get((tour, best_of), DURATION_FIT[("ATP", 3)])
    if not already_corrected:
        expected_games = correct_games(expected_games, tour, best_of)
    # Spread grows with match length by about 40% between short and long
    # matches, so it is read at the games estimate rather than fixed.
    residual_sd = residual_sd_for(expected_games, tour, best_of)
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
