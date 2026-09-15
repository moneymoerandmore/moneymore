from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import gymnasium as gym
import numpy as np
from gymnasium import spaces


LOOKBACK = 20
ONE_WAY_COST = 0.00061


def exposure_observation(
    returns: np.ndarray, index: int, previous_exposure: float, lookback: int = LOOKBACK
) -> np.ndarray:
    window = np.asarray(returns[max(0, index - lookback + 1) : index + 1], dtype=np.float32)
    if len(window) < lookback:
        window = np.pad(window, (lookback - len(window), 0))
    wealth = np.cumprod(1 + window)
    drawdown = float(wealth[-1] / max(float(wealth.max()), 1e-8) - 1)
    volatility = float(window.std(ddof=1)) if len(window) > 1 else 0.0
    return np.concatenate(
        [np.clip(window, -0.2, 0.2), [volatility, drawdown, previous_exposure]]
    ).astype(np.float32)


class TotalExposureEnv(gym.Env[np.ndarray, np.ndarray]):
    """FinRL-style market environment whose only action is total stock exposure."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        returns: np.ndarray,
        *,
        lookback: int = LOOKBACK,
        one_way_cost: float = ONE_WAY_COST,
        reward_scaling: float = 100.0,
    ) -> None:
        super().__init__()
        self.returns = np.asarray(returns, dtype=np.float32)
        if len(self.returns) <= lookback + 1:
            raise ValueError("FinRL exposure environment has insufficient returns")
        self.lookback = lookback
        self.one_way_cost = one_way_cost
        self.reward_scaling = reward_scaling
        self.action_space = spaces.Box(0.0, 1.0, shape=(1,), dtype=np.float32)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(lookback + 3,), dtype=np.float32
        )
        self.index = lookback - 1
        self.exposure = 0.0

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        self.index = self.lookback - 1
        self.exposure = 0.0
        return exposure_observation(self.returns, self.index, self.exposure, self.lookback), {}

    def step(self, action: np.ndarray):
        target = float(np.clip(np.asarray(action).reshape(-1)[0], 0, 1))
        cost = abs(target - self.exposure) * self.one_way_cost
        next_return = float(self.returns[self.index + 1])
        net_return = target * next_return - cost
        self.exposure = target
        self.index += 1
        terminated = self.index >= len(self.returns) - 1
        observation = exposure_observation(
            self.returns, self.index, self.exposure, self.lookback
        )
        return observation, net_return * self.reward_scaling, terminated, False, {
            "net_return": net_return,
            "target_exposure": target,
            "turnover_cost": cost,
        }


def evaluate_model(model, returns: np.ndarray) -> dict[str, float]:
    env = TotalExposureEnv(returns)
    observation, _ = env.reset()
    net_returns: list[float] = []
    exposures: list[float] = []
    done = False
    while not done:
        action, _ = model.predict(observation, deterministic=True)
        observation, _, terminated, truncated, info = env.step(action)
        done = terminated or truncated
        net_returns.append(float(info["net_return"]))
        exposures.append(float(info["target_exposure"]))
    values = np.asarray(net_returns)
    volatility = float(values.std(ddof=1)) if len(values) > 1 else 0.0
    sharpe = float(np.sqrt(242) * values.mean() / volatility) if volatility > 0 else 0.0
    nav = np.cumprod(1 + values)
    drawdown = nav / np.maximum.accumulate(nav) - 1
    exposure_values = np.asarray(exposures)
    realized_market = np.asarray(returns[LOOKBACK:], dtype=float)[: len(values)]
    benchmark = realized_market.copy()
    if len(benchmark):
        benchmark[0] -= ONE_WAY_COST
    benchmark_volatility = float(benchmark.std(ddof=1)) if len(benchmark) > 1 else 0.0
    benchmark_nav = np.cumprod(1 + benchmark)
    benchmark_sharpe = (
        float(np.sqrt(242) * benchmark.mean() / benchmark_volatility)
        if benchmark_volatility > 0 else 0.0
    )
    return {
        "sharpe": sharpe,
        "total_return": float(nav[-1] - 1),
        "maximum_drawdown": float(drawdown.min()),
        "average_exposure": float(np.mean(exposures)),
        "exposure_std": float(exposure_values.std(ddof=1)),
        "exposure_p05": float(np.quantile(exposure_values, 0.05)),
        "exposure_p50": float(np.quantile(exposure_values, 0.50)),
        "exposure_p95": float(np.quantile(exposure_values, 0.95)),
        "full_exposure_ratio": float(np.mean(exposure_values >= 0.95)),
        "average_exposure_before_negative_day": float(
            exposure_values[realized_market < 0].mean()
        ) if np.any(realized_market < 0) else 0.0,
        "average_exposure_before_positive_day": float(
            exposure_values[realized_market > 0].mean()
        ) if np.any(realized_market > 0) else 0.0,
        "fixed_100_total_return": float(benchmark_nav[-1] - 1),
        "fixed_100_sharpe": benchmark_sharpe,
        "excess_return_vs_fixed_100": float(nav[-1] - benchmark_nav[-1]),
    }


@dataclass(frozen=True)
class FinRLArtifact:
    directory: Path
    metadata: dict[str, object]

    @property
    def model_path(self) -> Path:
        return self.directory / "model.zip"


def latest_finrl_artifact(root: Path) -> FinRLArtifact | None:
    base = root / "state" / "exposure-league" / "finrl" / "candidates"
    candidates: list[FinRLArtifact] = []
    for metadata_path in base.glob("*/metadata.json") if base.exists() else []:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        artifact = FinRLArtifact(metadata_path.parent, metadata)
        if metadata.get("status") == "COMPLETED" and artifact.model_path.exists():
            candidates.append(artifact)
    return max(
        candidates,
        key=lambda item: (
            str(item.metadata.get("trained_until", "")),
            int(item.metadata.get("timesteps", 0) or 0),
            str(item.metadata.get("created_at", "")),
        ),
        default=None,
    )
