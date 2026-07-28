from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from .base import (
    Availability,
    FactorCategory,
    FactorDefinition,
    FactorDirection,
    FactorRegistry,
)

QLIB = "Qlib Alpha158 design pattern; MoneyMore native implementation"


def build_default_registry() -> FactorRegistry:
    registry = FactorRegistry()
    for window in (20, 60, 120, 250):
        registry.register(
            _definition(
                f"price_to_ma_{window}",
                FactorCategory.TREND,
                FactorDirection.HIGH,
                f"Adjusted close divided by its {window}-day moving average.",
                ("signal_close",),
                window,
                _price_to_ma(window),
                QLIB,
            )
        )
    for window in (20, 60, 120):
        registry.register(
            _definition(
                f"return_{window}",
                FactorCategory.MOMENTUM,
                FactorDirection.HIGH,
                f"{window}-day adjusted-price return.",
                ("signal_close",),
                window + 1,
                _return(window),
                QLIB,
            )
        )
    registry.register(
        _definition(
            "momentum_252_21",
            FactorCategory.MOMENTUM,
            FactorDirection.HIGH,
            "Twelve-to-one-month momentum, excluding the latest month.",
            ("signal_close",),
            253,
            _momentum_252_21,
            QLIB,
        )
    )
    for window in (20, 60):
        registry.register(
            _definition(
                f"volatility_{window}",
                FactorCategory.VOLATILITY,
                FactorDirection.LOW,
                f"Annualized standard deviation of {window} daily returns.",
                ("signal_close",),
                window + 1,
                _volatility(window),
                QLIB,
            )
        )
    registry.register(
        _definition(
            "downside_volatility_60",
            FactorCategory.VOLATILITY,
            FactorDirection.LOW,
            "Annualized root-mean-square of negative returns over 60 days.",
            ("signal_close",),
            61,
            _downside_volatility(60),
        )
    )
    registry.register(
        _definition(
            "drawdown_120",
            FactorCategory.VOLATILITY,
            FactorDirection.HIGH,
            "Current adjusted price versus its rolling 120-day high.",
            ("signal_close",),
            120,
            _drawdown(120),
        )
    )
    registry.register(
        _definition(
            "log_amount_20",
            FactorCategory.LIQUIDITY,
            FactorDirection.HIGH,
            "Log of 20-day average traded amount.",
            ("amount",),
            20,
            _log_rolling_mean("amount", 20),
            QLIB,
        )
    )
    registry.register(
        _definition(
            "turnover_mean_20",
            FactorCategory.LIQUIDITY,
            FactorDirection.HIGH,
            "Twenty-day average free-float turnover rate.",
            ("turnover_rate",),
            20,
            _rolling_mean("turnover_rate", 20),
        )
    )
    registry.register(
        _definition(
            "book_to_price",
            FactorCategory.VALUE,
            FactorDirection.HIGH,
            "Inverse price-to-book ratio.",
            ("pb",),
            1,
            _safe_inverse("pb"),
        )
    )
    registry.register(
        _definition(
            "earnings_yield",
            FactorCategory.VALUE,
            FactorDirection.HIGH,
            "Inverse positive trailing price-to-earnings ratio.",
            ("pe_ttm",),
            1,
            _positive_inverse("pe_ttm"),
        )
    )
    registry.register(
        _definition(
            "dividend_yield_ttm",
            FactorCategory.VALUE,
            FactorDirection.HIGH,
            "Trailing twelve-month dividend yield as a decimal.",
            ("dv_ttm",),
            1,
            lambda frame: frame["dv_ttm"] / 100.0,
        )
    )
    registry.register(
        _financial_definition(
            "roe", FactorCategory.QUALITY, FactorDirection.HIGH,
            "Reported return on equity.", "roe"
        )
    )
    registry.register(
        _financial_definition(
            "operating_cashflow_per_share",
            FactorCategory.QUALITY,
            FactorDirection.HIGH,
            "Reported operating cash flow per share.",
            "ocfps",
        )
    )
    registry.register(
        _financial_definition(
            "quarterly_profit_growth",
            FactorCategory.GROWTH,
            FactorDirection.HIGH,
            "Reported net-profit year-on-year growth.",
            "netprofit_yoy",
        )
    )
    registry.register(
        _financial_definition(
            "quarterly_revenue_growth",
            FactorCategory.GROWTH,
            FactorDirection.HIGH,
            "Reported quarterly revenue year-on-year growth.",
            "q_sales_yoy",
        )
    )
    return registry


