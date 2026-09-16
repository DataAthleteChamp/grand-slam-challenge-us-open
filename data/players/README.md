# Player data

> **Private archived data.** The root MIT license does not cover these tables.
> See [`../../DATA_LICENSE.md`](../../DATA_LICENSE.md) before any reuse or
> redistribution.

Derived aggregates built by `scripts/build_player_data.py`. Rebuild with:

```bash
python scripts/build_player_data.py            # both tours, 2021–2026, hard court
python scripts/build_player_data.py --refresh  # re-download the raw cache
```

Raw match files are cached under `data/raw/` and are gitignored. The derived
tables combine sources with CC BY-NC-SA, restricted, or unresolved rights and
therefore remain private.

## `{atp,wta}_players.csv`

One row per player, hard court, 2021–2026.

| Column | Meaning |
|---|---|
| `matches`, `svpt` | sample sizes — **always** check these before trusting a rate |
| `height`, `hand` | pooled from every row mentioning the player, then the reference file |
| `ace_rate`, `df_rate` | raw observed rates per service point |
| `ace_rate_prior` | height-derived prior (about 48% of ATP and 29% of WTA variance explained) |
| `ace_rate_shrunk` | credibility-weighted estimate — **this is what the models consume** |
| `first_in_rate`, `first_won_rate`, `second_won_rate` | serve breakdown |
| `spw`, `rpw` | raw service / return points won |
| `serve_skill`, `return_skill` | **opponent-adjusted** deviations from the tour baseline |
| `spw_adjusted` | baseline + `serve_skill`; expected spw against an average returner |
| `charted_matches` | Match Charting sample size — check before trusting the two below |
| `winner_rate`, `unforced_rate` | per point played, from the Match Charting Project |
| `pace_minutes` | duration residual: minutes above/below what the scoreline implies |

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

Fitted baselines: **ATP 0.6321**, **WTA 0.5691** service points won, against
literature values of 0.645 and ~0.585.

`Roster.load()` overlays the validated event-specific ace/double-fault blend
from `us_open_2026_draw.csv`; the base player tables remain the reproducible
career aggregates.

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


## Pace of play

`pace_minutes` is the part of match duration that the scoreline does *not*
explain. Total games already accounts for about 84% of the variance in minutes
(r = 0.91 ATP, 0.92 WTA), and this column captures the rest.

It is a strongly repeatable trait, not noise: between-player variance in the
residual is roughly **14× what label-shuffling produces (p = 0.0005)** over
14,325 ATP hard-court player-matches, with a **40-minute spread** end to end.

| Slowest | | Fastest | |
|---|---|---|---|
| Andy Murray | +17.8 | Mpetshi Perricard | −15.5 |
| Rafael Nadal | +14.9 | Nick Kyrgios | −12.8 |
| Etcheverry | +11.9 | Reilly Opelka | −12.2 |
| Schwartzman | +10.5 | Sam Querrey | −11.4 |

The data independently recovers Nadal's and Murray's well-known between-point
routines, and the fastest players are all big servers — aces end points
instantly. On the women's side the slowest are Sorribes Tormo and Siegemund,
both grinders.

This is the same permutation test with leave-one-out player controls that showed
**umpire identity (p = 0.16) and court assignment (p = 0.21) were noise**. The
method discriminates; pace is real and large, those were not.

## Winners and unforced errors

From the Match Charting Project — the only public source. Face validity holds:
most aggressive are Opelka, Karlović, Kyrgios, Raonić; most error-prone are
Paire, Ruusuvuori, Davidovich Fokina, Shapovalov.

Treat `unforced_rate` with suspicion. The forced/unforced boundary is a human
judgement call, and shown ten identical points three separate coaching
conferences agreed only three or four times out of ten. Counts are internally
consistent within a match but not comparable across matches, courts or charters.
The model therefore uses charted-match credibility shrinkage, match-length
exposure, and a 50% pull toward the base rate.

Coverage: 378 ATP and 377 WTA players matched to the main table.
