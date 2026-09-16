"""Tests for the market question parser and the API client contract."""

from __future__ import annotations

import pytest

from bot.markets.parser import MarketSpec, parse_question, reset_unparsed, unparsed_report
from bot.markets.taxonomy import Family
from bot.sportspredict.api import _validate_probability
from bot.sportspredict.config import Config, ConfigError, RateLimiter, retry_delay

PLAYERS = ("A. Sabalenka", "I. Swiatek")


@pytest.fixture(autouse=True)
def _clear_unparsed():
    reset_unparsed()
    yield
    reset_unparsed()


@pytest.mark.parametrize(
    ("text", "family"),
    [
        ("Will the match include a tiebreak?", Family.TIEBREAK),
        ("Will the match need a deciding set?", Family.DECIDING_SET),
        ("Will the match go the distance?", Family.DECIDING_SET),
        ("Will a set end in a lopsided score?", Family.LOPSIDED_SET),
        ("Will any set finish 6-0, 6-1 or 6-2?", Family.LOPSIDED_SET),
        ("Will the first set contain 10 or more games?", Family.SET_GAMES),
        ("Will the match last more than 150 minutes?", Family.MATCH_DURATION),
        ("Will one player serve more aces than the other?", Family.ACES_COMPARISON),
        ("Will one player double fault more than the other?", Family.DOUBLE_FAULTS_COMPARISON),
        ("Will one player out-hit the other in winners?", Family.WINNERS_COMPARISON),
        ("Will one player dominate points on first serve?", Family.FIRST_SERVE_COMPARISON),
        ("Will a player rack up excessive unforced errors?", Family.UNFORCED_ERRORS),
    ],
)
def test_published_question_archetypes_are_classified(text, family):
    """Every archetype published before the event must parse."""
    assert parse_question(text, players=PLAYERS).family is family


def test_double_fault_beats_the_generic_fault_rule():
    spec = parse_question("Will Sabalenka hit more double faults than Swiatek?", players=PLAYERS)
    assert spec.family is Family.DOUBLE_FAULTS_COMPARISON


def test_thousands_separator_is_preserved_in_threshold():
    spec = parse_question(
        "Will A. Sabalenka cover more than 10,300 feet?",
        players=("A. Sabalenka", "E. Rybakina"),
    )
    assert spec.family is Family.DISTANCE_COVERED
    assert spec.threshold == 10_300


def test_unforced_errors_beat_the_generic_winners_rule():
    spec = parse_question("Will Swiatek hit more unforced errors than winners?", players=PLAYERS)
    assert spec.family is Family.UNFORCED_ERRORS


def test_parser_never_raises_on_garbage():
    for text in ("", "   ", "???", "Will the sky be blue?", None):
        spec = parse_question(text)  # type: ignore[arg-type]
        assert isinstance(spec, MarketSpec)


def test_unknown_question_degrades_to_other_and_is_recorded():
    spec = parse_question("Will the roof be closed for this match?")
    assert spec.family is Family.OTHER
    assert spec.confidence == 0.0
    assert not spec.is_understood
    assert "Will the roof be closed for this match?" in unparsed_report()


def test_unparsed_report_deduplicates():
    parse_question("Totally novel question type")
    parse_question("Totally novel question type")
    assert unparsed_report().count("Totally novel question type") == 1


@pytest.mark.parametrize(
    ("text", "threshold", "comparator"),
    [
        ("Will the first set contain 10 or more games?", 10.0, "gte"),
        ("Will the match last more than 150 minutes?", 150.0, "gt"),
        ("Will there be at least 22 games?", 22.0, "gte"),
        ("Will the match finish in under 90 minutes?", 90.0, "lt"),
        ("Will a player hit exactly 30 unforced errors?", 30.0, "eq"),
    ],
)
def test_threshold_extraction(text, threshold, comparator):
    spec = parse_question(text)
    assert spec.threshold == threshold
    assert spec.comparator == comparator


def test_set_number_extraction():
    assert parse_question("Will the first set contain 10 or more games?").set_number == 1
    assert parse_question("Will the third set go to a tiebreak?").set_number == 3
    assert parse_question("Will the match include a tiebreak?").set_number is None


def test_subject_is_the_player_named_first():
    spec = parse_question("Will Swiatek serve more aces than Sabalenka?", players=PLAYERS)
    assert spec.subject == "I. Swiatek"
    assert spec.opponent == "A. Sabalenka"


def test_shortened_compound_name_resolves_without_substring_matching():
    players = ("D. Merida Aguilar", "Andrey Rublev")
    spec = parse_question(
        "Will D. Merida record more aces than A. Rublev?",
        players=players,
    )
    assert spec.subject == "D. Merida Aguilar"
    assert spec.opponent == "Andrey Rublev"


def test_comparison_without_identifiable_subject_loses_confidence():
    spec = parse_question("Will one player serve more aces than the other?", players=PLAYERS)
    assert spec.subject is None
    assert spec.confidence <= 0.4
    assert any("subject" in note for note in spec.notes)


def test_missing_threshold_is_flagged_not_guessed():
    spec = parse_question("Will a player rack up excessive unforced errors?")
    assert spec.family is Family.UNFORCED_ERRORS
    assert any("threshold" in note for note in spec.notes)


def test_spec_serialises_for_the_ledger():
    payload = parse_question("Will the match include a tiebreak?").as_dict()
    assert payload["family"] == "tiebreak"
    assert "raw" in payload


# ------------------------------------------------------------------- API layer


@pytest.mark.parametrize("value", [1, 50, 99])
def test_valid_probabilities_accepted(value):
    _validate_probability(value)


@pytest.mark.parametrize("value", [0, 100, -1, 3.5, "70", True, None])
def test_invalid_probabilities_rejected(value):
    """The API rejects 0 and 100; booleans and floats must not slip through."""
    with pytest.raises(ValueError):
        _validate_probability(value)


def test_config_requires_a_key():
    with pytest.raises(ConfigError):
        Config.from_env(env_file="/nonexistent/.env", require_api_key=True)


def test_config_can_be_built_without_a_key_for_dry_runs():
    config = Config.from_env(env_file="/nonexistent/.env", require_api_key=False)
    assert config.api_key == ""
    assert config.base_url.endswith("/api/v1")


def test_lane_selects_a_distinct_key_variable(monkeypatch):
    monkeypatch.setenv("SPORTSPREDICT_API_KEY", "sp_live_honest")
    monkeypatch.setenv("SPORTSPREDICT_API_KEY_SCENARIO", "sp_live_scenario")
    honest = Config.from_env(env_file="/nonexistent/.env", lane="honest")
    scenario = Config.from_env(env_file="/nonexistent/.env", lane="scenario")
    assert honest.api_key != scenario.api_key
    assert scenario.lane == "scenario"


def test_retry_delay_honours_retry_after_header():
    assert retry_delay(0, retry_after="7") == 7.0


def test_retry_delay_is_jittered_within_bounds():
    delays = {retry_delay(3) for _ in range(50)}
    assert all(0.0 <= d <= 8.0 for d in delays)
    assert len(delays) > 1, "jitter must vary between calls"


def test_rate_limiter_paces_requests():
    import time

    limiter = RateLimiter(min_interval_seconds=0.05)
    start = time.monotonic()
    for _ in range(3):
        limiter.wait()
    assert time.monotonic() - start >= 0.09
