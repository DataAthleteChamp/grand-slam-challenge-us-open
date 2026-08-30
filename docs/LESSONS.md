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

## What to actually do

1. **Do not retune the model on 11 matches.** Nothing is significant, and the
   most likely way to lose from here is to fit noise.
2. **Keep answers away from the tails.** Not because of a measured bias, but
   because the scoring rule pays about +1.9 per question for being reasonable
   and a -40 miss wipes out twenty of them. No sharpening below 20 or above 80.
3. **Do not shrink toward base rates.** Measured to be actively harmful.
4. **Re-run this analysis at 50+ matches.** Then the family numbers will mean
   something. The infrastructure exists, so it costs one command.

## Standing observations, held loosely

Directional and *not* yet significant:

- Duration is our best family; the crowd under-predicts long matches.
- We under-predict tiebreaks; the crowd does too, slightly less.
- Both we and the crowd say YES too often on ace and double-fault comparisons.
- On aces the crowd correlates **+0.79** with the outcome against our **+0.59**,
  suggesting they hold information our historical rates do not - recent form,
  injuries, conditions.

## One methodological note

The manual entry's tiebreak losses were not an independent draw: they were bot
1's numbers, sharpened. **All three entries share one model**, so spreading
across entries buys variance, not diversification, and gives no protection
against a family-level modelling error.
