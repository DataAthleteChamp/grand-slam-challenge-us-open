"""Tests for the completed-event archival path."""

from __future__ import annotations

import json

import pytest

from scripts.archive_final import (
    build_canonical_rows,
    build_manual_rows,
    infer_draw,
    infer_match_multipliers,
    infer_round,
    load_overrides,
)


def _result(*, brier: float, rbp: float, submitted: int = 80) -> dict:
    return {
        "market_id": "market-1",
        "match_id": "match-1",
        "question": "Will A. Alpha win?",
        "probability_submitted": submitted,
        "result_value": 100,
        "crowd_value": 50,
        "brier_score": brier,
        "rbp": rbp,
        "outcome_tag": "beat_crowd",
        "created_date": "2026-09-03T12:00:00Z",
    }


def test_multiplier_is_inferred_from_lane_score_difference():
    honest = [_result(brier=0.04, rbp=42.0)]
    scenario = [_result(brier=0.09, rbp=32.0, submitted=70)]
    assert infer_match_multipliers(honest, scenario) == {"match-1": 2}


def test_multiplier_rejects_an_inconsistent_score_difference():
    honest = [_result(brier=0.04, rbp=42.0)]
    scenario = [_result(brier=0.09, rbp=36.0, submitted=70)]
    with pytest.raises(ValueError, match="cannot infer multiplier"):
        infer_match_multipliers(honest, scenario)


@pytest.mark.parametrize(
    ("multiplier", "created_at", "expected"),
    [
        (1, "2026-09-12T00:00:00Z", "R128"),
        (2, "2026-09-04T23:59:59Z", "R32"),
        (2, "2026-09-05T00:00:00Z", "R16"),
        (3, "2026-09-08T23:59:59Z", "QF"),
        (3, "2026-09-09T00:00:00Z", "SF"),
        (3, "2026-09-11T00:00:00Z", "F"),
    ],
)
def test_round_reconstruction(multiplier, created_at, expected):
    assert infer_round(multiplier, created_at) == expected


def test_draw_can_be_recovered_when_only_one_player_is_in_the_roster():
    tours = {"B Player": "WTA"}
    assert infer_draw("A Player", "B Player", tours) == "womens"


def test_override_inherits_capture_time(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(
        json.dumps(
            {
                "captured_at": "2026-09-13T17:55:00Z",
                "submitted_overrides": [{"market_id": "m", "probability": 36}],
            }
        )
    )
    assert load_overrides(path)["m"]["captured_at"] == "2026-09-13T17:55:00Z"


def test_canonical_row_separates_api_score_from_consensus_skill():
    result = _result(brier=0.04, rbp=50.0)
    source = {
        "submission": 80,
        "source_artifact": "runs/run.json",
        "source_generated_at": "20260903T120000Z",
        "notes": [],
    }
    rows = build_canonical_rows(
        {"honest": [result], "scenario": [result]},
        {"match-1": 2},
        {
            ("honest", "market-1"): [source],
            ("scenario", "market-1"): [source],
        },
        {"match-1": {"player_a": "A Alpha", "player_b": "B Beta"}},
        {},
        {},
        {"A Alpha": "ATP", "B Beta": "ATP"},
    )
    assert len(rows) == 2
    assert rows[0]["consensus_skill_rbp"] == pytest.approx(42.0)
    assert rows[0]["field_variance_rbp"] == pytest.approx(8.0)
    assert rows[0]["api_rbp"] == pytest.approx(50.0)
    assert rows[0]["unweighted_api_rbp"] == pytest.approx(25.0)
    assert rows[0]["provenance"] == "exact_run_match"
    assert rows[0]["subject"] == "A Alpha"


def test_manual_rows_are_explicitly_estimated_not_authoritative(tmp_path):
    path = tmp_path / "entries.csv"
    path.write_text("market_id,submitted\nmarket-1,70\n")
    canonical = [
        {
            "lane": "honest",
            "market_id": "market-1",
            "match_id": "match-1",
            "match_name": "A vs B",
            "question": "Will A win?",
            "round_code": "R32",
            "multiplier": 2,
            "outcome": 1,
            "crowd": 0.5,
            "crowd_consensus_brier": 0.25,
            "field_variance_rbp": 8.0,
        }
    ]
    rows = build_manual_rows(path, canonical)
    assert rows[0]["consensus_skill_rbp"] == pytest.approx(32.0)
    assert rows[0]["estimated_api_rbp"] == pytest.approx(40.0)
    assert rows[0]["status"] == "recorded_value_not_server_verified"
