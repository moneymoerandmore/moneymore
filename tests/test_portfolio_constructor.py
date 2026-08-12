import pandas as pd
import pytest

from moneymore.portfolio_constructor import global_topk_portfolio


def test_global_portfolio_ignores_sector_and_weights_by_rank() -> None:
    scores = pd.DataFrame(
        {
            "symbol": list("ABCDEFGHIJKL"),
            "score": list(reversed(range(12))),
            "sector": ["bank"] * 10 + ["chip"] * 2,
        }
    )
    selected, weights, _ = global_topk_portfolio(
        scores, set(), symbol_column="symbol", top_k=10, exit_rank=15,
        max_replacements=3, gross_exposure=1.0, minimum_weight=0.05,
        maximum_weight=0.15,
    )
    assert selected == list("ABCDEFGHIJ")
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["A"] <= 0.15 + 1e-12
    assert weights["A"] > weights["J"] >= 0.05


def test_global_portfolio_retains_incumbent_inside_exit_buffer() -> None:
    scores = pd.DataFrame({"symbol": list("ABCDEFGHIJKLMNOP"), "score": list(reversed(range(16)))})
    selected, _, _ = global_topk_portfolio(
        scores, {"O"}, symbol_column="symbol", top_k=10, exit_rank=15,
        max_replacements=3,
    )
    assert "O" in selected
    assert "J" not in selected


def test_correlation_cluster_limits_duplicate_risk_without_sector_labels() -> None:
    symbols = list("ABCDEFGHIJKLMNOP")
    scores = pd.DataFrame({"symbol": symbols, "score": list(reversed(range(16)))})
    correlation = pd.DataFrame(0.0, index=symbols, columns=symbols)
    correlation.loc[list("ABCDEF"), list("ABCDEF")] = 0.9
    selected, _, _ = global_topk_portfolio(
        scores, set(), symbol_column="symbol", top_k=10, exit_rank=15,
        max_replacements=3, correlation=correlation,
        cluster_correlation_threshold=0.65, maximum_cluster_members=3,
    )
    assert len(set(selected) & set("ABCDEF")) == 3
