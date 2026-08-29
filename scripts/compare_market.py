"""Compare our numbers against the bookmaker consensus, question by question.

Run before submitting::

    PYTHONPATH=. .venv/bin/python scripts/compare_market.py

Only the match-winner family has a market to compare against, and that is the
point.  Every bookmaker on earth prices it, the closing line has a Brier near
0.203, and in the predecessor event a de-vigged consensus would have beaten the
entrant's own model by 566 relative points.  So a large disagreement there is
much more likely to be our error than an edge, and this script exists to make
those disagreements impossible to miss before they are submitted rather than
after they are scored.

The other four families have no market anywhere, which is exactly why they carry
the edge - but it also means nothing here can check them, and that asymmetry
should be held in mind rather than forgotten.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data.names import strip_accents  # noqa: E402
from bot.data.oddsapi import OddsClient, OddsError, match_winner  # noqa: E402
from bot.ledger import Ledger  # noqa: E402

CACHE = Path("/tmp")


def surname_key(name: str) -> str:
    """A key that survives 'M. Giron' vs 'Marcos Giron' vs 'M. Čilić'."""
    cleaned = strip_accents(str(name)).lower().replace(".", " ")
    parts = [p for p in cleaned.split() if len(p) > 1]
    return parts[-1] if parts else cleaned.strip()


def load_market(use_cache: bool) -> dict[str, dict[str, float]]:
    """``surname -> {opponent surname: consensus probability}``."""
    events = []
    for tag in ("tennis_atp_us_open", "tennis_wta_us_open"):
        cached = CACHE / f"odds_{tag}.json"
        if use_cache and cached.exists():
            events += json.loads(cached.read_text())
            continue
        try:
            fetched = OddsClient.from_env().odds(tag, markets="h2h", regions="eu,uk")
        except OddsError as exc:
            print(f"  ! {tag}: {exc}", file=sys.stderr)
            continue
        cached.write_text(json.dumps(fetched))
        events += fetched

    book: dict[str, dict[str, float]] = {}
    for event in events:
        agreed = match_winner(event)
        if len(agreed) != 2:
            continue
        names = list(agreed)
        for name in names:
            other = next(n for n in names if n != name)
            entry = book.setdefault(surname_key(name), {})
            entry[surname_key(other)] = agreed[name].probability
            entry["_books"] = agreed[name].n_books
            entry["_spread"] = agreed[name].spread
    return book


def compare(ledger: Ledger, book: dict[str, dict[str, float]]) -> list[dict]:
    rows = []
    for record in ledger.all_records():
        for entry in record.entries:
            if entry.family != "match_winner":
                continue
            subject = _subject_of(entry.question)
            if not subject:
                continue
            key = surname_key(subject)
            opponents = book.get(key)
            if not opponents:
                continue
            other = _other_of(record, subject)
            quoted = opponents.get(surname_key(other)) if other else None
            if quoted is None:
                continue
            rows.append(
                {
                    "match": record.match_name,
                    "draw": record.draw,
                    "subject": subject,
                    "ours": entry.submission,
                    "market": round(quoted * 100, 1),
                    "gap": round(entry.submission - quoted * 100, 1),
                    "books": opponents.get("_books", 0),
                    "spread": round(opponents.get("_spread", 0.0) * 100, 1),
                }
            )
    return sorted(rows, key=lambda r: -abs(r["gap"]))


def _subject_of(question: str) -> str:
    text = question.strip()
    if text.lower().startswith("will ") and text.rstrip("?").lower().endswith(" win"):
        return text[5:].rstrip("?").strip()[:-4].strip()
    return ""


def _other_of(record, subject: str) -> str:
    left, _, right = str(record.match_name).partition(" vs ")
    left, right = left.strip(), right.strip()
    return right if surname_key(left) == surname_key(subject) else left


def render(rows: list[dict]) -> str:
    if not rows:
        return "no match-winner questions could be matched to a market quote"
    header = (
        f"{'match':38s} {'subject':16s} {'ours':>5s} {'mkt':>6s} "
        f"{'gap':>6s} {'bk':>3s} {'sprd':>5s}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row['match'][:38]:38s} {row['subject'][:16]:16s} {row['ours']:5d} "
            f"{row['market']:6.1f} {row['gap']:+6.1f} {row['books']:3.0f} {row['spread']:5.1f}"
        )
    gaps = [abs(r["gap"]) for r in rows]
    lines.append("-" * len(header))
    lines.append(
        f"n={len(rows)}  mean |gap|={sum(gaps) / len(gaps):.1f}  "
        f"max={max(gaps):.1f}  over 15pts={sum(1 for g in gaps if g > 15)}"
    )
    signed = sum(r["gap"] for r in rows) / len(rows)
    lines.append(
        f"mean signed gap {signed:+.1f} — a large value means a systematic lean, "
        "not scattered disagreement"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="refetch odds (costs quota)")
    parser.add_argument("--top", type=int, default=25, help="how many rows to print")
    args = parser.parse_args(argv)

    book = load_market(use_cache=not args.live)
    print(f"market: {len(book)} players quoted\n")
    rows = compare(Ledger(), book)
    print(render(rows[: args.top] if args.top else rows))
    if len(rows) > args.top:
        print(f"\n({len(rows) - args.top} more rows omitted; --top 0 for all)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
