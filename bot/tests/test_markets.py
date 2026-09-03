"""Tests for market quotes and derived probabilities."""

from __future__ import annotations

import pytest

from bot.data.markets import (
    DEBIAS_EXPONENT,
    MIN_TRUSTWORTHY_PRICE,
    Quote,
    debias,
    kalshi_quote,
)
from bot.models.derive import (
    StrikeLadder,
    blend,
    deciding_set_probability,
    duration_probability,
    enforce_monotone,
    long_set_probability,
    parse_ladders,
    tiebreak_probability,
)


def _quote(**overrides) -> Quote:
    base = {
        "venue": "polymarket",
        "market_id": "m1",
        "question": "Will the match go to a deciding set?",
        "best_bid": 0.40,
        "best_ask": 0.42,
        "last_trade": 0.41,
        "volume": 50_000.0,
        "accepting_orders": True,
        "closed": False,
    }
    base.update(overrides)
    return Quote(**base)


# --------------------------------------------------------------------- quotes


def test_mid_price_is_the_average_of_the_book():
    assert _quote().mid == pytest.approx(0.41)


def test_one_sided_book_falls_back_to_the_available_side():
    assert _quote(best_bid=None).mid == pytest.approx(0.42)


def test_healthy_quote_is_trustworthy():
    assert _quote().is_trustworthy


def test_settled_market_with_stale_last_trade_is_rejected():
    """The load-bearing case.

    An eliminated player's market kept a last trade of 0.73 while the best ask
    had fallen to 0.001. Anything keying off last-trade would have priced him a
    co-favourite.
    """
    stale = _quote(
        best_bid=None,
        best_ask=0.001,
        last_trade=0.730,
        accepting_orders=False,
        closed=True,
    )
    assert stale.is_stale
    assert not stale.is_trustworthy


def test_crossed_book_is_detected():
    assert _quote(best_bid=0.60, best_ask=0.55).is_crossed


def test_last_trade_far_from_mid_is_stale():
    assert _quote(best_bid=0.40, best_ask=0.42, last_trade=0.90).is_stale


def test_extreme_prices_are_not_trusted():
    """A one-cent spread on a two-cent mid means the fair value is anywhere
    between one and three cents."""
    assert not _quote(best_bid=0.01, best_ask=0.02, last_trade=0.015).is_trustworthy
    assert MIN_TRUSTWORTHY_PRICE > 0.0


def test_debias_pushes_away_from_the_centre():
    """Markets overprice longshots and slightly underprice heavy favourites."""
    assert debias(0.10) < 0.10
    assert debias(0.90) > 0.90
    assert debias(0.50) == pytest.approx(0.50)
    assert DEBIAS_EXPONENT > 1.0


def test_kalshi_prices_are_converted_from_cents():
    quote = kalshi_quote(
        {
            "ticker": "KXATP-26USO-ALC",
            "title": "Alcaraz",
            "yes_bid": 26,
            "yes_ask": 28,
            "last_price": 27,
            "volume": 1000,
            "status": "active",
        }
    )
    assert quote.mid == pytest.approx(0.27)
    assert quote.venue == "kalshi"
    assert not quote.closed


def test_settled_kalshi_market_is_marked_closed():
    quote = kalshi_quote({"ticker": "X", "status": "settled", "yes_bid": 0, "yes_ask": 1})
    assert quote.closed
    assert quote.is_stale


# -------------------------------------------------------------------- ladders


def test_parse_ladders_groups_by_quantity():
    ladders = parse_ladders(
        {
            "Alcaraz vs Sinner: Set 1 Games O/U 8.5": 0.62,
            "Alcaraz vs Sinner: Set 1 Games O/U 9.5": 0.45,
            "Alcaraz vs Sinner: Set 1 Games O/U 10.5": 0.30,
            "Alcaraz vs Sinner: Match O/U 21.5": 0.70,
            "Alcaraz vs Sinner: Match O/U 22.5": 0.58,
            "Alcaraz vs Sinner: Total Sets O/U 2.5": 0.48,
        }
    )
    assert set(ladders) == {"set_1_games", "match_games", "total_sets"}
    assert ladders["set_1_games"].strikes == (8.5, 9.5, 10.5)


def test_survival_must_decrease_with_strike():
    """A rising survival curve implies negative probability mass — an arbitrage."""
    assert enforce_monotone((0.6, 0.7, 0.3)) == pytest.approx((0.65, 0.65, 0.3))


