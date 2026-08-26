"""Tests for US Open point-by-point ingestion."""

from __future__ import annotations

import pandas as pd
import pytest

from bot.data.pointbypoint import (
    MAX_POINT_GAP_SECONDS,
    US_OPEN_YEARS,
    _add_timing,
    _parse_elapsed,
    match_pace,
    official_rates,
    serve_speed,
)


@pytest.fixture
def points() -> pd.DataFrame:
    """Two matches with clean timing, one long gap, and one bad clock value."""
    return pd.DataFrame(
        [
            {
                "match_id": "m1",
                "PointNumber": 1,
                "ElapsedTime": "0:00:00",
                "PointServer": 1,
                "RallyCount": 1,
                "Speed_KMH": 200,
                "ServeNumber": 1,
                "P1Ace": 1,
                "P2Ace": 0,
                "P1DoubleFault": 0,
                "P2DoubleFault": 0,
                "P1Winner": 0,
                "P2Winner": 0,
                "P1UnfErr": 0,
                "P2UnfErr": 0,
                "year": 2024,
            },
            {
                "match_id": "m1",
                "PointNumber": 2,
                "ElapsedTime": "0:00:40",
                "PointServer": 1,
                "RallyCount": 5,
                "Speed_KMH": 180,
                "ServeNumber": 1,
                "P1Ace": 0,
                "P2Ace": 0,
                "P1DoubleFault": 0,
                "P2DoubleFault": 0,
                "P1Winner": 0,
                "P2Winner": 1,
                "P1UnfErr": 0,
                "P2UnfErr": 0,
                "year": 2024,
            },
            {
                "match_id": "m1",
                "PointNumber": 3,
                "ElapsedTime": "0:20:00",
                "PointServer": 2,
                "RallyCount": 3,
                "Speed_KMH": 0,
                "ServeNumber": 2,
                "P1Ace": 0,
                "P2Ace": 0,
                "P1DoubleFault": 0,
                "P2DoubleFault": 1,
                "P1Winner": 0,
                "P2Winner": 0,
                "P1UnfErr": 1,
                "P2UnfErr": 0,
                "year": 2024,
            },
            {
                "match_id": "m2",
                "PointNumber": 1,
                "ElapsedTime": "not-a-time",
                "PointServer": 1,
                "RallyCount": 2,
                "Speed_KMH": 190,
                "ServeNumber": 1,
                "P1Ace": 0,
                "P2Ace": 0,
                "P1DoubleFault": 0,
                "P2DoubleFault": 0,
                "P1Winner": 1,
                "P2Winner": 0,
                "P1UnfErr": 0,
                "P2UnfErr": 0,
                "year": 2024,
            },
        ]
    )


def test_us_open_year_coverage():
    """The mirror stops at 2024; 2025 onward is not available."""
    assert min(US_OPEN_YEARS) == 2011
    assert max(US_OPEN_YEARS) == 2024


def test_parse_elapsed_handles_clock_strings():
    parsed = _parse_elapsed(pd.Series(["0:00:00", "1:23:45", "0:00:40"]))
    assert list(parsed) == [0.0, 5025.0, 40.0]


def test_parse_elapsed_tolerates_malformed_values():
    """Earlier years contain entries that overflow a naive timedelta parser."""
    parsed = _parse_elapsed(pd.Series(["not-a-time", "", "999999999999999999999"]))
    assert parsed.isna().all()


def test_timing_computes_gaps_between_points(points):
    frame = _add_timing(points)
    first = frame[(frame.match_id == "m1") & (frame.PointNumber == 2)].iloc[0]
    assert first["point_seconds"] == pytest.approx(40.0)


def test_long_gaps_are_excluded_as_breaks(points):
    """A twenty-minute gap is a rain delay or medical timeout, not pace of play."""
    frame = _add_timing(points)
    third = frame[(frame.match_id == "m1") & (frame.PointNumber == 3)].iloc[0]
    assert pd.isna(third["point_seconds"])
    assert MAX_POINT_GAP_SECONDS < 20 * 60


def test_first_point_of_a_match_has_no_gap(points):
    frame = _add_timing(points)
    first = frame[(frame.match_id == "m1") & (frame.PointNumber == 1)].iloc[0]
    assert pd.isna(first["point_seconds"])


def test_match_pace_summarises_per_match(points):
    pace = match_pace(_add_timing(points))
    assert set(pace["match_id"]) == {"m1", "m2"}
    assert pace[pace.match_id == "m1"].iloc[0]["seconds_per_point"] == pytest.approx(40.0)


def test_official_rates_split_by_player_slot(points):
    rates = official_rates(_add_timing(points))
    assert set(rates["player_slot"]) == {1, 2}
    m1_p1 = rates[(rates.match_id == "m1") & (rates.player_slot == 1)].iloc[0]
    assert m1_p1["aces"] == 1
    assert m1_p1["serve_points"] == 2


def test_ace_rate_uses_service_points_as_denominator(points):
    rates = official_rates(_add_timing(points))
    m1_p1 = rates[(rates.match_id == "m1") & (rates.player_slot == 1)].iloc[0]
    assert m1_p1["ace_rate"] == pytest.approx(1 / 2)


def test_winner_rate_uses_all_points(points):
    """Winners can occur on any point, not only the player's own serve."""
    rates = official_rates(_add_timing(points))
    m1_p2 = rates[(rates.match_id == "m1") & (rates.player_slot == 2)].iloc[0]
    assert m1_p2["winner_rate"] == pytest.approx(1 / 3)


def test_serve_speed_filters_invalid_readings(points):
    """Zero-speed rows are missing readings, not slow serves."""
    speeds = serve_speed(_add_timing(points))
    assert (speeds["mean_kmh"] > 100).all()


def test_serve_speed_first_serve_filter(points):
    first_only = serve_speed(_add_timing(points), first_serve_only=True)
    assert first_only["serves"].sum() == 3


def test_empty_inputs_return_empty_frames():
    empty = pd.DataFrame()
    assert match_pace(empty).empty
    assert official_rates(empty).empty
    assert serve_speed(empty).empty
