from __future__ import annotations

from .provider import MarketDataProvider
from .quality import validate_range_bars, validate_stock_limits
from .store import ParquetStore


def sync_research_reference(
    provider: MarketDataProvider,
    store: ParquetStore,
    symbol: str,
    index_codes: tuple[str, ...],
    start_date: str,
    end_date: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for index_code in index_codes:
        frame = provider.index_daily(index_code, start_date, end_date)
        validate_range_bars(frame, f"index_daily[{index_code}]")
        key = index_code.replace(".", "_")
        store.save_snapshot(
            "index_daily",
            frame,
            provider.name,
            ["ts_code", "trade_date"],
            f"{key}_{start_date}_{end_date}",
        )
        counts[index_code] = len(frame)

    limits = provider.stock_limits(symbol, start_date, end_date)
    validate_stock_limits(limits)
    key = symbol.replace(".", "_")
    store.save_snapshot(
        "stock_limits",
        limits,
        provider.name,
        ["ts_code", "trade_date"],
        f"{key}_{start_date}_{end_date}",
    )
    counts["stock_limits"] = len(limits)

    suspensions = provider.suspensions(symbol, start_date, end_date)
    if suspensions is None or suspensions.empty:
        counts["suspensions"] = 0
    else:
        store.save_snapshot(
            "suspensions",
            suspensions,
            provider.name,
            ["ts_code", "trade_date", "suspend_type"],
            f"{key}_{start_date}_{end_date}",
        )
        counts["suspensions"] = len(suspensions)
    return counts
