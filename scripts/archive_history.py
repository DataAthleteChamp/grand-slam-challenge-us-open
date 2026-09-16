"""Archive settled questions during an event.

    PYTHONPATH=. .venv/bin/python scripts/archive_history.py

For the completed 2026 event, use ``scripts/archive_final.py`` instead. It
captures both lanes, reconstructs round weights independently, records void
markets, and writes checksums.

Run after each round.  Two artefacts are written:

``data/history/raw/<lane>-<timestamp>.json``
    The API response verbatim, append-only, never rewritten.  Storage is cheap
    and re-derivation is not: if a field turns out to matter later, it has to
    already be on disk.

``data/history/incremental-settled.csv``
    One tidy row per settled question per lane, joined to the match metadata and
    to our own pricing internals.  This is the file to analyse.

The join to our internals is what makes the archive worth more than the API
response.  ``crowd_value`` tells us *that* we lost a question; ``base_rate``,
``model``, ``market`` and ``confidence`` tell us *why* - whether the model was
wrong, the market disagreed and we ignored it, or we shrank a good answer toward
a bad anchor.  Only the second kind of record can change a decision.

Crowd values are visible only after settlement: an open prediction returns
nothing for ``crowd_value`` or ``current_value``.  So this archive is the single
source of truth about what the field believes, and every round it goes
unrecorded is information that cannot be recovered.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.ledger import Ledger  # noqa: E402
from bot.markets.parser import parse_question  # noqa: E402
from bot.sportspredict.api import SportsPredictClient, SportsPredictError  # noqa: E402
from bot.sportspredict.config import Config, ConfigError  # noqa: E402

HISTORY = Path(__file__).resolve().parent.parent / "data" / "history"
RAW = HISTORY / "raw"
TIDY = HISTORY / "incremental-settled.csv"

LANES = ("honest", "scenario")

#: Manual website entries are scored under the user account rather than a bot
#: API key, and the API exposes no user-level endpoint (/users/me returns 404).
#: They therefore have to be recorded by hand in data/manual/entries.csv as
#: ``market_id,submitted``. Crowd consensus and outcome recover only the
#: controllable consensus-skill term. Exact API RBP also contains field variance
#: and cannot be reconstructed for the manual account from these values alone.
MANUAL_FILE = HISTORY.parent / "manual" / "entries.csv"


def load_manual() -> dict[str, int]:
    if not MANUAL_FILE.exists():
        return {}
    out = {}
    with MANUAL_FILE.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                out[row["market_id"].strip()] = int(row["submitted"])
            except (KeyError, ValueError, AttributeError):
                continue
    return out


def manual_rows(results: list[dict], matches: list[dict], ledger: Ledger) -> list[dict]:
    """Estimate the manual entry's consensus-skill term for known values."""
    entered = load_manual()
    if not entered:
        return []
    rows = []
    for row in build_rows("manual", results, matches, ledger):
        submitted = entered.get(row["market_id"])
        if submitted is None:
            continue
        p = submitted / 100.0
        outcome = float(row["outcome"])
        brier = (p - outcome) ** 2
        rbp = (float(row["crowd_brier"]) - brier) * 100.0
        row["submitted"] = round(p, 4)
        row["brier"] = round(brier, 6)
        row["rbp"] = round(rbp, 3)
        row["weighted_rbp"] = round(rbp * int(row["multiplier"]), 3)
        row["outcome_tag"] = (
            "beat_crowd" if rbp > 0 else ("lost_to_crowd" if rbp < 0 else "tied_crowd")
        )
        # These describe the bot's reasoning, not the manual entry's.
        for field in ("base_rate", "model", "market", "market_weight", "confidence", "notes"):
            row[field] = ""
        row["notes"] = "manual consensus-skill estimate; exact API RBP unavailable"
        rows.append(row)
    return rows


FIELDS = [
    # identity
    "lane",
    "market_id",
    "match_id",
    "match_name",
    "draw",
    "round_code",
    "multiplier",
    "player_a",
    "player_b",
    # the question
    "question",
    "family",
    "threshold",
    "subject",
    # what we said and what happened
    "submitted",
    "crowd",
    "outcome",
    "outcome_tag",
    "brier",
    "crowd_brier",
    "rbp",
    "weighted_rbp",
    # why we said it - our own internals, joined from the ledger
    "base_rate",
    "model",
    "market",
    "market_weight",
    "confidence",
    "notes",
    # provenance
    "prediction_created_at",
    "settled_at",
    "archived_at",
]


