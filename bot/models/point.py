"""Analytic point -> game -> set -> match tennis model.

Everything here is exact enumeration, not simulation, so results are deterministic
and fast enough to run across a whole draw.  The Monte Carlo layer in
:mod:`bot.models.simulate` reuses these primitives and adds point-level detail
(aces, double faults, duration) that closed forms cannot express.

References
----------
O'Malley (2008), *Probability Formulas and Statistical Analysis in Tennis*.
Barnett & Clarke (2005), *Combining player statistics to predict outcomes*.
Klaassen & Magnus (2001), *Are Points in Tennis Independent and Identically
Distributed?* — points are very slightly non-iid; the deviation is small enough
that the iid model remains the right backbone.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

#: Tour-average share of service points won on hard court.
TOUR_SPW = {"ATP": 0.645, "WTA": 0.585}

#: Clamp for per-point serve probabilities; keeps the recursions well behaved.
P_CLIP = (0.05, 0.95)

#: Set scores counted as "lopsided" by the competition (6-0, 6-1, 6-2).
LOPSIDED_LOSER_GAMES = (0, 1, 2)


def clip(value: float, low: float = P_CLIP[0], high: float = P_CLIP[1]) -> float:
    """Clamp ``value`` into the supported probability range."""
    return max(low, min(high, value))


def serve_point_prob(spw_server: float, rpw_returner: float, tour: str = "ATP") -> float:
    """Matchup-specific probability that the server wins a point.

    The Barnett-Clarke additive form: start from the tour baseline, add the
    server's edge over it, subtract the returner's edge over the average returner.

    Parameters
    ----------
    spw_server:
        Server's share of service points won (surface-adjusted).
    rpw_returner:
        Returner's share of return points won (surface-adjusted).
    tour:
        ``"ATP"`` or ``"WTA"`` — selects the baseline.
    """
    base = TOUR_SPW[tour]
    serve_edge = spw_server - base
    return_edge = rpw_returner - (1.0 - base)
    return clip(base + serve_edge - return_edge)


def game_prob(p: float) -> float:
    """Probability the server holds, given per-point win probability ``p``.

    Closed form: win to love / 15 / 30, plus reaching deuce and winning from there.
    """
    q = 1.0 - p
    to_love = p**4
    to_15 = 4 * p**4 * q
    to_30 = 10 * p**4 * q**2
    deuce_reached = 20 * p**3 * q**3
    win_from_deuce = p**2 / (p**2 + q**2)
    return to_love + to_15 + to_30 + deuce_reached * win_from_deuce


def tiebreak_prob(pa: float, pb: float, target: int = 7) -> float:
    """Probability player A wins a tiebreak played to ``target`` points, win by two.

    Serve order is A, then B twice, then A twice, and so on.  ``target`` is 7 for a
    normal set tiebreak and 10 for the final-set match tiebreak used at all Slams
    since 2022.
    """
    pa, pb = clip(pa), clip(pb)

    @cache
    def rec(a: int, b: int) -> float:
        if a >= target and a - b >= 2:
            return 1.0
        if b >= target and b - a >= 2:
            return 0.0
        if a >= target - 1 and b >= target - 1:
            # At or past 6-6 the score cycles in pairs of points; solve the
            # two-point cycle in closed form instead of recursing forever.
            win_two = pa * (1.0 - pb)
            lose_two = (1.0 - pa) * pb
            if win_two + lose_two <= 0.0:
                return 0.5
            return win_two / (win_two + lose_two)
        served = a + b
        a_serves = ((served + 1) // 2) % 2 == 0
        p = pa if a_serves else 1.0 - pb
        return p * rec(a + 1, b) + (1.0 - p) * rec(a, b + 1)

    result = rec(0, 0)
    rec.cache_clear()
    return result


#: ``(games_a, games_b, went_to_tiebreak) -> probability``
SetDistribution = dict[tuple[int, int, bool], float]


def set_distribution(
    pa: float, pb: float, a_serves_first: bool = True, tiebreak_to: int = 7
) -> SetDistribution:
    """Full distribution over terminal set scores.

    Returning the whole distribution — rather than just a win probability — is what
    lets one pass answer the lopsided-set, set-length and tiebreak questions
    together, and keeps them mutually consistent by construction.
    """
    hold_a = game_prob(pa)
    hold_b = game_prob(pb)
    tb_a = (
        tiebreak_prob(pa, pb, tiebreak_to)
        if a_serves_first
        else 1.0 - tiebreak_prob(pb, pa, tiebreak_to)
    )

    dist: SetDistribution = {}

    def walk(a: int, b: int, prob: float) -> None:
        if prob < 1e-14:
            return
        if (a >= 6 and a - b >= 2) or (a == 7 and b == 5):
            dist[(a, b, False)] = dist.get((a, b, False), 0.0) + prob
            return
        if (b >= 6 and b - a >= 2) or (b == 7 and a == 5):
            dist[(a, b, False)] = dist.get((a, b, False), 0.0) + prob
            return
        if a == 6 and b == 6:
            dist[(7, 6, True)] = dist.get((7, 6, True), 0.0) + prob * tb_a
            dist[(6, 7, True)] = dist.get((6, 7, True), 0.0) + prob * (1.0 - tb_a)
            return
        # Serve alternates every game; A serves the even-indexed games iff A
        # served first.
        a_on_serve = ((a + b) % 2 == 0) == a_serves_first
        p_a_wins_game = hold_a if a_on_serve else 1.0 - hold_b
        walk(a + 1, b, prob * p_a_wins_game)
        walk(a, b + 1, prob * (1.0 - p_a_wins_game))

    walk(0, 0, 1.0)
    return dist


def set_win_prob(dist: SetDistribution) -> float:
    """Probability player A wins the set, from a set distribution."""
    return sum(p for (ga, gb, _), p in dist.items() if ga > gb)


def match_win_prob(set_prob: float, best_of: int = 3) -> float:
    """Match win probability given a constant per-set win probability.

    Best of 3: ``S^2 (3 - 2S)``.  Best of 5: ``S^3 (10 - 15S + 6S^2)``.
    """
    s = set_prob
    if best_of == 3:
        return s**2 * (3.0 - 2.0 * s)
    if best_of == 5:
        return s**3 * (10.0 - 15.0 * s + 6.0 * s**2)
    raise ValueError("best_of must be 3 or 5")


@dataclass(frozen=True)
class MatchShape:
    """Structural probabilities for one match — the Family A question set."""

    p_a_wins_match: float
    p_any_tiebreak: float
    p_deciding_set: float
    p_any_lopsided_set: float
    p_first_set_10plus_games: float
    p_first_set_tiebreak: float
    p_straight_sets: float
    expected_total_games: float
    expected_sets: float

    def as_dict(self) -> dict[str, float]:
        """Return the shape as a plain dict, rounded for logging."""
        return {
            "p_a_wins_match": round(self.p_a_wins_match, 4),
            "p_any_tiebreak": round(self.p_any_tiebreak, 4),
            "p_deciding_set": round(self.p_deciding_set, 4),
            "p_any_lopsided_set": round(self.p_any_lopsided_set, 4),
            "p_first_set_10plus_games": round(self.p_first_set_10plus_games, 4),
            "p_first_set_tiebreak": round(self.p_first_set_tiebreak, 4),
            "p_straight_sets": round(self.p_straight_sets, 4),
            "expected_total_games": round(self.expected_total_games, 2),
            "expected_sets": round(self.expected_sets, 3),
        }


def match_shape(
    pa: float,
    pb: float,
    best_of: int = 3,
    a_serves_first: bool = True,
    final_set_tiebreak_to: int = 10,
) -> MatchShape:
    """Enumerate the whole match and read off every structural probability.

    All Family A answers come from this single pass, so they are guaranteed to be
    mutually coherent — a set of independently-fitted per-question models is not.

    ``final_set_tiebreak_to`` is 10 at every Grand Slam since 2022.
    """
    pa, pb = clip(pa), clip(pb)
    sets_to_win = best_of // 2 + 1

    normal = {
        True: set_distribution(pa, pb, True, 7),
        False: set_distribution(pa, pb, False, 7),
    }
    decider = {
        True: set_distribution(pa, pb, True, final_set_tiebreak_to),
        False: set_distribution(pa, pb, False, final_set_tiebreak_to),
    }

    first = normal[a_serves_first]
    p_first_10plus = sum(p for (ga, gb, _), p in first.items() if ga + gb >= 10)
    p_first_tb = sum(p for (_, _, tb), p in first.items() if tb)

    totals = {
        "win": 0.0,
        "tb": 0.0,
        "dec": 0.0,
        "lop": 0.0,
        "straight": 0.0,
        "games": 0.0,
        "sets": 0.0,
    }

    def walk(
        sets_a: int,
        sets_b: int,
        serves_first: bool,
        prob: float,
        seen_tb: bool,
        seen_lop: bool,
        games: int,
    ) -> None:
        if prob < 1e-13:
            return
        if sets_a == sets_to_win or sets_b == sets_to_win:
            if sets_a == sets_to_win:
                totals["win"] += prob
            if seen_tb:
                totals["tb"] += prob
            if seen_lop:
                totals["lop"] += prob
            if min(sets_a, sets_b) == 0:
                totals["straight"] += prob
            totals["games"] += prob * games
            totals["sets"] += prob * (sets_a + sets_b)
            return

        is_decider = sets_a == sets_to_win - 1 and sets_b == sets_to_win - 1
        if is_decider:
            totals["dec"] += prob
        table = decider[serves_first] if is_decider else normal[serves_first]

        for (ga, gb, tb), set_p in table.items():
            # Serve carries over: whoever received the last game serves next set.
            next_serves_first = serves_first != ((ga + gb) % 2 == 1)
            walk(
                sets_a + (1 if ga > gb else 0),
                sets_b + (0 if ga > gb else 1),
                next_serves_first,
                prob * set_p,
                seen_tb or tb,
                seen_lop or (min(ga, gb) in LOPSIDED_LOSER_GAMES and max(ga, gb) == 6),
                games + ga + gb,
            )

    walk(0, 0, a_serves_first, 1.0, False, False, 0)

    return MatchShape(
        p_a_wins_match=totals["win"],
        p_any_tiebreak=totals["tb"],
        p_deciding_set=totals["dec"],
        p_any_lopsided_set=totals["lop"],
        p_first_set_10plus_games=p_first_10plus,
        p_first_set_tiebreak=p_first_tb,
        p_straight_sets=totals["straight"],
        expected_total_games=totals["games"],
        expected_sets=totals["sets"],
    )
