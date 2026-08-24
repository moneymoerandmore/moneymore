from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pandas as pd

from .data.provider import MarketDataProvider
from .data.quality import validate_daily_basic, validate_instruments
from .data.store import ParquetStore

UNIVERSE_NAME = "cn_a_market_cap_top1000"
UNIVERSE_SIZE = 1000


def board_for(symbol: str) -> str:
    code, exchange = symbol.split(".", 1)
    if exchange == "SH" and code.startswith(("688", "689")):
        return "科创板"
    if exchange == "SZ" and code.startswith(("300", "301")):
        return "创业板"
    if exchange == "SH":
        return "沪市主板"
    return "深市主板"


def eligible_a_share(symbol: str) -> bool:
    if symbol.endswith(".SH"):
        return symbol[:3] in {"600", "601", "603", "605", "688", "689"}
    if symbol.endswith(".SZ"):
        return symbol[:3] in {
            "000", "001", "002", "003", "300", "301",
        }
    return False


def build_market_cap_universe(
    instruments: pd.DataFrame,
    daily_basic: pd.DataFrame,
    effective_date: str,
    size: int = UNIVERSE_SIZE,
) -> pd.DataFrame:
    validate_instruments(instruments)
    validate_daily_basic(daily_basic)
    active = instruments.loc[
        (instruments["list_status"].astype(str) == "L")
        & instruments["ts_code"].astype(str).map(eligible_a_share)
    ].copy()
    # Risk-warning and delisting-transition names are not suitable for a
    # general-purpose simulated portfolio even if their market cap is large.
    names = active["name"].fillna("").astype(str).str.upper()
    active = active.loc[~names.str.contains("ST|退", regex=True)].copy()
    snapshot = daily_basic.copy()
    snapshot["total_mv"] = pd.to_numeric(snapshot["total_mv"], errors="coerce")
    snapshot = snapshot.dropna(subset=["total_mv"])
    latest = str(snapshot["trade_date"].astype(str).max())
    snapshot = snapshot.loc[snapshot["trade_date"].astype(str) == latest]
    ranked = active.merge(snapshot, on="ts_code", how="inner", validate="one_to_one")
    ranked = ranked.sort_values(
        ["total_mv", "ts_code"], ascending=[False, True]
    ).head(size).reset_index(drop=True)
    if len(ranked) != size:
        raise ValueError(f"market-cap universe expected {size} stocks, got {len(ranked)}")
    ranked["rank"] = ranked.index + 1
    ranked["symbol"] = ranked["ts_code"].astype(str)
    ranked["industry"] = ranked["industry"].fillna("未分类").astype(str)
    ranked["board"] = ranked["symbol"].map(board_for)
    ranked["effective_date"] = effective_date
    ranked["market_data_date"] = latest
    ranked["universe"] = UNIVERSE_NAME
    signature = "|".join(ranked["symbol"])
    ranked["universe_version"] = (
        f"{UNIVERSE_NAME}@{effective_date}-"
        f"{hashlib.sha256(signature.encode()).hexdigest()[:12]}"
    )
    ranked["classification_source"] = "TUSHARE_STOCK_BASIC_CURRENT"
    ranked["selection_source"] = "TUSHARE_DAILY_BASIC_TOTAL_MV"
    ranked["created_at"] = datetime.now(UTC).isoformat()
    return ranked[
        [
            "effective_date", "market_data_date", "universe", "universe_version",
            "rank", "symbol", "name", "industry", "board", "total_mv", "circ_mv",
            "classification_source", "selection_source", "created_at",
        ]
    ]


def refresh_market_cap_universe(
    provider: MarketDataProvider,
    store: ParquetStore,
    effective_date: str,
    size: int = UNIVERSE_SIZE,
    force: bool = False,
) -> dict[str, object]:
    if not force:
        try:
            active = active_strategy_universe(store, effective_date)
            if str(active.iloc[0]["effective_date"])[:6] == effective_date[:6]:
                return {**universe_summary(active), "refreshed": False}
        except (FileNotFoundError, ValueError):
            pass
    instruments = provider.instruments()
    daily_basic = provider.daily_basic("", effective_date, effective_date)
    universe = build_market_cap_universe(
        instruments, daily_basic, effective_date, size
    )
    store.save_snapshot(
        "instruments", instruments, provider.name, ["ts_code"], effective_date
    )
    store.save_snapshot(
        "daily_basic", daily_basic, provider.name,
        ["ts_code", "trade_date"], f"all_market_{effective_date}",
    )
    store.merge_curated(
        "strategy_universe", [universe], ["effective_date", "symbol"]
    )
    return {**universe_summary(universe), "refreshed": True}


def active_strategy_universe(
    store: ParquetStore, as_of_date: str | None = None
) -> pd.DataFrame:
    frame = store.read("strategy_universe")
    frame["effective_date"] = frame["effective_date"].astype(str)
    if as_of_date:
        frame = frame.loc[frame["effective_date"] <= as_of_date]
    if frame.empty:
        raise ValueError("no effective strategy universe is available")
    effective = str(frame["effective_date"].max())
    return frame.loc[frame["effective_date"] == effective].sort_values("rank")


def universe_summary(frame: pd.DataFrame) -> dict[str, object]:
    return {
        "universe": str(frame.iloc[0]["universe"]),
        "universe_version": str(frame.iloc[0]["universe_version"]),
        "effective_date": str(frame.iloc[0]["effective_date"]),
        "market_data_date": str(frame.iloc[0]["market_data_date"]),
        "size": len(frame),
        "board_counts": frame.groupby("board")["symbol"].count().to_dict(),
        "industry_counts": (
            frame.groupby("industry")["symbol"].count().sort_values(ascending=False).to_dict()
        ),
        "total_market_value": float(frame["total_mv"].astype(float).sum()),
    }


def execution_strategy_id(canonical: str, store: ParquetStore) -> str:
    universe = active_strategy_universe(store)
    version = str(universe.iloc[0]["universe_version"])
    return f"{canonical}__{version.split('@', 1)[-1]}"
