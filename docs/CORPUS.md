# The live question corpus

What the competition actually asks, captured from the API rather than assumed.
Everything here is observed, not inferred. Re-capture each round with:

```bash
PYTHONPATH=. .venv/bin/python scripts/capture_corpus.py
```

Archives land in `data/corpus/` and are committed, because the wording is the
specification this bot is written against and a later round changing it is
something we need to be able to diff.

## Capture log

| When | Event status | Matches | Markets | Note |
|---|---|---|---|---|
| 2026-08-27 18:02Z | `upcoming` | 0 | 0 | Entry open, nothing seeded |
| 2026-08-29 15:50Z | `active` | 64 | 320 | Round 1 seeded |

## Structure (Round 1)

Exactly **five questions per match**, the same five every time:

| Question | Family | Varies per match |
|---|---|---|
| `Will the match last longer than {N} minutes?` | duration | **yes** — N from 70 to 170 in steps of 5 |
| `Will {X} record more aces than {Y}?` | aces | subject side varies |
| `Will {X} record more double faults than {Y}?` | double faults | subject side varies |
| `Will {X} win?` | **match winner** | subject side varies |
| `Will the match feature at least {K} tiebreak(s)?` | tiebreak | **yes** — K is 1 (41x) or 2 (23x) |

64 matches x 5 = 320 markets. `multiplier` is 1 on every Round 1 match.

Markets open `2026-08-30T15:00Z` and close `2026-08-31T03:00Z` for 62 matches;
2 matches run a day later. That is a **twelve-hour window**, not an instant, so
there is time to price, review and revise before close.

### Match payload

```json
{
  "id": "f8779f1f-...", "name": "M. Giron vs I. Buse",
  "multiplier": 1, "open_market_count": 5, "settled_market_count": 0,
  "opening_time": "2026-08-30T15:00:00.000Z",
  "closing_time": "2026-08-31T03:00:00.000Z"
}
```

`multiplier` is given directly, so the round never has to be inferred from a
name. Players are only available as `name` split on `" vs "`, in
`"F. Surname"` form.

## What this overturns

**There is a match-winner question, and we had no model for it.** `Will {X}
win?` is 20% of every round and did not parse at all, so all 64 would have been
answered 50 from the base rate. It is also the one question the bookmakers price
directly and best — de-vigged consensus across 21 books, spread about 2 points.

**Tiebreak questions carry a count.** `at least 1` and `at least 2` are
different questions, and 23 of 64 asked for 2. Our measured base rate of 0.487
is for *at least one*. Answering both with it would have been badly wrong on a
third of the tiebreak markets.

**Duration is a per-match line, not a fixed threshold.** Thresholds span 70 to
170 minutes and are clearly tailored to each match, so they sit near the middle
of the distribution where the answer is most sensitive to the model and least
anchored by any base rate.

**Orientation is mixed, not positional.** The subject is the second-named player
74 times and the first-named player 52 times for comparisons, and 37/26 for the
winner question. Any assumption that the question is "about player B" would
invert roughly 40% of the highest-edge family. The subject must be parsed from
the text every time.

**Accents differ between fields.** The match is named `M. Čilić vs A. Rublev`
while its own questions say `M. Cilic`. Matching the question subject against
the match name must fold accents or it silently fails.

## Name resolution

119 of 128 Round 1 players resolve against our ratings (93%). The nine failures
are two distinct problems.

**Duplicate spellings of one player** (7 groups repo-wide) make a name look
ambiguous and get it dropped from the crosswalk:

| Spellings | Matches each |
|---|---|
| `Alex de Minaur` / `Alex De Minaur` | 249 / 1 |
| `Botic van de Zandschulp` / `Botic Van De Zandschulp` | 198 / 1 |
| `McCartney Kessler` / `Mccartney Kessler` | **41 / 29** |
| `Xinyu Wang` / `Xin Yu Wang` | 156 / 1 |

This is worse than a lookup failure. The match history is *split across both
spellings*, so the ratings themselves are computed from fewer matches and
shrunk harder toward the mean. Kessler is rated on 41 matches instead of 70.
`scripts/build_player_data.py` merges on the raw string rather than through the
crosswalk, which is the cause.

**Genuine collisions between different players** cannot be fixed by spelling:

| Query | Candidates |
|---|---|
| `B. Nakashima` | Brandon / Bryce |
| `D. Blanch` | Darwin / Dali |
| `L. Fernandez` | Leylah / Lya |
| `S. Sierra` | Solana / Salome |
| `A. Krueger` | Ashlyn / Adelina |

First initial plus surname is not enough. The tie-break has to be recent match
count and tour: a main-draw player at a slam has a tour record, an obscure
namesake does not. `T. Frodin` is absent from our data entirely.

## Priorities this implies

1. **Match-winner family** — 20% of questions, currently unpriced, and the one
   place bookmaker consensus is strongest. Highest value by a wide margin.
2. **Tiebreak counts** — a third of tiebreak questions ask for two.
3. **Duration thresholds** — read the per-match line, not a constant.
4. **Name resolution** — canonicalise on merge to recover split ratings, and
   disambiguate genuine collisions by match count and tour.
5. **Re-capture every round** — the question mix changes with the multiplier
   schedule (5 in R128 rising to 20 in the final), so later rounds will contain
   families not present here.
