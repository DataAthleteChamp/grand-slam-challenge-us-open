"""Download and cache the raw match data.

Sources are mirrors of the Sackmann schema — the original repositories were taken
down in mid-2026, but the archive mirrors carry the full 49-column format with
serve statistics, match duration, player height and rankings.

Raw files are cached under ``data/raw/`` and **gitignored**.  That is deliberate:
the underlying dataset is CC BY-NC-SA and is not ours to redistribute.  Only
derived aggregates — our own analysis — are committed to the repository.

Known coverage limits (verified 2026-08-26):

* Full-schema files stop at **2026-05-25**, when the source repositories went
  down.  Ace and first-serve rates are the most repeatable statistics in tennis
  (year-over-year r of roughly 0.80-0.90), so a window ending in May is
  acceptable for rate estimation.
* Results-only data runs to 2026-08-09 and is used for recency and fatigue
  features, where the score line alone is enough.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

#: Full 49-column mirrors, in fallback order.  Each entry is a URL template
#: taking ``tour`` (``atp``/``wta``) and ``year``.
STATS_MIRRORS = (
    "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/{tour}/{tour}_matches_{year}.csv",
    "https://raw.githubusercontent.com/halekevi/PropORACLE/main/Sports/Tennis/data/sackmann/{tour}_matches_{year}.csv",
    "https://raw.githubusercontent.com/Stattrackrr/stattrackr/master/data/tennis/{tour}_matches_{year}.csv",
)

#: Player reference files: height, handedness, date of birth, nationality.
PLAYERS_MIRRORS = (
    "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/{tour}/{tour}_players.csv",
)

#: Results-only mirror (Git LFS) that runs closer to the tournament.
RECENT_MIRROR = (
    "https://media.githubusercontent.com/media/LuckyLoser91/TennisCourtLog/main/"
    "tennis_{tour}/{tour}_matches_{year}.csv"
)

USER_AGENT = "grand-slam-challenge-bot/0.1 (data ingest)"


def _fetch(url: str, destination: Path, *, timeout: float = 60.0) -> bool:
    """Download ``url`` to ``destination``.  Returns False on any failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False
    # A Git LFS pointer is a ~130-byte text stub, not the file we asked for.
    if len(payload) < 500 or payload.lstrip().startswith(b"version https://git-lfs"):
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return True


def fetch_matches(
    tour: str, year: int, *, refresh: bool = False, pause: float = 0.3
) -> Path | None:
    """Download one year of full-schema matches, trying each mirror in turn."""
    tour = tour.lower()
    destination = RAW_DIR / tour / f"{tour}_matches_{year}.csv"
    if destination.exists() and not refresh:
        return destination
    for template in STATS_MIRRORS:
        if _fetch(template.format(tour=tour, year=year), destination):
            return destination
        time.sleep(pause)
    return None


def fetch_recent_results(tour: str, year: int, *, refresh: bool = False) -> Path | None:
    """Download results-only data, which runs later than the full-schema files."""
    tour = tour.lower()
    destination = RAW_DIR / tour / f"{tour}_results_{year}.csv"
    if destination.exists() and not refresh:
        return destination
    if _fetch(RECENT_MIRROR.format(tour=tour, year=year), destination):
        return destination
    return None


def fetch_players(tour: str, *, refresh: bool = False) -> Path | None:
    """Download the player reference file (height, hand, date of birth)."""
    tour = tour.lower()
    destination = RAW_DIR / tour / f"{tour}_players.csv"
    if destination.exists() and not refresh:
        return destination
    for template in PLAYERS_MIRRORS:
        if _fetch(template.format(tour=tour), destination):
            return destination
    return None


def load_matches(tour: str, years: list[int], *, refresh: bool = False) -> pd.DataFrame:
    """Fetch and concatenate several years of full-schema matches.

    Years that cannot be downloaded are skipped rather than raising, so a partial
    outage degrades the sample instead of breaking the pipeline.
    """
    frames = []
    for year in years:
        path = fetch_matches(tour, year, refresh=refresh)
        if path is None:
            continue
        frame = pd.read_csv(path, low_memory=False)
        frame["tour"] = tour.upper()
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_players(tour: str, *, refresh: bool = False) -> pd.DataFrame:
    """Load the player reference table with a convenient ``full_name`` column."""
    path = fetch_players(tour, refresh=refresh)
    if path is None:
        return pd.DataFrame()
    players = pd.read_csv(path, low_memory=False)
    first = players.get("name_first", pd.Series(dtype=str)).fillna("")
    last = players.get("name_last", pd.Series(dtype=str)).fillna("")
    players["full_name"] = (first.astype(str) + " " + last.astype(str)).str.strip()
    return players
