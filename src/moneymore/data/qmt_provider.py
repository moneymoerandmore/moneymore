from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
from requests.exceptions import RequestException

from .tushare_provider import TushareProvider

LOGGER = logging.getLogger(__name__)


class QmtTushareProvider:
    """QMT-first market data with Tushare for fundamentals and safe fallback.

    Huatai's broker SDK currently requires Python 3.11 while MoneyMore runs on
    Python 3.12, so read-only QMT calls cross a narrow subprocess boundary.
    """

    name = "qmt_tushare"

    def __init__(
        self,
        fallback: TushareProvider | None = None,
        root: str | Path | None = None,
        timeout_seconds: int = 300,
    ) -> None:
        self._fallback = fallback or TushareProvider()
        self._root = Path(root or Path(__file__).resolve().parents[3])
        self._python = Path(
            os.getenv(
                "MONEYMORE_QMT_PYTHON",
                self._root / ".runtime" / "qmt-py311" / "Scripts" / "python.exe",
            )
        )
        self._bridge = self._root / "scripts" / "qmt_data_bridge.py"
        self._timeout = timeout_seconds
        self._qmt_daily_cache: dict[str, pd.DataFrame] = {}
        self._factor_cache: dict[str, float] | None = None
        self._close_cache: dict[str, float] | None = None

    def _call(self, action: str, *arguments: str) -> Any:
        if not self._python.exists() or not self._bridge.exists():
            raise RuntimeError("QMT bridge runtime is not installed")
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        completed = subprocess.run(
            [str(self._python), str(self._bridge), action, *arguments],
            cwd=self._root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self._timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"QMT {action} failed: {detail}")
        return json.loads(completed.stdout)

    def probe(self) -> dict[str, object]:
        return dict(self._call("probe"))

    def intraday_quotes(self, symbols: list[str]) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame()
        return pd.DataFrame(
            self._call("intraday", "--symbols", ",".join(sorted(set(symbols))))
        )

    def instruments(self) -> pd.DataFrame:
        return self._fallback.instruments()

    def trading_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            rows = self._call("calendar", "--start", start_date, "--end", end_date)
            return pd.DataFrame(rows)
        except (
            json.JSONDecodeError,
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
            ValueError,
        ) as error:  # QMT must not strand recovery workflows
            LOGGER.warning("QMT calendar unavailable; using Tushare: %s", error)
            return self._fallback.trading_calendar(start_date, end_date)

    def daily_bars(self, trade_date: str) -> pd.DataFrame:
        try:
            rows = self._call("daily", "--trade-date", trade_date)
            frame = pd.DataFrame(rows)
            if frame.empty:
                raise RuntimeError(f"QMT returned no daily bars for {trade_date}")
            self._qmt_daily_cache[trade_date] = frame.copy()
            return frame
        except (
            json.JSONDecodeError,
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
            ValueError,
        ) as error:  # retain an observable, operational fallback
            LOGGER.warning("QMT daily unavailable; using Tushare: %s", error)
            return self._fallback.daily_bars(trade_date)

    def adjustment_factors(self, trade_date: str) -> pd.DataFrame:
        current = self._qmt_daily_cache.get(trade_date)
        if current is None:
            return self._fallback.adjustment_factors(trade_date)
        self._load_factor_state(trade_date)
        assert self._factor_cache is not None
        assert self._close_cache is not None
        rows: list[dict[str, object]] = []
        for row in current.itertuples(index=False):
            symbol = str(row.ts_code)
            previous_factor = float(self._factor_cache.get(symbol, 1.0))
            previous_close = self._close_cache.get(symbol)
            reference_close = float(getattr(row, "pre_close", 0.0) or 0.0)
            ratio = 1.0
            if previous_close and reference_close > 0:
                candidate = float(previous_close) / reference_close
                if 0.2 <= candidate <= 5.0:
                    ratio = candidate
            factor = previous_factor * ratio
            self._factor_cache[symbol] = factor
            self._close_cache[symbol] = float(row.close)
            rows.append(
                {"ts_code": symbol, "trade_date": trade_date, "adj_factor": factor}
            )
        return pd.DataFrame(rows)

    def _load_factor_state(self, trade_date: str) -> None:
        if self._factor_cache is not None and self._close_cache is not None:
            return
        factor_path = self._root / "data" / "processed" / "adj_factor.parquet"
        daily_path = self._root / "data" / "processed" / "daily.parquet"
        factors = pd.read_parquet(
            factor_path, columns=["ts_code", "trade_date", "adj_factor"]
        )
        factors = factors.loc[factors["trade_date"].astype(str) < trade_date]
        factors = (
            factors.sort_values(["ts_code", "trade_date"])
            .groupby("ts_code", as_index=False)
            .tail(1)
        )
        closes = pd.read_parquet(
            daily_path, columns=["ts_code", "trade_date", "close"]
        )
        closes = closes.loc[closes["trade_date"].astype(str) < trade_date]
        closes = (
            closes.sort_values(["ts_code", "trade_date"])
            .groupby("ts_code", as_index=False)
            .tail(1)
        )
        self._factor_cache = dict(
            zip(factors["ts_code"].astype(str), factors["adj_factor"].astype(float), strict=False)
        )
        self._close_cache = dict(
            zip(closes["ts_code"].astype(str), closes["close"].astype(float), strict=False)
        )

    def etf_instruments(self) -> pd.DataFrame:
        return self._fallback.etf_instruments()

    def etf_daily_bars(self, trade_date: str) -> pd.DataFrame:
        return self._fallback.etf_daily_bars(trade_date)

    def etf_adjustment_factors(self, trade_date: str) -> pd.DataFrame:
        return self._fallback.etf_adjustment_factors(trade_date)

    def index_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._fallback.index_daily(ts_code, start_date, end_date)

    def stock_limits(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._fallback.stock_limits(ts_code, start_date, end_date)

    def suspensions(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._fallback.suspensions(ts_code, start_date, end_date)

    def daily_basic(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            return self._fallback.daily_basic(ts_code, start_date, end_date)
        except RequestException as error:
            LOGGER.warning(
                "Tushare daily_basic unavailable; carrying last point-in-time snapshot: %s",
                error,
            )
            path = self._root / "data" / "processed" / "daily_basic.parquet"
            frame = pd.read_parquet(path)
            frame["trade_date"] = frame["trade_date"].astype(str)
            frame = frame.loc[frame["trade_date"] < start_date].copy()
            if ts_code:
                frame = frame.loc[frame["ts_code"].astype(str) == ts_code]
            frame = (
                frame.sort_values(["ts_code", "trade_date"])
                .groupby("ts_code", as_index=False)
                .tail(1)
            )
            if frame.empty:
                raise
            frame["source_trade_date"] = frame["trade_date"]
            frame["trade_date"] = end_date
            frame["data_status"] = "STALE_CARRY_FORWARD"
            return frame.reset_index(drop=True)

    def dividends(self, ts_code: str) -> pd.DataFrame:
        return self._fallback.dividends(ts_code)

    def financial_indicators(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return self._fallback.financial_indicators(ts_code, start_date, end_date)


def create_market_data_provider() -> TushareProvider | QmtTushareProvider:
    source = os.getenv("MONEYMORE_MARKET_DATA_SOURCE", "qmt").strip().lower()
    tushare = TushareProvider()
    if source == "tushare":
        return tushare
    if source != "qmt":
        raise ValueError(f"unsupported MONEYMORE_MARKET_DATA_SOURCE: {source}")
    return QmtTushareProvider(tushare)
