"""Root-cause analysis: gather evidence -> retrieve similar past incidents -> LLM (or rule-based fallback)."""
import json
import logging
import os

from database import repository as repo
from incidents import manager
from ml.detector import CPU_HARD, MEM_HARD

log = logging.getLogger("aiopsguard.rca")

PROTECTED = {"system", "system idle process", "registry", "memory compression", "systemd", "init", "sshd",
             "postgres", "postgres.exe", "svchost.exe", "lsass.exe", "csrss.exe", "wininit.exe",
             "services.exe", "explorer.exe", "winlogon.exe", "dwm.exe", "uvicorn", "kernel_task"}
CATEGORIES = ["cpu_saturation", "memory_pressure", "disk_full", "application_errors", "general_anomaly"]

SYSTEM_PROMPT = ("You are a senior site-reliability engineer. Diagnose infrastructure incidents strictly from the "
                 "evidence provided. Never invent numbers or processes. Reply with a single JSON object only.")


def collect_evidence(incident_id):
    inc = manager.get_incident(incident_id)
    if not inc:
        raise LookupError("incident not found")
    sid = inc["server_id"]
    metrics = repo.recent_metrics(sid, 15)
    metric_id = inc["trigger_metric_id"] or (metrics[-1]["metric_id"] if metrics else None)
    latest = metrics[-1]["metric_id"] if metrics else metric_id
    reasons = []
    for p in inc["predictions"]:
        for r in p["reasons"]:
            if r not in reasons:
                reasons.append(r)
    logs = repo.recent_logs(sid, 40, min_level="WARNING")[:15]
    return {
        "incident": {"id": inc["incident_id"], "hostname": inc["hostname"], "severity": inc["severity"],
                     "title": inc["title"]},
        "reasons": reasons,
        "metrics": [{k: (round(float(m[k]), 1)) for k in ("cpu_percent", "memory_percent", "disk_percent",
                                                         "process_count", "load_avg")} for m in metrics],
        "processes": repo.processes_for_metric(latest, 8) if latest else [],
        "logs": [{"level": l["level"], "message": l["message"][:200]} for l in logs],
    }


def classify(ev):
    last = ev["metrics"][-1] if ev["metrics"] else {"cpu_percent": 0, "memory_percent": 0, "disk_percent": 0}
    if last["disk_percent"] >= 95:
        return "disk_full"
    cpu_n, mem_n = last["cpu_percent"] / CPU_HARD, last["memory_percent"] / MEM_HARD
    if max(cpu_n, mem_n) >= 0.95 or any("cpu_percent" in r or "memory_percent" in r for r in ev["reasons"]):
        return "cpu_saturation" if cpu_n >= mem_n else "memory_pressure"
    if any(l["level"] in ("ERROR", "CRITICAL") for l in ev["logs"]):
        return "application_errors"
    return "general_anomaly"


def _safe_actions(actions, ev):
    """Only allow killing processes that were actually observed, and never protected/system ones."""
    known = {p["pid"]: p["name"] for p in ev["processes"]}
    out = []
    for a in actions or []:
        try:
            pid = int(a.get("pid"))
        except (TypeError, ValueError):
            continue
        name = known.get(pid)
        if name is None or name.lower() in PROTECTED:
            continue
        out.append({"action_type": "kill_process", "pid": pid, "name": name,
                    "reason": str(a.get("reason") or "Suggested by RCA")[:300]})
    return out[:2]


