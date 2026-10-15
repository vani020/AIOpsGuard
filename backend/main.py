
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend import pipeline
from database import repository as repo
from database.init_db import init_db
from incidents import manager, rca

log = logging.getLogger("aiopsguard.api")


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="AIOpsGuard API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


# ---------- schemas ----------
class MetricsIn(BaseModel):
    cpu_percent: float = Field(ge=0, le=100)
    memory_percent: float = Field(ge=0, le=100)
    disk_percent: float = Field(default=0, ge=0, le=100)
    process_count: int = Field(default=0, ge=0)
    load_avg: float = Field(default=0, ge=0)


class ProcessIn(BaseModel):
    pid: int
    name: str
    cpu_percent: float = Field(default=0, ge=0)
    memory_percent: float = Field(default=0, ge=0)
    status: Optional[str] = None


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
    decided_by: str = "admin"


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


@app.post("/api/ingest")
def ingest(payload: TelemetryIn, background: BackgroundTasks):
    result = pipeline.process_telemetry(payload.model_dump())
    if result["incident_created"]:
        background.add_task(_safe_rca, result["incident_id"])
    return result


@app.get("/api/servers")
def servers():
    return repo.list_servers()


@app.get("/api/servers/{server_id}/metrics")
def server_metrics(server_id: int, limit: int = Query(60, ge=1, le=500)):
    return repo.recent_metrics(server_id, limit)


@app.get("/api/servers/{server_id}/processes")
def server_processes(server_id: int, top: int = Query(10, ge=1, le=50)):
    return repo.latest_processes(server_id, top)


@app.get("/api/servers/{server_id}/logs")
def server_logs(server_id: int, limit: int = Query(40, ge=1, le=200), level: Optional[str] = None):
    return repo.recent_logs(server_id, limit, min_level=level)


@app.get("/api/servers/{server_id}/predictions")
def server_predictions(server_id: int, limit: int = Query(30, ge=1, le=200)):
    return repo.recent_predictions(server_id, limit)


# ---------- incidents ----------
@app.get("/api/incidents")
def incidents(status: Optional[str] = None, limit: int = Query(50, ge=1, le=200)):
    return manager.list_incidents(status, limit)


@app.get("/api/incidents/{incident_id}")
def incident(incident_id: int):
    inc = manager.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "Incident not found")
    return inc


@app.post("/api/incidents/{incident_id}/analyze")
def analyze(incident_id: int):
    try:
        return rca.analyze(incident_id)
    except LookupError:
        raise HTTPException(404, "Incident not found")


@app.post("/api/incidents/{incident_id}/status")
def set_status(incident_id: int, body: StatusIn):
    try:
        manager.update_status(incident_id, body.status)
    except LookupError:
        raise HTTPException(404, "Incident not found")
    return manager.get_incident(incident_id)


@app.post("/api/actions/{action_id}/decision")
def decide(action_id: int, body: DecisionIn):
    try:
        incident_id = manager.decide_action(action_id, body.approve, body.decided_by)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return manager.get_incident(incident_id)


# ---------- agent action channel ----------
@app.get("/api/agents/{hostname}/actions")
def agent_actions(hostname: str):
    return manager.pending_for_agent(hostname)


@app.post("/api/agents/actions/{action_id}/result")
def agent_result(action_id: int, body: ResultIn):
    try:
        manager.record_action_result(action_id, body.ok, body.message)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}
