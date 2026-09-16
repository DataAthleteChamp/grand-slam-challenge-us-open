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

from bot.cli import (
    _parse_args,
    apply_override_manifest,
    archive,
    build_count_book,
    collect,
    summarise,
)
from bot.markets.parser import Family
from bot.pricer import Pricer
from bot.roster import BEST_OF, Roster, normalise_round, players_from_match

#: The five shapes the competition actually asks, verified from the live API on
#: 29 Aug 2026. The fake previously used ten invented shapes that included no
#: match-winner question at all, so the whole CLI suite exercised questions the
#: competition does not ask and missed the family worth a fifth of the score.
QUESTIONS = [
    "Will the match last longer than 150 minutes?",
    "Will {b} record more aces than {a}?",
    "Will {b} record more double faults than {a}?",
    "Will {b} win?",
    "Will the match feature at least 2 tiebreak(s)?",
]


class FakeClient:
    """A SportsPredictClient stand-in that records what it was asked to do."""

    def __init__(self, matches, *, event_title="US Open Grand Slam Challenge"):
        self.event = {"id": "evt1", "title": event_title, "type": "probability"}
        self.lobby = {"id": "lob1", "joined": True}
        self._matches = matches
        self.reconciled: dict | None = None
        self.written: dict | None = None
        self.stored: dict[str, int] = {}
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
        return [
            {"id": f"p-{mid}", "market_id": mid, "probability": prob}
            for mid, prob in self.stored.items()
        ]

    def upsert_batch(self, lobby_id, desired, *, existing=None):
        """The WRITE path. Only this actually stores anything.

        The previous fake made reconcile record the batch, which made a
        submission path that called only reconcile look like it worked. The fake
        now mirrors the real split: reconcile reads, upsert_batch writes.
        """
        self.written = {"lobby_id": lobby_id, "desired": dict(desired)}
        created = sum(1 for mid in desired if mid not in self.stored)
        self.stored.update(desired)
        return {"created": created, "updated": len(desired) - created, "failed": 0, "errors": []}

    def reconcile(self, lobby_id, desired):
        """Read-only, exactly like the real client."""
        self.reconciled = {"lobby_id": lobby_id, "desired": dict(desired)}
        missing = sorted(set(desired) - set(self.stored))
        mismatched = {
            mid: {"desired": p, "actual": self.stored[mid]}
            for mid, p in desired.items()
            if mid in self.stored and self.stored[mid] != p
        }
        return {
            "in_sync": not missing and not mismatched,
            "missing": missing,
            "mismatched": mismatched,
            "submitted": len(self.stored),
            "desired": len(desired),
        }


