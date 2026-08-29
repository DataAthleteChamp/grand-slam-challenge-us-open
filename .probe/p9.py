"""Cross-check the Monte Carlo against the exact analytic enumeration (sigma=0)."""
from bot.models.point import match_shape
from bot.models.simulate import SimulationConfig, simulate_match, tiebreak_at_least

for (pa, pb, bo) in [(0.65,0.63,5),(0.70,0.55,3),(0.58,0.58,3),(0.62,0.62,5),(0.80,0.50,5)]:
    cfg = SimulationConfig(sigma=0.0, n_sims=40000, seed=7)
    sim = simulate_match(pa, pb, bo, config=cfg)
    exact = match_shape(pa, pb, bo, True, 10)
    print(f"pa={pa} pb={pb} bo={bo}")
    print(f"  win      sim={sim.p_a_wins_match:.4f} exact={exact.p_a_wins_match:.4f}")
    print(f"  anyTB    sim={sim.p_any_tiebreak:.4f} exact={exact.p_any_tiebreak:.4f}")
    print(f"  decider  sim={sim.p_deciding_set:.4f} exact={exact.p_deciding_set:.4f}")
    print(f"  lopsided sim={sim.p_any_lopsided_set:.4f} exact={exact.p_any_lopsided_set:.4f}")
    print(f"  set1_10+ sim={sim.p_first_set_10plus_games:.4f} exact={exact.p_first_set_10plus_games:.4f}")
    print(f"  games    sim={sim.mean_total_games:.3f} exact={exact.expected_total_games:.3f}")
    print(f"  tb>=1    {tiebreak_at_least(sim,1):.4f}  tb>=2 {tiebreak_at_least(sim,2):.4f} "
          f"(p_two_plus field {sim.p_two_plus_tiebreaks:.4f})")
    print(f"  max tiebreaks seen: {sim.tiebreaks.max()}  bo={bo}")
