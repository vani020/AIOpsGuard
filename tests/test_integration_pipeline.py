
import random

from tests.conftest import make_payload, requires_db

pytestmark = requires_db

RUNAWAY = [{"pid": 4242, "name": "stress-cpu", "cpu_percent": 85.0, "memory_percent": 1.5, "status": "running"},
           {"pid": 100, "name": "nginx", "cpu_percent": 2.0, "memory_percent": 2.0, "status": "running"}]


def warm_up(client, n=45, host="test-node"):
    r = random.Random(7)
    for _ in range(n):
        res = client.post("/api/ingest", json=make_payload(cpu=r.gauss(20, 3), mem=r.gauss(45, 2), host=host))
        assert res.status_code == 200
        assert not res.json()["is_anomaly"]


def test_normal_traffic_creates_no_incident(client):
    warm_up(client)
    assert client.get("/api/incidents").json() == []
    assert client.get("/api/servers").json()[0]["display_status"] == "healthy"


def test_cpu_overload_creates_incident_with_rca(client):
    warm_up(client)
    res = client.post("/api/ingest", json=make_payload(cpu=97, procs=RUNAWAY)).json()
    assert res["is_anomaly"] and res["incident_created"]
    inc = client.get(f"/api/incidents/{res['incident_id']}").json()
    assert inc["category"] == "cpu_saturation"
    assert inc["rca_source"] == "rules"
    assert "stress-cpu" in inc["root_cause"]
    assert inc["status"] == "pending_approval"
    assert inc["actions"][0]["target_pid"] == 4242 and inc["actions"][0]["status"] == "pending"


def test_repeated_anomalies_reuse_same_incident(client):
    warm_up(client)
    ids = {client.post("/api/ingest", json=make_payload(cpu=97, procs=RUNAWAY)).json()["incident_id"] for _ in range(3)}
    assert len(ids) == 1
    assert len(client.get("/api/incidents").json()) == 1


def test_log_anomaly_creates_incident(client):
    warm_up(client)
    logs = [{"level": "CRITICAL", "message": "worker out of memory"}, {"level": "ERROR", "message": "db connection timeout"}]
    res = client.post("/api/ingest", json=make_payload(logs=logs)).json()
    assert res["incident_created"]


def test_human_approval_flow_and_agent_channel(client):
    warm_up(client)
    res = client.post("/api/ingest", json=make_payload(cpu=97, procs=RUNAWAY)).json()
    inc = client.get(f"/api/incidents/{res['incident_id']}").json()
    action_id = inc["actions"][0]["action_id"]

    assert client.get("/api/agents/test-node/actions").json() == []          # not approved yet -> agent sees nothing
    assert client.post(f"/api/actions/{action_id}/decision", json={"approve": True, "decided_by": "ira"}).status_code == 200
    queued = client.get("/api/agents/test-node/actions").json()
    assert [a["target_pid"] for a in queued] == [4242]

    assert client.post(f"/api/agents/actions/{action_id}/result", json={"ok": True, "message": "killed"}).status_code == 200
    final = client.get(f"/api/incidents/{res['incident_id']}").json()
    assert final["actions"][0]["status"] == "executed"
    assert client.post(f"/api/actions/{action_id}/decision", json={"approve": True}).status_code == 404  # no double decision


def test_rejected_action_never_reaches_agent(client):
    warm_up(client)
    res = client.post("/api/ingest", json=make_payload(cpu=97, procs=RUNAWAY)).json()
    action_id = client.get(f"/api/incidents/{res['incident_id']}").json()["actions"][0]["action_id"]
    client.post(f"/api/actions/{action_id}/decision", json={"approve": False})
    assert client.get("/api/agents/test-node/actions").json() == []


def test_recovery_auto_resolves_incident(client):
    warm_up(client)
    res = client.post("/api/ingest", json=make_payload(cpu=97, procs=RUNAWAY)).json()
    r = random.Random(3)
    for _ in range(6):
        client.post("/api/ingest", json=make_payload(cpu=r.gauss(20, 3), mem=r.gauss(45, 2)))
    inc = client.get(f"/api/incidents/{res['incident_id']}").json()
    assert inc["status"] == "resolved" and inc["resolved_at"] is not None
    assert all(a["status"] != "pending" for a in inc["actions"])


def test_invalid_payload_rejected(client):
    bad = make_payload(cpu=150)
    assert client.post("/api/ingest", json=bad).status_code == 422


def test_similar_resolved_incident_is_retrieved(client, db):
    from database.seed import seed
    seed()
    warm_up(client)
    res = client.post("/api/ingest", json=make_payload(cpu=97, procs=RUNAWAY)).json()
    inc = client.get(f"/api/incidents/{res['incident_id']}").json()
    assert "similar past incident" in inc["explanation"]
