# Research findings

Every constant in the code traces back to something here. Findings are marked
**measured** (computed from data in this project), **published** (cited literature), or
**inferred** (reasoning, flagged as such).

---

## 1. The competition

| Fact | Source |
|---|---|
| 1,280 questions, 190 seeded matches, 27 Aug – 13 Sep 2026 | Official rules |
| Scoring `RBP = (crowd_brier − your_brier) × 100` | Scoring page |
| Multipliers 1× (R1–R2), 2× (R3, R16), 3× (QF, SF, F) | Scoring page |
| Ranking is **cumulative total RBP**, not average | Scoring page |
| **Ties broken on the final match** | Official rules §6 |
| Connected Models must use **publicly available data only** | Official rules §5 |
| Sponsor may request **access to inspect the model** | Official rules §5 |
| Two Connected Models per account; a second account voids all entries | Official rules §5 |
| Probabilities are integers **1–99**; 0 and 100 rejected | API docs |
| Rate limit **60 req/min per IP**; batches of ≤50 | API docs |

Predecessor event: the Jump Trading Probability Cup (2026 World Cup), **4,013 entrants**,
same platform and scoring. Field shrank from ~2,500 active in the group stage to **~467 by
the semi-finals** — so late-round questions carry a 3× multiplier *but a sharper crowd*.

---

## 2. Measured base rates

**Method.** ATP/WTA main-tour results, hard court, 2015–2026 (Sackmann-schema mirror).
Scores parsed with a tokenizer accepting both `7-6(6)` and bare `7-6`.

> **Data trap.** Files before 2024 carry tiebreak points in parentheses; 2024–26 files do
> not. A parser handling only one format silently drops *every tiebreak match* from half the
> sample. This bug initially produced an ATP tiebreak rate of 0.395; the correct figure is
> **0.487**, which matches the published 50.3%.

Base rates by tour and matchup: see the table in the README. Key derived facts:

- **measured** — Men reach a tiebreak 2.2× as often as women (0.487 vs 0.225), but women
  reach a deciding set 1.7× as often (0.337 vs 0.202). The two point in opposite directions.
- **measured** — 65% of matches contain a 6-0/6-1/6-2 set. Published secondary sources say
  20–30%; they reasoned from *per-set* bagel rates and forgot a best-of-five has five chances.
- **measured** — Set 1 has the *highest* tiebreak rate in every format (ATP Bo5: 0.165 in
  set 1 vs 0.116–0.133 later). Deciding sets are the loosest, not the tightest.

## 3. Count comparisons and ties

**measured**, 7,545 charted men's matches (Match Charting Project `Total` rows):

| Statistic | Mean/player | P(tie) | Even-matchup P(A>B) |
|---|---|---|---|
| Double faults | 2.9 | 0.158 | 0.421 |
| Aces | 7.0 | 0.080 | 0.460 |
| Winners | 29.1 | 0.029 | 0.485 |
| Unforced errors | 29.0 | 0.030 | 0.485 |

**published** — Skellam closed form `P(tie) = e^{−(μ₁+μ₂)} I₀(2√(μ₁μ₂))` predicts 0.167 for
double faults against 0.158 measured.

## 4. Signal vs noise

**measured** — permutation tests with leave-one-out player controls, Match Charting Project:

| Factor | Result |
|---|---|
| Chair umpire | **Not significant** after controlling player + surface (p = 0.16 ace, 0.82 DF). Raw p = 0.0005 was pure confounding — umpires are assigned to specific tournaments and marquee players. Power: 84% to detect a between-umpire SD of 6% of the mean. |
| Court assignment | **Not significant** (p = 0.21). Raw p = 0.0027 was a *labelling artefact* — "Ashe", "Arthur Ashe", "Arthur Ashe Stadium" and "Stadium" are one court recorded four ways, and the spread between those aliases was as wide as between genuinely different courts. |
| Carryover fatigue | **Significant**: −0.77pp ace rate from shortest to longest previous match (p = 0.011, n = 1,490, within-player centred). The raw 17pp win-rate gap is mostly confounding — weaker players have long matches *and* lose next round. |
| Within-match decay (Bo5) | First-serve points won −1.0pp from set 1 to set 5. Ace rate **flat**. Double faults **fall** (−0.35pp) — tired players serve more conservatively, the opposite of folklore. |

**published** — height explains **r² = 0.83** of men's ace rate and **r² = 0.92** of
first-serve points won (Sackmann, 156 player-seasons). Left-handedness is worth 2–4pp on
serve points won. Break-point "clutch" has year-over-year r ≈ 0.10–0.20 — noise.

**published** — unforced errors are subjectively scored: shown ten identical points, three
separate coaching conferences agreed on the forced/unforced call **3–4 times out of 10**.
Not comparable across matches, courts or tournaments. This is why the family defers to base
rate.

## 5. Where the crowd is wrong

**measured** — four frontier LLMs polled as ordinary entrants (no tools, no data), compared
to our base rates. The site promotes MCP/LLM integration and awards a Top Bot prize, so a
large share of the field is LLM-generated.

