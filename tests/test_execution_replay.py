from pathlib import Path

import pandas as pd
import pytest

from moneymore.config import BacktestConfig
from moneymore.execution_replay import run_execution_replay


def _config() -> BacktestConfig:
    return BacktestConfig(
        initial_cash=100_000,
        commission_rate=0.00015,
        minimum_commission=5,
        stamp_duty_rate=0.0005,
        transfer_fee_rate=0.00001,
        slippage_bps=2,
        lot_size=100,
        max_position_weight=0.5,
        max_gross_exposure=0.8,
        max_drawdown=0.15,
    )


def test_replay_uses_next_open_costs_t_plus_one_and_reconciles(tmp_path: Path) -> None:
    bars = pd.DataFrame(
        [
            {"date": "2026-01-05", "symbol": "AAA", "open": 10, "close": 10},
            {"date": "2026-01-06", "symbol": "AAA", "open": 10, "close": 11},
            {"date": "2026-01-07", "symbol": "AAA", "open": 11, "close": 11},
            {"date": "2026-01-08", "symbol": "AAA", "open": 12, "close": 12},
        ]
    )
    targets = pd.DataFrame(
        [
            {"date": "2026-01-05", "symbol": "AAA", "target_weight": 0.5},
            {"date": "2026-01-06", "symbol": "AAA", "target_weight": 0.0},
        ]
    )
    result = run_execution_replay(
        bars=bars,
        targets=targets,
        config=_config(),
        database=tmp_path / "replay.sqlite3",
        strategy_id="replay",
        account_id="replay",
    )
    assert result.fills[0]["trade_date"] == "20260106"
    assert result.fills[0]["price"] == pytest.approx(10.002)
    assert result.fills[1]["trade_date"] == "20260107"
    assert result.fills[1]["price"] == pytest.approx(10.9978)
    assert result.summary["transaction_cost"] > 10
    assert result.summary["reconciled"] is True


def test_replay_defers_limit_up_without_losing_order(tmp_path: Path) -> None:
    bars = pd.DataFrame(
        [
            {"date": "2026-01-05", "symbol": "AAA", "open": 10, "close": 10},
            {
                "date": "2026-01-06",
                "symbol": "AAA",
                "open": 11,
                "close": 11,
                "can_buy": False,
            },
            {"date": "2026-01-07", "symbol": "AAA", "open": 10, "close": 10},
        ]
    )
    targets = pd.DataFrame(
        [{"date": "2026-01-05", "symbol": "AAA", "target_weight": 0.5}]
    )
    result = run_execution_replay(
        bars=bars,
        targets=targets,
        config=_config(),
        database=tmp_path / "limit.sqlite3",
        strategy_id="replay",
        account_id="replay",
    )
    assert result.summary["deferred_count"] == 1
    assert result.fills[0]["trade_date"] == "20260107"
