# Calibration of all ten question families

Validated against the **official US Open point feed** (`data/raw/pointbypoint`, 625 men's and
622 women's completed matches, 2019–2024). That is the same source the questions settle on,
on the right surface, in the right format, scored by the same statisticians — so it is
tier-2 evidence in our hierarchy, above any backtest.

Where a family has no settled question yet, this is the only calibration evidence available,
and it is available *before* a single point is lost.

## Realised rates, split by tour

| quantity | ATP | WTA |
|---|---|---|
| P(at least 1 tiebreak) | 0.4560 | 0.2331 |
| P(at least 2 tiebreaks) | 0.1200 | 0.0193 |
| ratio 2+ / 1+ | **0.2632** | **0.0828** |
| P(lopsided set 6-0/6-1/6-2) | 0.6672 | 0.6254 |
| P(deciding set) | 0.2048 | 0.3055 |
| mean total games | 36.40 | 21.51 |

The tiebreak ratio differs by a factor of three between tours. A single pooled ratio would
badly misprice "at least 2" on the women's draw.

## Count families — tie structure

Ties resolve NO, so the fair price for two identical players is `(1 - P(tie)) / 2`, not 0.50.

| stat | tour | official P(tie) | our model | even price, official | ours |
|---|---|---|---|---|---|
| aces | ATP | 0.0432 | 0.0624 | 0.4784 | 0.4688 |
| aces | WTA | 0.1286 | **0.1293** | 0.4357 | 0.4354 |
| double faults | ATP | 0.1008 | 0.1086 | 0.4496 | 0.4457 |
| double faults | WTA | 0.1141 | 0.1243 | 0.4429 | 0.4379 |
| winners | ATP | 0.0192 | — | 0.4904 | — |
| winners | WTA | 0.0418 | — | 0.4791 | — |

The model reproduces the official tie structure to within 0.7–1.9 points. **This matters for
the diagnosis**: the women's double-fault deficit is therefore *not* a tie-handling error, it
is a resolution gap (our AUC 0.669 against the crowd's 0.904). No amount of level correction
can close it.

## Family-by-family status

| family | evidence | status |
|---|---|---|
| match winner | settled RBP, both lanes | sharpened at 1.35, +0.94 to +1.49/q, 8/8 splits positive |
| aces comparison | settled RBP + official ties | tie structure good; men's beats the crowd (AUC 0.909 vs 0.892) |
| double faults | settled RBP + official ties | tie structure good; **women's is our worst family, a resolution gap** |
| tiebreak | settled RBP + official rates | best family. ATP ">=2" prices at 0.130 against a realised 0.120 |
| match duration | official mean games | delivered 37.01 against a realised 36.40 (ATP), 21.60 against 21.51 (WTA) |
| deciding set | official rates | fixed this session. Even matchup now 0.291 (ATP) / 0.374 (WTA) against measured close-matchup rates of 0.274–0.302 and 0.323–0.376 |
| lopsided set | official rates | ATP book mean 0.6675 against 0.6672 realised |
| first serve | 28,000 player-matches | fixed this session; predictive sd 0.0867 against a measured 0.0859 |
| unforced errors | official feed | newly modelled at half weight; **least validated thing we ship** |
| winners comparison | official ties | tie rate only 1.9%/4.2%, so tie handling barely matters here |

## What is still open

1. **Women's double faults.** Resolution, not calibration. Needs information we do not have —
   the leading candidate is in-tournament form, since our WTA player data stops 2026-08-13.
2. **Unforced errors at 0.50 weight** is a risk posture, not a measurement. Revisit the moment
   one settles.
3. **ATP ace tie rate** is the one count cell where the model is visibly loose (0.0624 against
   an official 0.0432), worth about 1 point on an even matchup.
