"""Tests for the pricer and the pre-submission validator."""

from __future__ import annotations

import pytest

from bot.markets.parser import parse_question
from bot.markets.taxonomy import Family
from bot.models.simulate import SimulationConfig
from bot.pricer import MatchContext, PlayerProfile, PricedQuestion, Pricer
from bot.validate import (
    BASE_RATE_COLLAR,
    Severity,
    check_comparison_complement,
    check_match_coherence,
    validate_batch,
)

BIG_SERVER = PlayerProfile(
    name="Alexander Zverev",
    serve_skill=0.067,
    return_skill=0.031,
    matches=170,
    ace_rate=0.122,
    df_rate=0.055,
    winner_rate=0.20,
    unforced_rate=0.19,
    height=198,
)
RETURNER = PlayerProfile(
    name="Carlos Alcaraz",
    serve_skill=0.045,
    return_skill=0.061,
    matches=180,
    ace_rate=0.075,
    df_rate=0.030,
    winner_rate=0.21,
    unforced_rate=0.18,
    height=183,
)
UNKNOWN = PlayerProfile(name="Qualifier Nobody")

FAST_SIM = SimulationConfig(n_sims=800, seed=5)


def _context(a=BIG_SERVER, b=RETURNER, tour="ATP", best_of=5, rank_a=3, rank_b=2):
    return MatchContext(tour, best_of, a, b, rank_a=rank_a, rank_b=rank_b)


def _price(question: str, context=None, lane="honest"):
    context = context or _context()
    pricer = Pricer(lane=lane, sim_config=FAST_SIM)
    spec = parse_question(question, players=(context.player_a.name, context.player_b.name))
    return pricer.price(spec, context, market_id=question[:12])


# --------------------------------------------------------------------- pricer


def test_every_question_yields_a_valid_submission():
    for question in [
        "Will the match include a tiebreak?",
        "Will the match need a deciding set?",
        "Will a set end in a lopsided score?",
        "Will the first set contain 10 or more games?",
        "Will Zverev serve more aces than Alcaraz?",
        "Will a player rack up excessive unforced errors?",
    ]:
        result = _price(question)
        assert isinstance(result.submission, int)
        assert 1 <= result.submission <= 99, question


def test_unknown_question_falls_back_to_the_base_rate():
    result = _price("Will the roof be closed for this match?")
    assert result.family is Family.OTHER
    assert result.submission == pytest.approx(round(result.base_rate * 100), abs=2)
    assert any("not understood" in note for note in result.notes)


def test_bigger_server_is_favoured_on_aces():
    """Orientation is the highest-consequence lookup in the pricer.

    Getting it backwards turns a correct 42 into a 58 on the family carrying the
    most edge, and the mistake is invisible in a dry run.
    """
    result = _price("Will Zverev serve more aces than Alcaraz?")
    assert result.submission > 55


def test_orientation_reverses_when_the_question_reverses():
    forward = _price("Will Zverev serve more aces than Alcaraz?")
    reverse = _price("Will Alcaraz serve more aces than Zverev?")
    assert forward.submission > 50 > reverse.submission
    assert forward.submission + reverse.submission < 105


def test_double_fault_direction_follows_the_rates():
    """Alcaraz double faults less often, so he should be unlikely to hit more."""
    result = _price("Will Alcaraz hit more double faults than Zverev?")
    assert result.submission < 45


def test_unforced_errors_defer_to_the_base_rate():
    result = _price("Will a player rack up excessive unforced errors?")
    assert abs(result.probability - result.base_rate) < 0.10
    assert result.confidence <= 0.35


def test_unrated_players_reduce_confidence_and_pull_to_base():
    known = _price("Will the match include a tiebreak?", _context())
    unknown = _price("Will the match include a tiebreak?", _context(a=UNKNOWN))
    assert unknown.confidence < known.confidence
    assert (
        abs(unknown.probability - unknown.base_rate)
        < abs(known.probability - known.base_rate) + 1e-9
    )


def test_base_rate_is_tour_and_matchup_specific():
    atp = _price("Will the match need a deciding set?", _context(tour="ATP", best_of=5))
    wta = _price(
        "Will the match need a deciding set?",
        _context(tour="WTA", best_of=3),
    )
    assert atp.base_rate != wta.base_rate
    assert wta.base_rate > atp.base_rate, "women reach a decider far more often"


