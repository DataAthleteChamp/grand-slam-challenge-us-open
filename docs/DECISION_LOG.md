# Decision log

Every method we have tested, the verdict, the evidence, and **the condition under which
we would revisit it**. The last column matters most: a rejection is only valid under the
conditions it was measured in, and several of these would flip if the sample grew or the
data were repaired.

Units are RBP per question (`rbp = 100 * [(crowd-y)^2 + Var_crowd - (ours-y)^2]`), with a
bootstrap 95% CI. "SIG" means the CI excludes zero.

## Evidence hierarchy

Adopted after the Akuna competition shipped a change with t=2.83 in backtest that regressed
on the real grader. In descending order of trust:

1. **Replaying our own settled questions** against a counterfactual submission.
2. **Realised outcome data** over many events (e.g. measured tiebreak rates).
3. **Derivation from the rules** (e.g. a lopsided set is exactly "set total games <= 8").
4. **Historical backtests / simulator sweeps** — has been overruled twice, do not ship on this alone.

Two independent hold-outs are mandatory. A single hold-out passed four separate false
positives (see "Rejected" below).

---

## Adopted

| Change | Effect | Evidence | Revisit if |
|---|---|---|---|
| `COUNT_CONFIDENCE` unpinned to 1.00 | **+4.22** [+1.70,+6.75] | Largest validated gain. The 0.10 pin had been swept on corrupted WTA serve data. | The serve data is repaired — the pin might then be re-derivable, but on clean data. |
| Joint-grid reconciliation of the simulator to the market win probability | **+1.72** [+0.54,+2.98] | Ported from the Probability Cup entry. | Never; this is structural. |
| `NO_BASE_RATE_PULL` for ace/DF comparisons | **+2.28** [+0.47,+4.01] thin-record, **+0.90** [+0.42,+1.40] mild | Replay of settled questions. All hold-outs positive, half significant. | If a genuinely informative fallback becomes available for counts (e.g. a real ace market), the pull should be reinstated *toward that*, not toward a base rate. |
| Pinnacle total-games ladder blended into duration | shipped with `GAMES_MARKET_WEIGHT=0.60` | Market prices this directly and well. | If Pinnacle stops exposing the guest API. |
| `ACE_VULNERABILITY_EXPONENT` 0.5 -> 1.0, ATP only | positive on ATP, negative on WTA | The WTA fit sat on corrupted rows. | Repaired WTA data. |

## Rejected

| Idea | Result | Why it failed | Revisit if |
|---|---|---|---|
| Blanket de-shrink toward base rate (all families) | **-0.52** [-1.31,+0.28] | Pooling hid two opposite effects: counts +2.28, match winner **-5.12**. | Never as a blanket. The scoped version shipped. |
| Capping extreme submissions inside [0.05,0.95] etc. | **-0.002 to -0.034**, all SIG negative | Our extremes are our best region: p<0.15 scores +2.75 on a 24/24 hit rate. | Only if we start producing extremes from a *model* we distrust rather than from strong evidence. |
| Low-side recalibration (we say 16%, reality 2%) | +0.16, SIG in one direction only | Band gap flips sign by family: -0.19 match winner vs **+0.20** double faults. | If measured *per family* on a much larger sample. |
| Tie-mass / level shift on count comparisons | pooled **+0.31** [-0.80,+1.42] | The raw -0.159 gap on women's DF looked like the 15.8% tie rate, but is not significant in any of four lane x half splits. | If n grows a lot and women's DF still shows a stable level gap. **Cheapest re-check as data arrives.** |
| Pull near-50 answers to exactly 50 | +0.02 [-0.12,+0.16] | Flattening 0.44 -> 0.50 barely moves Brier. | Never. |
| Excluding corrupted serve blocks from the roster | **-2.23** [-4.19,-0.47] on women's aces | Corrupted rows hurt *validation* badly but *fitting* only mildly. | Use `drop_broken_serve_blocks` to pick validation windows, never to filter training data. |
| Common-opponent network features | +0.048 AUC then **-0.053** on the next window | Classic single-hold-out false positive. | Only with a pre-registered window and two hold-outs. |
| Shrinking / sharpening / bias correction / shading / blending to crowd | all <= 0 | **Murphy identity**: `RES(g(f)) = RES(f)` for strictly monotone `g`. We are better calibrated than the crowd on 4/5 families and lose on *resolution*, so no monotone transform can help. | Never. This closes the entire direction. |
| Duration threshold anomaly (women 81-100 min) | n=20, contradicted by every large-sample check | ~25 hypotheses tested; one bad cell at p~0.004 is expected. | If it persists past n=60. |
| WTA `TOUR_SIGMA` 0.05 -> 0.11 | reverted | Fitted on a sample whose tiebreak rate was 0.183 against three independent measurements of 0.220/0.240/0.246. Only *games* preferred it, and games has a downstream correction. | Never fit a shared parameter on the one constraint that has another remedy. |

