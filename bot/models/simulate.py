"""Monte Carlo match simulator with per-set form variation.

The analytic model in :mod:`bot.models.point` assumes a constant per-point serve
probability for the whole match.  That assumption produces the wrong shape:
measured against 2015-2026 hard-court data, it under-produces lopsided sets
(0.488 against 0.627 observed for evenly matched men) and over-produces deciding
sets.  Real matches contain runs where one player is temporarily far better, and
a constant ``p`` cannot generate them.

Klaassen and Magnus showed directly that points are not independent and
identically distributed, so the fix is to relax that rather than to tune the
base rates until the output looks right.

Each set draws a form shock::

    p_a(set) = p_a + e_a        p_b(set) = p_b + e_b
    (e_a, e_b) ~ N(0, sigma) with correlation rho

A negative ``rho`` means one player's good set tends to be the other's bad one,
which is what produces 6-1 sets between closely matched players.  Both parameters
are fitted to the measured base rates in ``scripts/calibrate_simulator.py``
rather than chosen by hand.

Sets are still solved analytically given the shock, so a simulation draws a set
score from an exact distribution instead of playing out points.  That keeps
50,000 matches well under a second while preserving the exact within-set
structure, and every question is read from the same runs, so the answers are
mutually coherent by construction.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .point import LOPSIDED_LOSER_GAMES, clip, set_distribution

#: Fitted defaults from ``scripts/backtest_simulator.py``, run over 150 real
#: ATP hard-court matches using the players' own opponent-adjusted ratings.
#:
#: ``sigma = 0.05`` with ``rho = -0.6`` removes the lopsided-set bias entirely
#: (mean prediction minus observed goes from -0.092 under constant ``p`` to
#: -0.000) and improves Brier by about 1%.
#:
#: ``SKILL_SCALE = 1.5`` corrects a separate problem: the ridge penalty in the
#: ratings fit shrinks every skill toward zero, so matchups look more even than
#: they are and matches are predicted too long.  Scaling the skill differences
#: reduces the deciding-set bias monotonically (+0.124 at 1.0, +0.055 at 2.5)
#: while Brier bottoms out at 1.5.
FITTED_SIGMA = 0.05
FITTED_RHO = -0.6
SKILL_SCALE = 1.5

#: Between-set form variation, fitted separately per tour.
#:
#: Both tours sit at 0.05.  This value was briefly raised to 0.11 for the women
#: and that was a mistake, recorded here because the way it went wrong is more
#: useful than the number.
#:
#: The change was fitted on a sample whose realised any-tiebreak rate was 0.183.
#: Three independent measurements put the true figure near 0.22-0.25:
#: TIEBREAK_COUNTS says 0.220 from 2,883 matches, parsing US Open scores directly
#: gives 0.246, and a fresh calibration on slam hard courts gives 0.240.  That one
#: sample was unrepresentative, and it was flagged as suspect at the time without
#: the conclusion being followed through.
#:
#: Raising sigma adds between-set variation, which produces more blowout sets and
#: fewer tiebreaks.  Both errors duly appeared, and both were measured before this
#: was traced back to its cause:
#:
#: | WTA, slam hard courts | sigma 0.05 | sigma 0.11 | realised |
#: |---|---|---|---|
#: | any lopsided set | 0.654 | 0.806 | **0.647** |
#: | any tiebreak | 0.249 | 0.191 | **0.240** |
#: | total games | 22.75 | 21.41 | **21.49** |
#:
#: At 0.05 the lopsided and tiebreak rates are both within a point of reality; at
#: 0.11 lopsided is out by +0.150 (t = 5.6) and tiebreak by -0.065 (t = -2.3).
#: Only total games favoured the higher value, and games is the one of the three
#: that has an explicit downstream correction in GAMES_BIAS - so the tail was
#: wagging the dog.
#:
#: The lesson worth keeping: when one fitted parameter is asked to satisfy three
#: constraints, fix it on the constraints that have no other remedy, and correct
#: the rest downstream.
TOUR_SIGMA = {"ATP": 0.05, "WTA": 0.05}

#: Fraction of the form shock that is a property of the MATCH rather than of each
#: individual set.
#:
#: The shocks used to be redrawn independently for every set, which makes set
#: results conditionally independent and therefore maximises the chance the sets
#: split. That showed up as a large, measurable error on deciding sets. Against
#: 602 ATP and 620 WTA completed US Open matches from 2021-26:
#:
#:     P(deciding set)        measured (close matchups)   simulated (share 0)
#:     ATP                    0.274-0.302                 0.374
#:     WTA                    0.323-0.376                 0.501
#:
#: Raising sigma cannot fix this - with an independent draw per set the split
#: probability is pinned no matter how large the shock is. Only a component that
#: PERSISTS across sets can, which is also the physically right story: a player
#: serving well in the first set tends to still be serving well in the second.
#:
#: Values chosen to hit the measured deciding-set rate while disturbing the
#: tiebreak family least, since that is our best-scoring family. At these values
#: P(any tiebreak) moves by only -0.025 (ATP) and -0.012 (WTA), and mean total
#: games moves toward its measured value on both tours.
#:
#: TIEBREAK_SIM_SCALE and GAMES_BIAS were partly compensating for this bug and
#: have been re-derived so their outputs are unchanged; the ATP best-of-3 games
#: bias falls to +0.04, i.e. essentially nothing left to correct, which is the
#: clearest sign the correction was standing in for this missing term.
TOUR_MATCH_SHARE = {"ATP": 0.55, "WTA": 1.0}



@dataclass(frozen=True)
class SimulationConfig:
    """Form-variation parameters, fitted rather than assumed."""

    sigma: float = FITTED_SIGMA
    rho: float = FITTED_RHO
    match_share: float = 0.0
    n_sims: int = 20_000
    seed: int = 0
    final_set_tiebreak_to: int = 10


@dataclass(frozen=True)
class SimulatedMatch:
    """Everything the question families need, from one set of runs."""

    n_sims: int
    p_a_wins_match: float
    p_any_tiebreak: float
    p_two_plus_tiebreaks: float
    p_deciding_set: float
    p_any_lopsided_set: float
    p_first_set_10plus_games: float
    p_first_set_tiebreak: float
    p_final_set_tiebreak: float
    p_straight_sets: float
    #: Some player wins two consecutive sets.  In a best-of-three this is every
    #: straight-sets win plus the three-set wins where the winner lost the
    #: opener, so it is far above even money and nowhere near the 50 a defaulted
    #: answer would give.
    p_two_sets_in_a_row: float
    #: ``p_scoreline[(sets_a, sets_b)]`` for every finished scoreline, so an
    #: exact-result question is read off rather than approximated.
    p_scoreline: dict[tuple[int, int], float]
    mean_total_games: float
    sd_total_games: float
    mean_sets: float
    total_games: np.ndarray
    sets_played: np.ndarray
    tiebreaks: np.ndarray

    def as_dict(self) -> dict[str, float]:
        return {
            "p_a_wins_match": round(self.p_a_wins_match, 4),
            "p_any_tiebreak": round(self.p_any_tiebreak, 4),
            "p_deciding_set": round(self.p_deciding_set, 4),
            "p_any_lopsided_set": round(self.p_any_lopsided_set, 4),
            "p_first_set_10plus_games": round(self.p_first_set_10plus_games, 4),
            "p_first_set_tiebreak": round(self.p_first_set_tiebreak, 4),
            "p_final_set_tiebreak": round(self.p_final_set_tiebreak, 4),
            "p_straight_sets": round(self.p_straight_sets, 4),
            "p_two_sets_in_a_row": round(self.p_two_sets_in_a_row, 4),
            "mean_total_games": round(self.mean_total_games, 2),
            "sd_total_games": round(self.sd_total_games, 2),
            "mean_sets": round(self.mean_sets, 3),
        }

    def probability_total_games_over(self, threshold: float) -> float:
        """``P(total games > threshold)`` straight from the simulated runs."""
        return float((self.total_games > threshold).mean())

    def probability_scoreline(self, sets_a: int, sets_b: int) -> float:
        """``P(the match ends exactly sets_a-sets_b)``."""
        return self.p_scoreline.get((sets_a, sets_b), 0.0)


class _SetSampler:
    """Vectorised sampler over cached set-score distributions.

    Solving a set exactly costs far more than sampling from a solved one, and
    shocks land on a continuum, so probabilities are rounded to a grid.  A grid
    of 0.002 is finer than the parameter uncertainty in ``p`` itself, and turns
    tens of thousands of solves into a few hundred.

    The default was 0.006 while this docstring claimed 0.002.  Because inputs
    are snapped *before* the set distribution is solved, that is deterministic
    bias rather than simulation noise: a sweep of Bo5 matchups found a true
    win probability of 0.4673 snapped to 0.5000, pricing a genuine favourite as
    a coin flip on the highest-multiplier family.

    Sampling is by inverse-CDF against a uniform draw rather than
    ``Generator.choice``, which is roughly two orders of magnitude faster here
    because it avoids rebuilding an alias table per call.
    """

    __slots__ = ("grid", "_cdf", "_scores")

    def __init__(self, grid: float = 0.002) -> None:
        self.grid = grid
        self._cdf: dict[tuple[int, int, bool, int], np.ndarray] = {}
        self._scores: dict[tuple[int, int, bool, int], list[tuple[int, int, bool]]] = {}

    def key(self, pa: float, pb: float, a_serves_first: bool, tiebreak_to: int):
        """Grid-rounded cache key for a set configuration."""
        return (
            int(round(pa / self.grid)),
            int(round(pb / self.grid)),
            bool(a_serves_first),
            int(tiebreak_to),
        )

    def _ensure(self, key) -> None:
        if key in self._cdf:
            return
        distribution = set_distribution(key[0] * self.grid, key[1] * self.grid, key[2], key[3])
        outcomes = list(distribution.items())
        probabilities = np.array([p for _, p in outcomes], dtype=float)
        probabilities /= probabilities.sum()
        self._cdf[key] = np.cumsum(probabilities)
        self._scores[key] = [score for score, _ in outcomes]

    def sample(self, key, draw: float) -> tuple[int, int, bool]:
        """Draw one set score from the cached distribution by inverse CDF."""
        self._ensure(key)
        index = int(np.searchsorted(self._cdf[key], draw, side="right"))
        scores = self._scores[key]
        return scores[min(index, len(scores) - 1)]


def simulate_match(
    pa: float,
    pb: float,
    best_of: int = 3,
    a_serves_first: bool = True,
    config: SimulationConfig | None = None,
) -> SimulatedMatch:
    """Simulate a match and read every structural question off the same runs."""
    cfg = config or SimulationConfig()
    rng = np.random.default_rng(cfg.seed)
    sampler = _SetSampler()
    sets_to_win = best_of // 2 + 1

    shocks_a, shocks_b = _draw_shocks(cfg, best_of, rng)
    # One uniform per possible set, drawn up front so the inner loop does no
    # random-number generation at all.
    uniforms = rng.random((cfg.n_sims, best_of))

    wins = np.zeros(cfg.n_sims, dtype=bool)
    tiebreaks = np.zeros(cfg.n_sims, dtype=int)
    decider = np.zeros(cfg.n_sims, dtype=bool)
    lopsided = np.zeros(cfg.n_sims, dtype=bool)
    first_long = np.zeros(cfg.n_sims, dtype=bool)
    first_tb = np.zeros(cfg.n_sims, dtype=bool)
    final_tb = np.zeros(cfg.n_sims, dtype=bool)
    straight = np.zeros(cfg.n_sims, dtype=bool)
    two_in_row = np.zeros(cfg.n_sims, dtype=bool)
    final_sets_a = np.zeros(cfg.n_sims, dtype=int)
    final_sets_b = np.zeros(cfg.n_sims, dtype=int)
    games = np.zeros(cfg.n_sims, dtype=int)
    sets_played = np.zeros(cfg.n_sims, dtype=int)

    for i in range(cfg.n_sims):
        sets_a = sets_b = 0
        serves_first = a_serves_first
        set_index = 0
        previous_winner: bool | None = None

        while sets_a < sets_to_win and sets_b < sets_to_win:
            is_decider = sets_a == sets_to_win - 1 and sets_b == sets_to_win - 1
            if is_decider:
                decider[i] = True

            pa_set = clip(pa + shocks_a[i, set_index])
            pb_set = clip(pb + shocks_b[i, set_index])
            tb_to = cfg.final_set_tiebreak_to if is_decider else 7

            key = sampler.key(pa_set, pb_set, serves_first, tb_to)
            ga, gb, went_tb = sampler.sample(key, uniforms[i, set_index])

            if set_index == 0:
                first_long[i] = (ga + gb) >= 10
                first_tb[i] = went_tb
            # Overwritten on every set, leaving the tiebreak state of the set
            # that actually ended the match.
            final_tb[i] = went_tb
            if went_tb:
                tiebreaks[i] += 1
            if min(ga, gb) in LOPSIDED_LOSER_GAMES and max(ga, gb) == 6:
                lopsided[i] = True

            games[i] += ga + gb
            set_winner = ga > gb
            if set_winner:
                sets_a += 1
            else:
                sets_b += 1
            if previous_winner is not None and set_winner == previous_winner:
                two_in_row[i] = True
            previous_winner = set_winner
            serves_first = serves_first != ((ga + gb) % 2 == 1)
            set_index += 1

        wins[i] = sets_a == sets_to_win
        straight[i] = min(sets_a, sets_b) == 0
        sets_played[i] = sets_a + sets_b
        final_sets_a[i] = sets_a
        final_sets_b[i] = sets_b

    scorelines: dict[tuple[int, int], float] = {}
    for a, b in zip(final_sets_a.tolist(), final_sets_b.tolist(), strict=True):
        scorelines[(a, b)] = scorelines.get((a, b), 0.0) + 1.0
    scorelines = {k: v / cfg.n_sims for k, v in scorelines.items()}

    return SimulatedMatch(
        n_sims=cfg.n_sims,
        p_a_wins_match=float(wins.mean()),
        p_any_tiebreak=float((tiebreaks >= 1).mean()),
        p_two_plus_tiebreaks=float((tiebreaks >= 2).mean()),
        tiebreaks=tiebreaks,
        p_deciding_set=float(decider.mean()),
        p_any_lopsided_set=float(lopsided.mean()),
        p_first_set_10plus_games=float(first_long.mean()),
        p_first_set_tiebreak=float(first_tb.mean()),
        p_final_set_tiebreak=float(final_tb.mean()),
        p_straight_sets=float(straight.mean()),
        p_two_sets_in_a_row=float(two_in_row.mean()),
        p_scoreline=scorelines,
        mean_total_games=float(games.mean()),
        sd_total_games=float(games.std()),
        mean_sets=float(sets_played.mean()),
        total_games=games,
        sets_played=sets_played,
    )


def _draw_shocks(
    cfg: SimulationConfig, best_of: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Draw correlated per-set form shocks for both players.

    Returns two arrays of shape ``(n_sims, best_of)``.  Shocks are drawn for
    every possible set up front, which is cheaper than drawing them one at a
    time and costs only the unused tail.
    """
    if cfg.sigma <= 0:
        zeros = np.zeros((cfg.n_sims, best_of))
        return zeros, zeros

    rho = float(np.clip(cfg.rho, -0.99, 0.99))
    cov = np.array([[1.0, rho], [rho, 1.0]]) * cfg.sigma**2
    share = float(np.clip(cfg.match_share, 0.0, 1.0))
    if share <= 0.0:
        draws = rng.multivariate_normal([0.0, 0.0], cov, size=(cfg.n_sims, best_of))
        return draws[:, :, 0], draws[:, :, 1]

    # Split the form shock into a component drawn once per match and a component
    # redrawn per set. Variances add, so the total stays cfg.sigma**2 and only
    # the correlation between sets changes. See TOUR_MATCH_SHARE.
    per_match = rng.multivariate_normal([0.0, 0.0], cov * share, size=cfg.n_sims)
    per_set = rng.multivariate_normal(
        [0.0, 0.0], cov * (1.0 - share), size=(cfg.n_sims, best_of)
    )
    return (
        per_set[:, :, 0] + per_match[:, None, 0],
        per_set[:, :, 1] + per_match[:, None, 1],
    )


