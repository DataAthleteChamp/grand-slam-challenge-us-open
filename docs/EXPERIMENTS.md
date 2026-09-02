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

### 1. Time-weighted ratings (exponential decay) — HIGH
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

### 2. Common-opponent adjustment — HIGH
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

### 3. Tournament/surface court-speed adjustment for aces — HIGH
- **Source:** tennis review, Area 6; court pace index literature. Our women's ace weakness is
  described as *"partly recoverable by per-surface, per-tournament speed adjustments."*
- **Why it might work here:** we have one global hard-court ace rate per player. The US Open
  plays faster than the average hard court, and ace rates scale with court speed. If the
  scaling differs by player (big servers gain more from a fast court), that is real
  resolution, not recalibration.
- **Test:** estimate a per-tournament ace multiplier from history; check whether a
  player×speed interaction predicts out-of-sample beyond the main effects.
- **Falsified if:** the interaction adds nothing once the tournament main effect is removed.

### 4. Serve/return Elo as a second rating system — MEDIUM
- **Source:** Kovalchik (2016) rating-system comparison; serve/return Elo the best standalone
  system, surface specificity worth ~0.5pp, ~0.3pp on hard.
- **Why it might work here:** we have exactly one rating system (ridge on match-level rates).
  Elo updates sequentially and handles recency natively. Two genuinely different estimators
  averaged is the one combination the small-sample literature *does* endorse (equal weights,
  no fitting).
- **Test:** implement serve/return Elo, compare alone and 50/50 against the ridge.
- **Falsified if:** the average is no better than the ridge alone.

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
