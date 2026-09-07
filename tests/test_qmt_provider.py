from __future__ import annotations

import pandas as pd
from requests.exceptions import ConnectionError

from moneymore.data.qmt_provider import QmtTushareProvider


class Fallback:
    def trading_calendar(self, start_date, end_date):
        return pd.DataFrame([{"cal_date": start_date, "is_open": "1"}])

    def daily_bars(self, trade_date):
        return pd.DataFrame([{"ts_code": "FALLBACK.SH", "trade_date": trade_date}])

    def daily_basic(self, ts_code, start_date, end_date):
        raise ConnectionError("offline")


def test_qmt_daily_rows_are_returned_as_a_frame(tmp_path, monkeypatch):
    provider = QmtTushareProvider(fallback=Fallback(), root=tmp_path)
    monkeypatch.setattr(
        provider,
        "_call",
        lambda *args: [
            {
                "ts_code": "600036.SH",
                "trade_date": "20260903",
                "open": 40.76,
                "high": 41.36,
                "low": 40.52,
                "close": 41.07,
                "vol": 768716.0,
                "amount": 3160615.627,
            }
        ],
    )
    result = provider.daily_bars("20260903")
    assert result.iloc[0]["ts_code"] == "600036.SH"
    assert result.iloc[0]["amount"] == 3160615.627


def test_qmt_failure_falls_back_without_stranding_pipeline(tmp_path, monkeypatch):
    provider = QmtTushareProvider(fallback=Fallback(), root=tmp_path)

    def fail(*args):
        raise RuntimeError("MiniQMT offline")

    monkeypatch.setattr(provider, "_call", fail)
    assert provider.daily_bars("20260903").iloc[0]["ts_code"] == "FALLBACK.SH"
    assert provider.trading_calendar("20260903", "20260903").iloc[0]["is_open"] == "1"


def test_daily_basic_carries_last_point_in_time_snapshot_on_network_failure(tmp_path):
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "ts_code": "600036.SH",
                "trade_date": "20260903",
                "close": 41.07,
                "pb": 1.0,
                "dv_ttm": 4.0,
                "total_mv": 100.0,
            }
        ]
    ).to_parquet(processed / "daily_basic.parquet", index=False)
    provider = QmtTushareProvider(fallback=Fallback(), root=tmp_path)
    result = provider.daily_basic("600036.SH", "20260904", "20260904")
    assert result.iloc[0]["trade_date"] == "20260904"
    assert result.iloc[0]["source_trade_date"] == "20260903"
    assert result.iloc[0]["data_status"] == "STALE_CARRY_FORWARD"
