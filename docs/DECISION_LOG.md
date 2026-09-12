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

## Blending an independent rate source into the roster (the women's count fix)

Priority was set by (skill deficit) x (weighted questions remaining). Only **two cells are
statistically significant losses**; everything else spans zero:

| cell | skill/q | CI |
|---|---|---|
| women's double faults | **-4.40** | [-6.54,-2.34] |
| women's aces | **-2.99** | [-4.91,-1.05] |
| men's aces | **+2.70** | [+0.66,+4.82] |

The same model wins on men and loses on women, so the fault is not the model structure.
Confirmed directly: on charted history the rate-difference AUC is **the same on both tours** —
ATP aces 0.793 vs WTA aces 0.786, ATP dfs 0.715 vs WTA dfs 0.716. The signal is there for
women; we were not delivering it.

The cause is the **input data**, not the model. Correlation of our roster rate with the
independent charting career rate:

| | ATP | WTA |
|---|---|---|
| ace_rate | 0.886 | 0.894 |
| df_rate | 0.874 | **0.676** |

Every cell agrees at ~0.88 except WTA double faults — exactly the worst-scoring family. The
roster is built from Sackmann files that include the corrupted 2025 WTA season and 2026
women's hard-court block; those rows are deliberately kept because dropping them removes 78
players, but they degrade the rates. The Match Charting Project is independent and hand-charted.

Credibility-blending the two (`scripts/blend_charting_rates.py`, K=10) is significant in
**both lanes at every K**, with all hold-outs positive:

| split | gain |
|---|---|
| pooled honest, K=10 | **+1.29** [+0.33,+2.31] |
| pooled scenario, K=10 | **+1.29** [+0.29,+2.30] |
| time EARLY / LATE | +1.89 [+0.58,+3.25] / +0.69 |
| tour WTA / ATP | +1.83 [+0.13,+3.57] / +0.68 |
| family df / ace | +1.72 [+0.16,+3.35] / +0.87 |

AUC moves WTA aces 0.782 -> 0.840 and WTA dfs 0.609 -> 0.727. Roster rate spread rises 29% on
women's aces, which is the resolution we were missing.

**ATP aces is excluded**: it is the one cell where the roster is better (AUC 0.863 vs 0.813),
it is where the ATP-only ace-vulnerability model is tuned, and it is our best family.

Two traps avoided:
- **Leakage.** Charting stops 2026-05-21/24, before the event on 2026-08-27. The script
  asserts this rather than trusting it.
- **My own analysis bug.** `player_a` in the settled table is NOT the question subject
  ("Will N. Podoroska record more DFs than I. Swiatek?" has player_a = Swiatek). Matching on
  player_a first flipped orientation and produced sub-0.5 AUC, which looked like a catastrophic
  model failure. The subject is whoever is named first in the question text.

## Kalshi: a second independent market, and what it validates

Verified live and unauthenticated at `https://api.elections.kalshi.com/trade-api/v2/`. It
carries a full US Open suite that Pinnacle does not: `KXATPMATCH`, `KXATPTOTALSETS`,
`KXATPEXACTMATCH`, `KXATPSETWINNER`, `KXATPGSPREAD`, `KXATPSSPREAD` and WTA equivalents.

**"Over 4.5 total sets" is exactly our deciding-set question** for a best-of-five match, and
the exact-scoreline market prices it independently (P(3-2) as the sum of the two 3-2 legs).
On one match the two agreed at 0.28 and 0.265 — a useful internal cross-check.

**Two traps, both of which nearly produced a wrong conclusion:**

1. **Most Kalshi tennis books are empty.** Of 12 ATP total-sets markets, 8 had spreads of
   0.01–0.05 and 4 had spreads of 0.72–0.98, where the "mid" is just (0+0.98)/2 = 0.49 and
   means nothing. Alcaraz vs Paul quoted bid 0.22 / ask 0.94. **Any use of Kalshi must filter
   on spread**; a mid without a spread check is noise.

2. **Compare against the right lane.** The scenario lane sharpens at temperature 0.80 by
   design, so its deciding-set numbers sit ~6 points below the model. Comparing it to Kalshi
   produced an apparent 6.3-point systematic bias on 8 of 8 matches, which looked like strong
   evidence that `TOUR_MATCH_SHARE` had overshot.

Against the **honest** lane the picture is the opposite and reassuring:

| | value |
|---|---|
| mean difference vs Kalshi | **+0.2 points** |
| mean absolute difference | 2.9 points |
| sign split | 4 above, 4 below |

