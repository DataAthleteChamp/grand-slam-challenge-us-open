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
from ..markets.taxonomy import Family  # noqa: F401

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
#: The honest lane leans on the bookmaker consensus for the match-winner family,
#: which is the single most efficiently priced market in tennis.
#:
#: The scenario lane used to halve that reliance (0.40) on the grounds that
#: leaning on our own simulation was an equally defensible stance and gave a
#: second draw from the outcome distribution.  The settled results have now
#: falsified the "equally defensible" half of that argument.  Sweeping the
#: match-winner market weight over 26 settled questions is strictly monotone in
#: favour of the market - skill per question runs -2.76 at weight 0.0, -0.31 at
#: 0.5 and +0.71 at 1.0 - because the market correlates +0.733 with outcomes and
#: our simulation only +0.585.  Downweighting it is not a different reading of
#: the same evidence, it is simply worse.
#:
#: Raised to 0.85 so the second bot is decorrelated by things that are not known
#: to be harmful - a larger between-set sigma and the tiebreak experiment -
#: rather than by a handicap on the one family where we have a genuinely superior
#: outside signal.  This is the same lesson the per-family shift experiment
#: taught: a second entry that is deliberately worse wastes the slot.
LANE_MARKET_SCALE = {"honest": 1.0, "slight": 0.9, "scenario": 0.85}

#: Lane name -> *offset* added to the tour's fitted between-set sigma.
#:
#: Market weight alone was not enough to separate the lanes.  Four of the five
#: live families have no market at all, so scaling market reliance left them
#: differing only by temperature - a monotone transform - and the two lanes came
#: out correlated at 0.991, which is one effective entry and a wasted slot.
#: Sigma is the size of the per-set form swing and it flows into every
#: simulation-derived family (tiebreaks, duration, match winner), so nudging it
#: separates the lanes in a way temperature cannot.
#:
#: This used to be an absolute sigma per lane (honest 0.05, scenario 0.10).  That
#: was written before the calibration was run per tour, and it silently applied
#: the men's fitted value to the women's draw.  ``TOUR_SIGMA`` now carries the
#: fitted base (ATP 0.05, WTA 0.11) and a lane only nudges it, so the second bot
#: stays decorrelated without being deliberately miscalibrated - the family-shift
#: experiment already showed that handicapping bot 2 costs points and teaches
#: nothing.
LANE_SIGMA_OFFSET = {"honest": 0.0, "slight": 0.01, "scenario": 0.02}

#: Lane name -> multiplier on model confidence.
#:
#: Confidence controls how far an answer is pulled back toward its base rate.
#: The scenario lane trusts the model further, which moves every family
#: including the four with no market.
LANE_CONFIDENCE_SCALE = {"honest": 1.0, "slight": 1.1, "scenario": 1.15}

#: Per-family probability shifts applied ONLY on the scenario lane.
#:
#: The first version of this experiment carried the per-family biases measured
#: over 11 settled matches (aces +0.087, double faults +0.128, tiebreak -0.105).
#: It has now been run and FALSIFIED, twice over:
#:
#:   1. After 37 settled matches the two lanes are indistinguishable: honest
#:      -0.46 RBP/question, scenario -0.47.  The shifts bought nothing.
#:   2. The underlying measurements themselves reversed sign as the sample
#:      tripled - double faults went +0.128 -> -0.090 and tiebreak went
#:      -0.105 -> +0.085.  Two of five flipping sign is what noise looks like,
#:      so the original shifts were pushing *against* the true direction.
#:
#: Chasing the new n=37 numbers would repeat the same mistake, so the aces and
#: double-fault shifts are retired outright.  Aces are now handled properly by
#: the ace-vulnerability model, which is fitted out-of-sample rather than to
#: this tournament; stacking a crude shift on top would double-count it.  Double
#: faults are ~80% irreducible noise (server identity explains 14-21% of the
#: variance), so there is nothing there to correct toward.
#:
#: One shift survives, because it is the only one with corroboration from a
#: source independent of this tournament.  Two separate datasets agree that the
#: simulator produces too many tiebreaks:
#:   - historical 2023-26 slam hard-court matches: simulated any-tiebreak rate
#:     runs +0.019 above realised;
#:   - the 37 settled matches here: we price 0.248 against a realised 0.162.
#: Tiebreak is also our only significantly positive family (+2.33 RBP/question,
#: 95% CI [+0.75, +3.57]), and the crowd is far more wrong than we are (+0.152),
#: so it is the pocket where extra accuracy is worth the most.
#:
#: Sized at -0.04: between the two estimates and nearer the corroborated
#: (smaller) one, because the live estimate is still only 37 matches deep.
LANE_FAMILY_SHIFT = {
    "scenario": {
        Family.TIEBREAK: -0.04,
    }
}

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
