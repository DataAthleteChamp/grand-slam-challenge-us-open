"""US Open point-by-point ingestion.

Official tournament point-level data for every Grand Slam 2011-2024, mirrored
from the ``tennis_slam_pointbypoint`` repository that went down in 2026.

This is the most valuable single source in the project, for three reasons.

**It is the settlement basis.**  Winners and unforced errors here come from the
tournament's own scorers — the same feed the competition resolves questions
against — rather than from volunteer charting.  Rates computed here are measured
on the basis that will actually decide the market.

**It carries timing.**  ``ElapsedTime`` gives the clock at every point, so pace of
play can be measured directly instead of inferred from a duration residual.

**It carries serve detail.**  Speed in km/h, plus width and depth of every serve,
which nothing else free provides.

A measured result worth knowing before building anything on it: seconds per point
barely varies with rally length (42.3s for a one-shot point against 44.4s for a
rally of ten or more, at the 2024 US Open).  Time *between* points dominates, so
match duration is driven by how many points are played rather than how long the
rallies are.  That is why the duration model keys off games rather than rally
length.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .ingest import RAW_DIR, _fetch

BASE_URL = (
    "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/slam_pointbypoint"
)

#: Years available for the US Open.  2025 onward is not in the mirror.
US_OPEN_YEARS = tuple(range(2011, 2025))

#: Ignore gaps longer than this between points; they are rain delays, medical
#: timeouts and set breaks rather than pace of play.
MAX_POINT_GAP_SECONDS = 300


def fetch_slam(
    year: int, slam: str = "usopen", kind: str = "points", *, refresh: bool = False
) -> Path | None:
    """Download one slam-year file (``points`` or ``matches``)."""
    name = f"{year}-{slam}-{kind}.csv"
    destination = RAW_DIR / "pointbypoint" / name
    if destination.exists() and not refresh:
        return destination
    return destination if _fetch(f"{BASE_URL}/{name}", destination, timeout=120.0) else None


def load_points(
    years: list[int] | None = None, slam: str = "usopen", *, refresh: bool = False
) -> pd.DataFrame:
    """Load point-by-point data for several years, with timing attached."""
    frames = []
    for year in years or list(US_OPEN_YEARS):
        path = fetch_slam(year, slam, "points", refresh=refresh)
        if path is None:
            continue
        frame = pd.read_csv(path, low_memory=False)
        frame["year"] = year
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return _add_timing(pd.concat(frames, ignore_index=True))


def _parse_elapsed(values: pd.Series) -> pd.Series:
    """Parse ``H:MM:SS`` clock strings into seconds, tolerating bad rows.

    Earlier years contain malformed entries that overflow pandas' timedelta
    parser, so split the string manually rather than trusting ``to_timedelta``.
    """
    text = values.astype(str).str.strip()
    parts = text.str.extract(r"^(\d{1,3}):(\d{1,2}):(\d{1,2})(?:\.\d+)?$")
    numeric = parts.apply(pd.to_numeric, errors="coerce")
    return numeric[0] * 3600 + numeric[1] * 60 + numeric[2]


def _add_timing(points: pd.DataFrame) -> pd.DataFrame:
    """Attach seconds elapsed and the gap since the previous point."""
    points["elapsed_seconds"] = _parse_elapsed(points.get("ElapsedTime", pd.Series(dtype=str)))
    points = points.sort_values(["match_id", "PointNumber"])
    gap = points.groupby("match_id")["elapsed_seconds"].diff()
    points["point_seconds"] = gap.where((gap > 0) & (gap < MAX_POINT_GAP_SECONDS))
    return points


def match_pace(points: pd.DataFrame) -> pd.DataFrame:
    """Seconds per point and rally length per match.

    Because time between points dominates, this is close to a direct measure of
    how quickly the two players get on with it.
    """
    if points.empty:
        return pd.DataFrame()
    return (
        points.groupby("match_id")
        .agg(
            year=("year", "first"),
            points=("PointNumber", "size"),
            seconds_per_point=("point_seconds", "mean"),
            median_seconds=("point_seconds", "median"),
            rally_mean=("RallyCount", "mean"),
        )
        .reset_index()
    )


def official_rates(points: pd.DataFrame) -> pd.DataFrame:
    """Per-player rates measured on the tournament's own scoring basis.

    Player identity lives in the accompanying ``-matches`` file, so this returns
    rates keyed by ``(match_id, player_slot)`` where the slot is 1 or 2; join to
    the match file to attach names.
    """
    if points.empty:
        return pd.DataFrame()

    rows = []
    for slot in (1, 2):
        served = points[points["PointServer"] == slot]
        grouped = points.groupby("match_id")
        serve_grouped = served.groupby("match_id")
        rows.append(
            pd.DataFrame(
                {
                    "match_id": grouped.size().index,
                    "player_slot": slot,
                    "points": grouped.size().to_numpy(),
                    "serve_points": serve_grouped.size()
                    .reindex(grouped.size().index)
                    .fillna(0)
                    .to_numpy(),
                    "aces": grouped[f"P{slot}Ace"].sum().to_numpy(),
                    "double_faults": grouped[f"P{slot}DoubleFault"].sum().to_numpy(),
                    "winners": grouped[f"P{slot}Winner"].sum().to_numpy(),
                    "unforced": grouped[f"P{slot}UnfErr"].sum().to_numpy(),
                }
            )
        )

    frame = pd.concat(rows, ignore_index=True)
    serve_points = frame["serve_points"].replace(0, np.nan)
    frame["ace_rate"] = frame["aces"] / serve_points
    frame["df_rate"] = frame["double_faults"] / serve_points
    frame["winner_rate"] = frame["winners"] / frame["points"]
    frame["unforced_rate"] = frame["unforced"] / frame["points"]
    return frame


def serve_speed(points: pd.DataFrame, *, first_serve_only: bool = True) -> pd.DataFrame:
    """Average and maximum serve speed per match and serving player.

    Nothing else free provides serve speed.  It is largely redundant with ace
    rate for prediction, but it separates genuinely fast servers from players who
    ace through placement, which is a different mechanism.
    """
    if points.empty or "Speed_KMH" not in points.columns:
        return pd.DataFrame()
    frame = points[points["Speed_KMH"] > 100]
    if first_serve_only and "ServeNumber" in frame.columns:
        frame = frame[frame["ServeNumber"] == 1]
    if frame.empty:
        return pd.DataFrame()
    return (
        frame.groupby(["match_id", "PointServer"])["Speed_KMH"]
        .agg(mean_kmh="mean", max_kmh="max", serves="size")
        .reset_index()
        .rename(columns={"PointServer": "player_slot"})
    )
