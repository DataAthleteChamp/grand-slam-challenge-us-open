"""Download and cache the raw match data.

Primary source is the **TennisMyLife live service**, which publishes the full
49-column Sackmann-format schema — serve statistics, match duration, player
height, rankings — and is updated daily.  Verified 2026-08-26: ATP data runs to
2026-08-24 (through Cincinnati), WTA to 2026-08-13, and there is a Challenger
file too, which matters because US Open qualifiers play Challengers and are
otherwise invisible in tour-level data.

A trap worth recording: the project's **GitHub mirror is frozen at 2026-01-17**
while the **website is current**.  Reading the GitHub copy and concluding the
data was stale cost real time here; always use ``stats.tennismylife.org``.

Fallback mirrors carry the same schema but stop at 2026-05-25, when the original
Sackmann repositories were deleted.  They are kept only as a backstop.

Raw files are cached under ``data/raw/`` and **gitignored**: the underlying
dataset is not ours to redistribute, and only derived aggregates are committed.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

#: Live, daily-updated source.  ``{stem}`` is a year plus an optional suffix,
#: e.g. ``2026``, ``2026_wta``, ``2026_challenger``.
TML_DATA_URL = "https://stats.tennismylife.org/data/{stem}.csv"

#: Index of every file the service publishes, with sizes and modification times.
TML_INDEX_URL = "https://stats.tennismylife.org/api/data-files"

#: Frozen mirrors of the deleted Sackmann repositories (to 2026-05-25).
STATS_MIRRORS = (
    "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/{tour}/{tour}_matches_{year}.csv",
    "https://raw.githubusercontent.com/halekevi/PropORACLE/main/Sports/Tennis/data/sackmann/{tour}_matches_{year}.csv",
    "https://raw.githubusercontent.com/Stattrackrr/stattrackr/master/data/tennis/{tour}_matches_{year}.csv",
)

#: Player reference files: height, handedness, date of birth, nationality.
PLAYERS_MIRRORS = (
    "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/{tour}/{tour}_players.csv",
)

#: Results-only mirror (Git LFS) used as a cross-check on scorelines.
RECENT_MIRROR = (
    "https://media.githubusercontent.com/media/LuckyLoser91/TennisCourtLog/main/"
    "tennis_{tour}/{tour}_matches_{year}.csv"
)

USER_AGENT = "grand-slam-challenge-bot/0.1 (data ingest)"


def _tml_stem(tour: str, year: int, *, level: str = "tour") -> str | None:
    """File stem used by the live service, or ``None`` if the file does not exist.

    The service publishes ``YYYY.csv`` (ATP tour), ``YYYY_wta.csv``,
    ``YYYY_challenger.csv`` and ``atp_quali/YYYY_atp_quali.csv``.  Both the
    Challenger and qualifying files are **men only** — there is no WTA
    equivalent.  Requesting one for the WTA silently returns men's matches and
    contaminates the ratings, which is caught here rather than downstream.
    """
    is_atp = tour.lower() == "atp"
    if level == "tour":
        return f"{year}" if is_atp else f"{year}_wta"
    if level == "challenger":
        return f"{year}_challenger" if is_atp else None
    if level == "quali":
        return f"atp_quali/{year}_atp_quali" if is_atp else None
    raise ValueError(f"unknown level {level!r}")


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
    tour: str, year: int, *, refresh: bool = False, pause: float = 0.3, level: str = "tour"
) -> Path | None:
    """Download one year of full-schema matches at a given level.

    Tries the live service first, then the frozen mirrors.  The live service is
    strongly preferred: the mirrors stop at 2026-05-25 and so miss the entire
    grass season and US Open warm-up.
    """
    tour = tour.lower()
    stem = _tml_stem(tour, year, level=level)
    if stem is None:
        return None

    suffix = "" if level == "tour" else f"_{level}"
    destination = RAW_DIR / tour / f"{tour}_matches_{year}{suffix}.csv"
    if destination.exists() and not refresh:
        return destination

    if _fetch(TML_DATA_URL.format(stem=stem), destination):
        return destination
    if level != "tour":
        return None

    for template in STATS_MIRRORS:
        if _fetch(template.format(tour=tour, year=year), destination):
            return destination
        time.sleep(pause)
    return None


def data_index() -> pd.DataFrame:
    """List every file the live service publishes, with sizes and update times.

    Use this to check freshness before a run: ``mtime`` tells you how current the
    season file is, which matters during the tournament when it updates daily.
    """
    request = urllib.request.Request(TML_INDEX_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = pd.read_json(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return pd.DataFrame()
    if "files" not in payload.columns:
        return pd.DataFrame()
    return pd.DataFrame(list(payload["files"]))


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


def load_matches(
    tour: str, years: list[int], *, refresh: bool = False, levels: tuple[str, ...] = ("tour",)
) -> pd.DataFrame:
    """Fetch and concatenate several years of full-schema matches.

    Years that cannot be downloaded are skipped rather than raising, so a partial
    outage degrades the sample instead of breaking the pipeline.

    ``levels`` may include ``"tour"``, ``"challenger"`` and ``"quali"``.  The
    latter two exist for the men only; requesting them for the WTA returns
    nothing rather than silently loading men's matches.  They matter because US
    Open qualifiers play at those levels and are otherwise invisible, leaving
    their rates shrunk entirely to the population prior.
    """
    frames = []
    for year in years:
        for level in levels:
            path = fetch_matches(tour, year, refresh=refresh, level=level)
            if path is None:
                continue
            frame = pd.read_csv(path, low_memory=False)
            frame["tour"] = tour.upper()
            frame["level"] = level
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


#: Minimum rows in a (year, surface) block before it is worth checking.
_QUALITY_MIN_ROWS = 150

#: A block fails if winners do not double-fault less and ace more than losers.
#:
#: Both hold overwhelmingly in every clean block of this dataset - winners are
#: ahead on 0.55-0.65 of ace comparisons and behind on only 0.30-0.40 of
#: double-fault comparisons - because serving well is most of how matches are
#: won.  A block where the relationship vanishes or reverses is not describing
#: tennis.
_QUALITY_MAX_DF_SHARE = 0.45
_QUALITY_MIN_ACE_SHARE = 0.50


def serve_stats_look_sane(frame: pd.DataFrame) -> bool:
    """Whether one block's winner/loser serve statistics are self-consistent."""
    needed = {"w_df", "l_df", "w_ace", "l_ace"}
    if not needed.issubset(frame.columns):
        return True
    rows = frame.dropna(subset=list(needed))
    if len(rows) < _QUALITY_MIN_ROWS:
        return True
    df_share = float((rows.w_df > rows.l_df).mean())
    ace_share = float((rows.w_ace > rows.l_ace).mean())
    return df_share <= _QUALITY_MAX_DF_SHARE and ace_share >= _QUALITY_MIN_ACE_SHARE


