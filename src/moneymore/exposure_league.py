from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExposureContext:
    """Information allowed to cross the stock-selection/exposure boundary."""

    as_of_date: str
    risky_asset_returns: tuple[float, ...]
    cash_returns: tuple[float, ...]
    previous_exposure: float | None = None


@dataclass(frozen=True)
class ExposureDecision:
    method_id: str
    framework: str
    as_of_date: str
    status: str
    target_exposure: float | None
    model_version: str | None
    trained_until: str | None
    explanation: str

    def __post_init__(self) -> None:
        if self.target_exposure is not None and not 0 <= self.target_exposure <= 1:
            raise ValueError("target_exposure must be between zero and one")

    def to_record(self) -> dict[str, object]:
        record = asdict(self)
        # Dashboard time-series components use the common trade_date axis.
        record["trade_date"] = self.as_of_date
        return record


class ExposurePolicy(Protocol):
    """Every league contestant may emit one scalar exposure and nothing else."""

    method_id: str
    framework: str

    def decide(self, context: ExposureContext) -> ExposureDecision: ...


class NotTrainedExposurePolicy:
    def __init__(self, method_id: str, framework: str, explanation: str) -> None:
        self.method_id = method_id
        self.framework = framework
        self.explanation = explanation

    def decide(self, context: ExposureContext) -> ExposureDecision:
        return ExposureDecision(
            method_id=self.method_id,
            framework=self.framework,
            as_of_date=context.as_of_date,
            status="NOT_TRAINED",
            target_exposure=None,
            model_version=None,
            trained_until=None,
            explanation=self.explanation,
        )


class PysystemtradeVolTargetPolicy:
    """Cash-equity adaptation of pysystemtrade's documented sizing defaults."""

    method_id = "pysystemtrade_vol_target"
    framework = "pysystemtrade"

    def __init__(
        self,
        annual_vol_target: float = 0.16,
        fast_span: int = 35,
        min_periods: int = 10,
        slow_years: int = 10,
        slow_weight: float = 0.30,
        buffer_size: float = 0.10,
        maximum_exposure: float = 1.0,
    ) -> None:
        self.annual_vol_target = annual_vol_target
        self.fast_span = fast_span
        self.min_periods = min_periods
        self.slow_years = slow_years
        self.slow_weight = slow_weight
        self.buffer_size = buffer_size
        self.maximum_exposure = maximum_exposure

    def decide(self, context: ExposureContext) -> ExposureDecision:
        returns = pd.Series(context.risky_asset_returns, dtype=float).replace(
            [np.inf, -np.inf], np.nan
        ).dropna()
        if len(returns) < self.min_periods:
            return ExposureDecision(
                self.method_id, self.framework, context.as_of_date, "INSUFFICIENT_HISTORY",
                None, "pst-mixed-vol-defaults-v1", context.as_of_date,
                f"至少需要{self.min_periods}个风险资产收益观测",
            )
        fast = returns.ewm(span=self.fast_span, min_periods=self.min_periods).std().iloc[-1]
        slow_span = self.slow_years * 242
        slow = returns.ewm(
            span=slow_span, min_periods=self.min_periods
        ).std().iloc[-1]
        daily_vol = max(
            (1 - self.slow_weight) * float(fast) + self.slow_weight * float(slow),
            1e-10,
        )
        annual_vol = daily_vol * np.sqrt(242)
        optimal = min(self.maximum_exposure, self.annual_vol_target / annual_vol)
        target = optimal
        previous = context.previous_exposure
        if previous is not None:
            lower = max(0.0, optimal * (1 - self.buffer_size))
            upper = min(self.maximum_exposure, optimal * (1 + self.buffer_size))
            if lower <= previous <= upper:
                target = previous
            elif previous < lower:
                target = lower
            else:
                target = upper
        return ExposureDecision(
            method_id=self.method_id,
            framework=self.framework,
            as_of_date=context.as_of_date,
            status="READY",
            target_exposure=float(target),
            model_version="pst-mixed-vol-defaults-v1",
            trained_until=context.as_of_date,
            explanation=(
                f"16%年化风险目标；35日快波动与10年慢波动按70/30混合；"
                f"预测年化波动{annual_vol:.2%}；10%仓位缓冲；A股本地上限100%"
            ),
        )


