from pathlib import Path

import pandas as pd

from moneymore.data.research import (
    load_adjusted_stock_bars,
    load_point_in_time_features,
)
from moneymore.data.store import ParquetStore


def test_research_bars_keep_raw_execution_price_and_adjust_signal_price(tmp_path: Path):
    store = ParquetStore(tmp_path)
    daily = pd.DataFrame(
        [
            {"ts_code": "AAA", "trade_date": "20250102", "open": 10.0, "close": 10.0},
            {"ts_code": "AAA", "trade_date": "20250103", "open": 5.0, "close": 5.0},
        ]
    )
    factors = pd.DataFrame(
        [
            {"ts_code": "AAA", "trade_date": "20250102", "adj_factor": 1.0},
            {"ts_code": "AAA", "trade_date": "20250103", "adj_factor": 2.0},
        ]
    )
    store.save_snapshot("daily", daily, "fake", ["ts_code", "trade_date"], "bars")
    store.save_snapshot(
        "adj_factor", factors, "fake", ["ts_code", "trade_date"], "factors"
    )

    result = load_adjusted_stock_bars(store, "AAA")

    assert result["open"].tolist() == [10.0, 5.0]
    assert result["signal_close"].tolist() == [5.0, 5.0]


def test_point_in_time_features_lag_market_data_and_financial_announcements(
    tmp_path: Path,
):
    store = ParquetStore(tmp_path)
    dates = ["20250102", "20250103", "20250106"]
    daily = pd.DataFrame(
        [
            {
                "ts_code": "AAA", "trade_date": date, "open": 10.0,
                "high": 10.0, "low": 10.0, "close": 10.0,
            }
            for date in dates
        ]
    )
    factors = pd.DataFrame(
        [{"ts_code": "AAA", "trade_date": date, "adj_factor": 1.0} for date in dates]
    )
    basic = pd.DataFrame(
        [
            {
                "ts_code": "AAA", "trade_date": date, "dv_ttm": value,
                "pb": 1.0, "pe_ttm": 8.0,
            }
            for date, value in zip(dates, [1.0, 2.0, 3.0], strict=True)
        ]
    )
    financial = pd.DataFrame(
        [
            {
                "ts_code": "AAA", "ann_date": "20250103",
                "end_date": "20241231", "roe": 12.0, "ocfps": 1.2,
            }
        ]
    )
    store.save_snapshot("daily", daily, "fake", ["ts_code", "trade_date"], "d")
    store.save_snapshot(
        "adj_factor", factors, "fake", ["ts_code", "trade_date"], "a"
    )
    store.save_snapshot(
        "daily_basic", basic, "fake", ["ts_code", "trade_date"], "b"
    )
    store.save_snapshot(
        "fina_indicator",
        financial,
        "fake",
        ["ts_code", "ann_date", "end_date"],
        "f",
    )

    result = load_point_in_time_features(store, "AAA")

    assert pd.isna(result.loc[0, "dv_ttm"])
    assert result.loc[1, "dv_ttm"] == 1.0
    assert pd.isna(result.loc[1, "roe"])
    assert result.loc[2, "roe"] == 12.0
