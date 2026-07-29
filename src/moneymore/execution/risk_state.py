from __future__ import annotations

from dataclasses import dataclass

from ..config import BacktestConfig


@dataclass(frozen=True)
class RiskStateDecision:
    proposed_state: str
    reason_code: str
    explanation: str


def evaluate_account_risk_state(
    config: BacktestConfig,
    equity: float,
    cash: float,
    gross_exposure: float,
    drawdown: float,
    data_health_status: str,
    reconciled: bool,
) -> RiskStateDecision:
    if not reconciled:
        return RiskStateDecision(
            "SUSPENDED", "RECONCILIATION_FAILED", "对账失败，暂停全部模拟交易"
        )
    if data_health_status == "BLOCKED":
        return RiskStateDecision(
            "SUSPENDED", "DATA_QUALITY_BLOCKED", "数据质量阻断，暂停全部模拟交易"
        )
    if drawdown <= -config.max_drawdown:
        return RiskStateDecision(
            "SELL_ONLY", "DRAWDOWN_LIMIT", "达到最大回撤线，只允许降低持仓"
        )
    if gross_exposure > config.max_gross_exposure + 1e-9:
        return RiskStateDecision(
            "SELL_ONLY", "GROSS_EXPOSURE_LIMIT", "总仓位超过上限，只允许卖出"
        )
    cash_fraction = cash / equity if equity else 0.0
    if drawdown <= -config.max_drawdown * 2 / 3:
        return RiskStateDecision(
            "REDUCE_ONLY", "DRAWDOWN_WARNING", "回撤进入预警区，禁止增加风险"
        )
    if cash_fraction < 0.10:
        return RiskStateDecision(
            "REDUCE_ONLY", "CASH_BUFFER_LOW", "现金低于10%，禁止增加风险"
        )
    return RiskStateDecision("NORMAL", "RISK_CHECKS_PASS", "账户风险检查通过")


def constrained_target_weight(
    state: str, model_target: float, actual_weight: float
) -> float | None:
    if state == "NORMAL":
        return model_target
    if state == "REDUCE_ONLY":
        return min(model_target, actual_weight)
    if state == "SELL_ONLY":
        return 0.0
    if state == "SUSPENDED":
        return None
    raise ValueError(f"invalid risk state: {state}")
