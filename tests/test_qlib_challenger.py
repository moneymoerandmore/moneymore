import numpy as np
import pandas as pd

from moneymore.qlib_challenger import (
    QlibPanelDataset,
    evaluate_predictions,
    rank_blend_predictions,
    research_gate_diagnostics,
)
from moneymore.qlib_challenger_daily import _latest_market_state


def test_qlib_dataset_adapter_respects_segments_and_column_sets() -> None:
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(["2025-01-02", "2025-01-03"]), ["A", "B"]],
        names=["datetime", "instrument"],
    )
    frame = pd.DataFrame(
        np.arange(12, dtype="float32").reshape(4, 3),
        index=index,
        columns=pd.MultiIndex.from_tuples(
            [("feature", "f0"), ("feature", "f1"), ("label", "LABEL0")]
        ),
    )
    dataset = QlibPanelDataset(
        frame,
        {"train": ("2025-01-02", "2025-01-02"), "test": ("2025-01-03", "2025-01-03")},
    )

    train = dataset.prepare("train", ["feature", "label"])
    test_features = dataset.prepare("test", "feature")

    assert len(train) == 2
    assert list(test_features.columns) == ["f0", "f1"]
    assert set(test_features.index.get_level_values("datetime")) == {
        pd.Timestamp("2025-01-03")
    }


def test_challenger_metrics_use_daily_cross_section_rank() -> None:
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(["2025-01-02", "2025-01-03"]), ["A", "B", "C"]],
        names=["datetime", "instrument"],
    )
    labels = pd.Series([1, 2, 3, -1, 0, 1], index=index, dtype=float)
    predictions = labels.copy()

    result = evaluate_predictions(
        predictions, labels, "perfect_model", "test", top_k=1
    )

    assert result.samples == 6
    assert result.rank_ic == 1.0
    assert result.top_k_excess_return == 2.0


def test_latest_market_state_marks_held_positions_before_observation_snapshot() -> None:
    histories = {
        "HELD": pd.DataFrame(
            [
                {
                    "date": "2026-07-30",
                    "raw_open": 9.8,
                    "raw_close": 10.0,
                    "can_buy": True,
                    "can_sell": True,
                },
                {
                    "date": "2026-07-31",
                    "raw_open": 10.1,
                    "raw_close": 10.5,
                    "can_buy": True,
                    "can_sell": True,
                },
            ]
        )
    }
    class Store:
        def read(self, table, **_kwargs):
            if table == "stock_limits":
                raise FileNotFoundError
            return pd.DataFrame(
                [
                    {
                        "ts_code": "HELD",
                        "trade_date": row["date"].replace("-", ""),
                        "open": row["raw_open"],
                        "close": row["raw_close"],
                    }
                    for row in histories["HELD"].to_dict("records")
                ]
            )

    marks, bars = _latest_market_state(
        Store(), {"HELD"}, pd.Timestamp("2026-07-31"), "20260731"
    )

    assert marks == {"HELD": 10.5}
    assert bars["HELD"].close == 10.5


def test_rank_blend_makes_heterogeneous_model_scales_comparable() -> None:
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(["2026-01-02"]), ["A", "B", "C"]],
        names=["datetime", "instrument"],
    )
    tree = pd.Series([1000.0, 2000.0, 3000.0], index=index)
    gru = pd.Series([0.03, 0.02, 0.01], index=index)
    blended = rank_blend_predictions([(tree, 0.5), (gru, 0.5)])
    assert blended.loc[(pd.Timestamp("2026-01-02"), "A")] == 2 / 3
    assert blended.loc[(pd.Timestamp("2026-01-02"), "B")] == 2 / 3
    assert blended.loc[(pd.Timestamp("2026-01-02"), "C")] == 2 / 3


def test_v2_gate_requires_cross_model_confirmation_and_large_universe() -> None:
    config = {
        "model_id": "ensemble",
        "protocol_version": "challenger_v2",
        "research_gate": {
            "minimum_samples": 100,
            "minimum_rank_ic": 0.01,
            "minimum_rank_ic_ir": 0.1,
            "minimum_cost_adjusted_excess_return": 0.0,
            "minimum_seed_count": 2,
            "minimum_positive_seed_ratio": 0.5,
            "require_positive_lightgbm_rank_ic": True,
            "minimum_universe_size": 800,
        },
    }
    research = {
        "protocol": config,
        "universe_size": 1000,
        "metrics": [
            {"model_id": "ensemble", "samples": 1000, "rank_ic": 0.02,
             "rank_ic_ir": 0.2, "cost_adjusted_top_k_excess_return": 0.01},
            {"model_id": "qlib_lightgbm_alpha360_v1", "rank_ic": -0.01},
        ],
        "stability": {"seed_count": 5, "positive_seed_ratio": 0.8},
    }
    passed, failures = research_gate_diagnostics(research, config)
    assert passed is False
    assert failures == ["CROSS_MODEL_LIGHTGBM_RANK_IC"]
