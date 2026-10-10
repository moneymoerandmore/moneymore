# ruff: noqa: I001
"""Research adaptive TQQQ protection without changing the paper strategy.

The study deliberately keeps every candidate outside the live account.  It uses
only information available at each close, applies the resulting weight on the
next session, charges 6 bps per unit of turnover, and reports expanding
walk-forward results instead of selecting on the full sample.
"""

from __future__ import annotations

import itertools
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from moneymore.leveraged_etf import YahooDailyClient


OUTPUT = ROOT / "data" / "research" / "tqqq_protection"
START = "1999-01-01"
END = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
ANNUAL_DRAG = 0.0095
TURNOVER_COST = 0.0006


def load_history() -> pd.DataFrame:
    client = YahooDailyClient(timeout=60)
    frames = {}
    for symbol in ("QQQ", "TQQQ", "SQQQ", "^VIX"):
        frame = client.history(symbol, START, END).copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        frames[symbol] = frame.set_index("trade_date")
    combined = frames["QQQ"][["close"]].rename(columns={"close": "qqq"})
    combined = combined.join(
        frames["^VIX"][["close"]].rename(columns={"close": "vix"}), how="left"
    ).ffill()
    for symbol in ("TQQQ", "SQQQ"):
        combined = combined.join(
            frames[symbol][["close"]].rename(columns={"close": symbol.lower()}),
            how="left",
        )
    return combined


def baseline_weights(frame: pd.DataFrame) -> pd.DataFrame:
    close = frame["qqq"]
    returns = close.pct_change()
    ma180 = close.rolling(180).mean()
    vol = returns.rolling(20).std() * np.sqrt(252)
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    histogram = macd - macd.ewm(span=9, adjust=False).mean()
    slope = ma180.pct_change(20) * 100
    vix_average = frame["vix"].rolling(20).mean()
    below = close <= ma180
    spike = frame["vix"] > vix_average * 1.20
    bear = below & (((frame["vix"] > 25) & (slope < -0.04)) | (spike & (slope < -0.04)))

    score = pd.Series(0.0, index=frame.index)
    score += np.where(vol <= 0.20, 20, np.where(vol < 0.35, 20 * (1 - (vol - 0.20) / 0.15), 0))
    score += np.where(frame["vix"] <= 15, 20, np.where(frame["vix"] < 35, 20 * (1 - (frame["vix"] - 15) / 20), 0))
    score += np.where(histogram > 0, 20, np.where(histogram > -1, 10, 0))
    score += np.where(slope > 0.05, 20, np.where(slope > -0.02, 10, 0))
    score += np.where(~below, 20, 0)
    tqqq = pd.Series(
        np.where(score >= 80, 1.0, np.where(score >= 60, 0.70 + 0.30 * (score - 60) / 20,
        np.where(score >= 40, 0.30 + 0.40 * (score - 40) / 20, 0.0))), index=frame.index,
    )
    early = below & ~bear & (histogram > 0) & (slope > -0.04)
    tqqq = tqqq.where(~early, 0.50).where(~bear, 0.0)
    sqqq = (((ma180 - close) / ma180 * 2.5).clip(0, 0.20)).where(
        bear & (histogram < -1) & (slope < -0.10), 0.0
    )
    return pd.DataFrame({"tqqq": tqqq.fillna(0), "sqqq": sqqq.fillna(0)})


def managed_overlay(
    base: pd.DataFrame,
    qqq: pd.Series,
    target_volatility: float,
    no_trade_band: float,
    risk_on_step: float,
) -> pd.DataFrame:
    """Continuous risk sizing with asymmetric response and a no-trade band.

    De-risking is immediate.  Re-risking is rate limited, because a false recovery
    signal is much more expensive in a 3x fund than waiting another few sessions.
    The no-trade band is applied to the last implemented target, not yesterday's
    theoretical signal, so turnover estimates are realistic.
    """
    qqq_volatility = qqq.pct_change().rolling(21).std(ddof=1) * np.sqrt(252)
    estimated_position_volatility = 3.0 * qqq_volatility
    scalar = (target_volatility / estimated_position_volatility.clip(lower=1e-8)).clip(
        upper=1.0
    ).fillna(0.0)
    desired = (base["tqqq"] * scalar).clip(0.0, 1.0)
    implemented = np.zeros(len(desired), dtype=float)
    for index, target in enumerate(desired.to_numpy(dtype=float)):
        previous = implemented[index - 1] if index else 0.0
        if target < previous:
            candidate = target
        else:
            candidate = min(target, previous + risk_on_step)
        implemented[index] = candidate if abs(candidate - previous) >= no_trade_band else previous
    result = base.copy()
    result["tqqq"] = implemented
    return result


