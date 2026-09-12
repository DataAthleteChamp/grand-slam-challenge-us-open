"""Attach bookmaker consensus to the matches we are about to price.

The pricer already knows how to blend a market quote into the match-winner
answer, and weights it at 0.85 because that family is the one place a liquid,
efficient market exists.  Nothing was passing it one.  All 64 Round 1
match-winner questions were priced from simulation alone, each carrying the note
"no market quote; this is the family that most needs one", while a de-vigged
consensus across up to 40 books sat one HTTP call away.

That is not a small miss.  The closing line has a measured Brier near 0.203, and
in the predecessor event a de-vigged consensus with no model on top would have
scored +3,924 relative points against the entrant's actual +3,419 - their alpha
layer was worth *negative* 566.

Joining is by surname because the two sources spell players differently: the
competition says "M. Giron", the books say "Marcos Giron", and the competition's
own match name may carry diacritics its questions drop.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from bot.data.names import strip_accents
from bot.data.oddsapi import US_OPEN_SPORTS, OddsClient, OddsError, match_winner

#: Below this many books a quote is one opinion rather than a consensus.
MIN_BOOKS = 8

#: Above this spread the books themselves disagree enough that the median is not
#: a reliable summary, and we should lean on our own model instead.
MAX_SPREAD = 0.12

#: Raw Odds API responses, kept so a quote outlives the request that fetched it.
SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "markets" / "oddsapi"

#: How stale a cached snapshot may be before it is discarded.  Tennis
#: match-winner lines move slowly outside of injury news, and the alternative is
#: not a fresher quote but *no quote at all*, which measured 271.7 skill points
#: worse across 107 settled questions.  A day-old consensus over dozens of books
#: still beats our own model comfortably.
MAX_SNAPSHOT_AGE_SECONDS = 36 * 3600


def surname_key(name: str) -> str:
    """A join key that survives 'M. Giron', 'Marcos Giron' and 'M. Čilić'."""
    cleaned = strip_accents(str(name)).lower().replace(".", " ").replace("-", " ")
    parts = [p for p in cleaned.split() if len(p) > 1]
    return parts[-1] if parts else cleaned.strip()


@dataclass
class MarketBook:
    """De-vigged match-winner consensus, joinable by surname."""

    quotes: dict[tuple[str, str], float] = field(default_factory=dict)
    books: dict[tuple[str, str], int] = field(default_factory=dict)
    spreads: dict[tuple[str, str], float] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    @classmethod
    def fetch(cls, client: OddsClient | None = None) -> MarketBook:
        """Read both draws.  Never raises: no market is worse than no bot."""
        book = cls()
        try:
            client = client or OddsClient.from_env()
        except OddsError as exc:
            book.skipped.append(f"no odds client: {exc}")
            book.absorb_snapshots()
            return book

        for sport in US_OPEN_SPORTS:
            try:
                events = client.odds(sport, markets="h2h", regions="eu,uk")
            except OddsError as exc:
                book.skipped.append(f"{sport}: {exc}")
                continue
            book.save_snapshot(sport, events)
            book.absorb(events)

        # The free quota runs out exactly when it matters most, at the end of the
        # tournament when every remaining question scores at 3x.  A quote that
        # was fetched hours ago is still a de-vigged consensus over dozens of
        # books; throwing it away and pricing the final from the model alone is
        # the expensive option, not the safe one.
        if book.skipped:
            book.absorb_snapshots()
        return book

    def save_snapshot(self, sport: str, events: list[dict]) -> None:
        """Persist a successful pull so the quote outlives its request."""
        if not events:
            return
        try:
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            path = SNAPSHOT_DIR / f"{sport}-{stamp}.json"
            path.write_text(json.dumps(events), encoding="utf-8")
        except OSError:
            pass

    def absorb_snapshots(self) -> None:
        """Fill any gaps from the newest cached snapshot on disk."""
        if not SNAPSHOT_DIR.exists():
            return
        now = time.time()
        files = sorted(SNAPSHOT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for path in files:
            age = now - path.stat().st_mtime
            if age > MAX_SNAPSHOT_AGE_SECONDS:
                continue
            try:
                events = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(events, list):
                # Newest last, so later files overwrite earlier ones.
                self.absorb(events)
                self.skipped.append(f"used cached quotes from {path.name} ({age / 3600:.1f}h old)")

    def absorb(self, events: list[dict]) -> None:
        for event in events:
            agreed = match_winner(event)
            if len(agreed) != 2:
                continue
            names = list(agreed)
            for name in names:
                other = next(n for n in names if n != name)
                consensus = agreed[name]
                key = (surname_key(name), surname_key(other))
                self.quotes[key] = consensus.probability
                self.books[key] = consensus.n_books
                self.spreads[key] = consensus.spread

    def probability(self, subject: str, opponent: str) -> float | None:
        """``P(subject beats opponent)``, or None when there is no usable quote.

        A thin or wildly disagreeing book is treated as no quote at all.  Half a
        market is not half as useful as a full one; it is a number with unknown
        provenance dressed up as a consensus.
        """
        key = (surname_key(subject), surname_key(opponent))
        value = self.quotes.get(key)
        if value is None:
            return None
        if self.books.get(key, 0) < MIN_BOOKS:
            return None
        if self.spreads.get(key, 1.0) > MAX_SPREAD:
            return None
        return value

    def ladders_for(self, player_a: str, player_b: str) -> dict[str, dict[str, float]]:
        """The ``ladders`` payload the pricer expects, keyed by roster name.

        Both directions are supplied so the answer does not depend on which
        player the question happens to name.
        """
        forward = self.probability(player_a, player_b)
        backward = self.probability(player_b, player_a)
        if forward is None and backward is None:
            return {}
        quotes: dict[str, float] = {}
        if forward is not None:
            quotes[player_a] = forward
            quotes.setdefault(player_b, 1.0 - forward)
        if backward is not None:
            quotes[player_b] = backward
            quotes.setdefault(player_a, 1.0 - backward)
        return {"match_winner": quotes}

    def __len__(self) -> int:
        return len(self.quotes)