def _matches():
    return [
        {
            "id": "m1",
            "title": "Carlos Alcaraz vs Jannik Sinner",
            "round": "First Round",
            "tour": "ATP",
        },
        {
            "id": "m2",
            "player_a": {"name": "Aryna Sabalenka"},
            "player_b": {"name": "Iga Swiatek"},
            "round": "First Round",
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
    assert result.rounds["m1"] == "R128"
    assert result.rounds["m2"] == "R128"


def test_missing_round_stays_unknown_even_with_twenty_questions(roster):
    class FinalClient(FakeClient):
        def list_markets(self, lobby_id, match_id=None):
            return [
                {
                    "id": f"{match_id}-final-{i}",
                    "question": "Will Alexander Zverev win?",
                }
                for i in range(20)
            ]

    match = {
        "id": "final",
        "title": "Alexander Zverev vs Ben Shelton",
        "tour": "ATP",
    }
    result = collect(FinalClient([match]), roster, Pricer())
    assert result.rounds["final"] == "UNKNOWN"
    assert result.contexts["final"].round_code == "UNKNOWN"


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
    """A best-of-five match is not a best-of-three one.

    Tiebreak count is the sharpest live discriminator: two tiebreaks occur in
    13.4% of men's slam matches against 2.0% of women's.
    """
    atp = _by_family(result.by_match["m1"], Family.TIEBREAK)
    wta = _by_family(result.by_match["m2"], Family.TIEBREAK)
    assert atp.submission != wta.submission
    assert atp.submission > wta.submission


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
        (None, "UNKNOWN"),
        ("nonsense", "UNKNOWN"),
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


def test_runtime_loads_the_validated_draw_rate_overlay(roster):
    import csv

    with Path("data/players/us_open_2026_draw.csv").open() as handle:
        row = next(row for row in csv.DictReader(handle) if row["canonical_name"] == "Iga Swiatek")
    profile = roster.find("Iga Swiatek")
    assert profile.ace_rate == pytest.approx(float(row["ace_rate"]))
    assert profile.df_rate == pytest.approx(float(row["df_rate"]))


# ------------------------------------------------- draw roster and overrides


def test_verified_overrides_beat_the_automatic_rules(roster):
    """'X. Wang' is Xiyu, not the better-attested Xinyu. A hand-verified mapping
    has to outrank career record, or the automatic rule silently wins."""
    if not roster.overrides:
        pytest.skip("no overrides file")
    assert roster.find("X. Wang").name == "Xiyu Wang"
    assert roster.find("D. Blanch").name == "Darwin Blanch"


def test_an_override_with_an_empty_value_means_verified_absent(roster):
    """Different from 'not looked at yet': it must not be retried by the
    automatic rules and must not resolve to a wrong player."""
    if "T. Frodin" not in roster.overrides:
        pytest.skip("no Frodin override")
    assert roster.find("T. Frodin") is None


def test_the_whole_draw_resolves_except_verified_absences(roster):
    import glob
    import json

    files = sorted(glob.glob("data/corpus/corpus-*.json"))
    if not files:
        pytest.skip("no corpus captured")
    corpus = json.loads(open(files[-1]).read())
    names = set()
    for record in corpus["matches"]:
        left, _, right = record["match"]["name"].partition(" vs ")
        names.update({left.strip(), right.strip()})
    if not names:
        pytest.skip("corpus has no matches yet")
    unresolved = {n for n in names if roster.find(n) is None}
    assert unresolved <= {k for k, v in roster.overrides.items() if not v}, unresolved


# ------------------------------------------------- the submission path


def _run(argv, fake):
    from unittest.mock import patch

    import bot.cli as cli
    from bot.sportspredict.config import Config

    with (
        patch.object(cli, "SportsPredictClient", lambda c: fake),
        patch.object(Config, "from_env", classmethod(lambda cls, **k: cls(api_key="sp_live_x"))),
    ):
        return cli.run(argv)


def test_completed_event_refuses_submission(tmp_path):
    fake = FakeClient(_matches())
    code = _run(["--submit", "--force", "--out", str(tmp_path)], fake)
    assert fake.written is None
    assert fake.reconciled is None
    assert not fake.stored
    assert code == 2


def test_dry_run_writes_nothing(tmp_path):
    fake = FakeClient(_matches())
    ledger_dir = tmp_path / "ledger"
    _run(["--out", str(tmp_path), "--ledger-dir", str(ledger_dir)], fake)
    assert fake.written is None
    assert not fake.stored
    assert not ledger_dir.exists()


def test_override_manifest_changes_desired_state_before_archiving(roster, tmp_path):
    local_result = collect(FakeClient(_matches()), roster, Pricer())
    market_id = local_result.priced[0].market_id
    path = tmp_path / "overrides.json"
    path.write_text(
        json.dumps(
            {
                "submitted_overrides": [
                    {
                        "market_id": market_id,
                        "probability": 73,
                        "reason": "verified direct market",
                    }
                ]
            }
        )
    )
    assert apply_override_manifest(local_result, path) == 1
    overridden = next(
        question for question in local_result.priced if question.market_id == market_id
    )
    assert overridden.submission == 73
    assert overridden.probability == pytest.approx(0.73)
    assert "manual override" in overridden.notes[-1]


def test_count_quotes_are_keyed_by_both_players(tmp_path):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    start = int((now + timedelta(hours=1)).timestamp() * 1000)
    records = [
        {
            "match": "A Player vs B Rival",
            "start_time": start,
            "family": "aces",
            "player": "A Player",
            "p_more": 0.60,
        },
        {
            "match": "A Player vs C Challenger",
            "start_time": start,
            "family": "aces",
            "player": "A Player",
            "p_more": 0.40,
        },
    ]
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    (tmp_path / f"bovada-{stamp}.json").write_text(json.dumps(records))
    book = build_count_book(tmp_path, now=now)
    assert book[("aces", "player", "rival")] == pytest.approx(0.60)
    assert book[("aces", "player", "challenger")] == pytest.approx(0.40)
    assert len(book) == 2