def asset_returns(frame: pd.DataFrame, real: bool) -> pd.DataFrame:
    qqq_return = frame["qqq"].pct_change().fillna(0)
    if real:
        return frame[["tqqq", "sqqq"]].pct_change().fillna(0)
    drag = ANNUAL_DRAG / 252
    return pd.DataFrame(
        {"tqqq": (3 * qqq_return - drag).clip(lower=-0.99),
         "sqqq": (-3 * qqq_return - drag).clip(lower=-0.99)}, index=frame.index
    )


def equity_curve(weights: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    held = weights.shift(1).fillna(0)
    turnover = weights.diff().abs().sum(axis=1).shift(1).fillna(0)
    daily = (held * returns).sum(axis=1) - turnover * TURNOVER_COST
    return (1 + daily.clip(lower=-0.99)).cumprod()


def metrics(equity: pd.Series, weights: pd.DataFrame | None = None) -> dict[str, float]:
    equity = equity.dropna()
    daily = equity.pct_change().dropna()
    if isinstance(equity.index, pd.DatetimeIndex):
        years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 252)
    else:
        years = max(len(equity) / 252, 1 / 252)
    cagr = float(equity.iloc[-1] ** (1 / years) - 1)
    drawdown = equity / equity.cummax() - 1
    sharpe = float(daily.mean() / daily.std(ddof=1) * np.sqrt(252)) if daily.std(ddof=1) else 0.0
    max_dd = float(drawdown.min())
    underwater = drawdown < -1e-12
    groups = underwater.ne(underwater.shift()).cumsum()
    max_duration = int(underwater.groupby(groups).sum().max()) if len(underwater) else 0
    rolling_21 = equity / equity.shift(21) - 1
    result = {
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar": cagr / abs(max_dd) if max_dd else 0.0,
        "worst_21d": float(rolling_21.min()),
        "max_drawdown_days": max_duration,
    }
    if weights is not None:
        aligned = weights.reindex(equity.index).ffill().fillna(0.0)
        result["annual_turnover"] = float(aligned.diff().abs().sum(axis=1).mean() * 252)
        result["average_tqqq_weight"] = float(aligned["tqqq"].mean())
    return result


def score(equity: pd.Series) -> float:
    """Median annual Calmar makes one spectacular bull market insufficient."""
    values = []
    for _, yearly in equity.groupby(equity.index.year):
        if len(yearly) >= 100:
            values.append(metrics(yearly)["calmar"])
    return float(np.median(values)) if values else -np.inf


def bootstrap_audit(daily_returns: pd.Series, samples: int = 2_000) -> pd.DataFrame:
    values = daily_returns.dropna().to_numpy(dtype=float)
    block = min(21, len(values))
    blocks_needed = int(np.ceil(len(values) / block))
    random = np.random.default_rng(7)
    rows = []
    for sample in range(samples):
        starts = random.integers(0, len(values) - block + 1, size=blocks_needed)
        simulated = np.concatenate([values[start : start + block] for start in starts])[:len(values)]
        equity = pd.Series((1 + simulated).cumprod(), index=pd.RangeIndex(len(simulated)))
        result = metrics(equity)
        rows.append({"sample": sample, **result})
    return pd.DataFrame(rows)


