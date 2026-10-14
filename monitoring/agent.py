"""AIOpsGuard monitoring agent (psutil). Samples CPU/RAM/disk/processes/logs and ships them to the API.
Also executes ONLY actions that a human approved in the dashboard.

    python -m monitoring.agent --server http://localhost:8000 --interval 5 --role api
"""
import argparse
import os
import platform
import re
import socket
import time
from pathlib import Path

import psutil
import requests

DEFAULT_LOG = str(Path(__file__).with_name("demo_app.log"))
LEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b")
NEVER_KILL = {"system", "registry", "systemd", "init", "sshd", "svchost.exe", "lsass.exe", "csrss.exe",
              "wininit.exe", "services.exe", "explorer.exe", "winlogon.exe", "postgres", "postgres.exe"}
PROTECTED_CMDLINE = ("uvicorn", "monitoring.agent", "agent.py")

_procs = {}  # pid -> psutil.Process kept alive so per-process cpu_percent has a baseline


class LogTailer:
    def __init__(self, path):
        self.path, self.offset = path, None

    def read(self, max_lines=50):
        if not os.path.exists(self.path):
            return []
        size = os.path.getsize(self.path)
        if self.offset is None or size < self.offset:  # first run or truncated: start at end
            self.offset = size
            return []
        out = []
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self.offset)
            for line in f.readlines()[-max_lines:]:
                line = line.strip()
                if line:
                    m = LEVEL_RE.search(line)
                    out.append({"level": m.group(1) if m else "INFO", "source": os.path.basename(self.path),
                                "message": line[:500]})
            self.offset = f.tell()
        return out


def top_processes(n=10):
    ncpu = psutil.cpu_count() or 1
    rows, alive = [], set()
    for p in psutil.process_iter(["pid", "name", "memory_percent", "status"]):
        try:
            pid = p.info["pid"]
            if pid == 0 or not p.info["name"]:
                continue
            alive.add(pid)
            proc = _procs.setdefault(pid, p)
            rows.append({"pid": pid, "name": p.info["name"], "status": p.info["status"],
                         "cpu_percent": round(min(proc.cpu_percent(None) / ncpu, 100.0), 1),
                         "memory_percent": round(p.info["memory_percent"] or 0.0, 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for pid in list(_procs):
        if pid not in alive:
            _procs.pop(pid, None)
    rows.sort(key=lambda r: (r["cpu_percent"], r["memory_percent"]), reverse=True)
    return rows[:n]


def sample(top_n):
    try:
        load = psutil.getloadavg()[0]
    except (AttributeError, OSError):
        load = 0.0
    disk_root = os.path.abspath(os.sep)
    return {"cpu_percent": psutil.cpu_percent(interval=None), "memory_percent": psutil.virtual_memory().percent,
            "disk_percent": psutil.disk_usage(disk_root).percent, "process_count": len(psutil.pids()),
            "load_avg": round(load, 2)}


def execute_action(act):
    """Kill a process only if PID+name still match and it is not protected. Returns (ok, message)."""
    pid, name = act["target_pid"], act["target_name"]
    try:
        p = psutil.Process(pid)
        if p.name().lower() != name.lower():
            return False, f"PID {pid} is now '{p.name()}', expected '{name}'. Refusing."
        if p.name().lower() in NEVER_KILL or pid in (os.getpid(), os.getppid(), 0, 4):
            return False, f"'{name}' is protected."
        cmd = " ".join(p.cmdline()).lower()
        if any(tag in cmd for tag in PROTECTED_CMDLINE):
            return False, "Process belongs to AIOpsGuard itself. Refusing."
        p.terminate()
        try:
            p.wait(timeout=5)
        except psutil.TimeoutExpired:
            p.kill()
        return True, f"Terminated {name} (PID {pid})"
    except psutil.NoSuchProcess:
        return False, "Process no longer exists"
    except psutil.AccessDenied:
        return False, "Access denied (run the agent with sufficient privileges)"


def run(server, interval, hostname, role, log_files, top_n):
    tailers = [LogTailer(p) for p in log_files]
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = None
    psutil.cpu_percent(interval=None)
    top_processes(top_n)  # prime per-process counters
    print(f"[agent] {hostname} -> {server} every {interval}s (Ctrl+C to stop)")
    while True:
        time.sleep(interval)
        try:
            logs = [l for t in tailers for l in t.read()]
            payload = {"hostname": hostname, "ip_address": ip, "os_name": f"{platform.system()} {platform.release()}",
                       "role": role, "metrics": sample(top_n), "processes": top_processes(top_n), "logs": logs}
            r = requests.post(f"{server}/api/ingest", json=payload, timeout=10)
            r.raise_for_status()
            res = r.json()
            flag = "ANOMALY" if res["is_anomaly"] else "ok"
            print(f"[agent] cpu={payload['metrics']['cpu_percent']:.0f}% mem={payload['metrics']['memory_percent']:.0f}% "
                  f"risk={res['failure_risk']:.2f} {flag}")
            for act in requests.get(f"{server}/api/agents/{hostname}/actions", timeout=10).json():
                ok, msg = execute_action(act)
                requests.post(f"{server}/api/agents/actions/{act['action_id']}/result",
                              json={"ok": ok, "message": msg}, timeout=10)
                print(f"[agent] action #{act['action_id']}: {msg}")
        except requests.RequestException as exc:
            print(f"[agent] backend unreachable: {exc}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--interval", type=float, default=5)
    ap.add_argument("--hostname", default=socket.gethostname())
    ap.add_argument("--role", default="worker", choices=["api", "database", "worker", "other"])
    ap.add_argument("--log-file", action="append", default=None, help="log file(s) to tail")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    try:
        run(a.server, a.interval, a.hostname, a.role, a.log_file or [DEFAULT_LOG], a.top)
    except KeyboardInterrupt:
        print("\n[agent] stopped")