def test_provenance_is_recorded():
    """A number with no explanation is a number that cannot be debugged."""
    payload = _price("Will the match include a tiebreak?").as_dict()
    for key in ("base_rate", "model", "market_weight", "confidence", "lane", "family"):
        assert key in payload


def test_duration_probability_decreases_with_the_threshold():
    short = _price("Will the match last more than 150 minutes?")
    long = _price("Will the match last more than 260 minutes?")
    assert short.submission > long.submission


def test_pricer_never_raises_on_garbage():
    for question in ["", "   ", "???", "Will something happen?"]:
        result = _price(question)
        assert 1 <= result.submission <= 99


# ------------------------------------------------------------------ validator


def _q(market_id, submission, base=0.5, family=Family.TIEBREAK, confidence=0.8):
    return PricedQuestion(
        market_id=market_id,
        family=family,
        probability=submission / 100.0,
        submission=submission,
        base_rate=base,
        confidence=confidence,
    )


def test_clean_batch_passes():
    batch = [_q(f"m{i}", 40 + (i * 7) % 40) for i in range(40)]
    report = validate_batch(batch)
    assert not report.blocked


def test_out_of_range_submission_blocks():
    batch = [_q("m1", 0), _q("m2", 50)]
    report = validate_batch(batch)
    assert report.blocked
    assert any(f.check == "api_range" for f in report.blocks)


def test_nan_probability_blocks():
    bad = _q("m1", 50)
    bad.probability = float("nan")
    report = validate_batch([bad, _q("m2", 60)])
    assert report.blocked
    assert any(f.check == "probability_sanity" for f in report.blocks)


def test_collapsed_model_blocks():
    """The classic silent failure: a lookup returns None, everything falls back
    to one default, and the batch is uniform. Normal one row at a time."""
    batch = [_q(f"m{i}", 50) for i in range(40)]
    report = validate_batch(batch)
    assert report.blocked
    assert any(f.check == "output_diversity" for f in report.blocks)


def test_large_base_rate_deviation_warns_but_does_not_block():
    batch = [_q(f"m{i}", 40 + (i * 7) % 40) for i in range(40)]
    batch.append(_q("far", 95, base=0.30))
    report = validate_batch(batch)
    assert not report.blocked
    assert any(f.check == "base_rate_collar" for f in report.warnings)
    assert BASE_RATE_COLLAR < 0.5


def test_too_many_extremes_warns():
    batch = [_q(f"m{i}", 96 if i % 3 == 0 else 40 + i % 30) for i in range(40)]
    report = validate_batch(batch)
    assert any(f.check == "extreme_share" for f in report.warnings)


def test_round_number_clustering_warns():
    """Submitting 73 rather than 70 is free differentiation; clustering gives it away."""
    batch = [_q(f"m{i}", [50, 60, 70, 75, 80][i % 5]) for i in range(40)]
    report = validate_batch(batch)
    assert any(f.check == "round_number_clustering" for f in report.warnings)


def test_many_unparsed_questions_block():
    batch = [
        _q(f"m{i}", 45 + i % 20, family=Family.OTHER if i % 2 else Family.TIEBREAK)
        for i in range(40)
    ]
    report = validate_batch(batch)
    assert report.blocked
    assert any(f.check == "unparsed_share" for f in report.blocks)


def test_a_single_unparsed_question_only_warns():
    batch = [_q(f"m{i}", 40 + (i * 7) % 40) for i in range(40)]
    batch.append(_q("odd", 50, family=Family.OTHER))
    report = validate_batch(batch)
    assert not report.blocked
    assert any(f.check == "unparsed_questions" for f in report.warnings)


def test_empty_batch_blocks():
    assert validate_batch([]).blocked


def test_drift_against_the_previous_run_warns():
    batch = [_q(f"m{i}", 80) if i % 2 else _q(f"m{i}", 20) for i in range(20)]
    previous = {f"m{i}": 50 for i in range(20)}
    report = validate_batch(batch, previous=previous)
    assert any(f.check == "run_to_run_drift" for f in report.warnings)


# ------------------------------------------------------------------ coherence


def test_inverted_comparison_pair_blocks():
    """The most expensive single error available: both sides answering the same way.

    P(A>B) + P(B>A) + P(tie) = 1, so two complementary comparisons summing to
    1.8 means one is inverted.
    """
    a = _q("m1", 90, family=Family.ACES_COMPARISON)
    b = _q("m2", 90, family=Family.ACES_COMPARISON)
    finding = check_comparison_complement(a, b)
    assert finding is not None
    assert finding.severity is Severity.BLOCK