class SkfolioCashAllocatorPolicy:
    """Native skfolio mean-risk allocator with time-aware model selection."""

    method_id = "skfolio_cash_allocator"
    framework = "skfolio"

    def __init__(
        self,
        train_size: int = 120,
        test_size: int = 20,
        risk_aversion_grid: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0, 50.0),
        evaluation_risk_aversion: float = 10.0,
        one_way_cost: float = 0.00061,
        run_cpcv: bool = True,
    ) -> None:
        self.train_size = train_size
        self.test_size = test_size
        self.risk_aversion_grid = risk_aversion_grid
        self.evaluation_risk_aversion = evaluation_risk_aversion
        self.one_way_cost = one_way_cost
        self.run_cpcv = run_cpcv

    @staticmethod
    def _fit_weight(returns: np.ndarray, risk_aversion: float) -> float:
        from skfolio.measures import RiskMeasure
        from skfolio.optimization import MeanRisk, ObjectiveFunction

        model = MeanRisk(
            objective_function=ObjectiveFunction.MAXIMIZE_UTILITY,
            risk_measure=RiskMeasure.VARIANCE,
            risk_aversion=risk_aversion,
            budget=None,
            min_budget=0.0,
            max_budget=1.0,
        )
        model.fit(pd.DataFrame({"stock_proxy": returns}))
        return float(np.clip(np.asarray(model.weights_).reshape(-1)[0], 0, 1))

    def _score_splits(
        self, returns: np.ndarray, splits: list[tuple[np.ndarray, np.ndarray]], risk_aversion: float
    ) -> tuple[float, list[float]]:
        fold_scores: list[float] = []
        previous = 0.0
        for train, test in splits:
            if isinstance(test, (list, tuple)):
                test = np.concatenate([np.asarray(part, dtype=int) for part in test])
                test.sort()
            if not len(train) or not len(test):
                continue
            weight = self._fit_weight(returns[train], risk_aversion)
            net = weight * returns[test].copy()
            net[0] -= abs(weight - previous) * self.one_way_cost
            previous = weight
            fold_scores.append(
                float(net.mean() - self.evaluation_risk_aversion * net.var(ddof=1))
            )
        return (float(np.mean(fold_scores)) if fold_scores else -np.inf), fold_scores

    def decide(self, context: ExposureContext) -> ExposureDecision:
        returns = np.asarray(context.risky_asset_returns, dtype=float)
        returns = returns[np.isfinite(returns)]
        minimum = self.train_size + self.test_size
        if len(returns) < minimum:
            return ExposureDecision(
                self.method_id, self.framework, context.as_of_date, "INSUFFICIENT_HISTORY",
                None, "skfolio-mean-risk-wf-cpcv-v1", None,
                f"至少需要{minimum}个风险资产收益观测",
            )

        from skfolio.model_selection import CombinatorialPurgedCV, WalkForward

        frame = pd.DataFrame({"stock_proxy": returns})
        walk_forward = WalkForward(
            train_size=self.train_size, test_size=self.test_size, purged_size=1,
            reduce_test=True,
        )
        wf_splits = list(walk_forward.split(frame))
        ranked: list[tuple[float, float]] = []
        for risk_aversion in self.risk_aversion_grid:
            score, _ = self._score_splits(returns, wf_splits, risk_aversion)
            ranked.append((score, risk_aversion))
        _, selected = max(ranked, key=lambda item: (item[0], -item[1]))

        # CPCV is an independent robustness diagnostic, not another source of future data.
        cpcv_scores: list[float] = []
        if self.run_cpcv:
            cpcv = CombinatorialPurgedCV(
                n_folds=6, n_test_folds=2, purged_size=1, embargo_size=1
            )
            _, cpcv_scores = self._score_splits(
                returns, list(cpcv.split(frame)), selected
            )
        weight = self._fit_weight(returns, selected)
        if context.previous_exposure is not None:
            # Feed the actual transition cost into the final native optimization.
            from skfolio.measures import RiskMeasure
            from skfolio.optimization import MeanRisk, ObjectiveFunction

            model = MeanRisk(
                objective_function=ObjectiveFunction.MAXIMIZE_UTILITY,
                risk_measure=RiskMeasure.VARIANCE,
                risk_aversion=selected,
                budget=None,
                min_budget=0.0,
                max_budget=1.0,
                transaction_costs=self.one_way_cost,
                previous_weights=[context.previous_exposure],
            )
            model.fit(frame)
            weight = float(np.clip(np.asarray(model.weights_).reshape(-1)[0], 0, 1))
        positive = float(np.mean(np.asarray(cpcv_scores) > 0)) if cpcv_scores else 0.0
        return ExposureDecision(
            self.method_id,
            self.framework,
            context.as_of_date,
            "READY",
            weight,
            "skfolio-mean-risk-wf-cpcv-v1",
            context.as_of_date,
            (
                f"原生MeanRisk最大化效用；WalkForward {self.train_size}/{self.test_size}日"
                f"选择风险厌恶{selected:g}；"
                f"{'CPCV正效用折占比' + format(positive, '.0%') if self.run_cpcv else '历史走步重估'}；"
                f"单边换仓成本{self.one_way_cost:.3%}；现金为未投资预算"
            ),
        )


