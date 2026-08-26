# Player data

Derived aggregates built by `scripts/build_player_data.py`. Rebuild with:

```bash
python scripts/build_player_data.py            # both tours, 2021–2026, hard court
python scripts/build_player_data.py --refresh  # re-download the raw cache
```

Raw match files are cached under `data/raw/` and are **gitignored** — the source
dataset is CC BY-NC-SA and is not ours to redistribute. Only our own derived
analysis is committed here.

## `{atp,wta}_players.csv`

One row per player, hard court, 2021–2026.

| Column | Meaning |
|---|---|
| `matches`, `svpt` | sample sizes — **always** check these before trusting a rate |
| `height`, `hand` | pooled from every row mentioning the player, then the reference file |
| `ace_rate`, `df_rate` | raw observed rates per service point |
| `ace_rate_prior` | height-derived prior (height explains ~83% of men's ace variance) |
| `ace_rate_shrunk` | credibility-weighted estimate — **this is what the models consume** |
| `first_in_rate`, `first_won_rate`, `second_won_rate` | serve breakdown |
| `spw`, `rpw` | raw service / return points won |
| `serve_skill`, `return_skill` | **opponent-adjusted** deviations from the tour baseline |
| `spw_adjusted` | baseline + `serve_skill`; expected spw against an average returner |

### Why both raw and adjusted

Raw rates are confounded by schedule. A player who spent a season facing
qualifiers looks like a better server than one who faced the top ten. The
adjusted columns come from a two-way fit over every match at once:

```
spw(i serving to j) = base + serve_i − return_j
```

fitted by alternating ridge regression, where the ridge penalty doubles as
credibility weighting so sparse players are pulled toward the baseline
automatically.

Face validity (a fit that ranks players wrongly is worthless regardless of its
residuals): the ATP serve leaders come out as Djokovic, Opelka, Isner, Zverev,
Sinner; return leaders Medvedev, Alcaraz, Sinner, De Minaur. WTA serve leaders
Rybakina, Świątek, Sabalenka, Osaka; return leaders Świątek, Kasatkina, Gauff,
Pegula. Note Djokovic tops *serve skill* on a modest 0.103 ace rate — the model
correctly separates winning service points from hitting aces.

Fitted baselines: **ATP 0.6441**, **WTA 0.5692** service points won, against
literature values of 0.645 and ~0.585.

## `{atp,wta}_head_to_head.csv`

Meetings, wins and losses per ordered pair. Kept for provenance and
opponent-strength features, **not** as a predictive feature: head-to-head records
add nothing over a calibrated rating, because most pairs have met fewer than five
times and any real signal is already in the ratings.

## Known gaps

| Gap | Detail |
|---|---|
| **WTA heights** | Present for only **36.6%** of players. Zheng, for example, has no height in *any* of her 213 rows. This is a source gap, not a join bug. `height_prior_ace_rate` falls back to the tour baseline, which is the honest answer — but it means the ace prior is weaker for unseeded women. ATP coverage is 98%. |
| **Serve stats end 2026-05-25** | The source repositories went down in May. Tolerable: ace rate is the most repeatable statistic in tennis (year-over-year r ≈ 0.80–0.90). Results-only data runs to 2026-08-09 for recency and fatigue features. |
| **Qualifiers** | Many US Open qualifiers will have few or no rows here. That is what `ace_rate_shrunk` is for: a player with 8 matches keeps roughly 15% of their own rate and takes 85% from the prior. |
