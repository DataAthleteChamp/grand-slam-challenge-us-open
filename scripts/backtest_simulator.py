"""Backtest the simulator against real matches with real player ratings.

Fitting form-variation parameters to a handful of hand-picked scenarios is the
wrong experiment: an "evenly matched" bucket is a *mixture* of matchups with
different skill gaps, so no single gap reproduces it and any parameter fitted
that way absorbs the mismatch.

This instead takes actual matches, derives each one's serve probabilities from
the opponent-adjusted ratings of the two players who really played, simulates it,
and compares predicted against observed rates in aggregate.  That is the same
quantity the competition scores, measured on the same distribution of matchups
the tournament will produce.

Run with::

    python scripts/backtest_simulator.py                    # default grid
    python scripts/backtest_simulator.py --tour wta --n 400

Reported per parameter pair:

* mean predicted rate against observed rate for each structural question
* Brier score of the per-match predictions
* the Murphy decomposition, which separates miscalibration from a genuine
  failure to discriminate
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.models.point import clip  # noqa: E402
from bot.models.scoring import brier_score, murphy_decomposition  # noqa: E402
from bot.models.simulate import SimulationConfig, simulate_match  # noqa: E402

_SET_TOKEN = re.compile(r"^(\d+)-(\d+)(?:\(\d+\))?$")

QUESTIONS = {
    "tiebreak": "p_any_tiebreak",
    "deciding_set": "p_deciding_set",
    "lopsided_set": "p_any_lopsided_set",
    "first_set_10plus": "p_first_set_10plus_games",
}


def parse_sets(score: object) -> list[tuple[int, int]] | None:
    """Parse a score string, accepting both ``7-6(6)`` and bare ``7-6``."""
    if not isinstance(score, str) or re.search(r"RET|W/O|DEF|ABN", score, re.I):
        return None
    sets = []
    for token in score.split():
        match = _SET_TOKEN.match(token)
        if not match:
            return None
        sets.append((int(match.group(1)), int(match.group(2))))
    return sets or None


def observed_outcomes(sets: list[tuple[int, int]], best_of: int) -> dict[str, int]:
    """Resolve the structural questions for one completed match."""
    return {
        "tiebreak": int(any(max(a, b) == 7 and min(a, b) == 6 for a, b in sets)),
        "deciding_set": int(len(sets) == best_of),
        "lopsided_set": int(any(min(a, b) <= 2 and max(a, b) == 6 for a, b in sets)),
        "first_set_10plus": int(sets[0][0] + sets[0][1] >= 10),
    }


def load_matches(tour: str, limit: int) -> pd.DataFrame:
    """Recent hard-court matches with both players present in the ratings table."""
    from bot.data.ingest import load_matches as load_raw

    raw = load_raw(tour, [2025, 2026], levels=("tour",))
    raw = raw[(raw["surface"] == "Hard") & raw["score"].notna()].copy()
    raw["sets"] = raw["score"].map(parse_sets)
    raw = raw.dropna(subset=["sets"])
    raw = raw[raw["best_of"].isin([3, 5])]
    return raw.tail(limit)


def match_probabilities(row, ratings: pd.DataFrame, base: float) -> tuple[float, float] | None:
    """Serve probabilities for the two players who actually played."""
    winner = ratings.get(row.winner_name)
    loser = ratings.get(row.loser_name)
    if winner is None or loser is None:
        return None
    pa = clip(base + winner["serve"] - loser["ret"])
    pb = clip(base + loser["serve"] - winner["ret"])
    return pa, pb


def run(tour: str, limit: int, sigmas, rhos, n_sims: int) -> None:
    players = pd.read_csv(ROOT / "data" / "players" / f"{tour}_players.csv")
    players = players.dropna(subset=["serve_skill", "return_skill"])
    ratings = {
        row.player: {"serve": row.serve_skill, "ret": row.return_skill}
        for row in players.itertuples()
    }
    base = 0.645 if tour == "atp" else 0.585

    matches = load_matches(tour, limit)
    usable = []
    for row in matches.itertuples():
        probabilities = match_probabilities(row, ratings, base)
        if probabilities is None:
            continue
        usable.append((row, probabilities))

    print(f"{tour.upper()}: {len(usable)} of {len(matches)} matches have ratings for both players")
    if not usable:
        return

    observed = pd.DataFrame([observed_outcomes(row.sets, int(row.best_of)) for row, _ in usable])
    print("\nObserved rates in this sample:")
    for question in QUESTIONS:
        print(f"  {question:20} {observed[question].mean():.3f}")

    print(f"\nGrid: {len(sigmas)} sigma x {len(rhos)} rho, {n_sims} sims per match\n")
    header = f"  {'sigma':>6} {'rho':>6} {'brier':>8} {'reliab':>8} {'resol':>8}  " + "".join(
        f"{q[:9]:>11}" for q in QUESTIONS
    )
    print(header)
    print(
        f"  {'':>6} {'':>6} {'':>8} {'':>8} {'':>8}  "
        + "".join(f"{'pred-obs':>11}" for _ in QUESTIONS)
    )

    results = []
    for sigma in sigmas:
        for rho in rhos:
            config = SimulationConfig(sigma=float(sigma), rho=float(rho), n_sims=n_sims, seed=11)
            predictions = {q: [] for q in QUESTIONS}
            for row, (pa, pb) in usable:
                result = simulate_match(pa, pb, int(row.best_of), config=config).as_dict()
                for question, key in QUESTIONS.items():
                    predictions[question].append(result[key])

            total_brier = 0.0
            rel = res = 0.0
            deltas = {}
            for question in QUESTIONS:
                p = np.array(predictions[question])
                y = observed[question].to_numpy(dtype=float)
                total_brier += brier_score(p, y)
                decomposition = murphy_decomposition(np.round(p, 2), y)
                rel += decomposition.reliability
                res += decomposition.resolution
                deltas[question] = p.mean() - y.mean()

            n = len(QUESTIONS)
            results.append((total_brier / n, float(sigma), float(rho), deltas))
            row_text = (
                f"  {sigma:6.3f} {rho:6.2f} {total_brier / n:8.4f} "
                f"{rel / n:8.4f} {res / n:8.4f}  "
                + "".join(f"{deltas[q]:>+11.3f}" for q in QUESTIONS)
            )
            print(row_text)

    results.sort()
    best_brier, best_sigma, best_rho, best_deltas = results[0]
    baseline = next(r for r in results if r[1] == 0.0)
    print(f"\nBest: sigma={best_sigma:.3f} rho={best_rho:.2f} brier={best_brier:.4f}")
    print(f"Constant-p baseline: brier={baseline[0]:.4f}")
    print(f"Improvement: {(baseline[0] - best_brier) / baseline[0]:+.2%}")
    print("\nMean prediction minus observed at the best fit:")
    for question, delta in best_deltas.items():
        print(f"  {question:20} {delta:+.3f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tour", choices=["atp", "wta"], default="atp")
    parser.add_argument("--n", type=int, default=250, help="matches to backtest")
    parser.add_argument("--n-sims", type=int, default=600)
    parser.add_argument("--fine", action="store_true")
    args = parser.parse_args()

    if args.fine:
        sigmas = np.arange(0.0, 0.075, 0.0125)
        rhos = np.array([-0.8, -0.5, -0.2, 0.0])
    else:
        sigmas = np.array([0.0, 0.025, 0.05])
        rhos = np.array([-0.6, 0.0])

    run(args.tour, args.n, sigmas, rhos, args.n_sims)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
