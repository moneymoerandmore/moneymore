from pathlib import Path

from moneymore.point_in_time import materialize_point_in_time_store

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    print(materialize_point_in_time_store(ROOT))