So the deciding-set model is already calibrated to an independent sharp market, and the
`TOUR_MATCH_SHARE` fix landed correctly — at share 0.55 an even ATP matchup prices at 0.2963
against a measured close-matchup rate of 0.274–0.302. **No change made.**

Still worth having: Kalshi is a genuine second market for match winner, set winner and game
spread, and its `KXATPACES` series (per-player ace totals) ran at Wimbledon 2026 and may open
at the US Open — that would be the first sharp market ever available for our ace family.

Other verified sources: Polymarket has outright-winner markets only (gamma + CLOB, no auth).
**The `tennis_atp`, `tennis_wta` and `tennis_slam_pointbypoint` repos now return 404** — only
`tennis_MatchChartingProject` survives publicly, which is the source we just blended in.
Our local copies are therefore irreplaceable; do not delete `data/raw/`.


## Kalshi ace markets: closed as a price source, opened as a data source

`KXATPACES` ("ATP Tennis Aces") was the last high-expected-value lead on the board — the first
sharp market that would ever have priced our ace family. Tested directly and **it does not work
as a price source**:

- **Zero open events** for the US Open. All 50 settled events are from Wimbledon (July).
- **The books carry no pre-match price.** A representative market shows `liquidity_dollars:
  0.0000`, `no_bid 0.0000 / no_ask 1.0000` and `open_interest_fp: 38.64` at settlement.
  *Correction to an earlier reading of this:* the `/markets/trades` endpoint shows the markets
  **did** trade — 5, 1 and 1 trades on three sampled markets, in sizes of 25 to 555 contracts.
  But every trade lands at **0.98-0.99 in the last hour before expiry**, i.e. in-play once the
  ace count was already near-certain. Timestamps of 12:07-12:12 against a 13:00 expiry make
  this unambiguous. So the markets were traded, just never as a pre-match forecast, which is
  the only thing we could have used.
- The settled `last_price` values are 0.00 or 0.99 almost everywhere and are **settlement
  artefacts, not forecasts** — one market shows `last=0.0000` with `result=yes`.
- **No WTA ace series has ever existed**, and the women's draw is where we actually lose points.

So there is no ace market to reconcile against, and polling for one is not worth the cost.
This closes the last large lead cleanly.

**However** the same endpoint yields something we do lack. Settled markets expose
`expiration_value`, e.g. `p1=31 p2=10` — the **actual ace counts for both players**, together
with `rules_primary` naming the match and tournament. Since `tennis_atp`, `tennis_wta` and
`tennis_slam_pointbypoint` all now return 404, and Sofascore is Cloudflare-blocked, this is one
of the few remaining public routes to real per-match serve statistics.

Its value is nevertheless limited by the saturation result: the draw median is 162 matches
(men) and 103 (women) against a learning curve that flattens at ~20, so extra volume buys
almost nothing. It is worth harvesting only for the 9 players below saturation, 8 of whom are
women — not as a general data expansion.

## Bovada prices our two worst families directly (the find of the survey)

`https://www.bovada.lv/services/sports/event/coupon/events/A/description/tennis?lang=en&eventsLimit=50`
returns 1.37 MB of JSON, **no auth, no geo-block, no API key**, and carries two markets nobody
else in the world offers:

* **"Most Aces"** — head to head, with an explicit **Draw** outcome
* **"Most Double Faults"** — same structure

Those are exactly the two families where we lose points, and the three-way structure means a
proportional de-vig yields P(A more) with the tie already priced, which is the precise quantity
our question asks for. Six matches each at the time of testing.

**Immediate orientation-independent result: the market confirms our tie constants.**

| | Bovada, de-vigged | our measured (official feed) |
|---|---|---|
| double-fault ties | **0.1107** | ATP 0.1008 / WTA 0.1141 |
| ace ties | **0.0806** | ATP 0.0432 / WTA 0.1286 |

An independent bookmaker, pricing with its own money, agrees with the tie structure we derived
from the official point feed. That is meaningful confirmation of `COUNT_STATS`, and it is safe
to state because a tie probability does not depend on which player the question names first.

**Not yet acted on, deliberately.** The per-match gaps between our submissions and this market
look large in places, but our submission is oriented to the *question's subject*, which is not
always `player_a` — the exact trap that produced sub-0.5 AUC earlier in this session. Anyone
building on this must resolve the subject from the question text before comparing, and should
re-read the orientation note in the charting-blend entry above.

Other survey results: no source anywhere prices winners, unforced errors or first-serve %.
Kalshi has aces but zero open US Open events and no WTA series ever. Betfair has the deepest
tennis liquidity and free historical files but needs a free application key. Smarkets and SX
Bet work unauthenticated but carry no props.

