# Legacy model ledger

`results/matches/` is the tournament-era model ledger. It is retained as private
provenance, but it is **not** the authoritative submission or scoring record:

- older runs keyed forecasts only by market ID, so one lane could overwrite the
  other;
- duplicate and replaced match IDs remain as historical evidence;
- many rows were never settled locally;
- the stored round labels are unreliable;
- run-time probabilities are model intentions, not server submission receipts.

Use `data/history/final/settled.csv` for final submissions, outcomes, Brier
scores, API RBP, round multipliers, and lane identity. See `RESULTS.md` for the
reconciled tournament report.
