"""Human-approved traffic failover to a safer server."""
import pytest
import requests

from incidents import failover
from tests.conftest import VIEWER, make_payload, requires_db
from tests.test_integration_pipeline import RUNAWAY, warm_up

pytestmark = requires_db


def healthy(client, host, n=3, **kw):
    for _ in range(n):
        assert client.post("/api/ingest", json=make_payload(cpu=18, mem=40, host=host, **kw)).status_code == 200


def go_critical(client, host="node-a", mem=95):
    warm_up(client, host=host)
    res = client.post("/api/ingest", json=make_payload(cpu=99, mem=mem, host=host, procs=RUNAWAY)).json()
    inc = client.get(f"/api/incidents/{res['incident_id']}").json()
    assert inc["severity"] == "critical", inc["severity"]
    return inc


def migrate_actions(inc):
    return [a for a in inc["actions"] if a["action_type"] == "migrate_traffic"]


def route(client, service):
    return next(r for r in client.get("/api/routes").json()["routes"] if r["service_name"] == service)


def test_each_server_gets_a_default_route(client):
    healthy(client, "node-a", 1)
    r = route(client, "node-a-service")
    assert r["primary_hostname"] == r["active_hostname"] == "node-a" and not r["failed_over"]


def test_critical_incident_proposes_shift_to_healthy_server(client):
    healthy(client, "node-b")
    inc = go_critical(client)
    (m,) = migrate_actions(inc)
    assert (m["status"], m["from_hostname"], m["to_hostname"], m["service_name"]) == ("pending", "node-a", "node-b", "node-a-service")
    assert inc["status"] == "pending_approval" and "node-b" in m["reason"]
    assert route(client, "node-a-service")["active_hostname"] == "node-a"      # nothing moves before approval


def test_any_anomaly_proposes_failover_by_default(client):
    """Not only critical: the approval request appears as soon as an anomaly incident exists."""
    healthy(client, "node-b")
    warm_up(client, host="node-a")
    res = client.post("/api/ingest", json=make_payload(cpu=93, host="node-a", procs=RUNAWAY)).json()
    inc = client.get(f"/api/incidents/{res['incident_id']}").json()
    assert inc["severity"] in ("medium", "high", "critical") and len(migrate_actions(inc)) == 1
    assert inc["status"] == "pending_approval"


def test_minimum_severity_can_be_raised_to_critical_only(client, monkeypatch):
    monkeypatch.setenv("FAILOVER_MIN_SEVERITY", "critical")
    healthy(client, "node-b")
    warm_up(client, host="node-a")
    res = client.post("/api/ingest", json=make_payload(cpu=93, host="node-a", procs=RUNAWAY)).json()
    inc = client.get(f"/api/incidents/{res['incident_id']}").json()
    if inc["severity"] != "critical":
        assert migrate_actions(inc) == []


def test_no_proposal_when_no_safe_server_exists(client):
    inc = go_critical(client)                     # only one server in the fleet
    assert migrate_actions(inc) == []
    opts = client.get(f"/api/incidents/{inc['incident_id']}/failover-options").json()
    assert opts["candidates"] == []


@pytest.mark.parametrize("kw,reason", [
    (dict(cpu=75, mem=40), "CPU"), (dict(cpu=18, mem=82), "RAM"), (dict(cpu=18, mem=40, disk=90.0), "disk"),
    (dict(cpu=18, mem=40, swap_percent=70.0), "swap")])
def test_unsafe_servers_are_never_proposed(client, kw, reason):
    for _ in range(3):
        client.post("/api/ingest", json=make_payload(host="node-b", **kw))
    inc = go_critical(client)
    assert migrate_actions(inc) == []
    cand = client.get(f"/api/incidents/{inc['incident_id']}/failover-options").json()["candidates"][0]
    assert not cand["safe"] and any(reason in w for w in cand["rejected_because"])


