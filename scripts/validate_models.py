"""Sanity-check the models against the empirically measured base rates.

Run with ``make validate``.  This is not a unit test — it is a calibration report
that shows how far the analytic model sits from what the data actually did, which
is information we need before trusting any submission.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.data.baserates import COUNT_STATS, structural  # noqa: E402
from bot.models.counts import skellam_comparison  # noqa: E402
from bot.models.point import game_prob, match_shape, serve_point_prob  # noqa: E402


def _row(label: str, model: float, measured: float) -> str:
    delta = model - measured
    flag = "  <-- check" if abs(delta) > 0.10 else ""
    return f"  {label:<22} model={model:6.3f}  measured={measured:6.3f}  delta={delta:+6.3f}{flag}"


def main() -> int:
    print("Hold rates from tour-average serve/return inputs")
    atp_p = serve_point_prob(0.645, 0.355, "ATP")
    wta_p = serve_point_prob(0.585, 0.415, "WTA")
    print(f"  ATP  p_serve={atp_p:.4f}  hold={game_prob(atp_p):.4f}  (observed ~0.805)")
    print(f"  WTA  p_serve={wta_p:.4f}  hold={game_prob(wta_p):.4f}  (observed ~0.675)")

    print("\nStructural questions, evenly matched players")
    atp = match_shape(atp_p, atp_p, best_of=5)
    measured = structural("ATP", "even")
    print(_row("ATP tiebreak", atp.p_any_tiebreak, measured.tiebreak))
    print(_row("ATP deciding set", atp.p_deciding_set, measured.deciding_set))
    print(_row("ATP lopsided set", atp.p_any_lopsided_set, measured.lopsided_set))
    print(_row("ATP set1 >=10 games", atp.p_first_set_10plus_games, measured.first_set_10plus))

    wta = match_shape(wta_p, wta_p, best_of=3)
    measured = structural("WTA", "even")
    print(_row("WTA tiebreak", wta.p_any_tiebreak, measured.tiebreak))
    print(_row("WTA deciding set", wta.p_deciding_set, measured.deciding_set))
    print(_row("WTA lopsided set", wta.p_any_lopsided_set, measured.lopsided_set))
    print(_row("WTA set1 >=10 games", wta.p_first_set_10plus_games, measured.first_set_10plus))

    print("\nCount comparisons, identical players (strict '>'), by tour")
    for tour, stats in COUNT_STATS.items():
        for stat, values in stats.items():
            mu = values["mean_per_player"]
            comparison = skellam_comparison(mu, mu)
            print(
                _row(f"{tour} {stat} P(A>B)", comparison.resolve("no"), values["even_p_a_greater"])
            )
            print(_row(f"{tour} {stat} P(tie)", comparison.p_tie, values["p_tie"]))

    print(
        "\nKnown bias: the iid model under-produces lopsided sets and over-produces\n"
        "deciders, because constant per-point probabilities cannot generate the\n"
        "within-match form swings that real blowout sets come from (Klaassen &\n"
        "Magnus 2001). The simulator must add a per-set random effect before these\n"
        "numbers are used directly."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
