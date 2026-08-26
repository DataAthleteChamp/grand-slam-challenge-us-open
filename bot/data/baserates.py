"""Empirically measured base rates for the US Open question families.

Every number here was computed from match data rather than taken from a published
summary, because the published summaries were wrong in at least one important
place: secondary sources put "share of matches containing a 6-0/6-1/6-2 set" at
20-30%, whereas the data says 65%.  The error came from reasoning about *per-set*
bagel rates and forgetting that a best-of-five match has up to five chances.

Source: ATP and WTA main-tour match results, hard court, 2015-2026
(Sackmann-schema mirror).  Scores were parsed with a tokenizer that accepts both
``7-6(6)`` and bare ``7-6`` notation — older files carry the tiebreak points and
newer ones do not, and a parser that only handles one silently drops every
tiebreak match from half the sample.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Tour-average service hold percentage on hard court.  This single number drives
#: most of the structural difference between the two draws.
HOLD_RATE = {"ATP": 0.805, "WTA": 0.675}


@dataclass(frozen=True)
class StructuralRates:
    """Base rates for the Family A (match shape) questions."""

    n: int
    tiebreak: float
    deciding_set: float
    lopsided_set: float
    first_set_10plus: float

    def as_dict(self) -> dict[str, float]:
        return {
            "tiebreak": self.tiebreak,
            "deciding_set": self.deciding_set,
            "lopsided_set": self.lopsided_set,
            "first_set_10plus": self.first_set_10plus,
        }


#: Hard-court structural base rates by tour and matchup type.
#:
#: ``mismatch`` = a top-8 player against someone ranked 100 or worse, which is the
#: typical first-round seeded match.  ``even`` = both players inside the top 15,
#: the typical quarter-final onward.
STRUCTURAL: dict[str, dict[str, StructuralRates]] = {
    "ATP": {
        "mismatch": StructuralRates(138, 0.377, 0.101, 0.768, 0.406),
        "even": StructuralRates(118, 0.500, 0.229, 0.627, 0.517),
        "all": StructuralRates(2882, 0.487, 0.202, 0.653, 0.515),
    },
    "WTA": {
        "mismatch": StructuralRates(369, 0.179, 0.260, 0.753, 0.331),
        "even": StructuralRates(609, 0.271, 0.366, 0.601, 0.420),
        "all": StructuralRates(16838, 0.225, 0.337, 0.641, 0.427),
    },
}

#: Tiebreak rate by set number.  The first set is the *most* likely to reach 6-6 in
#: every format — deciding sets are the loosest, not the tightest, because serve
#: effectiveness decays and breaks become more likely.  Most forecasters assume the
#: opposite.
TIEBREAK_BY_SET = {
    ("ATP", 5): {1: 0.165, 2: 0.134, 3: 0.116, 4: 0.133, 5: 0.122},
    ("ATP", 3): {1: 0.155, 2: 0.135, 3: 0.151},
    ("WTA", 3): {1: 0.082, 2: 0.071, 3: 0.080},
}

#: Per-match count statistics and their measured tie rates, from 7,545 charted
#: men's matches.  ``even_p_a_greater`` is the fair price for "A records strictly
#: more than B" between two identical players — emphatically not 50.
COUNT_STATS = {
    "aces": {"mean_per_player": 7.0, "p_tie": 0.080, "even_p_a_greater": 0.460},
    "double_faults": {"mean_per_player": 2.9, "p_tie": 0.158, "even_p_a_greater": 0.421},
    "winners": {"mean_per_player": 29.1, "p_tie": 0.029, "even_p_a_greater": 0.485},
    "unforced_errors": {"mean_per_player": 29.0, "p_tie": 0.030, "even_p_a_greater": 0.485},
}

#: Within-match serve decay, measured within-player (so player quality is removed).
#: First-serve effectiveness falls about one point across a five-setter; ace rate is
#: essentially flat, and double faults *fall* rather than rise — tired players serve
#: more conservatively, which is the opposite of the folk theory.
SERVE_DECAY_BO5 = {
    1: {"ace": +0.0005, "df": +0.0013, "first_won": +0.0042},
    2: {"ace": +0.0001, "df": -0.0007, "first_won": +0.0032},
    3: {"ace": -0.0008, "df": +0.0003, "first_won": -0.0035},
    4: {"ace": +0.0004, "df": -0.0005, "first_won": -0.0048},
    5: {"ace": -0.0001, "df": -0.0022, "first_won": -0.0060},
}

#: Carryover fatigue, measured on 1,490 Grand Slam hard-court matches with a
#: previous round in the same event, within-player centred.  A player coming off a
#: four-hour match loses roughly 0.8 points of ace rate relative to one who came
#: through in 90 minutes (p = 0.011).
FATIGUE_ACE_DELTA = {
    "short": +0.0055,   # previous match ~107 minutes
    "medium": +0.0009,  # ~143 minutes
    "long": -0.0043,    # ~180 minutes
    "very_long": -0.0022,  # ~232 minutes
}

#: Question families that should stay near the base rate no matter what a model
#: says.  Unforced errors are scored subjectively and courtside statisticians agree
#: on the forced/unforced call only three or four times in ten, so the classification
#: noise swamps any real signal.  This is the tennis analogue of the fouls market
#: that cost 125 relative points in the Probability Cup.
DEFER_TO_BASE_RATE = frozenset({"unforced_errors"})


def structural(tour: str, matchup: str = "all") -> StructuralRates:
    """Return the structural base rates for a tour and matchup type."""
    return STRUCTURAL[tour][matchup]


def classify_matchup(rank_a: float | None, rank_b: float | None) -> str:
    """Bucket a matchup as ``mismatch``, ``even``, or ``all``.

    ``all`` means "neither bucket applies", not "unknown" — it is the correct
    fallback because it is the unconditional base rate.
    """
    if rank_a is None or rank_b is None:
        return "all"
    high, low = min(rank_a, rank_b), max(rank_a, rank_b)
    if high <= 8 and low >= 100:
        return "mismatch"
    if low <= 15:
        return "even"
    return "all"