class FinRLExposurePolicy:
    method_id = "finrl_exposure_agent"
    framework = "FinRL"

    def __init__(self, root: Path | None) -> None:
        self.root = root

    def decide(self, context: ExposureContext) -> ExposureDecision:
        if self.root is None:
            artifact = None
        else:
            from .finrl_exposure import latest_finrl_artifact

            artifact = latest_finrl_artifact(self.root)
        if artifact is None:
            return ExposureDecision(
                self.method_id, self.framework, context.as_of_date, "NOT_TRAINED",
                None, None, None, "等待首个每周PPO候选完成训练",
            )
        returns = np.asarray(context.risky_asset_returns, dtype=np.float32)
        returns = returns[np.isfinite(returns)]
        if len(returns) < 20:
            return ExposureDecision(
                self.method_id, self.framework, context.as_of_date, "INSUFFICIENT_HISTORY",
                None, str(artifact.metadata.get("candidate_tag")),
                str(artifact.metadata.get("trained_until")), "至少需要20个收益观测用于推理",
            )
        from stable_baselines3 import PPO
        from .finrl_exposure import exposure_observation

        previous = float(context.previous_exposure or 0.0)
        model = PPO.load(artifact.model_path, device="cpu")
        observation = exposure_observation(returns, len(returns) - 1, previous)
        action, _ = model.predict(observation, deterministic=True)
        target = float(np.clip(np.asarray(action).reshape(-1)[0], 0, 1))
        metrics = artifact.metadata.get("test_metrics", {})
        test_sharpe = metrics.get("sharpe") if isinstance(metrics, dict) else None
        diagnostic = str(artifact.metadata.get("diagnostic", "PENDING_DIAGNOSTIC"))
        if diagnostic.startswith("DEGENERATE"):
            return ExposureDecision(
                self.method_id, self.framework, context.as_of_date, "DEGENERATE",
                None, str(artifact.metadata.get("candidate_tag")),
                str(artifact.metadata.get("trained_until")),
                f"{diagnostic}；模型保留审计但不参与有效仓位排名",
            )
        return ExposureDecision(
            self.method_id, self.framework, context.as_of_date, "READY", target,
            str(artifact.metadata.get("candidate_tag")),
            str(artifact.metadata.get("trained_until")),
            f"PPO单一仓位动作；独立测试Sharpe {float(test_sharpe):.2f}；每周训练、每日推理"
            if test_sharpe is not None else "PPO单一仓位动作；每周训练、每日推理",
        )


class DeepDowExposurePolicy:
    method_id = "deepdow_exposure_network"
    framework = "DeepDow"

    def __init__(self, root: Path | None) -> None:
        self.root = root

    def decide(self, context: ExposureContext) -> ExposureDecision:
        if self.root is None:
            artifact = None
        else:
            from .deepdow_exposure import latest_deepdow_artifact

            artifact = latest_deepdow_artifact(self.root)
        if artifact is None:
            return ExposureDecision(
                self.method_id, self.framework, context.as_of_date, "NOT_TRAINED",
                None, None, None, "等待首个每周DeepDow候选完成训练",
            )
        if str(artifact.metadata.get("production_gate_passed", "")).lower() == "false" or str(
            artifact.metadata.get("diagnostic", "")
        ).startswith("SATURATED"):
            return ExposureDecision(
                self.method_id, self.framework, context.as_of_date, "ELIMINATED",
                None, str(artifact.metadata.get("candidate_tag")),
                str(artifact.metadata.get("trained_until")),
                "Sharpe尺度不变导致全仓饱和；已淘汰，仅保留审计产物",
            )
        returns = np.asarray(context.risky_asset_returns, dtype=np.float32)
        returns = returns[np.isfinite(returns)]
        from .deepdow_exposure import load_deepdow_model, predict_exposure

        model = load_deepdow_model(artifact)
        target = predict_exposure(model, returns)
        metrics = artifact.metadata.get("test_metrics", {})
        test_sharpe = metrics.get("sharpe") if isinstance(metrics, dict) else None
        diagnostic = str(artifact.metadata.get("diagnostic", "PASS"))
        return ExposureDecision(
            self.method_id, self.framework, context.as_of_date, "READY", target,
            str(artifact.metadata.get("candidate_tag")),
            str(artifact.metadata.get("trained_until")),
            f"DeepDow兼容Softmax二资产权重；独立测试Sharpe {float(test_sharpe):.2f}；"
            f"诊断{diagnostic}；每周训练、每日推理"
            if test_sharpe is not None else "DeepDow兼容Softmax二资产权重；每周训练、每日推理",
        )


