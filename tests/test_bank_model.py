import pandas as pd
import pytest

from moneymore.research.bank_model import (
    score_bank_factors,
    topk_dropout_targets,
)


def _factor_rows(date: str, count: int = 15):
    rows = []
    for index in range(count):
        value = float(index) / count
        rows.append(
            {
                "date": date,
                "symbol": f"S{index:02d}",
                "dividend_yield_ttm": value,
                "earnings_yield": value,
                "book_to_price": value,
                "volatility_60": value,
                "drawdown_120": value,
                "momentum_252_21": value,
                "return_120": value,
                "roe": value,
                "quarterly_revenue_growth": value,
            }
        )
    return rows


def test_bank_score_has_fixed_group_weights_and_minimum_coverage():
    frame = pd.DataFrame(_factor_rows("2025-01-31"))
    frame.loc[0, ["roe", "quarterly_revenue_growth"]] = None

    result = score_bank_factors(frame)

    assert result.loc[14, "score"] > result.loc[1, "score"]
    assert result.loc[0, "available_groups"] == 3
    assert result.loc[0, "active_weight"] == pytest.approx(0.85)


def test_topk_buffer_prevents_churn_and_limits_normal_replacements():
    first = pd.DataFrame(
        {
            "date": "2025-01-31",
            "symbol": [f"S{i:02d}" for i in range(15)],
            "score": list(reversed(range(15))),
        }
    )
    second = first.copy()
    second["date"] = "2025-02-28"
    second["score"] = second["score"].where(
        ~second["symbol"].isin(["S00", "S01", "S02"]), -10
    )

    targets = topk_dropout_targets(
        pd.concat([first, second]), top_k=8, exit_rank=12, max_replacements=2
    )
    january = set(
        targets.loc[
            (targets["date"] == pd.Timestamp("2025-01-31"))
            & targets["selected"],
            "symbol",
        ]
    )
    february = set(
        targets.loc[
            (targets["date"] == pd.Timestamp("2025-02-28"))
            & targets["selected"],
            "symbol",
        ]
    )

    assert len(january) == 8
    assert len(february) == 8
    assert len(january - february) == 2