### Orientation-correct comparison, and why nothing is blended yet

Resolving the subject from the question text (never from a positional player field) gives 12
matched pairs against the live book:

| | correlation with Bovada | mean signed diff |
|---|---|---|
| aces | **+0.797** | +7.9 pts |
| double faults | **+0.434** | +4.1 pts |
| pooled | +0.653 | +6.0 pts, mean absolute 13.9 |

The correlation confirms we are reading the same quantity, so this is not a parsing error. But
the agreement is far weaker on double faults — our worst family — and the four largest
disagreements, all 23 to 27 points, are **women's matches**:

    Sabalenka v Townsend  aces  ours 60  market 36.4
    Kalinskaya v Navarro  aces  ours 45  market 21.0
    Sabalenka v Townsend  dfs   ours 61  market 36.7
    Kalinskaya v Navarro  dfs   ours 52  market 78.7

That is exactly the cell where we lose 4.40 RBP/question, and a bookmaker is taking the other
side by 25 points.

**Deliberately not blended yet.** There are no historical Bovada prices anywhere, so this input
cannot be replayed against settled questions — tier 1 of our evidence hierarchy is simply
unavailable, and every time this project shipped on weaker evidence it later had to be undone
(the WTA sigma change, four separate double-shrinks). `scripts/fetch_bovada.py` therefore
snapshots the quotes with a UTC timestamp so the history builds itself. Once the captured
matches settle we can measure whether Bovada beats us rather than assuming it, and blend at a
weight the measurement supports.

## Is the Bovada blend a gamble? Three checks that do not need settlement

A fair challenge: blending an unvalidated retail line into our worst families could be
gambling rather than modelling. Only 4 Bovada-priced questions have settled, far too few to
answer directly, so here are the checks that ARE available now, on 56 quotes across 14 matches.

**1. The line tracks real skill, not noise.** Comparing each match's two players by the gap in
their career rates against the gap in Bovada's prices:

| family | correlation |
|---|---|
| aces | **+0.763** |
| double faults | **+0.601** |

Bovada is pricing the same underlying quantity we model. A near-zero correlation would have
meant we were blending in something arbitrary; this is not that.

**2. The margin is professionally maintained.** Overround averages 1.1026 on aces and 1.1007
on double faults with a standard deviation of only 0.008-0.010 across every snapshot. A book
that keeps its margin that tight is running a real pricing process.

**3. The tie mass independently reproduces our own constants.** Bovada's de-vigged draw
probability is 0.0815 on aces and 0.1066 on double faults, against our measured 0.043 ATP /
0.129 WTA and 0.101 ATP / 0.114 WTA. The snapshots mix tours, so those are the right ballpark.

**What this does and does not justify.** It justifies blending at all. It does not justify any
particular weight. The 0.75 on women's double faults rests on a separate measurement - that
replacing our answers there with the base rate scores -0.00 RBP/question, so the incumbent is
provably worth nothing - not on any belief that Bovada is excellent.

**What is still unmeasured.** Whether Bovada beats us on the questions where we disagree. The
correlations of 0.60-0.76 mean substantial disagreement, and that disagreement is either
information we lack or noise we are importing. Only settled outcomes can separate the two.

**No second book exists.** Bovada's endpoint pattern was tested against its Bodog-family
sister sites: sports.bovada.lv is the same book, BetOnline and SportsBetting.ag return 403,
bodog.eu does not resolve. A multi-book consensus, which would be the statistically preferable
design, is not currently available for these two families.

## 2026-09-09 — the settlement feed was silently broken, and we are under-confident

### The bug that hid everything

`scripts/settle.py` probed `result` before `result_value`. The API added
`result_value` (0-100), `crowd_value` and a per-question `rbp` partway through the
event, and left `result` as a display string — `"NO (0)"`. No outcome parser
recognises that, so every settled row was discarded and the ledger reported
"nothing settled yet" for the entire tournament. 1135 questions of tier-1
evidence were sitting unread behind a key-ordering mistake.

Fixed by probing `result_value`/`crowd_value` first and accepting 100 as "it
happened". The ledger now settles 1155 records.

### Where the points actually go

    total RBP  +1913.4 over 1135 questions  (+1.686/q)
      free term  +2675.5  (+2.357/q)   paid to everyone who answers
      skill term  -762.1  (-0.671/q)   the only part we control

