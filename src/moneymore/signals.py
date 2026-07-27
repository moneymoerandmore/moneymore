from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class SignalDecision:
    strategy_id: str
    symbol: str
    as_of_date: str
    target_weight: float
    action: str
    reason_code: str
    close: float
    fast_ma: float | None
    slow_ma: float | None
    history_bars: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def write_signal_artifact(
    decision: SignalDecision, directory: str | Path
) -> Path:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (
        f"{decision.as_of_date}_{decision.symbol.replace('.', '_')}_"
        f"{decision.strategy_id}.json"
    )
    content = json.dumps(decision.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"immutable signal artifact differs: {target}")
        return target
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)
    return target


def trend_decision(
    bars: pd.DataFrame,
    as_of_date: str,
    fast: int = 120,
    slow: int = 250,
    active_weight: float = 0.10,
    strategy_id: str | None = None,
) -> SignalDecision:
    required = {"date", "symbol", "signal_close", "raw_close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"missing signal columns: {sorted(missing)}")
    as_of = pd.Timestamp(as_of_date)
    history = bars.loc[pd.to_datetime(bars["date"]) <= as_of].sort_values("date").copy()
    if history.empty:
        raise ValueError(f"no data available on or before {as_of_date}")
    symbol = str(history["symbol"].iloc[-1])
    actual_date = pd.Timestamp(history["date"].iloc[-1]).strftime("%Y%m%d")
    values = history["signal_close"].astype(float)
    fast_ma = values.rolling(fast).mean()
    slow_ma = values.rolling(slow).mean()
    enough = len(history) >= slow and pd.notna(slow_ma.iloc[-1])
    current_active = bool(
        enough
        and values.iloc[-1] > slow_ma.iloc[-1]
        and fast_ma.iloc[-1] > slow_ma.iloc[-1]
    )
    previous_active = False
    if len(history) > slow:
        previous_active = bool(
            values.iloc[-2] > slow_ma.iloc[-2]
            and fast_ma.iloc[-2] > slow_ma.iloc[-2]
        )

    if not enough:
        action, reason = "STAY_CASH", "INSUFFICIENT_HISTORY"
    elif current_active and not previous_active:
        action, reason = "ENTER", "TREND_CONFIRMED"
    elif current_active:
        action, reason = "HOLD", "TREND_REMAINS_ACTIVE"
    elif previous_active:
        action, reason = "EXIT", "TREND_BROKEN"
    else:
        action, reason = "STAY_CASH", "TREND_INACTIVE"

    return SignalDecision(
        strategy_id=strategy_id or f"cmb_ma_{fast}_{slow}_v1",
        symbol=symbol,
        as_of_date=actual_date,
        target_weight=active_weight if current_active else 0.0,
        action=action,
        reason_code=reason,
        close=float(history["raw_close"].iloc[-1]),
        fast_ma=float(fast_ma.iloc[-1]) if pd.notna(fast_ma.iloc[-1]) else None,
        slow_ma=float(slow_ma.iloc[-1]) if pd.notna(slow_ma.iloc[-1]) else None,
        history_bars=len(history),
    )
