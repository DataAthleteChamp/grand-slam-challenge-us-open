"""EXPLORATORY ONLY — superseded by ``scripts/backtest_simulator.py``.

Fit the simulator's form-variation parameters to measured base rates.

.. warning::

   Do not fit shipped parameters here. This searches against four stylised
   scenarios, and an "evenly matched" bucket is a *mixture* of matchups with
   different skill gaps, so no single gap reproduces it and any parameter
   fitted this way absorbs the mismatch. Kept because sweeping a scenario is
   a fast way to see which direction a parameter moves the output; the
   numbers that ship come from the real-match backtest.

The constant-``p`` model produces the wrong shape: too few lopsided sets, too
many deciding sets.  Rather than tune the outputs, this fits the two parameters
that govern within-match form variation — ``sigma`` (how much a player's level
moves between sets) and ``rho`` (whether the two players move together or in
opposition) — by grid search against the empirical base rates.

Run with::

    python scripts/calibrate_simulator.py            # coarse grid, fast
    python scripts/calibrate_simulator.py --fine     # finer grid, slower

The loss is the sum of squared errors across the four structural questions,
weighted equally.  Lopsided sets and deciding sets are the two the base model
gets wrong, so they dominate the fit by construction rather than by weighting.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.data.baserates import structural  # noqa: E402
from bot.models.simulate import SimulationConfig, simulate_match  # noqa: E402

#: Scenarios to fit against, each with the serve probabilities that produce it.
#: The mismatch case uses a spread wide enough to reproduce the observed
#: straight-sets rate for a top-eight player against someone outside the top 100.
SCENARIOS = {
    ("ATP", "even"): {"pa": 0.645, "pb": 0.645, "best_of": 5},
    ("ATP", "mismatch"): {"pa": 0.700, "pb": 0.590, "best_of": 5},
    ("WTA", "even"): {"pa": 0.585, "pb": 0.585, "best_of": 3},
    ("WTA", "mismatch"): {"pa": 0.640, "pb": 0.530, "best_of": 3},
}

TARGET_KEYS = ("tiebreak", "deciding_set", "lopsided_set", "first_set_10plus")
SIM_KEYS = (
    "p_any_tiebreak",
    "p_deciding_set",
    "p_any_lopsided_set",
    "p_first_set_10plus_games",
)


def scenario_loss(sigma: float, rho: float, n_sims: int, seed: int) -> tuple[float, dict]:
    """Total squared error across every scenario, plus the per-scenario detail."""
    config = SimulationConfig(sigma=sigma, rho=rho, n_sims=n_sims, seed=seed)
    total = 0.0
    detail = {}

    for (tour, matchup), params in SCENARIOS.items():
        measured = structural(tour, matchup)
        result = simulate_match(
            params["pa"], params["pb"], best_of=params["best_of"], config=config
        )
        simulated = result.as_dict()

        errors = {}
        for target_key, sim_key in zip(TARGET_KEYS, SIM_KEYS, strict=True):
            observed = getattr(measured, target_key)
            predicted = simulated[sim_key]
            errors[target_key] = predicted - observed
            total += (predicted - observed) ** 2
        detail[f"{tour}-{matchup}"] = errors

    return total, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fine", action="store_true", help="search a finer grid")
    parser.add_argument("--n-sims", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    if args.fine:
        sigmas = np.arange(0.00, 0.085, 0.005)
        rhos = np.arange(-0.9, 0.31, 0.15)
    else:
        sigmas = np.arange(0.00, 0.09, 0.01)
        rhos = np.arange(-0.9, 0.31, 0.30)

    print(
        f"Grid search over {len(sigmas)} sigma x {len(rhos)} rho "
        f"= {len(sigmas) * len(rhos)} fits, {args.n_sims} sims each\n"
    )

    results = []
    for sigma in sigmas:
        for rho in rhos:
            loss, _ = scenario_loss(float(sigma), float(rho), args.n_sims, args.seed)
            results.append((loss, float(sigma), float(rho)))

    results.sort()
    print("Best five parameter pairs:")
    print(f"  {'sigma':>7} {'rho':>7} {'loss':>10}")
    for loss, sigma, rho in results[:5]:
        print(f"  {sigma:7.3f} {rho:7.2f} {loss:10.5f}")

    baseline = next(loss for loss, sigma, _ in results if sigma == 0.0)
    best_loss, best_sigma, best_rho = results[0]
    print(f"\nConstant-p baseline loss: {baseline:.5f}")
    print(f"Best fitted loss:         {best_loss:.5f}  ({1 - best_loss / baseline:.1%} better)")

    print(f"\nPer-scenario error at sigma={best_sigma:.3f}, rho={best_rho:.2f}")
    print("(positive means the simulation is above the measured rate)\n")
    _, detail = scenario_loss(best_sigma, best_rho, args.n_sims * 3, args.seed + 1)
    header = f"  {'scenario':<16}" + "".join(f"{k:>18}" for k in TARGET_KEYS)
    print(header)
    for scenario, errors in detail.items():
        row = f"  {scenario:<16}"
        for key in TARGET_KEYS:
            row += f"{errors[key]:>+18.3f}"
        print(row)

    print(
        "\nA negative rho means one player's strong set tends to be the other's "
        "weak one,\nwhich is what generates lopsided sets between closely matched "
        "players."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
