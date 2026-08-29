"""Build a player table keyed by the name SportsPredict uses.

The competition names players as ``"M. Giron"``; our data names them ``"Marcos
Giron"``.  Every lookup during a live run has to cross that gap, and a lookup
that fails does not raise - it quietly returns a tour-average player and prices
the match as though both sides were identical.  That failure is invisible in the
output, so the join is resolved once, written down, and checked in.

The output is the single place to look when asking "do we have data for this
player, and how good is it".
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.roster import Roster  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "players"

FIELDS = [
    "sportspredict_name",
    "canonical_name",
    "draw",
    "resolution",
    "matches",
    "rank",
    "height",
    "serve_skill",
    "return_skill",
    "ace_rate",
    "df_rate",
    "winner_rate",
    "unforced_rate",
    "pace_minutes",
    "opponent",
    "match_id",
]


def latest_corpus() -> dict:
    files = sorted(glob.glob(str(OUT_DIR.parent / "corpus" / "corpus-*.json")))
    if not files:
        raise SystemExit("no corpus captured yet; run scripts/capture_corpus.py first")
    return json.loads(Path(files[-1]).read_text(encoding="utf-8"))


def build(corpus: dict, roster: Roster) -> list[dict]:
    rows: list[dict] = []
    for record in corpus["matches"]:
        match = record["match"]
        left, _, right = str(match.get("name", "")).partition(" vs ")
        pair = [left.strip(), right.strip()]
        for index, name in enumerate(pair):
            if not name:
                continue
            profile = roster.find(name)
            canonical = profile.name if profile else ""
            rows.append(
                {
                    "sportspredict_name": name,
                    "canonical_name": canonical,
                    "draw": _draw_of(canonical, roster),
                    "resolution": _how(name, canonical, roster),
                    "matches": profile.matches if profile else 0,
                    "rank": _fmt(roster.rank_of(canonical) if canonical else None),
                    "height": _fmt(profile.height if profile else None),
                    "serve_skill": _fmt(profile.serve_skill if profile else None, 5),
                    "return_skill": _fmt(profile.return_skill if profile else None, 5),
                    "ace_rate": _fmt(profile.ace_rate if profile else None, 5),
                    "df_rate": _fmt(profile.df_rate if profile else None, 5),
                    "winner_rate": _fmt(profile.winner_rate if profile else None, 5),
                    "unforced_rate": _fmt(profile.unforced_rate if profile else None, 5),
                    "pace_minutes": _fmt(profile.pace_minutes if profile else None, 3),
                    "opponent": pair[1 - index],
                    "match_id": match.get("id", ""),
                }
            )
    return sorted(rows, key=lambda r: (r["draw"], r["sportspredict_name"]))


def _draw_of(canonical: str, roster: Roster) -> str:
    """Men's or women's, from which tour file the player came out of."""
    tour = roster.tours.get(canonical, "")
    return {"ATP": "mens", "WTA": "womens"}.get(tour, "unknown")


def _how(name: str, canonical: str, roster: Roster) -> str:
    """Record how the name was resolved, so a bad join can be traced."""
    if not canonical:
        return "UNRESOLVED"
    if name in roster.overrides:
        return "override"
    if name in roster.resolved_by_dominance:
        return "dominance"
    if name.strip() == canonical:
        return "exact"
    return "crosswalk"


def _fmt(value, places: int = 1) -> str:
    if value is None or value != value:
        return ""
    return f"{float(value):.{places}f}"


def report(rows: list[dict]) -> str:
    total = len(rows)
    by_draw: dict[str, int] = {}
    by_res: dict[str, int] = {}
    thin: list[str] = []
    for row in rows:
        by_draw[row["draw"]] = by_draw.get(row["draw"], 0) + 1
        by_res[row["resolution"]] = by_res.get(row["resolution"], 0) + 1
        if 0 < int(row["matches"]) < 20:
            thin.append(f"{row['sportspredict_name']} ({row['matches']})")

    lines = [f"players: {total}"]
    lines.append("draw:       " + ", ".join(f"{k}={v}" for k, v in sorted(by_draw.items())))
    lines.append("resolution: " + ", ".join(f"{k}={v}" for k, v in sorted(by_res.items())))

    unresolved = [r["sportspredict_name"] for r in rows if r["resolution"] == "UNRESOLVED"]
    if unresolved:
        lines.append(f"UNRESOLVED ({len(unresolved)}): {', '.join(sorted(set(unresolved)))}")
        lines.append("  These price at the tour average. That is honest, but it is not a read.")
    if thin:
        lines.append(f"THIN RECORD (<20 matches, {len(thin)}): {', '.join(sorted(set(thin))[:14])}")
        lines.append("  Ratings here are shrunk hard toward the mean and carry little signal.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT_DIR / "us_open_2026_draw.csv"))
    args = parser.parse_args(argv)

    rows = build(latest_corpus(), Roster.load())
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(report(rows))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
