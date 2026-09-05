"""Blend Match Charting Project serve rates into the draw roster.

Why this exists
---------------
The roster's ace and double-fault rates come from the Sackmann match files, which
carry a known defect on the women's side: the whole of the 2025 WTA season and the
2026 women's hard-court block have inverted or randomised winner/loser serve
statistics. ``drop_broken_serve_blocks`` can identify those rows but excluding
them is worse than keeping them, because it also deletes 78 players from the
roster entirely (see the comment in ``scripts/build_player_data.py``).

The Match Charting Project is an INDEPENDENT source, hand-charted, and does not
share that defect. It has the opposite problem: far better rates for the players
it covers, but it covers fewer players and fewer matches each.

Credibility-blending the two gets the coverage of one and the accuracy of the
other. Measured against the roster alone on our own settled questions, the
per-player rate agreement is the tell:

    correlation of roster rate with charting career rate
        ATP ace 0.886   ATP df 0.874   WTA ace 0.894   WTA df 0.676
                                                       ^^^^^^^^^^^^
and WTA double faults is exactly our worst-scoring family.

Result, repricing settled ace and double-fault questions (116 per lane):

    blend K=5    +1.52 RBP/q  CI [+0.39,+2.75]   (honest)
    blend K=10   +1.29        CI [+0.33,+2.31]
    blend K=20   +1.01        CI [+0.22,+1.81]
    scenario lane reproduces all three to within 0.01

Hold-outs at K=10, all positive: time EARLY +1.89 [+0.58,+3.25] / LATE +0.69,
tour WTA +1.83 [+0.13,+3.57] / ATP +0.68, family df +1.72 [+0.16,+3.35] /
ace +0.87.

**ATP aces is deliberately excluded.** It is the one cell where the roster is
better (AUC 0.863 against 0.813 blended, skill -0.57 at every K), which is also
the family where the ATP-only ace-vulnerability model is tuned. It is our best
scoring family and there is nothing to fix.

Leakage
-------
The charting files stop at 2026-05-21 (men) and 2026-05-24 (women), before the
2026 US Open began on 2026-08-27, so no tournament match can leak in. The script
asserts this rather than trusting it.

Usage
-----
    python scripts/blend_charting_rates.py            # rewrites the roster
    python scripts/blend_charting_rates.py --dry-run  # report only
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ROSTER = ROOT / "data/players/us_open_2026_draw.csv"
CHARTING = {
    "mens": ROOT / "data/raw/charting/charting-m-stats-Overview.csv",
    "womens": ROOT / "data/raw/charting/charting-w-stats-Overview.csv",
}

#: Credibility constant: charting matches needed before the charting rate carries
#: half the weight. Swept over 5/10/20; every value is significant in both lanes
#: and the gain rises as K falls, but so does the variance of a thin charting
#: sample, so the middle of the significant range is used.
CREDIBILITY_K = 10

#: (roster column, charting column) pairs to blend.
STATS = [("ace_rate", "aces"), ("df_rate", "dfs")]

#: Cells left alone. See the module docstring.
SKIP = {("mens", "ace_rate")}

#: The tournament these forecasts are for. Charting data at or after this date
#: would be leakage.
EVENT_START = 20260827


def normalise(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", text.lower())


def charting_rates(path: Path, column: str) -> dict[str, tuple[float, int]]:
    """Career rate per player, as counts over service points."""
    frame = pd.read_csv(path, low_memory=False)
    frame = frame[frame["set"].astype(str).eq("Total")]
    for field in (column, "serve_pts"):
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    frame = frame.dropna(subset=[column, "serve_pts"])
    frame = frame[(frame.serve_pts > 30) & (frame[column] >= 0) & (frame[column] < 60)]

    dates = pd.to_numeric(frame.match_id.astype(str).str[:8], errors="coerce")
    latest = int(dates.max())
    if latest >= EVENT_START:
        raise SystemExit(
            f"{path.name} contains matches on {latest}, at or after the event start "
            f"{EVENT_START}. Blending it would leak the outcome being forecast."
        )

    grouped = frame.groupby("player").agg(
        total=(column, "sum"), points=("serve_pts", "sum"), matches=(column, "size")
    )
    return {
        normalise(player): (row.total / row.points, int(row.matches))
        for player, row in grouped.iterrows()
        if row.points > 0
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("--k", type=float, default=CREDIBILITY_K)
    args = parser.parse_args(argv)

    roster = pd.read_csv(ROSTER)
    changed = 0

    for draw, path in CHARTING.items():
        if not path.exists():
            print(f"  {draw}: {path.name} missing, skipped")
            continue
        rows = roster.draw.eq(draw)
        for roster_column, charting_column in STATS:
            if (draw, roster_column) in SKIP:
                print(f"  {draw} {roster_column}: left at the roster value by policy")
                continue
            rates = charting_rates(path, charting_column)
            matched = blended = 0
            for index in roster.index[rows]:
                key = normalise(roster.at[index, "canonical_name"])
                current = roster.at[index, roster_column]
                if key not in rates or pd.isna(current):
                    continue
                rate, count = rates[key]
                matched += 1
                weight = count / (count + args.k)
                new = weight * rate + (1.0 - weight) * float(current)
                if abs(new - float(current)) > 1e-9:
                    roster.at[index, roster_column] = round(new, 5)
                    blended += 1
            changed += blended
            print(
                f"  {draw} {roster_column}: {matched} players matched, "
                f"{blended} rates blended at K={args.k:g}"
            )

    if args.dry_run:
        print(f"\ndry run: {changed} rates would change")
        return 0
    roster.to_csv(ROSTER, index=False)
    print(f"\nwrote {ROSTER.relative_to(ROOT)} ({changed} rates blended)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
