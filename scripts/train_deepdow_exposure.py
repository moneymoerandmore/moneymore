from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset

from moneymore.deepdow_exposure import (
    DeepDowExposureNet,
    deepdow_sharpe_loss,
    make_samples,
)
from train_finrl_exposure import proxy_returns

ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def metrics(model, x: np.ndarray, y: np.ndarray, device: str) -> dict[str, float]:
    model.eval()
    with torch.no_grad():
        weights = model(torch.from_numpy(x).to(device)).cpu().numpy()
    # One non-overlapping realized return per decision; future horizons overlap.
    daily = (y[:, 0, :] * weights).sum(axis=1)
    volatility = float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
    nav = np.cumprod(1 + daily)
    drawdown = nav / np.maximum.accumulate(nav) - 1
    return {
        "sharpe": float(np.sqrt(242) * daily.mean() / volatility) if volatility else 0.0,
        "total_return": float(nav[-1] - 1),
        "maximum_drawdown": float(drawdown.min()),
        "average_exposure": float(weights[:, 0].mean()),
    }


def train_once(x, y, seed: int, epochs: int, device: str):
    torch.manual_seed(seed)
    model = DeepDowExposureNet().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    loader = DataLoader(
        TensorDataset(torch.from_numpy(x), torch.from_numpy(y)),
        batch_size=64, shuffle=True, generator=torch.Generator().manual_seed(seed),
    )
    losses = []
    for _ in range(epochs):
        model.train()
        epoch_losses = []
        for xb, yb in loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            loss = deepdow_sharpe_loss(model(xb), yb)
            loss.backward()
            optimizer.step()
            epoch_losses.append(float(loss.detach().cpu()))
        losses.append(float(np.mean(epoch_losses)))
    return model, losses


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    parser.add_argument("--epochs", type=int, default=100)
    args = parser.parse_args()
    dates, returns = proxy_returns(ROOT)
    x, y = make_samples(returns)
    train_end = int(len(x) * 0.70)
    validation_end = int(len(x) * 0.85)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    seeds = (17, 42, 101)
    trials = []
    best = None
    for seed in seeds:
        model, losses = train_once(x[:train_end], y[:train_end], seed, args.epochs, device)
        result = metrics(model, x[train_end:validation_end], y[train_end:validation_end], device)
        trials.append({"seed": seed, "final_loss": losses[-1], **result})
        if best is None or result["sharpe"] > best[0]:
            best = (result["sharpe"], seed, model)
    assert best is not None
    _, selected_seed, selected = best
    test_metrics = metrics(selected, x[validation_end:], y[validation_end:], device)
    deployed, training_losses = train_once(x, y, selected_seed, args.epochs, device)
    tag = args.tag or datetime.now(SHANGHAI).strftime("%Y%m%d")
    target = ROOT / "state" / "exposure-league" / "deepdow" / "candidates" / tag
    target.mkdir(parents=True, exist_ok=True)
    torch.save(deployed.state_dict(), target / "model.pt")
    saturated = all(float(row["average_exposure"]) >= 0.98 for row in trials)
    metadata = {
        "status": "COMPLETED", "framework": "DeepDow 0.2.3 source-compatible subset",
        "candidate_tag": tag, "trained_until": dates[-1], "epochs": args.epochs,
        "selected_seed": selected_seed, "device": device,
        "input_contract": "(batch,3 channels,60 lookback,2 assets)",
        "output_contract": "Softmax weights for stock_proxy and cash",
        "loss": "DeepDow SharpeRatio equation, simple returns, epsilon=1e-4",
        "trials": trials, "test_metrics": test_metrics,
        "diagnostic": "SATURATED_FULL_EXPOSURE" if saturated else "PASS",
        "production_gate_passed": False,
        "training_loss_tail": training_losses[-10:],
        "created_at": datetime.now(SHANGHAI).isoformat(),
    }
    (target / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
