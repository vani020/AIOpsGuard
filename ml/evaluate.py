"""Validation of the anomaly detector / failure-risk predictor.

    python -m ml.evaluate                 # synthetic labelled benchmark (no database needed)
    python -m ml.evaluate --from-db       # back-test risk scores against real recorded incidents
    python -m ml.evaluate --json          # machine-readable output

SYNTHETIC BENCHMARK: each scenario = 60 samples of normal behaviour followed by an injected fault with known
ground truth. We report
  * detection rate      - was the fault flagged at all?
  * detection delay     - samples between fault start and first flag
  * early-warning lead  - for gradual faults (leak / ramp / disk fill): samples between the first
                          failure_risk >= RISK_WARN and the first hard-threshold breach (positive = early warning)
  * false-positive rate - flagged samples / total samples on pure-noise runs
This proves the logic behaves as designed on controlled data. It does NOT replace validation on real production
history - use --from-db once you have accumulated incidents.
"""
import argparse
import json
import random
from statistics import mean

from ml import detector

RISK_WARN = 0.5
NORMAL_N = 60
HARD = {"cpu_percent": detector.CPU_HARD, "memory_percent": detector.MEM_HARD, "disk_percent": detector.DISK_HARD}


def _m(r, cpu=20.0, mem=45.0, disk=40.0, net=300.0, dio=500.0, swap=1.0):
    return {"cpu_percent": min(max(r.gauss(cpu, 3), 0), 100), "memory_percent": min(max(r.gauss(mem, 2), 0), 100),
            "disk_percent": disk, "process_count": 120 + r.randint(-2, 2), "load_avg": max(r.gauss(0.8, 0.1), 0),
            "net_sent_kbps": max(r.gauss(net, 60), 0), "net_recv_kbps": max(r.gauss(net, 60), 0),
            "disk_read_kbps": max(r.gauss(dio, 80), 0), "disk_write_kbps": max(r.gauss(dio, 80), 0),
            "swap_percent": swap}


def _scenario(name, seed):
    """Return (samples, fault_start_index, gradual: bool)."""
    r = random.Random(seed)
    s = [_m(r) for _ in range(NORMAL_N)]
    if name == "cpu_spike":
        s += [_m(r, cpu=96) for _ in range(8)]
        return s, NORMAL_N, False
    if name == "cpu_ramp":
        s += [_m(r, cpu=min(20 + 4.5 * (i + 1), 99)) for i in range(20)]
        return s, NORMAL_N, True
    if name == "memory_leak":
        s += [_m(r, mem=min(45 + 2.2 * (i + 1), 97)) for i in range(25)]
        return s, NORMAL_N, True
    if name == "disk_fill":
        s += [_m(r, disk=min(60 + 2.0 * (i + 1), 99)) for i in range(25)]
        return s, NORMAL_N, True
    if name == "network_spike":
        s += [_m(r, net=40000) for _ in range(6)]
        return s, NORMAL_N, False
    if name == "disk_io_storm":
        s += [_m(r, dio=60000) for _ in range(6)]
        return s, NORMAL_N, False
    if name == "swap_thrash":
        s += [_m(r, mem=88, swap=92) for _ in range(6)]
        return s, NORMAL_N, False
    raise ValueError(name)


def _run(samples, sid):
    detector.reset()
    hist, out = [], []
    for cur in samples:
        res = detector.detect(sid, hist[-200:], cur)
        out.append(res)
        hist.append(cur)
    return out


def _hard_breach(m):
    return any(float(m[k]) >= v for k, v in HARD.items())


SCENARIOS = ["cpu_spike", "cpu_ramp", "memory_leak", "disk_fill", "network_spike", "disk_io_storm", "swap_thrash"]


def synthetic(seeds=5, noise_runs=10, noise_len=200):
    rows = []
    for name in SCENARIOS:
        detected, delays, leads = 0, [], []
        for seed in range(seeds):
            samples, start, gradual = _scenario(name, 100 + seed)
            res = _run(samples, 1000 + seed)
            flagged = [i for i in range(start, len(samples)) if res[i].is_anomaly]
            if flagged:
                detected += 1
                delays.append(flagged[0] - start)
            if gradual:
                breach = next((i for i in range(start, len(samples)) if _hard_breach(samples[i])), None)
                warn = next((i for i in range(start, len(samples)) if res[i].failure_risk >= RISK_WARN), None)
                if breach is not None and warn is not None:
                    leads.append(breach - warn)
        rows.append({"scenario": name, "detection_rate": round(detected / seeds, 2),
                     "avg_delay_samples": round(mean(delays), 1) if delays else None,
                     "avg_early_warning_samples": round(mean(leads), 1) if leads else None})
    fp = total = 0
    for k in range(noise_runs):
        r = random.Random(500 + k)
        samples = [_m(r) for _ in range(noise_len)]
        res = _run(samples, 2000 + k)
        fp += sum(1 for x in res[NORMAL_N:] if x.is_anomaly)
        total += len(res[NORMAL_N:])
    return {"scenarios": rows, "false_positive_rate": round(fp / total, 4), "noise_samples": total,
            "false_positive_samples": fp}


def from_db(horizon_min=30):
    """Back-test real data: how many high-risk predictions were followed by an incident (precision) and how many
    incidents were preceded by a risk >= RISK_WARN (recall)."""
    from database.db import fetch_one
    p = fetch_one("""SELECT count(*) AS alerts,
        count(*) FILTER (WHERE EXISTS (SELECT 1 FROM incidents i WHERE i.server_id = p.server_id
              AND i.detected_at BETWEEN p.created_at - interval '1 minute'
                                    AND p.created_at + make_interval(mins => %s))) AS true_alerts
        FROM predictions p WHERE p.failure_risk >= 0.65""", (horizon_min,))
    r = fetch_one("""SELECT count(*) AS incidents,
        count(*) FILTER (WHERE EXISTS (SELECT 1 FROM predictions p WHERE p.server_id = i.server_id
              AND p.failure_risk >= %s AND p.created_at BETWEEN i.detected_at - make_interval(mins => %s)
                                                         AND i.detected_at + interval '1 minute')) AS caught
        FROM incidents i WHERE i.status IN ('resolved', 'closed')""", (RISK_WARN, horizon_min))
    return {"high_risk_alerts": p["alerts"], "alerts_followed_by_incident": p["true_alerts"],
            "precision": round(p["true_alerts"] / p["alerts"], 3) if p["alerts"] else None,
            "resolved_incidents": r["incidents"], "incidents_preceded_by_risk_warning": r["caught"],
            "recall": round(r["caught"] / r["incidents"], 3) if r["incidents"] else None, "horizon_minutes": horizon_min}


def _print(rep):
    print(f"{'scenario':<16}{'detected':>9}{'delay':>8}{'early-warn':>12}")
    for r in rep["scenarios"]:
        print(f"{r['scenario']:<16}{r['detection_rate']:>9}{str(r['avg_delay_samples']):>8}"
              f"{str(r['avg_early_warning_samples']):>12}")
    print(f"\nfalse-positive rate on normal traffic: {rep['false_positive_rate']:.2%} "
          f"({rep['false_positive_samples']}/{rep['noise_samples']} samples)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-db", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.from_db:
        from dotenv import load_dotenv
        load_dotenv()
        print(json.dumps(from_db(), indent=2))
    else:
        rep = synthetic()
        print(json.dumps(rep, indent=2)) if a.json else _print(rep)
