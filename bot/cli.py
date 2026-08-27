"""The end-to-end run: event to matches to markets to numbers to submission.

Every other module in this package is a component with tests.  This is the only
place they are wired together, and therefore the only place that can actually
lose points.  Three properties matter more than anything else here:

1. **Dry run is the default.**  Submitting requires ``--submit`` explicitly.
   There is no configuration setting that makes submission the default, because
   the failure mode of an accidental submission is unrecoverable and the failure
   mode of an accidental dry run is that you run it again.
2. **Validation blocks.**  If ``validate_batch`` returns a BLOCK finding, nothing
   is sent, regardless of flags, unless ``--force`` is passed and the reason is
   printed.
3. **Everything is archived first.**  Raw markets and priced output are written
   to disk before any network write, so a bad run can always be reconstructed.

The submission itself goes through ``reconcile``, which diffs against what is
already on the server, so re-running is idempotent rather than duplicative.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.markets.parser import Family, parse_question, reset_unparsed, unparsed_report
from bot.pricer import MatchContext, PricedQuestion, Pricer
from bot.roster import Roster, players_from_match
from bot.sportspredict.api import SportsPredictClient, SportsPredictError
from bot.sportspredict.config import Config, ConfigError
from bot.validate import Severity, validate_batch

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"


@dataclass
class RunResult:
    """Everything one run produced, for archiving and for the tests."""

    event: dict[str, Any]
    lobby: dict[str, Any]
    priced: list[PricedQuestion]
    by_match: dict[str, list[PricedQuestion]]
    rounds: dict[str, str]
    contexts: dict[str, MatchContext]
    unparsed: list[str]
    roster_misses: list[tuple[str, int]]
    submitted: dict[str, Any] | None = None

    @property
    def desired(self) -> dict[str, int]:
        return {q.market_id: q.submission for q in self.priced if q.market_id}


def collect(
    client: SportsPredictClient,
    roster: Roster,
    pricer: Pricer,
    *,
    limit: int | None = None,
    match_filter: str = "",
) -> RunResult:
    """Walk the event and price every open market.

    Markets are fetched per match rather than in one call: the unfiltered
    response for a full slam is hundreds of kilobytes, and per-match scoping is
    what lets us attach the right context to each question.
    """
    reset_unparsed()
    event, lobby = client.bootstrap()
    event_id, lobby_id = str(event["id"]), str(lobby["id"])

    matches = client.list_matches(event_id=event_id, lobby_id=lobby_id)
    if match_filter:
        needle = match_filter.lower()
        matches = [m for m in matches if needle in json.dumps(m).lower()]
    if limit is not None:
        matches = matches[:limit]

    priced: list[PricedQuestion] = []
    by_match: dict[str, list[PricedQuestion]] = {}
    rounds: dict[str, str] = {}
    contexts: dict[str, MatchContext] = {}

    for match in matches:
        match_id = str(match.get("id") or "")
        if not match_id:
            continue
        name_a, name_b = players_from_match(match)
        context = roster.context(
            name_a,
            name_b,
            round_code=match.get("round") or match.get("round_name"),
            tour=match.get("tour") or match.get("category"),
        )
        contexts[match_id] = context
        rounds[match_id] = context.round_code

        try:
            markets = client.list_markets(lobby_id, match_id=match_id)
        except SportsPredictError as exc:
            print(f"  ! {match_id}: markets unavailable ({exc})", file=sys.stderr)
            continue

        match_priced = []
        for market in markets:
            question = _question_text(market)
            if not question:
                continue
            spec = parse_question(question, players=(context.player_a.name, context.player_b.name))
            result = pricer.price(spec, context, market_id=str(market.get("id") or ""))
            match_priced.append(result)

        by_match[match_id] = match_priced
        priced.extend(match_priced)

    return RunResult(
        event=event,
        lobby=lobby,
        priced=priced,
        by_match=by_match,
        rounds=rounds,
        contexts=contexts,
        unparsed=unparsed_report(),
        roster_misses=roster.miss_report,
    )


def _question_text(market: dict[str, Any]) -> str:
    for key in ("question", "title", "name", "description", "text"):
        value = market.get(key)
        if value:
            return str(value)
    return ""


def archive(result: RunResult, directory: Path) -> Path:
    """Write the full run to disk before anything is sent."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"run-{stamp}.json"
    payload = {
        "generated_at": stamp,
        "event": {"id": result.event.get("id"), "title": result.event.get("title")},
        "lobby": {"id": result.lobby.get("id")},
        "n_questions": len(result.priced),
        "n_matches": len(result.by_match),
        "unparsed": result.unparsed,
        "roster_misses": result.roster_misses,
        "matches": {
            match_id: {
                "round": result.rounds.get(match_id),
                "tour": context.tour,
                "player_a": context.player_a.name,
                "player_b": context.player_b.name,
                "rated_a": context.player_a.has_ratings,
                "rated_b": context.player_b.has_ratings,
                "questions": [q.as_dict() for q in result.by_match.get(match_id, [])],
            }
            for match_id, context in result.contexts.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def summarise(result: RunResult) -> str:
    """A short human read of what the run produced."""
    lines = [
        f"event    {result.event.get('title', '?')}",
        f"matches  {len(result.by_match)}",
        f"markets  {len(result.priced)}",
    ]
    if result.priced:
        by_family: dict[Family, int] = {}
        for question in result.priced:
            by_family[question.family] = by_family.get(question.family, 0) + 1
        families = sorted(by_family.items(), key=lambda kv: -kv[1])
        lines.append("families " + ", ".join(f"{f.value}={n}" for f, n in families[:8]))
        values = [q.submission for q in result.priced]
        lines.append(f"range    {min(values)}-{max(values)}, mean {sum(values) / len(values):.1f}")
    unrated = sum(
        1
        for c in result.contexts.values()
        if not c.player_a.has_ratings or not c.player_b.has_ratings
    )
    if unrated:
        lines.append(f"UNRATED  {unrated} matches have an unrated player, priced at tour average")
    if result.roster_misses:
        worst = ", ".join(f"{n} x{c}" for n, c in result.roster_misses[:5])
        lines.append(f"MISSES   {worst}")
    if result.unparsed:
        lines.append(f"UNPARSED {len(result.unparsed)}: {result.unparsed[:3]}")
    return "\n".join(lines)


def run(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = Config.from_env(lane=args.lane)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    client = SportsPredictClient(config)
    pricer = Pricer(lane=args.lane)
    roster = Roster.load()
    if not roster.profiles:
        print(
            "no player data found; run scripts/build_player_data.py first. "
            "Pricing without ratings makes every match a tour average.",
            file=sys.stderr,
        )
        return 2

    started = time.monotonic()
    try:
        result = collect(client, roster, pricer, limit=args.limit, match_filter=args.match)
    except SportsPredictError as exc:
        print(f"api error: {exc}", file=sys.stderr)
        return 2

    path = archive(result, Path(args.out))
    print(summarise(result))
    print(f"archived {path}")
    print(f"elapsed  {time.monotonic() - started:.1f}s")

    if not result.priced:
        print("nothing priced; no markets open yet", file=sys.stderr)
        return 1

    previous = None
    if args.diff_against_server:
        previous = {
            str(p.get("market_id")): int(p.get("probability", 0))
            for p in client.list_predictions(str(result.lobby["id"]))
            if p.get("market_id")
        }

    report = validate_batch(
        result.priced,
        previous=previous,
        questions_by_match=result.by_match,
        rounds_by_match=result.rounds,
    )
    print()
    print(report.render())

    if report.blocked and not args.force:
        print("\nBLOCKED: nothing submitted. Fix the findings or pass --force.", file=sys.stderr)
        return 1
    if report.blocked:
        print("\n--force: submitting despite BLOCK findings above.", file=sys.stderr)

    if not args.submit:
        print("\ndry run: pass --submit to send these to the API.")
        return 0

    desired = result.desired
    print(f"\nsubmitting {len(desired)} predictions to lobby {result.lobby['id']}...")
    result.submitted = client.reconcile(str(result.lobby["id"]), desired)
    print(json.dumps(result.submitted, indent=2, sort_keys=True))
    archive(result, Path(args.out))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m bot",
        description="Price every open Grand Slam Challenge market and, "
        "with --submit, send the numbers.",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="actually send predictions; without this the run is a dry run",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="submit even if validation raised BLOCK findings",
    )
    parser.add_argument("--lane", default="honest", help="bot lane (honest, scenario)")
    parser.add_argument("--limit", type=int, default=None, help="only price the first N matches")
    parser.add_argument("--match", default="", help="only matches whose payload contains this text")
    parser.add_argument("--out", default=str(RUNS_DIR), help="directory for run archives")
    parser.add_argument(
        "--diff-against-server",
        action="store_true",
        help="fetch existing predictions and report drift before submitting",
    )
    args = parser.parse_args(argv)
    if args.force and not args.submit:
        parser.error("--force only makes sense with --submit")
    return args


def main() -> int:
    try:
        return run()
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


__all__ = ["RunResult", "collect", "archive", "summarise", "run", "main", "Severity"]