def _definition(
    name: str,
    category: FactorCategory,
    direction: FactorDirection,
    description: str,
    inputs: tuple[str, ...],
    lookback: int,
    calculate: Callable[[pd.DataFrame], pd.Series],
    reference: str = "",
) -> FactorDefinition:
    return FactorDefinition(
        name=name,
        version=1,
        category=category,
        direction=direction,
        description=description,
        inputs=inputs,
        lookback=lookback,
        availability=Availability.MARKET_T_PLUS_1,
        calculate=calculate,
        reference=reference,
    )


def _financial_definition(
    name: str,
    category: FactorCategory,
    direction: FactorDirection,
    description: str,
    column: str,
) -> FactorDefinition:
    return FactorDefinition(
        name=name,
        version=1,
        category=category,
        direction=direction,
        description=description,
        inputs=(column,),
        lookback=1,
        availability=Availability.ANNOUNCEMENT_T_PLUS_1,
        calculate=lambda frame: frame[column],
    )


def _group(frame: pd.DataFrame, values: pd.Series):
    return values.groupby(frame["symbol"], sort=False)


def _return(window: int):
    return lambda frame: frame.groupby("symbol", sort=False)[
        "signal_close"
    ].pct_change(window, fill_method=None)


def _price_to_ma(window: int):
    def calculate(frame: pd.DataFrame) -> pd.Series:
        average = frame.groupby("symbol", sort=False)["signal_close"].transform(
            lambda values: values.rolling(window, min_periods=window).mean()
        )
        return frame["signal_close"] / average

    return calculate


def _momentum_252_21(frame: pd.DataFrame) -> pd.Series:
    grouped = frame.groupby("symbol", sort=False)["signal_close"]
    return grouped.shift(21) / grouped.shift(252) - 1


def _volatility(window: int):
    def calculate(frame: pd.DataFrame) -> pd.Series:
        returns = frame.groupby("symbol", sort=False)["signal_close"].pct_change(
            fill_method=None
        )
        return _group(frame, returns).transform(
            lambda values: values.rolling(window, min_periods=window).std(ddof=1)
        ) * np.sqrt(242)

    return calculate


def _downside_volatility(window: int):
    def calculate(frame: pd.DataFrame) -> pd.Series:
        returns = frame.groupby("symbol", sort=False)["signal_close"].pct_change(
            fill_method=None
        )
        downside_squared = returns.clip(upper=0).pow(2)
        return _group(frame, downside_squared).transform(
            lambda values: values.rolling(window, min_periods=window).mean()
        ).pow(0.5) * np.sqrt(242)

    return calculate


def _drawdown(window: int):
    def calculate(frame: pd.DataFrame) -> pd.Series:
        high = frame.groupby("symbol", sort=False)["signal_close"].transform(
            lambda values: values.rolling(window, min_periods=window).max()
        )
        return frame["signal_close"] / high - 1

    return calculate


def _rolling_mean(column: str, window: int):
    return lambda frame: frame.groupby("symbol", sort=False)[column].transform(
        lambda values: values.rolling(window, min_periods=window).mean()
    )


def _log_rolling_mean(column: str, window: int):
    def calculate(frame: pd.DataFrame) -> pd.Series:
        values = _rolling_mean(column, window)(frame)
        return np.log(values.where(values > 0))

    return calculate


def _safe_inverse(column: str):
    return lambda frame: 1.0 / frame[column].where(frame[column] != 0)


def _positive_inverse(column: str):
    return lambda frame: 1.0 / frame[column].where(frame[column] > 0)
