"""Credibility shrinkage for player rate estimates.

Half the questions in this competition compare two players' statistics, and in the
first two rounds one of those players is usually a qualifier with almost no recent
data.  Using their raw observed rate is the single easiest way to lose points.

The fix is the actuarial credibility estimator (Buhlmann), which is algebraically
identical to the empirical-Bayes posterior mean of a Beta-Binomial hierarchy and to
the sabermetric "regress to the mean" formula::

    estimate = Z * observed + (1 - Z) * prior
    Z = n / (n + K)          K = EPV / VHM

``K`` is the number of observations at which the individual estimate and the
population prior carry equal weight.  Larger ``K`` means a noisier statistic that
needs more evidence before it can be trusted.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Credibility constants in *matches*, by statistic.
#:
#: Ace rate is the most repeatable statistic in tennis (year-over-year r of roughly
#: 0.80-0.90), which is why it tolerates a relatively small K.  Break-point
#: conversion is close to noise and needs an enormous sample before the observed
#: rate means anything — in practice, do not model it as a skill at all.
CREDIBILITY_K = {
    "ace_rate": 45.0,
    "df_rate": 55.0,
    "first_serve_in": 25.0,
    "first_serve_won": 35.0,
    "second_serve_won": 40.0,
    "winner_rate": 50.0,
    "unforced_rate": 70.0,
    "break_conversion": 60.0,
}


@dataclass(frozen=True)
class Credibility:
    """A shrunk rate estimate and the weight that produced it."""

    estimate: float
    weight: float
    observed: float
    prior: float
    n: float

    def as_dict(self) -> dict[str, float]:
        return {
            "estimate": round(self.estimate, 5),
            "weight": round(self.weight, 4),
            "observed": round(self.observed, 5),
            "prior": round(self.prior, 5),
            "n": round(self.n, 1),
        }


def credibility_weight(n: float, k: float) -> float:
    """Return ``Z = n / (n + K)``, the weight given to the observed rate."""
    if n <= 0:
        return 0.0
    return float(n) / (float(n) + float(k))


def shrink(observed: float, n: float, prior: float, k: float) -> Credibility:
    """Shrink ``observed`` toward ``prior`` with credibility constant ``k``.

    A qualifier with 8 recent matches and ``k = 45`` gets ``Z = 0.15``: 85% of the
    estimate comes from the prior, which is exactly the intended behaviour.
    """
    z = credibility_weight(n, k)
    return Credibility(
        estimate=z * observed + (1.0 - z) * prior,
        weight=z,
        observed=observed,
        prior=prior,
        n=n,
    )


def shrink_stat(observed: float, n: float, prior: float, stat: str) -> Credibility:
    """Shrink using the credibility constant registered for ``stat``."""
    return shrink(observed, n, prior, CREDIBILITY_K.get(stat, 50.0))


def height_prior_ace_rate(height_cm: float | None, tour: str = "ATP") -> float:
    """Prior ace rate from height alone — the fallback for an unknown player.

    Height explains 48% of the variance in men's ace rate and 29% of the
    women's, measured directly on hard courts 2023-2026 over players with at
    least 20 matches (r = 0.693 and 0.539).

    An earlier version of this docstring claimed 83%, which was wrong and made
    the prior look far stronger than it is. Height remains the right fallback
    because it is known for essentially every player including qualifiers, so it
    is the only thing we have about a player we have never otherwise measured -
    but it is a moderate predictor, not a near-deterministic one, and on the
    women's draw it explains under a third of the variance.

    The slope is calibrated so a 175cm player sits near the bottom of the observed
    range and a 205cm player near the top.
    """
    baseline = {"ATP": 0.084, "WTA": 0.035}[tour]
    if height_cm is None or not np.isfinite(height_cm):
        return baseline
    reference = {"ATP": 188.0, "WTA": 176.0}[tour]
    slope = {"ATP": 0.0022, "WTA": 0.0012}[tour]
    estimate = baseline + slope * (float(height_cm) - reference)
    floor, ceiling = baseline * 0.35, baseline * 2.4
    return float(min(max(estimate, floor), ceiling))


def estimate_k_split_half(
    first_half: np.ndarray, second_half: np.ndarray, n_per_half: float
) -> float:
    """Estimate a credibility constant empirically from split-half reliability.

    With reliability ``r`` measured on samples of size ``n``, the sample size at
    which reliability reaches 0.5 is ``K = n * (1 - r) / r``.  Use this to replace
    the defaults in :data:`CREDIBILITY_K` with values fitted to our own data
    instead of borrowed rules of thumb.
    """
    mask = np.isfinite(first_half) & np.isfinite(second_half)
    if mask.sum() < 3:
        return float("nan")
    r = float(np.corrcoef(first_half[mask], second_half[mask])[0, 1])
    if r <= 0:
        return float("inf")
    return float(n_per_half * (1.0 - r) / r)
