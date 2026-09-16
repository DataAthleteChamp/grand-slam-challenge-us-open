"""Replay every settled question and test whether shrinking or shifting a family
would have scored better.

The API returns `result_value` (0/100), `crowd_value` (integer percent) and the
realised `rbp` per question, so this is tier-1 evidence rather than a backtest:
the counterfactual only changes our own submission and leaves the crowd fixed.

Skill is the only term we control.  Relative Brier points decompose as

    rbp = 100 * (field_mean_brier - our_brier)
        = 100 * (crowd_brier - our_brier) + 100 * var(field)
        =            skill                +   free term

and the free term is paid identically to everyone who answers, so a change is
worth making exactly when it raises the skill term.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RESULTS = Path(__file__).resolve().parent.parent / "data" / "history" / "final" / "settled.csv"

#: Grid of shrink factors.  1.0 is the live model, 0.0 collapses onto the
#: leave-one-out base rate, values above 1.0 extremise.
LAMBDAS = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25)


def classify(question: str) -> str:
    q = question.lower()
    if "more aces" in q:
        return "aces"
    if "double fault" in q:
        return "double_faults"
    if "winners" in q:
        return "winners"
    if "unforced" in q:
        return "unforced"
    if "first serve" in q or "first-serve" in q:
        return "first_serve"
    if "tiebreak" in q:
        return "tiebreak"
    if "win?" in q:
        return "match_winner"
    if "minutes" in q:
        return "duration"
    if "6" in q and "set" in q:
        return "lopsided_set"
    return "other"


def load(path: Path = RESULTS, *, lane: str = "honest") -> pd.DataFrame:
    d = pd.read_csv(path)
    d = d[d["lane"].eq(lane)].copy()
    d["p"] = d.submitted / 100.0
    d["c"] = d.crowd
    d["y"] = d.outcome
    d["skill"] = d.multiplier * ((d.c - d.y) ** 2 - (d.p - d.y) ** 2) * 100
    d["rbp"] = d.api_rbp
    d["day"] = pd.to_datetime(d.prediction_created_at).dt.strftime("%m-%d")
    return d


def auc(y: np.ndarray, s: np.ndarray) -> float:
    y = np.asarray(y)
    s = np.asarray(s)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    gt = (pos[:, None] > neg[None, :]).sum()
    eq = (pos[:, None] == neg[None, :]).sum()
    return float((gt + 0.5 * eq) / (len(pos) * len(neg)))


def loo_base(y: np.ndarray) -> np.ndarray:
    """Leave-one-out realised base rate, so the target never sees its own outcome."""
    n = len(y)
    total = y.sum()
    return (total - y) / (n - 1)


def skill_of(
    p: np.ndarray,
    c: np.ndarray,
    y: np.ndarray,
    multiplier: np.ndarray | None = None,
) -> np.ndarray:
    weight = 1.0 if multiplier is None else multiplier
    return weight * ((c - y) ** 2 - (p - y) ** 2) * 100


def bootstrap_ci(delta: np.ndarray, draws: int = 5000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(delta)
    idx = rng.integers(0, n, size=(draws, n))
    means = delta[idx].sum(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def shrink_grid(d: pd.DataFrame) -> pd.DataFrame:
    out = []
    for fam, x in d.groupby("family"):
        p, c, y = x.p.to_numpy(), x.c.to_numpy(), x.y.to_numpy()
        multiplier = x.multiplier.to_numpy()
        base = loo_base(y)
        current = skill_of(p, c, y, multiplier)
        for lam in LAMBDAS:
            cand = np.clip(base + lam * (p - base), 0.01, 0.99)
            got = skill_of(cand, c, y, multiplier)
            delta = got - current
            lo, hi = bootstrap_ci(delta)
            out.append(
                {
                    "family": fam,
                    "n": len(x),
                    "lambda": lam,
                    "skill": got.sum(),
                    "delta": delta.sum(),
                    "lo": lo,
                    "hi": hi,
                    "sig": "SIG" if (lo > 0 or hi < 0) else "",
                }
            )
    return pd.DataFrame(out)


def timesplit(d: pd.DataFrame, frac: float = 0.6) -> pd.DataFrame:
    """Pick lambda on the earlier questions, score it on the later ones."""
    out = []
    for fam, x in d.sort_values("prediction_created_at").groupby("family"):
        cut = int(len(x) * frac)
        if cut < 5 or len(x) - cut < 5:
            continue
        tr, te = x.iloc[:cut], x.iloc[cut:]
        base_tr = tr.y.mean()
        best, best_lam = -1e18, 1.0
        for lam in LAMBDAS:
            cand = np.clip(base_tr + lam * (tr.p - base_tr), 0.01, 0.99)
            s = skill_of(
                cand.to_numpy(),
                tr.c.to_numpy(),
                tr.y.to_numpy(),
                tr.multiplier.to_numpy(),
            ).sum()
            if s > best:
                best, best_lam = s, lam
        cand = np.clip(base_tr + best_lam * (te.p - base_tr), 0.01, 0.99)
        got = skill_of(
            cand.to_numpy(),
            te.c.to_numpy(),
            te.y.to_numpy(),
            te.multiplier.to_numpy(),
        ).sum()
        cur = skill_of(
            te.p.to_numpy(),
            te.c.to_numpy(),
            te.y.to_numpy(),
            te.multiplier.to_numpy(),
        ).sum()
        out.append(
            {
                "family": fam,
                "n_test": len(te),
                "lambda_chosen": best_lam,
                "held_out_now": cur,
                "held_out_new": got,
                "delta": got - cur,
            }
        )
    return pd.DataFrame(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", action="store_true", help="shrink grid with bootstrap CIs")
    parser.add_argument("--split", action="store_true", help="honest time-split check")
    parser.add_argument("--lane", choices=("honest", "scenario"), default="honest")
    parser.add_argument("--input", type=Path, default=RESULTS)
    args = parser.parse_args(argv)

    d = load(args.input, lane=args.lane)
    pd.set_option("display.width", 250)

    print(f"{args.lane}: settled {len(d)} questions   total API RBP {d.rbp.sum():+.1f}")
    print(f"skill {d.skill.sum():+.1f} ({d.skill.mean():+.3f}/q)   ", end="")
    print(f"free {(d.rbp - d.skill).sum():+.1f}")
    print()

    summary = []
    for fam, x in d.groupby("family"):
        summary.append(
            {
                "family": fam,
                "n": len(x),
                "skill": x.skill.sum(),
                "per_q": x.skill.mean(),
                "our_auc": auc(x.y, x.p),
                "crowd_auc": auc(x.y, x.c),
                "our_mean": x.p.mean(),
                "realised": x.y.mean(),
                "bias": x.p.mean() - x.y.mean(),
            }
        )
    s = pd.DataFrame(summary).sort_values("skill")
    print(s.to_string(index=False, float_format=lambda v: f"{v:8.3f}"))

    if args.grid:
        print("\n=== shrink grid (delta vs live model, leave-one-out base rate) ===")
        g = shrink_grid(d)
        for fam in s.family:
            sub = g[g.family == fam]
            print(f"\n{fam}  n={sub.n.iloc[0]}")
            print(
                sub[["lambda", "skill", "delta", "lo", "hi", "sig"]].to_string(
                    index=False, float_format=lambda v: f"{v:8.2f}"
                )
            )

    if args.split:
        print("\n=== honest time split: lambda fitted on first 60%, scored on last 40% ===")
        t = timesplit(d)
        print(t.to_string(index=False, float_format=lambda v: f"{v:8.2f}"))
        print(f"\ntotal held-out delta: {t.delta.sum():+.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
