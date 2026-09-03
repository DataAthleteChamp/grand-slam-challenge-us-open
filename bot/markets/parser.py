"""Parse raw market question text into a typed :class:`MarketSpec`.

This is the highest-risk component in the system: the exact wording and threshold
values are unknown until markets open on 27 August, and a mis-parsed threshold is a
guaranteed negative-RBP question.

Two rules follow from that:

1. **Never raise.**  An unrecognised question becomes :data:`Family.OTHER` with
   ``confidence = 0.0`` and is answered from the base rate.
2. **Never silently guess.**  Anything unclassified is recorded so it can be
   reviewed and a rule added; see :func:`unparsed_report`.

The design is deliberately a flat list of regex rules rather than a clever
classifier, because the failure mode we care about is *wrong* parsing, not
*missing* parsing, and rules are auditable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bot.data.names import strip_accents

from .taxonomy import COMPARISON_STAT, Family


@dataclass(frozen=True)
class MarketSpec:
    """A typed interpretation of one market question."""

    family: Family
    raw: str
    threshold: float | None = None
    comparator: str = "gt"
    set_number: int | None = None
    subject: str | None = None
    opponent: str | None = None
    confidence: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_understood(self) -> bool:
        """True when the question was classified with usable confidence."""
        return self.family is not Family.OTHER and self.confidence >= 0.5

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family.value,
            "threshold": self.threshold,
            "comparator": self.comparator,
            "set_number": self.set_number,
            "subject": self.subject,
            "opponent": self.opponent,
            "confidence": self.confidence,
            "notes": list(self.notes),
            "raw": self.raw,
        }


_NUMBER = r"(\d+(?:\.\d+)?)"

# Ordered most-specific first; the first match wins.
#
# Duration is tested before the score families on purpose, and the score pattern
# now requires a hyphen with digit boundaries. The earlier ``6-?0|6-?1|6-?2``
# made the hyphen optional, so "60" inside "160 minutes" matched and every
# duration question with a 6 in the tens place was silently routed to the
# lopsided-set family and priced by the wrong model.
_RULES: list[tuple[Family, re.Pattern[str], float]] = [
    (
        Family.MATCH_DURATION,
        re.compile(r"\bminutes?\b|\bhours?\b|\blast longer\b|\bduration\b", re.I),
        0.9,
    ),
    (
        Family.MATCH_WINNER,
        re.compile(r"\bwin\s+the\s+match\b|\bwin\?|\bto\s+win\b|\bwins\s+the\s+match\b", re.I),
        0.95,
    ),
    (Family.TIEBREAK, re.compile(r"\btie[\s-]?break", re.I), 0.95),
    (
        Family.DECIDING_SET,
        re.compile(r"\bdeciding set\b|\bgo the distance\b|\bfinal set\b", re.I),
        0.9,
    ),
    (
        Family.LOPSIDED_SET,
        re.compile(
            r"\blopsided\b|\bbagel\b|\bbreadstick\b|(?<!\d)6-[012](?!\d)"
            r"|\bwin\s+a\s+set\s+6\b",
            re.I,
        ),
        0.9,
    ),
    (Family.SET_GAMES, re.compile(r"\bset\b.*\bgames?\b|\bgames?\b.*\bset\b", re.I), 0.8),
    (Family.ACES_COMPARISON, re.compile(r"\baces?\b", re.I), 0.9),
    (Family.DOUBLE_FAULTS_COMPARISON, re.compile(r"\bdouble[\s-]?faults?\b", re.I), 0.95),
    (Family.WINNERS_COMPARISON, re.compile(r"\bwinners?\b", re.I), 0.75),
    (
        Family.FIRST_SERVE_COMPARISON,
        re.compile(
            r"first[\s-]?serve|points?\s+on\s+(?:their\s+)?serve|"
            r"service points?|serve points?|points? behind (?:their|the) serve",
            re.I,
        ),
        0.85,
    ),
    (Family.UNFORCED_ERRORS, re.compile(r"\bunforced\b|\berrors?\b", re.I), 0.9),
]

#: Marks a two-sided comparison. The subject is named before it.
_COMPARISON_MARKER = re.compile(r"\bthan\b|\bcompared with\b|\bversus\b|\bvs\.?\b")

_SET_ORDINAL = re.compile(r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\s+set\b", re.I)
_ORDINALS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
}

# Ordered so that the inclusive-upper forms are tested BEFORE the plain "more
# than" form. Otherwise the ``more than`` alternation matches the tail of "no
# more than 150", reporting a strictly-greater question as the exact opposite of
# what was asked.
_THRESHOLD_PATTERNS = [
    (re.compile(rf"(?:at most|no more than|no longer than|within)\s*{_NUMBER}", re.I), "lte"),
    (re.compile(rf"{_NUMBER}\s*(?:or more|\+|or above)", re.I), "gte"),
    (re.compile(rf"(?:at least|minimum of)\s*{_NUMBER}", re.I), "gte"),
    (re.compile(rf"{_NUMBER}\s*(?:or fewer|or less|or below)", re.I), "lte"),
    (re.compile(rf"(?:more than|over|above|longer than)\s*{_NUMBER}", re.I), "gt"),
    (re.compile(rf"(?:fewer than|less than|under|below|shorter than)\s*{_NUMBER}", re.I), "lt"),
    (re.compile(rf"(?:exactly)\s*{_NUMBER}", re.I), "eq"),
]

#: Direction words in a head-to-head comparison, which carries no number.
#: "Will A record FEWER aces than B" is the complement of the modelled
#: ``P(A > B)`` and without this is submitted uncomplemented.
_COMPARISON_DIRECTION = re.compile(r"\b(fewer|less|lower|worse)\b", re.I)

_UNPARSED: list[str] = []


#: Unicode dashes the platform uses interchangeably with a plain hyphen.
#:
#: Round 3 introduced "Will either player win a set 6-2, 6-1 or 6-0?" written with
#: EN DASHES (U+2013), and every scoreline pattern here was written with an ASCII
#: hyphen.  Nothing matched, so twelve markets fell through to their base rate
#: while a perfectly good model for them sat unused.  Normalising once at the
#: entry point protects every pattern rather than the one that happened to break,
#: which matters because later rounds score at 2x and 3x and the platform is
#: evidently willing to introduce new question types mid-event.
_DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                         "\u2014": "-", "\u2015": "-", "\u2212": "-"})


def normalise_dashes(text: str) -> str:
    """Fold every Unicode dash variant onto a plain hyphen."""
    return text.translate(_DASHES)


def parse_question(text: str, *, players: tuple[str, str] | None = None) -> MarketSpec:
    """Classify one market question.  Never raises."""
    raw = normalise_dashes((text or "").strip())
    if not raw:
        return MarketSpec(family=Family.OTHER, raw=raw, notes=("empty question",))

    family, confidence = _classify(raw)
    threshold, comparator = _extract_threshold(raw)
    set_number = _extract_set_number(raw)
    subject, opponent = _extract_players(raw, players)

    # A head-to-head comparison carries no number, so _extract_threshold finds
    # no comparator and leaves the default "gt". "Will A record FEWER aces than
    # B" is then submitted as P(A > B) — the exact complement of the answer.
    if family in COMPARISON_STAT and _COMPARISON_DIRECTION.search(raw):
        comparator = "lt"

    notes: list[str] = []
    if family in COMPARISON_STAT and subject is None:
        # A comparison without an identifiable subject cannot be priced
        # directionally; flag it rather than assuming an ordering.
        notes.append("comparison subject not identified")
        confidence = min(confidence, 0.4)
    if family is Family.OTHER:
        _UNPARSED.append(raw)
        notes.append("unclassified — will be priced from the base rate")
    if family in {Family.SET_GAMES, Family.UNFORCED_ERRORS} and threshold is None:
        notes.append("threshold not found — verify wording")
        confidence = min(confidence, 0.4)

    return MarketSpec(
        family=family,
        raw=raw,
        threshold=threshold,
        comparator=comparator,
        set_number=set_number,
        subject=subject,
        opponent=opponent,
        confidence=confidence,
        notes=tuple(notes),
    )


def _classify(text: str) -> tuple[Family, float]:
    """Return the first matching family, preferring more specific rules."""
    # Double faults must beat the generic "faults"/aces rules, and unforced errors
    # must beat the bare "winners" rule when both words appear.
    if re.search(r"\bdouble[\s-]?faults?\b", text, re.I):
        return Family.DOUBLE_FAULTS_COMPARISON, 0.95
    if re.search(r"\bunforced\b", text, re.I):
        return Family.UNFORCED_ERRORS, 0.92
    for family, pattern, confidence in _RULES:
        if pattern.search(text):
            return family, confidence
    return Family.OTHER, 0.0


def _extract_threshold(text: str) -> tuple[float | None, str]:
    """Pull a numeric threshold and its comparator out of the question text."""
    for pattern, comparator in _THRESHOLD_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return float(match.group(1)), comparator
            except (TypeError, ValueError):
                continue
    # A bare number is a weak signal, but better than nothing for O/U style lines.
    bare = re.search(rf"\b{_NUMBER}\b", text)
    if bare:
        try:
            return float(bare.group(1)), "gt"
        except (TypeError, ValueError):
            return None, "gt"
    return None, "gt"


def _extract_set_number(text: str) -> int | None:
    match = _SET_ORDINAL.search(text)
    if not match:
        return None
    return _ORDINALS.get(match.group(1).lower())


def _extract_players(text: str, players: tuple[str, str] | None) -> tuple[str | None, str | None]:
    """Identify which player a question is about.

    Two shapes occur, and conflating them inverts answers:

    *Comparisons* name both players either side of "than" - "Will A record more
    aces than B?".  The subject is the one named *before* the comparison word.
    Taking simply "the first player found" is wrong when only one of the two
    matched, because whichever player happened to match becomes the subject and
    a question about the other one is answered backwards.

    *Single-subject* questions name one player - "Will A win?".  Here exactly one
    match is correct and demanding two would leave every match-winner question
    unoriented, which silently forfeits a fifth of the competition.

    Matching folds accents: the API names a match "M. Čilić vs A. Rublev" and may
    ask its questions using either spelling.
    """
    if not players:
        return None, None

    folded = strip_accents(text).lower()
    positions: dict[str, int] = {}
    for name in players:
        surname = strip_accents(name.split()[-1]).lower() if name else ""
        if not surname:
            continue
        found = re.search(rf"\b{re.escape(surname)}\b", folded)
        if found:
            positions[name] = found.start()

    if not positions:
        return None, None

    comparison = _COMPARISON_MARKER.search(folded)
    if comparison is None:
        # Single-subject question. One name is the whole answer; two names means
        # we cannot tell which is meant.
        if len(positions) != 1:
            return None, None
        subject = next(iter(positions))
        return subject, next((n for n in players if n != subject), None)

    # Comparison. The subject sits before the comparison word.
    pivot = comparison.start()
    before = [n for n, at in positions.items() if at < pivot]
    if len(before) != 1:
        # Either both names precede "than" (unparseable) or the only name we
        # found sits after it, meaning the real subject is someone we failed to
        # match. Refusing costs a base-rate answer; guessing costs an inversion.
        return None, None
    subject = before[0]
    return subject, next((n for n in players if n != subject), None)


def unparsed_report() -> list[str]:
    """Return distinct question texts the parser could not classify.

    Run this after every market refresh.  An empty list means full coverage; a
    non-empty one is a to-do list, not a warning to ignore.
    """
    seen: dict[str, None] = {}
    for text in _UNPARSED:
        seen.setdefault(text, None)
    return list(seen)


def reset_unparsed() -> None:
    """Clear the unparsed buffer (used by tests)."""
    _UNPARSED.clear()
