"""Write the manual-entry numbers to ``data/manual/NUMBERS.txt``.

The manual entry is typed into the website by hand, so it needs the live bot
numbers laid out by match with the subject named on every line.  Orientation is
the expensive mistake here: a correct number entered against the wrong player is
a full inversion that looks entirely plausible.

The numbers are the honest lane's, with one adjustment.  Paired on the same 40
settled questions, sharpening the bot number gained on four families - aces
+3.19, double faults +1.61, duration +1.38, match winner +1.36 relative Brier
points per question - and lost 5.95 on tiebreak, because sharpening pushes an
already-too-high tiebreak number further up.  So tiebreak is left exactly as the
bot has it.

The sharpening is mild (a temperature of 1/1.15 in log-odds).  It used to be
1.25, but the bots no longer shrink their answers toward base rates, so most of
the gap that sharpening was closing is now inside the model itself.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.models.lanes import sharpen  # noqa: E402
from bot.sportspredict.api import SportsPredictClient  # noqa: E402
from bot.sportspredict.config import Config  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "manual" / "NUMBERS.txt"

#: Log-odds temperature applied to every family except tiebreak.
SHARPEN = 1.15

#: Questions are listed in this order within a match, which is how the website
#: presents them; it keeps hand-entry aligned with the screen.
ORDER = {"win": 0, "aces": 1, "double": 2, "tiebreak": 3, "last": 4}


def question_rank(question: str) -> int:
    lowered = question.lower()
    for token, rank in ORDER.items():
        if token in lowered:
            return rank
    return 9


def build(lane: str = "honest") -> str:
    client = SportsPredictClient(Config.from_env(lane=lane))
    _, lobby = client.bootstrap()
    lobby_id = str(lobby["id"])

    markets = {str(m["id"]): m for m in client.list_markets(lobby_id)}
    live: dict[str, float] = {}
    for prediction in client.list_predictions(lobby_id):
        market_id = prediction.get("market_id") or (prediction.get("market") or {}).get("id")
        value = prediction.get("probability", prediction.get("probability_submitted"))
        if market_id in markets and value is not None:
            live[str(market_id)] = float(value)

    by_match: dict[tuple[str, str], list[tuple[str, float]]] = collections.defaultdict(list)
    for market_id, value in live.items():
        market = markets[market_id]
        key = (market["match"]["name"], market["match"]["opening_time"])
        by_match[key].append((market["question"], value))

    lines = [
        f"MANUAL NUMBERS - {len(live)} questions across {len(by_match)} matches",
        f"Sharpened x{SHARPEN} in log-odds on winner/aces/double faults/duration.",
        "Tiebreak is left AT the bot number: sharpening it cost 5.95 RBP/question",
        "on the settled sample, because it pushes an already-high number higher.",
        "",
    ]
    for (name, opening), questions in sorted(by_match.items(), key=lambda kv: kv[0][1]):
        lines.append(f"--- {name}   (starts {opening[:16].replace('T', ' ')}Z)")
        for question, value in sorted(questions, key=lambda q: question_rank(q[0])):
            if "tiebreak" in question.lower():
                manual = int(round(value))
            else:
                manual = max(4, min(96, round(sharpen(value / 100.0, 1 / SHARPEN) * 100)))
            lines.append(f"     bot {value:3.0f}  ->  MANUAL {manual:3.0f}   {question}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", default="honest")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    text = build(args.lane)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"wrote {args.out} ({text.count('MANUAL')} numbers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
