"""Fetch Bovada's tennis coupon and extract the two markets nobody else prices.

Bovada is the only public source found that quotes **"Most Aces"** and **"Most Double
Faults"** head to head, and it quotes them three-way with an explicit ``Draw``. That is
exactly the shape of our question — "will A record more X than B?", where a tie resolves NO —
so a proportional de-vig of the three outcomes yields the answer directly, with the tie mass
already priced by someone risking their own money.

Those two families are where this entry loses points: women's double faults score -4.40
RBP/question and women's aces -2.99, against men's aces at +2.70 from the identical model.

No auth, no API key, no geo-block observed. The endpoint returns about 1.4 MB of JSON.

**This script is a capture tool first and a pricing input second.** We have no historical
Bovada prices, so there is currently no way to replay these quotes against settled questions,
which is what our evidence hierarchy demands before trusting a new input. Running it on a
schedule builds that history: each snapshot is written with a UTC timestamp, and once the
matches inside it settle we can measure whether Bovada beats our model rather than assuming it.

An orientation warning, learned the hard way: the competition question names its **subject
first** ("Will N. Podoroska record more double faults than I. Swiatek?" has Podoroska as the
subject even though the roster lists Swiatek as player A). Match Bovada outcomes to the
subject parsed from the question text, never to a positional player field.

Usage
-----
    python scripts/fetch_bovada.py                 # snapshot to data/markets/bovada/
    python scripts/fetch_bovada.py --print         # also print what was found
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

URL = (
    "https://www.bovada.lv/services/sports/event/coupon/events/A/description/"
    "tennis?lang=en&eventsLimit=50"
)
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
OUT = ROOT / "data/markets/bovada"

#: Bovada's label -> our family name.
WANTED = {"Most Aces": "aces", "Most Double Faults": "double_faults"}


def last_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    parts = re.sub(r"[^a-z ]", "", text.lower()).split()
    return parts[-1] if parts else ""


def fetch(url: str = URL, timeout: int = 60) -> list:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def extract(payload: list) -> list[dict]:
    """One record per player side of every ace / double-fault market."""
    events = []
    for block in payload:
        events.extend(block.get("events", []))

    records = []
    for event in events:
        for group in event.get("displayGroups", []):
            for market in group.get("markets", []):
                family = WANTED.get(market.get("description", ""))
                if family is None:
                    continue
                implied = {}
                for outcome in market.get("outcomes", []):
                    price = (outcome.get("price") or {}).get("decimal")
                    if not price:
                        continue
                    implied[outcome.get("description", "")] = 1.0 / float(price)
                overround = sum(implied.values())
                if overround <= 0 or "Draw" not in implied:
                    # Without the Draw leg we cannot separate the tie mass, and a
                    # two-way de-vig would silently fold ties into the favourite.
                    continue
                fair = {k: v / overround for k, v in implied.items()}
                tie = fair["Draw"]
                for name, probability in fair.items():
                    if name == "Draw":
                        continue
                    records.append(
                        {
                            "match": event.get("description"),
                            "start_time": event.get("startTime"),
                            "family": family,
                            "player": name,
                            "player_key": last_name(name),
                            "p_more": round(probability, 4),
                            "p_tie": round(tie, 4),
                            "overround": round(overround, 4),
                        }
                    )
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", action="store_true", dest="show")
    args = parser.parse_args(argv)

    try:
        payload = fetch()
    except Exception as exc:  # noqa: BLE001 - a snapshot failure must not be fatal
        print(f"bovada fetch failed: {exc}")
        return 1

    records = extract(payload)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = OUT / f"bovada-{stamp}.json"
    path.write_text(json.dumps(records, indent=1))

    matches = len({r["match"] for r in records})
    print(f"{len(records)} quotes across {matches} matches -> {path.relative_to(ROOT)}")
    if args.show:
        for record in sorted(records, key=lambda r: (r["family"], r["match"] or "")):
            print(
                f"  {record['family']:14s} {str(record['player'])[:22]:22s} "
                f"p_more {record['p_more']:.3f}  tie {record['p_tie']:.3f}  "
                f"{str(record['match'])[:34]}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
