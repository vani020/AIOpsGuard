
import logging
import os

import requests

from database.db import fetch_all, fetch_one, transaction

log = logging.getLogger("aiopsguard.failover")

SAFE_CPU, SAFE_MEM, SAFE_DISK, SAFE_SWAP = 60.0, 70.0, 85.0, 50.0
MAX_RISK = 0.4
STALE_SECONDS = int(os.getenv("FAILOVER_STALE_SECONDS", 60))


SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def min_severity():
    """FAILOVER_MIN_SEVERITY (default 'low' = every detected anomaly raises an approval request)."""
    v = os.getenv("FAILOVER_MIN_SEVERITY", "low").strip().lower()
    return v if v in SEVERITY_RANK else "low"


class NoSafeTargetError(ValueError):
    """No healthy server is available to take the traffic."""


class RouteConflictError(ValueError):
    """The route changed since the action was proposed."""


# ---------------------------------------------------------------- routes
def ensure_default_route(server_id, hostname):
    """Every reporting server owns one default service route (idempotent)."""
    from database.db import execute
    execute("""INSERT INTO traffic_routes (service_name, primary_server_id, active_server_id, updated_by)
               VALUES (%s, %s, %s, 'auto') ON CONFLICT (service_name) DO NOTHING""",
            (f"{hostname}-service", server_id, server_id))


def list_routes():
    routes = fetch_all("""SELECT r.route_id, r.service_name, r.updated_at, r.updated_by,
                                 r.primary_server_id, p.hostname AS primary_hostname,
                                 r.active_server_id,  a.hostname AS active_hostname,
                                 (r.primary_server_id <> r.active_server_id) AS failed_over
                          FROM traffic_routes r JOIN servers p ON p.server_id = r.primary_server_id
                          JOIN servers a ON a.server_id = r.active_server_id ORDER BY r.service_name""")
    events = fetch_all("""SELECT e.event_id, e.ts, e.kind, e.performed_by, e.reason, e.action_id, r.service_name,
                                 f.hostname AS from_hostname, t.hostname AS to_hostname
                          FROM route_events e JOIN traffic_routes r USING (route_id)
                          LEFT JOIN servers f ON f.server_id = e.from_server_id
                          LEFT JOIN servers t ON t.server_id = e.to_server_id ORDER BY e.event_id DESC LIMIT 15""")
    return {"routes": routes, "events": events}


# ---------------------------------------------------------------- target selection
def ranked_candidates(source_id):
    """Every other server with its health snapshot, a safety verdict, and a score (higher = more headroom)."""
    rows = fetch_all(
        """SELECT s.server_id, s.hostname, m.cpu_percent, m.memory_percent, m.disk_percent, m.swap_percent,
                  EXTRACT(EPOCH FROM (now() - s.last_seen)) AS seen_ago,
                  COALESCE((SELECT failure_risk FROM predictions p WHERE p.server_id = s.server_id
                            ORDER BY created_at DESC LIMIT 1), 0) AS risk,
                  EXISTS (SELECT 1 FROM incidents i WHERE i.server_id = s.server_id
                          AND i.status NOT IN ('resolved', 'closed')) AS has_incident,
                  (SELECT count(*) FROM traffic_routes r WHERE r.active_server_id = s.server_id) AS routes_hosted
           FROM servers s
           JOIN LATERAL (SELECT * FROM metrics WHERE server_id = s.server_id ORDER BY ts DESC LIMIT 1) m ON true
           WHERE s.server_id <> %s""", (source_id,))
    out = []
    for r in rows:
        why = []
        if r["seen_ago"] is None or r["seen_ago"] > STALE_SECONDS:
            why.append("not reporting (stale)")
        if r["has_incident"]:
            why.append("has an active incident")
        if r["cpu_percent"] >= SAFE_CPU:
            why.append(f"CPU {r['cpu_percent']:.0f}% >= {SAFE_CPU:.0f}%")
        if r["memory_percent"] >= SAFE_MEM:
            why.append(f"RAM {r['memory_percent']:.0f}% >= {SAFE_MEM:.0f}%")
        if r["disk_percent"] >= SAFE_DISK:
            why.append(f"disk {r['disk_percent']:.0f}% >= {SAFE_DISK:.0f}%")
        if r["swap_percent"] >= SAFE_SWAP:
            why.append(f"swap {r['swap_percent']:.0f}% >= {SAFE_SWAP:.0f}%")
        if r["risk"] >= MAX_RISK:
            why.append(f"failure risk {r['risk']:.2f} >= {MAX_RISK}")
        score = 100 - (0.4 * r["cpu_percent"] + 0.4 * r["memory_percent"] + 0.1 * r["disk_percent"]
                       + 0.1 * r["swap_percent"]) - 10 * r["routes_hosted"] - 30 * r["risk"]
        out.append({"server_id": r["server_id"], "hostname": r["hostname"], "cpu_percent": r["cpu_percent"],
                    "memory_percent": r["memory_percent"], "disk_percent": r["disk_percent"],
                    "swap_percent": r["swap_percent"], "risk": round(float(r["risk"]), 2),
                    "routes_hosted": r["routes_hosted"], "safe": not why, "rejected_because": why,
                    "score": round(score, 1)})
    return sorted(out, key=lambda c: (not c["safe"], -c["score"]))