def test_monotone_repair_leaves_valid_ladders_untouched():
    assert enforce_monotone((0.7, 0.5, 0.3)) == (0.7, 0.5, 0.3)


def test_mass_between_strikes_is_a_first_difference():
    """Digital quotes are already the first derivative of a call, so one
    differencing gives the density — not the Breeden-Litzenberger second."""
    ladder = StrikeLadder("set_1_games", (8.5, 9.5, 10.5), (0.62, 0.45, 0.30))
    mass = ladder.mass_between()
    assert mass[(8.5, 9.5)] == pytest.approx(0.17)
    assert mass[(9.5, 10.5)] == pytest.approx(0.15)


def test_expected_value_sits_inside_the_strike_range():
    ladder = StrikeLadder("match_games", (21.5, 22.5, 23.5), (0.70, 0.58, 0.44))
    assert 19.0 < ladder.expected_value() < 28.0


# ------------------------------------------------------------------ derivation


def test_deciding_set_is_a_direct_read_in_best_of_three():
    ladders = {"total_sets": StrikeLadder("total_sets", (2.5,), (0.48,))}
    assert deciding_set_probability(ladders, best_of=3) == pytest.approx(0.48)


def test_deciding_set_uses_a_different_strike_in_best_of_five():
    ladders = {"total_sets": StrikeLadder("total_sets", (2.5, 4.5), (0.90, 0.22))}
    assert deciding_set_probability(ladders, best_of=5) == pytest.approx(0.22)


def test_long_set_is_a_direct_read_of_the_nine_and_a_half_strike():
    """'Will the set contain 10 or more games' is literally the Over 9.5 price."""
    ladders = {"set_1_games": StrikeLadder("set_1_games", (8.5, 9.5, 10.5), (0.62, 0.45, 0.30))}
    assert long_set_probability(ladders) == pytest.approx(0.45)


def test_missing_market_returns_none_rather_than_a_guess():
    assert long_set_probability({}) is None
    assert deciding_set_probability({}) is None
    assert tiebreak_probability({}, base_rate=0.5) is None


def test_tiebreak_rises_with_expected_match_length():
    short = {"match_games": StrikeLadder("match_games", (21.5, 22.5), (0.30, 0.18))}
    long_match = {"match_games": StrikeLadder("match_games", (21.5, 22.5), (0.85, 0.75))}
    p_short = tiebreak_probability(short, base_rate=0.27, best_of=3)
    p_long = tiebreak_probability(long_match, base_rate=0.27, best_of=3)
    assert p_long > p_short


def test_tiebreak_stays_within_sane_bounds():
    extreme = {"match_games": StrikeLadder("match_games", (21.5, 22.5), (0.99, 0.99))}
    assert 0.0 < tiebreak_probability(extreme, base_rate=0.27) <= 0.95


def test_duration_derived_from_the_games_market():
    ladders = {"match_games": StrikeLadder("match_games", (21.5, 22.5, 23.5), (0.7, 0.58, 0.44))}
    low = duration_probability(ladders, 80, tour="ATP", best_of=3)
    high = duration_probability(ladders, 200, tour="ATP", best_of=3)
    assert low is not None and high is not None
    assert low > high


def test_slow_players_raise_the_duration_probability():
    ladders = {"match_games": StrikeLadder("match_games", (21.5, 22.5), (0.7, 0.58))}
    neutral = duration_probability(ladders, 120, pace_a=0, pace_b=0)
    slow = duration_probability(ladders, 120, pace_a=18, pace_b=15)
    assert slow > neutral


# ----------------------------------------------------------------------- blend


def test_blend_defaults_toward_the_market():
    """Two top-three finishers in the predecessor event independently found the
    market consensus carried the score and the alpha layer cost points."""
    assert blend(0.30, 0.70) > 0.5


def test_blend_returns_the_model_when_no_market_exists():
    assert blend(0.42, None) == pytest.approx(0.42)


def test_blend_respects_the_weight():
    assert blend(0.30, 0.70, market_weight=0.0) == pytest.approx(0.30, abs=1e-6)
    assert blend(0.30, 0.70, market_weight=1.0) == pytest.approx(0.70, abs=1e-6)


# ------------------------------------------- isotonic repair (PAVA) regressions


