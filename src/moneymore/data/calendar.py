from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from .provider import MarketDataProvider
from .quality import validate_calendar
from .store import ParquetStore


def sync_trading_calendar(
    provider: MarketDataProvider,
    store: ParquetStore,
    trade_date: str,
    history_days: int = 31,
    future_days: int = 90,
) -> pd.DataFrame:
    """Persist an official calendar window before any trading decision."""
    current = datetime.strptime(trade_date, "%Y%m%d").replace(tzinfo=UTC)
    start_date = (current - timedelta(days=history_days)).strftime("%Y%m%d")
    end_date = (current + timedelta(days=future_days)).strftime("%Y%m%d")
    calendar = provider.trading_calendar(start_date, end_date)
    validate_calendar(calendar)
    available = set(calendar["cal_date"].astype(str))
    if trade_date not in available:
        raise ValueError(
            f"official trading calendar does not cover requested date: {trade_date}"
        )
    store.save_snapshot(
        "trade_calendar",
        calendar,
        provider.name,
        ["exchange", "cal_date"]
        if "exchange" in calendar.columns
        else ["cal_date"],
        f"{start_date}_{end_date}",
    )
    return calendar
