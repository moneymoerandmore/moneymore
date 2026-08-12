from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data.research import load_total_return_stock_bars
from .data.store import ParquetStore


def dynamic_target_exposure(
    store: ParquetStore,
    symbols: Iterable[str],
    cutoff: str | pd.Timestamp,
    policy: dict[str, Any],
    *,
    previous_exposure: float | None = None,
    loader: Callable[[ParquetStore, str], pd.DataFrame] = load_total_return_stock_bars,
) -> dict[str, float | int | str | None]:
    """Resolve gross exposure using information available at cutoff."""
    method = str(policy.get("method", "fully_invested"))
    if method == "fully_invested":
        target = float(policy.get("target_exposure", 1.0))
        return {
            "method": method,
            "target_gross_exposure": target,
            "raw_target_exposure": target,
            "realized_volatility": None,
            "annual_target": None,
            "lookback": 0,
            "observations": 0,
            "minimum_exposure": target,
            "maximum_exposure": target,
            "previous_exposure": previous_exposure,
        }
    if method != "selected_portfolio_volatility_target":
        raise ValueError(f"unsupported exposure policy: {method}")
    lookback = int(policy.get("lookback", 60))
    minimum_observations = int(policy.get("minimum_observations", 20))
    annual_target = float(policy.get("annual_target", 0.12))
    minimum = float(policy.get("minimum_exposure", 0.20))
    maximum = float(policy.get("maximum_exposure", 0.80))
    step = float(policy.get("rounding_step", 0.05))
    maximum_change = float(policy.get("maximum_daily_change", 0.10))
    fallback = float(policy.get("fallback_exposure", 0.50))
    cutoff_ts = pd.Timestamp(cutoff)
    returns = []
    for symbol in sorted(set(symbols)):
        bars = loader(store, symbol).copy()
        bars["date"] = pd.to_datetime(bars["date"])
        eligible = bars.loc[bars["date"] <= cutoff_ts].sort_values("date").tail(
            lookback + 1
        )
        if len(eligible) < 2:
            continue
        returns.append(
            eligible.set_index("date")["close"].astype(float).pct_change().rename(symbol)
        )
    panel = pd.concat(returns, axis=1).tail(lookback) if returns else pd.DataFrame()
    portfolio_returns = panel.mean(axis=1, skipna=True).dropna()
    realized_volatility = (
        float(portfolio_returns.std(ddof=1) * np.sqrt(242))
        if len(portfolio_returns) >= minimum_observations
        else None
    )
    raw = (
        fallback
        if not realized_volatility or realized_volatility <= 0
        else annual_target / realized_volatility
    )
    bounded = min(max(raw, minimum), maximum)
    rounded = round(bounded / step) * step if step > 0 else bounded
    if previous_exposure is not None:
        rounded = min(
            max(rounded, previous_exposure - maximum_change),
            previous_exposure + maximum_change,
        )
    exposure = min(max(rounded, minimum), maximum)
    return {
        "method": method,
        "target_gross_exposure": float(round(exposure, 10)),
        "raw_target_exposure": float(raw),
        "realized_volatility": realized_volatility,
        "annual_target": annual_target,
        "lookback": lookback,
        "observations": len(portfolio_returns),
        "minimum_exposure": minimum,
        "maximum_exposure": maximum,
        "previous_exposure": previous_exposure,
    }


def previous_report_exposure(report_dir: Path, trade_date: str) -> float | None:
    import json

    candidates = []
    if report_dir.exists():
        for path in report_dir.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if str(payload.get("trade_date", "")) < trade_date:
                candidates.append(payload)
    if not candidates:
        return None
    latest = max(candidates, key=lambda row: str(row.get("trade_date", "")))
    value = latest.get("target_gross_exposure")
    return float(value) if value is not None else None
