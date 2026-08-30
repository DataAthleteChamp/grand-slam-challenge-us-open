# Lessons — US Open 2026

Written from settled results, not from theory. Every number here is measured.
Regenerate the underlying data with:

```bash
PYTHONPATH=. .venv/bin/python scripts/archive_history.py
PYTHONPATH=. .venv/bin/python scripts/score.py --detail
```

---

## Round 1, first 10 matches (50 questions per bot)

| Entry | n | Total RBP | Mean | Beat crowd | Our Brier |
|---|---|---|---|---|---|
| **bot 1 — honest** | 50 | **+106.9** | +2.14 | 56% | 0.1769 |
| bot 2 — scenario | 50 | +84.2 | +1.68 | 60% | 0.1814 |
| manual (sharpened) | 40 | **+8.8** | +0.22 | **70%** | 0.1775 |

### Total RBP by family

| Family | honest | manual | scenario |
|---|---|---|---|
| match_duration | +65.6 | +41.3 | **+77.8** |
| tiebreak | +22.7 | **−55.1** | −2.2 |
| match_winner | +10.9 | +5.0 | **−9.9** |
| double_faults | +5.9 | +8.0 | +5.4 |
| aces | +1.8 | +9.6 | +13.1 |

---

## Lesson 1 — Never sharpen a rare event

The manual entry **beat the crowd on 70% of questions, more often than either
bot, and still finished 12x behind them.** It won many small and lost a few
enormous, which is precisely what sharpening does to a low-probability question.

All the damage was in one family:

| bot 1 | bot 2 | manual | crowd | result | manual RBP |
|---|---|---|---|---|---|
| 22 | 14 | **7** | 34 | YES | **−42.9** |
| 23 | 14 | **7** | 30 | YES | **−37.5** |
| 48 | 41 | 37 | 48 | YES | −12.7 |
| 23 | 14 | 7 | 29 | NO | +7.9 |
| 27 | 17 | 9 | 35 | NO | +11.4 |

Five correct calls earned +38. Two wrong ones lost **−80**.

Brier loss is quadratic, so the penalty is violently asymmetric: being wrong at 7
costs `(0.93)² = 0.86`, against `(0.78)² = 0.61` at 22. Sharpening buys a few
points when right and pays multiples when wrong, and rare events are exactly
where "wrong" is most likely.

**Rule: apply no sharpening below 20 or above 80.** Sharpen the middle, where the
downside is bounded, and leave the tails alone.

## Lesson 2 — We under-predict tiebreaks, badly

| | ours | crowd | actual |
|---|---|---|---|
| tiebreak | 0.259 | 0.329 | **0.400** |

Both we and the crowd are too low, and we are the further out at **−0.141**. The
simulator was already known to mis-handle tiebreaks (an unexplained +0.089
over-prediction against backtest), and the live data says the live direction is
the opposite. Bot 1 still made +22.7 here, but only because the crowd was even
worse.

**Action: raise tiebreak probabilities. Do not sharpen them downward.**

## Lesson 3 — Duration is the biggest edge, and I under-used it

Duration produced more RBP than every other family combined (+65.6, +77.8). The
crowd systematically under-predicts long matches (−0.046), and we are close to
unbiased (+0.032).

**I got the direction of a correction wrong.** Mid-round I advised pulling the
manual duration numbers back from 96 to 91, reasoning that our model still leaned
long. The settled results say the opposite: the crowd is the one that leans
short, and our higher numbers were right. Every large duration win came from
being well above the crowd — 90 against 67, 87 against 64, 66 against 53.

**Action: keep duration high. Trust the model over the base rate here** — the raw
model's Brier (0.1690) was better than the shrunk submission (0.1745).

## Lesson 4 — Do not deviate from the market on match winner

| Entry | match_winner RBP |
|---|---|
| bot 1 (market weight 0.85) | **+10.9** |
| bot 2 (market weight 0.34) | **−9.9** |

The only structural difference between the bots on this family is how much they
trust the bookmaker consensus, and the one that trusted it more won by 20 points.
The archive shows the same thing directly: blending to market cut the
match-winner Brier from **0.1895 to 0.1213**.

**Action: keep market weight at 0.85 or raise it. This family is not where the
edge is.**

## Lesson 5 — Both we and the crowd say YES too often on count comparisons

| Family | ours | crowd | actual |
|---|---|---|---|
| aces | 0.440 | 0.456 | **0.300** |
| double faults | 0.396 | 0.434 | **0.300** |

The realised rate is 0.30 for both. We are nearer than the crowd, which is why
these families are positive, but we are still **+0.14 and +0.10 too high**.

Part of this is structural and expected: ties resolve NO, and ties are common
(ATP aces 8%, WTA aces 14.5%). The rest looks like the reputation bias the
research predicted, and we have not fully escaped it.

**Action: shade ace and double-fault comparisons downward.** The gap is large
enough to matter and consistent across both families.

---

## What to change before the next round

1. **Cap sharpening to the 20–80 band.** Costliest single lesson.
2. **Raise tiebreak numbers** — we are 14 points low against realised.
3. **Keep duration high and shrink it less** — the raw model beat the shrunk one.
4. **Keep market weight high on match winner** — deviation cost bot 2 20 RBP.
5. **Shade aces and double faults down** — both entries still say YES too often.

## Caveats

Ten matches, 10 questions per family. The direction is consistent within each
family, which is why these are worth acting on, but the magnitudes are not
reliable. Act moderately and re-measure after the next round.

One structural note: **all three entries share one model**, so their errors are
correlated. The manual entry's tiebreak disaster was not an independent draw — it
was bot 1's tiebreak number, sharpened. Diversification across entries does not
protect against a family-level modelling error.
