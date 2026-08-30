"""Score every settled question, per entry, and say what to change.

    PYTHONPATH=. .venv/bin/python scripts/score.py
    PYTHONPATH=. .venv/bin/python scripts/score.py --detail

The competition's own ``/results`` endpoint returns, for every settled
prediction, the number we submitted, the crowd's number, the outcome, our Brier
score and the resulting RBP.  That is a complete feedback signal and far better
than anything we could reconstruct ourselves, because ``crowd_value`` is not
observable any other way and RBP is meaningless without it.

Scoring is per API key, so each bot is queried separately.  A manual entry made
through the website belongs to the user account rather than to a bot key and is
not reachable here; the website's own history page is the only place to see it.

The report is organised by question family because that is the unit of decision.
Knowing the total is interesting; knowing that one family is losing while
another is winning is what changes next round's numbers.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.markets.parser import parse_question  # noqa: E402
from bot.sportspredict.api import SportsPredictClient, SportsPredictError  # noqa: E402
from bot.sportspredict.config import Config, ConfigError  # noqa: E402

LANES = ("honest", "scenario")


def fetch(lane: str) -> list[dict]:
    """Settled predictions for one bot, or an empty list if it cannot be read."""
    try:
        client = SportsPredictClient(Config.from_env(lane=lane))
    except ConfigError:
        return []
    try:
        _, lobby = client.bootstrap()
        rows = client.list_results(str(lobby["id"]))
    except SportsPredictError as exc:
        print(f"  ! {lane}: {exc}", file=sys.stderr)
        return []
    return [r for r in rows if r.get("market_status") == "settled"]


def family_of(row: dict) -> str:
    return parse_question(str(row.get("question", ""))).family.value


def summarise(rows: list[dict]) -> dict[str, dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[family_of(row)].append(row)

    out = {}
    for family, items in buckets.items():
        rbps = [float(r.get("rbp") or 0.0) for r in items]
        ours = [float(r.get("brier_score") or 0.0) for r in items]
        crowd = [
            (
                (float(r.get("crowd_value") or 0) / 100.0)
                - (float(r.get("result_value") or 0) / 100.0)
            )
            ** 2
            for r in items
        ]
        beat = sum(1 for r in items if str(r.get("outcome_tag")) == "beat_crowd")
        out[family] = {
            "n": len(items),
            "total_rbp": sum(rbps),
            "mean_rbp": sum(rbps) / len(items),
            "beat_crowd": beat / len(items),
            "our_brier": sum(ours) / len(items),
            "crowd_brier": sum(crowd) / len(items),
        }
    return out


def render(report: dict[str, dict], label: str) -> str:
    if not report:
        return f"{label}: nothing settled yet"
    header = (
        f"{'family':26s} {'n':>4s} {'total RBP':>10s} {'mean':>7s} "
        f"{'beat%':>7s} {'ours':>7s} {'crowd':>7s}"
    )
    lines = [f"=== {label} ===", header, "-" * len(header)]
    for family, row in sorted(report.items(), key=lambda kv: -kv[1]["total_rbp"]):
        lines.append(
            f"{family:26s} {row['n']:4d} {row['total_rbp']:10.1f} {row['mean_rbp']:7.2f} "
            f"{100 * row['beat_crowd']:6.1f}% {row['our_brier']:7.4f} {row['crowd_brier']:7.4f}"
        )
    total = sum(r["total_rbp"] for r in report.values())
    n = sum(r["n"] for r in report.values())
    beat = sum(r["beat_crowd"] * r["n"] for r in report.values()) / max(n, 1)
    lines.append("-" * len(header))
    lines.append(f"{'TOTAL':26s} {n:4d} {total:10.1f} {total / max(n, 1):7.2f} {100 * beat:6.1f}%")
    return "\n".join(lines)


def verdicts(report: dict[str, dict]) -> list[str]:
    """What the numbers imply, stated cautiously about small samples."""
    out = []
    for family, row in sorted(report.items(), key=lambda kv: kv[1]["total_rbp"]):
        n, beat, total = row["n"], row["beat_crowd"], row["total_rbp"]
        if n < 15:
            verdict = f"only {n} settled - too few to act on"
        elif beat <= 0.40 and total < 0:
            verdict = "LOSING to the crowd; move toward the crowd or the base rate"
        elif beat >= 0.60 and total > 0:
            verdict = "beating the crowd consistently; this is where the edge is"
        elif total < 0:
            verdict = "negative overall despite a fair hit rate; look for a few large misses"
        else:
            verdict = "roughly level with the crowd"
        out.append(f"  {family:26s} {total:+8.1f} RBP over {n:3d}  {verdict}")
    return out


def worst(rows: list[dict], k: int = 12) -> str:
    ranked = sorted(rows, key=lambda r: float(r.get("rbp") or 0.0))
    lines = [f"{'RBP':>8s} {'ours':>5s} {'crowd':>6s} {'result':>7s}  question"]
    for row in ranked[:k]:
        lines.append(
            f"{float(row.get('rbp') or 0):8.1f} {row.get('probability_submitted'):5} "
            f"{row.get('crowd_value'):6} {row.get('result'):>7}  {str(row.get('question'))[:56]}"
        )
    return "\n".join(lines)


def best(rows: list[dict], k: int = 8) -> str:
    ranked = sorted(rows, key=lambda r: -float(r.get("rbp") or 0.0))
    lines = [f"{'RBP':>8s} {'ours':>5s} {'crowd':>6s} {'result':>7s}  question"]
    for row in ranked[:k]:
        lines.append(
            f"{float(row.get('rbp') or 0):8.1f} {row.get('probability_submitted'):5} "
            f"{row.get('crowd_value'):6} {row.get('result'):>7}  {str(row.get('question'))[:56]}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detail", action="store_true", help="list best and worst questions")
    args = parser.parse_args(argv)

    everything = {}
    for lane in LANES:
        rows = fetch(lane)
        everything[lane] = rows
        report = summarise(rows)
        print(render(report, f"BOT: {lane}"))
        if report:
            print("\nwhat this implies:")
            print("\n".join(verdicts(report)))
        print()

    if args.detail:
        for lane, rows in everything.items():
            if not rows:
                continue
            print(f"=== {lane}: worst questions ===")
            print(worst(rows))
            print(f"\n=== {lane}: best questions ===")
            print(best(rows))
            print()

    print(
        "Manual website entries are scored under the user account rather than a\n"
        "bot API key, so they are not reachable here. Profile & History on the\n"
        "site shows them."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
