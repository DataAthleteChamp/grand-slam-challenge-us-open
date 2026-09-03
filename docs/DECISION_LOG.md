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
