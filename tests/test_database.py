import psycopg2
import pytest

from database import repository as repo
from tests.conftest import make_payload, requires_db

pytestmark = requires_db


def store(host="db-test", **kw):
    p = make_payload(host=host, **kw)
    return repo.store_telemetry(p["hostname"], p["ip_address"], p["os_name"], p["role"], p["metrics"],
                                p["processes"], p["logs"])


def test_store_creates_server_metric_and_processes(db):
    sid, mid = store()
    assert len(repo.processes_for_metric(mid)) == 1
    assert repo.get_server(sid)["hostname"] == "db-test"


def test_trigger_updates_last_seen(db):
    sid, _ = store()
    assert repo.get_server(sid)["last_seen"] is not None


def test_upsert_does_not_duplicate_servers(db):
    store(); store()
    assert db.fetch_one("SELECT count(*) AS n FROM servers")["n"] == 1


def test_check_constraint_rejects_invalid_cpu(db):
    sid, _ = store()
    with pytest.raises(psycopg2.errors.CheckViolation):
        db.execute("INSERT INTO metrics (server_id, cpu_percent, memory_percent) VALUES (%s, 150, 10)", (sid,))


def test_transaction_rolls_back_on_failure(db):
    p = make_payload(host="atomic")
    bad_logs = [{"level": "NOT_A_LEVEL", "message": "x"}]  # violates CHECK -> whole ingest must roll back
    with pytest.raises(psycopg2.Error):
        repo.store_telemetry("atomic", None, None, "api", p["metrics"], p["processes"], bad_logs)
    assert db.fetch_one("SELECT count(*) AS n FROM servers WHERE hostname = 'atomic'")["n"] == 0
    assert db.fetch_one("SELECT count(*) AS n FROM metrics")["n"] == 0


def test_one_active_incident_per_server(db):
    sid, mid = store()
    db.execute("INSERT INTO incidents (server_id, title) VALUES (%s, 'a')", (sid,))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        db.execute("INSERT INTO incidents (server_id, title) VALUES (%s, 'b')", (sid,))


def test_resolve_trigger_sets_resolved_at(db):
    sid, _ = store()
    db.execute("INSERT INTO incidents (server_id, title) VALUES (%s, 'a')", (sid,))
    db.execute("UPDATE incidents SET status = 'resolved'")
    assert db.fetch_one("SELECT resolved_at FROM incidents")["resolved_at"] is not None


def test_cascade_delete_removes_children(db):
    sid, _ = store()
    db.execute("DELETE FROM servers WHERE server_id = %s", (sid,))
    assert db.fetch_one("SELECT count(*) AS n FROM metrics")["n"] == 0
    assert db.fetch_one("SELECT count(*) AS n FROM processes")["n"] == 0


def test_overview_view_returns_latest_metrics(db):
    store(cpu=11.0); store(cpu=33.0)
    row = repo.list_servers()[0]
    assert row["cpu_percent"] == pytest.approx(33.0)
