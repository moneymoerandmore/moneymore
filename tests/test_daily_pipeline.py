from pathlib import Path

import pandas as pd

from moneymore.config import BacktestConfig
from moneymore.daily import run_daily_pipeline
from moneymore.data.store import ParquetStore
from moneymore.execution.paper import PaperBroker


class DailyProvider:
    name = "fake"

    def __init__(self, trade_date: str, is_open: bool = True) -> None:
        self.trade_date = trade_date
        self.is_open = is_open

    def trading_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            [{"cal_date": self.trade_date, "is_open": "1" if self.is_open else "0"}]
        )

    def daily_bars(self, trade_date: str) -> pd.DataFrame:
        return _daily_frame([trade_date], [300.0])

    def adjustment_factors(self, trade_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            [{"ts_code": "600036.SH", "trade_date": trade_date, "adj_factor": 1.0}]
        )

    def stock_limits(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "ts_code": ts_code,
                    "trade_date": self.trade_date,
                    "up_limit": 330.0,
                    "down_limit": 270.0,
                }
            ]
        )

    def suspensions(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return pd.DataFrame()


def _daily_frame(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": "600036.SH",
            "trade_date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "pre_close": closes,
            "change": 0.0,
            "pct_chg": 0.0,
            "vol": 1_000.0,
            "amount": 10_000.0,
        }
    )


def _config() -> BacktestConfig:
    return BacktestConfig(
        initial_cash=1_000_000,
        commission_rate=0.00015,
        minimum_commission=5,
        stamp_duty_rate=0.0005,
        transfer_fee_rate=0.00001,
        slippage_bps=2,
        lot_size=100,
        max_position_weight=0.1,
        max_gross_exposure=0.8,
        max_drawdown=0.15,
    )


def _seed_history(store: ParquetStore, current_date: str) -> None:
    dates = pd.bdate_range(end=pd.Timestamp(current_date), periods=260)
    prior = dates[:-1].strftime("%Y%m%d").tolist()
    closes = [float(index) for index in range(41, 300)]
    store.save_snapshot(
        "daily",
        _daily_frame(prior, closes),
        "seed",
        ["ts_code", "trade_date"],
        "history",
    )
    store.save_snapshot(
        "adj_factor",
        pd.DataFrame(
            {
                "ts_code": "600036.SH",
                "trade_date": prior,
                "adj_factor": 1.0,
            }
        ),
        "seed",
        ["ts_code", "trade_date"],
        "history",
    )


def test_daily_pipeline_is_idempotent_and_writes_audit_reports(tmp_path: Path):
    trade_date = "20251231"
    store = ParquetStore(tmp_path / "data")
    _seed_history(store, trade_date)
    broker = PaperBroker(tmp_path / "paper.sqlite3")
    broker.initialize_account(1_000_000)
    arguments = {
        "provider": DailyProvider(trade_date),
        "store": store,
        "broker": broker,
        "config": _config(),
        "symbol": "600036.SH",
        "trade_date": trade_date,
        "signal_dir": tmp_path / "signals",
        "report_dir": tmp_path / "reports",
    }

    first = run_daily_pipeline(**arguments)
    second = run_daily_pipeline(**arguments)

    assert first.status == "COMPLETED"
    assert first.paper_status == "PENDING"
    assert first.reconciliation["matched"]
    assert Path(first.report_path).exists()
    assert second.status == "COMPLETED"
    assert second.decision_status == "DUPLICATE"
    assert second.paper_status == "DUPLICATE_DECISION"
    assert len(list((tmp_path / "reports").glob("*.json"))) == 2
    assert len(broker.orders()) == 1


def test_daily_pipeline_skips_closed_market(tmp_path: Path):
    broker = PaperBroker(tmp_path / "paper.sqlite3")
    broker.initialize_account(1_000_000)
    result = run_daily_pipeline(
        provider=DailyProvider("20260101", is_open=False),
        store=ParquetStore(tmp_path / "data"),
        broker=broker,
        config=_config(),
        symbol="600036.SH",
        trade_date="20260101",
        report_dir=tmp_path / "reports",
    )
    assert result.status == "SKIPPED_MARKET_CLOSED"
    assert result.reconciliation["matched"]
