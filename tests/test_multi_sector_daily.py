import pandas as pd

from moneymore.multi_sector_daily import expand_sleeve_targets


def test_expand_sleeve_targets_splits_each_sleeve_equally() -> None:
    recommendation = pd.DataFrame(
        [
            {"sector": "bank", "target_weight": 0.18, "selected": "A,B,C"},
            {"sector": "chip", "target_weight": 0.06, "selected": "D,E"},
            {"sector": "cash", "target_weight": 0.0, "selected": ""},
        ]
    )

    targets, sectors = expand_sleeve_targets(recommendation)

    assert targets == {"A": 0.06, "B": 0.06, "C": 0.06, "D": 0.03, "E": 0.03}
    assert sectors == {
        "A": "bank",
        "B": "bank",
        "C": "bank",
        "D": "chip",
        "E": "chip",
    }
