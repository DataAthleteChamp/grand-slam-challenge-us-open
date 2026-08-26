"""Derive competition answers from correlated market quotes.

No venue prices most of our questions directly.  But several price quantities
that our questions are deterministic functions of, and a market on a *related*
quantity is often worth more than a model on the exact one.

The mapping, from strongest to weakest:

===========================================  ===================================
competition question                          market source
===========================================  ===================================
Will a set contain 10 or more games?          **direct read** — "Set 1 Games
                                              Over 9.5" *is* this probability
Will the match reach a deciding set?          **direct read** — Total Sets
                                              Over 2.5 (best of three)
Will the match include a tiebreak?            derived from the total-games
                                              distribution
Match duration over N minutes                 total games, then the
                                              minutes-per-game fit
Will a set end 6-0/6-1/6-2?                   no market; model only
Aces / double faults / winners / first serve  no market anywhere; model only
===========================================  ===================================

Several strikes on the same quantity trace the survival function, because a
prediction-market price *is* a probability: a quote on "Over 9.5" equals
``P(games > 9.5)``.  Differencing adjacent strikes gives the mass between them.
Note this is a *first* difference, not the second difference of the
Breeden-Litzenberger result — these are digital options, which are already the
first derivative of a vanilla call.

Prices must be monotone in the strike.  Where they are not, the market is stale
or crossed, and the repair is isotonic rather than trusting the raw quotes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .duration import forecast_duration

#: "Set 1 Games O/U 9.5", "Match O/U 22.5", "Total Sets O/U 2.5".
_STRIKE = re.compile(r"O/U\s*(\d+(?:\.\d+)?)", re.I)
_SET_NUMBER = re.compile(r"set\s*(\d+)\s*games", re.I)


@dataclass(frozen=True)
class StrikeLadder:
    """Survival probabilities across strikes on one quantity."""

    quantity: str
    strikes: tuple[float, ...]
    survival: tuple[float, ...]

    def probability_over(self, strike: float) -> float | None:
        """``P(X > strike)`` when that strike was quoted."""
        for k, p in zip(self.strikes, self.survival, strict=True):
            if abs(k - strike) < 1e-9:
                return p
        return None

    def mass_between(self) -> dict[tuple[float, float], float]:
        """Probability mass between adjacent strikes, by first differences."""
        out = {}
        for i in range(len(self.strikes) - 1):
            out[(self.strikes[i], self.strikes[i + 1])] = self.survival[i] - self.survival[i + 1]
        return out

    def expected_value(self, *, low: float | None = None, high: float | None = None) -> float:
        """Rough expected value of the quantity from the survival curve.

        Uses the midpoint of each interval, with open tails anchored by ``low``
        and ``high``.  Coarse, but adequate for feeding the duration model, whose
        own residual dominates this approximation error.
        """
        lo = low if low is not None else self.strikes[0] - 2.0
        hi = high if high is not None else self.strikes[-1] + 4.0
        edges = [lo, *self.strikes, hi]
        probabilities = [1.0, *self.survival, 0.0]

        total = 0.0
        for i in range(len(edges) - 1):
            mass = probabilities[i] - probabilities[i + 1]
            total += mass * (edges[i] + edges[i + 1]) / 2.0
        return total


def parse_ladders(questions: dict[str, float]) -> dict[str, StrikeLadder]:
    """Group quoted "over" probabilities into ladders by quantity.

    ``questions`` maps market text to the probability of the *over* outcome.
    """
    grouped: dict[str, list[tuple[float, float]]] = {}
    for text, probability in questions.items():
        strike_match = _STRIKE.search(text)
        if not strike_match:
            continue
        strike = float(strike_match.group(1))

        set_match = _SET_NUMBER.search(text)
        if set_match:
            quantity = f"set_{set_match.group(1)}_games"
        elif re.search(r"total\s*sets", text, re.I):
            quantity = "total_sets"
        elif re.search(r"match", text, re.I):
            quantity = "match_games"
        else:
            continue
        grouped.setdefault(quantity, []).append((strike, float(probability)))

    ladders = {}
    for quantity, pairs in grouped.items():
        pairs.sort()
        strikes = tuple(k for k, _ in pairs)
        survival = enforce_monotone(tuple(p for _, p in pairs))
        ladders[quantity] = StrikeLadder(quantity, strikes, survival)
    return ladders


def enforce_monotone(survival: tuple[float, ...]) -> tuple[float, ...]:
    """Force ``P(X > K)`` to be non-increasing in ``K``.

    A violation is an arbitrage: it implies negative probability mass between two
    strikes.  It happens when one strike is stale.  Repair by pooling adjacent
    violators — isotonic regression under the order constraint — rather than
    discarding, which would throw away a usable strike.
    """
    values = list(survival)
    changed = True
    while changed:
        changed = False
        for i in range(len(values) - 1):
            if values[i] < values[i + 1]:
                pooled = (values[i] + values[i + 1]) / 2.0
                values[i] = values[i + 1] = pooled
                changed = True
    return tuple(min(max(v, 0.0), 1.0) for v in values)


def deciding_set_probability(ladders: dict[str, StrikeLadder], best_of: int = 3) -> float | None:
    """``P(match reaches a deciding set)``.

    A direct read for best of three: reaching three sets *is* reaching the
    decider, so "Total Sets Over 2.5" answers the question outright.  For best of
    five the decider is a fifth set, which the 2.5 strike does not isolate.
    """
    ladder = ladders.get("total_sets")
    if ladder is None:
        return None
    if best_of == 3:
        return ladder.probability_over(2.5)
    return ladder.probability_over(4.5)


def long_set_probability(ladders: dict[str, StrikeLadder], set_number: int = 1) -> float | None:
    """``P(set contains 10 or more games)`` — a direct read of the 9.5 strike."""
    ladder = ladders.get(f"set_{set_number}_games")
    return ladder.probability_over(9.5) if ladder else None


def tiebreak_probability(
    ladders: dict[str, StrikeLadder], *, base_rate: float, best_of: int = 3
) -> float | None:
    """``P(match contains a tiebreak)``, inferred from the games distribution.

    There is no tiebreak market, but a tiebreak forces a set to 13 games, so
    long matches contain them far more often.  The measured correlation between
    "match has a tiebreak" and total games is about +0.42 to +0.47.

    This scales the base rate by how long the market expects the match to run,
    which is a genuine but indirect signal — treat it as a prior to blend with
    the simulation, not as an answer.
    """
    ladder = ladders.get("match_games")
    if ladder is None:
        return None
    expected = ladder.expected_value()
    typical = 22.0 if best_of == 3 else 38.0
    # An extra game above expectation lifts tiebreak odds by roughly three
    # points, from the measured games-tiebreak relationship.
    adjusted = base_rate + 0.03 * (expected - typical)
    return float(np.clip(adjusted, 0.02, 0.95))


def duration_probability(
    ladders: dict[str, StrikeLadder],
    threshold_minutes: float,
    *,
    tour: str = "ATP",
    best_of: int = 3,
    pace_a: float = 0.0,
    pace_b: float = 0.0,
) -> float | None:
    """``P(duration > threshold)`` from the market's total-games expectation.

    No venue prices match duration, but total games explains about 84% of the
    variance in minutes, so a liquid games market answers most of the question.
    """
    ladder = ladders.get("match_games")
    if ladder is None:
        return None
    expected_games = ladder.expected_value()
    spread = ladder.strikes[-1] - ladder.strikes[0] if len(ladder.strikes) > 1 else 2.0
    forecast = forecast_duration(
        expected_games,
        tour=tour,
        best_of=best_of,
        games_sd=max(spread, 2.0),
        pace_a=pace_a,
        pace_b=pace_b,
    )
    return forecast.probability_over(threshold_minutes)


def blend(model: float, market: float | None, market_weight: float = 0.6) -> float:
    """Blend a model probability with a market-derived one, in log-odds space.

    Weighted toward the market by default.  Two independent top-three finishers
    in the predecessor competition reported the same thing: a de-vigged market
    consensus carried the score, and the alpha layer added on top cost points in
    aggregate.  Deviate from the market where no market exists, not where one
    does.
    """
    if market is None:
        return float(model)
    w = min(max(float(market_weight), 0.0), 1.0)

    def logit(p: float) -> float:
        p = min(max(p, 1e-6), 1.0 - 1e-6)
        return float(np.log(p / (1.0 - p)))

    combined = (1.0 - w) * logit(model) + w * logit(market)
    return float(1.0 / (1.0 + np.exp(-combined)))
