# Experiment backlog — ranked by expected points, not by novelty

Compiled from ~60 sources across four literature reviews (quantitative betting markets,
cross-sport count modelling, tennis-specific modelling, forecasting theory) plus the
measured headroom in our own settled results.

---

## First, what the theory rules OUT

**Resolution cannot be improved by post-processing.** Murphy's (1973) decomposition gives
`Brier = Reliability − Resolution + Uncertainty`, and resolution depends only on the *rank
ordering* of forecasts. Any strictly monotone transformation `g` leaves it unchanged:
`RES(g∘f) = RES(f)`. Platt scaling, isotonic regression, beta calibration, shrinkage,
sharpening — all of them move reliability only.

Our Murphy decomposition says we are **better calibrated than the crowd on four of five
families** and lose on **resolution**. So every transform we tested was incapable of fixing
the thing that is actually broken. Those cross-validated rejections were the theoretically
correct outcome:

| tested | result |
|---|---|
| shrink toward 0.5 / base rate / family mean | no gain or worse |
| expand spread | +0.21, CI spans zero |
| per-family bias correction | −0.07, CI spans zero |
| pooled directional shading | +0.12, reverses on the manual lane |
| blend toward the actual crowd value | (previous campaign) −0.72 |
| sharpen at T=0.8 | helps 2 families, hurts 2, pooled not significant |

**Stop trying to post-process.** Every remaining idea below must bring *new information*.

**Second constraint: sample size.** DeMiguel, Garlappi & Uppal (2009) and the forecast
combination literature (Clemen 1989; Smith & Wallis 2009) both say fitted weights need
`T >> K²` before they beat equal weighting. At **n ≈ 68 per family** we cannot fit
per-family parameters and expect them to survive. Anything below must be either fitted on
*historical* data (thousands of matches) or accepted at a pre-specified value.

---

## Measured headroom — where the points actually are

Oracle floor = the Brier achievable if you knew each player's exact rate *for that match*
and still had to predict the random realisation. It is the hard ceiling.

| family / draw | our Brier | crowd | oracle floor | gap to crowd | gap to floor |
|---|---|---|---|---|---|
| aces / men | 0.1455 | 0.1566 | 0.0540 | **−1.11 (we lead)** | +9.15 |
| aces / women | 0.2079 | 0.1794 | 0.0441 | **+2.86** | +16.39 |
| double faults / men | 0.2681 | 0.2523 | 0.0550 | +1.58 | +21.31 |
| double faults / women | 0.2593 | 0.1938 | 0.0590 | **+6.55** | +20.03 |

Two readings. The gap to the *floor* is huge everywhere (9–21 RBP/question), so these
families are not intrinsically hopeless. But the gap to the *crowd* is what we are scored
on, and it is concentrated in **women's double faults (+6.55)** and **women's aces (+2.86)**.

Caveat: Wilkens (2021) puts match-prediction accuracy at a ~70% empirical ceiling, and the
tennis review argues our women's ace AUC of ~0.70 is "very close to the intrinsic ceiling
for that task". Treat the oracle floor as an upper bound that is not reachable.

---

## Ranked backlog

Ranked by `headroom × P(works) ÷ effort`. Each entry states how it will be tested and what
would falsify it.

### 1. Time-weighted ratings (exponential decay) — ~~HIGH~~ CLOSED, rejected
- **Source:** cross-sport review, Topic 3: *"the real gains come from better team strength
  estimation (e.g. time-weighting recent matches), not from the distribution choice."*
  Dixon & Coles (1997) half-life weighting is the canonical form.
- **Why it might work here:** our serve/return ridge and all count rates weight a 2021 match
  identically to last week's. We tested "recent form" before and rejected it — but that test
  was *last-3-matches*, a crude and noisy estimator. Exponential decay over the whole
  history is a different and much better-behaved thing.
- **Test:** refit rates with half-lives of 6/12/24/48 months, hold out 2024, compare
  head-to-head AUC and Brier on all four count cells. Fitted on thousands of matches, so
  sample size is not a constraint.