def drop_broken_serve_blocks(matches: pd.DataFrame, *, label: str = "") -> pd.DataFrame:
    """Remove (year, surface) blocks whose serve statistics are not credible.

    Some source files carry winner/loser serve statistics that are inverted or
    apparently randomised.  In this dataset the whole of the 2025 women's season
    is affected on every surface, and the 2026 women's hard-court block sits at
    exactly 0.500 on both checks, which is what randomly assigned numbers look
    like.  The men's files are clean throughout.

    This matters more than a data-hygiene note suggests.  Ratings fitted through
    such a block are not merely noisier, they are pulled toward the *opposite* of
    the truth, and a model validated against one looks worthless when it is
    fine: the women's double-fault confidence had been pinned near zero on
    exactly that evidence, which cost roughly 4 relative points per question on
    the worst-performing family in the entry.

    Blocks are judged rather than listed by year so that a future file going bad
    is caught automatically instead of silently retraining the model.
    """
    if matches.empty or "surface" not in matches.columns:
        return matches
    frame = matches.copy()
    year = pd.to_datetime(
        frame["tourney_date"].astype(str), format="%Y%m%d", errors="coerce"
    ).dt.year
    keep = pd.Series(True, index=frame.index)
    for (block_year, surface), rows in frame.groupby([year, frame["surface"].astype(str)]):
        if not serve_stats_look_sane(rows):
            keep.loc[rows.index] = False
            where = f"{label} " if label else ""
            print(
                f"  ! dropping {where}{int(block_year)} {surface}: "
                f"{len(rows):,} rows with implausible serve statistics"
            )
    return frame[keep]