def pick_safe_target(source_id, prefer=None):
    cands = ranked_candidates(source_id)
    safe = [c for c in cands if c["safe"]]
    if prefer is not None:
        for c in safe:
            if c["server_id"] == prefer:
                return c
    return safe[0] if safe else None


# ---------------------------------------------------------------- proposal
def propose(incident_id):
    """For an anomaly incident (severity >= FAILOVER_MIN_SEVERITY), queue one pending 'migrate_traffic' action per route currently served by the
    failing server (only if a safe target exists). Idempotent. Returns the number of new proposals."""
    inc = fetch_one("SELECT incident_id, server_id, severity, status FROM incidents WHERE incident_id = %s",
                    (incident_id,))
    if (not inc or SEVERITY_RANK.get(inc["severity"], 0) < SEVERITY_RANK[min_severity()]
            or inc["status"] in ("resolved", "closed")):
        return 0
    routes = fetch_all("SELECT route_id, service_name FROM traffic_routes WHERE active_server_id = %s",
                       (inc["server_id"],))
    if not routes:
        return 0
    queued = 0
    with transaction() as cur:
        src = fetch_one("SELECT hostname FROM servers WHERE server_id = %s", (inc["server_id"],))
        for rt in routes:
            cur.execute("""SELECT 1 FROM incident_actions WHERE incident_id = %s AND route_id = %s
                           AND action_type = 'migrate_traffic' AND status IN ('pending', 'approved')""",
                        (incident_id, rt["route_id"]))
            if cur.fetchone():
                continue
            tgt = pick_safe_target(inc["server_id"])
            if not tgt:
                continue
            reason = (f"Shift service '{rt['service_name']}' from {src['hostname']} (anomaly detected, {inc['severity']}) to {tgt['hostname']} "
                      f"(CPU {tgt['cpu_percent']:.0f}%, RAM {tgt['memory_percent']:.0f}%, "
                      f"disk {tgt['disk_percent']:.0f}%, risk {tgt['risk']:.2f})")
            cur.execute("""INSERT INTO incident_actions (incident_id, action_type, target_name, reason, proposed_by,
                               risk_level, route_id, from_server_id, to_server_id)
                           VALUES (%s, 'migrate_traffic', %s, %s, 'failover', 'medium', %s, %s, %s)""",
                        (incident_id, tgt["hostname"], reason, rt["route_id"], inc["server_id"], tgt["server_id"]))
            queued += 1
        if queued:
            cur.execute("""UPDATE incidents SET status = 'pending_approval'
                           WHERE incident_id = %s AND status IN ('open', 'investigating')""", (incident_id,))
    return queued


