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


# ------------------------------------------------- regressions from review


def test_duration_questions_are_not_parsed_as_lopsided_sets():
    """Regression: ``6-?0`` made the hyphen optional, so "60" inside "160
    minutes" matched and every duration question with a 6 in the tens place was
    silently routed to the lopsided-set family and priced by the wrong model."""
    for minutes in (60, 160, 260, 161, 162):
        spec = parse_question(f"Will the match last more than {minutes} minutes?")
        assert spec.family is Family.MATCH_DURATION, minutes


def test_real_score_patterns_still_parse_as_lopsided():
    for text in ("Will a set end 6-0, 6-1 or 6-2?", "Will a set end in a lopsided score?"):
        assert parse_question(text).family is Family.LOPSIDED_SET


def test_over_and_under_are_complements():
    """Regression: the comparator was parsed but never applied, so "more than
    150 minutes" and "under 150 minutes" both returned 73. A full inversion that
    looks entirely plausible and that nothing downstream can detect."""
    over = _price("Will the match last more than 150 minutes?")
    under = _price("Will the match finish in under 150 minutes?")
    assert over.submission + under.submission == pytest.approx(100, abs=2)
    assert over.submission != under.submission


def test_set_games_comparator_is_applied():
    more = _price("Will the first set contain 10 or more games?")
    fewer = _price("Will the first set contain fewer than 10 games?")
    assert more.submission + fewer.submission == pytest.approx(100, abs=2)


def test_complemented_question_records_the_flip():
    under = _price("Will the match finish in under 150 minutes?")
    assert any("complemented" in note for note in under.notes)


def test_tour_baselines_match_the_fitted_ratings():
    """The ratings are deviations from the fitted baseline, so pairing them with
    the literature value shifts every matchup."""
    from bot.pricer import TOUR_BASE

    assert TOUR_BASE["ATP"] == pytest.approx(0.6321, abs=0.001)
    assert TOUR_BASE["WTA"] == pytest.approx(0.5691, abs=0.001)


def test_validate_batch_runs_the_per_match_checks():
    """Regression: coherence, coverage and complement checks existed but were
    called only by tests. A check that is never invoked is worse than none -
    it creates the appearance of coverage while catching nothing."""
    inverted = [
        _q("m1", 90, family=Family.ACES_COMPARISON),
        _q("m2", 90, family=Family.ACES_COMPARISON),
    ]
    padding = [_q(f"p{i}", 40 + (i * 7) % 40) for i in range(30)]
    report = validate_batch(padding + inverted, questions_by_match={"match1": inverted})
    assert report.blocked
    assert any(f.check == "comparison_orientation" for f in report.blocks)


def test_validate_batch_runs_coverage_when_rounds_are_supplied():
    short = [_q(f"m{i}", 45 + i) for i in range(6)]
    padding = [_q(f"p{i}", 40 + (i * 7) % 40) for i in range(30)]
    report = validate_batch(
        padding + short,
        questions_by_match={"match1": short},
        rounds_by_match={"match1": "R32"},
    )
    assert any(f.check == "question_coverage" for f in report.blocks)


# ------------------------------------------------- serve-rate thresholds


def test_serve_rate_threshold_is_not_priced_as_a_comparison():
    """ "Win more than 65% of points on serve" compares a player to a number,
    not to the opponent. Both answers sit near 50, so pricing it as a
    head-to-head answers a different question invisibly."""
    spec = parse_question(
        "Will Carlos Alcaraz win more than 65% of points on serve?",
        players=("Carlos Alcaraz", "Jannik Sinner"),
    )
    assert spec.family is Family.FIRST_SERVE_COMPARISON
    assert spec.threshold == 65.0


def test_points_on_serve_wording_parses():
    """The published question type is "dominate points on serve", which the
    original ``first[\\s-]?serve`` pattern did not match at all."""
    for text in (
        "Will one player dominate points on serve?",
        "Will A B win more service points than C D?",
        "Will A B win a higher percentage of first serve points?",
    ):
        assert parse_question(text).family is Family.FIRST_SERVE_COMPARISON, text


