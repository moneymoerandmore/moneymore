from pathlib import Path

from moneymore.historical_pk import run_historical_pk

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    result = run_historical_pk(ROOT)
    for row in result["strategies"]:
        print(row["strategy_id"], row["summary"])
