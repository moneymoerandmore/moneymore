import numpy as np
import pandas as pd
import pytest

from moneymore.qlib_exposure import dynamic_target_exposure

POLICY = {"annual_target": 0.12, "lookback": 60, "minimum_observations": 20, "minimum_exposure": 0.20, "maximum_exposure": 0.80, "fallback_exposure": 0.50, "rounding_step": 0.05, "maximum_daily_change": 0.10}


def test_fully_invested_policy_uses_entire_risk_budget() -> None:
    result = dynamic_target_exposure(
        object(), [], "2026-12-31", {"method": "fully_invested", "target_exposure": 1.0}
    )
    assert result["target_gross_exposure"] == pytest.approx(1.0)


def _loader(volatility: float):
    def load(_store, symbol: str) -> pd.DataFrame:
        rng = np.random.default_rng(sum(map(ord, symbol)))
        returns = rng.normal(0, volatility / np.sqrt(242), 90)
        return pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=90), "close": 100 * np.cumprod(1 + returns)})
    return load


def test_dynamic_exposure_increases_when_selected_portfolio_volatility_is_low() -> None:
    policy = {"method": "selected_portfolio_volatility_target", **POLICY}
    low = dynamic_target_exposure(object(), ["A", "B"], "2026-12-31", policy, loader=_loader(0.08))
    high = dynamic_target_exposure(object(), ["A", "B"], "2026-12-31", policy, loader=_loader(0.30))
    assert low["target_gross_exposure"] == pytest.approx(0.8)
    assert 0.2 <= float(high["target_gross_exposure"]) < 0.8


def test_dynamic_exposure_limits_daily_change() -> None:
    policy = {"method": "selected_portfolio_volatility_target", **POLICY}
    result = dynamic_target_exposure(object(), ["A", "B"], "2026-12-31", policy, previous_exposure=0.4, loader=_loader(0.05))
    assert result["target_gross_exposure"] == pytest.approx(0.5)
