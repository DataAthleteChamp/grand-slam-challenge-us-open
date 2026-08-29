"""Tests for cross-source name matching and tennis-data ingestion."""

from __future__ import annotations

import pandas as pd
import pytest

from bot.data.names import (
    ambiguous_keys,
    build_crosswalk,
    candidate_keys,
    join_report,
    match_key,
    normalise,
    resolve,
    split_name,
    strip_accents,
)
from bot.data.tennisdata import ODDS_COLUMNS, _enrich, devig_two_way, market_calibration

CANONICAL = [
    "Carlos Alcaraz",
    "Roberto Bautista Agut",
    "Felix Auger Aliassime",
    "Alex De Minaur",
    "Botic Van De Zandschulp",
    "Iga Swiatek",
    "Stefanos Tsitsipas",
]


# ----------------------------------------------------------------- normalising


def test_strip_accents_makes_names_comparable():
    assert strip_accents("Świątek") == "Swiatek"
    assert strip_accents("Müller") == "Muller"


def test_normalise_removes_case_and_punctuation():
    assert normalise("Auger-Aliassime, F.") == "auger aliassime f"


def test_normalise_handles_non_strings():
    assert normalise(None) == ""  # type: ignore[arg-type]


def test_split_name_keeps_surname_particles_together():
    assert split_name("Alex De Minaur")[0] == "de minaur"
    assert split_name("Botic Van De Zandschulp")[0] == "van de zandschulp"


def test_split_name_parses_abbreviated_form():
    surname, initials = split_name("Bautista Agut R.")
    assert surname == "bautista agut"
    assert initials == "r"


# -------------------------------------------------------------------- matching


def test_abbreviated_and_full_forms_agree():
    assert match_key("Alcaraz C.") == match_key("Carlos Alcaraz")
    assert match_key("Swiatek I.") == match_key("Iga Swiatek")


def test_compound_surnames_are_indexed_under_several_keys():
    """Where a surname begins is ambiguous in a full name, so index every split.

    Matching only the final token loses every player with a compound surname,
    which is a large and non-random slice of the tour.
    """
    keys = candidate_keys("Roberto Bautista Agut")
    assert "bautista agut|r" in keys
    assert "agut|r" in keys


def test_crosswalk_resolves_compound_surnames():
    crosswalk = build_crosswalk(CANONICAL)
    assert resolve("Bautista Agut R.", crosswalk) == "Roberto Bautista Agut"
    assert resolve("Auger-Aliassime F.", crosswalk) == "Felix Auger Aliassime"
    assert resolve("De Minaur A.", crosswalk) == "Alex De Minaur"


def test_crosswalk_resolves_accented_names():
    crosswalk = build_crosswalk(CANONICAL)
    assert resolve("Świątek I.", crosswalk) == "Iga Swiatek"


def test_unknown_name_resolves_to_none_not_a_guess():
    """A wrong join is far more damaging than a missing one."""
    crosswalk = build_crosswalk(CANONICAL)
    assert resolve("Nonexistent Q.", crosswalk) is None


def test_ambiguous_keys_are_dropped_not_guessed():
    names = ["Mike Bryan", "Bob Bryan"]
    crosswalk = build_crosswalk(names)
    ambiguous = ambiguous_keys(names)
    assert "bryan|" not in crosswalk or len(ambiguous) > 0
    for key in ambiguous:
        assert key not in crosswalk


def test_join_report_surfaces_failures():
    crosswalk = build_crosswalk(CANONICAL)
    report = join_report(["Alcaraz C.", "Unknown Z."], crosswalk)
    assert report["total"] == 2
    assert report["matched"] == 1
    assert report["unmatched"] == ["Unknown Z."]


# ------------------------------------------------------------------ tennisdata


def test_devig_removes_the_margin():
    """Two-way normalisation must produce probabilities summing to one."""
    winner = pd.Series([1.5])
    loser = pd.Series([2.5])
    p_win = devig_two_way(winner, loser).iloc[0]
    p_lose = devig_two_way(loser, winner).iloc[0]
    assert p_win + p_lose == pytest.approx(1.0)
    assert p_win > p_lose


def test_devig_handles_missing_odds():
    assert pd.isna(devig_two_way(pd.Series([None]), pd.Series([2.0])).iloc[0])


