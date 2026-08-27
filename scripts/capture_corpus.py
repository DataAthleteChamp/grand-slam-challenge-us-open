"""Archive the live question corpus and report what the parser makes of it.

This runs once when markets open and again whenever a new round is seeded.  Its
job is not to price anything - it is to answer the questions that no amount of
offline research can settle:

* What is the **exact wording** of each question?  Every base rate in this repo
  is conditioned on an assumed reading, and a single unexpected phrase ("at
  least" rather than "more than", a set-level question where a match-level one
  was assumed) silently redirects a whole family to the wrong model.
* Which way does **YES** point?  The comparison families carry the largest edge
  in the predecessor event, and that edge is a number near 42 or near 58.  If
  the orientation is backwards, every one of those is inverted, and nothing in a
  dry run can reveal it.
* How are **ties** settled?  "More aces than" has a materially different answer
  depending on whether an equal count resolves NO or voids.  On the women's draw
  aces tie 14.5% of the time, so this is worth several points a match.
* Does the parser actually **understand** what arrived?  Anything falling to
  Family.OTHER is answered from a base rate, which is a silent forfeit of edge.

Raw payloads are written verbatim before any interpretation, so the corpus can
be re-analysed later without re-hitting the API.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.markets.parser import Family, parse_question, reset_unparsed
from bot.roster import players_from_match
from bot.sportspredict.api import SportsPredictClient, SportsPredictError
from bot.sportspredict.config import Config, ConfigError

CORPUS_DIR = Path(__file__).resolve().parent.parent / "data" / "corpus"

#: Phrases that change the meaning of a question and must never be assumed.
_FLAGS = {
    "inclusive_threshold": re.compile(r"\bat least\b|\bor more\b|\bor fewer\b|\bor less\b", re.I),
    "tie_rule": re.compile(r"\btie\b|\bties\b|\btied\b|\bequal\b|\bvoid\b|\bpush\b", re.I),
    "set_scoped": re.compile(r"\b(?:first|second|third|fourth|fifth|any|either)\s+set\b", re.I),
    "strictly": re.compile(r"\bstrictly\b|\bmore than\b|\bfewer than\b", re.I),
    "either_player": re.compile(r"\beither player\b|\bone player\b|\bany player\b", re.I),
    "both_players": re.compile(r"\bboth players\b|\beach player\b", re.I),
    "percentage": re.compile(r"%|\bpercent\b", re.I),
}


def capture(client: SportsPredictClient, *, limit: int | None = None) -> dict[str, Any]:
    """Pull the whole corpus and return it with the raw payloads intact."""
    reset_unparsed()
    event, lobby = client.bootstrap()
    matches = client.list_matches(event_id=str(event["id"]), lobby_id=str(lobby["id"]))
    if limit is not None:
        matches = matches[:limit]

    records = []
    for match in matches:
        match_id = str(match.get("id") or "")
        if not match_id:
            continue
        try:
            markets = client.list_markets(str(lobby["id"]), match_id=match_id)
        except SportsPredictError as exc:
            print(f"  ! {match_id}: {exc}", file=sys.stderr)
            continue
        records.append({"match": match, "markets": markets})
        print(f"  {match_id}: {len(markets)} markets", file=sys.stderr)

    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "event": event,
        "lobby": lobby,
        "matches": records,
    }


def analyse(corpus: dict[str, Any]) -> str:
    """Report what the corpus contains and what the parser does with it."""
    lines: list[str] = []
    families: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    unparsed: list[str] = []
    wordings: dict[str, set[str]] = {}
    per_match: Counter[int] = Counter()
    market_keys: Counter[str] = Counter()

    for record in corpus["matches"]:
        match, markets = record["match"], record["markets"]
        names = players_from_match(match)
        per_match[len(markets)] += 1
        for market in markets:
            market_keys.update(market.keys())
            text = _question_text(market)
            if not text:
                continue
            spec = parse_question(text, players=names)
            families[spec.family.value] += 1
            wordings.setdefault(spec.family.value, set()).add(_template(text, names))
            if spec.family is Family.OTHER or not spec.is_understood:
                unparsed.append(text)
            for name, pattern in _FLAGS.items():
                if pattern.search(text):
                    flags[name] += 1

    total = sum(families.values())
    lines.append(f"event    {corpus['event'].get('title')}")
    lines.append(f"captured {corpus['captured_at']}")
    lines.append(f"matches  {len(corpus['matches'])}")
    lines.append(f"markets  {total}")
    lines.append("")

    lines.append(
        "markets per match: " + ", ".join(f"{k}x{v}" for k, v in sorted(per_match.items()))
    )
    lines.append("")

    lines.append("--- families ---")
    for family, count in families.most_common():
        lines.append(f"  {family:28s} {count:5d}  {100 * count / max(total, 1):5.1f}%")
    lines.append("")

    lines.append("--- wording flags (each changes the correct answer) ---")
    if not flags:
        lines.append("  none detected")
    for name, count in flags.most_common():
        lines.append(f"  {name:22s} {count:5d}")
    lines.append("")

    lines.append("--- distinct wordings per family ---")
    for family, texts in sorted(wordings.items()):
        lines.append(f"  {family} ({len(texts)} distinct)")
        for text in sorted(texts)[:6]:
            lines.append(f"      {text}")
    lines.append("")

    lines.append("--- market payload fields ---")
    lines.append("  " + ", ".join(sorted(market_keys)))
    lines.append("")

    if unparsed:
        lines.append(f"!!! {len(unparsed)} UNPARSED — each is a silent forfeit of edge")
        for text in sorted(set(unparsed))[:25]:
            lines.append(f"      {text}")
    else:
        lines.append("all questions parsed")
    return "\n".join(lines)


def _question_text(market: dict[str, Any]) -> str:
    for key in ("question", "title", "name", "description", "text"):
        if market.get(key):
            return str(market[key])
    return ""


def _template(text: str, names: tuple[str, str]) -> str:
    """Replace player names with placeholders so wordings collapse to templates."""
    out = text
    for index, name in enumerate(names):
        if not name:
            continue
        placeholder = "{A}" if index == 0 else "{B}"
        out = re.sub(re.escape(name), placeholder, out, flags=re.I)
        surname = name.split()[-1]
        if len(surname) > 2:
            out = re.sub(rf"\b{re.escape(surname)}\b", placeholder, out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="only capture the first N matches")
    parser.add_argument("--out", default=str(CORPUS_DIR), help="where to write the archive")
    parser.add_argument("--lane", default="honest")
    parser.add_argument("--analyse", default="", help="analyse an existing archive instead")
    args = parser.parse_args(argv)

    if args.analyse:
        corpus = json.loads(Path(args.analyse).read_text(encoding="utf-8"))
        print(analyse(corpus))
        return 0

    try:
        config = Config.from_env(lane=args.lane)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    try:
        corpus = capture(SportsPredictClient(config), limit=args.limit)
    except SportsPredictError as exc:
        print(f"api error: {exc}", file=sys.stderr)
        return 2

    directory = Path(args.out)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"corpus-{stamp}.json"
    path.write_text(json.dumps(corpus, indent=2, sort_keys=True), encoding="utf-8")

    report = analyse(corpus)
    (directory / f"corpus-{stamp}.txt").write_text(report, encoding="utf-8")
    print(report)
    print(f"\narchived {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
