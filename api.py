#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

ENV_FILE = Path(__file__).parent / ".env"
if ENV_FILE.exists():
    load_dotenv(str(ENV_FILE))

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from core import __main__
from core.detect import Detector
from core.storage import StorageManager
from core.enrichment import enrich_hops

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

API_HOST = os.getenv("NMPL_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("NMPL_API_PORT", "8000"))
API_RELOAD = os.getenv("NMPL_API_RELOAD", "false").lower() == "true"
LOG_LEVEL = os.getenv("NMPL_LOG_LEVEL", "INFO").upper()
PROBE_INTERVAL = float(os.getenv("NMPL_PROBE_INTERVAL", "3.0"))
INCIDENT_COOLDOWN = 15.0
MAX_ACTIVE_TARGETS = int(os.getenv("NMPL_MAX_ACTIVE_TARGETS", "25"))

REGISTRATION_RATE_LIMIT = os.getenv("NMPL_REGISTRATION_RATE_LIMIT", "20/minute")
REPORT_RATE_LIMIT = os.getenv("NMPL_REPORT_RATE_LIMIT", "10/minute")
DELETE_RATE_LIMIT = os.getenv("NMPL_DELETE_RATE_LIMIT", "20/minute")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("nmpl_api")

db = StorageManager()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

limiter = Limiter(key_func=get_remote_address)

_TARGET_PATTERN = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9\-\.:]{0,253}[A-Za-z0-9])?$')


def _normalize_target(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    t = raw.strip().rstrip(".")
    if not t or t == "???":
        return None
    if not _TARGET_PATTERN.match(t):
        return None
    return t


_detectors = {}
_detectors_lock = threading.Lock()
_in_flight = set()
_in_flight_lock = threading.Lock()
_active_snapshots = set()
_active_snapshots_lock = threading.Lock()
_last_incident_times = {}
_worker_tasks = {}
_shutdown_event = asyncio.Event()


def _get_detector(target):
    with _detectors_lock:
        d = _detectors.get(target)
        if d is None:
            d = Detector()
            _detectors[target] = d
        return d


def _evaluate_and_route_incident_sync(target):
    with _active_snapshots_lock:
        if target in _active_snapshots:
            return
        _active_snapshots.add(target)
    try:
        logger.info(f"Target {target} degraded. Starting trace snapshot.")
        time.sleep(0.2)

        mtr_args = ["--mtr", target]
        incident_payload = __main__.main_with_result(mtr_args)

        if not incident_payload.get("hops"):
            logger.warning(f"MTR trace for {target} returned no hop data — skipping incident record.")
            return

        incident_payload["hops"] = enrich_hops(incident_payload["hops"])

        open_incident = db.get_open_incident(target)
        if open_incident:
            db.update_incident(open_incident[0], incident_payload, source="api")
            logger.info(f"Updated open incident #{open_incident[0]} with fresh evidence.")
        else:
            new_id = db.log_incident(target, incident_payload, source="api")
            logger.info(f"Opened new incident #{new_id}.")
    finally:
        with _active_snapshots_lock:
            _active_snapshots.discard(target)


def _probe_and_log_sync(target):
    with _in_flight_lock:
        if target in _in_flight:
            return
        _in_flight.add(target)

    try:
        d = _get_detector(target)
        d.probe(target)
        summary = d.status()

        warming_up = not d.baseline_established
        is_alert = (not warming_up) and d.is_alert
        status_flag = "ALERT" if is_alert else ("LEARNING" if warming_up else "OK")

        metrics = {
            "loss_pct": d.current_loss_pct(),
            "latency_ms": d.current_latency_ms(),
            "jitter_ms": d.current_jitter_ms()
        }

        db.log_heartbeat(target, summary, status_flag, metrics, source="api")
        logger.info(f"[{target}] {status_flag}: {summary}")

        if is_alert:
            now = time.time()
            last = _last_incident_times.get(target)
            if last is None or (now - last) >= INCIDENT_COOLDOWN:
                _evaluate_and_route_incident_sync(target)
                _last_incident_times[target] = now
        elif not warming_up:
            open_incident = db.get_open_incident(target)
            if open_incident:
                db.resolve_incident(open_incident[0])
                logger.info(f"[{target}] Resolved incident #{open_incident[0]}.")
    except Exception as e:
        logger.error(f"[{target}] Probe execution error: {e}")
    finally:
        with _in_flight_lock:
            _in_flight.discard(target)


async def _target_worker(target: str):
    while not _shutdown_event.is_set():
        try:
            await asyncio.to_thread(_probe_and_log_sync, target)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Worker iteration failed for {target}: {e}")
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=PROBE_INTERVAL)
        except asyncio.TimeoutError:
            pass