| family | n | skill | our AUC | crowd AUC | our mean | realised |
|---|---|---|---|---|---|---|
| double_faults | 177 | −289.1 | 0.638 | 0.718 | 0.442 | 0.508 |
| winners | 50 | −204.5 | 0.600 | 0.820 | 0.486 | 0.540 |
| lopsided_set | 50 | −181.8 | 0.322 | 0.345 | 0.609 | 0.460 |
| first_serve | 50 | −123.2 | 0.600 | 0.651 | 0.471 | 0.540 |
| match_winner | 177 | −49.1 | 0.875 | 0.881 | 0.523 | 0.475 |
| aces | 177 | −37.3 | 0.816 | 0.842 | 0.484 | 0.469 |
| tiebreak | 177 | **+149.5** | 0.674 | 0.644 | 0.262 | 0.209 |

Four families account for −798, more than the whole −762 deficit; everything else
nets positive.

### Adopted: logit sharpening where the ranking is good

Calibration bins showed severe under-confidence, not bias. On match winner we
submitted 28% where the truth was 7%, and 44% where it was 27%, while ranking
matches well (AUC 0.875). A compressed but correctly ordered forecast is exactly
what sharpening repairs.

| family | alpha | skill delta | recent-only | early-only | time split |
|---|---|---|---|---|---|
| aces | 1.40 | +123.5 [+28,+216] SIG | +64.2 SIG | +59.3 | +88.1 |
| match_winner | 1.30 on top of 1.35 | +137.0 [+54,+213] SIG | +22.0 | +114.9 SIG | +11.4 |

Positive in every subsample and in a fit-on-first-60%/score-on-last-40% split.
Chosen below the split's own optimum (1.75 and 2.00): after sharpening at these
values we are still short of the realised rate in every calibration bin, so this
corrects under-confidence without overshooting. `EXTREMISE_ALPHA` is now
`{MATCH_WINNER: 1.75, ACES_COMPARISON: 1.40}` — sharpening composes
multiplicatively, so 1.35 × 1.30 = 1.75.

### Rejected

| change | why |
|---|---|
| sharpen winners | AUC 0.600 against a crowd on 0.820. −25.6 at alpha 1.50. Sharpening amplifies a forecast pointing the wrong way. |
| sharpen double faults | AUC 0.638 against 0.718. −100.4 at alpha 1.50, −248.5 SIG at 2.00. |
| shrink double faults | grid +11.3 not significant, held-out −50.0. |
| shrink tiebreak | grid +56.3 not significant, held-out −53.5. It is our only strongly positive family; leave it alone. |
| shrink unforced | grid SIG at 1.25 but held-out −24.3. Conflict, so no. |
| shrink/shift first_serve, duration | positive in both tests but not significant in either. Four changes are already live and unmeasured; not stacking more. |

### Confirmed already-shipped

`SHRINK_TO_EVEN[LOPSIDED_SET] = 0.85` is right and lands hard: the grid gives
+242.2 [+4,+481] SIG at full shrink and the held-out split +83.8. The −181.8 in
the table predates it — it only reached submissions on 09-07.

`FIRST_SERVE_MODEL_WEIGHT = 1.00` is working: first serve went −51.8, −64.1,
−13.2, +10.8 by day. Aces turned positive from 09-02 (+109.9 since) after the WTA
coupling fix.

### Still open

Winners (−204.5) and double faults (−289.1) are discrimination failures, not
calibration failures. No transform of our own number helps, because the crowd
genuinely knows more. Neither is fixable by re-weighting; both need a better
signal or an admission that we do not have one.

## 2026-09-10 — final lock, on 1176 settled questions

Thirty questions remain, all at the 3x multiplier, so this is the last chance to
correct anything. Every family was re-tested on the updated sample.

### Checked and cleared

**Does sharpening aces double-count the Bovada blend?** Raised because our ace
numbers sit 7-10 points below Bovada on the two finals it prices (Zandschulp 27
against 34.8, Pegula 23 against 30.3), which is the new sharpening pushing a
market price further from even. Reconstructed the Bovada weight actually used for
every settled question from the run archives: only 20 ace questions ever had a
Bovada line, and 159 were priced at weight 0.00. So alpha 1.40 was measured on
essentially pure-model numbers, and on the 20 blended ones sharpening is still
positive (+5.1). No double-counting. Kept.

**First serve arithmetic.** Spot-checked against the run notes: Tiafoe expected
0.761 against a 0.700 threshold with sd 0.076 gives z = 0.80 and p = 0.789, which
is what was submitted. The model is doing the right calculation.

### Adopted: shrink the two remaining low-AUC families