@pytest.mark.parametrize(
    "tour,threshold,expected",
    [
        ("ATP", 0.55, 0.879),
        ("ATP", 0.65, 0.501),
        ("ATP", 0.70, 0.280),
        ("WTA", 0.55, 0.595),
        ("WTA", 0.65, 0.189),
        ("WTA", 0.70, 0.074),
    ],
)
def test_rate_anchor_matches_the_realised_frequency(tour, threshold, expected):
    """Measured against hard-court 2025-26: every one of these lands within
    0.015 of the frequency actually observed."""
    from bot.data.baserates import serve_rate_base

    assert serve_rate_base(threshold, tour) == pytest.approx(expected, abs=0.005)


def test_rate_anchor_is_not_one_half():
    """Regression: rate thresholds were shrunk toward the comparison base rate
    of 0.5, which moves an uncertain answer away from the truth. At 55% on the
    men's tour the honest anchor is 0.88."""
    from bot.data.baserates import serve_rate_base

    assert serve_rate_base(0.55, "ATP") > 0.85
    assert serve_rate_base(0.70, "WTA") < 0.10


def test_rate_anchor_is_monotone_in_the_threshold():
    from bot.data.baserates import serve_rate_base

    for tour in ("ATP", "WTA"):
        values = [serve_rate_base(t / 100, tour) for t in range(45, 80, 5)]
        assert values == sorted(values, reverse=True), tour


def test_serve_rate_dispersion_exceeds_binomial():
    """Treating a serve rate as a clean binomial draw makes this family about
    35% overconfident, pushing numbers toward 1 and 99 where errors cost most."""
    from bot.data.baserates import SERVE_RATE_FORM_SD

    assert SERVE_RATE_FORM_SD["ATP"] > 0.04
    assert SERVE_RATE_FORM_SD["WTA"] > SERVE_RATE_FORM_SD["ATP"]


def test_a_stronger_server_prices_higher_against_the_same_opponent():
    """Hold the opponent fixed and vary the server.

    The naive version of this test - Opelka vs Sinner, asking about each in
    turn - fails, and correctly so: a serve rate is serve skill minus the
    *opponent's* return skill, and Opelka returns so poorly (-0.049) that Sinner
    wins a larger share of his service points against Opelka (0.749) than Opelka
    does against Sinner (0.642). The big server does not automatically win the
    bigger number.
    """
    from bot.roster import Roster

    roster = Roster.load()
    pricer = Pricer()
    opponent = "Daniil Medvedev"
    results = {}
    for server in ("Reilly Opelka", "Diego Schwartzman"):
        if roster.find(server) is None or roster.find(opponent) is None:
            pytest.skip(f"{server} not in the roster")
        context = roster.context(server, opponent, tour="ATP")
        spec = parse_question(
            f"Will {server} win more than 65% of points on serve?",
            players=(server, opponent),
        )
        results[server] = pricer.price(spec, context, market_id=server).submission
    assert results["Reilly Opelka"] > results["Diego Schwartzman"]


def test_orientation_picks_the_named_player():
    """Getting this backwards turns a correct 42 into a 58 on the family that
    carries the most edge, and a dry run cannot show it.

    This checks the pricer's own lookup, not just the parser's, because the
    parser naming the right player is worth nothing if ``_orient`` then maps it
    onto the wrong profile.
    """
    from bot.roster import Roster

    roster = Roster.load()
    pricer = Pricer()
    for a, b in (("Carlos Alcaraz", "Jannik Sinner"), ("Jannik Sinner", "Carlos Alcaraz")):
        spec = parse_question(f"Will {a} serve more aces than {b}?", players=(a, b))
        assert spec.subject == a
        assert spec.opponent == b

        context = roster.context(a, b, tour="ATP")
        subject, opponent = pricer._orient(spec, context)
        assert subject is not None and subject.name == a
        assert opponent is not None and opponent.name == b


