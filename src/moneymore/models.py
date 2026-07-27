from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Order:
    symbol: str
    side: Side
    quantity: int
    signal_date: str


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: Side
    quantity: int
    price: float
    fee: float
    trade_date: str