# ---------------------------------------------------------------- execution (after human approval)
def execute_approved(action_id, decided_by):
    """Approve + execute atomically. Re-validates the target: if it is no longer safe, an alternative safe server
    is used; if none exists the action is rejected by the system and NoSafeTargetError is raised."""
    refusal = None
    with transaction() as cur:
        cur.execute("""SELECT * FROM incident_actions WHERE action_id = %s AND action_type = 'migrate_traffic'
                       AND status = 'pending' FOR UPDATE""", (action_id,))
        act = cur.fetchone()
        if not act:
            raise LookupError("action not found or already decided")
        cur.execute("SELECT * FROM traffic_routes WHERE route_id = %s FOR UPDATE", (act["route_id"],))
        route = cur.fetchone()
        if route is None or route["active_server_id"] != act["from_server_id"]:
            cur.execute("""UPDATE incident_actions SET status = 'failed', decided_by = %s, decided_at = now(),
                                  executed_at = now(), result_message = 'Route changed since proposal; nothing done'
                           WHERE action_id = %s""", (decided_by, action_id))
            conflict = True
        else:
            conflict = False
            tgt = pick_safe_target(act["from_server_id"], prefer=act["to_server_id"])
            if tgt is None:
                refusal = "No safe target server available at approval time"
                cur.execute("""UPDATE incident_actions SET status = 'rejected', decided_by = 'system:safety',
                                      decided_at = now(), result_message = %s WHERE action_id = %s""",
                            (refusal, action_id))
            else:
                names = fetch_one("SELECT (SELECT hostname FROM servers WHERE server_id = %s) AS f", (act["from_server_id"],))
                msg = (f"Traffic for '{route['service_name']}' shifted {names['f']} -> {tgt['hostname']}"
                       + ("" if tgt["server_id"] == act["to_server_id"]
                          else " (original target was no longer safe; re-selected)"))
                cur.execute("""UPDATE traffic_routes SET active_server_id = %s, updated_at = now(), updated_by = %s
                               WHERE route_id = %s""", (tgt["server_id"], decided_by, route["route_id"]))
                cur.execute("""INSERT INTO route_events (route_id, from_server_id, to_server_id, action_id, kind,
                                   performed_by, reason) VALUES (%s, %s, %s, %s, 'failover', %s, %s)""",
                            (route["route_id"], act["from_server_id"], tgt["server_id"], action_id, decided_by,
                             f"incident #{act['incident_id']} anomaly"))
                cur.execute("""UPDATE incident_actions SET status = 'executed', decided_by = %s, decided_at = now(),
                                      executed_at = now(), to_server_id = %s, target_name = %s, result_message = %s
                               WHERE action_id = %s""",
                            (decided_by, tgt["server_id"], tgt["hostname"], msg, action_id))
        cur.execute("SELECT 1 FROM incident_actions WHERE incident_id = %s AND status = 'pending'", (act["incident_id"],))
        if cur.fetchone() is None:
            cur.execute("""UPDATE incidents SET status = 'investigating'
                           WHERE incident_id = %s AND status = 'pending_approval'""", (act["incident_id"],))
    if conflict:
        raise RouteConflictError("route changed since this action was proposed")
    if refusal:
        raise NoSafeTargetError(refusal)
    _notify(action_id, route["service_name"], names["f"], tgt["hostname"])
    return act["incident_id"]


def _notify(action_id, service, src, dst):
    """Optional: tell your load balancer. Best-effort; routing is already recorded in the database."""
    url = os.getenv("FAILOVER_WEBHOOK_URL")
    if not url:
        return
    note = "webhook ok"
    try:
        r = requests.post(url, json={"event": "failover", "service": service, "from": src, "to": dst,
                                     "action_id": action_id}, timeout=3)
        r.raise_for_status()
    except Exception as exc:
        note = f"webhook failed: {type(exc).__name__}"
        log.warning("failover webhook failed: %s", exc)
    from database.db import execute
    execute("UPDATE incident_actions SET result_message = result_message || %s WHERE action_id = %s",
            (f" | {note}", action_id))


def restore_primary(route_id, performed_by, force=False):
    """Operator rolls a service back to its primary server (refused while the primary has an active incident)."""
    with transaction() as cur:
        cur.execute("SELECT * FROM traffic_routes WHERE route_id = %s FOR UPDATE", (route_id,))
        r = cur.fetchone()
        if not r:
            raise LookupError("route not found")
        if r["active_server_id"] == r["primary_server_id"]:
            raise RouteConflictError("service is already on its primary server")
        cur.execute("""SELECT 1 FROM incidents WHERE server_id = %s AND status NOT IN ('resolved', 'closed')""",
                    (r["primary_server_id"],))
        if cur.fetchone() and not force:
            raise NoSafeTargetError("primary server still has an active incident (use force to override)")
        cur.execute("""UPDATE traffic_routes SET active_server_id = primary_server_id, updated_at = now(),
                              updated_by = %s WHERE route_id = %s""", (performed_by, route_id))
        cur.execute("""INSERT INTO route_events (route_id, from_server_id, to_server_id, kind, performed_by, reason)
                       VALUES (%s, %s, %s, 'restore', %s, %s)""",
                    (route_id, r["active_server_id"], r["primary_server_id"], performed_by,
                     "forced" if force else "primary recovered"))
    return list_routes()