def test_enforce_monotone_terminates_on_the_input_that_hung():
    """Regression: this six-element ladder spun a core for eighteen hours.

    The old implementation averaged *pairs* until nothing changed. Setting two
    neighbours equal lets a later pass break the equality again, so differences
    shrink geometrically and never reach exactly zero. Because this runs on live
    market quotes inside the submission path, one stale strike could hang the
    bot with the markets open.
    """
    out = enforce_monotone((0.27, 0.041, 0.017, 0.813, 0.913, 0.607))
    assert len(out) == 6
    assert _is_non_increasing(out)


@pytest.mark.parametrize(
    "ladder",
    [
        (0.9, 0.1, 0.8, 0.2, 0.7),
        (0.1, 0.2, 0.3, 0.4, 0.5),
        (0.5, 0.9, 0.1, 0.9, 0.1, 0.9),
        (0.017, 0.813, 0.913, 0.607),
        (0.3, 0.3, 0.9),
        (0.0, 1.0),
        (1.0, 0.0),
    ],
)
def test_enforce_monotone_always_returns_a_non_increasing_ladder(ladder):
    out = enforce_monotone(ladder)
    assert len(out) == len(ladder)
    assert _is_non_increasing(out)


def test_enforce_monotone_preserves_total_mass():
    """A defining property of isotonic regression: pooling moves mass between
    strikes but never creates or destroys it. Unweighted pair averaging broke
    this, which is how the old version could be wrong even when it terminated.
    """
    for ladder in [(0.6, 0.7, 0.3), (0.9, 0.1, 0.8, 0.2), (0.27, 0.041, 0.017, 0.813)]:
        assert sum(enforce_monotone(ladder)) == pytest.approx(sum(ladder), abs=1e-9)


def test_enforce_monotone_uses_weighted_pooling():
    """Three violators must pool to their common mean, not to a pairwise cascade.

    (0.1, 0.2, 0.9) has mean 0.4. Averaging pairs left-to-right gives 0.15 then
    0.525, which is neither monotone nor mass-preserving.
    """
    assert enforce_monotone((0.1, 0.2, 0.9)) == pytest.approx((0.4, 0.4, 0.4))


def test_enforce_monotone_leaves_valid_ladders_untouched():
    ladder = (0.95, 0.7, 0.42, 0.11)
    assert enforce_monotone(ladder) == pytest.approx(ladder)


def test_enforce_monotone_clamps_to_probabilities():
    out = enforce_monotone((1.4, -0.3))
    assert all(0.0 <= v <= 1.0 for v in out)


def test_enforce_monotone_handles_degenerate_input():
    assert enforce_monotone(()) == ()
    assert enforce_monotone((0.5,)) == (0.5,)


def _is_non_increasing(values):
    return all(values[i] >= values[i + 1] for i in range(len(values) - 1))


def test_en_dash_scorelines_are_understood():
    """Round 3 introduced this question written with EN DASHES, not hyphens.

    Every scoreline pattern in the parser used an ASCII hyphen, so nothing
    matched and twelve markets fell through to their base rate while a working
    model for them sat unused.  The ATP lopsided-set model is unbiased against
    realised outcomes (-0.015, SE 0.026) and beats the flat base rate by about
    0.78 RBP/question, so this was pure forfeited value - and it would have
    recurred every round, at 2x and 3x.
    """
    from bot.markets.parser import parse_question
    from bot.markets.taxonomy import Family

    en_dash = "Will either player win a set 6\u20132, 6\u20131 or 6\u20130?"
    hyphen = "Will either player win a set 6-2, 6-1 or 6-0?"
    for text in (en_dash, hyphen):
        spec = parse_question(text, players=("A. Player", "B. Player"))
        assert spec.family is Family.LOPSIDED_SET, text
        assert spec.confidence >= 0.85


def test_dash_normalisation_does_not_disturb_other_families():
    from bot.markets.parser import parse_question
    from bot.markets.taxonomy import Family

    cases = {
        "Will the match feature at least 2 tiebreak(s)?": Family.TIEBREAK,
        "Will the match last longer than 150 minutes?": Family.MATCH_DURATION,
        "Will A. Player record more aces than B. Player?": Family.ACES_COMPARISON,
        "Will A. Player win?": Family.MATCH_WINNER,
    }
    for text, family in cases.items():
        assert parse_question(text, players=("A. Player", "B. Player")).family is family
