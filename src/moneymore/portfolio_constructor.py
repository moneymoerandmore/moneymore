from __future__ import annotations

import numpy as np
import pandas as pd

from .data.store import ParquetStore


def global_topk_portfolio(
    scores: pd.DataFrame,
    incumbents: set[str],
    *,
    symbol_column: str,
    top_k: int,
    exit_rank: int,
    max_replacements: int,
    gross_exposure: float = 1.0,
    minimum_weight: float = 0.05,
    maximum_weight: float = 0.15,
    correlation: pd.DataFrame | None = None,
    correlation_penalty: float = 0.0,
    cluster_correlation_threshold: float | None = None,
    maximum_cluster_members: int | None = None,
) -> tuple[list[str], dict[str, float], pd.DataFrame]:
    """Build one global rank portfolio; sector metadata is intentionally ignored."""
    if not 0 < top_k <= exit_rank:
        raise ValueError("top_k must be positive and no greater than exit_rank")
    if top_k * minimum_weight > gross_exposure + 1e-12:
        raise ValueError("minimum weights exceed gross exposure")
    if top_k * maximum_weight < gross_exposure - 1e-12:
        raise ValueError("maximum weights cannot deploy gross exposure")
    ranking = scores.reset_index(drop=True).dropna(subset=["score"]).sort_values(
        ["score", symbol_column], ascending=[False, True]
    ).drop_duplicates(symbol_column, keep="first").copy()
    ranking = _diversification_adjusted_order(
        ranking, symbol_column, correlation, correlation_penalty, exit_rank,
        cluster_correlation_threshold, maximum_cluster_members,
    )
    ranking["global_rank"] = range(1, len(ranking) + 1)
    ranks = dict(zip(ranking[symbol_column].astype(str), ranking["global_rank"], strict=True))
    eligible = set(ranks)
    held = set(incumbents) & eligible
    forced_cluster_exits: set[str] = set()
    if correlation is not None and cluster_correlation_threshold is not None and maximum_cluster_members is not None:
        clusters = _correlation_clusters(list(held), correlation, cluster_correlation_threshold)
        grouped: dict[str, list[str]] = {}
        for symbol in held:
            grouped.setdefault(clusters.get(symbol, symbol), []).append(symbol)
        for members in grouped.values():
            ordered_members = sorted(members, key=ranks.__getitem__)
            forced_cluster_exits.update(ordered_members[maximum_cluster_members:])
        held -= forced_cluster_exits
    exits = sorted(
        (symbol for symbol in held if ranks[symbol] > exit_rank),
        key=ranks.__getitem__,
        reverse=True,
    )[:max_replacements]
    held -= set(exits)
    additions = [
        symbol for symbol in ranking[symbol_column].astype(str) if symbol not in held
    ][: max(0, top_k - len(held))]
    held.update(additions)
    selected = sorted(held, key=ranks.__getitem__)[:top_k]
    raw = {symbol: float(top_k - index) for index, symbol in enumerate(selected)}
    weights = _bounded_pro_rata(raw, gross_exposure, minimum_weight, maximum_weight)
    ranking["selected"] = ranking[symbol_column].astype(str).isin(selected)
    ranking["target_weight"] = ranking[symbol_column].astype(str).map(weights).fillna(0.0)
    ranking["forced_risk_exit"] = ranking[symbol_column].astype(str).isin(forced_cluster_exits)
    return selected, weights, ranking


def trailing_return_correlation(
    price_panel: pd.DataFrame, cutoff: str | pd.Timestamp, lookback: int = 120
) -> pd.DataFrame:
    eligible = price_panel.loc[price_panel.index <= pd.Timestamp(cutoff)].tail(lookback + 1)
    return eligible.pct_change(fill_method=None).tail(lookback).corr(min_periods=40)