| pull | duration | first serve |
|---|---|---|
| 10% | +42.6 [+2.2,+85.4] **SIG** | +17.3 [−7.6,+44.0] |
| 20% | +73.0 [−8.1,+158.8] | +30.8 [−19.0,+84.1] |
| 40% | +97.3 [−65.8,+271.5] | +46.6 [−52.1,+152.3] |
| time split | **+60.5** held out | **+10.3** held out |

Set at 20% and 15%, well below the 40% optimum. Only the 10% duration cell is
individually significant; the case rests on the estimate being positive at every
factor across four independent checks, not on any single one clearing the bar.
This completes the rule the guard tests now enforce: sharpen where the ranking is
good (match winner 0.875, aces 0.816), shrink where it is poor (lopsided set
0.322, duration 0.581, first serve 0.622).

### Rejected again, with more data

| change | why |
|---|---|
| shrink winners | AUC 0.617, but worth between −5.6 and +2.3 across the whole range and −5.0 held out. The crowd is on 0.828; no repositioning of our own number reaches that. It stays our worst family and we have no fix. |
| shrink double faults | grid +21.8 at 25% pull, held-out −32.9. Conflict, so no. |
| shrink unforced | significantly **negative** at every factor (−29.8 to −162.6 SIG). Actively harmful. |

### Reading the 41 questions that settled under the new model

−94.3 skill looks alarming and means almost nothing: it is 4 matches, not 41
independent questions. The Alcaraz upset alone accounts for about −63 of it,
cascading through his match (winner −22.1, over 110 minutes −15.1, deciding set
−14.8, a tiebreak −11.4). Sharpening buys exactly that tail risk in exchange for
the +137 measured over 177 questions. Four matches cannot overturn it, and
reverting on this evidence would be the overreaction the earlier lessons warn
about. Encouraging at n=4 each: lopsided set +10.0, aces +3.7, winners +2.4.

## 2026-09-10 — the Zverev match, and sharpening was still too timid

Eleven questions settled under the locked-in model: **+90.2 RBP, +2.2 skill, 7 of
11 beat the crowd**. The first clearly positive cohort. Three lessons.

### 1. Bovada is excellent at aces and mediocre at double faults

The one large loss was Zandschulp to record more double faults: we submitted 82,
the crowd 66, and the answer was NO, for −23.68. Bovada had that at 84.85 and we
followed it at weight 0.85. Meanwhile the ace question went the other way — our
27 against Bovada's 34.8 and the crowd's 30, answer NO, +1.71.

Separating the two families on the 42 settled questions where a Bovada line was
actually used:

| | our Brier | crowd Brier | Bovada Brier | our AUC | crowd AUC | Bovada AUC |
|---|---|---|---|---|---|---|
| aces | 0.1771 | 0.1838 | **0.1529** | 0.841 | 0.827 | **0.875** |
| double faults | 0.2520 | **0.2426** | 0.2480 | 0.663 | 0.620 | 0.692 |

On aces Bovada beats everyone and moving further toward it is significant
(+21.3 at 0.35, +36.5 at 0.65). On double faults **the crowd beats Bovada**, and
moving toward Bovada is worth at most +8.4 and never significant. The 0.85 weight
was set from a pooled test that the ace half carried. Left in place for now
because it is not measurably harmful either, but it is not evidence-backed for
double faults and should not be cited as if it were.

### 2. A hypothesis I tested and had to abandon

Our ace numbers sit below Bovada because sharpening pushes them away from even,
which suggested sharpening might be double-counting a blend that is already
sharp. Splitting the ace questions by whether a line existed refutes it: dropping
to alpha 1.00 costs −18.1 on the 21 questions that had a line and −157.5 SIG on
the 159 that did not. The market blend and the sharpening correct different
things and neither substitutes for the other.

### 3. The first sharpening pass was too conservative

At alpha 1.40 and 1.75 both families remain under-confident in five of six
calibration bins — match winner submits 28% where the realised rate is **6.9%**,
aces submits 71% where it is **100%**.

| total alpha | skill delta |
|---|---|
| aces 1.47 | +21.6 [+8.4,+33.7] SIG |
| aces 1.61 | +58.3 [+19.6,+93.7] SIG |
| match winner 1.84 | +30.0 [+14.6,+44.8] SIG |
| match winner 2.01 | +80.9 [+35.7,+123.5] SIG |

