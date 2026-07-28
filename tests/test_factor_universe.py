import pandas as pd

from moneymore.factors import (
    UniverseDefinition,
    build_historical_membership,
    monthly_rebalance_dates,
)


def test_membership_includes_then_removes_delisted_stock_without_hindsight():
    instruments = pd.DataFrame(
        [
            {
                "ts_code": "OLD", "industry": "bank", "list_date": "20200101",
                "delist_date": "20250315", "list_status": "D",
            },
            {
                "ts_code": "NEW", "industry": "bank", "list_date": "20241201",
                "delist_date": None, "list_status": "L",
            },
            {
                "ts_code": "OTHER", "industry": "power", "list_date": "20200101",
                "delist_date": None, "list_status": "L",
            },
        ]
    )
    dates = pd.bdate_range("2025-01-01", "2025-04-30")
    bars = pd.DataFrame(
        [
            {"ts_code": symbol, "trade_date": date.strftime("%Y%m%d"), "amount": 200.0}
            for symbol in ("OLD", "NEW", "OTHER")
            for date in dates
        ]
    )
    rebalance = monthly_rebalance_dates(bars, "20250101", "20250430")
    definition = UniverseDefinition(
        "banks",
        ("bank",),
        minimum_listing_days=30,
        minimum_average_amount=100,
        liquidity_lookback=5,
    )

    result = build_historical_membership(
        instruments, bars, rebalance, definition
    )

    january = set(result.loc[result["date"].dt.month == 1, "ts_code"])
    april = set(result.loc[result["date"].dt.month == 4, "ts_code"])
    assert january == {"OLD", "NEW"}
    assert april == {"NEW"}
    assert not result["classification_point_in_time"].any()


def test_liquidity_filter_uses_trailing_data_known_at_rebalance():
    instruments = pd.DataFrame(
        [
            {
                "ts_code": "LIQ", "industry": "bank", "list_date": "20200101",
                "delist_date": None, "list_status": "L",
            },
            {
                "ts_code": "DRY", "industry": "bank", "list_date": "20200101",
                "delist_date": None, "list_status": "L",
            },
        ]
    )
    dates = pd.bdate_range("2025-01-01", "2025-02-28")
    bars = pd.DataFrame(
        [
            {
                "ts_code": symbol,
                "trade_date": date.strftime("%Y%m%d"),
                "amount": 200.0 if symbol == "LIQ" else 20.0,
            }
            for symbol in ("LIQ", "DRY")
            for date in dates
        ]
    )

    result = build_historical_membership(
        instruments,
        bars,
        monthly_rebalance_dates(bars, "20250101", "20250228"),
        UniverseDefinition(
            "banks", ("bank",), minimum_listing_days=1,
            minimum_average_amount=100, liquidity_lookback=5,
        ),
    )

    assert set(result["ts_code"]) == {"LIQ"}
