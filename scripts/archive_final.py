"""Freeze the completed 2026 Grand Slam Challenge into a canonical private archive.

The finished event no longer appears in the active-event endpoint, so this
script requires explicit event and lobby IDs. API result rows are authoritative
for submissions, outcomes, Brier scores, and RBP. Run files are used only to
recover model provenance and match names.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.markets.parser import parse_question  # noqa: E402
from bot.sportspredict.api import SportsPredictClient  # noqa: E402
from bot.sportspredict.config import Config  # noqa: E402

DEFAULT_OUTPUT = ROOT / "data" / "history" / "final"
DEFAULT_RUNS = ROOT / "runs"
DEFAULT_LEDGER = ROOT / "results" / "matches"
DEFAULT_OVERRIDES = ROOT / "data" / "markets" / "final-overrides-20260913.json"
DEFAULT_PLAYERS = ROOT / "data" / "players"
DEFAULT_MANUAL = ROOT / "data" / "manual" / "entries.csv"
LEADERBOARD_URL = "https://api.sportspredict.com/api/probability/leaderboard"
RANK_URL = "https://api.sportspredict.com/api/probability/rank"
LANES = ("honest", "scenario")
ENTRY_LABELS = {"honest": "Quant_Researcher", "scenario": "T-shirt Hunter"}

CANONICAL_FIELDS = [
    "lane",
    "entry_label",
    "market_id",
    "match_id",
    "match_name",
    "player_a",
    "player_b",
    "draw",
    "round_code",
    "multiplier",
    "question",
    "family",
    "threshold",
    "subject",
    "submitted",
    "model_submission",
    "outcome",
    "crowd",
    "brier",
    "crowd_consensus_brier",
    "consensus_skill_rbp",
    "field_variance_rbp",
    "api_rbp",
    "unweighted_api_rbp",
    "outcome_tag",
    "prediction_created_at",
    "provenance",
    "source_artifact",
    "source_generated_at",
    "base_rate",
    "model",
    "market",
    "market_weight",
    "model_probability",
    "confidence",
    "notes",
]

VOID_FIELDS = [
    "market_id",
    "match_id",
    "match_name",
    "player_a",
    "player_b",
    "question",
    "status",
    "last_seen_at",
    "last_seen_run",
    "lane_submissions",
]

MANUAL_FIELDS = [
    "market_id",
    "match_id",
    "match_name",
    "question",
    "round_code",
    "multiplier",
    "submitted",
    "outcome",
    "crowd",
    "brier",
    "crowd_consensus_brier",
    "consensus_skill_rbp",
    "estimated_field_variance_rbp",
    "estimated_api_rbp",
    "status",
    "source_artifact",
]


def fetch_results(lane: str, lobby_id: str) -> list[dict[str, Any]]:
    client = SportsPredictClient(Config.from_env(lane=lane))
    rows = client.list_results(lobby_id)
    settled = [row for row in rows if row.get("market_status") == "settled"]
    if len(settled) != len(rows):
        raise ValueError(f"{lane}: expected only settled results, got {len(settled)}/{len(rows)}")
    return settled


def infer_match_multipliers(
    honest: list[dict[str, Any]], scenario: list[dict[str, Any]]
) -> dict[str, int]:
    """Recover 1x/2x/3x weights from exact lane score differences.

    The field-average term is identical for both entries, so subtracting their
    API RBP leaves ``multiplier * (scenario_brier - honest_brier) * 100``.
    """
    scenario_by_market = {str(row["market_id"]): row for row in scenario}
    candidates: dict[str, list[int]] = defaultdict(list)

    for row in honest:
        market_id = str(row["market_id"])
        other = scenario_by_market.get(market_id)
        if other is None:
            raise ValueError(f"scenario lane is missing market {market_id}")
        brier_delta = float(other["brier_score"]) - float(row["brier_score"])
        if abs(brier_delta) <= 1e-12:
            continue
        rbp_delta = float(row["rbp"]) - float(other["rbp"])
        ratio = rbp_delta / (100.0 * brier_delta)
        multiplier = round(ratio)
        if multiplier not in {1, 2, 3} or abs(ratio - multiplier) > 0.03:
            raise ValueError(f"cannot infer multiplier for {market_id}: ratio={ratio:.6f}")
        candidates[str(row["match_id"])].append(multiplier)

    match_ids = {str(row["match_id"]) for row in honest}
    inferred: dict[str, int] = {}
    for match_id in sorted(match_ids):
        values = candidates.get(match_id, [])
        if not values:
            raise ValueError(f"cannot infer multiplier for match {match_id}")
        counts = Counter(values)
        if len(counts) != 1:
            raise ValueError(f"conflicting multipliers for match {match_id}: {dict(counts)}")
        inferred[match_id] = values[0]
    return inferred


def infer_round(multiplier: int, prediction_created_at: str) -> str:
    """Recover the event round from its known market-opening sequence."""
    if multiplier == 1:
        return "R128"
    opened = date.fromisoformat(prediction_created_at[:10])
    if multiplier == 2:
        return "R32" if opened <= date(2026, 9, 4) else "R16"
    if opened <= date(2026, 9, 8):
        return "QF"
    if opened <= date(2026, 9, 10):
        return "SF"
    return "F"


def load_run_index(
    directory: Path,
) -> tuple[
    dict[tuple[str, str], list[dict[str, Any]]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    candidates: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    matches: dict[str, dict[str, Any]] = {}
    observed: dict[str, dict[str, Any]] = {}

    for path in sorted(directory.glob("run-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        generated_at = str(payload.get("generated_at") or path.stem.removeprefix("run-"))
        for match_id, match in payload.get("matches", {}).items():
            match_id = str(match_id)
            matches[match_id] = {
                "player_a": str(match.get("player_a") or ""),
                "player_b": str(match.get("player_b") or ""),
            }
            for question in match.get("questions", []):
                market_id = str(question.get("market_id") or "")
                lane = str(question.get("lane") or "")
                if not market_id or lane not in LANES:
                    continue
                item = {
                    **question,
                    "match_id": match_id,
                    "player_a": matches[match_id]["player_a"],
                    "player_b": matches[match_id]["player_b"],
                    "source_artifact": str(path.relative_to(ROOT)),
                    "source_generated_at": generated_at,
                }
                candidates[(lane, market_id)].append(item)
                observed[market_id] = item
    return candidates, matches, observed


def load_ledger_index(directory: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload.get("entries", []):
            market_id = str(entry.get("market_id") or "")
            if not market_id:
                continue
            index[market_id] = {
                "question": str(entry.get("question") or ""),
                "draw": str(payload.get("draw") or ""),
                "match_name": str(payload.get("match_name") or ""),
            }
    return index


def load_player_tours(directory: Path) -> dict[str, str]:
    tours: dict[str, str] = {}
    for tour in ("atp", "wta"):
        path = directory / f"{tour}_players.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                name = str(row.get("player") or "").strip()
                if name:
                    tours[name] = tour.upper()
    return tours


def infer_draw(player_a: str, player_b: str, tours: dict[str, str]) -> str:
    known = {tours[name] for name in (player_a, player_b) if name in tours}
    if len(known) > 1:
        raise ValueError(f"players resolve to different tours: {player_a!r}, {player_b!r}")
    if not known:
        return ""
    return "mens" if known == {"ATP"} else "womens"


def load_overrides(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row["market_id"]): {
            **row,
            "captured_at": payload.get("captured_at", ""),
        }
        for row in payload.get("submitted_overrides", [])
        if row.get("market_id")
    }


def build_canonical_rows(
    results_by_lane: dict[str, list[dict[str, Any]]],
    multipliers: dict[str, int],
    run_candidates: dict[tuple[str, str], list[dict[str, Any]]],
    run_matches: dict[str, dict[str, Any]],
    ledger_index: dict[str, dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
    player_tours: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lane in LANES:
        for result in results_by_lane[lane]:
            market_id = str(result["market_id"])
            match_id = str(result["match_id"])
            submitted = int(result["probability_submitted"])
            candidates = run_candidates.get((lane, market_id), [])
            latest = candidates[-1] if candidates else {}
            exact = [
                candidate for candidate in candidates if int(candidate["submission"]) == submitted
            ]
            source = exact[-1] if exact else latest

            override = overrides.get(market_id)
            if exact:
                provenance = "exact_run_match"
            elif override and int(override["probability"]) == submitted:
                provenance = "documented_override"
            else:
                provenance = "unmatched_submission"

            players = run_matches.get(match_id, {})
            ledger = ledger_index.get(market_id, {})
            player_a = str(players.get("player_a") or "")
            player_b = str(players.get("player_b") or "")
            match_name = (
                f"{player_a} vs {player_b}"
                if player_a and player_b
                else str(ledger.get("match_name") or "")
            )
            question = str(result.get("question") or "")
            spec = parse_question(question, players=(player_a, player_b))
            outcome = int(round(float(result["result_value"]) / 100.0))
            crowd = float(result["crowd_value"]) / 100.0
            brier = float(result["brier_score"])
            api_rbp = float(result["rbp"])
            multiplier = multipliers[match_id]
            consensus_brier = (crowd - outcome) ** 2
            consensus_skill = multiplier * (consensus_brier - brier) * 100.0
            field_variance = api_rbp - consensus_skill
            predicted_at = str(result.get("created_date") or "")

            expected_brier = (submitted / 100.0 - outcome) ** 2
            if abs(brier - expected_brier) > 1e-9:
                raise ValueError(f"API Brier mismatch for {lane}/{market_id}")
            if field_variance < -0.011:
                raise ValueError(
                    f"negative field variance for {lane}/{market_id}: {field_variance}"
                )

            rows.append(
                {
                    "lane": lane,
                    "entry_label": ENTRY_LABELS[lane],
                    "market_id": market_id,
                    "match_id": match_id,
                    "match_name": match_name,
                    "player_a": player_a,
                    "player_b": player_b,
                    "draw": ledger.get("draw") or infer_draw(player_a, player_b, player_tours),
                    "round_code": infer_round(multiplier, predicted_at),
                    "multiplier": multiplier,
                    "question": question,
                    "family": spec.family.value,
                    "threshold": spec.threshold if spec.threshold is not None else "",
                    "subject": spec.subject or "",
                    "submitted": submitted,
                    "model_submission": source.get("submission", ""),
                    "outcome": outcome,
                    "crowd": _round(crowd),
                    "brier": _round(brier, 6),
                    "crowd_consensus_brier": _round(consensus_brier, 6),
                    "consensus_skill_rbp": _round(consensus_skill, 4),
                    "field_variance_rbp": _round(max(0.0, field_variance), 4),
                    "api_rbp": _round(api_rbp, 4),
                    "unweighted_api_rbp": _round(api_rbp / multiplier, 4),
                    "outcome_tag": result.get("outcome_tag", ""),
                    "prediction_created_at": predicted_at,
                    "provenance": provenance,
                    "source_artifact": (
                        str(DEFAULT_OVERRIDES.relative_to(ROOT))
                        if provenance == "documented_override"
                        else source.get("source_artifact", "")
                    ),
                    "source_generated_at": (
                        overrides[market_id].get("captured_at", "")
                        if provenance == "documented_override"
                        else source.get("source_generated_at", "")
                    ),
                    "base_rate": source.get("base_rate", ""),
                    "model": source.get("model", ""),
                    "market": source.get("market", ""),
                    "market_weight": source.get("market_weight", ""),
                    "model_probability": source.get("probability", ""),
                    "confidence": source.get("confidence", ""),
                    "notes": json.dumps(source.get("notes", []), ensure_ascii=True),
                }
            )
    return sorted(rows, key=lambda row: (row["lane"], row["match_id"], row["market_id"]))


def build_void_rows(
    observed: dict[str, dict[str, Any]],
    final_market_ids: set[str],
    run_candidates: dict[tuple[str, str], list[dict[str, Any]]],
    ledger_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for market_id in sorted(set(observed) - final_market_ids):
        latest = observed[market_id]
        player_a = str(latest.get("player_a") or "")
        player_b = str(latest.get("player_b") or "")
        submissions = {
            lane: run_candidates[(lane, market_id)][-1]["submission"]
            for lane in LANES
            if run_candidates.get((lane, market_id))
        }
        rows.append(
            {
                "market_id": market_id,
                "match_id": latest.get("match_id", ""),
                "match_name": (
                    f"{player_a} vs {player_b}"
                    if player_a and player_b
                    else ledger_index.get(market_id, {}).get("match_name", "")
                ),
                "player_a": player_a,
                "player_b": player_b,
                "question": ledger_index.get(market_id, {}).get("question", ""),
                "status": "deleted_or_void",
                "last_seen_at": latest.get("source_generated_at", ""),
                "last_seen_run": latest.get("source_artifact", ""),
                "lane_submissions": json.dumps(submissions, sort_keys=True),
            }
        )
    return rows


def build_manual_rows(path: Path, canonical: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    reference = {str(row["market_id"]): row for row in canonical if row["lane"] == "honest"}
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for recorded in csv.DictReader(handle):
            market_id = str(recorded.get("market_id") or "")
            settled = reference.get(market_id)
            if settled is None:
                raise ValueError(f"manual market {market_id} is absent from final results")
            submitted = int(recorded["submitted"])
            outcome = int(settled["outcome"])
            brier = (submitted / 100.0 - outcome) ** 2
            consensus_brier = float(settled["crowd_consensus_brier"])
            multiplier = int(settled["multiplier"])
            consensus_skill = multiplier * (consensus_brier - brier) * 100.0
            field_variance = float(settled["field_variance_rbp"])
            rows.append(
                {
                    "market_id": market_id,
                    "match_id": settled["match_id"],
                    "match_name": settled["match_name"],
                    "question": settled["question"],
                    "round_code": settled["round_code"],
                    "multiplier": multiplier,
                    "submitted": submitted,
                    "outcome": outcome,
                    "crowd": settled["crowd"],
                    "brier": _round(brier, 6),
                    "crowd_consensus_brier": _round(consensus_brier, 6),
                    "consensus_skill_rbp": _round(consensus_skill, 4),
                    "estimated_field_variance_rbp": _round(field_variance, 4),
                    "estimated_api_rbp": _round(consensus_skill + field_variance, 4),
                    "status": "recorded_value_not_server_verified",
                    "source_artifact": _display_path(path),
                }
            )
    return sorted(rows, key=lambda row: (row["match_id"], row["market_id"]))


def fetch_leaderboard(event_id: str, entry_type: str) -> tuple[int, list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    total = 0
    for skip in range(0, 300, 100):
        payload = _post_json(
            LEADERBOARD_URL,
            {
                "eventId": event_id,
                "skip": skip,
                "limit": 100,
                "sortDirection": "desc",
                "type": entry_type,
                "leaderboardType": "global",
            },
        )
        total = int(payload["total"])
        items.extend(payload["items"])
        if len(items) >= total:
            break
    if len(items) != total:
        raise ValueError(f"incomplete {entry_type} leaderboard: {len(items)}/{total}")
    return total, items


def build_leaderboard_summary(
    event_id: str,
    lobby_id: str,
    entry_ids: dict[str, str],
    captured_at: str,
) -> dict[str, Any]:
    overall_total, overall = fetch_leaderboard(event_id, "all")
    bot_total, bots = fetch_leaderboard(event_id, "bots")
    player_total, players = fetch_leaderboard(event_id, "players")
    by_type = {
        "honest": (bot_total, bots),
        "scenario": (bot_total, bots),
        "manual": (player_total, players),
    }

    entries = []
    for role in ("honest", "scenario", "manual"):
        user_id = entry_ids[role]
        overall_row = _one(row for row in overall if str(row.get("user_id")) == user_id)
        category_total, category_rows = by_type[role]
        category_row = _one(row for row in category_rows if str(row.get("user_id")) == user_id)
        rank = _post_json(
            RANK_URL,
            {"eventId": event_id, "lobbyId": lobby_id, "userId": user_id},
        )
        entries.append(
            {
                "role": role,
                "label": overall_row.get("label") or "manual",
                "score": rank["score"],
                "overall_rank": overall_row["rank"],
                "overall_total": overall_total,
                "category": "bots" if role != "manual" else "players",
                "category_rank": category_row["rank"],
                "category_total": category_total,
                "settled_predictions": rank["total"],
                "smart_rating": rank["smart_rating"],
            }
        )

    winner = overall[0]
    return {
        "schema_version": 1,
        "captured_at": captured_at,
        "source": LEADERBOARD_URL,
        "event_id": event_id,
        "lobby_id": lobby_id,
        "winner": {
            "label": winner.get("label") or winner.get("full_name"),
            "score": winner["score"],
            "rank": winner["rank"],
        },
        "entries": entries,
    }


def archive(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    raw_paths = [output / f"{lane}-results.json" for lane in LANES]
    if args.offline:
        raw_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in raw_paths]
        for lane, payload in zip(LANES, raw_payloads, strict=True):
            if payload.get("lane") != lane:
                raise ValueError(f"{lane} snapshot declares lane {payload.get('lane')!r}")
            if payload.get("event_id") != args.event_id:
                raise ValueError(
                    f"{lane} snapshot event {payload.get('event_id')!r} "
                    f"does not match {args.event_id!r}"
                )
            if payload.get("lobby_id") != args.lobby_id:
                raise ValueError(
                    f"{lane} snapshot lobby {payload.get('lobby_id')!r} "
                    f"does not match {args.lobby_id!r}"
                )
        captured = {str(payload["captured_at"]) for payload in raw_payloads}
        if len(captured) != 1:
            raise ValueError(f"raw snapshots have different capture times: {captured}")
        captured_at = captured.pop()
        results_by_lane = {
            lane: payload["results"] for lane, payload in zip(LANES, raw_payloads, strict=True)
        }
    else:
        captured_at = datetime.now(UTC).isoformat()
        results_by_lane = {lane: fetch_results(lane, args.lobby_id) for lane in LANES}
    universes = [{str(row["market_id"]) for row in results_by_lane[lane]} for lane in LANES]
    if universes[0] != universes[1]:
        raise ValueError("lane market universes differ")
    if len(universes[0]) != args.expected_settled_markets:
        raise ValueError(
            f"expected {args.expected_settled_markets} settled markets, got {len(universes[0])}"
        )
    match_ids = {str(row["match_id"]) for row in results_by_lane["honest"]}
    if len(match_ids) != args.expected_settled_matches:
        raise ValueError(
            f"expected {args.expected_settled_matches} settled matches, got {len(match_ids)}"
        )

    if not args.offline:
        for lane, path in zip(LANES, raw_paths, strict=True):
            _write_json(
                path,
                {
                    "schema_version": 1,
                    "captured_at": captured_at,
                    "source": f"{Config.from_env(lane=lane).base_url}/results",
                    "event_id": args.event_id,
                    "lobby_id": args.lobby_id,
                    "lane": lane,
                    "results": results_by_lane[lane],
                },
            )

    multipliers = infer_match_multipliers(results_by_lane["honest"], results_by_lane["scenario"])
    run_candidates, run_matches, observed = load_run_index(Path(args.runs))
    ledger_index = load_ledger_index(Path(args.ledger))
    overrides = load_overrides(Path(args.overrides))
    player_tours = load_player_tours(Path(args.players))
    canonical = build_canonical_rows(
        results_by_lane,
        multipliers,
        run_candidates,
        run_matches,
        ledger_index,
        overrides,
        player_tours,
    )
    canonical_path = output / "settled.csv"
    _write_csv(canonical_path, CANONICAL_FIELDS, canonical)

    void_rows = build_void_rows(observed, universes[0], run_candidates, ledger_index)
    if len(void_rows) != args.expected_void_markets:
        raise ValueError(
            f"expected {args.expected_void_markets} void markets, got {len(void_rows)}"
        )
    void_path = output / "void-markets.csv"
    _write_csv(void_path, VOID_FIELDS, void_rows)

    manual_rows = build_manual_rows(Path(args.manual_entries), canonical)
    manual_path = output / "manual-known.csv"
    _write_csv(manual_path, MANUAL_FIELDS, manual_rows)

    leaderboard_path = output / "leaderboard-summary.json"
    if args.offline:
        if not leaderboard_path.exists():
            raise FileNotFoundError(f"{leaderboard_path} is required for an offline rebuild")
        leaderboard = json.loads(leaderboard_path.read_text(encoding="utf-8"))
        if leaderboard.get("event_id") != args.event_id:
            raise ValueError("leaderboard event does not match the frozen result snapshots")
        if leaderboard.get("lobby_id") != args.lobby_id:
            raise ValueError("leaderboard lobby does not match the frozen result snapshots")
    elif all((args.honest_user_id, args.scenario_user_id, args.manual_user_id)):
        _write_json(
            leaderboard_path,
            build_leaderboard_summary(
                args.event_id,
                args.lobby_id,
                {
                    "honest": args.honest_user_id,
                    "scenario": args.scenario_user_id,
                    "manual": args.manual_user_id,
                },
                captured_at,
            ),
        )
    elif any((args.honest_user_id, args.scenario_user_id, args.manual_user_id)):
        raise ValueError("provide all three user IDs or none")

    artifacts = [*raw_paths, canonical_path, void_path, manual_path]
    if leaderboard_path.exists():
        artifacts.append(leaderboard_path)
    provenance_counts = Counter(str(row["provenance"]) for row in canonical)
    multiplier_rows = Counter(
        int(row["multiplier"]) for row in canonical if row["lane"] == "honest"
    )
    manifest = {
        "schema_version": 1,
        "captured_at": captured_at,
        "event": {
            "id": args.event_id,
            "lobby_id": args.lobby_id,
            "advertised_matches": args.advertised_matches,
            "advertised_markets": args.advertised_markets,
        },
        "final_api": {
            "settled_matches": len(match_ids),
            "settled_markets_per_lane": len(universes[0]),
            "rows": len(canonical),
            "api_rbp_sum": {
                lane: _round(sum(float(row["rbp"]) for row in results_by_lane[lane]), 6)
                for lane in LANES
            },
            "rows_by_multiplier_per_lane": dict(sorted(multiplier_rows.items())),
        },
        "run_archive": {
            "observed_markets": len(observed),
            "deleted_or_void_markets": len(void_rows),
            "provenance_rows": dict(sorted(provenance_counts.items())),
        },
        "manual_history": {
            "authoritative_rows_available": 0,
            "locally_recorded_values": len(manual_rows),
            "leaderboard_settled_predictions": 660,
            "status": "incomplete_requires_authenticated_export",
            "estimated_api_rbp_known_rows": _round(
                sum(float(row["estimated_api_rbp"]) for row in manual_rows), 4
            ),
        },
        "artifacts": {
            str(path.relative_to(output)): {
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in artifacts
        },
        "notes": [
            "API rbp is already stage-weighted.",
            "created_date is prediction creation time, not settlement time.",
            "Run archives are model intentions, not submission receipts.",
            "The final API omits multipliers; they are inferred from exact lane score differences.",
        ],
    }
    manifest_path = output / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "grand-slam-archive/1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    if not isinstance(parsed, dict):
        raise TypeError(f"expected object from {url}")
    return parsed


def _one(rows) -> dict[str, Any]:
    values = list(rows)
    if len(values) != 1:
        raise ValueError(f"expected one leaderboard row, got {len(values)}")
    return values[0]


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _round(value: float, places: int = 4) -> float:
    return round(float(value), places)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--lobby-id", required=True)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="rebuild derived files from the frozen raw snapshots without network access",
    )
    parser.add_argument("--honest-user-id")
    parser.add_argument("--scenario-user-id")
    parser.add_argument("--manual-user-id")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--runs", default=str(DEFAULT_RUNS))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--overrides", default=str(DEFAULT_OVERRIDES))
    parser.add_argument("--players", default=str(DEFAULT_PLAYERS))
    parser.add_argument("--manual-entries", default=str(DEFAULT_MANUAL))
    parser.add_argument("--advertised-matches", type=int, default=190)
    parser.add_argument("--advertised-markets", type=int, default=1280)
    parser.add_argument("--expected-settled-matches", type=int, default=189)
    parser.add_argument("--expected-settled-markets", type=int, default=1267)
    parser.add_argument("--expected-void-markets", type=int, default=28)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    manifest = archive(parse_args(argv))
    final = manifest["final_api"]
    print(
        f"archived {final['settled_matches']} matches, "
        f"{final['settled_markets_per_lane']} markets per lane, "
        f"{manifest['run_archive']['deleted_or_void_markets']} void markets"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