def test_server_with_active_incident_is_excluded(client):
    healthy(client, "node-b", 2)
    warm_up(client, host="node-b")
    client.post("/api/ingest", json=make_payload(cpu=97, host="node-b"))     # node-b now has its own incident
    inc = go_critical(client)
    assert migrate_actions(inc) == []


def test_best_headroom_server_is_chosen(client):
    healthy(client, "node-busy")
    for _ in range(3):
        client.post("/api/ingest", json=make_payload(cpu=50, mem=60, host="node-busy"))
    healthy(client, "node-idle")
    for _ in range(3):
        client.post("/api/ingest", json=make_payload(cpu=5, mem=20, host="node-idle"))
    (m,) = migrate_actions(go_critical(client))
    assert m["to_hostname"] == "node-idle"


def test_approval_shifts_route_atomically_and_is_audited(client):
    healthy(client, "node-b")
    inc = go_critical(client)
    (m,) = migrate_actions(inc)
    r = client.post(f"/api/actions/{m['action_id']}/decision", json={"approve": True, "decided_by": "mallory"})
    assert r.status_code == 200
    done = migrate_actions(r.json())[0]
    assert done["status"] == "executed" and done["decided_by"] == "alice" and "node-a -> node-b" in done["result_message"]
    rt = route(client, "node-a-service")
    assert rt["active_hostname"] == "node-b" and rt["failed_over"] and rt["updated_by"] == "alice"
    ev = client.get("/api/routes").json()["events"][0]
    assert (ev["kind"], ev["from_hostname"], ev["to_hostname"], ev["performed_by"]) == ("failover", "node-a", "node-b", "alice")
    audit = [a for a in client.get("/api/audit/actions").json() if a["action_type"] == "migrate_traffic"]
    assert audit and audit[0]["status"] == "executed"
    assert client.get("/api/agents/node-a/actions").json() == []          # agents never receive migrations


def test_rejecting_leaves_route_untouched(client):
    healthy(client, "node-b")
    (m,) = migrate_actions(go_critical(client))
    r = client.post(f"/api/actions/{m['action_id']}/decision", json={"approve": False})
    assert migrate_actions(r.json())[0]["status"] == "rejected"
    assert route(client, "node-a-service")["active_hostname"] == "node-a"


def test_viewer_and_anonymous_cannot_approve_failover(client, anon):
    healthy(client, "node-b")
    (m,) = migrate_actions(go_critical(client))
    url = f"/api/actions/{m['action_id']}/decision"
    assert anon.post(url, json={"approve": True}).status_code == 401
    assert anon.post(url, json={"approve": True}, headers=VIEWER).status_code == 403
    assert route(client, "node-a-service")["active_hostname"] == "node-a"


def test_target_that_became_unsafe_is_replaced_or_refused(client, db):
    healthy(client, "node-b")
    healthy(client, "node-c")
    (m,) = migrate_actions(go_critical(client))
    chosen = m["to_hostname"]
    other = "node-c" if chosen == "node-b" else "node-b"
    for _ in range(2):                                                          # chosen target overloads before approval
        client.post("/api/ingest", json=make_payload(cpu=88, mem=40, host=chosen))
    done = migrate_actions(client.post(f"/api/actions/{m['action_id']}/decision", json={"approve": True}).json())[0]
    assert done["status"] == "executed" and done["to_hostname"] == other and "re-selected" in done["result_message"]


def test_approval_refused_when_no_safe_target_remains(client):
    healthy(client, "node-b")
    (m,) = migrate_actions(go_critical(client))
    for _ in range(2):
        client.post("/api/ingest", json=make_payload(cpu=95, mem=40, host="node-b"))
    r = client.post(f"/api/actions/{m['action_id']}/decision", json={"approve": True})
    assert r.status_code == 409 and "No safe target" in r.json()["detail"]
    assert route(client, "node-a-service")["active_hostname"] == "node-a"
    audit = [a for a in client.get("/api/audit/actions").json() if a["action_type"] == "migrate_traffic"][0]
    assert audit["status"] == "rejected" and audit["decided_by"] == "system:safety"


