"""Tests for the bookmaker-odds client and de-vigging.

The fixture is a trimmed capture of a real response, so the payload shape is the
one the API actually returns rather than one convenient to test against.
"""

from __future__ import annotations

import pytest

from bot.data.oddsapi import (
    QUOTA_RESERVE,
    Budget,
    OddsError,
    consensus,
    devig_multiplicative,
    devig_shin,
    lines_for,
    match_winner,
    total_games_ladder,
)
from bot.models.derive import enforce_monotone


def _book(key, h2h=None, totals=None):
    markets = []
    if h2h:
        markets.append(
            {"key": "h2h", "outcomes": [{"name": n, "price": p} for n, p in h2h.items()]}
        )
    if totals:
        outcomes = []
        for point, prices in totals.items():
            outcomes += [{"name": n, "price": p, "point": point} for n, p in prices.items()]
        markets.append({"key": "totals", "outcomes": outcomes})
    return {"key": key, "markets": markets}


@pytest.fixture
def event():
    return {
        "home_team": "Alycia Parks",
        "away_team": "Ann Li",
        "bookmakers": [
            _book(
                "winamax_fr",
                {"Alycia Parks": 3.00, "Ann Li": 1.40},
                {21.5: {"Over": 1.90, "Under": 1.85}},
            ),
            _book(
                "onexbet",
                {"Alycia Parks": 3.00, "Ann Li": 1.39},
                {21.5: {"Over": 1.94, "Under": 1.80}, 20.5: {"Over": 1.70, "Under": 2.04}},
            ),
            _book(
                "betsson",
                {"Alycia Parks": 3.05, "Ann Li": 1.41},
                {20.5: {"Over": 1.67, "Under": 2.08}},
            ),
        ],
    }


# ------------------------------------------------------------------ de-vigging


@pytest.mark.parametrize(
    "odds",
    [
        {"A": 1.40, "B": 3.00},
        {"A": 1.10, "B": 8.00},
        {"A": 2.00, "B": 1.90},
        {"A": 1.02, "B": 21.0},
        {"A": 1.001, "B": 500.0},
    ],
)
def test_devig_produces_a_proper_distribution(odds):
    for method in (devig_shin, devig_multiplicative):
        fair = method(odds)
        assert sum(fair.values()) == pytest.approx(1.0, abs=1e-9)
        assert all(0.0 < v < 1.0 for v in fair.values())


def test_shin_gives_the_favourite_more_than_multiplicative():
    """Multiplicative spreads the margin proportionally, which overstates
    longshots. Shin removes more margin from the longshot, which is the
    direction the favourite-longshot literature reports."""
    for odds in ({"A": 1.10, "B": 8.00}, {"A": 1.02, "B": 21.0}, {"A": 1.40, "B": 3.00}):
        assert devig_shin(odds)["A"] > devig_multiplicative(odds)["A"]


def test_shin_and_multiplicative_agree_on_a_balanced_book():
    """With no longshot there is no longshot bias to correct, so the two methods
    should nearly coincide. If they diverge here, the solver is wrong."""
    shin = devig_shin({"A": 1.95, "B": 1.95})["A"]
    mult = devig_multiplicative({"A": 1.95, "B": 1.95})["A"]
    assert shin == pytest.approx(mult, abs=1e-3)
    assert shin == pytest.approx(0.5, abs=1e-6)


def test_devig_falls_back_when_the_book_is_not_two_way():
    fair = devig_shin({"A": 3.0, "B": 3.0, "C": 3.0})
    assert sum(fair.values()) == pytest.approx(1.0)
    assert len(fair) == 3


def test_devig_rejects_an_empty_book():
    with pytest.raises(OddsError):
        devig_multiplicative({})


def test_devig_handles_a_book_with_no_margin():
    fair = devig_shin({"A": 2.0, "B": 2.0})
    assert fair["A"] == pytest.approx(0.5)


# ------------------------------------------------------------------ consensus


