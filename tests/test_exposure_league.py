import pytest
import pandas as pd

from moneymore.exposure_league import (
    ExposureContext,
    ExposureDecision,
    PysystemtradeVolTargetPolicy,
    SkfolioCashAllocatorPolicy,
    build_pysystemtrade_exposure_history,
    initial_exposure_league,
    validate_exposure_only_payload,
)
from moneymore.market_risk import build_baseline_overlay_comparison


def test_all_contestants_share_scalar_exposure_contract():
    rows = initial_exposure_league("20260911")
    assert {row["framework"] for row in rows} == {
        "pysystemtrade", "skfolio", "FinRL", "DeepDow"
    }
    assert all(row["target_exposure"] is None for row in rows)
    for row in rows:
        validate_exposure_only_payload(row)


def test_exposure_decision_rejects_out_of_range_target():
    with pytest.raises(ValueError, match="between zero and one"):
        ExposureDecision("x", "x", "20260911", "READY", 1.1, "v1", "20260910", "x")


def test_exposure_record_exposes_common_chart_date_axis():
    row = ExposureDecision("x", "x", "20260911", "READY", 0.5, "v1", "20260910", "x")
    assert row.to_record()["trade_date"] == "20260911"


def test_contract_rejects_stock_selection_leakage():
    with pytest.raises(ValueError, match="leaked"):
        validate_exposure_only_payload({"target_exposure": 0.5, "symbols": ["600000.SH"]})


def test_pysystemtrade_adapter_outputs_only_bounded_total_exposure():
    returns = tuple([0.01, -0.01] * 40)
    decision = PysystemtradeVolTargetPolicy().decide(
        ExposureContext("20260911", returns, (), previous_exposure=0.5)
    )
    assert decision.status == "READY"
    assert 0 <= decision.target_exposure <= 1
    validate_exposure_only_payload(decision.to_record())


def test_pysystemtrade_buffer_keeps_prior_exposure_inside_band():
    returns = tuple([0.005, -0.005] * 40)
    policy = PysystemtradeVolTargetPolicy()
    raw = policy.decide(ExposureContext("20260911", returns, ()))
    buffered = policy.decide(
        ExposureContext("20260911", returns, (), previous_exposure=raw.target_exposure)
    )
    assert buffered.target_exposure == pytest.approx(raw.target_exposure)


def test_pysystemtrade_history_is_point_in_time_and_carries_exposure():
    rows = build_pysystemtrade_exposure_history(
        [(f"202601{day:02d}", 0.005 if day % 2 else -0.005) for day in range(1, 31)]
    )
    assert len(rows) == 30
    assert all(row["target_exposure"] is None for row in rows[:9])
    ready = [row for row in rows if row["status"] == "READY"]
    assert ready
    assert all(0 <= float(row["target_exposure"]) <= 1 for row in ready)


def test_baseline_overlay_uses_prior_day_exposure_without_mutating_baseline():
    baseline = pd.DataFrame(
        [
            {"trade_date": "20260101", "equity": 100.0, "gross_exposure": 1.0},
            {"trade_date": "20260102", "equity": 110.0, "gross_exposure": 1.0},
            {"trade_date": "20260103", "equity": 99.0, "gross_exposure": 1.0},
        ]
    )
    signals = [
        {"trade_date": "20260101", "target_exposure": 0.5},
        {"trade_date": "20260102", "target_exposure": 0.2},
    ]
    rows = build_baseline_overlay_comparison(baseline, signals)
    original = [row for row in rows if row["strategy_id"] == "baseline"]
    overlay = [row for row in rows if row["strategy_id"] == "baseline_pysystemtrade"]
    assert original[-1]["normalized_nav"] == pytest.approx(0.99)
    assert overlay[1]["normalized_nav"] == pytest.approx(1.05)
    assert overlay[2]["normalized_nav"] == pytest.approx(1.029)
    assert overlay[1]["target_exposure"] == pytest.approx(0.5)


def test_skfolio_allocator_uses_native_time_aware_selection():
    rng = __import__("numpy").random.default_rng(7)
    returns = tuple(rng.normal(0.0005, 0.01, 180))
    decision = SkfolioCashAllocatorPolicy().decide(
        ExposureContext("20260911", returns, (), previous_exposure=0.5)
    )
    assert decision.status == "READY"
    assert 0 <= float(decision.target_exposure) <= 1
    assert "WalkForward" in decision.explanation
    assert "CPCV" in decision.explanation
    validate_exposure_only_payload(decision.to_record())


def test_finrl_is_not_fabricated_without_a_completed_artifact(tmp_path):
    rows = initial_exposure_league(
        "20260911", tuple([0.001, -0.001] * 80), root=tmp_path
    )
    finrl = next(row for row in rows if row["framework"] == "FinRL")
    assert finrl["status"] == "NOT_TRAINED"
    assert finrl["target_exposure"] is None


def test_deepdow_network_emits_two_weights_and_scalar_contract():
    import torch
    from moneymore.deepdow_exposure import DeepDowExposureNet

    weights = DeepDowExposureNet()(torch.zeros(4, 3, 60, 2))
    assert tuple(weights.shape) == (4, 2)
    assert torch.allclose(weights.sum(dim=1), torch.ones(4))
    assert torch.all((weights >= 0) & (weights <= 1))
