from incidents import rca


def evidence(cpu=95, mem=40, procs=None, logs=None):
    return {"incident": {"id": 1, "hostname": "h", "severity": "high", "title": "t"},
            "reasons": ["CPU at 95% (threshold 90%)"],
            "metrics": [{"cpu_percent": cpu, "memory_percent": mem, "disk_percent": 30, "process_count": 100, "load_avg": 1}],
            "processes": procs or [], "logs": logs or []}
def test_cpu_culprit_gets_suggested_action():
    ev = evidence(procs=[{"pid": 42, "name": "stress-cpu", "cpu_percent": 80.0, "memory_percent": 1.0, "status": "running"}])
    out = rca.fallback_analysis(ev, [])
    assert out["category"] == "cpu_saturation"
    assert "stress-cpu" in out["root_cause"]
    assert out["suggested_actions"][0]["pid"] == 42
def test_no_dominant_process_means_no_action():
    ev = evidence(procs=[{"pid": 1, "name": "a", "cpu_percent": 5.0, "memory_percent": 1.0, "status": "running"}])
    assert rca.fallback_analysis(ev, [])["suggested_actions"] == []


def test_similar_incident_is_referenced():
    sim = [{"incident_id": 7, "title": "old", "recommendation": "restart the worker"}]
    assert "#7" in rca.fallback_analysis(evidence(), sim)["explanation"]


def test_llm_actions_are_sanitised():
    ev = evidence(procs=[{"pid": 5, "name": "postgres", "cpu_percent": 90.0, "memory_percent": 1.0, "status": "r"},
                         {"pid": 6, "name": "worker", "cpu_percent": 50.0, "memory_percent": 1.0, "status": "r"}])
    safe = rca._safe_actions([{"pid": 5}, {"pid": 6, "reason": "x"}, {"pid": 999}, {"pid": "bad"}], ev)
    assert [a["pid"] for a in safe] == [6]
