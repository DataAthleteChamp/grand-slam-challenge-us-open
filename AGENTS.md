# AGENTS.md — operating guide for this repo

A quant forecasting system + live API bot for the **SportsPredict Grand Slam Challenge 2026**
(Brier-scored probability forecasting over the US Open singles draws, 27 Aug – 13 Sep 2026).

Predecessor system: [`probabilitycup-jumptrading`](https://github.com/DataAthleteChamp/probabilitycup-jumptrading)
— 89th of 4,013 in the Jump Trading Probability Cup. The API client, lane architecture, results
ledger and lessons loop are ported from there; the sport models are new.

## Map

- `bot/sportspredict/` — REST client, config, rate limiter, reconciliation loop.
- `bot/markets/` — question taxonomy + parser (raw market text → typed `MarketSpec`).
- `bot/models/` — `point.py` (analytic point→game→set→match), `counts.py` (Skellam /
  beta-binomial), `shrink.py` (credibility), `lanes.py` (submission lanes), `scoring.py`
  (Brier / Murphy / calibration gates).
- `bot/data/` — `baserates.py` holds every empirically measured base rate.
- `results/` — the truth ledger: per-match forecast vs crowd vs outcome, plus lessons.
- `docs/` — the research findings behind every constant in the code.

## Golden rules

- **Report honest probabilities.** Brier is a proper scoring rule, and under *relative*
  scoring truth-telling is still optimal: `E[RBP] = E[(c−y)²] − E[(p−y)²]`, and the crowd
  term does not depend on your submission. Never guess the crowd and deviate.
- **Dry-run first.** Never submit without an explicit `--submit` and the user's go-ahead.
- **Never commit secrets.** Keys live in `bot/.env` (gitignored) or an Actions secret.
- **Never round to multiples of five.** The crowd clusters on 50/60/70/75/80; the API takes
  any integer 1–99. Submitting 73 instead of 70 is free differentiation.
- **Answer every question.** Ranking is cumulative total RBP, so a skipped question with a
  small edge is points left on the table.
- **Defer on unforced errors.** Courtside statisticians agree on the forced/unforced call
  only 3–4 times in 10. This is the tennis analogue of the fouls market that cost −125 RBP
  in the Probability Cup. Submit near the base rate; never hand-lean.

## Hard-won constraints

- **Lock times are unpredictable.** Only the first match on each court has a fixed start;
  the rest are "followed by", with 2–5h of slippage. Have a prediction in for every match
  on a court *before that court opens* (11:00 ET day, 19:00 ET night). Late refreshes are a
  bonus, never a dependency.
- **Score strings come in two formats.** Older files use `7-6(6)`, newer ones bare `7-6`.
  A parser handling only one silently drops every tiebreak match from half the sample.
- **The tie settlement rule is unknown until markets open.** It moves the fair price of an
  even double-fault comparison by ~8 points. Confirm it first thing on 27 August.

## Commands

```bash
make test        # run the test suite
make lint        # ruff
make validate    # model sanity checks against measured base rates
```

## Where the edge is

1. **Family B (aces / double faults / winners / first-serve %)** — Pinnacle refuses to price
   these at all, so there is no sharp market to fight. This maps onto the player-prop family
   that returned +957 RBP in the Probability Cup.
2. **Tie probabilities** — small integer counts make ties common (15.8% for double faults),
   so an even matchup is 42%, not 50%.
3. **Family A (match shape)** — take it from Polymarket, which prices total sets and set
   games directly. Do not rebuild what a liquid market already prices well.
