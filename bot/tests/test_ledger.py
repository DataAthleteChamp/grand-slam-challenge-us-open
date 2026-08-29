"""Tests for the results ledger and the evaluation harness."""

from __future__ import annotations

import json

import numpy as np
import pytest

from bot.evaluate import (
    bootstrap_skill,
    draw_outcomes,
    reliability_table,
    score,
)
from bot.ledger import Entry, Ledger, MatchRecord, family_report, render_report


def _entry(market_id="m1", family="tiebreak", submission=40, probability=0.40, **kw):
    return Entry(
        market_id=market_id,
        question="Will the match feature at least 1 tiebreak(s)?",
        family=family,
        submission=submission,
        probability=probability,
        base_rate=0.49,
        **kw,
    )


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path)


# ------------------------------------------------------------------- scoring


def test_rbp_is_positive_when_we_beat_the_crowd():
    """RBP is the only number that matters: a brilliant forecast that matches
    the crowd scores exactly zero."""
    entry = _entry(probability=0.90, outcome=1, crowd=0.50)
    assert entry.brier == pytest.approx(0.01)
    assert entry.crowd_brier == pytest.approx(0.25)
    assert entry.rbp == pytest.approx(24.0)


def test_rbp_is_negative_when_the_crowd_beats_us():
    entry = _entry(probability=0.90, outcome=0, crowd=0.50)
    assert entry.rbp == pytest.approx(-56.0)


def test_matching_the_crowd_scores_zero():
    entry = _entry(probability=0.42, outcome=1, crowd=0.42)
    assert entry.rbp == pytest.approx(0.0)


def test_scores_are_none_until_settled():
    entry = _entry()
    assert entry.brier is None and entry.rbp is None and entry.is_settled is False


def test_rbp_needs_the_crowd_not_just_the_outcome():
    """Knowing what happened is not enough to know whether it was a good
    forecast."""
    entry = _entry(outcome=1)
    assert entry.brier is not None
    assert entry.rbp is None


def test_round_multiplier_scales_the_score():
    record = MatchRecord(match_id="x", multiplier=3)
    record.entries = [_entry(probability=0.9, outcome=1, crowd=0.5)]
    assert record.total_rbp == pytest.approx(72.0)


# ----------------------------------------------------------------- round trip


def test_a_record_survives_a_round_trip(ledger):
    record = MatchRecord(match_id="abc", match_name="A vs B", multiplier=2, draw="mens")
    record.entries = [_entry(), _entry(market_id="m2", family="match_winner")]
    ledger.save(record)
    loaded = ledger.load("abc")
    assert loaded is not None
    assert loaded.match_name == "A vs B"
    assert loaded.multiplier == 2
    assert {e.market_id for e in loaded.entries} == {"m1", "m2"}


def test_derived_fields_are_written_but_recomputed_on_load(ledger, tmp_path):
    """An edited file must never be able to disagree with itself."""
    record = MatchRecord(match_id="abc")
    record.entries = [_entry(probability=0.9, outcome=1, crowd=0.5)]
    path = ledger.save(record)
    payload = json.loads(path.read_text())
    assert payload["entries"][0]["rbp"] == pytest.approx(24.0)

    payload["entries"][0]["rbp"] = 999.0
    path.write_text(json.dumps(payload))
    assert ledger.load("abc").entries[0].rbp == pytest.approx(24.0)


def test_a_missing_or_corrupt_record_returns_none(ledger, tmp_path):
    assert ledger.load("nope") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert ledger.load("bad") is None


def test_saving_is_atomic(ledger, tmp_path):
    """A half-written record is how a day of predictions gets lost."""
    record = MatchRecord(match_id="abc")
    record.entries = [_entry()]
    ledger.save(record)
    assert not list(tmp_path.glob("*.tmp"))


# ------------------------------------------------------------- re-pricing


def test_repricing_before_close_does_not_wipe_settlement(ledger):
    """Re-pricing a match before it closes is normal and must not destroy
    outcomes already recorded."""
    record = MatchRecord(match_id="abc")
    record = ledger.record_predictions(record, [_entry(probability=0.4)])
    ledger.save(record)
    ledger.settle("abc", {"m1": 1}, {"m1": 0.5})

    record = ledger.load("abc")
    record = ledger.record_predictions(record, [_entry(probability=0.7)])
    ledger.save(record)

    updated = ledger.load("abc").entries[0]
    assert updated.probability == pytest.approx(0.7)
    assert updated.outcome == 1
    assert updated.crowd == pytest.approx(0.5)


def test_recording_stamps_the_prediction_time(ledger):
    record = ledger.record_predictions(MatchRecord(match_id="abc"), [_entry()])
    assert record.entries[0].predicted_at


def test_settle_reports_how_many_it_touched(ledger):
    record = MatchRecord(match_id="abc")
    record.entries = [_entry(), _entry(market_id="m2")]
    ledger.save(record)
    assert ledger.settle("abc", {"m1": 1, "m2": 0}, {"m1": 0.5, "m2": 0.5}) == 2
    assert ledger.settle("missing", {"m1": 1}, {}) == 0


# -------------------------------------------------------------- family report


