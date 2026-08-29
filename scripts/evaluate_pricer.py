"""Monte Carlo evaluation of the pricer against known ground truth.

Run with::

    PYTHONPATH=. .venv/bin/python scripts/evaluate_pricer.py

The synthetic tournament is drawn from the same generative model the simulator
assumes, so "truth" is knowable exactly.  That makes this a test of the
machinery rather than of luck, which a 64-match backtest cannot be.

The most useful section is the last one.  Real risk is not simulator noise, it
is a mis-specified input, so compressed ratings and a shifted baseline are
injected deliberately to measure how much damage each does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data.baserates import tiebreak_count_base  # noqa: E402
from bot.evaluate import (  # noqa: E402
    bootstrap_skill,
    draw_outcomes,
    reliability_table,
    render_reliability,
    score,
)
from bot.models.simulate import SimulationConfig, simulate_match  # noqa: E402

# 20,000 truth simulations puts the standard error on a probability near
# 0.0035, comfortably below the model errors we are trying to detect, while
# keeping the whole sweep to a couple of minutes. The simulator's inner loop
# is Python-level, so this is the binding constraint.
TRUTH_SIMS = 20_000
MODEL_SIMS = 4_000


def synthetic_field(n_matches: int, tour: str, rng: np.random.Generator):
    """Draw plausible matchups: serve strength varies, and so does the gap."""
    base = 0.6321 if tour == "ATP" else 0.5691
    best_of = 5 if tour == "ATP" else 3
    for _ in range(n_matches):
        # Serve skill spread measured from the ratings: sd about 0.028.
        pa = float(np.clip(rng.normal(base, 0.030), 0.45, 0.80))
        pb = float(np.clip(rng.normal(base, 0.030), 0.45, 0.80))
        yield pa, pb, best_of


def truth_and_model(pa, pb, best_of, *, model_pa=None, model_pb=None):
    """Exact probabilities from a huge simulation, plus what the pricer sees."""
    truth = simulate_match(
        pa, pb, best_of=best_of, config=SimulationConfig(n_sims=TRUTH_SIMS, seed=12345)
    )
    model = simulate_match(
        model_pa if model_pa is not None else pa,
        model_pb if model_pb is not None else pb,
        best_of=best_of,
        config=SimulationConfig(n_sims=MODEL_SIMS, seed=7),
    )
    return truth, model


def sweep(tour, n_matches, rng, *, distort=None):
    """Simulate each match ONCE and read every family off it.

    The first version of this simulated the same match once per family, which
    was three times the work and, worse, broke the property the pricer relies
    on: families read off one simulation stay mutually coherent, families read
    off separate ones do not.
    """
    truth_rows, model_rows = [], []
    for pa, pb, best_of in synthetic_field(n_matches, tour, rng):
        mpa, mpb = distort(pa, pb) if distort else (pa, pb)
        truth, model = truth_and_model(pa, pb, best_of, model_pa=mpa, model_pb=mpb)
        truth_rows.append(truth)
        model_rows.append(model)
    return truth_rows, model_rows


def report_family(label, truth_rows, model_rows, extract, rng):
    truth_p = np.array([extract(m) for m in truth_rows])
    model_p = np.array([extract(m) for m in model_rows])
    outcomes = draw_outcomes(truth_p, rng)
    card = score(model_p, outcomes)
    _, low, high = bootstrap_skill(model_p, outcomes, n_boot=800, seed=1)
    print(f"  {label:16s} {card}")
    print(f"  {'':16s} skill 95% CI [{100 * low:+.1f}%, {100 * high:+.1f}%]")
    return model_p, outcomes, truth_p


EXTRACTORS = {
    "match winner": lambda m: m.p_a_wins_match,
    "tiebreak >=1": lambda m: m.p_any_tiebreak,
    "tiebreak >=2": lambda m: m.p_two_plus_tiebreaks,
}


def main() -> int:
    rng = np.random.default_rng(20260829)

    print("=" * 78)
    print("1. Does the pricer recover known truth?  (synthetic, truth known exactly)")
    print("=" * 78)
    pooled_model, pooled_outcome, pooled_truth = [], [], []
    for tour in ("ATP", "WTA"):
        print(f"\n{tour}")
        truth_rows, model_rows = sweep(tour, 120, rng)
        for label, extract in EXTRACTORS.items():
            m, o, t = report_family(label, truth_rows, model_rows, extract, rng)
            pooled_model.append(m)
            pooled_outcome.append(o)
            pooled_truth.append(t)

    model = np.concatenate(pooled_model)
    outcome = np.concatenate(pooled_outcome)
    truth = np.concatenate(pooled_truth)

    print("\n" + "=" * 78)
    print("2. Calibration - of things priced near X, did about X% happen?")
    print("=" * 78)
    print(render_reliability(reliability_table(model, outcome)))

    print("\n" + "=" * 78)
    print("3. How close is the model to exact truth, ignoring outcome noise?")
    print("=" * 78)
    error = model - truth
    print(f"  mean signed error {error.mean():+.4f}   mean |error| {np.abs(error).mean():.4f}")
    print(f"  95th percentile |error| {np.percentile(np.abs(error), 95):.4f}")
    print("  A small signed error means no systematic bias; the absolute error is")
    print("  the price of 4,000 simulations instead of 20,000.")

    print("\n" + "=" * 78)
    print("4. What happens when the INPUTS are wrong?  (the real risk)")
    print("=" * 78)
    distortions = {
        "ratings compressed 30%": lambda pa, pb: (
            0.6321 + (pa - 0.6321) * 0.7,
            0.6321 + (pb - 0.6321) * 0.7,
        ),
        "baseline shifted +0.02": lambda pa, pb: (pa + 0.02, pb + 0.02),
        "one player mis-rated +0.04": lambda pa, pb: (pa + 0.04, pb),
    }
    for label, distort in distortions.items():
        print(f"\n{label}")
        truth_rows, model_rows = sweep("ATP", 90, rng, distort=distort)
        for name in ("match winner", "tiebreak >=1"):
            report_family(name, truth_rows, model_rows, EXTRACTORS[name], rng)

    print("\n" + "=" * 78)
    print("5. Sanity: a flat base rate must not beat the model on its own ground")
    print("=" * 78)
    for tour in ("ATP", "WTA"):
        truth_rows, _ = sweep(tour, 120, rng)
        truths = np.array([m.p_any_tiebreak for m in truth_rows])
        outs = draw_outcomes(truths, rng)
        flat = np.full(truths.size, tiebreak_count_base(1, tour))
        print(f"  {tour} flat base rate: {score(flat, outs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
