from __future__ import annotations

import hashlib
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
    # QMT serializes this flag as "0"/"1" while Tushare commonly returns an
    # integer.  Persist one canonical schema so switching providers cannot
    # turn the column into an Arrow-incompatible mixed object dtype.
    calendar = calendar.copy()
    calendar["cal_date"] = calendar["cal_date"].astype(str)
    calendar["is_open"] = pd.to_numeric(calendar["is_open"], errors="raise").astype("int64")
    available = set(calendar["cal_date"])
    if trade_date not in available:
        raise ValueError(
            f"official trading calendar does not cover requested date: {trade_date}"
        )
    keys = ["exchange", "cal_date"] if "exchange" in calendar.columns else ["cal_date"]
    snapshot_key = f"{start_date}_{end_date}"
    try:
        store.save_snapshot(
            "trade_calendar", calendar, provider.name, keys, snapshot_key
        )
    except FileExistsError:
        digest = hashlib.sha256(
            calendar.sort_values(keys).to_json(orient="records").encode()
        ).hexdigest()[:12]
        store.save_snapshot(
            "trade_calendar",
            calendar,
            provider.name,
            keys,
            f"{snapshot_key}_revision_{digest}",
        )
    return calendar