def test_orientation_survives_surname_only_wording():
    """The API labels matches "A. Sabalenka vs I. Swiatek", so the question text
    may carry only a surname."""
    from bot.roster import Roster

    roster = Roster.load()
    pricer = Pricer()
    context = roster.context("Aryna Sabalenka", "Iga Swiatek", tour="WTA")
    spec = parse_question(
        "Will Sabalenka hit more double faults than Swiatek?",
        players=("Aryna Sabalenka", "Iga Swiatek"),
    )
    subject, opponent = pricer._orient(spec, context)
    assert subject is not None and subject.name == "Aryna Sabalenka"
    assert opponent is not None and opponent.name == "Iga Swiatek"


# ------------------------------------------ live families (from the corpus)


def _ctx(a, b, tour="ATP"):
    from bot.roster import Roster

    return Roster.load().context(a, b, tour=tour)


def test_match_winner_parses_and_is_not_the_base_rate():
    """20% of every round asks this and it did not parse at all, so 64 markets
    would have shipped 50 from the base rate."""
    spec = parse_question("Will I. Buse win?", players=("M. Giron", "I. Buse"))
    assert spec.family is Family.MATCH_WINNER
    assert spec.subject == "I. Buse"


def test_match_winner_complements_across_the_two_players():
    pricer = Pricer()
    a, b = "T. Fritz", "D. Blanch"
    first = pricer.price(
        parse_question(f"Will {a} win?", players=(a, b)), _ctx(a, b), market_id="1"
    )
    second = pricer.price(
        parse_question(f"Will {b} win?", players=(b, a)), _ctx(b, a), market_id="2"
    )
    assert first.submission + second.submission == pytest.approx(100, abs=2)
    assert first.submission > second.submission


def test_match_winner_respects_the_named_subject():
    """The simulator only reports p_a_wins_match, so asking about the second
    player means mirroring the match. The live questions name the second player
    more often than the first, so this is the common path."""
    pricer = Pricer()
    favourite = pricer.price(
        parse_question("Will I. Swiatek win?", players=("I. Swiatek", "X. Wang")),
        _ctx("I. Swiatek", "X. Wang", "WTA"),
        market_id="1",
    )
    underdog = pricer.price(
        parse_question("Will X. Wang win?", players=("X. Wang", "I. Swiatek")),
        _ctx("X. Wang", "I. Swiatek", "WTA"),
        market_id="2",
    )
    assert favourite.submission > 60
    assert underdog.submission < 40


@pytest.mark.parametrize(
    "tour,players", [("ATP", ("C. Alcaraz", "J. Sinner")), ("WTA", ("I. Swiatek", "X. Wang"))]
)
def test_tiebreak_count_falls_as_the_count_rises(tour, players):
    """'At least 1' and 'at least 2' are different questions and 23 of 64 Round
    1 markets asked for two. Two tiebreaks are 3.7x rarer than one on the men's
    side and eleven times rarer on the women's."""
    pricer = Pricer()
    context = _ctx(*players, tour=tour)
    values = [
        pricer.price(
            parse_question(f"Will the match feature at least {k} tiebreak(s)?"),
            context,
            market_id=str(k),
        ).submission
        for k in (1, 2, 3)
    ]
    assert values == sorted(values, reverse=True), values
    assert values[0] > values[1]


def test_tiebreak_base_rate_distinguishes_the_count():
    from bot.data.baserates import tiebreak_count_base

    assert tiebreak_count_base(1, "ATP") == pytest.approx(0.492, abs=0.005)
    assert tiebreak_count_base(2, "ATP") == pytest.approx(0.134, abs=0.005)
    assert tiebreak_count_base(1, "WTA") == pytest.approx(0.220, abs=0.005)
    assert tiebreak_count_base(2, "WTA") == pytest.approx(0.020, abs=0.005)
    for tour in ("ATP", "WTA"):
        assert tiebreak_count_base(2, tour) < tiebreak_count_base(1, tour)
        assert tiebreak_count_base(0, tour) == 1.0


