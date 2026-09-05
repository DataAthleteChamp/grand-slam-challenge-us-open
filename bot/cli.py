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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.ledger import Entry, Ledger, MatchRecord
from bot.marketbook import MarketBook
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
    raw_matches: dict[str, dict[str, Any]] = field(default_factory=dict)
    questions: dict[str, str] = field(default_factory=dict)
    submitted: dict[str, Any] | None = None

    @property
    def desired(self) -> dict[str, int]:
        return {q.market_id: q.submission for q in self.priced if q.market_id}


def build_count_book() -> dict[tuple[str, str], float]:
    """Bookmaker head-to-head ace / double-fault lines, newest snapshot wins.

    Reads whatever ``scripts/fetch_bovada.py`` has captured. Absent or stale
    snapshots simply yield an empty book, which prices exactly as before.
    """
    folder = RUNS_DIR.parent / "data/markets/bovada"
    files = sorted(folder.glob("bovada-*.json")) if folder.exists() else []
    if not files:
        return {}
    try:
        records = json.loads(files[-1].read_text())
    except Exception:  # noqa: BLE001 - a bad snapshot must not stop pricing
        return {}
    book: dict[tuple[str, str], float] = {}
    for record in records:
        key = (record.get("family"), record.get("player_key"))
        if key[0] and key[1] and record.get("p_more") is not None:
            book[key] = float(record["p_more"])
    return book


def build_games_book(roster: Roster, event_filter: str = "US Open") -> dict[frozenset[str], float]:
    """Map each match to Pinnacle's expected total games.

    Keyed on the pair of *roster-resolved* names so that Pinnacle's spelling
    ("Botic Van de Zandschulp") and the competition's ("B. Van de Zandschulp")
    land on the same entry.  A pair we cannot resolve is dropped rather than
    guessed: a mis-keyed line would be applied to the wrong match, which is
    worse than having no line at all.
    """
    from bot.markets.pinnacle import PinnacleError, fetch_games_lines

    try:
        lines = fetch_games_lines(event_filter=event_filter)
    except PinnacleError as exc:
        print(f"  ! total-games lines unavailable ({exc})", file=sys.stderr)
        return {}

    book: dict[frozenset[str], float] = {}
    for line in lines:
        a, b = roster.find(line.player_a), roster.find(line.player_b)
        if a is None or b is None or a.name == b.name:
            continue
        book[frozenset({a.name, b.name})] = line.total_games
    return book


def collect(
    client: SportsPredictClient,
    roster: Roster,
    pricer: Pricer,
    *,
    limit: int | None = None,
    match_filter: str = "",
    market_book: MarketBook | None = None,
    games_book: dict[frozenset[str], float] | None = None,
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
    raw_matches: dict[str, dict[str, Any]] = {}
    question_text: dict[str, str] = {}
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
        if games_book:
            line = games_book.get(frozenset({context.player_a.name, context.player_b.name}))
            if line is not None:
                context = replace(context, market_total_games=line)
        contexts[match_id] = context
        rounds[match_id] = context.round_code
        raw_matches[match_id] = match

        try:
            markets = client.list_markets(lobby_id, match_id=match_id)
        except SportsPredictError as exc:
            print(f"  ! {match_id}: markets unavailable ({exc})", file=sys.stderr)
            continue

        # Named market_book, not market: the loop below binds `market` to each
        # market dict, and a shadowed name here would silently feed the previous
        # match's payload to every match after the first.
        ladders = (
            market_book.ladders_for(context.player_a.name, context.player_b.name)
            if market_book
            else None
        )

        match_priced = []
        for market in markets:
            question = _question_text(market)
            if not question:
                continue
            spec = parse_question(question, players=(context.player_a.name, context.player_b.name))
            market_id = str(market.get("id") or "")
            result = pricer.price(spec, context, market_id=market_id, ladders=ladders)
            question_text[market_id] = question
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
        raw_matches=raw_matches,
        questions=question_text,
    )


