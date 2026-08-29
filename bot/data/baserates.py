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

#: Per-match count statistics and their measured tie rates, **split by tour**.
#: ``even_p_a_greater`` is the fair price for "A records strictly more than B"
#: between two identical players — emphatically not 50.
#:
#: Splitting matters more than it looks.  Women hit less than half as many aces
#: per match as men (2.9 against 7.0), so ace ties are nearly twice as common:
#: P(tie) 0.145 against 0.080, moving an even matchup from 0.460 to 0.427.
#: Applying the men's constant to the women's draw would have mispriced every ace
#: comparison in half the event by more than three points, invisibly.
#:
#: Measured over 7,545 charted men's and 4,070 charted women's matches.
COUNT_STATS = {
    "ATP": {
        "aces": {"mean_per_player": 7.0, "p_tie": 0.080, "even_p_a_greater": 0.460},
        "double_faults": {"mean_per_player": 2.9, "p_tie": 0.158, "even_p_a_greater": 0.421},
        "winners": {"mean_per_player": 29.1, "p_tie": 0.029, "even_p_a_greater": 0.485},
        "unforced_errors": {"mean_per_player": 29.0, "p_tie": 0.030, "even_p_a_greater": 0.485},
    },
    "WTA": {
        "aces": {"mean_per_player": 2.9, "p_tie": 0.145, "even_p_a_greater": 0.427},
        "double_faults": {"mean_per_player": 3.4, "p_tie": 0.134, "even_p_a_greater": 0.433},
        "winners": {"mean_per_player": 20.7, "p_tie": 0.031, "even_p_a_greater": 0.484},
        "unforced_errors": {"mean_per_player": 27.3, "p_tie": 0.036, "even_p_a_greater": 0.482},
    },
}


def count_stats(stat: str, tour: str = "ATP") -> dict[str, float]:
    """Measured count statistics for one statistic on one tour.

    Always pass the tour.  The men's and women's constants differ enough on aces
    to flip a submission by three points.
    """
    return COUNT_STATS[tour.upper()][stat]


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
    "short": +0.0055,  # previous match ~107 minutes
    "medium": +0.0009,  # ~143 minutes
    "long": -0.0043,  # ~180 minutes
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


# --------------------------------------------------- serve-rate dispersion

#: Extra match-to-match dispersion in a player's share of service points won,
#: beyond what binomial sampling over the match's service points explains.
#:
#: Measured on hard-court matches 2023-2026 (ATP n=11,960, WTA n=11,597) after
#: subtracting the opponent-adjusted prediction, so this is the residual a model
#: that already knows both players cannot remove.  Total residual sd came to
#: 1.35x (ATP) and 1.37x (WTA) the binomial-only sd, and the two tours agreeing
#: to within 0.02 on an independently computed quantity is the reason these are
#: used rather than a round number.
#:
#: Ignoring this term would treat a serve rate as if it were a clean binomial
#: draw and make every rate threshold roughly 35% more confident than the data
#: supports - which pushes numbers toward 1 and 99 exactly where being wrong
#: costs the most.
SERVE_RATE_FORM_SD = {"ATP": 0.0515, "WTA": 0.0575}

#: The opponent-adjusted prediction runs 0.7pp low on ATP hard courts and is
#: unbiased on WTA.  Small, but free to correct.
SERVE_RATE_BIAS = {"ATP": 0.0070, "WTA": 0.0004}

#: Correlation between the opponent-adjusted prediction and the realised match
#: serve rate, which is what justifies pricing this family off the model at all.
SERVE_RATE_SKILL_CORRELATION = {"ATP": 0.481, "WTA": 0.461}


#: Unconditional distribution of a player's share of service points won in a
#: hard-court match (2025-2026, ATP n=5,290, WTA n=5,165).
#:
#: This is the shrinkage target for absolute rate thresholds.  It is emphatically
#: not 0.5: "will this player clear 55% of service points" has an unconditional
#: answer near 0.88 on the men's tour, and pulling an uncertain estimate toward
#: 0.5 moves it away from the truth rather than toward it.  Getting the anchor
#: wrong costs more than getting the model wrong, because the anchor is what the
#: answer decays to exactly when the model is least trusted.
SERVE_RATE_MARGINAL = {
    "ATP": {"mean": 0.6502, "sd": 0.0855},
    "WTA": {"mean": 0.5715, "sd": 0.0890},
}


def serve_rate_base(threshold: float, tour: str) -> float:
    """Unconditional P(a player's service-point win rate exceeds ``threshold``)."""
    import math

    marginal = SERVE_RATE_MARGINAL.get(tour, SERVE_RATE_MARGINAL["ATP"])
    z = (threshold - marginal["mean"]) / marginal["sd"]
    return 0.5 * math.erfc(z / math.sqrt(2.0))


# ------------------------------------------------------- tiebreak counts

#: ``P(a match contains at least K tiebreaks)`` on hard-court slams,
#: 2015-2026 (ATP n=2,779 best-of-five, WTA n=2,883 best-of-three).
#:
#: The live questions ask for at least one *or at least two*, and 23 of the 64
#: Round 1 markets asked for two.  Two tiebreaks are roughly 3.7 times rarer
#: than one on the men's side and eleven times rarer on the women's, so
#: answering both from the any-tiebreak rate would have been badly wrong on a
#: third of the tiebreak markets.
#:
#: Retirements and walkovers are excluded: a match that stops early has no
#: honest tiebreak count.  Both score spellings are handled - pre-2024 files
#: write ``7-6(6)`` and 2024+ write a bare ``7-6``, and a parser that handles
#: only one silently drops every tiebreak match.
TIEBREAK_COUNTS = {
    "ATP": {1: 0.492, 2: 0.134, 3: 0.025},
    "WTA": {1: 0.220, 2: 0.020, 3: 0.001},
}


