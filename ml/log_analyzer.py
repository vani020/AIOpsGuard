"""Lightweight log anomaly scoring (error volume + known failure signatures)."""
import re
ERROR_LEVELS = {"ERROR", "CRITICAL"}
SIGNATURES = {
    r"out of memory|\boom\b|cannot allocate|memoryerror": "memory exhaustion",
    r"deadlock": "deadlock",
    r"timeout|timed out": "timeouts",
    r"connection (refused|reset|lost)|connection pool": "connection failures",
    r"segfault|segmentation fault|core dumped": "process crash",
    r"no space left|disk full": "disk full",
    r"too many open files": "file descriptor exhaustion",
}
LOG_ANOMALY_THRESHOLD = 0.5

def analyze_logs(logs):
    """logs: list of dicts with 'level' and 'message'. Returns {score 0..1, reasons, error_count}."""
    errors = [l for l in logs if str(l.get("level", "")).upper() in ERROR_LEVELS]
    found = []
    for pattern, label in SIGNATURES.items():
        if any(re.search(pattern, str(l.get("message", "")), re.I) for l in errors):
            found.append(label)
    score = min(1.0, 0.2 * len(errors) + 0.35 * len(found))
    reasons = []
    if errors:
        reasons.append(f"{len(errors)} error-level log line(s) in the latest batch")
    reasons += [f"log signature: {label}" for label in found]
    return {"score": round(score, 3), "reasons": reasons, "error_count": len(errors)}


def is_log_anomaly(result):
    return result["score"] >= LOG_ANOMALY_THRESHOLD
