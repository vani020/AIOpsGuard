-- Analysis queries for the report / viva.  psql -d aiopsguard -f database/queries.sql

-- 1. Current health of every server (view)
SELECT hostname, role, status, cpu_percent, memory_percent, failure_risk, open_incidents FROM v_server_overview;

-- 2. Average / peak CPU & memory per server in the last hour (join + aggregation)
SELECT s.hostname, round(avg(m.cpu_percent)::numeric, 1) AS avg_cpu, max(m.cpu_percent) AS peak_cpu,
       round(avg(m.memory_percent)::numeric, 1) AS avg_mem, max(m.memory_percent) AS peak_mem
FROM metrics m JOIN servers s USING (server_id)
WHERE m.ts > now() - interval '1 hour' GROUP BY s.hostname;

-- 3. Top 5 CPU-hungry processes at the moment each incident was triggered
SELECT i.incident_id, s.hostname, p.name, p.pid, p.cpu_percent
FROM incidents i
JOIN servers s USING (server_id)
JOIN LATERAL (SELECT * FROM processes WHERE metric_id = i.trigger_metric_id
              ORDER BY cpu_percent DESC LIMIT 5) p ON TRUE
ORDER BY i.incident_id, p.cpu_percent DESC;

-- 4. Mean time to resolve by category
SELECT category, count(*) AS incidents, avg(resolved_at - detected_at) AS mttr
FROM incidents WHERE resolved_at IS NOT NULL GROUP BY category ORDER BY incidents DESC;

-- 5. Servers with more than 3 anomalies in the last 15 minutes
SELECT s.hostname, count(*) AS anomalies
FROM predictions p JOIN servers s USING (server_id)
WHERE p.is_anomaly AND p.created_at > now() - interval '15 minutes'
GROUP BY s.hostname HAVING count(*) > 3;

-- 6. Pending approvals (human-in-the-loop queue)
SELECT a.action_id, s.hostname, a.action_type, a.target_name, a.target_pid, i.severity
FROM incident_actions a JOIN incidents i USING (incident_id) JOIN servers s USING (server_id)
WHERE a.status = 'pending' ORDER BY i.severity, a.requested_at;

-- 7. Error-log frequency per server (uses the partial index)
SELECT s.hostname, count(*) AS errors FROM logs l JOIN servers s USING (server_id)
WHERE l.level IN ('ERROR', 'CRITICAL') AND l.ts > now() - interval '1 hour' GROUP BY s.hostname;

-- 8. Housekeeping: delete telemetry older than 7 days (keeps incident trigger metrics)
SELECT purge_old_telemetry(7);
