from pathlib import Path

import pandas as pd

from moneymore.data.health import run_data_health_checks
from moneymore.data.store import ParquetStore


def test_data_health_blocks_stale_target_market_data(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "data")
    symbols = {"A", "B"}
    store.merge_curated(
        "trade_calendar",
        [pd.DataFrame({"cal_date": ["20260105", "20260106"], "is_open": ["1", "1"]})],
        ["cal_date"],
    )
    daily = pd.DataFrame(
        [
            {"ts_code": "A", "trade_date": "20260106", "open": 10, "high": 11, "low": 9, "close": 10},
            {"ts_code": "B", "trade_date": "20260105", "open": 10, "high": 11, "low": 9, "close": 10},
        ]
    )
    store.merge_curated("daily", [daily], ["ts_code", "trade_date"])
    store.merge_curated(
        "adj_factor",
        [daily[["ts_code", "trade_date"]].assign(adj_factor=1.0)],
        ["ts_code", "trade_date"],
    )
    store.merge_curated(
        "daily_basic",
        [pd.DataFrame({"ts_code": ["A", "B"], "trade_date": ["20260105", "20260105"]})],
        ["ts_code", "trade_date"],
    )
    store.merge_curated(
        "fina_indicator",
        [pd.DataFrame({"ts_code": ["A", "B"], "ann_date": ["20250101", "20250101"]})],
        ["ts_code", "ann_date"],
    )
    store.merge_curated(
        "dividend",
        [
            pd.DataFrame(
                {
                    "ts_code": ["A", "B"],
                    "end_date": ["20241231", "20241231"],
                    "ann_date": ["20250101", "20250101"],
                    "div_proc": ["实施", "实施"],
                }
            )
        ],
        ["ts_code", "end_date", "ann_date", "div_proc"],
    )
    store.merge_curated(
        "sector_model_scores",
        [pd.DataFrame({"date": ["20260106", "20260106"], "symbol": ["A", "B"]})],
        ["date", "symbol"],
    )

    report = run_data_health_checks(store, "20260106", symbols)

    assert report.status == "BLOCKED"
    stale = next(row for row in report.checks if row["code"] == "DAILY_FRESHNESS")
    assert stale["affected_symbols"] == "B"