| Question | Crowd (4-model mean) | Truth | Bias |
|---|---|---|---|
| ATP even matchup — **lopsided set** | 32.2 | 62.7 | **−30.4** |
| ATP even matchup — tiebreak | 72.5 | 50.0 | **+22.5** |
| WTA even matchup — tiebreak | 48.8 | 27.1 | **+21.6** |
| WTA R1 mismatch — tiebreak | 34.2 | 17.9 | +16.4 |
| ATP even matchup — decider | 35.5 | 22.9 | +12.6 |
| ATP R1 mismatch — decider | 9.8 | 10.1 | −0.4 |

Two systematic biases, both from one mental error — **conflating "competitive match" with
"every set competitive"**:

1. Tiebreaks are over-priced by 10–26 points, in every scenario, by every model.
2. Lopsided sets in even matchups are under-priced by ~30 points.

`E[RBP] = (c − μ)² × 100`, so an even-matchup question is worth ~13 RBP against ~0.8 for a
mismatch question. **But** the field also sharpens as casuals drop out, so the raw 16×
concentration is an upper bound, not a forecast.

All four models correctly shaded the count comparisons to 45–48, citing ties. The tie edge
is real but smaller against LLM competitors than against casual humans.

## 6. Modelling

**published** — Kovalchik (2016) compared 11 tennis models; surface-adjusted Elo beat all of
them including random forests. Ceiling for match-winner prediction is ~70–75% accuracy /
0.196 Brier, and betting markets sit at it. The best public system (DEUCE, 42 features,
XGBoost ensemble, daily retraining) reaches 0.1950 — i.e. it *matches* the market after
enormous effort. **Conclusion: do not build a match-winner model.**

**published** — head-to-head records add nothing over calibrated Elo (Kovalchik 2016;
Ingram 2019). Most pairs have met fewer than five times, and any real signal is already in
the ratings.

**measured** — conditioning on the ranking gap removes essentially none of the dependence
between structural questions:

| Pair | Unconditional ρ | Within rank-gap quartiles |
|---|---|---|
| tiebreak / long set 1 | +0.259 | +0.259 |
| lopsided / long set 1 | −0.334 | −0.333 |
| tiebreak / lopsided | −0.178 | −0.178 |

The dependence is **mechanical**, not compositional — every event is a function of the same
scoreline. Conditional independence fails, so the joint distribution must come from
simulating the match, not from correcting independent per-question models.

**Known model bias.** The iid Markov model under-produces lopsided sets (0.488 vs 0.627
measured) and over-produces deciders, because constant per-point probabilities cannot
generate within-match form swings. Klaassen & Magnus (2001) documented the non-iid effects
directly. The simulator needs a per-set random effect before its output is used raw.

## 7. Data sources

| Source | Status | Provides |
|---|---|---|
| Polymarket `gamma-api` | ✅ free, no key | Per-match total sets, set games O/U at 3 strikes, match/set winners |
| Sackmann-schema mirrors | ✅ frozen at 2026-05-25 | aces, DF, serve points, minutes, **height**, ranks |
| Match Charting Project | ✅ live | **winners / unforced errors** — the only public source |
| `LuckyLoser91/TennisCourtLog` | ✅ to 2026-08-09 (Git LFS) | results/scores only, no serve stats |
| TennisAbstract Elo | ✅ updated 2026-08-24 | overall + surface Elo (hElo) |
| SofaScore API | ✅ free, unofficial | live results during the tournament |
| Open-Meteo | ✅ free, no key | Flushing Meadows weather, forecast + archive |
| `JeffSackmann/tennis_atp` | ❌ **404 — deleted** | — |

**Gap**: no free serve-stat data between 2026-05-25 and the tournament. Tolerable because
ace rate is the most repeatable statistic in tennis (year-over-year r ≈ 0.80–0.90).

**Polymarket → probability.** Prices *are* probabilities, so a quote is the survival
function `P(games > K)`. With digitals you take **first** differences, not
Breeden–Litzenberger second differences: from strikes 8.5/9.5/10.5 with prices p₁,p₂,p₃,
`P(exactly 9) = p₁ − p₂`. Polymarket shows the favourite–longshot bias (events priced ≤10%
resolve ~14%), so extremes need mild recalibration toward the centre.

## 8. Lessons carried over from the Probability Cup

From [`probabilitycup-jumptrading`](https://github.com/DataAthleteChamp/probabilitycup-jumptrading)
(89th/4,013, beat the crowd on 67% of 1,196 markets):

| World Cup family | Result | Tennis analogue |
|---|---|---|
| Player props | **+957 RBP, 71%** | Family B — aces, DF, winners |
| Shots on target | +230, 90% — *"too timid"* | Ace comparisons; be bolder where the model is strong |
| **Fouls** | **−125, 35%** | **Unforced errors** — same signature: subjectively scored, crowd better calibrated than any model |

The fouls leak and unforced errors share the defining property: a human official makes a
judgement call, inconsistently, and no model can recover the noise. The rule learned there
— *submit the model price, never hand-lean* — is encoded here as `DEFER_TO_BASE_RATE`.

Independently, another top-3% finisher reported that a **de-vigged sharp-market consensus
alone would have scored higher than their full system**, with the alpha layer costing −566
points net. The lesson generalises: market backbone first, alpha only where a market does
not exist. In tennis that means Family A from Polymarket, and model effort concentrated on
Family B where no sharp market exists at all.
