# Lessons — US Open 2026

Generated from settled results. Regenerate with:

```bash
PYTHONPATH=. .venv/bin/python scripts/archive_history.py
PYTHONPATH=. .venv/bin/python scripts/analyse.py --plots
```

---

## The scoring rule is not what we assumed — the biggest finding so far

Our recomputed RBP did not match the API's on a single one of 55 questions. We
computed `(consensus_brier - our_brier) x 100` and got **+17.5**; the API said
**+124.3**. Solving for the difference, the gap is exactly the crowd's own
**variance**, because

```
E[(p_i - y)^2] = (mean p - y)^2 + Var(p_i)
```

**The competition scores us against the average individual forecaster, not
against the consensus.** Verified across all 55 settled questions: every implied
variance is positive - one negative would falsify it - and the implied crowd
standard deviation runs 0.084 to 0.189, a plausible spread for a field of humans.

### What this changes

| | RBP |
|---|---|
| Total actual | **+124.3** |
| Free: what copying the consensus would have earned | **+106.8** |
| Skill: actually beating the consensus | **+17.5** |

**86% of our score came from the scoring rule, not from us.** Sitting near the
consensus pays about **+1.94 RBP per question** for free.

The consequence is structural rather than statistical:

- Every question is worth about +1.9 just for being sensible.
- A single wild miss of -40 RBP forfeits **twenty questions'** worth of bonus.
- So **variance control matters more than sharpness**. This is the quantitative
  basis for the no-sharpening rule below; it no longer rests on a small sample.

---

## What is robust, and what is not

With 11 matches settled, **no per-family skill number is distinguishable from
noise.** Bootstrapped 95% intervals:

| Family | Skill | 95% CI | Verdict |
|---|---|---|---|
| match_duration | +47.7 | [-7.3, +87.7] | noise |
| tiebreak | +9.2 | [-43.5, +50.0] | noise |
| double_faults | -2.0 | [-54.6, +48.2] | noise |
| match_winner | -9.7 | [-22.7, +2.2] | noise |
| aces | -27.7 | [-65.5, +11.3] | noise |

The measured family *biases* are equally unstable - every CI spans zero, several
from -0.15 to +0.40.

**The earlier lessons that said "shift double faults down 0.13" and "shift
tiebreaks up 0.11" were fitted to noise and should not be applied.** The
counterfactuals that appeared to gain +17 and +12 RBP have intervals of roughly
plus or minus 60. Acting on them would have been curve-fitting eleven matches.

## Shrinking toward the base rate makes us worse

Tested directly. Shrinking every answer toward its base rate by weight w:

| w | Skill | 95% CI vs w=0 |
|---|---|---|
| 0.00 | **+17.5** | - |
| 0.05 | +10.6 | [-13.5, -0.2] |
| 0.10 | +3.1 | [-27.2, -0.4] |
| 0.20 | -13.7 | [-57.2, -2.0] |

Every interval excludes zero on the *bad* side. **Our raw numbers beat every
shrunk version**, so the answer to "should we hedge more" is measurably no.

---

## What to actually do  (rewritten at 37 settled matches)

1. **The free term is +2.00/question and nothing you submit changes it.**
   Confirmed exactly against 185 API-scored questions:
   `rbp = 100 * [ (crowd - y)^2 + Var_crowd - (ours - y)^2 ]`, with the gap term
   averaging **+2.001** (sd 0.73, range 0.42-5.12). So total points is just
   `2.00 * n_questions + 100 * sum(crowd_brier - our_brier)`. Two consequences:
   maximising score is *exactly* minimising our own Brier, and **every
   unanswered question is a guaranteed -2.00 against everyone who answered it.**
   Coverage has been verified complete (37 settled + 23 open matches, 5 of 5
   questions each, zero gaps) - keep it that way.

2. **Do not recalibrate on settled results.** Six separate post-hoc transforms
   have now been tested with leave-one-match-out cross-validation and bootstrap
   intervals. Every one is indistinguishable from zero or actively worse:

   | transform | out-of-sample result |
   |---|---|
   | shrink toward 50 | no gain |
   | shrink toward base rate | worse at every weight |
   | shrink toward our own family mean | worse at every weight (k=0 costs -6.5 on aces) |
   | expand spread (k=1.25) | +0.21 [-0.23, +0.63] |
   | per-family bias correction | -0.07 [-1.33, +1.19] |
   | pooled YES-shading | +0.12 [-0.57, +0.82], and it reverses on the manual lane |

   The reason they all fail: our deficit is **discrimination, not calibration**.
   On the women's draw our predictions correlate +0.58 with outcomes against the
   crowd's +0.70. No monotone transform of a weaker signal fixes that.

3. **Fix measured defects in the model instead.** The one change that survived
   scrutiny this round came from re-running a calibration *per tour* against
   historical matches, not from fitting this tournament. See below.

4. **Re-run this analysis every round.** One command, and the family intervals
   are still wide at n=37 per family.

---

