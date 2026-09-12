"""the-odds-api.com client: bookmaker consensus as a forecasting input.

Market consensus is the strongest single signal available to this project.  The
measured Brier of the closing line on 2025-26 ATP hard courts is 0.2031 for
Betfair and 0.2061 for Pinnacle, and in the predecessor event a de-vigged
consensus with no model on top of it would have scored +3,924 relative points
against an actual +3,419 - the alpha layer was worth *negative* 566.  The lesson
is not "do not model", it is "start from the market and justify every departure".

Two things make this venue useful beyond a single price:

**Twenty-one books per match.**  Their disagreement is small (about 2.5 points
of probability on a typical match) which makes the median a robust consensus and
the spread an honest confidence signal.

**Books disagree about the total-games line.**  One quotes 20.5, another 21.0,
another 21.5.  Each is a point on the survival curve of total games, so the
cross-section reconstructs a distribution rather than a single number - and
total games is what drives match duration (4.89-5.30 minutes per game, r=0.91),
long sets, and tiebreak probability.

Budget is the binding constraint: the free tier is 500 requests and a request
costs *markets x regions*, so h2h+totals for one region is 2.  Every call
records the remaining quota reported by the API, and the client refuses to spend
below a reserve so a careless loop cannot strand the bot mid-tournament.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BASE_URL = "https://api.the-odds-api.com/v4"
USER_AGENT = "grand-slam-challenge-bot/0.1 (odds)"

#: The two sports keys this competition needs.  Both are inactive until the
#: draw starts, which is not an error.
US_OPEN_SPORTS = ("tennis_atp_us_open", "tennis_wta_us_open")

#: Never spend the last of the quota; leave enough for the closing refresh of
#: the final, which is the highest-multiplier round and the tie-breaker.
#:
#: Lowered from 40 to 16 on 2026-09-11, deliberately, because the round it was
#: protecting has arrived.  Three matches remain in the tournament - both men's
#: semi-finals and the women's final - and all three score at 3x.  A refresh
#: costs markets x regions, so h2h+totals over two regions is 4 credits per
#: sport, and 16 covers a closing refresh of every remaining match with room to
#: spare.  Holding 40 back past this point would retire the quota unspent on the
#: highest-value questions of the event, which is the opposite of what the
#: reserve is for.
#: Lowered again from 16 to 2 on 2026-09-12.  The women's final is the last
#: match of the tournament, so there is no later round for the reserve to
#: protect.  8 credits remain and a refresh costs 2 per sport at one region, so
#: 2 held back still covers a closing look while leaving enough to price the
#: last twenty questions of the event, all at 3x.  Retiring the quota unspent on
#: them would be the exact failure the reserve exists to prevent.
QUOTA_RESERVE = 2

BUDGET_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "odds_budget.json"


class OddsError(Exception):
    """Raised when the odds venue cannot be read."""


@dataclass
class Budget:
    """What the API says is left, persisted so restarts do not lose track."""

    remaining: int | None = None
    used: int | None = None
    checked_at: str = ""

    @classmethod
    def load(cls, path: Path = BUDGET_FILE) -> Budget:
        try:
            return cls(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path = BUDGET_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")

    @property
    def spendable(self) -> int:
        if self.remaining is None:
            return QUOTA_RESERVE + 1
        return max(self.remaining - QUOTA_RESERVE, 0)


@dataclass
class BookLine:
    """One bookmaker's two-way price on one market."""

    bookmaker: str
    outcomes: dict[str, float]
    point: float | None = None

    @property
    def overround(self) -> float:
        return sum(1.0 / price for price in self.outcomes.values() if price > 0)


@dataclass
class Consensus:
    """The de-vigged cross-book view of one two-way market."""

    name: str
    probability: float
    n_books: int
    spread: float
    overround: float
    point: float | None = None

    @property
    def is_trustworthy(self) -> bool:
        """Two books agreeing means little; twenty agreeing means a lot.

        A wide spread is the market itself saying the number is uncertain, and
        that should reduce our confidence rather than be averaged away.
        """
        return self.n_books >= 3 and self.spread <= 0.10


