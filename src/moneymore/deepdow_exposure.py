from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn

LOOKBACK = 60
HORIZON = 20


class AnalyticalSoftmaxAllocator(nn.Module):
    """Source-compatible subset of DeepDow 0.2.3 SoftmaxAllocator."""

    def __init__(self, temperature: float = 1.0) -> None:
        super().__init__()
        self.temperature = temperature

    def forward(self, logits: torch.Tensor) -> torch.Tensor:
        return torch.softmax(logits / self.temperature, dim=1)


class DeepDowExposureNet(nn.Module):
    """DeepDow-shaped network: (batch, channels, lookback, assets) -> weights."""

    def __init__(self, channels: int = 3, hidden: int = 16) -> None:
        super().__init__()
        self.extractor = nn.Sequential(
            nn.Conv1d(channels, hidden, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.Conv1d(hidden, 1, kernel_size=3, padding=1),
        )
        self.allocator = AnalyticalSoftmaxAllocator()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch, channels, lookback, assets = x.shape
        per_asset = x.permute(0, 3, 1, 2).reshape(batch * assets, channels, lookback)
        logits = self.extractor(per_asset).mean(dim=2).reshape(batch, assets)
        return self.allocator(logits)


def deepdow_sharpe_loss(weights: torch.Tensor, future_returns: torch.Tensor) -> torch.Tensor:
    """DeepDow 0.2.3 SharpeRatio equation for simple, daily-rebalanced returns."""
    portfolio = (future_returns * weights[:, None, :]).sum(dim=2)
    return -(portfolio.mean(dim=1) / (portfolio.std(dim=1) + 1e-4)).mean()


def make_samples(returns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(returns, dtype=np.float32)
    risky = values
    cash = np.zeros_like(values)
    assets = np.stack([risky, cash], axis=1)
    features: list[np.ndarray] = []
    targets: list[np.ndarray] = []
    for end in range(LOOKBACK - 1, len(values) - HORIZON):
        window = assets[end - LOOKBACK + 1 : end + 1]
        cumulative = np.cumprod(1 + window, axis=0)
        drawdown = cumulative / np.maximum.accumulate(cumulative, axis=0) - 1
        rolling_vol = np.broadcast_to(window.std(axis=0, keepdims=True), window.shape)
        x = np.stack([window * 100, rolling_vol * 100, drawdown], axis=0)
        y = assets[end + 1 : end + 1 + HORIZON]
        features.append(x.astype(np.float32))
        targets.append(y.astype(np.float32))
    if not features:
        raise ValueError("DeepDow exposure dataset has insufficient history")
    return np.stack(features), np.stack(targets)


def predict_exposure(model: DeepDowExposureNet, returns: np.ndarray, device: str = "cpu") -> float:
    values = np.asarray(returns, dtype=np.float32)
    if len(values) < LOOKBACK:
        raise ValueError("DeepDow inference requires 60 observations")
    padded = np.concatenate([values, np.zeros(HORIZON, dtype=np.float32)])
    x, _ = make_samples(padded[-(LOOKBACK + HORIZON) :])
    model.eval()
    with torch.no_grad():
        weights = model(torch.from_numpy(x[-1:]).to(device)).cpu().numpy()[0]
    return float(np.clip(weights[0], 0, 1))


@dataclass(frozen=True)
class DeepDowArtifact:
    directory: Path
    metadata: dict[str, object]

    @property
    def model_path(self) -> Path:
        return self.directory / "model.pt"


def latest_deepdow_artifact(root: Path) -> DeepDowArtifact | None:
    base = root / "state" / "exposure-league" / "deepdow" / "candidates"
    candidates: list[DeepDowArtifact] = []
    for metadata_path in base.glob("*/metadata.json") if base.exists() else []:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        artifact = DeepDowArtifact(metadata_path.parent, metadata)
        if metadata.get("status") == "COMPLETED" and artifact.model_path.exists():
            candidates.append(artifact)
    return max(
        candidates,
        key=lambda item: (
            str(item.metadata.get("trained_until", "")),
            int(item.metadata.get("epochs", 0) or 0),
            str(item.metadata.get("created_at", "")),
        ),
        default=None,
    )


def load_deepdow_model(artifact: DeepDowArtifact, device: str = "cpu") -> DeepDowExposureNet:
    model = DeepDowExposureNet()
    model.load_state_dict(torch.load(artifact.model_path, map_location=device, weights_only=True))
    return model.to(device)