def test_double_approval_is_rejected(client):
    healthy(client, "node-b")
    (m,) = migrate_actions(go_critical(client))
    assert client.post(f"/api/actions/{m['action_id']}/decision", json={"approve": True}).status_code == 200
    assert client.post(f"/api/actions/{m['action_id']}/decision", json={"approve": True}).status_code == 404


def test_restore_primary_after_recovery_and_guard_while_still_critical(client):
    healthy(client, "node-b")
    inc = go_critical(client)
    (m,) = migrate_actions(inc)
    client.post(f"/api/actions/{m['action_id']}/decision", json={"approve": True})
    rid = route(client, "node-a-service")["route_id"]
    assert client.post(f"/api/routes/{rid}/restore").status_code == 409        # node-a still has an active incident
    client.post(f"/api/incidents/{inc['incident_id']}/status", json={"status": "resolved"})
    assert client.post(f"/api/routes/{rid}/restore").status_code == 200
    assert route(client, "node-a-service")["active_hostname"] == "node-a"
    assert client.get("/api/routes").json()["events"][0]["kind"] == "restore"
    assert client.post(f"/api/routes/{rid}/restore").status_code == 409        # already on primary


def test_viewer_cannot_restore(client, anon):
    healthy(client, "node-b")
    (m,) = migrate_actions(go_critical(client))
    client.post(f"/api/actions/{m['action_id']}/decision", json={"approve": True})
    rid = route(client, "node-a-service")["route_id"]
    assert anon.post(f"/api/routes/{rid}/restore", headers=VIEWER).status_code == 403


def test_closing_incident_cancels_pending_failover(client):
    healthy(client, "node-b")
    inc = go_critical(client)
    client.post(f"/api/incidents/{inc['incident_id']}/status", json={"status": "closed"})
    got = client.get(f"/api/incidents/{inc['incident_id']}").json()
    assert migrate_actions(got)[0]["status"] == "rejected"


def test_proposal_is_idempotent_across_repeated_samples(client):
    healthy(client, "node-b")
    inc = go_critical(client)
    for _ in range(3):
        client.post("/api/ingest", json=make_payload(cpu=99, mem=95, host="node-a", procs=RUNAWAY))
    assert len(migrate_actions(client.get(f"/api/incidents/{inc['incident_id']}").json())) == 1


def test_kill_and_failover_proposals_coexist_and_are_independent(client):
    healthy(client, "node-b")
    inc = go_critical(client, mem=60)           # CPU-dominated -> RCA also proposes killing the culprit
    kinds = sorted(a["action_type"] for a in inc["actions"])
    assert kinds == ["kill_process", "migrate_traffic"]
    kill = next(a for a in inc["actions"] if a["action_type"] == "kill_process")
    mig = migrate_actions(inc)[0]
    client.post(f"/api/actions/{mig['action_id']}/decision", json={"approve": True})
    assert client.get("/api/agents/node-a/actions").json() == []                # kill still needs its own approval
    client.post(f"/api/actions/{kill['action_id']}/decision", json={"approve": True})
    assert [a["action_id"] for a in client.get("/api/agents/node-a/actions").json()] == [kill["action_id"]]


def test_webhook_is_called_and_failure_does_not_undo_the_shift(client, monkeypatch):
    calls = []
    monkeypatch.setenv("FAILOVER_WEBHOOK_URL", "http://lb.local/hook")
    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        raise requests.ConnectionError("lb down")
    monkeypatch.setattr(failover.requests, "post", fake_post)
    healthy(client, "node-b")
    (m,) = migrate_actions(go_critical(client))
    r = client.post(f"/api/actions/{m['action_id']}/decision", json={"approve": True})
    assert r.status_code == 200 and calls[0]["to"] == "node-b" and calls[0]["service"] == "node-a-service"
    assert "webhook failed" in migrate_actions(r.json())[0]["result_message"]
    assert route(client, "node-a-service")["active_hostname"] == "node-b"
