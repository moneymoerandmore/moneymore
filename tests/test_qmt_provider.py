from __future__ import annotations

import pandas as pd

from moneymore.data.qmt_provider import QmtTushareProvider


class Fallback:
    def trading_calendar(self, start_date, end_date):
        return pd.DataFrame([{"cal_date": start_date, "is_open": "1"}])

    def daily_bars(self, trade_date):
        return pd.DataFrame([{"ts_code": "FALLBACK.SH", "trade_date": trade_date}])


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