## Structural facts that close directions

- **The crowd is invisible before settlement.** `crowd_value` appears only on settled
  markets; open markets return no consensus field. Crowd-deviation and defer-to-crowd
  strategies are impossible, not merely against the rules of thumb. Truth-telling costs
  nothing.
- **The free term is +2.001/question**, paid for answering and independent of the answer.
  Skipping a question forfeits it, at 2x or 3x in later rounds. Coverage beats cleverness.
- **A lopsided set is exactly "set total games <= 8"** (6-0/6-1/6-2 give 6/7/8 games), which
  Pinnacle prices as `s;1;ou;8.5`.

## Where we actually stand (settled sample to 2026-09-01)

Total skill is **-269** over 475 honest questions, and it is concentrated in two cells:

| family | men | women |
|---|---|---|
| aces | **+61** | **-157** |
| double faults | -101 | **-263** |
| tiebreak | +44 | +100 |
| match winner | +27 | +15 |
| duration | +21 | -35 |

**Men's aces is our best family and beats the crowd** (AUC 0.909 vs 0.892, Brier 0.1530 vs
0.1679). Women's double faults is the worst (AUC 0.669 vs crowd 0.904). The gap there is
**resolution, not level**, so it cannot be fixed by any shift — only by information we do
not currently have. That is the single highest-value open problem.

## Pending — blocked on data, with trigger conditions

| Idea | Why it should work | Blocked by | Trigger to run it |
|---|---|---|---|
| **In-tournament form for count comparisons** | Our player rates come from data that stops **2026-08-13** for the WTA, two weeks before the draw. The crowd watches the matches and knows who is spraying double faults *this* fortnight; we are blind to it. Reconstructable for free from our own settled outcomes, since each settled comparison reveals who had more. | Every settled count question so far is round 1, so both players almost never have prior history (2 qualifying questions out of 176). | Re-run `/tmp/inform.py` logic once round 3+ count questions settle. Needs ~40 questions where both players have history. **This is the top candidate for the women's double-fault resolution gap.** |
| Score-conditional double faults (break points) | Reported 30–60% higher DF rates on break points; split-half ICC of 0.94 says the family has real signal we are not extracting. | Needs Match Charting Project score-conditional data ingested. | Any time; this is a build, not a wait. |
| Pinnacle set-1 line blended into "lopsided set" | Exact identity: lopsided = set total games <= 8, priced as `s;1;ou;8.5` on 41/43 matches. Our set-1 model is biased low (ATP -0.057 t=-5.98, WTA -0.049 t=-4.25) but correlates 0.73/0.79, so the ordering is fine and only the level is off. | Needs a validated set-1 -> any-set conversion. | Once any `set_games`/lopsided questions settle in volume. |

## Read on whether the fixes are landing

Settled cohorts, honest lane, skill per question:

