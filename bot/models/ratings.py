"""Opponent-adjusted serve and return ratings.

A player's raw service statistics say as much about who they played as about how
they served.  Someone who spent a season facing qualifiers looks like a better
server than someone who faced the top ten, and using raw rates as model inputs
imports that bias straight into every forecast.

The fix is a two-way additive model fitted across all matches at once::

    spw(i serving to j) = base_surface + serve_i - return_j

which is the same structure as the batter-versus-pitcher problem in baseball and
as item-response theory.  Fitting is by alternating ridge regression: hold the
return ratings fixed and solve for serve, then swap, and repeat.  Each update is
a shrunk mean, so the ridge penalty doubles as credibility weighting and players
with few matches are pulled toward zero automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class ServeReturnRatings:
    """Fitted serve and return skills, expressed as deviations from the baseline."""

    base: float
    serve: dict[str, float] = field(default_factory=dict)
    ret: dict[str, float] = field(default_factory=dict)
    matches: dict[str, int] = field(default_factory=dict)
    iterations: int = 0
    residual_rmse: float = float("nan")

    def predict_spw(self, server: str, returner: str) -> float:
        """Predicted share of service points won for ``server`` against ``returner``."""
        return self.base + self.serve.get(server, 0.0) - self.ret.get(returner, 0.0)

    def as_frame(self) -> pd.DataFrame:
        """Return the ratings as a tidy table, best servers first."""
        players = sorted(set(self.serve) | set(self.ret))
        return pd.DataFrame(
            {
                "player": players,
                "serve_skill": [self.serve.get(p, 0.0) for p in players],
                "return_skill": [self.ret.get(p, 0.0) for p in players],
                "matches": [self.matches.get(p, 0) for p in players],
            }
        ).sort_values("serve_skill", ascending=False, ignore_index=True)


def fit_serve_return(
    long: pd.DataFrame,
    *,
    ridge: float = 40.0,
    iterations: int = 25,
    tolerance: float = 1e-6,
    weight_by_points: bool = True,
) -> ServeReturnRatings:
    """Fit opponent-adjusted serve and return ratings by alternating ridge.

    Parameters
    ----------
    long:
        Player-match table with ``player``, ``opponent``, ``spw`` and ``svpt``.
    ridge:
        Shrinkage strength in units of *matches* (or service points when
        ``weight_by_points``).  Larger values pull sparse players harder toward
        the baseline.  40 is roughly the credibility constant measured for serve
        statistics.
    weight_by_points:
        Weight each observation by service points, so a five-set match counts for
        more than a straight-sets one.
    """
    frame = long.dropna(subset=["player", "opponent", "spw"])
    if frame.empty:
        return ServeReturnRatings(base=float("nan"))
    frame = frame[np.isfinite(pd.to_numeric(frame["spw"], errors="coerce"))]
    if frame.empty:
        return ServeReturnRatings(base=float("nan"))

    servers = frame["player"].to_numpy()
    returners = frame["opponent"].to_numpy()
    observed = frame["spw"].to_numpy(dtype=float)
    weights = (
        frame["svpt"].fillna(0).to_numpy(dtype=float)
        if weight_by_points
        else np.ones(len(frame))
    )
    weights = np.where(weights > 0, weights, 1.0)

    base = float(np.average(observed, weights=weights))
    serve = dict.fromkeys(np.unique(servers), 0.0)
    ret = dict.fromkeys(np.unique(returners), 0.0)

    server_index = pd.Series(servers)
    returner_index = pd.Series(returners)
    weight_series = pd.Series(weights)

    previous = np.inf
    used = 0
    for step in range(1, iterations + 1):
        used = step
        # Serve step: residual after removing the returner's contribution.
        residual = observed - base + returner_index.map(ret).to_numpy(dtype=float)
        serve = _weighted_shrunk_mean(server_index, residual, weight_series, ridge)

        # Return step: the returner explains what the server did not.
        residual = base + server_index.map(serve).to_numpy(dtype=float) - observed
        ret = _weighted_shrunk_mean(returner_index, residual, weight_series, ridge)

        predicted = (
            base
            + server_index.map(serve).to_numpy(dtype=float)
            - returner_index.map(ret).to_numpy(dtype=float)
        )
        rmse = float(np.sqrt(np.average((observed - predicted) ** 2, weights=weights)))
        if abs(previous - rmse) < tolerance:
            previous = rmse
            break
        previous = rmse

    counts = frame["player"].value_counts().to_dict()
    return ServeReturnRatings(
        base=base,
        serve=serve,
        ret=ret,
        matches=counts,
        iterations=used,
        residual_rmse=previous,
    )


def _weighted_shrunk_mean(
    keys: pd.Series, values: np.ndarray, weights: pd.Series, ridge: float
) -> dict[str, float]:
    """Weighted group mean shrunk toward zero by ``ridge`` pseudo-observations."""
    frame = pd.DataFrame({"key": keys.to_numpy(), "v": values, "w": weights.to_numpy()})
    frame["wv"] = frame["v"] * frame["w"]
    grouped = frame.groupby("key", sort=False)[["wv", "w"]].sum()
    scale = float(frame["w"].mean()) or 1.0
    shrunk = grouped["wv"] / (grouped["w"] + ridge * scale)
    return shrunk.to_dict()


def matchup_point_probabilities(
    ratings: ServeReturnRatings, player_a: str, player_b: str
) -> tuple[float, float]:
    """Return ``(p_a_on_serve, p_b_on_serve)`` for a specific matchup.

    These are the inputs the point model expects, already corrected for the
    quality of the opponents each player has actually faced.
    """
    return (
        float(np.clip(ratings.predict_spw(player_a, player_b), 0.05, 0.95)),
        float(np.clip(ratings.predict_spw(player_b, player_a), 0.05, 0.95)),
    )
