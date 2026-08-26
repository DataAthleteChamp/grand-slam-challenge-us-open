"""Configuration for the SportsPredict Model API client.

Two bots may be connected per account (see the Grand Slam Challenge Official Rules,
Section 5 "Connected Models").  Each bot has its own ``sp_live_...`` key and is a
separate leaderboard entry; ``key_var`` selects which one is acting.

A second *account* would void every entry, so both keys must belong to one account.
"""

from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://api.sportspredict.com/api/v1"
DEFAULT_EVENT_MATCH = "grand slam"

# The documented limit is 60 requests/minute per IP, shared across REST and MCP.
# 1.1s between requests leaves ~9% headroom for clock skew and concurrent lanes.
DEFAULT_REQUEST_INTERVAL_SECONDS = 1.1

# Environment variable names for each lane's API key.
LANE_KEY_VARS = {
    "honest": "SPORTSPREDICT_API_KEY",
    "scenario": "SPORTSPREDICT_API_KEY_SCENARIO",
}


class ConfigError(ValueError):
    """Raised when required SportsPredict configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """SportsPredict client configuration loaded from environment variables."""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    event_match: str = DEFAULT_EVENT_MATCH
    lane: str = "honest"

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = None,
        *,
        require_api_key: bool = True,
        lane: str = "honest",
        key_var: str | None = None,
    ) -> Config:
        """Load configuration from ``bot/.env`` and the process environment.

        ``lane`` picks the bot; ``key_var`` overrides the variable name directly.
        """
        env_values = read_env_file(env_file or default_env_path())

        def get(name: str, default: str = "") -> str:
            value = os.environ.get(name)
            if value is None or value == "":
                value = env_values.get(name, default)
            return value.strip()

        var = key_var or LANE_KEY_VARS.get(lane, LANE_KEY_VARS["honest"])
        api_key = get(var)
        if require_api_key and not api_key:
            raise ConfigError(
                f"{var} is required for live SportsPredict API calls. "
                "Set it in the environment or bot/.env (never commit it)."
            )

        base_url = get("SPORTSPREDICT_BASE_URL", DEFAULT_BASE_URL).rstrip("/") or DEFAULT_BASE_URL
        event_match = get("SPORTSPREDICT_EVENT_MATCH", DEFAULT_EVENT_MATCH) or DEFAULT_EVENT_MATCH
        return cls(api_key=api_key, base_url=base_url, event_match=event_match, lane=lane)


class RateLimiter:
    """Minimum-interval limiter, shared across threads and lanes.

    The API limit is per IP, so both bot lanes must share one limiter instance.
    """

    def __init__(self, min_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def wait(self) -> None:
        """Block until a request may be sent without violating the interval."""
        if self.min_interval_seconds <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delay = self.min_interval_seconds - (now - self._last_request_at)
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._last_request_at = now


def retry_delay(attempt: int, retry_after: str | None = None, *, jitter: bool = True) -> float:
    """Exponential backoff with full jitter, honouring ``Retry-After`` when present.

    Full jitter (uniform on ``[0, 2**attempt]``) is the AWS-recommended strategy: it
    avoids the retry stampede that plain exponential backoff produces when several
    markets fail at once.
    """
    if retry_after:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            pass
    ceiling = float(2**attempt)
    return random.uniform(0.0, ceiling) if jitter else ceiling


def default_env_path() -> Path:
    """Return the default ``bot/.env`` path."""
    return Path(__file__).resolve().parents[1] / ".env"


def read_env_file(path: str | Path) -> dict[str, str]:
    """Read a small ``KEY=VALUE`` .env file without mutating ``os.environ``."""
    env_path = Path(path)
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = _parse_env_value(value.strip())
    return values


def _parse_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        inner = value[1:-1]
        return inner.replace(r"\n", "\n").replace(r"\t", "\t") if inner else inner
    comment_index = value.find(" #")
    if comment_index != -1:
        value = value[:comment_index]
    return value.strip()
