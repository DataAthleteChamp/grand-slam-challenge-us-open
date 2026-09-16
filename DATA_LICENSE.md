# Data provenance and licensing

The repository is private and retained as a research archive. This document is a
conservative source inventory, not legal advice.

## License boundary

The root [MIT License](LICENSE) applies only to original source code and original
documentation. It does **not** relicense data, market text, odds, scores, API
responses, or derived databases supplied by third parties.

No public redistribution right should be inferred from a file being present in
this private repository.

## Source inventory

| Source | Repository artifacts | Conservative status | Required treatment |
|---|---|---|---|
| SportsPredict | `data/corpus/`, `data/history/`, `results/`, `runs/` | API access permits model participation, but the general terms restrict copying, scraping, databases, and permanent Service-derived content. | Keep private. Obtain written permission before publishing questions, crowd values, IDs, result payloads, or gameplay history. |
| Flashscore | `data/raw/flashscore/`, parser-derived statistics | Terms restrict scraping, aggregation, and substantial database extraction/publication. | Keep private; do not publish the parsed tournament table without permission. |
| Bovada | `data/markets/bovada/` and derived prices | Terms restrict copying, derivative storage, and inclusion in other retrieval systems. | Keep private. Generic probability/de-vigging code is original; exact snapshots are not cleared for redistribution. |
| The Odds API | `data/markets/oddsapi/` | Terms permit storage, analysis, displays, derived calculations, and ML use, but restrict standalone raw-data redistribution. | Keep raw JSON private. Derived consensus probabilities may be used subject to current terms. |
| Polymarket | Sparse final-market values and client code | Documented public API access is intended, but no broad off-chain open-data license was identified. | Keep only sparse attributed derived values in any future release; do not publish a bulk historical feed without confirmation. |
| Kalshi | Sparse final-market values and client code | The developer agreement restricts API storage/use to specified purposes and restricts third-party sharing. | Keep private; seek written authorization before reuse or publication. |
| US Open / USTA | Final-match feed URLs and derived match statistics | Site terms restrict automated extraction, copying, publication, and competitive use. | Keep current-feed extracts private and seek permission before publication. |
| Match Charting Project | Winner, unforced-error, serve, and charting-derived rates | Licensed CC BY-NC-SA 4.0. | Attribution, license link, modification notice, NonCommercial, and ShareAlike are required for covered data/adaptations. Not MIT. |
| Jeff Sackmann / tennis-sackmann archive | Historical ATP/WTA and Slam point-by-point inputs | Archive material is distributed under CC BY-NC-SA 4.0, subject to the rights its licensors can grant. | Attribute and apply the CC terms to retained derived data; commercial use needs separate review. |
| TennisMyLife | Generated player and match tables | The website and repository give conflicting MIT/noncommercial/redistribution statements and document upstream data dependencies. | Treat rights as unresolved. Keep private or rebuild from a clearly licensed source before publication. |

## Source links

Terms and licenses checked during the post-event audit:

- [SportsPredict terms](https://sportspredict.com/terms)
- [SportsPredict Probability API](https://sportspredict.com/probability/api)
- [Flashscore terms](https://www.flashscore.com/terms-of-use/)
- [Bovada terms](https://www.bovada.lv/contents/terms_of_service_bvd.pdf)
- [The Odds API terms](https://the-odds-api.com/terms-and-conditions.html)
- [Polymarket API documentation](https://docs.polymarket.com/getting-started/api)
- [Polymarket terms](https://polymarket.com/tos)
- [Kalshi developer agreement](https://kalshi-public-docs.s3.amazonaws.com/Kalshi-Developer-Agreement.pdf)
- [US Open terms](https://www.usopen.org/en_US/info/terms/index.html)
- [Match Charting Project license](https://github.com/JeffSackmann/tennis_MatchChartingProject#license)
- [Sackmann archive license](https://github.com/Aneeshers/tennis-sackmann-archive#attribution-and-license)
- [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)

Terms can change. Recheck the current source terms before any use outside this
private archive.

## If a public release is ever created

Use a fresh, code-only history:

1. retain original code under MIT;
2. replace real API responses and question corpora with synthetic fixtures;
3. exclude `data/`, `results/`, `runs/`, and source-derived passages unless each
   source is independently cleared;
4. include path-scoped CC BY-NC-SA attribution only for data that is clearly
   licensed under those terms;
5. obtain written provider permission for any raw or row-level competition
   archive.
