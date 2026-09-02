"""Report how many open markets have no prediction, per lane.

Unanswered markets are the most reliable way this entry loses points.  Every
question carries a free term - the crowd's own variance, worth about +2.00
relative Brier points - which is paid to everyone who answers and forfeited by
everyone who does not, no matter how good or bad the answer is.  It does not
depend on being right.

That makes coverage worth more than any modelling change measured so far.  Over
one week three separate gaps opened up unnoticed: 195 questions, then 20, then
70.  The 70 alone was worth about 140 points on each lane.

From the third round onward the event scores rounds at 2x and 3x, so an
unanswered quarter-final question forfeits six free points rather than two.

This check is deliberately cheap - two API calls per lane, no pricing - so it
can run often.  Exit status is 1 when anything is unanswered, so it can gate a
scheduled job or a shell one-liner.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.sportspredict.api import SportsPredictClient, SportsPredictError  # noqa: E402
from bot.sportspredict.config import Config, ConfigError  # noqa: E402

LANES = ("honest", "scenario")


def unanswered_for(lane: str) -> tuple[int, int, list[str]]:
    """Return open market count, prediction count and the unanswered ids."""
    client = SportsPredictClient(Config.from_env(lane=lane))
    _, lobby = client.bootstrap()
    lobby_id = str(lobby["id"])

    markets = client.list_markets(lobby_id)
    open_ids = {str(m["id"]) for m in markets}

    answered: set[str] = set()
    for prediction in client.list_predictions(lobby_id):
        market_id = prediction.get("market_id") or (prediction.get("market") or {}).get("id")
        if market_id:
            answered.add(str(market_id))

    missing = sorted(open_ids - answered)
    return len(open_ids), len(answered), missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", action="append", choices=LANES)
    parser.add_argument(
        "--quiet", action="store_true", help="print only when something is unanswered"
    )
    args = parser.parse_args(argv)

    total_missing = 0
    for lane in args.lane or LANES:
        try:
            open_count, answered, missing = unanswered_for(lane)
        except (SportsPredictError, ConfigError) as exc:
            print(f"{lane}: could not check ({exc})", file=sys.stderr)
            return 2
        total_missing += len(missing)
        if missing or not args.quiet:
            print(
                f"{lane}: open markets {open_count}, predictions {answered}, "
                f"UNANSWERED {len(missing)}"
            )
        for market_id in missing[:10]:
            print(f"    missing {market_id}")

    if total_missing:
        print(
            f"\n{total_missing} unanswered across lanes - reprice and submit, "
            f"each one forfeits about 2 free points before any multiplier."
        )
    return 1 if total_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