## The men's/women's split was real, and cost us the women's draw

The simulator's between-set form sigma was fitted on 150 **ATP** matches and
then applied to both draws. Re-fitting per tour over 2023-26 slam hard-court
matches (120 per tour):

| sigma = 0.05 (shared) | total games | any tiebreak | any lopsided set |
|---|---|---|---|
| ATP | +1.98 | +0.019 | -0.041 |
| WTA | +1.91 | +0.073 | -0.097 |

ATP is minimised at 0.05; WTA at **0.11**, where every bias essentially
vanishes (games +0.57, tiebreak +0.003, lopsided +0.039). The women's game has
more between-set swing. This matters because the women's draw is exactly where
the live entry bled: **-1.37 RBP/question against -0.39 for the men's.**

Lesson: **before trusting any fitted constant, check what population it was
fitted on.** Sixteen of eighteen constants were already tour-split; this one was
not, and it was the one feeding three families at once.

---

## Where we actually stand (185 settled questions, honest lane)

| family | n | our bias | skill/q | 95% CI |
|---|---|---|---|---|
| tiebreak | 37 | +0.085 | **+2.33** | [+0.75, +3.57] |
| match_duration | 37 | +0.138 | +0.10 | [-3.65, +3.47] |
| match_winner | 37 | +0.101 | -0.43 | [-1.16, +0.27] |
| aces_comparison | 37 | +0.045 | -2.07 | [-4.44, +0.36] |
| double_faults_comparison | 37 | -0.090 | -2.22 | [-4.86, +0.33] |

Tiebreak is the only family that is significantly positive, and it is where the
crowd is most wrong (they price 0.315 against a realised 0.162). That is the
pocket worth defending and extending.

**Correction to an earlier entry in this file:** it previously said "we
under-predict tiebreaks". That was measured on 11 matches and is backwards. At
37 matches we *over*-predict them (+0.085), which is why the second bot's
tiebreak shift was pushing the wrong way.

---

## How much of the leaderboard is luck

Simulating a forecaster with **zero** skill (submits the crowd value plus noise)
over the same 185 questions: median **304**, 99th percentile 465, and across
20,000 draws the maximum seen was 576. Taking the best of N such entrants:

| entrants | expected top score |
|---|---|
| 200 | 493 |
| 1,000 | 528 |
| 5,000 | 558 |

Our own per-question skill has sd 7.50, so the standard error on our total over
185 questions is **+/-102 points**. A leader on ~600 is perhaps a 0.010-0.015
Brier edge per question above the crowd, but a large part of the visible gap is
selection across many entrants plus noise. **Chase expected skill, not the
leader's number** - and do not read our own rank movements as signal.

---

## Falsified: the per-family shift experiment

Bot 2 carried the n=11 family biases at half strength. Two ways of reading the
result, both negative:

* the lanes are indistinguishable after 37 matches (-0.46 vs -0.47 RBP/q);
* the underlying estimates reversed sign as the sample tripled - double faults
  **+0.128 -> -0.090**, tiebreak **-0.105 -> +0.085**. Two of five flipping sign
  is what noise looks like, so two of the three shifts were pushing against the
  true direction.

Retired. Only the tiebreak shift survives, and only because its direction is
corroborated by a dataset outside this tournament.

---

## Standing observations, held loosely

- On aces the crowd correlates **+0.71** with outcomes against our **+0.56**;
  they hold information our historical rates do not.
- Double faults are close to irreducible: server identity explains 14-21% of the
  variance. Do not try to win this family, just avoid losing it.
- When we deviate from the crowd by more than 4 points on aces or double faults,
  we have been right only 41% and 36% of the time. Deviate there only with a
  reason that was validated out-of-sample.

---

## One methodological note

The manual entry's numbers are bot 1's, sharpened. **All three entries share one
model**, so spreading across entries buys variance, not diversification, and
gives no protection against a family-level modelling error. Paired on the same
40 questions, sharpening gained on four families (+3.19 aces, +1.61 double
faults, +1.38 duration, +1.36 winner) and lost **-5.95 on tiebreak** - because
sharpening pushes an already-too-high tiebreak number further up. Sharpen the
four, leave tiebreak alone.

---

## Round 2: the loss was discrimination, and the shrinkage was the cause

A Murphy decomposition (Brier = reliability - resolution + uncertainty) over 210
settled questions finally located the problem. We are *better calibrated* than
the crowd on four of five families. Every loss is **resolution** - the ability
to separate yes from no:

| family | AUC ours | AUC crowd | calibration cost | discrimination cost |
|---|---|---|---|---|
| aces | 0.860 | 0.957 | -4.81 | **+6.20** |
| double faults | 0.630 | 0.718 | -1.27 | **+2.99** |
| match winner | 0.895 | 0.920 | -0.85 | **+2.01** |
| duration | 0.771 | 0.750 | +1.64 | -2.45 |
| tiebreak | 0.722 | 0.751 | **-2.26** | +1.02 |