A time split fitting the extra alpha on earlier questions and scoring on later
ones is positive at all three train fractions for both families (aces
+75.9/+74.3/+44.8, match winner +51.7/+15.0/+12.5). Raised to **1.60** and
**1.95** — short of the 2.24 and 2.80 the split wants, because the split hits the
top of the search grid, so its optimum is unidentified rather than measured. Both
chosen values stay inside the significant range and leave every calibration bin
still under-confident.

Sharpening matters least where it is riskiest: the remaining matches are late
rounds where our numbers sit near even, and 55 sharpens only to 60.

### Also worth recording

Winners returned **+14.95** on the single question in this batch, our best of the
cohort, in the family the previous entry called unfixable. One question proves
nothing, but the family is not uniformly hopeless.

## 2026-09-11 — the final asks ten questions no earlier round asked

`QUESTIONS_PER_MATCH["F"] = 20` was in the taxonomy from the start, but the
parser only knew the ten wordings the earlier rounds used. When the final's
markets opened, **seven questions fell through to `Family.OTHER` and were
answered with a flat 50 at the 3x multiplier**. The coverage guard reported zero
unanswered the whole time, because they *were* answered — just not with anything.

| question | was | now |
|---|---|---|
| Will both players win at least one set? | 50 | **37** |
| Will any player win 2 sets in a row? | 50 | **81** |
| Will the match end with E. Rybakina winning 2:1? | 50 | **18** |
| Will E. Rybakina win more than 73 total points? | 50 | **49** |
| Will A. Sabalenka win more than 75 total points? | 50 | **48** |
| Will X cover more than N feet? (x2) | 50 | 50, deliberately |

### The first one was not a modelling error, it was a contradiction

"Both players win at least one set" and "the match requires a deciding set" are
the **same event** in a best-of-three: the loser takes a set exactly when the
winner fails to sweep. We were answering 37 to one and 50 to the other about the
same match. Both are now read off `p_deciding_set`, and a test asserts they are
submitted identically so they can never diverge again.

### Two sets in a row was the largest single error

Every straight-sets win contains two consecutive sets, and so does every
three-setter where the winner lost the opener. That is `p_straight_sets` plus
about half of `p_deciding_set` — 0.81 here, not 0.50. A flat 50 was **31 points**
wrong on a 3x question.

### Total points: why the mean is the wrong summary

Expected points for Rybakina are 76.7 against a threshold of 73, which sounds
like a comfortable yes. Taking a mean and a standard deviation gives about 70.
The correct answer is **49**, because match length is strongly bimodal: a
straight-sets win is ~139 total points (subject ~69, below the line) and a
three-setter ~205 (subject ~102, far above it), with little mass between. The
answer is essentially `P(three sets)` plus a small tail, not a tail probability
around the mean. So the tail is evaluated run by run over the simulated games
and then averaged. Using the mean would have been 21 points wrong.

### Distance covered is left alone, on purpose

No source this entry can reach publishes metres covered — not the official feed,
not Flashscore, not any book. It is now *classified* so it stops counting as a
parse failure, and explicitly priced at the base rate with a note saying why.
Deriving it from match length would be a guess wearing a model's clothes.

### The existing tests caught a regression mid-change

The first version of the exact-scoreline rule matched a bare `N-N` and silently
stole "Will a set end 6-0, 6-1 or 6-2?" from the lopsided-set family. A guard
test that had been in the suite for weeks failed immediately. The rule now
requires match-level context ("winning 2:1", "the match ends 2:1"), and that
exact string is pinned in the new test.

## 2026-09-12 — the market is the answer, and first serve was shrinking to the wrong place

### Pinnacle's guest API is gone; The Odds API replaced it

`guest.api.arcadia.pinnacle.com` now fails to connect at all (status 000, DNS
level) — the free calibration benchmark recorded in LESSONS is dead. The Odds API
still answers and is strictly better: one call returns **57-58 books per match**,
including Pinnacle itself, all three Betfair exchanges, Smarkets and Matchbook.
Two research agents sent to scrape betting sites directly returned **nothing**;
the aggregator did the job in one request.

### The reserve existed for this, so it was spent

`QUOTA_RESERVE` was 40 against 40 remaining credits, so the bot had been refusing
every odds call for days — the Zverev semi-final was priced from the model alone
while a de-vigged 57-book consensus sat one request away. Lowered 40 → 16 → 2 as
the rounds ran out. Holding quota back past the last match of the tournament is
the precise failure the reserve is meant to prevent.

### Match winner: take the market whole

With quotes restored, the pipeline was blending the consensus 95/5 with our
model, pulling it 5% toward 0.50, and only then sharpening. Replaying the **107
settled match-winner questions that had a quote**:

