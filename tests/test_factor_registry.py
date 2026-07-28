import numpy as np
import pandas as pd
import pytest

from moneymore.factors import (
    Availability,
    FactorCategory,
    FactorDefinition,
    FactorDirection,
    FactorRegistry,
    build_default_registry,
)


def test_default_registry_has_versioned_cross_style_catalog():
    registry = build_default_registry()

    assert len(registry.names()) == 21
    assert len(set(registry.names())) == len(registry.names())
    categories = {item["category"] for item in registry.catalog()}
    assert categories == {
        "trend", "momentum", "volatility", "liquidity",
        "value", "quality", "growth",
    }
    assert registry.get("roe").availability == Availability.ANNOUNCEMENT_T_PLUS_1
    assert registry.get("return_20").identity == "return_20@v1"


def test_factor_engine_sorts_and_never_leaks_between_symbols():
    registry = build_default_registry()
    rows = []
    for symbol, base in (("BBB", 100.0), ("AAA", 10.0)):
        for index, date in enumerate(pd.bdate_range("2025-01-01", periods=25)):
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "signal_close": base + index,
                    "amount": 1_000_000.0,
                    "turnover_rate": 1.0,
                }
            )
    frame = pd.DataFrame(reversed(rows))

    result = registry.compute(frame, ["return_20", "price_to_ma_20"])

    assert result.iloc[0]["symbol"] == "AAA"
    assert result.groupby("symbol")["return_20"].apply(
        lambda values: values.iloc[:20].isna().all()
    ).all()
    aaa = result.loc[result["symbol"] == "AAA", "return_20"].iloc[20]
    bbb = result.loc[result["symbol"] == "BBB", "return_20"].iloc[20]
    assert aaa == pytest.approx(2.0)
    assert bbb == pytest.approx(0.2)


def test_factor_engine_rejects_missing_inputs_and_duplicate_names():
    registry = build_default_registry()
    frame = pd.DataFrame(
        [{"date": "20250101", "symbol": "AAA", "signal_close": 10.0}]
    )
    with pytest.raises(ValueError, match="missing inputs"):
        registry.compute(frame, ["book_to_price"])

    custom = FactorRegistry()
    definition = FactorDefinition(
        name="example",
        version=1,
        category=FactorCategory.VALUE,
        direction=FactorDirection.HIGH,
        description="example",
        inputs=("value",),
        lookback=1,
        availability=Availability.MARKET_T_PLUS_1,
        calculate=lambda values: values["value"],
    )
    custom.register(definition)
    with pytest.raises(ValueError, match="already registered"):
        custom.register(definition)


def test_non_finite_factor_values_are_normalized_to_missing():
    registry = build_default_registry()
    frame = pd.DataFrame(
        [
            {
                "date": "20250101", "symbol": "AAA", "pb": 0.0,
                "pe_ttm": -2.0, "dv_ttm": np.nan,
            }
        ]
    )

    result = registry.compute(
        frame, ["book_to_price", "earnings_yield", "dividend_yield_ttm"]
    )

    assert result[["book_to_price", "earnings_yield", "dividend_yield_ttm"]].isna().all().all()