def _ensure_worker(target: str):
    existing = _worker_tasks.get(target)
    if existing is None or existing.done():
        _worker_tasks[target] = asyncio.create_task(_target_worker(target))
        logger.info(f"Started monitoring worker for {target}.")


def _stop_worker(target: str):
    task = _worker_tasks.pop(target, None)
    if task and not task.done():
        task.cancel()
    with _detectors_lock:
        _detectors.pop(target, None)
    _last_incident_times.pop(target, None)


async def _startup_scan_loop():
    while not _shutdown_event.is_set():
        active = set()
        try:
            active = set(db.get_active_targets())
            for target in active:
                normalized = _normalize_target(target)
                if normalized:
                    _ensure_worker(normalized)
                else:
                    logger.warning(f"Skipping invalid target found in active_targets: {target!r}")
        except Exception as e:
            logger.error(f"Error scanning active targets: {e}")

        try:
            open_incidents = db.get_all_incidents(limit=200, open_only=True)
            for inc in open_incidents:
                inc_id, inc_target = inc[0], inc[2]
                if inc_target not in active:
                    db.resolve_incident(inc_id)
                    logger.info(f"Reconciliation: auto-resolved orphaned incident #{inc_id} for untracked target {inc_target!r}.")
        except Exception as e:
            logger.error(f"Error reconciling open incidents: {e}")

        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


