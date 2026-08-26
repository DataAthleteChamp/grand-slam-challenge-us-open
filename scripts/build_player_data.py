"""Build the player dataset: download in batches, fit ratings, write artefacts.

Usage::

    python scripts/build_player_data.py                 # both tours, 2021-2026
    python scripts/build_player_data.py --tour atp
    python scripts/build_player_data.py --years 2024 2025 2026 --refresh

Raw downloads are cached under ``data/raw/`` and are gitignored — the source
dataset is CC BY-NC-SA and is not ours to redistribute.  What lands in the
repository is ``data/players/``: our own derived aggregates, small enough to read
at a glance and to diff in review.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.data.ingest import load_matches, load_players  # noqa: E402
from bot.data.players import (  # noqa: E402
    build_player_matches,
    opponent_graph,
    summarise_players,
    with_recency,
)
from bot.models.ratings import fit_serve_return  # noqa: E402
from bot.models.shrink import CREDIBILITY_K, height_prior_ace_rate, shrink  # noqa: E402

OUT_DIR = ROOT / "data" / "players"
DEFAULT_YEARS = [2021, 2022, 2023, 2024, 2025, 2026]


def _fill_player_attributes(long: pd.DataFrame) -> pd.DataFrame:
    """Recover height and handedness from every row that mentions the player.

    A player whose own rows lack a height often has one recorded in the matches
    where they appear as the *opponent*, so pooling both sides fills a large part
    of the gap without touching any external source.
    """
    for attribute in ("ht", "hand"):
        own = long[["player", f"player_{attribute}"]].rename(
            columns={"player": "name", f"player_{attribute}": "value"}
        )
        other = long[["opponent", f"opponent_{attribute}"]].rename(
            columns={"opponent": "name", f"opponent_{attribute}": "value"}
        )
        pooled = pd.concat([own, other], ignore_index=True).dropna(subset=["name", "value"])
        if pooled.empty:
            continue
        # Bind the loop variable explicitly; a bare closure would capture the
        # final value and apply the wrong aggregator to the first attribute.
        aggregator = (
            (lambda s: s.median()) if attribute == "ht" else (lambda s: s.mode().iat[0])
        )
        lookup = pooled.groupby("name")["value"].agg(aggregator)
        long[f"player_{attribute}"] = long[f"player_{attribute}"].fillna(
            long["player"].map(lookup)
        )
        long[f"opponent_{attribute}"] = long[f"opponent_{attribute}"].fillna(
            long["opponent"].map(lookup)
        )
    return long


def build_tour(tour: str, years: list[int], *, refresh: bool, surface: str) -> dict[str, int]:
    """Build every artefact for one tour and return a small summary."""
    print(f"\n=== {tour.upper()} — downloading {len(years)} years ===")
    matches = load_matches(tour, years, refresh=refresh)
    if matches.empty:
        print(f"  no data available for {tour}")
        return {}
    print(f"  {len(matches):,} matches")

    long = with_recency(build_player_matches(matches))
    print(f"  {len(long):,} player-match rows")

    # Reference data: height and handedness matter far more than they look.
    # Height explains about 83% of the variance in men's ace rate, and it is
    # known for players we have no match statistics for at all.
    #
    # Coverage in the source is uneven (ATP match rows carry height 98% of the
    # time, WTA only 64%), so recover it from three places in order: the player's
    # own rows, the rows where they appear as somebody else's opponent, and
    # finally the standing player reference file.
    long = _fill_player_attributes(long)
    reference = load_players(tour, refresh=refresh)
    if not reference.empty:
        # The reference file spans decades and repeats some names, so collapse to
        # one row per name, preferring the entry that actually carries a height.
        reference = (
            reference.sort_values("height", na_position="last")
            .drop_duplicates(subset="full_name", keep="first")
            .set_index("full_name")
        )
        long["player_ht"] = long["player_ht"].fillna(long["player"].map(reference["height"]))
        long["player_hand"] = long["player_hand"].fillna(long["player"].map(reference["hand"]))

    surface_long = long[long["surface"] == surface]
    print(f"  {len(surface_long):,} rows on {surface}")

    print("  fitting opponent-adjusted serve/return ratings...")
    ratings = fit_serve_return(surface_long)
    print(
        f"    base spw={ratings.base:.4f}  iterations={ratings.iterations}  "
        f"residual rmse={ratings.residual_rmse:.4f}"
    )

    summary = summarise_players(long, surface=surface, min_matches=1)
    # ``matches`` exists on both sides; the summary count is the authoritative one.
    rating_frame = ratings.as_frame().drop(columns=["matches"])
    summary = summary.merge(rating_frame, on="player", how="left")

    # Opponent-adjusted expected service points won, and the shrunk ace rate that
    # the count models actually consume.
    summary["spw_adjusted"] = ratings.base + summary["serve_skill"].fillna(0.0)
    summary["ace_rate_prior"] = summary["height"].map(
        lambda h: height_prior_ace_rate(h, tour.upper())
    )
    summary["ace_rate_shrunk"] = [
        shrink(row.ace_rate, row.matches, row.ace_rate_prior, CREDIBILITY_K["ace_rate"]).estimate
        if pd.notna(row.ace_rate)
        else row.ace_rate_prior
        for row in summary.itertuples()
    ]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    columns = [
        "player", "tour", "matches", "svpt", "rank", "height", "hand",
        "ace_rate", "ace_rate_prior", "ace_rate_shrunk", "df_rate",
        "first_in_rate", "first_won_rate", "second_won_rate", "spw", "rpw",
        "serve_skill", "return_skill", "spw_adjusted", "win_rate", "last_played",
    ]
    summary = summary[[c for c in columns if c in summary.columns]].round(5)
    summary.to_csv(OUT_DIR / f"{tour}_players.csv", index=False)

    graph = opponent_graph(surface_long)
    graph.to_csv(OUT_DIR / f"{tour}_head_to_head.csv", index=False)

    print(f"  wrote {len(summary):,} players and {len(graph):,} head-to-head pairs")
    return {"matches": len(matches), "players": len(summary), "pairs": len(graph)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tour", choices=["atp", "wta", "both"], default="both")
    parser.add_argument("--years", type=int, nargs="+", default=DEFAULT_YEARS)
    parser.add_argument("--surface", default="Hard")
    parser.add_argument("--refresh", action="store_true", help="re-download cached files")
    args = parser.parse_args()

    tours = ["atp", "wta"] if args.tour == "both" else [args.tour]
    totals = {}
    for tour in tours:
        totals[tour] = build_tour(
            tour, args.years, refresh=args.refresh, surface=args.surface
        )

    print("\n=== summary ===")
    for tour, stats in totals.items():
        if stats:
            print(
                f"  {tour.upper()}: {stats['matches']:,} matches -> "
                f"{stats['players']:,} players, {stats['pairs']:,} pairs"
            )
    print(f"\nArtefacts in {OUT_DIR.relative_to(ROOT)}/ (raw cache is gitignored).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