- **Falsified if:** flat weighting wins at every half-life.
- **RESULT — REJECTED.** Half-lives of 3/6/12/24/48/flat months, two hold-outs per cell
  (train ≤2023 test 2024; train ≤2024 test 2026), subject assigned at random:

  | cell | best half-life | vs flat |
  |---|---|---|
  | ATP aces | flat | decay strictly worse at every H |
  | WTA aces | flat | decay strictly worse at every H |
  | ATP double faults | 12 months | +0.0039 AUC, CI [-0.0020, +0.0099] |
  | WTA double faults | 12 months | +0.0064 AUC, CI [-0.0004, +0.0135], and the two hold-outs disagree - 2026 improves AUC while worsening Brier |

  Both ace cells prefer flat weighting *monotonically*, which is a strong signal rather than a
  wash: a player's serve is stable enough that old matches are still informative, and
  discarding them only adds variance. The double-fault cells hint at a 12-month half-life but
  neither interval excludes zero. Nothing to adopt.

### 2. Common-opponent adjustment — ~~HIGH~~ CLOSED, rejected (failed to replicate)
- **Source:** Knottenbelt, Spanias & Moldovan (2012); Spanias & Knottenbelt (2013).
  Reported **66.1% accuracy vs 63.5%** for rankings and 64.2% for Elo of the era; +2–3pp
  over naive player averages, though only **~1pp on hard courts**.
- **Why it might work here:** our ridge already does opponent adjustment globally, which is
  arguably a superset. The specific claim worth testing is whether restricting to shared
  opponents helps for *thin-data* players — exactly the qualifiers and wildcards where our
  ratings are weakest and where we currently fall back to tour average.
- **Test:** for settled matches where at least one player has <20 rated matches, compare our
  current price against a common-opponent estimate.
- **Falsified if:** no gain on the thin-data subset (where the whole hypothesis lives).
- **RESULT — REJECTED, and instructive.** Common opponents were available for ~72-74% of
  matches. Overall the estimate was neutral on ATP (-0.007, CI [-0.018, +0.004]) and
  significantly worse on WTA (-0.017, CI [-0.031, -0.005]).

  On the thin-data subset the first hold-out looked like a genuine find:

  | tour | hold-out | global AUC | common AUC | difference | P(better) |
  |---|---|---|---|---|---|
  | ATP | ≤2023 → 2024 | 0.7374 | **0.7863** | **+0.0481** CI [+0.0071, +0.0915] | **99%** |
  | ATP | ≤2024 → 2026 | 0.7283 | 0.6769 | **-0.0527** CI [-0.1115, +0.0028] | 3% |
  | WTA | ≤2023 → 2024 | 0.7455 | 0.6987 | -0.0463 CI [-0.0933, -0.0012] | 2% |
  | WTA | ≤2024 → 2026 | 0.7470 | 0.6739 | -0.0730 CI [-0.1298, -0.0217] | 0% |

  The ATP 2024 result would have passed a single-hold-out test at 99% confidence and is
  flatly contradicted by the very next window. Three of four splits are negative. This is the
  clearest illustration yet of why one hold-out is not evidence, and it is exactly the shape
  of the two false positives already found this week.

### 3. Tournament/surface court-speed adjustment for aces — ~~HIGH~~ CLOSED, rejected
- **Source:** tennis review, Area 6; court pace index literature. Our women's ace weakness is
  described as *"partly recoverable by per-surface, per-tournament speed adjustments."*
- **Why it might work here:** we have one global hard-court ace rate per player. The US Open
  plays faster than the average hard court, and ace rates scale with court speed. If the
  scaling differs by player (big servers gain more from a fast court), that is real
  resolution, not recalibration.
- **Test:** estimate a per-tournament ace multiplier from history; check whether a
  player×speed interaction predicts out-of-sample beyond the main effects.
