"""Total-games lines from Pinnacle, used as a second opinion on match length.

Four of the five live families have no market at all in the odds feed we already
use, which is why the duration and tiebreak answers rest entirely on the
simulator.  Pinnacle publishes a per-player games total on essentially every
Grand Slam match, and the sum of the two sides is a sharp estimate of the total
games in the match - the quantity our duration model is built on.

This matters because the simulator is measurably wrong about it.  Against
Pinnacle's lines on 45 live US Open matches, the raw simulator ran +2.76 games
on the men's draw (sd 3.17, n=24, t=+4.27) and -0.48 on the women's (t=-1.12),
and the per-match correlation was only +0.52 and +0.63.  A correlation that low
means the two disagree about *which* matches will be long, not merely about the
average, so a flat offset cannot fix it and a second opinion is worth having.

The endpoint is the same unauthenticated one Pinnacle's own site calls, and this
module reads it at a low rate and caches the result for the life of a pricing
run.  Nothing here places a bet; the lines are used only as model input, exactly
as the existing bookmaker feed is used for the match-winner family.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

#: Pinnacle's internal sport id for tennis.
TENNIS_SPORT_ID = 33

BASE = "https://guest.api.arcadia.pinnacle.com/0.1"

#: The key the public site sends.  It is not a credential - it is a fixed
#: constant embedded in Pinnacle's own JavaScript bundle and identical for every
#: anonymous visitor.
GUEST_KEY = "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "X-API-Key": GUEST_KEY,
    "Accept": "application/json",
}

#: Seconds between requests.  One matchup needs one call, so a full US Open day
#: is a few dozen; this keeps us far below anything that could be a burden.
REQUEST_INTERVAL = 0.25

REQUEST_TIMEOUT = 25.0


class PinnacleError(RuntimeError):
    """Raised when the feed cannot be read at all."""


@dataclass(frozen=True)
class GamesLine:
    """One match's total-games estimate, and the moneyline that came with it."""

    player_a: str
    player_b: str
    total_games: float
    best_of: int
    p_a_wins: float | None = None


def _american_to_probability(price: float) -> float:
    """Convert American odds to an implied probability, vig included."""
    price = float(price)
    return 100.0 / (price + 100.0) if price > 0 else -price / (-price + 100.0)


def _get(url: str) -> Any:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PinnacleError(f"pinnacle request failed: {url}") from exc


def _devig(over: float, under: float) -> float:
    """Strip the vig from a two-way market, returning P(over)."""
    total = over + under
    return over / total if total > 0 else 0.5


def _line_from_team_totals(markets: list[dict]) -> float | None:
    """Recover expected total games from the per-player game totals.

    Pinnacle posts several alternative lines per player.  The one priced closest
    to a coin flip is the market's median, and the small residual imbalance says
    which way the true mean sits relative to that line, so the two together give
    a better estimate than the line alone.
    """
    best: dict[str, tuple[float, float, float]] = {}
    for market in markets:
        if market.get("type") != "team_total" or market.get("period") != 0:
            continue
        prices = {p.get("designation"): p.get("price") for p in market.get("prices", [])}
        points = next((p.get("points") for p in market.get("prices", [])), None)
        if "over" not in prices or "under" not in prices or points is None:
            continue
        side = str(market.get("key", "")).rsplit(";", 1)[-1]
        if side not in {"home", "away"}:
            continue
        p_over = _devig(
            _american_to_probability(prices["over"]),
            _american_to_probability(prices["under"]),
        )
        distance = abs(p_over - 0.5)
        if side not in best or distance < best[side][0]:
            best[side] = (distance, float(points), p_over)

    if "home" not in best or "away" not in best:
        return None
    # Each half-game of line is worth roughly half a game of mean, so the
    # imbalance is converted at 2 games per unit of probability.
    total = 0.0
    for _, points, p_over in best.values():
        total += points + (p_over - 0.5) * 2.0
    return round(total, 2)


def _moneyline(markets: list[dict]) -> float | None:
    for market in markets:
        if market.get("type") != "moneyline" or market.get("period") != 0:
            continue
        prices = {p.get("designation"): p.get("price") for p in market.get("prices", [])}
        if "home" in prices and "away" in prices:
            home = _american_to_probability(prices["home"])
            away = _american_to_probability(prices["away"])
            return round(_devig(home, away), 4)
    return None


def fetch_games_lines(event_filter: str = "US Open", limit: int | None = None) -> list[GamesLine]:
    """Read every tennis matchup whose league name contains ``event_filter``.

    Returns one entry per match that carries a usable total-games line.  A match
    that cannot be read is skipped rather than raising, because a missing line
    must never stop a pricing run: the simulator is still a complete model on
    its own and the blend simply falls back to it.
    """
    matchups = _get(f"{BASE}/sports/{TENNIS_SPORT_ID}/matchups?brandId=0")
    if not isinstance(matchups, list):
        raise PinnacleError("unexpected matchup payload")

    wanted = [
        m
        for m in matchups
        if m.get("type") == "matchup"
        and not m.get("parentId")
        and event_filter.lower() in str((m.get("league") or {}).get("name", "")).lower()
    ]
    if limit is not None:
        wanted = wanted[:limit]

    lines: list[GamesLine] = []
    for matchup in wanted:
        participants = [p.get("name") for p in matchup.get("participants", [])]
        if len(participants) != 2 or not all(participants):
            continue
        try:
            markets = _get(f"{BASE}/matchups/{matchup['id']}/markets/related/straight")
        except PinnacleError:
            continue
        if not isinstance(markets, list):
            continue
        total = _line_from_team_totals(markets)
        if total is None:
            continue
        lines.append(
            GamesLine(
                player_a=str(participants[0]),
                player_b=str(participants[1]),
                total_games=total,
                best_of=int((matchup.get("league") or {}).get("bestOfX") or 3),
                p_a_wins=_moneyline(markets),
            )
        )
        time.sleep(REQUEST_INTERVAL)
    return lines