def test_family_report_separates_winners_from_losers(ledger):
    """The question the ledger exists to answer. In the predecessor event player
    props returned +957 and fouls returned -125, and the right response to the
    latter was to stop departing from the crowd, not to model harder."""
    good = MatchRecord(match_id="g", multiplier=1)
    good.entries = [
        _entry(market_id=f"g{i}", family="aces_comparison", probability=0.9, outcome=1, crowd=0.5)
        for i in range(4)
    ]
    bad = MatchRecord(match_id="b", multiplier=1)
    bad.entries = [
        _entry(market_id=f"b{i}", family="unforced_errors", probability=0.9, outcome=0, crowd=0.5)
        for i in range(4)
    ]
    report = family_report([good, bad])
    assert report["aces_comparison"]["total_rbp"] > 0
    assert report["unforced_errors"]["total_rbp"] < 0
    assert report["aces_comparison"]["beat_crowd"] == 1.0
    assert report["unforced_errors"]["beat_crowd"] == 0.0
    assert "aces_comparison" in render_report(report)


def test_family_report_ignores_unsettled_questions():
    record = MatchRecord(match_id="x")
    record.entries = [_entry(), _entry(market_id="m2", outcome=1, crowd=0.5)]
    assert family_report([record])["tiebreak"]["n"] == 1


def test_empty_report_says_so():
    assert render_report({}) == "nothing settled yet"


# ------------------------------------------------------------- evaluation


def test_a_perfect_forecaster_scores_zero_brier():
    outcomes = np.array([1.0, 0.0, 1.0, 0.0])
    assert score(outcomes, outcomes).brier == pytest.approx(0.0)
    assert score(outcomes, outcomes).skill == pytest.approx(1.0)


def test_the_base_rate_has_no_skill():
    """A model that answers the base rate every time is perfectly calibrated and
    completely worthless, which is why calibration alone is never evidence."""
    rng = np.random.default_rng(0)
    outcomes = (rng.random(4000) < 0.3).astype(float)
    flat = np.full(4000, outcomes.mean())
    card = score(flat, outcomes)
    assert card.skill == pytest.approx(0.0, abs=1e-9)
    assert card.resolution == pytest.approx(0.0, abs=1e-9)


def test_a_well_calibrated_informative_model_has_positive_skill():
    """Checked against the analytic value rather than a guessed threshold.

    For a perfectly calibrated forecaster the Brier score is E[p(1-p)] =
    0.25 - Var(p), and the base rate scores 0.25, so skill is exactly
    Var(p)/0.25. With truth uniform on [0.05, 0.95], Var(p) = 0.9^2/12 =
    0.0675, giving skill 0.27. A round-number threshold here would have been
    testing my intuition rather than the code.
    """
    rng = np.random.default_rng(1)
    truth = rng.uniform(0.05, 0.95, 6000)
    outcomes = draw_outcomes(truth, rng)
    card = score(truth, outcomes)
    expected = (0.9**2 / 12) / 0.25
    assert card.skill == pytest.approx(expected, abs=0.02)
    assert card.calibration_error < 0.05
    assert card.resolution > 0.05


def test_calibration_slope_detects_overconfidence():
    """Slope below 1 means forecasts are spread wider than the evidence
    supports. The metric this replaced compared mean|f-0.5| to mean|o-0.5|,
    which is degenerate: outcomes are 0/1 so the second term is always 0.5 and
    the statistic could never be positive. It printed -0.34 every run, which
    looked like a finding and was arithmetic."""
    rng = np.random.default_rng(7)
    truth = rng.uniform(0.15, 0.85, 8000)
    outcomes = draw_outcomes(truth, rng)
    honest = score(truth, outcomes).calibration_slope
    pushed = np.clip((truth - 0.5) * 2.0 + 0.5, 0.01, 0.99)
    over = score(pushed, outcomes).calibration_slope
    assert honest == pytest.approx(1.0, abs=0.12)
    assert over < 0.75


def test_an_overconfident_model_is_flagged():
    rng = np.random.default_rng(2)
    truth = rng.uniform(0.2, 0.8, 4000)
    outcomes = draw_outcomes(truth, rng)
    pushed = np.clip((truth - 0.5) * 2.5 + 0.5, 0.01, 0.99)
    assert score(pushed, outcomes).calibration_error > score(truth, outcomes).calibration_error
    assert score(pushed, outcomes).brier > score(truth, outcomes).brier


def test_reliability_table_tracks_the_truth():
    rng = np.random.default_rng(3)
    truth = rng.uniform(0.05, 0.95, 20000)
    outcomes = draw_outcomes(truth, rng)
    for predicted, observed, count in reliability_table(truth, outcomes):
        if count >= 200:
            assert abs(predicted - observed) < 0.05


def test_bootstrap_interval_brackets_the_point_estimate():
    """A single round is 64 matches. A skill number without an interval invites
    tuning against noise."""
    rng = np.random.default_rng(4)
    truth = rng.uniform(0.05, 0.95, 500)
    outcomes = draw_outcomes(truth, rng)
    point, low, high = bootstrap_skill(truth, outcomes, n_boot=400, seed=5)
    assert low <= point <= high
    assert high - low > 0


def test_scoring_an_empty_batch_does_not_explode():
    card = score(np.array([]), np.array([]))
    assert card.n == 0 and card.skill == 0.0