def collect(
    lane: str,
    *,
    event_id: str | None = None,
    lobby_id: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return ``(settled results, matches)`` for one bot."""
    try:
        client = SportsPredictClient(Config.from_env(lane=lane))
    except ConfigError as exc:
        print(f"  ! {lane}: {exc}", file=sys.stderr)
        return [], []
    try:
        if bool(event_id) != bool(lobby_id):
            raise ValueError("event_id and lobby_id must be supplied together")
        if event_id and lobby_id:
            results = client.list_results(lobby_id)
            matches = client.list_matches(event_id=event_id, lobby_id=lobby_id)
        else:
            event, lobby = client.bootstrap()
            results = client.list_results(str(lobby["id"]))
            matches = client.list_matches(event_id=str(event["id"]), lobby_id=str(lobby["id"]))
    except SportsPredictError as exc:
        print(f"  ! {lane}: {exc}", file=sys.stderr)
        return [], []
    return [r for r in results if r.get("market_status") == "settled"], matches


def _number(text: str) -> str:
    """The threshold a question carries, if any."""
    found = re.search(r"(\d+(?:\.\d+)?)", text or "")
    return found.group(1) if found else ""


def build_rows(lane: str, results: list[dict], matches: list[dict], ledger: Ledger) -> list[dict]:
    by_match = {str(m.get("id")): m for m in matches}
    stamp = datetime.now(UTC).isoformat()

    # Our own pricing internals, keyed by market id.
    internals: dict[str, dict] = {}
    for record in ledger.all_records():
        for entry in record.entries:
            if entry.lane != lane:
                continue
            internals[entry.market_id] = {
                "match_name": record.match_name,
                "multiplier": record.multiplier,
                "base_rate": entry.base_rate,
                "model": entry.model,
                "market": entry.market,
                "market_weight": entry.market_weight,
                "confidence": entry.confidence,
                "notes": " | ".join(entry.notes),
                "draw": record.draw,
                "round_code": record.round_code,
                "player_a": record.player_a,
                "player_b": record.player_b,
            }

    rows = []
    for result in results:
        market_id = str(result.get("market_id") or "")
        match_id = str(result.get("match_id") or "")
        match = by_match.get(match_id, {})
        mine = internals.get(market_id, {})
        question = str(result.get("question") or "")
        spec = parse_question(question)

        submitted = float(result.get("probability_submitted") or 0) / 100.0
        crowd = float(result.get("crowd_value") or 0) / 100.0
        outcome = float(result.get("result_value") or 0) / 100.0
        # Settled matches drop off the /matches endpoint, so fall back to what
        # we recorded at submission time rather than losing the join.
        multiplier = int(match.get("multiplier") or mine.get("multiplier") or 1)
        rbp = float(result.get("rbp") or 0.0)

        rows.append(
            {
                "lane": lane,
                "market_id": market_id,
                "match_id": match_id,
                "match_name": match.get("name") or mine.get("match_name", ""),
                "draw": mine.get("draw", ""),
                "round_code": mine.get("round_code", ""),
                "multiplier": multiplier,
                "player_a": mine.get("player_a", ""),
                "player_b": mine.get("player_b", ""),
                "question": question,
                "family": spec.family.value,
                "threshold": spec.threshold if spec.threshold is not None else _number(question),
                "subject": spec.subject or "",
                "submitted": round(submitted, 4),
                "crowd": round(crowd, 4),
                "outcome": int(outcome),
                "outcome_tag": result.get("outcome_tag", ""),
                "brier": result.get("brier_score"),
                "crowd_brier": round((crowd - outcome) ** 2, 6),
                "rbp": round(rbp, 3),
                # API RBP already includes the stage multiplier.
                "weighted_rbp": round(rbp, 3),
                "base_rate": _round(mine.get("base_rate")),
                "model": _round(mine.get("model")),
                "market": _round(mine.get("market")),
                "market_weight": _round(mine.get("market_weight"), 3),
                "confidence": _round(mine.get("confidence"), 3),
                "notes": mine.get("notes", ""),
                "prediction_created_at": result.get("created_date", ""),
                "settled_at": "",
                "archived_at": stamp,
            }
        )
    return rows


def _round(value, places: int = 4):
    if value is None:
        return ""
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return ""


def merge(rows: list[dict], path: Path = TIDY) -> tuple[int, int]:
    """Merge into the tidy table, keyed by ``(lane, market_id)``.

    Re-running is idempotent and never loses a row: a question settled in an
    earlier round stays even once the API stops returning it.
    """
    existing: dict[tuple[str, str], dict] = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                existing[(row.get("lane", ""), row.get("market_id", ""))] = row

    before = len(existing)
    for row in rows:
        existing[(row["lane"], row["market_id"])] = row

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for key in sorted(existing):
            writer.writerow(existing[key])
    return before, len(existing)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(TIDY))
    parser.add_argument("--event-id")
    parser.add_argument("--lobby-id")
    args = parser.parse_args(argv)
    if bool(args.event_id) != bool(args.lobby_id):
        parser.error("--event-id and --lobby-id must be supplied together")

    ledger = Ledger()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    RAW.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    for lane in LANES:
        results, matches = collect(
            lane,
            event_id=args.event_id,
            lobby_id=args.lobby_id,
        )
        if not results:
            print(f"{lane}: nothing settled")
            continue
        (RAW / f"{lane}-{stamp}.json").write_text(
            json.dumps({"results": results, "matches": matches}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        rows = build_rows(lane, results, matches, ledger)
        all_rows.extend(rows)
        joined = sum(1 for r in rows if r["base_rate"] != "")
        print(f"{lane}: {len(rows)} settled, {joined} joined to our pricing internals")

    # Score the manual entry from the same settled outcomes.
    if all_rows:
        results, matches = collect(
            LANES[0],
            event_id=args.event_id,
            lobby_id=args.lobby_id,
        )
        manual = manual_rows(results, matches, ledger)
        if manual:
            all_rows.extend(manual)
            print(f"manual: {len(manual)} scored from data/manual/entries.csv")
        else:
            print("manual: no entries recorded (data/manual/entries.csv)")

    if not all_rows:
        return 0

    before, after = merge(all_rows, Path(args.out))
    print(f"\ntidy table: {before} -> {after} rows at {args.out}")
    print(f"raw archives in {RAW}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
