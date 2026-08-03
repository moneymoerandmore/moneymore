import pandas as pd
import pytest

from moneymore.config import BacktestConfig
from moneymore.execution.paper import ExecutionBar
from moneymore.multi_sector_daily import (
    _execution_attribution,
    _return_attribution,
    _risk_alerts,
    _weight_deviations,
    expand_sleeve_targets,
)


def test_expand_sleeve_targets_splits_each_sleeve_equally() -> None:
    recommendation = pd.DataFrame(
        [
            {"sector": "bank", "target_weight": 0.18, "selected": "A,B,C"},
            {"sector": "chip", "target_weight": 0.06, "selected": "D,E"},
            {"sector": "cash", "target_weight": 0.0, "selected": ""},
        ]
    )

    targets, sectors = expand_sleeve_targets(recommendation)

    assert targets == {"A": 0.06, "B": 0.06, "C": 0.06, "D": 0.03, "E": 0.03}
    assert sectors == {
        "A": "bank",
        "B": "bank",
        "C": "bank",
        "D": "chip",
        "E": "chip",
    }


def test_expand_sleeve_targets_aggregates_overlap_and_caps_single_stock() -> None:
    recommendation = pd.DataFrame(
        [
            {"sector": "chip", "target_weight": 0.18, "selected": "A,B"},
            {"sector": "science50", "target_weight": 0.12, "selected": "A,C"},
        ]
    )
    targets, sectors = expand_sleeve_targets(recommendation)
    assert targets == {"A": 0.10, "B": 0.09, "C": 0.06}
    assert sectors["A"] == "chip"


def test_weight_deviation_explains_below_one_lot() -> None:
    rows = _weight_deviations(
        {"equity": 1_000_000, "positions": []},
        {"A": 120.0},
        {"A": 0.01},
        {"A": "chip"},
        [
            {
                "symbol": "A",
                "rejection_code": "NO_REBALANCE_REQUIRED",
            }
        ],
        100,
    )

    assert rows[0]["reason"] == "BELOW_ONE_LOT"
    assert rows[0]["weight_gap"] == -0.01


def test_risk_alerts_block_stale_data() -> None:
    alerts = _risk_alerts(
        BacktestConfig(
            initial_cash=1_000_000,
            commission_rate=0.00015,
            minimum_commission=5,
            stamp_duty_rate=0.0005,
            transfer_fee_rate=0.00001,
            slippage_bps=2,
            lot_size=100,
            max_position_weight=0.10,
            max_gross_exposure=0.80,
            max_drawdown=0.15,
        ),
        {"gross_exposure": 0.0, "drawdown": 0.0},
        [],
        ["A", "B"],
        {"matched": True},
        [],
    )

    assert alerts == [
        {
            "code": "DATA_STALE",
            "severity": "BLOCK",
            "message": "2只目标股票缺少当日行情，禁止生成新订单",
        }
    ]


def test_return_attribution_reconciles_price_cost_and_dividend() -> None:
    rows, reconciliation = _return_attribution(
        account_before={
            "cash": 9_000.0,
            "positions": [{"symbol": "A", "quantity": 100}],
        },
        portfolio={"equity": 10_105.0},
        fills=[],
        marks={"A": 11.0},
        previous_marks={"A": 10.0},
        bars={"A": ExecutionBar("A", "20260729", 10.5, 11.0)},
        sectors={"A": "bank"},
        corporate_actions=[
            {"symbol": "A", "cash_amount": 5.0, "share_quantity": 0}
        ],
    )

    assert reconciliation["actual_pnl"] == 105.0
    assert reconciliation["explained_pnl"] == pytest.approx(105.0)
    assert reconciliation["matched"] is True
    assert {row["component"] for row in rows} == {
        "SECTOR_ALLOCATION",
        "DIVIDEND",
        "CASH",
    }


def test_execution_attribution_groups_weight_gaps_and_attempts() -> None:
    rows = _execution_attribution(
        [
            {"reason": "PENDING_T_PLUS_ONE", "weight_gap": -0.1},
            {"reason": "BELOW_ONE_LOT", "weight_gap": -0.01},
        ],
        [{"outcome": "DEFERRED", "reason_code": "LIMIT_UP"}],
    )
    indexed = {row["reason"]: row for row in rows}

    assert indexed["PENDING_T_PLUS_ONE"]["absolute_weight_gap"] == 0.1
    assert indexed["BELOW_ONE_LOT"]["symbol_count"] == 1
    assert indexed["LIMIT_UP"]["execution_events"] == 1
