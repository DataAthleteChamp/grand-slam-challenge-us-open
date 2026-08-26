<h1 align="center">Grand Slam Challenge 2026 — a quant forecasting system for the US Open</h1>

<p align="center">
  <img src="https://img.shields.io/badge/event-US%20Open%202026-F4C430?style=flat-square&labelColor=0B1B33" alt="Event">
  <img src="https://img.shields.io/badge/questions-1%2C280-4FB6FF?style=flat-square&labelColor=0B1B33" alt="Questions">
  <img src="https://img.shields.io/badge/scoring-relative%20Brier-4FB6FF?style=flat-square&labelColor=0B1B33" alt="Scoring">
  <img src="https://img.shields.io/badge/python-3.11-4FB6FF?style=flat-square&labelColor=0B1B33" alt="Python">
  <img src="https://img.shields.io/badge/dry--run-by%20default-3FB950?style=flat-square&labelColor=0B1B33" alt="Dry run">
</p>

A statistical forecasting system and live API bot for the **SportsPredict Grand Slam
Challenge** — 1,280 binary probability questions across the 2026 US Open singles draws,
scored by **Relative Brier Points** against the field.

Successor to [`probabilitycup-jumptrading`](https://github.com/DataAthleteChamp/probabilitycup-jumptrading)
(89th of 4,013 in the Jump Trading Probability Cup). The API client, lane architecture and
lessons loop are ported from that run; the sport models are new.

---

## The competition in one paragraph

Forecast 1,280 binary questions across 190 seeded matches, 27 August – 13 September 2026.
Submit an integer 1–99 per question. Scoring is `RBP = (crowd_brier − your_brier) × 100`,
multiplied by 1× in rounds 1–2, 2× in round 3 and the round of 16, and 3× from the
quarter-finals on. Ranking is **cumulative** total RBP, so every unanswered question is
points left on the table. Ties are broken by performance on the final match.

## Strategy in three lines

1. **Family A (match shape)** — tiebreaks, deciding sets, lopsided sets, set length,
   duration. Take these from the market: Polymarket prices total sets and set games
   directly, for free, and *"will the first set contain 10+ games"* is literally the
   Over 9.5 price.
2. **Family B (player statistics)** — aces, double faults, winners, first-serve points won.
   **This is where the edge is.** Pinnacle refuses to price these at all, overrounds at soft
   books run 12–25%, and the equivalent family returned +957 RBP in the Probability Cup.
3. **Unforced errors** — defer to the base rate. Courtside statisticians agree on the
   forced/unforced call only 3–4 times in 10, so the classification noise swamps the signal.

## Measured base rates

Computed from ATP/WTA hard-court results 2015–2026, not taken from published summaries —
those put "share of matches with a 6-0/6-1/6-2 set" at 20–30%, when the answer is 65%.

| Scenario | Tiebreak | Deciding set | Lopsided set | Set 1 ≥10 games |
|---|---|---|---|---|
| ATP Bo5, R1 mismatch (n=138) | 0.377 | 0.101 | 0.768 | 0.406 |
| ATP Bo5, both top-15 (n=118) | 0.500 | 0.229 | 0.627 | 0.517 |
| ATP Bo5, all (n=2,882) | 0.487 | 0.202 | 0.653 | 0.515 |
| WTA Bo3, R1 mismatch (n=369) | 0.179 | 0.260 | 0.753 | 0.331 |
| WTA Bo3, both top-15 (n=609) | 0.271 | 0.366 | 0.601 | 0.420 |
| WTA Bo3, all (n=16,838) | 0.225 | 0.337 | 0.641 | 0.427 |

**The structural asymmetry**: men reach a tiebreak more than twice as often as women, but
women reach a *deciding set* far more often — reaching 2-2 in a best-of-five is much rarer
than 1-1 in a best-of-three. Applying one mental model to both draws gets one of these
backwards.

## Ties are the cheapest edge on the board

Counts are small integers, so exact ties are common. If a comparison needs a strict `>`,
two identical players are **not** at 50%:

| Statistic | Mean/player | P(tie) | Even-matchup P(A > B) |
|---|---|---|---|
| Double faults | 2.9 | **0.158** | **0.421** |
| Aces | 7.0 | 0.080 | 0.460 |
| Winners | 29.1 | 0.029 | 0.485 |
| Unforced errors | 29.0 | 0.030 | 0.485 |

Measured over 7,545 charted men's matches; the Skellam closed form predicts 0.167 for
double faults against 0.158 observed. **The settlement rule for ties must be confirmed the
moment markets open** — it moves the fair price by ~8 points either way.

## Repository map

```
bot/
├── sportspredict/   REST client, config, rate limiter, reconciliation loop
├── markets/         question taxonomy + parser (raw text -> typed MarketSpec)
├── models/
│   ├── point.py     analytic point -> game -> set -> match enumeration
│   ├── counts.py    Skellam / binomial / beta-binomial count comparisons
│   ├── shrink.py    Buhlmann credibility for low-data qualifiers
│   ├── lanes.py     submission lanes + temperature sharpening
│   └── scoring.py   Brier, Murphy decomposition, calibration gates
├── data/baserates.py   every empirically measured constant
└── tests/           94 tests
docs/                research findings behind each constant
results/             the truth ledger + lessons
scripts/             validation and analysis tooling
```

## Design decisions worth knowing

**One simulation answers every question.** All Family A questions are deterministic
functions of the same scoreline, so they come from a single enumeration and are mutually
coherent by construction. Fitting ten independent per-question models produces marginals
that can each look plausible while being jointly impossible. Conditioning on the ranking
gap removes *none* of the dependence between these events (measured: ρ(tiebreak, long first
set) = +0.259 unconditionally, +0.259 within rank-gap quartiles), which is why the
dependence must be modelled structurally rather than as a correction.

**Unknown players are shrunk, not guessed.** In rounds 1–2 one player is usually a
qualifier with almost no data. Credibility weighting (`Z = n/(n+K)`, `K ≈ 45` for ace rate)
puts a qualifier with 8 matches at 15% own-rate, 85% prior — and the prior comes from
height, which explains ~83% of the variance in men's ace rate and is known for everyone.

**Truth-telling is optimal even under relative scoring.** `E[RBP] = E[(c−y)²] − E[(p−y)²]`;
the crowd term is independent of your submission, so the optimum is your honest probability
regardless of what the crowd does. There is no crowd-guessing game worth playing — the
crowd model only guides *effort allocation*, since expected gain scales with the *square* of
the crowd's error.

**Never round to multiples of five.** The crowd clusters on 50/60/70/75/80 and the API
accepts any integer. Submitting 73 rather than 70 is free differentiation, repeated 1,280
times.

## Operational constraints

- **Lock times are unpredictable.** Only the first match on each court has a fixed start;
  the rest are "followed by", with 2–5 hours of slippage. Every match on a court must have a
  prediction in **before that court opens** (11:00 ET day, 19:00 ET night). Late refinement
  is a bonus, never a dependency — hence create-then-PATCH rather than late submission.
- **Rate limit is 60 req/min per IP**, shared across both bot lanes, so they share one
  limiter.
- **Two Connected Models per account**, each a separate leaderboard entry. Two identical
  bots are one effective entry; effective independence is `1/√((1+ρ)/2)`.

## Quick start

```bash
make install
make test          # 94 tests
make validate      # model output vs measured base rates
```

Live submission requires a real key in `bot/.env` (see `bot/.env.example`) and is
**dry-run by default**.

## Status

Pre-tournament. Markets open 27 August 2026. The parser, models and scoring layer are in
place; the pricer, Polymarket client and submission pipeline land next — see
[`docs/RESEARCH.md`](docs/RESEARCH.md) for the findings behind every constant in the code.

## Licence

MIT.