@pytest.mark.parametrize(
    "tour,players", [("ATP", ("C. Alcaraz", "R. Opelka")), ("WTA", ("I. Swiatek", "X. Wang"))]
)
def test_duration_falls_as_the_threshold_rises(tour, players):
    """The live thresholds run 70 to 170 minutes and are tailored per match, so
    the whole ladder has to behave, not one fixed value."""
    pricer = Pricer()
    context = _ctx(*players, tour=tour)
    values = [
        pricer.price(
            parse_question(f"Will the match last longer than {n} minutes?"),
            context,
            market_id=str(n),
        ).submission
        for n in (70, 90, 110, 130, 150, 170, 190)
    ]
    assert values == sorted(values, reverse=True), values


def test_every_live_question_shape_parses():
    """The five shapes actually seen in the Round 1 corpus."""
    players = ("M. Giron", "I. Buse")
    expected = {
        "Will the match last longer than 145 minutes?": Family.MATCH_DURATION,
        "Will I. Buse record more aces than M. Giron?": Family.ACES_COMPARISON,
        "Will I. Buse record more double faults than M. Giron?": Family.DOUBLE_FAULTS_COMPARISON,
        "Will I. Buse win?": Family.MATCH_WINNER,
        "Will the match feature at least 2 tiebreak(s)?": Family.TIEBREAK,
    }
    for text, family in expected.items():
        assert parse_question(text, players=players).family is family, text


# --------------------------------------- adversarial input (found in probe)


@pytest.mark.parametrize(
    "question",
    [
        "Will the match last longer than 99999 minutes?",
        "Will the match feature at least 99 tiebreak(s)?",
        "Will the match last longer than -5 minutes?",
        "Will the match feature at least 0 tiebreak(s)?",
        "",
        "?",
        "Will  win?",
        "Will the match",
    ],
)
def test_adversarial_questions_never_crash_and_stay_in_range(question):
    """Regression: 'longer than 99999 minutes' raised OverflowError.

    _base_rate dispatched through a dict literal, which evaluates every value
    eagerly, so a duration question also ran the tiebreak anchor with 99999 as a
    tiebreak count and 5.0 ** 99997 overflowed. The question that crashed was
    not even the question being priced. Dispatch is now lazy.
    """
    from bot.roster import Roster

    context = Roster.load().context("C. Alcaraz", "J. Sinner", tour="ATP")
    result = Pricer().price(parse_question(question), context, market_id="x")
    assert 1 <= result.submission <= 99


def test_tiebreak_base_rate_survives_an_absurd_count():
    from bot.data.baserates import tiebreak_count_base

    for count in (5, 40, 99, 100000):
        value = tiebreak_count_base(count, "ATP")
        assert 0.0 <= value <= 1.0


def test_orientation_folds_accents():
    """Regression: the API names a match 'M. Čilić vs A. Rublev' and then asks
    its own questions about 'M. Cilic'. Raw string comparison failed to orient,
    and an unoriented question falls back to the base rate - which looks like a
    deliberate answer and is a forfeit."""
    from bot.roster import Roster

    roster = Roster.load()
    pricer = Pricer()
    for a, b in (("I. Jović", "M. Frech"), ("M. Čilić", "A. Rublev")):
        spec = parse_question(f"Will {a} win?", players=(a, b))
        subject, opponent = pricer._orient(spec, roster.context(a, b))
        assert subject is not None, a
        assert opponent is not None, b


def test_accented_names_still_complement():
    from bot.roster import Roster

    roster, pricer = Roster.load(), Pricer()
    a, b = "I. Jović", "M. Frech"
    first = pricer.price(
        parse_question(f"Will {a} win?", players=(a, b)), roster.context(a, b), market_id="1"
    )
    second = pricer.price(
        parse_question(f"Will {b} win?", players=(b, a)), roster.context(b, a), market_id="2"
    )
    assert first.submission + second.submission == pytest.approx(100, abs=3)