| submitted instead | skill |
|---|---|
| our live pipeline | +16.1 |
| the raw de-vigged market | +136.0 |
| the market sharpened at 1.95 | **+287.8** |

A time split fitting alpha on earlier questions and scoring on later ones is
positive at all three train fractions (**+166.8, +145.6, +116.5**), with fitted
alphas of 1.50-1.80 against our live 1.95, which sits in the flat top of the
curve. So when a quote exists the model weight and the base-rate pull are both
switched off and only the sharpening survives.

Why sharpening a market helps at all: the de-vigged consensus is itself
favourite-longshot biased in this sample — it prices the 0.133 band at 0.133 and
realises 0.000, and the 0.848 band at 0.848 and realises 0.957.

**It worked immediately.** "Will K. Khachanov win?" went out at **6** against a
crowd on 26 and settled NO: **+28.76 RBP**. The men's semi-final cohort scored
**+318.5 RBP, +26.1 skill, 21 of 30 beating the crowd**.

### First serve was shrinking toward the wrong number

First serve is now the worst family left, −142.8 over 58 questions, and the
damage is concentrated rather than spread: in the band where we submit 0.25-0.40
we say 0.331 and the realised rate is **0.625 over 16 questions** — 77% of the
family's entire loss, and significant on its own (10 of 16 against a predicted
0.331, p about 0.02). Both losses in the latest cohort were this: Sabalenka at 28
and Rybakina at 33, both YES, −17.03 and −16.80.

The tempting fix was to move the anchor to the family's realised rate of 0.586.
**Rejected**: 34 of 58 is only 1.3 standard errors from even money (p about
0.11), and a split that fits the anchor as well as the pull picks 0.552, 0.500
and 0.475 on three windows and loses 24.8 held out on the third. Moving an anchor
on evidence that weak is how the earlier base-rate hedges went wrong.

With the anchor held at 0.50, one fitted parameter is stable: the split picks
0.70, 0.65, 0.70 and scores **+21.8, +26.7, −0.4** held out. `SHRINK_TO_EVEN`
for first serve raised 0.15 → **0.50**, below every fitted optimum. Heavy
shrinkage is what the discrimination justifies: AUC 0.608 against a crowd on
0.648. The two final questions now go out at 50 and 43 rather than 28 and 33.

## 2026-09-13 — two bugs found on the finals, and a quote that outlives its request

### "Both players win at least one set" was wrong in a best-of-five

Shipped on 2026-09-11 reading `p_deciding_set`, with a docstring claiming the two
are the same event "whenever the match is decided by winning a majority of sets".
That is false. The loser takes a set exactly when the winner fails to sweep, so
the quantity is `1 - p_straight_sets`. In a best-of-three the only non-sweep is
2-1 and the two coincide, which is why the women's final looked right and the
test passed. In a best-of-five a **3-1 has both players winning a set and never
reaches a decider**.

The men's final went out at **25**, the same number as its deciding-set answer.
Corrected it is **60** — a 35-point error on a 3x question. The women's final is
unchanged at 37, still equal to its decider, as it should be. The test now
asserts the two are equal only in a best-of-three and strictly apart in a
best-of-five.

### The orientation block was crying wolf on every final