| cohort | n | aces | double faults | tiebreak | match winner |
|---|---|---|---|---|---|
| 2026-08-30 | 300 | -1.17 | -2.42 | +1.47 | -0.05 |
| 2026-09-01 | 192 | -2.07 | -4.83 | +1.59 | +0.82 |
| 2026-09-02 | 17 | **+2.48** | **+0.90** | -2.51 | -0.03 |

The 09-02 cohort is the first settled under the repaired count models and is positive on both
count families, but n=3 per family — this is a hint, not evidence. The honest read is that
**most settled data predates the fixes**, so the -289 total understates the current model.
Do not claim the fixes have worked until a full 2x round settles.

## The simulator had no persistent within-match form (structural bug, fixed)

Form shocks were redrawn independently for every set. `rho` correlates the two
*players* within a set; nothing correlated a player with **himself across sets**. That
makes set results conditionally independent, which maximises the chance the sets split,
which over-predicts deciding sets.

Measured on 602 ATP and 620 WTA completed US Open matches, 2021-26:

| P(deciding set) | measured, close matchups | simulated before | simulated after |
|---|---|---|---|
| ATP | 0.274–0.302 | 0.374 | 0.251 |
| WTA | 0.323–0.376 | 0.501 | 0.342 |

Raising `sigma` could never have fixed this: with an independent draw per set the split
probability is pinned no matter how large the shock is. Verified directly — sweeping sigma
from 0.05 to 0.20 left P(deciding set) at 0.374/0.501 to three decimals. **That also explains
why the earlier WTA sigma=0.11 experiment failed**: it was the only knob available, and it
was the wrong one.

`TOUR_MATCH_SHARE = {"ATP": 0.55, "WTA": 1.0}` splits the shock into a per-match and a
per-set component. Variances add, so total shock size is unchanged and only the
between-set correlation moves.

**`GAMES_BIAS` and `TIEBREAK_SIM_SCALE` were partly standing in for this bug**, so both were
re-derived over a realistic spread of matchups to hold their delivered outputs constant.
Verified end to end: tiebreak drift ±0.0000, mean-games drift -0.003. The ATP best-of-3 games
offset falls from -1.20 to +0.04 — essentially nothing left to correct, which is the clearest
evidence the correction was masking this term. Delivered games now sit at 37.01 against a
measured 36.60 (ATP) and 21.60 against 21.62 (WTA).

Deliberately scoped so only deciding-set and straight-sets change. Those families are live and
unsettled, so there is no settled-RBP evidence yet; this ships on tier-2 evidence (realised
outcomes over 1,222 matches) plus a derivation, and the families it could have disturbed are
held fixed by construction.

## The first-serve family was priced in the wrong space (live bug, caught before settlement)

The question is *"Will X win more than T% of points played on their **first serve**?"* Our
serve ratings predict the share of **all** service points won. Those are different
quantities and we were using one for the other.

| hard courts, 2022-26 | all service points | first serve only |
|---|---|---|
| ATP population | 0.646 | **0.728** |
| WTA population | 0.570 | **0.644** |

The model was therefore about **8 points too low on every question in the family**, which is
why it was submitting numbers as extreme as 2. Two compounding errors:

1. **Wrong level.** Fixed with a per-player regression of first-serve-won on all-serve-won
   (192 ATP / 198 WTA players, >=15 matches): `ATP fs = 1.104*all + 0.015` (r=0.892),
   `WTA fs = 1.241*all - 0.064` (r=0.865). A slope above 1 is expected — first-serve
   outcomes amplify serve skill.
2. **Wrong denominator.** The binomial trials are first serves that land *in*, not all
   service points. The measured first-serve-in rate is 0.626/0.619, so the old denominator
   was ~1.6x too large and understated sampling error by a factor of 1.27.

Both corrections together reproduce the realised spread almost exactly:

