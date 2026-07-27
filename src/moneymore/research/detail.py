from __future__ import annotations

import pandas as pd

from ..backtest import BacktestResult, run_daily_backtest
from ..config import BacktestConfig
from ..data.research import load_total_return_stock_bars
from ..data.store import ParquetStore
from ..models import Side
from ..strategy import moving_average_trend
from .metrics import performance_metrics, slice_equity


def candidate_result(
    store: ParquetStore,
    symbol: str,
    config: BacktestConfig,
    weight: float,
) -> BacktestResult:
    bars = load_total_return_stock_bars(store, symbol)
    signals = moving_average_trend(
        bars, fast=120, slow=250, price_column="signal_close"
    )
    research_config = config.model_copy(
        update={
            "max_position_weight": weight,
            "max_gross_exposure": weight,
            "max_drawdown": 0.99,
        }
    )
    return run_daily_backtest(signals, research_config)


def allocation_comparison(
    store: ParquetStore,
    symbol: str,
    config: BacktestConfig,
    start_date: str = "2022-01-01",
    end_date: str = "2026-07-24",
) -> pd.DataFrame:
    rows = []
    for weight in (0.1, 0.2, 0.8):
        result = candidate_result(store, symbol, config, weight)
        rows.append(
            {
                "weight": weight,
                **performance_metrics(
                    slice_equity(result.equity, start_date, end_date)
                ),
            }
        )
    return pd.DataFrame(rows)


def trade_ledger(result: BacktestResult) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    entry = None
    for fill in result.fills:
        if fill.side is Side.BUY:
            entry = fill
        elif fill.side is Side.SELL and entry is not None:
            entry_cost = entry.quantity * entry.price + entry.fee
            exit_value = fill.quantity * fill.price - fill.fee
            rows.append(
                {
                    "entry_date": entry.trade_date,
                    "exit_date": fill.trade_date,
                    "quantity": entry.quantity,
                    "entry_price": entry.price,
                    "exit_price": fill.price,
                    "holding_days": (
                        pd.Timestamp(fill.trade_date) - pd.Timestamp(entry.trade_date)
                    ).days,
                    "net_return": exit_value / entry_cost - 1,
                    "fees": entry.fee + fill.fee,
                }
            )
            entry = None
    return pd.DataFrame(rows)


def annual_comparison(
    store: ParquetStore,
    symbol: str,
    config: BacktestConfig,
) -> pd.DataFrame:
    candidate = candidate_result(store, symbol, config, 0.8)
    bars = load_total_return_stock_bars(store, symbol)
    buy_hold_bars = bars.copy()
    buy_hold_bars["target"] = True
    buy_hold_config = config.model_copy(
        update={
            "max_position_weight": 0.8,
            "max_gross_exposure": 0.8,
            "max_drawdown": 0.99,
        }
    )
    buy_hold = run_daily_backtest(buy_hold_bars, buy_hold_config)
    series = {
        "trend_120_250": _equity_series(candidate.equity),
        "cmb_buy_hold_80": _equity_series(buy_hold.equity),
        "csi300_price": _index_series(store, "000300.SH"),
        "csi_bank_price": _index_series(store, "399986.SZ"),
    }
    year_ends = pd.DataFrame(
        {name: values.resample("YE").last() for name, values in series.items()}
    )
    returns = year_ends.pct_change()
    first_year = year_ends.index[0].year
    for name, values in series.items():
        start = values.loc[values.index.year == first_year].iloc[0]
        returns.loc[returns.index[0], name] = year_ends.iloc[0][name] / start - 1
    returns.index = returns.index.year
    returns.index.name = "year"
    return returns.reset_index()


def benchmark_metrics(
    store: ParquetStore,
    start_date: str = "2022-01-01",
    end_date: str = "2026-07-24",
) -> pd.DataFrame:
    rows = []
    for code, name in (
        ("000300.SH", "csi300_price"),
        ("399986.SZ", "csi_bank_price"),
    ):
        values = _index_series(store, code)
        equity = pd.DataFrame({"date": values.index, "equity": values.values})
        rows.append(
            {
                "benchmark": name,
                **performance_metrics(slice_equity(equity, start_date, end_date)),
            }
        )
    return pd.DataFrame(rows)


def _equity_series(equity: pd.DataFrame) -> pd.Series:
    return pd.Series(
        equity["equity"].to_numpy(),
        index=pd.to_datetime(equity["date"]),
        dtype=float,
    )


def _index_series(store: ParquetStore, code: str) -> pd.Series:
    frame = store.read(
        "index_daily",
        columns=["ts_code", "trade_date", "close"],
        filters=[("ts_code", "==", code)],
    ).sort_values("trade_date")
    if frame.empty:
        raise ValueError(f"index data not found: {code}")
    return pd.Series(
        frame["close"].to_numpy(dtype=float),
        index=pd.to_datetime(frame["trade_date"]),
    )
