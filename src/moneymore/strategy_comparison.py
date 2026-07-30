from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def build_fair_comparison(
    histories: dict[str, pd.DataFrame],
    *,
    minimum_observation_days: int = 20,
    target_volatility: float = 0.10,
) -> dict[str, Any]:
    """Compare accounts only on dates observed by every account."""
    prepared = {
        account_id: _prepare_history(history)
        for account_id, history in histories.items()
    }
    if not prepared or any(history.empty for history in prepared.values()):
        common_dates: list[str] = []
    else:
        common_dates = sorted(
            set.intersection(*(set(history["trade_date"]) for history in prepared.values()))
        )

    aligned_histories: dict[str, list[dict[str, Any]]] = {}
    metrics: list[dict[str, Any]] = []
    for account_id, history in prepared.items():
        aligned = history[history["trade_date"].isin(common_dates)].copy()
        aligned = aligned.sort_values("trade_date").reset_index(drop=True)
        if not aligned.empty:
            aligned["normalized_nav"] = aligned["equity"] / aligned["equity"].iloc[0]
            aligned["daily_return"] = aligned["normalized_nav"].pct_change().fillna(0.0)
        aligned_histories[account_id] = _records(aligned)
        metrics.append(_performance(account_id, aligned, target_volatility))

    common_days = len(common_dates)
    return {
        "protocol": {
            "date_alignment": "INTERSECTION",
            "nav_base": 1.0,
            "annualization_days": TRADING_DAYS,
            "target_volatility": target_volatility,
            "minimum_observation_days": minimum_observation_days,
            "cost_basis": "ACCOUNT_NET_EQUITY",
        },
        "common_start_date": common_dates[0] if common_dates else None,
        "common_end_date": common_dates[-1] if common_dates else None,
        "common_observation_days": common_days,
        "ready": common_days >= minimum_observation_days,
        "status": "READY" if common_days >= minimum_observation_days else "COLLECTING_EVIDENCE",
        "metrics": metrics,
        "histories": aligned_histories,
    }


def _prepare_history(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty or not {"trade_date", "equity"}.issubset(history.columns):
        return pd.DataFrame(columns=["trade_date", "equity", "gross_exposure"])
    result = history.copy()
    result["trade_date"] = result["trade_date"].astype(str)
    result["equity"] = pd.to_numeric(result["equity"], errors="coerce")
    if "gross_exposure" not in result:
        market_value = pd.to_numeric(
            result.get("market_value", pd.Series(index=result.index, dtype=float)),
            errors="coerce",
        )
        result["gross_exposure"] = market_value / result["equity"].replace(0, np.nan)
    else:
        result["gross_exposure"] = pd.to_numeric(result["gross_exposure"], errors="coerce")
    return (
        result.dropna(subset=["trade_date", "equity"])
        .drop_duplicates("trade_date", keep="last")
        .sort_values("trade_date")
    )


def _performance(
    account_id: str,
    history: pd.DataFrame,
    target_volatility: float,
) -> dict[str, Any]:
    if history.empty:
        return {
            "account_id": account_id,
            "observation_days": 0,
            "total_return": None,
            "annualized_volatility": None,
            "sharpe": None,
            "max_drawdown": None,
            "average_gross_exposure": None,
            "risk_normalized_return": None,
        }
    nav = pd.to_numeric(history["normalized_nav"], errors="coerce").dropna()
    returns = nav.pct_change().dropna()
    drawdown = nav / nav.cummax() - 1
    volatility = (
        float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS))
        if len(returns) > 1
        else None
    )
    sharpe = (
        float(returns.mean() / returns.std(ddof=1) * np.sqrt(TRADING_DAYS))
        if len(returns) > 1 and returns.std(ddof=1) > 0
        else None
    )
    risk_normalized_return = None
    if volatility is not None and volatility > 0:
        risk_normalized_return = float(
            (1.0 + returns * (target_volatility / volatility)).prod() - 1.0
        )
    exposure = pd.to_numeric(history["gross_exposure"], errors="coerce")
    return {
        "account_id": account_id,
        "observation_days": len(nav),
        "total_return": float(nav.iloc[-1] - 1.0),
        "annualized_volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()),
        "average_gross_exposure": float(exposure.mean()) if exposure.notna().any() else None,
        "risk_normalized_return": risk_normalized_return,
    }


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return frame.replace({np.nan: None}).to_dict("records")
