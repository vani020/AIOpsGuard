-- AIOpsGuard relational schema (PostgreSQL 13+). Idempotent: safe to re-run.
-- Normalised to 3NF: processes reference metrics (not servers) so server_id is not duplicated.

CREATE TABLE IF NOT EXISTS servers (
    server_id   SERIAL PRIMARY KEY,
    hostname    VARCHAR(100) NOT NULL UNIQUE,
    ip_address  VARCHAR(45),
    os_name     VARCHAR(60),
    role        VARCHAR(20) NOT NULL DEFAULT 'worker'
                CHECK (role IN ('api', 'database', 'worker', 'other')),
    status      VARCHAR(15) NOT NULL DEFAULT 'healthy'
                CHECK (status IN ('healthy', 'warning', 'critical', 'offline')),
    last_seen   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS metrics (
    metric_id      BIGSERIAL PRIMARY KEY,
    server_id      INT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    cpu_percent    REAL NOT NULL CHECK (cpu_percent    BETWEEN 0 AND 100),
    memory_percent REAL NOT NULL CHECK (memory_percent BETWEEN 0 AND 100),
    disk_percent   REAL NOT NULL DEFAULT 0 CHECK (disk_percent BETWEEN 0 AND 100),
    process_count  INT  NOT NULL DEFAULT 0 CHECK (process_count >= 0),
    load_avg       REAL NOT NULL DEFAULT 0 CHECK (load_avg >= 0)
);
CREATE INDEX IF NOT EXISTS idx_metrics_server_ts ON metrics (server_id, ts DESC);

CREATE TABLE IF NOT EXISTS processes (
    process_id     BIGSERIAL PRIMARY KEY,
    metric_id      BIGINT NOT NULL REFERENCES metrics(metric_id) ON DELETE CASCADE,
    pid            INT NOT NULL,
    name           VARCHAR(255) NOT NULL,
    cpu_percent    REAL NOT NULL DEFAULT 0 CHECK (cpu_percent >= 0),
    memory_percent REAL NOT NULL DEFAULT 0 CHECK (memory_percent >= 0),
    status         VARCHAR(20)
);
CREATE INDEX IF NOT EXISTS idx_processes_metric ON processes (metric_id);

CREATE TABLE IF NOT EXISTS logs (
    log_id     BIGSERIAL PRIMARY KEY,
    server_id  INT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    level      VARCHAR(10) NOT NULL DEFAULT 'INFO'
               CHECK (level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')),
    source     VARCHAR(100),
    message    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_server_ts ON logs (server_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_logs_errors ON logs (server_id, ts DESC) WHERE level IN ('ERROR', 'CRITICAL');

CREATE TABLE IF NOT EXISTS incidents (
    incident_id       SERIAL PRIMARY KEY,
    server_id         INT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
    trigger_metric_id BIGINT REFERENCES metrics(metric_id) ON DELETE SET NULL,
    title             VARCHAR(200) NOT NULL,
    severity          VARCHAR(10) NOT NULL DEFAULT 'medium'
                      CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status            VARCHAR(20) NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'investigating', 'pending_approval', 'resolved', 'closed')),
    category          VARCHAR(40),
    root_cause        TEXT,
    explanation       TEXT,
    recommendation    TEXT,
    rca_source        VARCHAR(10) CHECK (rca_source IN ('llm', 'rules')),
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at       TIMESTAMPTZ,
    CHECK (resolved_at IS NULL OR status IN ('resolved', 'closed'))
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents (status, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_category ON incidents (category) WHERE status IN ('resolved', 'closed');
-- At most ONE active incident per server (also protects against race conditions).
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_incident_per_server
    ON incidents (server_id) WHERE status NOT IN ('resolved', 'closed');

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id BIGSERIAL PRIMARY KEY,
    server_id     INT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
    metric_id     BIGINT REFERENCES metrics(metric_id) ON DELETE CASCADE,
    incident_id   INT REFERENCES incidents(incident_id) ON DELETE SET NULL,
    model_name    VARCHAR(50) NOT NULL,
    anomaly_score REAL NOT NULL DEFAULT 0,
    is_anomaly    BOOLEAN NOT NULL DEFAULT FALSE,
    failure_risk  REAL NOT NULL DEFAULT 0 CHECK (failure_risk BETWEEN 0 AND 1),
    reasons       JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_predictions_server ON predictions (server_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_incident ON predictions (incident_id);

-- Human-in-the-loop remediation + audit trail
CREATE TABLE IF NOT EXISTS incident_actions (
    action_id      SERIAL PRIMARY KEY,
    incident_id    INT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    action_type    VARCHAR(30) NOT NULL DEFAULT 'kill_process' CHECK (action_type IN ('kill_process')),
    target_pid     INT NOT NULL,
    target_name    VARCHAR(255) NOT NULL,
    reason         TEXT,
    status         VARCHAR(10) NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'rejected', 'executed', 'failed')),
    requested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by     VARCHAR(100),
    decided_at     TIMESTAMPTZ,
    executed_at    TIMESTAMPTZ,
    result_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_actions_incident ON incident_actions (incident_id, status);

-- ---------- Triggers ----------
CREATE OR REPLACE FUNCTION fn_touch_server_last_seen() RETURNS trigger AS $$
BEGIN
    UPDATE servers SET last_seen = NEW.ts WHERE server_id = NEW.server_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_metrics_last_seen ON metrics;
CREATE TRIGGER trg_metrics_last_seen AFTER INSERT ON metrics
    FOR EACH ROW EXECUTE FUNCTION fn_touch_server_last_seen();

CREATE OR REPLACE FUNCTION fn_incident_lifecycle() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    IF NEW.status IN ('resolved', 'closed') THEN
        IF NEW.resolved_at IS NULL THEN NEW.resolved_at := now(); END IF;
    ELSE
        NEW.resolved_at := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_incident_lifecycle ON incidents;
CREATE TRIGGER trg_incident_lifecycle BEFORE INSERT OR UPDATE ON incidents
    FOR EACH ROW EXECUTE FUNCTION fn_incident_lifecycle();

-- ---------- Views ----------
CREATE OR REPLACE VIEW v_server_overview AS
SELECT s.server_id, s.hostname, s.ip_address, s.os_name, s.role, s.status, s.last_seen,
       m.metric_id, m.cpu_percent, m.memory_percent, m.disk_percent, m.process_count, m.load_avg,
       p.failure_risk, p.is_anomaly,
       (SELECT count(*) FROM incidents i
         WHERE i.server_id = s.server_id AND i.status NOT IN ('resolved', 'closed')) AS open_incidents
FROM servers s
LEFT JOIN LATERAL (SELECT * FROM metrics WHERE server_id = s.server_id
                   ORDER BY ts DESC, metric_id DESC LIMIT 1) m ON TRUE
LEFT JOIN LATERAL (SELECT * FROM predictions WHERE server_id = s.server_id
                   ORDER BY created_at DESC, prediction_id DESC LIMIT 1) p ON TRUE;

CREATE OR REPLACE VIEW v_incident_summary AS
SELECT i.*, s.hostname,
       (SELECT count(*) FROM incident_actions a
         WHERE a.incident_id = i.incident_id AND a.status = 'pending') AS pending_actions
FROM incidents i JOIN servers s USING (server_id);

-- ---------- Maintenance ----------
CREATE OR REPLACE FUNCTION purge_old_telemetry(keep_days INT DEFAULT 7) RETURNS INT AS $$
DECLARE removed INT;
BEGIN
    DELETE FROM metrics WHERE ts < now() - make_interval(days => keep_days)
      AND metric_id NOT IN (SELECT trigger_metric_id FROM incidents WHERE trigger_metric_id IS NOT NULL);
    GET DIAGNOSTICS removed = ROW_COUNT;
    DELETE FROM logs WHERE ts < now() - make_interval(days => keep_days);
    RETURN removed;
END;
$$ LANGUAGE plpgsql;
