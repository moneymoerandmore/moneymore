from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

from .data.store import ParquetStore
from .exposure_league import (
    PysystemtradeAsymmetricPolicy,
    PysystemtradeTrendCapPolicy,
    build_exposure_policy_history,
    build_pysystemtrade_exposure_history,
)


def build_baseline_overlay_comparison(
    baseline: pd.DataFrame,
    exposure_history: list[dict[str, object]],
    *,
    strategy_id: str = "baseline_pysystemtrade",
    strategy: str = "基线 + pysystemtrade",
) -> list[dict[str, object]]:
    """Build an auditable close-to-close counterfactual for the exposure overlay.

    The exposure observed on T is shifted to the next baseline trading day.  The
    baseline's return is first converted to a stock-sleeve return using the prior
    close gross exposure, then resized to the pysystemtrade target.  This keeps the
    original baseline untouched and avoids applying a close signal retroactively.
    """
    required = {"trade_date", "equity", "gross_exposure"}
    if baseline.empty or not required.issubset(baseline.columns):
        return []
    frame = baseline.copy()
    frame["trade_date"] = frame["trade_date"].astype(str).str.replace("-", "", regex=False)
    frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
    frame["gross_exposure"] = pd.to_numeric(frame["gross_exposure"], errors="coerce")
    frame = frame.dropna(subset=["equity"]).drop_duplicates("trade_date", keep="last").sort_values("trade_date")
    if frame.empty:
        return []
    exposure = {
        str(row.get("trade_date") or row.get("as_of_date")): float(row["target_exposure"])
        for row in exposure_history
        if row.get("target_exposure") is not None
    }
    visible_signals: list[float | None] = []
    last_signal: float | None = None
    for trade_date in frame["trade_date"]:
        visible_signals.append(last_signal)
        if str(trade_date) in exposure:
            last_signal = exposure[str(trade_date)]
    frame["overlay_exposure"] = visible_signals
    frame["baseline_return"] = frame["equity"].pct_change().fillna(0.0)
    prior_gross = frame["gross_exposure"].shift(1)
    sleeve_return = frame["baseline_return"].where(
        prior_gross.isna() | (prior_gross <= 0.05),
        frame["baseline_return"] / prior_gross,
    )
    applied = frame["overlay_exposure"].fillna(prior_gross).fillna(0.0).clip(0.0, 1.0)
    frame["overlay_return"] = sleeve_return * applied
    frame["baseline_nav"] = frame["equity"] / float(frame["equity"].iloc[0])
    frame["overlay_nav"] = (1.0 + frame["overlay_return"]).cumprod()
    rows: list[dict[str, object]] = []
    for row in frame.to_dict("records"):
        common = {"trade_date": str(row["trade_date"])}
        rows.append({**common, "strategy_id": "baseline", "strategy": "原基线", "normalized_nav": float(row["baseline_nav"])})
        rows.append({**common, "strategy_id": strategy_id, "strategy": strategy, "normalized_nav": float(row["overlay_nav"]), "target_exposure": float(row["overlay_exposure"]) if pd.notna(row["overlay_exposure"]) else None})
    return rows


@lru_cache(maxsize=4)
def build_market_risk_snapshot(
    data_root: str,
    daily_mtime_ns: int,
    baseline_mtime_ns: int = 0,
) -> dict[str, object]:
    """Build a point-in-time market-risk overlay from the active A-share universe.

    The overlay is deliberately independent from stock-selection scores.  A signal
    observed after close on T is an exposure proposal for T+1; it does not mutate
    any paper account until it passes forward validation and is explicitly enabled.
    ``daily_mtime_ns`` and ``baseline_mtime_ns`` are cache-version inputs.  The
    latter matters because market data is persisted before the strategy account
    snapshot; caching on daily bars alone can otherwise hide the newest baseline
    NAV until the following trading day.
    """
    del daily_mtime_ns, baseline_mtime_ns  # cache invalidation keys
    store = ParquetStore(Path(data_root))
    universe = store.read("strategy_universe")
    effective = str(universe["effective_date"].astype(str).max())
    symbols = sorted(
        universe.loc[universe["effective_date"].astype(str) == effective, "symbol"]
        .astype(str)
        .unique()
    )
    all_dates = store.read("daily", columns=["trade_date"])["trade_date"].astype(str)
    dates = sorted(all_dates.unique())
    start = dates[max(0, len(dates) - 800)]
    bars = store.read(
        "daily",
        columns=["ts_code", "trade_date", "close", "pre_close"],
        filters=[("trade_date", ">=", start), ("ts_code", "in", symbols)],
    ).copy()
    bars["trade_date"] = bars["trade_date"].astype(str)
    bars["return"] = bars["close"].astype(float) / bars["pre_close"].astype(float) - 1
    daily = bars.groupby("trade_date").agg(
        proxy_return=("return", "mean"),
        coverage=("return", "count"),
    ).sort_index()
    latest = daily.dropna(subset=["proxy_return"]).iloc[-1]
    dated_returns = [
        (str(date), float(value))
        for date, value in daily["proxy_return"].dropna().items()
    ]
    pst_history = build_pysystemtrade_exposure_history(dated_returns)
    asymmetric_history = build_exposure_policy_history(
        dated_returns, PysystemtradeAsymmetricPolicy()
    )
    trend_history = build_exposure_policy_history(
        dated_returns, PysystemtradeTrendCapPolicy()
    )
    league_history = pst_history + asymmetric_history + trend_history
    history_by_method = {
        "pysystemtrade_vol_target": pst_history,
        "pysystemtrade_asymmetric": asymmetric_history,
        "pysystemtrade_trend_cap": trend_history,
    }
    effective_contestants = [
        rows[-1] for rows in history_by_method.values() if rows
    ]
    try:
        baseline = store.read("multi_sector_account_daily")
    except FileNotFoundError:
        baseline = pd.DataFrame()
    comparison = build_baseline_overlay_comparison(baseline, pst_history)
    comparison.extend(
        row for row in build_baseline_overlay_comparison(
            baseline, asymmetric_history,
            strategy_id="baseline_pysystemtrade_asymmetric",
            strategy="基线 + 非对称调仓",
        ) if row["strategy_id"] != "baseline"
    )
    comparison.extend(
        row for row in build_baseline_overlay_comparison(
            baseline, trend_history,
            strategy_id="baseline_pysystemtrade_trend_cap",
            strategy="基线 + 趋势上限",
        ) if row["strategy_id"] != "baseline"
    )
    return {
        "status": "RESEARCH_ONLY",
        "as_of_date": str(latest.name),
        "effective_for": "NEXT_TRADING_DAY",
        "universe_version": effective,
        "universe_size": len(symbols),
        "coverage": int(latest["coverage"]),
        "exposure_league": {
            "league_id": "total_equity_exposure_v1",
            "status": "CONTRACT_READY",
            "output_contract": "target_exposure only; scalar in [0, 1]",
            "stock_selection_integration": "BASELINE_COUNTERFACTUAL",
            "contestants": effective_contestants,
            "audit_contestants": [],
            "history": league_history,
            "strategy_comparison": comparison,
            "comparison_mode": "T日收盘仓位信号作用于下一交易日；历史为反事实模拟，不改写原基线账本",
            "input_asset": "Top1000等权市场代理",
            "history_mode": "逐日仅使用当时可见数据，并继承上一日缓冲后仓位",
        },
    }
