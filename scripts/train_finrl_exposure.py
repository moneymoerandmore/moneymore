from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from stable_baselines3 import PPO

from moneymore.data.store import ParquetStore
from moneymore.finrl_exposure import TotalExposureEnv, evaluate_model

ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def proxy_returns(root: Path, lookback: int = 800) -> tuple[list[str], np.ndarray]:
    store = ParquetStore(root / "data")
    universe = store.read("strategy_universe")
    effective = str(universe["effective_date"].astype(str).max())
    symbols = sorted(
        universe.loc[universe["effective_date"].astype(str) == effective, "symbol"]
        .astype(str).unique()
    )
    dates = sorted(store.read("daily", columns=["trade_date"])["trade_date"].astype(str).unique())
    start = dates[max(0, len(dates) - lookback)]
    bars = store.read(
        "daily", columns=["ts_code", "trade_date", "close", "pre_close"],
        filters=[("trade_date", ">=", start), ("ts_code", "in", symbols)],
    )
    bars["trade_date"] = bars["trade_date"].astype(str)
    bars["return"] = bars["close"].astype(float) / bars["pre_close"].astype(float) - 1
    daily = bars.groupby("trade_date")["return"].mean().dropna().sort_index()
    return daily.index.tolist(), daily.to_numpy(dtype=np.float32)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    parser.add_argument("--timesteps", type=int, default=20_000)
    parser.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cpu")
    args = parser.parse_args()
    dates, returns = proxy_returns(ROOT)
    if len(returns) < 240:
        raise RuntimeError("FinRL training requires at least 240 market observations")
    train_end = int(len(returns) * 0.70)
    validation_end = int(len(returns) * 0.85)
    train = returns[:train_end]
    validation = returns[train_end - 20 : validation_end]
    test = returns[validation_end - 20 :]
    tag = args.tag or datetime.now(SHANGHAI).strftime("%Y%m%d")
    target = ROOT / "state" / "exposure-league" / "finrl" / "candidates" / tag
    target.mkdir(parents=True, exist_ok=True)
    seeds = (17, 42, 101)
    trials = []
    best = None
    for seed in seeds:
        model = PPO(
            "MlpPolicy", TotalExposureEnv(train), seed=seed, verbose=0,
            n_steps=256, batch_size=128, learning_rate=0.0003,
            policy_kwargs={"net_arch": [64, 64]}, device=args.device,
        )
        model.learn(total_timesteps=args.timesteps)
        metrics = evaluate_model(model, validation)
        trials.append({"seed": seed, **metrics})
        if best is None or metrics["sharpe"] > best[0]:
            best = (metrics["sharpe"], seed, model)
    assert best is not None
    _, selected_seed, selected_model = best
    test_metrics = evaluate_model(selected_model, test)
    degenerate = (
        float(test_metrics["full_exposure_ratio"]) >= 0.90
        and float(test_metrics["exposure_p50"]) >= 0.95
    )
    # After the untouched test report is frozen, retrain the selected configuration
    # on all information available today. This deployed model is only used forward.
    deployed_model = PPO(
        "MlpPolicy", TotalExposureEnv(returns), seed=selected_seed, verbose=0,
        n_steps=256, batch_size=128, learning_rate=0.0003,
        policy_kwargs={"net_arch": [64, 64]}, device=args.device,
    )
    deployed_model.learn(total_timesteps=args.timesteps)
    deployed_model.save(target / "model")
    metadata = {
        "status": "COMPLETED",
        "framework": "FinRL-style train-test-trade + Stable-Baselines3",
        "algorithm": "PPO",
        "candidate_tag": tag,
        "selection_trained_until": dates[train_end - 1],
        "validated_until": dates[validation_end - 1],
        "tested_until": dates[-1],
        "trained_until": dates[-1],
        "selected_seed": selected_seed,
        "timesteps": args.timesteps,
        "device": str(selected_model.device),
        "feature_contract": "20 lagged market-proxy returns, volatility, drawdown, prior exposure",
        "action_contract": "one scalar target_exposure in [0,1]",
        "trials": trials,
        "test_metrics": test_metrics,
        "diagnostic": "DEGENERATE_FULL_EXPOSURE" if degenerate else "PASS",
        "production_gate_passed": False,
        "created_at": datetime.now(SHANGHAI).isoformat(),
    }
    (target / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
