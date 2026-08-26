"""Match Charting Project ingestion.

The only public source of per-player **winners** and **unforced errors**, which
two of the ten question families depend on.  The repository is live and current,
unlike the match-statistics repositories that went down in May 2026.

Coverage is deliberately biased toward high-profile players and later rounds,
which for once works in our favour: the competition only covers matches involving
the top 32 seeds, and those are exactly the players volunteers chart most.

One caveat is load-bearing. Winners and unforced errors are assigned by a human
watching the match, and the forced/unforced boundary is a judgement call: shown
ten identical points, three separate coaching conferences agreed only three or
four times out of ten. Counts are internally consistent *within* a match but not
comparable across matches, courts or charters. Treat winner rates as weak
evidence and unforced-error rates as barely evidence at all — which is why
``DEFER_TO_BASE_RATE`` pins that family near its base rate.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .ingest import RAW_DIR, _fetch

BASE_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master"

#: ``tour`` -> file prefix used by the project ("m" for men, "w" for women).
_PREFIX = {"atp": "m", "wta": "w"}


def fetch_charting(tour: str, file: str, *, refresh: bool = False) -> Path | None:
    """Download one Match Charting Project file, e.g. ``stats-Overview``."""
    prefix = _PREFIX[tour.lower()]
    name = f"charting-{prefix}-{file}.csv"
    destination = RAW_DIR / "charting" / name
    if destination.exists() and not refresh:
        return destination
    return destination if _fetch(f"{BASE_URL}/{name}", destination) else None


def load_overview(tour: str, *, refresh: bool = False) -> pd.DataFrame:
    """Load per-match, per-player totals: winners, unforced errors, serve counts.

    Only the ``Total`` rows are kept; the file also carries a row per set.
    """
    stats_path = fetch_charting(tour, "stats-Overview", refresh=refresh)
    if stats_path is None:
        return pd.DataFrame()

    stats = pd.read_csv(stats_path, on_bad_lines="skip", low_memory=False)
    stats = stats[stats["set"].astype(str).str.lower() == "total"].copy()

    meta_path = fetch_charting(tour, "matches", refresh=refresh)
    if meta_path is not None:
        meta = pd.read_csv(meta_path, on_bad_lines="skip", low_memory=False)
        keep = ["match_id", "Date", "Tournament", "Surface", "Best of", "Round"]
        meta = meta[[c for c in keep if c in meta.columns]]
        stats = stats.merge(meta, on="match_id", how="left")
        stats["date"] = pd.to_datetime(
            pd.to_numeric(stats.get("Date"), errors="coerce"), format="%Y%m%d", errors="coerce"
        )

    stats["tour"] = tour.upper()
    return _add_charting_rates(stats)


def _add_charting_rates(stats: pd.DataFrame) -> pd.DataFrame:
    """Attach per-point rates.  Counts scale with match length; rates do not."""
    serve_points = pd.to_numeric(stats.get("serve_pts"), errors="coerce").replace(0, pd.NA)
    return_points = pd.to_numeric(stats.get("return_pts"), errors="coerce").replace(0, pd.NA)
    total_points = serve_points.add(return_points, fill_value=0)

    for column in ("winners", "unforced", "aces", "dfs"):
        stats[column] = pd.to_numeric(stats.get(column), errors="coerce")

    # Winners and errors can happen on any point, so the denominator is all
    # points the player was involved in, not just their service points.
    stats["winner_rate"] = stats["winners"] / total_points
    stats["unforced_rate"] = stats["unforced"] / total_points
    stats["ace_rate_mcp"] = stats["aces"] / serve_points
    stats["df_rate_mcp"] = stats["dfs"] / serve_points
    stats["total_points"] = total_points
    return stats


def summarise_charting(
    stats: pd.DataFrame, *, surface: str | None = "Hard", min_matches: int = 1
) -> pd.DataFrame:
    """Aggregate charted matches into per-player winner and error rates."""
    if stats.empty:
        return pd.DataFrame()

    frame = stats
    if surface and "Surface" in frame.columns:
        frame = frame[frame["Surface"].astype(str).str.lower() == surface.lower()]
    frame = frame[frame["total_points"].notna() & (frame["total_points"] > 0)]
    if frame.empty:
        return pd.DataFrame()

    grouped = frame.groupby("player")
    summary = grouped.agg(
        tour=("tour", "first"),
        charted_matches=("player", "size"),
        total_points=("total_points", "sum"),
        winners=("winners", "sum"),
        unforced=("unforced", "sum"),
    ).reset_index()

    summary["winner_rate"] = summary["winners"] / summary["total_points"]
    summary["unforced_rate"] = summary["unforced"] / summary["total_points"]
    summary["winner_per_match"] = summary["winners"] / summary["charted_matches"]
    summary["unforced_per_match"] = summary["unforced"] / summary["charted_matches"]

    summary = summary[summary["charted_matches"] >= min_matches]
    return summary.sort_values("charted_matches", ascending=False, ignore_index=True)