def test_unrated_players_degrade_to_the_base_rate():
    """Missing data must reduce confidence, not produce a confident number."""
    from bot.roster import Roster

    context = Roster.load().context("A. Nobody", "B. Nobody", tour="ATP")
    for question in (
        "Will A. Nobody win?",
        "Will A. Nobody record more aces than B. Nobody?",
        "Will the match last longer than 150 minutes?",
    ):
        result = Pricer().price(
            parse_question(question, players=("A. Nobody", "B. Nobody")), context, market_id="x"
        )
        assert abs(result.submission - result.base_rate * 100) < 20
        assert result.confidence <= 0.5


# ------------------------------------------- findings from the code review


def test_comparator_applies_to_tiebreak_questions():
    """Latent inversion: 'fewer than 2 tiebreaks' returned 6 where 94 is right.

    An 88-point error, the largest single mistake available in this system.
    Round 1 phrases these positively so it never fired, but a latent inversion
    is the same problem waiting for a rewording.
    """
    from bot.roster import Roster

    context = Roster.load().context("N. Djokovic", "M. Navone", tour="ATP")
    pricer = Pricer()
    at_least = pricer.price(
        parse_question("Will the match feature at least 2 tiebreak(s)?"), context, market_id="1"
    )
    fewer = pricer.price(
        parse_question("Will the match feature fewer than 2 tiebreaks?"), context, market_id="2"
    )
    assert at_least.submission + fewer.submission == pytest.approx(100, abs=2)
    assert fewer.submission > at_least.submission


def test_comparator_applies_to_count_comparisons():
    """'more aces than' and 'fewer aces than' returned an identical number:
    the comparison families had no direction detection at all."""
    from bot.roster import Roster

    context = Roster.load().context("N. Djokovic", "M. Navone", tour="ATP")
    pricer = Pricer()
    players = ("N. Djokovic", "M. Navone")
    more = pricer.price(
        parse_question("Will N. Djokovic record more aces than M. Navone?", players=players),
        context,
        market_id="1",
    )
    fewer = pricer.price(
        parse_question("Will N. Djokovic record fewer aces than M. Navone?", players=players),
        context,
        market_id="2",
    )
    assert more.submission != fewer.submission
    assert more.submission + fewer.submission == pytest.approx(100, abs=2)


@pytest.mark.parametrize(
    "text,comparator",
    [
        ("Will the match last at most 150 minutes?", "lte"),
        ("Will the match last no more than 150 minutes?", "lte"),
        ("Will the match be completed within 150 minutes?", "lte"),
        ("Will the match last no longer than 150 minutes?", "lte"),
        ("Will the match last longer than 150 minutes?", "gt"),
        ("Will the match last more than 150 minutes?", "gt"),
        ("Will the match last under 150 minutes?", "lt"),
        ("Will the match last at least 150 minutes?", "gte"),
    ],
)
def test_inclusive_upper_bounds_are_not_read_as_greater_than(text, comparator):
    """The 'more than' alternation matched the tail of 'no more than 150',
    reporting the exact opposite of what was asked."""
    assert parse_question(text).comparator == comparator


def test_only_one_player_found_in_a_comparison_refuses_to_guess():
    """Taking 'the first player found' made whichever player happened to match
    the subject, so a question about the other one was answered backwards."""
    players = ("Marin Cilic", "Andrey Rublev")
    spec = parse_question("Will Q. Nobody record more aces than A. Rublev?", players=players)
    assert spec.subject is None


def test_match_winner_still_orients_with_one_name():
    """Demanding two names would leave every match-winner question unoriented,
    silently forfeiting a fifth of the competition."""
    players = ("Marin Cilic", "Andrey Rublev")
    for text, expected in (
        ("Will M. Cilic win?", "Marin Cilic"),
        ("Will A. Rublev win?", "Andrey Rublev"),
        ("Will M. Čilić win?", "Marin Cilic"),
    ):
        assert parse_question(text, players=players).subject == expected


