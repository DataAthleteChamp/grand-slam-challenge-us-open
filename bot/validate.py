"""Pre-submission validation.

Once a market locks, a submission is final.  The failure that costs points is
not a crash — it is a number that is wrong but plausible, submitted, locked, and
indistinguishable from a right one in the logs.  Everything here exists to catch
silent wrongness, and the checks are deliberately biased toward catching a
systematic error across many questions rather than an unusual single number.

The design borrows the pre-trade risk stack used for automated order entry: a
sanity band around a reference price, a limit on how far any one value may sit
from that reference, batch-level fat-finger checks, and a hard block on anything
outside the venue's accepted range.  The reference price here is the measured
base rate.

Checks are either **BLOCK** — refuse to submit, because the number is impossible
or the batch shows a systematic fault — or **WARN**, meaning surface it for
review but let it through.  Blocking on anything merely unusual would be worse
than useless: it would train us to override the checks.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from .markets.taxonomy import COMPARISON, QUESTIONS_PER_MATCH, Family
from .pricer import PricedQuestion


class Severity(StrEnum):
    BLOCK = "block"
    WARN = "warn"


@dataclass(frozen=True)
class Finding:
    """One validation result."""

    check: str
    severity: Severity
    message: str
    market_ids: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        marker = "BLOCK" if self.severity is Severity.BLOCK else "warn "
        return f"[{marker}] {self.check}: {self.message}"


@dataclass
class ValidationReport:
    """The outcome of validating a batch."""

    findings: list[Finding] = field(default_factory=list)
    n_questions: int = 0

    @property
    def blocked(self) -> bool:
        return any(f.severity is Severity.BLOCK for f in self.findings)

    @property
    def blocks(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.BLOCK]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARN]

    def summary(self) -> str:
        if not self.findings:
            return f"{self.n_questions} questions validated, no findings"
        return (
            f"{self.n_questions} questions validated: "
            f"{len(self.blocks)} blocking, {len(self.warnings)} warnings"
        )

    def render(self) -> str:
        """The full report, blocks first, for a terminal."""
        lines = [self.summary()]
        for finding in self.blocks + self.warnings:
            marker = "BLOCK" if finding.severity is Severity.BLOCK else " WARN"
            lines.append(f"  {marker}  {finding.check}: {finding.message}")
        return "\n".join(lines)


# --------------------------------------------------------------------- limits

#: The API rejects 0 and 100 outright.
API_MIN, API_MAX = 1, 99

#: How far a submission may sit from its base rate before it is worth a look.
#: A genuine edge of 30 points is possible; it should just never be silent.
BASE_RATE_COLLAR = 0.30

#: Batch-level fat-finger thresholds.
MAX_SHARE_AT_EXTREMES = 0.15
MAX_SHARE_ROUND_NUMBERS = 0.45
MIN_DISTINCT_SHARE = 0.10
MAX_SHARE_IDENTICAL = 0.30

#: Round numbers the crowd clusters on and we should not.
ROUND_NUMBERS = frozenset({5, 10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 95})


def validate_batch(
    priced: list[PricedQuestion],
    *,
    previous: dict[str, int] | None = None,
    questions_by_match: dict[str, list[PricedQuestion]] | None = None,
    rounds_by_match: dict[str, str] | None = None,
) -> ValidationReport:
    """Run every check over a batch of priced questions.

    This is the only entry point the submission path calls, so every check must
    be wired in here.  A check that exists but is never invoked is worse than no
    check at all: it produces the appearance of coverage while catching nothing,
    and its passing tests reinforce the illusion.
    """
    report = ValidationReport(n_questions=len(priced))
    if not priced:
        report.findings.append(Finding("empty_batch", Severity.BLOCK, "nothing to submit"))
        return report

    for check in (
        _check_api_range,
        _check_probability_sanity,
        _check_base_rate_collar,
        _check_extreme_share,
        _check_round_number_clustering,
        _check_output_diversity,
        _check_confidence_floor,
        _check_unparsed_share,
        check_baseline_tripwire,
    ):
        report.findings.extend(check(priced))

    if previous is not None:
        report.findings.extend(_check_drift(priced, previous))

    # Per-match checks: coherence between related questions, complementary
    # comparison pairs, and whether every question the round should carry
    # actually arrived.
    if questions_by_match:
        for match_questions in questions_by_match.values():
            report.findings.extend(check_match_coherence(match_questions))
            report.findings.extend(_check_comparison_pairs(match_questions))
        if rounds_by_match:
            report.findings.extend(check_question_coverage(questions_by_match, rounds_by_match))

    return report


def _check_comparison_pairs(priced: list[PricedQuestion]) -> list[Finding]:
    """Check every pair of same-family comparisons within one match."""
    findings = []
    by_family: dict[Family, list[PricedQuestion]] = {}
    for question in priced:
        if question.family in COMPARISON:
            by_family.setdefault(question.family, []).append(question)

    for questions in by_family.values():
        for i in range(len(questions)):
            for j in range(i + 1, len(questions)):
                finding = check_comparison_complement(questions[i], questions[j])
                if finding is not None:
                    findings.append(finding)
    return findings


# --------------------------------------------------------------------- checks


def _check_api_range(priced: list[PricedQuestion]) -> list[Finding]:
    """Submissions must be integers the venue will accept."""
    bad = [
        q.market_id
        for q in priced
        if not isinstance(q.submission, int) or not API_MIN <= q.submission <= API_MAX
    ]
    if bad:
        return [
            Finding(
                "api_range",
                Severity.BLOCK,
                f"{len(bad)} submissions outside the accepted range {API_MIN}-{API_MAX}",
                tuple(bad[:10]),
            )
        ]
    return []


def _check_probability_sanity(priced: list[PricedQuestion]) -> list[Finding]:
    """Probabilities must be finite and inside the unit interval.

    A NaN reaching this point means an upstream division produced one and it was
    averaged onward silently, which is the exact class of fault this module is
    for.
    """
    bad = [
        q.market_id
        for q in priced
        if not (0.0 <= q.probability <= 1.0) or q.probability != q.probability
    ]
    if bad:
        return [
            Finding(
                "probability_sanity",
                Severity.BLOCK,
                f"{len(bad)} probabilities are NaN or outside [0, 1]",
                tuple(bad[:10]),
            )
        ]
    return []


def _check_base_rate_collar(priced: list[PricedQuestion]) -> list[Finding]:
    """Flag submissions far from their base rate.

    This is the price collar from pre-trade risk: not a claim that the number is
    wrong, but a requirement that a large deviation be deliberate. A sign
    inversion or a bad join usually shows up here first.
    """
    far = [q for q in priced if abs(q.probability - q.base_rate) > BASE_RATE_COLLAR]
    if not far:
        return []
    worst = max(far, key=lambda q: abs(q.probability - q.base_rate))
    return [
        Finding(
            "base_rate_collar",
            Severity.WARN,
            f"{len(far)} submissions more than {BASE_RATE_COLLAR:.0%} from their base rate "
            f"(worst: {worst.family.value} at {worst.probability:.2f} against "
            f"base {worst.base_rate:.2f})",
            tuple(q.market_id for q in far[:10]),
        )
    ]


def _check_extreme_share(priced: list[PricedQuestion]) -> list[Finding]:
    """Too many confident calls is a symptom, not a strength.

    Brier punishes confident error quadratically, so a batch that is mostly
    extreme is far more likely to be a broken model than an unusually knowable
    day.
    """
    extreme = [q for q in priced if q.submission <= 10 or q.submission >= 90]
    share = len(extreme) / len(priced)
    if share > MAX_SHARE_AT_EXTREMES:
        return [
            Finding(
                "extreme_share",
                Severity.WARN,
                f"{share:.1%} of submissions are below 10 or above 90 "
                f"(threshold {MAX_SHARE_AT_EXTREMES:.0%})",
                tuple(q.market_id for q in extreme[:10]),
            )
        ]
    return []


def _check_round_number_clustering(priced: list[PricedQuestion]) -> list[Finding]:
    """We must not cluster where the crowd clusters.

    Submitting 73 rather than 70 is free differentiation repeated across every
    question. Heavy clustering on multiples of five means something upstream is
    rounding, and the edge is being given away.
    """
    rounds = [q for q in priced if q.submission in ROUND_NUMBERS]
    share = len(rounds) / len(priced)
    if share > MAX_SHARE_ROUND_NUMBERS:
        return [
            Finding(
                "round_number_clustering",
                Severity.WARN,
                f"{share:.1%} of submissions land on round numbers "
                f"(threshold {MAX_SHARE_ROUND_NUMBERS:.0%}); "
                "check for rounding upstream",
            )
        ]
    return []


def _check_output_diversity(priced: list[PricedQuestion]) -> list[Finding]:
    """Detect a model that has silently collapsed to a constant.

    The classic silent failure: a lookup starts returning None, everything falls
    back to one default, and the batch is uniform. It looks entirely normal one
    row at a time.
    """
    findings = []
    counts = Counter(q.submission for q in priced)
    distinct_share = len(counts) / len(priced)
    if len(priced) >= 20 and distinct_share < MIN_DISTINCT_SHARE:
        findings.append(
            Finding(
                "output_diversity",
                Severity.BLOCK,
                f"only {len(counts)} distinct values across {len(priced)} submissions; "
                "the model may have collapsed to a constant",
            )
        )

    value, count = counts.most_common(1)[0]
    if len(priced) >= 20 and count / len(priced) > MAX_SHARE_IDENTICAL:
        findings.append(
            Finding(
                "repeated_value",
                Severity.WARN,
                f"{count / len(priced):.1%} of submissions are exactly {value}",
            )
        )
    return findings


def _check_confidence_floor(priced: list[PricedQuestion]) -> list[Finding]:
    """A batch priced almost entirely at low confidence is not worth submitting blind."""
    low = [q for q in priced if q.confidence < 0.35]
    share = len(low) / len(priced)
    if share > 0.5:
        return [
            Finding(
                "low_confidence_batch",
                Severity.WARN,
                f"{share:.1%} of questions were priced at low confidence; "
                "check that player ratings and market data loaded",
            )
        ]
    return []


def _check_unparsed_share(priced: list[PricedQuestion]) -> list[Finding]:
    """Too many unrecognised questions means the parser needs a rule, not a fallback.

    Falling back to the base rate is safe for one stray question and a silent
    surrender of edge across a whole round.
    """
    unparsed = [q for q in priced if q.family is Family.OTHER]
    share = len(unparsed) / len(priced)
    if share > 0.2:
        return [
            Finding(
                "unparsed_share",
                Severity.BLOCK,
                f"{share:.1%} of questions were not understood; add parser rules before submitting",
                tuple(q.market_id for q in unparsed[:10]),
            )
        ]
    if unparsed:
        return [
            Finding(
                "unparsed_questions",
                Severity.WARN,
                f"{len(unparsed)} questions were not understood and defaulted to the base rate",
                tuple(q.market_id for q in unparsed[:10]),
            )
        ]
    return []


def _check_drift(priced: list[PricedQuestion], previous: dict[str, int]) -> list[Finding]:
    """Compare against the previous run for the same markets.

    Large unexplained moves between runs on unchanged inputs indicate
    nondeterminism or a data problem rather than new information.
    """
    moves = [
        (q.market_id, abs(q.submission - previous[q.market_id]))
        for q in priced
        if q.market_id in previous
    ]
    if not moves:
        return []
    big = [m for m in moves if m[1] > 20]
    if len(big) / len(moves) > 0.25:
        return [
            Finding(
                "run_to_run_drift",
                Severity.WARN,
                f"{len(big)} of {len(moves)} submissions moved more than 20 points "
                "since the last run",
                tuple(m[0] for m in big[:10]),
            )
        ]
    return []


# ------------------------------------------------------------------ coherence


def check_match_coherence(priced: list[PricedQuestion]) -> list[Finding]:
    """Check questions about the same match for logical consistency.

    Coherence is checked rather than enforced. When every structural answer comes
    from one simulation these hold by construction, so a violation means
    something bypassed the simulation — a market read, a fallback, or a bug. That
    makes the check a tripwire on the pipeline rather than a correction to the
    numbers.
    """
    findings = []
    by_family = {q.family: q for q in priced}

    straight = by_family.get(Family.DECIDING_SET)
    lopsided = by_family.get(Family.LOPSIDED_SET)
    tiebreak = by_family.get(Family.TIEBREAK)
    long_set = by_family.get(Family.SET_GAMES)

    # A deciding set and a lopsided set are negatively related but not exclusive;
    # both being near-certain is not impossible, only suspicious.
    if straight and lopsided and straight.probability > 0.8 and lopsided.probability > 0.9:
        findings.append(
            Finding(
                "coherence_decider_lopsided",
                Severity.WARN,
                f"deciding set at {straight.probability:.2f} with lopsided set at "
                f"{lopsided.probability:.2f}; these are negatively correlated in the data",
            )
        )

    # A long first set makes a first-set tiebreak more likely, so a near-certain
    # tiebreak with a very short expected first set is contradictory.
    if tiebreak and long_set and tiebreak.probability > 0.8 and long_set.probability < 0.2:
        findings.append(
            Finding(
                "coherence_tiebreak_set_length",
                Severity.WARN,
                f"tiebreak at {tiebreak.probability:.2f} but a long first set at only "
                f"{long_set.probability:.2f}; a tiebreak requires a set to reach 6-6",
            )
        )

    return findings


def check_question_coverage(
    questions_by_match: dict[str, list[PricedQuestion]],
    rounds_by_match: dict[str, str],
) -> list[Finding]:
    """Verify every match produced the number of questions its round should carry.

    A question that is silently dropped during parsing is never submitted, and an
    unanswered question is the worst possible outcome — strictly worse than a
    mediocre guess, because there is no crowd comparison to earn from.  The
    expected counts already exist in the taxonomy; nothing was checking against
    them.
    """
    findings = []
    for match_id in sorted(set(questions_by_match) | set(rounds_by_match)):
        priced = questions_by_match.get(match_id, [])
        round_code = rounds_by_match.get(match_id, "UNKNOWN")
        expected = QUESTIONS_PER_MATCH.get(round_code)
        if expected is None:
            findings.append(
                Finding(
                    "question_coverage",
                    Severity.BLOCK,
                    f"match {match_id} has unknown round {round_code!r}; "
                    "expected question count cannot be established independently",
                    (match_id,),
                )
            )
            continue
        market_ids = [question.market_id for question in priced]
        if any(not market_id for market_id in market_ids):
            findings.append(
                Finding(
                    "question_coverage",
                    Severity.BLOCK,
                    f"match {match_id} ({round_code}) contains an empty market id",
                    (match_id,),
                )
            )
        if len(set(market_ids)) != len(market_ids):
            findings.append(
                Finding(
                    "question_coverage",
                    Severity.BLOCK,
                    f"match {match_id} ({round_code}) contains duplicate market ids",
                    (match_id,),
                )
            )
        if len(priced) != expected:
            findings.append(
                Finding(
                    "question_coverage",
                    Severity.BLOCK,
                    f"match {match_id} ({round_code}) produced {len(priced)} questions, "
                    f"expected exactly {expected}",
                    (match_id,),
                )
            )
    return findings


def check_orientation_consistency(
    priced: PricedQuestion,
    subject_rate: float | None,
    opponent_rate: float | None,
    *,
    higher_is_yes: bool = True,
) -> Finding | None:
    """Verify a comparison points the same way as the underlying rates.

    ``_orient`` returns ``(None, None)`` on a clean miss, which the pricer
    handles.  The dangerous case is a *near* match on the wrong name, which
    silently assigns the opponent's rates to the subject slot and inverts the
    answer while looking entirely normal.

    This compares the direction of the submission against the direction of the
    inputs that produced it.  It warns rather than blocks, because a genuinely
    close matchup can sit either side of even.
    """
    if priced.family not in COMPARISON:
        return None
    if subject_rate is None or opponent_rate is None:
        return None
    if abs(subject_rate - opponent_rate) < 1e-9:
        return None

    subject_favoured = (subject_rate > opponent_rate) == higher_is_yes
    submission_favours_subject = priced.probability > 0.5
    if subject_favoured != submission_favours_subject and abs(priced.probability - 0.5) > 0.05:
        return Finding(
            "orientation_consistency",
            Severity.WARN,
            f"{priced.family.value} priced at {priced.probability:.2f} but the subject's rate "
            f"({subject_rate:.4f}) points the other way against {opponent_rate:.4f}; "
            "check the player join",
            (priced.market_id,),
        )
    return None


def check_baseline_tripwire(priced: list[PricedQuestion]) -> list[Finding]:
    """Compare the batch against the zero-skill baseline of always answering the base rate.

    Standard forecast-evaluation practice is to run a trivial benchmark alongside
    any real model, because a model that cannot beat the base rate has negative
    expected value under Brier scoring and there is otherwise nothing to notice.

    Two failure shapes are visible here: a batch that never moves from its base
    rates is adding no information, and a batch that moves systematically in one
    direction across every family suggests a sign error rather than an insight.
    """
    findings = []
    deviations = [q.probability - q.base_rate for q in priced]
    if not deviations:
        return findings

    mean_abs = sum(abs(d) for d in deviations) / len(deviations)
    if len(priced) >= 20 and mean_abs < 0.02:
        findings.append(
            Finding(
                "baseline_tripwire",
                Severity.WARN,
                f"submissions sit on average only {mean_abs * 100:.1f} points from their base "
                "rates; the model may not be adding information",
            )
        )

    mean_signed = sum(deviations) / len(deviations)
    if len(priced) >= 30 and abs(mean_signed) > 0.12:
        findings.append(
            Finding(
                "baseline_directional_bias",
                Severity.WARN,
                f"submissions average {mean_signed * 100:+.1f} points from base rate across "
                "the whole batch; a one-directional shift suggests a systematic error "
                "rather than an edge",
            )
        )
    return findings


def check_comparison_complement(
    priced_a: PricedQuestion, priced_b: PricedQuestion
) -> Finding | None:
    """Two comparisons on the same statistic, oriented oppositely, must be complements.

    ``P(A > B) + P(B > A) + P(tie) = 1``, so the two submissions should sum to
    roughly one minus the tie probability. A sum near 1.0 means the tie mass was
    dropped; a sum near 2.0 or 0.0 means one of them is inverted — the most
    expensive single error available on the highest-edge family.

    First serve is deliberately exempt.  Despite the family name it is not a
    head-to-head question in this event: it asks whether each player separately
    clears a threshold of their own ("Zverev more than 78% of first-serve
    points", "Shelton more than 74%").  Those are independent events about
    different players against different lines and are under no obligation to sum
    to anything, so the rule fired on every final and would have trained us to
    pass --force past the genuine inversions it exists to catch.
    """
    if priced_a.family is not priced_b.family or priced_a.family not in COMPARISON:
        return None
    if priced_a.family is Family.FIRST_SERVE_COMPARISON:
        return None
    total = priced_a.probability + priced_b.probability
    if total > 1.05:
        return Finding(
            "comparison_orientation",
            Severity.BLOCK,
            f"complementary {priced_a.family.value} questions sum to {total:.2f}; "
            "at least one is inverted",
            (priced_a.market_id, priced_b.market_id),
        )
    if total < 0.55:
        return Finding(
            "comparison_orientation",
            Severity.BLOCK,
            f"complementary {priced_a.family.value} questions sum to only {total:.2f}; "
            "both appear to be answering the same side",
            (priced_a.market_id, priced_b.market_id),
        )
    return None
