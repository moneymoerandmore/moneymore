import pandas as pd
import pytest

from moneymore.research.metrics import performance_metrics, slice_equity


def test_performance_metrics_report_drawdown_and_return():
    equity = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=4),
            "equity": [100.0, 110.0, 88.0, 121.0],
        }
    )
    metrics = performance_metrics(equity)
    assert metrics["total_return"] == pytest.approx(0.21)
    assert round(metrics["max_drawdown"], 6) == -0.2


def test_slice_equity_uses_requested_period():
    equity = pd.DataFrame(
        {
            "date": pd.date_range("2024-12-30", periods=5),
            "equity": [1, 2, 3, 4, 5],
        }
    )
    selected = slice_equity(equity, "2025-01-01", "2025-01-03")
    assert selected["equity"].tolist() == [3, 4, 5]