def test_ace_comparison_uses_each_players_own_height_prior():
    """Regressing the opponent toward the SUBJECT's height prior made the answer
    depend on which player the question named first. P(A>B)+P(B>A)+P(tie) came to
    1.207 on a live match instead of 1.000."""
    from bot.data.baserates import count_stats
    from bot.models.counts import skellam_comparison
    from bot.models.shrink import CREDIBILITY_K, height_prior_ace_rate, shrink
    from bot.roster import Roster

    roster, pricer = Roster.load(), Pricer()
    a, b = "T. Fritz", "D. Blanch"
    if roster.find(a) is None or roster.find(b) is None:
        pytest.skip("players absent")
    context = roster.context(a, b)

    forward = pricer.price(
        parse_question(f"Will {a} record more aces than {b}?", players=(a, b)),
        context,
        market_id="1",
    ).probability
    backward = pricer.price(
        parse_question(f"Will {b} record more aces than {a}?", players=(b, a)),
        roster.context(b, a),
        market_id="2",
    ).probability

    mu = []
    for name in (a, b):
        profile = roster.find(name)
        prior = height_prior_ace_rate(profile.height, context.tour)
        k = CREDIBILITY_K.get("ace_rate", 50.0)
        mu.append(
            shrink(profile.ace_rate, profile.matches, prior, k).estimate
            * context.expected_service_points
        )
    tie = skellam_comparison(mu[0], mu[1]).p_tie
    count_stats("aces", context.tour)
    assert forward + backward + tie == pytest.approx(1.0, abs=0.05)


def test_double_fault_prior_matches_the_observed_rate():
    """The ATP prior was derived by dividing a mixed-format per-match count by
    best-of-five service points, landing 33% below the rate it shrinks toward
    and dragging every low-sample ATP player down with it."""
    from bot.data.baserates import COUNT_RATE_PRIOR

    assert COUNT_RATE_PRIOR[("ATP", "double_faults")] == pytest.approx(0.0386, abs=0.002)
    assert COUNT_RATE_PRIOR[("WTA", "double_faults")] == pytest.approx(0.0508, abs=0.002)


def test_simulator_grid_matches_its_documented_resolution():
    """Inputs are snapped BEFORE the set distribution is solved, so a coarse
    grid is deterministic bias, not noise. At 0.006 a true 0.4673 snapped to
    0.5000 - a genuine favourite priced as a coin flip."""
    from bot.models.simulate import SimulationConfig, simulate_match

    match = simulate_match(0.65, 0.63, best_of=5, config=SimulationConfig(n_sims=20000, sigma=0.0))
    assert match.p_a_wins_match == pytest.approx(0.6229, abs=0.012)


# ---------------------------------------------- outcome coupling (validated)


def test_outcome_coupling_is_only_applied_where_it_validated():
    """Four families looked promising in-sample. Only one survived a true
    out-of-sample test, and one was actively harmful.

    | family | OOS Brier | 95% CI | verdict |
    |---|---|---|---|
    | ATP aces | +4.3% | [+0.0025, +0.0150] | significant |
    | ATP double faults | +1.7% | [-0.0000, +0.0062] | not significant |
    | WTA aces | -2.7% | [-0.0135, +0.0001] | not significant |
    | WTA double faults | -2.9% | [-0.0125, -0.0040] | HARMFUL |

    Trusting the in-sample fit would have made the women's double-fault family
    measurably worse.
    """
    from bot.data.baserates import outcome_coupling

    assert outcome_coupling("aces", "ATP") == pytest.approx(0.602, abs=0.01)
    assert outcome_coupling("double_faults", "ATP") == 0.0
    assert outcome_coupling("aces", "WTA") == 0.0
    assert outcome_coupling("double_faults", "WTA") == 0.0


def test_coupling_mixes_over_the_unknown_result():
    """The result is unknown at submission time, so the coupling is a mixture
    over both branches, not a shift assuming a winner."""
    from bot.pricer import _mix_over_outcome

    delta = 0.602
    assert _mix_over_outcome(0.5, 0.5, delta) == pytest.approx(0.5, abs=1e-9)
    assert _mix_over_outcome(0.5, 1.0, delta) > 0.6
    assert _mix_over_outcome(0.5, 0.0, delta) < 0.4
    # Symmetric in the win probability around an even model.
    assert _mix_over_outcome(0.5, 0.9, delta) + _mix_over_outcome(0.5, 0.1, delta) == pytest.approx(
        1.0, abs=1e-9
    )


