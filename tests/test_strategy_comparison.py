import pandas as pd
import pytest

from moneymore.strategy_comparison import build_fair_comparison


def test_fair_comparison_uses_common_dates_and_normalizes_nav() -> None:
    factor = pd.DataFrame(
        [
            {"trade_date": "20260728", "equity": 100.0, "gross_exposure": 0.0},
            {"trade_date": "20260729", "equity": 110.0, "gross_exposure": 0.4},
            {"trade_date": "20260730", "equity": 121.0, "gross_exposure": 0.4},
        ]
    )
    qlib = pd.DataFrame(
        [
            {"trade_date": "20260729", "equity": 200.0, "market_value": 80.0},
            {"trade_date": "20260730", "equity": 210.0, "market_value": 84.0},
        ]
    )
    result = build_fair_comparison(
        {"factor": factor, "qlib": qlib}, minimum_observation_days=2
    )
    assert result["common_start_date"] == "20260729"
    assert result["common_observation_days"] == 2
    assert result["ready"] is True
    by_account = {row["account_id"]: row for row in result["metrics"]}
    assert by_account["factor"]["total_return"] == pytest.approx(0.1)
    assert by_account["qlib"]["total_return"] == pytest.approx(0.05)
    assert by_account["qlib"]["average_gross_exposure"] == pytest.approx(0.4)
    assert result["histories"]["factor"][0]["normalized_nav"] == 1.0


def test_fair_comparison_waits_without_common_evidence() -> None:
    factor = pd.DataFrame([{"trade_date": "20260728", "equity": 100.0}])
    qlib = pd.DataFrame([{"trade_date": "20260729", "equity": 100.0}])
    result = build_fair_comparison({"factor": factor, "qlib": qlib})
    assert result["common_observation_days"] == 0
    assert result["status"] == "COLLECTING_EVIDENCE"
    assert result["ready"] is False
