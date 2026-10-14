import random

from ml import detector


def metric(cpu=20, mem=45, disk=40, procs=120, load=0.8):
    return {"cpu_percent": cpu, "memory_percent": mem, "disk_percent": disk, "process_count": procs, "load_avg": load}


def normal_history(n=60, seed=1):
    r = random.Random(seed)
    return [metric(r.gauss(20, 3), r.gauss(45, 2), 40, 120 + r.randint(-2, 2), r.gauss(0.8, 0.1)) for _ in range(n)]


def test_normal_sample_is_not_anomaly():
    detector.reset()
    res = detector.detect(1, normal_history(), metric())
    assert not res.is_anomaly
    assert res.failure_risk < 0.5


def test_cpu_spike_flagged_with_reason():
    detector.reset()
    res = detector.detect(1, normal_history(), metric(cpu=97))
    assert res.is_anomaly and res.failure_risk >= 0.6
    assert any("CPU" in r for r in res.reasons)


def test_memory_hard_threshold_flagged():
    detector.reset()
    res = detector.detect(1, normal_history(), metric(mem=92))
    assert res.is_anomaly


def test_cold_start_uses_thresholds_only():
    detector.reset()
    res = detector.detect(2, normal_history(5), metric(cpu=95))
    assert res.is_anomaly and res.model_name == "thresholds"


def test_statistical_outlier_below_hard_threshold_is_caught():
    detector.reset()
    res = detector.detect(3, normal_history(80), metric(cpu=70, procs=400, load=6))
    assert res.is_anomaly and res.model_name.startswith("isolation_forest")


def test_rising_memory_raises_risk():
    detector.reset()
    hist = [metric(mem=45 + i * 2) for i in range(10)]
    rising = detector.detect(4, hist, metric(mem=65))
    flat = detector.detect(5, [metric(mem=65)] * 10, metric(mem=65))
    assert rising.failure_risk > flat.failure_risk
