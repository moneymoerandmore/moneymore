from __future__ import annotations

from typing import Protocol

import pandas as pd


class MarketDataProvider(Protocol):
    """Vendor-neutral historical market-data contract."""

    @property
    def name(self) -> str: ...

    def instruments(self) -> pd.DataFrame: ...

    def trading_calendar(self, start_date: str, end_date: str) -> pd.DataFrame: ...

    def daily_bars(self, trade_date: str) -> pd.DataFrame: ...

    def adjustment_factors(self, trade_date: str) -> pd.DataFrame: ...

    def etf_instruments(self) -> pd.DataFrame: ...

    def etf_daily_bars(self, trade_date: str) -> pd.DataFrame: ...

    def etf_adjustment_factors(self, trade_date: str) -> pd.DataFrame: ...

    def index_daily(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame: ...

    def stock_limits(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame: ...

    def suspensions(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame: ...

    def daily_basic(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame: ...

    def dividends(self, ts_code: str) -> pd.DataFrame: ...

    def financial_indicators(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame: ...
