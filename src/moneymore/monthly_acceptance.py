from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AcceptanceCheck:
    code: str
    passed: bool
    observed: object
    required: object
    explanation: str


@dataclass(frozen=True)
class MonthlyAcceptance:
    cycle_id: str
    start_date: str
    observation_date: str
    status: str
    progress: float
    expected_earliest_completion: str
    checks: list[dict[str, object]]
    evidence: dict[str, object]
    report_path: str | None


def evaluate_monthly_cycle(
    *,
    account_daily: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    model_versions: list[dict[str, Any]],
    start_date: str,
    observation_date: str,
    minimum_trading_days: int = 20,
) -> MonthlyAcceptance:
    daily = sorted(
        (
            row
            for row in account_daily
            if start_date <= str(row["trade_date"]) <= observation_date
        ),
        key=lambda row: str(row["trade_date"]),
    )
    cycle_fills = [
        row
        for row in fills
        if start_date <= str(row["trade_date"]) <= observation_date
    ]
    buys = [row for row in cycle_fills if row["side"] == "BUY"]
    sells = [row for row in cycle_fills if row["side"] == "SELL"]
    first_buy_date = min((str(row["trade_date"]) for row in buys), default=None)
    initial_symbols = {
        str(row["symbol"])
        for row in buys
        if first_buy_date and str(row["trade_date"]) == first_buy_date
    }
    later_buy_symbols = {
        str(row["symbol"])
        for row in buys
        if first_buy_date and str(row["trade_date"]) > first_buy_date
    }
    new_symbols = sorted(later_buy_symbols - initial_symbols)
    buy_dates = {str(row["trade_date"]) for row in buys}
    sell_dates = {str(row["trade_date"]) for row in sells}
    rebalance_dates = sorted(buy_dates & sell_dates)
    holding_days = sum(
        float(row.get("gross_exposure", 0)) > 0
        and str(row["trade_date"]) not in buy_dates
        and str(row["trade_date"]) not in sell_dates
        for row in daily
    )
    hashes = {
        "config_hashes": sorted(
            {
                str(row["config_hash"])
                for row in model_versions
                if start_date <= str(row["data_cutoff"]) <= observation_date
            }
        ),
        "code_hashes": sorted(
            {
                str(row["code_hash"])
                for row in model_versions
                if start_date <= str(row["data_cutoff"]) <= observation_date
            }
        ),
    }
    frozen = (
        len(hashes["config_hashes"]) == 1
        and len(hashes["code_hashes"]) == 1
    )
    reconciled_days = sum(bool(row.get("reconciled")) for row in daily)
    checks = [
        AcceptanceCheck(
            "MINIMUM_TRADING_DAYS",
            len(daily) >= minimum_trading_days,
            len(daily),
            minimum_trading_days,
            "至少观察一个20交易日的完整策略周期",
        ),
        AcceptanceCheck(
            "INITIAL_BUILD",
            bool(buys),
            len(buys),
            ">=1 BUY fill",
            "首次建仓必须产生真实影子成交",
        ),
        AcceptanceCheck(
            "HOLDING_OBSERVED",
            holding_days > 0,
            holding_days,
            ">=1 day",
            "至少存在一个有持仓且无换手的观察日",
        ),
        AcceptanceCheck(
            "SELL_OBSERVED",
            bool(sells),
            len(sells),
            ">=1 SELL fill",
            "必须验证卖出、费用和T+1可用数量",
        ),
        AcceptanceCheck(
            "REBALANCE_OBSERVED",
            bool(rebalance_dates),
            rebalance_dates,
            ">=1 buy/sell date",
            "同一周期内必须出现买卖并行的换仓日",
        ),
        AcceptanceCheck(
            "NEW_POSITION_OBSERVED",
            bool(new_symbols),
            new_symbols,
            ">=1 new symbol",
            "首次建仓后必须验证新标的买入",
        ),
        AcceptanceCheck(
            "DAILY_RECONCILIATION",
            bool(daily) and reconciled_days == len(daily),
            f"{reconciled_days}/{len(daily)}",
            "all days",
            "周期内每日现金、持仓、订单和成交必须全部对账",
        ),
        AcceptanceCheck(
            "MODEL_FROZEN",
            frozen,
            {
                "config_versions": len(hashes["config_hashes"]),
                "code_versions": len(hashes["code_hashes"]),
            },
            "1 config + 1 code",
            "验收期不得修改核心配置或模型代码",
        ),
    ]
    passed = sum(check.passed for check in checks)
    status = "PASSED" if passed == len(checks) else "COLLECTING_EVIDENCE"
    cycle_id = f"shadow-{start_date}-{observation_date}"
    return MonthlyAcceptance(
        cycle_id=cycle_id,
        start_date=start_date,
        observation_date=observation_date,
        status=status,
        progress=passed / len(checks),
        expected_earliest_completion=_add_weekdays(
            start_date, minimum_trading_days - 1
        ),
        checks=[asdict(check) for check in checks],
        evidence={
            "trading_days": len(daily),
            "buy_fills": len(buys),
            "sell_fills": len(sells),
            "holding_days": holding_days,
            "rebalance_dates": rebalance_dates,
            "new_symbols": new_symbols,
            **hashes,
        },
        report_path=None,
    )


def freeze_passed_report(
    result: MonthlyAcceptance, directory: str | Path
) -> MonthlyAcceptance:
    if result.status != "PASSED":
        return result
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{result.cycle_id}.json"
    payload = {
        **asdict(result),
        "report_path": str(target),
        "frozen_at": datetime.now(UTC).isoformat(),
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        payload["frozen_at"] = existing["frozen_at"]
        content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if target.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"immutable acceptance report differs: {target}")
    else:
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
    return MonthlyAcceptance(
        **{
            **asdict(result),
            "report_path": str(target),
        }
    )


def _add_weekdays(start_date: str, count: int) -> str:
    current = datetime.strptime(start_date, "%Y%m%d").replace(tzinfo=UTC)
    added = 0
    while added < count:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current.strftime("%Y%m%d")
