# Final limitations

This document separates the verified tournament result from information that
cannot be recovered or should not be treated as production-ready.

## Historical record

- The final server result is complete for both bots: 1,267 settled markets per
  lane across 189 matches.
- The manual entry has 660 settled predictions on the leaderboard, but only 40
  values were recorded locally. The missing 620 predictions require an
  authenticated Profile & History export.
- The API does not expose a trustworthy settlement timestamp. Its
  `created_date` field is prediction creation time.
- Run files were written around pricing cycles, not as immutable submission
  receipts. They omit the server response, reconciliation state, Git SHA,
  dependency versions, and input hashes.
- Twenty-nine final bot submissions do not appear in a matching run. They remain
  classified as `unmatched_submission`.
- No single commit can be claimed as the exact model that produced every final
  server value. Post-lock fixes and manual overrides span multiple worktree
  states.
- Twenty-eight observed markets were deleted, replaced, or voided. The available
  API does not distinguish those reasons precisely.

## Historical model limitations

The final submitted forecasts were produced before the post-event corrections.
The canonical archive preserves the actual server values; it does not rewrite
history with corrected outputs.

- Winner and unforced-error rates were measured per all match points, but the
  tournament-era pricer used service-point or fixed exposure and total career
  match counts. The archived code now uses total-point exposure and
  `charted_matches`.
- Final total-points forecasts used 6.6 points per game. A chronological fit on
  official US Open point data gives 6.330 for ATP and 6.630 for WTA, with 2024
  held out. The code now uses the tour-specific values, but submitted history is
  unchanged.
- Some WTA source blocks contained implausible/inverted serve statistics. The
  validated Match Charting ace/double-fault blend is now loaded at runtime, but
  the underlying third-party source defect remains in historical build inputs.
- Lopsided-set forecasts had poor ranking performance and were shrunk after
  settlement evidence arrived. That family remained a loss.
- Match-winner market reconciliation searched at lower simulation resolution
  than the final pricing run, so the achieved probability could differ from the
  target.
- Final-set-tiebreak semantics are correct, but some fallback calibration was
  anchored to earlier market types rather than dedicated final-only history.
- Polymarket/Kalshi structural ladder readers were not fully wired into the
  production market book. The live backbone was narrower than early
  documentation claimed.

## Reproducibility boundary

The final API result and generated report are reproducible from
`data/history/final/`. The exact pre-submission modelling environment is not:

- dependencies were lower-bounded rather than locked during the event;
- many raw data files were local and ignored;
- external APIs and bookmaker feeds were mutable;
- run files did not record the code or data hashes.

The repository now records these gaps rather than presenting an approximate
replay as exact.

## Operational status

- The CLI is archived read-only.
- `--submit` is rejected.
- Dry-run ledger writes require explicit `--record-ledger`.
- This repository should not be reused for a future event without a new project,
  fresh source review, chronological model validation, and explicit event/lobby
  safety fences.