def adjusted_close_panel(store: ParquetStore, symbols: list[str]) -> pd.DataFrame:
    requested = sorted(set(symbols))
    if not requested:
        return pd.DataFrame()
    daily = store.read(
        "daily",
        columns=["ts_code", "trade_date", "close"],
        filters=[("ts_code", "in", requested)],
    )
    adjustment = store.read(
        "adj_factor",
        columns=["ts_code", "trade_date", "adj_factor"],
        filters=[("ts_code", "in", requested)],
    )
    bars = daily.merge(
        adjustment,
        on=["ts_code", "trade_date"],
        how="inner",
        validate="one_to_one",
    ).sort_values(["ts_code", "trade_date"])
    latest_factor = bars.groupby("ts_code")["adj_factor"].transform("last")
    bars["adjusted_close"] = (
        pd.to_numeric(bars["close"], errors="coerce")
        * pd.to_numeric(bars["adj_factor"], errors="coerce")
        / latest_factor
    )
    bars["date"] = pd.to_datetime(bars["trade_date"], format="%Y%m%d")
    return (
        bars.pivot(index="date", columns="ts_code", values="adjusted_close")
        .sort_index()
        .rename_axis(columns=None)
    )


def _diversification_adjusted_order(
    ranking: pd.DataFrame,
    symbol_column: str,
    correlation: pd.DataFrame | None,
    penalty: float,
    depth: int,
    cluster_threshold: float | None,
    maximum_cluster_members: int | None,
) -> pd.DataFrame:
    if correlation is None or correlation.empty or (
        penalty <= 0 and not cluster_threshold
    ):
        return ranking
    working = ranking.copy()
    symbols = working[symbol_column].astype(str).tolist()
    score_rank = {symbol: index for index, symbol in enumerate(symbols)}
    denominator = max(len(symbols) - 1, 1)
    alpha = {symbol: 1.0 - score_rank[symbol] / denominator for symbol in symbols}
    ordered: list[str] = []
    remaining = set(symbols)
    adjusted: dict[str, float] = {}
    clusters = _correlation_clusters(symbols, correlation, cluster_threshold)
    while remaining and len(ordered) < depth:
        candidates = []
        for symbol in remaining:
            if maximum_cluster_members is not None:
                cluster = clusters.get(symbol, symbol)
                if sum(clusters.get(item, item) == cluster for item in ordered) >= maximum_cluster_members:
                    continue
            correlations = [
                float(correlation.loc[symbol, existing])
                for existing in ordered
                if symbol in correlation.index
                and existing in correlation.columns
                and pd.notna(correlation.loc[symbol, existing])
            ]
            redundancy = float(np.mean(np.clip(correlations, 0.0, 1.0))) if correlations else 0.0
            utility = alpha[symbol] - penalty * redundancy
            candidates.append((utility, alpha[symbol], symbol))
        if not candidates:
            candidates = [(alpha[symbol], alpha[symbol], symbol) for symbol in remaining]
        utility, _, chosen = max(candidates)
        ordered.append(chosen)
        adjusted[chosen] = utility
        remaining.remove(chosen)
    ordered.extend(symbol for symbol in symbols if symbol in remaining)
    result = working.set_index(symbol_column).loc[ordered].reset_index()
    result["diversification_adjusted_score"] = result[symbol_column].astype(str).map(adjusted)
    return result


def _correlation_clusters(
    symbols: list[str], correlation: pd.DataFrame, threshold: float | None
) -> dict[str, str]:
    if threshold is None:
        return {symbol: symbol for symbol in symbols}
    parent = {symbol: symbol for symbol in symbols}

    def find(symbol: str) -> str:
        while parent[symbol] != symbol:
            parent[symbol] = parent[parent[symbol]]
            symbol = parent[symbol]
        return symbol

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    available = [symbol for symbol in symbols if symbol in correlation.index]
    for index, left in enumerate(available):
        for right in available[index + 1 :]:
            value = correlation.loc[left, right]
            if pd.notna(value) and float(value) >= threshold:
                union(left, right)
    return {symbol: find(symbol) for symbol in symbols}


def _bounded_pro_rata(
    raw: dict[str, float], total: float, minimum: float, maximum: float
) -> dict[str, float]:
    weights = {symbol: minimum for symbol in raw}
    remaining = total - minimum * len(raw)
    active = set(raw)
    while active and remaining > 1e-12:
        denominator = sum(raw[symbol] for symbol in active)
        proposed = {
            symbol: remaining * raw[symbol] / denominator for symbol in active
        }
        capped = {
            symbol
            for symbol, increment in proposed.items()
            if weights[symbol] + increment >= maximum - 1e-12
        }
        if not capped:
            for symbol, increment in proposed.items():
                weights[symbol] += increment
            break
        for symbol in capped:
            addition = maximum - weights[symbol]
            weights[symbol] += addition
            remaining -= addition
            active.remove(symbol)
    return weights
