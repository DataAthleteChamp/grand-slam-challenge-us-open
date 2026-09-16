"""Validate the canonical final archive and generated tournament report."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.summarise_final import render  # noqa: E402

DEFAULT_ARCHIVE = ROOT / "data" / "history" / "final"
DEFAULT_RESULTS = ROOT / "RESULTS.md"


def validate(archive: Path, results_path: Path) -> list[str]:
    errors: list[str] = []
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    leaderboard = json.loads((archive / "leaderboard-summary.json").read_text(encoding="utf-8"))
    raw_payloads = {
        lane: json.loads((archive / f"{lane}-results.json").read_text(encoding="utf-8"))
        for lane in ("honest", "scenario")
    }
    with (archive / "settled.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    with (archive / "void-markets.csv").open(newline="", encoding="utf-8") as handle:
        void_rows = list(csv.DictReader(handle))
    with (archive / "manual-known.csv").open(newline="", encoding="utf-8") as handle:
        manual_rows = list(csv.DictReader(handle))

    for relative, metadata in manifest["artifacts"].items():
        path = archive / relative
        _expect(errors, path.exists(), f"missing artifact: {relative}")
        if not path.exists():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        _expect(errors, digest == metadata["sha256"], f"checksum mismatch: {relative}")
        _expect(
            errors,
            path.stat().st_size == metadata["bytes"],
            f"size mismatch: {relative}",
        )

    final = manifest["final_api"]
    event = manifest["event"]
    for lane, payload in raw_payloads.items():
        _expect(errors, payload.get("lane") == lane, f"{lane} raw lane identity")
        _expect(
            errors,
            payload.get("event_id") == event["id"],
            f"{lane} raw event identity",
        )
        _expect(
            errors,
            payload.get("lobby_id") == event["lobby_id"],
            f"{lane} raw lobby identity",
        )
    _expect(
        errors,
        leaderboard.get("event_id") == event["id"],
        "leaderboard event identity",
    )
    _expect(
        errors,
        leaderboard.get("lobby_id") == event["lobby_id"],
        "leaderboard lobby identity",
    )
    _expect(errors, len(rows) == final["rows"] == 2534, "canonical row count")
    keys = {(row["lane"], row["market_id"]) for row in rows}
    _expect(errors, len(keys) == len(rows), "lane/market identity is not unique")

    lane_markets = {}
    for lane in ("honest", "scenario"):
        lane_rows = [row for row in rows if row["lane"] == lane]
        lane_markets[lane] = {row["market_id"] for row in lane_rows}
        _expect(
            errors,
            len(lane_rows) == final["settled_markets_per_lane"] == 1267,
            f"{lane} row count",
        )
        _expect(
            errors,
            len({row["match_id"] for row in lane_rows}) == final["settled_matches"] == 189,
            f"{lane} match count",
        )
        score = round(sum(float(row["api_rbp"]) for row in lane_rows), 6)
        _expect(
            errors,
            abs(score - float(final["api_rbp_sum"][lane])) < 1e-6,
            f"{lane} API RBP sum",
        )
        multiplier_counts = Counter(int(row["multiplier"]) for row in lane_rows)
        expected_counts = {
            int(multiplier): count
            for multiplier, count in final["rows_by_multiplier_per_lane"].items()
        }
        _expect(
            errors,
            multiplier_counts == expected_counts,
            f"{lane} multiplier distribution",
        )
        for row in lane_rows:
            submitted = int(row["submitted"]) / 100.0
            outcome = int(row["outcome"])
            _expect(
                errors,
                abs((submitted - outcome) ** 2 - float(row["brier"])) < 1e-9,
                f"{lane}/{row['market_id']} Brier mismatch",
            )
            _expect(
                errors,
                float(row["field_variance_rbp"]) >= 0,
                f"{lane}/{row['market_id']} negative field variance",
            )
            if row["family"] in {
                "match_winner",
                "aces_comparison",
                "double_faults_comparison",
                "winners_comparison",
                "first_serve_comparison",
                "unforced_errors",
                "distance_covered",
                "total_points",
                "exact_scoreline",
            }:
                _expect(
                    errors,
                    bool(row["subject"]),
                    f"{lane}/{row['market_id']} missing subject",
                )

    _expect(errors, lane_markets["honest"] == lane_markets["scenario"], "lane universes differ")
    final_markets = lane_markets["honest"]
    _expect(errors, len(void_rows) == 28, "void market count")
    _expect(
        errors,
        not final_markets.intersection(row["market_id"] for row in void_rows),
        "void market appears in final results",
    )
    _expect(errors, len(manual_rows) == 40, "known manual row count")
    _expect(
        errors,
        all(row["status"] == "recorded_value_not_server_verified" for row in manual_rows),
        "manual row status",
    )

    entry_by_role = {entry["role"]: entry for entry in leaderboard["entries"]}
    for lane in ("honest", "scenario"):
        rounded_sum = float(final["api_rbp_sum"][lane])
        _expect(
            errors,
            abs(float(entry_by_role[lane]["score"]) - rounded_sum) < 0.05,
            f"{lane} leaderboard reconciliation",
        )
    _expect(
        errors,
        not _contains_key(leaderboard, "user_id"),
        "leaderboard summary contains user IDs",
    )

    expected_report = render(rows, manifest, leaderboard)
    _expect(
        errors,
        results_path.read_text(encoding="utf-8") == expected_report,
        "RESULTS.md is stale",
    )
    return errors


def _expect(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def _contains_key(value, wanted: str) -> bool:
    if isinstance(value, dict):
        return wanted in value or any(_contains_key(item, wanted) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, wanted) for item in value)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)

    errors = validate(args.archive, args.results)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("final archive valid: 2,534 bot rows, 189 matches, 28 void markets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
