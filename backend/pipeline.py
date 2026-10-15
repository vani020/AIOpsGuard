
from database import repository as repo
from incidents import manager
from ml import detector, log_analyzer

LEVEL_MAP = {"WARN": "WARNING", "FATAL": "CRITICAL", "ERR": "ERROR", "TRACE": "DEBUG"}
VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _norm_level(level):
    level = str(level or "INFO").upper()
    level = LEVEL_MAP.get(level, level)
    return level if level in VALID_LEVELS else "INFO"


def process_telemetry(payload):
    logs = [{**l, "level": _norm_level(l.get("level"))} for l in payload.get("logs", [])]
    server_id, metric_id = repo.store_telemetry(
        payload["hostname"], payload.get("ip_address"), payload.get("os_name"), payload.get("role", "worker"),
        payload["metrics"], payload.get("processes", []), logs)

    history = repo.recent_metrics(server_id, 200)
    current, prior = history[-1], history[:-1]
    det = detector.detect(server_id, prior, current)
    log_res = log_analyzer.analyze_logs(logs)

    reasons = list(det.reasons) + [f"Log analysis: {r}" for r in log_res["reasons"]] \
        if log_analyzer.is_log_anomaly(log_res) else list(det.reasons)
    is_anomaly = det.is_anomaly or log_analyzer.is_log_anomaly(log_res)
    risk = max(det.failure_risk, 0.6 * log_res["score"] if log_analyzer.is_log_anomaly(log_res) else 0.0)
    if is_anomaly:
        risk = max(risk, 0.4)

    pred_id = repo.insert_prediction(server_id, metric_id, det.model_name, det.anomaly_score, is_anomaly, risk, reasons)

    incident, created = None, False
    if is_anomaly:
        incident, created = manager.open_or_update(server_id, metric_id, risk, reasons)
        repo.attach_prediction(pred_id, incident["incident_id"])
        repo.set_server_status(server_id, "critical" if risk >= 0.65 else "warning")
    else:
        manager.auto_resolve_if_recovered(server_id)
        still_open = manager.get_active_incident(server_id) is not None
        repo.set_server_status(server_id, "warning" if (risk >= 0.5 or still_open) else "healthy")

    return {"server_id": server_id, "metric_id": metric_id, "prediction_id": pred_id, "is_anomaly": is_anomaly,
            "failure_risk": round(risk, 3), "reasons": reasons, "incident_id": incident["incident_id"] if incident else None,
            "incident_created": created}
