from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..data.research import load_total_return_stock_bars
from ..data.store import ParquetStore


@dataclass(frozen=True)
class BankFactorModel:
    model_id: str = "bank_multifactor_v1"
    value_weight: float = 0.40
    defensive_weight: float = 0.25
    momentum_weight: float = 0.20
    quality_weight: float = 0.15

    @property
    def groups(self) -> dict[str, tuple[str, ...]]:
        return {
            "value": ("dividend_yield_ttm", "earnings_yield", "book_to_price"),
            "defensive": ("volatility_60", "drawdown_120"),
            "momentum": ("momentum_252_21", "return_120"),
            "quality": ("roe", "quarterly_revenue_growth"),
        }

    @property
    def weights(self) -> dict[str, float]:
        return {
            "value": self.value_weight,
            "defensive": self.defensive_weight,
            "momentum": self.momentum_weight,
            "quality": self.quality_weight,
        }


def score_bank_factors(
    factors: pd.DataFrame, model: BankFactorModel | None = None
) -> pd.DataFrame:
    model = model or BankFactorModel()
    required = {"date", "symbol"}
    for columns in model.groups.values():
        required.update(columns)
    missing = sorted(required - set(factors.columns))
    if missing:
        raise ValueError(f"bank factor model missing columns: {missing}")
    scored = factors.copy()
    for group, columns in model.groups.items():
        scored[f"{group}_score"] = scored[list(columns)].mean(
            axis=1, skipna=True
        )
    group_scores = [f"{group}_score" for group in model.groups]
    scored["available_groups"] = scored[group_scores].notna().sum(axis=1)
    scored["score"] = 0.0
    scored["active_weight"] = 0.0
    for group, weight in model.weights.items():
        available = scored[f"{group}_score"].notna()
        scored.loc[available, "score"] += (
            scored.loc[available, f"{group}_score"] * weight
        )
        scored.loc[available, "active_weight"] += weight
    scored["score"] = scored["score"] / scored["active_weight"].replace(0, pd.NA)
    scored.loc[scored["available_groups"] < 3, "score"] = pd.NA
    scored["model_id"] = model.model_id
    return scored


def topk_dropout_targets(
    scores: pd.DataFrame,
    top_k: int = 8,
    exit_rank: int = 12,
    max_replacements: int = 2,
    position_weight: float = 0.10,
) -> pd.DataFrame:
    if not 0 < top_k <= exit_rank:
        raise ValueError("top_k must be positive and no greater than exit_rank")
    if max_replacements < 1:
        raise ValueError("max_replacements must be positive")
    held: set[str] = set()
    previous_universe: set[str] = set()
    rows: list[dict[str, object]] = []
    ordered = scores.copy()
    ordered["date"] = pd.to_datetime(ordered["date"])
    for date, group in ordered.groupby("date", sort=True):
        eligible = group.dropna(subset=["score"]).sort_values(
            ["score", "symbol"], ascending=[False, True]
        )
        ranks = {
            symbol: rank
            for rank, symbol in enumerate(eligible["symbol"], start=1)
        }
        eligible_symbols = set(ranks)
        forced_exits = held - eligible_symbols
        held -= forced_exits
        normal_exits = sorted(
            (symbol for symbol in held if ranks[symbol] > exit_rank),
            key=lambda symbol: ranks[symbol],
            reverse=True,
        )[:max_replacements]
        held -= set(normal_exits)
        vacancies = top_k - len(held)
        additions = [
            symbol for symbol in eligible["symbol"] if symbol not in held
        ][:vacancies]
        held.update(additions)
        output_symbols = eligible_symbols | previous_universe | forced_exits
        for symbol in sorted(output_symbols):
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "target": position_weight if symbol in held else 0.0,
                    "rank": ranks.get(symbol),
                    "selected": symbol in held,
                }
            )
        previous_universe = eligible_symbols
    return pd.DataFrame(rows)


def rebalance_topk_holdings(
    scores: pd.DataFrame,
    previous_holdings: set[str],
    top_k: int = 8,
    exit_rank: int = 12,
    max_replacements: int = 2,
) -> tuple[set[str], pd.DataFrame]:
    eligible = scores.dropna(subset=["score"]).sort_values(
        ["score", "symbol"], ascending=[False, True]
    ).copy()
    eligible["rank"] = range(1, len(eligible) + 1)
    ranks = dict(zip(eligible["symbol"], eligible["rank"], strict=True))
    eligible_symbols = set(ranks)
    held = set(previous_holdings) & eligible_symbols
    normal_exits = sorted(
        (symbol for symbol in held if ranks[symbol] > exit_rank),
        key=lambda symbol: ranks[symbol],
        reverse=True,
    )[:max_replacements]
    held -= set(normal_exits)
    additions = [
        symbol for symbol in eligible["symbol"] if symbol not in held
    ][: max(0, top_k - len(held))]
    held.update(additions)
    eligible["selected"] = eligible["symbol"].isin(held)
    eligible["target"] = eligible["selected"].astype(float) * 0.10
    return held, eligible


def build_bank_backtest_signals(
    store: ParquetStore,
    targets: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    bars = load_bank_backtest_bars(
        store, sorted(targets["symbol"].unique()), start_date, end_date
    )
    return apply_bank_targets(bars, targets)


def load_bank_backtest_bars(
    store: ParquetStore,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    panels = []
    for symbol in symbols:
        bars = load_total_return_stock_bars(store, symbol)
        bars["date"] = pd.to_datetime(bars["date"])
        panels.append(
            bars.loc[
                (bars["date"] >= pd.Timestamp(start_date))
                & (bars["date"] <= pd.Timestamp(end_date))
            ].copy()
        )
    return pd.concat(panels, ignore_index=True).sort_values(
        ["date", "symbol"]
    ).reset_index(drop=True)


def apply_bank_targets(
    bars: pd.DataFrame, targets: pd.DataFrame
) -> pd.DataFrame:
    target_frame = targets.copy()
    target_frame["date"] = pd.to_datetime(target_frame["date"])
    panels = []
    for symbol in sorted(target_frame["symbol"].unique()):
        symbol_bars = bars.loc[bars["symbol"] == symbol].copy()
        symbol_targets = target_frame.loc[
            target_frame["symbol"] == symbol, ["date", "target"]
        ]
        symbol_bars = symbol_bars.merge(
            symbol_targets, on="date", how="left", validate="one_to_one"
        )
        symbol_bars["target"] = symbol_bars["target"].ffill().fillna(0.0)
        panels.append(symbol_bars)
    return pd.concat(panels, ignore_index=True).sort_values(
        ["date", "symbol"]
    ).reset_index(drop=True)