def test_complementary_pair_passes():
    a = _q("m1", 55, family=Family.ACES_COMPARISON)
    b = _q("m2", 37, family=Family.ACES_COMPARISON)
    assert check_comparison_complement(a, b) is None


def test_both_sides_answering_the_same_side_blocks():
    a = _q("m1", 20, family=Family.ACES_COMPARISON)
    b = _q("m2", 20, family=Family.ACES_COMPARISON)
    finding = check_comparison_complement(a, b)
    assert finding is not None and finding.severity is Severity.BLOCK


def test_coherence_flags_contradictory_structural_answers():
    batch = [
        _q("m1", 90, family=Family.TIEBREAK),
        _q("m2", 10, family=Family.SET_GAMES),
    ]
    findings = check_match_coherence(batch)
    assert any(f.check == "coherence_tiebreak_set_length" for f in findings)


def test_coherence_passes_on_a_consistent_match():
    batch = [
        _q("m1", 55, family=Family.TIEBREAK),
        _q("m2", 52, family=Family.SET_GAMES),
        _q("m3", 30, family=Family.DECIDING_SET),
        _q("m4", 60, family=Family.LOPSIDED_SET),
    ]
    assert check_match_coherence(batch) == []


# ------------------------------------------------- coverage / tripwire checks


def test_missing_questions_block():
    """A dropped question is never submitted, and an unanswered question is the
    worst outcome available: there is no crowd comparison to earn from."""
    from bot.validate import check_question_coverage

    findings = check_question_coverage(
        {"match1": [_q(f"m{i}", 50 + i) for i in range(7)]},
        {"match1": "R32"},  # R32 carries 10 questions
    )
    assert findings and findings[0].severity is Severity.BLOCK
    assert "7" in findings[0].message and "10" in findings[0].message


def test_full_question_coverage_passes():
    from bot.validate import check_question_coverage

    findings = check_question_coverage(
        {"match1": [_q(f"m{i}", 50 + i) for i in range(5)]},
        {"match1": "R128"},  # R128 carries 5
    )
    assert findings == []


def test_orientation_cross_check_flags_a_reversed_answer():
    """_orient returning (None, None) is handled; a near-match on the wrong name
    silently assigns the opponent's rates and inverts the answer."""
    from bot.validate import check_orientation_consistency

    priced = _q("m1", 25, family=Family.ACES_COMPARISON)
    # Subject aces far more often, yet the submission says they are unlikely to.
    finding = check_orientation_consistency(priced, subject_rate=0.14, opponent_rate=0.05)
    assert finding is not None
    assert finding.severity is Severity.WARN


def test_orientation_cross_check_passes_when_consistent():
    from bot.validate import check_orientation_consistency

    priced = _q("m1", 72, family=Family.ACES_COMPARISON)
    assert check_orientation_consistency(priced, subject_rate=0.14, opponent_rate=0.05) is None


def test_orientation_cross_check_tolerates_close_matchups():
    from bot.validate import check_orientation_consistency

    priced = _q("m1", 51, family=Family.ACES_COMPARISON)
    assert check_orientation_consistency(priced, subject_rate=0.05, opponent_rate=0.06) is None


def test_baseline_tripwire_flags_a_model_adding_nothing():
    """A model that cannot beat the base rate has negative expected value, and
    nothing else in the pipeline would notice."""
    from bot.validate import check_baseline_tripwire

    batch = [_q(f"m{i}", 50, base=0.50) for i in range(30)]
    findings = check_baseline_tripwire(batch)
    assert any(f.check == "baseline_tripwire" for f in findings)


def test_baseline_tripwire_flags_one_directional_bias():
    from bot.validate import check_baseline_tripwire

    batch = [_q(f"m{i}", 85, base=0.50) for i in range(40)]
    findings = check_baseline_tripwire(batch)
    assert any(f.check == "baseline_directional_bias" for f in findings)


def test_baseline_tripwire_quiet_on_a_healthy_batch():
    from bot.validate import check_baseline_tripwire

    batch = [_q(f"m{i}", 50 + (15 if i % 2 else -15), base=0.50) for i in range(40)]
    assert check_baseline_tripwire(batch) == []