def run_study() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = load_history()
    base = baseline_weights(frame)
    candidates = {"baseline": base}
    for target, band, step in itertools.product(
        (0.30, 0.40, 0.50, 0.60), (0.03, 0.07, 0.12), (0.10, 0.25, 1.00)
    ):
        name = f"managed_v{target:.2f}_b{band:.2f}_r{step:.2f}"
        candidates[name] = managed_overlay(base, frame["qqq"], target, band, step)

    synthetic_returns = asset_returns(frame, real=False)
    curves = {name: equity_curve(weight, synthetic_returns) for name, weight in candidates.items()}
    splits = (("2005-2009", "2005-01-01", "2009-12-31"),
              ("2010-2014", "2010-01-01", "2014-12-31"),
              ("2015-2019", "2015-01-01", "2019-12-31"),
              ("2020-2022", "2020-01-01", "2022-12-31"),
              ("2023-now", "2023-01-01", END))
    selections = []
    oos_parts = []
    for label, start, end in splits:
        train_end = pd.Timestamp(start) - timedelta(days=1)
        eligible = {name: curve.loc[:train_end] for name, curve in curves.items()}
        winner = max(eligible, key=lambda name: score(eligible[name]))
        test = curves[winner].loc[start:end]
        if test.empty:
            continue
        normalized = test / test.iloc[0]
        selections.append({"test_period": label, "selected": winner,
                           "train_start": str(frame.index[0].date()),
                           "train_end": str(train_end.date()),
                           **metrics(normalized, candidates[winner].loc[start:end])})
        oos_parts.append(normalized.pct_change().fillna(0))
    stitched = (1 + pd.concat(oos_parts).sort_index()).cumprod()

    rows = [{"dataset": "synthetic_3x_1999_now", "strategy": name,
             **metrics(curve, candidates[name])}
            for name, curve in curves.items()]
    rows.append({"dataset": "synthetic_walk_forward", "strategy": "adaptive_selected", **metrics(stitched)})

    real = frame.dropna(subset=["tqqq", "sqqq"])
    real_returns = asset_returns(real, real=True)
    for name, weight in candidates.items():
        curve = equity_curve(weight.reindex(real.index).ffill(), real_returns)
        rows.append({"dataset": "real_etf_2010_now", "strategy": name,
                     **metrics(curve, weight.reindex(real.index).ffill())})

    # One-time untouched holdout: select through 2020, inspect 2021+ once.
    selection_end = pd.Timestamp("2020-12-31")
    managed_names = [name for name in candidates if name != "baseline"]
    frozen = max(managed_names, key=lambda name: score(curves[name].loc[:selection_end]))
    audit_rows = []
    for name in ("baseline", frozen):
        for dataset, returns, index in (
            ("synthetic", synthetic_returns, frame.index),
            ("real_etf", real_returns, real.index),
        ):
            weights = candidates[name].reindex(index).ffill()
            curve = equity_curve(weights, returns).loc["2021-01-01":]
            audit_rows.append({"dataset": dataset, "strategy": name,
                               **metrics(curve / curve.iloc[0], weights.loc[curve.index])})
    frozen_weights = candidates[frozen].reindex(real.index).ffill()
    frozen_curve = equity_curve(frozen_weights, real_returns).loc["2021-01-01":]
    bootstrap = bootstrap_audit(frozen_curve.pct_change())

    crisis_rows = []
    periods = (("dotcom", "2000-03-01", "2002-10-31"), ("gfc", "2007-10-01", "2009-03-31"),
               ("covid", "2020-02-01", "2020-04-30"), ("2022_bear", "2022-01-01", "2022-12-31"))
    selected_names = {row["selected"] for row in selections} | {"baseline"}
    for name in sorted(selected_names):
        for period, start, end in periods:
            part = curves[name].loc[start:end]
            if len(part) > 1:
                normalized = part / part.iloc[0]
                crisis_rows.append({"strategy": name, "period": period, **metrics(normalized)})
    return (
        pd.DataFrame(rows), pd.DataFrame(selections), pd.DataFrame(crisis_rows),
        pd.DataFrame(audit_rows), bootstrap,
    )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    summary, selections, crises, holdout, bootstrap = run_study()
    summary.to_csv(OUTPUT / "summary.csv", index=False)
    selections.to_csv(OUTPUT / "walk_forward_selections.csv", index=False)
    crises.to_csv(OUTPUT / "crisis_periods.csv", index=False)
    holdout.to_csv(OUTPUT / "holdout_2021_now.csv", index=False)
    bootstrap.to_csv(OUTPUT / "bootstrap_2021_now.csv", index=False)
    print(selections.to_string(index=False))
    print("\nTop synthetic:\n", summary[summary.dataset == "synthetic_3x_1999_now"].nlargest(8, "calmar").to_string(index=False))
    print("\nWalk-forward:\n", summary[summary.dataset == "synthetic_walk_forward"].to_string(index=False))
    print("\nFrozen holdout audit:\n", holdout.to_string(index=False))
    print("\nBootstrap quantiles:\n", bootstrap[["cagr", "max_drawdown", "worst_21d"]].quantile([0.05, 0.5, 0.95]).to_string())
    print(f"\nSaved to {OUTPUT}")


if __name__ == "__main__":
    main()
