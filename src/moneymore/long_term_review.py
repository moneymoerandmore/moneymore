from __future__ import annotations

import json
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .data.store import ParquetStore
from .execution.paper import PaperBroker
from .qlib_governance import deployment_snapshot

FACTOR_ACCOUNT = "multi_sector_shadow"
QLIB_ACCOUNT = "qlib_gru_shadow"


def evaluate_long_term_review(
    root: Path,
    store: ParquetStore,
    broker: PaperBroker,
    trade_date: str,
) -> dict[str, Any]:
    point_in_time = _read_json(root / "state" / "point-in-time" / "latest.json")
    trustworthy_start = (
        point_in_time.get("historical_replay_gate", {}).get(
            "earliest_trustworthy_date"
        )
    )
    factor = _read_history(store, "multi_sector_account_daily")
    challenger = _read_history(store, "qlib_challenger_account_daily")
    governance = deployment_snapshot(root)
    drift = _read_json(root / "state" / "qlib-governance" / "latest-drift.json")
    forward = _read_json(
        root / "state" / "qlib-challenger" / "forward-evaluation.json"
    )
    research = _read_json(root / "state" / "qlib-challenger" / "latest-research.json")
    trial_count = max(len(governance.get("releases", [])), 1)
    evaluation = evaluate_paired_performance(
        factor,
        challenger,
        trustworthy_start_date=str(trustworthy_start or "99991231"),
        trial_count=trial_count,
    )
    costs = {
        FACTOR_ACCOUNT: _cost_ratio(
            broker, FACTOR_ACCOUNT, evaluation.get("common_start_date")
        ),
        QLIB_ACCOUNT: _cost_ratio(
            broker, QLIB_ACCOUNT, evaluation.get("common_start_date")
        ),
    }
    promotion_config = yaml.safe_load(
        (root / "configs" / "qlib_challenger.yaml").read_text(encoding="utf-8")
    )
    research_gate = _research_promotion_gate(
        research, forward, promotion_config
    )
    criteria = {
        "minimum_60_common_days": evaluation["common_observation_days"] >= 60,
        "positive_excess_confidence": (
            evaluation.get("annualized_excess_ci_low") is not None
            and float(evaluation["annualized_excess_ci_low"]) > 0
        ),
        "risk_normalized_outperformance": (
            evaluation.get("qlib_risk_normalized_return") is not None
            and evaluation.get("factor_risk_normalized_return") is not None
            and float(evaluation["qlib_risk_normalized_return"])
            > float(evaluation["factor_risk_normalized_return"])
        ),
        "drawdown_not_materially_worse": (
            evaluation.get("qlib_max_drawdown") is not None
            and evaluation.get("factor_max_drawdown") is not None
            and float(evaluation["qlib_max_drawdown"])
            >= float(evaluation["factor_max_drawdown"]) - 0.02
        ),
        "cost_not_materially_worse": (
            costs[QLIB_ACCOUNT] <= costs[FACTOR_ACCOUNT] + 0.002
        ),
        "deflated_sharpe_confidence": (
            evaluation.get("deflated_sharpe_probability") is not None
            and float(evaluation["deflated_sharpe_probability"]) >= 0.95
        ),
        "research_and_forward_gate": research_gate,
        "drift_monitoring_passed": drift.get("status") == "PASS",
        "point_in_time_boundary_present": bool(trustworthy_start),
    }
    days = int(evaluation["common_observation_days"])
    if days < 20:
        status = "COLLECTING_EVIDENCE"
    elif days < 60:
        status = "PRELIMINARY_ONLY"
    elif all(criteria.values()):
        status = "ELIGIBLE_FOR_MANUAL_PROMOTION_REVIEW"
    else:
        status = "FORMAL_REVIEW_NOT_PASSED"
    payload = {
        "trade_date": trade_date,
        "status": status,
        "decision_authority": "MANUAL_ONLY",
        "champion_replacement_automatic": False,
        "minimum_preliminary_days": 20,
        "minimum_formal_days": 60,
        "trustworthy_start_date": trustworthy_start,
        "evaluation": evaluation,
        "cost_ratios": costs,
        "criteria": criteria,
        "deployment": {
            key: governance.get(key)
            for key in ("release_id", "model_id", "lifecycle", "execution_mode")
        },
    }
    target = root / "state" / "qlib-governance" / "long-term-review.json"
    _write_json(target, payload)
    if days >= 60:
        immutable = (
            root
            / "state"
            / "qlib-governance"
            / "reviews"
            / f"{evaluation['common_end_date']}.json"
        )
        if not immutable.exists():
            _write_json(immutable, payload)
    return payload


