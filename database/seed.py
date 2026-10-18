"""Optional: seed resolved historical incidents so the RCA engine has history to retrieve.

    python -m database.seed
"""
from database.db import transaction
from database.init_db import init_db

HISTORY = [
    ("cpu_saturation", "Runaway batch job saturated CPU",
     "A batch process spun in a tight loop and held all cores.",
     "Terminate the runaway process, add CPU limits and schedule batch work off-peak."),
    ("memory_pressure", "Memory leak in worker process",
     "A worker process leaked memory until the host ran out of free RAM.",
     "Restart the worker, enable periodic recycling, and profile allocations before the next release."),
    ("application_errors", "Burst of database connection errors",
     "Connection pool exhaustion caused repeated timeouts in the API layer.",
     "Increase pool size, add retry with backoff, and alert on pool utilisation above 80%."),
]

def seed():
    init_db()
    with transaction() as cur:
        cur.execute("""INSERT INTO servers (hostname, role, os_name) VALUES ('seed-history', 'other', 'n/a')
                       ON CONFLICT (hostname) DO UPDATE SET role = EXCLUDED.role RETURNING server_id""")
        sid = cur.fetchone()["server_id"]
        for cat, title, cause, rec in HISTORY:
            cur.execute(
                """INSERT INTO incidents (server_id, title, severity, status, category, root_cause, explanation,
                                          recommendation, rca_source, detected_at)
                   VALUES (%s, %s, 'high', 'resolved', %s, %s, %s, %s, 'rules', now() - interval '3 days')""",
                (sid, title, cat, cause, cause, rec),
            )
    print(f"Seeded {len(HISTORY)} historical incidents.")


if __name__ == "__main__":
    seed()