def test_enrich_derives_scoreline_features():
    frame = pd.DataFrame(
        [
            {
                "Date": "2026-08-01",
                "Comment": "Completed",
                "Best of": 3,
                "W1": 7,
                "L1": 6,
                "W2": 6,
                "L2": 1,
                "PSW": 1.5,
                "PSL": 2.5,
                "Tournament": "US Open",
            },
            {
                "Date": "2026-08-02",
                "Comment": "Completed",
                "Best of": 3,
                "W1": 6,
                "L1": 4,
                "W2": 3,
                "L2": 6,
                "W3": 6,
                "L3": 4,
                "PSW": 2.0,
                "PSL": 1.8,
                "Tournament": "US Open",
            },
        ]
    )
    out = _enrich(frame)
    assert out.iloc[0]["had_tiebreak"]
    assert out.iloc[0]["had_lopsided_set"]
    assert out.iloc[0]["total_games"] == 20  # 7-6 is 13 games, 6-1 is 7
    assert not out.iloc[1]["had_tiebreak"]
    assert out.iloc[1]["went_distance"]
    assert out.iloc[1]["sets_played"] == 3


def test_enrich_adds_devigged_probabilities():
    frame = pd.DataFrame(
        [
            {
                "Date": "2026-08-01",
                "Comment": "Completed",
                "Best of": 3,
                "W1": 6,
                "L1": 4,
                "W2": 6,
                "L2": 4,
                "PSW": 1.5,
                "PSL": 2.5,
            }
        ]
    )
    out = _enrich(frame)
    assert "p_pinnacle" in out.columns
    assert 0.5 < out.iloc[0]["p_pinnacle"] < 1.0


def test_odds_columns_cover_the_sharp_books():
    """Pinnacle and Betfair are the two worth trusting; both must be mapped."""
    assert "pinnacle" in ODDS_COLUMNS
    assert "betfair" in ODDS_COLUMNS


def test_market_calibration_reports_brier_against_a_known_outcome():
    """Rows are oriented to the winner, so the outcome is always 1."""
    frame = pd.DataFrame({"p_pinnacle": [0.8, 0.6, 0.9]})
    result = market_calibration(frame, "pinnacle")
    assert result["n"] == 3
    expected = ((0.8 - 1) ** 2 + (0.6 - 1) ** 2 + (0.9 - 1) ** 2) / 3
    assert result["brier"] == pytest.approx(expected)


def test_market_calibration_missing_book_returns_empty():
    assert market_calibration(pd.DataFrame({"p_pinnacle": [0.5]}), "nonexistent") == {}


# ------------------------------------------- canonicalisation and collisions


def test_identity_key_folds_case_and_accents_only():
    from bot.data.names import identity_key

    assert identity_key("Alex de Minaur") == identity_key("Alex De Minaur")
    assert identity_key("McCartney Kessler") == identity_key("Mccartney Kessler")
    assert identity_key("Julio Cesar Porras") == identity_key("Julio César Porras")
    # Different given names are different people and must never fold together.
    assert identity_key("Brandon Nakashima") != identity_key("Bryce Nakashima")
    assert identity_key("Xinyu Wang") != identity_key("Xiyu Wang")


def test_canonical_spellings_prefers_the_best_attested_form():
    from bot.data.names import canonical_spellings

    mapping = canonical_spellings(
        ["Alex de Minaur", "Alex De Minaur"], weights={"Alex de Minaur": 249}
    )
    assert mapping["Alex De Minaur"] == "Alex de Minaur"
    assert mapping["Alex de Minaur"] == "Alex de Minaur"


def test_canonical_spellings_is_order_independent():
    from bot.data.names import canonical_spellings

    forward = canonical_spellings(["A B", "a b", "A  B"])
    backward = canonical_spellings(["A  B", "a b", "A B"])
    assert set(forward.values()) == set(backward.values())


def test_canonical_spellings_keeps_different_players_apart():
    from bot.data.names import canonical_spellings

    mapping = canonical_spellings(["Brandon Nakashima", "Bryce Nakashima"])
    assert mapping["Brandon Nakashima"] != mapping["Bryce Nakashima"]


def test_disambiguate_resolves_an_overwhelming_favourite():
    from bot.data.names import disambiguate

    chosen = disambiguate(
        "nakashima|b",
        ["Brandon Nakashima", "Bryce Nakashima"],
        {"Brandon Nakashima": 180, "Bryce Nakashima": 2},
    )
    assert chosen == "Brandon Nakashima"


def test_disambiguate_refuses_a_close_call():
    """The case that justifies the threshold.

    'X. Wang' in the 2026 draw is Xiyu Wang (98 matches), not Xinyu Wang (157).
    A ratio of 1.6 is not evidence, and a lower threshold would have supplied a
    real but wrong player's ratings with every downstream check passing.
    """
    from bot.data.names import disambiguate

    assert (
        disambiguate("wang|x", ["Xinyu Wang", "Xiyu Wang"], {"Xinyu Wang": 157, "Xiyu Wang": 98})
        is None
    )


def test_disambiguate_refuses_when_nobody_has_a_record():
    from bot.data.names import disambiguate

    assert disambiguate("x|y", ["A", "B"], {"A": 0, "B": 0}) is None
