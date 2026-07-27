from .config import BacktestConfig
from .models import Side


def execution_price(open_price: float, side: Side, slippage_bps: float) -> float:
    direction = 1 if side is Side.BUY else -1
    return open_price * (1 + direction * slippage_bps / 10_000)


def transaction_fee(notional: float, side: Side, config: BacktestConfig) -> float:
    commission = max(config.minimum_commission, notional * config.commission_rate)
    transfer_fee = notional * config.transfer_fee_rate
    stamp_duty = notional * config.stamp_duty_rate if side is Side.SELL else 0.0
    return commission + transfer_fee + stamp_duty