def evaluate_paired_performance(
    factor: pd.DataFrame,
    challenger: pd.DataFrame,
    *,
    trustworthy_start_date: str,
    trial_count: int = 1,
    target_volatility: float = 0.10,
    bootstrap_samples: int = 2000,
) -> dict[str, Any]:
    left = _prepare(factor, "factor_equity", trustworthy_start_date)
    right = _prepare(challenger, "qlib_equity", trustworthy_start_date)
    aligned = left.merge(right, on="trade_date", how="inner").sort_values("trade_date")
    if aligned.empty:
        return _empty_evaluation(trial_count)
    aligned["factor_nav"] = aligned["factor_equity"] / aligned["factor_equity"].iloc[0]
    aligned["qlib_nav"] = aligned["qlib_equity"] / aligned["qlib_equity"].iloc[0]
    factor_returns = aligned["factor_nav"].pct_change().dropna()
    qlib_returns = aligned["qlib_nav"].pct_change().dropna()
    paired = qlib_returns - factor_returns
    factor_vol = _annualized_volatility(factor_returns)
    qlib_vol = _annualized_volatility(qlib_returns)
    ci_low, ci_high = _block_bootstrap_ci(
        paired,
        samples=bootstrap_samples,
        adjusted_alpha=0.05 / max(trial_count, 1),
    )
    factor_drawdown = aligned["factor_nav"] / aligned["factor_nav"].cummax() - 1
    qlib_drawdown = aligned["qlib_nav"] / aligned["qlib_nav"].cummax() - 1
    return {
        "common_start_date": str(aligned.iloc[0]["trade_date"]),
        "common_end_date": str(aligned.iloc[-1]["trade_date"]),
        "common_observation_days": len(aligned),
        "paired_return_days": len(paired),
        "factor_total_return": float(aligned.iloc[-1]["factor_nav"] - 1),
        "qlib_total_return": float(aligned.iloc[-1]["qlib_nav"] - 1),
        "factor_annualized_volatility": factor_vol,
        "qlib_annualized_volatility": qlib_vol,
        "factor_risk_normalized_return": _risk_normalized_return(
            factor_returns, factor_vol, target_volatility
        ),
        "qlib_risk_normalized_return": _risk_normalized_return(
            qlib_returns, qlib_vol, target_volatility
        ),
        "factor_max_drawdown": float(factor_drawdown.min()),
        "qlib_max_drawdown": float(qlib_drawdown.min()),
        "annualized_excess_return": (
            float(paired.mean() * 252) if len(paired) else None
        ),
        "annualized_excess_ci_low": ci_low,
        "annualized_excess_ci_high": ci_high,
        "excess_sharpe": _sharpe(paired),
        "deflated_sharpe_probability": _deflated_sharpe_probability(
            paired, trial_count
        ),
        "trial_count": trial_count,
        "confidence_method": "PAIRED_BLOCK_BOOTSTRAP_BONFERRONI",
        "target_volatility": target_volatility,
    }


def _block_bootstrap_ci(
    returns: pd.Series,
    *,
    samples: int,
    adjusted_alpha: float,
    block_size: int = 5,
) -> tuple[float | None, float | None]:
    values = returns.dropna().to_numpy(dtype=float)
    if len(values) < 19:
        return None, None
    rng = np.random.default_rng(20260730)
    starts = np.arange(max(len(values) - block_size + 1, 1))
    estimates = []
    for _ in range(samples):
        selected = []
        while len(selected) < len(values):
            start = int(rng.choice(starts))
            selected.extend(values[start : start + block_size])
        estimates.append(float(np.mean(selected[: len(values)]) * 252))
    return (
        float(np.quantile(estimates, adjusted_alpha / 2)),
        float(np.quantile(estimates, 1 - adjusted_alpha / 2)),
    )