- **Falsified if:** the interaction adds nothing once the tournament main effect is removed.
- **RESULT — REJECTED, in three steps.**
  1. *The main effect is real, and tour-specific.* Tournament ace multipliers span 0.86-1.19
     (WTA) and 0.85-1.26 (ATP). The US Open sits at **1.095, CI [1.046, 1.147] for the
     women** - genuinely ~9.5% faster - but **0.985, CI [0.958, 1.013] for the men**, i.e.
     average. Worth knowing, and not something I would have guessed.
  2. *But it cancels in a comparison.* Both players share the tournament, so a common
     multiplier scales both rates and barely moves P(A > B): ΔAUC between -0.0002 and
     +0.0010, ΔBrier under 0.001, hold-outs disagreeing on sign. Rejected as a comparison
     feature.
  3. *And the interaction runs the wrong way.* The player x speed coefficient is consistently
     **negative** (WTA -0.89 and -0.67; ATP -0.35 and -0.51), so fast courts *compress*
     ace-rate differences rather than expanding them. Out of sample it made the women's cell
     worse. The hypothesis was backwards.

### 4. Serve/return Elo as a second rating system — ~~MEDIUM~~ CLOSED, rejected
- **Source:** Kovalchik (2016) rating-system comparison; serve/return Elo the best standalone
  system, surface specificity worth ~0.5pp, ~0.3pp on hard.
- **Why it might work here:** we have exactly one rating system (ridge on match-level rates).
  Elo updates sequentially and handles recency natively. Two genuinely different estimators
  averaged is the one combination the small-sample literature *does* endorse (equal weights,
  no fitting).
- **Test:** implement serve/return Elo, compare alone and 50/50 against the ridge.
- **Falsified if:** the average is no better than the ridge alone.
- **RESULT — REJECTED, and reassuring.** K swept over {4, 8, 16, 32}, chosen on held-out
  service-point RMSE, compared like-for-like on rows where both systems have a rating:

  | tour / hold-out | ridge Brier | Elo Brier | 50/50 |
  |---|---|---|---|
  | ATP ≤2023→2024 | **0.2121** | 0.2281 | 0.2164 |
  | ATP ≤2024→2026 | **0.2278** | 0.2444 | 0.2329 |
  | WTA ≤2023→2024 | **0.2081** | 0.2324 | 0.2165 |

  Elo is worse everywhere (Brier +0.016 to +0.024, all CIs excluding zero) and the 50/50
  average is also worse, so there is nothing to gain even from equal-weight combination.
  Kovalchik rates serve/return Elo the best standalone tennis system, so our batch-refitted
  ridge clearing it on every metric and both hold-outs is a genuine check on the core of the
  model. Career rate alone trails badly (Brier 0.243-0.262), confirming the opponent
  adjustment is doing real work.

### 5. Shin de-vigging for longshot quotes — MEDIUM, cheap
- **Source:** Štrumbelj (2014), *International Journal of Forecasting*; Shin (1993).
  Multiplicative ≈ power ≈ Shin at p > 0.40, but Shin is **10–15% better in log-loss for
  p ≤ 0.15**. Code exists: `penaltyblog.implied.probabilities(odds, method='shin')`.
- **Why it might work here:** we de-vig 40 books multiplicatively. Most match-winner quotes
  are mid-range where it does not matter, but heavy favourites/longshots are common in early
  rounds, and Pinnacle totals are two-way markets where the same applies.
- **Test:** re-de-vig the archived quotes with Shin and power, rescore settled match-winner
  questions. Purely a re-computation on stored data — no new fitting.
- **Falsified if:** Brier difference is under 0.001, which the literature says is likely for
  two-way markets.

### 6. COM-Poisson for double faults — ~~MEDIUM~~ CLOSED, falsified
- **Source:** cross-sport review, Topic 2. Conway-Maxwell-Poisson handles **under**-dispersion
  (`ν > 1`), which Poisson and negative-binomial cannot.
- **Why it might work here:** we assume beta-binomial (over-dispersed) for both aces and
  double faults. Double faults may well be *under*-dispersed — a player has a fairly stable
  second-serve routine, so counts may cluster more tightly than binomial. If so we are
  systematically over-stating the variance and pushing every comparison toward 50.
