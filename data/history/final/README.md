# Final private archive

This directory is the canonical record of the completed SportsPredict Grand Slam
Challenge 2026. It is retained in a **private repository** because the raw API
payloads contain SportsPredict questions, crowd values, identifiers, and gameplay
history that are not cleared for public redistribution.

## Files

| File | Purpose |
|---|---|
| `honest-results.json` | Frozen final `/results` response for Quant_Researcher. |
| `scenario-results.json` | Frozen final `/results` response for T-shirt Hunter. |
| `settled.csv` | Canonical 2,534-row bot history, keyed by `(lane, market_id)`. |
| `void-markets.csv` | The 28 markets observed in runs but absent from final results. |
| `manual-known.csv` | Forty locally recorded manual values; not server-verified. |
| `leaderboard-summary.json` | Winner and the owner's three final entries, without user IDs. |
| `manifest.json` | Counts, provenance coverage, limitations, and SHA-256 checksums. |

`api_rbp` is the authoritative SportsPredict score and is already stage-weighted.
`consensus_skill_rbp` is only the controllable score against the mean crowd
probability. Their difference is the field-variance contribution.

The results endpoint exposes `created_date`, which is prediction creation time.
It does not expose a trustworthy settlement timestamp, so none is invented here.

## Manual history limitation

The public leaderboard records 660 settled manual predictions, but only 40 values
were recorded locally. `manual-known.csv` adds an estimated API score to those 40
using the field-variance term recovered from the bot results. The rows remain
marked `recorded_value_not_server_verified`; they are not a substitute for an
authenticated Profile & History export.

## Offline rebuild

```bash
PYTHONPATH=. python scripts/archive_final.py --offline \
  --event-id 0cf0efd1-7e12-4ca9-8068-89eb48466a37 \
  --lobby-id b1419bb4-6dff-4172-9a42-a206b0a24ad9
python scripts/summarise_final.py
```

The offline command rebuilds every derived CSV and the manifest from the frozen
JSON snapshots, run archive, legacy ledger, overrides, and local manual file.