def test_coupling_moves_the_favourite_toward_out_acing():
    from bot.pricer import _mix_over_outcome

    plain = 0.55
    coupled = _mix_over_outcome(plain, 0.90, 0.602)
    assert coupled > plain


def test_a_zero_coupling_changes_nothing():
    from bot.pricer import _mix_over_outcome

    for w in (0.1, 0.5, 0.9):
        assert _mix_over_outcome(0.62, w, 0.0) == pytest.approx(0.62, abs=1e-9)


def test_lanes_differ_structurally_not_just_in_temperature():
    """A temperature transform is monotone, so two lanes differing only in
    temperature always agree on direction. The scenario lane halves its reliance
    on the bookmaker consensus instead, which produces genuine disagreement."""
    from bot.models.lanes import LANE_MARKET_SCALE

    assert LANE_MARKET_SCALE["honest"] == 1.0
    assert LANE_MARKET_SCALE["scenario"] < 0.6

    honest = Pricer(lane="honest")
    scenario = Pricer(lane="scenario")
    assert scenario.market_weights[Family.MATCH_WINNER] < honest.market_weights[Family.MATCH_WINNER]


def test_both_lanes_stay_in_api_range():
    from bot.roster import Roster

    roster = Roster.load()
    context = roster.context("C. Alcaraz", "J. Sinner", tour="ATP")
    ladders = {"match_winner": {context.player_a.name: 0.93, context.player_b.name: 0.07}}
    for lane in ("honest", "scenario"):
        result = Pricer(lane=lane).price(
            parse_question("Will C. Alcaraz win?", players=("C. Alcaraz", "J. Sinner")),
            context,
            market_id="x",
            ladders=ladders,
        )
        assert 1 <= result.submission <= 99


# ------------------------------------------------- ace vulnerability


def test_ace_vulnerability_is_a_distinct_skill():
    """Conceding aces is not the same as winning return points: the two
    correlate only -0.29. Adding it lifts a server-plus-returner model's
    R-squared from 0.367 to 0.465 on the men's draw."""
    from bot.roster import Roster

    roster = Roster.load()
    medvedev = roster.find("D. Medvedev")
    if medvedev is None:
        pytest.skip("player missing")
    assert medvedev.ace_vulnerability < 0.75, "elite returner should concede few aces"


def test_ace_vulnerability_is_damped():
    """The undamped multiplier scores better out-of-sample (+12.9% against
    +8.6%) but says Opelka is 35% to out-ace Medvedev. Opelka out-aces his
    opponent in 93% of hard-court matches and out-aced Medvedev in all four of
    their hard-court meetings, so the fitted optimum is picking up confounding:
    vulnerability is never adjusted for which servers a player faced."""
    from bot.data.baserates import ACE_VULNERABILITY_EXPONENT

    assert 0.3 <= ACE_VULNERABILITY_EXPONENT <= 0.7


def test_ace_vulnerability_applies_to_men_only():
    """Measured +12.9% on the men's draw, -1.2% and not significant on the
    women's, where the far lower ace rate leaves less room for a returner
    effect."""
    from bot.data.baserates import ACE_VULNERABILITY_TOURS

    assert "ATP" in ACE_VULNERABILITY_TOURS
    assert "WTA" not in ACE_VULNERABILITY_TOURS


def test_vulnerability_moves_the_ace_price_the_right_way():
    from bot.roster import Roster

    roster, pricer = Roster.load(), Pricer()
    tough = roster.find("D. Medvedev")
    easy = roster.find("Y. Nishioka")
    if tough is None or easy is None:
        pytest.skip("players missing")
    assert easy.ace_vulnerability > tough.ace_vulnerability

    def price(opponent):
        context = roster.context("R. Opelka", opponent, tour="ATP")
        spec = parse_question(
            f"Will R. Opelka record more aces than {opponent}?",
            players=("R. Opelka", opponent),
        )
        return pricer.price(spec, context, market_id="x").submission

    assert price("Y. Nishioka") > price("D. Medvedev")
