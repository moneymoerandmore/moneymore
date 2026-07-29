from __future__ import annotations

import pandas as pd

from .data.store import ParquetStore
from .execution.paper import PaperBroker


def process_corporate_actions(
    store: ParquetStore,
    broker: PaperBroker,
    account_id: str,
    trade_date: str,
    symbols: set[str],
) -> dict[str, object]:
    """Register record-date entitlements, then settle due cash and shares."""
    registered: list[dict[str, object]] = []
    try:
        dividends = store.read("dividend")
    except FileNotFoundError:
        dividends = pd.DataFrame()
    if not dividends.empty:
        actions = _normalized_actions(dividends, symbols)
        for row in actions.loc[actions["record_date"] == trade_date].to_dict("records"):
            status = broker.register_corporate_entitlement(
                account_id=account_id,
                symbol=str(row["ts_code"]),
                action_key=str(row["action_key"]),
                record_date=trade_date,
                cash_per_share=float(row["cash_per_share"]),
                stock_ratio=float(row["stock_ratio"]),
                cash_pay_date=_optional_date(row["cash_pay_date"]),
                stock_list_date=_optional_date(row["stock_list_date"]),
            )
            registered.append(
                {
                    "symbol": row["ts_code"],
                    "action_key": row["action_key"],
                    "status": status,
                    "cash_per_share": row["cash_per_share"],
                    "stock_ratio": row["stock_ratio"],
                }
            )
    settled = broker.settle_corporate_actions(account_id, trade_date)
    return {"registered": registered, "settled": settled}


def _normalized_actions(
    dividends: pd.DataFrame, symbols: set[str]
) -> pd.DataFrame:
    frame = dividends.loc[dividends["ts_code"].astype(str).isin(symbols)].copy()
    for column in ("record_date", "pay_date", "div_listdate", "ex_date"):
        frame[column] = frame[column].fillna("").astype(str).str.replace(".0", "")
    frame = frame.loc[frame["record_date"].str.fullmatch(r"\d{8}")]
    cash_tax = pd.to_numeric(frame["cash_div_tax"], errors="coerce")
    cash_gross = pd.to_numeric(frame["cash_div"], errors="coerce")
    frame["cash_per_share"] = cash_tax.fillna(cash_gross).fillna(0).clip(lower=0)
    frame["stock_ratio"] = (
        pd.to_numeric(frame["stk_div"], errors="coerce").fillna(0).clip(lower=0)
    )
    frame["cash_pay_date"] = frame["pay_date"].where(
        frame["pay_date"].str.fullmatch(r"\d{8}"), frame["ex_date"]
    )
    frame["stock_list_date"] = frame["div_listdate"].where(
        frame["div_listdate"].str.fullmatch(r"\d{8}"), frame["ex_date"]
    )
    frame["action_key"] = (
        frame["ts_code"].astype(str)
        + ":"
        + frame["end_date"].astype(str)
        + ":"
        + frame["record_date"]
    )
    frame = frame.sort_values(
        ["ts_code", "end_date", "record_date", "ann_date"],
        na_position="first",
    ).drop_duplicates("action_key", keep="last")
    return frame


def _optional_date(value: object) -> str | None:
    text = str(value)
    return text if len(text) == 8 and text.isdigit() else None