| | model sd | measured sd |
|---|---|---|
| ATP | 0.0867 | 0.0859 |
| WTA | 0.0936 | 0.0934 |

Effect on a live question, a big server against a 77% threshold: **10.7 -> 46.4**.

**This is the template for pricing a new family we cannot yet score.** There is no settled
RBP for it, so instead we checked the model's *predictive distribution* against ~28,000
real player-matches. A family whose simulated spread matches reality and whose level is
unbiased will beat a crowd that is anchoring on round numbers; one that does not, will not.
That check is available before a single question settles.

## Unforced errors: the defer policy was right about the player, wrong about the match

The question is *"Will X commit more than N unforced errors?"* — a per-player count threshold.
We were answering **50 to every one of them**, which guarantees zero skill on 16 live
questions at 2x. The policy existed because the forced/unforced call is subjective.

Measured on the **official US Open point feed** (`data/raw/pointbypoint`, 1,254 matches /
2,508 player-matches, 2019–24) — the same source these questions settle on:

| finding | value | reading |
|---|---|---|
| errors per point, official | 0.1768 | vs 0.175–0.195 volunteer-charted, only **4.4% apart**, so the roster's rates are usable |
| corr(points in match, errors) | **0.715** | the count is driven by match length, which we forecast |
| player's own error rate, ICC | **0.178** | barely repeatable — the subjectivity argument is correct about the *player* |
| AUC from realised points | 0.854 | an oracle: we never know realised points |
| sd of realised points | 69 | against a simulator sd of ~57 (ATP) for a *fixed* matchup |

So the length term is real and the player term is not, and the oracle AUC overstates the
usable edge because most length variation is irreducible. The model therefore ships at
**half weight** (`UNFORCED_MODEL_WEIGHT = 0.50`) and the experimental lane is forbidden from
sharpening it (`NEVER_SHARPEN`). Live prices move to roughly 37–63 rather than a flat 50.

Two double-shrink bugs were caught while doing this, both of which would have silently
undone the change: `DEFER_TO_BASE_RATE` stacking an 0.85 pull on top (leaving 7% of the
model) and the confidence pull stacking a further 0.45 (leaving 22%). There is now exactly
one knob.

**Revisit the moment any unforced-error question settles** — this is the least-validated
thing we ship, and 0.50 was chosen as a risk posture, not measured.

## Also confirmed for the winners family

Official feed: P(winners tie) = **3.1%** and P(unforced tie) = 2.4%, against 15.8% for double
faults. Tie handling therefore barely matters for "more winners than", unlike the count
families where it moves the fair price by ~8 points. P(player 1 has more winners) = 0.4896,
so there is no orientation bias to correct.

## First live read on the fixes (2026-09-04)

**`settled_at` in the tidy table is the market CREATION date, not settlement.** Splitting on
it puts every question in the "before" bucket and silently answers the wrong question. True
cohorts have to be reconstructed from the timestamped files in `data/history/raw/`, taking the
first archive in which each result appears.

Done properly, 84 of 635 settled questions were first priced by the repaired model:

| | honest | scenario |
|---|---|---|
| before (n=551) | -0.50 | -0.50 |
| after (n=84) | **+0.83** | **+0.84** |
| difference | +1.33 [-0.28,+2.88] | +1.35 [-1.09,+3.56] |

**Not significant**, but the two independent lanes agree to within 0.02 RBP/q, and the families
that moved are exactly the ones that were changed:

| family | before | after | change |
|---|---|---|---|
| aces | -1.11 | **+3.78** [+0.96,+6.51] SIG | **+4.89** |
| double faults | -2.86 | +0.82 | +3.68 |
| duration | -0.06 | +1.35 | +1.41 |
| tiebreak | +1.22 | +0.57 | -0.65 |
| match winner | +0.29 | -2.38 | -2.67 |

### Match winner went backwards — but not because of the sharpening

The obvious reading is that the 1.35 log-odds sharpening backfired. Decomposing it says the
opposite. Inverting the transform recovers what we would have submitted without it:

