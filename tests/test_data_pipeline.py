from pathlib import Path

import pandas as pd
import pytest

from moneymore.data.quality import DataQualityError, validate_daily_bars
from moneymore.data.store import ParquetStore
from moneymore.data.sync import (
    sync_daily_history,
    sync_etf_history,
    sync_etf_reference_data,
    sync_reference_data,
)


class FakeProvider:
    name = "fake"

    def instruments(self):
        return pd.DataFrame(
            [
                {
                    "ts_code": "510300.SH",
                    "list_status": "L",
                    "list_date": "20120528",
                }
            ]
        )

    def trading_calendar(self, start_date, end_date):
        return pd.DataFrame(
            [
                {"exchange": "SSE", "cal_date": start_date, "is_open": "1"},
                {"exchange": "SSE", "cal_date": end_date, "is_open": "0"},
            ]
        )

    def daily_bars(self, trade_date):
        return pd.DataFrame(
            [
                {
                    "ts_code": "510300.SH",
                    "trade_date": trade_date,
                    "open": 4.0,
                    "high": 4.2,
                    "low": 3.9,
                    "close": 4.1,
                    "vol": 1000,
                    "amount": 4100,
                }
            ]
        )

    def adjustment_factors(self, trade_date):
        return pd.DataFrame(
            [{"ts_code": "510300.SH", "trade_date": trade_date, "adj_factor": 1.0}]
        )

    def etf_instruments(self):
        return self.instruments()

    def etf_daily_bars(self, trade_date):
        return self.daily_bars(trade_date)

    def etf_adjustment_factors(self, trade_date):
        return self.adjustment_factors(trade_date)


def test_pipeline_saves_raw_manifest_and_curated_tables(tmp_path: Path):
    provider = FakeProvider()
    store = ParquetStore(tmp_path)
    sync_reference_data(provider, store, "20250102", "20250103")
    summary = sync_daily_history(provider, store, "20250102", "20250103")

    assert summary.open_days == 1
    assert len(store.read("daily")) == 1
    assert (tmp_path / "raw/fake/daily/20250102.json").exists()
    assert (tmp_path / "processed/adj_factor.parquet").exists()


def test_etf_pipeline_uses_separate_semantic_tables(tmp_path: Path):
    provider = FakeProvider()
    store = ParquetStore(tmp_path)
    sync_etf_reference_data(provider, store)
    summary = sync_etf_history(provider, store, "20250102", "20250103")

    assert summary.open_days == 1
    assert len(store.read("etf_daily")) == 1
    assert (tmp_path / "processed/etf_adj_factor.parquet").exists()


def test_immutable_snapshot_rejects_changed_vendor_history(tmp_path: Path):
    store = ParquetStore(tmp_path)
    original = pd.DataFrame([{"ts_code": "AAA", "trade_date": "20250102", "close": 1.0}])
    changed = pd.DataFrame([{"ts_code": "AAA", "trade_date": "20250102", "close": 2.0}])
    store.save_snapshot("daily", original, "fake", ["ts_code", "trade_date"], "20250102")
    with pytest.raises(FileExistsError):
        store.save_snapshot("daily", changed, "fake", ["ts_code", "trade_date"], "20250102")


def test_resume_skips_existing_snapshots_without_provider_calls(tmp_path: Path):
    class CountingProvider(FakeProvider):
        daily_calls = 0
        factor_calls = 0

        def daily_bars(self, trade_date):
            self.daily_calls += 1
            return super().daily_bars(trade_date)

        def adjustment_factors(self, trade_date):
            self.factor_calls += 1
            return super().adjustment_factors(trade_date)

    provider = CountingProvider()
    store = ParquetStore(tmp_path)
    sync_daily_history(provider, store, "20250102", "20250103", batch_days=10)
    sync_daily_history(provider, store, "20250102", "20250103", batch_days=10)

    assert provider.daily_calls == 1
    assert provider.factor_calls == 1


def test_resume_recovers_raw_snapshot_missing_from_curated(tmp_path: Path):
    provider = FakeProvider()
    store = ParquetStore(tmp_path)
    date = "20250102"
    bars = provider.daily_bars(date)
    factors = provider.adjustment_factors(date)
    store.save_snapshot("daily", bars, provider.name, ["ts_code", "trade_date"], date)
    store.save_snapshot(
        "adj_factor",
        factors,
        provider.name,
        ["ts_code", "trade_date"],
        date,
    )
    (tmp_path / "processed/daily.parquet").unlink()
    (tmp_path / "processed/adj_factor.parquet").unlink()

    class NoFetchProvider(FakeProvider):
        def daily_bars(self, trade_date):
            raise AssertionError("existing raw daily snapshot must not be fetched")

        def adjustment_factors(self, trade_date):
            raise AssertionError("existing raw factor snapshot must not be fetched")

    sync_daily_history(NoFetchProvider(), store, date, "20250103")

    assert len(store.read("daily")) == 1
    assert len(store.read("adj_factor")) == 1


def test_batch_updates_curated_table_once(tmp_path: Path, monkeypatch):
    store = ParquetStore(tmp_path)
    writes = []
    original = pd.DataFrame.to_parquet

    def count_writes(self, path, *args, **kwargs):
        writes.append(str(path))
        return original(self, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", count_writes)
    frames = [
        (date, pd.DataFrame([{"ts_code": "AAA", "trade_date": date, "close": 1.0}]))
        for date in ("20250102", "20250103", "20250106")
    ]
    store.save_snapshots("daily", frames, "fake", ["ts_code", "trade_date"])

    curated_writes = [path for path in writes if "processed" in path]
    assert len(curated_writes) == 1


def test_quality_gate_rejects_impossible_ohlc():
    frame = FakeProvider().daily_bars("20250102")
    frame.loc[0, "high"] = 3.0
    with pytest.raises(DataQualityError):
        validate_daily_bars(frame, "20250102")
