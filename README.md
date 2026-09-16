<h1 align="center">Grand Slam Challenge 2026</h1>

<p align="center">
  <strong>A completed quant-forecasting system and private research archive for the 2026 US Open.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-archived%20read--only-8B949E?style=flat-square&labelColor=0B1B33" alt="Archived">
  <img src="https://img.shields.io/badge/final%20rank-20%20%2F%20271-3FB950?style=flat-square&labelColor=0B1B33" alt="Final rank 20 of 271">
  <img src="https://img.shields.io/badge/settled%20forecasts-1%2C267-4FB6FF?style=flat-square&labelColor=0B1B33" alt="1,267 settled forecasts">
  <img src="https://img.shields.io/badge/tests-515-4FB6FF?style=flat-square&labelColor=0B1B33" alt="515 tests">
  <img src="https://img.shields.io/badge/python-3.11%2B-4FB6FF?style=flat-square&labelColor=0B1B33" alt="Python 3.11+">
</p>

This repository contains the forecasting engine, API bot, tournament decisions,
run artifacts, and final reconciled history used for the
**SportsPredict Grand Slam Challenge 2026**. The main honest model,
**Quant_Researcher**, finished **20th of 271 entries** and **12th of 117 bots**.

The tournament is over. The repository is intentionally kept **private**, and
the command-line submission path is permanently disabled. It is now an
offline-reproducible research record rather than a live betting or prediction
service.

> [!IMPORTANT]
> The root MIT license covers original code only. SportsPredict, bookmaker,
> tournament-feed, and CC BY-NC-SA-derived data have separate or restricted
> terms. Do not redistribute the data archives. See
> [`DATA_LICENSE.md`](DATA_LICENSE.md).

## Final result

| Entry | Forecasts | Score | Overall | Category | Smart Rating |
|---|---:|---:|---:|---:|---:|
| **Quant_Researcher** (`honest`) | 1,267 | **2,768.479** | **20 / 271** | 12 / 117 bots | 93.0 |
| T-shirt Hunter (`scenario`) | 1,267 | 1,655.959 | 39 / 271 | 29 / 117 bots | 85.9 |
| Manual entry | 660 | 1,392.675 | 43 / 271 | 12 / 154 players | 84.4 |

Winner: **ServeSim**, 5,062.839 RBP.

The event advertised 190 matches and 1,280 questions. The final server record
contains **189 matches and 1,267 settled questions per bot**. The run archive saw
1,295 market IDs; 28 were later deleted, replaced, or voided. Those numbers are
kept separate rather than forcing the event into the advertised total.

Full round and family attribution is in [`RESULTS.md`](RESULTS.md).

## What worked

The honest lane's largest positive families were:

| Family | Questions | API RBP |
|---|---:|---:|
| Tiebreaks | 190 | **+795.78** |
| Ace comparisons | 188 | **+718.93** |
| Match duration | 188 | +385.82 |
| Match winner | 189 | +363.04 |

Three ideas survived the tournament:

1. **Tie mass matters.** Small integer counts are not coin flips. Explicit
   beta-binomial tie probabilities were a real edge in ace and double-fault
   comparisons.
2. **One scoreline simulation should answer every structural question.**
   Tiebreak, deciding-set, set-length, duration, and scoreline forecasts stayed
   coherent because they came from the same simulated match.
3. **Direct markets are strongest where they are liquid.** The bookmaker
   consensus was most useful for match winner and match shape; it was not
   treated as a substitute for player-statistic modelling.

## What failed

The honest lane lost most clearly on winners (-121.00 RBP) and lopsided sets
(-55.27). The scenario lane's aggressive shifts were much worse on lopsided sets
(-361.20) and deciding sets (-342.01), and finished 19 places below the honest
lane.

The post-event audit also found implementation defects that are now either fixed
or explicitly preserved as historical limitations:

- lane identity was previously keyed only by market ID, allowing scenario
  internals to overwrite honest internals;
- API RBP was already stage-weighted but the old history script multiplied it a
  second time;
- a surname substring collision could resolve Linda Noskova as Ann Li;
- strict "fewer than" count comparisons incorrectly included ties;
- missing round metadata defaulted to `R128`, making coverage checks circular;
- winner and unforced-error rates used the wrong point exposure and sample size;
- cached count odds were not keyed by both opponents;
- final override evidence was not wired into the archived desired state;
- dry-run tests could write synthetic `m1.json` and `m2.json` files into the
  production ledger.

See [`docs/DECISION_LOG.md`](docs/DECISION_LOG.md) for the chronological research
record and [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) for what remains
unrecoverable or unsuitable for reuse.

## Scoring

For outcome \(y \in \{0,1\}\) and submitted probability \(p\):

\[
\operatorname{Brier}(p,y) = (p-y)^2
\]

SportsPredict scored against the average individual forecaster and applied a
round multiplier:

