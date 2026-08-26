"""Count and count-difference distributions for the player-statistic questions.

These cover the "will A record more X than B" family (aces, double faults,
winners) and the "will a player exceed N" family (unforced errors).

The single most important property of these questions is the **tie probability**.
Counts are small integers, so exact ties are common, and if the competition
requires a strict ``>`` then two identical players do *not* sit at 50%:

===============  ==============  ===============  ==========================
statistic        mean / player   P(tie), measured  even-matchup P(A > B)
===============  ==============  ===============  ==========================
double faults    2.9             0.158             0.421
aces             7.0             0.080             0.460
winners          29.1            0.029             0.485
unforced errors  29.0            0.030             0.485
===============  ==============  ===============  ==========================

Measured over 7,545 charted men's matches (Match Charting Project, ``Total`` rows).
Closed-form agreement is good: Skellam with mu=2.9 predicts P(tie)=0.167 vs 0.158
observed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class Comparison:
    """Outcome probabilities for a head-to-head count comparison."""

    p_a_greater: float
    p_tie: float
    p_b_greater: float

    def resolve(self, ties_count_as: str = "no") -> float:
        """Probability the market resolves YES for "A records more than B".

        ``ties_count_as`` must be ``"no"`` (strict ``>``, the usual convention),
        ``"yes"`` (``>=``), or ``"void"`` (renormalise, excluding ties).

        The settlement rule is worth confirming the moment markets open: it moves
        the fair price of an even double-fault matchup by roughly 8 points.
        """
        if ties_count_as == "no":
            return self.p_a_greater
        if ties_count_as == "yes":
            return self.p_a_greater + self.p_tie
        if ties_count_as == "void":
            live = self.p_a_greater + self.p_b_greater
            return self.p_a_greater / live if live > 0 else 0.5
        raise ValueError("ties_count_as must be 'no', 'yes' or 'void'")


def skellam_comparison(mu_a: float, mu_b: float) -> Comparison:
    """Compare two independent Poisson counts via the Skellam distribution.

    ``P(tie) = exp(-(mu_a + mu_b)) * I_0(2*sqrt(mu_a*mu_b))`` where ``I_0`` is the
    modified Bessel function of the first kind.

    Fast and closed-form, but it assumes Poisson counts and independent totals.
    Prefer :func:`binomial_comparison` when the number of service points is known,
    because tennis counts are binomial draws from a random number of trials.
    """
    mu_a, mu_b = max(mu_a, 1e-9), max(mu_b, 1e-9)
    p_tie = float(stats.skellam.pmf(0, mu_a, mu_b))
    p_a = float(stats.skellam.sf(0, mu_a, mu_b))
    return Comparison(p_a_greater=p_a, p_tie=p_tie, p_b_greater=1.0 - p_a - p_tie)


def binomial_comparison(
    rate_a: float,
    rate_b: float,
    trials_a: int,
    trials_b: int,
    *,
    overdispersion: float = 0.0,
) -> Comparison:
    """Compare two binomial counts by exact convolution over their supports.

    This is the right model for tennis: a player hits ``k`` aces out of ``n``
    service points, so the count is ``Binomial(n, rate)`` rather than Poisson.

    ``overdispersion`` (0 = pure binomial) widens each count toward a beta-binomial
    to absorb match-to-match variation in a player's true rate.  It is the
    intraclass correlation ``rho``; the beta prior is derived as
    ``alpha = rate * m``, ``beta = (1 - rate) * m`` with ``m = 1/rho - 1``.
    """
    pmf_a = _count_pmf(rate_a, trials_a, overdispersion)
    pmf_b = _count_pmf(rate_b, trials_b, overdispersion)

    # P(tie) = sum_k P(A = k) P(B = k); P(A > B) = sum_k P(A = k) P(B < k).
    length = min(len(pmf_a), len(pmf_b))
    cdf_b = np.cumsum(pmf_b)
    p_tie = float(np.dot(pmf_a[:length], pmf_b[:length]))
    p_a = 0.0
    for k in range(1, len(pmf_a)):
        below = cdf_b[min(k - 1, len(cdf_b) - 1)]
        p_a += pmf_a[k] * below
    p_a = float(p_a)
    return Comparison(p_a_greater=p_a, p_tie=p_tie, p_b_greater=max(0.0, 1.0 - p_a - p_tie))


def _count_pmf(rate: float, trials: int, overdispersion: float) -> np.ndarray:
    """PMF of a count over ``0..trials``, binomial or beta-binomial."""
    trials = max(int(trials), 1)
    rate = min(max(rate, 1e-6), 1.0 - 1e-6)
    support = np.arange(trials + 1)
    if overdispersion <= 0.0:
        return stats.binom.pmf(support, trials, rate)
    m = max(1.0 / overdispersion - 1.0, 1e-6)
    return stats.betabinom.pmf(support, trials, rate * m, (1.0 - rate) * m)


def threshold_probability(
    rate: float, trials: int, threshold: float, *, overdispersion: float = 0.0
) -> float:
    """``P(count > threshold)`` for a single player's count.

    Used for the "will a player rack up excessive unforced errors" family.  Note
    that unforced errors are scored subjectively — courtside statisticians agree
    on the forced/unforced call only three or four times in ten — so these
    forecasts should stay close to the base rate regardless of what the model says.
    """
    pmf = _count_pmf(rate, trials, overdispersion)
    support = np.arange(len(pmf))
    return float(pmf[support > threshold].sum())


def beta_comparison(
    wins_a: int, trials_a: int, wins_b: int, trials_b: int, *, prior: tuple[float, float] = (1.0, 1.0)
) -> float:
    """``P(theta_A > theta_B)`` for two independent Beta posteriors.

    Answers "does A have the higher true rate", which is the right question for
    comparing underlying skill.  For "who wins the comparison in *this* match",
    use :func:`binomial_comparison` — the single-match observation is far noisier
    than the underlying rate.
    """
    a0, b0 = prior
    post_a = (wins_a + a0, trials_a - wins_a + b0)
    post_b = (wins_b + b0, trials_b - wins_b + b0)
    grid = np.linspace(1e-6, 1 - 1e-6, 2000)
    pdf_a = stats.beta.pdf(grid, *post_a)
    cdf_b = stats.beta.cdf(grid, *post_b)
    return float(np.trapezoid(pdf_a * cdf_b, grid))
