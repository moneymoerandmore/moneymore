from pathlib import Path

import pandas as pd
import pytest

from moneymore.config import BacktestConfig
from moneymore.execution.paper import ExecutionBar, PaperBroker
from moneymore.execution.risk import (
    OrderIntent,
    PortfolioSnapshot,
    RiskResult,
    create_order_intent,
)
from moneymore.models import Side
from moneymore.signals import trend_decision, write_signal_artifact


def _bars(active: bool = True) -> pd.DataFrame:
    count = 260
    values = list(range(1, count + 1)) if active else list(range(count, 0, -1))
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=count),
            "symbol": "AAA",
            "signal_close": values,
            "raw_close": values,
        }
    )


def test_signal_and_risk_create_ten_percent_target_order():
    decision = trend_decision(_bars(), "20251231")
    result = create_order_intent(
        decision,
        PortfolioSnapshot(
            cash=100_000, equity=100_000, position_quantity=0, reference_price=100
        ),
    )
    assert decision.target_weight == 0.10
    assert result.accepted
    assert result.intent is not None
    assert result.intent.quantity == 100


def test_weight_above_limit_is_rejected():
    decision = trend_decision(_bars(), "20251231")
    decision = decision.__class__(**{**decision.to_dict(), "target_weight": 0.2})
    result = create_order_intent(
        decision,
        PortfolioSnapshot(
            cash=100_000, equity=100_000, position_quantity=0, reference_price=10
        ),
    )
    assert not result.accepted
    assert result.rejection_code == "TARGET_WEIGHT_EXCEEDS_LIMIT"


def test_paper_broker_is_idempotent(tmp_path: Path):
    decision = trend_decision(_bars(), "20251231")
    result = create_order_intent(
        decision,
        PortfolioSnapshot(
            cash=1_000_000,
            equity=1_000_000,
            position_quantity=0,
            reference_price=100,
        ),
    )
    broker = PaperBroker(tmp_path / "paper.sqlite3")
    assert broker.submit(result) == "PENDING"
    assert broker.submit(result) == "DUPLICATE"
    assert len(broker.orders()) == 1


def test_decision_and_signal_artifact_are_idempotent(tmp_path: Path):
    decision = trend_decision(_bars(), "20251231")
    first = write_signal_artifact(decision, tmp_path / "signals")
    second = write_signal_artifact(decision, tmp_path / "signals")
    broker = PaperBroker(tmp_path / "paper.sqlite3")

    assert first == second
    assert broker.record_decision(decision) == "RECORDED"
    assert broker.record_decision(decision) == "DUPLICATE"


def _config() -> BacktestConfig:
    return BacktestConfig(
        initial_cash=100_000,
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


def _risk(key: str, side: Side, quantity: int = 100) -> RiskResult:
    return RiskResult(
        accepted=True,
        intent=OrderIntent(
            idempotency_key=key,
            strategy_id="test",
            symbol="600036.SH",
            side=side,
            quantity=quantity,
            signal_date="20260105",
            reason_code="TEST",
        ),
        rejection_code=None,
    )


def test_pending_order_fills_only_after_signal_date_and_reconciles(tmp_path: Path):
    broker = PaperBroker(tmp_path / "paper.sqlite3")
    assert broker.initialize_account(100_000) == "INITIALIZED"
    assert broker.submit(_risk("buy-1", Side.BUY)) == "PENDING"

    same_day = ExecutionBar("600036.SH", "20260105", 40.0, 40.2)
    assert broker.execute_pending(same_day, _config()) == []

    next_day = ExecutionBar("600036.SH", "20260106", 40.0, 40.2)
    outcome = broker.execute_pending(next_day, _config())
    assert outcome[0]["outcome"] == "FILLED"
    assert outcome[0]["price"] == pytest.approx(40.008)
    portfolio = broker.portfolio(40.2, "600036.SH")
    assert portfolio["position_quantity"] == 100
    assert portfolio["available_quantity"] == 0
    assert broker.reconcile().matched


def test_limit_and_t_plus_one_defer_without_losing_order(tmp_path: Path):
    broker = PaperBroker(tmp_path / "paper.sqlite3")
    broker.initialize_account(100_000)
    broker.submit(_risk("buy-1", Side.BUY))

    limit_up = ExecutionBar(
        "600036.SH", "20260106", 40.0, 40.0, can_buy=False
    )
    assert broker.execute_pending(limit_up, _config())[0]["reason_code"] == "LIMIT_UP"
    assert broker.orders()[0]["status"] == "PENDING"

    broker.submit(_risk("sell-1", Side.SELL))
    outcomes = broker.execute_pending(
        ExecutionBar("600036.SH", "20260107", 40.0, 40.0), _config()
    )
    assert [item["outcome"] for item in outcomes] == ["FILLED", "DEFERRED"]
    assert outcomes[1]["reason_code"] == "T_PLUS_ONE"

    outcomes = broker.execute_pending(
        ExecutionBar("600036.SH", "20260108", 41.0, 41.0), _config()
    )
    assert outcomes[0]["outcome"] == "FILLED"
    portfolio = broker.portfolio(41.0, "600036.SH")
    assert portfolio["position_quantity"] == 0
    assert broker.reconcile().matched


def test_cancelled_order_can_be_reopened_after_data_correction(tmp_path: Path):
    broker = PaperBroker(tmp_path / "paper.sqlite3")
    broker.initialize_account(100_000)
    risk = _risk("corrected-buy", Side.BUY)

    assert broker.submit(risk) == "PENDING"
    assert broker.cancel_pending("default", "DATA_STALE", "20260105") == 1
    assert broker.submit(risk) == "REOPENED"
    assert broker.orders()[0]["status"] == "PENDING"


def test_cash_and_stock_dividends_are_idempotent_and_reconcile(tmp_path: Path):
    broker = PaperBroker(tmp_path / "paper.sqlite3")
    broker.initialize_account(100_000)
    broker.submit(_risk("buy-for-dividend", Side.BUY))
    broker.execute_pending(
        ExecutionBar("600036.SH", "20260106", 40.0, 40.0), _config()
    )

    assert broker.register_corporate_entitlement(
        "default",
        "600036.SH",
        "600036.SH:20251231:20260106",
        "20260106",
        cash_per_share=0.5,
        stock_ratio=0.1,
        cash_pay_date="20260107",
        stock_list_date="20260107",
    ) == "REGISTERED"
    assert broker.register_corporate_entitlement(
        "default",
        "600036.SH",
        "600036.SH:20251231:20260106",
        "20260106",
        cash_per_share=0.5,
        stock_ratio=0.1,
        cash_pay_date="20260107",
        stock_list_date="20260107",
    ) == "DUPLICATE"

    settled = broker.settle_corporate_actions("default", "20260107")
    assert [row["action_type"] for row in settled] == [
        "CASH_DIVIDEND",
        "STOCK_DIVIDEND",
    ]
    assert broker.settle_corporate_actions("default", "20260107") == []
    portfolio = broker.portfolio(40.0, "600036.SH")
    assert portfolio["position_quantity"] == 110
    assert portfolio["cash"] == pytest.approx(96_044.2)
    assert broker.reconcile().matched
