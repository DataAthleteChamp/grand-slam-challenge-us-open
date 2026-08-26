"""tennis-data.co.uk ingestion: recent results plus bookmaker odds.

Fills two gaps the Sackmann-schema mirrors leave open.

**Recency.**  The full-schema serve-statistic files stop at 2026-05-25, when the
upstream repositories were deleted.  This source runs to 2026-08-24 — through
Cincinnati, Toronto and Washington, the entire US Open warm-up — so the match
shape questions can be answered from current data even though serve rates cannot.

**Odds.**  Pinnacle, Bet365, Betfair Exchange and market-average prices for every
match, which is the only free historical record of what the market thought.  That
matters for a competition scored against a crowd: it lets us measure, on
completed matches, how well market-implied probabilities would have answered the
structural questions, rather than assuming they would have done well.

There are no serve statistics here.  Use it for scorelines, recency and odds; use
the Sackmann mirrors for serve rates.

Note the file layout: per-tournament ``.csv`` paths redirect to HTML, so fetch the
per-year ``.xlsx`` files instead.  HTTPS fails on this host; HTTP is required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .ingest import RAW_DIR, _fetch

BASE_URL = "http://www.tennis-data.co.uk"

#: Bookmaker odds columns, winner/loser pairs.  Pinnacle is the sharpest of these
#: and Betfair Exchange the closest to a true probability, so prefer them.
ODDS_COLUMNS = {
    "pinnacle": ("PSW", "PSL"),
    "bet365": ("B365W", "B365L"),
    "market_max": ("MaxW", "MaxL"),
    "market_avg": ("AvgW", "AvgL"),
    "betfair": ("BFEW", "BFEL"),
}

_SET_COLUMNS = [("W1", "L1"), ("W2", "L2"), ("W3", "L3"), ("W4", "L4"), ("W5", "L5")]


def fetch_year(year: int, tour: str = "atp", *, refresh: bool = False) -> Path | None:
    """Download one season.  ``tour`` is ``atp`` or ``wta``."""
    suffix = "" if tour.lower() == "atp" else "w"
    destination = RAW_DIR / "tennisdata" / f"{tour.lower()}_{year}.xlsx"
    if destination.exists() and not refresh:
        return destination
    url = f"{BASE_URL}/{year}{suffix}/{year}.xlsx"
    return destination if _fetch(url, destination, timeout=90.0) else None


def load_years(years: list[int], tour: str = "atp", *, refresh: bool = False) -> pd.DataFrame:
    """Load several seasons of results and odds."""
    frames = []
    for year in years:
        path = fetch_year(year, tour, refresh=refresh)
        if path is None:
            continue
        frame = pd.read_excel(path)
        frame["season"] = year
        frame["tour"] = tour.upper()
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return _enrich(pd.concat(frames, ignore_index=True))


def _enrich(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach derived scoreline features and de-vigged market probabilities."""
    frame["date"] = pd.to_datetime(frame.get("Date"), errors="coerce")
    frame["completed"] = (
        frame.get("Comment", pd.Series(dtype=str)).astype(str).str.strip().eq("Completed")
    )

    sets_played, total_games, tiebreaks, lopsided = [], [], [], []
    for row in frame.itertuples():
        scores = []
        for winner_col, loser_col in _SET_COLUMNS:
            w, loss = getattr(row, winner_col, None), getattr(row, loser_col, None)
            if pd.isna(w) or pd.isna(loss):
                continue
            scores.append((int(w), int(loss)))
        sets_played.append(len(scores) or None)
        total_games.append(sum(a + b for a, b in scores) or None)
        tiebreaks.append(
            any(max(a, b) == 7 and min(a, b) == 6 for a, b in scores) if scores else None
        )
        lopsided.append(
            any(min(a, b) <= 2 and max(a, b) == 6 for a, b in scores) if scores else None
        )

    frame["sets_played"] = sets_played
    frame["total_games"] = total_games
    frame["had_tiebreak"] = tiebreaks
    frame["had_lopsided_set"] = lopsided
    frame["went_distance"] = frame["sets_played"] == frame.get("Best of")

    for book, (winner_col, loser_col) in ODDS_COLUMNS.items():
        if winner_col in frame.columns and loser_col in frame.columns:
            frame[f"p_{book}"] = devig_two_way(frame[winner_col], frame[loser_col])
    return frame


def devig_two_way(odds_winner: pd.Series, odds_loser: pd.Series) -> pd.Series:
    """Remove the bookmaker margin from a two-way market by normalisation.

    Returns the implied probability of the player who actually won, which is what
    makes these rows usable directly as calibration data: the outcome is known
    and always 1.

    Simple normalisation is used rather than Shin or the power method because it
    is transparent and, on two-way markets with a small margin, the three agree
    to within a fraction of a point.
    """
    win = pd.to_numeric(odds_winner, errors="coerce")
    lose = pd.to_numeric(odds_loser, errors="coerce")
    implied_win, implied_lose = 1.0 / win, 1.0 / lose
    overround = implied_win + implied_lose
    return (implied_win / overround).where(overround > 0)


def normalise_players(frame: pd.DataFrame, crosswalk: dict[str, str]) -> pd.DataFrame:
    """Resolve ``Winner``/``Loser`` (``Last F.`` form) to canonical full names.

    Unresolved names become ``None`` rather than being guessed at, so a failed
    join is visible in the data instead of silently producing a wrong row.
    """
    from .names import resolve

    frame = frame.copy()
    frame["winner_canonical"] = frame["Winner"].map(lambda n: resolve(str(n), crosswalk))
    frame["loser_canonical"] = frame["Loser"].map(lambda n: resolve(str(n), crosswalk))
    return frame


def market_calibration(frame: pd.DataFrame, book: str = "pinnacle") -> dict[str, float]:
    """How well did this book's de-vigged prices predict the actual winner?

    Because the row is oriented around the winner, a perfectly calibrated book
    would show a mean implied probability equal to its hit rate, and the Brier
    score is computed against an outcome of 1 throughout.
    """
    column = f"p_{book}"
    if column not in frame.columns:
        return {}
    probabilities = pd.to_numeric(frame[column], errors="coerce").dropna()
    if probabilities.empty:
        return {}
    return {
        "n": int(len(probabilities)),
        "mean_implied": float(probabilities.mean()),
        "brier": float(((probabilities - 1.0) ** 2).mean()),
        "favourite_hit_rate": float((probabilities > 0.5).mean()),
    }


def us_open_history(frame: pd.DataFrame) -> pd.DataFrame:
    """Rows from US Open main draws only."""
    name = frame.get("Tournament", pd.Series(dtype=str)).astype(str)
    return frame[name.str.contains(re.compile(r"us\s*open", re.I), na=False)]