# ----------------------------------------------------------------- de-vigging


def devig_multiplicative(odds: dict[str, float]) -> dict[str, float]:
    """Normalise implied probabilities to sum to one.

    Simple and unbiased in aggregate, but it spreads the margin proportionally,
    which systematically overstates longshots - exactly the bias the favourite
    -longshot literature describes.
    """
    implied = {k: 1.0 / v for k, v in odds.items() if v > 0}
    total = sum(implied.values())
    if total <= 0:
        raise OddsError("no positive prices to de-vig")
    return {k: v / total for k, v in implied.items()}


def devig_shin(odds: dict[str, float], *, tolerance: float = 1e-10) -> dict[str, float]:
    """Shin (1993) de-vigging: solve for the implied insider share ``z``.

    Shin models the bookmaker's margin as protection against better-informed
    traders rather than a flat tax.  The margin it removes is therefore larger
    on longshots, which is the empirically right direction and why this is the
    standard choice for two-way markets.

    Falls back to multiplicative when the book is not two-way or the solve does
    not converge, because a slightly biased number beats no number.
    """
    implied = {k: 1.0 / v for k, v in odds.items() if v > 0}
    if len(implied) != 2:
        return devig_multiplicative(odds)
    total = sum(implied.values())
    if total <= 0:
        raise OddsError("no positive prices to de-vig")
    if total <= 1.0:
        return devig_multiplicative(odds)

    def normalised(z: float) -> dict[str, float]:
        out = {}
        for key, pi in implied.items():
            root = math.sqrt(max(z * z + 4.0 * (1.0 - z) * pi * pi / total, 0.0))
            out[key] = (root - z) / (2.0 * (1.0 - z)) if z < 1.0 else pi / total
        return out

    low, high = 0.0, 0.99
    for _ in range(200):
        mid = (low + high) / 2.0
        if abs(sum(normalised(mid).values()) - 1.0) < tolerance:
            break
        if sum(normalised(mid).values()) > 1.0:
            low = mid
        else:
            high = mid
    result = normalised((low + high) / 2.0)
    total_out = sum(result.values())
    if total_out <= 0 or not all(0.0 < v < 1.0 for v in result.values()):
        return devig_multiplicative(odds)
    return {k: v / total_out for k, v in result.items()}


def consensus(lines: list[BookLine], *, method: str = "shin") -> dict[str, Consensus]:
    """Median de-vigged probability across books, with the spread retained.

    The median rather than the mean because a single book with a stale or
    mistyped price should not move the answer, and tennis books do occasionally
    leave a line up after news.
    """
    devig = devig_shin if method == "shin" else devig_multiplicative
    per_outcome: dict[str, list[float]] = {}
    overrounds: list[float] = []
    for line in lines:
        if len(line.outcomes) != 2:
            continue
        try:
            fair = devig(line.outcomes)
        except OddsError:
            continue
        overrounds.append(line.overround)
        for name, probability in fair.items():
            per_outcome.setdefault(name, []).append(probability)

    out: dict[str, Consensus] = {}
    point = lines[0].point if lines else None
    for name, values in per_outcome.items():
        if not values:
            continue
        out[name] = Consensus(
            name=name,
            probability=float(statistics.median(values)),
            n_books=len(values),
            spread=float(max(values) - min(values)),
            overround=float(statistics.median(overrounds)) if overrounds else 1.0,
            point=point,
        )
    return out


# --------------------------------------------------------------------- client


