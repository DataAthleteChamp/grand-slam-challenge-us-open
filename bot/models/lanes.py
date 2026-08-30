"""Submission lanes and probability post-processing.

Two Connected Models are permitted per account, and each is a separate leaderboard
entry.  Running both on the same model wastes a slot: adding a second entry with
correlation ``rho`` to the first leaves you with effective variance ``(1 + rho)/2``,
so at ``rho = 1`` you have one entry, and at ``rho = 0`` you have roughly 1.4.

The lanes differ by a *temperature* applied in log-odds space::

    p' = p^(1/T) / (p^(1/T) + (1 - p)^(1/T))

``T = 1`` leaves the honest model number alone.  ``T < 1`` sharpens toward 0/100 —
a deliberate variance play that maximises the chance one lane nails a match, at the
cost of expected value.  ``T > 1`` shrinks toward 50.

This is the same functional form as the Good Judgment Project's extremizing
transform, used there to *undo* the compression that averaging introduces.

Important: under relative Brier scoring the expected-value-maximising submission is
still your honest probability.  ``E[RBP] = E[(c - y)^2] - E[(p - y)^2]``, and the
first term does not depend on ``p``, so the optimum is ``p = true probability``
regardless of what the crowd does.  The sharpened lane is therefore *not* an
expected-value improvement — it is a rank-tournament play, and it belongs on a
secondary bot, never on the main entry.
"""

from __future__ import annotations

from ..data.baserates import DEFER_TO_BASE_RATE
from ..markets.taxonomy import Family

#: Lane name -> temperature.
#:
#: ``scenario`` is deliberately mild (0.80, not the old 0.45).  Temperature is a
#: *monotone* transform, so two lanes differing only in temperature always agree
#: on direction and disagree only on magnitude - which is weak decorrelation
#: bought at real expected-value cost.  The scenario lane now gets most of its
#: independence from a structural difference instead (see ``LANE_MARKET_SCALE``),
#: and uses temperature only for a light extra spread.
LANE_TEMPERATURE = {"honest": 1.0, "slight": 0.85, "scenario": 0.80}

#: Lane name -> multiplier on every family's market weight.
#:
#: This is where the real decorrelation comes from.  The honest lane leans on the
#: bookmaker consensus for the match-winner family, which is the single most
#: efficiently priced market in tennis.  The scenario lane halves that reliance
#: and leans on our own simulation instead.
#:
#: Both stances are defensible, which is the point: a second entry that is simply
#: a worse version of the first wastes the slot, while one that takes a genuinely
#: different and arguable position gives a second draw from the outcome
#: distribution.  Our simulation correlates 0.845 with the market and disagrees
#: by 14.8 points on average, so the two lanes genuinely diverge on direction and
#: not merely on confidence.
LANE_MARKET_SCALE = {"honest": 1.0, "slight": 0.75, "scenario": 0.40}

#: Lane name -> per-set form-shock sigma in the match simulation.
#:
#: Market weight alone was not enough.  Four of the five live families have no
#: market at all, so scaling market reliance left them differing only by
#: temperature - a monotone transform - and the two lanes came out correlated at
#: 0.991, which is one effective entry and a wasted slot.
#:
#: Sigma is the size of the per-set form swing, and it flows into every
#: simulation-derived family: tiebreaks, duration and match winner.  A larger
#: sigma produces more lopsided sets and fewer tiebreaks.  Our fitted value is
#: 0.05, but the simulator still over-predicts tiebreaks by about 0.089 against a
#: real-match backtest, so a larger form swing is a genuinely arguable reading of
#: the same evidence rather than a deliberate handicap.
LANE_SIGMA = {"honest": 0.05, "slight": 0.07, "scenario": 0.10}

#: Lane name -> multiplier on model confidence.
#:
#: Confidence controls how far an answer is pulled back toward its base rate.
#: The scenario lane trusts the model further, which moves every family
#: including the four with no market.
LANE_CONFIDENCE_SCALE = {"honest": 1.0, "slight": 1.1, "scenario": 1.35}

#: Submission bounds.  The API rejects 0 and 100 outright; we keep a wider margin
#: on the honest lane because the Brier penalty for confident error is quadratic.
HONEST_BOUNDS = (3, 97)
SCENARIO_BOUNDS = (2, 98)


def sharpen(probability: float, temperature: float) -> float:
    """Apply a temperature transform in log-odds space.

    ``probability`` is a fraction in ``(0, 1)``; the return value is too.
    """
    p = min(max(float(probability), 1e-6), 1.0 - 1e-6)
    if temperature == 1.0:
        return p
    power = 1.0 / max(temperature, 1e-6)
    num = p**power
    return num / (num + (1.0 - p) ** power)


def shrink_to_base(probability: float, base_rate: float, weight: float) -> float:
    """Blend a model probability toward a base rate.

    ``weight`` is the share given to the base rate, so ``1.0`` returns the base
    rate unchanged.  Used to enforce the defer-to-base-rate policy on question
    families where the underlying statistic is too noisy to model.
    """
    w = min(max(float(weight), 0.0), 1.0)
    return (1.0 - w) * float(probability) + w * float(base_rate)


def apply_lane(
    probability: float,
    family: Family,
    lane: str = "honest",
    *,
    base_rate: float | None = None,
) -> int:
    """Turn a model probability into the integer 1-99 to submit.

    Applies, in order: the defer-to-base-rate policy, the lane temperature, the
    lane bounds, and rounding.

    Note the deliberate absence of round-number snapping.  Crowds cluster on
    50/60/70/75/80 and the API accepts any integer, so submitting 73 rather than
    70 is a free differentiator repeated across every question.
    """
    p = float(probability)

    # Families whose underlying statistic is dominated by measurement noise are
    # pinned near the base rate no matter how confident the model is.  In the
    # Probability Cup the equivalent policy was learned the expensive way, from a
    # foul-market category that finished 125 relative points down.
    stat = _family_stat(family)
    if stat in DEFER_TO_BASE_RATE and base_rate is not None:
        p = shrink_to_base(p, base_rate, weight=0.85)
        lane = "honest"  # never sharpen a market we have chosen not to model

    temperature = LANE_TEMPERATURE.get(lane, 1.0)
    p = sharpen(p, temperature)

    low, high = SCENARIO_BOUNDS if lane == "scenario" else HONEST_BOUNDS
    value = int(round(p * 100))
    return max(low, min(high, value))


def _family_stat(family: Family) -> str:
    """Map a family to the statistic name used by the base-rate tables."""
    if family is Family.UNFORCED_ERRORS:
        return "unforced_errors"
    return family.value


def effective_entries(correlation: float) -> float:
    """Effective number of independent entries for two bots with ``correlation``.

    ``1 / sqrt((1 + rho) / 2)``: 1.0 at rho = 1, about 1.41 at rho = 0.  A useful
    sanity check that the two lanes are actually diversified.
    """
    rho = min(max(float(correlation), -1.0), 1.0)
    return (2.0 / (1.0 + rho)) ** 0.5 if rho > -1.0 else float("inf")
