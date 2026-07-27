from __future__ import annotations

import math

import numpy as np
import pandas as pd


def performance_metrics(equity: pd.DataFrame) -> dict[str, float]:
    if len(equity) < 2:
        raise ValueError("at least two equity observations are required")
    values = equity["equity"].astype(float)
    returns = values.pct_change().dropna()
    years = len(returns) / 242
    total_return = float(values.iloc[-1] / values.iloc[0] - 1)
    cagr = float((1 + total_return) ** (1 / years) - 1) if years > 0 else math.nan
    volatility = float(returns.std(ddof=1) * np.sqrt(242))
    sharpe = (
        float(returns.mean() / returns.std(ddof=1) * np.sqrt(242))
        if returns.std(ddof=1) > 0
        else math.nan
    )
    max_drawdown = float((values / values.cummax() - 1).min())
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else math.nan
    return {
        "total_return": total_return,
        "cagr": cagr,
        "volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
    }


def slice_equity(
    equity: pd.DataFrame, start_date: str, end_date: str
) -> pd.DataFrame:
    dates = pd.to_datetime(equity["date"])
    selected = equity.loc[
        (dates >= pd.Timestamp(start_date)) & (dates <= pd.Timestamp(end_date))
    ].copy()
    if len(selected) < 2:
        raise ValueError(f"insufficient equity data in {start_date}..{end_date}")
    return selected.reset_index(drop=True)