`check_comparison_complement` requires paired comparisons to sum to about one.
Correct for aces, double faults and winners, and wrong for first serve, which
despite its family name is not head to head in this event: it asks whether each
player separately clears a threshold of their own ("Zverev more than 78% of
first-serve points", "Shelton more than 74%"). Independent events against
different lines are under no obligation to sum to anything, and the check fired
BLOCK on every final, forcing a `--force` that would equally have waved through a
genuine inversion. First serve is now exempt, with the reason recorded.

### A market quote now outlives the request that fetched it

The 500-request free tier runs out exactly when it matters most: both finals
score at 3x and the quota hit zero the morning of the men's final, so the
match-winner question was priced from the model alone at **19** while the sharp
consensus said **0.4297**. Sharpened properly that is **37**.

`MarketBook.fetch` now writes every successful pull to `data/markets/oddsapi/`
and, when a call fails, falls back to the newest snapshot under 36 hours old.
A day-old de-vigged consensus over 33 books is not a compromise: discarding it
measured **271.7 skill points worse** across 107 settled questions. Two tests
cover it, one that a fresh snapshot is used and one that a stale one is ignored.

### Also

Pinnacle's guest API is dead (connection fails at DNS level). The Odds API
replaced it and returns 57-58 books per match including Pinnacle itself and three
exchanges. Two research agents sent to scrape bookmakers directly returned
nothing at all; the aggregator did it in one request. Quota is now exhausted, so
the snapshot cache is what carries both finals.

## 2026-09-12 — the women's decider ran low, and `closing_time` is not the lock time

### A real bias, found one hour too late

Going through the women's final question by question against Polymarket, its
set-winner prices implied `P(deciding set)` near **0.49** while we were submitting
**0.37**. That prompted a check of the family, which found a genuine and
substantial bias — but only on one draw:

| draw | n | ours | realised | skill | +0.10 shift |
|---|---|---|---|---|---|
| men's (best-of-five) | 24 | 0.247 | 0.292 | −2.9 | **−2.4** |
| women's (best-of-three) | 30 | 0.331 | **0.467** | **−41.3** | **+51.2** |

Pooling the draws hides this completely, and my first pass did exactly that: the
"women-only" filter matched all 58 rows because the question carries no player
name, so it silently did nothing. A best-of-five decider and a best-of-three
decider are different events that happen to share a wording.

Women-only, a split fitting the shift on earlier questions and scoring it on
later ones is positive at all three train fractions (+26.6, +34.4, +29.6) with
fitted shifts of 0.14, 0.07 and 0.10; the binomial on the bias gives p = 0.086.
Shipped at **+0.08**, below the median fitted value, keyed on `("WTA", family)`
and covering `BOTH_WIN_SET` too because in a best-of-three it is the same event.

**It did not reach the women's final.** The market locked while the analysis was
running, so the entry went out at 37 on both questions. Since the correction is
WTA-only and no women's matches remain, it changes nothing this tournament. It is
recorded for the next one.

### The lesson that cost it: `closing_time` is not the lock time

The markets API advertised `closing_time` of **2026-09-13T08:00Z** for a match
starting 2026-09-12T19:30Z, and reported every market `open` 40 minutes after
play began. They locked about an hour into the match, roughly eleven hours before
the advertised close. Treating `closing_time` as the deadline is what turned a
usable finding into a missed one.

The existing rule — have a number in before the court opens — stands, and the
corollary is now explicit: **once a match is under way, treat every question on
it as already locked**, whatever the API says about when it closes.

### Also checked, and clean

Bovada stopped repricing at the first ball: ten snapshots spanning seven hours
are identical to four decimal places, before and after the start. There is no
live signal there. Kalshi carries the players only inside cross-category parlays
with no bids, so it is not a usable source for this event. Polymarket is the one
venue that prices these matches live and to the set.

## 2026-09-13 — the market reconciliation had never once run

Scenario-testing the men's final turned up a contradiction: the reconciled
simulation returned P(Zverev) = **0.757** while the market it was supposedly
reconciled against said **0.570**.

`_simulate` looked the quote up as `ladders.get(context.player_a.name)`, but the
payload is nested — `{"match_winner": {name: probability}}` — which is what
`_read_market` and `_price_outcome` both correctly expect. The top-level lookup
therefore returned `None` every time, `target` stayed `None`, and the shift
search never ran. **Not once, for the entire tournament.**

Its own docstring describes the gain it was supposed to deliver: +2.57
RBP/question on duration (CI [+0.46, +4.67]), +0.87 on tiebreak, +1.72 pooled
(CI [+0.54, +2.98]), measured on 49 settled matches. All of it was dead, and the
result was exactly the inconsistency the method exists to prevent: the
match-winner answer came from the market while every structural question came
from a simulation that disagreed with it by 19 points.

Effect on the men's final, raw against reconciled:

| quantity | raw | reconciled |
|---|---|---|
| P(Zverev wins) | 0.757 | **0.603** (market 0.570) |
| deciding set | 0.252 | **0.293** |
| any tiebreak | 0.781 | **0.816** |
| two or more tiebreaks | 0.406 | **0.469** |
| both win a set | 0.593 | **0.654** |
| lopsided set | 0.359 | 0.324 |
| mean total games | 41.2 | **42.8** |

Every move is in the direction the market implies: a closer match runs longer,
produces more tiebreaks and more deciders, and fewer lopsided sets. Submissions
moved accordingly — both win a set 60 to 66, final-set tiebreak 70 to 73, two or
more tiebreaks 36 to 41, deciding set 25 to 29, Shelton 3:2 from 9 to 13.

Both payload shapes are now accepted, and two tests guard it: one asserts the
reconciled run differs from the raw one **and moves toward the quote**, the other
that a bare `{name: probability}` mapping still works. Asserting only that the
arithmetic is right would not have caught this — the arithmetic was always right,
it was never reached.
