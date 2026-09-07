import pandas as pd

from moneymore.strategy_comparison import build_branch_history


def test_branch_history_inherits_parent_before_activation_only() -> None:
    parent = pd.DataFrame(
        [
            {"trade_date": "20260907", "equity": 1_020_000},
            {"trade_date": "20260908", "equity": 1_010_000},
        ]
    )
    branch = pd.DataFrame(
        [
            {"trade_date": "20260908", "equity": 1_015_000},
            {"trade_date": "20260909", "equity": 1_030_000},
        ]
    )

    result = build_branch_history(
        parent,
        branch,
        branch_account_id="intraday",
        activation_date="20260908",
    )

    assert result[["trade_date", "equity"]].to_dict("records") == [
        {"trade_date": "20260907", "equity": 1_020_000},
        {"trade_date": "20260908", "equity": 1_015_000},
        {"trade_date": "20260909", "equity": 1_030_000},
    ]
    assert result["history_source"].tolist() == [
        "PARENT_BASELINE",
        "INTRADAY_BRANCH",
        "INTRADAY_BRANCH",
    ]
