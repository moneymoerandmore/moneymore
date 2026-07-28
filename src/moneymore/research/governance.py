from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class PromotionCriteria:
    minimum_train_sharpe: float = 0.30
    minimum_validation_sharpe: float = 0.60
    maximum_validation_drawdown: float = 0.20
    minimum_double_cost_cagr: float = 0.0
    require_pristine_forward_period: bool = True


def evaluate_bank_model_promotion(
    report: pd.DataFrame,
    robustness: pd.DataFrame,
    has_pristine_forward_period: bool,
    criteria: PromotionCriteria | None = None,
) -> dict[str, object]:
    criteria = criteria or PromotionCriteria()
    train = _single(report, model="bank_multifactor_v1", period="in_sample")
    validation = _single(
        report, model="bank_multifactor_v1", period="out_of_sample"
    )
    double_cost = _single(
        robustness,
        test="cost",
        scenario="cost_2x",
        period="out_of_sample",
    )
    checks = {
        "train_sharpe": float(train["sharpe"]) >= criteria.minimum_train_sharpe,
        "validation_sharpe": (
            float(validation["sharpe"]) >= criteria.minimum_validation_sharpe
        ),
        "validation_drawdown": (
            abs(float(validation["max_drawdown"]))
            <= criteria.maximum_validation_drawdown
        ),
        "double_cost_cagr": (
            float(double_cost["cagr"]) >= criteria.minimum_double_cost_cagr
        ),
        "pristine_forward_period": (
            has_pristine_forward_period
            or not criteria.require_pristine_forward_period
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "eligible": not failures,
        "status": "ELIGIBLE_FOR_PAPER" if not failures else "RESEARCH_ONLY",
        "checks": checks,
        "failures": failures,
        "criteria": {
            "minimum_train_sharpe": criteria.minimum_train_sharpe,
            "minimum_validation_sharpe": criteria.minimum_validation_sharpe,
            "maximum_validation_drawdown": criteria.maximum_validation_drawdown,
            "minimum_double_cost_cagr": criteria.minimum_double_cost_cagr,
            "require_pristine_forward_period": (
                criteria.require_pristine_forward_period
            ),
        },
    }


def _single(frame: pd.DataFrame, **filters: object) -> pd.Series:
    selected = frame
    for column, value in filters.items():
        selected = selected.loc[selected[column] == value]
    if len(selected) != 1:
        raise ValueError(f"expected one row for {filters}, found {len(selected)}")
    return selected.iloc[0]