def tiebreak_count_base(count: int, tour: str) -> float:
    """Unconditional ``P(at least `count` tiebreaks)`` for a tour."""
    table = TIEBREAK_COUNTS.get(tour, TIEBREAK_COUNTS["ATP"])
    if count <= 0:
        return 1.0
    if count in table:
        return table[count]
    # Beyond the measured range each extra tiebreak is roughly five times
    # rarer; the questions only ask for one or two, so this is a guard rather
    # than a model.
    deepest = max(table)
    # Clamped because the exponent is attacker-controlled in practice: a
    # threshold from another family can arrive here, and 5.0 ** 99997 raises
    # OverflowError rather than returning a small number.
    steps = min(count - deepest, 40)
    return table[deepest] / (5.0**steps)


# ----------------------------------------------------------- match duration

#: ``P(match lasts longer than N minutes)`` on hard-court slams, 2015-2026
#: (ATP n=2,910 best-of-five, WTA n=2,649 best-of-three).
#:
#: The live questions set a per-match line anywhere from 70 to 170 minutes, and
#: the correct answer at a given line differs enormously by draw: "longer than
#: 150 minutes" is 0.514 on the men's side and 0.083 on the women's.  The pricer
#: previously anchored every duration question at 0.5 regardless of tour or
#: threshold, which is the same class of error as anchoring a rate threshold at
#: 0.5 - and the anchor is what the answer decays to exactly when the model is
#: least trusted.
#:
#: Stored empirically rather than as a fitted lognormal.  The lognormal tracks
#: the men's curve to within 0.01 but misses the women's by up to 0.043 around
#: the median, which is precisely where the questions are set.
DURATION_SURVIVAL = {
    "ATP": {
        50: 0.9948,
        55: 0.9931,
        60: 0.9918,
        65: 0.99,
        70: 0.9883,
        75: 0.9845,
        80: 0.9787,
        85: 0.968,
        90: 0.9546,
        95: 0.9347,
        100: 0.912,
        105: 0.8787,
        110: 0.8471,
        115: 0.8089,
        120: 0.7643,
        125: 0.7179,
        130: 0.6715,
        135: 0.6333,
        140: 0.5904,
        145: 0.5519,
        150: 0.5144,
        155: 0.4821,
        160: 0.446,
        165: 0.4141,
        170: 0.3808,
        175: 0.3502,
        180: 0.3162,
        185: 0.2883,
        190: 0.2636,
        195: 0.2371,
        200: 0.2113,
        205: 0.1835,
        210: 0.1577,
        215: 0.1371,
        220: 0.1196,
        225: 0.1034,
        230: 0.0873,
        235: 0.0766,
        240: 0.0622,
        245: 0.0502,
        250: 0.0399,
        255: 0.0337,
        260: 0.0268,
    },
    "WTA": {
        50: 0.9864,
        55: 0.9641,
        60: 0.923,
        65: 0.8637,
        70: 0.7946,
        75: 0.7157,
        80: 0.6368,
        85: 0.5685,
        90: 0.5055,
        95: 0.4504,
        100: 0.3994,
        105: 0.3511,
        110: 0.3194,
        115: 0.2839,
        120: 0.2529,
        125: 0.2193,
        130: 0.185,
        135: 0.157,
        140: 0.1284,
        145: 0.1038,
        150: 0.0827,
        155: 0.0634,
        160: 0.0487,
        165: 0.0362,
        170: 0.0283,
        175: 0.0185,
        180: 0.0155,
        185: 0.0102,
        190: 0.0079,
        195: 0.0053,
        200: 0.003,
        205: 0.003,
        210: 0.0026,
        215: 0.0019,
        220: 0.0008,
        225: 0.0004,
        230: 0.0004,
        235: 0.0004,
        240: 0.0004,
        245: 0.0004,
        250: 0.0004,
        255: 0.0004,
        260: 0.0004,
    },
}


def duration_base(minutes: float, tour: str) -> float:
    """Unconditional ``P(match runs longer than `minutes`)``, interpolated.

    Linear interpolation between five-minute grid points, clamped at both ends.
    The questions are set on the same five-minute grid, so interpolation is
    usually exact rather than approximate.
    """
    table = DURATION_SURVIVAL.get(tour, DURATION_SURVIVAL["ATP"])
    keys = sorted(table)
    if minutes <= keys[0]:
        return table[keys[0]]
    if minutes >= keys[-1]:
        return table[keys[-1]]
    for low, high in zip(keys, keys[1:], strict=False):
        if low <= minutes <= high:
            if high == low:
                return table[low]
            weight = (minutes - low) / (high - low)
            return table[low] + weight * (table[high] - table[low])
    return 0.5


# ------------------------------------------------- count shrinkage priors

#: Per-service-point rates a player with no record is assumed to have,
#: measured over our own player table (players with >=30 matches: ATP n=484,
#: WTA n=169).
#:
#: The pricer previously derived this by dividing a per-match count measured on
#: mixed-format charted matches by best-of-five slam service points.  For the
#: women's draw the two happen to agree, but for the men's the prior came out
#: 0.02589 against an observed 0.03858 - 33% low - so every low-sample ATP
#: player was dragged well below their true double-fault rate, biasing the
#: answer toward whichever player had fewer recorded matches.
#:
#: Ace rate is deliberately absent: it is predicted far better by height than by
#: a population mean, and ``height_prior_ace_rate`` is used instead.
COUNT_RATE_PRIOR = {
    ("ATP", "double_faults"): 0.03858,
    ("WTA", "double_faults"): 0.05083,
}
