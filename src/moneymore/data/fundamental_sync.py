from __future__ import annotations

from datetime import UTC, datetime

from .provider import MarketDataProvider
from .quality import (
    validate_daily_basic,
    validate_dividends,
    validate_financial_indicators,
)
from .store import ParquetStore


def sync_stock_fundamentals(
    provider: MarketDataProvider,
    store: ParquetStore,
    symbol: str,
    start_date: str,
    end_date: str,
) -> dict[str, int]:
    """Persist point-in-time valuation, dividend and financial data for one stock."""
    key = symbol.replace(".", "_")
    daily = provider.daily_basic(symbol, start_date, end_date)
    validate_daily_basic(daily)
    store.save_snapshot(
        "daily_basic",
        daily,
        provider.name,
        ["ts_code", "trade_date"],
        f"{key}_{start_date}_{end_date}",
    )

    dividends = provider.dividends(symbol)
    validate_dividends(dividends)
    # Dividend plans evolve from proposal to implementation. Keep each published
    # state instead of collapsing an entire reporting period to its final state.
    captured = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    store.save_snapshot(
        "dividend",
        dividends,
        provider.name,
        ["ts_code", "end_date", "ann_date", "div_proc"],
        f"{key}_{captured}",
    )

    indicators = provider.financial_indicators(symbol, start_date, end_date)
    validate_financial_indicators(indicators)
    store.save_snapshot(
        "fina_indicator",
        indicators,
        provider.name,
        ["ts_code", "ann_date", "end_date"],
        f"{key}_{start_date}_{end_date}_{captured}",
    )
    return {
        "daily_basic": len(daily),
        "dividend": len(dividends),
        "fina_indicator": len(indicators),
    }
