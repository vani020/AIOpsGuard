"""Incident lifecycle: create, update, retrieve, resolve + human-approved remediation actions."""
from psycopg2.errors import UniqueViolation

from database.db import fetch_all, fetch_one, execute, transaction

STATUSES = ("open", "investigating", "pending_approval", "resolved", "closed")
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
def severity_from_risk(risk):
    if risk >= 0.85:
        return "critical"
    if risk >= 0.65:
        return "high"
    if risk >= 0.4:
        return "medium"
    return "low"


def open_or_update(server_id, metric_id, risk, reasons):
    """Return (incident, created). Only one active incident per server; severity can only escalate."""
    sev = severity_from_risk(risk)
    try:
        with transaction() as cur:
            cur.execute("""SELECT * FROM incidents WHERE server_id = %s AND status NOT IN ('resolved', 'closed')
                           FOR UPDATE""", (server_id,))
            row = cur.fetchone()
            if row:
                if SEVERITY_RANK[sev] > SEVERITY_RANK[row["severity"]]:
                    cur.execute("UPDATE incidents SET severity = %s WHERE incident_id = %s RETURNING *",
                                (sev, row["incident_id"]))
                    row = cur.fetchone()
                return row, False
            title = (reasons[0] if reasons else "Anomalous behaviour detected")[:200]
            cur.execute("""INSERT INTO incidents (server_id, trigger_metric_id, title, severity)
                           VALUES (%s, %s, %s, %s) RETURNING *""", (server_id, metric_id, title, sev))
            return cur.fetchone(), True
    except UniqueViolation:  # lost a race with a concurrent request
        return open_or_update(server_id, metric_id, risk, reasons)


def get_active_incident(server_id):
    return fetch_one("SELECT * FROM incidents WHERE server_id = %s AND status NOT IN ('resolved', 'closed')",
                     (server_id,))


def get_incident(incident_id):
    inc = fetch_one("SELECT * FROM v_incident_summary WHERE incident_id = %s", (incident_id,))
    if not inc:
        return None
    inc["actions"] = fetch_all("SELECT * FROM incident_actions WHERE incident_id = %s ORDER BY action_id",
                               (incident_id,))
    inc["predictions"] = fetch_all(
        """SELECT prediction_id, created_at, anomaly_score, failure_risk, is_anomaly, reasons, model_name
           FROM predictions WHERE incident_id = %s ORDER BY created_at DESC LIMIT 10""", (incident_id,))
    return inc


def list_incidents(status=None, limit=50):
    if status == "active":
        return fetch_all("""SELECT * FROM v_incident_summary WHERE status NOT IN ('resolved', 'closed')
                            ORDER BY detected_at DESC LIMIT %s""", (limit,))
    if status:
        return fetch_all("SELECT * FROM v_incident_summary WHERE status = %s ORDER BY detected_at DESC LIMIT %s",
                         (status, limit))
    return fetch_all("SELECT * FROM v_incident_summary ORDER BY detected_at DESC LIMIT %s", (limit,))


def update_status(incident_id, status):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with transaction() as cur:
        cur.execute("UPDATE incidents SET status = %s WHERE incident_id = %s RETURNING incident_id",
                    (status, incident_id))
        if not cur.fetchone():
            raise LookupError("incident not found")
        if status in ("resolved", "closed"):
            _cancel_pending(cur, incident_id, "system:incident closed")


def _cancel_pending(cur, incident_id, who):
    cur.execute("""UPDATE incident_actions SET status = 'rejected', decided_by = %s, decided_at = now()
                   WHERE incident_id = %s AND status = 'pending'""", (who, incident_id))


def auto_resolve_if_recovered(server_id, quiet_samples=5):
    """Resolve the active incident when the last N predictions were all normal."""
    inc = get_active_incident(server_id)
    if not inc:
        return False
    rows = fetch_all("SELECT is_anomaly FROM predictions WHERE server_id = %s ORDER BY created_at DESC, prediction_id DESC LIMIT %s",
                     (server_id, quiet_samples))
    if len(rows) < quiet_samples or any(r["is_anomaly"] for r in rows):
        return False
    update_status(inc["incident_id"], "resolved")
    return True


def save_rca(incident_id, rca, source):
    """Persist RCA output and queue suggested actions for human approval."""
    actions = rca.get("suggested_actions") or []
    with transaction() as cur:
        cur.execute("""UPDATE incidents SET category = %s, root_cause = %s, explanation = %s,
                              recommendation = %s, rca_source = %s
                       WHERE incident_id = %s AND status NOT IN ('resolved', 'closed') RETURNING status""",
                    (rca.get("category"), rca.get("root_cause"), rca.get("explanation"),
                     rca.get("recommendation"), source, incident_id))
        row = cur.fetchone()
        if not row:
            return
        queued = 0
        for a in actions:
            cur.execute("""SELECT 1 FROM incident_actions WHERE incident_id = %s AND target_pid = %s
                           AND status IN ('pending', 'approved')""", (incident_id, a["pid"]))
            if cur.fetchone():
                continue
            cur.execute("""INSERT INTO incident_actions (incident_id, action_type, target_pid, target_name, reason)
                           VALUES (%s, 'kill_process', %s, %s, %s)""",
                        (incident_id, a["pid"], a["name"], a.get("reason")))
            queued += 1
        if row["status"] in ("open", "investigating"):
            new_status = "pending_approval" if (queued or _has_pending(cur, incident_id)) else "investigating"
            cur.execute("UPDATE incidents SET status = %s WHERE incident_id = %s", (new_status, incident_id))


def _has_pending(cur, incident_id):
    cur.execute("SELECT 1 FROM incident_actions WHERE incident_id = %s AND status = 'pending'", (incident_id,))
    return cur.fetchone() is not None


def find_similar(category, exclude_id, limit=3):
    """Historical retrieval: resolved incidents of the same category (most recent first)."""
    if not category:
        return []
    return fetch_all(
        """SELECT incident_id, title, category, root_cause, recommendation, detected_at
           FROM incidents WHERE status IN ('resolved', 'closed') AND category = %s
             AND incident_id <> %s AND root_cause IS NOT NULL
           ORDER BY detected_at DESC LIMIT %s""", (category, exclude_id, limit))


# ---------- human approval workflow ----------
def decide_action(action_id, approve, decided_by="admin"):
    with transaction() as cur:
        cur.execute("""UPDATE incident_actions SET status = %s, decided_by = %s, decided_at = now()
                       WHERE action_id = %s AND status = 'pending' RETURNING incident_id""",
                    ("approved" if approve else "rejected", decided_by, action_id))
        row = cur.fetchone()
        if not row:
            raise LookupError("action not found or already decided")
        inc_id = row["incident_id"]
        if not _has_pending(cur, inc_id):
            cur.execute("UPDATE incidents SET status = 'investigating' WHERE incident_id = %s AND status = 'pending_approval'",
                        (inc_id,))
        return inc_id


def pending_for_agent(hostname):
    return fetch_all(
        """SELECT a.action_id, a.action_type, a.target_pid, a.target_name
           FROM incident_actions a JOIN incidents i USING (incident_id) JOIN servers s USING (server_id)
           WHERE s.hostname = %s AND a.status = 'approved' ORDER BY a.action_id""", (hostname,))


def record_action_result(action_id, ok, message):
    n = execute("""UPDATE incident_actions SET status = %s, executed_at = now(), result_message = %s
                   WHERE action_id = %s AND status = 'approved'""",
                ("executed" if ok else "failed", (message or "")[:500], action_id))
    if not n:
        raise LookupError("action not found or not approved")