def fallback_analysis(ev, similar):
    cat = classify(ev)
    last = ev["metrics"][-1] if ev["metrics"] else {}
    procs = ev["processes"]
    err_logs = [l for l in ev["logs"] if l["level"] in ("ERROR", "CRITICAL")]
    actions, culprit = [], None
    if cat == "cpu_saturation" and procs:
        top = max(procs, key=lambda p: p["cpu_percent"])
        if top["cpu_percent"] >= 25:
            culprit = top
            cause = (f"Process '{top['name']}' (PID {top['pid']}) is using {top['cpu_percent']:.0f}% CPU, "
                     f"driving host CPU to {last.get('cpu_percent', 0):.0f}%.")
    elif cat == "memory_pressure" and procs:
        top = max(procs, key=lambda p: p["memory_percent"])
        if top["memory_percent"] >= 10:
            culprit = top
            cause = (f"Process '{top['name']}' (PID {top['pid']}) holds {top['memory_percent']:.0f}% of RAM, "
                     f"pushing host memory to {last.get('memory_percent', 0):.0f}%.")
    if culprit is None:
        cause = {"disk_full": "Disk usage has crossed the critical threshold.",
                 "application_errors": "A burst of error-level log lines indicates an application-level failure.",
                 }.get(cat, "Resource usage deviates from this server's learned normal behaviour with no single dominant process.")
    else:
        actions.append({"pid": culprit["pid"], "name": culprit["name"],
                        "reason": f"Dominant resource consumer during {cat.replace('_', ' ')}"})
    explanation = ("Detected signals: " + "; ".join(ev["reasons"][:4]) + "." if ev["reasons"] else "Anomaly detected.")
    if err_logs:
        explanation += f" Recent logs show errors such as: \"{err_logs[0]['message'][:120]}\"."
    steps = {
        "cpu_saturation": "1. Review the flagged process and stop it if non-essential (approval required). 2. Add CPU limits or reschedule heavy jobs. 3. Check for runaway loops after the last deploy.",
        "memory_pressure": "1. Restart or stop the flagged process (approval required). 2. Check for memory leaks and recycle workers periodically. 3. Add a memory alert at 75%.",
        "disk_full": "1. Find large files/log directories and rotate or archive them. 2. Enable log rotation. 3. Add a disk alert at 80%.",
        "application_errors": "1. Inspect the application log around the first error. 2. Check downstream dependencies (database, network). 3. Roll back the latest change if errors began after a deploy.",
    }.get(cat, "1. Inspect recent changes and logs. 2. Compare against the server's normal baseline. 3. Escalate if the anomaly persists.")
    if similar:
        s = similar[0]
        explanation += f" A similar past incident (#{s['incident_id']}: {s['title']}) was resolved by: {s['recommendation']}"
    return {"category": cat, "root_cause": cause, "explanation": explanation, "recommendation": steps,
            "suggested_actions": actions}


def _call_llm(prompt):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(model=os.getenv("LLM_MODEL", "claude-sonnet-5-5"), max_tokens=1200,
                                 system=SYSTEM_PROMPT, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _parse_json(text):
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def _build_prompt(ev, similar):
    return (
        "Analyse this incident. Return JSON with keys: category (one of " + ", ".join(CATEGORIES) + "), "
        "root_cause (1-2 sentences), explanation (3-5 sentences citing numbers from the evidence), "
        "recommendation (short numbered steps in one string), suggested_actions (list of objects with keys "
        "action_type='kill_process', pid, name, reason; only PIDs present in 'processes'; empty list if not clearly justified).\n\n"
        f"EVIDENCE:\n{json.dumps(ev, default=str)}\n\n"
        f"SIMILAR PAST INCIDENTS:\n{json.dumps(similar, default=str)}"
    )


def analyze(incident_id):
    """Run RCA for an incident, store the result, and return the updated incident."""
    ev = collect_evidence(incident_id)
    similar = manager.find_similar(classify(ev), incident_id)
    result, source = None, "rules"
    try:
        text = _call_llm(_build_prompt(ev, similar))
        if text:
            result, source = _parse_json(text), "llm"
            if result.get("category") not in CATEGORIES:
                result["category"] = classify(ev)
    except Exception as exc:  # network / quota / bad JSON -> degrade gracefully
        log.warning("LLM RCA failed (%s); using rule-based fallback", exc)
        result = None
    if result is None:
        result, source = fallback_analysis(ev, similar), "rules"
    result["suggested_actions"] = _safe_actions(result.get("suggested_actions"), ev)
    for k in ("root_cause", "explanation", "recommendation"):
        if not isinstance(result.get(k), str):
            result[k] = str(result.get(k) or "")
    manager.save_rca(incident_id, result, source)
    return manager.get_incident(incident_id)
