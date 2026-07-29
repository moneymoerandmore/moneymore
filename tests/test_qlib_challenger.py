import numpy as np
import pandas as pd

from moneymore.qlib_challenger import (
    QlibPanelDataset,
    evaluate_predictions,
)


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
