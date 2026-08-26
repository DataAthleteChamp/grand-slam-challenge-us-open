"""Taxonomy of Grand Slam Challenge market types.

The competition publishes ten question archetypes but not their exact wording, and
the wording is only visible once markets open.  The parser is therefore built to
*fail loudly and safely*: anything it cannot classify becomes :data:`Family.OTHER`,
which the pricer answers from the base rate rather than guessing.

Families are grouped into three structurally different sets:

``STRUCTURAL``
    Deterministic functions of the match scoreline.  All are read off a single
    simulation so they stay mutually coherent.
``COMPARISON``
    "Does A record more X than B" — small integer counts where ties matter.
``THRESHOLD``
    "Does a player exceed N of X" — a tail probability on one count.
"""

from __future__ import annotations

from enum import Enum


class Family(str, Enum):
    """Market families, matching the ten published question archetypes."""

    TIEBREAK = "tiebreak"
    DECIDING_SET = "deciding_set"
    LOPSIDED_SET = "lopsided_set"
    SET_GAMES = "set_games"
    MATCH_DURATION = "match_duration"
    ACES_COMPARISON = "aces_comparison"
    DOUBLE_FAULTS_COMPARISON = "double_faults_comparison"
    WINNERS_COMPARISON = "winners_comparison"
    FIRST_SERVE_COMPARISON = "first_serve_comparison"
    UNFORCED_ERRORS = "unforced_errors"
    OTHER = "other"


STRUCTURAL = frozenset(
    {
        Family.TIEBREAK,
        Family.DECIDING_SET,
        Family.LOPSIDED_SET,
        Family.SET_GAMES,
        Family.MATCH_DURATION,
    }
)

COMPARISON = frozenset(
    {
        Family.ACES_COMPARISON,
        Family.DOUBLE_FAULTS_COMPARISON,
        Family.WINNERS_COMPARISON,
        Family.FIRST_SERVE_COMPARISON,
    }
)

THRESHOLD = frozenset({Family.UNFORCED_ERRORS})

#: Maps a comparison family to the count statistic it compares.
COMPARISON_STAT = {
    Family.ACES_COMPARISON: "aces",
    Family.DOUBLE_FAULTS_COMPARISON: "double_faults",
    Family.WINNERS_COMPARISON: "winners",
    Family.FIRST_SERVE_COMPARISON: "first_serve_won",
}

#: Round multipliers published in the scoring rules.
ROUND_MULTIPLIER = {
    "R128": 1, "R64": 1, "R32": 2, "R16": 2, "QF": 3, "SF": 3, "F": 3,
}

#: Questions per match by round, used to sanity-check that we saw them all.
QUESTIONS_PER_MATCH = {
    "R128": 5, "R64": 5, "R32": 10, "R16": 10, "QF": 10, "SF": 10, "F": 20,
}
