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
from dataclasses import dataclass, field, replace
from typing import Any

from .data.baserates import (
    ACE_VULNERABILITY_EXPONENT,
    ACE_VULNERABILITY_TOURS,
    COUNT_RATE_PRIOR,
    DEFER_TO_BASE_RATE,
    DURATION_SURVIVAL,
    SERVE_RATE_BIAS,
    SERVE_RATE_FORM_SD,
    SERVE_RATE_MARGINAL,
    classify_matchup,
    count_confidence,
    count_overdispersion,
    count_stats,
    duration_base,
    outcome_coupling,
    serve_rate_base,
    service_points_for,
    structural,
    tiebreak_count_base,
    tiebreak_sim_scale,
)
from .data.names import strip_accents
from .markets.parser import MarketSpec
from .markets.taxonomy import COMPARISON, OUTCOME, STRUCTURAL, THRESHOLD, Family
from .models.counts import binomial_comparison
from .models.derive import (
    blend,
    deciding_set_probability,
    duration_probability,
    long_set_probability,
    tiebreak_probability,
)
from .models.lanes import (
    LANE_CONFIDENCE_SCALE,
    LANE_FAMILY_SHIFT,
    LANE_MARKET_SCALE,
    LANE_SIGMA_OFFSET,
    apply_lane,
)
from .models.shrink import CREDIBILITY_K, height_prior_ace_rate, shrink
from .models.simulate import (
    FITTED_SIGMA,
    TOUR_SIGMA,
    SimulationConfig,
    matchup_probabilities,
    simulate_match,
    tiebreak_at_least,
)

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
#: Families whose model returns ``P(X > threshold)`` and therefore must be
#: complemented when the question asks the other way.
#:
#: TIEBREAK and the comparison families were missing.  Round 1 phrases them
#: positively so nothing fired, but "fewer than 2 tiebreaks" would have
#: submitted 6 where 94 was correct - an 88-point error, and the largest single
#: mistake available anywhere in this system.  A latent inversion is not a
#: smaller problem than a live one, it is the same problem waiting for a
#: rewording.
_THRESHOLD_SENSITIVE = frozenset(
    {
        Family.MATCH_DURATION,
        Family.SET_GAMES,
        Family.UNFORCED_ERRORS,
        Family.TIEBREAK,
        Family.ACES_COMPARISON,
        Family.DOUBLE_FAULTS_COMPARISON,
        Family.WINNERS_COMPARISON,
        Family.FIRST_SERVE_COMPARISON,
    }
)


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
#:
#: Match winner is at 0.95 because the settled results say the market alone is
#: better than any blend with our model.  Sweeping the weight over 26 settled
#: match-winner questions gives a strictly monotone curve - skill per question
#: runs -2.76 at weight 0.0, -0.31 at 0.5, +0.48 at 0.8 and +0.71 at 1.0 - and
#: the market correlates +0.733 with outcomes against our model's +0.585.  A
#: forty-book consensus prices injuries, late withdrawals, travel and conditions
#: that a ratings-only simulator cannot see, so this is expected rather than
#: surprising.
#:
#: Not set to a full 1.0.  The last step from 0.9 is worth only 0.08 RBP and
#: keeping a sliver of model weight is cheap insurance against a bad quote - a
#: de-vig error or a mismatched player would otherwise pass straight through.
MARKET_WEIGHT = {
    Family.MATCH_WINNER: 0.95,
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
    #: Aces conceded per return point, relative to tour average. 1.0 is average,
    #: above 1 means this player is read easily on serve. A distinct skill from
    #: winning return points: the two correlate only -0.29.
    ace_vulnerability: float = 1.0

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
    #: Pinnacle's expected total games for this match, when it published a line.
    #: ``None`` means the simulator answers alone, which is the old behaviour.
    market_total_games: float | None = None

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
        self._sim_config_override = sim_config
        self.sim_config = sim_config or SimulationConfig(n_sims=4000)
        # Scaling the market weight by lane is what makes the second entry a
        # genuinely different model rather than a louder copy of the first.
        scale = LANE_MARKET_SCALE.get(lane, 1.0)
        base_weights = market_weights or dict(MARKET_WEIGHT)
        self.market_weights = {f: w * scale for f, w in base_weights.items()}
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

        if spec.family in OUTCOME:
            return self._price_outcome(spec, context, base, ladders, market_id, notes)
        if spec.family in STRUCTURAL:
            return self._price_structural(spec, context, base, ladders, market_id, notes)
        if spec.family in COMPARISON:
            return self._price_comparison(spec, context, base, market_id, notes, ladders)
        if spec.family in THRESHOLD:
            return self._price_threshold(spec, context, base, market_id, notes)

        notes.append("no pricing route for this family")
        return self._finalise(spec, base, base, None, 0.0, 0.0, market_id, notes, context)

    # ------------------------------------------------------------------ routes

    def _price_outcome(self, spec, context, base, ladders, market_id, notes) -> PricedQuestion:
        """Who wins the match - the one question the market already answers.

        Every bookmaker on earth prices this, the closing line has a Brier of
        about 0.203, and in the predecessor event a de-vigged consensus would
        have beaten the entrant's own model by 566 relative points.  So the
        market is the answer when we have one, and the simulation is a fallback
        rather than a rival.  Departing from a liquid consensus on the most
        efficiently priced market in tennis needs evidence we do not have.
        """
        if spec.subject is None:
            notes.append("no identifiable subject; cannot tell which player is meant")
            return self._finalise(spec, base, base, None, None, 0.0, market_id, notes, context, 0.2)

        subject, opponent = self._orient(spec, context)
        if subject is None or opponent is None:
            notes.append("subject did not match either player")
            return self._finalise(spec, base, base, None, None, 0.0, market_id, notes, context, 0.2)

        model, confidence = base, 0.3
        if subject.has_ratings and opponent.has_ratings:
            oriented = context if subject is context.player_a else _swap(context)
            simulation = self._simulate(oriented)
            model = simulation.p_a_wins_match
            confidence = 0.7
            notes.append(f"simulated win probability {model:.3f}")
        else:
            notes.append("one or both players unrated; using an even prior")

        market = None
        if ladders:
            quoted = ladders.get("match_winner")
            if isinstance(quoted, dict):
                market = quoted.get(subject.name)
        default = 0.85 * LANE_MARKET_SCALE.get(self.lane, 1.0)
        weight = self.market_weights.get(spec.family, default) if market is not None else 0.0
        if market is not None:
            notes.append(f"market consensus {market:.3f}, weight {weight:.2f}")
            confidence = max(confidence, 0.85)
        else:
            notes.append("no market quote; this is the family that most needs one")

        probability = blend(model, market, weight)
        return self._finalise(
            spec, probability, base, model, market, weight, market_id, notes, context, confidence
        )

    def _duration_base(self, spec, context) -> float:
        """Anchor a duration question on its own threshold and draw.

        A hardcoded 0.5 was wrong in both directions at once: "longer than 150
        minutes" is 0.514 for a men's best-of-five and 0.083 for a women's
        best-of-three. Anchoring the women's question at 0.5 would drag an
        uncertain answer six times too high, and the anchor is what the number
        decays to precisely when the model is least trusted.
        """
        if spec.threshold is None:
            return 0.5
        tour = context.tour if context.tour in DURATION_SURVIVAL else "ATP"
        return duration_base(float(spec.threshold), tour)

    def _tiebreak_base(self, spec, context, rates) -> float:
        """Anchor a tiebreak question on the count it actually asks about.

        Two tiebreaks are about 3.7 times rarer than one on the men's side and
        eleven times rarer on the women's.  Anchoring both on the any-tiebreak
        rate would drag the two-tiebreak answer far too high, and the anchor is
        what the number decays to exactly when the model is least trusted.
        """
        wanted = int(spec.threshold) if spec.threshold else 1
        if wanted <= 1:
            return rates.tiebreak
        tour = context.tour if context.tour in {"ATP", "WTA"} else "ATP"
        scale = rates.tiebreak / max(tiebreak_count_base(1, tour), 1e-9)
        return min(max(tiebreak_count_base(wanted, tour) * scale, 0.005), 0.995)

    def _price_structural(self, spec, context, base, ladders, market_id, notes) -> PricedQuestion:
        """Match-shape questions: simulate, then blend toward the market."""
        model = base
        confidence = 0.35

        if context.player_a.has_ratings and context.player_b.has_ratings:
            simulation = self._simulate(context)
            model = self._read_simulation(spec, simulation, base, notes, context)
            # 0.8 here was an unmeasured guess, and unlike COUNT_CONFIDENCE it
            # was never swept.  Against 42 settled matches, dropping the pull
            # toward the base rate entirely would have gained +0.91 RBP/question
            # on duration and +0.69 on tiebreak, and cost nothing on match
            # winner (-0.24, CI [-2.40, +2.22]) because the market blend is
            # applied before the pull and so survives it.  Pooled across all
            # families the gain is +0.77, CI [+0.08, +1.47].
            #
            # The pull is not removed outright: these are simulated quantities
            # and a small anchor still limits the damage when the ratings are
            # thin.  0.95 keeps that guard while recovering most of the loss.
            # Note the base rate for "will X win" is 0.50, which carries no
            # information about the matchup at all, so pulling a market-informed
            # number toward it was never defensible.
            confidence = 0.95
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

    def _price_comparison(
        self, spec, context, base, market_id, notes, ladders=None
    ) -> PricedQuestion:
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
            expected_games = win_probability = None
            if subject.has_ratings and opponent.has_ratings:
                oriented = context if subject is context.player_a else _swap(context)
                simulation = self._simulate(oriented)
                expected_games = simulation.mean_total_games
                win_probability = simulation.p_a_wins_match
                # The market price is a better win probability than ours
                # wherever one exists, and it is the whole point of the coupling.
                quoted = (ladders or {}).get("match_winner")
                if isinstance(quoted, dict) and subject.name in quoted:
                    win_probability = quoted[subject.name]
            model, confidence = self._count_comparison(
                stat, subject, opponent, context, base, notes, expected_games, win_probability
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
        result = simulate_match(pa, pb, context.best_of, config=self._sim_config_for(context.tour))
        self._sim_cache[key] = result
        return result

    def _sim_config_for(self, tour: str) -> SimulationConfig:
        """Between-set variation is fitted per tour, not shared.

        An explicit ``sim_config`` passed to the constructor still wins, so
        backtests and tests can pin the parameters.
        """
        if self._sim_config_override is not None:
            return self._sim_config_override
        sigma = TOUR_SIGMA.get(tour, FITTED_SIGMA) + LANE_SIGMA_OFFSET.get(self.lane, 0.0)
        return replace(self.sim_config, sigma=sigma)

    def _read_simulation(self, spec, simulation, base, notes, context) -> float:
        """Read one question off a simulated match."""
        if spec.family is Family.TIEBREAK:
            # The question carries a count: 'at least 1' and 'at least 2' are
            # different questions and a third of them asked for two.
            wanted = int(spec.threshold) if spec.threshold else 1
            raw = tiebreak_at_least(simulation, wanted)
            scale = tiebreak_sim_scale(context.tour)
            if scale != 1.0:
                notes.append(f"tiebreak iid correction x{scale:.2f} ({context.tour})")
            return min(max(raw * scale, 0.002), 0.998)
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
            from .models.duration import GAMES_MARKET_WEIGHT, correct_games, forecast_duration

            expected_games = correct_games(
                simulation.mean_total_games, context.tour, context.best_of
            )
            if context.market_total_games is not None:
                weight = GAMES_MARKET_WEIGHT
                blended = (1.0 - weight) * expected_games + weight * context.market_total_games
                notes.append(
                    f"total games: simulator {expected_games:.1f}, "
                    f"Pinnacle {context.market_total_games:.1f}, "
                    f"used {blended:.1f} at weight {weight:.2f}"
                )
                expected_games = blended

            forecast = forecast_duration(
                expected_games,
                tour=context.tour,
                best_of=context.best_of,
                games_sd=simulation.sd_total_games,
                pace_a=context.player_a.pace_minutes,
                pace_b=context.player_b.pace_minutes,
                already_corrected=True,
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
        # Accents differ between fields of the same record: the API names a
        # match "M. Čilić vs A. Rublev" and then asks its own questions about
        # "M. Cilic". Comparing raw strings silently fails to orient, and an
        # unoriented comparison falls back to the base rate - which looks like a
        # deliberate answer and is a forfeit.
        subject_name = strip_accents(spec.subject or "").lower()
        for candidate, other in (
            (context.player_a, context.player_b),
            (context.player_b, context.player_a),
        ):
            surname = strip_accents(candidate.name.split()[-1]).lower() if candidate.name else ""
            if surname and surname in subject_name:
                return candidate, other
        return None, None

    def _count_comparison(
        self,
        stat,
        subject,
        opponent,
        context,
        base,
        notes,
        expected_games=None,
        win_probability=None,
    ):
        """P(subject records strictly more than opponent) for a count statistic."""
        measured = count_stats(stat, context.tour)
        population = measured["mean_per_player"]

        rate_subject = getattr(subject, f"{stat_field(stat)}", None)
        rate_opponent = getattr(opponent, f"{stat_field(stat)}", None)
        if rate_subject is None or rate_opponent is None:
            notes.append(f"missing {stat} rates; using the measured even-matchup price")
            return measured["even_p_a_greater"], 0.3

        # One prior per player, not one shared prior. Regressing the opponent
        # toward the *subject's* height prior made the answer depend on which
        # player the question happened to name first: P(A>B) + P(B>A) + P(tie)
        # came to 1.207 on one live match instead of 1.000, and the error was
        # largest exactly where the heights differ most, which is where the
        # model should be most confident.
        k = CREDIBILITY_K.get("ace_rate" if stat == "aces" else "df_rate", 50.0)
        prior_subject = self._count_prior(stat, subject, context, population)
        prior_opponent = self._count_prior(stat, opponent, context, population)
        shrunk_subject = shrink(rate_subject, subject.matches, prior_subject, k).estimate
        shrunk_opponent = shrink(rate_opponent, opponent.matches, prior_opponent, k).estimate

        # Scale each server's ace rate by how easily the OTHER player is aced.
        # Conceding aces is a different skill from winning return points - the
        # two correlate only -0.29 - and the multiplier spans 0.56 to 1.43, so
        # ignoring it discarded a real and large effect. Out-of-sample this
        # improved the men's ace Brier by 12.9%; the women's effect was absent,
        # so it is applied to the men's draw only.
        if stat == "aces" and context.tour in ACE_VULNERABILITY_TOURS:
            e = ACE_VULNERABILITY_EXPONENT
            shrunk_subject *= opponent.ace_vulnerability**e
            shrunk_opponent *= subject.ace_vulnerability**e
            notes.append(
                f"ace vulnerability: {opponent.name} {opponent.ace_vulnerability:.2f}, "
                f"{subject.name} {subject.ace_vulnerability:.2f}"
            )

        # Exposure, not a constant. Both players serve in the same match, so a
        # 20-game blowout gives each of them about 61 service points and a
        # 44-game marathon about 138. Pricing that 2.3-fold range as fixed is
        # what made the two counts look independent when they are not: raw ace
        # counts correlate +0.26, but only +0.02 once exposure is controlled.
        points = context.expected_service_points
        if expected_games is not None:
            points = service_points_for(expected_games, context.tour, context.best_of)
            notes.append(f"exposure {points} service points from {expected_games:.1f} games")

        # Beta-binomial rather than Skellam. A player's ace count varies about
        # 2.2 times more than binomial sampling allows, because their true rate
        # moves between matches. Poisson and binomial tails are both too thin,
        # which understates lopsided counts and misstates P(tie).
        rho = count_overdispersion(stat, context.tour)
        comparison = binomial_comparison(
            shrunk_subject, shrunk_opponent, points, points, overdispersion=rho
        )

        model = comparison.resolve("no")
        # Measured per family rather than assumed. The sweep found the women's
        # double-fault model is beaten outright by its own base rate, so trusting
        # it uniformly would have cost points on a quarter of the count questions.
        measured_confidence = count_confidence(stat, context.tour)
        confidence = measured_confidence if min(subject.matches, opponent.matches) >= 20 else 0.45

        # Winning and out-acing share a cause the historical rate cannot see: on
        # a day when a player's serve is working they do both. Mixing over the
        # unknown result rather than assuming one converts our best-priced
        # family into an edge on a family with no market at all.
        delta = outcome_coupling(stat, context.tour)
        if delta and win_probability is not None:
            model = _mix_over_outcome(model, win_probability, delta)
            notes.append(f"outcome coupling: P(win)={win_probability:.3f}, delta={delta:+.2f}")

        return model, confidence

    def _count_prior(self, stat: str, player: PlayerProfile, context, population: float) -> float:
        """The rate a player with no record is assumed to have.

        Ace rate is predicted well by height, so each player gets their own
        height prior.  Double faults are not, so the prior is the population
        mean expressed as a rate over the format's own service points - dividing
        a mixed-format per-match count by best-of-five service points put the
        ATP prior 33% below the rate it was shrinking toward.
        """
        if stat == "aces":
            return height_prior_ace_rate(player.height, context.tour)
        measured = COUNT_RATE_PRIOR.get((context.tour, stat))
        if measured is not None:
            return measured
        return population / max(context.expected_service_points, 1)

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
            # Dispatched with if/elif rather than a dict literal on purpose. A
            # dict evaluates every value eagerly, so a duration question asking
            # about 99999 minutes also ran _tiebreak_base with 99999 as a
            # tiebreak count, and 5.0 ** 99997 raised OverflowError. The
            # question that crashed was not even the question being priced.
            if spec.family is Family.TIEBREAK:
                return self._tiebreak_base(spec, context, rates)
            if spec.family is Family.MATCH_DURATION:
                return self._duration_base(spec, context)
            if spec.family is Family.DECIDING_SET:
                return rates.deciding_set
            if spec.family is Family.LOPSIDED_SET:
                return rates.lopsided_set
            if spec.family is Family.SET_GAMES:
                return rates.first_set_10plus
            return 0.5
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
        scaled = confidence * LANE_CONFIDENCE_SCALE.get(self.lane, 1.0)
        pull = 1.0 - min(max(scaled, 0.0), 1.0)
        probability = (1.0 - pull) * probability + pull * base

        # Experimental per-family shift, second bot only. See LANE_FAMILY_SHIFT:
        # these biases are not significant at eleven matches, so they are tested
        # on the secondary entry rather than risked on the main one.
        shift = LANE_FAMILY_SHIFT.get(self.lane, {}).get(spec.family, 0.0)
        if shift:
            probability = min(max(probability + shift, 0.02), 0.98)
            notes.append(f"scenario lane: experimental {spec.family.value} shift {shift:+.3f}")

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


def _swap(context: MatchContext) -> MatchContext:
    """Return the same match with the players exchanged.

    The simulator always reports ``p_a_wins_match``, so asking about the
    second-named player means simulating the mirrored match. The live questions
    name the second player 37 times out of 63, so this is the common case, not
    an edge case.
    """
    return replace(
        context,
        player_a=context.player_b,
        player_b=context.player_a,
        rank_a=context.rank_b,
        rank_b=context.rank_a,
    )


def _mix_over_outcome(model: float, win_probability: float, delta: float) -> float:
    """Mix a count comparison over the unknown match result.

    The result is not known at submission time, so the coupling is applied as a
    probability-weighted mixture of the two branches rather than as a single
    shift. Assuming a winner would be a much stronger claim than the evidence
    supports.
    """
    p = min(max(float(model), 1e-6), 1.0 - 1e-6)
    logit = math.log(p / (1.0 - p))
    won = 1.0 / (1.0 + math.exp(-(logit + delta)))
    lost = 1.0 / (1.0 + math.exp(-(logit - delta)))
    w = min(max(float(win_probability), 0.0), 1.0)
    return w * won + (1.0 - w) * lost
