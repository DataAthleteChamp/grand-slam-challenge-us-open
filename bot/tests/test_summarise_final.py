"""Integrity tests for the generated final tournament report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.summarise_final import load_rows, render
from scripts.validate_archive import validate

FINAL = Path("data/history/final")


def test_final_report_reconciles_to_manifest_and_leaderboard():
    rows = load_rows(FINAL / "settled.csv")
    manifest = json.loads((FINAL / "manifest.json").read_text())
    leaderboard = json.loads((FINAL / "leaderboard-summary.json").read_text())

    assert len(rows) == manifest["final_api"]["rows"] == 2534
    for lane in ("honest", "scenario"):
        lane_rows = [row for row in rows if row["lane"] == lane]
        assert len(lane_rows) == 1267
        score = sum(float(row["api_rbp"]) for row in lane_rows)
        assert score == pytest.approx(
            manifest["final_api"]["api_rbp_sum"][lane],
            abs=1e-6,
        )

    report = render(rows, manifest, leaderboard)
    assert "20 / 271" in report
    assert "2,534 rows" in report
    assert "API RBP is already stage-weighted" in report


def test_final_archive_passes_strict_validation():
    assert validate(FINAL, Path("RESULTS.md")) == []
