from pathlib import Path

import pandas as pd
import pytest

from moneymore.data.calendar import sync_trading_calendar
from moneymore.data.store import ParquetStore


class CalendarProvider:
    name = "calendar-test"

    def __init__(self, include_requested: bool = True) -> None:
        self.include_requested = include_requested
        self.requested: tuple[str, str] | None = None

    def trading_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        self.requested = (start_date, end_date)
        dates = ["20260729"] if self.include_requested else ["20260728"]
        return pd.DataFrame(
            {
                "exchange": ["SSE"] * len(dates),
                "cal_date": dates,
                "is_open": [1] * len(dates),
                "pretrade_date": ["20260728"] * len(dates),
            }
        )


def test_calendar_sync_persists_official_window(tmp_path: Path) -> None:
    provider = CalendarProvider()
    store = ParquetStore(tmp_path / "data")

    result = sync_trading_calendar(provider, store, "20260729")

    assert provider.requested == ("20260628", "20261027")
    assert result.iloc[0]["cal_date"] == "20260729"
    curated = store.read("trade_calendar")
    assert curated.iloc[0]["exchange"] == "SSE"


def test_calendar_sync_rejects_response_without_requested_date(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="does not cover requested date"):
        sync_trading_calendar(
            CalendarProvider(include_requested=False),
            ParquetStore(tmp_path / "data"),
            "20260729",
        )