async def custom_rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    logger.warning(f"Rate limit hit by {request.client.host if request.client else 'unknown'} on {request.url.path}")

    is_htmx = request.headers.get("HX-Request") == "true"
    path = request.url.path

    if not is_htmx:
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded: {exc.detail}"}
        )

    if path.startswith("/partials/metrics") or path.startswith("/targets/"):
        html_error = (
            "<tr><td colspan='6' class='text-red mono' style='text-align: center; padding: 20px; font-weight: 600;'>"
            "Rate limit exceeded. Slow down your requests."
            "</td></tr>"
        )
        return HTMLResponse(content=html_error, status_code=429)

    if path.startswith("/report/"):
        html_error = (
            "<span class='text-red mono' style='font-weight: 600;'>"
            "Rate limit exceeded. Slow down your requests."
            "</span>"
        )
        return HTMLResponse(content=html_error, status_code=429)

    return HTMLResponse(
        content="<div class='text-red mono' style='padding: 12px;'>Rate limit exceeded.</div>",
        status_code=429,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_storage()
    scan_task = asyncio.create_task(_startup_scan_loop())

    yield

    _shutdown_event.set()
    scan_task.cancel()

    tasks_to_gather = [scan_task]
    for task in _worker_tasks.values():
        task.cancel()
        tasks_to_gather.append(task)

    await asyncio.gather(*tasks_to_gather, return_exceptions=True)

    while True:
        with _in_flight_lock, _active_snapshots_lock:
            if not _in_flight and not _active_snapshots:
                break
        await asyncio.sleep(0.1)

    if hasattr(db, "close"):
        db.close()


app = FastAPI(
    title="NMPL API",
    description="Continuous network diagnostics API",
    version="1.0.0",
    lifespan=lifespan
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, custom_rate_limit_exceeded_handler)


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
    resolved: bool = False


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    try:
        return templates.TemplateResponse(request=request, name="dashboard.html", context={})
    except Exception as e:
        logger.error(f"Failed to render dashboard: {e}")
        raise HTTPException(status_code=500, detail="Template render error")


@app.get("/archive", response_class=HTMLResponse)
def archive_page(request: Request, status: Optional[str] = None, target: Optional[str] = None):
    try:
        rows = db.get_all_incidents(limit=500, open_only=False)
    except Exception as e:
        logger.error(f"Failed to fetch incident archive: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    status_filter = (status or "all").lower()
    target_filter = (target or "").strip().lower()

    def _matches(row):
        resolved = bool(row[8]) if len(row) > 8 else False
        if status_filter == "open" and resolved:
            return False
        if status_filter == "resolved" and not resolved:
            return False
        if target_filter and target_filter not in (row[2] or "").lower():
            return False
        return True

    filtered = [r for r in rows if _matches(r)]

    return templates.TemplateResponse(
        request=request,
        name="archive.html",
        context={
            "incidents": filtered,
            "status_filter": status_filter,
            "target_filter": target or "",
            "total_count": len(rows),
            "filtered_count": len(filtered),
        }
    )


@app.delete("/incident/{incident_id}")
def delete_incident(request: Request, incident_id: int):
    try:
        row = db.get_incident(incident_id)
        if not row:
            raise HTTPException(status_code=404, detail="Incident not found")

        resolved = bool(row[8]) if len(row) > 8 else False
        if not resolved:
            raise HTTPException(status_code=400, detail="Cannot delete an open incident — resolve it first")

        deleted = db.delete_incident(incident_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Incident not found")

        client_host = request.client.host if request.client else "unknown"
        logger.info(f"Incident #{incident_id} permanently deleted (requested by {client_host}).")
        return Response(status_code=200)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete incident {incident_id}: {e}")
        raise HTTPException(status_code=500, detail="Database error")


@app.get("/partials/metrics", response_class=HTMLResponse)
@limiter.limit(REGISTRATION_RATE_LIMIT)
def partial_metrics(request: Request, target: Optional[str] = None, limit: int = 40):
    query_target = _normalize_target(target)
    capped_message = None

    if query_target:
        active_targets = db.get_active_targets()
        if query_target not in active_targets:
            if len(active_targets) >= MAX_ACTIVE_TARGETS:
                logger.warning(
                    f"Refusing to register {query_target}: active target cap ({MAX_ACTIVE_TARGETS}) reached."
                )
                capped_message = (
                    f"Not monitoring — {MAX_ACTIVE_TARGETS} target limit reached. "
                    f"Remove an existing target first."
                )
            else:
                db.register_active_target(query_target)
                _ensure_worker(query_target)
        else:
            _ensure_worker(query_target)

    try:
        rows = db.get_metrics_timeline(target=query_target, limit=limit)
    except Exception as e:
        logger.error(f"Failed to fetch metrics timeline: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    if not rows and query_target:
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")
        placeholder_summary = capped_message or "Initializing diagnostic sweep..."
        rows = [(0, current_time, query_target, placeholder_summary, "PENDING", None, 0.0, None)]

    return templates.TemplateResponse(request=request, name="partials/metrics.html", context={"rows": rows})


@app.get("/partials/engine", response_class=HTMLResponse)
def partial_engine(request: Request, target: Optional[str] = None, incident_id: Optional[int] = None):
    incident = None
    query_target = _normalize_target(target)

    try:
        if incident_id:
            incident = db.get_incident(incident_id)
        elif query_target:
            incidents = db.get_all_incidents(limit=100, open_only=True)
            filtered = [inc for inc in incidents if inc[2] == query_target]
            if filtered:
                incident = filtered[0]
        else:
            active = set(db.get_active_targets())
            incidents = db.get_all_incidents(limit=100, open_only=True)
            live_incidents = [inc for inc in incidents if inc[2] in active]
            if live_incidents:
                incident = live_incidents[0]
    except Exception as e:
        logger.error(f"Failed to fetch incident data: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    return templates.TemplateResponse(request=request, name="partials/engine.html", context={"incident": incident})


@app.get("/partials/route/{incident_id}", response_class=HTMLResponse)
def partial_route(request: Request, incident_id: int):
    try:
        row = db.get_incident(incident_id)
    except Exception as e:
        logger.error(f"Failed to fetch incident {incident_id}: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    if not row:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    try:
        raw = json.loads(row[7]) if isinstance(row[7], str) else {}
    except json.JSONDecodeError:
        logger.warning(f"Incident {incident_id} has malformed JSON log")
        raw = {}

    hops = raw.get("hops", [])
    analysis = raw.get("analyzer", {}) or raw.get("analysis", {})

    return templates.TemplateResponse(request=request, name="partials/route.html", context={"hops": hops, "analysis": analysis})


@app.delete("/targets/{target}")
@limiter.limit(DELETE_RATE_LIMIT)
async def delete_target(request: Request, target: str):
    normalized = _normalize_target(target)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid target")

    client_host = request.client.host if request.client else "unknown"
    logger.info(f"Delete requested for {normalized} by {client_host}.")

    _stop_worker(normalized)

    try:
        db.remove_active_target(normalized)
        open_incident = db.get_open_incident(normalized)
        if open_incident:
            db.resolve_incident(open_incident[0])
            logger.info(f"Auto-resolved incident #{open_incident[0]} on target removal ({normalized}).")
    except Exception as e:
        logger.error(f"Failed to remove target {normalized}: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    logger.info(f"Stopped monitoring {normalized} (requested by {client_host}).")
    return {"status": "removed", "target": normalized}


@app.get("/incidents", response_model=List[Incident])
def get_incidents(limit: int = 100):
    try:
        rows = db.get_all_incidents(limit=limit)
    except Exception as e:
        logger.error(f"Failed to fetch incidents: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    return [
        {
            "id": r[0], "timestamp": r[1], "target": r[2], "structural_fault_summary": r[3],
            "bottleneck_hop": r[4] if len(r) > 4 else None,
            "bottleneck_host": r[5] if len(r) > 5 else None,
            "bottleneck_loss_pct": r[6] if len(r) > 6 else None,
            "resolved": bool(r[8]) if len(r) > 8 else False
        }
        for r in rows
    ]


@app.get("/incident/{incident_id}", response_model=Incident)
def get_incident(incident_id: int):
    try:
        row = db.get_incident(incident_id)
    except Exception as e:
        logger.error(f"Failed to fetch incident {incident_id}: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    if not row:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    return {
        "id": row[0], "timestamp": row[1], "target": row[2], "structural_fault_summary": row[3],
        "bottleneck_hop": row[4] if len(row) > 4 else None,
        "bottleneck_host": row[5] if len(row) > 5 else None,
        "bottleneck_loss_pct": row[6] if len(row) > 6 else None,
        "resolved": bool(row[8]) if len(row) > 8 else False
    }


@app.post("/report/{incident_id}")
@limiter.limit(REPORT_RATE_LIMIT)
async def generate_report(request: Request, incident_id: str, target: Optional[str] = None):
    query_target = _normalize_target(target)
    current_time = time.strftime("%Y-%m-%d %H:%M:%S UTC")

    report_lines = [
        "NMPL REPORT",
        f"Generated: {current_time}"
    ]

    if incident_id == "snapshot":
        try:
            db_rows = db.get_metrics_timeline(target=query_target, limit=100)
        except Exception as e:
            logger.error(f"Failed to fetch metrics for snapshot report: {e}")
            raise HTTPException(status_code=500, detail="Database error")

        report_path = str(REPORTS_DIR / f"nmpl_report_snapshot_{int(time.time())}.txt")

        report_lines.extend([
            f"Status: NOMINAL",
            f"Target Scope: {query_target or 'ALL_TARGETS'}",
            f"Samples Captured: {len(db_rows)}"
        ])

        for r in db_rows:
            report_lines.append(
                f"[{r[1]}] Target: {r[2]} | Status: {r[4]} | Latency: {r[5] or 0.0}ms | Loss: {r[6] or 0.0}%"
            )

        json_payload = {
            "meta": {
                "export_timestamp": current_time,
                "status_flag": "NOMINAL",
                "scope": query_target or "ALL_TARGETS"
            },
            "timeline_matrix": [
                {
                    "timestamp": r[1],
                    "target": r[2],
                    "status": r[4],
                    "metrics": {
                        "latency_ms": r[5],
                        "loss_pct": r[6],
                        "jitter_ms": r[7]
                    }
                }
                for r in db_rows
            ]
        }

    else:
        try:
            inc_id = int(incident_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Incident Identifier")

        try:
            row = db.get_incident(inc_id)
        except Exception as e:
            logger.error(f"Failed to fetch incident {inc_id} for report: {e}")
            raise HTTPException(status_code=500, detail="Database error")

        if not row:
            raise HTTPException(status_code=404, detail=f"Incident {inc_id} not found")

        report_path = str(REPORTS_DIR / f"nmpl_report_incident_{inc_id}.txt")

        try:
            raw_log = json.loads(row[7]) if isinstance(row[7], str) else {}
        except json.JSONDecodeError:
            logger.warning(f"Incident {inc_id} has malformed JSON log")
            raw_log = {}

        hops = raw_log.get("hops", [])
        resolved = bool(row[8]) if len(row) > 8 else False

        # Check stored analysis (in the JSON payload) to prevent assigning fault for cosmetic ICMP rate-limiting.
        analysis_data = raw_log.get("analyzer") or raw_log.get("analysis") or {}
        likely_rate_limited = analysis_data.get("likely_rate_limited", False)
        latency_spike_isolated = analysis_data.get("latency_spike_isolated")

        bottleneck_host = row[5] if len(row) > 5 else None
        bottleneck_enrichment = next(
            (h.get("enrichment") for h in hops if h.get("host") == bottleneck_host and h.get("enrichment")),
            None
        )

        bottleneck_provider_line = ""
        if bottleneck_enrichment and bottleneck_enrichment.get("status") == "confirmed":
            if likely_rate_limited:
                # DO NOT BLAME OPERATORS FOR COSMETIC LOSS (rate-limited ICMP)
                bottleneck_provider_line = (
                    f"Note: Hop {row[4] if len(row) > 4 else 'N/A'} ({bottleneck_enrichment['org']}, "
                    f"confirmed via {bottleneck_enrichment['source']}) shows elevated loss, but the "
                    f"destination remains clean — this pattern typically indicates ICMP rate-limiting "
                    f"at that router, not a real forwarding fault. Not asserted as the cause of any "
                    f"end-to-end degradation."
                )
            elif bottleneck_enrichment.get("asn"):
                bottleneck_provider_line = (
                    f"Bottleneck Operator: {bottleneck_enrichment['org']} "
                    f"(AS{bottleneck_enrichment['asn']}, confirmed via {bottleneck_enrichment['source']})"
                )
            else:
                bottleneck_provider_line = (
                    f"Bottleneck Operator: {bottleneck_enrichment['org']} "
                    f"(confirmed via {bottleneck_enrichment['source']})"
                )

        latency_spike_note = ""
        if latency_spike_isolated:
            latency_spike_note = (
                f"Note: Hop {latency_spike_isolated.get('hop')} ({latency_spike_isolated.get('host')}) "
                f"shows an isolated latency spike that recovers by the next hop — likely ICMP reply "
                f"deprioritization on that router's control plane, not added forwarding delay. "
                f"Not asserted as evidence of a real latency fault."
            )

        report_lines.extend(
            [
                f"Status: FAILURE",
                f"Incident Reference: INC-{inc_id:03d}",
                f"Target Destination: {row[2]}",
                f"Root Fault Summary: {row[3]}",
                f"Localized Bottleneck: Hop {row[4] if len(row) > 4 else 'N/A'} ({row[5] if len(row) > 5 else 'Unknown'})",
            ]
            + ([bottleneck_provider_line] if bottleneck_provider_line else [])
            + ([latency_spike_note] if latency_spike_note else [])
        )

        for hop in hops:
            h_num = hop.get("hop", "?")
            h_host = hop.get("host", "*")
            h_loss = hop.get("loss", "0.0")
            h_delay = hop.get("delay", hop.get("avg", "0.0"))
            enrichment = hop.get("enrichment") or {}
            provider_note = ""
            if enrichment.get("status") == "confirmed":
                provider_note = f" [{enrichment['org']}, confirmed via {enrichment['source']}]"
            report_lines.append(f"Hop {h_num} | Host/IP: {h_host} | Loss: {h_loss}% | Delay: {h_delay}ms{provider_note}")

        json_payload = {
            "meta": {
                "export_timestamp": current_time,
                "incident_id": inc_id,
                "status_flag": "ALERT_RESOLVED" if resolved else "ALERT_ACTIVE"
            },
            "incident_analysis": {
                "target": row[2],
                "summary": row[3],
                "bottleneck": {
                    "hop": row[4] if len(row) > 4 else None,
                    "host": row[5] if len(row) > 5 else None,
                    "loss_pct": row[6] if len(row) > 6 else None,
                    "likely_rate_limited": likely_rate_limited,
                },
                "latency_spike_isolated": latency_spike_isolated,
                "nested_hops_trace": hops
            }
        }

    report_lines.extend([
        "",
        "MACHINE LOGS",
        "---START_JSON_PAYLOAD---",
        json.dumps(json_payload, indent=2),
        "---END_JSON_PAYLOAD---"
    ])

    output_content = "\n".join(report_lines)
    await asyncio.to_thread(Path(report_path).write_text, output_content)

    media_type = "text/plain" if report_path.endswith(".txt") else "application/json"

    return FileResponse(
        report_path,
        media_type=media_type,
        filename=Path(report_path).name,
    )


if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host=API_HOST,
        port=API_PORT,
        reload=API_RELOAD,
    )