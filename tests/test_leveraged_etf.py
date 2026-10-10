from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from moneymore.leveraged_etf import ACCOUNT_ID, INITIAL_CASH, LeveragedEtfPaper


def _market(direction: float = 0.001) -> dict[str, pd.DataFrame]:
    dates = pd.bdate_range("2025-01-02", periods=240).strftime("%Y-%m-%d")
    qqq_close = 400 * np.cumprod(np.full(len(dates), 1 + direction))
    result: dict[str, pd.DataFrame] = {}
    for symbol, close in {
        "QQQ": qqq_close,
        "TQQQ": 50 * np.cumprod(np.full(len(dates), 1 + direction * 3)),
        "SQQQ": 40 * np.cumprod(np.full(len(dates), 1 - direction * 3)),
        "^VIX": np.full(len(dates), 14.0),
    }.items():
        result[symbol] = pd.DataFrame(
            {
                "trade_date": dates,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": np.full(len(dates), 1_000_000),
            }
        )
    return result


class StaticClient:
    def __init__(self, data: dict[str, pd.DataFrame]) -> None:
        self.data = data

    def history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return self.data[symbol].copy()


def test_bull_signal_queues_tqqq_in_isolated_account(tmp_path: Path) -> None:
    database = tmp_path / "us-etf.sqlite3"
    paper = LeveragedEtfPaper(database, tmp_path / "cache")

    snapshot = paper.run(StaticClient(_market()))

    assert snapshot["account_id"] == ACCOUNT_ID
    assert snapshot["initial_cash"] == INITIAL_CASH
    assert snapshot["equity"] == INITIAL_CASH
    assert snapshot["latest_signal"]["regime"] == "BULL"
    assert snapshot["latest_signal"]["tqqq"] > 0
    assert {row["symbol"] for row in snapshot["orders"]} == {"TQQQ"}
    assert all(row["status"] == "PENDING" for row in snapshot["orders"])


def test_same_session_is_idempotent(tmp_path: Path) -> None:
    paper = LeveragedEtfPaper(tmp_path / "us-etf.sqlite3", tmp_path / "cache")
    client = StaticClient(_market())

    first = paper.run(client)
    second = paper.run(client)

    assert len(second["orders"]) == len(first["orders"])
    assert second["cash"] == first["cash"]
    assert second["fills"] == []


def test_managed_layer_reduces_risk_fast_and_rebuilds_slowly() -> None:
    calm = _market()
    rebuilding = LeveragedEtfPaper.allocation(calm, previous_tqqq=0.20)
    inside_band = LeveragedEtfPaper.allocation(calm, previous_tqqq=0.95)

    assert rebuilding.base_tqqq == 1.0
    assert rebuilding.tqqq == 0.30
    assert inside_band.tqqq == 0.95

    reduced, position_volatility, scalar = LeveragedEtfPaper.manage_tqqq(
        base_tqqq=1.0, qqq_volatility=0.40, previous_tqqq=1.0
    )
    assert position_volatility == pytest.approx(1.20)
    assert scalar < 0.50
    assert reduced < 0.50


def test_outage_replays_every_missing_us_session(tmp_path: Path) -> None:
    paper = LeveragedEtfPaper(tmp_path / "us-etf.sqlite3", tmp_path / "cache")
    full = _market()
    partial = {symbol: frame.iloc[:-5].copy() for symbol, frame in full.items()}

    first = paper.run(StaticClient(partial))
    caught_up = paper.run(StaticClient(full))

    assert first["latest_signal"]["signal_date"] == str(partial["QQQ"].iloc[-1]["trade_date"])
    assert caught_up["latest_signal"]["signal_date"] == str(full["QQQ"].iloc[-1]["trade_date"])
    assert len(caught_up["history"]) == 6
    assert caught_up["fills"]
    assert caught_up["positions"]


def test_storage_is_not_the_moneymore_paper_broker_schema(tmp_path: Path) -> None:
    database = tmp_path / "us-etf.sqlite3"
    LeveragedEtfPaper(database, tmp_path / "cache")

    import sqlite3

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "account" in tables
    assert "accounts" not in tables
    assert "rejections" not in tables
