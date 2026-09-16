"""Turn API match payloads into the ``MatchContext`` the pricer needs.

The API gives us two player names and a round.  The pricer needs ratings, serve
and return skill, count rates and pace.  This module is the join, and the join is
where a forecasting bot quietly loses most of its edge: every name that fails to
resolve falls back to a tour-average player, which prices the match as if both
sides were identical.  That failure is invisible downstream - the numbers look
perfectly reasonable, they are simply the base rate wearing a model's clothes.

So misses are counted and reported rather than swallowed.
"""

from __future__ import annotations

import configparser
import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from bot.data.names import ambiguous_keys, build_crosswalk, candidate_keys, disambiguate, resolve
from bot.pricer import MatchContext, PlayerProfile

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "players"
OVERRIDES_FILE = DATA_DIR / "name_overrides.ini"

# Best-of by tour.  Grand slam men's singles is best-of-five, women's
# best-of-three; nothing else appears in this competition.
BEST_OF = {"ATP": 5, "WTA": 3}

# Service points per match, needed to convert per-point rates into per-match
# counts.  Measured from the tidy match table, median over hard-court slam
# matches, split by format because a five-set match is not a three-set match
# with more points at the same rate.
SERVICE_POINTS = {("ATP", 5): 112, ("ATP", 3): 74, ("WTA", 3): 65, ("WTA", 5): 65}

_ROUND_ALIASES = {
    "final": "F",
    "f": "F",
    "semifinal": "SF",
    "semi-final": "SF",
    "semifinals": "SF",
    "sf": "SF",
    "quarterfinal": "QF",
    "quarter-final": "QF",
    "quarterfinals": "QF",
    "qf": "QF",
    "round of 16": "R16",
    "r16": "R16",
    "fourth round": "R16",
    "round of 32": "R32",
    "r32": "R32",
    "third round": "R32",
    "round of 64": "R64",
    "r64": "R64",
    "second round": "R64",
    "round of 128": "R128",
    "r128": "R128",
    "first round": "R128",
}


def normalise_round(value: str | None) -> str:
    """Map whatever the API calls a round onto our internal codes."""
    if not value:
        return "UNKNOWN"
    text = str(value).strip().lower()
    if text in _ROUND_ALIASES:
        return _ROUND_ALIASES[text]
    upper = text.upper().replace(" ", "")
    if upper in {"F", "SF", "QF", "R16", "R32", "R64", "R128"}:
        return upper
    return "UNKNOWN"


