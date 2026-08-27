"""End-to-end tests for the runner, against a fake API.

These are the only tests that exercise the whole path: event, matches, markets,
parse, price, validate, submit.  Every component has its own tests and they all
passed while the pipeline as a whole submitted nothing at all, which is exactly
the gap these close.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.cli import _parse_args, archive, collect, summarise
from bot.markets.parser import Family
from bot.pricer import Pricer
from bot.roster import BEST_OF, Roster, normalise_round, players_from_match

QUESTIONS = [
    "Will the match include a tiebreak?",
    "Will the match need a deciding set?",
    "Will a set end in a lopsided score?",
    "Will {a} serve more aces than {b}?",
    "Will {a} hit more double faults than {b}?",
    "Will the match last more than 150 minutes?",
    "Will the first set contain 10 or more games?",
    "Will {a} hit more winners than {b}?",
    "Will {a} rack up more than 30 unforced errors?",
    "Will {a} win more than 65% of points on serve?",
]


class FakeClient:
    """A SportsPredictClient stand-in that records what it was asked to do."""

    def __init__(self, matches, *, event_title="US Open Grand Slam Challenge"):
        self.event = {"id": "evt1", "title": event_title, "type": "probability"}
        self.lobby = {"id": "lob1", "joined": True}
        self._matches = matches
        self.reconciled: dict | None = None
        self.market_calls: list[str] = []

    def bootstrap(self):
        return self.event, self.lobby

    def list_matches(self, event_id=None, lobby_id=None):
        return self._matches

    def list_markets(self, lobby_id, match_id=None):
        self.market_calls.append(match_id)
        match = next(m for m in self._matches if m["id"] == match_id)
        a, b = players_from_match(match)
        return [
            {"id": f"{match_id}-m{i}", "question": text.format(a=a, b=b)}
            for i, text in enumerate(QUESTIONS)
        ]

    def list_predictions(self, lobby_id=None):
        return []

    def reconcile(self, lobby_id, desired):
        self.reconciled = {"lobby_id": lobby_id, "desired": desired}
        return {"created": len(desired), "updated": 0}


def _matches():
    return [
        {
            "id": "m1",
            "title": "Carlos Alcaraz vs Jannik Sinner",
            "round": "Final",
            "tour": "ATP",
        },
        {
            "id": "m2",
            "player_a": {"name": "Aryna Sabalenka"},
            "player_b": {"name": "Iga Swiatek"},
            "round": "Semifinal",
            "tour": "WTA",
        },
    ]


@pytest.fixture(scope="module")
def roster():
    return Roster.load()


@pytest.fixture(scope="module")
def result(roster):
    return collect(FakeClient(_matches()), roster, Pricer())


# --------------------------------------------------------------- the pipeline


def test_collect_prices_every_market(result):
    assert len(result.priced) == len(QUESTIONS) * 2
    assert set(result.by_match) == {"m1", "m2"}


def test_every_submission_is_in_api_range(result):
    for question in result.priced:
        assert 1 <= question.submission <= 99, question.market_id


def test_markets_are_fetched_per_match(roster):
    client = FakeClient(_matches())
    collect(client, roster, Pricer())
    assert client.market_calls == ["m1", "m2"]


def test_context_carries_tour_and_format(result):
    assert result.contexts["m1"].tour == "ATP"
    assert result.contexts["m1"].best_of == 5
    assert result.contexts["m2"].tour == "WTA"
    assert result.contexts["m2"].best_of == 3


def test_rounds_are_normalised(result):
    assert result.rounds["m1"] == "F"
    assert result.rounds["m2"] == "SF"


def test_real_players_resolve_to_ratings(result):
    """If this fails the bot still runs and prices everything at the tour
    average, which is the failure that looks most like success."""
    for context in result.contexts.values():
        assert context.player_a.has_ratings, context.player_a.name
        assert context.player_b.has_ratings, context.player_b.name


def test_questions_are_understood(result):
    unknown = [q for q in result.priced if q.family is Family.OTHER]
    assert not unknown, [q.market_id for q in unknown]


def test_five_set_and_three_set_price_differently(result):
    """A best-of-five decider is not a best-of-three decider."""
    atp = _by_family(result.by_match["m1"], Family.DECIDING_SET)
    wta = _by_family(result.by_match["m2"], Family.DECIDING_SET)
    assert atp.submission != wta.submission


def _by_family(questions, family):
    return next(q for q in questions if q.family is family)


# --------------------------------------------------------------- the archive


def test_archive_is_written_before_anything_is_sent(result, tmp_path):
    path = archive(result, tmp_path)
    payload = json.loads(path.read_text())
    assert payload["n_questions"] == len(result.priced)
    assert set(payload["matches"]) == {"m1", "m2"}
    assert payload["matches"]["m1"]["questions"][0]["submission"] >= 1


def test_archive_records_whether_players_were_rated(result, tmp_path):
    payload = json.loads(archive(result, tmp_path).read_text())
    assert payload["matches"]["m1"]["rated_a"] is True


def test_summary_mentions_unrated_players(roster, tmp_path):
    unknown = [{"id": "m9", "title": "Nobody One vs Nobody Two", "round": "R128"}]
    text = summarise(collect(FakeClient(unknown), roster, Pricer()))
    assert "UNRATED" in text


# ------------------------------------------------------------------- the flags


def test_dry_run_is_the_default():
    assert _parse_args([]).submit is False


def test_force_requires_submit():
    with pytest.raises(SystemExit):
        _parse_args(["--force"])


def test_submit_is_explicit():
    assert _parse_args(["--submit"]).submit is True


# -------------------------------------------------------------- name handling


@pytest.mark.parametrize(
    "match,expected",
    [
        ({"title": "A B vs C D"}, ("A B", "C D")),
        ({"title": "A B vs. C D"}, ("A B", "C D")),
        ({"title": "A B - C D"}, ("A B", "C D")),
        ({"player_a": "A B", "player_b": "C D"}, ("A B", "C D")),
        ({"home": {"name": "A B"}, "away": {"name": "C D"}}, ("A B", "C D")),
        ({"players": [{"full_name": "A B"}, {"full_name": "C D"}]}, ("A B", "C D")),
        ({"title": "no separator here"}, ("", "")),
    ],
)
def test_player_extraction_handles_payload_shapes(match, expected):
    assert players_from_match(match) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Final", "F"),
        ("final", "F"),
        ("Semifinal", "SF"),
        ("Quarter-final", "QF"),
        ("Round of 16", "R16"),
        ("R32", "R32"),
        ("First Round", "R128"),
        (None, "R128"),
        ("nonsense", "R128"),
    ],
)
def test_round_normalisation(value, expected):
    assert normalise_round(value) == expected


def test_roster_resolves_names_through_the_crosswalk(roster):
    assert roster.find("Carlos Alcaraz") is not None
    assert roster.find("C. Alcaraz") is not None


def test_unresolved_names_are_counted_not_swallowed(roster):
    fresh = Roster.load()
    fresh.find("Definitely Not A Player")
    assert fresh.miss_report[0][0] == "Definitely Not A Player"


def test_unrated_profile_is_visibly_unrated(roster):
    assert roster.profile("Definitely Not A Player").has_ratings is False


def test_best_of_matches_grand_slam_rules():
    assert BEST_OF == {"ATP": 5, "WTA": 3}


def test_player_data_is_present():
    """The runner refuses to start without this, so make the failure loud."""
    directory = Path("data/players")
    assert (directory / "atp_players.csv").exists()
    assert (directory / "wta_players.csv").exists()
