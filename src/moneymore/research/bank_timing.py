from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from ..backtest import run_daily_backtest
from ..config import BacktestConfig
from .bank_model import apply_bank_targets
from .metrics import performance_metrics, slice_equity


class RiskDegreeStrategy(Protocol):
    """Qlib-compatible semantic contract for dynamic market exposure."""

    strategy_id: str

    def risk_degrees(self, market: pd.DataFrame) -> pd.DataFrame:
        """Return date/risk_degree, observed on T and executed on T+1."""


@dataclass(frozen=True)
class FixedRiskDegree:
    risk_degree: float = 0.8
    strategy_id: str = "fixed_80"

    def risk_degrees(self, market: pd.DataFrame) -> pd.DataFrame:
        result = market[["date"]].copy()
        result["risk_degree"] = self.risk_degree
        return result


@dataclass(frozen=True)
class KAMARiskDegree:
    """Long/cash timing using Kaufman's Adaptive Moving Average."""

    er_window: int = 10
    fast_period: int = 2
    slow_period: int = 30
    invested_degree: float = 0.8
    cash_degree: float = 0.0
    strategy_id: str = "kama_10_2_30"

    def risk_degrees(self, market: pd.DataFrame) -> pd.DataFrame:
        frame = market.sort_values("date").copy()
        kama = kaufman_adaptive_moving_average(
            frame["market_index"],
            self.er_window,
            self.fast_period,
            self.slow_period,
        )
        frame["risk_degree"] = np.where(
            frame["market_index"] > kama,
            self.invested_degree,
            self.cash_degree,
        )
        frame.loc[kama.isna(), "risk_degree"] = self.cash_degree
        return frame[["date", "risk_degree"]]


@dataclass(frozen=True)
class VolatilityTargetRiskDegree:
    annual_target: float = 0.12
    lookback: int = 60
    minimum_degree: float = 0.2
    maximum_degree: float = 0.8
    rebalance_days: int = 20
    strategy_id: str = "vol_target_12"

    def risk_degrees(self, market: pd.DataFrame) -> pd.DataFrame:
        frame = market.sort_values("date").copy()
        returns = frame["market_index"].pct_change()
        annual_volatility = returns.rolling(self.lookback).std(ddof=1) * np.sqrt(242)
        raw = (self.annual_target / annual_volatility.replace(0, np.nan)).clip(
            self.minimum_degree, self.maximum_degree
        )
        stepped = (raw / 0.1).round() * 0.1
        scheduled = stepped.where(np.arange(len(frame)) % self.rebalance_days == 0)
        frame["risk_degree"] = scheduled.ffill().fillna(self.minimum_degree)
        return frame[["date", "risk_degree"]]


def kaufman_adaptive_moving_average(
    prices: pd.Series,
    er_window: int = 10,
    fast_period: int = 2,
    slow_period: int = 30,
) -> pd.Series:
    if er_window < 2 or fast_period < 1 or slow_period <= fast_period:
        raise ValueError("invalid KAMA parameters")
    values = prices.astype(float)
    change = values.diff(er_window).abs()
    volatility = values.diff().abs().rolling(er_window).sum()
    efficiency = (change / volatility.replace(0, np.nan)).clip(0, 1)
    fast = 2 / (fast_period + 1)
    slow = 2 / (slow_period + 1)
    smoothing = (efficiency * (fast - slow) + slow) ** 2
    output = pd.Series(np.nan, index=values.index, dtype=float)
    first = smoothing.first_valid_index()
    if first is None:
        return output
    first_position = values.index.get_loc(first)
    output.iloc[first_position] = values.iloc[first_position]
    for position in range(first_position + 1, len(values)):
        previous = output.iloc[position - 1]
        coefficient = smoothing.iloc[position]
        output.iloc[position] = previous + coefficient * (
            values.iloc[position] - previous
        )
    return output


def build_bank_market_index(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "symbol", "signal_close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"bank market index missing columns: {sorted(missing)}")
    frame = bars[["date", "symbol", "signal_close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["symbol", "date"])
    frame["return"] = frame.groupby("symbol")["signal_close"].pct_change()
    market = (
        frame.groupby("date", as_index=False)["return"]
        .mean()
        .sort_values("date")
        .reset_index(drop=True)
    )
    market["market_index"] = (1 + market["return"].fillna(0)).cumprod()
    return market[["date", "market_index"]]


def apply_risk_degree(
    bars_with_targets: pd.DataFrame,
    degrees: pd.DataFrame,
    base_gross_exposure: float = 0.8,
) -> pd.DataFrame:
    if base_gross_exposure <= 0:
        raise ValueError("base_gross_exposure must be positive")
    frame = bars_with_targets.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    risk = degrees.copy()
    risk["date"] = pd.to_datetime(risk["date"])
    frame = frame.merge(risk, on="date", how="left", validate="many_to_one")
    frame["risk_degree"] = frame["risk_degree"].ffill().fillna(0.0)
    frame["base_target"] = frame["target"].astype(float)
    frame["target"] = (
        frame["base_target"] * frame["risk_degree"] / base_gross_exposure
    )
    return frame


def research_bank_timing(
    bars: pd.DataFrame,
    targets: pd.DataFrame,
    config: BacktestConfig,
    train_start: str = "2015-04-30",
    train_end: str = "2021-12-31",
    test_start: str = "2022-01-01",
    test_end: str = "2026-07-27",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = apply_bank_targets(bars, targets)
    market = build_bank_market_index(bars)
    research_config = config.model_copy(update={"max_drawdown": 0.99})
    strategies: list[RiskDegreeStrategy] = [
        FixedRiskDegree(),
        KAMARiskDegree(),
        VolatilityTargetRiskDegree(),
    ]
    reports: list[dict[str, object]] = []
    degree_panels = []
    equity_panels = []
    for strategy in strategies:
        degrees = strategy.risk_degrees(market)
        degrees["strategy"] = strategy.strategy_id
        degree_panels.append(degrees)
        signals = apply_risk_degree(base, degrees)
        result = run_daily_backtest(signals, research_config)
        equity = result.equity.copy()
        equity["strategy"] = strategy.strategy_id
        equity_panels.append(equity)
        for period, start, end in (
            ("sample_in", train_start, train_end),
            ("sample_out", test_start, test_end),
        ):
            reports.append(
                {
                    "strategy": strategy.strategy_id,
                    "period": period,
                    **performance_metrics(slice_equity(result.equity, start, end)),
                    "fills": len(result.fills),
                    "average_risk_degree": float(
                        degrees.loc[
                            (degrees["date"] >= pd.Timestamp(start))
                            & (degrees["date"] <= pd.Timestamp(end)),
                            "risk_degree",
                        ].mean()
                    ),
                }
            )
    return (
        pd.DataFrame(reports),
        pd.concat(degree_panels, ignore_index=True),
        pd.concat(equity_panels, ignore_index=True),
    )
