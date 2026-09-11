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
``OUTCOME``
    "Does A win the match" — the one question bookmakers price directly and
    efficiently, so it is read from the market rather than argued with.
"""

from __future__ import annotations

from enum import StrEnum


class Family(StrEnum):
    """Market families, matching the ten published question archetypes."""

    MATCH_WINNER = "match_winner"
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
    #: Final-only question types.  The final carries twenty questions rather than
    #: ten, and the extra ten introduced wordings no earlier round used.  Until
    #: these were added every one of them fell through to OTHER and was answered
    #: with a flat 50 at the 3x multiplier.
    SETS_IN_A_ROW = "sets_in_a_row"
    BOTH_WIN_SET = "both_win_set"
    EXACT_SCORELINE = "exact_scoreline"
    TOTAL_POINTS = "total_points"
    DISTANCE_COVERED = "distance_covered"
    OTHER = "other"


STRUCTURAL = frozenset(
    {
        Family.TIEBREAK,
        Family.DECIDING_SET,
        Family.LOPSIDED_SET,
        Family.SET_GAMES,
        Family.MATCH_DURATION,
        # All three are deterministic functions of the set sequence, so they are
        # read off the same simulation and stay mutually coherent with the
        # deciding-set and straight-sets answers.
        Family.SETS_IN_A_ROW,
        Family.BOTH_WIN_SET,
        Family.EXACT_SCORELINE,
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

THRESHOLD = frozenset({Family.UNFORCED_ERRORS, Family.TOTAL_POINTS})

#: The match-winner question. Every bookmaker on earth prices this one, the
#: closing line has a Brier of about 0.203, and the predecessor event showed a
#: de-vigged consensus beating the entrant's own model by 566 relative points.
#: Treat the market as the answer and require evidence to depart from it.
OUTCOME = frozenset({Family.MATCH_WINNER})

#: Maps a comparison family to the count statistic it compares.
COMPARISON_STAT = {
    Family.ACES_COMPARISON: "aces",
    Family.DOUBLE_FAULTS_COMPARISON: "double_faults",
    Family.WINNERS_COMPARISON: "winners",
    Family.FIRST_SERVE_COMPARISON: "first_serve_won",
}

#: Round multipliers published in the scoring rules.
ROUND_MULTIPLIER = {
    "R128": 1,
    "R64": 1,
    "R32": 2,
    "R16": 2,
    "QF": 3,
    "SF": 3,
    "F": 3,
}

#: Questions per match by round, used to sanity-check that we saw them all.
QUESTIONS_PER_MATCH = {
    "R128": 5,
    "R64": 5,
    "R32": 10,
    "R16": 10,
    "QF": 10,
    "SF": 10,
    "F": 20,
}
