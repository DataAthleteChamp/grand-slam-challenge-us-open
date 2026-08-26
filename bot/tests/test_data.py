"""Tests for the player data layer and opponent-adjusted ratings."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.data.players import (
    build_player_matches,
    opponent_graph,
    summarise_players,
    with_recency,
)
from bot.models.ratings import ServeReturnRatings, fit_serve_return, matchup_point_probabilities


def _match(winner, loser, *, w_ace=8, l_ace=4, w_svpt=80, l_svpt=80, date=20260101, rnd="R32"):
    """One synthetic match row in the source (winner/loser) schema."""
    return {
        "tour": "ATP",
        "tourney_id": "2026-100",
        "tourney_name": "Test Open",
        "tourney_level": "G",
        "tourney_date": date,
        "surface": "Hard",
        "round": rnd,
        "best_of": 5,
        "minutes": 120,
        "score": "6-4 6-4 6-4",
        "winner_id": hash(winner) % 10_000,
        "winner_name": winner,
        "winner_hand": "R",
        "winner_ht": 190,
        "winner_age": 25.0,
        "winner_rank": 10,
        "loser_id": hash(loser) % 10_000,
        "loser_name": loser,
        "loser_hand": "R",
        "loser_ht": 185,
        "loser_age": 27.0,
        "loser_rank": 50,
        "w_ace": w_ace,
        "w_df": 3,
        "w_svpt": w_svpt,
        "w_1stIn": 50,
        "w_1stWon": 38,
        "w_2ndWon": 15,
        "w_SvGms": 12,
        "w_bpSaved": 3,
        "w_bpFaced": 5,
        "l_ace": l_ace,
        "l_df": 4,
        "l_svpt": l_svpt,
        "l_1stIn": 48,
        "l_1stWon": 33,
        "l_2ndWon": 12,
        "l_SvGms": 12,
        "l_bpSaved": 2,
        "l_bpFaced": 6,
    }


@pytest.fixture
def matches() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _match("Alice", "Bob", rnd="R32", date=20260101),
            _match("Alice", "Carol", rnd="R16", date=20260101),
            _match("Bob", "Carol", rnd="R32", date=20260201),
            _match("Carol", "Bob", rnd="R16", date=20260301),
        ]
    )


def test_build_player_matches_creates_two_rows_per_match(matches):
    long = build_player_matches(matches)
    assert len(long) == 2 * len(matches)


def test_every_player_row_carries_its_opponent(matches):
    long = build_player_matches(matches)
    assert long["opponent"].notna().all()
    assert (long["player"] != long["opponent"]).all()


def test_winner_and_loser_rows_are_mirrored(matches):
    long = build_player_matches(matches)
    alice = long[(long.player == "Alice") & (long.opponent == "Bob")].iloc[0]
    bob = long[(long.player == "Bob") & (long.opponent == "Alice")].iloc[0]
    assert alice["ace"] == bob["opp_ace"]
    assert alice["won"] == 1 and bob["won"] == 0


def test_rates_are_per_point_not_counts(matches):
    """Counts scale with match length; rates are what generalise across matches."""
    long = build_player_matches(matches)
    row = long.iloc[0]
    assert row["ace_rate"] == pytest.approx(row["ace"] / row["svpt"])
    assert 0.0 <= row["spw"] <= 1.0


def test_return_points_won_is_derived_from_the_opponent_side(matches):
    long = build_player_matches(matches)
    row = long.iloc[0]
    expected = 1.0 - (row["opp_1stWon"] + row["opp_2ndWon"]) / row["opp_svpt"]
    assert row["rpw"] == pytest.approx(expected)


def test_with_recency_adds_previous_match_context(matches):
    long = with_recency(build_player_matches(matches))
    alice = long[long.player == "Alice"].sort_values("round_order")
    assert pd.isna(alice.iloc[0]["prev_minutes"]), "first match has no predecessor"
    assert alice.iloc[1]["prev_minutes"] == 120


def test_summarise_players_keeps_sample_sizes(matches):
    """A rate without its n cannot be shrunk, so n must survive aggregation."""
    summary = summarise_players(build_player_matches(matches))
    assert "matches" in summary.columns
    assert summary["matches"].sum() == 2 * len(matches)
    assert set(summary["player"]) == {"Alice", "Bob", "Carol"}


def test_summarise_players_filters_by_surface():
    clay = pd.DataFrame([_match("Alice", "Bob")])
    clay["surface"] = "Clay"
    assert summarise_players(build_player_matches(clay), surface="Hard").empty


def test_opponent_graph_counts_meetings(matches):
    graph = opponent_graph(build_player_matches(matches))
    pair = graph[(graph.player == "Bob") & (graph.opponent == "Carol")].iloc[0]
    assert pair["meetings"] == 2
    assert pair["wins"] + pair["losses"] == 2


# ------------------------------------------------------------------- ratings


def _synthetic_tour(n_players=12, n_matches=600, seed=0) -> pd.DataFrame:
    """Generate matches from known serve/return skills so the fit can be graded."""
    rng = np.random.default_rng(seed)
    players = [f"P{i:02d}" for i in range(n_players)]
    serve = {p: rng.normal(0, 0.03) for p in players}
    ret = {p: rng.normal(0, 0.03) for p in players}
    base = 0.64

    rows = []
    for _ in range(n_matches):
        a, b = rng.choice(players, size=2, replace=False)
        for server, returner in ((a, b), (b, a)):
            true_spw = base + serve[server] - ret[returner]
            rows.append(
                {
                    "player": server,
                    "opponent": returner,
                    "spw": float(np.clip(rng.normal(true_spw, 0.02), 0.05, 0.95)),
                    "svpt": 80,
                }
            )
    frame = pd.DataFrame(rows)
    frame.attrs["serve"] = serve
    frame.attrs["ret"] = ret
    frame.attrs["base"] = base
    return frame


def test_fit_recovers_the_baseline():
    frame = _synthetic_tour()
    ratings = fit_serve_return(frame)
    assert ratings.base == pytest.approx(frame.attrs["base"], abs=0.01)


def test_fit_recovers_the_serve_ordering():
    """The fit must rank servers correctly even though every player faces a
    different mix of opponents — that is the whole point of adjusting."""
    frame = _synthetic_tour()
    ratings = fit_serve_return(frame)
    truth = frame.attrs["serve"]
    players = sorted(truth)
    fitted = [ratings.serve.get(p, 0.0) for p in players]
    actual = [truth[p] for p in players]
    assert np.corrcoef(fitted, actual)[0, 1] > 0.9


def test_fit_recovers_the_return_ordering():
    frame = _synthetic_tour()
    ratings = fit_serve_return(frame)
    truth = frame.attrs["ret"]
    players = sorted(truth)
    fitted = [ratings.ret.get(p, 0.0) for p in players]
    actual = [truth[p] for p in players]
    assert np.corrcoef(fitted, actual)[0, 1] > 0.9


def test_fit_converges_and_reports_residual():
    ratings = fit_serve_return(_synthetic_tour())
    assert ratings.iterations >= 1
    assert 0.0 < ratings.residual_rmse < 0.1


def test_sparse_players_are_shrunk_toward_the_baseline():
    """A player with one match must not be rated as confidently as a regular."""
    frame = _synthetic_tour(n_players=8, n_matches=400)
    extra = pd.DataFrame([{"player": "NEWCOMER", "opponent": "P00", "spw": 0.95, "svpt": 80}])
    ratings = fit_serve_return(pd.concat([frame, extra], ignore_index=True))
    regular = max(abs(v) for k, v in ratings.serve.items() if k != "NEWCOMER")
    assert abs(ratings.serve["NEWCOMER"]) < regular * 1.5


def test_predict_spw_is_symmetric_in_the_right_direction():
    ratings = ServeReturnRatings(base=0.64, serve={"A": 0.05, "B": 0.0}, ret={"A": 0.0, "B": 0.02})
    assert ratings.predict_spw("A", "B") == pytest.approx(0.64 + 0.05 - 0.02)
    assert ratings.predict_spw("B", "A") == pytest.approx(0.64)


def test_matchup_probabilities_are_clipped_into_range():
    ratings = ServeReturnRatings(base=0.64, serve={"A": 5.0}, ret={"B": -5.0})
    pa, pb = matchup_point_probabilities(ratings, "A", "B")
    assert 0.05 <= pa <= 0.95
    assert 0.05 <= pb <= 0.95


def test_empty_input_does_not_raise():
    ratings = fit_serve_return(pd.DataFrame(columns=["player", "opponent", "spw", "svpt"]))
    assert np.isnan(ratings.base)


def test_as_frame_is_sorted_by_serve_skill():
    ratings = fit_serve_return(_synthetic_tour())
    frame = ratings.as_frame()
    assert list(frame["serve_skill"]) == sorted(frame["serve_skill"], reverse=True)


# ------------------------------------------------------- source level handling


def test_challenger_and_quali_files_are_men_only():
    """Regression: the Challenger and qualifying files contain men's matches only.

    Requesting them for the WTA previously returned the ATP file, which put
    Cressy, Halys and Eubanks at the top of the women's serve ratings. Silent
    cross-tour contamination is exactly the failure that face-validity checks
    exist to catch.
    """
    from bot.data.ingest import _tml_stem

    assert _tml_stem("atp", 2026, level="challenger") == "2026_challenger"
    assert _tml_stem("atp", 2026, level="quali") == "atp_quali/2026_atp_quali"
    assert _tml_stem("wta", 2026, level="challenger") is None
    assert _tml_stem("wta", 2026, level="quali") is None


def test_tour_level_stems_differ_by_tour():
    from bot.data.ingest import _tml_stem

    assert _tml_stem("atp", 2026) == "2026"
    assert _tml_stem("wta", 2026) == "2026_wta"


def test_unknown_level_is_rejected():
    from bot.data.ingest import _tml_stem

    with pytest.raises(ValueError):
        _tml_stem("atp", 2026, level="nonsense")


# ------------------------------------------------------------------ validation


def test_validation_drops_arithmetically_impossible_rows():
    """Rules encode facts about tennis, not guesses about the data."""
    from bot.data.players import validate

    frame = pd.DataFrame(
        [
            {
                "svpt": 80,
                "1stIn": 50,
                "1stWon": 40,
                "2ndWon": 20,
                "ace": 8,
                "df": 3,
                "bpSaved": 3,
                "bpFaced": 5,
                "SvGms": 12,
            },
            # First serves in exceed total service points: impossible.
            {
                "svpt": 60,
                "1stIn": 70,
                "1stWon": 40,
                "2ndWon": 10,
                "ace": 5,
                "df": 2,
                "bpSaved": 1,
                "bpFaced": 3,
                "SvGms": 10,
            },
            # More break points saved than faced: impossible.
            {
                "svpt": 70,
                "1stIn": 45,
                "1stWon": 35,
                "2ndWon": 15,
                "ace": 6,
                "df": 2,
                "bpSaved": 9,
                "bpFaced": 4,
                "SvGms": 11,
            },
        ]
    )
    kept, violations = validate(frame)
    assert len(kept) == 1
    assert "first_serves_exceed_service_points" in violations
    assert "break_points_saved_exceed_faced" in violations


def test_validation_can_report_without_dropping():
    from bot.data.players import validate

    frame = pd.DataFrame(
        [
            {
                "svpt": 60,
                "1stIn": 70,
                "1stWon": 40,
                "2ndWon": 10,
                "ace": 5,
                "df": 2,
                "bpSaved": 1,
                "bpFaced": 3,
                "SvGms": 10,
            }
        ]
    )
    kept, violations = validate(frame, drop=False)
    assert len(kept) == 1
    assert violations
