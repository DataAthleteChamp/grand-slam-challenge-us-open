"""Fit tour-specific US Open points-per-game constants with a final-year holdout.

The input is the ignored local Sackmann-format point archive under
``data/raw/pointbypoint``. The fitting window is 2019 and 2021-2023; 2024 is
reported only as an out-of-sample stability check.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "pointbypoint"
FIT_YEARS = (2019, 2021, 2022, 2023)
HOLDOUT_YEAR = 2024
TOUR_CODE = {"1": "ATP", "2": "WTA"}


def load_matches(directory: Path = RAW) -> pd.DataFrame:
    frames = []
    for year in (*FIT_YEARS, HOLDOUT_YEAR):
        path = directory / f"{year}-usopen-points.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} is missing; fetch the private point archive before fitting"
            )
        points = pd.read_csv(path, low_memory=False)
        point_count = points.groupby("match_id").size().rename("points")
        game_count = (
            points[["match_id", "SetNo", "GameNo"]]
            .drop_duplicates()
            .groupby("match_id")
            .size()
            .rename("games")
        )
        matches = pd.concat([point_count, game_count], axis=1).reset_index()
        matches["year"] = year
        matches["tour"] = (
            matches["match_id"].str.extract(r"usopen-(\d)", expand=False).map(TOUR_CODE)
        )
        frames.append(matches)
    combined = pd.concat(frames, ignore_index=True).dropna(subset=["tour"])
    return combined[(combined["games"] > 0) & (combined["points"] >= 4 * combined["games"])]


def aggregate_rate(matches: pd.DataFrame) -> float:
    return float(matches["points"].sum() / matches["games"].sum())


def bootstrap_interval(
    matches: pd.DataFrame,
    *,
    iterations: int,
    seed: int,
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    points = matches["points"].to_numpy()
    games = matches["games"].to_numpy()
    estimates = np.empty(iterations)
    for index in range(iterations):
        sample = rng.integers(0, len(matches), len(matches))
        estimates[index] = points[sample].sum() / games[sample].sum()
    low, high = np.quantile(estimates, [0.025, 0.975])
    return float(low), float(high)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstraps", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260916)
    args = parser.parse_args(argv)

    matches = load_matches()
    print(
        f"fit years={','.join(map(str, FIT_YEARS))}; "
        f"holdout={HOLDOUT_YEAR}; bootstrap={args.bootstraps}; seed={args.seed}"
    )
    for offset, tour in enumerate(("ATP", "WTA")):
        fit = matches[(matches["tour"] == tour) & (matches["year"].isin(FIT_YEARS))]
        holdout = matches[(matches["tour"] == tour) & (matches["year"] == HOLDOUT_YEAR)]
        estimate = aggregate_rate(fit)
        low, high = bootstrap_interval(
            fit,
            iterations=args.bootstraps,
            seed=args.seed + offset,
        )
        observed = aggregate_rate(holdout)
        print(
            f"{tour}: fit_n={len(fit)} estimate={estimate:.6f} "
            f"95%_ci=[{low:.6f},{high:.6f}] "
            f"holdout_n={len(holdout)} holdout={observed:.6f} "
            f"delta={observed - estimate:+.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
