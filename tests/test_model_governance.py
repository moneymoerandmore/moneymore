import pandas as pd

from moneymore.research.governance import evaluate_bank_model_promotion


def test_bank_model_cannot_promote_without_train_quality_and_forward_period():
    report = pd.DataFrame(
        [
            {
                "model": "bank_multifactor_v1", "period": "in_sample",
                "sharpe": 0.1, "max_drawdown": -0.2, "cagr": 0.01,
            },
            {
                "model": "bank_multifactor_v1", "period": "out_of_sample",
                "sharpe": 0.9, "max_drawdown": -0.15, "cagr": 0.1,
            },
        ]
    )
    robustness = pd.DataFrame(
        [
            {
                "test": "cost", "scenario": "cost_2x",
                "period": "out_of_sample", "cagr": 0.09,
            }
        ]
    )

    result = evaluate_bank_model_promotion(
        report, robustness, has_pristine_forward_period=False
    )

    assert not result["eligible"]
    assert result["status"] == "RESEARCH_ONLY"
    assert result["failures"] == ["train_sharpe", "pristine_forward_period"]
