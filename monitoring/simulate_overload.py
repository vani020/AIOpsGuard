"""Controlled fault injection for demos/testing.  Always stoppable with Ctrl+C.

    python -m monitoring.simulate_overload --mode cpu --duration 60
    python -m monitoring.simulate_overload --mode memory --target 88
    python -m monitoring.simulate_overload --mode logs
    python -m monitoring.simulate_overload --mode all

WARNING: cpu mode loads every core and memory mode fills RAM up to --target percent.
Save your work first and keep the target below ~90%.
"""
import argparse
import multiprocessing as mp
import random
import time
from datetime import datetime
from pathlib import Path

import psutil

LOG = Path(__file__).with_name("demo_app.log")
ERRORS = ["ERROR db: connection timeout after 30s", "ERROR api: connection refused by upstream service",
          "CRITICAL worker: out of memory while allocating buffer", "ERROR db: deadlock detected, transaction aborted"]


def _burn(stop_at):
    while time.time() < stop_at:
        sum(i * i for i in range(10_000))


def cpu_load(duration):
    stop_at = time.time() + duration
    workers = [mp.Process(target=_burn, args=(stop_at,), name="stress-cpu") for _ in range(psutil.cpu_count() or 2)]
    for w in workers:
        w.start()
    return workers


def memory_load(duration, target):
    blocks, stop_at = [], time.time() + duration
    while psutil.virtual_memory().percent < target and time.time() < stop_at:
        blocks.append(bytearray(64 * 1024 * 1024))
        for i in range(0, len(blocks[-1]), 4096):  # touch pages so they are really resident
            blocks[-1][i] = 1
        time.sleep(0.2)
    while time.time() < stop_at:
        time.sleep(0.5)
    return len(blocks)


def write_logs(duration):
    stop_at = time.time() + duration
    with open(LOG, "a", encoding="utf-8") as f:
        while time.time() < stop_at:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {random.choice(ERRORS)}\n")
            f.flush()
            time.sleep(0.7)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["cpu", "memory", "logs", "all"], default="cpu")
    ap.add_argument("--duration", type=int, default=45)
    ap.add_argument("--target", type=float, default=88, help="memory %% to reach in memory mode")
    a = ap.parse_args()
    workers = []
    try:
        print(f"[sim] mode={a.mode} duration={a.duration}s (Ctrl+C to abort)")
        if a.mode in ("cpu", "all"):
            workers += cpu_load(a.duration)
        if a.mode in ("logs", "all"):
            p = mp.Process(target=write_logs, args=(a.duration,), name="stress-logs")
            p.start()
            workers.append(p)
        if a.mode in ("memory", "all"):
            memory_load(a.duration, a.target)
        for w in workers:
            w.join()
    except KeyboardInterrupt:
        pass
    finally:
        for w in workers:
            if w.is_alive():
                w.terminate()
        print("[sim] finished, resources released")