def initial_exposure_league(
    as_of_date: str,
    risky_asset_returns: tuple[float, ...] = (),
    previous_exposure: float | None = None,
    root: Path | None = None,
    previous_exposures: dict[str, float | None] | None = None,
) -> list[dict[str, object]]:
    """Declare the four adapters without fabricating untrained model outputs."""
    policies: list[ExposurePolicy] = [
        PysystemtradeVolTargetPolicy(),
        SkfolioCashAllocatorPolicy(),
        FinRLExposurePolicy(root),
        DeepDowExposurePolicy(root),
    ]
    previous_by_method = previous_exposures or {}
    records = []
    for policy in policies:
        fallback = previous_exposure if policy.method_id == "pysystemtrade_vol_target" else None
        context = ExposureContext(
            as_of_date,
            risky_asset_returns,
            (),
            previous_by_method.get(policy.method_id, fallback),
        )
        records.append(policy.decide(context).to_record())
    return records


def build_pysystemtrade_exposure_history(
    dated_returns: list[tuple[str, float]],
    initial_exposure: float | None = None,
) -> list[dict[str, object]]:
    """Run the policy point-in-time, carrying yesterday's buffered exposure forward."""
    policy = PysystemtradeVolTargetPolicy()
    observed: list[float] = []
    previous = initial_exposure
    history: list[dict[str, object]] = []
    for trade_date, daily_return in dated_returns:
        if np.isfinite(daily_return):
            observed.append(float(daily_return))
        decision = policy.decide(
            ExposureContext(
                as_of_date=str(trade_date),
                risky_asset_returns=tuple(observed),
                cash_returns=(),
                previous_exposure=previous,
            )
        )
        if decision.target_exposure is not None:
            previous = decision.target_exposure
        history.append(decision.to_record())
    return history


def build_skfolio_exposure_history(
    dated_returns: list[tuple[str, float]], rebalance_days: int = 20
) -> list[dict[str, object]]:
    """Expanding point-in-time Walk Forward calibration, held between rebalances."""
    policy = SkfolioCashAllocatorPolicy(run_cpcv=False)
    clean = [(str(date), float(value)) for date, value in dated_returns if np.isfinite(value)]
    history: list[dict[str, object]] = []
    previous: float | None = None
    last_decision: ExposureDecision | None = None
    minimum = policy.train_size + policy.test_size
    for index, (trade_date, _) in enumerate(clean):
        should_rebalance = index >= minimum - 1 and (
            (index - (minimum - 1)) % rebalance_days == 0 or index == len(clean) - 1
        )
        if should_rebalance:
            last_decision = policy.decide(
                ExposureContext(
                    trade_date,
                    tuple(value for _, value in clean[: index + 1]),
                    (),
                    previous,
                )
            )
            previous = last_decision.target_exposure
        if last_decision is None:
            history.append(
                ExposureDecision(
                    policy.method_id, policy.framework, trade_date,
                    "INSUFFICIENT_HISTORY", None, None, None,
                    f"至少需要{minimum}个风险资产收益观测",
                ).to_record()
            )
        else:
            row = last_decision.to_record()
            row["as_of_date"] = trade_date
            history.append(row)
    return history


def validate_exposure_only_payload(payload: dict[str, object]) -> None:
    forbidden = {"symbol", "symbols", "industry", "sector", "stock_weights", "target_weights"}
    leaked = forbidden.intersection(payload)
    if leaked:
        raise ValueError(f"exposure policy leaked stock-selection fields: {sorted(leaked)}")
    target = payload.get("target_exposure")
    if target is not None and not 0 <= float(target) <= 1:
        raise ValueError("target_exposure must be between zero and one")
