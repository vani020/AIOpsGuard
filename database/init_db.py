"""Create (or reset) the AIOpsGuard schema.

    python -m database.init_db           # create tables if missing
    python -m database.init_db --reset   # DROP everything and recreate (dev only!)
"""
import sys
from pathlib import Path

from database.db import transaction

SCHEMA = Path(__file__).with_name("schema.sql")


def init_db(reset: bool = False) -> None:
    with transaction() as cur:
        if reset:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cur.execute(SCHEMA.read_text(encoding="utf-8"))


if __name__ == "__main__":
    init_db(reset="--reset" in sys.argv)
    print("Database ready.")
