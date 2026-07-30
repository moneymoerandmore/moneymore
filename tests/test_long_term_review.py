import numpy as np
import pandas as pd

from moneymore.long_term_review import evaluate_paired_performance


def test_review_uses_only_trustworthy_common_dates() -> None:
    dates = pd.bdate_range("2026-07-27", periods=25)
    factor = pd.DataFrame(
        {"trade_date": dates.strftime("%Y%m%d"), "equity": 100 * 1.001 ** np.arange(25)}
    )
    qlib = pd.DataFrame(
        {"trade_date": dates.strftime("%Y%m%d"), "equity": 100 * 1.002 ** np.arange(25)}
    )
    result = evaluate_paired_performance(
        factor,
        qlib,
        trustworthy_start_date=dates[5].strftime("%Y%m%d"),
        bootstrap_samples=100,
    )
    assert result["common_start_date"] == dates[5].strftime("%Y%m%d")
    assert result["common_observation_days"] == 20
    assert result["qlib_total_return"] > result["factor_total_return"]
    assert result["annualized_excess_ci_low"] > 0


def test_review_refuses_statistics_before_twenty_returns() -> None:
    factor = pd.DataFrame(
        {"trade_date": ["20260730", "20260731"], "equity": [100, 101]}
    )
    qlib = pd.DataFrame(
        {"trade_date": ["20260730", "20260731"], "equity": [100, 102]}
    )
    result = evaluate_paired_performance(
        factor, qlib, trustworthy_start_date="20260730"
    )
    assert result["common_observation_days"] == 2
    assert result["annualized_excess_ci_low"] is None
    assert result["deflated_sharpe_probability"] is None