@dataclass
class Roster:
    """Player profiles keyed by name, with fuzzy resolution."""

    profiles: dict[str, PlayerProfile] = field(default_factory=dict)
    tours: dict[str, str] = field(default_factory=dict)
    ranks: dict[str, float] = field(default_factory=dict)
    crosswalk: dict[str, str] = field(default_factory=dict)
    misses: Counter = field(default_factory=Counter)
    resolved_by_dominance: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)
    _ambiguous: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path | str = DATA_DIR) -> Roster:
        directory = Path(data_dir)
        roster = cls()
        for tour in ("atp", "wta"):
            path = directory / f"{tour}_players.csv"
            if not path.exists():
                continue
            with path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    roster._add(row)
        roster._apply_draw_rates(directory / "us_open_2026_draw.csv")
        # Ace vulnerability: how many aces this player concedes per return
        # point, relative to tour average. A different skill from winning
        # return points, and worth 10 points of R-squared on the men's draw.
        for tour in ("atp", "wta"):
            path = directory / f"{tour}_ace_vulnerability.csv"
            if not path.exists():
                continue
            with path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    name = (row.get("opponent") or "").strip()
                    profile = roster.profiles.get(name)
                    if profile is not None:
                        profile.ace_vulnerability = _number(row.get("vuln"), 1.0)

        names = list(roster.profiles)
        roster.crosswalk = build_crosswalk(names)
        roster._ambiguous = ambiguous_keys(names)
        roster.overrides = load_overrides(directory / OVERRIDES_FILE.name)
        return roster

    def _apply_draw_rates(self, path: Path) -> None:
        """Overlay the validated event-specific ace/DF credibility blend."""
        if not path.exists():
            return
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                name = (row.get("canonical_name") or "").strip()
                profile = self.profiles.get(name)
                if profile is None:
                    continue
                ace_rate = _optional(row.get("ace_rate"))
                df_rate = _optional(row.get("df_rate"))
                if ace_rate is not None:
                    profile.ace_rate = ace_rate
                if df_rate is not None:
                    profile.df_rate = df_rate

    def _resolve_ambiguous(self, name: str) -> str | None:
        """Break a genuine collision by career record.

        ``B. Nakashima`` is Brandon or Bryce and no amount of string handling can
        tell them apart, but a main-draw player at a slam has a tour record and
        an obscure namesake does not.  Resolve only when one candidate dominates
        tenfold; otherwise leave it unresolved so the pricer falls back to a tour
        average rather than confidently using the wrong player's ratings.
        """
        weights = {n: p.matches for n, p in self.profiles.items()}
        for key in candidate_keys(name):
            candidates = self._ambiguous.get(key)
            if not candidates:
                continue
            chosen = disambiguate(key, list(candidates), weights)
            if chosen is not None:
                self.resolved_by_dominance[name] = chosen
                return chosen
        return None

    def _add(self, row: dict[str, str]) -> None:
        name = (row.get("player") or "").strip()
        if not name:
            return
        profile = PlayerProfile(
            name=name,
            serve_skill=_number(row.get("serve_skill"), 0.0),
            return_skill=_number(row.get("return_skill"), 0.0),
            matches=int(_number(row.get("matches"), 0.0)),
            ace_rate=_optional(row.get("ace_rate_shrunk") or row.get("ace_rate")),
            df_rate=_optional(row.get("df_rate")),
            winner_rate=_optional(row.get("winner_rate")),
            unforced_rate=_optional(row.get("unforced_rate")),
            charted_matches=int(_number(row.get("charted_matches"), 0.0)),
            height=_optional(row.get("height")),
            pace_minutes=_number(row.get("pace_minutes"), 0.0),
            ace_vulnerability=_number(row.get("ace_vulnerability"), 1.0),
        )
        # Keep the profile with more matches when a name appears twice, which
        # happens when the raw feeds spell one player two ways.
        existing = self.profiles.get(name)
        if existing is None or profile.matches > existing.matches:
            self.profiles[name] = profile
            self.tours[name] = (row.get("tour") or "").strip().upper()
            rank = _optional(row.get("rank"))
            if rank is not None:
                self.ranks[name] = rank

    # ---------------------------------------------------------------- lookups

    def find(self, name: str) -> PlayerProfile | None:
        """Resolve a name to a profile, exactly or through the crosswalk."""
        if not name:
            return None
        cleaned = name.strip()
        if cleaned in self.overrides:
            # A hand-verified mapping outranks every automatic rule: it exists
            # precisely because the automatic rules were wrong or refused.
            canonical = self.overrides[cleaned]
            return self.profiles.get(canonical) if canonical else None
        direct = self.profiles.get(cleaned)
        if direct is not None:
            return direct
        resolved = resolve(name, self.crosswalk)
        if resolved is not None:
            return self.profiles.get(resolved)
        dominant = self._resolve_ambiguous(name)
        if dominant is not None:
            return self.profiles.get(dominant)
        self.misses[name] += 1
        return None

    def profile(self, name: str) -> PlayerProfile:
        """Resolve a name, or return an unrated placeholder.

        An unrated profile prices at the tour average, which is the honest answer
        when we know nothing - but ``matches == 0`` makes that visible to the
        confidence machinery rather than passing it off as a real read.
        """
        return self.find(name) or PlayerProfile(name=name or "unknown")

    def tour_of(self, *names: str) -> str:
        for name in names:
            resolved = resolve(name, self.crosswalk) if name else None
            tour = self.tours.get(resolved or (name or "").strip())
            if tour:
                return tour
        return "ATP"

    def rank_of(self, name: str) -> float | None:
        resolved = resolve(name, self.crosswalk) if name else None
        return self.ranks.get(resolved or (name or "").strip())

    def context(
        self,
        player_a: str,
        player_b: str,
        *,
        round_code: str | None = None,
        tour: str | None = None,
    ) -> MatchContext:
        """Build the pricing context for one match."""
        resolved_tour = (tour or self.tour_of(player_a, player_b)).upper()
        if resolved_tour not in BEST_OF:
            resolved_tour = "ATP"
        best_of = BEST_OF[resolved_tour]
        return MatchContext(
            tour=resolved_tour,
            best_of=best_of,
            player_a=self.profile(player_a),
            player_b=self.profile(player_b),
            round_code=normalise_round(round_code),
            rank_a=self.rank_of(player_a),
            rank_b=self.rank_of(player_b),
            expected_service_points=SERVICE_POINTS.get((resolved_tour, best_of), 80),
        )

    @property
    def miss_report(self) -> list[tuple[str, int]]:
        """Names we could not resolve, worst first.  Check this every run."""
        return self.misses.most_common()


def players_from_match(match: dict) -> tuple[str, str]:
    """Pull the two player names out of an API match payload.

    The exact field names are not documented, so several spellings are tried
    before falling back to splitting the title on a separator.
    """
    for key_a, key_b in (
        ("player_a", "player_b"),
        ("home_team", "away_team"),
        ("home", "away"),
        ("team_a", "team_b"),
        ("competitor_a", "competitor_b"),
    ):
        left, right = match.get(key_a), match.get(key_b)
        if left and right:
            return _name_of(left), _name_of(right)

    players = match.get("players") or match.get("competitors")
    if isinstance(players, list) and len(players) >= 2:
        return _name_of(players[0]), _name_of(players[1])

    title = str(match.get("title") or match.get("name") or "")
    for separator in (" vs. ", " vs ", " v ", " - ", " — "):
        if separator in title:
            left, _, right = title.partition(separator)
            return left.strip(), right.strip()
    return "", ""


def _name_of(value: object) -> str:
    if isinstance(value, dict):
        for key in ("name", "full_name", "display_name", "title", "short_name"):
            if value.get(key):
                return str(value[key]).strip()
        return ""
    return str(value).strip()


def _number(value: str | None, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _optional(value: str | None) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def load_overrides(path: Path = OVERRIDES_FILE) -> dict[str, str]:
    """Read hand-verified name mappings for genuine collisions.

    An empty value means "verified absent from our data", which is different
    from "not looked at yet" and must not be retried by the automatic rules.
    """
    if not path.exists():
        return {}
    parser = configparser.ConfigParser(allow_no_value=True)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return {}
    out: dict[str, str] = {}
    for section in parser.sections():
        for key, value in parser.items(section):
            out[key.strip()] = (value or "").strip()
    return out
