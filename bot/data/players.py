"""Build the tidy player-match table.

One row per (match, player), carrying that player's serve statistics, the
opponent they were achieved against, and the match context.

This shape is the point.  A pre-aggregated "per-player averages" table cannot be
opponent-adjusted after the fact, cannot be re-sliced by surface or opponent
strength, and loses the sample sizes that shrinkage needs.  Every summary in the
system is a view over this table.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Serve columns in the source schema, prefixed ``w_``/``l_`` for winner/loser.
_SERVE_COLUMNS = (
    "ace",
    "df",
    "svpt",
    "1stIn",
    "1stWon",
    "2ndWon",
    "SvGms",
    "bpSaved",
    "bpFaced",
)

#: Qualifying rounds sort *before* the main draw. Leaving them unmapped made
#: them NaN, and pandas sorts NaN last, so a player's Q1 match was compared
#: against the final when computing rest and previous-match load. That silently
#: corrupted the fatigue features for 1,055 of 8,283 rows once qualifying data
#: was ingested.
_ROUND_ORDER = {
    "Q1": -3,
    "Q2": -2,
    "Q3": -1,
    "R128": 1,
    "R64": 2,
    "R32": 3,
    "R16": 4,
    "QF": 5,
    "SF": 6,
    "F": 7,
    "RR": 3,
    "BR": 6,
}


def build_player_matches(matches: pd.DataFrame) -> pd.DataFrame:
    """Explode a winner/loser match table into one row per player per match."""
    if matches.empty:
        return pd.DataFrame()

    frames = []
    for side, other in (("w", "l"), ("l", "w")):
        prefix = "winner" if side == "w" else "loser"
        opp_prefix = "loser" if side == "w" else "winner"

        frame = pd.DataFrame(
            {
                "tour": matches.get("tour"),
                "tourney_id": matches.get("tourney_id"),
                "tourney_name": matches.get("tourney_name"),
                "tourney_level": matches.get("tourney_level"),
                "tourney_date": matches.get("tourney_date"),
                "surface": matches.get("surface"),
                "round": matches.get("round"),
                "best_of": matches.get("best_of"),
                "minutes": matches.get("minutes"),
                "score": matches.get("score"),
                "player": matches.get(f"{prefix}_name"),
                "player_id": matches.get(f"{prefix}_id"),
                "player_hand": matches.get(f"{prefix}_hand"),
                "player_ht": matches.get(f"{prefix}_ht"),
                "player_age": matches.get(f"{prefix}_age"),
                "player_rank": matches.get(f"{prefix}_rank"),
                "opponent": matches.get(f"{opp_prefix}_name"),
                "opponent_id": matches.get(f"{opp_prefix}_id"),
                "opponent_hand": matches.get(f"{opp_prefix}_hand"),
                "opponent_ht": matches.get(f"{opp_prefix}_ht"),
                "opponent_rank": matches.get(f"{opp_prefix}_rank"),
                "won": 1 if side == "w" else 0,
            }
        )
        for column in _SERVE_COLUMNS:
            frame[column] = matches.get(f"{side}_{column}")
            frame[f"opp_{column}"] = matches.get(f"{other}_{column}")
        frames.append(frame)

    long = pd.concat(frames, ignore_index=True)
    long["date"] = pd.to_datetime(
        long["tourney_date"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce"
    )
    long["round_order"] = long["round"].map(_ROUND_ORDER)
    return _add_rates(long)


def _add_rates(long: pd.DataFrame) -> pd.DataFrame:
    """Attach per-point rates.  Rates, not counts — counts scale with match length.

    Numeric columns are coerced explicitly.  Combining several sources means some
    arrive with stray strings or empty fields in otherwise numeric columns, and
    an uncoerced column propagates a type error deep into the pipeline where it
    is hard to attribute.
    """
    numeric_columns = list(_SERVE_COLUMNS) + [f"opp_{c}" for c in _SERVE_COLUMNS]
    numeric_columns += ["minutes", "player_ht", "opponent_ht", "player_rank", "opponent_rank"]
    for column in numeric_columns:
        if column in long.columns:
            long[column] = pd.to_numeric(long[column], errors="coerce")

    svpt = long["svpt"].replace(0, np.nan)
    long["ace_rate"] = long["ace"] / svpt
    long["df_rate"] = long["df"] / svpt
    long["first_in_rate"] = long["1stIn"] / svpt
    long["first_won_rate"] = long["1stWon"] / long["1stIn"].replace(0, np.nan)
    long["second_won_rate"] = long["2ndWon"] / (svpt - long["1stIn"]).replace(0, np.nan)
    long["spw"] = (long["1stWon"] + long["2ndWon"]) / svpt

    # Return points won is the mirror of the opponent's service points.
    opp_svpt = long["opp_svpt"].replace(0, np.nan)
    long["rpw"] = 1.0 - (long["opp_1stWon"] + long["opp_2ndWon"]) / opp_svpt
    long["opp_ace_rate"] = long["opp_ace"] / opp_svpt
    return long


#: Rows failing these are impossible, not merely unusual, and are dropped.
_VALIDATION_RULES = {
    "first_serves_exceed_service_points": lambda d: d["1stIn"] > d["svpt"],
    "first_serve_wins_exceed_first_serves": lambda d: d["1stWon"] > d["1stIn"],
    "second_serve_wins_exceed_attempts": lambda d: d["2ndWon"] > (d["svpt"] - d["1stIn"]),
    "aces_exceed_service_points": lambda d: d["ace"] > d["svpt"],
    "double_faults_exceed_second_serves": lambda d: d["df"] > (d["svpt"] - d["1stIn"]),
    "break_points_saved_exceed_faced": lambda d: d["bpSaved"] > d["bpFaced"],
    "implausible_ace_rate": lambda d: (d["ace"] / d["svpt"].replace(0, np.nan)) > 0.40,
    "negative_counts": lambda d: (d[list(_SERVE_COLUMNS)] < 0).any(axis=1),
}


def validate(long: pd.DataFrame, *, drop: bool = True) -> tuple[pd.DataFrame, dict[str, int]]:
    """Check rows against arithmetic identities that cannot legitimately fail.

    Every rule encodes a fact about tennis rather than a guess about the data: a
    player cannot land more first serves than they hit service points, nor save
    more break points than they faced.  A row breaking one is corrupt, and
    keeping it silently poisons the rate it feeds.

    Returns the (optionally filtered) frame and a count of violations per rule so
    that a source degrading over time is visible rather than absorbed.
    """
    scored = long[long["svpt"].notna() & (long["svpt"] > 0)]
    violations: dict[str, int] = {}
    bad = pd.Series(False, index=long.index)

    for name, rule in _VALIDATION_RULES.items():
        try:
            failed = rule(scored).fillna(False)
        except (KeyError, TypeError):
            continue
        count = int(failed.sum())
        if count:
            violations[name] = count
        bad.loc[failed[failed].index] = True

    return (long[~bad] if drop else long), violations


def with_recency(long: pd.DataFrame) -> pd.DataFrame:
    """Add days of rest and previous-match duration, within a tournament.

    Carryover fatigue is measurable on serve statistics: coming off a four-hour
    match costs roughly 0.8 percentage points of ace rate relative to a 90-minute
    win (p = 0.011, measured within-player over 1,490 Grand Slam matches).  The
    raw win-rate gap is far larger but mostly confounded — weaker players both
    play long matches and lose the next one.
    """
    # Drop rows whose round we cannot order rather than letting them sort last;
    # an unorderable round produces a meaningless "previous match".
    long = long.dropna(subset=["round_order"])
    long = long.sort_values(["tourney_id", "player", "round_order"]).copy()
    grouped = long.groupby(["tourney_id", "player"], sort=False)
    long["prev_minutes"] = grouped["minutes"].shift(1)
    long["prev_date"] = grouped["date"].shift(1)
    long["days_rest"] = (long["date"] - long["prev_date"]).dt.days
    return long


def summarise_players(
    long: pd.DataFrame,
    *,
    surface: str | None = "Hard",
    since: str | None = None,
    min_matches: int = 1,
) -> pd.DataFrame:
    """Aggregate the long table into per-player rates with sample sizes.

    Sample sizes are returned alongside every rate because a rate without its
    ``n`` cannot be shrunk, and an unshrunk rate from a qualifier with six
    matches is worse than no estimate at all.
    """
    frame = long
    if surface:
        frame = frame[frame["surface"] == surface]
    if since:
        frame = frame[frame["date"] >= pd.Timestamp(since)]
    frame = frame[frame["svpt"].fillna(0) > 0]
    if frame.empty:
        return pd.DataFrame()

    grouped = frame.groupby("player")
    summary = grouped.agg(
        tour=("tour", "first"),
        matches=("player", "size"),
        svpt=("svpt", "sum"),
        ace=("ace", "sum"),
        df=("df", "sum"),
        first_in=("1stIn", "sum"),
        first_won=("1stWon", "sum"),
        second_won=("2ndWon", "sum"),
        height=("player_ht", "median"),
        hand=("player_hand", lambda s: s.dropna().mode().iat[0] if s.notna().any() else None),
        rank=("player_rank", "median"),
        last_played=("date", "max"),
        wins=("won", "sum"),
    ).reset_index()

    summary["ace_rate"] = summary["ace"] / summary["svpt"]
    summary["df_rate"] = summary["df"] / summary["svpt"]
    summary["first_in_rate"] = summary["first_in"] / summary["svpt"]
    summary["first_won_rate"] = summary["first_won"] / summary["first_in"]
    summary["second_won_rate"] = summary["second_won"] / (summary["svpt"] - summary["first_in"])
    summary["spw"] = (summary["first_won"] + summary["second_won"]) / summary["svpt"]
    summary["win_rate"] = summary["wins"] / summary["matches"]

    # Return points won has to be computed from the opponent side of each row.
    returns = frame.groupby("player").apply(
        lambda g: (
            1.0 - (g["opp_1stWon"].sum() + g["opp_2ndWon"].sum()) / max(g["opp_svpt"].sum(), 1)
        ),
        include_groups=False,
    )
    summary["rpw"] = summary["player"].map(returns)

    return summary[summary["matches"] >= min_matches].sort_values(
        "matches", ascending=False, ignore_index=True
    )


def opponent_graph(long: pd.DataFrame) -> pd.DataFrame:
    """Head-to-head counts per (player, opponent) pair.

    Kept for provenance and for opponent-strength features, **not** as a
    predictive feature in its own right: head-to-head records add nothing over a
    properly calibrated rating, because most pairs have met fewer than five times
    and any real signal is already in the ratings.
    """
    if long.empty:
        return pd.DataFrame()
    graph = (
        long.groupby(["player", "opponent"])
        .agg(meetings=("won", "size"), wins=("won", "sum"), last=("date", "max"))
        .reset_index()
    )
    graph["losses"] = graph["meetings"] - graph["wins"]
    return graph.sort_values("meetings", ascending=False, ignore_index=True)
