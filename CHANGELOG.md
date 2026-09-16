# Changelog

## 1.0.1 - 2026-09-16

- Constrained NumPy, SciPy, and pandas to final releases that provide CPython
  3.11 Linux wheels, matching the supported runtime and CI environment.

## 1.0.0 - 2026-09-16

Final archival release of the private 2026 Grand Slam Challenge project.

- Reconciled the final 1,267-market result for both bot lanes.
- Recorded final ranks, leaderboard scores, Smart Ratings, and round/family
  attribution.
- Added a checksummed, offline-rebuildable canonical history.
- Separated 28 deleted/replaced/void markets from settled results.
- Preserved 40 known manual values while documenting 620 unavailable rows.
- Corrected lane identity, RBP weighting, player orientation, strict count
  comparisons, round coverage, point-exposure units, and cache identity.
- Connected the validated event-specific ace/double-fault rate overlay.
- Disabled all CLI submissions after the completed event.
- Made dry-run tests and ledger output hermetic.
- Removed scratch probes, synthetic production ledgers, and stale result exports.
- Replaced pre-tournament documentation with the final result, data policy, and
  limitations.
