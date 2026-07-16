#!/usr/bin/env python3

import argparse
import json
import logging
import os
import time
import random
import threading
from pathlib import Path
from datetime import datetime
from functools import partial
from concurrent.futures import ThreadPoolExecutor

from core import __main__
from core.detect import Detector
from core.storage import StorageManager

# Configuration from env
BASE_DIR = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

DEFAULT_INTERVAL = float(os.getenv("NMPL_PROBE_INTERVAL", "3.0"))
DEFAULT_TARGETS = os.getenv("NMPL_DEFAULT_TARGETS", "")
LOG_LEVEL = os.getenv("NMPL_LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("nmpl_cli")

db = StorageManager()
_active_snapshots = set()


def evaluate_and_route_incident(engine_args, target):
    global _active_snapshots
    if target in _active_snapshots:
        return

    try:
        _active_snapshots.add(target)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Target {target} degraded. Starting trace snapshot.")
        time.sleep(random.uniform(0.1, 0.5))

        mtr_args = [arg for arg in engine_args if arg != target] + ["--mtr", target]
        incident_payload = __main__.main_with_result(mtr_args)

        if not incident_payload.get("hops"):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] MTR trace for {target} returned no hop data — skipping incident record.")
            return

        open_incident = db.get_open_incident(target)
        if open_incident:
            db.update_incident(open_incident[0], incident_payload, source="cli")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Updated open incident #{open_incident[0]} with fresh evidence.")
        else:
            new_id = db.log_incident(target, incident_payload, source="cli")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Opened new incident #{new_id}.")
    finally:
        _active_snapshots.discard(target)


def _evaluate_and_log(target, engine_args):
    result = __main__.main_with_result(engine_args)

    engine_metrics = result.get("metrics", {})
    metrics = {
        "loss_pct": engine_metrics.get("loss_pct", 0.0) or 0.0,
        "latency_ms": engine_metrics.get("latency_ms", 0.0) or 0.0,
        "jitter_ms": engine_metrics.get("jitter_ms", 0.0) or 0.0
    }
    summary = result.get("summary", "") or ""
    is_alert = result.get("is_alert", False)
    status_flag = "ALERT" if is_alert else "OK"

    db.log_heartbeat(target, summary, status_flag, metrics, source="cli-once")

    return result, is_alert, status_flag


def run_once(engine_args):
    db.init_storage()

    target = "unknown"
    if engine_args:
        if not engine_args[-1].startswith("-"):
            target = engine_args[-1]

    result, is_alert, status_flag = _evaluate_and_log(target, engine_args)
    summary = result.get("summary", "") or ""

    if is_alert:
        evaluate_and_route_incident(engine_args, target)
    else:
        open_incident = db.get_open_incident(target)
        if open_incident:
            db.resolve_incident(open_incident[0])
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Resolved incident #{open_incident[0]} for {target}.")

    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="Export JSON")
    parser.add_argument("--csv", help="Export CSV")
    parser.add_argument("--quiet", action="store_true")
    args, _ = parser.parse_known_args()

    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2))
    if args.csv:
        Path(args.csv).write_text(result.get("csv", ""))
    if not args.quiet:
        print(summary)


_detectors = {}
_detectors_lock = threading.Lock()
_in_flight = set()
_in_flight_lock = threading.Lock()


def _get_detector(target):
    with _detectors_lock:
        d = _detectors.get(target)
        if d is None:
            d = Detector()
            _detectors[target] = d
        return d


def probe_target(target, engine_args_unused, last_incident_times, INCIDENT_COOLDOWN):
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

        db.log_heartbeat(target, summary, status_flag, metrics, source="cli")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [{target}] {status_flag}: {summary}")

        if is_alert:
            now = time.time()
            if target not in last_incident_times or last_incident_times[target] is None or (now - last_incident_times[target]) >= INCIDENT_COOLDOWN:
                evaluate_and_route_incident([target], target)
                last_incident_times[target] = now
        elif not warming_up:
            open_incident = db.get_open_incident(target)
            if open_incident:
                db.resolve_incident(open_incident[0])
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [{target}] Resolved incident #{open_incident[0]}.")
    except Exception as exc:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [{target}] Probe execution error: {exc}")
    finally:
        with _in_flight_lock:
            _in_flight.discard(target)


def run_daemon(initial_targets, interval=3.0, base_args=None):
    db.init_storage()
    base_args = base_args or []
    last_incident_times = {}
    INCIDENT_COOLDOWN = 15.0

    for t in initial_targets:
        db.register_active_target(t)

    print(f"Monitoring daemon active with {interval}s intervals.\nUse Ctrl+C to stop.\n")

    probe_worker = partial(
        probe_target,
        engine_args_unused=base_args,
        last_incident_times=last_incident_times,
        INCIDENT_COOLDOWN=INCIDENT_COOLDOWN
    )

    try:
        with ThreadPoolExecutor(max_workers=20) as executor:
            while True:
                loop_start = time.time()
                targets = db.get_active_targets()

                if targets:
                    list(executor.map(probe_worker, targets))

                time.sleep(max(0.1, interval - (time.time() - loop_start)))
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


def generate_isp_report(incident_id: int):
    row = db.get_incident(incident_id)
    if not row:
        print(f"Error: Incident {incident_id} not found.")
        return

    try:
        raw_runlog = json.loads(row[7]) if isinstance(row[7], str) else {}
    except json.JSONDecodeError:
        print(f"Warning: Incident {incident_id} has malformed JSON log.")
        raw_runlog = {}

    report_data = {
        "id": row[0],
        "timestamp": row[1],
        "target": row[2],
        "summary": row[3],
        "status": "Resolved" if row[8] == 1 else "Active/Open",
        "resolved_at": row[9],
        "source": row[10] if len(row) > 10 else None,
        "evidence": {
            "bottleneck_hop": row[4],
            "bottleneck_host": row[5],
            "bottleneck_loss_pct": row[6]
        },
        "raw_runlog": raw_runlog
    }
    report_path = str(REPORTS_DIR / f"nmpl_report_incident_{incident_id}.json")
    Path(report_path).write_text(json.dumps(report_data, indent=2))
    print(f"[REPORT] ISP-ready report saved to: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="NMPL - Continuous Network Diagnostics Daemon")
    parser.add_argument("--targets", default=DEFAULT_TARGETS, required=False,
                         help="Comma-separated target endpoints to monitor")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                         help="Probe interval in seconds")
    parser.add_argument("--once", action="store_true", help="Single CLI run")
    parser.add_argument("--report", type=int, help="Generate report for incident ID")
    parser.add_argument("--json", help="Export JSON")
    parser.add_argument("--csv", help="Export CSV")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    args, unknown = parser.parse_known_args()

    if args.report:
        generate_isp_report(args.report)
        return

    target_list = [t.strip() for t in args.targets.split(",")] if args.targets else []

    cleaned_unknown = [arg for arg in unknown if arg not in ("--json", "--csv", "--quiet")]

    if args.once or (not target_list and not args.targets):
        if target_list:
            for target in target_list:
                run_once([arg for arg in (cleaned_unknown + [target]) if arg != "--once"])
        else:
            run_once([arg for arg in cleaned_unknown if arg != "--once"])
        return

    run_daemon(target_list, args.interval, cleaned_unknown)


if __name__ == "__main__":
    main()