| on the after cohort, both lanes (n=34) | skill/q |
|---|---|
| as submitted, alpha 1.35 | -2.09 |
| unsharpened, alpha 1.00 | **-2.94** [-5.57,-0.32] SIG |
| **gain from sharpening** | **+0.85** |

and the alpha sweep is monotone: 1.00 → -2.94, 1.15 → -2.51, 1.35 → -2.09, 1.50 → -1.87.
On the 551 older questions, sharpening would have gained +0.98 [+0.53,+1.39] SIG. The family
simply met a round of upsets; the change made a bad round less bad.

**Method worth repeating: to judge a shipped change, reconstruct the counterfactual submission
and difference it, rather than reading the family total.** The family total would have
condemned a change that was helping.

### Still no read at all on

Unforced errors, first serve, winners, deciding set and lopsided set have **not settled a
single question**, and every settled question so far is 1x. The 300 questions now live are all
2x, so the next cohort carries double weight and will be the first real test of those five.

## First settled cohort for the five new families (2026-09-05)

160 questions settled at **2x**, including the first 16 of every new family. Old families all
improved at 2x (tiebreak +2.04, match winner +0.76, aces +0.50, duration +0.34); the new ones
scored winners -4.51, first serve -3.24, lopsided -1.61, unforced -1.21, deciding +0.20.

**Every one of those CIs spans zero at n=16.** The correct response is to check each family
against large-sample data rather than to react to the live number.

### First serve: a real bug, fixed

The confidence pull was dragging a per-player model toward a base rate that ignores which
player is serving — model 0.888 submitted as 78, model 0.212 as 10.

| | value |
|---|---|
| submitted mean | 0.346 |
| model mean | 0.503 |
| realised | 0.562 |

The model was nearly unbiased and **the pull supplied all of the error**. Replacing it with a
fixed weight scores, on the honest lane and independently on the scenario lane:
weight 0.6 → +1.90 / +1.48, weight 0.8 → +4.13 / +3.71, weight 1.0 → +5.53 / +5.12. All
positive in both lanes, none significant at n=16, so `FIRST_SERVE_MODEL_WEIGHT = 0.75` keeps
most of the gain while leaving some pull in place. This is the fourth double-shrink of the
same shape: **when a family gets its own explicit weight, the confidence pull must be
suppressed for it.**

### Winners: the model is good, the live number was luck

Live AUC was 0.567 against a crowd 0.833, which looks broken. It is not. On 9,144 charted
matches, using each player's career rate from their *other* matches, our actual count model
scores **AUC 0.743 (ATP) and 0.820 (WTA)**, with Brier 0.2102 and 0.1731 against 0.25 for a
flat 50. The family has real signal and the pipeline delivers it; 16 questions is simply too
few. **No change made.** Player-level winners repeatability is only ICC 0.27/0.21, but that
does not matter here because both players share the match, so length cancels in a
head-to-head.

### Winners overdispersion: missing, and tour-specific

Winners were absent from `COUNT_OVERDISPERSION` and defaulted to a pure binomial draw, though
the realised count is ~3x as variable (ATP 2.98x, WTA 2.70x) — an inflation of confidence of
about 1.7x. But the fix is **men's only**:

| | rho=0 | measured rho | gain |
|---|---|---|---|
| ATP | Brier 0.2078, buckets off by +0.100 and +0.114 | 0.2042 | **+0.360** [+0.137,+0.603] |
| WTA | Brier 0.1760, buckets off by -0.002, +0.027 | 0.1830 | **-0.703** [-0.912,-0.485] |

ATP at rho=0 is genuinely overconfident and the sweep bottoms out near 0.008; WTA is already
calibrated and its sweep is monotone increasing, so no positive value helps. Shipping the
measured value on both tours would have been net negative — another case where the pooled
answer is worse than either tour-specific one.