def matchup_probabilities(
    base: float,
    serve_a: float,
    return_a: float,
    serve_b: float,
    return_b: float,
    *,
    skill_scale: float = SKILL_SCALE,
) -> tuple[float, float]:
    """Serve probabilities for a matchup, correcting for ridge compression.

    The ratings fit shrinks skills toward zero, which is right for estimating a
    single player but leaves the *spread* between players too narrow.  Left
    uncorrected the simulator predicts matches that are too close and therefore
    too long: measured over 150 real matches, deciding sets were over-predicted
    by 12 points.  Scaling the differences back out fixes most of it.
    """
    pa = clip(base + skill_scale * (serve_a - return_b))
    pb = clip(base + skill_scale * (serve_b - return_a))
    return pa, pb


def monte_carlo_error(probability: float, n_sims: int) -> float:
    """Standard error of a simulated probability.

    Useful for deciding how many runs are enough: at 20,000 runs a probability
    near 0.5 carries a standard error of about 0.0035, comfortably finer than
    the one-point granularity of a submission.
    """
    p = min(max(float(probability), 0.0), 1.0)
    return float(np.sqrt(p * (1.0 - p) / max(n_sims, 1)))


def tiebreak_at_least(match: SimulatedMatch, count: int) -> float:
    """``P(the match contains at least `count` tiebreaks)``.

    The live questions ask for one *or two*, and 23 of the 64 Round 1 markets
    asked for two.  Answering both with the any-tiebreak rate would have been
    badly wrong on a third of them, so the count is read from the simulation
    rather than assumed.
    """
    if count <= 0:
        return 1.0
    return float((match.tiebreaks >= count).mean())
