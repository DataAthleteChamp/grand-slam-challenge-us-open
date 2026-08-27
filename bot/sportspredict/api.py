"""SportsPredict Model API client.

Ported from the Probability Cup bot with four changes learned from that run:

* full-jitter backoff instead of plain exponential (avoids retry stampedes),
* retries on the whole 5xx family, not just 500,
* :meth:`SportsPredictClient.reconcile` — a desired-vs-actual control loop so a
  timed-out request can never leave a market unpriced,
* :meth:`SportsPredictClient.upsert_batch` — create-or-update in one call, which is
  what "submit early, refine later" actually needs.

Tennis matters here: US Open matches after the first on each court have no fixed
start time, so lock times are unpredictable.  Never rely on a late refresh — get a
prediction in early and PATCH it afterwards.
"""

from __future__ import annotations

import json as jsonlib
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import Config, ConfigError, RateLimiter, retry_delay

USER_AGENT = "grand-slam-challenge-bot/0.1"

#: Statuses worth retrying: rate limiting plus the transient 5xx family.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

#: The API accepts at most 50 predictions per batch request.
MAX_BATCH_SIZE = 50


class SportsPredictError(Exception):
    """Raised for SportsPredict API errors."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"SportsPredict API error {status}: {message}")


class SportsPredictClient:
    """Client for the SportsPredict Model API."""

    def __init__(
        self,
        config: Config,
        *,
        rate_limiter: RateLimiter | None = None,
        max_attempts: int = 5,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not config.api_key:
            raise ConfigError("An API key is required to create a live API client.")
        self.config = config
        self.rate_limiter = rate_limiter or RateLimiter()
        self.max_attempts = max(1, int(max_attempts))
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------ discovery

    def list_events(self) -> list[dict[str, Any]]:
        """Return active and upcoming events."""
        return self._request("GET", "/events", params={"limit": 100})

    def find_probability_event(self) -> dict[str, Any]:
        """Find the configured event by title.

        There is deliberately no fallback to "the first probability event".  A
        fallback here fails open: if the Grand Slam event is absent or renamed,
        the bot would submit a full slate of tennis forecasts into whatever
        unrelated event happened to be listed first, and every downstream check
        would pass because the numbers themselves are fine.  Better to stop.
        """
        events = self.list_events()
        match_text = self.config.event_match.lower()
        for event in events:
            if match_text and match_text in str(event.get("title", "")).lower():
                return event
        titles = ", ".join(repr(str(e.get("title", ""))) for e in events[:10]) or "none"
        raise SportsPredictError(
            404,
            f"No event title contains {self.config.event_match!r}. Available: {titles}. "
            "Set SPORTSPREDICT_EVENT_MATCH to the correct event rather than "
            "guessing, submissions are not reversible.",
        )

    def list_lobbies(self, event_id: str) -> list[dict[str, Any]]:
        """Return lobbies for an event."""
        return self._request("GET", "/lobbies", params={"event_id": event_id})

    def get_lobby(self, event_id: str) -> dict[str, Any]:
        """Return the first (shared) lobby for an event."""
        lobbies = self.list_lobbies(event_id)
        if not lobbies:
            raise SportsPredictError(404, f"No lobby found for event {event_id}.")
        return lobbies[0]

    def join_lobby(self, lobby_id: str) -> Any:
        """Join a lobby; HTTP 409 means already joined and is not an error."""
        try:
            return self._request("POST", f"/lobbies/{lobby_id}/join")
        except SportsPredictError as exc:
            if exc.status == 409:
                return {"id": lobby_id, "joined": True}
            raise

    def ensure_joined(self, lobby: dict[str, Any]) -> dict[str, Any]:
        """Join the lobby if needed and return a joined lobby dict."""
        if lobby.get("joined"):
            return lobby
        lobby_id = str(lobby.get("id") or "")
        if not lobby_id:
            raise ValueError("lobby must include an id")
        self.join_lobby(lobby_id)
        return {**lobby, "joined": True}

    def bootstrap(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Find the event, get its lobby, join it, and return ``(event, lobby)``."""
        event = self.find_probability_event()
        lobby = self.ensure_joined(self.get_lobby(str(event["id"])))
        return event, lobby

    def list_matches(
        self, event_id: str | None = None, lobby_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return matches with at least one open market."""
        params = _clean_params({"event_id": event_id, "lobby_id": lobby_id})
        return self._request("GET", "/matches", params=params)

    def list_markets(self, lobby_id: str, match_id: str | None = None) -> list[dict[str, Any]]:
        """Return open markets, optionally scoped to one match.

        Always pass ``match_id`` when feeding an LLM: the unfiltered response for a
        large event runs to hundreds of kilobytes.
        """
        params = _clean_params({"lobby_id": lobby_id, "match_id": match_id})
        return self._request("GET", "/markets", params=params)

    # ----------------------------------------------------------------- submission

    def submit_prediction(self, market_id: str, lobby_id: str, probability: int) -> dict[str, Any]:
        """Create one prediction with an integer probability from 1 to 99."""
        _validate_probability(probability)
        return self._request(
            "POST",
            "/predictions",
            json={"market_id": market_id, "lobby_id": lobby_id, "probability": probability},
        )

    def update_prediction(self, prediction_id: str, probability: int) -> dict[str, Any]:
        """Update an existing prediction before its market closes."""
        _validate_probability(probability)
        return self._request(
            "PATCH", f"/predictions/{prediction_id}", json={"probability": probability}
        )

    def submit_batch(self, predictions: list[dict[str, Any]]) -> dict[str, Any]:
        """Create predictions in chunks of at most 50 and aggregate the responses."""
        normalized = []
        for prediction in predictions:
            probability = prediction.get("probability")
            _validate_probability(probability)
            normalized.append(
                {
                    "market_id": prediction["market_id"],
                    "lobby_id": prediction["lobby_id"],
                    "probability": probability,
                }
            )

        aggregate: dict[str, Any] = {"total": 0, "succeeded": 0, "failed": 0, "results": []}
        for start in range(0, len(normalized), MAX_BATCH_SIZE):
            chunk = normalized[start : start + MAX_BATCH_SIZE]
            response = self._request("POST", "/predictions/batch", json={"predictions": chunk})
            aggregate["total"] += int(response.get("total", len(chunk)))
            aggregate["succeeded"] += int(response.get("succeeded", 0))
            aggregate["failed"] += int(response.get("failed", 0))
            aggregate["results"].extend(response.get("results", []))
        return aggregate

    def list_predictions(self, lobby_id: str | None = None) -> list[dict[str, Any]]:
        """Return submitted predictions, optionally scoped to a lobby."""
        return self._request("GET", "/predictions", params=_clean_params({"lobby_id": lobby_id}))

    def list_results(self, lobby_id: str | None = None) -> list[dict[str, Any]]:
        """Return settled predictions, each carrying its Brier score."""
        return self._request("GET", "/results", params=_clean_params({"lobby_id": lobby_id}))

    # ------------------------------------------------------------- reconciliation

    def upsert_batch(
        self, lobby_id: str, desired: dict[str, int], *, existing: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Drive submitted state toward ``desired`` (``market_id`` -> probability).

        New markets are created via the batch endpoint; markets already holding a
        prediction are PATCHed.  This is the create-or-update primitive behind
        "submit early, refine later".

        ``existing`` maps ``market_id`` -> ``prediction_id``; it is fetched when omitted.
        """
        if existing is None:
            existing = {
                str(p["market_id"]): str(p["id"])
                for p in self.list_predictions(lobby_id)
                if p.get("market_id") and p.get("id")
            }

        creates = [
            {"market_id": mid, "lobby_id": lobby_id, "probability": prob}
            for mid, prob in desired.items()
            if mid not in existing
        ]
        updates = {mid: prob for mid, prob in desired.items() if mid in existing}

        summary: dict[str, Any] = {"created": 0, "updated": 0, "failed": 0, "errors": []}
        if creates:
            response = self.submit_batch(creates)
            summary["created"] = response.get("succeeded", 0)
            summary["failed"] += response.get("failed", 0)
            summary["errors"].extend(
                r.get("error") for r in response.get("results", []) if not r.get("success")
            )

        for market_id, probability in updates.items():
            try:
                self.update_prediction(existing[market_id], probability)
                summary["updated"] += 1
            except SportsPredictError as exc:
                summary["failed"] += 1
                summary["errors"].append(f"{market_id}: {exc.message}")
        return summary

    def reconcile(self, lobby_id: str, desired: dict[str, int]) -> dict[str, Any]:
        """Report the drift between intended and actually-submitted predictions.

        Read-only.  Run it after any submission cycle: a request that times out may
        still have succeeded server-side, so the only trustworthy check is to read
        state back and diff it.
        """
        actual = {
            str(p["market_id"]): p for p in self.list_predictions(lobby_id) if p.get("market_id")
        }
        missing = sorted(set(desired) - set(actual))
        mismatched = {
            mid: {"desired": prob, "actual": actual[mid].get("probability")}
            for mid, prob in desired.items()
            if mid in actual and int(actual[mid].get("probability", -1)) != int(prob)
        }
        return {
            "in_sync": not missing and not mismatched,
            "missing": missing,
            "mismatched": mismatched,
            "submitted": len(actual),
            "desired": len(desired),
        }

    # ---------------------------------------------------------------- transport

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Send one HTTP request with pacing, jittered retry, and JSON error handling."""
        url = self._build_url(path, params)
        body = None
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if json is not None:
            body = jsonlib.dumps(json).encode("utf-8")
            headers["Content-Type"] = "application/json"

        last_status, last_message = 0, "request failed"
        for attempt in range(self.max_attempts):
            self.rate_limiter.wait()
            request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return _parse_response(response.read())
            except urllib.error.HTTPError as exc:
                last_status = exc.code
                last_message = _error_message(exc.read().decode("utf-8", "replace"), exc.reason)
                if exc.code in RETRYABLE_STATUSES and attempt < self.max_attempts - 1:
                    time.sleep(retry_delay(attempt, exc.headers.get("Retry-After")))
                    continue
                raise SportsPredictError(exc.code, last_message) from exc
            except urllib.error.URLError as exc:
                last_status, last_message = 0, str(exc.reason)
                if attempt < self.max_attempts - 1:
                    time.sleep(retry_delay(attempt))
                    continue
                raise SportsPredictError(
                    0, f"Network failure on {method.upper()} {url}: {last_message}"
                ) from exc

        raise SportsPredictError(
            last_status, f"Gave up after {self.max_attempts} attempts: {last_message}"
        )

    def _build_url(self, path: str, params: dict[str, Any] | None = None) -> str:
        base = self.config.base_url.rstrip("/")
        url = f"{base}/{path.lstrip('/')}"
        clean = _clean_params(params or {})
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
        return url


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value is not None}


def _parse_response(raw_body: bytes) -> Any:
    if not raw_body:
        return None
    return jsonlib.loads(raw_body.decode("utf-8"))


def _error_message(raw_body: str, fallback: str | None = None) -> str:
    if raw_body:
        try:
            payload = jsonlib.loads(raw_body)
        except jsonlib.JSONDecodeError:
            return raw_body
        message = payload.get("message") if isinstance(payload, dict) else None
        if isinstance(message, list):
            return "; ".join(str(item) for item in message)
        if message:
            return str(message)
        if isinstance(payload, dict) and payload.get("statusCode"):
            return f"HTTP {payload['statusCode']}"
    return fallback or "unknown error"


def _validate_probability(probability: Any) -> None:
    """The API rejects 0 and 100 to keep Brier behaviour meaningful."""
    if type(probability) is not int or not 1 <= probability <= 99:
        raise ValueError("probability must be an int in [1, 99]")
