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
#: Per-family log-odds sharpening for the hand-entered third account, measured on
#: settled questions by replaying each submission against its sharpened self,
#: pooled over both bot lanes for power (n>=212 per family):
#:
#:   aces           +0.23 [+0.03,+0.43]  SIG   -> sharpen
#:   match winner   +0.52 [+0.30,+0.71]  SIG   -> but the bot ALREADY sharpens this
#:                                                at 1.75, so sharpening again here
#:                                                would compound well past the
#:                                                measured range
#:   double faults  -0.41 [-0.65,-0.17]  SIG   -> do NOT sharpen
#:   duration       -0.34 [-0.62,-0.07]  SIG   -> do NOT sharpen
#:   tiebreak       +0.04 [-0.21,+0.28]        -> nothing to gain
#:
#: Sharpening only helps where the number is under-confident. Double faults and
#: duration are not, and blanket sharpening was quietly costing this account
#: points on two of its five families.
#:
#: NOTE ON COMPOUNDING: the bot now applies EXTREMISE_ALPHA 1.40 to aces itself,
#: so this 1.15 lands on top of it and the hand-entered account effectively runs
#: aces at 1.61. That is deliberate rather than an oversight. Replaying 1135
#: settled questions found ace sharpening significantly positive at every alpha
#: from 1.10 to 1.60 (+41.6 to +151.3 skill points), with the point estimate still
#: rising at 1.80, so 1.61 sits inside the measured range. Holding the two bots at
#: the conservative 1.40 and this account at 1.61 spreads the three entries across
#: that range instead of betting all of them on one end of it.
SHARPEN_BY_TOKEN = {"aces": 1.15}

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
        f"Sharpened x{SHARPEN_BY_TOKEN.get('aces')} in log-odds on ACES ONLY.",
        "Everything else is left AT the bot number. Sharpening double faults cost",
        "0.41 RBP/question on the settled sample and duration 0.34, both significant;",
        "the winner number is already sharpened inside the bot, so doing it again here",
        "would compound. See SHARPEN_BY_TOKEN for the per-family measurements.",
        "",
    ]
    for (name, opening), questions in sorted(by_match.items(), key=lambda kv: kv[0][1]):
        lines.append(f"--- {name}   (starts {opening[:16].replace('T', ' ')}Z)")
        for question, value in sorted(questions, key=lambda q: question_rank(q[0])):
            lowered = question.lower()
            alpha = next(
                (a for token, a in SHARPEN_BY_TOKEN.items() if token in lowered), None
            )
            if alpha is None:
                manual = int(round(value))
            else:
                manual = max(4, min(96, round(sharpen(value / 100.0, 1 / alpha) * 100)))
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
