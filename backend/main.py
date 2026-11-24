"""AIOpsGuard FastAPI gateway.   Run from the project root:  uvicorn backend.main:app --reload --port 8000"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend import auth, pipeline
from backend.auth import require_agent, require_operator, require_viewer
from database import repository as repo
from database.init_db import init_db
from incidents import failover, manager, rag, rca, safety

log = logging.getLogger("aiopsguard.api")


RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", 7))
RETENTION_INTERVAL_HOURS = float(os.getenv("RETENTION_INTERVAL_HOURS", 6))


async def _retention_loop():
    """Scheduled data-lifecycle maintenance. Incident evidence is preserved inside purge_old_telemetry()."""
    while True:
        await asyncio.sleep(RETENTION_INTERVAL_HOURS * 3600)
        try:
            report = await asyncio.to_thread(repo.run_retention, RETENTION_DAYS, "scheduler")
            log.info("retention run: %s", report)
        except Exception:
            log.exception("scheduled retention failed")


@asynccontextmanager
async def lifespan(app):
    init_db()
    if not auth.enabled():
        log.warning("AIOPS_AUTH=off: the API is UNAUTHENTICATED. Use for local development only.")
    task = None
    if os.getenv("RETENTION_ENABLED", "1") != "0":
        task = asyncio.create_task(_retention_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(title="AIOpsGuard API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["Authorization", "Content-Type", "X-Agent-Token"])


# ---------- schemas ----------
class MetricsIn(BaseModel):
    cpu_percent: float = Field(ge=0, le=100)
    memory_percent: float = Field(ge=0, le=100)
    disk_percent: float = Field(default=0, ge=0, le=100)
    process_count: int = Field(default=0, ge=0)
    load_avg: float = Field(default=0, ge=0)
    swap_percent: float = Field(default=0, ge=0, le=100)
    uptime_seconds: int = Field(default=0, ge=0)
    net_sent_kbps: float = Field(default=0, ge=0)
    net_recv_kbps: float = Field(default=0, ge=0)
    disk_read_kbps: float = Field(default=0, ge=0)
    disk_write_kbps: float = Field(default=0, ge=0)


class ProcessIn(BaseModel):
    pid: int
    name: str
    cpu_percent: float = Field(default=0, ge=0)
    memory_percent: float = Field(default=0, ge=0)
    status: Optional[str] = None
    num_threads: int = Field(default=0, ge=0)


class LogIn(BaseModel):
    level: str = "INFO"
    source: Optional[str] = None
    message: str
    ts: Optional[datetime] = None


class TelemetryIn(BaseModel):
    hostname: str = Field(min_length=1, max_length=100)
    ip_address: Optional[str] = None
    os_name: Optional[str] = None
    role: Literal["api", "database", "worker", "other"] = "worker"
    metrics: MetricsIn
    processes: list[ProcessIn] = []
    logs: list[LogIn] = []


class StatusIn(BaseModel):
    status: Literal["open", "investigating", "pending_approval", "resolved", "closed"]


class DecisionIn(BaseModel):
    approve: bool
    decided_by: Optional[str] = None   # ignored: the approver is taken from the authenticated token


class ResultIn(BaseModel):
    ok: bool
    message: str = ""


def _safe_rca(incident_id):
    try:
        rca.analyze(incident_id)
    except Exception:
        log.exception("RCA failed for incident %s", incident_id)


# ---------- telemetry ----------
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/ingest", dependencies=[Depends(require_agent)])
def ingest(payload: TelemetryIn, background: BackgroundTasks):
    result = pipeline.process_telemetry(payload.model_dump())
    if result["incident_created"]:
        background.add_task(_safe_rca, result["incident_id"])
    return result


@app.get("/api/servers", dependencies=[Depends(require_viewer)])
def servers():
    return repo.list_servers()


@app.get("/api/servers/{server_id}/metrics", dependencies=[Depends(require_viewer)])
def server_metrics(server_id: int, limit: int = Query(60, ge=1, le=500)):
    return repo.recent_metrics(server_id, limit)


@app.get("/api/servers/{server_id}/processes", dependencies=[Depends(require_viewer)])
def server_processes(server_id: int, top: int = Query(10, ge=1, le=50)):
    return repo.latest_processes(server_id, top)


@app.get("/api/servers/{server_id}/logs", dependencies=[Depends(require_viewer)])
def server_logs(server_id: int, limit: int = Query(40, ge=1, le=200), level: Optional[str] = None):
    return repo.recent_logs(server_id, limit, min_level=level)


@app.get("/api/servers/{server_id}/predictions", dependencies=[Depends(require_viewer)])
def server_predictions(server_id: int, limit: int = Query(30, ge=1, le=200)):
    return repo.recent_predictions(server_id, limit)


# ---------- incidents ----------
@app.get("/api/incidents", dependencies=[Depends(require_viewer)])
def incidents(status: Optional[str] = None, limit: int = Query(50, ge=1, le=200)):
    return manager.list_incidents(status, limit)


@app.get("/api/incidents/{incident_id}", dependencies=[Depends(require_viewer)])
def incident(incident_id: int):
    inc = manager.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "Incident not found")
    return inc


@app.post("/api/incidents/{incident_id}/analyze", dependencies=[Depends(require_operator)])
def analyze(incident_id: int):
    try:
        return rca.analyze(incident_id)
    except LookupError:
        raise HTTPException(404, "Incident not found")


@app.post("/api/incidents/{incident_id}/status", dependencies=[Depends(require_operator)])
def set_status(incident_id: int, body: StatusIn):
    try:
        manager.update_status(incident_id, body.status)
    except LookupError:
        raise HTTPException(404, "Incident not found")
    return manager.get_incident(incident_id)


@app.post("/api/actions/{action_id}/decision")
def decide(action_id: int, body: DecisionIn, user: str = Depends(require_operator)):
    try:
        incident_id = manager.decide_action(action_id, body.approve, user)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except safety.ProtectedProcessError as exc:
        raise HTTPException(409, f"Refused by safety policy: {exc}")
    except (failover.NoSafeTargetError, failover.RouteConflictError) as exc:
        raise HTTPException(409, str(exc))
    log.info("action %s %s by %s", action_id, "APPROVED" if body.approve else "REJECTED", user)
    return manager.get_incident(incident_id)


# ---------- agent action channel ----------
@app.get("/api/agents/{hostname}/actions", dependencies=[Depends(require_agent)])
def agent_actions(hostname: str):
    return manager.pending_for_agent(hostname)


@app.post("/api/agents/actions/{action_id}/result", dependencies=[Depends(require_agent)])
def agent_result(action_id: int, body: ResultIn):
    try:
        manager.record_action_result(action_id, body.ok, body.message)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


# ---------- identity, RCA status, analytics, audit, retention ----------
@app.get("/api/auth/session")
def session(authorization: Optional[str] = Header(default=None)):
    user, role = auth.identify(authorization)
    return {"user": user, "role": role, "auth_enabled": auth.enabled()}


@app.get("/api/rca/status", dependencies=[Depends(require_viewer)])
def rca_status(ping: bool = False):
    """ping=true performs a live LLM round-trip (costs a few tokens)."""
    return rca.llm_status(ping=ping)


@app.get("/api/analytics/summary", dependencies=[Depends(require_viewer)])
def analytics(hours: int = Query(1, ge=1, le=720)):
    return repo.analytics_summary(hours)


@app.get("/api/audit/actions", dependencies=[Depends(require_viewer)])
def audit(limit: int = Query(100, ge=1, le=500), status: Optional[str] = None):
    return repo.audit_trail(limit, status)


@app.post("/api/maintenance/retention")
def run_retention(keep_days: int = Query(RETENTION_DAYS, ge=1, le=365), user: str = Depends(require_operator)):
    return repo.run_retention(keep_days, f"manual:{user}")


@app.get("/api/maintenance/history", dependencies=[Depends(require_viewer)])
def retention_history(limit: int = Query(10, ge=1, le=100)):
    return repo.maintenance_history(limit)


@app.post("/api/rag/reindex", dependencies=[Depends(require_operator)])
def rag_reindex():
    return {"indexed": rag.reindex_missing()}


# ---------- traffic failover ----------
@app.get("/api/routes", dependencies=[Depends(require_viewer)])
def routes():
    return failover.list_routes()


@app.get("/api/incidents/{incident_id}/failover-options", dependencies=[Depends(require_viewer)])
def failover_options(incident_id: int):
    inc = manager.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "incident not found")
    return {"severity": inc["severity"], "candidates": failover.ranked_candidates(inc["server_id"])}


@app.post("/api/routes/{route_id}/restore")
def restore_route(route_id: int, force: bool = False, user: str = Depends(require_operator)):
    try:
        return failover.restore_primary(route_id, user, force)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except (failover.NoSafeTargetError, failover.RouteConflictError) as exc:
        raise HTTPException(409, str(exc))
