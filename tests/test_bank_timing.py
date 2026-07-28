import pandas as pd
import pytest

from moneymore.research.bank_timing import (
    FixedRiskDegree,
    KAMARiskDegree,
    VolatilityTargetRiskDegree,
    apply_risk_degree,
    kaufman_adaptive_moving_average,
)


def _market(count: int = 100) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=count, freq="D"),
            "market_index": [1 + index * 0.01 for index in range(count)],
        }
    )


def test_fixed_risk_degree_matches_qlib_position_percentage_semantics():
    result = FixedRiskDegree(0.8).risk_degrees(_market())
    assert result["risk_degree"].eq(0.8).all()


def test_kama_uses_only_past_prices_and_invests_in_established_uptrend():
    market = _market()
    result = KAMARiskDegree().risk_degrees(market)
    assert result.iloc[:10]["risk_degree"].eq(0).all()
    assert result.iloc[-1]["risk_degree"] == pytest.approx(0.8)
    shortened = KAMARiskDegree().risk_degrees(market.iloc[:-1])
    pd.testing.assert_series_equal(
        result.iloc[:-1]["risk_degree"].reset_index(drop=True),
        shortened["risk_degree"].reset_index(drop=True),
    )


def test_kama_rejects_invalid_parameters():
    with pytest.raises(ValueError):
        kaufman_adaptive_moving_average(pd.Series([1.0, 2.0]), slow_period=1)


def test_volatility_target_stays_inside_declared_risk_bounds():
    result = VolatilityTargetRiskDegree().risk_degrees(_market())
    assert result["risk_degree"].between(0.2, 0.8).all()


def test_risk_degree_scales_topk_weights_without_changing_selection():
    bars = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01", "2025-01-01"]),
            "symbol": ["A", "B"],
            "target": [0.1, 0.0],
        }
    )
    degrees = pd.DataFrame(
        {"date": pd.to_datetime(["2025-01-01"]), "risk_degree": [0.4]}
    )
    result = apply_risk_degree(bars, degrees)
    assert result.loc[result["symbol"] == "A", "target"].iloc[0] == pytest.approx(0.05)
    assert result.loc[result["symbol"] == "B", "target"].iloc[0] == 0