\[
\operatorname{RBP}
= m \times 100 \times
  \left(\operatorname{mean}_i (p_i-y)^2 - (p-y)^2\right)
\]

The API's `rbp` field is **already multiplied** by \(m\). The mean crowd
probability alone recovers only the controllable consensus-skill term:

\[
m \times 100 \times
  \left((\bar p-y)^2 - (p-y)^2\right)
\]

The difference is the field-variance contribution. The canonical archive stores
all three values separately so local analysis does not claim an approximate
score is the official one.

## Architecture

```text
bot/
|-- sportspredict/       REST client, configuration, rate limiting, reconciliation
|-- markets/             question taxonomy and raw-text parser
|-- models/
|   |-- point.py         point -> game -> set -> match probabilities
|   |-- simulate.py      shared deterministic scoreline simulation
|   |-- counts.py        binomial/beta-binomial comparisons with tie mass
|   |-- shrink.py        empirical-Bayes credibility shrinkage
|   |-- lanes.py         honest and scenario transformations
|   `-- scoring.py       Brier and Murphy decomposition
|-- data/                ingestion, ratings, base rates, name resolution
|-- pricer.py            model/market composition and final probability routing
|-- ledger.py            lane-safe legacy model ledger
`-- tests/               515 regression and model tests

data/history/final/      canonical private results and checksums
docs/                    research, decisions, lessons, and limitations
results/matches/         legacy model-intention ledger (not authoritative)
runs/                    timestamped model-intention snapshots
scripts/                 data builds, evaluation, archival, and reporting
```

### Model flow

```text
SportsPredict market text
          |
          v
typed MarketSpec + resolved player identities
          |
          +--> point/game/set simulation --> structural probabilities
          |
          +--> player rates + exposure --> count comparisons and thresholds
          |
          +--> permitted market evidence --> de-vigged external probability
          |
          v
confidence/calibration/lane transform
          |
          v
integer probability 1-99 + provenance
```

## Canonical history

`data/history/final/settled.csv` is the authoritative bot history:

- 2,534 rows: 1,267 per lane;
- identity is `(lane, market_id)`;
- all 189 final match IDs are represented;
- rounds reconstruct to 635 rows at 1x, 480 at 2x, and 152 at 3x per lane;
- API Brier and RBP are preserved directly;
- model provenance is classified as exact run match, documented override, or
  unmatched submission;
- 28 removed markets live separately in `void-markets.csv`;
- every frozen artifact has a SHA-256 hash in `manifest.json`.

The public leaderboard reports 660 manual forecasts, but only 40 values were
recorded locally. `manual-known.csv` keeps those values and labels their scores
as estimates. No missing manual predictions are invented.

## Reproduce the finished report

Install the project:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Run it from a source checkout. The wheel intentionally excludes private
tournament/player data and is useful only for importing the original code.

Run maintained checks:

```bash
make lint
make test
make validate
```

Rebuild every derived final-history file without accessing SportsPredict:

```bash
PYTHONPATH=. python scripts/archive_final.py --offline \
  --event-id 0cf0efd1-7e12-4ca9-8068-89eb48466a37 \
  --lobby-id b1419bb4-6dff-4172-9a42-a206b0a24ad9
python scripts/summarise_final.py
```

Analyze either lane:

```bash
python scripts/analyse_settled.py --lane honest
python scripts/analyse_settled.py --lane scenario
```

The ignored local point archive can reproduce the tour-specific points-per-game
fit:

```bash
python scripts/fit_points_per_game.py
```

## Safety and archival status

- `python -m bot` remains dry-run by default.
- `--submit` now returns an error because this event is archived.
- Dry runs do not write the legacy ledger unless `--record-ledger` is explicit.
- Unknown rounds, non-exact market coverage, blank market IDs, and duplicate
  market IDs block validation.
- Secrets belong only in ignored `bot/.env`; the repository and reachable Git
  history were scanned without finding a committed credential.
- The GitHub repository remains private.

## Data and licensing

Original source code is licensed under the [MIT License](LICENSE).

Data is not covered by that license. The private archive includes material
derived from SportsPredict, Flashscore, Bovada, The Odds API, Polymarket, Kalshi,
official US Open feeds, TennisMyLife, Jeff Sackmann datasets, and the Match
Charting Project. Their terms range from restricted redistribution to
CC BY-NC-SA 4.0.

The source-by-source policy and provenance are documented in
[`DATA_LICENSE.md`](DATA_LICENSE.md). Nothing in this repository should be
treated as permission to redistribute a provider's data.

## Project lineage

This system follows
[`probabilitycup-jumptrading`](https://github.com/DataAthleteChamp/probabilitycup-jumptrading),
which finished 89th of 4,013 in the Jump Trading Probability Cup. The REST
client, lane concept, results loop, and operational lessons came from that
project; the tennis models were built for this event.

## Final state

Development ended after the post-tournament reconciliation. The repository is
kept private as an immutable research record; future tennis work should start in
a new project rather than reopening this event-specific submission system.
