from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..backtest import BacktestResult, run_daily_backtest
from ..config import BacktestConfig
from ..data.research import load_total_return_stock_bars
from ..data.store import ParquetStore
from ..strategy import moving_average_trend
from .metrics import performance_metrics, slice_equity


@dataclass(frozen=True)
class TrendSpecification:
    name: str
    fast: int
    slow: int


PRE_REGISTERED_TRENDS = (
    TrendSpecification("trend_20_120", 20, 120),
    TrendSpecification("trend_60_200", 60, 200),
    TrendSpecification("trend_120_250", 120, 250),
)

ROBUSTNESS_TRENDS = (
    TrendSpecification("trend_100_220", 100, 220),
    TrendSpecification("trend_120_220", 120, 220),
    TrendSpecification("trend_120_250", 120, 250),
    TrendSpecification("trend_120_280", 120, 280),
    TrendSpecification("trend_150_250", 150, 250),
)


def research_single_stock(
    store: ParquetStore,
    symbol: str,
    config: BacktestConfig,
    train_start: str = "2015-01-01",
    train_end: str = "2021-12-31",
    test_start: str = "2022-01-01",
    test_end: str = "2026-07-24",
) -> pd.DataFrame:
    bars = load_total_return_stock_bars(store, symbol)
    sleeve_config = config.model_copy(
        update={
            "max_position_weight": 0.8,
            "max_gross_exposure": 0.8,
            "max_drawdown": 0.99,
        }
    )
    results: list[dict[str, object]] = []

    buy_hold = bars.copy()
    buy_hold["target"] = True
    benchmark = run_daily_backtest(buy_hold, sleeve_config)
    results.extend(
        _period_rows(
            "buy_hold_80",
            benchmark,
            train_start,
            train_end,
            test_start,
            test_end,
            fills=len(benchmark.fills),
            exposure=0.8,
        )
    )

    for specification in PRE_REGISTERED_TRENDS:
        signals = moving_average_trend(
            bars,
            fast=specification.fast,
            slow=specification.slow,
            price_column="signal_close",
        )
        result = run_daily_backtest(signals, sleeve_config)
        results.extend(
            _period_rows(
                specification.name,
                result,
                train_start,
                train_end,
                test_start,
                test_end,
                fills=len(result.fills),
                exposure=float(signals["target"].mean() * 0.8),
            )
        )
    return pd.DataFrame(results)


def robustness_single_stock(
    store: ParquetStore,
    symbol: str,
    config: BacktestConfig,
    test_start: str = "2022-01-01",
    test_end: str = "2026-07-24",
) -> pd.DataFrame:
    bars = load_total_return_stock_bars(store, symbol)
    sleeve_config = config.model_copy(
        update={
            "max_position_weight": 0.8,
            "max_gross_exposure": 0.8,
            "max_drawdown": 0.99,
        }
    )
    rows: list[dict[str, object]] = []
    for specification in ROBUSTNESS_TRENDS:
        signals = moving_average_trend(
            bars,
            fast=specification.fast,
            slow=specification.slow,
            price_column="signal_close",
        )
        result = run_daily_backtest(signals, sleeve_config)
        rows.append(
            _robustness_row(
                specification.name,
                "base",
                result,
                test_start,
                test_end,
            )
        )

    base_signals = moving_average_trend(
        bars, fast=120, slow=250, price_column="signal_close"
    )
    expensive = sleeve_config.model_copy(
        update={"commission_rate": 0.0005, "slippage_bps": 10.0}
    )
    rows.append(
        _robustness_row(
            "trend_120_250",
            "cost_10bps_commission5bp",
            run_daily_backtest(base_signals, expensive),
            test_start,
            test_end,
        )
    )
    for delay in (2, 3):
        delayed = base_signals.copy()
        delayed["target"] = delayed.groupby("symbol")["target"].shift(
            delay - 1, fill_value=False
        )
        rows.append(
            _robustness_row(
                "trend_120_250",
                f"execution_delay_{delay}d",
                run_daily_backtest(delayed, sleeve_config),
                test_start,
                test_end,
            )
        )
    return pd.DataFrame(rows)


def _period_rows(
    strategy: str,
    result: BacktestResult,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    fills: int,
    exposure: float,
) -> list[dict[str, object]]:
    rows = []
    for period, start, end in (
        ("in_sample", train_start, train_end),
        ("out_of_sample", test_start, test_end),
        ("full", train_start, test_end),
    ):
        metrics = performance_metrics(slice_equity(result.equity, start, end))
        rows.append(
            {
                "strategy": strategy,
                "period": period,
                **metrics,
                "fills_full": fills,
                "average_exposure": exposure,
            }
        )
    return rows


def _robustness_row(
    strategy: str,
    scenario: str,
    result: BacktestResult,
    start_date: str,
    end_date: str,
) -> dict[str, object]:
    return {
        "strategy": strategy,
        "scenario": scenario,
        **performance_metrics(slice_equity(result.equity, start_date, end_date)),
        "fills_full": len(result.fills),
    }
