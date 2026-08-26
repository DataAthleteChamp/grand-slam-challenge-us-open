"""Prediction market clients and probability extraction.

Two venues are worth reading, and only one of them matters much.

**Polymarket** is essential.  Free, no key, and the only venue that prices tennis
*per match* rather than just tournament outrights.  A single US Open match carries
around 17 sub-markets: match winner, set winners, Total Sets O/U 2.5, Set 1 and
Set 2 Games O/U at 8.5/9.5/10.5, Match Total Games O/U at 21.5/22.5/23.5, plus
set handicap and game spread.  Verified $11M+ volume on the men's event.

**Kalshi** is a cross-check.  Free reads, no key.  It runs tournament outrights
(``KXATP-26USO``) and has a per-match series (``KXATPGAME``) that is empty before
the draw and populates closer to play.  Worth polling, not worth depending on.

Everything else was checked and rejected: Manifold is play money, Betfair and
Smarkets need accounts, and the DeFi venues have negligible liquidity.

The important structural fact: **no venue prices aces, double faults, winners or
unforced errors.**  Those exist only as soft sportsbook props with no order book
and no public API.  That is precisely why those questions are where the edge is —
there is no sharp price to fight.

Prices here are probabilities already, so a quote on "Over 9.5 games" *is*
``P(games > 9.5)``.  With several strikes on the same quantity the quotes trace
the survival function directly, and differencing adjacent strikes gives the
probability mass between them.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
KALSHI_URL = "https://api.elections.kalshi.com/trade-api/v2"

USER_AGENT = "grand-slam-challenge-bot/0.1 (markets)"

#: Below this price the tick size swamps the signal — a one-cent spread on a
#: two-cent mid means the fair value could be anywhere from 1c to 3c.
MIN_TRUSTWORTHY_PRICE = 0.03

#: Power-law debiasing exponent.  Prediction markets show the same
#: favourite-longshot bias as bookmakers but milder: longshots are overpriced and
#: heavy favourites slightly underpriced.  1.1 suits liquid markets; thin ones
#: need more.
DEBIAS_EXPONENT = 1.1


class MarketError(Exception):
    """Raised when a market venue cannot be read."""


def _get(url: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> Any:
    """GET and parse JSON, raising :class:`MarketError` on failure."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise MarketError(f"failed to read {url}: {exc}") from exc


# --------------------------------------------------------------------- quality


@dataclass(frozen=True)
class Quote:
    """One market quote with the metadata needed to judge whether to trust it."""

    venue: str
    market_id: str
    question: str
    best_bid: float | None
    best_ask: float | None
    last_trade: float | None
    volume: float
    accepting_orders: bool
    closed: bool
    captured_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def mid(self) -> float | None:
        """Mid-price, or ``None`` when the book is one-sided."""
        if self.best_bid is None or self.best_ask is None:
            return self.best_ask if self.best_bid is None else self.best_bid
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def is_crossed(self) -> bool:
        """A bid at or above the ask means a stale or broken snapshot."""
        if self.best_bid is None or self.best_ask is None:
            return False
        return self.best_bid >= self.best_ask

    @property
    def is_stale(self) -> bool:
        """Whether the quote should be distrusted.

        The load-bearing case: a settled market can keep a last-trade price from
        days earlier.  Sinner's last trade read 0.730 while his best ask was
        0.001, because he was already out.  Anything keying off last-trade would
        have priced him a co-favourite.
        """
        if self.closed or not self.accepting_orders:
            return True
        if self.is_crossed:
            return True
        mid, spread = self.mid, self.spread
        if mid is None:
            return True
        if self.last_trade is not None and spread is not None and spread > 0:
            if abs(self.last_trade - mid) > 5 * spread:
                return True
        return False

    @property
    def is_trustworthy(self) -> bool:
        """Usable as a probability estimate."""
        mid = self.mid
        if mid is None or self.is_stale:
            return False
        return MIN_TRUSTWORTHY_PRICE <= mid <= 1.0 - MIN_TRUSTWORTHY_PRICE


def debias(probability: float, exponent: float = DEBIAS_EXPONENT) -> float:
    """Correct market prices for the favourite-longshot bias.

    ``p' = p^k / (p^k + (1-p)^k)`` with ``k > 1`` pushes prices away from the
    centre, undoing the market's tendency to overprice longshots and slightly
    underprice heavy favourites.
    """
    p = min(max(float(probability), 1e-6), 1.0 - 1e-6)
    numerator = p**exponent
    return numerator / (numerator + (1.0 - p) ** exponent)


# ------------------------------------------------------------------ polymarket


def tennis_events(*, closed: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    """List tennis events from Polymarket's Gamma API.  No key required."""
    return _get(
        f"{GAMMA_URL}/events",
        {"tag_slug": "tennis", "closed": str(closed).lower(), "limit": limit},
    )


def us_open_events(events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Filter to US Open events."""
    source = events if events is not None else tennis_events()
    return [
        event
        for event in source
        if "us open" in str(event.get("title", "")).lower()
        or "usopen" in str(event.get("slug", "")).lower()
    ]


def quotes_from_event(event: dict[str, Any]) -> list[Quote]:
    """Extract a :class:`Quote` for every sub-market of an event."""
    quotes = []
    for market in event.get("markets", []):
        quotes.append(
            Quote(
                venue="polymarket",
                market_id=str(market.get("conditionId") or market.get("id") or ""),
                question=str(market.get("question") or ""),
                best_bid=_as_float(market.get("bestBid")),
                best_ask=_as_float(market.get("bestAsk")),
                last_trade=_as_float(market.get("lastTradePrice")),
                volume=_as_float(market.get("volume")) or 0.0,
                accepting_orders=bool(market.get("acceptingOrders")),
                closed=bool(market.get("closed")),
            )
        )
    return quotes


def clob_midpoint(token_id: str) -> float | None:
    """Live midpoint from the CLOB, which is fresher than the Gamma snapshot."""
    try:
        payload = _get(f"{CLOB_URL}/midpoint", {"token_id": token_id})
    except MarketError:
        return None
    return _as_float(payload.get("mid"))


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------- kalshi


def kalshi_markets(
    series_ticker: str, *, status: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    """List Kalshi markets for a series.  Free reads, no key required.

    Useful tickers: ``KXATP-26USO`` and ``KXWTA-26USO`` for tournament outrights,
    ``KXATPGAME`` for per-match winners.  The per-match series is empty before
    the draw is published and fills closer to play, so poll it rather than
    assuming it is unavailable.
    """
    params: dict[str, Any] = {"series_ticker": series_ticker, "limit": limit}
    if status:
        params["status"] = status
    try:
        return _get(f"{KALSHI_URL}/markets", params).get("markets", [])
    except MarketError:
        return []


def kalshi_quote(market: dict[str, Any]) -> Quote:
    """Convert a Kalshi market into a :class:`Quote`.  Prices are in cents."""
    bid = _as_float(market.get("yes_bid"))
    ask = _as_float(market.get("yes_ask"))
    last = _as_float(market.get("last_price"))
    return Quote(
        venue="kalshi",
        market_id=str(market.get("ticker") or ""),
        question=str(market.get("title") or market.get("subtitle") or ""),
        best_bid=bid / 100.0 if bid is not None else None,
        best_ask=ask / 100.0 if ask is not None else None,
        last_trade=last / 100.0 if last is not None else None,
        volume=_as_float(market.get("volume")) or 0.0,
        accepting_orders=str(market.get("status", "")).lower() == "active",
        closed=str(market.get("status", "")).lower() in {"settled", "finalized", "closed"},
    )
