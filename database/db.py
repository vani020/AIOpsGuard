"""PostgreSQL connection pool + small helpers. All access goes through transaction()."""
import os
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

load_dotenv()
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        dsn = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/aiopsguard")
        _pool = ThreadedConnectionPool(1, 10, dsn)
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def transaction():
    """Yields a dict cursor. Commits on success, rolls back on any exception."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def fetch_all(sql, params=None):
    with transaction() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql, params=None):
    with transaction() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql, params=None):
    with transaction() as cur:
        cur.execute(sql, params)
        return cur.rowcount
