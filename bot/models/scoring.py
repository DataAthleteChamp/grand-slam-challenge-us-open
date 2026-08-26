"""Brier scoring, Murphy decomposition, and calibration diagnostics.

Relative Brier Points are ``(crowd_brier - your_brier) * 100``, so the headline
number tells you *whether* you beat the field but not *why*.  The Murphy
decomposition does::

    BS = REL - RES + UNC

``REL`` (reliability, lower better) is miscalibration: your 70% forecasts do not
happen 70% of the time.  ``RES`` (resolution, higher better) is discrimination:
how far your forecasts move away from the base rate in the right direction.
``UNC`` is fixed by the questions themselves.

The distinction matters because the remedies are opposite.  Losing on ``REL``
means recalibrate.  Losing on ``RES`` means you need better information — no
amount of recalibration will help.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BrierDecomposition:
    """Murphy's three-component partition of the Brier score."""

    brier: float
    reliability: float
    resolution: float
    uncertainty: float
    n: int

    def as_dict(self) -> dict[str, float]:
        return {
            "brier": round(self.brier, 5),
            "reliability": round(self.reliability, 5),
            "resolution": round(self.resolution, 5),
            "uncertainty": round(self.uncertainty, 5),
            "n": self.n,
        }


def brier_score(predictions: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean squared error of probabilistic forecasts.  Lower is better."""
    p = np.asarray(predictions, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    return float(np.mean((p - y) ** 2))


def relative_brier_points(
    predictions: np.ndarray, crowd: np.ndarray, outcomes: np.ndarray, multiplier: float = 1.0
) -> np.ndarray:
    """Per-question RBP: ``(crowd_brier - your_brier) * 100 * multiplier``."""
    p = np.asarray(predictions, dtype=float)
    c = np.asarray(crowd, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    return ((c - y) ** 2 - (p - y) ** 2) * 100.0 * float(multiplier)


def expected_rbp(crowd: float, truth: float) -> float:
    """Expected RBP from submitting the true probability against a biased crowd.

    ``E[RBP] = (c - mu)^2 * 100``.  The gain scales with the *square* of the
    crowd's error, which means effort belongs where the crowd is most wrong — not
    where you are most confident.  Those are different question sets.
    """
    return (float(crowd) - float(truth)) ** 2 * 100.0


def murphy_decomposition(
    predictions: np.ndarray, outcomes: np.ndarray, n_bins: int | None = None
) -> BrierDecomposition:
    """Decompose the Brier score into reliability, resolution and uncertainty.

    ``BS = REL - RES + UNC`` holds *exactly* only when forecasts are grouped by
    identical value; with equal-width bins the within-bin spread of forecasts
    leaks an extra term and the identity no longer closes.

    Submissions to this competition are integers 1-99, so the default
    (``n_bins=None``) groups by distinct forecast value and the decomposition is
    exact.  Pass an integer to force coarser equal-width bins for plotting, at the
    cost of the identity holding only approximately.
    """
    p = np.asarray(predictions, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    n = len(p)
    if n == 0:
        return BrierDecomposition(0.0, 0.0, 0.0, 0.0, 0)

    o_bar = float(y.mean())
    if n_bins is None:
        groups = [np.flatnonzero(p == value) for value in np.unique(p)]
    else:
        edges = np.linspace(0.0, 1.0, n_bins + 1)
        idx = np.clip(np.digitize(p, edges) - 1, 0, n_bins - 1)
        groups = [np.flatnonzero(idx == k) for k in range(n_bins)]

    reliability = 0.0
    resolution = 0.0
    for members in groups:
        n_k = len(members)
        if n_k == 0:
            continue
        p_bar = float(p[members].mean())
        o_k = float(y[members].mean())
        reliability += n_k * (p_bar - o_k) ** 2
        resolution += n_k * (o_k - o_bar) ** 2

    reliability /= n
    resolution /= n
    uncertainty = o_bar * (1.0 - o_bar)
    return BrierDecomposition(
        brier=brier_score(p, y),
        reliability=reliability,
        resolution=resolution,
        uncertainty=uncertainty,
        n=n,
    )


def spiegelhalter_z(predictions: np.ndarray, outcomes: np.ndarray) -> float:
    """Spiegelhalter's calibration test statistic, distributed ``N(0, 1)``.

    ``z = sum(y - p) / sqrt(sum(p (1 - p)))``.  Positive means under-prediction,
    negative means over-confidence.  Uses every forecast and needs no binning,
    which makes it the right early-tournament diagnostic when bins are too thin
    for a reliability diagram.
    """
    p = np.asarray(predictions, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    denominator = np.sqrt(np.sum(p * (1.0 - p)))
    if denominator <= 0:
        return 0.0
    return float(np.sum(y - p) / denominator)


def reliability_curve(
    predictions: np.ndarray, outcomes: np.ndarray, n_bins: int = 10
) -> list[dict[str, float]]:
    """Binned observed frequency against mean forecast, for a reliability diagram."""
    p = np.asarray(predictions, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, n_bins - 1)

    rows = []
    for k in range(n_bins):
        mask = idx == k
        n_k = int(mask.sum())
        if n_k == 0:
            continue
        rows.append(
            {
                "bin_low": float(edges[k]),
                "bin_high": float(edges[k + 1]),
                "n": n_k,
                "mean_forecast": float(p[mask].mean()),
                "observed_rate": float(y[mask].mean()),
            }
        )
    return rows


#: Minimum settled-question counts before each recalibration step is justified.
#:
#: Below roughly 50 settled forecasts, refitting a calibration map reliably makes
#: things *worse* — the variance of the fitted parameters exceeds the bias they
#: remove.  Detecting a Brier difference of 0.02 with 80% power needs about 400
#: observations, so early-tournament swings are noise, not evidence.
RECALIBRATION_GATES = {
    "diagnose_only": 0,
    "intercept_shift": 180,
    "platt": 300,
    "isotonic": 480,
}


def recalibration_stage(n_settled: int) -> str:
    """Return the strongest recalibration justified by ``n_settled`` observations."""
    stage = "diagnose_only"
    for name, gate in RECALIBRATION_GATES.items():
        if n_settled >= gate:
            stage = name
    return stage