def record_to_ledger(result: RunResult, ledger: Ledger) -> int:
    """Write every priced question to the ledger before anything is sent.

    Predictions have to be recorded before outcomes are known, or the ledger
    records what we wish we had thought. Writing happens even on a dry run,
    because a forecast we chose not to submit is still evidence about the model.
    """
    written = 0
    for match_id, questions in result.by_match.items():
        if not questions:
            continue
        context = result.contexts.get(match_id)
        record = ledger.load(match_id) or MatchRecord(match_id=match_id)
        raw = result.raw_matches.get(match_id, {})
        record.match_name = str(raw.get("name") or record.match_name)
        record.multiplier = int(raw.get("multiplier") or record.multiplier or 1)
        record.opening_time = str(raw.get("opening_time") or record.opening_time)
        record.closing_time = str(raw.get("closing_time") or record.closing_time)
        record.round_code = result.rounds.get(match_id, record.round_code)
        if context is not None:
            record.player_a = context.player_a.name
            record.player_b = context.player_b.name
            record.draw = {"ATP": "mens", "WTA": "womens"}.get(context.tour, "")
        entries = [
            Entry(
                market_id=q.market_id,
                question=result.questions.get(q.market_id, ""),
                family=q.family.value,
                submission=q.submission,
                probability=q.probability,
                base_rate=q.base_rate,
                model=q.model_probability,
                market=q.market_probability,
                market_weight=q.market_weight,
                confidence=q.confidence,
                lane=q.lane,
                notes=list(q.notes),
            )
            for q in questions
            if q.market_id
        ]
        ledger.save(ledger.record_predictions(record, entries))
        written += len(entries)
    return written


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
        # Later rounds pay 2x and 3x.  A missed question in the quarter-finals
        # forfeits three times the free points a first-round one does, so the
        # weighting is printed rather than left implicit.
        weighted: dict[int, int] = {}
        for match_id, questions in result.by_match.items():
            payload = result.raw_matches.get(match_id) or {}
            multiplier = int(payload.get("multiplier") or 1)
            weighted[multiplier] = weighted.get(multiplier, 0) + len(questions)
        if weighted and set(weighted) != {1}:
            spread = ", ".join(f"{m}x={n}" for m, n in sorted(weighted.items()))
            total = sum(m * n for m, n in weighted.items())
            lines.append(f"boost    {spread}  ({total} multiplier-weighted questions)")
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
    count_book = build_count_book()
    if count_book:
        print(f"counts   {len(count_book)} bookmaker ace/double-fault lines loaded")
    pricer = Pricer(lane=args.lane, count_book=count_book)
    roster = Roster.load()
    if not roster.profiles:
        print(
            "no player data found; run scripts/build_player_data.py first. "
            "Pricing without ratings makes every match a tour average.",
            file=sys.stderr,
        )
        return 2

    market_book = None
    if not args.no_market:
        market_book = MarketBook.fetch()
        if len(market_book):
            print(f"market   {len(market_book)} de-vigged quotes loaded")
        else:
            print("market   NO QUOTES — match winner will price from the model alone")
        for note in market_book.skipped:
            print(f"  ! {note}", file=sys.stderr)

    games_book: dict[frozenset[str], float] = {}
    if not args.no_market:
        games_book = build_games_book(roster)
        print(f"games    {len(games_book)} total-games lines loaded")

    started = time.monotonic()
    try:
        result = collect(
            client,
            roster,
            pricer,
            limit=args.limit,
            match_filter=args.match,
            market_book=market_book,
            games_book=games_book,
        )
    except SportsPredictError as exc:
        print(f"api error: {exc}", file=sys.stderr)
        return 2

    path = archive(result, Path(args.out))
    written = record_to_ledger(result, Ledger())
    print(summarise(result))
    print(f"ledger   {written} predictions recorded before submission")
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
    lobby_id = str(result.lobby["id"])
    print(f"\nsubmitting {len(desired)} predictions to lobby {lobby_id}...")

    # upsert_batch is the write path. reconcile is read-only: it diffs desired
    # against what the server actually holds. Calling only reconcile printed a
    # convincing "submitting 320 predictions..." and sent nothing at all, and
    # the test suite passed because the fake client's reconcile recorded the
    # batch. A mock that does what you wish the real method did turns a green
    # suite into evidence of nothing.
    written = client.upsert_batch(lobby_id, desired)
    print(json.dumps(written, indent=2, sort_keys=True))

    # Verify by reading state back: a request that times out may still have
    # succeeded server-side, so the only trustworthy check is the server's copy.
    drift = client.reconcile(lobby_id, desired)
    result.submitted = {"written": written, "drift": drift}
    if drift.get("in_sync"):
        print(f"verified: all {len(desired)} predictions match the server")
    else:
        missing, mismatched = drift.get("missing", []), drift.get("mismatched", {})
        print(
            f"NOT IN SYNC: {len(missing)} missing, {len(mismatched)} mismatched",
            file=sys.stderr,
        )
        print(json.dumps(drift, indent=2, sort_keys=True), file=sys.stderr)

    archive(result, Path(args.out))
    return 0 if drift.get("in_sync") else 1


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
    parser.add_argument(
        "--no-market",
        action="store_true",
        help="skip bookmaker odds; match winner then prices from the model alone",
    )
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
