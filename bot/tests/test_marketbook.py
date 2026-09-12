"""Tests for joining bookmaker consensus onto our matches."""

from __future__ import annotations

import pytest

from bot.marketbook import MAX_SPREAD, MIN_BOOKS, MarketBook, surname_key


def _event(a, b, price_a, price_b, n_books=20):
    return {
        "home_team": a,
        "away_team": b,
        "bookmakers": [
            {
                "key": f"book{i}",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": a, "price": price_a},
                            {"name": b, "price": price_b},
                        ],
                    }
                ],
            }
            for i in range(n_books)
        ],
    }


@pytest.fixture
def book():
    market = MarketBook()
    market.absorb(
        [
            _event("Luciano Darderi", "Harry Wendelken", 1.34, 3.45),
            _event("Marin Cilic", "Andrey Rublev", 3.80, 1.30),
        ]
    )
    return market


def test_surname_key_survives_every_spelling():
    """The competition says 'M. Giron', the books say 'Marcos Giron', and the
    competition's own match name may carry diacritics its questions drop."""
    assert surname_key("M. Giron") == surname_key("Marcos Giron")
    assert surname_key("M. Čilić") == surname_key("Marin Cilic")
    assert surname_key("F. Auger-Aliassime") == surname_key("Felix Auger Aliassime")
    assert surname_key("I. Jović") == surname_key("Iva Jovic")


def test_surnames_of_different_players_do_not_collide():
    assert surname_key("A. Rublev") != surname_key("M. Cilic")


def test_quotes_are_devigged_and_complementary(book):
    forward = book.probability("Luciano Darderi", "Harry Wendelken")
    backward = book.probability("Harry Wendelken", "Luciano Darderi")
    assert forward == pytest.approx(0.735, abs=0.02)
    assert forward + backward == pytest.approx(1.0, abs=0.01)


def test_ladders_supply_both_directions(book):
    """The answer must not depend on which player the question names."""
    ladders = book.ladders_for("Luciano Darderi", "Harry Wendelken")
    quotes = ladders["match_winner"]
    assert set(quotes) == {"Luciano Darderi", "Harry Wendelken"}
    assert sum(quotes.values()) == pytest.approx(1.0, abs=0.01)


def test_a_thin_book_is_treated_as_no_quote():
    """Half a market is not half as useful as a full one; it is a number with
    unknown provenance dressed up as a consensus."""
    market = MarketBook()
    market.absorb([_event("A Player", "B Player", 1.5, 2.5, n_books=MIN_BOOKS - 1)])
    assert market.probability("A Player", "B Player") is None


def test_a_wildly_disagreeing_book_is_treated_as_no_quote():
    market = MarketBook()
    market.absorb([_event("A Player", "B Player", 1.5, 2.5, n_books=20)])
    key = (surname_key("A Player"), surname_key("B Player"))
    market.spreads[key] = MAX_SPREAD + 0.01
    assert market.probability("A Player", "B Player") is None


def test_an_unknown_match_returns_no_ladders(book):
    assert book.ladders_for("Nobody One", "Nobody Two") == {}


def test_fetch_never_raises_without_a_key(monkeypatch, tmp_path):
    """No market is bad; no bot is worse.

    The snapshot directory is redirected at a temporary path so this exercises
    the no-key route itself rather than picking up whatever cached quotes happen
    to be on disk; the cache fallback has its own test below.
    """
    monkeypatch.setattr("bot.marketbook.SNAPSHOT_DIR", tmp_path)
    monkeypatch.setenv("ODDS_API_KEY", "")
    monkeypatch.setattr(
        "bot.marketbook.OddsClient.from_env",
        classmethod(
            lambda cls: (_ for _ in ()).throw(
                __import__("bot.data.oddsapi", fromlist=["OddsError"]).OddsError("no key")
            )
        ),
    )
    market = MarketBook.fetch()
    assert len(market) == 0
    assert market.skipped


def test_pricer_uses_the_quote_when_given_one():
    """Regression: all 64 Round 1 match-winner questions carried the note 'no
    market quote; this is the family that most needs one'. The pricer knew how
    to blend at weight 0.85 and nothing was passing it a quote, while a
    de-vigged consensus across 40 books sat one HTTP call away."""
    from bot.markets.parser import parse_question
    from bot.pricer import Pricer
    from bot.roster import Roster

    roster = Roster.load()
    a, b = "C. Alcaraz", "J. Sinner"
    context = roster.context(a, b, tour="ATP")
    spec = parse_question(f"Will {a} win?", players=(a, b))

    without = Pricer().price(spec, context, market_id="x")
    ladders = {"match_winner": {context.player_a.name: 0.90, context.player_b.name: 0.10}}
    with_market = Pricer().price(spec, context, market_id="x", ladders=ladders)

    assert without.market_probability is None
    assert with_market.market_probability == pytest.approx(0.90)
    assert with_market.market_weight > 0.5
    assert with_market.submission > without.submission


