"""Compose models, market data and base rates into a submittable number.

This is the layer where every earlier decision becomes a single integer, and it
is deliberately the most defensive code in the project.  The failure mode that
costs points here is not a crash — it is a number that is wrong but plausible,
submitted, locked, and indistinguishable from a right one in the logs.

Three principles follow.

**Every price carries its provenance.**  A :class:`PricedQuestion` records the
base rate it started from, the model number, the market number, the blend weight
and the lane, so any submission can be reconstructed and argued with after the
fact.  A number with no explanation is a number that cannot be debugged.

**Routing is explicit and total.**  Every question family maps to exactly one
pricing path, and anything unrecognised falls to the base rate rather than to
whichever branch happens to catch it.

**Confidence degrades rather than failing.**  Missing ratings, an unparsed
threshold or an untrustworthy quote each reduce confidence and pull the answer
toward the base rate.  The system should get less opinionated as it gets less
informed, not more.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .data.baserates import (
    DEFER_TO_BASE_RATE,
    SERVE_RATE_BIAS,
    SERVE_RATE_FORM_SD,
    SERVE_RATE_MARGINAL,
    classify_matchup,
    count_stats,
    serve_rate_base,
    structural,
)
from .markets.parser import MarketSpec
from .markets.taxonomy import COMPARISON, STRUCTURAL, THRESHOLD, Family
from .models.counts import skellam_comparison
from .models.derive import (
    blend,
    deciding_set_probability,
    duration_probability,
    long_set_probability,
    tiebreak_probability,
)
from .models.lanes import apply_lane
from .models.shrink import CREDIBILITY_K, height_prior_ace_rate, shrink
from .models.simulate import SimulationConfig, matchup_probabilities, simulate_match

#: Baseline service points won that the opponent-adjusted ratings deviate from.
#: These must be the *fitted* baselines from the ratings run, not the literature
#: values: serve_skill and return_skill are deviations from whatever base the fit
#: produced, so pairing them with a different number shifts every matchup.
#: Fitted 2026-08-27: ATP 0.6321, WTA 0.5691.
TOUR_BASE = {"ATP": 0.6321, "WTA": 0.5691}


#: Comparators that ask about the *lower* side of a threshold.  The models all
#: compute "greater than", so these require the complement.
_BELOW_COMPARATORS = frozenset({"lt", "lte"})

#: Families whose answer is a threshold on a quantity, and therefore sensitive to
#: which side of the threshold the question asks about.
_THRESHOLD_SENSITIVE = frozenset({Family.MATCH_DURATION, Family.SET_GAMES, Family.UNFORCED_ERRORS})


def apply_comparator(probability: float, spec: MarketSpec) -> tuple[float, bool]:
    """Orient a "greater than" probability to the direction the question asks.

    Every threshold model here computes ``P(X > threshold)``.  A question phrased
    "will the match finish in under 150 minutes" wants the complement, and
    submitting the uncomplemented number turns a correct 27 into a 73.  That is
    the single most expensive routing error available: it is a full inversion, it
    looks entirely plausible, and nothing downstream can detect it.

    Returns the oriented probability and whether it was flipped.
    """
    if spec.family not in _THRESHOLD_SENSITIVE:
        return probability, False
    if spec.comparator in _BELOW_COMPARATORS:
        return 1.0 - probability, True
    return probability, False


#: Market weight by how much we trust the market for that family.  Structural
#: questions have a liquid market and a documented history of market consensus
#: outscoring model alpha; the statistic families have no market at all.
MARKET_WEIGHT = {
    Family.DECIDING_SET: 0.85,
    Family.SET_GAMES: 0.85,
    Family.TIEBREAK: 0.55,
    Family.MATCH_DURATION: 0.45,
}


@dataclass
class PlayerProfile:
    """Everything the pricer needs about one player."""

    name: str
    serve_skill: float = 0.0
    return_skill: float = 0.0
    matches: int = 0
    ace_rate: float | None = None
    df_rate: float | None = None
    winner_rate: float | None = None
    unforced_rate: float | None = None
    height: float | None = None
    pace_minutes: float = 0.0

    @property
    def has_ratings(self) -> bool:
        return self.matches > 0


@dataclass
class MatchContext:
    """The match a question belongs to."""

    tour: str
    best_of: int
    player_a: PlayerProfile
    player_b: PlayerProfile
    round_code: str = "R128"
    rank_a: float | None = None
    rank_b: float | None = None
    expected_service_points: int = 80

    @property
    def matchup(self) -> str:
        return classify_matchup(self.rank_a, self.rank_b)


@dataclass
class PricedQuestion:
    """A submittable number and the full reasoning that produced it."""

    market_id: str
    family: Family
    probability: float
    submission: int
    base_rate: float
    model_probability: float | None = None
    market_probability: float | None = None
    market_weight: float = 0.0
    confidence: float = 0.0
    lane: str = "honest"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "family": self.family.value,
            "submission": self.submission,
            "probability": round(self.probability, 4),
            "base_rate": round(self.base_rate, 4),
            "model": None if self.model_probability is None else round(self.model_probability, 4),
            "market": None
            if self.market_probability is None
            else round(self.market_probability, 4),
            "market_weight": round(self.market_weight, 3),
            "confidence": round(self.confidence, 3),
            "lane": self.lane,
            "notes": list(self.notes),
        }


class Pricer:
    """Turns parsed questions into submittable probabilities."""

    def __init__(
        self,
        *,
        lane: str = "honest",
        sim_config: SimulationConfig | None = None,
        market_weights: dict[Family, float] | None = None,
    ) -> None:
        self.lane = lane
        self.sim_config = sim_config or SimulationConfig(n_sims=4000)
        self.market_weights = market_weights or dict(MARKET_WEIGHT)
        self._sim_cache: dict[tuple, Any] = {}

    # ------------------------------------------------------------------ public

    def price(
        self,
        spec: MarketSpec,
        context: MatchContext,
        *,
        market_id: str = "",
        ladders: dict | None = None,
    ) -> PricedQuestion:
        """Price one question.  Never raises; degrades toward the base rate."""
        base = self._base_rate(spec, context)
        notes: list[str] = list(spec.notes)

        if not spec.is_understood:
            notes.append("question not understood; answering from the base rate")
            return self._finalise(spec, base, base, None, 0.0, 0.0, market_id, notes, context)

        if spec.family in STRUCTURAL:
            return self._price_structural(spec, context, base, ladders, market_id, notes)
        if spec.family in COMPARISON:
            return self._price_comparison(spec, context, base, market_id, notes)
        if spec.family in THRESHOLD:
            return self._price_threshold(spec, context, base, market_id, notes)

        notes.append("no pricing route for this family")
        return self._finalise(spec, base, base, None, 0.0, 0.0, market_id, notes, context)

    # ------------------------------------------------------------------ routes

    def _price_structural(self, spec, context, base, ladders, market_id, notes) -> PricedQuestion:
        """Match-shape questions: simulate, then blend toward the market."""
        model = base
        confidence = 0.35

        if context.player_a.has_ratings and context.player_b.has_ratings:
            simulation = self._simulate(context)
            model = self._read_simulation(spec, simulation, base, notes, context)
            confidence = 0.8
        else:
            notes.append("one or both players unrated; using the base rate as the model")

        market = self._read_market(spec, context, ladders, base)
        weight = self.market_weights.get(spec.family, 0.0) if market is not None else 0.0
        if market is None and ladders:
            notes.append("no usable market quote for this question")

        probability = blend(model, market, weight)
        return self._finalise(
            spec, probability, base, model, market, weight, market_id, notes, context, confidence
        )

    def _price_comparison(self, spec, context, base, market_id, notes) -> PricedQuestion:
        """Head-to-head count comparisons.  No market exists for any of these."""
        stat = {
            Family.ACES_COMPARISON: "aces",
            Family.DOUBLE_FAULTS_COMPARISON: "double_faults",
            Family.WINNERS_COMPARISON: "winners",
            Family.FIRST_SERVE_COMPARISON: "first_serve",
        }[spec.family]

        if spec.subject is None:
            notes.append("no identifiable subject; cannot price directionally")
            return self._finalise(spec, base, base, None, None, 0.0, market_id, notes, context, 0.2)

        subject, opponent = self._orient(spec, context)
        if subject is None or opponent is None:
            notes.append("subject did not match either player")
            return self._finalise(spec, base, base, None, None, 0.0, market_id, notes, context, 0.2)

        # "Will X win more than 65% of points on serve?" compares one player
        # against an absolute number, not against the other player. Pricing it
        # as a head-to-head would answer a different question, and because both
        # answers land near 50 the error is invisible in the output.
        if stat == "first_serve" and _is_rate_threshold(spec):
            model, confidence = self._serve_rate_threshold(
                spec, subject, opponent, context, base, notes
            )
        elif stat == "first_serve":
            model, confidence = self._first_serve_comparison(
                subject, opponent, context, base, notes
            )
        else:
            model, confidence = self._count_comparison(
                stat, subject, opponent, context, base, notes
            )

        return self._finalise(
            spec, model, base, model, None, 0.0, market_id, notes, context, confidence
        )

    def _serve_rate_threshold(self, spec, subject, opponent, context, base, notes):
        """P(subject's share of service points won clears an absolute threshold).

        The realised rate is a binomial draw around the opponent-adjusted
        expectation, widened by genuine match-to-match form.  Both terms are
        measured (``SERVE_RATE_FORM_SD``); using the binomial term alone would
        make this family about 35% overconfident.
        """
        if not (subject.has_ratings and opponent.has_ratings):
            notes.append("missing serve ratings; using the base rate")
            return base, 0.3

        threshold = _as_rate(spec.threshold)
        if threshold is None:
            notes.append("threshold not interpretable as a rate; using the base rate")
            return base, 0.25

        tour = context.tour if context.tour in TOUR_BASE else "ATP"
        expected = (
            TOUR_BASE[tour]
            + subject.serve_skill
            - opponent.return_skill
            + SERVE_RATE_BIAS.get(tour, 0.0)
        )
        expected = min(max(expected, 0.30), 0.90)

        points = max(context.expected_service_points, 20)
        binomial_var = expected * (1.0 - expected) / points
        form_var = SERVE_RATE_FORM_SD.get(tour, 0.052) ** 2
        sd = math.sqrt(binomial_var + form_var)

        probability = 1.0 - _normal_cdf((threshold - expected) / sd)
        notes.append(
            f"serve rate: expected {expected:.3f} vs threshold {threshold:.3f}, "
            f"sd {sd:.3f} ({math.sqrt(binomial_var):.3f} sampling + "
            f"{math.sqrt(form_var):.3f} form)"
        )
        # Confidence falls away as the threshold approaches the expectation,
        # where the answer is a coin flip that no amount of rating quality fixes.
        margin = abs(expected - threshold) / sd
        return probability, min(0.75, 0.35 + 0.25 * margin)

    def _price_threshold(self, spec, context, base, market_id, notes) -> PricedQuestion:
        """Threshold questions, currently only unforced errors.

        The family defers to the base rate by policy rather than by weakness of
        the model: the forced/unforced call is a subjective human judgement with
        roughly three-or-four-in-ten inter-rater agreement, so the classification
        noise swamps any real signal.  This is the tennis analogue of the foul
        markets that finished 125 relative points down in the predecessor event.
        """
        notes.append("threshold family defers to the base rate by policy")
        return self._finalise(spec, base, base, base, None, 0.0, market_id, notes, context, 0.3)

    # ----------------------------------------------------------------- helpers

    def _simulate(self, context: MatchContext):
        """Simulate the match once and reuse it for every structural question."""
        key = (
            context.player_a.name,
            context.player_b.name,
            context.best_of,
            context.tour,
        )
        cached = self._sim_cache.get(key)
        if cached is not None:
            return cached

        pa, pb = matchup_probabilities(
            TOUR_BASE[context.tour],
            context.player_a.serve_skill,
            context.player_a.return_skill,
            context.player_b.serve_skill,
            context.player_b.return_skill,
        )
        result = simulate_match(pa, pb, context.best_of, config=self.sim_config)
        self._sim_cache[key] = result
        return result

    def _read_simulation(self, spec, simulation, base, notes, context) -> float:
        """Read one question off a simulated match."""
        if spec.family is Family.TIEBREAK:
            return simulation.p_any_tiebreak
        if spec.family is Family.DECIDING_SET:
            return simulation.p_deciding_set
        if spec.family is Family.LOPSIDED_SET:
            return simulation.p_any_lopsided_set
        if spec.family is Family.SET_GAMES:
            return simulation.p_first_set_10plus_games
        if spec.family is Family.MATCH_DURATION:
            if spec.threshold is None:
                notes.append("duration threshold unknown; using the base rate")
                return base
            from .models.duration import forecast_duration

            forecast = forecast_duration(
                simulation.mean_total_games,
                tour=context.tour,
                best_of=context.best_of,
                games_sd=simulation.sd_total_games,
                pace_a=context.player_a.pace_minutes,
                pace_b=context.player_b.pace_minutes,
            )
            return forecast.probability_over(spec.threshold)
        return base

    def _read_market(self, spec, context, ladders, base) -> float | None:
        """Read the market's answer to this question, if one exists."""
        if not ladders:
            return None
        if spec.family is Family.DECIDING_SET:
            return deciding_set_probability(ladders, context.best_of)
        if spec.family is Family.SET_GAMES:
            return long_set_probability(ladders, spec.set_number or 1)
        if spec.family is Family.TIEBREAK:
            return tiebreak_probability(ladders, base_rate=base, best_of=context.best_of)
        if spec.family is Family.MATCH_DURATION and spec.threshold is not None:
            return duration_probability(
                ladders,
                spec.threshold,
                tour=context.tour,
                best_of=context.best_of,
                pace_a=context.player_a.pace_minutes,
                pace_b=context.player_b.pace_minutes,
            )
        return None

    def _orient(self, spec, context) -> tuple[PlayerProfile | None, PlayerProfile | None]:
        """Map the question's named subject onto the two player profiles.

        Orientation is the highest-consequence lookup in the pricer: getting it
        backwards turns a correct 42 into a 58 on the family that carries the
        most edge, and the mistake is invisible in a dry run.
        """
        subject_name = (spec.subject or "").lower()
        for candidate, other in (
            (context.player_a, context.player_b),
            (context.player_b, context.player_a),
        ):
            surname = candidate.name.split()[-1].lower() if candidate.name else ""
            if surname and surname in subject_name:
                return candidate, other
        return None, None

    def _count_comparison(self, stat, subject, opponent, context, base, notes):
        """P(subject records strictly more than opponent) for a count statistic."""
        measured = count_stats(stat, context.tour)
        population = measured["mean_per_player"]

        rate_subject = getattr(subject, f"{stat_field(stat)}", None)
        rate_opponent = getattr(opponent, f"{stat_field(stat)}", None)
        if rate_subject is None or rate_opponent is None:
            notes.append(f"missing {stat} rates; using the measured even-matchup price")
            return measured["even_p_a_greater"], 0.3

        prior = (
            height_prior_ace_rate(subject.height, context.tour)
            if stat == "aces"
            else population / context.expected_service_points
        )
        k = CREDIBILITY_K.get("ace_rate" if stat == "aces" else "df_rate", 50.0)
        shrunk_subject = shrink(rate_subject, subject.matches, prior, k).estimate
        shrunk_opponent = shrink(rate_opponent, opponent.matches, prior, k).estimate

        mu_subject = shrunk_subject * context.expected_service_points
        mu_opponent = shrunk_opponent * context.expected_service_points
        comparison = skellam_comparison(mu_subject, mu_opponent)

        confidence = 0.75 if min(subject.matches, opponent.matches) >= 20 else 0.45
        return comparison.resolve("no"), confidence

    def _first_serve_comparison(self, subject, opponent, context, base, notes):
        """P(subject wins a higher share of first-serve points)."""
        if not (subject.has_ratings and opponent.has_ratings):
            notes.append("missing serve ratings; using the base rate")
            return base, 0.3
        edge = (subject.serve_skill - opponent.return_skill) - (
            opponent.serve_skill - subject.return_skill
        )
        # A one-point edge in service points won is worth roughly six points of
        # win probability on a single-match comparison; the slope is deliberately
        # shallow because one match is a small sample of a rate.
        probability = 0.5 + 6.0 * edge
        return min(max(probability, 0.10), 0.90), 0.6

    def _base_rate(self, spec, context) -> float:
        """The base rate this question falls back to.

        The anchor matters more than the model, because it is what the answer
        decays to precisely when the model is least trusted.  A rate threshold
        anchored at 0.5 would be badly wrong: "will this player clear 55% of
        service points" has an unconditional answer near 0.88 on the men's tour.
        """
        if spec.family in STRUCTURAL:
            rates = structural(context.tour, context.matchup)
            return {
                Family.TIEBREAK: rates.tiebreak,
                Family.DECIDING_SET: rates.deciding_set,
                Family.LOPSIDED_SET: rates.lopsided_set,
                Family.SET_GAMES: rates.first_set_10plus,
                Family.MATCH_DURATION: 0.5,
            }.get(spec.family, 0.5)
        if spec.family in COMPARISON:
            if spec.family is Family.FIRST_SERVE_COMPARISON and _is_rate_threshold(spec):
                threshold = _as_rate(spec.threshold)
                if threshold is not None:
                    tour = context.tour if context.tour in SERVE_RATE_MARGINAL else "ATP"
                    return serve_rate_base(threshold, tour)
            stat = {
                Family.ACES_COMPARISON: "aces",
                Family.DOUBLE_FAULTS_COMPARISON: "double_faults",
                Family.WINNERS_COMPARISON: "winners",
                Family.FIRST_SERVE_COMPARISON: "winners",
            }[spec.family]
            return count_stats(stat, context.tour)["even_p_a_greater"]
        return 0.5

    def _finalise(
        self,
        spec,
        probability,
        base,
        model,
        market,
        weight,
        market_id,
        notes,
        context,
        confidence: float = 0.3,
    ) -> PricedQuestion:
        """Apply the comparator, the confidence pull and the lane, then round.

        Comparator orientation happens here, at the single point every route
        passes through, so no future branch can forget it.
        """
        probability, flipped = apply_comparator(probability, spec)
        if flipped:
            # Complement the reference values too, or the collar check and the
            # recorded provenance would describe the opposite question.
            base = 1.0 - base
            if model is not None:
                model = 1.0 - model
            if market is not None:
                market = 1.0 - market
            notes.append(f"complemented for comparator {spec.comparator!r}")

        # Low confidence pulls the answer toward the base rate. The system should
        # get less opinionated as it gets less informed.
        pull = 1.0 - min(max(confidence, 0.0), 1.0)
        probability = (1.0 - pull) * probability + pull * base

        stat_key = "unforced_errors" if spec.family is Family.UNFORCED_ERRORS else spec.family.value
        submission = apply_lane(
            probability,
            spec.family,
            self.lane,
            base_rate=base if stat_key in DEFER_TO_BASE_RATE else None,
        )
        return PricedQuestion(
            market_id=market_id,
            family=spec.family,
            probability=probability,
            submission=submission,
            base_rate=base,
            model_probability=model,
            market_probability=market,
            market_weight=weight,
            confidence=confidence,
            lane=self.lane,
            notes=tuple(notes),
        )


def stat_field(stat: str) -> str:
    """Map a statistic name to the :class:`PlayerProfile` field holding its rate."""
    return {
        "aces": "ace_rate",
        "double_faults": "df_rate",
        "winners": "winner_rate",
        "unforced_errors": "unforced_rate",
    }[stat]


def _is_rate_threshold(spec: MarketSpec) -> bool:
    """True when the question sets an absolute rate rather than comparing players.

    A percent sign, or the word "percent", is the reliable tell.  A bare number
    with no unit is treated as a comparison, because "more than 65% of points"
    and "more points than" are the two shapes this family takes and only the
    first carries a number.
    """
    if spec.threshold is None:
        return False
    text = spec.raw.lower()
    return "%" in text or "percent" in text


def _as_rate(threshold: float | None) -> float | None:
    """Read a threshold as a probability, accepting both 65 and 0.65."""
    if threshold is None:
        return None
    value = threshold / 100.0 if threshold > 1.0 else threshold
    return value if 0.0 < value < 1.0 else None


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
