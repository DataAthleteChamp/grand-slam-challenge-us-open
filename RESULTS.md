# Final results - SportsPredict Grand Slam Challenge 2026

The tournament is complete. This repository is retained as a private research
archive; the command-line submission path is disabled.

## Result

| Entry | Forecasts | Score | Overall | Category | Smart Rating |
|---|---:|---:|---:|---:|---:|
| Quant_Researcher | 1,267 | 2,768.479 | 20 / 271 | 12 / 117 bots | 93.0 |
| T-shirt Hunter | 1,267 | 1,655.959 | 39 / 271 | 29 / 117 bots | 85.9 |
| manual | 660 | 1,392.675 | 43 / 271 | 12 / 154 players | 84.4 |

Winner: **ServeSim**, 5,062.839 RBP.

Quant_Researcher is the honest lane. T-shirt Hunter is the scenario lane;
the unlabeled manual entry is separate.

## Event accounting

| Measure | Matches | Markets |
|---|---:|---:|
| Advertised | 190 | 1,280 |
| Final settled API | 189 | 1,267 |
| Observed in run archives | - | 1,295 |
| Deleted, replaced, or void | - | 28 |

The advertised, observed, and settled counts are deliberately not collapsed.
Both bots answered every market that remained scoreable.

## Score by round

| Round | Honest questions | Honest RBP | Scenario questions | Scenario RBP |
|---|---:|---:|---:|---:|
| R128 | 635 | +1,104.68 | 635 | +1,104.05 |
| R32 | 320 | +494.97 | 320 | +13.18 |
| R16 | 160 | +298.74 | 160 | +105.96 |
| QF | 72 | +61.78 | 72 | -349.40 |
| SF | 40 | +471.24 | 40 | +542.22 |
| F | 40 | +337.03 | 40 | +239.92 |

API RBP is already stage-weighted. The sum of rounded result rows is
2,768.44 for honest and 1,655.93 for scenario; the
leaderboard retains additional internal precision.

## Performance by market family

### Quant_Researcher

| Family | N | API RBP | Consensus skill | Mean Brier |
|---|---:|---:|---:|---:|
| `tiebreak` | 190 | +795.78 | +188.22 | 0.1632 |
| `aces_comparison` | 188 | +718.93 | +96.95 | 0.1785 |
| `match_duration` | 188 | +385.82 | -103.25 | 0.2416 |
| `match_winner` | 189 | +363.04 | -111.64 | 0.1560 |
| `first_serve_comparison` | 63 | +245.51 | -218.87 | 0.2520 |
| `unforced_errors` | 63 | +218.93 | -82.41 | 0.2370 |
| `double_faults_comparison` | 188 | +99.88 | -343.63 | 0.2440 |
| `deciding_set` | 61 | +67.20 | -167.89 | 0.2322 |
| `distance_covered` | 4 | +66.16 | +4.23 | 0.1994 |
| `total_points` | 4 | +22.60 | -33.00 | 0.1975 |
| `both_win_set` | 2 | -4.17 | -24.30 | 0.2405 |
| `exact_scoreline` | 2 | -10.98 | -44.70 | 0.3490 |
| `sets_in_a_row` | 2 | -23.99 | -52.08 | 0.3293 |
| `lopsided_set` | 62 | -55.27 | -319.07 | 0.2953 |
| `winners_comparison` | 61 | -121.00 | -379.79 | 0.2380 |

### T-shirt Hunter

| Family | N | API RBP | Consensus skill | Mean Brier |
|---|---:|---:|---:|---:|
| `aces_comparison` | 188 | +908.82 | +286.84 | 0.1708 |
| `tiebreak` | 190 | +706.99 | +99.43 | 0.1666 |
| `match_winner` | 189 | +445.97 | -28.71 | 0.1515 |
| `unforced_errors` | 63 | +218.93 | -82.41 | 0.2370 |
| `first_serve_comparison` | 63 | +186.39 | -277.99 | 0.2560 |
| `match_duration` | 188 | +177.01 | -312.06 | 0.2468 |
| `distance_covered` | 4 | +66.16 | +4.23 | 0.1994 |
| `total_points` | 4 | +12.22 | -43.38 | 0.2061 |
| `both_win_set` | 2 | -44.97 | -65.10 | 0.3085 |
| `exact_scoreline` | 2 | -52.26 | -85.98 | 0.4178 |
| `sets_in_a_row` | 2 | -69.53 | -97.62 | 0.4052 |
| `double_faults_comparison` | 188 | -71.78 | -515.29 | 0.2523 |
| `winners_comparison` | 61 | -124.81 | -383.60 | 0.2395 |
| `deciding_set` | 61 | -342.01 | -577.10 | 0.2591 |
| `lopsided_set` | 62 | -361.20 | -625.01 | 0.3197 |

## Main lessons

- Honest lane's strongest family was `tiebreak` at +795.78 RBP.
- Honest lane's weakest family was `winners_comparison` at -121.00 RBP.
- Scenario lane's largest loss was `lopsided_set` at -361.20 RBP.
- Tiebreak and ace questions contained the most durable edge. Winner and
  lopsided-set models were the clearest areas where ranking or calibration
  was not good enough.
- Scenario shifts increased variance without improving the final result;
  the honest lane finished 19 places higher.

## Archive integrity

- Canonical bot history: **2,534 rows**, keyed by lane and market ID.
- Exact run match: 2,489 rows.
- Documented final override: 16 rows.
- Final submission absent from runs: 29 rows.
- Manual history: 40 locally recorded values
  out of 660 settled predictions.

Run files are model intentions, not submission receipts. They omit server
responses and exact model-version hashes, so unmatched rows are left unknown
rather than assigned to an invented commit. `created_date` from the results
API is prediction creation time, not settlement time.

## Reproduction

The final derived archive can be rebuilt without network access:

```bash
PYTHONPATH=. python scripts/archive_final.py --offline \
  --event-id 0cf0efd1-7e12-4ca9-8068-89eb48466a37 \
  --lobby-id b1419bb4-6dff-4172-9a42-a206b0a24ad9
python scripts/summarise_final.py
```

Checksums for the frozen inputs and derived files are in
`data/history/final/manifest.json`.
