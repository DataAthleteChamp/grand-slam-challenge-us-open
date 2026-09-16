"""The results ledger: what we predicted, what happened, and what it cost.

One JSON file per match under ``results/matches/<match_id>.json``, keyed by
SportsPredict's own match and market UUIDs so a row here can always be traced
back to the thing it was scored on.  One file per match rather than one big
table because files are appended by different runs at different times, and a
single shared file is how you lose a day's predictions to a half-written write.

The ledger exists to answer one question honestly: **which families are we
actually good at?**  The predecessor event answered it decisively - player props
returned +957 relative points while fouls returned -125, and the fouls markets
were ones where the crowd beat every model. Knowing that a year earlier would
have been worth more than any modelling improvement, because the correct action
was not "model fouls better" but "stop departing from the crowd on fouls".

That only works if predictions are recorded *before* outcomes are known. A
ledger written after the fact records what we wish we had thought.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results" / "matches"


@dataclass
class Entry:
    """One question: our answer, and eventually what happened."""

    market_id: str
    question: str
    family: str
    submission: int
    probability: float
    base_rate: float
    model: float | None = None
    market: float | None = None
    market_weight: float = 0.0
    confidence: float = 0.0
    lane: str = "honest"
    notes: list[str] = field(default_factory=list)
    predicted_at: str = ""

    # Filled in at settlement.
    outcome: int | None = None
    crowd: float | None = None
    api_brier_score: float | None = None
    api_rbp: float | None = None
    settled_at: str = ""

    @property
    def is_settled(self) -> bool:
        return self.outcome is not None

    @property
    def brier(self) -> float | None:
        if self.outcome is None:
            return None
        if self.api_brier_score is not None:
            return self.api_brier_score
        return (self.submission / 100.0 - self.outcome) ** 2

    @property
    def crowd_brier(self) -> float | None:
        if self.outcome is None or self.crowd is None:
            return None
        return (self.crowd - self.outcome) ** 2

    @property
    def consensus_skill_rbp(self) -> float | None:
        """Skill term against the mean crowd probability.

        This omits the field-variance term in SportsPredict's official score and
        must not be presented as exact API RBP.
        """
        ours, theirs = self.brier, self.crowd_brier
        if ours is None or theirs is None:
            return None
        return (theirs - ours) * 100.0

    @property
    def rbp(self) -> float | None:
        """Official API RBP when archived, otherwise the consensus skill term."""
        return self.api_rbp if self.api_rbp is not None else self.consensus_skill_rbp

    def weighted_rbp(self, multiplier: int) -> float | None:
        if self.api_rbp is not None:
            return self.api_rbp
        value = self.consensus_skill_rbp
        return None if value is None else value * multiplier


@dataclass
class MatchRecord:
    """Everything about one match, prediction through settlement."""

    match_id: str
    match_name: str = ""
    draw: str = ""
    round_code: str = ""
    multiplier: int = 1
    opening_time: str = ""
    closing_time: str = ""
    player_a: str = ""
    player_b: str = ""
    entries: list[Entry] = field(default_factory=list)

    @property
    def settled(self) -> list[Entry]:
        return [e for e in self.entries if e.is_settled]

    @property
    def total_rbp(self) -> float:
        return sum(e.weighted_rbp(self.multiplier) or 0.0 for e in self.settled)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entries"] = [
            {
                **asdict(entry),
                "brier": _round(entry.brier),
                "crowd_brier": _round(entry.crowd_brier),
                "consensus_skill_rbp": _round(entry.consensus_skill_rbp, 2),
                "rbp": _round(entry.rbp, 2),
            }
            for entry in self.entries
        ]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MatchRecord:
        entries = []
        for raw in payload.get("entries", []):
            # Derived fields are written for readability and recomputed on load,
            # so an edited file can never disagree with itself.
            clean = {k: v for k, v in raw.items() if k in Entry.__dataclass_fields__}
            entries.append(Entry(**clean))
        fields = {k: v for k, v in payload.items() if k in cls.__dataclass_fields__}
        fields["entries"] = entries
        return cls(**fields)


class Ledger:
    """Read and write match records."""

    def __init__(self, directory: Path | str = RESULTS_DIR) -> None:
        self.directory = Path(directory)

    def path_for(self, match_id: str) -> Path:
        return self.directory / f"{match_id}.json"

    def load(self, match_id: str) -> MatchRecord | None:
        path = self.path_for(match_id)
        if not path.exists():
            return None
        try:
            return MatchRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    def save(self, record: MatchRecord) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(record.match_id)
        # Write to a temporary file and move it into place, so an interrupted
        # run cannot leave a half-written record that silently loses a match.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(record.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(path)
        return path

    def record_predictions(self, record: MatchRecord, entries: list[Entry]) -> MatchRecord:
        """Add or update lane-specific predictions without rewriting settlement.

        Re-pricing before close replaces the desired value for the same lane.
        Once settled, that lane/market pair is immutable.
        """
        stamp = datetime.now(UTC).isoformat()
        existing = {(e.lane, e.market_id): e for e in record.entries}
        for entry in entries:
            entry.predicted_at = entry.predicted_at or stamp
            key = (entry.lane, entry.market_id)
            previous = existing.get(key)
            if previous is not None and previous.is_settled:
                continue
            existing[key] = entry
        record.entries = list(existing.values())
        return record

    def settle(
        self,
        match_id: str,
        outcomes: dict[str, int],
        crowd: dict[str, float],
        *,
        lane: str | None = None,
        api_brier: dict[str, float] | None = None,
        api_rbp: dict[str, float] | None = None,
    ) -> int:
        """Attach lane-specific outcomes and official API scores."""
        record = self.load(match_id)
        if record is None:
            return 0
        api_brier = api_brier or {}
        api_rbp = api_rbp or {}
        stamp = datetime.now(UTC).isoformat()
        n = 0
        for entry in record.entries:
            if entry.market_id in outcomes and (lane is None or entry.lane == lane):
                entry.outcome = int(outcomes[entry.market_id])
                entry.crowd = crowd.get(entry.market_id)
                entry.api_brier_score = api_brier.get(entry.market_id)
                entry.api_rbp = api_rbp.get(entry.market_id)
                entry.settled_at = stamp
                n += 1
        if n:
            self.save(record)
        return n

    def all_records(self) -> list[MatchRecord]:
        if not self.directory.exists():
            return []
        records = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                records.append(MatchRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError):
                continue
        return records


def family_report(records: list[MatchRecord]) -> dict[str, dict[str, float]]:
    """Per-family scorecard: the question the ledger exists to answer.

    ``beat_crowd`` matters more than mean RBP. A family can look profitable on
    the strength of one lucky match while losing more often than it wins, and
    that is a family to stop trusting rather than to scale up.
    """
    buckets: dict[str, list[tuple[float, float, float]]] = {}
    for record in records:
        for entry in record.settled:
            rbp = entry.weighted_rbp(record.multiplier)
            if rbp is None or entry.brier is None or entry.crowd_brier is None:
                continue
            buckets.setdefault(entry.family, []).append((rbp, entry.brier, entry.crowd_brier))

    report: dict[str, dict[str, float]] = {}
    for family, rows in sorted(buckets.items()):
        rbps = [r for r, _, _ in rows]
        report[family] = {
            "n": len(rows),
            "total_rbp": round(sum(rbps), 1),
            "mean_rbp": round(sum(rbps) / len(rbps), 2),
            "beat_crowd": round(sum(1 for r in rbps if r > 0) / len(rbps), 3),
            "our_brier": round(sum(b for _, b, _ in rows) / len(rows), 4),
            "crowd_brier": round(sum(c for _, _, c in rows) / len(rows), 4),
        }
    return report


def render_report(report: dict[str, dict[str, float]]) -> str:
    if not report:
        return "nothing settled yet"
    header = (
        f"{'family':28s} {'n':>5s} {'total':>9s} {'mean':>8s} "
        f"{'beat%':>7s} {'ours':>7s} {'crowd':>7s}"
    )
    lines = [header, "-" * len(header)]
    for family, row in sorted(report.items(), key=lambda kv: -kv[1]["total_rbp"]):
        lines.append(
            f"{family:28s} {row['n']:5.0f} {row['total_rbp']:9.1f} {row['mean_rbp']:8.2f} "
            f"{100 * row['beat_crowd']:6.1f}% {row['our_brier']:7.4f} {row['crowd_brier']:7.4f}"
        )
    total = sum(r["total_rbp"] for r in report.values())
    lines.append("-" * len(header))
    lines.append(f"{'TOTAL':28s} {sum(r['n'] for r in report.values()):5.0f} {total:9.1f}")
    return "\n".join(lines)


def _round(value: float | None, places: int = 4) -> float | None:
    return None if value is None else round(value, places)
