from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

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
        return self._fallback.adjustment_factors(trade_date)

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
        return self._fallback.daily_basic(ts_code, start_date, end_date)

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
