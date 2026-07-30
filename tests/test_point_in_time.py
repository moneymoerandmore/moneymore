from pathlib import Path

import pandas as pd

from moneymore.point_in_time import audit_target_membership


def test_historical_orders_before_capture_are_blocked(tmp_path: Path) -> None:
    pd.DataFrame(
        [{"signal_date": "20250105", "symbol": "AAA"}]
    ).to_parquet(tmp_path / "factor-orders.parquet", index=False)
    snapshots = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "available_from": "20260730",
                "valid_to": None,
            }
        ]
    )
    result = audit_target_membership(tmp_path, snapshots)
    assert result["status"] == "BLOCKED"
    assert result["coverage"] == 0
    assert result["earliest_trustworthy_date"] == "20260730"


def test_forward_orders_after_capture_pass(tmp_path: Path) -> None:
    pd.DataFrame(
        [{"signal_date": "20260731", "symbol": "AAA"}]
    ).to_parquet(tmp_path / "factor-orders.parquet", index=False)
    snapshots = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "available_from": "20260730",
                "valid_to": None,
            }
        ]
    )
    result = audit_target_membership(tmp_path, snapshots)
    assert result["status"] == "PASS"
    assert result["coverage"] == 1


def test_multiple_snapshots_do_not_double_count_an_order(tmp_path: Path) -> None:
    pd.DataFrame(
        [{"signal_date": "20260801", "symbol": "AAA"}]
    ).to_parquet(tmp_path / "factor-orders.parquet", index=False)
    snapshots = pd.DataFrame(
        [
            {"symbol": "AAA", "available_from": "20260730", "valid_to": None},
            {"symbol": "AAA", "available_from": "20260731", "valid_to": None},
        ]
    )
    result = audit_target_membership(tmp_path, snapshots)
    assert result["tested_orders"] == 1
    assert result["eligible_orders"] == 1
