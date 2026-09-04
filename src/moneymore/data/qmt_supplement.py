from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

FINANCIAL_COLUMNS = {
    "s_fa_ocfps": "qmt_ocfps",
    "s_fa_bps": "qmt_bps",
    "s_fa_eps_basic": "qmt_eps",
    "du_return_on_equity": "qmt_roe",
    "sales_gross_profit": "qmt_gross_margin",
    "inc_revenue_rate": "qmt_revenue_growth",
    "inc_net_profit_rate": "qmt_net_profit_growth",
    "sales_cash_flow": "qmt_sales_cash_flow",
    "gear_ratio": "qmt_gear_ratio",
    "inventory_turnover": "qmt_inventory_turnover",
}


def normalize_qmt_supplement(
    payload: Mapping[str, Mapping[str, pd.DataFrame]],
) -> dict[str, pd.DataFrame]:
    financial_rows: list[pd.DataFrame] = []
    holder_rows: list[pd.DataFrame] = []
    top10_rows: list[pd.DataFrame] = []
    for symbol, tables in payload.items():
        financial = tables.get("PershareIndex", pd.DataFrame()).copy()
        if not financial.empty:
            financial = financial.rename(
                columns={
                    "m_timetag": "end_date",
                    "m_anntime": "ann_date",
                    **FINANCIAL_COLUMNS,
                }
            )
            financial["ts_code"] = symbol
            selected = ["ts_code", "ann_date", "end_date", *FINANCIAL_COLUMNS.values()]
            financial_rows.append(_valid_dates(financial, selected))

        holders = tables.get("HolderNum", pd.DataFrame()).copy()
        if not holders.empty:
            holders = holders.rename(
                columns={
                    "declareDate": "ann_date",
                    "endDate": "end_date",
                    "shareholder": "shareholder_count",
                    "shareholderA": "shareholder_a_count",
                    "shareholderH": "shareholder_h_count",
                }
            )
            holders["ts_code"] = symbol
            selected = [
                "ts_code",
                "ann_date",
                "end_date",
                "shareholder_count",
                "shareholder_a_count",
                "shareholder_h_count",
            ]
            holder_rows.append(_valid_dates(holders, selected))

        for table_name, holder_type in (
            ("Top10Holder", "total"),
            ("Top10FlowHolder", "float"),
        ):
            top10 = tables.get(table_name, pd.DataFrame()).copy()
            if top10.empty:
                continue
            top10 = top10.rename(
                columns={"declareDate": "ann_date", "endDate": "end_date"}
            )
            top10["ratio"] = pd.to_numeric(top10["ratio"], errors="coerce")
            grouped = (
                top10.groupby(["ann_date", "end_date"], as_index=False)["ratio"]
                .sum(min_count=1)
                .rename(columns={"ratio": "top10_ratio"})
            )
            grouped["ts_code"] = symbol
            grouped["holder_type"] = holder_type
            top10_rows.append(
                _valid_dates(
                    grouped,
                    ["ts_code", "ann_date", "end_date", "holder_type", "top10_ratio"],
                )
            )
    return {
        "qmt_financial_indicator": _concat(financial_rows),
        "qmt_holder_count": _concat(holder_rows),
        "qmt_top10_concentration": _concat(top10_rows),
    }


def qmt_candidate_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Derive research-only factors; callers decide whether they pass promotion gates."""
    result = frame.copy()
    result["qmt_cashflow_quality"] = _numeric(result, "qmt_sales_cash_flow")
    result["qmt_low_leverage"] = -_numeric(result, "qmt_gear_ratio")
    result["qmt_holder_count_change"] = result.groupby("symbol", sort=False)[
        "shareholder_count"
    ].pct_change(fill_method=None)
    result["qmt_holder_concentration"] = _numeric(result, "top10_float_ratio") / 100.0
    return result


def _valid_dates(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = frame.reindex(columns=columns).copy()
    result["ann_date"] = result["ann_date"].astype(str).str.replace(".0", "", regex=False)
    result["end_date"] = result["end_date"].astype(str).str.replace(".0", "", regex=False)
    valid = result["ann_date"].str.fullmatch(r"\d{8}") & result["end_date"].str.fullmatch(
        r"\d{8}"
    )
    return result.loc[valid].replace([np.inf, -np.inf], np.nan)


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")