def test_match_winner_consensus_sums_to_one(event):
    agreed = match_winner(event)
    assert set(agreed) == {"Alycia Parks", "Ann Li"}
    assert sum(c.probability for c in agreed.values()) == pytest.approx(1.0, abs=0.02)


def test_consensus_records_the_spread_not_just_the_median(event):
    """A wide spread is the market saying it is unsure, and that should reduce
    our confidence rather than be averaged away."""
    agreed = match_winner(event)["Ann Li"]
    assert agreed.n_books == 3
    assert agreed.spread > 0.0
    assert agreed.overround > 1.0


def test_a_single_book_is_not_trustworthy():
    lines = lines_for(_pack(_book("solo", {"A": 2.0, "B": 2.0})), "h2h")
    agreed = consensus(lines[None])
    assert agreed["A"].n_books == 1
    assert agreed["A"].is_trustworthy is False


def test_a_wildly_disagreeing_book_is_not_trustworthy():
    event = _pack(
        _book("a", {"A": 1.2, "B": 5.0}),
        _book("b", {"A": 4.0, "B": 1.25}),
        _book("c", {"A": 1.25, "B": 4.5}),
    )
    agreed = consensus(lines_for(event, "h2h")[None])
    assert agreed["A"].spread > 0.10
    assert agreed["A"].is_trustworthy is False


def test_median_resists_one_stale_price():
    """Tennis books do leave lines up after news, so one bad quote must not
    move the answer."""
    clean = _pack(*[_book(f"b{i}", {"A": 2.0, "B": 2.0}) for i in range(5)])
    stale = _pack(
        *[_book(f"b{i}", {"A": 2.0, "B": 2.0}) for i in range(5)],
        _book("stale", {"A": 1.05, "B": 15.0}),
    )
    assert consensus(lines_for(clean, "h2h")[None])["A"].probability == pytest.approx(
        consensus(lines_for(stale, "h2h")[None])["A"].probability, abs=1e-6
    )


# --------------------------------------------------------------- games ladder


def test_total_games_ladder_uses_every_line_the_books_quote(event):
    """Books disagreeing about where to set the line is the useful part: each
    line is another point on the survival curve of total games."""
    ladder = total_games_ladder(event)
    assert set(ladder) == {20.5, 21.5}


def test_games_ladder_is_non_increasing(event):
    ladder = total_games_ladder(event)
    values = tuple(ladder[k] for k in sorted(ladder))
    assert values == pytest.approx(enforce_monotone(values), abs=1e-9)


def test_ladder_ignores_a_line_only_one_book_quotes():
    event = _pack(
        _book("a", totals={21.5: {"Over": 1.9, "Under": 1.9}, 30.5: {"Over": 5.0, "Under": 1.1}}),
        _book("b", totals={21.5: {"Over": 1.9, "Under": 1.9}}),
    )
    assert set(total_games_ladder(event)) == {21.5}


def test_lines_for_ignores_one_sided_quotes():
    event = _pack(
        {"key": "x", "markets": [{"key": "h2h", "outcomes": [{"name": "A", "price": 2.0}]}]}
    )
    assert lines_for(event, "h2h") == {}


# ------------------------------------------------------------------- budgeting


def test_budget_reserves_credits_for_the_final():
    """The free tier is 500 requests for a fifteen-day tournament, so a runaway
    loop must not be able to strand the bot before the highest-multiplier round.
    """
    assert Budget(remaining=QUOTA_RESERVE + 10).spendable == 10
    assert Budget(remaining=QUOTA_RESERVE).spendable == 0
    assert Budget(remaining=5).spendable == 0


def test_budget_round_trips(tmp_path):
    path = tmp_path / "budget.json"
    Budget(remaining=321, used=179, checked_at="now").save(path)
    assert Budget.load(path).remaining == 321


def test_budget_survives_a_missing_or_corrupt_file(tmp_path):
    assert Budget.load(tmp_path / "absent.json").remaining is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert Budget.load(bad).remaining is None


def _pack(*books):
    return {"home_team": "A", "away_team": "B", "bookmakers": list(books)}
