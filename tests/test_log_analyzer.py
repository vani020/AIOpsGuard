from ml.log_analyzer import analyze_logs, is_log_anomaly
def test_info_logs_are_quiet():
    res = analyze_logs([{"level": "INFO", "message": "request ok"}] * 5)
    assert res["score"] == 0 and not is_log_anomaly(res)

def test_oom_critical_is_anomalous():
    res = analyze_logs([{"level": "CRITICAL", "message": "worker out of memory"}])
    assert is_log_anomaly(res)
    assert any("memory exhaustion" in r for r in res["reasons"])


def test_error_burst_is_anomalous():
    res = analyze_logs([{"level": "ERROR", "message": "boom"}] * 3)
    assert is_log_anomaly(res)
