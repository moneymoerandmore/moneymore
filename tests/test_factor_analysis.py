import numpy as np
import pandas as pd
import pytest

from moneymore.factors import (
    PreprocessConfig,
    attach_forward_returns,
    factor_correlation,
    factor_ic_period_report,
    factor_ic_report,
    preprocess_cross_section,
    quantile_return_report,
)


def _panel(days: int = 8, assets: int = 20):
    rows = []
    for day, date in enumerate(pd.bdate_range("2025-01-01", periods=days)):
        for asset in range(assets):
            rows.append(
                {
                    "date": date,
                    "symbol": f"S{asset:03d}",
                    "alpha": float(asset),
                    "noisy": float((asset * 7 + day) % assets),
                    "signal_close": 100.0 * (1.0 + asset * 0.001) ** day,
                    "total_mv": float(1000 + asset * 100),
                    "industry": "A" if asset < assets / 2 else "B",
                }
            )
    return pd.DataFrame(rows)


def test_preprocessing_is_date_local_robust_and_rank_normalized():
    panel = _panel()
    panel.loc[(panel["date"] == panel["date"].min()) & (panel["symbol"] == "S019"), "alpha"] = 1e9

    result = preprocess_cross_section(
        panel,
        ["alpha"],
        PreprocessConfig(neutralize_size=False, industry_column=None),
    )

    by_date = result.groupby("date")["alpha"]
    assert by_date.max().eq(1.0).all()
    assert by_date.min().eq(-0.9).all()
    assert result["alpha"].between(-1, 1).all()


def test_preprocessing_supports_realtime_single_date_cross_section():
    panel = _panel(days=1)

    result = preprocess_cross_section(panel, ["alpha"])

    assert len(result) == 20
    assert result["alpha"].notna().all()


def test_forward_returns_and_rank_ic_find_monotonic_signal():
    panel = _panel(days=12)
    factors = panel[["date", "symbol", "alpha", "noisy"]]
    labeled = attach_forward_returns(factors, panel, horizons=(1, 5))

    report = factor_ic_report(
        labeled, ["alpha"], horizons=(1, 5), minimum_assets=10
    )

    assert report["observations"].min() > 0
    assert report["mean_rank_ic"].min() > 0.99
    assert report["positive_rate"].eq(1.0).all()


def test_quantile_spread_and_factor_correlation():
    panel = _panel(days=12)
    factors = panel[["date", "symbol", "alpha", "noisy"]]
    labeled = attach_forward_returns(factors, panel, horizons=(5,))

    quantiles = quantile_return_report(
        labeled, ["alpha"], horizon=5, minimum_assets=10
    )
    spread = quantiles.loc[
        quantiles["quantile"] == "top_minus_bottom", "mean_forward_return"
    ]
    correlation = factor_correlation(factors, ["alpha", "noisy"])

    assert spread.iloc[0] > 0
    assert correlation.loc["alpha", "alpha"] == pytest.approx(1.0)
    assert np.isfinite(correlation.loc["alpha", "noisy"])


def test_period_report_keeps_train_and_test_separate():
    panel = _panel(days=12)
    labeled = attach_forward_returns(
        panel[["date", "symbol", "alpha"]], panel, horizons=(1,)
    )

    report = factor_ic_period_report(
        labeled,
        ["alpha"],
        {
            "train": ("2025-01-01", "2025-01-08"),
            "test": ("2025-01-09", "2025-01-31"),
        },
        horizons=(1,),
        minimum_assets=10,
    )

    assert set(report["period"]) == {"train", "test"}
    assert report["mean_rank_ic"].min() > 0.99
