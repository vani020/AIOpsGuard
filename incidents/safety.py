"""Remediation safety rules shared by the RCA engine, the approval API and the tests.

Defence in depth (the monitoring agent has its own copy of the protected list and re-checks at execution time):
  1. RCA / LLM output is sanitised (only observed PIDs, never protected processes, must be a real resource hog)
  2. approval endpoint re-checks the protected list (a human cannot approve killing a protected process)
  3. approvals expire (a stale approval cannot hit a recycled PID hours later)
  4. the agent verifies PID + process name + command line before terminating anything
"""
import os
import re

# NOTE: "python" is deliberately NOT listed - the demo load generators are python processes. AIOpsGuard's own
# backend/agent are protected by command line inside the agent (uvicorn / monitoring.agent).
PROTECTED = {
    "system", "system idle process", "registry", "memory compression", "systemd", "init", "sshd", "kernel_task",
    "postgres", "postgres.exe", "svchost.exe", "lsass.exe", "csrss.exe", "wininit.exe", "services.exe",
    "explorer.exe", "winlogon.exe", "dwm.exe", "uvicorn", "node", "node.exe",
}
# processes whose termination would very likely interrupt a user-facing service
SERVICE_PATTERN = re.compile(r"nginx|apache|httpd|mysql|mariadb|mongo|redis|java|docker|kube|gunicorn|tomcat|iis",
                             re.I)
MIN_CPU_FOR_ACTION = 25.0      # % of host CPU a process must use to be proposed for a CPU incident
MIN_MEM_FOR_ACTION = 10.0      # % of host RAM a process must hold to be proposed for a memory incident
APPROVAL_TTL_SECONDS = int(os.getenv("APPROVAL_TTL_SECONDS", 600))


class ProtectedProcessError(ValueError):
    """Raised when someone tries to approve termination of a protected process."""


def is_protected(name):
    return str(name or "").strip().lower() in PROTECTED


def risk_level(name):
    """Heuristic blast-radius label shown next to every proposed action."""
    return "high" if SERVICE_PATTERN.search(str(name or "")) else "medium"
