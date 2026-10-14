"""Anomaly detection + failure-risk prediction.

Hybrid approach (explainable by design):
  * hard safety thresholds (CPU / memory / disk)           -> always flagged
  * per-server Isolation Forest, retrained periodically    -> flagged only if ALSO a feature is >=3 sigma
    above that server's own normal behaviour (cuts false positives)
  * cold start (< MIN_TRAIN samples) falls back to thresholds only
Failure risk (0..1) projects CPU/memory 10 samples ahead using a linear trend.
"""
import os
import threading
from dataclasses import dataclass, field

import numpy as np
from sklearn.ensemble import IsolationForest

FEATURES = ["cpu_percent", "memory_percent", "disk_percent", "process_count", "load_avg"]
MIN_TRAIN = 40
RETRAIN_EVERY = 30
Z_THRESHOLD = 3.0
CPU_HARD = float(os.getenv("CPU_HARD_THRESHOLD", 90))
MEM_HARD = float(os.getenv("MEM_HARD_THRESHOLD", 85))
DISK_HARD = 95.0

_models = {}
_lock = threading.Lock()


@dataclass
class DetectionResult:
    is_anomaly: bool
    anomaly_score: float
    failure_risk: float
    model_name: str
    reasons: list = field(default_factory=list)


def reset():
    with _lock:
        _models.clear()


def _vec(m):
    return [float(m.get(f) or 0.0) for f in FEATURES]


def _train(history):
    rows = [_vec(m) for m in history
            if float(m["cpu_percent"]) < CPU_HARD and float(m["memory_percent"]) < MEM_HARD]
    if len(rows) < MIN_TRAIN:
        return None
    X = np.array(rows)
    mean, std = X.mean(axis=0), X.std(axis=0)
    std = np.maximum(std, np.maximum(0.1 * np.abs(mean), 1.0))  # floor avoids absurd z-scores
    model = IsolationForest(n_estimators=100, contamination=0.02, random_state=42).fit(X)
    return {"model": model, "mean": mean, "std": std, "since_train": 0}


def _norm(v):
    return min(max((v - 50.0) / 50.0, 0.0), 1.0)


def _slope(values):
    if len(values) < 3:
        return 0.0
    return float(np.polyfit(np.arange(len(values)), np.array(values, dtype=float), 1)[0])


def detect(server_id, history, current):
    """history: previous metric dicts oldest->newest; current: the newest metric dict."""
    cpu, mem, disk = float(current["cpu_percent"]), float(current["memory_percent"]), float(current.get("disk_percent") or 0)
    reasons = []

    if cpu >= CPU_HARD:
        reasons.append(f"CPU at {cpu:.0f}% (threshold {CPU_HARD:.0f}%)")
    if mem >= MEM_HARD:
        reasons.append(f"Memory at {mem:.0f}% (threshold {MEM_HARD:.0f}%)")
    if disk >= DISK_HARD:
        reasons.append(f"Disk at {disk:.0f}% (threshold {DISK_HARD:.0f}%)")
    hard = bool(reasons)

    with _lock:
        entry = _models.get(server_id)
        if entry is None or entry["since_train"] >= RETRAIN_EVERY:
            trained = _train(history)
            if trained:
                _models[server_id] = entry = trained
        if entry:
            entry["since_train"] += 1

    stat_anomaly = False
    if entry:
        x = np.array([_vec(current)])
        anomaly_score = float(-entry["model"].score_samples(x)[0])
        iso_flag = bool(entry["model"].decision_function(x)[0] < 0)
        z = (x[0] - entry["mean"]) / entry["std"]
        z_reasons = [f"{FEATURES[i]} is {z[i]:.1f} sigma above this server's normal"
                     for i in range(len(FEATURES)) if z[i] >= Z_THRESHOLD]
        stat_anomaly = iso_flag and bool(z_reasons)
        if stat_anomaly:
            reasons += z_reasons
        model_name = "isolation_forest+thresholds"
    else:
        anomaly_score = max(cpu, mem, disk) / 100.0
        model_name = "thresholds"

    is_anomaly = hard or stat_anomaly

    tail = history[-9:] + [current]
    cpu_p = min(100.0, cpu + max(_slope([float(m["cpu_percent"]) for m in tail]), 0) * 10)
    mem_p = min(100.0, mem + max(_slope([float(m["memory_percent"]) for m in tail]), 0) * 10)
    if mem_p - mem >= 5 and not hard:
        reasons.append(f"Memory trending up: projected {mem_p:.0f}% within 10 samples")
    if cpu_p - cpu >= 10 and not hard:
        reasons.append(f"CPU trending up: projected {cpu_p:.0f}% within 10 samples")
    ns = [_norm(cpu_p), _norm(mem_p), _norm(disk)]
    risk = 0.7 * max(ns) + 0.3 * (sum(ns) / 3) + (0.15 if is_anomaly else 0.0)

    return DetectionResult(is_anomaly, round(anomaly_score, 4), round(min(risk, 1.0), 4), model_name, reasons)