- **Test:** measure the variance-to-mean ratio of double-fault counts per player directly.
  This is a one-line diagnostic that decides whether the idea is worth any implementation.
- **Falsified if:** the ratio is ≥ 1 (over-dispersed), in which case beta-binomial is right.
- **RESULT — FALSIFIED, closed.** Ratio of realised variance to binomial variance, per
  player with 25+ matches, on clean blocks only:

  | | aces | double faults |
  |---|---|---|
  | ATP | 2.85 | **1.67** |
  | WTA | 1.82 | **1.53** |

  Every cell is over-dispersed, double faults included. Beta-binomial is the correct family
  and COM-Poisson has nothing to add. Cost: one query. This is what a well-posed falsifier
  is for.

### 7. Direct binary classifier for the comparison — LOW/MEDIUM
- **Source:** Karlis & Ntzoufras (2009) on Skellam; Ley, Van de Wiele & Van Eetvelde (2019).
  Modelling the difference directly is **equivalent** to convolving correct marginals, and
  only wins when the marginals are misspecified — then AUC 0.70–0.73 vs 0.68–0.72.
- **Why it might work here:** our marginals may well be misspecified (see idea 6). A direct
  logistic model on rate differences, exposure and vulnerability sidesteps the distribution
  question entirely.
- **Test:** fit on 2021–23, test on 2024, compare against the current pipeline.
- **Falsified if:** it does not beat the convolution, which is the literature's default
  expectation.

### 8. Session timing — day vs night — LOW, but cheap to check
- **Source:** tennis review, Area 6. US Open day/night sessions differ in temperature and
  humidity; cooler denser night air is slower, which suppresses aces.
- **Test:** we have `opening_time` on every match. Split settled ace questions by session and
  look for a rate difference.
- **Falsified if:** no measurable split, which is likely at our sample size.

---

## Explicitly not on the list

- **Anything that post-processes our existing numbers.** Ruled out by the Murphy identity.
- **Fitting per-family or per-cell weights on our ~68 settled questions.** Ruled out by the
  sample-size results; we have already been burned twice.
- **Chasing match-winner accuracy.** With 40 books and a 0.95 market weight we score ≈0 there
  by construction, and the previous campaign measured the same thing (−34 with odds against
  +44 without). Effort there is wasted.
- **Deliberate variance-seeking to catch up.** Simulated at −EV here, and independently
  concluded in the previous campaign's `VARIANCE_STRATEGY.md`.

---

## Scoreboard

Four experiments run properly, four rejected. That is a real result, not a wasted night: it
says the remaining standard techniques are already priced into this model, and it means the
two changes that *did* validate this week - unpinning the women's count confidence (+4.22
RBP/question on the worst cell) and reconciling the simulation to the winner market (+1.72
pooled) - were where the value actually was.

The common-opponent test is the one to remember. On ATP thin-data players its first hold-out
gave +0.0481 AUC at 99% confidence, and the next window gave -0.0527. That is now the third
false positive this week that a single hold-out would have waved through, alongside the
ace-vulnerability leak and the women's confidence sweep. **Two hold-outs, always.**

---

## Method notes that apply to every experiment

1. **Check the fitting window.** Two false results this week came from data problems: the
   ace-vulnerability table was fitted on 2021–26 and validated on 2025–26, and the women's
   count confidence was swept on 2025–26 where the WTA serve statistics are corrupted. Use
   `drop_broken_serve_blocks` to see which blocks are trustworthy, and never validate on a
   window that overlaps the fit.
2. **Validate on the competition's own settled questions where possible.** The historical
   backtest and the live replay disagreed on the roster rebuild, and the live one was right.
3. **Pre-specify the parameter.** A swept optimum on 68 observations is noise.
4. **Multipliers make this urgent.** 790 weighted questions remain against 337 settled, so
   anything shipped now is worth roughly 2.3× what the same change was worth last week.
