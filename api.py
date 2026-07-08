#!/usr/bin/env python3

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict

from core.storage import StorageManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("nmpl_api")

db = StorageManager()
templates = Jinja2Templates(directory="templates")

@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_storage()
    yield
    if hasattr(db, "close"):
        db.close()

app = FastAPI(
    title="NMPL API",
    description="Continuous network diagnostics API",
    version="1.0.0",
    lifespan=lifespan
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = (time.time() - start_time) * 1000
    logger.info("%s %s | Status: %s | %.2fms", request.method, request.url.path, response.status_code, process_time)
    return response

class Metric(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    timestamp: str
    target: str
    summary: str
    status_flag: str
    latency_ms: Optional[float] = None
    loss_pct: Optional[float] = None
    jitter_ms: Optional[float] = None

class Incident(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    timestamp: str
    target: str
    structural_fault_summary: str
    bottleneck_hop: Optional[int] = None
    bottleneck_host: Optional[str] = None
    bottleneck_loss_pct: Optional[float] = None

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html", context={})

@app.get("/partials/metrics", response_class=HTMLResponse)
def partial_metrics(request: Request, target: Optional[str] = None, limit: int = 40):
    rows = db.get_metrics_timeline(target=target, limit=limit)
    return templates.TemplateResponse(request=request, name="partials/metrics.html", context={"rows": rows})

@app.get("/partials/engine", response_class=HTMLResponse)
def partial_engine(request: Request, limit: int = 1):
    incidents = db.get_all_incidents(limit=limit)
    incident = incidents if incidents else None
    return templates.TemplateResponse(request=request, name="partials/engine.html", context={"incident": incident})

@app.get("/partials/route/{incident_id}", response_class=HTMLResponse)
def partial_route(request: Request, incident_id: int):
    row = db.get_incident(incident_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    
    raw = json.loads(row) if row else {}
    hops = raw.get("hops", [])
    analysis = raw.get("analyzer", {}) or raw.get("analysis", {})
    return templates.TemplateResponse(request=request, name="partials/route.html", context={"hops": hops, "analysis": analysis})

@app.get("/metrics", response_model=List[Metric])
def get_metrics(target: Optional[str] = None, limit: int = 100):
    rows = db.get_metrics_timeline(target=target, limit=limit)
    return [
        {
            "id": r, "timestamp": r, "target": r, "summary": r,
            "status_flag": r, "latency_ms": r, "loss_pct": r, "jitter_ms": r
        }
        for r in rows
    ]

@app.get("/incidents", response_model=List[Incident])
def get_incidents(limit: int = 100):
    rows = db.get_all_incidents(limit=limit)
    return [
        {
            "id": r, "timestamp": r, "target": r, "structural_fault_summary": r,
            "bottleneck_hop": r, "bottleneck_host": r, "bottleneck_loss_pct": r
        }
        for r in rows
    ]

@app.get("/incident/{incident_id}", response_model=Incident)
def get_incident(incident_id: int):
    row = db.get_incident(incident_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return {
        "id": row, "timestamp": row, "target": row, "structural_fault_summary": row,
        "bottleneck_hop": row, "bottleneck_host": row, "bottleneck_loss_pct": row
    }

@app.post("/report/{incident_id}")
async def generate_report(incident_id: int):
    row = db.get_incident(incident_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    report_data = {
        "id": row,
        "timestamp": row,
        "target": row,
        "summary": row,
        "evidence": {
            "bottleneck_hop": row,
            "bottleneck_host": row,
            "bottleneck_loss_pct": row
        },
        "raw_runlog": json.loads(row) if row else {}
    }

    report_path = f"nmpl_report_incident_{incident_id}.json"
    await asyncio.to_thread(Path(report_path).write_text, json.dumps(report_data, indent=2))

    return {
        "message": f"Report saved to: {report_path}",
        "path": report_path,
        "incident_id": incident_id
    }

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