@dataclass
class OddsClient:
    """Quota-aware reader for the-odds-api.com."""

    api_key: str
    budget: Budget = field(default_factory=Budget.load)
    regions: str = "eu"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> OddsClient:
        source = env if env is not None else dict(os.environ)
        key = source.get("ODDS_API_KEY", "").strip().strip('"')
        if not key:
            from bot.sportspredict.config import default_env_path, read_env_file

            key = read_env_file(default_env_path()).get("ODDS_API_KEY", "").strip().strip('"')
        if not key:
            raise OddsError("ODDS_API_KEY is not set; add it to bot/.env")
        return cls(api_key=key)

    def _get(self, path: str, params: dict[str, Any], *, cost: int) -> Any:
        if cost > self.budget.spendable:
            raise OddsError(
                f"refusing to spend {cost} credits: {self.budget.remaining} remain and "
                f"{QUOTA_RESERVE} are reserved for the final. "
                "Reduce markets or regions, or raise the reserve deliberately."
            )
        query = urllib.parse.urlencode({**params, "apiKey": self.api_key})
        request = urllib.request.Request(
            f"{BASE_URL}{path}?{query}", headers={"User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self._record(response.headers)
        except urllib.error.HTTPError as exc:
            raise OddsError(f"HTTP {exc.code} from {path}: {exc.read()[:200]!r}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise OddsError(f"{path} unreachable: {exc}") from exc
        return payload

    def _record(self, headers: Any) -> None:
        remaining = headers.get("x-requests-remaining")
        used = headers.get("x-requests-used")
        if remaining is not None:
            self.budget.remaining = int(float(remaining))
        if used is not None:
            self.budget.used = int(float(used))
        self.budget.checked_at = datetime.now(UTC).isoformat()
        self.budget.save()

    def sports(self, *, all_sports: bool = False) -> list[dict[str, Any]]:
        """List sports.  This endpoint is free and does not consume quota."""
        params = {"all": "true"} if all_sports else {}
        return self._get("/sports/", params, cost=0)

    def us_open_status(self) -> dict[str, bool]:
        """Whether each US Open draw is live yet.  Free."""
        available = {s["key"]: bool(s.get("active")) for s in self.sports(all_sports=True)}
        return {key: available.get(key, False) for key in US_OPEN_SPORTS}

    def odds(
        self, sport: str, *, markets: str = "h2h,totals", regions: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch odds for one sport.  Costs ``len(markets) x len(regions)``."""
        region = regions or self.regions
        cost = len(markets.split(",")) * len(region.split(","))
        return self._get(
            f"/sports/{sport}/odds/",
            {"regions": region, "markets": markets, "oddsFormat": "decimal"},
            cost=cost,
        )


# ----------------------------------------------------------------- extraction


def lines_for(event: dict[str, Any], market_key: str) -> dict[float | None, list[BookLine]]:
    """Group every book's quote on one market by its line."""
    grouped: dict[float | None, list[BookLine]] = {}
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") != market_key:
                continue
            by_point: dict[float | None, dict[str, float]] = {}
            for outcome in market.get("outcomes", []):
                name, price = outcome.get("name"), outcome.get("price")
                if name is None or not price:
                    continue
                by_point.setdefault(outcome.get("point"), {})[str(name)] = float(price)
            for point, outcomes in by_point.items():
                if len(outcomes) == 2:
                    grouped.setdefault(point, []).append(
                        BookLine(
                            bookmaker=str(book.get("key", "?")), outcomes=outcomes, point=point
                        )
                    )
    return grouped


def match_winner(event: dict[str, Any], *, method: str = "shin") -> dict[str, Consensus]:
    """De-vigged consensus probability for each player to win the match."""
    grouped = lines_for(event, "h2h")
    return consensus(grouped.get(None, []), method=method)


def total_games_ladder(event: dict[str, Any], *, method: str = "shin") -> dict[float, float]:
    """``P(total games > line)`` at every line the books quote.

    Books disagreeing about where to set the line is the useful part: each line
    is a separate point on the survival curve, so the cross-section gives a
    distribution instead of a single number.
    """
    ladder: dict[float, float] = {}
    for point, lines in lines_for(event, "totals").items():
        if point is None:
            continue
        agreed = consensus(lines, method=method)
        over = agreed.get("Over")
        if over is not None and over.n_books >= 2:
            ladder[float(point)] = over.probability
    return dict(sorted(ladder.items()))
