from __future__ import annotations

from dataclasses import dataclass

from ..models import Side
from ..signals import SignalDecision


@dataclass(frozen=True)
class PortfolioSnapshot:
    cash: float
    equity: float
    position_quantity: int
    reference_price: float


@dataclass(frozen=True)
class OrderIntent:
    idempotency_key: str
    strategy_id: str
    symbol: str
    side: Side
    quantity: int
    signal_date: str
    reason_code: str


@dataclass(frozen=True)
class RiskResult:
    accepted: bool
    intent: OrderIntent | None
    rejection_code: str | None


def create_order_intent(
    decision: SignalDecision,
    portfolio: PortfolioSnapshot,
    lot_size: int = 100,
    max_symbol_weight: float = 0.10,
) -> RiskResult:
    if portfolio.equity <= 0 or portfolio.reference_price <= 0:
        return RiskResult(False, None, "INVALID_PORTFOLIO_SNAPSHOT")
    if decision.target_weight < 0 or decision.target_weight > max_symbol_weight:
        return RiskResult(False, None, "TARGET_WEIGHT_EXCEEDS_LIMIT")
    target_value = portfolio.equity * decision.target_weight
    target_quantity = int(target_value / portfolio.reference_price / lot_size) * lot_size
    difference = target_quantity - portfolio.position_quantity
    if difference == 0:
        return RiskResult(False, None, "NO_REBALANCE_REQUIRED")
    side = Side.BUY if difference > 0 else Side.SELL
    quantity = abs(difference)
    if side is Side.SELL:
        quantity = min(quantity, portfolio.position_quantity)
    estimated_cost = quantity * portfolio.reference_price
    if side is Side.BUY and estimated_cost > portfolio.cash:
        return RiskResult(False, None, "INSUFFICIENT_CASH")
    key = (
        f"{decision.strategy_id}:{decision.symbol}:"
        f"{decision.as_of_date}:{side.value}:{quantity}"
    )
    return RiskResult(
        True,
        OrderIntent(
            idempotency_key=key,
            strategy_id=decision.strategy_id,
            symbol=decision.symbol,
            side=side,
            quantity=quantity,
            signal_date=decision.as_of_date,
            reason_code=decision.reason_code,
        ),
        None,
    )