def test_run_passes_the_market_through_to_the_pricer(tmp_path, monkeypatch):
    """Regression, and the third time this exact failure mode has bitten.

    A string-replace patch to the collect() call silently no-opped because ruff
    had already reformatted the target onto one line. The run fetched 252
    de-vigged quotes, printed that it had them, and then dropped them: every
    match-winner question carried 'no market quote; this is the family that most
    needs one' while the quotes sat in a local variable.

    Printing that something was loaded is not evidence it was used. This test
    asserts the quote reaches the priced output.
    """
    from unittest.mock import patch

    import bot.cli as cli
    from bot.sportspredict.config import Config
    from bot.tests.test_cli import FakeClient, _matches

    fake = FakeClient(_matches())
    book = MarketBook()
    book.absorb(
        [
            _event("Carlos Alcaraz", "Jannik Sinner", 1.60, 2.40),
            _event("Aryna Sabalenka", "Iga Swiatek", 2.10, 1.75),
        ]
    )

    captured = {}
    real_collect = cli.collect

    def spy(*args, **kwargs):
        captured["market_book"] = kwargs.get("market_book")
        return real_collect(*args, **kwargs)

    with (
        patch.object(cli, "SportsPredictClient", lambda c: fake),
        patch.object(cli, "MarketBook", type("M", (), {"fetch": staticmethod(lambda: book)})),
        patch.object(cli, "collect", spy),
        patch.object(Config, "from_env", classmethod(lambda cls, **k: cls(api_key="sp_live_x"))),
    ):
        cli.run(["--out", str(tmp_path)])

    assert captured["market_book"] is book, "collect() was called without the market"

    result = real_collect(
        fake,
        __import__("bot.roster", fromlist=["Roster"]).Roster.load(),
        __import__("bot.pricer", fromlist=["Pricer"]).Pricer(),
        market_book=book,
    )
    winners = [q for qs in result.by_match.values() for q in qs if q.family.value == "match_winner"]
    assert winners, "no match-winner questions produced"
    assert any(q.market_probability is not None for q in winners), (
        "the market was passed but no question used it"
    )


def test_a_cached_snapshot_is_used_when_the_quota_is_exhausted(monkeypatch, tmp_path):
    """The free quota runs out exactly when it matters most.

    Both semi-finals and both finals score at 3x, and that is precisely when the
    500-request tier is spent.  Discarding a quote fetched hours earlier and
    pricing the final from the model alone measured 271.7 skill points worse
    across 107 settled match-winner questions, so a recent snapshot is reused
    rather than thrown away.
    """
    import json

    from bot.data.oddsapi import OddsError

    event = {
        "home_team": "Alexander Zverev",
        "away_team": "Ben Shelton",
        "bookmakers": [
            {
                "key": f"book{i}",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Alexander Zverev", "price": 1.75},
                            {"name": "Ben Shelton", "price": 2.10},
                        ],
                    }
                ],
            }
            for i in range(12)
        ],
    }
    (tmp_path / "atp.json").write_text(json.dumps([event]), encoding="utf-8")
    monkeypatch.setattr("bot.marketbook.SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(
        "bot.marketbook.OddsClient.from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(OddsError("quota exhausted"))),
    )

    market = MarketBook.fetch()
    quote = market.probability("B. Shelton", "A. Zverev")
    assert quote is not None, "a fresh snapshot on disk must be used"
    assert 0.4 < quote < 0.5
    assert any("cached quotes" in note for note in market.skipped)


def test_a_stale_snapshot_is_ignored(monkeypatch, tmp_path):
    """A quote from a previous round is about a different match entirely."""
    import json
    import os
    import time

    from bot.data.oddsapi import OddsError
    from bot.marketbook import MAX_SNAPSHOT_AGE_SECONDS

    path = tmp_path / "atp.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    old = time.time() - MAX_SNAPSHOT_AGE_SECONDS - 3600
    os.utime(path, (old, old))
    monkeypatch.setattr("bot.marketbook.SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(
        "bot.marketbook.OddsClient.from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(OddsError("quota exhausted"))),
    )

    market = MarketBook.fetch()
    assert not any("cached quotes" in note for note in market.skipped)
