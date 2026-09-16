"""Generate the final tournament report from the canonical private archive."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FINAL = ROOT / "data" / "history" / "final"
DEFAULT_OUTPUT = ROOT / "RESULTS.md"
ROUND_ORDER = ("R128", "R32", "R16", "QF", "SF", "F")
LANE_ORDER = ("honest", "scenario")


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def render(
    rows: list[dict[str, str]],
    manifest: dict,
    leaderboard: dict,
) -> str:
    entries = {entry["role"]: entry for entry in leaderboard["entries"]}
    winner = leaderboard["winner"]
    final_api = manifest["final_api"]
    run_archive = manifest["run_archive"]
    manual = manifest["manual_history"]

    lines = [
        "# Final results - SportsPredict Grand Slam Challenge 2026",
        "",
        "The tournament is complete. This repository is retained as a private research",
        "archive; the command-line submission path is disabled.",
        "",
        "## Result",
        "",
        "| Entry | Forecasts | Score | Overall | Category | Smart Rating |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for role in ("honest", "scenario", "manual"):
        entry = entries[role]
        label = entry["label"]
        lines.append(
            f"| {label} | {entry['settled_predictions']:,} | "
            f"{entry['score']:,.3f} | {entry['overall_rank']} / "
            f"{entry['overall_total']} | {entry['category_rank']} / "
            f"{entry['category_total']} {entry['category']} | "
            f"{entry['smart_rating']:.1f} |"
        )
    lines.extend(
        [
            "",
            f"Winner: **{winner['label']}**, {winner['score']:,.3f} RBP.",
            "",
            "Quant_Researcher is the honest lane. T-shirt Hunter is the scenario lane;",
            "the unlabeled manual entry is separate.",
            "",
            "## Event accounting",
            "",
            "| Measure | Matches | Markets |",
            "|---|---:|---:|",
            f"| Advertised | {manifest['event']['advertised_matches']} | "
            f"{manifest['event']['advertised_markets']:,} |",
            f"| Final settled API | {final_api['settled_matches']} | "
            f"{final_api['settled_markets_per_lane']:,} |",
            f"| Observed in run archives | - | {run_archive['observed_markets']:,} |",
            f"| Deleted, replaced, or void | - | {run_archive['deleted_or_void_markets']} |",
            "",
            "The advertised, observed, and settled counts are deliberately not collapsed.",
            "Both bots answered every market that remained scoreable.",
            "",
            "## Score by round",
            "",
            "| Round | Honest questions | Honest RBP | Scenario questions | Scenario RBP |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    round_summary = _group(rows, "round_code")
    for round_code in ROUND_ORDER:
        honest = round_summary["honest"].get(round_code, _empty())
        scenario = round_summary["scenario"].get(round_code, _empty())
        lines.append(
            f"| {round_code} | {honest['n']} | {honest['api_rbp']:+,.2f} | "
            f"{scenario['n']} | {scenario['api_rbp']:+,.2f} |"
        )
    lines.extend(
        [
            "",
            "API RBP is already stage-weighted. The sum of rounded result rows is",
            f"{final_api['api_rbp_sum']['honest']:,.2f} for honest and "
            f"{final_api['api_rbp_sum']['scenario']:,.2f} for scenario; the",
            "leaderboard retains additional internal precision.",
            "",
            "## Performance by market family",
            "",
        ]
    )
    family_summary = _group(rows, "family")
    for lane in LANE_ORDER:
        lines.extend(
            [
                f"### {entries[lane]['label']}",
                "",
                "| Family | N | API RBP | Consensus skill | Mean Brier |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        families = sorted(
            family_summary[lane].items(),
            key=lambda item: item[1]["api_rbp"],
            reverse=True,
        )
        for family, values in families:
            lines.append(
                f"| `{family}` | {values['n']} | {values['api_rbp']:+,.2f} | "
                f"{values['skill_rbp']:+,.2f} | "
                f"{values['brier'] / values['n']:.4f} |"
            )
        lines.append("")

    honest_families = family_summary["honest"]
    scenario_families = family_summary["scenario"]
    honest_best = max(honest_families.items(), key=lambda item: item[1]["api_rbp"])
    honest_worst = min(honest_families.items(), key=lambda item: item[1]["api_rbp"])
    scenario_worst = min(scenario_families.items(), key=lambda item: item[1]["api_rbp"])
    lines.extend(
        [
            "## Main lessons",
            "",
            f"- Honest lane's strongest family was `{honest_best[0]}` at "
            f"{honest_best[1]['api_rbp']:+,.2f} RBP.",
            f"- Honest lane's weakest family was `{honest_worst[0]}` at "
            f"{honest_worst[1]['api_rbp']:+,.2f} RBP.",
            f"- Scenario lane's largest loss was `{scenario_worst[0]}` at "
            f"{scenario_worst[1]['api_rbp']:+,.2f} RBP.",
            "- Tiebreak and ace questions contained the most durable edge. Winner and",
            "  lopsided-set models were the clearest areas where ranking or calibration",
            "  was not good enough.",
            "- Scenario shifts increased variance without improving the final result;",
            "  the honest lane finished 19 places higher.",
            "",
            "## Archive integrity",
            "",
            f"- Canonical bot history: **{len(rows):,} rows**, keyed by lane and market ID.",
            f"- Exact run match: {run_archive['provenance_rows']['exact_run_match']:,} rows.",
            f"- Documented final override: "
            f"{run_archive['provenance_rows']['documented_override']} rows.",
            f"- Final submission absent from runs: "
            f"{run_archive['provenance_rows']['unmatched_submission']} rows.",
            f"- Manual history: {manual['locally_recorded_values']} locally recorded values",
            f"  out of {manual['leaderboard_settled_predictions']} settled predictions.",
            "",
            "Run files are model intentions, not submission receipts. They omit server",
            "responses and exact model-version hashes, so unmatched rows are left unknown",
            "rather than assigned to an invented commit. `created_date` from the results",
            "API is prediction creation time, not settlement time.",
            "",
            "## Reproduction",
            "",
            "The final derived archive can be rebuilt without network access:",
            "",
            "```bash",
            "PYTHONPATH=. python scripts/archive_final.py --offline \\",
            "  --event-id 0cf0efd1-7e12-4ca9-8068-89eb48466a37 \\",
            "  --lobby-id b1419bb4-6dff-4172-9a42-a206b0a24ad9",
            "python scripts/summarise_final.py",
            "```",
            "",
            "Checksums for the frozen inputs and derived files are in",
            "`data/history/final/manifest.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def _group(rows: list[dict[str, str]], field: str) -> dict[str, dict[str, dict]]:
    grouped: dict[str, dict[str, dict]] = {lane: defaultdict(_empty) for lane in LANE_ORDER}
    for row in rows:
        values = grouped[row["lane"]][row[field]]
        values["n"] += 1
        values["api_rbp"] += float(row["api_rbp"])
        values["skill_rbp"] += float(row["consensus_skill_rbp"])
        values["brier"] += float(row["brier"])
    return grouped


def _empty() -> dict[str, float]:
    return {"n": 0, "api_rbp": 0.0, "skill_rbp": 0.0, "brier": 0.0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=FINAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    rows = load_rows(args.archive / "settled.csv")
    manifest = json.loads((args.archive / "manifest.json").read_text(encoding="utf-8"))
    leaderboard = json.loads(
        (args.archive / "leaderboard-summary.json").read_text(encoding="utf-8")
    )
    text = render(rows, manifest, leaderboard)
    args.output.write_text(text, encoding="utf-8")
    print(f"wrote {args.output} ({len(rows)} canonical rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
