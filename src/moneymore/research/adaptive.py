from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest import run_daily_backtest
from ..config import BacktestConfig
from ..data.research import load_point_in_time_features, load_total_return_stock_bars
from ..data.store import ParquetStore
from ..strategy import moving_average_trend
from .metrics import performance_metrics, slice_equity


def research_weighted_strategies(
    store: ParquetStore,
    symbol: str,
    config: BacktestConfig,
    train_start: str = "2015-01-01",
    train_end: str = "2021-12-31",
    test_start: str = "2022-01-01",
    test_end: str = "2026-07-24",
) -> pd.DataFrame:
    """Compare pre-declared core/tactical allocations without parameter search."""
    bars = load_total_return_stock_bars(store, symbol)
    research_config = config.model_copy(
        update={
            "max_position_weight": 0.8,
            "max_gross_exposure": 0.8,
            "max_drawdown": 0.99,
        }
    )
    variants = {
        "buy_hold_80": _constant_weight(bars, 0.8),
        "binary_ma120_250": _binary_trend(bars, 120, 250, 0.8),
        "core40_ma120_250_40": _core_plus_trend(bars, 0.4, 0.4, 120, 250),
        "core60_ma120_250_20": _core_plus_trend(bars, 0.6, 0.2, 120, 250),
        "core40_ma20_120_40": _core_plus_trend(bars, 0.4, 0.4, 20, 120),
        "vol_target_12": _volatility_target(bars, annual_target=0.12),
    }
    try:
        feature_bars = load_point_in_time_features(store, symbol)
    except FileNotFoundError:
        feature_bars = None
    if feature_bars is not None:
        variants["dividend_value_quality"] = _dividend_value_quality(feature_bars)
    rows: list[dict[str, object]] = []
    for strategy, signals in variants.items():
        result = run_daily_backtest(signals, research_config)
        for period, start, end in (
            ("in_sample", train_start, train_end),
            ("out_of_sample", test_start, test_end),
        ):
            rows.append(
                {
                    "strategy": strategy,
                    "period": period,
                    **performance_metrics(slice_equity(result.equity, start, end)),
                    "fills_full": len(result.fills),
                    "average_target": float(signals["target"].mean()),
                }
            )
    return pd.DataFrame(rows)


def _dividend_value_quality(bars: pd.DataFrame) -> pd.DataFrame:
    """Monthly, slow-moving allocation with expanding point-in-time ranks.

    Volatility controls the base exposure; dividend yield, PB and reported
    operating cash flow only tilt it by +/-10 percentage points.
    """
    signals = _volatility_target(bars, annual_target=0.12)
    dividend_rank = bars["dv_ttm"].expanding(min_periods=242).rank(pct=True)
    pb_rank = bars["pb"].expanding(min_periods=242).rank(pct=True)
    quality = (bars.get("roe", pd.Series(index=bars.index, dtype=float)) > 0) & (
        bars.get("ocfps", pd.Series(index=bars.index, dtype=float)) > 0
    )
    tilt = pd.Series(0.0, index=bars.index)
    tilt += (dividend_rank >= 0.6).astype(float) * 0.1
    tilt -= (dividend_rank <= 0.2).astype(float) * 0.1
    tilt += (pb_rank <= 0.4).astype(float) * 0.1
    tilt -= (pb_rank >= 0.8).astype(float) * 0.1
    tilt -= (~quality.fillna(False)).astype(float) * 0.1
    observation = signals.groupby("symbol").cumcount()
    scheduled = (signals["target"] + tilt).clip(0.2, 0.8).where(
        observation % 20 == 0
    )
    signals["target"] = scheduled.groupby(signals["symbol"]).ffill().fillna(0.3)
    return signals


def _constant_weight(bars: pd.DataFrame, weight: float) -> pd.DataFrame:
    signals = bars.copy()
    signals["target"] = weight
    return signals


def _binary_trend(
    bars: pd.DataFrame, fast: int, slow: int, weight: float
) -> pd.DataFrame:
    signals = moving_average_trend(
        bars,
        fast=fast,
        slow=slow,
        price_column="signal_close",
    )
    signals["target"] = signals["target"].astype(float) * weight
    return signals


def _core_plus_trend(
    bars: pd.DataFrame,
    core_weight: float,
    tactical_weight: float,
    fast: int,
    slow: int,
) -> pd.DataFrame:
    signals = moving_average_trend(
        bars,
        fast=fast,
        slow=slow,
        price_column="signal_close",
    )
    signals["target"] = core_weight + (
        signals["target"].astype(float) * tactical_weight
    )
    return signals


def _volatility_target(
    bars: pd.DataFrame,
    annual_target: float,
    lookback: int = 60,
    minimum_weight: float = 0.3,
    maximum_weight: float = 0.8,
    rebalance_days: int = 20,
) -> pd.DataFrame:
    signals = bars.sort_values(["symbol", "date"]).copy()
    returns = signals.groupby("symbol")["signal_close"].pct_change()
    annual_volatility = returns.groupby(signals["symbol"]).transform(
        lambda values: values.rolling(lookback).std(ddof=1) * np.sqrt(242)
    )
    raw_weight = (annual_target / annual_volatility.replace(0, np.nan)).clip(
        lower=minimum_weight,
        upper=maximum_weight,
    )
    stepped_weight = (raw_weight / 0.1).round() * 0.1
    observation = signals.groupby("symbol").cumcount()
    scheduled = stepped_weight.where(observation % rebalance_days == 0)
    signals["target"] = scheduled.groupby(signals["symbol"]).ffill().fillna(
        minimum_weight
    )
    return signals