def _deflated_sharpe_probability(
    returns: pd.Series, trial_count: int
) -> float | None:
    values = returns.dropna()
    if len(values) < 20 or values.std(ddof=1) <= 0:
        return None
    daily_sharpe = float(values.mean() / values.std(ddof=1))
    trials = max(trial_count, 1)
    if trials == 1:
        expected_max = 0.0
    else:
        expected_max = NormalDist().inv_cdf(1 - 1 / trials) / np.sqrt(252)
    skew = float(values.skew())
    kurtosis = float(values.kurtosis() + 3)
    variance = (
        1
        - skew * daily_sharpe
        + ((kurtosis - 1) / 4) * daily_sharpe**2
    ) / max(len(values) - 1, 1)
    if variance <= 0:
        return None
    statistic = (daily_sharpe - expected_max) / np.sqrt(variance)
    return float(NormalDist().cdf(statistic))


def _prepare(frame: pd.DataFrame, equity_name: str, start: str) -> pd.DataFrame:
    if frame.empty or not {"trade_date", "equity"}.issubset(frame.columns):
        return pd.DataFrame(columns=["trade_date", equity_name])
    result = frame[["trade_date", "equity"]].copy()
    result["trade_date"] = result["trade_date"].astype(str)
    result["equity"] = pd.to_numeric(result["equity"], errors="coerce")
    result = result.loc[result["trade_date"] >= start].dropna()
    return result.rename(columns={"equity": equity_name}).drop_duplicates(
        "trade_date", keep="last"
    )


def _annualized_volatility(returns: pd.Series) -> float | None:
    return (
        float(returns.std(ddof=1) * np.sqrt(252))
        if len(returns) > 1
        else None
    )


def _risk_normalized_return(
    returns: pd.Series,
    volatility: float | None,
    target: float,
) -> float | None:
    if volatility is None or volatility <= 0:
        return None
    return float((1 + returns * target / volatility).prod() - 1)


def _sharpe(returns: pd.Series) -> float | None:
    if len(returns) < 2 or returns.std(ddof=1) <= 0:
        return None
    return float(returns.mean() / returns.std(ddof=1) * np.sqrt(252))


def _read_history(store: ParquetStore, table: str) -> pd.DataFrame:
    try:
        return store.read(table)
    except FileNotFoundError:
        return pd.DataFrame()


def _cost_ratio(
    broker: PaperBroker, account_id: str, start_date: str | None
) -> float:
    if not start_date:
        return 0.0
    fills = [
        row
        for row in broker.fills(account_id)
        if str(row["trade_date"]) >= start_date
    ]
    notional = sum(float(row["price"]) * int(row["quantity"]) for row in fills)
    return sum(float(row["fee"]) for row in fills) / notional if notional else 0.0


def _research_promotion_gate(
    research: dict[str, Any],
    forward: dict[str, Any],
    config: dict[str, Any],
) -> bool:
    gate = research.get("promotion_gate")
    if isinstance(gate, dict):
        return bool(gate.get("passed"))
    metrics = next(
        (
            row
            for row in research.get("metrics", [])
            if row.get("model_id") == config["model_id"]
        ),
        None,
    )
    promotion = config["promotion_gate"]
    return bool(
        metrics
        and float(metrics["rank_ic"]) >= float(promotion["minimum_rank_ic"])
        and float(metrics["rank_ic_ir"]) >= float(promotion["minimum_rank_ic_ir"])
        and int(forward.get("matured_days", 0))
        >= int(promotion["minimum_forward_days"])
        and float(forward.get("rank_ic") or -1)
        > float(promotion["minimum_forward_rank_ic"])
    )


def _empty_evaluation(trial_count: int) -> dict[str, Any]:
    return {
        "common_start_date": None,
        "common_end_date": None,
        "common_observation_days": 0,
        "paired_return_days": 0,
        "trial_count": trial_count,
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