That is why all six recalibration transforms failed: a monotone transform cannot
add resolution. It also explains the fix. Our *raw model* discriminates far
better than what we were submitting (ace AUC 0.938 raw against 0.860 submitted),
because the confidence pull dragged every answer toward a base rate that carries
no matchup information. Replaying the settled questions without that pull:

| family | gain from submitting the raw model | 95% CI |
|---|---|---|
| aces (men) | +1.09 | [+0.39, +2.00] |
| aces (women) | +1.73 | [+0.69, +2.77] |
| tiebreak (women) | +1.02 | [+0.40, +1.58] |
| pooled, all families | **+0.77** | **[+0.08, +1.47]** |

**Lesson: shrinking toward a base rate is only conservative if the base rate is
informative.** For "will A out-ace B" it is ~0.45 regardless of who A and B are,
and for "will X win" it is exactly 0.50. Pulling toward those numbers is not
caution, it is deleting the signal.

## Pinnacle is a free, immediate calibration benchmark

`https://guest.api.arcadia.pinnacle.com/0.1/` serves tennis matchups and markets
without authentication, including per-player game totals (`team_total`). Summing
the two sides gives a sharp total-games line for every match. This does not need
settled results, so the simulator can be checked *before* anything resolves:

    ATP  simulator - Pinnacle  +2.76 games  sd 3.17  n=24  t=+4.27
    WTA  simulator - Pinnacle  -0.48 games  sd 1.98  n=21  t=-1.12

That single check confirmed the per-tour sigma fix had worked on the women's
draw (historical bias +1.91 -> effectively zero) and caught that the women's
`GAMES_BIAS` offset had become a double-count. Re-run it whenever a
simulation parameter changes.

The men's residual is consistent with Klaassen & Magnus (2001), *Are Points in
Tennis Independent and Identically Distributed?* - servers win fewer of the
important points than iid assumes, so real matches have more breaks and fewer
games than an iid simulator produces. It is a point-level effect, which is why
the between-set sigma cannot remove it.

## Coverage is worth more than cleverness

A routine repricing created **195 new predictions** that did not exist before:
new rounds seed continuously and each unanswered question forfeits the free
~2.00 RBP everyone else collects. That single run was worth roughly 390 points,
far more than any modelling change found so far. **Reprice on a schedule, not
only when the model changes.**

## An open question about the benchmark

The crowd's ace AUC over 28 questions with a known draw is **1.000** - perfect
separation, which chance alone would produce with probability 3.3e-08. Our best
model reaches 0.938 and the best naive career-rate rule about 0.95. Markets were
also verified earlier to accept writes for hours after a match has begun, while
the FAQ states they close at the first ball. Those two facts together suggest
part of the crowd value may be informed by play already completed.

This is *not* being exploited, and it should not be: it depends on a gap between
the stated rules and the implementation. It is recorded because it bounds what
honest modelling can achieve - if the benchmark is partly post-hoc, a negative
skill number is not by itself proof the model is bad.

---

## The rounds ahead are worth 2.34x everything played so far

The event applies a **scoring multiplier by round**, exposed per match by the API as
`multiplier` and archived with every settled row:

| Round | Dates | Multiplier |
|---|---|---|
| First round | 30 Aug - 2 Sep | 1x |
| Second round | 2 - 4 Sep | 1x |
| Third round | 4 - 6 Sep | **2x** |
| Round of 16 | 6 - 8 Sep | **2x** |
| Quarter-finals | 8 - 9 Sep | **3x** |
| Semi-finals | 10 - 11 Sep | **3x** |
| Finals | 12 - 13 Sep | **3x** |

Everything settled so far scored at 1x.  What remains:

| Round | Questions | Weighted | Free points |
|---|---|---|---|
| rest of second round | 100 | 100 | 200 |
| third round | 160 | 320 | 640 |
| round of 16 | 80 | 160 | 320 |
| quarter-finals | 40 | 120 | 240 |
| semi-finals | 20 | 60 | 120 |
| finals | 10 | 30 | 60 |
| **total** | **410** | **790** | **1,581** |

**790 weighted questions against 337 already settled - 2.34x the weight of the entire
tournament to date, and none of it scored yet.**

Two consequences.

**The gap is not what it looks like.** The leader's implied skill is only **+0.33
RBP/question**; the rest of their lead is the free term everyone collects.  Simulating the
remaining schedule:

| our skill/question | P(overtake the leader) |
|---|---|
| -0.5 | 0.7% |
| 0.0 | 6.3% |
| **+0.5** | **26.4%** |
| **+1.0** | **60.7%** |
| +1.5 | 87.7% |

**Every model improvement is now worth 2-3x what the same improvement was worth last week**,
and so is every mistake.  A missed question in the quarter-finals forfeits six free points,
not two.  Coverage discipline matters three times as much from here.

This does *not* change what to submit on any individual question - the multiplier scales the
score, not the odds, so honest reporting stays optimal.  It changes only where attention is
worth spending, and how expensive an unanswered market is.
