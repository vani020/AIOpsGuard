"""Data-access layer: every SQL statement for telemetry lives here."""
from psycopg2.extras import Json, execute_values

from database.db import fetch_all, fetch_one, execute, transaction


def store_telemetry(hostname, ip_address, os_name, role, metrics, processes, logs):
    """Atomically store server + metric + processes + logs. Returns (server_id, metric_id)."""
    with transaction() as cur:
        cur.execute(
            """INSERT INTO servers (hostname, ip_address, os_name, role)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (hostname) DO UPDATE
                 SET ip_address = EXCLUDED.ip_address, os_name = EXCLUDED.os_name, role = EXCLUDED.role
               RETURNING server_id""",
            (hostname, ip_address, os_name, role),
        )
        server_id = cur.fetchone()["server_id"]
        cur.execute(
            """INSERT INTO metrics (server_id, cpu_percent, memory_percent, disk_percent, process_count, load_avg)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING metric_id""",
            (server_id, metrics["cpu_percent"], metrics["memory_percent"], metrics.get("disk_percent", 0),
             metrics.get("process_count", 0), metrics.get("load_avg", 0)),
        )
        metric_id = cur.fetchone()["metric_id"]
        if processes:
            execute_values(
                cur,
                "INSERT INTO processes (metric_id, pid, name, cpu_percent, memory_percent, status) VALUES %s",
                [(metric_id, p["pid"], p["name"][:255], p.get("cpu_percent", 0), p.get("memory_percent", 0),
                  p.get("status")) for p in processes],
            )
        if logs:
            execute_values(
                cur,
                "INSERT INTO logs (server_id, ts, level, source, message) VALUES %s",
                [(server_id, l.get("ts"), l.get("level", "INFO"), l.get("source"), l["message"]) for l in logs],
                template="(%s, COALESCE(%s, now()), %s, %s, %s)",
            )
        return server_id, metric_id


def recent_metrics(server_id, limit=200):
    """Newest `limit` metric rows, returned oldest -> newest."""
    return fetch_all(
        """SELECT * FROM (SELECT * FROM metrics WHERE server_id = %s
                          ORDER BY ts DESC, metric_id DESC LIMIT %s) t
           ORDER BY ts, metric_id""",
        (server_id, limit),
    )


def processes_for_metric(metric_id, top=10):
    return fetch_all(
        """SELECT pid, name, cpu_percent, memory_percent, status FROM processes
           WHERE metric_id = %s ORDER BY cpu_percent DESC, memory_percent DESC LIMIT %s""",
        (metric_id, top),
    )


def latest_processes(server_id, top=10):
    row = fetch_one("SELECT metric_id FROM metrics WHERE server_id = %s ORDER BY ts DESC, metric_id DESC LIMIT 1",
                    (server_id,))
    return processes_for_metric(row["metric_id"], top) if row else []


def recent_logs(server_id, limit=50, min_level=None):
    sql = "SELECT log_id, ts, level, source, message FROM logs WHERE server_id = %s"
    if min_level == "ERROR":
        sql += " AND level IN ('ERROR', 'CRITICAL')"
    elif min_level == "WARNING":
        sql += " AND level IN ('WARNING', 'ERROR', 'CRITICAL')"
    sql += " ORDER BY ts DESC, log_id DESC LIMIT %s"
    return fetch_all(sql, (server_id, limit))


def insert_prediction(server_id, metric_id, model_name, anomaly_score, is_anomaly, failure_risk, reasons):
    row = fetch_one(
        """INSERT INTO predictions (server_id, metric_id, model_name, anomaly_score, is_anomaly, failure_risk, reasons)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING prediction_id""",
        (server_id, metric_id, model_name, anomaly_score, is_anomaly, min(max(failure_risk, 0), 1), Json(reasons)),
    )
    return row["prediction_id"]


def attach_prediction(prediction_id, incident_id):
    execute("UPDATE predictions SET incident_id = %s WHERE prediction_id = %s", (incident_id, prediction_id))


def recent_predictions(server_id, limit=10):
    return fetch_all(
        "SELECT * FROM predictions WHERE server_id = %s ORDER BY created_at DESC, prediction_id DESC LIMIT %s",
        (server_id, limit),
    )


def list_servers():
    return fetch_all(
        """SELECT v.*, CASE WHEN v.last_seen < now() - interval '30 seconds' THEN 'offline' ELSE v.status END
                       AS display_status
           FROM v_server_overview v WHERE v.last_seen IS NOT NULL ORDER BY v.hostname"""
    )


def get_server(server_id):
    return fetch_one("SELECT * FROM servers WHERE server_id = %s", (server_id,))


def set_server_status(server_id, status):
    execute("UPDATE servers SET status = %s WHERE server_id = %s", (status, server_id))
