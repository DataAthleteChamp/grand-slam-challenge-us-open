"""Evaluate the pricer by Monte Carlo against known ground truth.

Backtesting on real matches is limited by how few of them there are: one US Open
round is 64 matches, so a Brier difference of 0.01 is well inside the noise and
almost any change can be made to look like an improvement.

This harness sidesteps that. It draws a synthetic tournament from the *same*
generative model the simulator assumes, where the true probability of every
question is known exactly, and asks whether the pricer recovers it. That answers
a different and sharper question than a backtest: not "did we do well on the
matches that happened", but "is the machinery correct, and where does it break".

Three things it measures:

**Calibration.** Of the questions we priced near 30, did about 30% happen? A
model can have a good Brier and still be systematically over-confident, and
over-confidence is what the 1-99 clamp punishes hardest.

**Resolution.** Does the model separate likely from unlikely at all? A model
that answers the base rate every time is perfectly calibrated and worthless, so
calibration alone is never evidence of skill.

**Sensitivity to being wrong.** The real risk is not simulator noise but a
mis-specified input: ratings that are too compressed, or a tour baseline that is
off. Those are injected deliberately to see how much damage they do, because a
model that degrades gracefully under bad inputs is worth more than one that is
sharper when everything is perfect.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class Scorecard:
    """What a batch of forecasts was worth."""

    n: int
    brier: float
    base_brier: float
    calibration_error: float
    resolution: float
    sharpness: float
    calibration_slope: float

    @property
    def skill(self) -> float:
        """Brier skill score against always answering the base rate."""
        if self.base_brier <= 0:
            return 0.0
        return 1.0 - self.brier / self.base_brier

    def __str__(self) -> str:
        return (
            f"n={self.n}  brier={self.brier:.4f}  base={self.base_brier:.4f}  "
            f"skill={100 * self.skill:+.1f}%  cal_err={self.calibration_error:.4f}  "
            f"resolution={self.resolution:.4f}  slope={self.calibration_slope:.3f}"
        )


def score(forecasts: np.ndarray, outcomes: np.ndarray, *, bins: int = 10) -> Scorecard:
    """Decompose forecast quality.

    Uses the Murphy decomposition: Brier = reliability - resolution +
    uncertainty. Reliability is the part a model controls by being honest;
    resolution is the part it controls by being informative.
    """
    forecasts = np.asarray(forecasts, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    if forecasts.size == 0:
        return Scorecard(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    base = float(outcomes.mean())
    brier = float(np.mean((forecasts - outcomes) ** 2))
    base_brier = float(np.mean((base - outcomes) ** 2))

    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.digitize(forecasts, edges[1:-1]), 0, bins - 1)
    reliability = resolution = 0.0
    for b in range(bins):
        mask = index == b
        count = int(mask.sum())
        if count == 0:
            continue
        mean_forecast = float(forecasts[mask].mean())
        mean_outcome = float(outcomes[mask].mean())
        weight = count / forecasts.size
        reliability += weight * (mean_forecast - mean_outcome) ** 2
        resolution += weight * (mean_outcome - base) ** 2

    return Scorecard(
        n=int(forecasts.size),
        brier=brier,
        base_brier=base_brier,
        calibration_error=math.sqrt(reliability),
        resolution=resolution,
        sharpness=float(np.mean(np.abs(forecasts - 0.5))),
        calibration_slope=_calibration_slope(forecasts, outcomes),
    )


def _calibration_slope(forecasts: np.ndarray, outcomes: np.ndarray) -> float:
    """Slope of ``outcome ~ forecast``.  1.0 is perfectly calibrated.

    Below 1 the forecasts are spread wider than the evidence supports, which is
    over-confidence; above 1 they are too timid.

    This replaces a metric that compared mean |forecast - 0.5| against mean
    |outcome - 0.5|.  That was degenerate: outcomes are 0 or 1, so the second
    term is always exactly 0.5 and the statistic could never be positive no
    matter how over-confident the model was.  It reported -0.34 for every run,
    which looked like a finding and was arithmetic.
    """
    if forecasts.size < 3:
        return 1.0
    spread = float(np.var(forecasts))
    if spread <= 1e-12:
        return 1.0
    covariance = float(np.cov(forecasts, outcomes, bias=True)[0, 1])
    return covariance / spread


def draw_outcomes(truth: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Realise binary outcomes from known true probabilities."""
    return (rng.random(truth.shape) < truth).astype(float)


def bootstrap_skill(
    forecasts: np.ndarray,
    outcomes: np.ndarray,
    *,
    n_boot: int = 2000,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Bootstrap confidence interval on Brier skill.

    A single round is 64 matches. Reporting a skill number from that without an
    interval invites tuning against noise, which is how a backtest turns into
    overfitting.
    """
    rng = np.random.default_rng(seed)
    forecasts = np.asarray(forecasts, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    n = forecasts.size
    if n == 0:
        return 0.0, 0.0, 0.0
    skills = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, n, n)
        skills[i] = score(forecasts[pick], outcomes[pick]).skill
    point = score(forecasts, outcomes).skill
    return point, float(np.percentile(skills, 2.5)), float(np.percentile(skills, 97.5))


def reliability_table(
    forecasts: np.ndarray, outcomes: np.ndarray, *, bins: int = 10
) -> list[tuple[float, float, int]]:
    """``(mean forecast, observed rate, count)`` per bin, for eyeballing."""
    forecasts = np.asarray(forecasts, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.digitize(forecasts, edges[1:-1]), 0, bins - 1)
    rows = []
    for b in range(bins):
        mask = index == b
        if not mask.any():
            continue
        rows.append((float(forecasts[mask].mean()), float(outcomes[mask].mean()), int(mask.sum())))
    return rows


def render_reliability(rows: list[tuple[float, float, int]]) -> str:
    lines = [f"{'predicted':>10s} {'observed':>10s} {'n':>7s}  {'gap':>7s}"]
    for predicted, observed, count in rows:
        lines.append(f"{predicted:10.3f} {observed:10.3f} {count:7d}  {observed - predicted:+7.3f}")
    return "\n".join(lines)
