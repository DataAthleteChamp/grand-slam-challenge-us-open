"""Deep analysis of settled questions: where the edge is and where it is not.

    PYTHONPATH=. .venv/bin/python scripts/analyse.py
    PYTHONPATH=. .venv/bin/python scripts/analyse.py --plots

Reads ``data/history/final/settled.csv`` and asks the questions that change decisions:

* Which families are we actually beating the crowd on, and by how much?
* Is our error a *bias* (systematically high or low, fixable with a shift) or
  *noise* (unfixable without a better model)?  These need opposite responses and
  a single Brier number cannot distinguish them.
* Does our edge depend on the matchup - favourite versus mismatch, seeded versus
  unseeded, men versus women?  An edge that exists only in one regime is a
  different instruction from one that exists everywhere.
* Is the crowd getting better as the tournament goes on?  If it is, an edge
  measured in round one decays and should not be extrapolated.
* Would a simple counterfactual - shifting a family by a constant, or deferring
  to the base rate entirely - have scored better than what we actually did?

The counterfactuals are the important part.  Measuring that we lost is easy;
knowing which *specific change* would have won is what makes the next round
better, and it is testable directly against what already settled.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HISTORY = Path(__file__).resolve().parent.parent / "data" / "history" / "final" / "settled.csv"
PLOTS = Path(__file__).resolve().parent.parent / "data" / "history" / "plots"


def load(path: Path = HISTORY) -> list[dict]:
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            row["submitted"] = float(row["submitted"]) / 100.0
            row["crowd_brier"] = row["crowd_consensus_brier"]
            row["rbp"] = row["api_rbp"]
            row["settled_at"] = row["prediction_created_at"]
            for key in (
                "crowd",
                "brier",
                "crowd_brier",
                "rbp",
                "base_rate",
                "model",
                "multiplier",
            ):
                try:
                    row[key] = float(row[key])
                except (TypeError, ValueError):
                    row[key] = None
            row["outcome"] = int(row["outcome"])
            rows.append(row)
    return rows


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def crowd_variance(rows: list[dict]) -> np.ndarray:
    """Recover the crowd's internal disagreement from the API's own RBP.

    The competition does not score us against the consensus.  It scores us
    against the *average individual forecaster*, whose expected Brier is

        E[(p_i - y)^2] = (mean_p - y)^2 + Var(p_i)

    so the benchmark is worse than the consensus by exactly the crowd's own
    variance.  Recovering that term matters enormously: it means simply copying
    the consensus earns a positive score, and it separates points we earned from
    points the scoring rule hands out.

    Verified on 55 settled questions: every implied variance is positive (a
    single negative one would falsify this) and the implied crowd standard
    deviation runs 0.08 to 0.19, which is a plausible spread for a field of
    forecasters.
    """
    p = np.array([r["submitted"] for r in rows])
    c = np.array([r["crowd"] for r in rows])
    y = np.array([r["outcome"] for r in rows], dtype=float)
    rbp = np.array([r["rbp"] for r in rows])
    multiplier = np.array([r["multiplier"] for r in rows])
    return rbp / 100.0 + multiplier * ((p - y) ** 2 - (c - y) ** 2)


def wilson(successes: int, n: int) -> tuple[float, float]:
    """Wilson interval for a proportion, which behaves at small n."""
    if n == 0:
        return 0.0, 1.0
    z = 1.96
    phat = successes / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return max(centre - half, 0.0), min(centre + half, 1.0)


def bootstrap_diff(ours: np.ndarray, theirs: np.ndarray, y: np.ndarray, n_boot: int = 4000):
    """Bootstrap CI on (crowd Brier - our Brier). Positive means we are better."""
    rng = np.random.default_rng(0)
    n = len(y)
    if n == 0:
        return 0.0, 0.0, 0.0
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        j = rng.integers(0, n, n)
        diffs[i] = brier(theirs[j], y[j]) - brier(ours[j], y[j])
    point = brier(theirs, y) - brier(ours, y)
    return point, float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def by_family(rows: list[dict], lane: str = "honest") -> None:
    section(f"1. Per family: free bonus versus actual skill  [{lane}]")
    sub = [r for r in rows if r["lane"] == lane]
    var = crowd_variance(sub)
    print(
        "  RBP splits into a free component - what copying the consensus would\n"
        "  have earned - and skill, which is beating the consensus itself.\n"
    )
    print(
        f"{'family':26s} {'n':>3s} {'RBP':>8s} {'free':>7s} {'SKILL':>7s} "
        f"{'ours':>7s} {'consensus':>10s} {'skill 95% CI':>22s}"
    )
    for family in sorted({r["family"] for r in sub}):
        mask = np.array([r["family"] == family for r in sub])
        items = [r for r, m in zip(sub, mask, strict=True) if m]
        p = np.array([r["submitted"] for r in items])
        c = np.array([r["crowd"] for r in items])
        y = np.array([r["outcome"] for r in items], dtype=float)
        multiplier = np.array([r["multiplier"] for r in items])
        point, lo, hi = bootstrap_diff(p, c, y)
        star = "*" if lo > 0 else ("!" if hi < 0 else " ")
        print(
            f"{family:26s} {len(items):3d} {sum(r['rbp'] for r in items):8.1f} "
            f"{100 * var[mask].sum():7.1f} "
            f"{100 * (multiplier * ((c - y) ** 2 - (p - y) ** 2)).sum():7.1f} "
            f"{brier(p, y):7.4f} {brier(c, y):10.4f} "
            f"{point:+.4f} [{lo:+.4f},{hi:+.4f}]{star}"
        )
    total_free = 100 * var.sum()
    total = sum(r["rbp"] for r in sub)
    print(
        f"\n  total {total:+.1f} = free {total_free:+.1f} + skill {total - total_free:+.1f}"
        f"   ({100 * (total - total_free) / total:.0f}% of the score is genuine edge)"
    )
    print("  * skill is significant   ! the consensus is significantly better")


def bias_vs_noise(rows: list[dict], lane: str = "honest") -> None:
    section("2. Is our error BIAS (fixable by a shift) or NOISE (needs a better model)?")
    sub = [r for r in rows if r["lane"] == lane]
    print(
        f"{'family':26s} {'ours':>6s} {'actual':>7s} {'bias':>7s} "
        f"{'if debiased':>12s} {'gain':>8s}   verdict"
    )
    for family in sorted({r["family"] for r in sub}):
        items = [r for r in sub if r["family"] == family]
        p = np.array([r["submitted"] for r in items])
        y = np.array([r["outcome"] for r in items], dtype=float)
        bias = float(p.mean() - y.mean())
        shifted = np.clip(p - bias, 0.01, 0.99)
        before, after = brier(p, y), brier(shifted, y)
        gain = before - after
        if abs(bias) < 0.03:
            verdict = "well centred; error is noise"
        elif gain > 0.01:
            verdict = f"BIAS: shifting by {-bias:+.2f} would help"
        else:
            verdict = "biased but shifting gains little"
        print(
            f"{family:26s} {p.mean():6.3f} {y.mean():7.3f} {bias:+7.3f} "
            f"{after:12.4f} {gain:+8.4f}   {verdict}"
        )


def by_regime(rows: list[dict], lane: str = "honest") -> None:
    section("3. Does the edge depend on the matchup?")
    sub = [r for r in rows if r["lane"] == lane]

    def report(label: str, items: list[dict]) -> None:
        if len(items) < 6:
            print(f"  {label:34s} n={len(items):3d}  too few")
            return
        p = np.array([r["submitted"] for r in items])
        c = np.array([r["crowd"] for r in items])
        y = np.array([r["outcome"] for r in items], dtype=float)
        beat = sum(1 for r in items if r["outcome_tag"] == "beat_crowd")
        print(
            f"  {label:34s} n={len(items):3d}  RBP {sum(r['rbp'] for r in items):+8.1f}  "
            f"beat {100 * beat / len(items):3.0f}%  ours {brier(p, y):.4f} crowd {brier(c, y):.4f}"
        )

    print("\nby draw:")
    for draw in ("mens", "womens"):
        report(draw, [r for r in sub if r["draw"] == draw])

    print("\nby how lopsided the market thought it was (crowd on the winner question):")
    winners = {r["match_name"]: r["crowd"] for r in sub if r["family"] == "match_winner"}
    buckets = {"mismatch (crowd <25 or >75)": [], "close (25-75)": []}
    for r in sub:
        c = winners.get(r["match_name"])
        if c is None:
            continue
        key = "mismatch (crowd <25 or >75)" if (c < 0.25 or c > 0.75) else "close (25-75)"
        buckets[key].append(r)
    for label, items in buckets.items():
        report(label, items)

    print("\nby our own confidence:")
    for label, lo, hi in (
        ("low (<0.5)", 0.0, 0.5),
        ("mid (0.5-0.8)", 0.5, 0.8),
        ("high (>=0.8)", 0.8, 2.0),
    ):
        report(label, [r for r in sub if r["confidence"] and lo <= float(r["confidence"]) < hi])


def crowd_drift(rows: list[dict], lane: str = "honest") -> None:
    section("4. Is the crowd improving as the tournament goes on?")
    sub = sorted(
        [r for r in rows if r["lane"] == lane and r["settled_at"]],
        key=lambda r: r["settled_at"],
    )
    if len(sub) < 20:
        print("  too few settled questions to judge drift")
        return
    half = len(sub) // 2
    for label, items in (("earlier half", sub[:half]), ("later half", sub[half:])):
        p = np.array([r["submitted"] for r in items])
        c = np.array([r["crowd"] for r in items])
        y = np.array([r["outcome"] for r in items], dtype=float)
        print(
            f"  {label:14s} n={len(items):3d}  crowd Brier {brier(c, y):.4f}  "
            f"ours {brier(p, y):.4f}  our edge {brier(c, y) - brier(p, y):+.4f}"
        )
    print("  A shrinking edge means round-1 measurements should not be extrapolated.")


def counterfactuals(rows: list[dict], lane: str = "honest") -> None:
    section("5. Counterfactuals: which specific change would have scored better?")
    sub = [r for r in rows if r["lane"] == lane]
    var = crowd_variance(sub)
    # The free component does not depend on what we submit, so it is added back
    # to every variant. Omitting it made every counterfactual look catastrophic.
    free = 100 * var.sum()
    actual = sum(r["rbp"] for r in sub)
    print(f"  actual {actual:+.1f} = free {free:+.1f} + skill {actual - free:+.1f}\n")
    print(f"{'change':46s} {'total':>9s} {'skill':>9s} {'delta':>9s}")

    variants: dict[str, object] = {
        "as submitted": lambda r: r["submitted"],
        "copy the consensus": lambda r: r["crowd"],
        "always the raw model": lambda r: r["model"] if r["model"] else r["submitted"],
        "always the base rate": lambda r: r["base_rate"] if r["base_rate"] else 0.5,
        "always 50": lambda _r: 0.5,
    }
    for family in sorted({r["family"] for r in sub}):
        for shift in (-0.10, -0.05, 0.05, 0.10):
            variants[f"shift {family} by {shift:+.2f}"] = lambda r, f=family, s=shift: (
                min(max(r["submitted"] + s, 0.01), 0.99) if r["family"] == f else r["submitted"]
            )

    scored = []
    for label, fn in variants.items():
        skill = 0.0
        for r in sub:
            p = float(fn(r))
            skill += (
                r["multiplier"] * ((r["crowd"] - r["outcome"]) ** 2 - (p - r["outcome"]) ** 2) * 100
            )
        scored.append((label, free + skill, skill))

    for label, total, skill in sorted(scored, key=lambda kv: -kv[1])[:15]:
        print(f"{label:46s} {total:9.1f} {skill:9.1f} {total - actual:+9.1f}")


def plots(rows: list[dict], lane: str = "honest") -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(matplotlib not installed; skipping plots)")
        return

    PLOTS.mkdir(parents=True, exist_ok=True)
    sub = [r for r in rows if r["lane"] == lane]
    families = sorted({r["family"] for r in sub})

    fig, axes = plt.subplots(2, 3, figsize=(16, 9))
    for ax, family in zip(axes.flat, families, strict=False):
        items = [r for r in sub if r["family"] == family]
        p = np.array([r["submitted"] for r in items])
        c = np.array([r["crowd"] for r in items])
        y = np.array([r["outcome"] for r in items], dtype=float)
        ax.scatter(c[y == 1], p[y == 1], c="tab:green", s=42, label="happened")
        ax.scatter(c[y == 0], p[y == 0], c="tab:red", s=42, marker="x", label="did not")
        ax.plot([0, 1], [0, 1], "k--", lw=0.8)
        ax.set_title(f"{family}  (ours vs crowd)")
        ax.set_xlabel("crowd")
        ax.set_ylabel("ours")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.legend(fontsize=7)
    for ax in axes.flat[len(families) :]:
        ax.axis("off")
    fig.suptitle("Above the diagonal = we said YES more than the crowd", fontsize=11)
    fig.tight_layout()
    fig.savefig(PLOTS / "ours_vs_crowd.png", dpi=110)

    fig, ax = plt.subplots(figsize=(9, 5))
    width = 0.38
    x = np.arange(len(families))
    for offset, (label, key) in zip(
        (-width / 2, width / 2), (("ours", "submitted"), ("crowd", "crowd")), strict=True
    ):
        biases = []
        for family in families:
            items = [r for r in sub if r["family"] == family]
            biases.append(np.mean([r[key] for r in items]) - np.mean([r["outcome"] for r in items]))
        ax.bar(x + offset, biases, width, label=label)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(families, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("mean forecast minus realised rate")
    ax.set_title("Bias by family: positive means YES was said too often")
    ax.legend()
    fig.tight_layout()
    fig.savefig(PLOTS / "bias_by_family.png", dpi=110)
    print(f"\nplots written to {PLOTS}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", default="honest")
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args(argv)

    rows = load()
    print(f"{len(rows)} rows, {len({r['match_name'] for r in rows})} matches")
    by_family(rows, args.lane)
    bias_vs_noise(rows, args.lane)
    by_regime(rows, args.lane)
    crowd_drift(rows, args.lane)
    counterfactuals(rows, args.lane)
    if args.plots:
        plots(rows, args.lane)
    return 0


if __name__ == "__main__":
    sys.exit(main())
