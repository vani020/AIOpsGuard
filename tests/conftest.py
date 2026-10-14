"""Shared fixtures. DB tests run against TEST_DATABASE_URL and are skipped if PostgreSQL is unreachable.

One-time setup:   createdb aiopsguard_test
"""
import os
from dotenv import load_dotenv
import psycopg2
import pytest

load_dotenv()

TEST_URL = os.getenv("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/aiopsguard_test")
os.environ["DATABASE_URL"] = TEST_URL          # must happen before any database import
os.environ.pop("ANTHROPIC_API_KEY", None)      # tests always use the rule-based RCA


def _db_available():
    try:
        psycopg2.connect(TEST_URL, connect_timeout=3).close()
        return True
    except psycopg2.Error:
        return False


DB_UP = _db_available()
requires_db = pytest.mark.skipif(not DB_UP, reason="PostgreSQL test database not reachable")


@pytest.fixture
def db():
    from database import db as dbmod
    from database.init_db import init_db
    from ml import detector
    init_db()
    dbmod.execute("TRUNCATE servers, metrics, processes, logs, incidents, predictions, incident_actions RESTART IDENTITY CASCADE")
    detector.reset()
    yield dbmod


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


def make_payload(cpu=20.0, mem=45.0, host="test-node", procs=None, logs=None, disk=40.0):
    return {"hostname": host, "ip_address": "10.0.0.1", "os_name": "TestOS", "role": "api",
            "metrics": {"cpu_percent": cpu, "memory_percent": mem, "disk_percent": disk,
                        "process_count": 120, "load_avg": 0.8},
            "processes": procs if procs is not None else [
                {"pid": 100, "name": "nginx", "cpu_percent": 3.0, "memory_percent": 2.0, "status": "running"}],
            "logs": logs or []}
