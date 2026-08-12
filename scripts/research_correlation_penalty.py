from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from moneymore.data.store import ParquetStore
from moneymore.portfolio_constructor import (
    adjusted_close_panel,
    global_topk_portfolio,
    trailing_return_correlation,
)
from moneymore.research.sector_model import build_global_factor_panel

ROOT = Path(__file__).resolve().parents[1]
STORE = ParquetStore(ROOT / "data")
CONFIG = yaml.safe_load((ROOT / "configs" / "sector_models.yaml").read_text(encoding="utf-8"))
POLICY = CONFIG["global_selection"]
symbols = set(STORE.read("bank_model_scores")["symbol"].astype(str))
for definition in CONFIG["universes"].values():
    symbols.update(map(str, definition["holdings"]))
scores = build_global_factor_panel(STORE, sorted(symbols), POLICY["factors"])
scores["date"] = pd.to_datetime(scores["date"])
prices = adjusted_close_panel(STORE, sorted(symbols))
dates = sorted(set(scores["date"]) & set(prices.index))
correlations = {
    date: trailing_return_correlation(
        prices, date, int(POLICY["correlation_lookback"])
    )
    for date in dates[:-1]
}
penalties = [0.0, 0.25, 0.50, 0.75, 1.0]
rows = []
for penalty in penalties:
    held: set[str] = set()
    prior_weights: dict[str, float] = {}
    observations = []
    for date, next_date in pairwise(dates):
        day = scores.loc[scores["date"] == date]
        correlation = correlations[date]
        selected, weights, _ = global_topk_portfolio(
            day, held, symbol_column="symbol", top_k=int(POLICY["top_k"]),
            exit_rank=int(POLICY["exit_rank"]), max_replacements=int(POLICY["max_replacements"]),
            minimum_weight=float(POLICY["minimum_weight"]), maximum_weight=float(POLICY["maximum_weight"]),
            correlation=correlation, correlation_penalty=penalty,
        )
        period_returns = prices.loc[next_date, selected] / prices.loc[date, selected] - 1
        gross_return = sum(weights[symbol] * float(period_returns[symbol]) for symbol in selected if pd.notna(period_returns[symbol]))
        turnover = sum(abs(weights.get(symbol, 0.0) - prior_weights.get(symbol, 0.0)) for symbol in set(weights) | set(prior_weights)) / 2
        observations.append({"date": next_date, "return": gross_return - turnover * 0.00106, "turnover": turnover})
        held, prior_weights = set(selected), weights
    frame = pd.DataFrame(observations)
    for segment, start, end in (("train", "2015-01-01", "2021-12-31"), ("valid", "2022-01-01", "2024-12-31"), ("test", "2025-01-01", "2099-12-31")):
        part = frame.loc[frame["date"].between(start, end)].copy()
        equity = (1 + part["return"]).cumprod()
        drawdown = equity / equity.cummax() - 1
        annual_return = float(equity.iloc[-1] ** (12 / len(part)) - 1) if len(part) else np.nan
        annual_vol = float(part["return"].std(ddof=1) * np.sqrt(12)) if len(part) > 1 else np.nan
        rows.append({"penalty": penalty, "segment": segment, "months": len(part), "annual_return": annual_return, "annual_volatility": annual_vol, "sharpe": annual_return / annual_vol if annual_vol else np.nan, "max_drawdown": float(drawdown.min()) if len(part) else np.nan, "average_turnover": float(part["turnover"].mean()) if len(part) else np.nan})

result = pd.DataFrame(rows)
print(result.to_string(index=False))
